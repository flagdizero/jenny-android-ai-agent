"""Dove si giudica un monitor che ha passato il controllo a un aiutante.

L'agente principale gira in ``orchestrator_mode`` e non ha ``python_exec``: un
monitor che deve *fare* qualcosa per controllare lo delega con ``spawn``, che
ritorna subito. Il turno del monitor finisce prima che l'aiutante abbia
cominciato, e il suo silenzio non prova niente: fino al 31/10/2026 veniva
registrato ``silenced``, la sequenza dei controlli mancati si azzerava e l'esito
dell'aiutante si perdeva.

Ora il turno del monitor dichiara ``CHECK_DELEGATED`` e il run resta
``delegated`` (``CronJobState.pending_since_ms``). Il giudice giusto è il **turno
d'annuncio dell'aiutante**, che torna nella sessione del monitor (``cron:<id>`` o
``cron:nb:<quaderno>/<id>``): è l'unico che abbia il risultato in mano. Questo
modulo gli dà il vocabolario per dichiararlo (:meth:`MonitorFollowup.prompt_block`)
e ne rilegge il testo finale (:meth:`MonitorFollowup.record`). Un verdetto che
non arriva mai lo chiude il run successivo (:func:`resolve_pending_delegation`).

**Perché passa dal ``CronService``.** Il turno d'annuncio non nasce dal
dispatcher cron: nasce dal bus, e lo esegue ``AgentLoop``. Il servizio cron è
l'unico oggetto che entrambi hanno già in mano (``AgentLoop.cron_service``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from jenny.cron.could_not_check import (
    COULD_NOT_CHECK_MARKER,
    OK_MARKER,
    WARNED_MARKER,
    could_not_check_reason,
    parse_ok_marks,
    parse_warned_marks,
)
from jenny.cron.silence_watchdog import alert_silently_broken_checks
from jenny.cron.types import CronJobState
from jenny.session.keys import CRON_SESSION_PREFIX, NOTEBOOK_CRON_SESSION_PREFIX

if TYPE_CHECKING:
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob


def job_id_of_monitor_session(session_key: str) -> str | None:
    """L'id del job di una sessione di monitor (``cron:<id>``, ``cron:nb:<nb>/<id>``)."""
    if session_key.startswith(NOTEBOOK_CRON_SESSION_PREFIX):
        _, _, job_id = session_key[len(NOTEBOOK_CRON_SESSION_PREFIX):].partition("/")
        return job_id or None
    if session_key.startswith(CRON_SESSION_PREFIX):
        job_id = session_key[len(CRON_SESSION_PREFIX):]
        return job_id if job_id and ":" not in job_id and "/" not in job_id else None
    return None


def resolve_pending_delegation(state: CronJobState) -> bool:
    """Chiude una delega di cui non è mai arrivato il verdetto. Vero se ce n'era una.

    Va chiamata **all'inizio** di un run, prima di decidere l'escalation: una
    delega ancora in sospeso vuol dire che il ciclo precedente si è chiuso senza
    che nessuno dichiarasse niente. Vale la regola dei monitor — nessuna
    dichiarazione, quindi il controllo è avvenuto — e la sequenza riparte. Ciò
    che è già stato detto all'utente (``could_not_check_escalated``) resta: è un
    fatto nostro, non del controllo, e buttarlo farebbe riavvisare dello stesso
    guasto.
    """
    if state.pending_since_ms is None:
        return False
    state.pending_since_ms = None
    state.consecutive_could_not_check = 0
    state.could_not_check_since_ms = None
    return True


_FOLLOWUP_BLOCK = (
    "[This result belongs to a recurring check you run silently for the user, and you "
    "handed that check to the helper whose result is above. Now judge it.\n"
    "The check: {message}\n"
    "If the user needs to know something, call the `message` tool; otherwise say nothing. "
    "Your final text reaches nobody. End it with exactly one of these lines:\n"
    f"{OK_MARKER} — the check ran (whatever it found).\n"
    f"{COULD_NOT_CHECK_MARKER}: <one short line naming what stopped it> — the check did NOT "
    "run (the helper failed, a host was unreachable, a value never arrived).\n"
    "{escalation}"
    f"If you told the user that this check is not working, add one more line: {WARNED_MARKER}]"
)

_ESCALATE = (
    "This check has already failed {runs} times in a row and the user has not been told. "
    "If it did not run this time either, tell them now with `message` — plainly, in their "
    "language, what is not working — and write the warning line below.\n"
)

_ALREADY_WARNED = (
    "The user already knows this check is not working: do not tell them again.\n"
)


class MonitorFollowup:
    """Il turno d'annuncio di un aiutante, visto dal monitor che l'ha lanciato."""

    def __init__(self, *, cron: "CronService", now_ms: Callable[[], int]) -> None:
        self._cron = cron
        self._now_ms = now_ms

    def _pending_job(self, session_key: str) -> "CronJob | None":
        """Il monitor in attesa di verdetto di questa sessione, o ``None``.

        Chiamato per **ogni** annuncio di subagent: fuori da una sessione di
        monitor costa un confronto fra stringhe.
        """
        job_id = job_id_of_monitor_session(session_key)
        if job_id is None:
            return None
        job = self._cron.get_job(job_id)
        if job is None or job.payload.mode != "monitor" or job.state.pending_since_ms is None:
            return None
        return job

    def prompt_block(self, session_key: str) -> str:
        """Le righe da aggiungere al turno d'annuncio; stringa vuota il resto delle volte."""
        job = self._pending_job(session_key)
        if job is None:
            return ""
        from jenny.cron.bound_runner import (
            has_already_warned_could_not_check,
            should_escalate_could_not_check,
        )

        escalation = ""
        if should_escalate_could_not_check(job.state):
            escalation = _ESCALATE.format(runs=job.state.consecutive_could_not_check)
        elif has_already_warned_could_not_check(job.state):
            escalation = _ALREADY_WARNED
        return _FOLLOWUP_BLOCK.format(message=job.payload.message, escalation=escalation)

    def record(self, session_key: str, *, final_text: str, spoke: bool = False) -> None:
        """Registra il verdetto scritto dal turno d'annuncio.

        Un annuncio che non dichiara niente lascia la delega in sospeso: la chiude
        il run dopo (:func:`resolve_pending_delegation`).
        """
        job = self._pending_job(session_key)
        if job is None:
            return
        reason = could_not_check_reason(final_text)
        ok = bool(parse_ok_marks(final_text))
        if reason is None and not ok:
            logger.debug("Monitor follow-up: no verdict for job {}", job.id)
            return
        state = job.state
        state.pending_since_ms = None
        if reason is not None:
            state.last_status = "could_not_check"
            state.last_error = reason or None
            state.consecutive_could_not_check += 1
            if state.could_not_check_since_ms is None:
                state.could_not_check_since_ms = self._now_ms()
            if parse_warned_marks(final_text):
                state.could_not_check_escalated = True
            logger.warning(
                "Cron: delegated check of job '{}' could not run ({} in a row): {}",
                job.name, state.consecutive_could_not_check, reason or "no reason given",
            )
        else:
            state.last_status = "ok" if spoke else "silenced"
            state.last_error = None
            state.consecutive_could_not_check = 0
            state.could_not_check_since_ms = None
            state.could_not_check_escalated = False
        if state.run_history and state.run_history[-1].status == "delegated":
            state.run_history[-1].status = state.last_status
            state.run_history[-1].error = state.last_error
        self._cron.persist_job_state()
        if reason is not None:
            alert_silently_broken_checks(job.name, state, now_ms=self._now_ms())
