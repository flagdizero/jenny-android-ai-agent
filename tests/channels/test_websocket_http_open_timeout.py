"""Una route HTTP lenta risponde invece di chiudere la connessione vuota.

Per ``websockets`` una richiesta HTTP è un handshake mai concluso, e
``open_timeout`` lo taglia: col default di 10 s l'export di un backup grande
arrivava alla WebView come ``ERR_EMPTY_RESPONSE`` («Failed to fetch»).
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from port_alloc import free_port, wait_listening

import jenny.channels.websocket as websocket_module
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services


def _channel(port: int) -> WebSocketChannel:
    cfg = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port,
        "path": "/",
        "websocketRequiresToken": False,
        "tokenIssueSecret": "s",
    }
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    gateway = build_gateway_services(
        config=WebSocketConfig.model_validate(cfg),
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    channel = WebSocketChannel(cfg, bus, gateway=gateway)

    async def slow_route(connection, request):
        await asyncio.sleep(0.6)
        return connection.respond(200, "fatto\n")

    channel._dispatch_http = slow_route  # type: ignore[method-assign]
    return channel


async def _get(port: int) -> httpx.Response:
    return await asyncio.to_thread(
        functools.partial(httpx.get, f"http://127.0.0.1:{port}/api/lenta", timeout=5.0)
    )


async def _serve_and_get(port: int) -> httpx.Response:
    channel = _channel(port)
    task = asyncio.create_task(channel.start())
    await wait_listening(channel)
    try:
        return await _get(port)
    finally:
        await channel.stop()
        await task


def test_the_ceiling_fits_a_large_backup() -> None:
    # 310 MB sul Titan 2 superavano i 10 s del default: il tetto sta ben sopra.
    assert websocket_module._HTTP_OPEN_TIMEOUT_S >= 300


async def test_the_ceiling_is_what_the_server_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Col tetto sotto la durata della route, la risposta è vuota: il valore arriva a ``serve``.

    Se ``serve`` non lo ricevesse varrebbero i 10 s del default, e la route da
    0,6 s risponderebbe lo stesso.
    """
    monkeypatch.setattr(websocket_module, "_HTTP_OPEN_TIMEOUT_S", 0.2)
    with pytest.raises(httpx.RemoteProtocolError):
        await _serve_and_get(free_port())


async def test_a_slow_route_answers() -> None:
    response = await _serve_and_get(free_port())
    assert response.status_code == 200
    assert response.text == "fatto\n"
