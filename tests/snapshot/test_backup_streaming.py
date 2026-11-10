"""Export e import di un backup che non sta in memoria.

Sul telefono l'export falliva con un workspace di 310 MB (201 di foto della
chat): zip e container interi in RAM, e un solo ``doFinal`` javax su un heap
Java da 256 MB. Qui si guarda la forma del lavoro — segmenti limitati,
temporanei su disco e poi tolti, foto non ricompresse — con un file di pochi MB.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from jenny.config.schema import SnapshotConfig
from jenny.snapshot.backup import BackupManager
from jenny.snapshot.crypto import (
    DEFAULT_SEGMENT_SIZE,
    MAGIC,
    SEGMENTED_FORMAT_VERSION,
    TAG_LEN,
    decrypt_file,
)
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.service import SnapshotService
from jenny.utils.locations import STAGED_WORKSPACE_DIR_NAME

pytest.importorskip("cryptography")

from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend  # noqa: E402

_PASS = "passphrase"
_PHOTO = ".jenny/media/websocket/foto.jpg"


class _RecordingBackend(DevAesGcmBackend):
    def __init__(self) -> None:
        self.sizes: list[int] = []

    async def encrypt(self, key, nonce, plaintext, aad):
        self.sizes.append(len(plaintext))
        return await super().encrypt(key, nonce, plaintext, aad)

    async def decrypt(self, key, nonce, ciphertext, aad):
        self.sizes.append(len(ciphertext))
        return await super().decrypt(key, nonce, ciphertext, aad)


@pytest.fixture()
def backend(monkeypatch: pytest.MonkeyPatch) -> _RecordingBackend:
    recording = _RecordingBackend()
    monkeypatch.setattr("jenny.snapshot.crypto.get_crypto_backend", lambda: recording)
    return recording


def _manager(runtime_root: Path) -> BackupManager:
    workspace = runtime_root / "workspace"
    photo = workspace / _PHOTO
    photo.parent.mkdir(parents=True)
    # Casuale, cioè incomprimibile come un JPEG vero.
    photo.write_bytes(os.urandom(3 * DEFAULT_SEGMENT_SIZE + 123))
    (workspace / "memory").mkdir()
    (workspace / "memory" / "MEMORY.md").write_text("ricordo " * 500, encoding="utf-8")
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    return BackupManager(SnapshotService(engine, SnapshotConfig(pbkdf2_iterations=100_000)))


async def test_export_is_segmented_and_leaves_only_the_backup(
    tmp_path: Path, backend: _RecordingBackend
) -> None:
    manager = _manager(tmp_path / "data")

    result = await manager.export_backup(_PASS)

    staged = Path(result["staged_path"])
    head = staged.read_bytes()[: len(MAGIC) + 1]
    assert head == MAGIC + bytes([SEGMENTED_FORMAT_VERSION])
    assert result["size_bytes"] == staged.stat().st_size
    # Nessuna chiamata al backend ha visto più di un segmento.
    assert len(backend.sizes) >= 4
    assert max(backend.sizes) <= DEFAULT_SEGMENT_SIZE
    # In staging resta il backup e basta: lo zip in chiaro è stato tolto.
    assert sorted(p.name for p in staged.parent.iterdir()) == [staged.name]


async def test_photos_are_stored_and_text_is_deflated(
    tmp_path: Path, backend: _RecordingBackend
) -> None:
    manager = _manager(tmp_path / "data")
    staged = Path((await manager.export_backup(_PASS))["staged_path"])

    plain = tmp_path / "plain.zip"
    await decrypt_file(_PASS, staged, plain, backend=backend)
    with zipfile.ZipFile(plain) as archive:
        assert archive.getinfo(f"tree/{_PHOTO}").compress_type == zipfile.ZIP_STORED
        assert archive.getinfo("tree/memory/MEMORY.md").compress_type == zipfile.ZIP_DEFLATED


async def test_import_streams_back_byte_identical(
    tmp_path: Path, backend: _RecordingBackend
) -> None:
    runtime_root = tmp_path / "data"
    manager = _manager(runtime_root)
    photo = (runtime_root / "workspace" / _PHOTO).read_bytes()
    staged = Path((await manager.export_backup(_PASS))["staged_path"])
    manager.import_staged_path.write_bytes(staged.read_bytes())
    backend.sizes.clear()

    await manager.stage_import(str(manager.import_staged_path), _PASS)

    assert (runtime_root / STAGED_WORKSPACE_DIR_NAME / _PHOTO).read_bytes() == photo
    assert max(backend.sizes) <= DEFAULT_SEGMENT_SIZE + TAG_LEN
    assert not list(manager.import_staged_path.parent.glob("*.part"))


async def test_the_imported_file_goes_once_the_restore_is_ready(
    tmp_path: Path, backend: _RecordingBackend
) -> None:
    """Copiato dal selettore, grande quanto il backup: restava per sempre (04/11/2026)."""
    manager = _manager(tmp_path / "data")
    staged = Path((await manager.export_backup(_PASS))["staged_path"])
    manager.import_staged_path.write_bytes(staged.read_bytes())

    await manager.stage_import(str(manager.import_staged_path), _PASS)

    assert not manager.import_staged_path.exists()
    assert staged.exists()  # l'export in staging non si tocca


async def test_a_wrong_passphrase_leaves_the_imported_file(
    tmp_path: Path, backend: _RecordingBackend
) -> None:
    manager = _manager(tmp_path / "data")
    staged = Path((await manager.export_backup(_PASS))["staged_path"])
    manager.import_staged_path.write_bytes(staged.read_bytes())

    with pytest.raises(Exception):
        await manager.stage_import(str(manager.import_staged_path), "not-the-passphrase")

    assert manager.import_staged_path.exists()


async def test_a_failed_export_keeps_the_previous_one(
    tmp_path: Path, backend: _RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path / "data")
    previous = Path((await manager.export_backup(_PASS))["staged_path"])

    async def broken(*_args, **_kwargs):
        raise RuntimeError("heap esaurito")

    monkeypatch.setattr(backend, "encrypt", broken)
    with pytest.raises(RuntimeError):
        await manager.export_backup(_PASS)

    assert sorted(p.name for p in previous.parent.iterdir()) == [previous.name]


async def test_leftovers_of_a_killed_export_are_cleared(
    tmp_path: Path, backend: _RecordingBackend
) -> None:
    manager = _manager(tmp_path / "data")
    staging = manager.import_staged_path.parent
    staging.mkdir(parents=True)
    (staging / "export.zip.part").write_bytes(b"zip in chiaro di un processo morto")
    (staging / "jenny-backup-20260101-000000.jbk.part").write_bytes(b"mezzo")

    staged = Path((await manager.export_backup(_PASS))["staged_path"])

    assert sorted(p.name for p in staging.iterdir()) == [staged.name]
