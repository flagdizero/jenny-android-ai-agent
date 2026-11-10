"""Test di hardening del sottosistema backup/snapshot.

Copre gli scenari ostili e i percorsi completi che i test unitari non
esercitano: container manomessi (bomba KDF), zip-slip, archivi malformati,
e il ciclo di vita intero export → import → swap al boot con verifica
byte-per-byte del workspace ripristinato.
"""

from __future__ import annotations

import io
import json
import os
import struct
import zipfile
from pathlib import Path

import pytest

from jenny.config.schema import SnapshotConfig
from jenny.snapshot.backup import BackupError, BackupManager
from jenny.snapshot.crypto import (
    HEADER_LEN,
    MAGIC,
    MAX_KDF_ITERATIONS,
    decrypt_container,
    encrypt_container,
)
from jenny.snapshot.crypto_backends.base import CryptoAuthError
from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.restore_marker import apply_pending_restore
from jenny.snapshot.service import SnapshotService
from jenny.utils.locations import (
    MARKER_FILE_NAME,
    STAGED_SNAPSHOTS_DIR_NAME,
    STAGED_WORKSPACE_DIR_NAME,
)

pytest.importorskip("cryptography")

_BACKEND = DevAesGcmBackend()
_ITER = 1_000  # KDF veloce nei test; il default di produzione ha il suo KAT.
_PASSPHRASE = "pass🔑frase àè"


def _make_manager(runtime_root: Path) -> BackupManager:
    workspace = runtime_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    service = SnapshotService(engine, SnapshotConfig(pbkdf2_iterations=100_000))
    return BackupManager(service)


def _patch_iterations(container: bytes, iterations: int) -> bytes:
    offset = len(MAGIC) + 1
    return container[:offset] + struct.pack(">I", iterations) + container[offset + 4 :]


async def _container_with_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return await encrypt_container(
        _PASSPHRASE, buffer.getvalue(), iterations=_ITER, backend=_BACKEND
    )


async def _stage(manager: BackupManager, container: bytes):
    manager.import_staged_path.parent.mkdir(parents=True, exist_ok=True)
    manager.import_staged_path.write_bytes(container)
    return await manager.stage_import(str(manager.import_staged_path), _PASSPHRASE)


# -- container manomessi -----------------------------------------------------


async def test_kdf_iteration_bomb_rejected_before_kdf(tmp_path: Path) -> None:
    """iterations=2^32-1 nell'header deve fallire subito, non dopo ore di PBKDF2."""
    container = await encrypt_container(
        _PASSPHRASE, b"payload", iterations=_ITER, backend=_BACKEND
    )
    bomb = _patch_iterations(container, 0xFFFFFFFF)
    with pytest.raises(CryptoAuthError, match="implausible"):
        await decrypt_container(_PASSPHRASE, bomb, backend=_BACKEND)


async def test_zero_iterations_rejected_as_auth_error(tmp_path: Path) -> None:
    """iterations=0 è corruzione, non un ValueError di hashlib (→ HTTP 400, non 500)."""
    container = await encrypt_container(
        _PASSPHRASE, b"payload", iterations=_ITER, backend=_BACKEND
    )
    zeroed = _patch_iterations(container, 0)
    with pytest.raises(CryptoAuthError):
        await decrypt_container(_PASSPHRASE, zeroed, backend=_BACKEND)


async def test_max_iterations_boundary_accepted() -> None:
    """Il valore esattamente al tetto resta un header valido (fallirà solo il tag)."""
    container = await encrypt_container(
        _PASSPHRASE, b"payload", iterations=_ITER, backend=_BACKEND
    )
    patched = _patch_iterations(container, MAX_KDF_ITERATIONS + 1)
    with pytest.raises(CryptoAuthError, match="implausible"):
        await decrypt_container(_PASSPHRASE, patched, backend=_BACKEND)


async def test_encrypt_rejects_out_of_range_iterations() -> None:
    with pytest.raises(ValueError):
        await encrypt_container(_PASSPHRASE, b"x", iterations=0, backend=_BACKEND)
    with pytest.raises(ValueError):
        await encrypt_container(
            _PASSPHRASE, b"x", iterations=MAX_KDF_ITERATIONS + 1, backend=_BACKEND
        )


async def test_header_only_container_rejected() -> None:
    """Header valido ma ciphertext vuoto: mai un crash, solo CryptoAuthError."""
    container = await encrypt_container(
        _PASSPHRASE, b"payload", iterations=_ITER, backend=_BACKEND
    )
    with pytest.raises(CryptoAuthError):
        await decrypt_container(_PASSPHRASE, container[:HEADER_LEN], backend=_BACKEND)
    with pytest.raises(CryptoAuthError):
        await decrypt_container(_PASSPHRASE, container[: HEADER_LEN + 5], backend=_BACKEND)


# -- archivi ostili/malformati -------------------------------------------------


async def test_zip_slip_relative_traversal_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    manager = _make_manager(tmp_path / "data")
    container = await _container_with_zip(
        {
            "metadata.json": b"{}",
            "tree/ok.txt": b"ok",
            "tree/../../evil.txt": b"evil",
        }
    )
    with pytest.raises(BackupError, match="unsafe path"):
        await _stage(manager, container)
    # Nulla è sfuggito dallo staging e il restore non è stato impegnato.
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "data" / "evil.txt").exists()
    assert not (tmp_path / "data" / MARKER_FILE_NAME).exists()


async def test_zip_slip_absolute_path_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    manager = _make_manager(tmp_path / "data")
    container = await _container_with_zip(
        {
            "metadata.json": b"{}",
            "tree//tmp/evil_abs.txt": b"evil",
        }
    )
    with pytest.raises(BackupError):
        await _stage(manager, container)
    assert not Path("/tmp/evil_abs.txt").exists()
    assert not (tmp_path / "data" / MARKER_FILE_NAME).exists()


async def test_archive_without_tree_rejected_and_staging_cleaned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    manager = _make_manager(tmp_path / "data")
    container = await _container_with_zip({"metadata.json": b"{}"})
    with pytest.raises(BackupError, match="no workspace tree"):
        await _stage(manager, container)
    assert not (tmp_path / "data" / STAGED_WORKSPACE_DIR_NAME).exists()
    assert not (tmp_path / "data" / MARKER_FILE_NAME).exists()


async def test_archive_missing_metadata_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    manager = _make_manager(tmp_path / "data")
    container = await _container_with_zip({"tree/file.txt": b"x"})
    with pytest.raises(BackupError, match="metadata.json"):
        await _stage(manager, container)


async def test_decrypted_garbage_rejected(tmp_path: Path, monkeypatch) -> None:
    """Passphrase giusta ma payload che non è uno zip: BackupError, non crash."""
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    manager = _make_manager(tmp_path / "data")
    container = await encrypt_container(
        _PASSPHRASE, b"non sono uno zip", iterations=_ITER, backend=_BACKEND
    )
    with pytest.raises(BackupError, match="not a valid backup archive"):
        await _stage(manager, container)


async def test_extraneous_zip_entries_ignored(tmp_path: Path, monkeypatch) -> None:
    """Voci fuori da tree/ e snapshots/ vengono ignorate, non estratte."""
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    manager = _make_manager(tmp_path / "data")
    container = await _container_with_zip(
        {
            "metadata.json": b"{}",
            "tree/keep.txt": b"keep",
            "sneaky.txt": b"nope",
            "other/dir/file.txt": b"nope",
        }
    )
    result = await _stage(manager, container)
    assert result["metadata"] == {}
    staged = tmp_path / "data" / STAGED_WORKSPACE_DIR_NAME
    assert (staged / "keep.txt").read_bytes() == b"keep"
    assert not (staged / "sneaky.txt").exists()
    assert not (tmp_path / "data" / "sneaky.txt").exists()


# -- ciclo di vita completo ------------------------------------------------------


async def test_full_lifecycle_byte_fidelity(tmp_path: Path, monkeypatch) -> None:
    """Export → modifica il workspace → import → swap al boot: albero identico.

    Copre insieme: dedup dei contenuti, nomi unicode, file vuoti, blob binari,
    sotto-directory annidate, swap della storia importata, pulizia di marker e
    sanity file, safety copy dello stato pre-restore.
    """
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    (workspace / "memory" / "nested" / "deep").mkdir(parents=True)
    original = {
        "SOUL.md": "anima àèìòù".encode(),
        "memory/MEMORY.md": b"# memoria",
        "memory/nested/deep/file.json": json.dumps({"k": [1, 2, 3]}).encode(),
        "memory/vuoto.txt": b"",
        "memory/nested/nòme émoji \U0001f512.md": "contenuto 🔒".encode(),
        "media/blob.bin": os.urandom(256 * 1024),
    }
    for rel, content in original.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    manager = _make_manager(runtime_root)
    # Una storia pre-esistente che il backup deve trasportare.
    manager.list_snapshots()  # engine pronto
    first = await manager.create_snapshot(label="pre-export")
    assert first is not None

    exported = await manager.export_backup(_PASSPHRASE)
    exported_file = Path(exported["staged_path"])
    assert exported_file.is_file()

    # Il mondo va avanti: il workspace diverge dallo stato esportato.
    (workspace / "SOUL.md").write_bytes(b"CORROTTA DOPO")
    (workspace / "memory" / "MEMORY.md").unlink()
    (workspace / "nuovo.txt").write_bytes(b"nato dopo l'export")

    manager.import_staged_path.parent.mkdir(parents=True, exist_ok=True)
    manager.import_staged_path.write_bytes(exported_file.read_bytes())
    result = await manager.stage_import(str(manager.import_staged_path), _PASSPHRASE)
    assert result["metadata"]["format_version"] == 1
    assert (runtime_root / STAGED_SNAPSHOTS_DIR_NAME).is_dir()

    assert apply_pending_restore(runtime_root) is True

    # Fedeltà byte-per-byte dell'albero ripristinato.
    for rel, content in original.items():
        assert (workspace / rel).read_bytes() == content, f"mismatch: {rel}"
    assert not (workspace / "nuovo.txt").exists()

    # Marker e sanity file consumati; storia importata attiva.
    assert not (runtime_root / MARKER_FILE_NAME).exists()
    assert not any(workspace.rglob(".jenny_restore_manifest.json"))
    restored_engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    history = restored_engine.list_snapshots()
    ids = {s["id"] for s in history}
    assert first["id"] in ids
    # Unione additiva: anche il pre_restore locale (scattato allo staging,
    # con lo stato divergente) resta in storia — è la via di ritorno in UI.
    assert any(s["trigger"] == "pre_restore" for s in history)

    # Lo stato divergente sopravvive nella safety copy.
    safety_dirs = [p for p in runtime_root.glob("workspace_pre_restore_*") if p.is_dir()]
    assert len(safety_dirs) == 1
    assert (safety_dirs[0] / "SOUL.md").read_bytes() == b"CORROTTA DOPO"
    assert (safety_dirs[0] / "nuovo.txt").is_file()

    # Idempotenza: un secondo boot non trova nulla da fare.
    assert apply_pending_restore(runtime_root) is False
    assert (workspace / "SOUL.md").read_bytes() == original["SOUL.md"]

    # Con la storia a unione l'ultimo snapshot è il pre_restore (stato
    # divergente), quindi uno snapshot del tree ripristinato NON è un no-op:
    # fotografa il ritorno alla testa importata e riparte da lì.
    rebased = restored_engine.create_snapshot(trigger="manual")
    assert rebased is not None and rebased.parent in ids
    # Da fermo, ora sì che è un no-op.
    assert restored_engine.create_snapshot(trigger="manual") is None
    # Dopo una modifica la storia continua sul nuovo ramo.
    (workspace / "SOUL.md").write_bytes(b"nuova vita")
    post = restored_engine.create_snapshot(trigger="manual")
    assert post is not None and post.parent == rebased.id


async def test_snapshot_restore_full_cycle(tmp_path: Path, monkeypatch) -> None:
    """Restore da storia locale: staging dallo snapshot + swap, storia intatta."""
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "stato.txt").write_text("versione 1", encoding="utf-8")

    manager = _make_manager(runtime_root)
    v1 = await manager.create_snapshot(label="v1")
    assert v1 is not None
    (workspace / "stato.txt").write_text("versione 2", encoding="utf-8")
    v2 = await manager.create_snapshot(label="v2")
    assert v2 is not None
    # Modifica NON ancora snapshottata: il pre_restore deve fotografarla.
    (workspace / "stato.txt").write_text("versione 2 sporca", encoding="utf-8")

    await manager.stage_snapshot_restore(v1["id"])
    assert apply_pending_restore(runtime_root) is True
    assert (workspace / "stato.txt").read_text("utf-8") == "versione 1"

    # La storia locale è sopravvissuta allo swap (vive fuori dal workspace):
    # v1, v2 e il pre_restore con lo stato sporco sono tutti recuperabili.
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    ids = {s["id"] for s in engine.list_snapshots()}
    assert {v1["id"], v2["id"]} <= ids
    pre = [s for s in engine.list_snapshots() if s["trigger"] == "pre_restore"]
    assert len(pre) == 1
    recovered = runtime_root / "recovered"
    engine.restore_snapshot(pre[0]["id"], recovered)
    assert (recovered / "stato.txt").read_text("utf-8") == "versione 2 sporca"


async def test_concurrent_exports_serialize(tmp_path: Path, monkeypatch) -> None:
    """Due export concorrenti non si corrompono: vince l'ultimo, un solo file."""
    import asyncio

    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "f.txt").write_text("x", encoding="utf-8")
    manager = _make_manager(runtime_root)

    results = await asyncio.gather(
        manager.export_backup(_PASSPHRASE), manager.export_backup(_PASSPHRASE)
    )
    staging = manager.import_staged_path.parent
    exports = list(staging.glob("jenny-backup-*.jbk"))
    assert len(exports) == 1
    # Il file superstite è uno dei due dichiarati e decifrabile.
    assert str(exports[0]) in {r["staged_path"] for r in results}
    plain = await decrypt_container(_PASSPHRASE, exports[0].read_bytes(), backend=_BACKEND)
    assert zipfile.ZipFile(io.BytesIO(plain)).testzip() is None


async def test_export_survives_vanishing_file(tmp_path: Path, monkeypatch) -> None:
    """Un file che sparisce tra scan e zip non fa fallire l'export."""
    monkeypatch.setattr(
        "jenny.snapshot.crypto.get_crypto_backend", lambda: _BACKEND
    )
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "stabile.txt").write_text("resto", encoding="utf-8")
    (workspace / "effimero.txt").write_text("sparisco", encoding="utf-8")
    manager = _make_manager(runtime_root)

    real_iter = manager._engine.iter_tracked_files

    def vanishing_iter():
        for rel, full in real_iter():
            if rel == "effimero.txt":
                full.unlink(missing_ok=True)
            yield rel, full

    monkeypatch.setattr(manager._engine, "iter_tracked_files", vanishing_iter)
    result = await manager.export_backup(_PASSPHRASE)
    plain = await decrypt_container(
        _PASSPHRASE, Path(result["staged_path"]).read_bytes(), backend=_BACKEND
    )
    names = zipfile.ZipFile(io.BytesIO(plain)).namelist()
    assert "tree/stabile.txt" in names
    assert "tree/effimero.txt" not in names
