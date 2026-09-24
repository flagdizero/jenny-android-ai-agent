"""Tests for unified-session routing.

Covers:
- AgentLoop._dispatch() rewrites every session_key to "unified:default"
- Existing session_key_override is respected (not overwritten) — the contract
  internal keys (dream:/cron:/heartbeat) rely on
- /new command correctly clears the shared session
- /new is NOT a priority command (goes through _dispatch, key rewrite applies)
- Context window consolidation is unaffected by the key rewrite
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.agent import make_loop

from jenny.agent.loop import AgentLoop
from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command.builtin import cmd_new, register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.schema import Config
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import Session, SessionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loop(tmp_path: Path) -> AgentLoop:
    """Create a minimal AgentLoop for dispatch-level tests."""
    return make_loop(
        tmp_path, bare=True, model=None, context_window_tokens=None,
        patches=("jenny.agent.loop.SessionManager", "jenny.agent.loop.SubagentManager"),
    )


def _make_msg(channel: str = "websocket", chat_id: str = "111",
              session_key_override: str | None = None) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        chat_id=chat_id,
        sender_id="user1",
        content="hello",
        session_key_override=session_key_override,
    )


# ---------------------------------------------------------------------------
# TestUnifiedSessionDispatch — core behaviour
# ---------------------------------------------------------------------------

class TestUnifiedSessionDispatch:
    """AgentLoop._dispatch() session key rewriting logic."""

    @pytest.mark.asyncio
    async def test_unified_session_rewrites_key_to_unified_default(self, tmp_path: Path):
        """All messages use 'unified:default' as session key."""
        loop = _make_loop(tmp_path)

        captured: list[str] = []

        async def fake_process(msg, **kwargs):
            captured.append(msg.session_key)
            return None

        loop._process_message = fake_process  # type: ignore[method-assign]

        msg = _make_msg(channel="websocket", chat_id="111")
        await loop._dispatch(msg)

        assert captured == ["unified:default"]

    @pytest.mark.asyncio
    async def test_unified_session_different_channels_share_same_key(self, tmp_path: Path):
        """Messages from different channels all resolve to the same session key."""
        loop = _make_loop(tmp_path)

        captured: list[str] = []

        async def fake_process(msg, **kwargs):
            captured.append(msg.session_key)
            return None

        loop._process_message = fake_process  # type: ignore[method-assign]

        await loop._dispatch(_make_msg(channel="test-channel", chat_id="111"))
        await loop._dispatch(_make_msg(channel="other-channel", chat_id="222"))
        await loop._dispatch(_make_msg(channel="internal", chat_id="direct"))

        assert captured == ["unified:default", "unified:default", "unified:default"]

    @pytest.mark.asyncio
    async def test_unified_session_respects_existing_override(self, tmp_path: Path):
        """If session_key_override is already set (e.g. a channel thread), it is NOT overwritten."""
        loop = _make_loop(tmp_path)

        captured: list[str] = []

        async def fake_process(msg, **kwargs):
            captured.append(msg.session_key)
            return None

        loop._process_message = fake_process  # type: ignore[method-assign]

        msg = _make_msg(
            channel="websocket", chat_id="111", session_key_override="websocket:thread:42"
        )
        await loop._dispatch(msg)

        assert captured == ["websocket:thread:42"]


# ---------------------------------------------------------------------------
# Legacy config compatibility
# ---------------------------------------------------------------------------

class TestLegacyUnifiedSessionConfigKey:
    """Old config.json files still carry 'unifiedSession'; loading must not fail."""

    def test_config_ignores_legacy_unified_session_key(self):
        raw = {"agents": {"defaults": {"unifiedSession": True}}}
        config = Config.model_validate(raw)
        assert not hasattr(config.agents.defaults, "unified_session")


# ---------------------------------------------------------------------------
# TestCmdNewUnifiedSession — /new command behaviour in unified mode
# ---------------------------------------------------------------------------

class TestCmdNewUnifiedSession:
    """/new command routing and session-clear behaviour in unified mode."""

    def test_new_is_not_a_priority_command(self):
        """/new must NOT be in the priority table — it must go through _dispatch()
        so the unified session key rewrite applies before cmd_new runs."""
        router = CommandRouter()
        register_builtin_commands(router)
        assert router.is_priority("/new") is False

    def test_new_is_an_exact_command(self):
        """/new must be registered as an exact command."""
        router = CommandRouter()
        register_builtin_commands(router)
        assert "/new" in router._exact

    @pytest.mark.asyncio
    async def test_cmd_new_clears_unified_session(self, tmp_path: Path):
        """cmd_new called with key='unified:default' clears the shared session."""
        sessions = SessionManager(tmp_path)

        # Pre-populate the shared session with some messages
        shared = sessions.get_or_create("unified:default")
        shared.add_message("user", "hello from websocket")
        shared.add_message("assistant", "hi there")
        sessions.save(shared)
        assert len(sessions.get_or_create("unified:default").messages) == 2

        # _schedule_background is a *sync* method that schedules a coroutine via
        # asyncio.create_task().  Mirror that exactly so the coroutine is consumed
        # and no RuntimeWarning is emitted.
        forgotten: list[str] = []
        loop = SimpleNamespace(
            sessions=sessions,
            consolidator=SimpleNamespace(archive=AsyncMock(return_value=True)),
            _cancel_active_tasks=AsyncMock(return_value=0),
            # `/new` segna il pavimento del diario nei metadata della sessione:
            # da quel cursore in giu' il blocco `# Recent History` non entra piu'
            # nel prompt di questa conversazione (issue #11).
            context=SimpleNamespace(memory=MemoryStore(tmp_path)),
            # Svuotare la conversazione svuota anche il dedup delle letture: il
            # contenuto dei file stava nei messaggi che non ci sono piu'.
            forget_file_reads=forgotten.append,
        )
        loop._schedule_background = lambda coro: asyncio.ensure_future(coro)

        msg = InboundMessage(
            channel="websocket", sender_id="user1", chat_id="111", content="/new",
            session_key_override="unified:default",  # as _dispatch() would set it
        )
        ctx = CommandContext(msg=msg, session=None, key="unified:default", raw="/new", loop=loop)

        result = await cmd_new(ctx)

        assert "New session started" in result.content
        # Invalidate cache and reload from disk to confirm persistence
        sessions.invalidate("unified:default")
        reloaded = sessions.get_or_create("unified:default")
        assert reloaded.messages == []
        # E il dedup delle letture e' stato dimenticato con loro: senza, la prima
        # lettura della sessione nuova torna «invariato dall'ultima lettura» a una
        # conversazione che non ha mai letto niente (visto sul telefono il 23/08).
        assert forgotten == ["unified:default"]

    @pytest.mark.asyncio
    async def test_cmd_new_in_unified_mode_does_not_affect_other_sessions(self, tmp_path: Path):
        """Clearing unified:default must not touch other sessions on disk."""
        sessions = SessionManager(tmp_path)

        other = sessions.get_or_create("websocket:999")
        other.add_message("user", "other session message")
        sessions.save(other)

        shared = sessions.get_or_create("unified:default")
        shared.add_message("user", "shared message")
        sessions.save(shared)

        forgotten: list[str] = []
        loop = SimpleNamespace(
            sessions=sessions,
            consolidator=SimpleNamespace(archive=AsyncMock(return_value=True)),
            _cancel_active_tasks=AsyncMock(return_value=0),
            # `/new` segna il pavimento del diario nei metadata della sessione:
            # da quel cursore in giu' il blocco `# Recent History` non entra piu'
            # nel prompt di questa conversazione (issue #11).
            context=SimpleNamespace(memory=MemoryStore(tmp_path)),
            # Svuotare la conversazione svuota anche il dedup delle letture: il
            # contenuto dei file stava nei messaggi che non ci sono piu'.
            forget_file_reads=forgotten.append,
        )
        loop._schedule_background = lambda coro: asyncio.ensure_future(coro)

        msg = InboundMessage(
            channel="websocket", sender_id="user1", chat_id="111", content="/new",
            session_key_override="unified:default",
        )
        ctx = CommandContext(msg=msg, session=None, key="unified:default", raw="/new", loop=loop)
        await cmd_new(ctx)

        sessions.invalidate("unified:default")
        sessions.invalidate("websocket:999")
        assert sessions.get_or_create("unified:default").messages == []
        assert len(sessions.get_or_create("websocket:999").messages) == 1


# ---------------------------------------------------------------------------
# TestConsolidationUnaffectedByUnifiedSession — consolidation is key-agnostic
# ---------------------------------------------------------------------------

class TestConsolidationUnaffectedByUnifiedSession:
    """maybe_consolidate_by_tokens() behaviour is identical regardless of session key."""

    @pytest.mark.asyncio
    async def test_consolidation_skips_empty_session_for_unified_key(self):
        """Empty unified:default session → consolidation exits immediately, archive not called."""
        from jenny.agent.memory import Consolidator, MemoryStore

        store = MagicMock(spec=MemoryStore)
        mock_provider = MagicMock()
        mock_provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="summary"))
        # Use spec= so MagicMock doesn't auto-generate AsyncMock for non-async methods,
        # which would leave unawaited coroutines and trigger RuntimeWarning.
        sessions = MagicMock(spec=SessionManager)

        consolidator = Consolidator(
            store=store,
            provider=mock_provider,
            model="test-model",
            sessions=sessions,
            context_window_tokens=1000,
            build_messages=MagicMock(return_value=[]),
            get_tool_definitions=MagicMock(return_value=[]),
            max_completion_tokens=100,
        )
        consolidator.archive = AsyncMock()

        session = Session(key="unified:default")
        session.messages = []

        await consolidator.maybe_consolidate_by_tokens(session)

        consolidator.archive.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidation_behaviour_identical_for_any_key(self):
        """archive call count is the same for 'websocket:123' and 'unified:default'
        under identical token conditions."""
        from jenny.agent.memory import Consolidator, MemoryStore

        archive_calls: dict[str, int] = {}

        for key in ("websocket:123", "unified:default"):
            store = MagicMock(spec=MemoryStore)
            mock_provider = MagicMock()
            mock_provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="summary"))
            sessions = MagicMock(spec=SessionManager)

            consolidator = Consolidator(
                store=store,
                provider=mock_provider,
                model="test-model",
                sessions=sessions,
                context_window_tokens=1000,
                build_messages=MagicMock(return_value=[]),
                get_tool_definitions=MagicMock(return_value=[]),
                max_completion_tokens=100,
            )

            session = Session(key=key)
            session.messages = []  # empty → exits immediately for both keys

            consolidator.archive = AsyncMock()
            await consolidator.maybe_consolidate_by_tokens(session)
            archive_calls[key] = consolidator.archive.call_count

        assert archive_calls["websocket:123"] == archive_calls["unified:default"] == 0

    @pytest.mark.asyncio
    async def test_consolidation_triggers_when_over_budget_unified_key(self):
        """When tokens exceed budget, consolidation attempts to find a boundary —
        behaviour is identical to any other session key."""
        from jenny.agent.memory import Consolidator, MemoryStore

        store = MagicMock(spec=MemoryStore)
        mock_provider = MagicMock()
        sessions = MagicMock(spec=SessionManager)

        consolidator = Consolidator(
            store=store,
            provider=mock_provider,
            model="test-model",
            sessions=sessions,
            context_window_tokens=1000,
            build_messages=MagicMock(return_value=[]),
            get_tool_definitions=MagicMock(return_value=[]),
            max_completion_tokens=100,
        )

        session = Session(key="unified:default")
        session.messages = [{"role": "user", "content": "msg"}]
        sessions.get_or_create.return_value = session

        # Simulate over-budget: estimated > budget
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(950, "tiktoken"))
        # No valid boundary found → returns gracefully without archiving
        consolidator.pick_consolidation_boundary = MagicMock(return_value=None)
        consolidator.archive = AsyncMock()

        await consolidator.maybe_consolidate_by_tokens(session)

        # estimate was called (consolidation was attempted)
        consolidator.estimate_session_prompt_tokens.assert_called_once_with(
            session,
        )
        # but archive was not called (no valid boundary)
        consolidator.archive.assert_not_called()


# ---------------------------------------------------------------------------
# TestStopCommandWithUnifiedSession — /stop command integration
# ---------------------------------------------------------------------------


class TestStopCommandWithUnifiedSession:
    """Verify /stop command works correctly with unified session enabled."""

    @pytest.mark.asyncio
    async def test_active_tasks_use_effective_key_in_unified_mode(self, tmp_path: Path):
        """Tasks are stored under UNIFIED_SESSION_KEY."""
        loop = _make_loop(tmp_path)

        # Create a message from the websocket channel
        msg = _make_msg(channel="websocket", chat_id="123456")

        # Mock _dispatch to complete immediately
        async def fake_dispatch(m):
            pass

        loop._dispatch = fake_dispatch  # type: ignore[method-assign]

        # Simulate the task creation flow (from _run loop)
        effective_key = loop._effective_session_key(msg)
        task = asyncio.create_task(loop._dispatch(msg))
        loop._active_tasks.setdefault(effective_key, []).append(task)

        # Wait for task to complete
        await task

        # Verify the task is stored under UNIFIED_SESSION_KEY, not the original channel:chat_id
        assert UNIFIED_SESSION_KEY in loop._active_tasks
        assert "websocket:123456" not in loop._active_tasks

    @pytest.mark.asyncio
    async def test_stop_command_finds_task_in_unified_mode(self, tmp_path: Path):
        """cmd_stop can cancel tasks stored under the unified key."""
        from jenny.command.builtin import cmd_stop

        loop = _make_loop(tmp_path)

        # Create a long-running task stored under UNIFIED_SESSION_KEY
        async def long_running():
            await asyncio.sleep(10)  # Will be cancelled

        task = asyncio.create_task(long_running())
        loop._active_tasks[UNIFIED_SESSION_KEY] = [task]

        # Create a message that would have session_key=UNIFIED_SESSION_KEY after dispatch
        msg = InboundMessage(
            channel="websocket",
            chat_id="123456",
            sender_id="user1",
            content="/stop",
            session_key_override=UNIFIED_SESSION_KEY,  # Simulate post-dispatch state
        )

        ctx = CommandContext(msg=msg, session=None, key=UNIFIED_SESSION_KEY, raw="/stop", loop=loop)

        # Execute /stop
        result = await cmd_stop(ctx)

        # Verify task was cancelled
        assert task.cancelled() or task.done()
        assert "Stopped 1 task" in result.content

    @pytest.mark.asyncio
    async def test_stop_command_uses_effective_key_without_session_override(self, tmp_path: Path):
        """Priority /stop must cancel the unified session even before dispatch rewrites the message."""
        from jenny.command.builtin import cmd_stop

        loop = _make_loop(tmp_path)

        async def long_running():
            await asyncio.sleep(10)

        task = asyncio.create_task(long_running())
        loop._active_tasks[UNIFIED_SESSION_KEY] = [task]
        msg = InboundMessage(
            channel="websocket",
            chat_id="123456",
            sender_id="user1",
            content="/stop",
        )
        ctx = CommandContext(msg=msg, session=None, key=UNIFIED_SESSION_KEY, raw="/stop", loop=loop)

        result = await cmd_stop(ctx)

        assert task.cancelled() or task.done()
        assert "Stopped 1 task" in result.content

    @pytest.mark.asyncio
    async def test_stop_command_cross_channel_in_unified_mode(self, tmp_path: Path):
        """In unified mode, /stop from one channel cancels tasks from another channel."""
        from jenny.command.builtin import cmd_stop

        loop = _make_loop(tmp_path)

        # Create tasks from different channels, all stored under UNIFIED_SESSION_KEY
        async def long_running():
            await asyncio.sleep(10)

        task1 = asyncio.create_task(long_running())
        task2 = asyncio.create_task(long_running())
        loop._active_tasks[UNIFIED_SESSION_KEY] = [task1, task2]

        # /stop from one channel should cancel tasks started from another channel
        msg = InboundMessage(
            channel="other-channel",
            chat_id="789012",
            sender_id="user2",
            content="/stop",
            session_key_override=UNIFIED_SESSION_KEY,
        )

        ctx = CommandContext(msg=msg, session=None, key=UNIFIED_SESSION_KEY, raw="/stop", loop=loop)

        result = await cmd_stop(ctx)

        # Both tasks should be cancelled
        assert "Stopped 2 task" in result.content
