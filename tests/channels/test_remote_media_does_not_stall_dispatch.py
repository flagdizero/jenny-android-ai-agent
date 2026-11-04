"""Le immagini remote non fermano il dispatcher più di un budget.

``WebSocketChannel.send`` scarica in locale le immagini remote del messaggio
(``localize_remote_media``) una dopo l'altra, con 15 s di timeout ciascuna, e
lo fa dentro il ciclo seriale del dispatcher: finché scarica, nessun canale
riceve niente — nemmeno i delta di un altro turno, che intanto si ammucchiano e
vengono scartati. Qui l'ingest finto impiega 1 s per immagine: tre
immagini tenevano ``send`` fermo tre secondi. Ora c'è un budget totale, e allo
scadere il messaggio parte con gli URL remoti com'erano.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from port_alloc import free_port
from support.ws_channel import make_ws_channel

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.websocket import WebSocketChannel
from jenny.webui import media_ingest

URLS = [f"https://img.example.com/{i}.png" for i in range(3)]


@pytest.fixture(autouse=True)
def isolate_webui_workspace_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


def _channel(bus: MessageBus) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True, "allowFrom": ["*"], "host": "127.0.0.1", "port": free_port(),
        "path": "/ws", "websocketRequiresToken": False,
    }
    return make_ws_channel(cfg, bus)


async def test_slow_remote_images_are_bounded_by_a_total_budget(monkeypatch) -> None:
    async def _slow_ingest(url: str, **_kwargs: Any):
        await asyncio.sleep(1.0)
        return None

    monkeypatch.setattr("jenny.webui.media_gateway.ingest_remote_image", _slow_ingest)
    monkeypatch.setattr(media_ingest, "LOCALIZE_TOTAL_TIMEOUT_S", 0.3, raising=False)

    channel = _channel(MessageBus())
    ws = AsyncMock()
    channel._attach(ws, "default")
    text = "Ecco:\n" + "\n".join(f"![foto]({url})" for url in URLS)

    started = time.monotonic()
    await channel.send(OutboundMessage(channel="websocket", chat_id="default", content=text))
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    payload = json.loads(ws.send.await_args.args[0])
    # Degradazione morbida: gli URL restano quelli remoti.
    for url in URLS:
        assert url in payload["text"]


def test_the_budget_is_well_below_one_serial_timeout() -> None:
    assert media_ingest.LOCALIZE_TOTAL_TIMEOUT_S < media_ingest.TIMEOUT_S
