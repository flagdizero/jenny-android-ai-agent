"""Tests for SubagentManager lifecycle — spawn, run, announce, cancel."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jenny.agent.hook import AgentHookContext
from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import (
    SubagentManager,
    SubagentSpec,
    SubagentStatus,
    _SubagentHook,
)
from jenny.bus.queue import MessageBus
from jenny.config.schema import AgentDefaults
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
        # I test del ciclo di vita spawnano piu subagent senza esercitare il
        # limite di concorrenza (coperto a parte); alza il tetto di default.
        max_concurrent_subagents=8,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _spec(task: str = "do task", label: str = "label", **kw) -> SubagentSpec:
    defaults = dict(origin_channel="internal", origin_chat_id="direct")
    defaults.update(kw)
    return SubagentSpec(task=task, label=label, **defaults)


def _make_hook_context(**overrides) -> AgentHookContext:
    defaults = dict(
        iteration=1,
        tool_calls=[],
        tool_events=[],
        messages=[],
        usage={},
        error=None,
        stop_reason="completed",
        final_content="ok",
    )
    defaults.update(overrides)
    return AgentHookContext(**defaults)


async def _drain_subagent_tasks(sm: SubagentManager) -> None:
    tasks = list(sm._running_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# SubagentStatus defaults
# ---------------------------------------------------------------------------


class TestSubagentStatus:
    def test_defaults(self):
        s = SubagentStatus(
            task_id="abc", label="test", task_description="do stuff",
            started_at=time.monotonic(),
        )
        assert s.phase == "initializing"
        assert s.iteration == 0
        assert s.tool_events == []
        assert s.usage == {}
        assert s.stop_reason is None
        assert s.error is None


# ---------------------------------------------------------------------------
# set_provider
# ---------------------------------------------------------------------------


class TestSetProvider:
    def test_updates_provider_model_runner(self, tmp_path):
        sm = _manager(tmp_path)
        new_provider = MagicMock(spec=LLMProvider)
        sm.set_provider(new_provider, "new-model")
        assert sm.provider is new_provider
        assert sm.model == "new-model"
        assert sm.runner.provider is new_provider


# ---------------------------------------------------------------------------
# spawn
# ---------------------------------------------------------------------------


class TestSpawn:
    @pytest.mark.asyncio
    async def test_returns_string_with_task_id(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        result = await sm.spawn("do something")
        assert "started" in result
        assert "id:" in result

    @pytest.mark.asyncio
    async def test_creates_task_in_running_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task", session_key="s1")
        assert len(sm._running_tasks) == 1

        block.set()
        await _drain_subagent_tasks(sm)
        assert len(sm._running_tasks) == 0

    @pytest.mark.asyncio
    async def test_creates_status(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("my task")
        await _drain_subagent_tasks(sm)
        # Status cleaned up after task completes
        assert len(sm._task_statuses) == 0

    @pytest.mark.asyncio
    async def test_registers_in_session_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task", session_key="s1")
        assert "s1" in sm._session_tasks
        assert len(sm._session_tasks["s1"]) == 1

        block.set()
        await _drain_subagent_tasks(sm)
        assert "s1" not in sm._session_tasks

    @pytest.mark.asyncio
    async def test_no_session_key_no_registration(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task")
        assert len(sm._session_tasks) == 0

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_label_defaults_to_truncated_task(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        long_task = "A" * 50
        await sm.spawn(long_task, session_key="s1")
        status = next(iter(sm._task_statuses.values()))
        assert status.label == "A" * 40 + "…"

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_custom_label(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task", label="Custom Label", session_key="s1")
        status = next(iter(sm._task_statuses.values()))
        assert status.label == "Custom Label"

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_cleanup_callback_removes_all_entries(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("task", session_key="s1")
        await _drain_subagent_tasks(sm)
        assert len(sm._running_tasks) == 0
        assert len(sm._task_statuses) == 0
        assert len(sm._session_tasks) == 0

    @pytest.mark.asyncio
    async def test_raises_when_concurrency_limit_reached(self, tmp_path):
        """L'invariante di concorrenza e applicata nel manager: al raggiungimento
        del limite spawn solleva SubagentConcurrencyLimitError senza creare task."""
        from jenny.agent.subagent import SubagentConcurrencyLimitError

        sm = _manager(tmp_path, max_concurrent_subagents=1)
        block = asyncio.Event()

        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task1", session_key="s1")
        assert len(sm._running_tasks) == 1

        with pytest.raises(SubagentConcurrencyLimitError) as exc_info:
            await sm.spawn("task2", session_key="s1")
        assert exc_info.value.running == 1
        assert exc_info.value.limit == 1
        # Nessun task/status parziale creato dallo spawn rifiutato.
        assert len(sm._running_tasks) == 1
        assert len(sm._task_statuses) == 1

        block.set()
        await _drain_subagent_tasks(sm)

    def test_default_concurrency_leaves_room_for_a_reserved_slot(self):
        """Tre slot, uno dei quali resta riservato ai job quick.

        Tre e non cinque: ogni slot e una richiesta LLM in volo da un telefono, e
        oltre non cede la CPU ma il rate limit del provider. Il numero e comunque
        > 1, altrimenti la riserva si annulla (``_check_capacity``) e il fan-out
        si serializza — che era il bug osservato sul device.
        """
        assert AgentDefaults().max_concurrent_subagents == 3
        assert AgentDefaults().max_concurrent_subagents > 1

    @pytest.mark.asyncio
    async def test_reserves_one_slot_for_quick_spawns(self, tmp_path):
        """Uno spawn normale non puo prendere l'ultimo slot: senza riserva i
        long-running saturano il pool e non resta modo di fare un job breve."""
        from jenny.agent.subagent import SubagentConcurrencyLimitError

        sm = _manager(tmp_path, max_concurrent_subagents=5)
        block = asyncio.Event()

        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        for i in range(4):
            await sm.spawn(f"long task {i}", session_key="s1")
        assert sm.get_running_count() == 4

        with pytest.raises(SubagentConcurrencyLimitError) as exc_info:
            await sm.spawn("fifth long task", session_key="s1")
        assert exc_info.value.reserved is True
        assert exc_info.value.running == 4
        assert exc_info.value.limit == 5
        assert "reserved" in str(exc_info.value)
        assert sm.get_running_count() == 4

        # Lo slot riservato accetta un job quick.
        await sm.spawn("quick check", session_key="s1", quick=True)
        assert sm.get_running_count() == 5

        # Pool pieno: nemmeno un quick entra, e il rifiuto non è quello riservato.
        with pytest.raises(SubagentConcurrencyLimitError) as exc_info:
            await sm.spawn("another quick", session_key="s1", quick=True)
        assert exc_info.value.reserved is False

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_single_slot_config_still_spawns(self, tmp_path):
        """Con limite 1 la riserva si annulla, altrimenti non partirebbe nulla."""
        sm = _manager(tmp_path, max_concurrent_subagents=1)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        assert "started" in await sm.spawn("task", session_key="s1")
        await _drain_subagent_tasks(sm)


# ---------------------------------------------------------------------------
# retention Tier-1 alla terminazione
# ---------------------------------------------------------------------------


class TestTerminalRetention:
    @pytest.mark.asyncio
    async def test_completed_subagent_leaves_a_record(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="the answer is 42", messages=[], stop_reason="completed",
        ))
        sm.bus.publish_inbound = AsyncMock()

        await sm.spawn("compute", label="computer", session_key="s1")
        await _drain_subagent_tasks(sm)

        # Lo status vivo è sparito, ma il lavoro non è svanito con lui.
        assert sm._task_statuses == {}
        records = sm.list_records("s1")
        assert len(records) == 1
        record = records[0]
        assert record.state == "done"
        assert record.stop_reason == "completed"
        assert record.attempt == 1
        assert record.lineage_id
        assert record.spec.task == "compute"
        assert record.spec.label == "computer"
        assert record.spec.agent_type == "operator"
        assert "the answer is 42" in record.result_summary
        assert record.ended_at >= record.started_at

    @pytest.mark.asyncio
    async def test_failed_subagent_record_carries_the_error(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        sm.bus.publish_inbound = AsyncMock()

        await sm.spawn("compute", session_key="s1")
        await _drain_subagent_tasks(sm)

        record = sm.list_records("s1")[0]
        assert record.state == "failed"
        assert record.phase == "error"
        assert "LLM down" in (record.error or "")

    @pytest.mark.asyncio
    async def test_cancelled_subagent_is_recorded_as_cancelled(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()

        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("compute", session_key="s1")
        assert await sm.cancel_by_session("s1") == 1
        await _drain_subagent_tasks(sm)

        record = sm.list_records("s1")[0]
        assert record.state == "cancelled"

        block.set()

    @pytest.mark.asyncio
    async def test_retention_failure_does_not_break_the_subagent(self, tmp_path):
        """La persistenza del record non puo uccidere il subagent."""
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        sm.bus.publish_inbound = AsyncMock()
        sm._records.append = MagicMock(side_effect=OSError("read-only fs"))

        await sm.spawn("compute", session_key="s1")
        await _drain_subagent_tasks(sm)

        assert sm._running_tasks == {}
        sm.bus.publish_inbound.assert_awaited_once()


# ---------------------------------------------------------------------------
# _run_subagent
# ---------------------------------------------------------------------------


class TestRunSubagent:
    @pytest.mark.asyncio
    async def test_successful_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="Task done!", messages=[], stop_reason="completed",
        ))
        with patch.object(sm, "_announce_result", new_callable=AsyncMock) as mock_announce:
            await sm._run_subagent(
                "t1", _spec(),
                SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic()),
            )
            mock_announce.assert_called_once()
            assert mock_announce.call_args.args[-2] == "ok"

    @pytest.mark.asyncio
    async def test_tool_error_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content=None, messages=[], stop_reason="tool_error",
            tool_events=[{"name": "read_file", "status": "error", "detail": "not found"}],
        ))
        status = SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic())
        with patch.object(sm, "_announce_result", new_callable=AsyncMock) as mock_announce:
            await sm._run_subagent("t1", _spec(), status)
            assert mock_announce.call_args.args[-2] == "error"

    @pytest.mark.asyncio
    async def test_exception_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        status = SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic())
        with patch.object(sm, "_announce_result", new_callable=AsyncMock) as mock_announce:
            await sm._run_subagent("t1", _spec(), status)
            assert status.phase == "error"
            assert "LLM down" in status.error
            assert mock_announce.call_args.args[-2] == "error"

    @pytest.mark.asyncio
    async def test_status_updated_on_success(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="ok", messages=[], stop_reason="completed",
        ))
        status = SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic())
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm._run_subagent("t1", _spec(), status)
            assert status.phase == "done"
            assert status.stop_reason == "completed"


# ---------------------------------------------------------------------------
# _announce_result
# ---------------------------------------------------------------------------


class TestAnnounceResult:
    @pytest.mark.asyncio
    async def test_publishes_inbound_message(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result text",
            {"channel": "internal", "chat_id": "direct"}, "ok",
        )

        assert len(published) == 1
        msg = published[0]
        assert msg.channel == "system"
        assert msg.sender_id == "subagent"
        assert msg.metadata["injected_event"] == "subagent_result"
        assert msg.metadata["subagent_task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_session_key_override(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "websocket", "chat_id": "123", "session_key": "s1"}, "ok",
        )

        assert published[0].session_key_override == "s1"

    @pytest.mark.asyncio
    async def test_session_key_override_fallback(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "websocket", "chat_id": "123"}, "ok",
        )

        assert published[0].session_key_override == "websocket:123"

    @pytest.mark.asyncio
    async def test_ok_status_text(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "internal", "chat_id": "direct"}, "ok",
        )

        assert "completed successfully" in published[0].content

    @pytest.mark.asyncio
    async def test_error_status_text(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "error details",
            {"channel": "internal", "chat_id": "direct"}, "error",
        )

        assert "failed" in published[0].content

    @pytest.mark.asyncio
    async def test_origin_message_id_in_metadata(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "internal", "chat_id": "direct"}, "ok",
            origin_message_id="msg-123",
        )

        assert published[0].metadata["origin_message_id"] == "msg-123"


# ---------------------------------------------------------------------------
# _format_partial_progress
# ---------------------------------------------------------------------------


class TestFormatPartialProgress:
    def _make_result(self, tool_events=None, error=None):
        return MagicMock(tool_events=tool_events or [], error=error)

    def test_completed_only(self):
        result = self._make_result(tool_events=[
            {"name": "read_file", "status": "ok", "detail": "file content"},
            {"name": "python_exec", "status": "ok", "detail": "output"},
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "Completed steps:" in text
        assert "read_file" in text
        assert "python_exec" in text

    def test_failure_only(self):
        result = self._make_result(tool_events=[
            {"name": "read_file", "status": "error", "detail": "not found"},
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "Failure:" in text
        assert "not found" in text

    def test_completed_and_failure(self):
        result = self._make_result(tool_events=[
            {"name": "read_file", "status": "ok", "detail": "content"},
            {"name": "python_exec", "status": "error", "detail": "timeout"},
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "Completed steps:" in text
        assert "Failure:" in text

    def test_limited_to_last_three(self):
        result = self._make_result(tool_events=[
            {"name": f"tool_{i}", "status": "ok", "detail": f"result_{i}"}
            for i in range(5)
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "tool_2" in text
        assert "tool_3" in text
        assert "tool_4" in text
        assert "tool_0" not in text
        assert "tool_1" not in text

    def test_error_without_failure_event(self):
        result = self._make_result(
            tool_events=[{"name": "read_file", "status": "ok", "detail": "ok"}],
            error="Something went wrong",
        )
        text = SubagentManager._format_partial_progress(result)
        assert "Something went wrong" in text

    def test_empty_events_with_error(self):
        result = self._make_result(error="Total failure")
        text = SubagentManager._format_partial_progress(result)
        assert "Total failure" in text

    def test_empty_no_error_returns_fallback(self):
        result = self._make_result()
        text = SubagentManager._format_partial_progress(result)
        assert "Error" in text


# ---------------------------------------------------------------------------
# cancel_by_session
# ---------------------------------------------------------------------------


class TestCancelBySession:
    @pytest.mark.asyncio
    async def test_cancels_running_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task1", session_key="s1")
        await sm.spawn("task2", session_key="s1")
        assert len(sm._session_tasks.get("s1", set())) == 2

        count = await sm.cancel_by_session("s1")
        assert count == 2
        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_no_tasks_returns_zero(self, tmp_path):
        sm = _manager(tmp_path)
        count = await sm.cancel_by_session("nonexistent")
        assert count == 0

    @pytest.mark.asyncio
    async def test_already_done_not_counted(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("task1", session_key="s1")
        await _drain_subagent_tasks(sm)

        count = await sm.cancel_by_session("s1")
        assert count == 0


# ---------------------------------------------------------------------------
# get_running_count / get_running_count_by_session
# ---------------------------------------------------------------------------


class TestRunningCounts:
    @pytest.mark.asyncio
    async def test_running_count_zero(self, tmp_path):
        sm = _manager(tmp_path)
        assert sm.get_running_count() == 0

    @pytest.mark.asyncio
    async def test_running_count_tracks_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("t1", session_key="s1")
        await sm.spawn("t2", session_key="s1")
        assert sm.get_running_count() == 2
        assert sm.get_running_count_by_session("s1") == 2
        assert len(sm.get_running_ids_by_session("s1")) == 2
        assert sm.get_running_ids_by_session("altra") == frozenset()

        block.set()
        await _drain_subagent_tasks(sm)
        assert sm.get_running_count() == 0
        assert sm.get_running_ids_by_session("s1") == frozenset()

    @pytest.mark.asyncio
    async def test_running_count_by_session_nonexistent(self, tmp_path):
        sm = _manager(tmp_path)
        assert sm.get_running_count_by_session("nonexistent") == 0


# ---------------------------------------------------------------------------
# _SubagentHook
# ---------------------------------------------------------------------------


class TestSubagentHook:
    @pytest.mark.asyncio
    async def test_before_execute_tools_logs(self, tmp_path):
        hook = _SubagentHook("t1")
        tool_call = MagicMock()
        tool_call.name = "read_file"
        tool_call.arguments = {"path": "/tmp/test"}
        ctx = _make_hook_context(tool_calls=[tool_call])
        result = await hook.before_execute_tools(ctx)
        assert result is None
        assert ctx.tool_calls == [tool_call]

    @pytest.mark.asyncio
    async def test_after_iteration_updates_status(self):
        status = SubagentStatus(
            task_id="t1", label="test", task_description="do", started_at=time.monotonic(),
        )
        hook = _SubagentHook("t1", status)
        ctx = _make_hook_context(
            iteration=3,
            tool_events=[{"name": "read_file", "status": "ok", "detail": ""}],
            usage={"prompt_tokens": 100},
        )
        await hook.after_iteration(ctx)
        assert status.iteration == 3
        assert len(status.tool_events) == 1
        assert status.usage == {"prompt_tokens": 100}

    @pytest.mark.asyncio
    async def test_after_iteration_no_status_noop(self):
        hook = _SubagentHook("t1", status=None)
        ctx = _make_hook_context(iteration=5)
        result = await hook.after_iteration(ctx)
        assert result is None
        assert ctx.iteration == 5

    @pytest.mark.asyncio
    async def test_after_iteration_sets_error(self):
        status = SubagentStatus(
            task_id="t1", label="test", task_description="do", started_at=time.monotonic(),
        )
        hook = _SubagentHook("t1", status)
        ctx = _make_hook_context(error="something broke")
        await hook.after_iteration(ctx)
        assert status.error == "something broke"
