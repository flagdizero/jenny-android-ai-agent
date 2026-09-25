import asyncio
import copy

import pytest

from jenny.providers.base import GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls = 0
        self.last_kwargs: dict = {}

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def chat_stream(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        delta = getattr(response, "_test_stream_delta", None)
        if delta and kwargs.get("on_content_delta"):
            await kwargs["on_content_delta"](delta)
        tool_deltas = getattr(response, "_test_tool_call_deltas", None)
        if tool_deltas and kwargs.get("on_tool_call_delta"):
            for tool_delta in tool_deltas:
                await kwargs["on_tool_call_delta"](tool_delta)
        return response

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_chat_with_retry_retries_transient_error_then_succeeds(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit", finish_reason="error"),
        LLMResponse(content="ok"),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "stop"
    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_with_retry_does_not_retry_non_transient_error(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="401 unauthorized", finish_reason="error"),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "401 unauthorized"
    assert provider.calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_chat_with_retry_returns_final_error_after_retries(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit a", finish_reason="error"),
        LLMResponse(content="429 rate limit b", finish_reason="error"),
        LLMResponse(content="429 rate limit c", finish_reason="error"),
        LLMResponse(content="503 final server error", finish_reason="error"),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "503 final server error"
    assert provider.calls == 4
    assert delays == [1, 2, 4]


@pytest.mark.asyncio
async def test_chat_with_retry_emits_terminal_progress_when_standard_retries_exhaust(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit a", finish_reason="error"),
        LLMResponse(content="429 rate limit b", finish_reason="error"),
        LLMResponse(content="429 rate limit c", finish_reason="error"),
        LLMResponse(content="503 final server error", finish_reason="error"),
    ])
    progress: list[str] = []

    async def _fake_sleep(delay: int) -> None:
        return None

    async def _progress(msg: str) -> None:
        progress.append(msg)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_retry_wait=_progress,
    )

    assert response.content == "503 final server error"
    # 4 tentativi: la chiamata e tre ripetizioni. «4 retries» contava male.
    assert progress[-1] == "Model request failed after 4 attempts, giving up."


@pytest.mark.asyncio
async def test_chat_with_retry_preserves_cancelled_error() -> None:
    provider = ScriptedProvider([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_chat_stream_with_retry_does_not_retry_after_emitting_content(monkeypatch) -> None:
    first = LLMResponse(content="stream stalled", finish_reason="error")
    first._test_stream_delta = "partial"  # type: ignore[attr-defined]
    provider = ScriptedProvider([
        first,
        LLMResponse(content="ok"),
    ])
    deltas: list[str] = []
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_delta(delta: str) -> None:
        deltas.append(delta)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
    )

    assert response.content == "stream stalled"
    assert provider.calls == 1
    assert deltas == ["partial"]
    assert delays == []


@pytest.mark.asyncio
async def test_chat_stream_with_retry_retries_timeout_after_emitting_content(monkeypatch) -> None:
    first = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    first._test_stream_delta = "partial"  # type: ignore[attr-defined]
    provider = ScriptedProvider([
        first,
        LLMResponse(content="full retry response"),
    ])
    deltas: list[str] = []
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_delta(delta: str) -> None:
        deltas.append(delta)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
    )

    assert response.content == "full retry response"
    assert response.finish_reason == "stop"
    assert provider.calls == 2
    assert deltas == ["partial"]
    assert delays == [1]
    assert provider.last_kwargs.get("on_content_delta") is None


@pytest.mark.asyncio
async def test_chat_stream_with_retry_retries_timeout_in_new_stream_segment(
    monkeypatch,
) -> None:
    first = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    first._test_stream_delta = "partial"  # type: ignore[attr-defined]
    second = LLMResponse(content="full retry response")
    second._test_stream_delta = "full retry response"  # type: ignore[attr-defined]
    provider = ScriptedProvider([first, second])
    deltas: list[str] = []
    recoveries: list[str] = []
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_delta(delta: str) -> None:
        deltas.append(delta)

    async def _on_stream_recover() -> None:
        recoveries.append("recover")

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
        on_stream_recover=_on_stream_recover,
    )

    # The persisted/returned response must include the text from the stalled
    # first segment ("partial") *and* the successful retry's text, in the
    # order the user actually saw them on screen -- not just the last
    # attempt's content. Regression test for the stall-retry history-loss bug.
    assert response.content == "partialfull retry response"
    assert response.finish_reason == "stop"
    assert provider.calls == 2
    assert deltas == ["partial", "full retry response"]
    assert recoveries == ["recover"]
    assert delays == [1]
    assert provider.last_kwargs.get("on_content_delta") is not None


@pytest.mark.asyncio
async def test_chat_stream_with_retry_accumulates_across_multiple_stalls(monkeypatch) -> None:
    """Two consecutive stalls must each contribute their visible text, in order."""
    first = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    first._test_stream_delta = "Let me check that... "  # type: ignore[attr-defined]
    second = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    second._test_stream_delta = "Still working on it... "  # type: ignore[attr-defined]
    third = LLMResponse(content="Here is the final answer.")
    third._test_stream_delta = "Here is the final answer."  # type: ignore[attr-defined]
    provider = ScriptedProvider([first, second, third])

    async def _fake_sleep(delay: int) -> None:
        return None

    async def _on_delta(delta: str) -> None:
        return None

    async def _on_stream_recover() -> None:
        return None

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
        on_stream_recover=_on_stream_recover,
    )

    assert response.content == (
        "Let me check that... Still working on it... Here is the final answer."
    )
    assert response.finish_reason == "stop"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_chat_stream_with_retry_stall_accumulation_ignores_tool_call_fragments(
    monkeypatch,
) -> None:
    """Partial tool-call argument JSON must never leak into accumulated text content.

    Only whole text deltas delivered via on_content_delta are concatenated
    across a stall/retry; tool-call argument fragments flow through the
    separate on_tool_call_delta callback and must be unaffected.
    """
    first = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    first._test_stream_delta = "Let me look that up"  # type: ignore[attr-defined]
    first._test_tool_call_deltas = [  # type: ignore[attr-defined]
        {"index": 0, "call_id": "call_1", "name": "search", "arguments_delta": '{"query": "par'},
    ]
    second = LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(id="call_1", name="search", arguments={"query": "partial"}),
        ],
        finish_reason="tool_calls",
    )
    provider = ScriptedProvider([first, second])

    tool_deltas_seen: list[dict] = []

    async def _fake_sleep(delay: int) -> None:
        return None

    async def _on_delta(delta: str) -> None:
        return None

    async def _on_tool_call_delta(delta: dict) -> None:
        tool_deltas_seen.append(delta)

    async def _on_stream_recover() -> None:
        return None

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
        on_tool_call_delta=_on_tool_call_delta,
        on_stream_recover=_on_stream_recover,
    )

    # The stalled segment's *text* is preserved...
    assert response.content == "Let me look that up"
    # ...but the partial tool-call JSON fragment never shows up in it.
    assert '"query": "par' not in (response.content or "")
    # Tool call delta callback still received the raw fragment untouched.
    assert tool_deltas_seen == first._test_tool_call_deltas
    # Final tool call arguments come from the last (successful) attempt only.
    assert response.tool_calls == [
        ToolCallRequest(id="call_1", name="search", arguments={"query": "partial"}),
    ]
    assert response.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_chat_with_retry_uses_provider_generation_defaults() -> None:
    """When callers omit generation params, provider.generation defaults are used."""
    provider = ScriptedProvider([LLMResponse(content="ok")])
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=321, reasoning_effort="high")

    await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert provider.last_kwargs["temperature"] == 0.2
    assert provider.last_kwargs["max_tokens"] == 321
    assert provider.last_kwargs["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_chat_with_retry_explicit_override_beats_defaults() -> None:
    """Explicit kwargs should override provider.generation defaults."""
    provider = ScriptedProvider([LLMResponse(content="ok")])
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=321, reasoning_effort="high")

    await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.9,
        max_tokens=9999,
        reasoning_effort="low",
    )

    assert provider.last_kwargs["temperature"] == 0.9
    assert provider.last_kwargs["max_tokens"] == 9999
    assert provider.last_kwargs["reasoning_effort"] == "low"


# ---------------------------------------------------------------------------
# Image fallback tests
# ---------------------------------------------------------------------------

_IMAGE_MSG = [
    {"role": "user", "content": [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "/media/test.png"}},
    ]},
]

_IMAGE_MSG_NO_META = [
    {"role": "user", "content": [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]},
]


@pytest.mark.asyncio
async def test_non_transient_error_with_images_retries_without_images() -> None:
    """Any non-transient error retries once with images stripped when images are present."""
    provider = ScriptedProvider([
        LLMResponse(content="API调用参数有误,请检查文档", finish_reason="error"),
        LLMResponse(content="ok, no image"),
    ])

    response = await provider.chat_with_retry(messages=copy.deepcopy(_IMAGE_MSG))

    assert response.content == "ok, no image"
    assert response.images_stripped is True
    assert provider.calls == 2
    msgs_on_retry = provider.last_kwargs["messages"]
    for msg in msgs_on_retry:
        content = msg.get("content")
        if isinstance(content, list):
            assert all(b.get("type") != "image_url" for b in content)
            assert any("/media/test.png" in (b.get("text") or "") and "does not support image input" in (b.get("text") or "") for b in content)


@pytest.mark.asyncio
async def test_successful_image_retry_mutates_original_messages_in_place() -> None:
    """Successful no-image retry should update the caller's message history."""
    provider = ScriptedProvider([
        LLMResponse(content="model does not support images", finish_reason="error"),
        LLMResponse(content="ok, no image"),
    ])
    messages = copy.deepcopy(_IMAGE_MSG)

    response = await provider.chat_with_retry(messages=messages)

    assert response.content == "ok, no image"
    assert response.images_stripped is True
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert all(block.get("type") != "image_url" for block in content)
    assert any("/media/test.png" in (block.get("text") or "") and "does not support image input" in (block.get("text") or "") for block in content)


@pytest.mark.asyncio
async def test_non_transient_error_without_images_no_retry() -> None:
    """Non-transient errors without image content are returned immediately."""
    provider = ScriptedProvider([
        LLMResponse(content="401 unauthorized", finish_reason="error"),
    ])

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
    )

    assert provider.calls == 1
    assert response.finish_reason == "error"
    assert response.images_stripped is False


@pytest.mark.asyncio
async def test_image_fallback_returns_error_on_second_failure() -> None:
    """If the image-stripped retry also fails, return that error."""
    provider = ScriptedProvider([
        LLMResponse(content="some model error", finish_reason="error"),
        LLMResponse(content="still failing", finish_reason="error"),
    ])

    response = await provider.chat_with_retry(messages=copy.deepcopy(_IMAGE_MSG))

    assert provider.calls == 2
    assert response.content == "still failing"
    assert response.finish_reason == "error"
    # Both attempts errored: the image-dropped retry never "succeeded", so no
    # notice should be surfaced for content the user never actually got back.
    assert response.images_stripped is False


@pytest.mark.asyncio
async def test_image_fallback_without_meta_uses_default_placeholder() -> None:
    """When _meta is absent, the placeholder still explains the vision limit."""
    provider = ScriptedProvider([
        LLMResponse(content="error", finish_reason="error"),
        LLMResponse(content="ok"),
    ])

    response = await provider.chat_with_retry(messages=copy.deepcopy(_IMAGE_MSG_NO_META))

    assert response.content == "ok"
    assert provider.calls == 2
    msgs_on_retry = provider.last_kwargs["messages"]
    for msg in msgs_on_retry:
        content = msg.get("content")
        if isinstance(content, list):
            assert any("does not support image input" in (b.get("text") or "") for b in content)


@pytest.mark.asyncio
async def test_chat_with_retry_uses_retry_after_and_emits_wait_progress(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit, retry after 7s", finish_reason="error"),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []
    progress: list[str] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    async def _progress(msg: str) -> None:
        progress.append(msg)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_retry_wait=_progress,
    )

    assert response.content == "ok"
    assert delays == [7.0]
    assert progress and "7s" in progress[0]


def test_extract_retry_after_supports_common_provider_formats() -> None:
    assert LLMProvider._extract_retry_after('{"error":{"retry_after":20}}') == 20.0
    assert LLMProvider._extract_retry_after("Rate limit reached, please try again in 20s") == 20.0
    assert LLMProvider._extract_retry_after("retry-after: 20") == 20.0


def test_extract_retry_after_from_headers_supports_numeric_and_http_date() -> None:
    assert LLMProvider._extract_retry_after_from_headers({"Retry-After": "20"}) == 20.0
    assert LLMProvider._extract_retry_after_from_headers({"retry-after": "20"}) == 20.0
    assert LLMProvider._extract_retry_after_from_headers(
        {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},
    ) == 0.1


def test_extract_retry_after_from_headers_supports_retry_after_ms() -> None:
    assert LLMProvider._extract_retry_after_from_headers({"retry-after-ms": "250"}) == 0.25
    assert LLMProvider._extract_retry_after_from_headers({"Retry-After-Ms": "1000"}) == 1.0
    assert LLMProvider._extract_retry_after_from_headers(
        {"retry-after-ms": "500", "retry-after": "10"},
    ) == 0.5


@pytest.mark.asyncio
async def test_chat_with_retry_prefers_structured_retry_after_when_present(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit", finish_reason="error", retry_after=9.0),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert delays == [9.0]


@pytest.mark.asyncio
async def test_chat_with_retry_retries_structured_status_code_without_keyword(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content="request failed",
            finish_reason="error",
            error_status_code=409,
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_with_retry_stops_on_429_quota_exhausted(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content='{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}',
            finish_reason="error",
            error_status_code=429,
            error_type="insufficient_quota",
            error_code="insufficient_quota",
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "error"
    assert provider.calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_chat_with_retry_retries_429_transient_rate_limit(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content='{"error":{"type":"rate_limit_exceeded","code":"rate_limit_exceeded"}}',
            finish_reason="error",
            error_status_code=429,
            error_type="rate_limit_exceeded",
            error_code="rate_limit_exceeded",
            error_retry_after_s=0.2,
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [0.2]


@pytest.mark.asyncio
async def test_chat_with_retry_retries_structured_timeout_kind(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content="request failed",
            finish_reason="error",
            error_kind="timeout",
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_with_retry_structured_should_retry_false_disables_retry(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content="429 rate limit",
            finish_reason="error",
            error_should_retry=False,
        ),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "error"
    assert provider.calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_chat_with_retry_prefers_structured_retry_after(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content="429 rate limit, retry after 99s",
            finish_reason="error",
            error_retry_after_s=0.2,
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert delays == [0.2]


@pytest.mark.asyncio
async def test_persistent_retry_aborts_after_ten_identical_transient_errors(monkeypatch) -> None:
    provider = ScriptedProvider([
        *[LLMResponse(content="429 rate limit", finish_reason="error") for _ in range(10)],
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        retry_mode="persistent",
    )

    assert response.finish_reason == "error"
    assert response.content == "429 rate limit"
    assert provider.calls == 10
    assert delays == [1, 2, 4, 4, 4, 4, 4, 4, 4]


@pytest.mark.asyncio
async def test_persistent_retry_emits_terminal_progress_on_identical_error_limit(monkeypatch) -> None:
    provider = ScriptedProvider([
        *[LLMResponse(content="429 rate limit", finish_reason="error") for _ in range(10)],
    ])
    progress: list[str] = []

    async def _fake_sleep(delay: float) -> None:
        return None

    async def _progress(msg: str) -> None:
        progress.append(msg)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        retry_mode="persistent",
        on_retry_wait=_progress,
    )

    assert response.finish_reason == "error"
    assert progress[-1] == "Persistent retry stopped after 10 identical errors."


@pytest.mark.asyncio
async def test_chat_with_retry_normalizes_explicit_none_max_tokens() -> None:
    """Explicit max_tokens=None must fall back to generation defaults.

    Regression for #3102: callers that construct AgentRunSpec with
    max_tokens=None propagate None into chat_with_retry, which used to
    reach ``_build_kwargs`` and crash on ``max(1, None)``.
    """
    provider = ScriptedProvider([LLMResponse(content="ok")])

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=None,
        temperature=None,
    )

    assert response.content == "ok"
    # Generation settings default to 4096 / 0.7; explicit None should
    # have been replaced before reaching chat().
    assert provider.last_kwargs["max_tokens"] == 4096
    assert provider.last_kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_chat_with_retry_retries_zhipu_1302_rate_limit(monkeypatch) -> None:
    """ZhiPu returns code 1302 with Chinese rate-limit text instead of HTTP 429."""
    provider = ScriptedProvider([
        LLMResponse(
            content='Error: {\'code\': \'1302\', \'message\': \'您的账户已达到速率限制，请您控制请求频率\'}',
            finish_reason="error",
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_with_retry_retries_zhipu_1302_with_429_status(monkeypatch) -> None:
    """ZhiPu 1302 error with HTTP 429 status should also retry."""
    provider = ScriptedProvider([
        LLMResponse(
            content='Error: {\'code\': \'1302\', \'message\': \'您的账户已达到速率限制，请您控制请求频率\'}',
            finish_reason="error",
            error_status_code=429,
            error_code="1302",
        ),
        LLMResponse(content="ok"),
    ])
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_stream_with_retry_normalizes_explicit_none_max_tokens() -> None:
    """chat_stream_with_retry must apply the same None-guard as chat_with_retry."""
    provider = ScriptedProvider([LLMResponse(content="ok")])

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=None,
        temperature=None,
    )

    assert response.content == "ok"
    assert provider.last_kwargs["max_tokens"] == 4096
    assert provider.last_kwargs["temperature"] == 0.7
