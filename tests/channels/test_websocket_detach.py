"""Lasciare un quaderno lo lascia anche sul server (lato gateway).

Il client aveva ``detachChat`` ma il gateway conosceva solo ``attach``: la
connessione restava iscritta a ogni quaderno mai aperto e continuava a
riceverne i frame, che toccava al filtro sul ``chat_id`` di chi li consuma
scartare. Il frame ``{"type": "detach", "chat_id": "project:<nome>"}`` toglie
l'iscrizione e risponde ``{"event": "detached", "chat_id": ...}``. La chat
personale non si lascia: ogni connessione ci nasce iscritta e ci resta.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets
from port_alloc import free_port, wait_listening

from jenny.bus.events import OutboundMessage
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID


def _channel(port: int | None = None) -> WebSocketChannel:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port or free_port(),
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    gateway = build_gateway_services(
        config=WebSocketConfig.model_validate(cfg),
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


def _events(conn: AsyncMock) -> list[dict]:
    return [json.loads(call.args[0]) for call in conn.send.await_args_list]


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


async def test_detach_unsubscribes_the_connection_from_that_notebook() -> None:
    channel = _channel()
    conn = AsyncMock()
    channel._attach(conn, WEBUI_DEFAULT_CHAT_ID)
    await channel._dispatch_envelope(conn, "c", {"type": "attach", "chat_id": "project:demo"})
    assert conn in channel._subs["project:demo"]

    await channel._dispatch_envelope(conn, "c", {"type": "detach", "chat_id": "project:demo"})

    assert "project:demo" not in channel._subs
    assert channel._conn_chats[conn] == {WEBUI_DEFAULT_CHAT_ID}
    assert _events(conn)[-1] == {"event": "detached", "chat_id": "project:demo"}


async def test_detach_leaves_the_other_subscribers_alone() -> None:
    channel = _channel()
    a, b = AsyncMock(), AsyncMock()
    for conn in (a, b):
        await channel._dispatch_envelope(
            conn, "c", {"type": "attach", "chat_id": "project:demo"}
        )

    await channel._dispatch_envelope(a, "c", {"type": "detach", "chat_id": "project:demo"})

    assert channel._subs["project:demo"] == {b}


async def test_detach_is_idempotent() -> None:
    channel = _channel()
    conn = AsyncMock()
    for _ in range(2):
        await channel._dispatch_envelope(
            conn, "c", {"type": "detach", "chat_id": "project:demo"}
        )
    assert [e["event"] for e in _events(conn)] == ["detached", "detached"]
    assert "project:demo" not in channel._subs


@pytest.mark.parametrize("chat_id", [WEBUI_DEFAULT_CHAT_ID, None, "pippo", 42,
                                     "project:has space"])
async def test_the_personal_chat_is_never_detached(chat_id) -> None:
    """Né per nome né per una chiave che ci ricade (o che non è un progetto):
    niente iscrizione tolta, e niente frame d'errore, che il client mostrerebbe
    all'utente per una cosa che non ha chiesto."""
    channel = _channel()
    conn = AsyncMock()
    channel._attach(conn, WEBUI_DEFAULT_CHAT_ID)
    envelope: dict[str, Any] = {"type": "detach"}
    if chat_id is not None:
        envelope["chat_id"] = chat_id

    await channel._dispatch_envelope(conn, "c", envelope)

    assert conn in channel._subs[WEBUI_DEFAULT_CHAT_ID]
    assert _events(conn) == []


async def test_a_left_notebook_sends_no_more_frames_over_the_wire() -> None:
    port = free_port()
    channel = _channel(port)
    server = asyncio.create_task(channel.start())
    await wait_listening(channel)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=x") as client:
            assert json.loads(await client.recv())["event"] == "ready"
            await client.send(json.dumps({"type": "attach", "chat_id": "project:demo"}))
            assert json.loads(await client.recv())["event"] == "attached"
            await client.send(json.dumps({"type": "detach", "chat_id": "project:demo"}))
            assert json.loads(await client.recv()) == {
                "event": "detached", "chat_id": "project:demo",
            }

            await channel.send(OutboundMessage(
                channel="websocket", chat_id="project:demo", content="left behind",
            ))
            await channel.send(OutboundMessage(
                channel="websocket", chat_id=WEBUI_DEFAULT_CHAT_ID, content="still here",
            ))
            frame = json.loads(await asyncio.wait_for(client.recv(), timeout=2))
            assert frame.get("chat_id") == WEBUI_DEFAULT_CHAT_ID
            assert frame.get("text") == "still here"
    finally:
        await channel.stop()
        await server
