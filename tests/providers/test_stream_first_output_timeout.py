"""Il silenzio prima del primo token ha un budget diverso dall'idle inter-chunk.

Un model server locale macina il prompt senza scrivere nulla sulla connessione
(sui ~5.800 token dei soli schemi tool sono minuti), mentre a stream avviato un
buco lungo è davvero un blocco: i due casi vanno misurati con soglie diverse.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from jenny.providers.endpoint_budget import DEFAULT_REQUEST_TIMEOUT_S, LOCAL_REQUEST_TIMEOUT_S
from jenny.providers.openai_compat_helpers import _openai_compat_timeout_s
from jenny.providers.openai_compat_provider import OpenAICompatProvider

CONTENT_CHUNK = {"choices": [{"delta": {"content": "hi"}}]}


def _provider(api_base: str = "http://127.0.0.1:8080/v1") -> OpenAICompatProvider:
    return OpenAICompatProvider(api_key="EMPTY", api_base=api_base, default_model="qwen")


def _sse_with_delays(steps: list[tuple[float, dict[str, Any] | None]]):
    """Un iteratore SSE finto: (attesa, chunk); chunk None = fine stream."""

    async def _iter(_response):
        for delay, chunk in steps:
            if delay:
                await asyncio.sleep(delay)
            if chunk is None:
                return
            yield chunk

    return _iter


async def _run(provider: OpenAICompatProvider, steps, streamer=None):
    with (
        patch.object(provider, "_http_request", new=AsyncMock(return_value=object())),
        patch.object(provider, "_iter_chat_completion_sse", new=_sse_with_delays(steps)),
    ):
        return await provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            on_content_delta=streamer,
        )


async def test_slow_first_chunk_is_not_treated_as_a_stall(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_STREAM_IDLE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S", "5")

    response = await _run(_provider(), [(0.3, CONTENT_CHUNK), (0, None)])

    assert response.finish_reason != "error"
    assert response.content == "hi"


async def test_gap_after_the_first_output_still_stalls(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_STREAM_IDLE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S", "5")

    response = await _run(_provider(), [(0, CONTENT_CHUNK), (0.5, CONTENT_CHUNK)])

    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"
    assert "stream stalled for more than 0.05 seconds" in (response.content or "")


async def test_no_first_output_at_all_reports_the_longer_wait(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_STREAM_IDLE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S", "0.2")

    response = await _run(_provider(), [(5, CONTENT_CHUNK)])

    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"
    assert "no output from the model within 0.2 seconds" in (response.content or "")


async def test_keepalive_chunks_do_not_start_the_idle_clock(monkeypatch) -> None:
    """Un chunk senza delta utile (solo role) non conta come primo output."""
    monkeypatch.setenv("JENNY_STREAM_IDLE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S", "5")

    steps = [
        (0, {"choices": [{"delta": {"role": "assistant"}}]}),
        (0.3, CONTENT_CHUNK),
        (0, None),
    ]
    response = await _run(_provider(), steps)

    assert response.finish_reason != "error"
    assert response.content == "hi"


async def test_loopback_endpoints_get_the_longer_request_timeout() -> None:
    local = _provider()
    remote = _provider("https://api.groq.com/openai/v1")
    await local._ensure_client()
    await remote._ensure_client()

    assert local._http_client.timeout.connect == LOCAL_REQUEST_TIMEOUT_S
    assert remote._http_client.timeout.connect == DEFAULT_REQUEST_TIMEOUT_S
    assert local._http_client.timeout.read > remote._http_client.timeout.read
    assert LOCAL_REQUEST_TIMEOUT_S > DEFAULT_REQUEST_TIMEOUT_S


async def test_request_timeout_env_override_wins_for_both(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_OPENAI_COMPAT_TIMEOUT_S", "45")

    assert _openai_compat_timeout_s(local=True) == 45.0
    assert _openai_compat_timeout_s(local=False) == 45.0
