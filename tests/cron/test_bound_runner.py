"""Test per jenny/cron/bound_runner.py.

Copre ``run_bound_cron_job`` (validazione, esito ok/errore, gestione del
contesto ``CronTool``, metadata webui/trigger) e ``is_bound_cron_job``
(``jenny/cron/session_turns.py``): quest'ultima è il gate booleano usato da
``jenny/runtime/cron_dispatch.py`` subito prima di invocare
``run_bound_cron_job`` e non ha un file di test proprio, quindi la copriamo
qui insieme al runner che la consuma concettualmente.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any

import pytest
from support.sessions import FakeSessions

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.cron.bound_runner import (
    CRON_WAKELOCK_TIMEOUT_S,
    MONITOR_KEEP_RECENT_MESSAGES,
    run_bound_cron_job,
)
from jenny.cron.service import CronService
from jenny.cron.session_turns import (
    CRON_DEFER_UNTIL_IDLE_META,
    CRON_MONITOR_META,
    CRON_TRIGGER_META,
    is_bound_cron_job,
    monitor_session_key,
)
from jenny.cron.types import CronJob, CronJobSilencedError, CronPayload
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.turn_visibility import is_silent_turn
from jenny.utils.prompt_templates import render_template
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY, WEBUI_TURN_METADATA_KEY


def _bound_job(
    *,
    job_id: str = "job-1",
    name: str = "Promemoria",
    message: str = "annaffia le piante",
    origin_channel: str | None = "websocket",
    origin_chat_id: str | None = "chat-1",
    origin_metadata: dict[str, Any] | None = None,
    session_key: str | None = "unified:default",
    mode: str = "reminder",
) -> CronJob:
    return CronJob(
        id=job_id,
        name=name,
        payload=CronPayload(
            kind="agent_turn",
            mode=mode,  # type: ignore[arg-type]
            message=message,
            session_key=session_key,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            origin_metadata=dict(origin_metadata or {}),
        ),
    )


class _FakeAgent:
    """Agente finto: registra i messaggi ricevuti e restituisce (o solleva) una risposta."""

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        response: str | None = "fatto",
        error: Exception | None = None,
        events: list[str] | None = None,
        spoke: bool | None = None,
    ) -> None:
        self.tools = tools if tools is not None else ToolRegistry()
        self.sessions = FakeSessions()
        self._response = response
        self._error = error
        self._spoke = spoke
        self.events = events if events is not None else []
        self.received: list[InboundMessage] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.events.append("submit_cron_turn")
        self.received.append(msg)
        if self._error is not None:
            raise self._error
        if not self._response:
            # Ciò che fa la FSM per un turno silenzioso: nessun outbound (né
            # contenuto né placeholder), e l'esito dice da sé se il modello ha
            # parlato col tool ``message``.
            return TurnOutcome.spoke_via_tool() if self._spoke else TurnOutcome.silent()
        return TurnOutcome.delivered(
            OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=self._response)
        )


class _FakeCronRecorder:
    """Registra le run_record scritte, senza persistere nulla su disco."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        self.records.append((run_id, dict(record)))


class _SpyCronTool(CronTool):
    """CronTool reale (serve per l'isinstance) che registra set/reset del contesto."""

    def __init__(self, events: list[str]) -> None:
        super().__init__(cron_service=object())  # cron_service non è usato da questi metodi
        self._events = events

    def set_cron_context(self, active: bool):  # type: ignore[override]
        self._events.append(f"set_cron_context:{active}")
        return super().set_cron_context(active)

    def reset_cron_context(self, token) -> None:  # type: ignore[override]
        self._events.append("reset_cron_context")
        super().reset_cron_context(token)


class TestIsBoundCronJob:
    def test_true_for_complete_agent_turn_payload(self) -> None:
        assert is_bound_cron_job(_bound_job()) is True

    def test_false_for_system_event_kind(self) -> None:
        job = _bound_job()
        job.payload.kind = "system_event"
        assert is_bound_cron_job(job) is False

    def test_false_without_session_key(self) -> None:
        job = _bound_job(session_key=None)
        assert is_bound_cron_job(job) is False

    def test_false_without_origin_channel(self) -> None:
        job = _bound_job(origin_channel=None)
        assert is_bound_cron_job(job) is False

    def test_false_without_origin_chat_id(self) -> None:
        job = _bound_job(origin_chat_id=None)
        assert is_bound_cron_job(job) is False


class TestAReminderRunsInTheConversation:
    """La sessione in cui gira un promemoria e' quella in cui l'utente parla.

    Il giro completo, perche' il difetto stava fra due pezzi che da soli erano
    coerenti: ``CronTool`` registrava ``f"{canale}:{chat}"`` per tenere il job
    "attaccato alla chat che l'ha creato", e ``run_bound_cron_job`` usa quel
    valore come **chiave del turno**. Risultato: un promemoria creato dalla
    WebUI girava in ``websocket:default``, un file di sessione tutto suo con il
    suo consolidamento, accanto alla conversazione a cui il promemoria
    appartiene. Nessuno dei due lati, guardato da solo, sembrava sbagliato.
    """

    @pytest.mark.asyncio
    async def test_a_webui_job_survives_a_reload_as_the_one_conversation(
        self, tmp_path
    ) -> None:
        store = tmp_path / "jobs.json"
        tool = CronTool(CronService(store))
        tool.set_context(
            RequestContext(
                channel="websocket",
                chat_id="default",
                metadata={"webui": True},
                session_key=UNIFIED_SESSION_KEY,
            )
        )

        assert (
            await tool.execute(action="add", message="annaffia le piante", every_seconds=300)
        ).startswith("Created job")

        job = CronService(store).list_jobs()[0]
        assert job.payload.session_key == UNIFIED_SESSION_KEY
        # E resta consegnabile: l'attaccamento alla chat non e' la sessione.
        assert is_bound_cron_job(job) is True
        assert (job.payload.origin_channel, job.payload.origin_chat_id) == (
            "websocket",
            "default",
        )

    def test_a_monitor_is_the_exception_and_stays_one(self) -> None:
        """Un monitor gira in silenzio in una sessione sua: quello e' voluto."""
        assert monitor_session_key("job-m") != UNIFIED_SESSION_KEY


class TestMonitorMetadataHelpers:
    """Predicati di ``session_turns`` usati dalla FSM e dal runner."""

    def test_each_monitor_gets_its_own_session_namespaced_by_job_id(self) -> None:
        assert monitor_session_key("job-m") == "cron:job-m"
        assert monitor_session_key("job-m") != "unified:default"


class TestRunBoundCronJobValidation:
    async def test_missing_session_key_raises_value_error(self) -> None:
        job = _bound_job(session_key=None)
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        with pytest.raises(ValueError, match="missing payload.session_key"):
            await run_bound_cron_job(job, agent=agent, cron=cron)


class TestRunBoundCronJobSuccess:
    async def test_records_queued_then_ok_and_returns_response(self) -> None:
        job = _bound_job(job_id="job-42", name="Annaffia", message="annaffia le piante")
        agent = _FakeAgent(response="Fatto, annaffiato.")
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == "Fatto, annaffiato."
        statuses = [record["status"] for _run_id, record in cron.records]
        assert statuses == ["queued", "ok"]
        run_id_queued, _record_queued = cron.records[0]
        run_id_ok, record_ok = cron.records[1]
        assert run_id_queued == run_id_ok
        assert re.match(r"^job-42:\d+:[0-9a-f]{8}$", run_id_queued)
        assert record_ok["response"] == "Fatto, annaffiato."
        assert record_ok["job_id"] == "job-42"
        assert record_ok["job_name"] == "Annaffia"
        assert record_ok["session_key"] == "unified:default"
        expected_prompt = render_template(
            "agent/cron_reminder.md", strip=True, message="annaffia le piante"
        )
        assert record_ok["rendered_prompt"] == expected_prompt

    async def test_none_response_records_ok_with_empty_string(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(response=None)
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == ""
        assert cron.records[-1][1]["response"] == ""
        assert cron.records[-1][1]["status"] == "ok"

    async def test_inbound_message_uses_session_key_override_and_origin(self) -> None:
        job = _bound_job(
            origin_channel="websocket", origin_chat_id="chat-9", session_key="unified:default"
        )
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        received = agent.received[0]
        assert received.session_key_override == "unified:default"
        assert received.channel == "websocket"
        assert received.chat_id == "chat-9"
        assert received.sender_id == "cron"

    async def test_metadata_carries_cron_trigger_and_defer_flag(self) -> None:
        job = _bound_job(job_id="job-7", name="Sveglia", message="sveglia")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        trigger = metadata[CRON_TRIGGER_META]
        assert trigger["job_id"] == "job-7"
        assert trigger["job_name"] == "Sveglia"
        assert trigger["persist_content"] == "Scheduled cron job triggered: Sveglia\n\nsveglia"
        assert trigger["prompt_ref"]["id"] == "cron.agent_turn.reminder"
        assert trigger["prompt_ref"]["version"] == 1
        assert metadata[CRON_DEFER_UNTIL_IDLE_META] is True

    async def test_websocket_channel_adds_webui_metadata(self) -> None:
        job = _bound_job(origin_channel="websocket")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        assert metadata["webui"] is True
        assert metadata[WEBUI_TURN_METADATA_KEY].startswith(f"cron:{job.id}:")
        assert metadata[WEBUI_MESSAGE_SOURCE_METADATA_KEY] == {
            "kind": "cron",
            "label": job.name,
        }

    async def test_non_websocket_channel_has_no_webui_metadata(self) -> None:
        job = _bound_job(origin_channel="other-channel")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        assert "webui" not in metadata
        assert WEBUI_TURN_METADATA_KEY not in metadata
        assert WEBUI_MESSAGE_SOURCE_METADATA_KEY not in metadata


class TestRunBoundCronJobCronContext:
    async def test_cron_tool_context_set_before_and_reset_after_turn(self) -> None:
        events: list[str] = []
        registry = ToolRegistry()
        registry.register(_SpyCronTool(events))
        job = _bound_job()
        agent = _FakeAgent(tools=registry, events=events)
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert events == ["set_cron_context:True", "submit_cron_turn", "reset_cron_context"]

    async def test_missing_cron_tool_does_not_crash(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(tools=ToolRegistry())
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == "fatto"

    async def test_non_cron_tool_object_under_cron_name_is_ignored(self) -> None:
        registry = ToolRegistry()

        class _NotACronTool:
            name = "cron"

        # Iniezione diretta nel dict interno: `register()` richiede un'istanza
        # di `Tool`, qui invece serve un oggetto qualunque per esercitare il
        # ramo isinstance-negativo (nessuna gestione del contesto cron).
        registry._tools["cron"] = _NotACronTool()  # type: ignore[assignment]
        job = _bound_job()
        agent = _FakeAgent(tools=registry)
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == "fatto"


class TestRunBoundCronJobError:
    async def test_error_writes_error_record_and_reraises(self) -> None:
        job = _bound_job(job_id="job-err")
        agent = _FakeAgent(error=RuntimeError("provider down"))
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError, match="provider down"):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        statuses = [record["status"] for _run_id, record in cron.records]
        assert statuses == ["queued", "error"]
        assert cron.records[-1][1]["error"] == "provider down"

    async def test_error_without_message_uses_exception_class_name(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(error=RuntimeError())
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert cron.records[-1][1]["error"] == "RuntimeError"

    async def test_cron_context_reset_even_on_error(self) -> None:
        events: list[str] = []
        registry = ToolRegistry()
        registry.register(_SpyCronTool(events))
        job = _bound_job()
        agent = _FakeAgent(tools=registry, events=events, error=RuntimeError("boom"))
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert events == ["set_cron_context:True", "submit_cron_turn", "reset_cron_context"]


class TestReminderModeIsUnchanged:
    """Caratterizzazione del percorso ``reminder``: è il 99% dell'uso reale.

    La modalità monitor ha aggiunto rami dentro ``run_bound_cron_job``; questi
    test fissano il comportamento che un promemoria normale aveva prima, così
    che una regressione si veda qui e non sul telefono dell'utente.
    """

    async def test_a_reminder_runs_in_the_session_it_was_created_from(self) -> None:
        job = _bound_job(job_id="job-r", session_key="unified:default")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert agent.received[0].session_key_override == "unified:default"
        # Nessuna sessione isolata aperta né potata: quella è roba da monitor.
        assert agent.sessions.by_key == {}
        assert agent.sessions.saved == []

    async def test_a_reminder_run_record_carries_no_delivery_key(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(response="fatto")
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert [record["status"] for _run_id, record in cron.records] == ["queued", "ok"]
        assert all("delivery" not in record for _run_id, record in cron.records)

    async def test_a_silent_reminder_is_still_a_plain_ok(self) -> None:
        """Un reminder che non produce testo NON solleva ``CronJobSilencedError``."""
        job = _bound_job()
        agent = _FakeAgent(response=None)
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == ""
        assert cron.records[-1][1]["status"] == "ok"

    async def test_a_reminder_turn_is_never_flagged_as_monitor(self) -> None:
        job = _bound_job()
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        assert CRON_MONITOR_META not in metadata
        assert metadata[CRON_TRIGGER_META]["prompt_ref"]["id"] == "cron.agent_turn.reminder"

    async def test_a_reminder_keeps_the_stream_flag_from_its_origin_session(self) -> None:
        """``_wants_stream`` viene rimosso solo ai monitor: un reminder deve streammare."""
        job = _bound_job(origin_metadata={"_wants_stream": True})
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert agent.received[0].metadata["_wants_stream"] is True


class TestMonitorModeStaysQuiet:
    """Un monitor che non ha nulla da riferire non deve consegnare niente."""

    async def test_a_monitor_with_nothing_to_report_raises_silenced(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError, match="job-m"):
            await run_bound_cron_job(job, agent=agent, cron=cron)

    async def test_the_silent_run_is_recorded_as_silenced_and_suppressed(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert [record["status"] for _run_id, record in cron.records] == ["queued", "silenced"]
        _run_id, silenced = cron.records[-1]
        assert silenced["delivery"] == "suppressed"
        # Nessuna ``response``: non c'è stato niente da consegnare.
        assert "response" not in silenced
        # ``ok`` non deve comparire da nessuna parte, altrimenti la UI mostrerebbe
        # una consegna che non è mai avvenuta.
        assert "ok" not in [record["status"] for _run_id, record in cron.records]

    async def test_an_outcome_with_no_delivery_counts_as_silence(self) -> None:
        """Il ripiego sicuro è tacere: inventare una consegna che non c'è stata
        sarebbe peggio di perdere un avviso. Ora non c'è più uno stato ambiguo da
        interpretare — l'esito è uno dei tre valori di ``TurnDisposition``.
        """
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response=None, spoke=None)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

    async def test_a_monitor_turn_is_marked_silent_at_its_own_boundary(self) -> None:
        """Il marchio di visibilità è ciò che rende muto TUTTO il turno.

        Non solo la risposta finale: anche progress, reasoning, spinner e
        marcatore di fine turno leggono questo fatto dai metadata.
        """
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert is_silent_turn(agent.received[0].metadata) is True

    async def test_a_reminder_turn_is_not_marked_silent(self) -> None:
        job = _bound_job(mode="reminder")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert is_silent_turn(agent.received[0].metadata) is False

    async def test_a_monitor_turn_runs_in_its_own_session_not_the_users(self) -> None:
        """La sessione isolata è ciò che tiene i controlli fuori dalla chat."""
        job = _bound_job(job_id="job-m", mode="monitor", session_key="unified:default")
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        received = agent.received[0]
        assert received.session_key_override == "cron:job-m"
        assert received.session_key_override != "unified:default"
        # La run record continua a nominare la sessione d'origine: è il target di
        # consegna del job, non la sessione in cui il turno gira.
        assert cron.records[-1][1]["session_key"] == "unified:default"

    async def test_the_monitor_session_is_pruned_so_it_cannot_grow_forever(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        session = agent.sessions.by_key["cron:job-m"]
        assert session.retained == [MONITOR_KEEP_RECENT_MESSAGES]
        assert agent.sessions.saved == ["cron:job-m"]

    async def test_a_monitor_never_streams_into_the_chat_it_will_not_answer(self) -> None:
        """``_wants_stream`` ereditato dalla sessione WebUI va rimosso.

        Lasciandolo, il turno streammerebbe testo in chat per poi non consegnare
        nulla: esattamente il silenzio che il monitor promette, rotto.
        """
        job = _bound_job(mode="monitor", origin_metadata={"_wants_stream": True})
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert "_wants_stream" not in agent.received[0].metadata

    async def test_a_monitor_turn_is_flagged_and_uses_the_monitor_prompt(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor", message="controlla la posta")
        agent = _FakeAgent(response=None, spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        assert metadata[CRON_MONITOR_META] is True
        assert metadata[CRON_TRIGGER_META]["prompt_ref"]["id"] == "cron.agent_turn.monitor"
        expected_prompt = render_template(
            "agent/cron_monitor.md", strip=True, message="controlla la posta"
        )
        assert cron.records[-1][1]["rendered_prompt"] == expected_prompt
        assert agent.received[0].content == expected_prompt


class TestMonitorModeSpeaks:
    """Quando il monitor trova qualcosa, la consegna va alla chat d'origine."""

    async def test_a_monitor_that_spoke_completes_without_raising(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response="", spoke=True)
        cron = _FakeCronRecorder()

        # Nessuna eccezione: parlare è l'esito ordinario, non un caso speciale.
        assert await run_bound_cron_job(job, agent=agent, cron=cron) == ""

    async def test_the_spoken_run_is_recorded_as_ok_delivered_by_the_message_tool(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response="", spoke=True)
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert [record["status"] for _run_id, record in cron.records] == ["queued", "ok"]
        assert cron.records[-1][1]["delivery"] == "agent_message"
        assert "silenced" not in [record["status"] for _run_id, record in cron.records]

    async def test_the_delivery_targets_the_origin_chat_not_the_isolated_session(self) -> None:
        job = _bound_job(
            job_id="job-m",
            mode="monitor",
            origin_channel="websocket",
            origin_chat_id="chat-9",
            session_key="unified:default",
        )
        agent = _FakeAgent(response="", spoke=True)
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        received = agent.received[0]
        # Il turno gira isolato, ma il messaggio deve uscire dove l'utente guarda.
        assert received.session_key_override == "cron:job-m"
        assert received.channel == "websocket"
        assert received.chat_id == "chat-9"

    async def test_a_monitor_that_spoke_still_prunes_its_session(self) -> None:
        job = _bound_job(job_id="job-m", mode="monitor")
        agent = _FakeAgent(response="", spoke=True)
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert agent.sessions.by_key["cron:job-m"].retained == [MONITOR_KEEP_RECENT_MESSAGES]


class TestWakelock:
    """Un job cron gira dentro un wakelock, altrimenti a schermo spento non gira.

    ``keep_awake`` è spiata e non eseguita: il context manager vero è un no-op
    fuori da Android, quindi eseguirlo non direbbe nulla. Quello che va fissato
    è che il percorso del job ci passi dentro — la regressione, se sparisce, è
    muta ovunque tranne che sul telefono.
    """

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch, module: str) -> list[tuple[str, str, float]]:
        events: list[tuple[str, str, float]] = []

        @asynccontextmanager
        async def fake_keep_awake(tag: str, *, timeout_s: float = 0.0):
            events.append(("enter", tag, timeout_s))
            try:
                yield True
            finally:
                events.append(("exit", tag, timeout_s))

        monkeypatch.setattr(f"{module}.keep_awake", fake_keep_awake)
        return events

    async def test_the_job_body_runs_inside_the_wakelock(self, monkeypatch) -> None:
        events = self._spy(monkeypatch, "jenny.cron.bound_runner")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(_bound_job(), agent=agent, cron=cron)

        assert [e[0] for e in events] == ["enter", "exit"]
        assert events[0][1] == "cron"
        assert events[0][2] == CRON_WAKELOCK_TIMEOUT_S

    async def test_a_failing_job_still_leaves_the_block(self, monkeypatch) -> None:
        events = self._spy(monkeypatch, "jenny.cron.bound_runner")
        agent = _FakeAgent(error=RuntimeError("boom"))
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError):
            await run_bound_cron_job(_bound_job(), agent=agent, cron=cron)

        assert [e[0] for e in events] == ["enter", "exit"]

    async def test_a_silenced_monitor_still_leaves_the_block(self, monkeypatch) -> None:
        # ``CronJobSilencedError`` è un esito RIUSCITO che esce per eccezione:
        # il ramo più facile da dimenticare quando si sposta un `finally`.
        events = self._spy(monkeypatch, "jenny.cron.bound_runner")
        agent = _FakeAgent(response="", spoke=False)
        cron = _FakeCronRecorder()

        with pytest.raises(CronJobSilencedError):
            await run_bound_cron_job(
                _bound_job(job_id="job-m", mode="monitor"), agent=agent, cron=cron
            )

        assert [e[0] for e in events] == ["enter", "exit"]
