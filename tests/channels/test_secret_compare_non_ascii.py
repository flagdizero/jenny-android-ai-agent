"""Un segreto non ASCII non fa esplodere l'autenticazione del gateway.

``hmac.compare_digest`` su due ``str`` accetta solo ASCII: con un ``é`` nel
token solleva ``TypeError`` *prima* dell'autenticazione, e il traceback (con
le variabili locali, segreto compreso) finiva nel log. Ogni confronto deve
rispondere ``False`` — cioè un 401 pulito — su qualunque input, header con
byte non ASCII compresi (``websockets`` li decodifica in surrogati).
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import websockets
from port_alloc import free_port, wait_listening

from jenny.channels.http_utils import (
    check_api_secret,
    check_app_secret,
    issue_route_secret_matches,
    secret_matches,
)
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services

SECRET = "route-secret"

# Un carattere non ASCII qualunque, un surrogato come quelli che ``websockets``
# produce da un byte non ASCII in un header, e un emoji fuori dal BMP.
HOSTILE = ["sécret", "\udce9", "\U0001f600", SECRET + "é"]


@pytest.mark.parametrize("supplied", HOSTILE)
def test_secret_matches_is_false_on_non_ascii_input(supplied: str) -> None:
    assert secret_matches(supplied, SECRET) is False
    assert secret_matches(SECRET, supplied) is False


def test_secret_matches_accepts_the_right_secret_and_a_non_ascii_one() -> None:
    assert secret_matches(SECRET, SECRET) is True
    assert secret_matches("sécret", "sécret") is True
    assert secret_matches("", SECRET) is False


@pytest.mark.parametrize("supplied", HOSTILE)
def test_every_gateway_check_is_false_on_non_ascii_token(supplied: str) -> None:
    # Dizionari e non ``Headers``: websockets 16.1 rifiuta già alla costruzione
    # un valore con surrogati o fuori da Latin-1, e qui conta cosa fanno le
    # funzioni con qualunque stringa arrivi (16.0, sul Mac, la lascia passare).
    bearer = {"Authorization": f"Bearer {supplied}"}
    jenny_auth = {"X-Jenny-Auth": supplied}
    none: dict[str, str] = {}
    assert issue_route_secret_matches(bearer, SECRET) is False
    assert issue_route_secret_matches(jenny_auth, SECRET) is False
    assert check_api_secret(bearer, "/api/x", SECRET) is False
    assert check_api_secret(none, "/api/x?token=" + supplied, SECRET) is False
    assert check_app_secret(bearer, "/apps/a/", SECRET, "a") is False
    assert check_app_secret(none, "/apps/a/?token=" + supplied, SECRET, "a") is False


def _channel(port: int) -> WebSocketChannel:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port,
        "path": "/ws",
        "tokenIssueSecret": SECRET,
        "websocketRequiresToken": True,
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


async def _get(url: str, headers: dict[str, Any] | None = None) -> httpx.Response:
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, headers=headers or {}, timeout=5.0)
    )


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_non_ascii_token_gets_a_clean_401_on_every_door() -> None:
    port = free_port()
    channel = _channel(port)
    server_task = asyncio.create_task(channel.start())
    await wait_listening(channel)
    base = f"http://127.0.0.1:{port}"
    try:
        # ?token= percentuale-codificato: arriva decodificato come "sécret".
        resp = await _get(f"{base}/api/sessions/x/webui-thread?token=s%C3%A9cret")
        assert resp.status_code == 401
        # Header con byte non ASCII (UTF-8 e Latin-1 grezzi).
        for raw in ("sécret".encode(), "sécret".encode("latin-1")):
            resp = await _get(f"{base}/api/sessions/x/webui-thread",
                              {"Authorization": b"Bearer " + raw})
            assert resp.status_code == 401
            resp = await _get(f"{base}/webui/bootstrap", {"X-Jenny-Auth": raw})
            assert resp.status_code == 401
            resp = await _get(f"{base}/webui/bootstrap", {"Authorization": b"Bearer " + raw})
            assert resp.status_code == 401
        # Upgrade WebSocket con un token non ASCII.
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token=s%C3%A9cret"):
                pass
        assert excinfo.value.response.status_code == 401
        # Il segreto giusto passa ancora.
        resp = await _get(f"{base}/webui/bootstrap", {"X-Jenny-Auth": SECRET})
        assert resp.status_code == 200
    finally:
        await channel.stop()
        await server_task
