"""Test message tool suppress logic for final replies."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.agent import make_loop

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.message import MessageTool
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> AgentLoop:
    return make_loop(tmp_path, bare=True, context_window_tokens=None)


class TestMessageToolSuppressLogic:
    """Final reply suppressed only when message tool sends to the same target."""

    @pytest.mark.asyncio
    async def test_suppress_when_sent_to_same_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            arguments={"content": "Hello", "channel": "websocket", "chat_id": "chat123"},
        )
        calls = iter([
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="Done", tool_calls=[]),
        ])
        loop.provider.chat_with_retry = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(channel="websocket", sender_id="user1", chat_id="chat123", content="Send")
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert result.message is None  # suppressed

    @pytest.mark.asyncio
    async def test_not_suppress_when_sent_to_different_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            arguments={
                "content": "Cross-channel content",
                "channel": "other-channel",
                "chat_id": "chat999",
            },
        )
        calls = iter([
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="I've sent the message.", tool_calls=[]),
        ])
        loop.provider.chat_with_retry = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(
            channel="websocket", sender_id="user1", chat_id="chat123", content="Send elsewhere"
        )
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert sent[0].channel == "other-channel"
        assert result.message is not None  # not suppressed
        assert result.message.channel == "websocket"

    @pytest.mark.asyncio
    async def test_not_suppress_when_no_message_tool_used(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Hello!", tool_calls=[]))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(channel="websocket", sender_id="user1", chat_id="chat123", content="Hi")
        result = await loop._process_message(msg)

        assert result.message is not None
        assert "Hello" in result.text

    @pytest.mark.asyncio
    async def test_injected_followup_with_message_tool_does_not_emit_empty_fallback(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            arguments={"content": "Tool reply", "channel": "websocket", "chat_id": "chat123"},
        )
        calls = iter([
            LLMResponse(content="First answer", tool_calls=[]),
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="", tool_calls=[]),
            LLMResponse(content="", tool_calls=[]),
            LLMResponse(content="", tool_calls=[]),
        ])
        loop.provider.chat_with_retry = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        pending_queue = asyncio.Queue()
        await pending_queue.put(
            InboundMessage(channel="websocket", sender_id="user1", chat_id="chat123", content="follow-up")
        )

        msg = InboundMessage(channel="websocket", sender_id="user1", chat_id="chat123", content="Start")
        result = await loop._process_message(msg, pending_queue=pending_queue)

        assert len(sent) == 1
        assert sent[0].content == "Tool reply"
        assert result.message is None

    async def test_progress_hides_internal_reasoning(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(id="call1", name="read_file", arguments={"path": "foo.txt"})
        calls = iter([
            LLMResponse(
                content="Visible<think>hidden</think>",
                tool_calls=[tool_call],
                reasoning_content="secret reasoning",
                thinking_blocks=[{"signature": "sig", "thought": "secret thought"}],
            ),
            LLMResponse(content="Done", tool_calls=[]),
        ])
        loop.provider.chat_with_retry = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.tools.execute = AsyncMock(return_value="ok")

        progress: list[tuple[str, bool]] = []

        async def on_progress(content: str, *, tool_hint: bool = False) -> None:
            progress.append((content, tool_hint))

        final_content, _, _, _, _ = await loop._run_agent_loop([], on_progress=on_progress)

        assert final_content == "Done"
        assert progress == [
            ("Visible", False),
            ('read foo.txt', True),
        ]

class TestMessageToolTurnTracking:

    def test_sent_in_turn_tracks_same_target(self) -> None:
        tool = MessageTool()
        from jenny.agent.tools.context import RequestContext
        tool.set_context(RequestContext(channel="websocket", chat_id="chat1"))
        assert not tool._sent_in_turn
        tool._sent_in_turn = True
        assert tool._sent_in_turn

    def test_start_turn_resets(self) -> None:
        tool = MessageTool()
        tool._sent_in_turn = True
        tool.start_turn()
        assert not tool._sent_in_turn

    async def test_flag_written_in_child_task_is_visible_to_the_turn(self) -> None:
        """Il flag deve attraversare il confine di task, non solo il context.

        I tool girano dentro ``asyncio.wait_for``/``gather`` (tool_execution),
        che li avvolge in un Task con una *copia* del context: un flag tenuto
        come valore della ContextVar verrebbe perso al ritorno e la risposta
        finale non sarebbe soppressa (utente = messaggio doppio).
        """
        tool = MessageTool()
        tool.start_turn()

        async def child() -> None:
            tool._sent_in_turn = True

        await asyncio.wait_for(child(), timeout=5)

        assert tool._sent_in_turn

    async def test_concurrent_turns_do_not_share_the_flag(self) -> None:
        """L'isolamento fra turni concorrenti resta: un dict per turno."""
        tool = MessageTool()
        started = asyncio.Event()

        async def turn_that_sends() -> bool:
            tool.start_turn()
            tool._sent_in_turn = True
            started.set()
            await asyncio.sleep(0)
            return tool._sent_in_turn

        async def turn_that_does_not() -> bool:
            await started.wait()
            tool.start_turn()
            await asyncio.sleep(0)
            return tool._sent_in_turn

        sent, quiet = await asyncio.gather(turn_that_sends(), turn_that_does_not())

        assert sent is True
        assert quiet is False

    def test_schema_discourages_current_chat_replies(self) -> None:
        tool = MessageTool()

        assert "Do not use this for the normal reply in the current chat" in tool.description
        assert (
            "Do not use this for a normal reply in the current chat"
            in tool.parameters["properties"]["content"]["description"]
        )
