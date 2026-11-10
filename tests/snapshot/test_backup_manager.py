"""Test dei rami di errore e sicurezza di ``BackupManager``.

Copre la validazione security-critical di ``_extract_backup`` (zip-slip,
archivi malformati), la rotazione degli export in staging e i contratti
d'errore a livello manager (le route HTTP hanno test propri).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.config.schema import SnapshotConfig
from jenny.snapshot.backup import BackupError, BackupManager
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.service import SnapshotService
from jenny.utils.locations import (
    STAGED_SNAPSHOTS_DIR_NAME,
    STAGED_WORKSPACE_DIR_NAME,
)

_METADATA = json.dumps({"format_version": 1}).encode("utf-8")


def _make_env(tmp_path: Path) -> SimpleNamespace:
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("anima", encoding="utf-8")
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    service = SnapshotService(engine, SnapshotConfig(pbkdf2_iterations=100_000))
    return SimpleNamespace(
        manager=BackupManager(service),
        engine=engine,
        workspace=workspace,
        runtime_root=runtime_root,
        staging=runtime_root / "backup_staging",
    )


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# -- _extract_backup: validazione dell'archivio decifrato -----------------------


def test_extract_rejects_non_zip(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    with pytest.raises(BackupError, match="not a valid backup archive"):
        env.manager._extract_backup(b"questo non e' uno zip")


def test_extract_requires_metadata(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    payload = _zip_bytes({"tree/SOUL.md": b"x"})
    with pytest.raises(BackupError, match="missing metadata.json"):
        env.manager._extract_backup(payload)


def test_extract_rejects_unreadable_metadata(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    for corrupt in (b"{json corrotto", b"\xff\xfe\x00"):
        payload = _zip_bytes({"metadata.json": corrupt, "tree/SOUL.md": b"x"})
        with pytest.raises(BackupError, match="unreadable"):
            env.manager._extract_backup(payload)


def test_extract_rejects_zip_slip_dotdot(tmp_path: Path) -> None:
    """Un entry con ``..`` non deve mai scrivere fuori dallo staging."""
    env = _make_env(tmp_path)
    payload = _zip_bytes(
        {"metadata.json": _METADATA, "tree/../evasa.txt": b"malizioso"}
    )
    with pytest.raises(BackupError, match="unsafe path"):
        env.manager._extract_backup(payload)
    assert not (env.runtime_root / "evasa.txt").exists()
    assert not (tmp_path / "evasa.txt").exists()


def test_extract_rejects_absolute_path(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    payload = _zip_bytes({"metadata.json": _METADATA, "tree//etc/evasa": b"x"})
    with pytest.raises(BackupError, match="unsafe path"):
        env.manager._extract_backup(payload)


def test_extract_requires_workspace_tree(tmp_path: Path) -> None:
    """Un backup senza ``tree/`` è invalido e lo staging viene ripulito."""
    env = _make_env(tmp_path)
    payload = _zip_bytes({"metadata.json": _METADATA, "snapshots/index.json": b"{}"})
    with pytest.raises(BackupError, match="no workspace tree"):
        env.manager._extract_backup(payload)
    assert not (env.runtime_root / STAGED_WORKSPACE_DIR_NAME).exists()
    assert not (env.runtime_root / STAGED_SNAPSHOTS_DIR_NAME).exists()


def test_extract_happy_path_ignores_stray_entries(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    payload = _zip_bytes(
        {
            "metadata.json": _METADATA,
            "tree/SOUL.md": b"ripristinata",
            "tree/memory/MEMORY.md": b"# memoria",
            "snapshots/index.json": b"{}",
            "estranea.txt": b"ignorata",
        }
    )
    metadata = env.manager._extract_backup(payload)
    assert metadata["format_version"] == 1
    staged_ws = env.runtime_root / STAGED_WORKSPACE_DIR_NAME
    assert (staged_ws / "SOUL.md").read_bytes() == b"ripristinata"
    assert (staged_ws / "memory" / "MEMORY.md").read_bytes() == b"# memoria"
    assert (env.runtime_root / STAGED_SNAPSHOTS_DIR_NAME / "index.json").exists()
    assert not (staged_ws / "estranea.txt").exists()


# -- export: zip e rotazione dello staging ---------------------------------------


def test_build_zip_metadata_and_tree(tmp_path: Path) -> None:
    import jenny

    env = _make_env(tmp_path)
    env.manager._build_zip(tmp_path / "export.zip")
    with zipfile.ZipFile(tmp_path / "export.zip") as archive:
        metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
        assert metadata["format_version"] == 1
        assert metadata["jenny_version"] == jenny.__version__
        assert isinstance(metadata["exported_at_ms"], int)
        assert archive.read("tree/SOUL.md") == b"anima"


def test_jenny_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from jenny.snapshot.backup import _jenny_version

    monkeypatch.delattr("jenny.__version__")
    assert _jenny_version() == "unknown"


async def test_export_replaces_previous_staged_file(tmp_path: Path) -> None:
    """In staging resta al più un export: il nuovo sostituisce il vecchio."""
    pytest.importorskip("cryptography")
    env = _make_env(tmp_path)
    first = await env.manager.export_backup("passphrase")
    (env.workspace / "SOUL.md").write_text("anima 2", encoding="utf-8")
    second = await env.manager.export_backup("passphrase")

    staged = sorted(env.staging.glob("jenny-backup-*.jbk"))
    assert [str(p) for p in staged] == [second["staged_path"]]
    assert not Path(first["staged_path"]).exists() or first["staged_path"] == second[
        "staged_path"
    ]


# -- contratti d'errore a livello manager ----------------------------------------


async def test_export_requires_passphrase(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    with pytest.raises(BackupError, match="passphrase required"):
        await env.manager.export_backup("")


async def test_stage_import_requires_passphrase(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    with pytest.raises(BackupError, match="passphrase required"):
        await env.manager.stage_import(str(env.manager.import_staged_path), "")


async def test_stage_import_rejects_path_outside_staging(tmp_path: Path) -> None:
    """La containment guard scatta prima di qualunque accesso al file."""
    env = _make_env(tmp_path)
    rogue = tmp_path / "rogue.jbk"
    with pytest.raises(BackupError, match="staging directory"):
        await env.manager.stage_import(str(rogue), "x")
