"""Tests per il watchdog di stallo: marca, non condanna."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.aio import wait_until

from jenny.agent.hook import AgentHookContext
from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import SubagentManager, SubagentStatus, _SubagentHook
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    defaults = dict(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        stall_threshold_s=1.0,
        stall_check_interval_s=0.01,
    )
    defaults.update(kw)
    sm = SubagentManager(**defaults)
    sm.bus.publish_inbound = AsyncMock()
    return sm


def _blocking_runner(sm: SubagentManager, block: asyncio.Event) -> None:
    async def _run(spec):
        await block.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _run


async def _settle(sm: SubagentManager, block: asyncio.Event | None = None) -> None:
    if block is not None:
        block.set()
    tasks = [t for t in sm._running_tasks.values() if not t.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)
    await sm._cancel_stall_watchdog()


# ---------------------------------------------------------------------------
# last_progress_at
# ---------------------------------------------------------------------------


class TestProgressStamp:
    def test_defaults_to_started_at(self):
        status = SubagentStatus(
            task_id="t1", label="l", task_description="d", started_at=123.0,
        )
        assert status.last_progress_at == 123.0
        assert status.state == "running"

    def test_touch_advances_and_clears_stalled(self):
        status = SubagentStatus(
            task_id="t1", label="l", task_description="d", started_at=time.monotonic(),
        )
        status.last_progress_at = 1.0
        status.state = "stalled"
        status.touch()
        assert status.last_progress_at > 1.0
        assert status.state == "running"

    def test_touch_does_not_resurrect_a_terminal_state(self):
        status = SubagentStatus(
            task_id="t1", label="l", task_description="d", started_at=time.monotonic(),
        )
        status.state = "cancelled"
        status.touch()
        assert status.state == "cancelled"

    @pytest.mark.asyncio
    async def test_after_iteration_stamps_progress(self):
        status = SubagentStatus(
            task_id="t1", label="l", task_description="d", started_at=time.monotonic(),
        )
        status.last_progress_at = 1.0
        status.state = "stalled"
        hook = _SubagentHook("t1", status)
        await hook.after_iteration(AgentHookContext(
            iteration=2, tool_calls=[], tool_events=[], messages=[], usage={},
            error=None, stop_reason=None, final_content=None,
        ))
        assert status.last_progress_at > 1.0
        assert status.state == "running"

    @pytest.mark.asyncio
    async def test_checkpoint_stamps_progress(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=0.0)
        observed: dict = {}

        async def _run(spec):
            status = next(iter(sm._task_statuses.values()))
            status.last_progress_at = 1.0
            status.state = "stalled"
            await spec.checkpoint_callback({"phase": "awaiting_tools", "iteration": 3})
            observed["last_progress_at"] = status.last_progress_at
            observed["state"] = status.state
            observed["phase"] = status.phase
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

        sm.runner.run = _run
        await sm.spawn("task", session_key="s1")
        await _settle(sm)

        assert observed["last_progress_at"] > 1.0
        assert observed["state"] == "running"
        assert observed["phase"] == "awaiting_tools"


# ---------------------------------------------------------------------------
# check_stalls
# ---------------------------------------------------------------------------


class TestCheckStalls:
    @pytest.mark.asyncio
    async def test_marks_stalled_without_cancelling(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        task_id, status = next(iter(sm._task_statuses.items()))
        status.last_progress_at = time.monotonic() - 300

        assert sm.check_stalls() == [task_id]
        assert status.state == "stalled"
        # Marcatura sola: il subagent resta vivo e in pool.
        assert not sm._running_tasks[task_id].done()
        assert sm.get_running_count() == 1
        assert sm.get_running_count_by_session("s1") == 1

        await _settle(sm, block)

    @pytest.mark.asyncio
    async def test_marking_is_reported_once(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        status = next(iter(sm._task_statuses.values()))
        status.last_progress_at = time.monotonic() - 300

        assert len(sm.check_stalls()) == 1
        assert sm.check_stalls() == []
        assert status.state == "stalled"

        await _settle(sm, block)

    @pytest.mark.asyncio
    async def test_resumed_progress_clears_stalled(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        status = next(iter(sm._task_statuses.values()))
        status.last_progress_at = time.monotonic() - 300
        sm.check_stalls()
        assert status.state == "stalled"

        status.last_progress_at = time.monotonic()
        assert sm.check_stalls() == []
        assert status.state == "running"

        await _settle(sm, block)

    @pytest.mark.asyncio
    async def test_fresh_subagent_is_not_marked(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        assert sm.check_stalls() == []
        assert next(iter(sm._task_statuses.values())).state == "running"

        await _settle(sm, block)


# ---------------------------------------------------------------------------
# ciclo di vita del task di vigilanza
# ---------------------------------------------------------------------------


class TestWatchdogTask:
    @pytest.mark.asyncio
    async def test_single_task_created_lazily_for_all_subagents(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0, max_concurrent_subagents=8)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        assert sm._watchdog_task is None

        await sm.spawn("a", session_key="s1")
        watchdog = sm._watchdog_task
        assert watchdog is not None and not watchdog.done()

        await sm.spawn("b", session_key="s1")
        # Uno solo per tutti: niente un task per subagent.
        assert sm._watchdog_task is watchdog

        await _settle(sm, block)

    @pytest.mark.asyncio
    async def test_loop_marks_stalled_subagents(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=0.05, stall_check_interval_s=0.01)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        task_id, status = next(iter(sm._task_statuses.items()))

        await wait_until(lambda: status.state == "stalled")
        assert not sm._running_tasks[task_id].done()

        await _settle(sm, block)

    @pytest.mark.asyncio
    async def test_drain_cancels_the_watchdog(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        watchdog = sm._watchdog_task
        assert watchdog is not None

        drained = await sm.drain(timeout_s=0.01)
        assert drained == 1
        assert sm._watchdog_task is None
        assert watchdog.done()

        block.set()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_drain_without_subagents_still_cancels_the_watchdog(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        watchdog = sm._watchdog_task
        assert watchdog is not None
        await _settle(sm, block)
        sm._watchdog_task = watchdog  # simula un watchdog ancora vivo

        assert await sm.drain(timeout_s=0.01) == 0
        assert sm._watchdog_task is None
        assert watchdog.done()

    @pytest.mark.asyncio
    async def test_loop_exits_on_its_own_when_nothing_is_left(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=60.0, stall_check_interval_s=0.01)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))

        await sm.spawn("quick work", session_key="s1")
        watchdog = sm._watchdog_task
        assert watchdog is not None
        await asyncio.gather(*sm._running_tasks.values(), return_exceptions=True)

        await wait_until(watchdog.done)
        assert not watchdog.cancelled()

        # Uno spawn successivo ne crea uno nuovo.
        await sm.spawn("more work", session_key="s1")
        assert sm._watchdog_task is not None and sm._watchdog_task is not watchdog
        await _settle(sm)

    @pytest.mark.asyncio
    async def test_non_positive_threshold_disables_the_watchdog(self, tmp_path: Path):
        sm = _manager(tmp_path, stall_threshold_s=0.0)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("slow work", session_key="s1")
        assert sm._watchdog_task is None

        await _settle(sm, block)

    def test_threshold_default_comes_from_agent_defaults(self, tmp_path: Path):
        from jenny.config.schema import AgentDefaults

        sm = _manager(tmp_path, stall_threshold_s=None)
        assert sm.stall_threshold_s == float(AgentDefaults().subagent_stall_threshold_seconds)
        assert sm.stall_threshold_s == 180.0
