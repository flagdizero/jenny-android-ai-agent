"""Backup nel cloud: lo store degli snapshot a pacchi cifrati su una destinazione remota.

Tutto contro ``MemorySink`` (e una volta ``LocalDirSink``): la logica di
pacchi, indici, ripresa e ripristino non ha bisogno di rete per essere provata.
"""

from __future__ import annotations

import json
import os
import zlib
from pathlib import Path

import pytest

from jenny.snapshot.crypto import encrypt_container
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.types import FileEntry, SnapshotManifest
from jenny_extensions.drive.cloud import (
    INDEX_DIR,
    PACKS_DIR,
    REPO_MARKER_KEY,
    CloudBackupError,
    CloudRepository,
    RepoNotFoundError,
    WrongKeyError,
    generate_master_key,
    parse_snapshot_name,
    snapshot_key,
)
from jenny_extensions.drive.cloud_sink import (
    LocalDirSink,
    MemorySink,
    RemoteNotFoundError,
    split_key,
)

pytest.importorskip("cryptography")

from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend  # noqa: E402

_BACKEND = DevAesGcmBackend()


def _workspace(root: Path) -> SnapshotEngine:
    (root / "memory").mkdir(parents=True)
    (root / "uploads").mkdir()
    (root / "SOUL.md").write_text("anima", encoding="utf-8")
    (root / "USER.md").write_text("l'utente", encoding="utf-8")
    (root / "memory" / "MEMORY.md").write_text("# memoria\n" * 50, encoding="utf-8")
    # Stesso contenuto in due posti: un blob solo, due file al ripristino.
    (root / "memory" / "copia.md").write_text("anima", encoding="utf-8")
    (root / "uploads" / "foto.jpg").write_bytes(os.urandom(40_000))
    return SnapshotEngine(root, root / ".jenny" / "snapshots")


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".jenny" not in p.parts
    }


async def _open(sink, key, state, *, create=True, pack_target=1 << 20) -> CloudRepository:
    return await CloudRepository.open(
        sink, key, state, create=create, pack_target_bytes=pack_target, backend=_BACKEND
    )


async def test_roundtrip_restores_the_workspace(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    assert manifest is not None
    sink = MemorySink()
    key = generate_master_key()
    repo = await _open(sink, key, tmp_path / "state")

    result = await repo.backup(engine, manifest.id)
    assert not result.already_remote
    assert result.blobs_uploaded == 4  # cinque file, «anima» due volte
    assert result.packs_uploaded == 1

    # Il telefono nuovo: altro stato locale, stessa chiave.
    fresh = await _open(sink, key, tmp_path / "state2", create=False)
    snapshots = await fresh.list_snapshots()
    assert [s.id for s in snapshots] == [manifest.id]
    progress: list[tuple[int, int]] = []
    restored = await fresh.restore(
        snapshots[0], tmp_path / "back", progress=lambda d, t: progress.append((d, t))
    )
    assert restored.id == manifest.id
    assert _tree(tmp_path / "back") == _tree(tmp_path / "ws")
    assert progress == [(1, 1)]


async def test_nothing_on_the_destination_is_readable_without_the_key(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    (tmp_path / "ws" / "USER.md").write_text("segreto-riconoscibile", encoding="utf-8")
    manifest = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    repo = await _open(sink, generate_master_key(), tmp_path / "state")
    await repo.backup(engine, manifest.id)

    for key, data in sink.files.items():
        assert b"segreto-riconoscibile" not in data, key
        assert b"USER.md" not in data, key
    marker = json.loads(sink.files[REPO_MARKER_KEY])
    assert marker["format"] == "jenny-cloud-backup"
    assert set(marker) == {"format", "version", "repo_id"}


async def test_second_run_sends_only_what_changed(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    engine = _workspace(ws)
    first = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    repo = await _open(sink, generate_master_key(), tmp_path / "state")
    await repo.backup(engine, first.id)
    packs_before = await sink.list(PACKS_DIR)

    (ws / "memory" / "MEMORY.md").write_text("# memoria cambiata", encoding="utf-8")
    second = engine.create_snapshot(trigger="cloud")
    result = await repo.backup(engine, second.id)

    assert result.blobs_uploaded == 1
    assert result.packs_uploaded == 1
    assert len(await sink.list(PACKS_DIR)) == len(packs_before) + 1
    assert [s.id for s in await repo.list_snapshots()] == [second.id, first.id]


async def test_same_snapshot_twice_uploads_nothing(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    repo = await _open(sink, generate_master_key(), tmp_path / "state")
    await repo.backup(engine, manifest.id)
    puts = len(sink.puts)

    again = await repo.backup(engine, manifest.id)

    assert again.already_remote
    assert len(sink.puts) == puts


async def test_small_pack_target_splits_and_restores(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    engine = _workspace(ws)
    for i in range(12):
        (ws / "uploads" / f"f{i}.bin").write_bytes(os.urandom(3_000))
    manifest = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    key = generate_master_key()
    repo = await _open(sink, key, tmp_path / "state", pack_target=8_000)

    result = await repo.backup(engine, manifest.id)

    assert result.packs_uploaded > 3
    assert len(await sink.list(INDEX_DIR)) == result.packs_uploaded
    fresh = await _open(sink, key, tmp_path / "state2", create=False)
    await fresh.restore((await fresh.list_snapshots())[0], tmp_path / "back")
    assert _tree(tmp_path / "back") == _tree(ws)


class _FlakySink(MemorySink):
    """Si rompe dopo *budget* caricamenti, come una rete che cade a metà."""

    def __init__(self, budget: int) -> None:
        super().__init__()
        self.budget = budget

    async def put_bytes(self, key: str, data: bytes) -> None:
        if self.budget <= 0:
            raise OSError("network is unreachable")
        self.budget -= 1
        await super().put_bytes(key, data)


async def test_an_interrupted_run_resumes_without_resending(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    engine = _workspace(ws)
    for i in range(10):
        (ws / "uploads" / f"f{i}.bin").write_bytes(os.urandom(3_000))
    manifest = engine.create_snapshot(trigger="cloud")
    # README, keycheck, marker + tre coppie pacco/indice, poi la rete cade.
    sink = _FlakySink(budget=3 + 6)
    key = generate_master_key()
    repo = await _open(sink, key, tmp_path / "state", pack_target=8_000)

    with pytest.raises(OSError):
        await repo.backup(engine, manifest.id)
    # Niente snapshot zoppo: il manifest va su solo alla fine.
    assert await repo.list_snapshots() == []
    assert not list((tmp_path / "state" / "tmp").iterdir())

    sink.budget = 10_000
    sent_packs = set(await sink.list(PACKS_DIR))
    result = await repo.backup(engine, manifest.id)
    assert result.packs_uploaded >= 1
    assert sent_packs <= set(await sink.list(PACKS_DIR))
    # I tre pacchi già su non tornano su.
    reuploaded = [k for k in sink.puts if k.startswith(PACKS_DIR) and k.split("/")[1] in sent_packs]
    assert len(reuploaded) == len(sent_packs)

    fresh = await _open(sink, key, tmp_path / "state2", create=False)
    await fresh.restore((await fresh.list_snapshots())[0], tmp_path / "back")
    assert _tree(tmp_path / "back") == _tree(ws)


async def test_a_lost_cache_is_rebuilt_from_the_remote_index(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    engine = _workspace(ws)
    first = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    key = generate_master_key()
    await (await _open(sink, key, tmp_path / "state")).backup(engine, first.id)

    (ws / "SOUL.md").write_text("anima nuova", encoding="utf-8")
    second = engine.create_snapshot(trigger="cloud")
    other = await _open(sink, key, tmp_path / "fresh-state", create=False)
    result = await other.backup(engine, second.id)

    assert result.blobs_uploaded == 1


async def test_a_cache_from_another_repository_is_ignored(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    state = tmp_path / "state"
    await (await _open(MemorySink(), generate_master_key(), state)).backup(engine, manifest.id)

    # Altro account, stesso telefono: la cache dice «c'è già tutto» e mente.
    other_sink = MemorySink()
    other = await _open(other_sink, generate_master_key(), state)
    result = await other.backup(engine, manifest.id)

    assert result.blobs_uploaded == 4


async def test_wrong_key_and_missing_repo(tmp_path: Path) -> None:
    sink = MemorySink()
    await _open(sink, generate_master_key(), tmp_path / "state")

    with pytest.raises(WrongKeyError):
        await _open(sink, generate_master_key(), tmp_path / "s2", create=False)
    with pytest.raises(RepoNotFoundError):
        await _open(MemorySink(), generate_master_key(), tmp_path / "s3", create=False)


async def test_a_foreign_folder_is_not_taken_over(tmp_path: Path) -> None:
    sink = MemorySink()
    await sink.put_bytes(REPO_MARKER_KEY, b'{"format": "something-else"}')

    with pytest.raises(CloudBackupError):
        await _open(sink, generate_master_key(), tmp_path / "state", create=True)


async def test_a_damaged_pack_fails_the_restore(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    key = generate_master_key()
    await (await _open(sink, key, tmp_path / "state")).backup(engine, manifest.id)
    pack = f"{PACKS_DIR}/{(await sink.list(PACKS_DIR))[0]}"
    data = bytearray(sink.files[pack])
    data[len(data) // 2] ^= 0xFF
    sink.files[pack] = bytes(data)

    fresh = await _open(sink, key, tmp_path / "state2", create=False)
    with pytest.raises(CloudBackupError, match="damaged"):
        await fresh.restore((await fresh.list_snapshots())[0], tmp_path / "back")


async def test_a_manifest_with_an_escaping_path_writes_nothing(tmp_path: Path) -> None:
    sink = MemorySink()
    key = generate_master_key()
    repo = await _open(sink, key, tmp_path / "state")
    evil = SnapshotManifest(
        id="a" * 64,
        created_at_ms=1,
        trigger="cloud",
        files=[FileEntry(path="../fuori.txt", hash="b" * 64, size=1, mtime_ns=0)],
    )
    sealed = await encrypt_container(
        key.hex(), zlib.compress(json.dumps(evil.to_dict()).encode()), iterations=1,
        backend=_BACKEND,
    )
    await sink.put_bytes(snapshot_key(evil), sealed)

    with pytest.raises(CloudBackupError, match="unsafe path"):
        await repo.restore((await repo.list_snapshots())[0], tmp_path / "back" / "ws")
    assert not (tmp_path / "back").exists()


async def test_a_missing_blob_is_reported_before_writing(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    key = generate_master_key()
    await (await _open(sink, key, tmp_path / "state")).backup(engine, manifest.id)
    for name in await sink.list(INDEX_DIR):
        await sink.delete(f"{INDEX_DIR}/{name}")

    fresh = await _open(sink, key, tmp_path / "state2", create=False)
    with pytest.raises(CloudBackupError, match="missing"):
        await fresh.restore((await fresh.list_snapshots())[0], tmp_path / "back")
    assert not (tmp_path / "back").exists()


async def test_works_on_a_real_folder(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    sink = LocalDirSink(tmp_path / "drive")
    key = generate_master_key()
    await (await _open(sink, key, tmp_path / "state")).backup(engine, manifest.id)

    assert (tmp_path / "drive" / "LEGGIMI.txt").is_file()
    fresh = await _open(LocalDirSink(tmp_path / "drive"), key, tmp_path / "s2", create=False)
    await fresh.restore((await fresh.list_snapshots())[0], tmp_path / "back")
    assert _tree(tmp_path / "back") == _tree(tmp_path / "ws")


async def test_state_records_the_last_success(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    repo = await _open(MemorySink(), generate_master_key(), tmp_path / "state")
    assert repo.read_state() == {}

    await repo.backup(engine, manifest.id)
    repo.record_failure("boom")

    state = repo.read_state()
    assert state["last_snapshot_id"] == manifest.id
    assert state["last_success_ms"] > 0
    assert state["last_error"] == "boom"


def test_snapshot_names_and_keys() -> None:
    assert parse_snapshot_name(f"0000000001000-{'c' * 64}.jsn") == (1000, "c" * 64)
    assert parse_snapshot_name("0000000001000-../x.jsn") is None
    assert parse_snapshot_name("note.txt") is None
    assert split_key("packs/a.jpk") == ("packs", "a.jpk")
    for bad in ("", "a/b/c", "../x", "packs/..", "/abs"):
        with pytest.raises(ValueError):
            split_key(bad)


async def test_memory_sink_missing_keys() -> None:
    sink = MemorySink()
    with pytest.raises(RemoteNotFoundError):
        await sink.get_bytes("nope")
    with pytest.raises(RemoteNotFoundError):
        await sink.delete("nope")


async def test_an_index_renamed_onto_another_pack_is_refused(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    sink = MemorySink()
    key = generate_master_key()
    await (await _open(sink, key, tmp_path / "state")).backup(engine, manifest.id)
    (name,) = await sink.list(INDEX_DIR)
    sink.files[f"{INDEX_DIR}/{'d' * 64}.jix"] = sink.files.pop(f"{INDEX_DIR}/{name}")

    fresh = await _open(sink, key, tmp_path / "state2", create=False)
    with pytest.raises(CloudBackupError, match="another pack"):
        await fresh.refresh_index()


async def test_progress_counts_file_bytes_pack_by_pack(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    engine = _workspace(ws)
    for i in range(6):
        (ws / "uploads" / f"f{i}.bin").write_bytes(os.urandom(4_000))
    manifest = engine.create_snapshot(trigger="cloud")
    repo = await _open(MemorySink(), generate_master_key(), tmp_path / "state", pack_target=8_000)
    seen: list[tuple[int, int]] = []

    result = await repo.backup(engine, manifest.id, progress=lambda d, t: seen.append((d, t)))

    unique_bytes = sum({e.hash: e.size for e in manifest.files}.values())
    assert seen[0] == (0, unique_bytes)
    assert seen[-1] == (unique_bytes, unique_bytes)
    assert len(seen) == result.packs_uploaded + 1
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert result.file_count == manifest.file_count
    assert repo.stored_bytes() > 0


async def test_an_already_remote_snapshot_still_reports_its_files(tmp_path: Path) -> None:
    engine = _workspace(tmp_path / "ws")
    manifest = engine.create_snapshot(trigger="cloud")
    repo = await _open(MemorySink(), generate_master_key(), tmp_path / "state")
    await repo.backup(engine, manifest.id)

    again = await repo.backup(engine, manifest.id)

    assert again.already_remote and again.file_count == manifest.file_count
