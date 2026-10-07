"""Tests for subagent tool registration and wiring."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.config.schema import AgentDefaults

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _spec(task: str = "do task", label: str = "label", **kw):
    from jenny.agent.subagent import SubagentSpec

    defaults = dict(origin_channel="test", origin_chat_id="c1")
    defaults.update(kw)
    return SubagentSpec(task=task, label=label, **defaults)


@pytest.mark.asyncio
async def test_subagent_exec_tool_receives_allowed_env_keys(tmp_path):
    """allowed_modules from PythonExecConfig must be forwarded to the subagent's PythonExecTool."""
    from jenny.agent.subagent import SubagentManager, SubagentStatus
    from jenny.agent.tools.python_exec import PythonExecConfig
    from jenny.bus.queue import MessageBus
    from jenny.config.schema import ToolsConfig

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        tools_config=ToolsConfig(python_exec=PythonExecConfig(allowed_modules=["os", "json", "math"])),
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        exec_tool = spec.tools.get("python_exec")
        assert exec_tool is not None
        assert exec_tool.namespace.allowed_modules == {"os", "json", "math"}
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    await mgr._run_subagent("sub-1", _spec(), status)

    mgr.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_subagent_uses_configured_max_iterations(tmp_path):
    """Subagents should honor the configured tool-iteration limit."""
    from jenny.agent.subagent import SubagentManager, SubagentStatus
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_iterations=37,
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        assert spec.max_iterations == 37
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    await mgr._run_subagent("sub-1", _spec(), status)

    mgr.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_spawn_forwards_temperature_to_run_spec(tmp_path):
    """A temperature passed to spawn() should reach the AgentRunSpec."""
    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()

    seen = {}

    async def fake_run(spec):
        seen["temperature"] = spec.temperature
        return SimpleNamespace(
            stop_reason="done", final_content="done", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    await mgr.spawn(task="do task", temperature=0.9)
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)

    assert seen["temperature"] == 0.9


@pytest.mark.asyncio
async def test_spawn_tool_rejects_when_at_concurrency_limit(tmp_path):
    """SpawnTool should return an error string when the concurrency limit is reached."""
    from jenny.agent.subagent import SubagentManager
    from jenny.agent.tools.spawn import SpawnTool
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        # Il limite di default è 5: qui serve un pool a slot singolo per
        # esercitare il rifiuto formattato dal tool layer.
        max_concurrent_subagents=1,
    )
    mgr._announce_result = AsyncMock()

    # Block the first subagent so it stays "running"
    release = asyncio.Event()

    async def fake_run(spec):
        await release.wait()
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    from jenny.agent.tools.context import RequestContext

    tool = SpawnTool(mgr)
    tool.set_context(RequestContext(channel="test", chat_id="c1", session_key="test:c1"))

    # First spawn succeeds
    result = await tool.execute(task="first task")
    assert "started" in result

    # Second spawn should be rejected (limit is 1 for this manager)
    result = await tool.execute(task="second task")
    assert "Cannot spawn subagent" in result
    assert "concurrency limit reached" in result

    # Release the first subagent
    release.set()
    # Allow cleanup
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)


def test_subagent_default_max_concurrent_matches_agent_defaults(tmp_path):
    """Direct SubagentManager construction should use the agent default concurrency limit."""
    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert mgr.max_concurrent_subagents == AgentDefaults().max_concurrent_subagents


def test_subagent_default_max_iterations_matches_agent_defaults(tmp_path):
    """Direct SubagentManager construction should use the agent default limit."""
    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert mgr.max_iterations == AgentDefaults().max_tool_iterations


def test_agent_loop_passes_max_iterations_to_subagents(tmp_path):
    """AgentLoop's configured limit should be shared with spawned subagents."""
    from jenny.agent.loop import AgentLoop
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=42,
    )

    assert loop.subagents.max_iterations == 42


@pytest.mark.asyncio
async def test_agent_loop_syncs_updated_max_iterations_before_run(tmp_path):
    """Runtime max_iterations changes should be reflected before tool execution."""
    from jenny.agent.loop import AgentLoop
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=42,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])

    async def fake_run(spec):
        assert spec.max_iterations == 55
        assert loop.subagents.max_iterations == 55
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
            images_stripped=False,
            goal_stalled=False,
        )

    loop.runner.run = AsyncMock(side_effect=fake_run)
    loop.max_iterations = 55

    await loop._run_agent_loop([])

    loop.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_pending_never_blocks_while_subagents_running(tmp_path):
    """_drain_pending returns at once even while a sub-agent spawned in this turn runs.

    Fino al 02/10/2026 qui il turno restava fermo fino a 300 secondi: il risultato
    di un subagent che arriva a turno chiuso apre ora un turno suo.
    """
    from jenny.agent.loop import AgentLoop
    from jenny.bus.events import InboundMessage
    from jenny.bus.queue import MessageBus
    from jenny.session.manager import Session

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    pending_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
    session = Session(key="test:drain-block")
    injection_callback = None

    async def _hang_forever():
        await asyncio.Event().wait()

    hang_task = asyncio.create_task(_hang_forever())

    async def fake_runner_run(spec):
        nonlocal injection_callback
        injection_callback = spec.injection_callback
        # Il subagent nasce dentro il turno e resta vivo.
        loop.subagents._session_tasks.setdefault(session.key, set()).add("sub-drain-1")
        loop.subagents._running_tasks["sub-drain-1"] = hang_task
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
            images_stripped=False,
            goal_stalled=False,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    try:
        await loop._run_agent_loop(
            [{"role": "user", "content": "test"}],
            session=session,
            channel="test",
            chat_id="c1",
            pending_queue=pending_queue,
        )
        assert injection_callback is not None

        assert await asyncio.wait_for(injection_callback(), timeout=1.0) == []

        # Quello che e' gia' in coda entra ancora, come prima.
        await pending_queue.put(InboundMessage(
            sender_id="subagent",
            channel="test",
            chat_id="c1",
            content="Sub-agent result",
            media=None,
            metadata={},
        ))
        results = await asyncio.wait_for(injection_callback(), timeout=1.0)
        assert len(results) == 1
        assert results[0]["role"] == "user"
        assert "Sub-agent result" in str(results[0]["content"])
    finally:
        hang_task.cancel()
        await asyncio.gather(hang_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_drain_pending_no_block_when_no_subagents(tmp_path):
    """_drain_pending should not block when no sub-agents are running."""
    from jenny.agent.loop import AgentLoop
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    pending_queue: asyncio.Queue = asyncio.Queue()
    injection_callback = None

    async def fake_runner_run(spec):
        nonlocal injection_callback
        injection_callback = spec.injection_callback
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
            images_stripped=False,
            goal_stalled=False,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    await loop._run_agent_loop(
        [{"role": "user", "content": "test"}],
        session=None,
        channel="test",
        chat_id="c1",
        pending_queue=pending_queue,
    )

    assert injection_callback is not None

    # With no sub-agents and empty queue, should return immediately
    results = await asyncio.wait_for(injection_callback(), timeout=1.0)
    assert results == []


async def test_an_app_builder_is_spawned_only_for_a_user_conversation() -> None:
    """Un costruttore lanciato da un cron non potrebbe scrivere: lo si dice
    subito, invece di spendere un giro intero."""
    from jenny.agent.tools.context import RequestContext
    from jenny.agent.tools.spawn import SpawnTool

    mgr = MagicMock()
    mgr.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(mgr)
    tool.set_context(RequestContext(channel="internal", chat_id="j", session_key="cron:job"))
    out = await tool.execute(task="fai un'app", agent_type="app-builder")
    assert out.startswith("Cannot spawn an app-builder here") and not mgr.spawn.await_count
    tool.set_context(RequestContext(channel="websocket", chat_id="project:soldi",
                                    session_key="project:soldi"))
    assert await tool.execute(task="fai un'app", agent_type="app-builder") == "started"
