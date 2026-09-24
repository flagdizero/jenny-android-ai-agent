"""Tests for WebSocketDispatcher routing of model reasoning content.

Reasoning is delivered through plugin streaming primitives
(``send_reasoning_delta`` / ``send_reasoning_end``) so each channel
controls in-place rendering — mirroring the existing answer ``send_delta``
/ ``stream_end`` pair. The manager forwards reasoning frames only to
channels that opt in via ``channel.show_reasoning``; plugins without a
low-emphasis UI primitive keep the base no-op and the content silently
drops at dispatch.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.aio import queue_idle, wait_until

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.websocket import WebSocketChannel
from jenny.config.schema import Config


def _mock_gateway() -> MagicMock:
    g = MagicMock()
    g.http = MagicMock()
    g.tokens = MagicMock()
    g.media = MagicMock()
    g.transcripts = MagicMock()
    g.workspaces = MagicMock()
    return g


class _MockChannel(WebSocketChannel):
    name = "mock"

    def __init__(self, config, bus):
        super().__init__(config, bus, gateway=_mock_gateway())
        self._send_mock = AsyncMock(return_value=[])
        self._delta_mock = AsyncMock(return_value=[])
        self._end_mock = AsyncMock(return_value=[])
        self._file_edit_mock = AsyncMock(return_value=[])

    async def start(self):  # pragma: no cover - not exercised
        pass

    async def stop(self):  # pragma: no cover - not exercised
        pass

    async def send(self, msg, *, only_conns=None, skip_persist=False):
        return await self._send_mock(msg)

    async def send_reasoning_delta(
        self, chat_id, delta, metadata=None, *, only_conns=None, skip_persist=False
    ):
        return await self._delta_mock(chat_id, delta, metadata)

    async def send_reasoning_end(
        self, chat_id, metadata=None, *, only_conns=None, skip_persist=False
    ):
        return await self._end_mock(chat_id, metadata)

    async def send_file_edit_events(
        self, chat_id, edits, metadata=None, *, only_conns=None, skip_persist=False
    ):
        return await self._file_edit_mock(chat_id, edits, metadata)


@pytest.fixture
def manager() -> WebSocketDispatcher:
    mgr = WebSocketDispatcher(Config(), MessageBus())
    mgr.channels["websocket"] = _MockChannel({}, mgr.bus)
    return mgr


def test_websocket_gateway_uses_configured_workspace_restriction(tmp_path):
    config = Config.model_validate(
        {
            "agents": {"defaults": {"workspace": str(tmp_path)}},
            "tools": {"restrictToWorkspace": True},
            "websocket": {
                "enabled": True,
                "websocketRequiresToken": False,
            },
        }
    )

    mgr = WebSocketDispatcher(config, MessageBus())
    channel = mgr.channels["websocket"]

    scope = channel.gateway.workspaces.default_scope()
    assert scope.restrict_to_workspace is True


@pytest.mark.asyncio
async def test_reasoning_delta_routes_to_send_reasoning_delta(manager):
    channel = manager.channels["websocket"]
    msg = OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="step-by-step",
        metadata={"_progress": True, "_reasoning_delta": True, "_stream_id": "r1"},
    )
    await manager._send_once(channel, msg)
    channel._delta_mock.assert_awaited_once()
    args = channel._delta_mock.await_args.args
    assert args[0] == "c1"
    assert args[1] == "step-by-step"
    channel._send_mock.assert_not_awaited()
    channel._end_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reasoning_end_routes_to_send_reasoning_end(manager):
    channel = manager.channels["websocket"]
    msg = OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="",
        metadata={"_progress": True, "_reasoning_end": True, "_stream_id": "r1"},
    )
    await manager._send_once(channel, msg)
    channel._end_mock.assert_awaited_once()
    channel._delta_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_drops_reasoning_when_channel_opts_out(manager):
    channel = manager.channels["websocket"]
    channel.show_reasoning = False
    msg = OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="hidden thinking",
        metadata={"_progress": True, "_reasoning_delta": True},
    )
    await manager.bus.publish_outbound(msg)

    await _pump_one(manager)

    channel._delta_mock.assert_not_awaited()
    channel._end_mock.assert_not_awaited()
    channel._send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_delivers_reasoning_when_channel_opts_in(manager):
    channel = manager.channels["websocket"]
    channel.show_reasoning = True
    for chunk in ("first ", "second"):
        await manager.bus.publish_outbound(OutboundMessage(
            channel="websocket",
            chat_id="c1",
            content=chunk,
            metadata={"_progress": True, "_reasoning_delta": True, "_stream_id": "r1"},
        ))
    await manager.bus.publish_outbound(OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="",
        metadata={"_progress": True, "_reasoning_end": True, "_stream_id": "r1"},
    ))

    await _pump_one(manager)

    assert channel._delta_mock.await_count == 2
    channel._end_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_silently_drops_reasoning_for_unknown_channel(manager):
    msg = OutboundMessage(
        channel="ghost",
        chat_id="c1",
        content="nobody home",
        metadata={"_progress": True, "_reasoning_delta": True},
    )
    await manager.bus.publish_outbound(msg)

    await _pump_one(manager)

    manager.channels["websocket"]._delta_mock.assert_not_awaited()
    manager.channels["websocket"]._send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_channel_reasoning_primitives_are_noop_safe():
    """Plugins that don't override the streaming primitives must not blow up."""

    class _Plain(WebSocketChannel):
        name = "plain"

        async def start(self):  # pragma: no cover
            pass

        async def stop(self):  # pragma: no cover
            pass

        async def send(self, msg):  # pragma: no cover
            pass

    channel = _Plain({}, MessageBus(), gateway=_mock_gateway())
    assert await channel.send_reasoning_delta("c", "x") == []
    assert await channel.send_reasoning_end("c") == []


@pytest.mark.asyncio
async def test_file_edit_events_route_to_channel_capability(manager):
    channel = manager.channels["websocket"]
    edits = [{"version": 1, "phase": "start", "path": "src/app.py"}]
    msg = OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="",
        metadata={"_progress": True, "_file_edit_events": edits},
    )

    await manager._send_once(channel, msg)

    channel._file_edit_mock.assert_awaited_once_with(
        "c1", edits, {"_progress": True, "_file_edit_events": edits}
    )
    channel._send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_channel_file_edit_events_are_noop_safe():
    class _Plain(WebSocketChannel):
        name = "plain"

        async def start(self):  # pragma: no cover
            pass

        async def stop(self):  # pragma: no cover
            pass

        async def send(self, msg):  # pragma: no cover
            raise AssertionError("file edit events should not call send")

    channel = _Plain({}, MessageBus(), gateway=_mock_gateway())
    assert await channel.send_file_edit_events("c", [{"path": "a.py"}]) == []


@pytest.mark.asyncio
async def test_reasoning_routing_does_not_consult_send_progress(manager):
    """`show_reasoning` is orthogonal to `send_progress` — turning off
    progress streaming must not silence reasoning."""
    channel = manager.channels["websocket"]
    channel.send_progress = False
    channel.show_reasoning = True
    await manager.bus.publish_outbound(OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="still surfaces",
        metadata={"_progress": True, "_reasoning_delta": True},
    ))

    await _pump_one(manager)

    channel._delta_mock.assert_awaited_once()


async def _pump_one(manager: WebSocketDispatcher) -> None:
    """Drive the dispatcher until it has handled the whole queue, then cancel.

    Il ciclo di prima usciva dopo 50 giri anche con la coda piena, e i test
    negativi di questo file («non è stato mandato niente») passavano senza che
    il dispatcher avesse mai lavorato; e «coda vuota» non vuol dire «ultimo
    messaggio lavorato». Qui si aspetta che torni fermo sulla coda, o si
    fallisce (v. ``support.aio.queue_idle``)."""
    task = asyncio.create_task(manager._dispatch_outbound())
    await wait_until(
        lambda: queue_idle(manager.bus.outbound),
        msg="il dispatcher non ha smaltito la coda",
    )
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
