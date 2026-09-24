"""Un turno silenzioso non raggiunge l'utente da nessuna delle sue uscite.

Il difetto che questi test bloccano è concreto e misurato sul dispositivo: una
voce di ``HEARTBEAT.md`` delegava il controllo a un subagent con ``spawn``, il
turno heartbeat finiva 27 secondi prima che il dato esistesse (e il suo testo
veniva correttamente zittito), e poi il subagent terminava e il suo **annuncio**
apriva un turno nuovo — ``_process_system_message``, che per contratto esplicito
"restituisce sempre una risposta (contenuto o fallback)" — consegnato in chat
senza passare da alcun gate. È da lì che arrivavano gli "All clear." che l'utente
vedeva.

Copre le tre uscite che un turno ha verso la chat: la risposta finale del path di
sistema, il ramo d'errore del dispatch, e lo stream dei delta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.aio import drain_nowait

from jenny.agent.loop import AgentLoop
from jenny.agent.turn_types import TurnDisposition
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse
from jenny.session.keys import HEARTBEAT_SESSION_KEY, UNIFIED_SESSION_KEY
from jenny.session.turn_visibility import silent_turn_metadata
from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _stub_run(loop: AgentLoop, final_content: str = "All clear.") -> None:
    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return (
            final_content,
            [],
            [*initial_messages, {"role": "assistant", "content": final_content}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )


def _announce(session_key: str) -> InboundMessage:
    """L'annuncio che ``SubagentManager._announce_result`` pubblica sul bus.

    ``session_key_override`` è la session key d'ORIGINE: è da lì che il turno
    d'annuncio eredita la visibilità.
    """
    return InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="websocket:default",
        content="subagent completed successfully: waterbot-umidita-check",
        metadata={"subagent_task_id": "sub-1"},
        session_key_override=session_key,
    )


async def _drain(loop: AgentLoop) -> list[OutboundMessage]:
    return drain_nowait(loop.bus.outbound)


class TestTheSubagentAnnounceInheritsTheOriginVisibility:
    async def test_an_announce_from_internal_work_delivers_nothing(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path)
        _stub_run(loop)

        outcome = await loop._process_message(_announce(HEARTBEAT_SESSION_KEY))

        assert outcome.disposition is TurnDisposition.SILENT
        assert outcome.message is None

    async def test_an_announce_from_a_monitor_delivers_nothing(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path)
        _stub_run(loop)

        outcome = await loop._process_message(_announce("cron:job-m"))

        assert outcome.message is None

    async def test_an_announce_from_a_user_turn_still_delivers(
        self, tmp_path: Path
    ) -> None:
        """Controprova: il subagent lanciato dall'utente deve continuare a rispondere."""
        loop = _make_loop(tmp_path)
        _stub_run(loop, final_content="ecco il risultato")

        outcome = await loop._process_message(_announce(UNIFIED_SESSION_KEY))

        assert outcome.disposition is TurnDisposition.DELIVERED
        assert outcome.message is not None
        assert outcome.message.content == "ecco il risultato"

    async def test_a_silent_announce_gets_no_placeholder_fallback(
        self, tmp_path: Path
    ) -> None:
        """Il vecchio contratto sostituiva il vuoto con "Background task completed."
        e lo consegnava. Su lavoro interno quel riempitivo era il messaggio che
        l'utente non doveva vedere."""
        loop = _make_loop(tmp_path)
        _stub_run(loop, final_content="")

        outcome = await loop._process_message(_announce(HEARTBEAT_SESSION_KEY))

        assert outcome.message is None

    async def test_a_visible_announce_keeps_its_fallback(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        _stub_run(loop, final_content="")

        outcome = await loop._process_message(_announce(UNIFIED_SESSION_KEY))

        assert outcome.message is not None
        assert outcome.message.content == "Background task completed."


class TestTheErrorBranchRespectsTheContract:
    """Un turno silenzioso non consegna nemmeno i propri errori."""

    async def _dispatch_failing(self, loop: AgentLoop, msg: InboundMessage) -> None:
        async def boom(*_a, **_k):
            raise RuntimeError("boom")

        loop._process_message = boom  # type: ignore[method-assign]
        await loop._dispatch(msg)

    async def test_a_silent_turn_publishes_no_error_bubble(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="default",
            content="controlla l'umidità",
            metadata=silent_turn_metadata(),
            session_key_override=HEARTBEAT_SESSION_KEY,
        )

        await self._dispatch_failing(loop, msg)

        contents = [m.content for m in await _drain(loop)]
        assert "Sorry, I encountered an error." not in contents

    async def test_a_user_turn_still_gets_told_something_broke(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="c1",
            content="ciao",
            session_key_override=UNIFIED_SESSION_KEY,
        )

        await self._dispatch_failing(loop, msg)

        contents = [m.content for m in await _drain(loop)]
        assert "Sorry, I encountered an error." in contents


class TestSilentTurnsDoNotStream:
    """``_wants_stream`` ereditato da una sessione WebUI non deve streammare.

    Lasciandolo passare il turno mostrerebbe testo in chat per poi non consegnare
    nulla: il silenzio promesso, rotto a metà.
    """

    async def _captured_stream_flag(
        self, tmp_path: Path, *, session_key: str
    ) -> bool | None:
        loop = _make_loop(tmp_path)
        seen: dict[str, object] = {}

        async def capture(msg, *, on_stream=None, on_stream_end=None, **_kwargs):
            seen["on_stream"] = on_stream
            from jenny.agent.turn_types import TurnOutcome

            return TurnOutcome.silent()

        loop._process_message = capture  # type: ignore[method-assign]
        await loop._dispatch(
            InboundMessage(
                channel="websocket",
                sender_id="cron",
                chat_id="default",
                content="check",
                metadata={"_wants_stream": True},
                session_key_override=session_key,
            )
        )
        return seen["on_stream"] is not None

    async def test_internal_work_gets_no_stream_callback(self, tmp_path: Path) -> None:
        assert await self._captured_stream_flag(
            tmp_path, session_key=HEARTBEAT_SESSION_KEY
        ) is False

    async def test_a_user_turn_keeps_streaming(self, tmp_path: Path) -> None:
        assert await self._captured_stream_flag(
            tmp_path, session_key=UNIFIED_SESSION_KEY
        ) is True


class TestTheOnlyWayOutIsTheMessageTool:
    async def test_a_silent_turn_that_called_message_reports_it(
        self, tmp_path: Path
    ) -> None:
        """Il segnale che il cron runner legge per distinguere "ho avvisato" da
        "non c'era niente da dire"."""
        from jenny.agent.tools.message import MessageTool

        loop = _make_loop(tmp_path)
        tool = loop.tools.get("message")
        assert isinstance(tool, MessageTool)
        loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
            return_value=False
        )

        async def fake_run_agent_loop(initial_messages, **_kwargs):
            # La consegna avviene DENTRO il turno: BUILD azzera i flag per-turno
            # del MessageTool, quindi scriverlo prima non conterebbe.
            tool._sent_in_turn = True
            return ("", [], list(initial_messages), "stop", False)

        loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

        outcome = await loop._process_message(
            InboundMessage(
                channel="websocket",
                sender_id="cron",
                chat_id="default",
                content="controlla l'umidità",
                metadata=silent_turn_metadata(),
            ),
            session_key=HEARTBEAT_SESSION_KEY,
        )

        assert outcome.disposition is TurnDisposition.SPOKE_VIA_TOOL
        assert outcome.message is None
        assert outcome.spoke is True

    @pytest.mark.parametrize("origin_channel", ["websocket", "internal"])
    async def test_the_alert_it_sends_is_a_turn_that_closes(
        self, tmp_path: Path, origin_channel: str
    ) -> None:
        """L'unica uscita di un turno silenzioso apre un turno WebUI, e quel
        turno deve chiudersi.

        Il turno che manda l'avviso non ha vista WebUI, quindi nessun
        ``turn_end`` arriva dal coordinator: senza quello del deliverer il
        client resta con un turno aperto per sempre — mascotte incantata in
        ``thinking``, e l'avviso successivo che sovrascrive la bolla del
        precedente invece di aprirne una nuova.

        Le due parametrizzazioni sono i due modi in cui l'avviso può partire:
        stesso target del turno, o cross-channel (l'heartbeat gira sul canale
        interno e consegna alla WebUI). Nel secondo il tool non eredita i
        metadata del turno d'origine, e la visibilità deve arrivare comunque.
        """
        from jenny.agent.tools.context import RequestContext
        from jenny.agent.tools.message import MessageTool
        from jenny.runtime.delivery import ChannelDeliverer

        bus = MessageBus()
        deliverer = ChannelDeliverer(bus=bus, session_manager=MagicMock())
        tool = MessageTool(send_callback=lambda msg: deliverer.deliver(msg))
        tool.set_context(
            RequestContext(
                channel=origin_channel,
                chat_id="default" if origin_channel == "websocket" else "heartbeat",
                metadata=silent_turn_metadata(),
            )
        )

        await tool.execute(
            content="il monitoraggio delle piante non sta girando",
            channel="websocket",
            chat_id="default",
        )

        published = [await bus.consume_outbound() for _ in range(bus.outbound_size)]
        assert [
            (m.channel, bool(m.metadata.get("_turn_end"))) for m in published
        ] == [("websocket", False), ("websocket", True)]
        # Stesso turno: è l'id condiviso a far annotare la chiusura come
        # ``complete`` di quell'avviso e non di un turno altrui.
        assert (
            published[1].metadata[WEBUI_TURN_METADATA_KEY]
            == published[0].metadata[WEBUI_TURN_METADATA_KEY]
        )


class TestTheAnnouncePromptFollowsTheVisibility:
    """Il prompt d'annuncio non può dire "riassumi per l'utente" a un turno muto.

    Misurato sul Titan 2 (ciclo 19:08): il turno d'annuncio — il solo che avesse
    il dato — ha prodotto un riassunto che non è arrivato a nessuno, perché il
    testo unico gli chiedeva di parlare invece di *decidere*.
    """

    def _rendered(self, *, silent: bool) -> str:
        from jenny.utils.prompt_templates import render_template

        return render_template(
            "agent/subagent_announce.md",
            label="waterbot-umidita-check",
            status_text="completed successfully",
            task="controlla l'umidità",
            result="Acerello 44%, Albinella 79%",
            silent=silent,
        )

    def test_a_silent_announce_is_told_it_is_the_decision_point(self) -> None:
        text = self._rendered(silent=True)

        assert "you are the turn that decides" in text
        assert "`message` tool" in text
        # Le due uscite, e nessuna delle due è "non fare niente": è l'istruzione
        # che il modello non sapeva eseguire, ed è da lì che venivano le bolle
        # `placeholder` / `silent-skip` misurate sul dispositivo il 3/09/2026.
        assert "`nothing_to_report`" in text
        assert "call nothing and end the turn" not in text

    def test_a_silent_announce_is_not_told_to_summarize_for_the_user(self) -> None:
        assert "Summarize this naturally" not in self._rendered(silent=True)

    def test_a_silent_announce_forbids_the_filler(self) -> None:
        text = self._rendered(silent=True)
        for filler in ("All clear.", "All done.", "nothing to report"):
            assert filler in text, f"il divieto non nomina {filler!r}"

    def test_a_visible_announce_keeps_the_old_instruction(self) -> None:
        text = self._rendered(silent=False)

        assert "Summarize this naturally" in text
        assert "you are the turn that decides" not in text

    def test_both_branches_carry_the_result(self) -> None:
        for silent in (True, False):
            text = self._rendered(silent=silent)
            assert "Acerello 44%" in text
            assert "waterbot-umidita-check" in text


@pytest.mark.parametrize("channel", ["websocket", "telegram"])
async def test_a_silent_turn_is_silent_on_every_user_channel(
    tmp_path: Path, channel: str
) -> None:
    """La regola non è "non su websocket": è "non verso l'utente"."""
    loop = _make_loop(tmp_path)
    _stub_run(loop)

    outcome = await loop._process_message(
        InboundMessage(
            channel=channel,
            sender_id="cron",
            chat_id="42",
            content="check",
            metadata=silent_turn_metadata(),
        ),
        session_key="cron:job-m",
    )

    assert outcome.message is None
    assert await asyncio.wait_for(_drain(loop), timeout=1.0) == []
