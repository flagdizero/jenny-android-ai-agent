"""I job di ``cron/`` nel database: ``jobs.json``, il giornale delle azioni, i record.

Prima di ``state.sqlite`` (``jenny/cron/store.py``) lo scheduler teneva nel
workspace:

- ``cron/jobs.json`` — ``{"version", "jobs": [...]}``, con accanto
  ``jobs.json.bak`` (l'ultimo contenuto valido);
- ``cron/action.jsonl`` — le scritture fatte a scheduler fermo, una per riga:
  ``{"action": "add"|"del", "params": ...}``, da applicare sopra ``jobs.json``
  (``add`` è un upsert, con i campi in snake_case di ``asdict(CronJob)``);
- ``cron/runs/<job>_<ms>_<hex>.json`` — un record per esecuzione;
- ``cron.lock`` accanto alla cartella.

Si fonde per id: un job che il database ha già resta com'è. Poi la cartella
diventa ``cron.migrated/``, che 30 giorni dopo se ne va.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

OLD_DIR = "cron"
MIGRATED_DIR = "cron.migrated"


def pending(ctx: MigrationContext) -> bool:
    return (ctx.workspace / OLD_DIR).is_dir()


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny.cron import store as cron_store
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob

    old = ctx.workspace / OLD_DIR
    if not old.is_dir():
        return MigrationResult(NOTHING)
    database = cron_store.cron_database(ctx.workspace)

    jobs: dict[str, dict[str, Any]] = {}
    for raw in _jobs_file(old):
        try:
            job = CronService._parse_job(raw)
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.warning("Cron migration: job {} unreadable, skipped", raw.get("id"))
            continue
        jobs[job.id] = CronService._job_to_dict(job)
    for action in _actions(old):
        params = action.get("params") or {}
        if action.get("action") == "del":
            jobs.pop(str(params.get("job_id") or ""), None)
            continue
        try:
            # Riletto dal parser dello scheduler, come una riga di ``jobs.json``:
            # un ``mode`` ignoto torna ``reminder`` anche da qui.
            job = CronService._parse_job(CronService._job_to_dict(CronJob.from_dict(params)))
            jobs[job.id] = CronService._job_to_dict(job)
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.warning("Cron migration: action line unreadable, skipped")

    existing = cron_store.load_rows(database)
    have = {str(j.get("id")) for j in existing}
    added = [j for job_id, j in jobs.items() if job_id not in have]
    runs = _runs(old)
    with database.write():
        if added:
            cron_store.save_rows(database, existing + added)
        known = {r["run_id"] for r in database.read("SELECT run_id FROM cron_runs")}
        fresh = [r for r in runs if r[0] not in known]
        for run_id, job_id, at_ms, record in fresh:
            cron_store.write_run(database, run_id, job_id, at_ms, record)

    target = ctx.workspace / MIGRATED_DIR
    if target.exists():
        shutil.rmtree(target)
    old.rename(target)
    (ctx.workspace / f"{OLD_DIR}.lock").unlink(missing_ok=True)
    return MigrationResult(DONE, sources=[OLD_DIR],
                           counts={"jobs": len(added), "runs": len(fresh)})


def cleanup(ctx: MigrationContext) -> None:
    shutil.rmtree(ctx.workspace / MIGRATED_DIR, ignore_errors=True)


def _jobs_file(old: Path) -> list[dict[str, Any]]:
    for name in ("jobs.json", "jobs.json.bak"):
        try:
            data = json.loads((old / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            logger.warning("Cron migration: {} unreadable, trying the next copy", name)
            continue
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return [j for j in data["jobs"] if isinstance(j, dict)]
    return []


def _actions(old: Path) -> list[dict[str, Any]]:
    from jenny.utils.path import iter_jsonl

    path = old / "action.jsonl"
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [a for a in iter_jsonl(fh) if isinstance(a, dict) and "action" in a]


def _runs(old: Path) -> list[tuple[str, str, int, dict[str, Any]]]:
    out = []
    folder = old / "runs"
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        run_id = str(record.get("run_id") or path.stem)
        at_ms = record.get("updated_at_ms")
        if not isinstance(at_ms, int):
            at_ms = int(path.stat().st_mtime * 1000)
        out.append((run_id, str(record.get("job_id") or ""), at_ms, record))
    return out
