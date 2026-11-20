"""Il percorso ``/peer`` sul secondo ascoltatore: chi passa, dove, con quale chiave.

Il canale vero, con l'ascoltatore remoto su loopback e il controllo «è della
tailnet?» allargato a ``127.0.0.1``, come in ``tests/channels/test_ws_remote.py``.
Quello che conta qui è che i due mondi non si incrocino: ``/peer`` non apre la
WebView, la WebView non apre ``/peer``, e l'ascoltatore della WebView sul
telefono (``127.0.0.1`` vero) non risponde mai alle altre Jenny.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets
from support.ws_channel import make_bus, make_ws_channel

from jenny.channels import ws_remote
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.channels.ws_remote import RemoteListener
from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.peers.link import PeerLink
from jenny.runtime.context import get_runtime_context


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    get_runtime_context().config_path = path
    return path


def _write_config(path: Path, *, peers: bool, webui: bool) -> None:
    cfg = Config()
    cfg.peers.enabled = peers
    cfg.gateway.remote_access.enabled = webui
    cfg.gateway.remote_access.key = "remote-key" if webui else ""
    save_config(cfg, path)


class _Started:
    def __init__(self, ch: WebSocketChannel, task: asyncio.Task[None], port: int, remote: int):
        self.ch, self.task, self.port, self.remote = ch, task, port, remote


async def _start(tmp_path: Path, config_file: Path, *, peers: bool, webui: bool) -> _Started:
    _write_config(config_file, peers=peers, webui=webui)
    port, remote_port = _free_port(), _free_port()
    parsed = WebSocketConfig.model_validate({
        "enabled": True, "allowFrom": ["*"], "host": "127.0.0.1", "port": port, "path": "/",
        "tokenIssueSecret": "master-secret", "websocketRequiresToken": True,
    })
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    ch = make_ws_channel(parsed, make_bus(), workspace_path=workspace)
    link = PeerLink(workspace, port=remote_port, allow_address=lambda _a: True)
    ch._peer_link = link
    link.on_change = ch.apply_remote_access
    ch._remote = RemoteListener(
        serve=ch._serve_remote, port=remote_port, detect=lambda: ("127.0.0.1",), poll_s=3600,
    )
    task = asyncio.create_task(ch.start())
    for _ in range(100):
        if ch._remote.bound or not (peers or webui):
            break
        await asyncio.sleep(0.05)
    return _Started(ch, task, port, remote_port)


async def _stop(s: _Started) -> None:
    await s.ch.stop()
    await s.task


async def _peer_handshake_accepted(port: int) -> bool:
    """Vero se ``/peer`` accetta l'handshake WebSocket (poi l'abbinamento decide)."""
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/peer", open_timeout=5) as ws:
            # Un primo frame che non è del protocollo: il collegamento chiude.
            await ws.send(json.dumps({"type": "nope"}))
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), timeout=5)
        return True
    except websockets.exceptions.InvalidStatus:
        return False


async def _bootstrap(port: int, secret: str) -> int:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"http://127.0.0.1:{port}/webui/bootstrap", headers={"X-Jenny-Auth": secret},
        )
        return r.status_code


async def test_peers_alone_keep_the_listener_up_for_peer_only(tmp_path, config_file, monkeypatch):
    monkeypatch.setattr(ws_remote, "is_tailnet_ip", lambda host: host == "127.0.0.1")
    s = await _start(tmp_path, config_file, peers=True, webui=False)
    try:
        assert s.ch._remote.bound == ("127.0.0.1",)
        assert await _peer_handshake_accepted(s.remote)
        # La WebView da fuori è spenta: nessuna chiave la apre, nemmeno la vecchia.
        assert await _bootstrap(s.remote, "remote-key") == 403
        assert await _bootstrap(s.remote, "master-secret") == 403
    finally:
        await _stop(s)


async def test_peer_path_is_refused_when_peers_are_off(tmp_path, config_file, monkeypatch):
    monkeypatch.setattr(ws_remote, "is_tailnet_ip", lambda host: host == "127.0.0.1")
    s = await _start(tmp_path, config_file, peers=False, webui=True)
    try:
        assert await _bootstrap(s.remote, "remote-key") == 200
        assert not await _peer_handshake_accepted(s.remote)
    finally:
        await _stop(s)


async def test_peer_path_from_outside_the_tailnet_is_refused(tmp_path, config_file):
    # Il loopback non è della tailnet: senza l'allargamento, ``/peer`` risponde 403.
    s = await _start(tmp_path, config_file, peers=True, webui=False)
    try:
        assert not await _peer_handshake_accepted(s.remote)
    finally:
        await _stop(s)


async def test_the_webview_listener_never_serves_peers(tmp_path, config_file, monkeypatch):
    monkeypatch.setattr(ws_remote, "is_tailnet_ip", lambda host: host == "127.0.0.1")
    s = await _start(tmp_path, config_file, peers=True, webui=False)
    try:
        assert not await _peer_handshake_accepted(s.port)
    finally:
        await _stop(s)


async def test_peer_status_over_rpc(tmp_path, config_file):
    s = await _start(tmp_path, config_file, peers=True, webui=False)
    try:
        url = f"ws://127.0.0.1:{s.port}/?client_id=phone&token=master-secret"
        async with websockets.connect(url) as ws:
            assert json.loads(await ws.recv())["event"] == "ready"
            await ws.send(json.dumps({"type": "rpc", "id": "1", "method": "peer.status"}))
            reply: dict[str, Any] = json.loads(await ws.recv())
            while reply.get("event") != "rpc_result":
                reply = json.loads(await ws.recv())
            assert reply["ok"], reply
            result = reply["result"]
            assert result["enabled"] is True and result["peers"] == []
            assert len(result["self_id"]) == 32

            await ws.send(json.dumps({"type": "rpc", "id": "2", "method": "peer.pair.start"}))
            reply = json.loads(await ws.recv())
            while reply.get("event") != "rpc_result":
                reply = json.loads(await ws.recv())
            pair = reply["result"]["pair"]
            assert pair["link"].startswith("jenny://pair?")
            assert "code=" in pair["link"] and f"port={s.remote}" in pair["link"]
            assert reply["result"]["pairing"]["code"] == pair["code"]
    finally:
        await _stop(s)
