"""Le conversazioni di ``sessions/`` nel database.

Fino alla 2.x ogni sessione era ``sessions/<chiave>.jsonl``: una riga di
metadati ``{"_type": "metadata", "key", "created_at", "updated_at",
"metadata", "last_consolidated"}``, poi un messaggio per riga. Accanto:

- ``<chiave>.turn-journal`` — il diario del turno in corso, una riga
  ``{"_type": "turn_journal", "stamp"}`` e poi i messaggi;
- ``.project-rename-pending.json`` — il giornale dei rinomini di progetto
  aperti, che ora sta in ``.jenny/``.

La chiave si prende dai metadati, non dal nome del file (``safe_key`` perde i
``:``). Le sessioni di Atlas, un lavoratore ritirato, non si portano. Si fonde
per chiave: una sessione che il database ha già resta com'è. Poi la cartella
diventa ``sessions.migrated/``, che 30 giorni dopo se ne va.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

OLD_DIR = "sessions"
MIGRATED_DIR = "sessions.migrated"
_RETIRED_PREFIXES = ("atlas:",)


def pending(ctx: MigrationContext) -> bool:
    return (ctx.workspace / OLD_DIR).is_dir()


def run(ctx: MigrationContext) -> MigrationResult:
    from jenny.session import store

    old = ctx.workspace / OLD_DIR
    if not old.is_dir():
        return MigrationResult(NOTHING)
    database = store.session_database(ctx.workspace)

    counts = {"sessions": 0, "messages": 0, "turns": 0, "skipped": 0}
    for path in sorted(old.glob("*.jsonl")):
        head, messages = _read(path)
        key = head.get("key") if isinstance(head.get("key"), str) else None
        key = key or path.stem.replace("_", ":", 1)
        if key.startswith(_RETIRED_PREFIXES) or store.head(database, key) is not None:
            counts["skipped"] += 1
            continue
        store.save(
            database, key,
            created_at=str(head.get("created_at") or ""),
            updated_at=str(head.get("updated_at") or ""),
            metadata=json.dumps(head.get("metadata") if isinstance(head.get("metadata"), dict)
                                else {}, ensure_ascii=False),
            last_consolidated=_int(head.get("last_consolidated")),
            lines=[json.dumps(m, ensure_ascii=False) for m in messages],
            known=None, known_version=None,
        )
        counts["sessions"] += 1
        counts["messages"] += len(messages)
        turn = path.with_suffix(".turn-journal")
        if turn.is_file():
            stamp, lines = _turn(turn)
            if stamp:
                store.turn_reset(database, key, stamp, lines)
                counts["turns"] += 1

    renames = old / ".project-rename-pending.json"
    target = ctx.workspace / ".jenny" / "project-rename-pending.json"
    if renames.is_file() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(renames, target)

    moved = ctx.workspace / MIGRATED_DIR
    if moved.exists():
        shutil.rmtree(moved)
    old.rename(moved)
    return MigrationResult(DONE, sources=[OLD_DIR], counts=counts)


def cleanup(ctx: MigrationContext) -> None:
    shutil.rmtree(ctx.workspace / MIGRATED_DIR, ignore_errors=True)


def _read(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """I metadati (l'ultimo record ``metadata`` vince) e i messaggi; le righe rotte si saltano."""
    head: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(data, dict):
                bad += 1
            elif data.get("_type") == "metadata":
                head = data
            else:
                messages.append(data)
    if bad:
        logger.warning("Sessions migration: {} unreadable line(s) in {}", bad, path.name)
    return head, messages


def _turn(path: Path) -> tuple[str | None, list[str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        first = json.loads(lines[0]) if lines else None
    except (OSError, ValueError):
        return None, []
    if not isinstance(first, dict) or first.get("_type") != "turn_journal":
        return None, []
    kept = []
    for line in lines[1:]:
        try:
            if isinstance(json.loads(line), dict):
                kept.append(line)
        except ValueError:
            break
    return str(first.get("stamp") or ""), kept


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
