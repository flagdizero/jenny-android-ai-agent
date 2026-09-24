"""Quando il tool ``message`` ha parlato, il turno non streamma più.

Il testo finale di un turno in cui il tool ``message`` ha già consegnato nella
conversazione corrente viene **soppresso** (``AgentLoop._assemble_outbound``):
l'utente ha ricevuto l'avviso, e ciò che il modello scrive dopo è una nota di
servizio rivolta a se stesso. La soppressione però arrivava a fine turno, mentre
i delta di quel testo erano già partiti — quindi la WebUI, e solo la WebUI, la
vedeva comunque.

Misurato sul dispositivo il 27/08/2026, cron ``chiusura-giornata`` delle 20:00:
il tool consegna "ciao papi, sono le 20:00 — ora di mollare tutto", il modello
scrive poi "L'ho chiamato. Ora aspetto la sua risposta", e in chat è comparso il
secondo — sovrascrivendo il primo, perché il client riusava la bolla
(v. ``tests/webui/test_message_bubble_client.py``). Notifica Android e transcript
avevano l'avviso vero: le due superfici dicevano cose diverse.

Il gate sta in ``_dispatch``, unico posto che costruisce i callback di stream, e
insieme al ``_streamed`` di ``_assemble_outbound`` forma una coppia: se il gate
scarta i delta, il finale non può dichiararsi "già visto dai client".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from support.aio import drain_nowait

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.message import MessageTool
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse
from jenny.session.keys import UNIFIED_SESSION_KEY


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _drain(loop: AgentLoop) -> list[OutboundMessage]:
    return drain_nowait(loop.bus.outbound)


async def _stream_callback(loop: AgentLoop):
    """Il vero ``on_stream`` di un turno utente che streamma, preso da ``_dispatch``.

    Il turno non viene eseguito: ``_process_message`` è sostituito, così resta in
    mano il solo callback — che è l'oggetto sotto misura.
    """
    seen: dict[str, object] = {}

    async def capture(msg, *, on_stream=None, on_stream_end=None, **_kwargs):
        seen["on_stream"] = on_stream
        return TurnOutcome.silent()

    loop._process_message = capture  # type: ignore[method-assign]
    await loop._dispatch(
        InboundMessage(
            channel="websocket",
            sender_id="user",
            chat_id="default",
            content="ciao",
            metadata={"_wants_stream": True},
            session_key_override=UNIFIED_SESSION_KEY,
        )
    )
    on_stream = seen["on_stream"]
    assert on_stream is not None, "un turno utente WebUI deve streammare"
    _drain(loop)
    return on_stream


def _mark_spoken(loop: AgentLoop) -> MessageTool:
    """Il tool ha consegnato verso il target d'origine in questo turno."""
    mt = loop.tools.get("message")
    assert isinstance(mt, MessageTool)
    mt.start_turn()
    mt._sent_in_turn = True
    return mt


class TestTheStreamStopsWhenTheToolHasSpoken:
    async def test_deltas_flow_before_the_tool_speaks(self, tmp_path: Path) -> None:
        """Il caso normale non cambia: una risposta senza tool streamma."""
        loop = _make_loop(tmp_path)
        on_stream = await _stream_callback(loop)

        await on_stream("una risposta")

        published = _drain(loop)
        assert [m.content for m in published] == ["una risposta"]
        assert published[0].metadata.get("_stream_delta") is True

    async def test_no_delta_after_the_tool_has_spoken(self, tmp_path: Path) -> None:
        """La nota di servizio del modello non raggiunge nessun client."""
        loop = _make_loop(tmp_path)
        on_stream = await _stream_callback(loop)
        _mark_spoken(loop)

        await on_stream("L'ho chiamato. Ora aspetto la sua risposta")

        assert _drain(loop) == []


class TestTheFinalMessageDoesNotLieAboutHavingStreamed:
    """``_streamed`` decide se il dispatcher ri-consegna il finale o solo notifica.

    Con il gate attivo la coppia deve restare coerente: ciò che non è stato
    streammato non può dichiararsi streammato, o l'unico caso in cui la
    soppressione non scatta perderebbe il testo del tutto.
    """

    def _outbound(self, loop: AgentLoop, *, had_injections: bool):
        async def on_stream(_delta: str) -> None:  # pragma: no cover - mai chiamato
            raise AssertionError("non deve essere invocato qui")

        return loop._assemble_outbound(
            InboundMessage(
                channel="websocket", sender_id="user", chat_id="default", content="ciao"
            ),
            "risposta all'injection",
            [],
            "stop",
            had_injections,
            on_stream,
        )

    def test_a_normal_streamed_reply_is_marked_streamed(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        outbound = self._outbound(loop, had_injections=False)
        assert outbound is not None
        assert outbound.metadata.get("_streamed") is True

    def test_the_reply_to_an_injection_is_deliverable(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        _mark_spoken(loop)

        outbound = self._outbound(loop, had_injections=True)

        assert outbound is not None, "un'injection va risposta, tool o non tool"
        assert not outbound.metadata.get("_streamed"), (
            "il gate ha scartato i delta: marcarlo streammato lo farebbe scartare "
            "anche al dispatcher, e il testo non arriverebbe da nessuna parte"
        )

    def test_without_an_injection_the_reply_stays_suppressed(self, tmp_path: Path) -> None:
        """Il contratto di prima resta: l'avviso è la risposta, non ce n'è una seconda."""
        loop = _make_loop(tmp_path)
        _mark_spoken(loop)
        assert self._outbound(loop, had_injections=False) is None
