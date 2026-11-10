"""Lo stato in SQLite nel backup e nel ripristino; l'annulla che si svuota.

Il giro intero: lo stato va nel ``.jbk`` e negli snapshot (a pezzi), torna
allo staging, e al boot prende il posto di quello vivo, mentre il diario
dell'annulla riparte vuoto. Poi i casi storti: uno ``state.sqlite`` rotto
nell'archivio, uno snapshot senza stato (che lascia quello di adesso), il giro
dopo un crash a metà.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny import db as jdb
from jenny.config.schema import SnapshotConfig
from jenny.db import shards
from jenny.db.restore import PRE_RESTORE_SUFFIX, UNDO_OBJECTS_DIR_NAME
from jenny.db.schema import Schema
from jenny.snapshot.backup import BACKUP_STATE_ENTRY, BackupError, BackupManager
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.restore_marker import apply_pending_restore, write_marker
from jenny.snapshot.service import SnapshotService
from jenny.utils.locations import STAGED_DB_DIR_NAME, STAGED_WORKSPACE_DIR_NAME

_KV = Schema("kv", ("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT);",))


@pytest.fixture
def env(tmp_path: Path):
    yield from _env(tmp_path, with_state=False)


@pytest.fixture
def state_env(tmp_path: Path):
    yield from _env(tmp_path, with_state=True)


def _env(tmp_path: Path, *, with_state: bool):
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("anima", encoding="utf-8")
    # Come il composition root: lo stato a pezzi in ogni snapshot.
    extra = (lambda: shards.export(jdb.database(runtime_root, jdb.STATE))) if with_state else None
    engine = SnapshotEngine(workspace, runtime_root / "snapshots", extra_entries=extra)
    service = SnapshotService(engine, SnapshotConfig(pbkdf2_iterations=100_000))
    yield SimpleNamespace(
        manager=BackupManager(service), service=service, engine=engine,
        workspace=workspace, runtime_root=runtime_root,
    )
    jdb.close_all()


def _set(root: Path, value: str) -> None:
    state = jdb.database(root, jdb.STATE)
    state.require(_KV)
    with state.write() as conn:
        conn.execute("INSERT INTO kv VALUES ('who', ?) "
                     "ON CONFLICT(k) DO UPDATE SET v = excluded.v", (value,))


def _get(root: Path) -> str:
    state = jdb.database(root, jdb.STATE)
    state.require(_KV)
    return state.read("SELECT v FROM kv WHERE k = 'who'")[0]["v"]


def _dirty_undo(root: Path) -> None:
    jdb.database(root, jdb.UNDO)
    objects = root / "db" / UNDO_OBJECTS_DIR_NAME / "ab"
    objects.mkdir(parents=True)
    (objects / "abcd").write_bytes(b"x")


async def test_the_state_goes_into_the_backup_and_comes_back(env) -> None:
    pytest.importorskip("cryptography")
    _set(env.runtime_root, "dal backup")
    exported = await env.manager.export_backup("passphrase")
    with _plain_zip(env) as archive:
        assert BACKUP_STATE_ENTRY in archive.namelist()
    _set(env.runtime_root, "di adesso")
    _dirty_undo(env.runtime_root)
    shutil.copy(exported["staged_path"], env.manager.import_staged_path)
    await env.manager.stage_import(str(env.manager.import_staged_path), "passphrase")
    assert (env.runtime_root / STAGED_DB_DIR_NAME / "state.sqlite").is_file()

    jdb.close_all()  # il boot: niente è aperto quando si scambia
    assert apply_pending_restore(env.runtime_root)

    assert _get(env.runtime_root) == "dal backup"
    live = env.runtime_root / "db"
    assert (live / f"state.sqlite{PRE_RESTORE_SUFFIX}").is_file()
    assert not (live / "undo.sqlite").exists()
    assert not (live / UNDO_OBJECTS_DIR_NAME).exists()
    assert not (env.runtime_root / STAGED_DB_DIR_NAME).exists()


def test_the_kept_state_expires_with_the_workspace_copy(tmp_path: Path) -> None:
    """Lo ``state.sqlite.pre-restore`` porta la data del ripristino e se ne va
    dopo 7 giorni, con la copia del workspace. Fino al 03/11/2026 restava per
    sempre: sul telefono di prova, 119 MB."""
    import os
    import time

    from jenny.db.restore import apply_staged
    from jenny.snapshot.restore_marker import sweep_safety_copies

    live = tmp_path / "db"
    staged = tmp_path / STAGED_DB_DIR_NAME
    for folder in (live, staged):
        folder.mkdir()
        (folder / "state.sqlite").write_bytes(b"SQLite format 3\x00" + folder.name.encode())
    week_ago = time.time() - 30 * 86_400
    os.utime(live / "state.sqlite", (week_ago, week_ago))
    assert apply_staged(tmp_path)
    kept = live / f"state.sqlite{PRE_RESTORE_SUFFIX}"
    assert kept.is_file() and kept.stat().st_mtime > time.time() - 60, "la data del ripristino"

    sweep_safety_copies(tmp_path, max_age_days=7)
    assert kept.is_file(), "un ripristino di oggi si tiene"
    old = time.time() - 8 * 86_400
    os.utime(kept, (old, old))
    sweep_safety_copies(tmp_path, max_age_days=7)
    assert not kept.exists()
    assert (live / "state.sqlite").is_file()


def _plain_zip(env) -> zipfile.ZipFile:
    dest = env.runtime_root / "plain.zip"
    env.manager._build_zip(dest)
    return zipfile.ZipFile(dest)


def test_a_damaged_state_in_the_archive_fails_the_import(env) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"format_version": 1}))
        archive.writestr("tree/SOUL.md", b"x")
        archive.writestr(BACKUP_STATE_ENTRY, b"SQLite format 3\x00" + b"\x00" * 100)
    with pytest.raises(BackupError, match="state.sqlite"):
        env.manager._extract_backup(payload.getvalue())
    assert not (env.runtime_root / STAGED_DB_DIR_NAME).exists()
    assert not (env.runtime_root / STAGED_WORKSPACE_DIR_NAME).exists()


async def test_a_snapshot_without_state_keeps_the_state_but_empties_the_undo(env) -> None:
    """Uno snapshot di prima che lo stato fosse in SQLite: i file che riporta li
    fonde la migrazione, lo stato di adesso resta."""
    _set(env.runtime_root, "di adesso")
    manifest = await env.service.snapshot_now("manual")
    assert manifest is not None
    _dirty_undo(env.runtime_root)
    await env.manager.stage_snapshot_restore(manifest.id)
    jdb.close_all()
    assert apply_pending_restore(env.runtime_root)
    assert _get(env.runtime_root) == "di adesso"
    assert not (env.runtime_root / "db" / UNDO_OBJECTS_DIR_NAME).exists()


def test_after_a_crash_past_the_swap_the_state_still_comes_in(env) -> None:
    """Il workspace era già scambiato, il marker c'è ancora: i database si
    scambiano al giro dopo."""
    _set(env.runtime_root, "dal backup")
    staged = env.runtime_root / STAGED_DB_DIR_NAME
    jdb.database(env.runtime_root, jdb.STATE).backup_to(staged / "state.sqlite")
    _set(env.runtime_root, "di adesso")
    jdb.close_all()
    write_marker(env.runtime_root, source="backup_file")
    assert not apply_pending_restore(env.runtime_root)  # nessuno scambio del workspace
    assert _get(env.runtime_root) == "dal backup"


async def test_a_failed_fill_leaves_no_staged_state(env) -> None:
    staged = env.runtime_root / STAGED_DB_DIR_NAME
    staged.mkdir(parents=True)
    (staged / "state.sqlite").write_bytes(b"vecchio")

    async def fill(_tree: Path) -> None:
        raise RuntimeError("Drive irraggiungibile")

    with pytest.raises(RuntimeError):
        await env.service.stage_restore(fill, source="test")
    assert not staged.exists()


async def test_a_snapshot_carries_the_state_and_brings_it_back(state_env) -> None:
    env = state_env
    _set(env.runtime_root, "nello snapshot")
    manifest = await env.service.snapshot_now("manual")
    assert manifest is not None
    assert any(e.path == ".jenny-state/kv/all.jsonl" for e in manifest.files)
    # I conteggi che la casa mostra sono quelli dei file del workspace.
    assert manifest.file_count == 1
    _set(env.runtime_root, "di adesso")
    _dirty_undo(env.runtime_root)
    await env.manager.stage_snapshot_restore(manifest.id)
    # I pezzi non restano nell'albero: non sono file del workspace.
    assert not (env.runtime_root / STAGED_WORKSPACE_DIR_NAME / ".jenny-state").exists()
    jdb.close_all()
    assert apply_pending_restore(env.runtime_root)
    assert _get(env.runtime_root) == "nello snapshot"
    assert not (env.workspace / ".jenny-state").exists()
    assert not (env.runtime_root / "db" / UNDO_OBJECTS_DIR_NAME).exists()


async def test_a_still_state_makes_no_new_snapshot(state_env) -> None:
    env = state_env
    _set(env.runtime_root, "uno")
    first = await env.service.snapshot_now("manual")
    assert first is not None
    assert await env.service.snapshot_now("manual") is None
    _set(env.runtime_root, "due")
    second = await env.service.snapshot_now("manual")
    assert second is not None
    piece = {e.path: e.hash for e in second.files}
    assert piece[".jenny-state/kv/all.jsonl"] != {
        e.path: e.hash for e in first.files}[".jenny-state/kv/all.jsonl"]


async def test_a_real_jenny_state_folder_is_never_tracked(state_env) -> None:
    env = state_env
    _set(env.runtime_root, "vero")
    fake = env.workspace / ".jenny-state" / "kv"
    fake.mkdir(parents=True)
    (fake / "all.jsonl").write_text("finto")
    manifest = await env.service.snapshot_now("manual")
    assert manifest is not None
    entries = {e.path: e.size for e in manifest.files}
    assert entries[".jenny-state/kv/all.jsonl"] != len("finto")


async def test_a_failed_export_still_makes_the_snapshot(env, monkeypatch) -> None:
    def broken() -> list[tuple[str, bytes]]:
        raise RuntimeError("database rotto")

    monkeypatch.setattr(env.engine, "_extra_entries", broken)
    manifest = await env.service.snapshot_now("manual")
    assert manifest is not None
    assert [e.path for e in manifest.files] == ["SOUL.md"]


async def test_a_restore_takes_the_plans_away_with_their_diary(state_env) -> None:
    """I piani stanno in ``undo.sqlite`` col diario: un ripristino li svuota
    insieme, e nessun piano resta a promettere un «torna a prima»."""
    from jenny.plan.model import validate_proposal
    from jenny.plan.store import PlanStore

    env = state_env
    store = PlanStore.for_workspace(env.workspace)
    store.create("piante", validate_proposal(
        "Rifare", [{"title": t, "detail": "x", "done_when": "x",
                    "kind": "check" if t == "Due" else "other"} for t in ("Uno", "Due")],
        [{"item": "Acero", "have": "", "missing": "", "strategy": "notebook"}]))
    manifest = await env.service.snapshot_now("manual")
    await env.manager.stage_snapshot_restore(manifest.id)
    jdb.close_all()
    assert apply_pending_restore(env.runtime_root)
    assert PlanStore.for_workspace(env.workspace).list_all() == []
