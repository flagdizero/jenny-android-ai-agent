"""Tests for the app_data_changed WebSocket push."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from port_alloc import free_port
from support.ws_channel import make_ws_channel

from jenny.bus.events import OutboundMessage
from jenny.channels.websocket import WebSocketChannel


def _ch(bus: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": free_port(),
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    return make_ws_channel(cfg, bus)


def _fake_connection() -> MagicMock:
    conn = MagicMock()
    conn.send = AsyncMock()
    return conn


class TestAppDataChanged:
    async def test_metadata_flag_broadcasts_event(self):
        channel = _ch(MagicMock())
        conn = _fake_connection()
        channel._conn_chats[conn] = {"webui:default"}

        await channel.send(OutboundMessage(
            channel="websocket", chat_id="webui:default", content="",
            metadata={"_app_data_changed": True, "app_slug": "note"},
        ))

        conn.send.assert_awaited_once()
        frame = json.loads(conn.send.await_args.args[0])
        assert frame == {"event": "app_data_changed", "slug": "note"}

    async def test_flag_without_slug_sends_nothing(self):
        channel = _ch(MagicMock())
        conn = _fake_connection()
        channel._conn_chats[conn] = {"webui:default"}

        await channel.send(OutboundMessage(
            channel="websocket", chat_id="webui:default", content="",
            metadata={"_app_data_changed": True},
        ))

        conn.send.assert_not_awaited()

    async def test_broadcast_reaches_all_connections(self):
        channel = _ch(MagicMock())
        conns = [_fake_connection() for _ in range(3)]
        for i, conn in enumerate(conns):
            channel._conn_chats[conn] = {f"chat{i}"}

        await channel.send_app_data_changed("piante")

        for conn in conns:
            conn.send.assert_awaited_once()


class TestBoardChanged:
    """«La bacheca di un quaderno è cambiata»: la bacheca aperta si ridisegna."""

    async def test_metadata_flag_broadcasts_event(self):
        channel = _ch(MagicMock())
        conns = [_fake_connection() for _ in range(2)]
        for i, conn in enumerate(conns):
            channel._conn_chats[conn] = {f"chat{i}"}

        await channel.send(OutboundMessage(
            channel="websocket", chat_id="webui", content="",
            metadata={"_board_changed": True, "notebook": "piante"},
        ))

        for conn in conns:
            frame = json.loads(conn.send.await_args.args[0])
            assert frame == {"event": "board_changed", "notebook": "piante"}

    async def test_flag_without_notebook_sends_nothing(self):
        channel = _ch(MagicMock())
        conn = _fake_connection()
        channel._conn_chats[conn] = {"webui:default"}

        await channel.send(OutboundMessage(
            channel="websocket", chat_id="webui", content="",
            metadata={"_board_changed": True},
        ))

        conn.send.assert_not_awaited()


class TestStagedChanged:
    """«Le bozze da approvare sono cambiate»: la scheda della casa si rilegge."""

    async def test_metadata_flag_broadcasts_event_with_count(self):
        channel = _ch(MagicMock())
        conns = [_fake_connection() for _ in range(2)]
        for i, conn in enumerate(conns):
            channel._conn_chats[conn] = {f"chat{i}"}

        await channel.send(OutboundMessage(
            channel="websocket", chat_id="webui", content="",
            metadata={"_staged_changed": True, "count": 2},
        ))

        for conn in conns:
            frame = json.loads(conn.send.await_args.args[0])
            assert frame == {"event": "staged_changed", "count": 2}
