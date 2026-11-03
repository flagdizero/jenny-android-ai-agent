"""I resti del vecchio annulla: ``subagents/journal`` nel workspace, e le foto dei piani.

Prima di ``undo.sqlite`` il registro di ogni aiutante stava in
``<workspace>/subagents/journal/<lineage>/`` (``entries.jsonl``, ``blobs/``,
``undo.json``). Non si importa: l'annulla non promette niente sul lavoro fatto
prima dell'aggiornamento (decisione del 02/11/2026). Si cancella, e con lei le
voci che la tenevano fuori dalle foto e dagli aiutanti
(``snapshot.engine.ALWAYS_EXCLUDED_DIRS``, ``subagent_policy._SECRET_DIRS``)
quando questo modulo si cancella.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

OLD_JOURNAL = ("subagents", "journal")


def pending(ctx: MigrationContext) -> bool:
    if ctx.workspace.joinpath(*OLD_JOURNAL).exists():
        return True
    base = ctx.runtime_root / PLANS_DIR
    if not base.is_dir():
        return False
    from jenny.utils.path import read_json

    for plan_dir in (p for p in base.iterdir() if p.is_dir()):
        if (plan_dir / "photos").is_dir() or any(plan_dir.glob("rollback.journal*")):
            return True
        data = read_json(plan_dir / "plan.json")
        if isinstance(data, dict) and data.get("format", 1) == 1:
            return True
    return False


def run(ctx: MigrationContext) -> MigrationResult:
    counts: dict[str, int] = {}
    sources: list[str] = []
    old = ctx.workspace.joinpath(*OLD_JOURNAL)
    if old.exists():
        counts["old_journals_removed"] = (
            sum(1 for p in old.iterdir() if p.is_dir()) if old.is_dir() else 0)
        shutil.rmtree(old)
        sources.append("/".join(OLD_JOURNAL))
    plans = convert_plans(ctx.runtime_root)
    if any(plans.values()):
        counts.update(plans)
        sources.append(PLANS_DIR)
    return MigrationResult(DONE if sources else NOTHING, sources=sources, counts=counts)


# ── I piani di prima: formato 1, con le foto ────────────────────────────────

PLANS_DIR = "plans"
_OPEN = ("running", "pausing", "paused", "blocked")


def convert_plans(runtime_root: Path) -> dict[str, int]:
    """I piani di formato 1 passano al 2, e le loro foto se ne vanno.

    Nessuna compatibilità per l'annulla: un piano aperto si ferma con una nota
    («aggiornamento»), uno proposto si scarta, uno chiuso non si può più
    riportare indietro. Si legge il JSON a mano, senza il modello: il formato 2
    non sa leggere il formato 1.
    """
    import json

    from jenny.utils.path import atomic_write, read_json

    counts = {"plans_converted": 0, "plans_stopped": 0, "photos_removed": 0}
    base = runtime_root / PLANS_DIR
    if not base.is_dir():
        return counts
    for plan_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for name in ("photos",):
            if (plan_dir / name).is_dir():
                shutil.rmtree(plan_dir / name)
                counts["photos_removed"] += 1
        for leftover in plan_dir.glob("rollback.journal*"):
            leftover.unlink(missing_ok=True)
        path = plan_dir / "plan.json"
        data = read_json(path)
        if not isinstance(data, dict) or data.get("format", 1) != 1:
            continue
        if convert_plan_dict(data):
            counts["plans_stopped"] += 1
        atomic_write(path, json.dumps(data, ensure_ascii=False))
        counts["plans_converted"] += 1
    return counts


def convert_plan_dict(data: dict) -> bool:
    """Un piano di formato 1 portato al 2, sul posto; True se l'ha fermato o scartato."""
    data.pop("photos", None)
    for step in data.get("steps") or []:
        if isinstance(step, dict):
            step.pop("photo_before", None)
            step.pop("photo_after", None)
    status = data.get("status")
    stamp = str(data.get("updated_at") or "")
    stopped = False
    if status in _OPEN or status == "proposed":
        new = "discarded" if status == "proposed" else "stopped"
        data["status"] = new
        history = data.setdefault("history", [])
        if isinstance(history, list):
            history.append({"at": stamp, "event": new, "from": status,
                            "reason": "update", "note": "update: no going back"})
        stopped = True
    data["format"] = 2
    data.setdefault("place", f"nb:{data.get('notebook', '')}")
    return stopped
