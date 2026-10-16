"""``DriveSink``: Google Drive come destinazione, contro un Drive finto.

Il finto (``fake_drive.py``) risponde come l'API v3 per quel che il sink usa,
e permette di iniettare 401, 500, rate limit e la rete che cade a metà di un
caricamento ripartibile.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from jenny.snapshot.engine import SnapshotEngine
from jenny_extensions.drive.cloud import CloudRepository, generate_master_key
from jenny_extensions.drive.cloud_drive import DriveConsentError, DriveError, DriveSink
from jenny_extensions.drive.cloud_sink import RemoteNotFoundError

from .fake_drive import FOLDER_MIME, FakeDrive

pytest.importorskip("cryptography")

from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend  # noqa: E402

_CHUNK = 256 << 10


class _Tokens:
    """Token provider finto: ``tok-1``, poi ``tok-2`` a ogni rinnovo forzato."""

    def __init__(self, drive: FakeDrive) -> None:
        self.drive = drive
        self.calls: list[bool] = []
        self.current = "tok-1"

    async def __call__(self, force: bool) -> str:
        self.calls.append(force)
        if force:
            self.current = f"tok-{len(self.calls) + 1}"
        return self.current


def _sink(drive: FakeDrive, **kw) -> tuple[DriveSink, _Tokens, list[float]]:
    tokens = _Tokens(drive)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    sink = DriveSink(
        tokens,
        client=httpx.AsyncClient(transport=drive.transport()),
        chunk_size=kw.pop("chunk_size", _CHUNK),
        resumable_threshold=kw.pop("resumable_threshold", 100_000),
        sleep=sleep,
        **kw,
    )
    return sink, tokens, sleeps


async def test_creates_the_folder_tree_on_first_write(tmp_path: Path) -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive)

    assert await sink.list("packs") == []
    assert not drive.files  # leggere non crea niente
    await sink.put_bytes("packs/a.jpk", b"uno")
    await sink.put_bytes("repo.json", b"{}")

    (root,) = drive.find("Jenny")
    assert root.parents == ["root"] and root.mime == FOLDER_MIME
    (packs,) = drive.find("packs")
    assert packs.parents == [root.id]
    assert drive.find("a.jpk")[0].parents == [packs.id]
    assert await sink.list("packs") == ["a.jpk"]
    assert await sink.list("") == ["repo.json"]  # le cartelle non sono file
    assert await sink.get_bytes("packs/a.jpk") == b"uno"


async def test_putting_an_existing_key_updates_it(tmp_path: Path) -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    await sink.put_bytes("repo.json", b"v1")

    fresh, _, _ = _sink(drive)  # un altro giro: niente cache
    await fresh.put_bytes("repo.json", b"v2")

    assert len(drive.find("repo.json")) == 1
    assert await fresh.get_bytes("repo.json") == b"v2"


async def test_duplicate_folders_resolve_to_the_oldest() -> None:
    drive = FakeDrive()
    old = drive.add("Jenny", ["root"], FOLDER_MIME)
    drive.add("Jenny", ["root"], FOLDER_MIME)
    sink, _, _ = _sink(drive)

    await sink.put_bytes("repo.json", b"x")

    assert drive.find("repo.json")[0].parents == [old.id]


async def test_large_file_goes_up_in_chunks_and_survives_a_dropped_chunk(tmp_path: Path) -> None:
    drive = FakeDrive()
    sink, _, sleeps = _sink(drive)
    data = os.urandom(3 * _CHUNK + 1234)
    source = tmp_path / "big.bin"
    source.write_bytes(data)
    puts = 0

    def drop_second_chunk(request: httpx.Request):
        nonlocal puts
        if request.method == "PUT" and "upload_id" in str(request.url):
            puts += 1
            if puts == 2:
                return httpx.ConnectError("network is unreachable")
        return None

    drive.fail_next(drop_second_chunk)
    await sink.put_file("packs/big.jpk", source)

    assert await sink.get_bytes("packs/big.jpk") == data
    assert len(drive.find("big.jpk")) == 1
    assert sleeps  # ha aspettato prima di ritentare
    ranges = [
        r.headers["Content-Range"] for r in drive.requests
        if r.method == "PUT" and "upload_id" in str(r.url)
    ]
    assert f"bytes */{len(data)}" in ranges  # ha chiesto dov'era arrivato


async def test_an_updated_large_file_keeps_its_id(tmp_path: Path) -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    source = tmp_path / "big.bin"
    source.write_bytes(os.urandom(2 * _CHUNK))
    await sink.put_file("packs/big.jpk", source)
    (first,) = drive.find("big.jpk")

    source.write_bytes(b"z" * (2 * _CHUNK))
    await sink.put_file("packs/big.jpk", source)

    (again,) = drive.find("big.jpk")
    assert again.id == first.id and again.content == b"z" * (2 * _CHUNK)


async def test_an_expired_token_is_refreshed_once() -> None:
    drive = FakeDrive()
    sink, tokens, _ = _sink(drive)
    await sink.put_bytes("repo.json", b"x")
    drive.valid_tokens = {"tok-3"}  # il primo è scaduto, il rinnovo dà tok-3

    assert await sink.get_bytes("repo.json") == b"x"
    assert tokens.calls[-1] is True


async def test_a_revoked_permission_asks_for_consent() -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    drive.valid_tokens = set()

    with pytest.raises(DriveConsentError):
        await sink.list("")


async def test_server_errors_and_rate_limits_are_retried() -> None:
    drive = FakeDrive()
    sink, _, sleeps = _sink(drive)
    drive.fail_next(lambda r: httpx.Response(503) if r.method == "GET" else None)
    drive.fail_next(
        lambda r: httpx.Response(
            403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}
        ) if r.method == "GET" else None
    )

    assert await sink.list("") == []
    assert len(sleeps) == 2


async def test_a_real_permission_error_is_not_retried() -> None:
    drive = FakeDrive()
    sink, _, sleeps = _sink(drive)
    drive.fail_next(
        lambda r: httpx.Response(
            403, json={"error": {"errors": [{"reason": "insufficientFilePermissions"}]}}
        )
    )

    with pytest.raises(DriveError):
        await sink.list("")
    assert sleeps == []


async def test_missing_keys() -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    with pytest.raises(RemoteNotFoundError):
        await sink.get_bytes("repo.json")
    await sink.put_bytes("repo.json", b"x")
    with pytest.raises(RemoteNotFoundError):
        await sink.get_bytes("packs/none.jpk")
    with pytest.raises(RemoteNotFoundError):
        await sink.delete("nope.txt")
    await sink.delete("repo.json")
    assert await sink.list("") == []


async def test_names_with_quotes_are_escaped() -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive, root_name="Jenny's")

    await sink.put_bytes("it's.txt", b"x")

    fresh, _, _ = _sink(drive, root_name="Jenny's")
    assert await fresh.list("") == ["it's.txt"]


async def test_a_whole_repository_roundtrip_over_drive(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    (ws / "MEMORY.md").write_text("ricordi", encoding="utf-8")
    for i in range(6):
        (ws / "memory" / f"f{i}.bin").write_bytes(os.urandom(200_000))
    engine = SnapshotEngine(ws, ws / ".jenny" / "snapshots")
    manifest = engine.create_snapshot(trigger="cloud")
    drive = FakeDrive()
    key = generate_master_key()
    backend = DevAesGcmBackend()

    sink, _, _ = _sink(drive)
    repo = await CloudRepository.open(
        sink, key, tmp_path / "state", create=True, pack_target_bytes=600_000, backend=backend
    )
    result = await repo.backup(engine, manifest.id)
    assert result.packs_uploaded == 2

    other, _, _ = _sink(drive)
    fresh = await CloudRepository.open(other, key, tmp_path / "state2", backend=backend)
    await fresh.restore((await fresh.list_snapshots())[0], tmp_path / "back")
    for path in ws.rglob("*"):
        if path.is_file() and ".jenny" not in path.parts:
            assert (tmp_path / "back" / path.relative_to(ws)).read_bytes() == path.read_bytes()


async def test_a_chunk_received_but_unanswered_is_not_sent_twice(tmp_path: Path) -> None:
    # Il caso cattivo: Drive ha preso il pezzo, la risposta si è persa. Il
    # client deve chiedere dov'è arrivato e ripartire da lì, non dal suo offset.
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    data = os.urandom(3 * _CHUNK)
    source = tmp_path / "big.bin"
    source.write_bytes(data)
    puts = 0

    def swallow_answer(request: httpx.Request):
        nonlocal puts
        if request.method == "PUT" and "upload_id" in str(request.url):
            puts += 1
            if puts == 2:
                drive._handle_unfaulted(request)
                return httpx.ReadTimeout("answer lost")
        return None

    drive.fail_next(swallow_answer)
    await sink.put_file("packs/big.jpk", source)

    assert await sink.get_bytes("packs/big.jpk") == data


async def test_a_large_upload_reports_progress_chunk_by_chunk(tmp_path: Path) -> None:
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    source = tmp_path / "big.bin"
    source.write_bytes(os.urandom(3 * _CHUNK + 10))
    seen: list[tuple[int, int]] = []

    await sink.put_file("packs/big.jpk", source, lambda sent, size: seen.append((sent, size)))

    size = 3 * _CHUNK + 10
    assert [s for s, _ in seen] == [_CHUNK, 2 * _CHUNK, 3 * _CHUNK]
    assert all(t == size for _, t in seen)


async def test_a_single_big_file_moves_the_bar_while_its_pack_goes_up(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "uploads").mkdir(parents=True)
    (ws / "uploads" / "foto.jpg").write_bytes(os.urandom(4 * _CHUNK))
    engine = SnapshotEngine(ws, ws / ".jenny" / "snapshots")
    manifest = engine.create_snapshot(trigger="cloud")
    drive = FakeDrive()
    sink, _, _ = _sink(drive)
    repo = await CloudRepository.open(
        sink, generate_master_key(), tmp_path / "state", create=True, backend=DevAesGcmBackend()
    )
    seen: list[int] = []

    await repo.backup(engine, manifest.id, progress=lambda d, t: seen.append(d))

    total = 4 * _CHUNK
    assert seen[0] == 0 and seen[-1] == total
    assert any(0 < d < total for d in seen), f"la barra salta da 0 a 100: {seen}"
    assert seen == sorted(seen)
