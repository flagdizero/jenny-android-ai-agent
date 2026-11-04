"""I verbali della WebUI di ``.jenny/webui/`` nel database.

Fino alla 2.x il verbale di una conversazione era ``.jenny/webui/<stem>.jsonl``
(un frame per riga), e oltre 8 MB i turni vecchi ruotavano in
``<stem>.segments/NNNNNN.jsonl`` con un ``manifest.json``: in ordine, prima i
segmenti e poi il file attivo. Lo ``stem`` è la chiave del verbale
(``websocket:<chat_id>``) con i ``:`` diventati ``_``; il ``chat_id`` di un
progetto è ``project:<nome>``, e un nome di progetto non ha ``:``, quindi la
chiave si ricostruisce: ``websocket_project_orto_bio`` è
``websocket:project:orto_bio``.

Si fonde per chiave: un verbale che il database ha già resta com'è. I file
vanno in ``.jenny/webui.migrated/``, che 30 giorni dopo se ne va. Un thread
``<stem>.json`` di prima del verbale resta dov'è.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from loguru import logger

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

WEBUI = (".jenny", "webui")
MIGRATED = (".jenny", "webui.migrated")
_PREFIX = "websocket_"


def _sources(ctx: MigrationContext) -> list[Path]:
    base = ctx.workspace.joinpath(*WEBUI)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir()
                  if p.name.startswith(_PREFIX)
                  and (p.suffix == ".jsonl" and p.is_file() or p.suffix == ".segments"))


def pending(ctx: MigrationContext) -> bool:
    return bool(_sources(ctx))


def key_of(stem: str) -> str | None:
    if not stem.startswith(_PREFIX):
        return None
    chat = stem[len(_PREFIX):]
    if chat.startswith("project_"):
        chat = "project:" + chat[len("project_"):]
    return f"websocket:{chat}"


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny import db
    from jenny.webui.transcript_store import SCHEMA

    sources = _sources(ctx)
    if not sources:
        return MigrationResult(NOTHING)
    database = db.database_for_workspace(ctx.workspace, db.STATE)
    database.require(SCHEMA)
    base = ctx.workspace.joinpath(*WEBUI)

    counts = {"transcripts": 0, "rows": 0, "skipped": 0}
    for stem in sorted({p.name.rsplit(".", 1)[0] for p in sources}):
        key = key_of(stem)
        if key is None:
            continue
        files = sorted((base / f"{stem}.segments").glob("[0-9]*.jsonl"))
        if (base / f"{stem}.jsonl").is_file():
            files.append(base / f"{stem}.jsonl")
        with database.write() as conn:
            if conn.execute("SELECT 1 FROM transcripts WHERE key = ?", (key,)).fetchone():
                counts["skipped"] += 1
                continue
            seq = turn = 0
            for path in files:
                for raw in _lines(path):
                    conn.execute("INSERT INTO transcript_rows(key, seq, turn, data) "
                                 "VALUES (?, ?, ?, ?)", (key, seq, turn, raw))
                    seq += 1
                    if json.loads(raw).get("event") == "turn_end":
                        turn += 1
            conn.execute("INSERT INTO transcripts(key, next_seq, turn) VALUES (?, ?, ?)",
                         (key, seq, turn))
        counts["transcripts"] += 1
        counts["rows"] += seq

    target = ctx.workspace.joinpath(*MIGRATED)
    target.mkdir(parents=True, exist_ok=True)
    for path in sources:
        dest = target / path.name
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        path.rename(dest)
    return MigrationResult(DONE, sources=["/".join(WEBUI)], counts=counts)


def cleanup(ctx: MigrationContext) -> None:
    shutil.rmtree(ctx.workspace.joinpath(*MIGRATED), ignore_errors=True)


def _lines(path: Path) -> list[str]:
    """Le righe che sono un oggetto JSON, ricompattate; le altre si saltano."""
    out = []
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(record, dict):
                out.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    if bad:
        logger.warning("Transcripts migration: {} unreadable line(s) in {}", bad, path.name)
    return out
