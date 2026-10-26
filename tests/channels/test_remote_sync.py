"""Il telefono resta allineato con le chat fatte da un altro dispositivo.

Misurato il 14/10/2026 su jenny_s22 + Mac via Tailscale: il transcript era gia'
completo, ma dal vivo il telefono riceveva le risposte di Jenny senza le
domande scritte dal Mac, la casa non rileggeva tornando visibile, e ogni
risposta faceva suonare il telefono. Qui le tre regole:

- un messaggio scritto in una finestra arriva subito alle altre (evento
  ``user``), una volta sola nel transcript, con la provenienza se e' da fuori;
- un turno partito da fuori non fa suonare il telefono, ne' streammato ne' no;
- il filo ha una revisione che la lettura, ``turn_end`` e l'eco riportano.
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
from jenny.channels import ws_remote
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.channels.ws_remote import RemoteListener
from jenny.webui.gateway_services import build_gateway_services
from jenny.webui.transcript_replay import replay_transcript_to_ui_messages
from jenny.webui.transcript_store import (
    append_transcript_object,
    read_transcript_lines,
    transcript_revision,
)


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    return tmp_path


def _channel(bus: Any, port: int) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port,
        "path": "/",
        "tokenIssueSecret": "master-secret",
        "websocketRequiresToken": True,
    }
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(parsed, bus, gateway=gateway)


async def _start(ch: WebSocketChannel, remote_port: int) -> asyncio.Task[None]:
    ch._remote = RemoteListener(
        serve=ch._serve_remote,
        port=remote_port,
        detect=lambda: ("127.0.0.1",),
        lookup_name=lambda ip: "macbook.tail1a2b3c.ts.net",
        poll_s=3600,
    )
    task = asyncio.create_task(ch.start())
    await wait_listening(ch)
    await ch._remote.apply(enabled=True, key="remote-key")
    return task


async def _next(ws: Any, event: str, timeout: float = 5) -> dict[str, Any]:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if frame.get("event") == event:
            return frame


async def _silent(ws: Any, event: str, wait: float = 0.5) -> bool:
    """Nessun frame *event* entro *wait* secondi."""
    try:
        await _next(ws, event, timeout=wait)
    except asyncio.TimeoutError:
        return True
    return False


async def test_a_message_from_the_mac_reaches_the_phone_once_with_its_origin(
    bus, data_dir, monkeypatch,
) -> None:
    monkeypatch.setattr(ws_remote, "is_tailnet_ip", lambda host: host == "127.0.0.1")
    port, remote_port = free_port(), free_port()
    ch = _channel(bus, port)
    task = await _start(ch, remote_port)
    try:
        async with (
            websockets.connect(f"ws://127.0.0.1:{port}/?token=master-secret") as phone,
            websockets.connect(f"ws://127.0.0.1:{remote_port}/?token=remote-key") as mac,
        ):
            await _next(phone, "ready")
            await _next(mac, "ready")
            await asyncio.sleep(0.1)  # il nome MagicDNS, chiesto in disparte

            await mac.send(json.dumps({"type": "message", "content": "dal Mac", "webui": True}))
            echo = await _next(phone, "user")
            assert echo["text"] == "dal Mac"
            assert echo["origin"] == "remote" and echo["device"] == "macbook"
            assert echo["rev"] == transcript_revision("websocket:default")
            # Chi l'ha scritto l'ha gia' a schermo.
            assert await _silent(mac, "user")

            # Il turno parte col segno della provenienza.
            inbound = bus.publish_inbound.await_args.args[0]
            assert inbound.metadata["origin_channel"] == "remote"
            assert inbound.metadata["origin_device"] == "macbook"

            # E al contrario: dal telefono al Mac, senza provenienza.
            await phone.send(json.dumps({"type": "message", "content": "dal telefono"}))
            back = await _next(mac, "user")
            assert back["text"] == "dal telefono" and "origin" not in back
            assert await _silent(phone, "user")
            assert "origin_channel" not in bus.publish_inbound.await_args.args[0].metadata

        rows = [r for r in read_transcript_lines("websocket:default") if r.get("event") == "user"]
        assert [(r["text"], r.get("origin"), r.get("device")) for r in rows] == [
            ("dal Mac", "remote", "macbook"),
            ("dal telefono", None, None),
        ]
    finally:
        await ch.stop()
        await task


def test_the_device_survives_a_reload() -> None:
    lines = [{
        "event": "user", "chat_id": "default", "text": "dal Mac",
        "origin": "remote", "device": "macbook",
    }]
    [row] = [m for m in replay_transcript_to_ui_messages(lines) if m["role"] == "user"]
    assert row["origin"] == "remote" and row["device"] == "macbook"


def _spy(monkeypatch, target: str) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(target, lambda content, metadata, *, chat_id=None: seen.append(content))
    return seen


async def test_an_answer_to_a_turn_from_the_mac_does_not_ring_the_phone(
    bus, data_dir, monkeypatch,
) -> None:
    seen = _spy(monkeypatch, "jenny.channels.ws_sender.notify_delivery")
    ch = _channel(bus, free_port())
    remote = {"origin_channel": "remote", "origin_device": "macbook"}
    await ch.send(OutboundMessage(channel="websocket", chat_id="default", content="al Mac",
                                  metadata=remote))
    await ch.send(OutboundMessage(channel="websocket", chat_id="default", content="al telefono"))
    assert seen == ["al telefono"]


async def test_a_streamed_answer_to_a_turn_from_the_mac_does_not_ring_either(monkeypatch) -> None:
    """Lo streaming e' il caso normale, e ha il suo punto di notifica nel dispatcher."""
    seen = _spy(monkeypatch, "jenny.channels.dispatcher.notify_delivery")
    for metadata in ({"_streamed": True, "origin_channel": "remote"}, {"_streamed": True}):
        msg = OutboundMessage(channel="websocket", chat_id="default", content="fatto",
                              metadata=metadata)
        await WebSocketDispatcher._send_once(MagicMock(), msg)
    assert seen == ["fatto"]


async def test_the_revision_grows_with_every_row_and_rides_turn_end(bus, data_dir) -> None:
    key = "websocket:default"
    before = transcript_revision(key)
    append_transcript_object(key, {"event": "message", "chat_id": "default", "text": "uno"})
    after = transcript_revision(key)
    assert after != before
    assert transcript_revision("websocket:project:altro") != after  # un filo, una revisione

    ch = _channel(bus, free_port())
    conn = AsyncMock()
    ch._attach(conn, "default")
    await ch.send_turn_end("default")
    frame = json.loads(conn.send.await_args.args[0])
    # Dopo la riga del turn_end, non prima: chi la riceve ha tutto fino a qui.
    assert frame["rev"] == transcript_revision(key) != after


async def test_the_thread_route_returns_the_revision(bus, data_dir) -> None:
    port = free_port()
    ch = _channel(bus, port)
    append_transcript_object("websocket:default", {"event": "message", "chat_id": "default",
                                                   "text": "ciao"})
    task = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{port}/api/sessions/websocket%3Adefault/webui-thread?limit=1",
                headers={"Authorization": "Bearer master-secret"},
            )
        assert r.status_code == 200
        assert r.json()["rev"] == transcript_revision("websocket:default")
    finally:
        await ch.stop()
        await task
