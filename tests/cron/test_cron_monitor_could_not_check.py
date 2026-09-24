"""Il terzo esito di un monitor: "non ho potuto controllare" (B8).

Prima di questo stato un monitor aveva due soli finali e producevano lo stesso
identico output — niente. Un controllo delle piante rotto era indistinguibile da
un giardino sano, e la skill aveva pure un motivo legittimo di tacere ("se hps è
irraggiungibile salta il ciclo in silenzio"), quindi il guasto era perfettamente
mimetizzato.

Il vincolo che questi test difendono, nell'ordine:

1. **Un giro senza novità non parla.** È il valore dell'heartbeat: costa zero
   quando non c'è niente da dire. Se un monitor sano dicesse qualcosa, la
   feature sarebbe sbagliata, non incompleta.
2. Un singolo fallimento non parla nemmeno lui: le reti cadono.
3. Tre fallimenti di fila producono **un** messaggio, e uno solo, finché lo
   stato non cambia.

L'agente finto qui sotto non è un mock che restituisce copioni: implementa il
contratto del prompt (parla solo se il prompt glielo chiede), quindi il test
misura davvero la catena prompt → esito → stato → prompt del giro dopo.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from support.sessions import FakeSessions

from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage
from jenny.cron.bound_runner import (
    MONITOR_ESCALATE_AFTER_FAILURES,
    could_not_check_reason,
    run_bound_cron_job,
)
from jenny.cron.could_not_check import ESCALATION_ASK_LIMIT
from jenny.cron.service import CronService
from jenny.cron.types import CronJobState, CronSchedule
from jenny.utils.prompt_templates import render_template

_MESSAGE = "controlla l'umidità delle piante e avvisami solo sotto il 15%"

# Il caso della frase più ordinaria che ci sia: l'utente chiede il controllo e
# nella stessa riga chiede di non essere disturbato quando l'host è giù.
_QUIET_MESSAGE = "controlla il server hps, e se è irraggiungibile non dire niente"

# Le due frasi del prompt su cui gli agenti finti decidono. Stanno qui, in un
# posto solo, perché sono l'unico punto in cui questi test conoscono la prosa
# del template: se la riscrivi, si aggiornano qui e i test di contratto qui
# sotto ti dicono subito che l'hai fatto.
_SILENCE_OVERRIDE = "still write the line"
_ALREADY_WARNED = "ALREADY been told"
_MUST_WARN = "must find out"


@cache
def _base_prompt(message: str = _MESSAGE) -> str:
    """Il prompt di un monitor sano.

    L'agente finto distingue i giri di escalation confrontandosi con questo
    invece di cercare una frase: così il test non si rompe se la prosa
    dell'escalation viene riscritta, e resta rotto se il prompt di un giro
    normale cambia — che è l'invariante da difendere. Pigro perché il workspace
    dei template lo configura una fixture, non l'import.
    """
    return render_template("agent/cron_monitor.md", strip=True, message=message)


class _FakeMonitorAgent:
    """Agente che *segue il prompt* invece di eseguire un copione.

    - ``healthy=True``: il controllo gira e non trova niente → silenzio, e
      nessun marcatore.
    - ``healthy=False``: il controllo non parte → marcatore nel testo finale,
      che non raggiunge nessuno.
    - Parla (tool ``message``) **solo** se il prompt di questo giro contiene
      l'istruzione di avvisare, e mai se contiene quella di tacere. Sono le sole
      due cose che il modello vero decide.
    """

    message = _MESSAGE

    def __init__(self) -> None:
        self.tools = ToolRegistry()
        self.sessions = FakeSessions()
        self.healthy = False
        self.messages: list[str] = []
        self.prompts: list[str] = []

    def _asked_to_warn(self, prompt: str) -> bool:
        # Il blocco "gliel'hai già detto" è l'altra cosa che cambia il prompt
        # rispetto a un giro sano: senza distinguerlo, "diverso dal base" vorrebbe
        # dire "avvisa" anche quando il prompt chiede l'opposto.
        return _ALREADY_WARNED not in prompt and prompt != _base_prompt(self.message)

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="Tutte le piante sopra il 15%.")
        if not self._asked_to_warn(msg.content):
            return TurnOutcome.silent(final_text="CHECK_FAILED: hps non raggiungibile")
        self.messages.append("Il controllo delle piante non riesce a partire da un po'.")
        # E dichiara l'avviso: il timbro nello stato viene da questa riga, non
        # dal fatto che ``message`` sia stato chiamato — un messaggio può parlare
        # d'altro, e per tre commit è stato quello a zittire il guasto.
        return TurnOutcome.spoke_via_tool(
            final_text="CHECK_FAILED: hps non raggiungibile\nCHECK_WARNED"
        )


def _monitor(
    tmp_path: Path,
    *,
    agent: _FakeMonitorAgent | None = None,
    message: str = _MESSAGE,
) -> tuple[CronService, str, _FakeMonitorAgent]:
    agent = agent or _FakeMonitorAgent()
    service = CronService(tmp_path / "cron" / "jobs.json")

    async def on_job(job: Any) -> str | None:
        return await run_bound_cron_job(job, agent=agent, cron=service)

    service.on_job = on_job
    job = service.add_job(
        name="piante",
        schedule=CronSchedule(kind="every", every_ms=1_800_000),
        message=message,
        mode="monitor",
        session_key="unified:default",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    return service, job.id, agent


async def _cycles(service: CronService, job_id: str, count: int) -> None:
    for _ in range(count):
        await service.run_job(job_id)


def _state(service: CronService, job_id: str) -> CronJobState:
    job = service.get_job(job_id)
    assert job is not None
    return job.state


class TestSilenceStaysFree:
    """Il caso che non deve regredire: un monitor sano non dice niente."""

    async def test_a_healthy_monitor_never_says_anything(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)
        agent.healthy = True

        await _cycles(service, job_id, 10)

        assert agent.messages == []
        state = _state(service, job_id)
        assert state.last_status == "silenced"
        assert state.consecutive_could_not_check == 0

    async def test_a_healthy_monitor_gets_the_plain_prompt_every_time(
        self, tmp_path: Path
    ) -> None:
        """Nessuna riga in più nel prompt finché non c'è un guasto."""
        service, job_id, agent = _monitor(tmp_path)
        agent.healthy = True

        await _cycles(service, job_id, 5)

        assert agent.prompts == [_base_prompt()] * 5

    async def test_a_single_failed_check_produces_no_message(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, 1)

        assert agent.messages == []
        state = _state(service, job_id)
        assert state.last_status == "could_not_check"
        assert state.consecutive_could_not_check == 1
        assert state.last_error == "hps non raggiungibile"

    async def test_two_failed_checks_still_produce_no_message(self, tmp_path: Path) -> None:
        """La soglia è tre: due sono ancora dentro il margine di una rete ballerina."""
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES - 1)

        assert agent.messages == []
        assert _state(service, job_id).consecutive_could_not_check == 2


class TestTheStreakSpeaksOnce:
    async def test_three_failed_checks_in_a_row_produce_exactly_one_message(
        self, tmp_path: Path
    ) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert len(agent.messages) == 1

    async def test_the_alert_is_not_repeated_while_the_monitor_stays_broken(
        self, tmp_path: Path
    ) -> None:
        """Un guasto che dura un giorno deve costare un messaggio, non quarantotto."""
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, 12)

        assert len(agent.messages) == 1
        state = _state(service, job_id)
        assert state.consecutive_could_not_check == 12
        assert state.could_not_check_escalated is True

    async def test_the_prompt_counts_the_runs_that_have_actually_happened(
        self, tmp_path: Path
    ) -> None:
        """Il numero nel prompt è quello vero, non la soglia.

        Al giro dell'escalation i run mancati sono ``K - 1``: il terzo è questo,
        e se il controllo riesce adesso non ci sarà nessun terzo guasto. Dire
        ``K`` sarebbe una cosa non ancora successa, e il modello quel numero lo
        può riferire all'utente. (È ciò che fa ``escalation_block`` sul ramo
        heartbeat, che la costante la scrive a mano.)
        """
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        escalation_prompt = agent.prompts[MONITOR_ESCALATE_AFTER_FAILURES - 1]
        assert f" {MONITOR_ESCALATE_AFTER_FAILURES - 1} times in a row" in escalation_prompt

    async def test_the_run_that_alerted_still_counts_as_a_missed_check(
        self, tmp_path: Path
    ) -> None:
        """Legare l'esito a "ha parlato" azzererebbe la sequenza proprio lì.

        Il conteggio ripartirebbe da zero a ogni avviso, e l'avviso tornerebbe
        ogni tre cicli per sempre: il rumore che questa feature deve evitare.
        """
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert agent.messages  # ha parlato
        state = _state(service, job_id)
        assert state.last_status == "could_not_check"
        assert state.consecutive_could_not_check == MONITOR_ESCALATE_AFTER_FAILURES

    async def test_a_model_that_ignores_the_instruction_is_asked_again(
        self, tmp_path: Path
    ) -> None:
        """L'avviso è "dato" solo se è uscito davvero."""
        service, job_id, agent = _monitor(tmp_path)

        async def mute(msg: InboundMessage) -> TurnOutcome:
            agent.prompts.append(msg.content)
            return TurnOutcome.silent(final_text="CHECK_FAILED: hps non raggiungibile")

        agent.submit_cron_turn = mute  # type: ignore[method-assign]
        await _cycles(service, job_id, 5)

        state = _state(service, job_id)
        assert state.could_not_check_escalated is False
        # Dal terzo giro in poi il prompt continua a chiedere di avvisare.
        assert agent.prompts[-1] != _base_prompt()

    async def test_the_broken_monitor_stays_armed(self, tmp_path: Path) -> None:
        """Non riuscire a controllare non è un motivo per smettere di provarci."""
        service, job_id, _agent = _monitor(tmp_path)

        await _cycles(service, job_id, 4)

        job = service.get_job(job_id)
        assert job is not None
        assert job.enabled is True
        assert job.state.next_run_at_ms is not None


class _ObedientlySilentAgent(_FakeMonitorAgent):
    """Il modello che applica "non dire niente" anche al marcatore.

    Non è un capriccio del finto: è il run delle 09:18 del 2026-08-16 sul Titan
    2, sul ramo heartbeat (v. ``ea70015``). L'utente ha scritto "se è
    irraggiungibile non dire niente", il prompt non diceva da nessuna parte che
    il marcatore è un'altra cosa, e il turno è finito muto — nessun messaggio e
    nessuna riga. Il run è stato archiviato come riuscito.

    Finché il prompt non lo smentisce, questo agente fa esattamente quello.
    """

    message = _QUIET_MESSAGE

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="hps risponde, nulla da segnalare.")
        if _SILENCE_OVERRIDE not in msg.content:
            # "Non dire niente" applicato alla lettera: niente messaggio e
            # niente marcatore.
            return TurnOutcome.silent(final_text="")
        if not self._asked_to_warn(msg.content):
            return TurnOutcome.silent(final_text="CHECK_FAILED: hps irraggiungibile")
        self.messages.append("Il controllo del server non riesce a partire da un po'.")
        return TurnOutcome.spoke_via_tool(
            final_text="CHECK_FAILED: hps irraggiungibile\nCHECK_WARNED"
        )


class TestAnInstructedSilenceIsAboutTheMessage:
    """Il guasto perfettamente mimetizzato, e la frase che lo produce.

    "Controlla il server, e se è irraggiungibile non dire niente" è il modo più
    ordinario che ci sia di scrivere un monitor. Senza l'override nel prompt il
    turno tace del tutto, il run viene archiviato come ``silenced`` — cioè come
    un successo — la sequenza non parte e l'escalation non arriva mai:
    indistinguibile da un monitor sano, per sempre.
    """

    async def test_a_check_told_to_stay_quiet_still_records_a_failure(
        self, tmp_path: Path
    ) -> None:
        service, job_id, agent = _monitor(
            tmp_path, agent=_ObedientlySilentAgent(), message=_QUIET_MESSAGE
        )

        await _cycles(service, job_id, 1)

        assert agent.messages == []  # l'utente non va disturbato: quello vale
        state = _state(service, job_id)
        assert state.last_status == "could_not_check"
        assert state.consecutive_could_not_check == 1

    async def test_a_check_told_to_stay_quiet_still_reaches_escalation(
        self, tmp_path: Path
    ) -> None:
        """Il punto di tutta la contabilità: un guasto muto deve venire fuori."""
        service, job_id, agent = _monitor(
            tmp_path, agent=_ObedientlySilentAgent(), message=_QUIET_MESSAGE
        )

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert len(agent.messages) == 1
        assert _state(service, job_id).could_not_check_escalated is True

    async def test_the_prompt_says_the_line_is_written_anyway(self) -> None:
        """Il contratto, in chiaro: obbedisci sul messaggio, scrivi la riga."""
        prompt = _base_prompt()
        assert _SILENCE_OVERRIDE in prompt
        assert "reaches nobody" in prompt

    async def test_the_legitimate_silent_case_survives(self) -> None:
        """Un controllo che gira e non trova niente resta un successo muto."""
        prompt = _base_prompt()
        assert "ran and found nothing is a success" in prompt


class _RepeatsItsOwnWarning(_FakeMonitorAgent):
    """Parla quando glielo si chiede, e poi ne riparla da sola.

    Misurato sul device (v. ``already_warned_block`` in ``heartbeat_tasks``):
    con l'escalation già data e nessuna riga che chieda di parlare, il modello
    ha chiamato ``message`` di propria iniziativa. Il guasto ce l'ha davanti e
    la coda della sessione contiene il suo stesso avviso di due ore prima, e
    "non ti sto chiedendo di parlare" non è "non parlare".
    """

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="Tutte le piante sopra il 15%.")
        final = "CHECK_FAILED: hps non raggiungibile"
        if _ALREADY_WARNED in msg.content:
            return TurnOutcome.silent(final_text=final)
        if not self._asked_to_warn(msg.content) and not self.messages:
            return TurnOutcome.silent(final_text=final)
        self.messages.append("Il controllo delle piante non riesce a partire da un po'.")
        return TurnOutcome.spoke_via_tool(final_text=final + "\nCHECK_WARNED")


class TestTheUserIsNotToldTwice:
    """L'equivalente di ``already_warned_block`` per il monitor.

    ``should_escalate_could_not_check`` smette di chiedere, e basta: il modello
    si ritrova il prompt di sempre con il proprio avviso ancora nella coda della
    sessione. Smettere di chiedere non è chiedere di tacere.
    """

    async def test_the_alert_is_not_repeated_by_a_model_that_speaks_on_its_own(
        self, tmp_path: Path
    ) -> None:
        service, job_id, agent = _monitor(tmp_path, agent=_RepeatsItsOwnWarning())

        await _cycles(service, job_id, 12)

        assert len(agent.messages) == 1

    async def test_the_prompt_asks_for_silence_once_the_user_knows(
        self, tmp_path: Path
    ) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES + 1)

        assert _ALREADY_WARNED in agent.prompts[-1]
        # E resta un prompt di monitor: il marcatore si scrive comunque.
        assert _state(service, job_id).consecutive_could_not_check == 4

    async def test_a_prompt_never_asks_for_both_at_once(self, tmp_path: Path) -> None:
        """Parlare e tacere dello stesso controllo, nello stesso prompt."""
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, 8)

        for prompt in agent.prompts:
            assert not (_ALREADY_WARNED in prompt and _MUST_WARN in prompt)

    async def test_the_block_is_gone_once_the_check_works_again(
        self, tmp_path: Path
    ) -> None:
        """Nessuna riga in più nel prompt di un monitor tornato sano."""
        service, job_id, agent = _monitor(tmp_path)
        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES + 1)
        agent.healthy = True

        await _cycles(service, job_id, 2)

        assert agent.prompts[-1] == _base_prompt()


class _WarnsWithoutBeingAsked(_FakeMonitorAgent):
    """Avvisa al primo guasto, senza che nessuno glielo abbia chiesto — e lo dichiara.

    Misurato sul device alle 10:19 del 2026-08-16 (v.
    ``roadmap/heartbeat-escalation-amnesia.md``, punto 3): il guasto ce l'ha
    davanti, e il fatto che il prompt non chieda ancora di parlare non gli
    impedisce di chiamare ``message``. Tace solo quando il prompt glielo chiede
    esplicitamente, che è l'unica riga che il modello vero rispetta.

    La riga ``CHECK_WARNED`` la scrive perché il prompt gliela chiede fra le
    regole di sempre, non solo nel blocco di escalation: è quella l'istruzione
    che rende registrabile un avviso spontaneo, e senza di essa non lo era.
    """

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="Tutte le piante sopra il 15%.")
        final = "CHECK_FAILED: hps non raggiungibile"
        if _ALREADY_WARNED in msg.content:
            return TurnOutcome.silent(final_text=final)
        self.messages.append("Il controllo delle piante non riesce a partire da un po'.")
        return TurnOutcome.spoke_via_tool(final_text=final + "\nCHECK_WARNED")


class _WarnsAndNeverDeclaresIt(_FakeMonitorAgent):
    """Avvisa a ogni giro e non scrive mai la riga. Il caso peggiore possibile.

    Ignora entrambe le istruzioni: quella che chiede la riga e quella che chiede
    di tacere. Non è quel che fa un modello vero — è il tetto del costo, ed è qui
    per fissarlo.
    """

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="Tutte le piante sopra il 15%.")
        self.messages.append("Il controllo delle piante non riesce a partire da un po'.")
        return TurnOutcome.spoke_via_tool(final_text="CHECK_FAILED: hps non raggiungibile")


class _ReportsAFindingAndAlsoFails(_FakeMonitorAgent):
    """Riporta un risultato degno di nota e, nello stesso turno, non completa il controllo.

    Non è un caso di scuola. ``cron_monitor.md:11`` elenca fra le cose degne di
    nota "una soglia superata, una scadenza in arrivo", quindi un monitor che
    parla di ciò che ha trovato sta obbedendo. Un controllo in più parti legge la
    prima e non raggiunge la seconda: risultato da riferire *e* ``CHECK_FAILED``.
    Il tetto di ``message.py:251`` — un messaggio per run silenzioso — rende i due
    esiti mutuamente esclusivi: se parla del risultato, del guasto non parla.
    """

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="Tutte le piante sopra il 15%.")
        self.messages.append("Basilico all'11%.")
        return TurnOutcome.spoke_via_tool(final_text="CHECK_FAILED: hps non raggiungibile")


class TestAMessageAboutSomethingElseDoesNotCountAsTheWarning:
    """``spoke`` è di turno e non ha soggetto: da solo attribuisce male.

    Con ``escalated=outcome.spoke`` questo agente si timbrava "già avvisato" allo
    streak 1 per un messaggio che del guasto non parlava, e da lì
    ``cron_monitor.md:18`` gli diceva di tacere "qualunque cosa tu trovi". Per
    sempre: il re-arm su messaggio utente itera ``state.task_checks``, che per un
    monitor è vuoto, quindi era l'unico latch senza via d'uscita.
    """

    async def test_a_finding_does_not_latch_the_failure_as_already_warned(
        self, tmp_path: Path
    ) -> None:
        service, job_id, agent = _monitor(tmp_path, agent=_ReportsAFindingAndAlsoFails())

        await _cycles(service, job_id, 1)

        assert agent.messages == ["Basilico all'11%."]
        state = _state(service, job_id)
        assert state.consecutive_could_not_check == 1
        assert state.could_not_check_escalated is False

    async def test_the_escalation_is_asked_for_at_the_threshold(self, tmp_path: Path) -> None:
        """Alla soglia il prompt chiede di avvisare, invece di tacere.

        Attenzione a cosa questo test *non* prova: asserisce il prompt, non una
        consegna. Che l'avviso arrivi davvero non è deducibile da qui, e misurato
        il 2026-08-17 spesso non arriva — ``message.py`` lascia passare un solo
        invio per run silenzioso, quindi un turno che ha già parlato del proprio
        risultato si vede rifiutare il secondo, e ``escalated`` si timbra comunque
        perché è inferito da ``outcome.spoke``, che non ha soggetto.
        Quello resta aperto: la correzione vera è derivare ``escalated``
        dall'*invio* dell'escalation e non dal turno.
        """
        service, job_id, agent = _monitor(tmp_path, agent=_ReportsAFindingAndAlsoFails())

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert _MUST_WARN in agent.prompts[-1]
        assert _ALREADY_WARNED not in agent.prompts[-1]


class TestAnUnaskedWarningIsRecorded:
    """Un avviso spontaneo che il turno dichiara **entra** nello stato.

    Questa classe pinnava l'opposto, e non per un capriccio: registrarlo
    richiedeva di credere che il messaggio riguardasse il guasto, e
    :class:`_ReportsAFindingAndAlsoFails` mostra che da ``spoke`` non è
    deducibile. Fra i due errori si scelse la direzione — un doppione è rumore
    recuperabile, un guasto zittito per sempre no — e si pagò il doppione
    misurato alle 10:19 del 2026-08-16.

    ``CHECK_WARNED`` toglie la scelta: il soggetto lo scrive il modello, quindi un
    avviso spontaneo si può registrare senza dedurre niente, e all'utente non
    arriva la stessa cosa due volte.
    """

    async def test_an_unasked_warning_is_remembered(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path, agent=_WarnsWithoutBeingAsked())

        await _cycles(service, job_id, 1)

        assert len(agent.messages) == 1
        assert _MUST_WARN not in agent.prompts[0]  # nessuno gliel'aveva chiesto
        state = _state(service, job_id)
        assert state.consecutive_could_not_check == 1
        assert state.could_not_check_escalated is True

    async def test_the_threshold_does_not_ask_for_it_a_second_time(
        self, tmp_path: Path
    ) -> None:
        """Il doppione misurato, ora chiuso: un avviso, non due."""
        service, job_id, agent = _monitor(tmp_path, agent=_WarnsWithoutBeingAsked())

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES + 3)

        assert len(agent.messages) == 1
        assert _MUST_WARN not in agent.prompts[MONITOR_ESCALATE_AFTER_FAILURES - 1]
        assert _ALREADY_WARNED in agent.prompts[-1]


class TestTheAskIsBoundedNotEndless:
    """Il costo residuo, e il suo tetto.

    Il timbro adesso è una riga che il modello deve scrivere, quindi un modello
    che non la scrive mai non fa scattare ``already_warned``: senza un limite la
    richiesta tornerebbe nel prompt a ogni run per sempre, e con lei un messaggio
    all'utente ogni mezz'ora. "Per sempre" non è un costo accettabile nemmeno
    nella direzione del rumore.

    Dove la finestra finisce comincia ``jenny/cron/silence_watchdog.py``, che
    l'allarme lo alza da sé — v. ``ESCALATION_ASK_LIMIT``.
    """

    async def test_the_prompt_stops_asking_after_the_limit(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path, agent=_WarnsAndNeverDeclaresIt())

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES + ESCALATION_ASK_LIMIT)

        asked = [p for p in agent.prompts if _MUST_WARN in p]
        assert len(asked) == ESCALATION_ASK_LIMIT
        # E l'ultimo run non chiede più niente: il prompt è quello di sempre.
        assert agent.prompts[-1] == _base_prompt()

    async def test_the_watchdog_picks_up_where_the_asking_stops(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le due finestre sono contigue: non resta un run scoperto."""
        alerts: list[str] = []
        monkeypatch.setattr(
            "jenny.runtime.notifier.notify_delivery",
            lambda content, metadata: alerts.append(content),
        )
        service, job_id, agent = _monitor(tmp_path, agent=_WarnsAndNeverDeclaresIt())
        last_ask = MONITOR_ESCALATE_AFTER_FAILURES - 1 + ESCALATION_ASK_LIMIT

        await _cycles(service, job_id, last_ask)
        assert alerts == []

        await _cycles(service, job_id, 1)
        assert len(alerts) == 1

    async def test_the_escalate_block_claims_the_one_message_for_the_warning(
        self, tmp_path: Path
    ) -> None:
        """La mitigazione dov'è utile: contro il tetto di un messaggio per run.

        Una prima versione qui diceva al modello "a meno che non gliel'abbia già
        detto", per limitare il doppione. Sbagliata, e misurata: se il modello
        obbedisce non parla, quindi ``spoke`` è falso proprio al run
        dell'escalation, ``escalated`` non si timbra mai e il blocco si ri-rende
        per sempre. Le due mitigazioni si combattevano.

        Quel che serve invece è dire di *cosa* parlare: ``message.py`` lascia
        passare un solo invio per run silenzioso, quindi un turno che spende quel
        messaggio su un risultato non può più avvisare del guasto — ed è il buco
        vero, perché ``escalated`` si timbra comunque.
        """
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        escalation_prompt = agent.prompts[-1]
        assert _MUST_WARN in escalation_prompt
        assert "this run's message is the warning" in escalation_prompt
        assert "already told them" not in escalation_prompt


class TestRecovery:
    async def test_a_check_that_works_again_resets_the_counter(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)
        assert len(agent.messages) == 1

        agent.healthy = True
        await _cycles(service, job_id, 1)

        state = _state(service, job_id)
        assert state.last_status == "silenced"
        assert state.last_error is None
        assert state.consecutive_could_not_check == 0
        assert state.could_not_check_since_ms is None
        assert state.could_not_check_escalated is False
        # E il ritorno alla normalità non è una notizia: nessun secondo messaggio.
        assert len(agent.messages) == 1

    async def test_a_second_outage_can_alert_again(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)
        agent.healthy = True
        await _cycles(service, job_id, 1)
        agent.healthy = False
        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert len(agent.messages) == 2

    async def test_a_spoken_finding_also_proves_the_check_ran(self, tmp_path: Path) -> None:
        """Un monitor che trova qualcosa e lo dice è ``ok``, e chiude la sequenza."""
        service, job_id, agent = _monitor(tmp_path)
        await _cycles(service, job_id, 2)

        async def found_something(msg: InboundMessage) -> TurnOutcome:
            agent.prompts.append(msg.content)
            return TurnOutcome.spoke_via_tool(final_text="Basilico all'11%.")

        agent.submit_cron_turn = found_something  # type: ignore[method-assign]
        await _cycles(service, job_id, 1)

        state = _state(service, job_id)
        assert state.last_status == "ok"
        assert state.consecutive_could_not_check == 0


class TestPersistedState:
    """Ciò che un lettore esterno (WebUI, tool ``cron``) troverà nello store."""

    async def test_the_streak_survives_a_restart(self, tmp_path: Path) -> None:
        service, job_id, _agent = _monitor(tmp_path)
        await _cycles(service, job_id, 2)

        reloaded = CronService(tmp_path / "cron" / "jobs.json").get_job(job_id)

        assert reloaded is not None
        assert reloaded.state.last_status == "could_not_check"
        assert reloaded.state.consecutive_could_not_check == 2
        assert reloaded.state.could_not_check_since_ms is not None
        assert reloaded.state.could_not_check_escalated is False

    async def test_the_state_is_written_in_camel_case_like_the_rest(
        self, tmp_path: Path
    ) -> None:
        service, job_id, _agent = _monitor(tmp_path)
        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        raw = json.loads((tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8"))
        state = raw["jobs"][0]["state"]
        assert state["lastStatus"] == "could_not_check"
        assert state["consecutiveCouldNotCheck"] == MONITOR_ESCALATE_AFTER_FAILURES
        assert state["couldNotCheckEscalated"] is True
        assert isinstance(state["couldNotCheckSinceMs"], int)

    async def test_the_run_history_keeps_the_third_status(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)
        await _cycles(service, job_id, 2)
        agent.healthy = True
        await _cycles(service, job_id, 1)

        state = _state(service, job_id)
        assert [r.status for r in state.run_history] == [
            "could_not_check",
            "could_not_check",
            "silenced",
        ]

    async def test_the_since_timestamp_marks_the_start_not_the_last_run(
        self, tmp_path: Path
    ) -> None:
        """"Da quando è rotto" è la domanda che il conteggio da solo non risponde."""
        service, job_id, _agent = _monitor(tmp_path)
        await _cycles(service, job_id, 1)
        first = _state(service, job_id).could_not_check_since_ms

        await _cycles(service, job_id, 3)

        assert _state(service, job_id).could_not_check_since_ms == first


class TestMarkerParsing:
    """Il marcatore è scritto da un modello: va letto con un po' di tolleranza."""

    def test_a_plain_marker_line_is_read_with_its_reason(self) -> None:
        assert could_not_check_reason("CHECK_FAILED: hps unreachable") == "hps unreachable"

    def test_a_marker_at_the_end_of_a_longer_answer_still_counts(self) -> None:
        text = "Ho provato tre volte a leggere la sonda.\n\nCHECK_FAILED: timeout"
        assert could_not_check_reason(text) == "timeout"

    def test_markdown_decoration_around_the_marker_is_ignored(self) -> None:
        assert could_not_check_reason("**CHECK_FAILED: import rotto**") == "import rotto"

    def test_a_marker_without_a_reason_is_still_a_marker(self) -> None:
        """Vuoto non è assente: il chiamante deve distinguere con ``is not None``."""
        assert could_not_check_reason("CHECK_FAILED") == ""

    def test_an_ordinary_answer_has_no_marker(self) -> None:
        assert could_not_check_reason("Tutte le piante sono sopra il 15%.") is None

    def test_no_answer_at_all_is_not_a_failure(self) -> None:
        assert could_not_check_reason("") is None
        assert could_not_check_reason(None) is None

    def test_the_reason_is_truncated_so_it_cannot_bloat_the_store(self) -> None:
        assert len(could_not_check_reason("CHECK_FAILED: " + "x" * 5000) or "") == 200


@pytest.mark.parametrize("mode", ["reminder"])
async def test_a_reminder_ignores_the_marker_entirely(tmp_path: Path, mode: str) -> None:
    """Il terzo stato è un fatto dei monitor: un reminder parla sempre e basta."""
    agent = _FakeMonitorAgent()
    service = CronService(tmp_path / "cron" / "jobs.json")

    async def on_job(job: Any) -> str | None:
        return await run_bound_cron_job(job, agent=agent, cron=service)

    service.on_job = on_job
    job = service.add_job(
        name="promemoria",
        schedule=CronSchedule(kind="every", every_ms=1_800_000),
        message=_MESSAGE,
        mode=mode,  # type: ignore[arg-type]
        session_key="unified:default",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    await service.run_job(job.id)

    state = _state(service, job.id)
    assert state.last_status == "ok"
    assert state.consecutive_could_not_check == 0
