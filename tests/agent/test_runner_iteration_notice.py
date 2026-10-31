"""L'avviso di budget di ``AgentRunSpec.iteration_notice``.

Un aiutante non sapeva di avere un budget: si fermava a meta di una lettura,
senza aver scritto niente (prova del 24/10/2026). L'avviso entra **solo** dopo un
giro di tool (Checkpoint 1): prima della risposta finale la trasformerebbe in
un'altra iterazione, e la coda delle iniezioni ha un tetto di cicli suo.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from support.runner import empty_tools, make_spec

from jenny.agent.runner import AgentRunner
from jenny.agent.subagent import _budget_notice
from jenny.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from jenny.session.history_meta import INJECTED_EVENT_META
from jenny.utils.runtime import BUDGET_NOTICE_EVENT


def _tool_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="call_1", name="read_file", arguments={"path": "x"})],
    )


def _recording_provider(responses: list[LLMResponse]) -> tuple[MagicMock, list[list[dict]]]:
    seen: list[list[dict]] = []
    queue = list(responses)

    async def _chat(**kwargs: Any) -> LLMResponse:
        seen.append([dict(m) for m in kwargs["messages"]])
        return queue.pop(0) if len(queue) > 1 else queue[0]

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(side_effect=_chat)
    return provider, seen


def _notices(messages: list[dict]) -> list[dict]:
    return [m for m in messages if "NOTICE" in str(m.get("content"))]


async def test_the_notice_arrives_once_after_the_tools_of_its_iteration() -> None:
    provider, seen = _recording_provider([_tool_response()])
    calls: list[int] = []

    def notice(iteration: int) -> str | None:
        calls.append(iteration)
        return "NOTICE: 2 calls left" if iteration == 2 else None

    result = await AgentRunner(provider).run(make_spec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=empty_tools(),
        max_iterations=5,
        finalize_on_max_iterations=False,
        iteration_notice=notice,
    ))

    assert calls == [0, 1, 2, 3, 4], "called after each tool round, with the iteration index"
    # Le richieste 0..3 non lo vedono; dalla 4 (dopo i tool dell'iterazione 2) si.
    assert [len(_notices(m)) for m in seen] == [0, 0, 0, 1, 1]
    request = seen[3]
    assert "NOTICE" in str(request[-1]["content"]), "the last thing read before answering"
    assert request[-2]["role"] == "tool"
    stored = _notices(result.messages)
    assert len(stored) == 1
    assert stored[0][INJECTED_EVENT_META] == BUDGET_NOTICE_EVENT
    assert result.had_injections is False, "not an orchestrator message"


async def test_no_notice_after_a_final_response() -> None:
    provider, seen = _recording_provider([LLMResponse(content="done", tool_calls=[])])
    calls: list[int] = []

    def notice(iteration: int) -> str | None:
        calls.append(iteration)
        return "NOTICE"

    result = await AgentRunner(provider).run(make_spec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=empty_tools(),
        max_iterations=5,
        iteration_notice=notice,
    ))

    assert calls == []
    assert result.final_content == "done"
    assert len(seen) == 1


async def test_a_broken_notice_does_not_stop_the_run() -> None:
    provider, _ = _recording_provider([_tool_response(), LLMResponse(content="ok")])

    def notice(iteration: int) -> str | None:
        raise RuntimeError("boom")

    result = await AgentRunner(provider).run(make_spec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=empty_tools(),
        max_iterations=5,
        iteration_notice=notice,
    ))

    assert result.final_content == "ok"


def test_the_subagent_notice_fires_once_at_eighty_percent() -> None:
    notice = _budget_notice(10)
    fired = {i: notice(i) for i in range(10)}

    # Otto chiamate fatte (indice 7): ne restano due.
    assert [i for i, text in fired.items() if text] == [7]
    assert "2 model calls left" in str(fired[7])


def test_the_subagent_notice_does_not_miss_its_iteration() -> None:
    # Il runner lo chiama solo dopo un giro di tool: se proprio l'iterazione
    # della soglia non ne ha avuto uno, l'avviso arriva alla successiva.
    notice = _budget_notice(10)
    assert notice(5) is None
    assert notice(8) is not None
    assert notice(9) is None


def test_no_notice_when_no_call_is_left() -> None:
    assert _budget_notice(1)(0) is None
    assert _budget_notice(2)(0) is not None
