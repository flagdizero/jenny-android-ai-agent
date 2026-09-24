"""Fake provider per i test dei subagent, valido su entrambi i path del runner.

Un subagent chiede lo streaming (``_SubagentHook.wants_streaming``), quindi il
runner chiama ``chat_stream_with_retry``, non ``chat_with_retry``. Un fake che
stubba solo il secondo non fallisce in modo leggibile: il ``MagicMock`` non
awaitabile diventa un ``Error:`` dentro il risultato del turno, e il test asserisce
su una stringa che non parla di streaming.

Questi helper stubbano *entrambi* i metodi con la stessa sceneggiatura, così il
test resta fedele al path che il subagent prende davvero in produzione e non si
rompe se quel path cambia di nuovo.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from unittest.mock import MagicMock

from jenny.providers.base import LLMResponse


def script_provider(
    provider: Any,
    responses: Sequence[LLMResponse] | Callable[..., Awaitable[LLMResponse]],
    *,
    stream_content: bool = True,
) -> Any:
    """Stubba ``chat_with_retry`` e ``chat_stream_with_retry`` sulla stessa sceneggiatura.

    ``responses`` è una sequenza consumata in ordine (l'ultima si ripete, così un
    test che non sa quante iterazioni servono non deve contarle) oppure un
    callable async che riceve gli stessi kwargs del provider vero.

    Con ``stream_content`` il path streaming invoca ``on_content_delta`` col
    contenuto della risposta prima di restituirla: serve ai test che verificano
    cosa il subagent osserva mentre il testo si forma, non solo il risultato.
    """
    if callable(responses):
        produce = responses
    else:
        queue = list(responses) or [LLMResponse(content="")]
        state = {"i": 0}

        async def produce(**_kwargs: Any) -> LLMResponse:
            index = min(state["i"], len(queue) - 1)
            state["i"] += 1
            return queue[index]

    async def _chat(**kwargs: Any) -> LLMResponse:
        return await produce(**kwargs)

    async def _chat_stream(**kwargs: Any) -> LLMResponse:
        response = await produce(**kwargs)
        on_delta = kwargs.get("on_content_delta")
        if stream_content and on_delta is not None and response.content:
            await on_delta(response.content)
        return response

    provider.chat_with_retry = _chat
    provider.chat_stream_with_retry = _chat_stream
    return provider


def fake_provider(
    responses: Sequence[LLMResponse] | Callable[..., Awaitable[LLMResponse]],
    *,
    model: str = "test-model",
    stream_content: bool = True,
) -> MagicMock:
    """``MagicMock`` di provider già scriptato su entrambi i path."""
    provider = MagicMock()
    provider.get_default_model.return_value = model
    return script_provider(provider, responses, stream_content=stream_content)
