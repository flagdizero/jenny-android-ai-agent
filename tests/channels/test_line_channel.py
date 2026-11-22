"""La linea passata vista dal canale WebSocket e dal dispatcher.

Tre giunti, uno per lato (``jenny/peers/line.py`` li spiega):

- chi **riceve**: il messaggio arrivato dalla linea entra nella chat come uno
  scritto da un'altra finestra — eco alle finestre aperte, riga nel transcript
  con la provenienza, turno sul bus col segno della linea;
- chi **passa**: finché la chat è passata, quel che l'utente scrive non diventa
  un turno e non lascia niente nel transcript;
- il **ritorno**: il dispatcher dà a ``PeerLines`` solo i messaggi di un turno
  arrivato da una linea.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import websockets
from port_alloc import free_port, wait_listening
from support.aio import wait_until
from support.ws_channel import make_ws_channel

from jenny.bus.events import WEBSOCKET_CHANNEL, OutboundMessage
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.transcript_store import read_transcript_lines

PEER = "a" * 32
LID = "b" * 32


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    return tmp_path


def _channel(bus: Any, port: int) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True, "allowFrom": ["*"], "host": "127.0.0.1", "port": port, "path": "/",
        "tokenIssueSecret": "master-secret", "websocketRequiresToken": True,
    }
    return make_ws_channel(WebSocketConfig.model_validate(cfg), bus)


async def _next(ws: Any, event: str, timeout: float = 5) -> dict[str, Any]:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if frame.get("event") == event:
            return frame


class _Lines:
    """Un ``PeerLines`` di prova: dice se la chat è passata e ricorda cosa ha visto."""

    def __init__(self, *, passed: bool) -> None:
        self.passed = passed
        self.routed: list[tuple[str, str, str]] = []
        self.finals: list[Any] = []

    async def route(self, channel: str, chat_id: str, text: str) -> bool:
        self.routed.append((channel, chat_id, text))
        return self.passed

    async def on_outbound(self, msg: Any) -> None:
        self.finals.append(msg)


async def test_a_message_from_a_line_enters_the_chat_with_its_origin(bus, data_dir) -> None:
    port = free_port()
    ch = _channel(bus, port)
    task = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/?token=master-secret") as phone:
            await _next(phone, "ready")

            await ch.inject_user_message(
                "default", "ci sono email nuove?",
                {"origin": "peer_line", "device": "Personale"},
                {"_line_peer": PEER, "_line_lid": LID},
            )

            echo = await _next(phone, "user")
            assert echo["text"] == "ci sono email nuove?"
            assert echo["origin"] == "peer_line" and echo["device"] == "Personale"
            inbound = bus.publish_inbound.await_args.args[0]
            assert (inbound.channel, inbound.chat_id) == (WEBSOCKET_CHANNEL, "default")
            assert inbound.content == "ci sono email nuove?"
            assert inbound.metadata["_line_peer"] == PEER
            assert inbound.metadata["_line_lid"] == LID
            assert inbound.metadata["origin_channel"] == "peer_line"
            rows = [
                row if isinstance(row, dict) else json.loads(row)
                for row in read_transcript_lines("websocket:default")
            ]
            assert [r.get("origin") for r in rows if r.get("event") == "user"] == ["peer_line"]
    finally:
        await ch.stop()
        task.cancel()


async def test_a_passed_chat_starts_no_turn_and_leaves_nothing_on_disk(bus, data_dir) -> None:
    port = free_port()
    ch = _channel(bus, port)
    lines = _Lines(passed=True)
    ch._peer_link = SimpleNamespace(lines=lines)
    task = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/?token=master-secret") as phone:
            await _next(phone, "ready")
            await phone.send(json.dumps({"type": "message", "content": "per Lavoro", "webui": True}))
            await wait_until(lambda: lines.routed)

            assert lines.routed == [(WEBSOCKET_CHANNEL, "default", "per Lavoro")]
            bus.publish_inbound.assert_not_awaited()
            assert read_transcript_lines("websocket:default") == []

            # E quel che la linea ha da dire arriva alle finestre di quella chat.
            await ch.send_line_event("default", {"kind": "item", "item": {"role": "peer", "text": "ok"}})
            frame = await _next(phone, "line")
            assert frame["chat_id"] == "default" and frame["item"]["text"] == "ok"
    finally:
        await ch.stop()
        task.cancel()


async def test_without_a_line_the_message_is_a_turn_as_always(bus, data_dir) -> None:
    port = free_port()
    ch = _channel(bus, port)
    lines = _Lines(passed=False)
    ch._peer_link = SimpleNamespace(lines=lines)
    task = asyncio.create_task(ch.start())
    await wait_listening(ch)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/?token=master-secret") as phone:
            await _next(phone, "ready")
            await phone.send(json.dumps({"type": "message", "content": "per Jenny", "webui": True}))
            await wait_until(lambda: bus.publish_inbound.await_count >= 1)
            assert bus.publish_inbound.await_args.args[0].content == "per Jenny"
    finally:
        await ch.stop()
        task.cancel()


async def test_the_dispatcher_hands_back_only_the_turns_of_a_line() -> None:
    lines = _Lines(passed=False)
    dispatcher = WebSocketDispatcher.__new__(WebSocketDispatcher)
    dispatcher.channels = {  # type: ignore[attr-defined]
        WEBSOCKET_CHANNEL: SimpleNamespace(peer_link=SimpleNamespace(lines=lines)),
    }

    def out(channel: str, **metadata: Any) -> OutboundMessage:
        return OutboundMessage(channel=channel, chat_id="default", content="x", metadata=metadata)

    await dispatcher._return_line_final(out(WEBSOCKET_CHANNEL))
    await dispatcher._return_line_final(out("telegram", _line_peer=PEER))
    assert lines.finals == []
    await dispatcher._return_line_final(out(WEBSOCKET_CHANNEL, _line_peer=PEER, _line_lid=LID))
    assert len(lines.finals) == 1
