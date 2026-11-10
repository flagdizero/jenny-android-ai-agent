"""Quel che i pezzi del driver dei piani hanno in comune: costanti, piccoli tipi, aiuti sui passi.

Sta in un modulo a sé perché lo importano sia ``driver.py`` sia i suoi mixin
(``driver_*.py``); è testo spostato da ``driver.py``, non riscritto.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from jenny.plan.audit import Audit
from jenny.plan.model import Plan, PlanError, Step, apply_transition, now_iso
from jenny.plan.place import PlanPlace, notebook_place, place_from_id

# Lo stato del promemoria di un passo, nell'``extra`` del passo: ``sent`` è
# partito e il suo turno non è ancora finito, ``used`` è stato speso. Un solo
# promemoria per tratto senza aiutanti: la lezione dei nove solleciti in 45
# secondi è che uno sprone che si ripete non sblocca niente.
_NUDGE = "nudge"


# I buchi che restano alla chiusura di un passo (budget finito, o un passo che
# non apre giri) non si ereditano: fino al 26/10 andavano al passo di controllo
# — o all'ultimo passo qualunque fosse, e sul Titan «Riscrivi i drawer» ha
# ricevuto 7 temi di ricerca — e lì si pagava un altro giro. Ora vanno in un
# file del quaderno, che la pianificazione successiva legge, e nelle pagine come
# «non documentato». Scritto nel registro del piano, col passo: il ritorno lo toglie.
OPEN_GAPS_PATH = "raw/notes/open-gaps.md"


_GAPS_LISTED = "gaps_listed"


class _DraftsUnreadableError(Exception):
    """Le bozze del quaderno non si leggono: chi decide una chiusura rifiuta e riprova."""


# Il controllo a macchina del passo ``check`` (``jenny/plan/audit.py``),
# nell'``extra`` del passo: il giro (1 all'apertura, 2 dopo il primo rifiuto),
# cosa ha trovato, e alla chiusura cosa resta. Nell'``extra`` del piano, la base:
# i difetti che il quaderno aveva all'Approva, che non sono lavoro del piano.
_MACHINE = "machine_check"


_AUDIT_BASELINE = "audit_baseline"


_EMPTY_AT_START = "empty_folders_at_start"


# Le voci rimaste che la scheda mostra, al massimo.
_MAX_LEFT_ITEMS = 12


_RUNNING = frozenset({"running", "pausing"})


# Gli stati in cui il resoconto di un aiutante del passo si registra: anche
# fermo su una domanda, perché gli aiutanti partiti prima del ``plan_blocked``
# rientrano lì, e scartarli perdeva i loro buchi (Titan, 26/10).
_REPORTING = _RUNNING | frozenset({"blocked"})


# I due versi di un ritorno, nell'intestazione del journal: il boot li rilegge
# per portare il piano dove il ritorno l'avrebbe portato.
_KIND_ROLLBACK = "rollback"


_KIND_UNDO = "undo"


# Il ritorno in corso, scritto nel piano prima di toccare un file: il registro in
# cui annota, il tipo, i gruppi tenuti. Il boot lo chiude (:meth:`_recover_undo`).
PENDING_UNDO_KEY = "pending_undo"


_MAX_NOTE = 500


# Nell'``extra`` di un passo fermo sulle sue bozze: come il modello l'ha chiuso
# (``done``/``skipped``) e la sua nota, per chiuderlo così quando l'utente ha
# deciso; e cosa ha deciso per ogni bozza, per la nota scritta dal codice.
_PENDING_OUTCOME = "pending_outcome"


_PENDING_NOTE = "pending_note"


_DRAFT_OUTCOMES = "draft_outcomes"


# Una domanda che aspetta dietro le bozze del passo, e il suo motivo.
_PENDING_QUESTION = "pending_question"


_PENDING_REASON = "pending_reason"


# Lo stato a cui tornare quando il blocco si scioglie: ``pausing`` se l'utente
# aveva chiesto la Pausa prima del blocco, o durante.
_RESUME_TO = "resume_to"


# Nell'``extra`` del piano: l'istante dell'Approva in millisecondi.
_APPROVED_MS = "approved_ms"


class PlanCommandError(PlanError):
    """Un rifiuto per l'utente, con il codice che la RPC rimanda.

    Codici: ``not_found``, ``conflict`` (il mondo si è mosso: una versione più
    nuova, un quaderno cambiato dopo il conto del ritorno), ``busy`` (un turno
    o un aiutante in volo), ``bad_state`` (non da questo stato),
    ``bad_request``, ``plan_running`` (un piano più nuovo tiene il quaderno),
    ``rollback_failed`` (il ritorno è partito e si è fermato a metà).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PlanHost(Protocol):
    """Quel che il driver chiede all'agente. In produzione è ``LoopPlanHost``."""

    def turn_in_flight(self, session_key: str) -> bool: ...

    def running_helpers(self, session_key: str) -> frozenset[str] | None: ...

    async def cancel(self, session_key: str) -> int: ...

    def notebook_root(self, notebook: str) -> Path | None: ...

    def append_note(self, session_key: str, text: str) -> None: ...

    def notebook_drafts(self, notebook: str) -> list[dict[str, Any]]:
        """Le bozze in attesa (``staged_writes``) con il file da cambiare nel quaderno.

        Ognuna con ``id``, ``target`` (relativo alla radice delle bozze, come lo
        mostra ``staged.list``) e ``created_ms``.
        """
        ...

    def discard_draft(self, draft_id: str) -> bool:
        """Butta una bozza in attesa. ``True`` se c'era."""
        ...


@dataclass
class _Live:
    """Una voce dell'indice in memoria: il piano aperto di una sessione."""

    plan_id: str
    notebook: str
    status: str
    step_now: bool


@dataclass
class _HeldResults:
    """I resoconti di aiutanti di una sessione scritti in storia senza turno.

    Il gruppo è ``(plan_id, plan_step)`` degli aiutanti: si tengono solo
    resoconti dello stesso gruppo, finché un fratello ``running`` lavora. Gli id
    restano qui fino al turno che li fa vedere a Jenny (``take_held_results``):
    è lì che il loop li marca ``handled``.
    """

    plan_id: str | None
    plan_step: str | None
    task_ids: list[str]
    first_at: float
    timer: asyncio.Task[Any] | None = None
    #: Il risveglio di sicurezza è già partito: non se ne pubblica un secondo.
    woken: bool = False


def _step_now(plan: Plan) -> Step | None:
    for step in plan.steps:
        if step.status == "now":
            return step
    return None


def place_of(plan: Plan) -> PlanPlace:
    return place_from_id(plan.place) or notebook_place(plan.notebook)


def _cut(text: str, limit: int, what: str) -> str:
    """*text* entro *limit*, e il taglio nel log: fino al 26/10 era muto."""
    if len(text) <= limit:
        return text
    logger.info("Plan: {} cut from {} to {} characters", what, len(text), limit)
    return text[:limit]


def _machine_view(audit: Audit, *, round_: int, final: bool = False) -> dict[str, Any]:
    """Il controllo a macchina nell'``extra`` del passo: conteggi, e alla fine cosa resta."""
    view: dict[str, Any] = {
        "round": round_,
        "pages": audit.pages,
        "cards": audit.cards,
        "open_reminders": audit.open_reminders,
        "counts": audit.counts(),
        "preexisting": audit.preexisting,
        "not_documented": audit.not_documented,
        "removed_dirs": audit.removed_dirs,
    }
    if final:
        view["final"] = True
        view["left"] = len(audit.findings)
        view["left_items"] = [
            f"{f.path}: {f.detail}" for f in audit.findings[:_MAX_LEFT_ITEMS]
        ]
    return view


def _find(plan: Plan, step_id: str) -> Step:
    for step in plan.steps:
        if step.id == step_id:
            return step
    raise PlanError(f"plan {plan.id} has no step {step_id}")


def _block(
    plan: Plan,
    step_id: str,
    question: str,
    reason: str,
    *,
    kind: str = "question",
    drafts: list[dict[str, str]] | None = None,
) -> None:
    step = _find(plan, step_id)
    step.status = "blocked"
    step.extra.pop(_NUDGE, None)
    if plan.status == "pausing":
        # La Pausa chiesta prima del blocco si ricorda: sciolto il blocco, il
        # passo finisce e il piano va in pausa, invece di ripartire.
        step.extra[_RESUME_TO] = "pausing"
    stamp = now_iso()
    apply_transition(plan, "blocked", at=stamp, step=step_id, reason=reason)
    plan.blocked = {
        "step": step_id,
        "question": question,
        "reason": reason,
        "at": stamp,
        "kind": kind,
        "drafts": [dict(d) for d in drafts or ()],
    }


class _UnchangedError(Exception):
    """Il piano è già nello stato che la scrittura gli darebbe."""
