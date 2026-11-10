"""Test dell'ordine di boot in ``android_entry.run_gateway``.

Il contratto critico: ``apply_pending_restore`` va eseguito PRIMA che
qualunque componente tocchi il workspace (mkdir, set_workspace_dir, sync dei
template), perché lo swap atomico richiede un workspace freddo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny import android_entry
from jenny.snapshot.restore_marker import write_marker, write_staging_sanity
from jenny.utils.locations import (
    SAFETY_DIR_PREFIX,
    STAGED_WORKSPACE_DIR_NAME,
)


@pytest.fixture()
def _boot_env(monkeypatch):
    """Neutralizza il gateway vero e ripristina il workspace globale a fine test."""
    import jenny.gateway_runtime as gateway_runtime
    from jenny.config import paths as paths_mod

    async def fake_run_gateway(**_kwargs) -> None:
        return None

    monkeypatch.setattr(gateway_runtime, "_run_gateway", fake_run_gateway)
    previous = paths_mod.get_workspace_path()
    yield
    paths_mod.set_workspace_dir(str(previous))


def test_restore_applied_before_workspace_setup(
    tmp_path: Path, monkeypatch, _boot_env
) -> None:
    """Il restore pendente viene valutato a workspace ancora freddo."""
    import jenny.snapshot.restore_marker as marker_mod

    data_dir = tmp_path / "data"
    calls: list[Path] = []

    def fake_apply(data_path: Path) -> bool:
        calls.append(Path(data_path))
        # A questo punto il boot non deve ancora aver creato il workspace.
        assert not (Path(data_path) / "workspace").exists()
        return False

    monkeypatch.setattr(marker_mod, "apply_pending_restore", fake_apply)
    android_entry.run_gateway(str(data_dir))

    assert calls == [data_dir]
    # Dopo il boot il workspace esiste (creato DOPO il punto di restore).
    assert (data_dir / "workspace").is_dir()


def test_pending_restore_swaps_workspace_at_boot(tmp_path: Path, _boot_env) -> None:
    """End-to-end: marker + staging preparati → il boot promuove lo staging."""
    data_dir = tmp_path / "data"
    workspace = data_dir / "workspace"
    workspace.mkdir(parents=True)
    # Nome file NON di template: il sync post-swap non deve toccarlo.
    (workspace / "nota_utente.md").write_text("attuale", encoding="utf-8")

    staged = data_dir / STAGED_WORKSPACE_DIR_NAME
    staged.mkdir()
    (staged / "nota_utente.md").write_text("ripristinata", encoding="utf-8")
    write_staging_sanity(staged)
    write_marker(data_dir, source="backup_file")

    android_entry.run_gateway(str(data_dir))

    assert (workspace / "nota_utente.md").read_text("utf-8") == "ripristinata"
    safety = list(data_dir.glob(f"{SAFETY_DIR_PREFIX}*"))
    assert len(safety) == 1
    assert (safety[0] / "nota_utente.md").read_text("utf-8") == "attuale"
