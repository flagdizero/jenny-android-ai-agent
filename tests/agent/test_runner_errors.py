"""Tests for AgentRunner error handling: tool errors, LLM errors,
session message isolation, and tool result preservation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from support.runner import make_spec

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _FakeAPIError(Exception):
    """Stand-in for the httpx/SDK exception AnthropicProvider._handle_error consumes.

    Mirrors the attributes ``_handle_error`` reads off a real API exception:
    a parsed JSON ``body`` and a ``status_code``.
    """

    def __init__(self, body: dict, status_code: int = 400):
        super().__init__(str(body))
        self.body = body
        self.status_code = status_code


@pytest.mark.asyncio
async def test_runner_returns_structured_tool_error():
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="working",
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=RuntimeError("boom"))

    runner = AgentRunner(provider)

    result = await runner.run(make_spec(
        initial_messages=[],
        tools=tools,
        max_iterations=2,
        fail_on_tool_error=True,
    ))

    assert result.stop_reason == "tool_error"
    assert result.error == "Error: RuntimeError: boom"
    assert result.tool_events == [
        {"name": "list_dir", "status": "error", "detail": "boom"}
    ]


@pytest.mark.asyncio
async def test_llm_error_not_appended_to_session_messages():
    """When LLM returns finish_reason='error', the error content must NOT be
    appended to the messages list (prevents polluting session history)."""
    from jenny.agent.runner import (
        _PERSISTED_MODEL_ERROR_PLACEHOLDER,
        AgentRunner,
    )

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="429 rate limit exceeded", finish_reason="error", tool_calls=[], usage={},
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        max_iterations=5,
    ))

    assert result.stop_reason == "error"
    assert result.final_content == "429 rate limit exceeded"
    assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]
    assert all("429" not in (m.get("content") or "") for m in assistant_msgs), \
        "Error content should not appear in session messages"
    assert assistant_msgs[-1]["content"] == _PERSISTED_MODEL_ERROR_PLACEHOLDER


@pytest.mark.asyncio
async def test_llm_error_with_partial_content_persists_partial_and_marker():
    """A genuine mid-stream exception that already streamed text to the user
    must persist that text + an interruption marker instead of discarding it
    for the generic placeholder (#audit mid-stream-exception loss)."""
    from jenny.agent.runner import (
        _PARTIAL_CONTENT_INTERRUPTED_MARKER,
        _PERSISTED_MODEL_ERROR_PLACEHOLDER,
        AgentRunner,
    )

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="Error calling LLM: connection reset mid-stream",
        finish_reason="error",
        tool_calls=[],
        usage={},
        partial_content="Here is the beginning of my answer",
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        max_iterations=5,
    ))

    assert result.stop_reason == "error"
    assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]
    assert assistant_msgs[-1]["content"] == (
        f"Here is the beginning of my answer\n\n{_PARTIAL_CONTENT_INTERRUPTED_MARKER}"
    )
    assert assistant_msgs[-1]["content"] != _PERSISTED_MODEL_ERROR_PLACEHOLDER


@pytest.mark.asyncio
async def test_llm_arrearage_error_surfaces_clear_message():
    """Arrearage errors yield a clear user-facing message, not a raw dump (#3006)."""
    from jenny.agent.runner import _ARREARAGE_ERROR_MESSAGE, AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="HTTP 402 insufficient_quota", finish_reason="error", error_status_code=402,
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        max_iterations=5,
    ))

    assert result.stop_reason == "error"
    assert result.final_content == _ARREARAGE_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_runner_tool_error_sets_final_content():
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)

    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="call_1", name="read_file", arguments={"path": "x"})],
            usage={},
        )

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=RuntimeError("boom"))

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=tools,
        max_iterations=1,
        fail_on_tool_error=True,
    ))

    assert result.final_content == "Error: RuntimeError: boom"
    assert result.stop_reason == "tool_error"


@pytest.mark.asyncio
async def test_runner_tool_error_preserves_tool_results_in_messages():
    """When a tool raises a fatal error, its results must still be appended
    to messages so the session never contains orphan tool_calls (#2943)."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)

    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="tc1", name="read_file", arguments={"path": "a"}),
                ToolCallRequest(id="tc2", name="python_exec", arguments={"code": "raise RuntimeError('bad')"}),
            ],
            usage={},
        )

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry

    call_idx = 0

    async def fake_execute(name, args, **kw):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 2:
            raise RuntimeError("boom")
        return "file content"

    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=fake_execute)

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do stuff"}],
        tools=tools,
        max_iterations=1,
        fail_on_tool_error=True,
    ))

    assert result.stop_reason == "tool_error"
    # Both tool results must be in messages even though tc2 had a fatal error.
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["tool_call_id"] == "tc1"
    assert tool_msgs[1]["tool_call_id"] == "tc2"
    # The assistant message with tool_calls must precede the tool results.
    asst_tc_idx = next(
        i for i, m in enumerate(result.messages)
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    tool_indices = [
        i for i, m in enumerate(result.messages) if m.get("role") == "tool"
    ]
    assert all(ti > asst_tc_idx for ti in tool_indices)


def test_anthropic_context_overflow_error_is_detected_as_context_length_error():
    """Anthropic's real API error shape for context overflow is a 400
    invalid_request_error with a message like:
        "prompt is too long: 250000 tokens > 200000 maximum"
    This must be recognized by the runner's context-length-error detector so the
    automatic shrink-and-retry recovery kicks in for Anthropic, not just OpenAI's
    ``context_length_exceeded`` error code shape.
    """
    from jenny.agent.runner import AgentRunner

    payload = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "prompt is too long: 250000 tokens > 200000 maximum",
        },
    }
    response = AnthropicProvider._handle_error(_FakeAPIError(payload))

    # Sanity-check the exact field values _handle_error actually produced,
    # so the assertion below is proven to exercise the real bug scenario.
    assert response.finish_reason == "error"
    assert response.error_type == "invalid_request_error"
    assert response.error_code is None
    assert "prompt is too long" in (response.content or "").lower()

    assert AgentRunner._is_context_length_error(response) is True
    assert AgentRunner._extract_context_limit(response) == 200000


def test_anthropic_unrelated_invalid_request_error_is_not_misdetected():
    """A non-overflow Anthropic ``invalid_request_error`` (e.g. a malformed
    request) must NOT be misdetected as a context-length error -- matching on
    ``invalid_request_error`` alone would be far too broad, since Anthropic uses
    that error type for many unrelated validation failures.
    """
    from jenny.agent.runner import AgentRunner

    payload = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "messages: at least one message is required",
        },
    }
    response = AnthropicProvider._handle_error(_FakeAPIError(payload))

    assert response.error_type == "invalid_request_error"
    assert AgentRunner._is_context_length_error(response) is False


def test_openai_context_length_exceeded_still_detected():
    """Guard against regressions in the pre-existing OpenAI-shaped detection
    while extending the matcher for Anthropic."""
    from jenny.agent.runner import AgentRunner

    response = LLMResponse(
        content=(
            "This model's maximum context length is 8192 tokens. "
            "However, your messages resulted in 9000 tokens."
        ),
        finish_reason="error",
        error_type="invalid_request_error",
        error_code="context_length_exceeded",
    )

    assert AgentRunner._is_context_length_error(response) is True
    assert AgentRunner._extract_context_limit(response) == 8192
