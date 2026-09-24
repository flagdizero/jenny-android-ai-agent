"""Tests for /stop preserving partial context from interrupted turns.

When /stop cancels an active task, the runtime checkpoint (tool results,
assistant messages accumulated so far) should be materialized into session
history rather than silently discarded.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from support.agent import make_loop, make_provider

from jenny.agent.loop import AgentLoop


def _make_loop(tmp_path: Path) -> AgentLoop:
    """Create a real AgentLoop with mocked provider — avoids patching __init__."""
    return make_loop(
        tmp_path, provider=make_provider(spec=False), model=None,
        context_window_tokens=None, patch_deps=True,
    )


class TestStopPreservesContext:
    """Verify that /stop restores partial context via checkpoint."""

    def test_restore_checkpoint_method_exists(self, tmp_path):
        """AgentLoop should have _restore_runtime_checkpoint."""
        loop = _make_loop(tmp_path)
        assert hasattr(loop, "_restore_runtime_checkpoint")

    def test_checkpoint_key_constant(self, tmp_path):
        """The runtime checkpoint key should be defined."""
        loop = _make_loop(tmp_path)
        assert loop._RUNTIME_CHECKPOINT_KEY == "runtime_checkpoint"

    def test_cancel_dispatch_restores_checkpoint(self, tmp_path):
        """When a task is cancelled, the checkpoint should be restored."""
        loop = _make_loop(tmp_path)
        session = MagicMock()
        session.metadata = {
            "runtime_checkpoint": {
                "phase": "awaiting_tools",
                "iteration": 0,
                "assistant_message": {
                    "role": "assistant",
                    "content": "Let me search for that.",
                    "tool_calls": [{"id": "tc_1", "type": "function",
                                    "function": {"name": "web_search", "arguments": "{}"}}],
                },
                "completed_tool_results": [
                    {"role": "tool", "tool_call_id": "tc_1",
                     "content": "Search results: ..."},
                ],
                "pending_tool_calls": [],
            }
        }
        session.messages = [
            {"role": "user", "content": "Search for something"},
        ]
        loop.sessions.get_or_create.return_value = session

        restored = loop._restore_runtime_checkpoint(session)
        assert restored is True
        assert len(session.messages) > 1
        assert "runtime_checkpoint" not in session.metadata


@pytest.mark.asyncio
async def test_dispatch_cancellation_restores_checkpoint():
    """Regression for #2966: /stop interrupting _dispatch must materialize the
    in-flight runtime checkpoint into session.messages before the cancellation
    unwinds, so the next turn can see the partial work.

    This exercises the real _dispatch path (locks, pending queues, the
    CancelledError handler) rather than poking _restore_runtime_checkpoint in
    isolation, so a future refactor that drops the cancel-time restore is
    caught by CI instead of silently regressing.
    """
    from jenny.bus.events import InboundMessage
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
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)

    checkpoint_key = loop._RUNTIME_CHECKPOINT_KEY
    session = SimpleNamespace(
        key="unified:default",
        metadata={
            checkpoint_key: {
                "phase": "awaiting_tools",
                "iteration": 0,
                "assistant_message": {
                    "role": "assistant",
                    "content": "Let me search.",
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": "{}"},
                        }
                    ],
                },
                "completed_tool_results": [
                    {"role": "tool", "tool_call_id": "tc_1", "content": "Search hit."},
                ],
                "pending_tool_calls": [],
            }
        },
        messages=[{"role": "user", "content": "Search for something"}],
    )

    loop.sessions.get_or_create = MagicMock(return_value=session)
    loop.sessions.save = MagicMock()

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    loop._process_message = _cancel

    msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="work")

    with pytest.raises(asyncio.CancelledError):
        await loop._dispatch(msg)

    roles = [m.get("role") for m in session.messages]
    assert roles == ["user", "assistant", "tool"], (
        "Expected the assistant message and completed tool result from the "
        f"interrupted turn to be materialized into session.messages; got {roles}"
    )
    assert checkpoint_key not in session.metadata, \
        "Checkpoint metadata should be cleared after restore"
    assert loop.sessions.save.called, \
        "Session should be persisted so the restored state survives process restart"


@pytest.mark.asyncio
async def test_dispatch_cancellation_after_bump_skips_restore():
    """Un turno RIPUDIATO (epoch bumpato da /stop) NON deve ripristinare il
    checkpoint dal proprio handler CancelledError: lo fa /stop in modo
    sincrono, e l'handler dello zombie può girare molto più tardi."""
    from jenny.bus.events import InboundMessage
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
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)

    session = SimpleNamespace(
        key="unified:default",
        metadata={
            loop._RUNTIME_CHECKPOINT_KEY: {
                "phase": "awaiting_tools",
                "iteration": 0,
                "assistant_message": {"role": "assistant", "content": "wip"},
                "completed_tool_results": [],
                "pending_tool_calls": [],
            }
        },
        messages=[{"role": "user", "content": "work"}],
    )
    loop.sessions.get_or_create = MagicMock(return_value=session)
    loop.sessions.save = MagicMock()

    async def _bump_then_cancel(*_args, **_kwargs):
        loop._turn_epochs.bump("unified:default")
        raise asyncio.CancelledError()

    loop._process_message = _bump_then_cancel
    msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="work")

    with pytest.raises(asyncio.CancelledError):
        await loop._dispatch(msg)

    assert [m.get("role") for m in session.messages] == ["user"], \
        "il turno ripudiato non deve materializzare il checkpoint"
    assert loop._RUNTIME_CHECKPOINT_KEY in session.metadata, \
        "il checkpoint resta per il restore sincrono di /stop"
    assert not loop.sessions.save.called
