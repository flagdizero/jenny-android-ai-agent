"""Il provider che rifiuta di rispondere (``content_filter``, ``refusal``).

Misurato sul telefono: dopo il rientro di un aiutante DeepSeek rispondeva
``content_filter`` a vuoto, e il runner lo trattava come una risposta vuota —
due retry identici, la finalizzazione senza tool, tutti rifiutati uguali, poi
«non sono riuscita a scrivere una risposta». Qui: un solo tentativo col
risultato dell'aiutante tolto dalla richiesta, poi ``provider_refused``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from support.runner import empty_tools, make_spec

from jenny.agent.context_governor import last_helper_result, mask_refused_results
from jenny.agent.response_outcome import ResponseOutcome, classify_response
from jenny.agent.runner import AgentRunner
from jenny.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from jenny.runtime.user_text import say
from jenny.session.history_meta import INJECTED_EVENT_META, SUBAGENT_RESULT_EVENT
from jenny.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    PROVIDER_REFUSED_MESSAGE,
    PROVIDER_REFUSED_RESULT_PLACEHOLDER,
)

HELPER_RESULT = "[Helper result] The researcher wrote wiki/acero.md: sap, leaves, ..."


def _helper_turn() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "do the step"},
        {"role": "assistant", "content": "I started a helper."},
        {
            "role": "user",
            "content": HELPER_RESULT,
            INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT,
            "subagent_task_id": "t1",
        },
    ]


def _refused(reason: str = "content_filter") -> LLMResponse:
    return LLMResponse(
        content=None, finish_reason=reason, usage={"prompt_tokens": 50, "completion_tokens": 0}
    )


class Provider:
    """Risponde in ordine dalla lista; registra cosa ha visto in ogni richiesta."""

    def __init__(self, *responses: LLMResponse, then: LLMResponse | None = None) -> None:
        self.responses = list(responses)
        self.then = then
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, messages, tools=None, **kwargs) -> LLMResponse:
        self.calls.append({
            "contents": [m.get("content") for m in messages if m.get("role") == "user"],
            "tools": tools,
        })
        if self.responses:
            return self.responses.pop(0)
        assert self.then is not None, "unexpected extra request"
        return self.then


def _runner(provider: Provider) -> AgentRunner:
    llm = MagicMock(spec=LLMProvider)
    llm.chat_with_retry = provider
    llm.chat_stream_with_retry = provider
    return AgentRunner(llm)


async def _run(provider: Provider, messages: list[dict[str, Any]], **spec: Any):
    spec.setdefault("max_iterations", 5)
    return await _runner(provider).run(make_spec(
        initial_messages=messages, tools=empty_tools(), **spec,
    ))


# ── il classificatore ───────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["content_filter", "refusal"])
def test_a_blank_refusal_is_its_own_outcome(reason: str) -> None:
    assert classify_response(_refused(reason), None, max_tokens=8192) is ResponseOutcome.REFUSED


def test_a_refusal_with_text_is_an_answer() -> None:
    """Se il provider ha scritto qualcosa, quel testo è la risposta."""
    response = LLMResponse(content="I can't help with that.", finish_reason="refusal")
    assert classify_response(response, response.content, max_tokens=8192) is ResponseOutcome.OK


# ── il runner ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["content_filter", "refusal"])
async def test_one_retry_without_the_helper_result_then_the_turn_goes_on(reason: str) -> None:
    provider = Provider(_refused(reason), LLMResponse(content="Step done.", usage={}))
    messages = _helper_turn()
    result = await _run(provider, messages)

    assert result.final_content == "Step done."
    assert result.stop_reason == "completed"
    assert len(provider.calls) == 2
    assert provider.calls[0]["contents"][-1] == HELPER_RESULT
    assert provider.calls[1]["contents"][-1] == PROVIDER_REFUSED_RESULT_PLACEHOLDER
    # Il tentativo è un turno normale, coi suoi tool.
    assert provider.calls[1]["tools"] is not None
    # La storia tiene il risultato com'era: il segnaposto era solo nella richiesta.
    helper_rows = [m for m in result.messages if m.get(INJECTED_EVENT_META)]
    assert [m["content"] for m in helper_rows] == [HELPER_RESULT]
    assert messages[-1]["content"] == HELPER_RESULT


async def test_the_placeholder_stays_for_the_rest_of_the_run() -> None:
    """Il tentativo chiama un tool: la richiesta dopo non rimette il risultato rifiutato."""
    provider = Provider(
        _refused(),
        LLMResponse(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id="c1", name="read_file", arguments={"path": "a"})],
        ),
        LLMResponse(content="Read the file instead.", usage={}),
    )
    result = await _run(provider, _helper_turn())
    assert result.final_content == "Read the file instead."
    assert len(provider.calls) == 3
    assert HELPER_RESULT not in provider.calls[2]["contents"]
    assert PROVIDER_REFUSED_RESULT_PLACEHOLDER in provider.calls[2]["contents"]


@pytest.mark.parametrize("reason", ["content_filter", "refusal"])
async def test_refused_again_ends_the_turn_as_provider_refused(reason: str) -> None:
    provider = Provider(then=_refused(reason))
    result = await _run(provider, _helper_turn())

    # La richiesta originale e un solo tentativo, diverso: niente retry identici,
    # niente finalizzazione senza tool.
    assert len(provider.calls) == 2
    assert all(call["tools"] is not None for call in provider.calls)
    assert result.stop_reason == "provider_refused"
    assert result.final_content == say("turn.refused")
    assert result.messages[-1] == {"role": "assistant", "content": PROVIDER_REFUSED_MESSAGE}


async def test_a_plain_user_message_last_gets_no_retry() -> None:
    """Non c'è un risultato d'aiutante da togliere: niente tentativo."""
    provider = Provider(then=_refused())
    result = await _run(provider, [{"role": "user", "content": "tell me about maples"}])

    assert len(provider.calls) == 1
    assert result.stop_reason == "provider_refused"
    assert result.final_content == say("turn.refused")


async def test_a_blank_stop_keeps_the_empty_response_recovery() -> None:
    """Non regressione: ``stop`` a vuoto resta il ramo vuoto, con le sue richieste."""
    provider = Provider(then=LLMResponse(content=None, finish_reason="stop", usage={}))
    result = await _run(provider, _helper_turn(), max_iterations=3)

    # Due retry ciechi e la finalizzazione senza tool, sul contesto intatto.
    assert len(provider.calls) == 3
    assert provider.calls[2]["tools"] is None
    assert all(HELPER_RESULT in call["contents"] for call in provider.calls)
    assert result.stop_reason == "empty_final_response"
    assert result.final_content == say("turn.empty")
    assert result.messages[-1]["content"] == EMPTY_FINAL_RESPONSE_MESSAGE


# ── il segnaposto ───────────────────────────────────────────────────────────


def test_only_a_helper_result_as_the_last_user_row_is_a_candidate() -> None:
    assert last_helper_result(_helper_turn()) == HELPER_RESULT
    after_tools = [
        *_helper_turn(),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file"},
    ]
    assert last_helper_result(after_tools) == HELPER_RESULT
    assert last_helper_result([*_helper_turn(), {"role": "user", "content": "and?"}]) is None
    assert last_helper_result([{"role": "user", "content": "hi"}]) is None


def test_the_placeholder_keeps_the_runtime_context() -> None:
    """Il messaggio corrente di un turno d'annuncio porta anche piano e ora."""
    from jenny.agent.context import ContextBuilder

    runtime = f"{ContextBuilder._RUNTIME_CONTEXT_TAG}\nPlan: step 2 of 3\n[/Runtime Context]"
    text_row = {"role": "user", "content": f"{HELPER_RESULT}\n\n{runtime}"}
    blocks_row = {
        "role": "user",
        "content": [{"type": "text", "text": HELPER_RESULT}, {"type": "text", "text": runtime}],
    }
    other = {"role": "user", "content": "untouched"}
    messages = [other, text_row, blocks_row]

    masked = mask_refused_results(messages, [text_row["content"], blocks_row["content"]])

    assert masked[0] is other
    assert masked[1]["content"] == f"{PROVIDER_REFUSED_RESULT_PLACEHOLDER}\n\n{runtime}"
    assert masked[2]["content"] == [
        {"type": "text", "text": PROVIDER_REFUSED_RESULT_PLACEHOLDER},
        {"type": "text", "text": runtime},
    ]
    assert messages[1]["content"].startswith(HELPER_RESULT)
    assert mask_refused_results(messages, []) is messages
