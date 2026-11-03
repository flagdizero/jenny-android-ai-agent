"""Lo stato in SQLite nel backup e nel ripristino; l'annulla che si svuota.

Il giro intero: lo stato va nel ``.jbk``, torna allo staging, e al boot prende
il posto di quello vivo, mentre il diario dell'annulla riparte vuoto. Poi i
casi storti: uno ``state.sqlite`` rotto nell'archivio, un ripristino dalla
storia locale (che lo stato non lo porta), il giro dopo un crash a metà.
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
from jenny.db.restore import PRE_RESTORE_SUFFIX, UNDO_OBJECTS_DIR_NAME
from jenny.db.schema import Schema
from jenny.snapshot.backup import BACKUP_STATE_ENTRY, BackupError, BackupManager
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.locations import STAGED_DB_DIR_NAME, STAGED_WORKSPACE_DIR_NAME
from jenny.snapshot.restore_marker import apply_pending_restore, write_marker
from jenny.snapshot.service import SnapshotService

_KV = Schema("kv", ("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT);",))


@pytest.fixture
def env(tmp_path: Path):
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("anima", encoding="utf-8")
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
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


async def test_a_restore_from_history_keeps_the_state_but_empties_the_undo(env) -> None:
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
