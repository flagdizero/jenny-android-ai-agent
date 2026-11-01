"""Il turno silenzioso della FSM: la meccanica su cui poggiano i monitor.

Prima di questa feature il silenzio da un lavoro schedulato era **impossibile**:
un turno che finiva senza contenuto veniva sostituito con
``EMPTY_FINAL_RESPONSE_MESSAGE`` (``jenny/agent/runner.py``, ``_finish_on_blank``)
e quel placeholder veniva consegnato in chat. ``suppress_response`` disattiva
quella sostituzione (``jenny/agent/turn_states.py``, ``_state_save``) e azzera
l'outbound (``_state_respond``).

Sono i due punti che, se regredissero, romperebbero il silenzio: il job
continuerebbe a girare, i test del runner cron resterebbero verdi, e l'utente si
ritroverebbe "Non ho prodotto una risposta" ogni cinque minuti. Da qui il test
dedicato.

Il terzo punto, aggiunto con :mod:`jenny.session.turn_visibility`, è **da dove**
arriva la soppressione: non più un flag cron letto a mano, ma la visibilità del
turno — e "ha parlato col tool ``message``" viaggia nel ``TurnOutcome`` invece di
essere contrabbandato dentro il dict metadata del chiamante.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop, TurnContext, TurnState
from jenny.agent.tools.message import MessageTool
from jenny.agent.turn_types import TurnDisposition
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.session_turns import CRON_MONITOR_META, CRON_TRIGGER_META
from jenny.providers.base import LLMResponse
from jenny.runtime.user_text import say
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.turn_visibility import TURN_VISIBILITY_META, mark_silent_turn

# In inglese esplicito: a livello di modulo la fixture della lingua non c'e' ancora.
EMPTY_FINAL_RESPONSE_MESSAGE = say("turn.empty", "en")

MONITOR_SESSION_KEY = "cron:job-m"


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )


def _cron_metadata(*, monitor: bool) -> dict:
    metadata: dict = {CRON_TRIGGER_META: {"job_id": "job-m", "run_id": "run-1"}}
    if monitor:
        metadata[CRON_MONITOR_META] = True
        mark_silent_turn(metadata)
    return metadata


def _ctx(
    loop: AgentLoop,
    *,
    metadata: dict,
    suppress: bool,
    final_content: str | None,
    session_key: str = MONITOR_SESSION_KEY,
) -> TurnContext:
    ctx = TurnContext(
        msg=InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="chat-1",
            content="controlla la posta",
            metadata=metadata,
        ),
        session_key=session_key,
        state=TurnState.SAVE,
        turn_id="turn-1",
    )
    ctx.session = loop.sessions.get_or_create(session_key)
    ctx.silent = suppress
    ctx.suppress_response = suppress
    ctx.final_content = final_content
    ctx.turn_latency_ms = 0
    return ctx


@pytest.fixture
def loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentLoop:
    agent_loop = _make_loop(tmp_path)
    # La persistenza di fine turno non è ciò che stiamo testando: la neutralizziamo
    # per isolare l'unica riga in gioco (la sostituzione del contenuto vuoto).
    monkeypatch.setattr(agent_loop, "_finalize_turn_save", lambda *a, **k: None)
    return agent_loop


class _StopTurnError(Exception):
    """Interrompe la FSM appena il TurnContext è stato costruito."""


class TestSuppressionComesFromTheTurnVisibility:
    """``_process_message`` decide la soppressione dalla visibilità del turno."""

    async def _captured_ctx(
        self,
        loop: AgentLoop,
        monkeypatch: pytest.MonkeyPatch,
        metadata: dict,
        *,
        session_key: str = MONITOR_SESSION_KEY,
    ) -> TurnContext:
        captured: list[TurnContext] = []

        async def capture(ctx: TurnContext) -> str:
            captured.append(ctx)
            raise _StopTurnError

        monkeypatch.setattr(loop, "_state_restore", capture)
        msg = InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="chat-1",
            content="controlla la posta",
            metadata=metadata,
        )
        with pytest.raises(_StopTurnError):
            await loop._process_message(msg, session_key=session_key)
        return captured[0]

    async def test_a_monitor_cron_message_starts_a_silent_turn(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = await self._captured_ctx(loop, monkeypatch, _cron_metadata(monitor=True))

        assert ctx.silent is True
        assert ctx.suppress_response is True

    async def test_a_reminder_cron_message_starts_a_visible_turn(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un reminder gira nella sessione dell'utente: deve poter parlare."""
        ctx = await self._captured_ctx(
            loop,
            monkeypatch,
            _cron_metadata(monitor=False),
            session_key=UNIFIED_SESSION_KEY,
        )

        assert ctx.silent is False
        assert ctx.suppress_response is False

    async def test_a_user_message_starts_a_visible_turn(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = await self._captured_ctx(
            loop, monkeypatch, {}, session_key=UNIFIED_SESSION_KEY
        )

        assert ctx.silent is False
        assert ctx.suppress_response is False

    async def test_internal_work_on_a_user_channel_is_silent_without_a_flag(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il caso dell'annuncio di un subagent nato dentro un monitor.

        Nessun flag nei metadata: la provenienza (session key interna su un canale
        utente) basta da sola. È ciò che impedisce a quel turno di consegnare in
        chat come faceva prima.
        """
        ctx = await self._captured_ctx(loop, monkeypatch, {}, session_key="cron:rain-check")

        assert ctx.silent is True

    async def test_a_silent_turn_is_marked_in_its_metadata(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il marchio serve ai consumatori che hanno solo i metadata."""
        ctx = await self._captured_ctx(loop, monkeypatch, {}, session_key="cron:rain-check")

        assert ctx.msg.metadata[TURN_VISIBILITY_META] == "silent"

    async def test_a_visible_turn_carries_no_marker(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I metadata inbound finiscono nell'outbound: nessun flag fino al client."""
        ctx = await self._captured_ctx(
            loop, monkeypatch, {}, session_key=UNIFIED_SESSION_KEY
        )

        assert TURN_VISIBILITY_META not in ctx.msg.metadata


class TestBlankAnswerPlaceholder:
    """``_state_save`` sostituisce il vuoto con un placeholder — tranne se soppresso."""

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    async def test_a_normal_turn_gets_the_placeholder_when_it_says_nothing(
        self, loop: AgentLoop, blank: str
    ) -> None:
        """Il comportamento storico, che un turno silenzioso deve disattivare."""
        ctx = _ctx(loop, metadata={}, suppress=False, final_content=blank)

        assert await loop._state_save(ctx) == "ok"

        assert ctx.final_content == EMPTY_FINAL_RESPONSE_MESSAGE

    async def test_a_normal_turn_with_no_content_at_all_gets_the_placeholder(
        self, loop: AgentLoop
    ) -> None:
        ctx = _ctx(loop, metadata={}, suppress=False, final_content=None)

        await loop._state_save(ctx)

        assert ctx.final_content == EMPTY_FINAL_RESPONSE_MESSAGE

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    async def test_a_suppressed_turn_keeps_its_silence_instead_of_the_placeholder(
        self, loop: AgentLoop, blank: str
    ) -> None:
        """La riga che rende possibile il silenzio di un monitor.

        Se questa sostituzione tornasse ad applicarsi, il monitor consegnerebbe
        "Non ho prodotto una risposta" a ogni ciclo silenzioso.
        """
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content=blank,
        )

        assert await loop._state_save(ctx) == "ok"

        assert ctx.final_content == blank
        assert ctx.final_content != EMPTY_FINAL_RESPONSE_MESSAGE

    async def test_a_suppressed_turn_with_no_content_at_all_stays_empty(
        self, loop: AgentLoop
    ) -> None:
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content=None,
        )

        await loop._state_save(ctx)

        assert ctx.final_content is None

    async def test_a_suppressed_turn_that_did_produce_text_keeps_it(
        self, loop: AgentLoop
    ) -> None:
        """La soppressione non riscrive il contenuto: si limita a non consegnarlo."""
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content="ho trovato qualcosa",
        )

        await loop._state_save(ctx)

        assert ctx.final_content == "ho trovato qualcosa"


class TestSuppressedTurnProducesNoOutbound:
    """``_state_respond``: da un turno soppresso non esce nessun messaggio."""

    async def test_a_silent_monitor_turn_ends_with_no_outbound(self, loop: AgentLoop) -> None:
        """Il percorso completo save → respond su un turno che non ha detto nulla."""
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content="",
        )

        await loop._state_save(ctx)
        assert await loop._state_respond(ctx) == "ok"

        # Né il placeholder, né un outbound: è esattamente ciò che l'utente
        # non deve vedere arrivare in chat.
        assert ctx.final_content == ""
        assert ctx.outbound is None

    async def test_a_suppressed_turn_with_content_still_delivers_nothing(
        self, loop: AgentLoop
    ) -> None:
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content="testo che nessuno deve ricevere",
        )

        await loop._state_respond(ctx)

        assert ctx.outbound is None

    async def test_a_reminder_cron_turn_still_produces_its_outbound(
        self, loop: AgentLoop
    ) -> None:
        """Controprova: senza soppressione l'outbound c'è, e porta il testo."""
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=False),
            suppress=False,
            final_content="promemoria consegnato",
            session_key=UNIFIED_SESSION_KEY,
        )

        await loop._state_respond(ctx)

        assert ctx.outbound is not None
        assert ctx.outbound.content == "promemoria consegnato"
        assert ctx.outbound.channel == "websocket"
        assert ctx.outbound.chat_id == "chat-1"


class TestSilentTurnsInstallNoBusCallbacks:
    """``_state_build``: un turno silenzioso non pubblica nemmeno il progress.

    Le righe di progress sono indirizzate alla chat d'origine e vengono
    *persistite* nel transcript: senza questo gate un monitor riempirebbe la
    conversazione di attività e reasoning pur non consegnando mai una risposta.
    """

    async def _built_ctx(self, loop: AgentLoop, *, suppress: bool) -> TurnContext:
        ctx = _ctx(loop, metadata={}, suppress=suppress, final_content=None)
        ctx.state = TurnState.BUILD
        assert await loop._state_build(ctx) == "ok"
        return ctx

    async def test_a_silent_turn_gets_a_progress_callback_that_publishes_nothing(
        self, loop: AgentLoop
    ) -> None:
        published: list[object] = []
        loop.bus.publish_outbound = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda msg: published.append(msg)
        )

        ctx = await self._built_ctx(loop, suppress=True)
        assert ctx.on_progress is not None
        await ctx.on_progress("sto guardando la pioggia")
        assert ctx.on_retry_wait is not None
        await ctx.on_retry_wait("retry")

        assert published == []

    async def test_a_visible_turn_keeps_publishing_its_progress(
        self, loop: AgentLoop
    ) -> None:
        published: list[object] = []

        async def _capture(msg: object) -> None:
            published.append(msg)

        loop.bus.publish_outbound = _capture  # type: ignore[method-assign]

        ctx = await self._built_ctx(loop, suppress=False)
        assert ctx.on_progress is not None
        await ctx.on_progress("sto lavorando")

        assert len(published) == 1


class TestSpokeViaToolTravelsInTheOutcome:
    """"Ha parlato" è un fatto dell'esito, non un flag nei metadata del chiamante."""

    def _message_tool(self, loop: AgentLoop, *, sent: bool) -> MessageTool:
        tool = loop.tools.get("message")
        assert isinstance(tool, MessageTool)
        tool.start_turn()
        tool._sent_in_turn = sent
        return tool

    async def test_a_delivery_through_the_message_tool_is_reported_as_spoken(
        self, loop: AgentLoop
    ) -> None:
        self._message_tool(loop, sent=True)
        ctx = _ctx(loop, metadata=_cron_metadata(monitor=True), suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert ctx.spoke_via_tool is True
        assert ctx.outbound is None

    async def test_a_turn_without_deliveries_is_reported_as_silent(
        self, loop: AgentLoop
    ) -> None:
        self._message_tool(loop, sent=False)
        ctx = _ctx(loop, metadata=_cron_metadata(monitor=True), suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert ctx.spoke_via_tool is False

    async def test_a_delivery_through_a_per_turn_registry_is_reported_as_spoken(
        self, loop: AgentLoop
    ) -> None:
        """``_sent_in_turn`` vive in una ContextVar *per istanza*: leggere il
        MessageTool di default mentre il turno ne usa un altro darebbe sempre
        ``False``, e il monitor risulterebbe muto a ogni ciclo senza che nulla
        lo segnali. Il registry del turno deve avere la precedenza."""
        from jenny.agent.tools.registry import ToolRegistry

        # Il MessageTool di default resta a mani vuote: se la FSM leggesse
        # quello, il turno passerebbe per silenzioso.
        self._message_tool(loop, sent=False)

        turn_tool = MessageTool(send_callback=AsyncMock(), workspace=loop.workspace)
        turn_tool.start_turn()
        turn_tool._sent_in_turn = True
        turn_registry = ToolRegistry()
        turn_registry.register(turn_tool)

        ctx = _ctx(loop, metadata=_cron_metadata(monitor=True), suppress=True, final_content="")
        ctx.tools = turn_registry

        await loop._state_respond(ctx)

        assert ctx.spoke_via_tool is True

    async def test_the_metadata_the_caller_handed_over_is_not_used_as_a_channel(
        self, loop: AgentLoop
    ) -> None:
        """Il vecchio segnale mutava il dict del chiamante. Ora non serve più.

        Restano solo le chiavi che il chiamante ha scritto lui, più il marchio di
        visibilità: nessun esito contrabbandato.
        """
        self._message_tool(loop, sent=True)
        metadata = _cron_metadata(monitor=True)
        before = set(metadata)

        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")
        await loop._state_respond(ctx)

        assert set(metadata) == before
        assert ctx.msg.metadata is metadata

    async def test_a_goal_continuation_is_suppressed_without_being_silent(
        self, loop: AgentLoop
    ) -> None:
        """Le goal continuation condividono ``suppress_response`` ma non sono
        lavoro interno: restano turni visibili, e ``spoke_via_tool`` si limita a
        riferire il fatto senza cambiare comportamento."""
        self._message_tool(loop, sent=True)
        ctx = _ctx(
            loop,
            metadata={},
            suppress=False,
            final_content="",
            session_key=UNIFIED_SESSION_KEY,
        )
        ctx.suppress_response = True  # acceso a metà turno dalla continuation

        await loop._state_respond(ctx)

        assert ctx.silent is False
        assert ctx.outbound is None


class TestOutcomeMapping:
    """``TurnOutcome.of``: i due soli fatti che il turno conosce, esaustivi."""

    def test_an_outbound_is_delivered(self) -> None:
        from jenny.agent.turn_types import TurnOutcome
        from jenny.bus.events import OutboundMessage

        message = OutboundMessage(channel="websocket", chat_id="c", content="ciao")
        outcome = TurnOutcome.of(message, spoke_via_tool=False)

        assert outcome.disposition is TurnDisposition.DELIVERED
        assert outcome.spoke is True
        assert outcome.text == "ciao"

    def test_no_outbound_but_a_tool_delivery_still_counts_as_spoken(self) -> None:
        from jenny.agent.turn_types import TurnOutcome

        outcome = TurnOutcome.of(None, spoke_via_tool=True)

        assert outcome.disposition is TurnDisposition.SPOKE_VIA_TOOL
        assert outcome.spoke is True
        assert outcome.text == ""

    def test_nothing_at_all_is_a_successful_silence(self) -> None:
        from jenny.agent.turn_types import TurnOutcome

        outcome = TurnOutcome.of(None, spoke_via_tool=False)

        assert outcome.disposition is TurnDisposition.SILENT
        assert outcome.spoke is False
        assert outcome.text == ""
