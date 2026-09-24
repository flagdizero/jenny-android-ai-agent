"""Protocollo di ripristino del workspace al boot (marker + swap atomico).

Il ripristino non avviene mai a gateway vivo: chi lo richiede (API di backup)
prepara un albero *staged* nella runtime root, scrive il marker PER ULTIMO e
riavvia il processo. Al boot successivo — prima che qualunque componente
tocchi il workspace — ``apply_pending_restore`` esegue lo swap:

    1. ``os.replace(workspace → workspace_pre_restore_<ts>)``  (copia di sicurezza)
    2. ``os.replace(workspace_staged → workspace)``

Entrambe le mosse sono rename atomici sullo stesso filesystem; il protocollo
è idempotente e riprende da dove si era interrotto in caso di crash a metà.
``apply_pending_restore`` non solleva mai: nel dubbio lascia il workspace
esistente e logga.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.snapshot.locations import (
    MARKER_FILE_NAME,
    SAFETY_DIR_PREFIX,
    SANITY_FILE_NAME,
    STAGED_SNAPSHOTS_DIR_NAME,
    STAGED_WORKSPACE_DIR_NAME,
)
from jenny.utils.clock import now_ms as _now_ms
from jenny.utils.path import atomic_write

# -- marker -------------------------------------------------------------------


def write_marker(runtime_root: Path, *, source: str, snapshot_id: str | None = None) -> None:
    """Scrive il marker di restore pendente. Va chiamato PER ULTIMO nello staging."""
    payload = {
        "version": 1,
        "source": source,  # "backup_file" | "snapshot"
        "snapshot_id": snapshot_id,
        "created_at_ms": _now_ms(),
    }
    atomic_write(runtime_root / MARKER_FILE_NAME, json.dumps(payload, ensure_ascii=False))


def read_marker(runtime_root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((runtime_root / MARKER_FILE_NAME).read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Unreadable restore marker; treating as absent")
        return None
    return data if isinstance(data, dict) else None


def clear_marker(runtime_root: Path) -> None:
    (runtime_root / MARKER_FILE_NAME).unlink(missing_ok=True)


# -- sanity file dello staging --------------------------------------------------


def write_staging_sanity(staged_dir: Path) -> None:
    """Marca lo staging come completo (conteggio file per la verifica al boot)."""
    file_count = sum(1 for p in staged_dir.rglob("*") if p.is_file())
    atomic_write(
        staged_dir / SANITY_FILE_NAME,
        json.dumps({"version": 1, "file_count": file_count + 1}),
        fsync_dir=False,
    )


def _staging_is_sane(staged_dir: Path) -> bool:
    try:
        data = json.loads((staged_dir / SANITY_FILE_NAME).read_text("utf-8"))
        expected = int(data["file_count"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    actual = sum(1 for p in staged_dir.rglob("*") if p.is_file())
    if actual != expected:
        logger.error(
            "Staged restore tree at {} has {} files, expected {}", staged_dir, actual, expected
        )
        return False
    return True


# -- swap al boot ----------------------------------------------------------------


def apply_pending_restore(runtime_root: Path) -> bool:
    """Applica un eventuale restore pendente. Ritorna True se ha scambiato.

    Chiamata una sola volta, all'inizio di ``android_entry.run_gateway``,
    prima di ``set_workspace_dir``/``sync_workspace_templates``. Mai raises.
    """
    try:
        return _apply_pending_restore(Path(runtime_root))
    except Exception:
        logger.opt(exception=True).error(
            "Pending restore failed unexpectedly; continuing with the existing workspace"
        )
        return False


def _apply_pending_restore(runtime_root: Path) -> bool:
    marker = read_marker(runtime_root)
    if marker is None:
        return False

    workspace = runtime_root / "workspace"
    staged = runtime_root / STAGED_WORKSPACE_DIR_NAME
    staged_snapshots = runtime_root / STAGED_SNAPSHOTS_DIR_NAME
    created_at = int(marker.get("created_at_ms") or _now_ms())
    safety = runtime_root / f"{SAFETY_DIR_PREFIX}{created_at}"

    if not staged.is_dir():
        if workspace.is_dir():
            # Marker orfano (staging mai completato o già consumato): boot normale.
            logger.error("Restore marker without staged tree; clearing and booting normally")
        else:
            # Crash dopo la mossa 1 ma senza staged: ripristina la copia di
            # sicurezza per non avviarsi mai senza workspace.
            logger.error("Restore marker with neither staged tree nor workspace")
            _recover_from_safety(runtime_root, workspace)
        clear_marker(runtime_root)
        return False

    if not _staging_is_sane(staged):
        logger.error("Staged restore tree failed sanity check; keeping current workspace")
        if not workspace.is_dir():
            _recover_from_safety(runtime_root, workspace)
        clear_marker(runtime_root)
        shutil.rmtree(staged, ignore_errors=True)
        return False

    # Mossa 1: metti in sicurezza il workspace corrente (salta se un crash
    # precedente l'aveva già fatta: workspace assente + staged presente).
    if workspace.is_dir():
        if safety.exists():
            safety = runtime_root / f"{SAFETY_DIR_PREFIX}{created_at}_{_now_ms()}"
        os.replace(workspace, safety)

    # Mossa 2: promuovi lo staging a workspace.
    os.replace(staged, workspace)
    (workspace / SANITY_FILE_NAME).unlink(missing_ok=True)

    # Storia importata dal backup (assente nel restore da snapshot, dove la
    # storia corrente resta valida perché vive fuori dal workspace): unione
    # additiva nello store locale, mai sostituzione — così la storia locale,
    # incluso il pre_restore scattato allo staging, resta raggiungibile
    # dalla UI (la safety copy su Android non lo è).
    if staged_snapshots.is_dir():
        _merge_snapshot_store(staged_snapshots, runtime_root / "snapshots")

    clear_marker(runtime_root)
    # Nel retry post-crash la mossa 1 era già avvenuta: safety può non esistere.
    logger.info(
        "Workspace restored from {} (previous workspace kept at {})",
        marker.get("source", "unknown"),
        safety.name if safety.is_dir() else "an earlier safety copy",
    )
    return True


def _merge_snapshot_store(staged: Path, current: Path) -> None:
    """Fonde lo store di snapshot importato in quello locale (unione additiva).

    Blob e manifest sono content-addressed: un path già presente ha contenuto
    identico per costruzione, quindi si sposta solo ciò che manca. L'index e
    lo scan_state locali restano: ``_load_index`` riconcilia da sé l'indice
    quando il conteggio dei manifest non torna. Idempotente, quindi sicura
    anche nel retry post-crash.
    """
    for item in sorted(staged.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(staged)
        if rel.parts[0] not in ("objects", "manifests"):
            continue  # index.json/scan_state.json del backup: ricostruiti localmente
        dest = current / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(item, dest)
    shutil.rmtree(staged, ignore_errors=True)


def _recover_from_safety(runtime_root: Path, workspace: Path) -> None:
    """Ultima risorsa: rimette in servizio la copia di sicurezza più recente."""
    candidates = sorted(
        (p for p in runtime_root.glob(f"{SAFETY_DIR_PREFIX}*") if p.is_dir()),
        key=lambda p: p.name,
    )
    if candidates:
        os.replace(candidates[-1], workspace)
        logger.warning("Recovered workspace from safety copy {}", candidates[-1].name)


# -- pulizia copie di sicurezza ----------------------------------------------------


def sweep_safety_copies(runtime_root: Path, *, max_age_days: int = 7) -> int:
    """Elimina le copie di sicurezza più vecchie di ``max_age_days``. Mai raises."""
    removed = 0
    threshold = time.time() - max_age_days * 86_400
    try:
        for candidate in Path(runtime_root).glob(f"{SAFETY_DIR_PREFIX}*"):
            try:
                if candidate.is_dir() and candidate.stat().st_mtime < threshold:
                    shutil.rmtree(candidate, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    if removed:
        logger.info("Removed {} old workspace safety cop(ies)", removed)
    return removed
