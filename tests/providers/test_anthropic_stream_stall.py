"""Lo stream Anthropic che si ferma: il messaggio dice se era gia' partito.

Il gemello per OpenAI-compat e' ``test_stream_first_output_timeout.py``; il
lato Anthropic non aveva un banco, e le due frasi erano copiate nei due
provider. Qui si fissano entrambe: niente output entro il budget lungo, e
stallo dopo che qualcosa era arrivato.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from jenny.providers import anthropic_provider
from jenny.providers.anthropic_provider import AnthropicProvider

TEXT = {"_event_type": "content_block_delta", "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "ciao"}}


def _provider(monkeypatch: pytest.MonkeyPatch, events_before_stall: list[dict]) -> AnthropicProvider:
    monkeypatch.setattr(anthropic_provider, "resolve_stream_idle_timeout_s", lambda: 0.05)
    monkeypatch.setattr(anthropic_provider, "resolve_first_output_timeout_s", lambda **_k: 0.2)

    async def _sse(_self, _response):
        for event in events_before_stall:
            yield event
        await asyncio.sleep(30)

    monkeypatch.setattr(AnthropicProvider, "_iter_anthropic_sse", _sse)
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    provider._http_client = httpx.AsyncClient(
        base_url="https://api.example.com",
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, content=b"")),
    )
    return provider


async def test_no_output_within_the_first_budget(monkeypatch) -> None:
    provider = _provider(monkeypatch, [])
    response = await provider.chat_stream(messages=[{"role": "user", "content": "ciao"}])
    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"
    assert response.content == "Error calling LLM: no output from the model within 0.2 seconds"


async def test_a_stall_after_output_says_so(monkeypatch) -> None:
    provider = _provider(monkeypatch, [TEXT])
    response = await provider.chat_stream(messages=[{"role": "user", "content": "ciao"}])
    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"
    assert response.content == "Error calling LLM: stream stalled for more than 0.05 seconds"
