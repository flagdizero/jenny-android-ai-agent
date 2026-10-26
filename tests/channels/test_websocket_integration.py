"""Integration tests for the WebSocket channel using WsTestClient.

Complements the unit/lightweight tests in test_websocket_channel.py by covering
multi-client scenarios, edge cases, and realistic usage patterns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets
from port_alloc import free_port, wait_listening
from ws_test_client import WsTestClient

from jenny.bus.events import OutboundMessage
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services


def _ch(bus: Any, port: int, **kw: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port,
        "path": "/",
        "websocketRequiresToken": False,
    }
    cfg.update(kw)
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


# -- Connection basics ----------------------------------------------------


@pytest.mark.asyncio
async def test_ready_event_fields(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="c1") as c:
            r = await c.recv_ready()
            assert r.event == "ready"
            assert r.chat_id == "default"
            assert r.client_id == "c1"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_anonymous_client_gets_generated_id(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="") as c:
            r = await c.recv_ready()
            assert r.client_id.startswith("anon-")
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_each_connection_shares_unified_chat_id(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="a") as c1:
            async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="b") as c2:
                r1, r2 = await c1.recv_ready(), await c2.recv_ready()
                assert r1.chat_id == r2.chat_id == "default"
    finally:
        await ch.stop()
        await t


# -- Inbound messages (client -> server) ----------------------------------


@pytest.mark.asyncio
async def test_plain_text(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="p") as c:
            await c.recv_ready()
            await c.send_text("hello world")
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.content == "hello world"
            assert inbound.sender_id == "p"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_json_content_field(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="j") as c:
            await c.recv_ready()
            await c.send_json({"content": "structured"})
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].content == "structured"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_json_text_and_message_fields(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="x") as c:
            await c.recv_ready()
            await c.send_json({"text": "via text"})
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].content == "via text"
            await c.send_json({"message": "via message"})
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].content == "via message"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_empty_payload_ignored(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="e") as c:
            await c.recv_ready()
            await c.send_text("   ")
            await c.send_json({})
            await asyncio.sleep(0.1)
            bus.publish_inbound.assert_not_awaited()
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_messages_preserve_order(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="o") as c:
            await c.recv_ready()
            for i in range(5):
                await c.send_text(f"msg-{i}")
            await asyncio.sleep(0.2)
            contents = [call[0][0].content for call in bus.publish_inbound.call_args_list]
            assert contents == [f"msg-{i}" for i in range(5)]
    finally:
        await ch.stop()
        await t


# -- Outbound messages (server -> client) ---------------------------------


@pytest.mark.asyncio
async def test_server_send_message(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="r") as c:
            ready = await c.recv_ready()
            await ch.send(OutboundMessage(
                channel="websocket", chat_id=ready.chat_id, content="reply",
            ))
            msg = await c.recv_message()
            assert msg.text == "reply"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_server_send_tags_tool_hint_with_kind(bus: MagicMock) -> None:
    """``_tool_hint`` metadata must surface as ``kind: "tool_hint"`` so WS
    clients render breadcrumbs separately from conversational replies."""
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="h") as c:
            ready = await c.recv_ready()
            # Plain reply: no "kind" field.
            await ch.send(OutboundMessage(
                channel="websocket", chat_id=ready.chat_id, content="hi",
            ))
            plain = await c.recv_message()
            assert plain.raw.get("kind") is None

            # Tool-hint breadcrumb: kind == "tool_hint".
            await ch.send(OutboundMessage(
                channel="websocket", chat_id=ready.chat_id,
                content='weather("get")',
                metadata={"_progress": True, "_tool_hint": True},
            ))
            hint = await c.recv_message()
            assert hint.raw.get("kind") == "tool_hint"
            assert hint.text == 'weather("get")'

            # Generic progress (non-tool-hint) gets the softer "progress" label.
            await ch.send(OutboundMessage(
                channel="websocket", chat_id=ready.chat_id,
                content="thinking…",
                metadata={"_progress": True},
            ))
            prog = await c.recv_message()
            assert prog.raw.get("kind") == "progress"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_server_send_with_media_and_reply(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="m") as c:
            ready = await c.recv_ready()
            await ch.send(OutboundMessage(
                channel="websocket", chat_id=ready.chat_id, content="img",
                media=["/tmp/a.png"],
            ))
            msg = await c.recv_message()
            assert msg.text == "img"
            assert msg.media == ["/tmp/a.png"]
    finally:
        await ch.stop()
        await t


# -- Streaming ------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_deltas_and_end(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port, streaming=True)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="s") as c:
            cid = (await c.recv_ready()).chat_id
            for part in ("Hello", " ", "world", "!"):
                await ch.send_delta(cid, part, {"_stream_delta": True, "_stream_id": "s1"})
            await ch.send_delta(cid, "", {"_stream_end": True, "_stream_id": "s1"})

            msgs = await c.collect_stream()
            deltas = [m for m in msgs if m.event == "delta"]
            assert "".join(d.text for d in deltas) == "Hello world!"
            ends = [m for m in msgs if m.event == "stream_end"]
            assert len(ends) == 1
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_interleaved_streams(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port, streaming=True)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="i") as c:
            cid = (await c.recv_ready()).chat_id
            await ch.send_delta(cid, "A1", {"_stream_delta": True, "_stream_id": "sa"})
            await ch.send_delta(cid, "B1", {"_stream_delta": True, "_stream_id": "sb"})
            await ch.send_delta(cid, "A2", {"_stream_delta": True, "_stream_id": "sa"})
            await ch.send_delta(cid, "", {"_stream_end": True, "_stream_id": "sa"})
            await ch.send_delta(cid, "B2", {"_stream_delta": True, "_stream_id": "sb"})
            await ch.send_delta(cid, "", {"_stream_end": True, "_stream_id": "sb"})

            msgs = await c.recv_n(6)
            sa = "".join(m.text for m in msgs if m.event == "delta" and m.stream_id == "sa")
            sb = "".join(m.text for m in msgs if m.event == "delta" and m.stream_id == "sb")
            assert sa == "A1A2"
            assert sb == "B1B2"
    finally:
        await ch.stop()
        await t


# -- Multi-client ---------------------------------------------------------


@pytest.mark.asyncio
async def test_unified_chat_fans_out_to_all_clients(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="u1") as c1:
            async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="u2") as c2:
                await c1.recv_ready()
                await c2.recv_ready()
                await ch.send(OutboundMessage(
                    channel="websocket", chat_id="default", content="for-all",
                ))
                assert (await c1.recv_message()).text == "for-all"
                assert (await c2.recv_message()).text == "for-all"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_disconnected_client_cleanup(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="tmp") as c:
            chat_id = (await c.recv_ready()).chat_id
        # disconnected
        await asyncio.sleep(0.1)
        await ch.send(OutboundMessage(
            channel="websocket", chat_id=chat_id, content="orphan",
        ))
        assert chat_id not in ch._subs
    finally:
        await ch.stop()
        await t


# -- Authentication -------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_accepted(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port, tokenIssueSecret="secret", websocketRequiresToken=True)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="a", token="secret") as c:
            assert (await c.recv_ready()).client_id == "a"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_secret_rejected(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port, tokenIssueSecret="correct", websocketRequiresToken=True)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
            async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="b", token="wrong"):
                pass
        assert exc.value.response.status_code == 401
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_secret_required_for_websocket(bus: MagicMock) -> None:
    port = free_port()
    port2 = free_port()
    secret = "s"
    ch = _ch(bus, port, path="/ws",
             tokenIssueSecret=secret,
             websocketRequiresToken=True)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        # no token -> 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
            async with WsTestClient(f"ws://127.0.0.1:{port}/ws", client_id="x"):
                pass
        assert exc.value.response.status_code == 401

        # valid secret -> ok
        async with WsTestClient(f"ws://127.0.0.1:{port}/ws", client_id="ok", token=secret) as c:
            assert (await c.recv_ready()).client_id == "ok"

        # reconnect with the SAME secret (simulating the client replaying it
        # after a dropped connection) -> still ok
        async with WsTestClient(f"ws://127.0.0.1:{port}/ws", client_id="r", token=secret) as c:
            assert (await c.recv_ready()).client_id == "r"

        # wrong secret -> 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
            async with WsTestClient(
                f"ws://127.0.0.1:{port}/ws", client_id="n", token="wrong-secret"
            ):
                pass
        assert exc.value.response.status_code == 401

        # no secret configured on a channel that does not require auth -> ok
        open_ch = _ch(bus, port2, path="/ws", websocketRequiresToken=False)
        open_t = asyncio.create_task(open_ch.start())
        await wait_listening(open_ch)
        try:
            async with WsTestClient(f"ws://127.0.0.1:{port2}/ws", client_id="open") as c:
                assert (await c.recv_ready()).client_id == "open"
        finally:
            await open_ch.stop()
            await open_t
    finally:
        await ch.stop()
        await t


# -- Path routing ---------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_path(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port, path="/my-chat")
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/my-chat", client_id="p") as c:
            assert (await c.recv_ready()).event == "ready"
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_wrong_path_serves_webui(bus: MagicMock) -> None:
    """Non-WebSocket paths serve the WebUI HTML instead of returning 404."""
    port = free_port()
    ch = _ch(bus, port, path="/ws")
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
            async with WsTestClient(f"ws://127.0.0.1:{port}/wrong", client_id="x"):
                pass
        assert exc.value.response.status_code == 200
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_trailing_slash_normalized(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port, path="/ws")
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/ws/", client_id="s") as c:
            assert (await c.recv_ready()).event == "ready"
    finally:
        await ch.stop()
        await t


# -- Edge cases -----------------------------------------------------------


@pytest.mark.asyncio
async def test_large_message(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="big") as c:
            await c.recv_ready()
            big = "x" * 100_000
            await c.send_text(big)
            await asyncio.sleep(0.2)
            assert bus.publish_inbound.call_args[0][0].content == big
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_unicode_roundtrip(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="u") as c:
            ready = await c.recv_ready()
            text = "你好世界 🌍 日本語テスト"
            await c.send_text(text)
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].content == text
            await ch.send(OutboundMessage(
                channel="websocket", chat_id=ready.chat_id, content=text,
            ))
            assert (await c.recv_message()).text == text
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_rapid_fire(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="r") as c:
            ready = await c.recv_ready()
            for i in range(50):
                await c.send_text(f"in-{i}")
            await asyncio.sleep(0.5)
            assert bus.publish_inbound.await_count == 50
            for i in range(50):
                await ch.send(OutboundMessage(
                    channel="websocket", chat_id=ready.chat_id, content=f"out-{i}",
                ))
            received = [(await c.recv_message()).text for _ in range(50)]
            assert received == [f"out-{i}" for i in range(50)]
    finally:
        await ch.stop()
        await t


@pytest.mark.asyncio
async def test_invalid_json_as_plain_text(bus: MagicMock) -> None:
    port = free_port()
    ch = _ch(bus, port)
    t = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with WsTestClient(f"ws://127.0.0.1:{port}/", client_id="j") as c:
            await c.recv_ready()
            await c.send_text("{broken json")
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].content == "{broken json"
    finally:
        await ch.stop()
        await t
