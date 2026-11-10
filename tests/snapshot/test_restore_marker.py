"""Test del protocollo restore-marker: swap, sanity, crash recovery."""

from __future__ import annotations

import json
from pathlib import Path

from jenny.snapshot.restore_marker import (
    apply_pending_restore,
    clear_marker,
    read_marker,
    sweep_safety_copies,
    write_marker,
    write_staging_sanity,
)
from jenny.utils.locations import (
    MARKER_FILE_NAME,
    SAFETY_DIR_PREFIX,
    SANITY_FILE_NAME,
    STAGED_SNAPSHOTS_DIR_NAME,
    STAGED_WORKSPACE_DIR_NAME,
)


def _make_workspace(root: Path, content: str = "attuale") -> Path:
    ws = root / "workspace"
    (ws / "memory").mkdir(parents=True)
    (ws / "SOUL.md").write_text(content, encoding="utf-8")
    (ws / "memory" / "MEMORY.md").write_text(content, encoding="utf-8")
    return ws


def _make_staging(root: Path, content: str = "ripristinato") -> Path:
    staged = root / STAGED_WORKSPACE_DIR_NAME
    (staged / "memory").mkdir(parents=True)
    (staged / "SOUL.md").write_text(content, encoding="utf-8")
    (staged / "memory" / "MEMORY.md").write_text(content, encoding="utf-8")
    write_staging_sanity(staged)
    return staged


def test_no_marker_is_noop(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    assert apply_pending_restore(tmp_path) is False
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "attuale"


def test_marker_roundtrip(tmp_path: Path) -> None:
    write_marker(tmp_path, source="backup_file", snapshot_id=None)
    marker = read_marker(tmp_path)
    assert marker is not None
    assert marker["source"] == "backup_file"
    clear_marker(tmp_path)
    assert read_marker(tmp_path) is None


def test_normal_swap(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    _make_staging(tmp_path)
    write_marker(tmp_path, source="backup_file")

    assert apply_pending_restore(tmp_path) is True

    ws = tmp_path / "workspace"
    assert ws.joinpath("SOUL.md").read_text("utf-8") == "ripristinato"
    # Il sanity file non deve finire nel workspace promosso.
    assert not ws.joinpath(SANITY_FILE_NAME).exists()
    # Marker consumato, staging sparito, safety copy presente col vecchio contenuto.
    assert read_marker(tmp_path) is None
    assert not (tmp_path / STAGED_WORKSPACE_DIR_NAME).exists()
    safety = list(tmp_path.glob(f"{SAFETY_DIR_PREFIX}*"))
    assert len(safety) == 1
    assert (safety[0] / "SOUL.md").read_text("utf-8") == "attuale"


def test_swap_with_imported_snapshot_history(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    _make_staging(tmp_path)
    (tmp_path / "snapshots" / "manifests").mkdir(parents=True)
    (tmp_path / "snapshots" / "manifests" / "old.json").write_text("{}", encoding="utf-8")
    staged_snap = tmp_path / STAGED_SNAPSHOTS_DIR_NAME / "manifests"
    staged_snap.mkdir(parents=True)
    (staged_snap / "imported.json").write_text("{}", encoding="utf-8")
    write_marker(tmp_path, source="backup_file")

    assert apply_pending_restore(tmp_path) is True
    # Unione additiva: la storia importata E quella locale convivono nello store
    # (il pre_restore scattato allo staging deve restare raggiungibile dalla UI).
    assert (tmp_path / "snapshots" / "manifests" / "imported.json").exists()
    assert (tmp_path / "snapshots" / "manifests" / "old.json").exists()
    assert not (tmp_path / STAGED_SNAPSHOTS_DIR_NAME).exists()


def test_orphan_marker_cleared(tmp_path: Path) -> None:
    """Marker senza staging: boot normale, workspace intatto."""
    _make_workspace(tmp_path)
    write_marker(tmp_path, source="snapshot", snapshot_id="abc")
    assert apply_pending_restore(tmp_path) is False
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "attuale"
    assert read_marker(tmp_path) is None


def test_insane_staging_rejected(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    staged = _make_staging(tmp_path)
    # Manomette lo staging DOPO il sanity file: conteggio disallineato.
    (staged / "file-extra.txt").write_text("x", encoding="utf-8")
    write_marker(tmp_path, source="backup_file")

    assert apply_pending_restore(tmp_path) is False
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "attuale"
    assert read_marker(tmp_path) is None
    assert not (tmp_path / STAGED_WORKSPACE_DIR_NAME).exists()


def test_missing_sanity_file_rejected(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    staged = tmp_path / STAGED_WORKSPACE_DIR_NAME
    staged.mkdir()
    (staged / "SOUL.md").write_text("x", encoding="utf-8")
    write_marker(tmp_path, source="backup_file")
    assert apply_pending_restore(tmp_path) is False
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "attuale"


def test_crash_recovery_retries_second_move(tmp_path: Path) -> None:
    """Crash tra mossa 1 e mossa 2: workspace assente, staged presente."""
    _make_staging(tmp_path)
    # Simula la mossa 1 già avvenuta: nessun workspace, solo la safety copy.
    safety = tmp_path / f"{SAFETY_DIR_PREFIX}123"
    safety.mkdir()
    (safety / "SOUL.md").write_text("vecchia", encoding="utf-8")
    write_marker(tmp_path, source="backup_file")

    assert apply_pending_restore(tmp_path) is True
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "ripristinato"
    assert (safety / "SOUL.md").exists()


def test_last_resort_recovers_from_safety_copy(tmp_path: Path) -> None:
    """Marker presente ma né workspace né staging: recupera la safety copy."""
    safety = tmp_path / f"{SAFETY_DIR_PREFIX}999"
    safety.mkdir()
    (safety / "SOUL.md").write_text("salvata", encoding="utf-8")
    write_marker(tmp_path, source="backup_file")

    assert apply_pending_restore(tmp_path) is False
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "salvata"
    assert read_marker(tmp_path) is None


def test_corrupt_marker_treated_as_absent(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    (tmp_path / MARKER_FILE_NAME).write_text("{corrotto", encoding="utf-8")
    assert apply_pending_restore(tmp_path) is False
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "attuale"


def test_sweep_safety_copies(tmp_path: Path) -> None:
    import os
    import time

    old = tmp_path / f"{SAFETY_DIR_PREFIX}old"
    old.mkdir()
    (old / "f.txt").write_text("x", encoding="utf-8")
    recent = tmp_path / f"{SAFETY_DIR_PREFIX}recent"
    recent.mkdir()
    ancient = time.time() - 30 * 86_400
    os.utime(old, (ancient, ancient))

    assert sweep_safety_copies(tmp_path, max_age_days=7) == 1
    assert not old.exists()
    assert recent.exists()


def test_a_fresh_safety_copy_of_an_old_workspace_is_kept(tmp_path: Path) -> None:
    """La copia di sicurezza e' il workspace **rinominato**, e il rename tiene
    l'mtime della cartella — l'ultima volta che nella sua radice si e' creato o
    tolto qualcosa, magari mesi prima. La pulizia all'avvio, che gira subito dopo
    il ripristino, la cancellava al primo boot. L'eta' e' quella del restore,
    scritta nel nome."""
    import os
    import time

    now_ms = int(time.time() * 1000)
    fresh = tmp_path / f"{SAFETY_DIR_PREFIX}{now_ms}"
    fresh.mkdir()
    retried = tmp_path / f"{SAFETY_DIR_PREFIX}{now_ms}_{now_ms + 5}"
    retried.mkdir()
    ancient = time.time() - 90 * 86_400
    for d in (fresh, retried):
        os.utime(d, (ancient, ancient))
    stale = tmp_path / f"{SAFETY_DIR_PREFIX}{now_ms - 30 * 86_400_000}"
    stale.mkdir()

    assert sweep_safety_copies(tmp_path, max_age_days=7) == 1
    assert fresh.exists() and retried.exists()
    assert not stale.exists()


def test_a_restore_applied_days_after_staging_keeps_its_safety_copy(tmp_path: Path) -> None:
    """La copia di sicurezza portava la data dello *staging*, non del restore: un
    ripristino preparato e applicato al riavvio di dieci giorni dopo produceva
    una copia gia' «vecchia», e la pulizia all'avvio la cancellava subito."""
    import time

    _make_workspace(tmp_path)
    _make_staging(tmp_path)
    write_marker(tmp_path, source="backup_file")
    marker_path = tmp_path / MARKER_FILE_NAME
    marker = json.loads(marker_path.read_text("utf-8"))
    marker["created_at_ms"] = int(time.time() * 1000) - 10 * 86_400_000
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    before_ms = int(time.time() * 1000)
    assert apply_pending_restore(tmp_path) is True
    assert sweep_safety_copies(tmp_path, max_age_days=7) == 0

    (safety,) = tmp_path.glob(f"{SAFETY_DIR_PREFIX}*")
    assert (safety / "SOUL.md").read_text("utf-8") == "attuale"
    assert int(safety.name[len(SAFETY_DIR_PREFIX):].split("_")[0]) >= before_ms


def test_safety_dir_collision_gets_unique_suffix(tmp_path: Path, monkeypatch) -> None:
    """Se la safety dir esiste già, il vecchio workspace va in una dir con suffisso."""
    from jenny.snapshot import restore_marker

    _make_workspace(tmp_path)
    _make_staging(tmp_path)
    write_marker(tmp_path, source="backup_file")
    clock = iter(range(1_000_000, 1_000_100))
    monkeypatch.setattr(restore_marker, "_now_ms", lambda: next(clock))
    occupied = tmp_path / f"{SAFETY_DIR_PREFIX}1000000"
    occupied.mkdir()
    (occupied / "estranea.txt").write_text("x", encoding="utf-8")

    assert apply_pending_restore(tmp_path) is True
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "ripristinato"
    # La dir preesistente è intatta; il vecchio workspace vive in una seconda safety.
    assert (occupied / "estranea.txt").exists()
    safeties = sorted(tmp_path.glob(f"{SAFETY_DIR_PREFIX}*"))
    assert len(safeties) == 2
    moved = next(d for d in safeties if d != occupied)
    assert (moved / "SOUL.md").read_text("utf-8") == "attuale"


def test_snapshot_history_merge_on_crash_retry(tmp_path: Path) -> None:
    """Retry post-crash (mossa 1 già fatta): il merge additivo resta corretto
    anche senza safety dir di questo giro — nessuno store va perso."""
    _make_staging(tmp_path)  # workspace assente: la mossa 1 viene saltata
    staged_snap = tmp_path / STAGED_SNAPSHOTS_DIR_NAME / "manifests"
    staged_snap.mkdir(parents=True)
    (staged_snap / "imported.json").write_text("{}", encoding="utf-8")
    # File presente in entrambi gli store: il locale non va sovrascritto.
    (staged_snap / "old.json").write_text('{"stale": true}', encoding="utf-8")
    (tmp_path / "snapshots" / "manifests").mkdir(parents=True)
    (tmp_path / "snapshots" / "manifests" / "old.json").write_text("{}", encoding="utf-8")
    write_marker(tmp_path, source="backup_file")

    assert apply_pending_restore(tmp_path) is True
    assert (tmp_path / "workspace" / "SOUL.md").read_text("utf-8") == "ripristinato"
    assert (tmp_path / "snapshots" / "manifests" / "imported.json").exists()
    assert (tmp_path / "snapshots" / "manifests" / "old.json").read_text("utf-8") == "{}"
    assert not (tmp_path / STAGED_SNAPSHOTS_DIR_NAME).exists()


def test_sanity_file_counts_itself(tmp_path: Path) -> None:
    staged = _make_staging(tmp_path)
    data = json.loads((staged / SANITY_FILE_NAME).read_text("utf-8"))
    actual = sum(1 for p in staged.rglob("*") if p.is_file())
    assert data["file_count"] == actual
