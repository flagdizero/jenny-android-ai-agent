"""L'avviso di un turno finito vuoto arriva alla WebUI, come errore.

Il runner chiude un turno senza risposta con un avviso (``_finish_on_blank``:
«Ho finito i passaggi ma non sono riuscita a scrivere una risposta…»). Nessuno
l'ha streammato, ma ``_assemble_outbound`` lo segnava ``_streamed`` e il
dispatcher lo scartava: dal vivo, nella casa, il turno finiva senza niente.
Ora porta un ``error`` col suo tipo, e la casa ne fa una bolla con «Riprova».
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from support.agent import make_loop

from jenny.bus.events import InboundMessage
from jenny.providers.base import LLMResponse, ToolCallRequest


async def _turn(tmp_path: Path, final: str) -> dict:
    loop = make_loop(tmp_path, bare=True)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )
    calls = iter([
        LLMResponse(content="si annaffia una volta a settimana", tool_calls=[
            ToolCallRequest(id="c1", name="list_dir", arguments={"path": "."}),
        ]),
        *[LLMResponse(content=final, tool_calls=[]) for _ in range(6)],
    ])

    async def chat(*_args, **_kwargs):
        return next(calls)

    loop.provider.chat_with_retry = AsyncMock(side_effect=chat)
    loop.provider.chat_stream_with_retry = AsyncMock(side_effect=chat)

    async def on_stream(_delta: str) -> None:
        pass

    async def on_stream_end(**_kwargs) -> None:
        pass

    outcome = await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default",
                       content="ogni quanto la annaffio?", metadata={"webui": True}),
        on_stream=on_stream, on_stream_end=on_stream_end,
    )
    assert outcome.message is not None
    return outcome.message.metadata


async def test_the_empty_turn_notice_is_delivered_as_an_error(tmp_path: Path) -> None:
    meta = await _turn(tmp_path, final="")

    assert not meta.get("_streamed")
    assert meta.get("error") == {"kind": "empty"}


async def test_a_streamed_answer_stays_streamed(tmp_path: Path) -> None:
    meta = await _turn(tmp_path, final="una volta a settimana")

    assert meta.get("_streamed") is True
    assert "error" not in meta
