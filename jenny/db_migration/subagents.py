"""I record, i segnaposti e l'attività degli aiutanti nel database.

Fino alla 2.x stavano sotto ``<workspace>/subagents/``:

- ``records/<chiave>.jsonl`` — un ``SubagentRecord.to_dict`` per riga, uno
  storico per session key (il nome del file perdeva i ``:``: la chiave vera è
  dentro il record);
- ``running/<task_id>.json`` — il segnaposto di un tentativo in corso;
- ``activity/<task_id>.json`` — ``{"version", "task_id", "written_at",
  "events"}``, il digest di attività.

Tutto in una transazione, fondendo per ``task_id``: una riga che il database ha
già resta com'è. Poi ogni cartella diventa ``<nome>.migrated``, che 30 giorni
dopo se ne va.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from loguru import logger

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

ROOT = "subagents"
FOLDERS = ("records", "running", "activity")
_SUFFIX = ".migrated"


def pending(ctx: MigrationContext) -> bool:
    return any((ctx.workspace / ROOT / name).is_dir() for name in FOLDERS)


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny.agent.subagent_records import SubagentRecord, subagents_database

    base = ctx.workspace / ROOT
    present = [name for name in FOLDERS if (base / name).is_dir()]
    if not present:
        return MigrationResult(NOTHING)
    database = subagents_database(ctx.workspace)
    if database is None:
        return MigrationResult(NOTHING)

    records: list[SubagentRecord] = []
    for path in sorted((base / "records").glob("*.jsonl")) if "records" in present else []:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            record = _record(line, SubagentRecord)
            if record is not None:
                records.append(record)
    running: list[SubagentRecord] = []
    for path in sorted((base / "running").glob("*.json")) if "running" in present else []:
        record = _record(_text(path), SubagentRecord)
        if record is not None:
            running.append(record)
    digests: list[tuple[str, str]] = []
    for path in sorted((base / "activity").glob("*.json")) if "activity" in present else []:
        raw = _text(path)
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if isinstance(task_id, str) and task_id:
            digests.append((task_id, raw))

    counts = {"records": 0, "running": 0, "digests": 0}
    with database.write() as conn:
        def have(table: str) -> set[str]:
            return {r["task_id"] for r in conn.execute(f"SELECT task_id FROM {table}")}

        known = have("subagent_records")
        for record in records:
            if record.task_id in known:
                continue
            known.add(record.task_id)
            conn.execute("INSERT INTO subagent_records(task_id, session_key, ended_at, data) "
                         "VALUES (?, ?, ?, ?)",
                         (record.task_id, record.session_key, record.ended_at,
                          json.dumps(record.to_dict(), ensure_ascii=False)))
            counts["records"] += 1
        known = have("subagent_running")
        for record in running:
            if record.task_id in known:
                continue
            known.add(record.task_id)
            conn.execute("INSERT INTO subagent_running(task_id, started_at, data) VALUES (?, ?, ?)",
                         (record.task_id, record.started_at,
                          json.dumps(record.to_dict(), ensure_ascii=False)))
            counts["running"] += 1
        known = have("subagent_digests")
        for task_id, raw in digests:
            if task_id in known:
                continue
            known.add(task_id)
            conn.execute("INSERT INTO subagent_digests(task_id, data) VALUES (?, ?)",
                         (task_id, raw))
            counts["digests"] += 1

    for name in present:
        target = base / (name + _SUFFIX)
        if target.exists():
            shutil.rmtree(target)
        (base / name).rename(target)
    return MigrationResult(DONE, sources=[f"{ROOT}/{n}" for n in present], counts=counts)


def cleanup(ctx: MigrationContext) -> None:
    for name in FOLDERS:
        shutil.rmtree(ctx.workspace / ROOT / (name + _SUFFIX), ignore_errors=True)


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _record(line: str, cls):
    if not line.strip():
        return None
    try:
        return cls.from_dict(json.loads(line))
    except (ValueError, TypeError):
        logger.warning("Subagents migration: an unreadable record was skipped")
        return None
