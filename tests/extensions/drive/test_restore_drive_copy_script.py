"""``scripts/restore_drive_copy.py``: la copia si riapre senza Jenny.

Una cartella come quella che Drive dà scaricando «Jenny» (stessa forma della
``LocalDirSink``), la password della copia, e l'albero torna.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jenny.snapshot.engine import SnapshotEngine
from jenny_extensions.drive.cloud import CloudRepository, generate_master_key, seal_key
from jenny_extensions.drive.cloud_sink import LocalDirSink

pytest.importorskip("cryptography")

from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "restore_drive_copy.py"
PW = "la password della copia"


async def _drive_folder(tmp_path: Path) -> tuple[Path, bytes, Path]:
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    (ws / "memory" / "MEMORY.md").write_text("ricordi", encoding="utf-8")
    (ws / "foto.jpg").write_bytes(os.urandom(5000))
    engine = SnapshotEngine(ws, tmp_path / "snapshots")
    manifest = engine.create_snapshot(trigger="cloud")
    key = generate_master_key()
    drive = tmp_path / "Jenny"
    repo = await CloudRepository.open(
        LocalDirSink(drive), key, tmp_path / "state", create=True, backend=DevAesGcmBackend()
    )
    await repo.backup(engine, manifest.id)
    await seal_key(LocalDirSink(drive), key, PW, iterations=1000, backend=DevAesGcmBackend())
    return drive, key, ws


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
        env={**os.environ, **(env or {})}, timeout=60,
    )


async def test_list_and_restore_with_the_password(tmp_path: Path) -> None:
    drive, _key, ws = await _drive_folder(tmp_path)

    listed = _run(str(drive), "--list", env={"JENNY_BACKUP_PASSWORD": PW})
    assert listed.returncode == 0, listed.stderr
    assert "la più recente" in listed.stdout

    out = tmp_path / "back"
    done = _run(str(drive), "--out", str(out), env={"JENNY_BACKUP_PASSWORD": PW})
    assert done.returncode == 0, done.stderr
    assert (out / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "ricordi"
    assert (out / "foto.jpg").read_bytes() == (ws / "foto.jpg").read_bytes()


async def test_a_wrong_password_says_so(tmp_path: Path) -> None:
    drive, _key, _ = await _drive_folder(tmp_path)
    result = _run(str(drive), "--list", env={"JENNY_BACKUP_PASSWORD": "non è questa"})
    assert result.returncode != 0
    assert "non apre" in result.stderr


async def test_a_copy_without_a_password_says_so(tmp_path: Path) -> None:
    drive, _key, _ = await _drive_folder(tmp_path)
    (drive / "key.jbk").unlink()
    result = _run(str(drive), "--list", env={"JENNY_BACKUP_PASSWORD": PW})
    assert result.returncode != 0
    assert "key.jbk" in result.stderr


def test_not_a_jenny_folder(tmp_path: Path) -> None:
    result = _run(str(tmp_path), "--list", env={"JENNY_BACKUP_PASSWORD": PW})
    assert result.returncode != 0
    assert "repo.json" in result.stderr
