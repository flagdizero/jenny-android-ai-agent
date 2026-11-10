"""Un manifest che arriva da un ``.jbk`` e' dato non fidato.

La guardia zip-slip di ``_extract_backup`` controlla i nomi **dello zip**, non i
percorsi scritti **dentro** i manifest dello store importato. Quei manifest
entrano nella storia locale al boot (``_merge_snapshot_store``), e ripristinarne
uno scriveva ogni ``entry.path`` sotto lo staging senza guardarlo: un ``..``
usciva dal workspace.
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
from jenny.snapshot.store import put_blob
from jenny.utils.locations import STAGED_SNAPSHOTS_DIR_NAME

_HASH = "a" * 64


def _env(tmp_path: Path) -> SimpleNamespace:
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("anima", encoding="utf-8")
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    service = SnapshotService(engine, SnapshotConfig(pbkdf2_iterations=100_000))
    return SimpleNamespace(manager=BackupManager(service), engine=engine,
                           runtime_root=runtime_root, tmp=tmp_path)


def _manifest(path: str, hash_hex: str = _HASH, snapshot_id: str = "b" * 64) -> bytes:
    return json.dumps({
        "id": snapshot_id, "created_at_ms": 1, "trigger": "manual",
        "files": [{"path": path, "hash": hash_hex, "size": 1, "mtime_ns": 0}],
    }).encode("utf-8")


def _archive(manifest: bytes, name: str = f"manifests/{'b' * 64}.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("metadata.json", b'{"format_version": 1}')
        archive.writestr("tree/SOUL.md", b"anima")
        archive.writestr(f"snapshots/{name}", manifest)
    return buffer.getvalue()


@pytest.mark.parametrize("bad", ["../fuori.txt", "a/../../fuori.txt", "/etc/passwd", "", "a//b"])
def test_an_imported_manifest_with_an_unsafe_path_is_refused(tmp_path, bad) -> None:
    env = _env(tmp_path)

    with pytest.raises(BackupError, match="unsafe"):
        env.manager._extract_backup(_archive(_manifest(bad)))

    assert not (env.runtime_root / STAGED_SNAPSHOTS_DIR_NAME).exists()


def test_an_imported_manifest_with_a_hash_that_is_not_a_hash_is_refused(tmp_path) -> None:
    env = _env(tmp_path)

    with pytest.raises(BackupError, match="unsafe"):
        env.manager._extract_backup(_archive(_manifest("ok.txt", hash_hex="../../x")))


def test_a_sane_imported_manifest_is_staged(tmp_path) -> None:
    env = _env(tmp_path)

    env.manager._extract_backup(_archive(_manifest("memory/MEMORY.md")))

    assert (env.runtime_root / STAGED_SNAPSHOTS_DIR_NAME / "manifests").is_dir()


def test_restore_snapshot_refuses_a_path_outside_the_destination(tmp_path) -> None:
    """La guardia sul posto: anche un manifest arrivato da un'altra strada."""
    env = _env(tmp_path)
    content = b"payload"
    hash_hex = put_blob(env.engine.objects_dir, content)
    env.engine.manifests_dir.mkdir(parents=True, exist_ok=True)
    (env.engine.manifests_dir / f"{'b' * 64}.json").write_bytes(
        _manifest("../../fuori.txt", hash_hex=hash_hex)
    )
    dest = tmp_path / "staged" / "workspace"

    with pytest.raises(ValueError, match="unsafe"):
        env.engine.restore_snapshot("b" * 64, dest)

    assert not (tmp_path / "fuori.txt").exists()
    assert not list(tmp_path.rglob("fuori.txt"))


def test_restore_snapshot_refuses_a_path_that_a_link_takes_outside(tmp_path) -> None:
    """``link/dato.txt`` e' una stringa sana: nessun ``..``, nessun assoluto. Se
    nella destinazione ``link`` e' un collegamento verso fuori, scriverci vuol
    dire scrivere fuori — e questo lo vede solo il confronto sul percorso
    risolto, non il controllo sulla stringa."""
    env = _env(tmp_path)
    hash_hex = put_blob(env.engine.objects_dir, b"payload")
    env.engine.manifests_dir.mkdir(parents=True, exist_ok=True)
    (env.engine.manifests_dir / f"{'b' * 64}.json").write_bytes(
        _manifest("link/dato.txt", hash_hex=hash_hex)
    )
    outside = tmp_path / "fuori"
    outside.mkdir()
    dest = tmp_path / "staged" / "workspace"
    dest.mkdir(parents=True)
    (dest / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="leaves the destination"):
        env.engine.restore_snapshot("b" * 64, dest)

    assert list(outside.iterdir()) == []


# ---------------------------------------------------------------------------
# L'id del manifest: finisce in un percorso, quindi e' dato non fidato anche lui
# ---------------------------------------------------------------------------
#
# ``apply_retention`` cancella ``manifests/<id>.json`` con l'id letto **dentro**
# il manifest: un id ``../../workspace/config`` importato con un ``.jbk`` faceva
# cancellare ``config.json`` alla prima passata della retention.

_EVIL_ID = "../../workspace/config"


@pytest.mark.parametrize(
    ("snapshot_id", "name"),
    [
        (_EVIL_ID, f"manifests/{'b' * 64}.json"),
        ("B" * 64, f"manifests/{'B' * 64}.json"),
        ("c" * 64, f"manifests/{'b' * 64}.json"),
        ("b" * 64, f"manifests/sotto/{'b' * 64}.json"),
    ],
)
def test_an_imported_manifest_whose_id_is_not_its_file_name_is_refused(
    tmp_path, snapshot_id: str, name: str
) -> None:
    env = _env(tmp_path)

    with pytest.raises(BackupError, match="snapshot id"):
        env.manager._extract_backup(
            _archive(_manifest("ok.txt", snapshot_id=snapshot_id), name=name)
        )

    assert not (env.runtime_root / STAGED_SNAPSHOTS_DIR_NAME).exists()


def _plant(engine: SnapshotEngine, file_id: str, snapshot_id: str, created_at_ms: int = 1) -> None:
    engine.manifests_dir.mkdir(parents=True, exist_ok=True)
    (engine.manifests_dir / f"{file_id}.json").write_text(json.dumps({
        "id": snapshot_id, "created_at_ms": created_at_ms, "trigger": "auto", "files": [],
    }), encoding="utf-8")


def test_retention_never_deletes_outside_the_manifests(tmp_path) -> None:
    """Il manifest arrivato per un'altra strada — lo store fuso al boot, un file
    messo a mano — non entra nella storia, e la retention non lo segue fuori."""
    env = _env(tmp_path)
    config = env.runtime_root / "workspace" / "config.json"
    config.write_text('{"segreto": 1}', encoding="utf-8")
    env.engine.create_snapshot(trigger="manual")
    _plant(env.engine, "d" * 64, _EVIL_ID)

    assert _EVIL_ID not in [s["id"] for s in env.engine.list_snapshots()]
    env.engine.apply_retention(keep_recent=1, thin_after_days=7, max_age_days=30)

    assert config.read_text(encoding="utf-8") == '{"segreto": 1}'


def test_retention_ignores_an_index_row_whose_id_is_not_an_id(tmp_path) -> None:
    """L'indice e' una cache dei manifest: una riga con un id che non e' un id
    non viene da questo motore, e la retention non la segue fuori dallo store."""
    env = _env(tmp_path)
    config = env.runtime_root / "workspace" / "config.json"
    config.write_text('{"segreto": 1}', encoding="utf-8")
    first = env.engine.create_snapshot(trigger="manual", now_ms=10_000)
    assert first is not None
    _plant(env.engine, "e" * 64, "e" * 64, created_at_ms=2)
    # Tante righe quanti manifest, cosi' ``_load_index`` si fida dell'indice.
    env.engine.index_path.write_text(json.dumps({"version": 1, "snapshots": [
        {"id": _EVIL_ID, "created_at_ms": 1, "trigger": "auto"},
        first.summary(),
    ]}), encoding="utf-8")

    assert _EVIL_ID not in [s["id"] for s in env.engine.list_snapshots()]
    env.engine.apply_retention(
        keep_recent=1, thin_after_days=0, max_age_days=1, now_ms=10 * 86_400_000
    )

    assert config.read_text(encoding="utf-8") == '{"segreto": 1}'


def test_load_manifest_refuses_an_id_that_is_not_an_id(tmp_path) -> None:
    """``stage_snapshot_restore`` riceve l'id dalla WebUI: ``../../x`` leggeva
    un JSON qualunque fuori dallo store."""
    env = _env(tmp_path)
    # La cartella deve esistere: ``manifests/..`` non si attraversa altrimenti.
    env.engine.manifests_dir.mkdir(parents=True)
    outside = env.runtime_root / "workspace" / "config.json"
    outside.write_text(json.dumps({"id": "x", "created_at_ms": 1, "files": []}), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        env.engine.load_manifest(_EVIL_ID)


def test_load_manifest_refuses_a_file_that_names_another_snapshot(tmp_path) -> None:
    env = _env(tmp_path)
    _plant(env.engine, "d" * 64, "f" * 64)

    with pytest.raises(ValueError, match="snapshot id"):
        env.engine.load_manifest("d" * 64)
