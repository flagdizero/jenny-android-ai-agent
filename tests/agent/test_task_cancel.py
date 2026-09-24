"""Tests for /stop task cancellation."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jenny.config.schema import AgentDefaults
from jenny.session.keys import UNIFIED_SESSION_KEY

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _sub_spec(task: str = "do task", label: str = "label", **kw):
    """Spec minimale per invocare ``_run_subagent`` direttamente."""
    from jenny.agent.subagent import SubagentSpec

    defaults = dict(origin_channel="test", origin_chat_id="c1")
    defaults.update(kw)
    return SubagentSpec(task=task, label=label, **defaults)


def _make_loop(*, tools_config=None):
    """Create a minimal AgentLoop with mocked dependencies."""
    from jenny.agent.loop import AgentLoop
    from jenny.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with patch("jenny.agent.loop.ContextBuilder"), \
         patch("jenny.agent.loop.SessionManager"), \
         patch("jenny.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace, tools_config=tools_config)
    return loop, bus


class TestHandleStop:
    @pytest.mark.asyncio
    async def test_stop_no_active_task(self):
        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)
        assert "No active task" in out.content

    @pytest.mark.asyncio
    async def test_stop_cancels_active_task(self):
        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext

        loop, bus = _make_loop()
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks[UNIFIED_SESSION_KEY] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert cancelled.is_set()
        assert "stopped" in out.content.lower()

    @pytest.mark.asyncio
    async def test_stop_cancels_multiple_tasks(self):
        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext

        loop, bus = _make_loop()
        events = [asyncio.Event(), asyncio.Event()]

        async def slow(idx):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events[idx].set()
                raise

        tasks = [asyncio.create_task(slow(i)) for i in range(2)]
        await asyncio.sleep(0)
        loop._active_tasks[UNIFIED_SESSION_KEY] = tasks

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert all(e.is_set() for e in events)
        assert "2 task" in out.content


class TestStopClearsStuckGoalState:
    """A ``/stop`` on a cancelled goal turn must not permanently disable the LLM
    wall-clock timeout for the session (see jenny.session.goal_state)."""

    def _make_loop_with_real_sessions(self, tmp_path):
        from jenny.agent.loop import AgentLoop
        from jenny.bus.queue import MessageBus
        from jenny.session.manager import SessionManager

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        workspace = MagicMock()
        workspace.__truediv__ = MagicMock(return_value=MagicMock())
        sessions = SessionManager(tmp_path)

        with patch("jenny.agent.loop.ContextBuilder"), \
             patch("jenny.agent.loop.SubagentManager") as mock_sub_mgr:
            mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
            loop = AgentLoop(
                bus=bus, provider=provider, workspace=workspace, session_manager=sessions,
            )
        return loop, bus

    @pytest.mark.asyncio
    async def test_stop_resets_stuck_active_goal_and_reenables_timeout(self, tmp_path):
        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext
        from jenny.session.goal_state import (
            GOAL_STATE_KEY,
            runner_wall_llm_timeout_s,
            sustained_goal_active,
        )

        loop, bus = self._make_loop_with_real_sessions(tmp_path)
        key = "test:c1"

        # Simulate the real long_task code path: a sustained goal is active on the session.
        session = loop.sessions.get_or_create(key)
        session.metadata[GOAL_STATE_KEY] = {
            "status": "active",
            "objective": "do the thing forever",
            "started_at": "2026-01-01T00:00:00",
        }
        loop.sessions.save(session)

        # Sanity: while the goal is active, the wall-clock timeout is disabled.
        assert sustained_goal_active(loop.sessions.get_or_create(key).metadata) is True
        assert runner_wall_llm_timeout_s(loop.sessions, key) == 0.0

        # Simulate a hung turn the user cancels via /stop.
        hung = asyncio.Event()

        async def hung_turn():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                hung.set()
                raise

        task = asyncio.create_task(hung_turn())
        await asyncio.sleep(0)
        loop._active_tasks[key] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=key, raw="/stop", loop=loop)
        await cmd_stop(ctx)

        assert hung.is_set()

        # The goal must no longer read as "active" for any later, unrelated turn on
        # this session, and the LLM wall-clock timeout must be re-enabled.
        reloaded = loop.sessions.get_or_create(key)
        assert sustained_goal_active(reloaded.metadata) is False
        assert reloaded.metadata[GOAL_STATE_KEY]["status"] == "cancelled"
        assert runner_wall_llm_timeout_s(loop.sessions, key) is None

    @pytest.mark.asyncio
    async def test_stop_says_out_loud_that_it_cancelled_a_goal(self, tmp_path, caplog):
        """La risposta di /stop conta solo task: il goal cancellato va nel log.

        Senza questa riga non c'era modo — né in chat né in logcat — di sapere se
        un obiettivo fosse ancora vivo dopo uno /stop.
        """
        import logging

        from loguru import logger as loguru_logger

        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext
        from jenny.session.goal_state import GOAL_STATE_KEY

        loop, _bus = self._make_loop_with_real_sessions(tmp_path)
        key = "test:c1"
        session = loop.sessions.get_or_create(key)
        session.metadata[GOAL_STATE_KEY] = {
            "status": "active",
            "objective": "do the thing forever",
            "ui_summary": "the thing",
            "started_at": "2026-01-01T00:00:00",
        }
        loop.sessions.save(session)

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=key, raw="/stop", loop=loop)
        handler_id = loguru_logger.add(caplog.handler, format="{message}", level="INFO")
        try:
            with caplog.at_level(logging.INFO):
                await cmd_stop(ctx)
        finally:
            loguru_logger.remove(handler_id)

        assert "Sustained goal cancelled by hard-stop" in caplog.text
        assert "the thing" in caplog.text

    @pytest.mark.asyncio
    async def test_stop_leaves_non_goal_session_untouched(self, tmp_path):
        """No active goal: /stop must not fabricate goal_state metadata."""
        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext
        from jenny.session.goal_state import GOAL_STATE_KEY

        loop, bus = self._make_loop_with_real_sessions(tmp_path)
        key = "test:c1"

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert "No active task" in out.content
        assert GOAL_STATE_KEY not in loop.sessions.get_or_create(key).metadata


class TestDispatch:
    def test_exec_tool_not_registered_when_disabled(self):
        from jenny.agent.tools.python_exec import PythonExecConfig
        from jenny.config.schema import ToolsConfig

        loop, _bus = _make_loop(tools_config=ToolsConfig(python_exec=PythonExecConfig(enable=False)))

        assert loop.tools.get("python_exec") is None

    @pytest.mark.asyncio
    async def test_dispatch_processes_and_publishes(self):
        from jenny.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        from jenny.agent.turn_types import TurnOutcome

        loop._process_message = AsyncMock(
            return_value=TurnOutcome.delivered(
                OutboundMessage(channel="test", chat_id="c1", content="hi")
            )
        )
        await loop._dispatch(msg)
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert out.content == "hi"

    @pytest.mark.asyncio
    async def test_dispatch_streaming_preserves_message_metadata(self):
        from jenny.bus.events import InboundMessage

        loop, bus = _make_loop()
        msg = InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="room-1",
            content="hello",
            metadata={
                "_wants_stream": True,
                "thread_root_event_id": "$root1",
                "thread_reply_to_event_id": "$reply1",
            },
        )

        async def fake_process(_msg, *, on_stream=None, on_stream_end=None, **kwargs):
            assert on_stream is not None
            assert on_stream_end is not None
            await on_stream("hi")
            await on_stream_end(resuming=False)
            return None

        loop._process_message = fake_process

        await loop._dispatch(msg)
        first = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        second = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert first.metadata["thread_root_event_id"] == "$root1"
        assert first.metadata["thread_reply_to_event_id"] == "$reply1"
        assert first.metadata["_stream_delta"] is True
        assert second.metadata["thread_root_event_id"] == "$root1"
        assert second.metadata["thread_reply_to_event_id"] == "$reply1"
        assert second.metadata["_stream_end"] is True

    @pytest.mark.asyncio
    async def test_processing_lock_serializes(self):
        from jenny.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        order = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def mock_process(m, **kwargs):
            order.append(f"start-{m.content}")
            if m.content == "a":
                first_started.set()
                await release_first.wait()
            order.append(f"end-{m.content}")
            return OutboundMessage(channel="test", chat_id="c1", content=m.content)

        loop._process_message = mock_process
        msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="b")

        t1 = asyncio.create_task(loop._dispatch(msg1))
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        t2 = asyncio.create_task(loop._dispatch(msg2))
        await asyncio.sleep(0)
        assert order == ["start-a"]

        release_first.set()
        await asyncio.gather(t1, t2)
        assert order == ["start-a", "end-a", "start-b", "end-b"]


class TestSubagentCancellation:
    @pytest.mark.asyncio
    async def test_cancel_by_session(self):
        from jenny.agent.subagent import SubagentManager
        from jenny.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )

        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0)
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        count = await mgr.cancel_by_session("test:c1")
        assert count == 1
        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_cancel_by_session_no_tasks(self):
        from jenny.agent.subagent import SubagentManager
        from jenny.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        assert await mgr.cancel_by_session("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_subagent_preserves_reasoning_fields_in_tool_turn(self, monkeypatch, tmp_path):
        from jenny.agent.subagent import SubagentManager
        from jenny.bus.queue import MessageBus
        from jenny.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        captured_second_call: list[dict] = []

        call_count = {"n": 0}

        async def scripted_chat_with_retry(*, messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return LLMResponse(
                    content="thinking",
                    tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                    reasoning_content="hidden reasoning",
                    thinking_blocks=[{"type": "thinking", "thinking": "step"}],
                )
            captured_second_call[:] = messages
            return LLMResponse(content="done", tool_calls=[])

        async def _scripted(**kwargs):
            return await scripted_chat_with_retry(**kwargs)

        # Entrambi i path: un subagent chiede lo streaming, quindi il runner passa
        # da ``chat_stream_with_retry``.
        from support.subagent_provider_fakes import script_provider
        script_provider(provider, _scripted)
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )

        async def fake_execute(self, **kwargs):
            return "tool result"

        monkeypatch.setattr("jenny.agent.tools.filesystem.ListDirTool.execute", fake_execute)

        from jenny.agent.subagent import SubagentStatus
        status = SubagentStatus(task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic())
        await mgr._run_subagent("sub-1", _sub_spec(), status)

        assistant_messages = [
            msg for msg in captured_second_call
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["reasoning_content"] == "hidden reasoning"
        assert assistant_messages[0]["thinking_blocks"] == [{"type": "thinking", "thinking": "step"}]

    @pytest.mark.asyncio
    async def test_subagent_exec_tool_not_registered_when_disabled(self, tmp_path):
        from jenny.agent.subagent import SubagentManager
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
            tools_config=ToolsConfig(python_exec=PythonExecConfig(enable=False)),
        )
        mgr._announce_result = AsyncMock()

        async def fake_run(spec):
            assert spec.tools.get("python_exec") is None
            return SimpleNamespace(
                stop_reason="done",
                final_content="done",
                error=None,
                tool_events=[],
            )

        mgr.runner.run = AsyncMock(side_effect=fake_run)

        from jenny.agent.subagent import SubagentStatus
        status = SubagentStatus(task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic())
        await mgr._run_subagent("sub-1", _sub_spec(), status)

        mgr.runner.run.assert_awaited_once()
        mgr._announce_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subagent_announces_error_when_tool_execution_fails(self, monkeypatch, tmp_path):
        from jenny.agent.subagent import SubagentManager
        from jenny.bus.queue import MessageBus
        from jenny.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        # Entrambi i path: un subagent chiede lo streaming.
        from support.subagent_provider_fakes import script_provider
        script_provider(provider, [LLMResponse(
            content="thinking",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        )])
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        mgr._announce_result = AsyncMock()

        calls = {"n": 0}

        async def fake_execute(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "first result"
            raise RuntimeError("boom")

        monkeypatch.setattr("jenny.agent.tools.filesystem.ListDirTool.execute", fake_execute)

        from jenny.agent.subagent import SubagentStatus
        status = SubagentStatus(task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic())
        await mgr._run_subagent("sub-1", _sub_spec(), status)

        mgr._announce_result.assert_awaited_once()
        args = mgr._announce_result.await_args.args
        assert "Completed steps:" in args[3]
        assert "- list_dir: first result" in args[3]
        assert "Failure:" in args[3]
        assert "- list_dir: boom" in args[3]
        assert args[5] == "error"

    @pytest.mark.asyncio
    async def test_cancel_by_session_cancels_running_subagent_tool(self, monkeypatch, tmp_path):
        from jenny.agent.subagent import SubagentManager, SubagentStatus
        from jenny.bus.queue import MessageBus
        from jenny.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        # Entrambi i path: un subagent chiede lo streaming.
        from support.subagent_provider_fakes import script_provider
        script_provider(provider, [LLMResponse(
            content="thinking",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        )])
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        mgr._announce_result = AsyncMock()

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_execute(self, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("jenny.agent.tools.filesystem.ListDirTool.execute", fake_execute)

        task = asyncio.create_task(
            mgr._run_subagent(
                "sub-1", _sub_spec(),
                SubagentStatus(task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()),
            )
        )
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        await asyncio.wait_for(started.wait(), timeout=1.0)

        count = await mgr.cancel_by_session("test:c1")

        assert count == 1
        assert cancelled.is_set()
        assert task.cancelled()
        mgr._announce_result.assert_not_awaited()


class TestSubagentAnnounceSessionKey:
    """Verify _announce_result uses the effective session key for mid-turn routing."""

    def _make_mgr(self):
        """Create a SubagentManager with mocked deps and its bus."""
        from jenny.agent.subagent import SubagentManager
        from jenny.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        return mgr, bus

    @pytest.mark.asyncio
    async def test_announce_uses_effective_key_in_unified_mode(self):
        """In unified session mode, session_key_override must be 'unified:default'
        so the result matches the pending queue key."""
        mgr, bus = self._make_mgr()

        origin = {"channel": "websocket", "chat_id": "111", "session_key": UNIFIED_SESSION_KEY}
        await mgr._announce_result("sub-1", "label", "task", "result", origin, "ok")

        msg = await bus.consume_inbound()
        assert msg.session_key_override == UNIFIED_SESSION_KEY
        assert msg.session_key == UNIFIED_SESSION_KEY

    @pytest.mark.asyncio
    async def test_announce_uses_raw_key_in_normal_mode(self):
        """Without unified sessions, session_key_override is the raw channel:chat_id."""
        mgr, bus = self._make_mgr()

        origin = {"channel": "websocket", "chat_id": "222", "session_key": "websocket:222"}
        await mgr._announce_result("sub-2", "label", "task", "result", origin, "ok")

        msg = await bus.consume_inbound()
        assert msg.session_key_override == "websocket:222"
        assert msg.session_key == "websocket:222"

    @pytest.mark.asyncio
    async def test_announce_falls_back_to_origin_when_no_session_key(self):
        """When session_key is None, fallback to f'{channel}:{chat_id}'."""
        mgr, bus = self._make_mgr()

        origin = {"channel": "websocket", "chat_id": "333", "session_key": None}
        await mgr._announce_result("sub-3", "label", "task", "result", origin, "ok")

        msg = await bus.consume_inbound()
        assert msg.session_key_override == "websocket:333"
        assert msg.channel == "system"
        assert msg.chat_id == "websocket:333"

    @pytest.mark.asyncio
    async def test_session_key_flows_through_run_subagent(self):
        """Verify session_key in origin propagates from _run_subagent to _announce_result."""
        from jenny.agent.subagent import SubagentStatus

        mgr, bus = self._make_mgr()

        async def fake_run(spec):
            return SimpleNamespace(
                stop_reason="done",
                final_content="done",
                error=None,
                tool_events=[],
            )

        mgr.runner.run = AsyncMock(side_effect=fake_run)

        status = SubagentStatus(
            task_id="sub-4", label="label", task_description="task",
            started_at=time.monotonic(),
        )
        await mgr._run_subagent(
            "sub-4",
            _sub_spec(
                task="task",
                origin_channel="websocket",
                origin_chat_id="444",
                session_key=UNIFIED_SESSION_KEY,
            ),
            status,
        )

        msg = await bus.consume_inbound()
        assert msg.session_key_override == UNIFIED_SESSION_KEY


class TestStopAbandonsStuckTasks:
    """/stop bounded: cancel → grace → abbandono sicuro via epoch di turno."""

    def _stubborn_task(self):
        """Task che ingoia la cancellazione (simula un thread non interrompibile)."""
        release = asyncio.Event()

        async def stubborn():
            while not release.is_set():
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    # Ignora la cancel: come un run_in_executor bloccato.
                    continue

        task = asyncio.create_task(stubborn())
        return task, release

    @pytest.mark.asyncio
    async def test_stop_abandons_stuck_task_within_grace(self):
        loop, bus = _make_loop()
        task, release = self._stubborn_task()
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        t0 = time.monotonic()
        total = await loop._cancel_active_tasks("test:c1", grace_s=0.05)
        elapsed = time.monotonic() - t0

        assert total == 1
        assert elapsed < 1.0, "lo stop deve essere bounded, non attendere lo zombie"
        assert not task.done(), "lo zombie è stato abbandonato, non atteso"
        release.set()
        task.cancel()

    @pytest.mark.asyncio
    async def test_stop_bumps_epoch_and_repudiates_zombie(self):
        loop, bus = _make_loop()
        token = loop._turn_epochs.issue("test:c1")
        task, release = self._stubborn_task()
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        await loop._cancel_active_tasks("test:c1", grace_s=0.05)

        assert loop._turn_epochs.is_current(token) is False
        release.set()
        task.cancel()

    @pytest.mark.asyncio
    async def test_stop_rotates_lock_when_abandoned(self):
        loop, bus = _make_loop()
        old_lock = loop._session_locks.get("test:c1")
        holder_started = asyncio.Event()
        release = asyncio.Event()

        async def hold_lock_stubbornly():
            async with old_lock:
                holder_started.set()
                while not release.is_set():
                    try:
                        await asyncio.sleep(60)
                    except asyncio.CancelledError:
                        continue

        task = asyncio.create_task(hold_lock_stubbornly())
        await holder_started.wait()
        loop._active_tasks["test:c1"] = [task]

        await loop._cancel_active_tasks("test:c1", grace_s=0.05)

        new_lock = loop._session_locks.get("test:c1")
        assert new_lock is not old_lock, "la lock orfana doveva essere ruotata"
        assert not new_lock.locked(), "il turno successivo acquisisce subito"
        release.set()
        task.cancel()

    @pytest.mark.asyncio
    async def test_stop_keeps_lock_when_cancel_lands(self):
        loop, bus = _make_loop()
        old_lock = loop._session_locks.get("test:c1")

        async def cooperative():
            await asyncio.sleep(60)

        task = asyncio.create_task(cooperative())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        await loop._cancel_active_tasks("test:c1", grace_s=1.0)
        assert loop._session_locks.get("test:c1") is old_lock

    @pytest.mark.asyncio
    async def test_stop_drains_zombie_pending_queue(self):
        from jenny.bus.events import InboundMessage

        loop, bus = _make_loop()
        loop.bus.publish_inbound = AsyncMock()
        task, release = self._stubborn_task()
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]
        queued = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="follow-up")
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(queued)
        loop._pending_queues["test:c1"] = q

        await loop._cancel_active_tasks("test:c1", grace_s=0.05)

        assert "test:c1" not in loop._pending_queues
        loop.bus.publish_inbound.assert_awaited_once_with(queued)
        release.set()
        task.cancel()

    @pytest.mark.asyncio
    async def test_cancel_excludes_own_task_and_readopts_token(self):
        """/new dentro il proprio turno: il task del comando non si auto-cancella
        e il suo token viene ri-adottato al nuovo epoch."""
        loop, bus = _make_loop()
        result: dict = {}

        async def own_turn():
            current = asyncio.current_task()
            token = loop._turn_epochs.issue("test:c1")
            loop._turn_tokens_by_task[current] = token
            loop._active_tasks["test:c1"] = [current]
            await loop._cancel_active_tasks("test:c1", grace_s=0.05)
            result["cancelled"] = current.cancelling() > 0
            result["token_current"] = loop._turn_epochs.is_current(token)

        await asyncio.create_task(own_turn())
        assert result["cancelled"] is False
        # Nessun altro task da cancellare -> nessun bump -> token resta valido.
        assert result["token_current"] is True

    @pytest.mark.asyncio
    async def test_stop_restores_checkpoint_and_emits_turn_end(self):
        """cmd_stop materializza il checkpoint e chiude il turno verso la UI
        (turn_completed + idle) al posto dello zombie ripudiato."""
        from jenny.bus.events import InboundMessage
        from jenny.command.builtin import cmd_stop
        from jenny.command.router import CommandContext

        loop, bus = _make_loop()
        session = SimpleNamespace(
            key=UNIFIED_SESSION_KEY,
            metadata={
                "runtime_checkpoint": {
                    "phase": "awaiting_tools",
                    "iteration": 0,
                    "assistant_message": {
                        "role": "assistant",
                        "content": "Working...",
                        "tool_calls": [{"id": "tc_1", "type": "function",
                                        "function": {"name": "grep", "arguments": "{}"}}],
                    },
                    "completed_tool_results": [
                        {"role": "tool", "tool_call_id": "tc_1", "content": "hit"},
                    ],
                    "pending_tool_calls": [],
                }
            },
            messages=[{"role": "user", "content": "do work"}],
        )
        loop.sessions.get_or_create = MagicMock(return_value=session)
        loop.sessions.save = MagicMock()
        loop.runtime_event_publisher = MagicMock(
            turn_completed=AsyncMock(),
            run_status_changed=AsyncMock(),
            clear_turn=MagicMock(),
        )

        task, release = self._stubborn_task()
        await asyncio.sleep(0)
        loop._active_tasks[UNIFIED_SESSION_KEY] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert "1 task" in out.content
        roles = [m.get("role") for m in session.messages]
        assert roles == ["user", "assistant", "tool"]
        assert "runtime_checkpoint" not in session.metadata
        loop.runtime_event_publisher.turn_completed.assert_awaited_once()
        loop.runtime_event_publisher.run_status_changed.assert_awaited_once_with(
            msg, UNIFIED_SESSION_KEY, "idle"
        )
        release.set()
        task.cancel()

    @pytest.mark.asyncio
    async def test_cancel_by_session_abandons_stuck_subagent_and_suppresses_announce(self):
        from jenny.agent.subagent import SubagentManager
        from jenny.bus.queue import MessageBus

        bus = MessageBus()
        bus.publish_inbound = AsyncMock()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=MagicMock(),
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )

        release = asyncio.Event()

        async def stubborn():
            while not release.is_set():
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    continue

        task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        mgr._running_tasks["sub-z"] = task
        mgr._session_tasks["test:c1"] = {"sub-z"}

        count = await mgr.cancel_by_session("test:c1", grace_s=0.05)
        assert count == 1
        assert "sub-z" in mgr._repudiated_task_ids

        # L'announce tardivo dello zombie viene soppresso, non inietta turni.
        await mgr._announce_result(
            "sub-z", "label", "task", "stale result",
            {"channel": "test", "chat_id": "c1"}, "ok",
        )
        bus.publish_inbound.assert_not_awaited()
        assert "sub-z" not in mgr._repudiated_task_ids
        release.set()
        task.cancel()
