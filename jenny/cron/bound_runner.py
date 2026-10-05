"""Execution helpers for session-bound cron jobs."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, Protocol

from jenny.agent.tools.cron import CronTool
from jenny.agent.turn_types import TurnOutcome
from jenny.board.cron_link import last_message_text, link_alive, record_result
from jenny.bus.events import InboundMessage
from jenny.cron.could_not_check import (
    ESCALATE_AFTER_FAILURES,
    ESCALATION_ASK_LIMIT,
    could_not_check_reason,
    parse_warned_marks,
)
from jenny.cron.session_delivery import origin_delivery_context
from jenny.cron.session_turns import (
    CRON_DEFER_UNTIL_IDLE_META,
    CRON_MONITOR_META,
    CRON_TRIGGER_META,
    monitor_session_key,
)
from jenny.cron.types import (
    CronJob,
    CronJobSilencedError,
    CronJobState,
    CronMonitorCouldNotCheckError,
)
from jenny.cron.webui_metadata import cron_proactive_delivery_metadata
from jenny.runtime.power import keep_awake
from jenny.session.keys import notebook_cron_session_key
from jenny.session.turn_visibility import mark_silent_turn
from jenny.utils.prompt_templates import render_template

# Coda di storico tenuta nella sessione isolata di un monitor. Stesso valore del
# default di ``heartbeat.keep_recent_messages``: un monitor gira all'infinito e
# senza potatura la sua sessione cresce senza limite.
MONITOR_KEEP_RECENT_MESSAGES = 8

# Scadenza del wakelock che copre l'esecuzione di un job cron. Tag distinto da
# quello del turno perché il job può essere DIFFERITO (``CRON_DEFER_UNTIL_IDLE``)
# e restare in attesa che la sessione si liberi: il lavoro vero è tutto dentro
# questo blocco, e senza CPU un job che scade a schermo spento resta appeso
# invece di girare — è la causa misurata degli scarti fra 30 e 83 minuti su un
# cron da mezz'ora.
CRON_WAKELOCK_TIMEOUT_S = 1800.0

# Marcatore, soglia e parser vivono in :mod:`jenny.cron.could_not_check`: da
# quando l'heartbeat ha lo stesso terzo esito (per-task), i lettori sono due e
# la forma che il modello scrive deve restare una sola. Ri-esportati qui coi
# nomi con cui il monitor li conosce.
MONITOR_ESCALATE_AFTER_FAILURES = ESCALATE_AFTER_FAILURES


def has_already_warned_could_not_check(state: CronJobState) -> bool:
    """L'utente sa già che questo controllo non funziona, e nulla è cambiato.

    Serve a *chiedere di tacere*, non solo a smettere di chiedere di parlare:
    finché c'era il solo ramo di escalation, un monitor già annunciato tornava
    a ricevere il prompt di sempre con il proprio avviso di due ore prima ancora
    in coda alla sessione e il guasto ancora davanti — ed è la situazione in cui
    il modello, misurato sul device, ha richiamato ``message`` di testa sua
    (v. la docstring di ``already_warned_block`` in
    :mod:`jenny.cron.heartbeat_tasks`, dove il caso è documentato per esteso).

    Unica lettura di ``could_not_check_escalated``, e :func:`should_escalate_could_not_check`
    la nega: i due rami finiscono nello stesso prompt e se divergessero di un
    caso quel prompt chiederebbe insieme di parlare e di tacere dello stesso
    controllo. Nel template sono comunque ``if``/``elif``, che è la seconda
    metà della stessa garanzia.
    """
    return state.could_not_check_escalated


def should_escalate_could_not_check(state: CronJobState) -> bool:
    """True quando è QUESTO run a dover avvisare, se anche lui non controlla.

    Il conteggio nello stato riguarda i run già conclusi, quindi la soglia si
    confronta con ``K - 1``: con K=3 l'istruzione entra nel prompt del terzo
    tentativo, ed è quel turno — l'unico che sappia se il controllo è riuscito
    adesso — a decidere se chiamare ``message``. Nessun turno in più, e nessuna
    consegna generata da fuori il turno: il dispatcher cron non ne ha una, per
    scelta (v. la docstring di ``jenny/runtime/cron_dispatch.py``).

    E una finestra, non una semiretta: si chiede per ``ESCALATION_ASK_LIMIT`` run
    e poi si smette. Il timbro che chiuderebbe la richiesta è una riga che il
    modello deve scrivere (``CHECK_WARNED``), e un modello che non la scrive mai
    si vedrebbe altrimenti chiedere l'avviso ogni mezz'ora per sempre. Dove
    finisce la finestra comincia ``silence_watchdog``, che non passa dal modello.
    """
    streak = state.consecutive_could_not_check
    return (
        not has_already_warned_could_not_check(state)
        and MONITOR_ESCALATE_AFTER_FAILURES - 1
        <= streak
        < MONITOR_ESCALATE_AFTER_FAILURES - 1 + ESCALATION_ASK_LIMIT
    )


class BoundCronAgent(Protocol):
    tools: Any
    sessions: Any

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        ...


class CronRunRecorder(Protocol):
    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        ...


def _cron_prompt_ref(prompt: str, *, monitor: bool = False) -> dict[str, Any]:
    return {
        "id": "cron.agent_turn.monitor" if monitor else "cron.agent_turn.reminder",
        "version": 1,
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }


def _bound_session_delivery_context(
    job: CronJob,
    *,
    turn_seed: str,
    source_label: str | None,
    monitor: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    channel, chat_id, metadata = origin_delivery_context(job)

    if monitor:
        # Un monitor è lavoro interno: dichiararlo esplicitamente qui è ciò che
        # rende muto tutto il turno (risposta finale, progress, reasoning,
        # spinner, marcatore di fine turno) — vedi
        # :mod:`jenny.session.turn_visibility`.
        mark_silent_turn(metadata)
        # ``origin_metadata`` è stato catturato quando l'utente ha creato il job,
        # quindi può contenere ``_wants_stream`` della sua sessione WebUI. Il gate
        # di ``AgentLoop._dispatch`` non streamma comunque un turno silenzioso;
        # il flag va rimosso perché portarselo dietro sarebbe uno stato ereditato
        # e falso, non perché serva come difesa.
        metadata.pop("_wants_stream", None)

    if channel == "websocket":
        metadata["webui"] = True
        metadata.update(
            cron_proactive_delivery_metadata(
                "websocket",
                metadata,
                turn_seed=turn_seed,
                source_label=source_label,
            )
        )

    return channel, chat_id, metadata


def _prune_monitor_session(agent: BoundCronAgent, session_key: str) -> None:
    """Tiene limitata la sessione isolata di un monitor, che gira all'infinito."""
    session = agent.sessions.get_or_create(session_key)
    session.retain_recent_legal_suffix(MONITOR_KEEP_RECENT_MESSAGES)
    agent.sessions.save(session)


async def run_bound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Execute a session-bound cron job as a normal agent session turn.

    Guscio sottile attorno a :func:`_run_bound_cron_job`: tiene la CPU sveglia
    per tutta la durata del job. Il turno che ne nasce prenderà anche il proprio
    lock ``"turn"`` — sono tag diversi e indipendenti, e il conteggio di ciascuno
    torna a zero per conto suo; qui serve comunque, perché la parte del job
    fuori dal turno (attesa della sessione libera, scrittura delle run record,
    potatura della sessione monitor) resta scoperta.
    """
    async with keep_awake("cron", timeout_s=CRON_WAKELOCK_TIMEOUT_S):
        return await _run_bound_cron_job(job, agent=agent, cron=cron)


async def _run_bound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Corpo del job: identico a prima, senza la gestione del wakelock."""
    session_key = job.payload.session_key
    if not session_key:
        raise ValueError(f"cron job {job.id} is missing payload.session_key")

    monitor = job.payload.mode == "monitor"
    board_link = job.payload.board
    if board_link is not None and not link_alive(board_link):
        # Il quaderno o la scheda non ci sono piu': il cron non ha piu' casa.
        raise ValueError(f"notebook gone: {board_link.get('notebook')}/{board_link.get('card')}")
    # Un monitor gira in una sessione tutta sua: ``payload.session_key`` resta il
    # target di consegna e il valore registrato nella run record, ma non è più la
    # sessione in cui il turno viene eseguito. Quella di un cron del quaderno
    # porta il nome del quaderno, da cui il turno ricava il recinto.
    if monitor and board_link is not None:
        turn_session_key = notebook_cron_session_key(str(board_link["notebook"]), job.id)
    else:
        turn_session_key = monitor_session_key(job.id) if monitor else session_key

    # L'escalation si decide PRIMA del turno, perché è una riga di prompt: solo
    # il modello, dentro il turno, sa se il controllo è riuscito adesso, ed è
    # anche l'unico che possa consegnare (tool ``message``).
    escalate = monitor and should_escalate_could_not_check(job.state)
    prompt = render_template(
        "agent/cron_monitor.md" if monitor else "agent/cron_reminder.md",
        strip=True,
        message=job.payload.message,
        # Assenti dal template del reminder e falsi sul monitor normale: il
        # prompt reso resta byte-identico a prima finché non c'è un guasto.
        escalate=escalate,
        failed_runs=job.state.consecutive_could_not_check if escalate else 0,
        # Mutuamente esclusivo con ``escalate`` per costruzione, e nel template
        # è l'``elif`` dello stesso ``if``.
        already_warned=monitor and has_already_warned_could_not_check(job.state),
    )
    prompt_ref = _cron_prompt_ref(prompt, monitor=monitor)
    run_id = f"{job.id}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"
    channel, chat_id, metadata = _bound_session_delivery_context(
        job,
        turn_seed=f"cron:{job.id}",
        source_label=job.name,
        monitor=monitor,
    )
    if monitor:
        metadata[CRON_MONITOR_META] = True
    metadata[CRON_TRIGGER_META] = {
        "job_id": job.id,
        "job_name": job.name,
        "run_id": run_id,
        "prompt_ref": prompt_ref,
        "persist_content": (
            f"Scheduled cron job triggered: {job.name}\n\n{job.payload.message}"
        ),
    }
    metadata[CRON_DEFER_UNTIL_IDLE_META] = True
    run_record_base: dict[str, Any] = {
        "job_id": job.id,
        "job_name": job.name,
        "session_key": session_key,
        "prompt_ref": prompt_ref,
        "prompt_vars": {"message": job.payload.message},
        "rendered_prompt": prompt,
    }

    cron.write_run_record(
        run_id,
        {
            **run_record_base,
            "status": "queued",
        },
    )

    cron_tool = agent.tools.get("cron")
    cron_token = None
    if isinstance(cron_tool, CronTool):
        cron_token = cron_tool.set_cron_context(True)
    try:
        outcome = await agent.submit_cron_turn(
            InboundMessage(
                channel=channel,
                sender_id="cron",
                chat_id=chat_id,
                content=prompt,
                metadata=metadata,
                session_key_override=turn_session_key,
            )
        )
    except (Exception, asyncio.CancelledError) as exc:
        error_text = str(exc) or exc.__class__.__name__
        cron.write_run_record(
            run_id,
            {
                **run_record_base,
                "status": "error",
                "error": error_text,
            },
        )
        raise
    finally:
        if isinstance(cron_tool, CronTool) and cron_token is not None:
            cron_tool.reset_cron_context(cron_token)

    response = outcome.text

    if board_link is not None:
        said = response or ""
        if monitor and outcome.spoke:
            try:
                said = last_message_text(agent.sessions.get_or_create(turn_session_key))
            except Exception:  # noqa: BLE001 — l'esito nel cassetto e' una cortesia
                said = ""
            said = said or outcome.final_text
        record_result(board_link, status="ok" if (outcome.spoke or not monitor) else "silent",
                      text=said if (outcome.spoke or not monitor) else "", job_name=job.name)

    if monitor:
        _prune_monitor_session(agent, turn_session_key)
        # Terzo stato, e va guardato PRIMA di ``spoke``: un monitor che non ha
        # potuto controllare resta un monitor che non ha controllato anche
        # quando ha parlato per dirlo. Legarlo a ``spoke`` azzererebbe la
        # sequenza proprio sul run dell'escalation, e l'avviso ripartirebbe ogni
        # tre cicli per sempre.
        reason = could_not_check_reason(outcome.final_text)
        if reason is not None:
            cron.write_run_record(
                run_id,
                {
                    **run_record_base,
                    "status": "could_not_check",
                    "reason": reason or None,
                    "delivery": "agent_message" if outcome.spoke else "suppressed",
                },
            )
            raise CronMonitorCouldNotCheckError(
                f"cron monitor job {job.id} could not run its check",
                reason=reason or None,
                # Il timbro viene da ciò che il turno **dichiara**, non da un suo
                # effetto collaterale. Storia breve di come si è arrivati qui,
                # perché è il genere di riga che qualcuno riscriverà.
                #
                # Con ``outcome.spoke`` da solo: ``spoke`` è di turno e non ha
                # soggetto — è vero per QUALUNQUE ``message`` riuscito. Ma
                # ``cron_monitor.md:11`` autorizza esplicitamente il monitor a
                # segnalare "una soglia superata, una scadenza in arrivo", e
                # ``message.py:251`` ne lascia passare uno solo per run
                # silenzioso. Un turno che riporta un risultato legittimo e poi
                # non riesce a completare il controllo si timbrava ``escalated``
                # allo streak 1 per un messaggio che del guasto non parlava. Da
                # lì ``cron_monitor.md`` dice "non dirglielo di nuovo, qualunque
                # cosa tu trovi" — per sempre, perché il re-arm su messaggio
                # utente itera ``state.task_checks``, che per un monitor è vuoto.
                # Misurato il 2026-08-17 sul dispatcher vero: 19 run, zero
                # avvisi, controllo morto tutto il tempo.
                #
                # Con ``escalate and outcome.spoke``: un avviso spontaneo sotto
                # soglia non veniva registrato, la soglia scattava lo stesso due
                # giri dopo e l'utente sentiva la stessa cosa due volte
                # (misurato alle 10:19). Si scelse il doppione, cioè la
                # direzione d'errore giusta fra due letture entrambe sbagliate.
                #
                # ``CHECK_WARNED`` toglie la scelta: il turno dice di aver
                # avvisato, e il timbro registra quello. Nessun ``escalate and``
                # — un avviso di propria iniziativa è un avviso, e registrarlo è
                # esattamente ciò che impedisce il doppione due giri dopo. Se il
                # modello dimentica la riga il guasto si ripete: rumore, non
                # silenzio.
                escalated=bool(parse_warned_marks(outcome.final_text)),
            )
        # L'esito lo dice da sé: per un monitor l'outbound finale è sempre None,
        # e ``spoke`` distingue "ho parlato col tool ``message``" da "non avevo
        # nulla da riferire" — che è un successo, non un fallimento.
        if not outcome.spoke:
            cron.write_run_record(
                run_id,
                {
                    **run_record_base,
                    "status": "silenced",
                    "delivery": "suppressed",
                },
            )
            raise CronJobSilencedError(f"cron monitor job {job.id} had nothing to report")

    record: dict[str, Any] = {
        **run_record_base,
        "status": "ok",
        "response": response,
    }
    if monitor:
        # L'unica consegna possibile per un monitor è il tool ``message``.
        record["delivery"] = "agent_message"
    cron.write_run_record(run_id, record)
    return response
