"""Le descrizioni delle bozze e il registro del taint nel database.

Fino alla 2.x:

- ogni bozza era ``.jenny/staged/<id>/meta.json`` + ``content``; il contenuto
  resta dov'è, la descrizione diventa una riga (``staged_drafts``);
- il registro del taint era ``.jenny/taint_ledger.json`` =
  ``{"v": 1, "entries": {rel: {"t", "src"}}}``, e diventa la tabella
  ``taint_ledger``.

Si fonde per chiave (id della bozza, percorso del taint): una riga che il
database ha già resta com'è. Poi ``meta.json`` e il registro prendono il
suffisso ``.migrated``, e 30 giorni dopo se ne vanno.
"""

from __future__ import annotations

import json
from pathlib import Path

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

STAGED = (".jenny", "staged")
LEDGER = (".jenny", "taint_ledger.json")
_SUFFIX = ".migrated"


def _metas(ws: Path) -> list[Path]:
    base = ws.joinpath(*STAGED)
    return sorted(base.glob("*/meta.json")) if base.is_dir() else []


def pending(ctx: MigrationContext) -> bool:
    return bool(_metas(ctx.workspace)) or ctx.workspace.joinpath(*LEDGER).is_file()


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny import db
    from jenny.agent.staged_writes import SCHEMA as STAGED_SCHEMA
    from jenny.security.taint_ledger import SCHEMA as TAINT_SCHEMA

    metas = _metas(ctx.workspace)
    ledger = ctx.workspace.joinpath(*LEDGER)
    if not metas and not ledger.is_file():
        return MigrationResult(NOTHING)
    database = db.database_for_workspace(ctx.workspace, db.STATE)
    database.require(STAGED_SCHEMA)
    database.require(TAINT_SCHEMA)

    drafts = []
    for path in metas:
        meta = _json(path)
        if isinstance(meta, dict) and isinstance(meta.get("target"), str):
            meta["id"] = path.parent.name
            drafts.append(meta)
    raw = _json(ledger)
    entries = raw.get("entries") if isinstance(raw, dict) else None
    taints = [(k, v) for k, v in (entries or {}).items()
              if isinstance(k, str) and isinstance(v, dict)]

    counts = {"drafts": 0, "taint": 0}
    with database.write() as conn:
        have = {r["id"] for r in conn.execute("SELECT id FROM staged_drafts")}
        for meta in drafts:
            if meta["id"] in have:
                continue
            conn.execute(
                "INSERT INTO staged_drafts(id, target, task_id, created_ms, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (meta["id"], meta["target"], meta.get("task_id"),
                 int(meta.get("created_ms") or 0), json.dumps(meta, ensure_ascii=False)),
            )
            counts["drafts"] += 1
        have = {r["path"] for r in conn.execute("SELECT path FROM taint_ledger")}
        for rel, entry in taints:
            if rel in have:
                continue
            conn.execute("INSERT INTO taint_ledger(path, t, src) VALUES (?, ?, ?)",
                         (rel, int(entry.get("t") or 0), entry.get("src")))
            counts["taint"] += 1

    sources = []
    for path in metas:
        path.replace(path.with_name(path.name + _SUFFIX))
    if metas:
        sources.append("/".join(STAGED))
    if ledger.is_file():
        ledger.replace(ledger.with_name(ledger.name + _SUFFIX))
        sources.append("/".join(LEDGER))
    return MigrationResult(DONE, sources=sources, counts=counts)


def cleanup(ctx: MigrationContext) -> None:
    base = ctx.workspace.joinpath(*STAGED)
    if base.is_dir():
        for path in base.glob("*/meta.json" + _SUFFIX):
            path.unlink(missing_ok=True)
    ledger = ctx.workspace.joinpath(*LEDGER)
    ledger.with_name(ledger.name + _SUFFIX).unlink(missing_ok=True)


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
