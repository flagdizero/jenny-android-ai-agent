"""I comandi ``turn.*``: «Ho fatto» sotto una risposta, e tornare a prima.

Il registro di ogni turno ordinario (``turn-<id>``) e la sua riga in
``undo.sqlite`` li scrive ``agent/turn_work.py``; qui li legge e li usa la
casa, con l'id del turno del verbale (lo stesso ``turn_id`` dei frame).

- ``turn.summaries {turns}`` → per ogni turno che ha toccato qualcosa, i conti
  della riga: ``{files, jobs, others, working, state}``. Un turno che non ha
  toccato niente non compare: niente riga sotto la sua risposta. Un turno che
  ha solo cercato sul web nemmeno — le ricerche si elencano dentro, quando c'e'
  dell'altro.
- ``turn.work {turn}`` → l'elenco intero, con il gettone dell'anteprima
  (``expect_end``) che ``turn.undo`` verifica.
- ``turn.undo {turn, expect_end}`` → rimette i file come prima del turno
  (anche quelli dei suoi aiutanti) e toglie i promemoria che il turno ha
  creato. Rifiuta finche' un aiutante del turno lavora (``busy``), e se i file
  sono cambiati dall'anteprima (``conflict``).
- ``turn.redo {turn}`` → l'«Annulla» dell'annulla: i file tornano come il turno
  li aveva lasciati, e i promemoria tornano (``CronService.restore_job``, finche'
  la sua finestra e' aperta).

Ogni annulla e ogni ripristino lasciano una nota in inglese nella sessione,
come fa il piano: senza, Jenny crederebbe che i file ci siano ancora.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.webui.commands import CommandContext, CommandError

# Quanti turni si chiedono in una volta: una pagina di storia.
MAX_TURNS = 120

# Le note che non bastano da sole a fare una riga: cercare non cambia niente.
_QUIET_NOTES = frozenset({"web_search", "web_fetch", "browser"})


def _root(ctx: CommandContext) -> Path:
    return Path(ctx.get_workspace_root())


def _turn(params: Mapping[str, Any]) -> str:
    from jenny.agent.turn_work import _TURN_ID_RE

    value = params.get("turn")
    if not isinstance(value, str) or not _TURN_ID_RE.match(value):
        raise CommandError("bad_request", "turn required")
    return value


def _cron(ctx: CommandContext) -> Any | None:
    getter = getattr(ctx, "get_cron_service", None)
    try:
        return getter() if getter else None
    except Exception:  # noqa: BLE001 — senza scheduler i promemoria non si toccano
        return None


class _JobsNow:
    """Quali promemoria esistono, letti sul loop: ``turn_items`` gira in un thread.

    Lo scheduler si legge sul loop, come fanno le route e il tool (in un thread
    ``list_jobs`` riassegnava lo store sotto di loro). Qui si fotografa prima, e
    il thread chiede alla foto.
    """

    def __init__(self, cron: Any | None) -> None:
        self._ids: set[str] = set()
        if cron is None:
            self._live = False
            return
        self._live = True
        try:
            self._ids = {j.id for j in cron.list_jobs(include_disabled=True)}
        except Exception:  # noqa: BLE001
            self._live = False

    def get_job(self, job_id: str) -> object | None:
        return True if self._live and job_id in self._ids else None

    @property
    def usable(self) -> "_JobsNow | None":
        return self if self._live else None


def _working(ctx: CommandContext, turn: str) -> bool:
    getter = getattr(ctx, "get_subagent_manager", None)
    try:
        manager = getter() if getter else None
        return bool(manager and manager.working_for_turn(turn))
    except Exception:  # noqa: BLE001
        return False


def summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    """I conti della riga chiusa: file, promemoria, il resto."""
    files = sum(1 for i in items if i.get("kind") == "file")
    jobs = sum(1 for i in items if i.get("kind") == "job")
    others = len(items) - files - jobs
    return {"files": files, "jobs": jobs, "others": others}


def worth_a_row(items: list[dict[str, Any]], working: bool) -> bool:
    """Se la risposta prende la riga «Ho fatto»: ha toccato qualcosa che resta.

    Le sole ricerche non bastano: ogni risposta che ha guardato il web
    porterebbe «Ho fatto: 1 ricerca». Un aiutante ancora al lavoro si',
    perche' i suoi file arriveranno.
    """
    if working:
        return True
    for item in items:
        if item.get("kind") in {"file", "job"}:
            return True
        if item.get("kind") != "file" and item.get("what") not in _QUIET_NOTES:
            return True
    return False


async def turn_summaries(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.turn_work import read_row, turn_items

    turns = params.get("turns")
    if not isinstance(turns, list) or len(turns) > MAX_TURNS:
        raise CommandError("bad_request", f"turns must be a list of at most {MAX_TURNS}")
    wanted = [_turn({"turn": t}) for t in turns]
    root = _root(ctx)
    cron = _JobsNow(_cron(ctx)).usable

    def read() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for turn in wanted:
            items = turn_items(root, turn, cron=cron)
            working = _working(ctx, turn)
            if not worth_a_row(items, working):
                continue
            row = read_row(root, turn)
            out[turn] = {**summarize(items), "working": working,
                         "state": row["state"] if row else "open"}
        return out

    return {"ok": True, "turns": await asyncio.to_thread(read)}


async def turn_work(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.turn_work import (
        STATE_OPEN,
        journals_of,
        turn_view,
        undo_journal_key,
    )
    from jenny.agent.work_done import undo_journals

    turn = _turn(params)
    root = _root(ctx)
    cron = _JobsNow(_cron(ctx)).usable
    working = _working(ctx, turn)

    def read() -> dict[str, Any]:
        view = turn_view(root, turn, cron=cron, working=working)
        token = None
        if view["state"] == STATE_OPEN and not working:
            try:
                report = undo_journals(root, journals_of(root, turn), undo_journal_key(turn),
                                       dry_run=True)
                token = report.token if report is not None else None
            except Exception:  # noqa: BLE001 — senza anteprima si mostra l'elenco
                logger.opt(exception=True).warning("Turn {}: undo preview failed", turn)
        return {"ok": True, **view, "expect_end": token}

    return await asyncio.to_thread(read)


def _note(ctx: CommandContext, session: str, text: str) -> None:
    getter = getattr(ctx, "get_plan_driver", None)
    try:
        driver = getter() if getter else None
        host = getattr(driver, "host", None)
        if host is not None and session:
            host.append_note(session, text)
    except Exception:  # noqa: BLE001 — la nota non deve far fallire il gesto
        logger.opt(exception=True).warning("Turn note for {} not written", session)


async def turn_undo(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.turn_work import (
        STATE_OPEN,
        journals_of,
        mark_state,
        read_row,
        undo_journal_key,
    )
    from jenny.agent.work_done import undo_journals
    from jenny.agent.write_journal import UndoConflictError

    turn = _turn(params)
    token = params.get("expect_end")
    if token is not None and not isinstance(token, str):
        raise CommandError("bad_request", "expect_end must be a string")
    if _working(ctx, turn):
        raise CommandError("busy", "a helper of this turn is still working")
    root = _root(ctx)
    row = read_row(root, turn)
    if row is not None and row["state"] != STATE_OPEN:
        raise CommandError("bad_state", f"this turn is {row['state']}")
    cron = _cron(ctx)

    def undo() -> dict[str, Any]:
        report = undo_journals(root, journals_of(root, turn), undo_journal_key(turn), token=token)
        counts = report.outcomes() if report is not None else {}
        mark_state(root, turn, "undone")
        return {"restored": len(counts.get("restored", ())), "removed": len(counts.get("removed", ())),
                "left": len(counts.get("left", ())), "jobs": 0}

    try:
        result = await asyncio.to_thread(undo)
    except UndoConflictError as exc:
        raise CommandError("conflict", "files changed since the preview") from exc
    # Lo scheduler sul loop, come le route e il tool ``cron``: due thread sul suo
    # store sarebbero due scrittori.
    if cron is not None and row is not None:
        for job in row["jobs"]:
            job_id = str(job.get("id") or "")
            if job_id and cron.get_job(job_id) is not None and cron.remove_job(job_id) == "removed":
                result["jobs"] += 1
    _note(ctx, row["session"] if row else "", (
        "The user went back to before one of your earlier turns: "
        f"{result['restored']} file(s) restored, {result['removed']} removed, "
        f"{result['left']} left as they were because they changed afterwards, "
        f"{result['jobs']} reminder(s) you had created removed. "
        "What that turn wrote is no longer there."
    ))
    return {"ok": True, **result}


async def turn_redo(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.turn_work import mark_state, read_row, undo_journal_key
    from jenny.agent.work_done import undo_journals

    turn = _turn(params)
    root = _root(ctx)
    row = read_row(root, turn)
    if row is None or row["state"] != "undone":
        raise CommandError("bad_state", "this turn was not undone")
    cron = _cron(ctx)

    def redo() -> dict[str, Any]:
        report = undo_journals(root, [undo_journal_key(turn)], f"{undo_journal_key(turn)}-r")
        counts = report.outcomes() if report is not None else {}
        mark_state(root, turn, "open")
        return {"restored": len(counts.get("restored", ())), "removed": len(counts.get("removed", ())),
                "jobs": 0}

    result = await asyncio.to_thread(redo)
    if cron is not None:
        for job in row["jobs"]:
            job_id = str(job.get("id") or "")
            if job_id and cron.get_job(job_id) is None and cron.restore_job(job_id) == "restored":
                result["jobs"] += 1
    _note(ctx, row["session"], (
        "The user undid going back: what that earlier turn of yours wrote is there again "
        f"({result['restored']} file(s) restored, {result['jobs']} reminder(s) back)."
    ))
    return {"ok": True, **result}


TURN_COMMANDS = {
    "turn.summaries": turn_summaries,
    "turn.work": turn_work,
    "turn.undo": turn_undo,
    "turn.redo": turn_redo,
}
