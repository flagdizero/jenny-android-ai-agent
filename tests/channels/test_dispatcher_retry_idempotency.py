"""Regression tests: `WebSocketDispatcher._send_with_retry` must be idempotent.

Bug context: `_send_with_retry` used to retry the *entire* `_send_once` call on
any non-`ConnectionClosed` exception. Because `WebSocketChannel.send()` /
`send_delta()` / `send_turn_end()` persist to the transcript *before* fanning
out to every subscribed connection, a partial multi-connection failure meant:

  * the transcript row got persisted a second time on retry, and
  * connections that already received the message on the first attempt got it
    again on retry (since the whole connection list was resent), and
  * for `send_delta`'s `stream_end` path specifically, the buffered stream
    text was popped (destructively consumed) on the first attempt, so a retry
    after partial failure finalized with truncated/empty text instead of the
    full streamed answer.

These tests exercise the real `WebSocketChannel` + `WebSocketDispatcher`
together (not mocks) so the fix is verified at the level the bug actually
occurred: multi-connection fan-out through `_send_with_retry`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from port_alloc import free_port

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels import dispatcher as dispatcher_mod
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.config.schema import Config
from jenny.webui.gateway_services import build_gateway_services
from jenny.webui.transcript import read_transcript_lines


def _make_channel(bus: Any) -> WebSocketChannel:
    cfg = WebSocketConfig.model_validate({
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": free_port(),
        "path": "/ws",
        "websocketRequiresToken": False,
    })
    gateway = build_gateway_services(
        config=cfg,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


class _FlakyConn:
    """A fake connection whose next N ``send`` calls raise, then it behaves."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.calls = 0
        self.fail_next = 0

    async def send(self, raw: str) -> None:
        self.calls += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("transient send failure")
        self.sent.append(raw)


@pytest.fixture(autouse=True)
def isolate_webui_workspace_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


@pytest.fixture
def dispatcher(monkeypatch) -> WebSocketDispatcher:
    # Don't actually sleep for the real (1, 2, 4)s backoff during retries.
    monkeypatch.setattr(dispatcher_mod, "_SEND_RETRY_DELAYS", (0, 0, 0))
    mgr = WebSocketDispatcher(Config(), MessageBus())
    mgr.channels["websocket"] = _make_channel(mgr.bus)
    return mgr


@pytest.mark.asyncio
async def test_two_connection_fanout_retry_does_not_duplicate_persist_or_resend(
    dispatcher: WebSocketDispatcher,
) -> None:
    """conn #2 fails once (conn #1 already delivered); the dispatcher retry
    must resend only to conn #2, and the transcript must gain exactly one row.
    """
    channel = dispatcher.channels["websocket"]
    conn1 = _FlakyConn()
    conn2 = _FlakyConn()
    channel._attach(conn1, "chat-1")
    channel._attach(conn2, "chat-1")
    conn2.fail_next = 1

    msg = OutboundMessage(channel="websocket", chat_id="chat-1", content="hello world")
    await dispatcher._send_with_retry(channel, msg)

    # conn1 already had the message on attempt 1 — must not be resent on retry.
    assert conn1.calls == 1
    assert len(conn1.sent) == 1
    # conn2 failed once, then received it on the targeted retry.
    assert conn2.calls == 2
    assert len(conn2.sent) == 1

    rows = read_transcript_lines("websocket:chat-1")
    message_rows = [
        r for r in rows if r.get("event") == "message" and r.get("text") == "hello world"
    ]
    assert len(message_rows) == 1, f"expected exactly one persisted row, got {message_rows}"


@pytest.mark.asyncio
async def test_turn_end_partial_failure_retries_only_failed_connection(
    dispatcher: WebSocketDispatcher,
) -> None:
    """Same idempotency contract for `send_turn_end`, reached via `channel.send()`."""
    channel = dispatcher.channels["websocket"]
    conn1 = _FlakyConn()
    conn2 = _FlakyConn()
    channel._attach(conn1, "chat-1")
    channel._attach(conn2, "chat-1")
    conn2.fail_next = 1

    msg = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={"_turn_end": True, "latency_ms": 42},
    )
    await dispatcher._send_with_retry(channel, msg)

    # What must not happen is either connection seeing the *turn_end* frame
    # more than once.
    conn1_turn_ends = [json.loads(r) for r in conn1.sent if json.loads(r).get("event") == "turn_end"]
    conn2_turn_ends = [json.loads(r) for r in conn2.sent if json.loads(r).get("event") == "turn_end"]
    assert len(conn1_turn_ends) == 1
    assert len(conn2_turn_ends) == 1

    rows = read_transcript_lines("websocket:chat-1")
    turn_end_rows = [r for r in rows if r.get("event") == "turn_end"]
    assert len(turn_end_rows) == 1


@pytest.mark.asyncio
async def test_send_delta_stream_end_buffer_survives_partial_failure() -> None:
    """Directly exercises the send_delta contract the dispatcher relies on:
    a failed stream_end delivery must leave the stream buffer intact (peeked,
    not popped) so a same-connections-only retry still finalizes with the
    complete streamed text instead of a truncated/empty one.
    """
    channel = _make_channel(MessageBus())
    conn_ok = _FlakyConn()
    conn_bad = _FlakyConn()
    channel._attach(conn_ok, "chat-1")
    channel._attach(conn_bad, "chat-1")

    meta = {"_stream_delta": True, "_stream_id": "s1"}
    for chunk in ("foo", "bar"):
        pending = await channel.send_delta("chat-1", chunk, meta)
        assert pending == []

    conn_bad.fail_next = 1
    end_meta = {"_stream_delta": True, "_stream_end": True, "_stream_id": "s1"}
    # The trailing chunk arrives bundled with stream_end, as the real runner does.
    pending = await channel.send_delta("chat-1", "!", end_meta)

    # Partial failure reported back; conn_ok already has the stream_end frame
    # (it also has the two earlier `delta` frames from the loop above).
    assert pending == [conn_bad]
    assert len(conn_ok.sent) == 3
    ok_body = json.loads(conn_ok.sent[-1])
    assert ok_body["event"] == "stream_end"
    assert ok_body["text"] == "foobar!"

    # The buffer must still hold the accumulated delta text — not popped by the
    # failed attempt. (The stream_end's own trailing "!" is reconstructed fresh
    # from `delta` on each attempt rather than mutating the shared buffer.)
    assert channel._stream_text_buffers[("chat-1", "s1")] == ["foo", "bar"]

    # Retry exactly like the dispatcher would: only the failed connection, no re-persist.
    pending2 = await channel.send_delta(
        "chat-1", "!", end_meta, only_conns=pending, skip_persist=True,
    )
    assert pending2 == []
    assert ("chat-1", "s1") not in channel._stream_text_buffers

    bad_body = json.loads(conn_bad.sent[-1])
    assert bad_body["text"] == "foobar!"  # not truncated/empty, and not duplicated ("foobar!!")

    rows = read_transcript_lines("websocket:chat-1")
    stream_end_rows = [r for r in rows if r.get("event") == "stream_end"]
    assert len(stream_end_rows) == 1
    assert stream_end_rows[0].get("text") == "foobar!"


@pytest.mark.asyncio
async def test_send_delta_stream_end_via_dispatcher_retry_delivers_full_text(
    dispatcher: WebSocketDispatcher,
) -> None:
    """End-to-end: stream deltas + a stream_end that partially fails, driven
    through the real `_send_with_retry` loop, must finalize with the full
    text and leave no stale buffer entry behind.
    """
    channel = dispatcher.channels["websocket"]
    conn1 = _FlakyConn()
    conn2 = _FlakyConn()
    channel._attach(conn1, "chat-1")
    channel._attach(conn2, "chat-1")

    meta = {"_stream_delta": True, "_stream_id": "s1"}
    for chunk in ("Hello", ", ", "world"):
        delta_msg = OutboundMessage(channel="websocket", chat_id="chat-1", content=chunk, metadata=meta)
        await dispatcher._send_with_retry(channel, delta_msg)

    conn2.fail_next = 1
    end_msg = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        # Trailing chunk bundled with stream_end, as the real runner does —
        # this forces the frame to carry `text` so truncation is observable.
        content="!",
        metadata={"_stream_delta": True, "_stream_end": True, "_stream_id": "s1"},
    )
    await dispatcher._send_with_retry(channel, end_msg)

    assert ("chat-1", "s1") not in channel._stream_text_buffers

    end_bodies = [json.loads(raw) for raw in conn2.sent if json.loads(raw).get("event") == "stream_end"]
    assert len(end_bodies) == 1
    assert end_bodies[0]["text"] == "Hello, world!"

    rows = read_transcript_lines("websocket:chat-1")
    stream_end_rows = [r for r in rows if r.get("event") == "stream_end"]
    assert len(stream_end_rows) == 1
    assert stream_end_rows[0].get("text") == "Hello, world!"
