"""Il consumo di token nel database.

Fino alla 2.x i conti stavano in ``<workspace>/.jenny/webui/token-usage.json``
= ``{"schema_version", "days": {"YYYY-MM-DD": {...}}, "updated_at"}``. Ogni
giorno diventa una riga di ``token_usage_days``; un giorno che il database ha
già resta com'è. Poi il file prende il suffisso ``.migrated``, e 30 giorni dopo
se ne va.
"""

from __future__ import annotations

import json

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

USAGE = (".jenny", "webui", "token-usage.json")
_SUFFIX = ".migrated"


def pending(ctx: MigrationContext) -> bool:
    return ctx.workspace.joinpath(*USAGE).is_file()


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny import db
    from jenny.agent.token_usage import SCHEMA, normalize_token_usage_state

    path = ctx.workspace.joinpath(*USAGE)
    if not path.is_file():
        return MigrationResult(NOTHING)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    state = normalize_token_usage_state(raw)
    stamp = state.get("updated_at") or ""
    database = db.database_for_workspace(ctx.workspace, db.STATE)
    database.require(SCHEMA)
    added = 0
    with database.write() as conn:
        have = {r["day"] for r in conn.execute("SELECT day FROM token_usage_days")}
        for day, row in state["days"].items():
            if day in have:
                continue
            conn.execute("INSERT INTO token_usage_days(day, updated_at, data) VALUES (?, ?, ?)",
                         (day, stamp, json.dumps(row, ensure_ascii=False, sort_keys=True)))
            added += 1
    path.replace(path.with_name(path.name + _SUFFIX))
    return MigrationResult(DONE, sources=["/".join(USAGE)], counts={"days": added})


def cleanup(ctx: MigrationContext) -> None:
    path = ctx.workspace.joinpath(*USAGE)
    path.with_name(path.name + _SUFFIX).unlink(missing_ok=True)
