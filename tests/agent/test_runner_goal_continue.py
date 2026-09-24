"""Tests for sustained-goal continuation in AgentRunner.

When a goal_active_predicate returns True and the model has just done real work,
the runner must not exit with stop_reason="completed" after a plain-text final
response: it injects a continuation message and keeps looping (similar to
mid-turn injection).

The nudge is bounded, though. It is withheld when the model produced no tool call
since the previous continuation, when the final text reads as a question to the
user, and when the per-run cap is reached — the three shapes that made a guided
Q&A flow burn 9 LLM calls in 45 seconds on 2026-08-12. In all three cases the run
ends normally and reports ``goal_stalled``, so the product layer can park the goal
instead of letting the model close it with a false recap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from support.runner import empty_tools, make_spec

from jenny.providers.base import LLMProvider, LLMResponse, ToolCallRequest


def _tools() -> MagicMock:
    """Registry that executes any tool call and returns a short result."""
    tools = empty_tools()
    return tools


def _tool_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="call_1", name="read_file", arguments={"path": "x"})],
    )


def _goal_continue_messages(messages: list[dict]) -> list[dict]:
    return [
        m
        for m in messages
        if m.get("role") == "user" and "sustained goal" in str(m.get("content", ""))
    ]


@pytest.mark.asyncio
async def test_runner_exits_normally_without_predicate():
    """Baseline: no predicate, runner exits with completed on final text."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="all done", tool_calls=[], usage={},
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=tools,
        max_iterations=2,
    ))

    assert result.stop_reason == "completed"
    assert result.final_content == "all done"


@pytest.mark.asyncio
async def test_runner_exits_normally_with_inactive_goal():
    """Predicate returns False, runner should exit normally."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="all done", tool_calls=[], usage={},
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=tools,
        max_iterations=2,
        goal_active_predicate=lambda: False,
    ))

    assert result.stop_reason == "completed"
    assert result.final_content == "all done"


@pytest.mark.asyncio
async def test_runner_forces_continue_after_real_tool_progress():
    """Tool work then plain text with an active goal → one continuation, then stop.

    Il nudge utile è questo: il modello ha eseguito un tool e si è fermato a
    commentare mentre il goal è ancora aperto. Al giro dopo, però, non c'è nuovo
    lavoro da cui ripartire, quindi il run si chiude invece di insistere.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_response()
        return LLMResponse(content="still working on it", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=_tools(),
        max_iterations=20,
        goal_active_predicate=lambda: True,
    ))

    # tool → testo (nudge) → testo (niente progresso: stop). Non 20.
    assert calls["n"] == 3
    assert result.stop_reason == "completed"
    assert len(_goal_continue_messages(result.messages)) == 1
    assert result.goal_stalled is True


@pytest.mark.asyncio
async def test_runner_withholds_continuation_without_tool_progress():
    """Text-only answers with an active goal must not be nudged at all.

    Regressione diretta dell'incidente del 2026-08-12: la continuation sintetica
    non veniva contata da ``_MAX_INJECTION_CYCLES``, quindi un flusso guidato che
    risponde solo a parole veniva spronato fino a ``max_iterations`` (200 di
    default). Ora un turno senza tool esce al primo giro.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="ok, tell me more when you can", tool_calls=[], usage={},
    ))

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=_tools(),
        max_iterations=50,
        goal_active_predicate=lambda: True,
        finalize_on_max_iterations=False,
    ))

    assert provider.chat_with_retry.await_count == 1
    assert result.stop_reason == "completed"
    assert result.final_content == "ok, tell me more when you can"
    assert _goal_continue_messages(result.messages) == []
    assert result.goal_stalled is True


@pytest.mark.asyncio
async def test_runner_withholds_continuation_when_answer_is_a_question():
    """A final response that asks the user something parks the goal, tools or not.

    È la forma esatta di ``app-creator``: una domanda per turno. Nessuna
    continuation può rispondere al posto dell'utente.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_response()
        return LLMResponse(
            content="prima domanda: cosa dovrebbe fare questa app?\n\ndammi un'idea anche vaga",
            tool_calls=[],
            usage={},
        )

    provider.chat_with_retry = chat_with_retry

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "create an app, guide me"}],
        tools=_tools(),
        max_iterations=50,
        goal_active_predicate=lambda: True,
    ))

    # Il tool ha prodotto progresso, ma la risposta è una domanda: zero nudge.
    assert calls["n"] == 2
    assert result.stop_reason == "completed"
    assert _goal_continue_messages(result.messages) == []
    assert result.goal_stalled is True


@pytest.mark.asyncio
async def test_runner_goal_continue_respects_per_run_cap(monkeypatch):
    """Even alternating tool/text work cannot nudge past _MAX_GOAL_CONTINUE_CYCLES."""
    from jenny.agent import runner as runner_mod
    from jenny.agent.runner import AgentRunner

    monkeypatch.setattr(runner_mod, "_MAX_GOAL_CONTINUE_CYCLES", 2)
    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        # Alterna lavoro e commento: ogni testo è preceduto da progresso vero,
        # quindi solo il tetto può fermare la catena.
        if calls["n"] % 2 == 1:
            return _tool_response()
        return LLMResponse(content="chunk done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=_tools(),
        max_iterations=50,
        goal_active_predicate=lambda: True,
    ))

    assert len(_goal_continue_messages(result.messages)) == 2
    assert result.stop_reason == "completed"
    assert result.goal_stalled is True


@pytest.mark.asyncio
async def test_runner_goal_stalled_false_when_goal_inactive():
    """No goal, no parking: goal_stalled stays False on an ordinary run."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="anything else?", tool_calls=[], usage={},
    ))

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=_tools(),
        max_iterations=3,
        goal_active_predicate=lambda: False,
    ))

    assert result.goal_stalled is False
    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_runner_respects_max_iterations_even_with_active_goal():
    """A goal that keeps calling tools still stops at max_iterations.

    Il budget di iterazioni resta il tetto del turno: il fix tocca solo i nudge
    sintetici, non la libertà di un goal che sta davvero lavorando.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=_tool_response())

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=_tools(),
        max_iterations=1,
        goal_active_predicate=lambda: True,
        finalize_on_max_iterations=False,
    ))

    assert result.stop_reason == "max_iterations"
    # Un turno chiuso dal budget non è un goal in attesa: la continuazione
    # interna (session.turn_continuation) ha ancora la parola.
    assert result.goal_stalled is False


@pytest.mark.asyncio
async def test_runner_does_not_force_continue_on_error():
    """Even with active goal, an LLM error should exit with stop_reason="error"."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content=None, tool_calls=[], usage={},
        finish_reason="error",
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=tools,
        max_iterations=2,
        goal_active_predicate=lambda: True,
    ))

    assert result.stop_reason == "error"


@pytest.mark.asyncio
async def test_runner_uses_custom_goal_continue_message():
    """Custom goal_continue_message should be injected instead of the default."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_response()
        return LLMResponse(content="still working", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry

    custom_msg = "CUSTOM_CONTINUE_PLEASE"

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=_tools(),
        max_iterations=3,
        goal_active_predicate=lambda: True,
        goal_continue_message=custom_msg,
    ))

    user_msgs = [m for m in result.messages if m.get("role") == "user"]
    assert any(custom_msg in str(m.get("content", "")) for m in user_msgs)


@pytest.mark.asyncio
async def test_runner_resolves_goal_continue_message_lazily():
    """The continuation text can depend on goal metadata created during the run."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    llm_calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        llm_calls["n"] += 1
        if llm_calls["n"] == 1:
            return _tool_response()
        return LLMResponse(content="still working", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    calls = {"n": 0}

    def dynamic_msg() -> str:
        calls["n"] += 1
        return "Goal (active):\nWrite the article draft."

    runner = AgentRunner(provider)
    result = await runner.run(make_spec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=_tools(),
        max_iterations=2,
        goal_active_predicate=lambda: True,
        goal_continue_message=dynamic_msg,
        finalize_on_max_iterations=False,
    ))

    user_msgs = [m for m in result.messages if m.get("role") == "user"]
    assert calls["n"] == 1
    assert any("Write the article draft." in str(m.get("content", "")) for m in user_msgs)
