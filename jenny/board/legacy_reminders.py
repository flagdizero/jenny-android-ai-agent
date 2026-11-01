"""Le attese di ``board/reminders.json`` (fino alla 2.0.0) diventano job dello scheduler.

Dal 31/10/2026 un promemoria del quaderno è un job di ``cron/jobs.json``
(:func:`jenny.board.cron_link.add_notebook_job`), e l'orologio dei quaderni non
legge più ``reminders.json``. Un'installazione che si aggiorna dalla 2.0.0 ha
però quel file, con le attese aperte dell'utente: senza questa passata
smetterebbero di suonare in silenzio, e nessuna superficie lo direbbe (review
del 01/11/2026; la regola sui nomi persistiti è in ``AGENTS.md``).

La passata la fa l'orologio (:func:`jenny.board.clock.run_reminders`), una volta
per quaderno: ogni attesa ``open`` diventa un job del quaderno — un singolo per
una data, un ricorrente col filtro per una condizione — e il file prende il
suffisso ``.migrated``, così non si rilegge e resta da vedere. Un'attesa a data
già passata suona al primo giro, come un singolo recuperato a telefono spento:
in ritardo, ma suona. Un'attesa ``done`` o ``dropped`` è storia e non diventa
niente.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model
from jenny.board.store import board_dir, read_board

LEGACY_REMINDERS_FILE = "reminders.json"
MIGRATED_SUFFIX = ".migrated"
UNREADABLE_SUFFIX = ".unreadable"

# Un'attesa già scaduta suona al primo giro utile: fra un minuto.
_LATE_RING_MS = 60 * 1000


def legacy_reminders_path(root: Path) -> Path:
    """Il file delle attese di prima, che un quaderno aggiornato può avere ancora."""
    return board_dir(root) / LEGACY_REMINDERS_FILE


def migrate_legacy_reminders(
    root: Path, cron: Any, *, timezone_name: str | None = None, now: datetime | None = None,
) -> int:
    """Le attese aperte di ``reminders.json`` diventano job del quaderno *root*.

    Torna quante ne ha scritte. Senza scheduler (*cron* ``None``) o senza il file
    non fa niente. Va chiamata sul loop, come ogni gesto sullo scheduler.
    """
    from jenny.board.cron_link import add_notebook_job

    path = legacy_reminders_path(root)
    if cron is None or not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("board: {} is unreadable ({}): its reminders are not migrated", path, exc)
        _set_aside(path, UNREADABLE_SUFFIX)
        return 0
    board = read_board(root) or model.empty_board()
    current = now or datetime.now().astimezone()
    now_ms = int(current.timestamp() * 1000)
    migrated = 0
    for rem in (raw.get("reminders") or []) if isinstance(raw, dict) else []:
        if not isinstance(rem, dict) or rem.get("state", "open") != "open":
            continue
        rid = str(rem.get("id") or "?")
        text = model.clean_text(rem.get("text"), model.MAX_TITLE * 2)
        if not text:
            continue
        try:
            schedule, condition = _legacy_when(rem.get("when"), timezone_name, now_ms)
        except ValueError as exc:
            logger.warning("board: reminder {} of {} not migrated: {}", rid, root.name, exc)
            continue
        card = rem.get("card")
        scope = (card if isinstance(card, str) and card and model.card_by_id(board, card)
                 else model.NOTEBOOK)
        why = rem.get("why") if isinstance(rem.get("why"), str) else ""
        why = why.removeprefix("wiki/") if why and (root / "wiki" / why).is_file() else None
        meta = {
            "by": rem.get("by") if rem.get("by") in (model.USER, model.JENNY) else model.USER,
            "legacyId": rid,
        }
        try:
            add_notebook_job(cron, root.name, scope, name=text, message=text,
                             schedule=schedule, why=why, condition=condition,
                             origin_metadata=meta)
        except ValueError as exc:
            logger.warning("board: reminder {} of {} not migrated: {}", rid, root.name, exc)
            continue
        migrated += 1
    _set_aside(path, MIGRATED_SUFFIX)
    logger.info("board: {} reminder(s) of {} moved from reminders.json to the scheduler",
                migrated, root.name)
    return migrated


def _legacy_when(when: Any, timezone_name: str | None, now_ms: int) -> tuple[Any, Any]:
    """Lo ``schedule`` e la ``condition`` di un'attesa di prima, o ``ValueError``."""
    from jenny.board.clock import CLOCK_EVERY_MS
    from jenny.cron.dates import date_schedule
    from jenny.cron.types import CronSchedule

    if not isinstance(when, dict):
        raise ValueError("no when")
    if when.get("kind") == "date":
        day = str(when.get("date") or "")
        value = day[:7] if when.get("precision") == "month" else day[:10]
        schedule = date_schedule(value, timezone_name)
        if (schedule.at_ms or 0) <= now_ms:
            # Già scaduta: suona al primo giro, con la precisione che aveva.
            schedule = CronSchedule(kind="at", at_ms=now_ms + _LATE_RING_MS,
                                    precision=schedule.precision)
        return schedule, None
    if when.get("kind") == "condition":
        if not model.is_valid_id(when.get("app")):
            raise ValueError("a condition on a micro app of before has no app to read")
        condition = {
            "app": when["app"],
            "params": dict(when["params"]) if isinstance(when.get("params"), dict) else {},
            "field": when.get("field"), "op": when.get("op"), "value": when.get("value"),
        }
        return CronSchedule(kind="every", every_ms=CLOCK_EVERY_MS), condition
    raise ValueError(f"unknown when {when.get('kind')!r}")


def _set_aside(path: Path, suffix: str) -> None:
    try:
        os.replace(path, path.with_name(path.name + suffix))
    except OSError as exc:
        logger.error("board: {} not set aside ({}): it will be read again", path, exc)
