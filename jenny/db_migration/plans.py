"""I piani di ``<runtime_root>/plans/`` nel database, accanto al diario dell'annulla.

Fino alla 2.x ogni piano era ``plans/<id>/plan.json`` (``Plan.to_dict``), e le
pianificazioni in corso stavano in ``plans/drafting.json``
(``{"format", "notebooks": {posto: voce}}``, con chiavi che prima dei posti
erano il nome nudo del quaderno). Un piano di formato 1 (con le foto) lo porta
al 2 la stessa conversione di ``undo_cleanup``, così questa migrazione regge
anche se quella non è ancora riuscita.

Si fonde per id: un piano che il database ha già resta com'è, e così una
pianificazione. Poi la cartella diventa ``plans.migrated/``, che 30 giorni dopo
se ne va.
"""

from __future__ import annotations

import json
import shutil
from typing import Any

from loguru import logger

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult
from jenny.db_migration.undo_cleanup import convert_plan_dict

OLD_DIR = "plans"
MIGRATED_DIR = "plans.migrated"


def pending(ctx: MigrationContext) -> bool:
    return (ctx.runtime_root / OLD_DIR).is_dir()


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny.plan.model import Plan, PlanFormatError
    from jenny.plan.store import PlanStore, is_plan_id, slot_id

    old = ctx.runtime_root / OLD_DIR
    if not old.is_dir():
        return MigrationResult(NOTHING)
    store = PlanStore(old, workspace=ctx.workspace)

    plans: list[Plan] = []
    for plan_dir in sorted(p for p in old.iterdir() if p.is_dir() and is_plan_id(p.name)):
        data = _read(plan_dir / "plan.json")
        if data is None:
            continue
        if data.get("format", 1) == 1:
            convert_plan_dict(data)
        try:
            plan = Plan.from_dict(data)
        except (PlanFormatError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Plans migration: {} unreadable, skipped: {}", plan_dir.name, exc)
            continue
        if plan.id == plan_dir.name:
            plans.append(plan)
    drafting = _read(old / "drafting.json") or {}
    entries = drafting.get("notebooks")
    if not isinstance(entries, dict):
        entries = {}

    added = 0
    with store._db.write() as conn:
        have = {r["id"] for r in conn.execute("SELECT id FROM plans")}
        for plan in plans:
            if plan.id not in have:
                store._write(conn, plan)
                added += 1
        current = store._read_drafting(conn)
        for name, entry in entries.items():
            slot = slot_id(str(name))
            if isinstance(entry, dict) and slot not in current:
                store._write_drafting(conn, slot, dict(entry))

    target = ctx.runtime_root / MIGRATED_DIR
    if target.exists():
        shutil.rmtree(target)
    old.rename(target)
    return MigrationResult(DONE, sources=[OLD_DIR],
                           counts={"plans": added, "drafting": len(entries)})


def cleanup(ctx: MigrationContext) -> None:
    shutil.rmtree(ctx.runtime_root / MIGRATED_DIR, ignore_errors=True)


def _read(path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Plans migration: {} unreadable, skipped", path.name)
        return None
    return data if isinstance(data, dict) else None
