"""La notifica di un messaggio consegnato in un quaderno sa da che quaderno arriva.

Il ``chat_id`` della consegna (``project:<nome>``) deve arrivare fino a
``notify_delivery``: è da lì che il quaderno entra nel tag della notifica, e
dal tag Kotlin ricava dove portare il tocco. Fino al 10/10/2026 si perdeva qui,
e ogni notifica apriva la chat personale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services


def _spy(monkeypatch, target: str) -> list[tuple[str, Any]]:
    seen: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        target, lambda content, metadata, *, chat_id=None: seen.append((content, chat_id))
    )
    return seen


def _ws_channel(bus: MessageBus) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": 29878,
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


async def test_a_notebook_answer_passes_its_chat_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    seen = _spy(monkeypatch, "jenny.channels.ws_sender.notify_delivery")
    ws = _ws_channel(MessageBus())
    await ws.send(OutboundMessage(channel="websocket", chat_id="project:Casa", content="fatto"))
    await ws.send(OutboundMessage(channel="websocket", chat_id="default", content="ciao"))
    assert seen == [("fatto", "project:Casa"), ("ciao", "default")]


async def test_a_streamed_final_passes_the_chat_id_only_from_the_webui(monkeypatch) -> None:
    """Il percorso del finale streammato è comune a tutti i canali: un
    ``chat_id`` che non è della WebUI non nomina un quaderno, e non passa."""
    seen = _spy(monkeypatch, "jenny.channels.dispatcher.notify_delivery")
    for channel in ("websocket", "telegram"):
        msg = OutboundMessage(
            channel=channel, chat_id="project:Casa", content="fatto",
            metadata={"_streamed": True},
        )
        await WebSocketDispatcher._send_once(MagicMock(), msg)
    assert seen == [("fatto", "project:Casa"), ("fatto", None)]
