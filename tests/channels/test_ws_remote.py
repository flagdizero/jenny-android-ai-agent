"""L'ascoltatore Tailscale: chi passa, quando ascolta, quale chiave vale.

Le prove senza rete reggono il filtro e il ciclo di vita con un ``serve`` finto;
quelle in fondo aprono il canale vero, con l'ascoltatore remoto su loopback e il
controllo «è del tailnet?» allargato a ``127.0.0.1`` — l'unica cosa che una
prova non può avere è un vero indirizzo Tailscale.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import websockets
from port_alloc import free_port

from jenny.channels import ws_remote
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.channels.ws_remote import (
    RemoteListener,
    auth_secret,
    detect_tailscale,
    is_tailnet_ip,
    on_remote_listener,
    peer_allowed,
    write_remote_access,
)
from jenny.config.loader import load_config
from jenny.runtime.context import get_runtime_context
from jenny.webui.gateway_services import build_gateway_services

TS4 = "100.94.12.7"
TS6 = "fd7a:115c:a1e0::1234"

# -- Il filtro --------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("100.64.0.1", True),
        ("100.127.255.254", True),
        ("::ffff:100.101.7.33", True),
        ("fd7a:115c:a1e0:ab12::1", True),
        ("100.128.0.1", False),  # fuori dal /10
        ("192.168.1.20", False),
        ("10.0.0.5", False),
        ("127.0.0.1", False),
        ("fe80::1%wlan0", False),
        ("fd00::1", False),
        ("", False),
        (None, False),
        ("not-an-ip", False),
    ],
)
def test_tailnet_ranges(host: str | None, expected: bool) -> None:
    assert is_tailnet_ip(host) is expected


def test_peer_must_be_tailnet_and_arrive_on_a_bound_address() -> None:
    bound = (TS4, TS6)
    assert peer_allowed((TS4, 18790), ("100.101.7.33", 51000), bound)
    assert peer_allowed((TS6, 18790, 0, 0), ("fd7a:115c:a1e0::9", 51000, 0, 0), bound)
    # Un vicino di Wi-Fi che manda al nostro 100.x: l'indirizzo locale torna,
    # il suo no.
    assert not peer_allowed((TS4, 18790), ("192.168.1.20", 51000), bound)
    # Un 100.x che arriva su un indirizzo non legato (il CGNAT dell'operatore).
    assert not peer_allowed(("100.70.1.1", 18790), ("100.101.7.33", 51000), bound)
    assert not peer_allowed(None, ("100.101.7.33", 51000), bound)
    assert not peer_allowed((TS4, 18790), None, bound)
    assert not peer_allowed((TS4, 18790), ("100.101.7.33", 51000), ())


def test_detect_needs_the_tailscale_ipv6_before_trusting_a_100_x(monkeypatch) -> None:
    def fake(answers: dict[int, str | None]):
        return lambda family, probe: answers[family]

    import socket

    monkeypatch.setattr(ws_remote, "_source_address", fake({socket.AF_INET6: TS6, socket.AF_INET: TS4}))
    assert detect_tailscale() == (TS4, TS6)

    # Rete mobile col CGNAT dell'operatore, Tailscale spento: il 100.x non basta.
    monkeypatch.setattr(
        ws_remote, "_source_address", fake({socket.AF_INET6: "2a01:db8::5", socket.AF_INET: "100.70.1.1"})
    )
    assert detect_tailscale() == ()

    monkeypatch.setattr(ws_remote, "_source_address", fake({socket.AF_INET6: None, socket.AF_INET: None}))
    assert detect_tailscale() == ()

    # Tailscale solo IPv6: si ascolta su quello.
    monkeypatch.setattr(ws_remote, "_source_address", fake({socket.AF_INET6: TS6, socket.AF_INET: "192.168.1.9"}))
    assert detect_tailscale() == (TS6,)


# -- Il ciclo di vita, con un serve finto ---------------------------------------


class _FakeServer:
    def __init__(self, hosts: tuple[str, ...]) -> None:
        self.hosts = hosts
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _Rig:
    def __init__(self) -> None:
        self.addresses: tuple[str, ...] = (TS4, TS6)
        self.servers: list[_FakeServer] = []
        self.fail: OSError | None = None
        self.listener = RemoteListener(
            serve=self.serve,
            port=18790,
            detect=lambda: self.addresses,
            lookup_name=lambda ip: {"100.94.12.7": "titan.tail1a2b3c.ts.net"}.get(ip),
            poll_s=3600,
        )

    async def serve(self, hosts: tuple[str, ...]) -> _FakeServer:
        if self.fail is not None:
            raise self.fail
        server = _FakeServer(hosts)
        self.servers.append(server)
        return server


async def test_enable_listens_on_the_tailscale_addresses() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="k1")
    try:
        assert [s.hosts for s in rig.servers] == [(TS4, TS6)]
        status = await rig.listener.status()
        assert status["enabled"] and status["listening"]
        assert status["address"] == TS4
        assert status["name"] == "titan.tail1a2b3c.ts.net"
    finally:
        await rig.listener.stop()
    assert rig.servers[0].closed
    assert not (await rig.listener.status())["listening"]


async def test_disabled_never_listens_but_still_says_whether_tailscale_is_up() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=False, key="k1")
    assert rig.servers == []
    status = await rig.listener.status()
    assert status == {**status, "enabled": False, "listening": False, "address": TS4}


async def test_no_key_means_off() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="")
    assert rig.servers == [] and not rig.listener.enabled


async def test_follows_tailscale_going_down_and_coming_back() -> None:
    rig = _Rig()
    rig.addresses = ()
    await rig.listener.apply(enabled=True, key="k1")
    try:
        assert rig.servers == []
        status = await rig.listener.status()
        assert status["enabled"] and not status["listening"] and status["error"] is None

        rig.addresses = (TS4, TS6)
        await rig.listener._sync()
        assert [s.hosts for s in rig.servers] == [(TS4, TS6)]

        await rig.listener._sync()  # niente di cambiato: niente da rifare
        assert len(rig.servers) == 1

        rig.addresses = ("100.94.12.8", TS6)  # IP cambiato
        await rig.listener._sync()
        assert rig.servers[0].closed
        assert rig.servers[1].hosts == ("100.94.12.8", TS6)

        rig.addresses = ()
        await rig.listener._sync()
        assert rig.servers[1].closed
        assert not (await rig.listener.status())["listening"]
    finally:
        await rig.listener.stop()


async def test_a_bind_error_is_reported_and_retried() -> None:
    rig = _Rig()
    rig.fail = OSError(98, "Address already in use")
    await rig.listener.apply(enabled=True, key="k1")
    try:
        status = await rig.listener.status()
        assert not status["listening"]
        assert status["error"] == "Address already in use"
        rig.fail = None
        await rig.listener._sync()
        assert (await rig.listener.status())["listening"]
    finally:
        await rig.listener.stop()


async def test_a_new_key_closes_the_connections_opened_with_the_old_one() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="k1")
    try:
        conn = MagicMock()
        conn.remote_address = ("100.101.7.33", 51000)
        conn.close = AsyncMock()
        rig.listener.track(conn)
        assert rig.listener.is_remote(conn)

        await rig.listener.apply(enabled=True, key="k1")  # stessa chiave: niente
        conn.close.assert_not_awaited()

        second = MagicMock()
        second.remote_address = ("100.101.7.33", 51001)
        second.close = AsyncMock()
        rig.listener.track(second)
        peers = (await rig.listener.status())["peers"]
        assert [(p["ip"], p["windows"]) for p in peers] == [("100.101.7.33", 2)]

        await rig.listener.apply(enabled=True, key="k2")
        conn.close.assert_awaited_once()
        second.close.assert_awaited_once()
        assert not rig.listener.is_remote(conn)
        assert len(rig.servers) == 1 and not rig.servers[0].closed  # l'ascolto resta
    finally:
        await rig.listener.stop()


async def test_admit_sets_the_key_only_for_that_request() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="remote-key")
    try:
        good = MagicMock(local_address=(TS4, 18790), remote_address=("100.101.7.33", 5))
        bad = MagicMock(local_address=(TS4, 18790), remote_address=("192.168.1.20", 5))

        async def request(conn: Any) -> tuple[str | None, str, bool]:
            key = rig.listener.admit(conn)
            return key, auth_secret("master"), on_remote_listener()

        # Ogni richiesta ha il suo task, come in ``websockets``: la chiave non
        # trapela da una all'altra, ne' nel chiamante.
        assert await asyncio.create_task(request(good)) == ("remote-key", "remote-key", True)
        assert await asyncio.create_task(request(bad)) == (None, "master", False)
        assert auth_secret("master") == "master"
    finally:
        await rig.listener.stop()


# -- La config ----------------------------------------------------------------


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    get_runtime_context().config_path = path
    return path


async def test_write_generates_the_key_once_and_keeps_it_when_off(config_file: Path) -> None:
    await write_remote_access(enabled=True)
    first = load_config().gateway.remote_access
    assert first.enabled and len(first.key) >= 32

    await write_remote_access(enabled=False)
    off = load_config().gateway.remote_access
    assert not off.enabled and off.key == first.key

    await write_remote_access(rotate=True)
    rotated = load_config().gateway.remote_access
    assert rotated.key != first.key

    raw = json.loads(config_file.read_text("utf-8"))
    assert raw["gateway"]["remoteAccess"]["key"] == rotated.key
    # La chiave remota non e' quella della WebView.
    assert rotated.key != raw.get("websocket", {}).get("token_issue_secret")


# -- Il canale vero -------------------------------------------------------------


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


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
    # L'ascoltatore remoto su loopback, su un'altra porta.
    ch._remote = RemoteListener(
        serve=ch._serve_remote, port=remote_port, detect=lambda: ("127.0.0.1",), poll_s=3600,
    )
    task = asyncio.create_task(ch.start())
    await asyncio.sleep(0.3)
    await ch._remote.apply(enabled=True, key="remote-key")
    return task


async def _bootstrap(port: int, secret: str) -> int:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"http://127.0.0.1:{port}/webui/bootstrap", headers={"X-Jenny-Auth": secret},
        )
        return r.status_code


async def test_outside_the_tailnet_the_remote_listener_answers_403(bus, config_file) -> None:
    port, remote_port = free_port(), free_port()
    ch = _channel(bus, port)
    task = await _start(ch, remote_port)
    try:
        assert await _bootstrap(remote_port, "remote-key") == 403
        assert await _bootstrap(port, "master-secret") == 200
    finally:
        await ch.stop()
        await task


async def test_each_listener_takes_only_its_own_key(bus, config_file, monkeypatch) -> None:
    monkeypatch.setattr(ws_remote, "is_tailnet_ip", lambda host: host == "127.0.0.1")
    port, remote_port = free_port(), free_port()
    ch = _channel(bus, port)
    task = await _start(ch, remote_port)
    try:
        assert await _bootstrap(remote_port, "remote-key") == 200
        assert await _bootstrap(remote_port, "master-secret") == 401
        assert await _bootstrap(port, "master-secret") == 200
        assert await _bootstrap(port, "remote-key") == 401

        url = f"ws://127.0.0.1:{remote_port}/?client_id=mac&token="
        with pytest.raises(websockets.exceptions.InvalidStatus):
            async with websockets.connect(url + "master-secret"):
                pass

        async with websockets.connect(url + "remote-key") as ws:
            ready = json.loads(await ws.recv())
            assert ready["event"] == "ready"
            await ws.send(json.dumps({"type": "rpc", "id": "1", "method": "remote.status"}))
            reply = json.loads(await ws.recv())
            while reply.get("event") != "rpc_result":
                reply = json.loads(await ws.recv())
            assert reply["ok"] and reply["result"]["here"] is True
            assert reply["result"]["peers"][0]["ip"] == "127.0.0.1"

            # Il cambio di chiave scollega questa connessione.
            await ch._remote.apply(enabled=True, key="new-key")
            with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=5)
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008

        assert await _bootstrap(remote_port, "remote-key") == 401
        assert await _bootstrap(remote_port, "new-key") == 200
    finally:
        await ch.stop()
        await task


async def test_remote_set_from_the_phone_turns_it_on_and_off(bus, config_file) -> None:
    port = free_port()
    ch = _channel(bus, port)
    ch._remote = RemoteListener(
        serve=ch._serve_remote, port=free_port(), detect=lambda: ("127.0.0.1",), poll_s=3600,
    )
    task = asyncio.create_task(ch.start())
    await asyncio.sleep(0.3)

    ids = iter(range(1, 100))

    async def call(ws: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        rpc_id = str(next(ids))
        await ws.send(json.dumps({"type": "rpc", "id": rpc_id, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if reply.get("event") == "rpc_result" and reply.get("id") == rpc_id:
                return reply

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/?token=master-secret") as ws:
            await ws.recv()  # ready
            off = await call(ws, "remote.status")
            assert off["ok"] and off["result"]["enabled"] is False
            assert off["result"]["key"] is None and off["result"]["here"] is False

            on = await call(ws, "remote.set", {"enabled": True})
            assert on["ok"] and on["result"]["enabled"] and on["result"]["listening"]
            key = on["result"]["key"]
            assert key and key == load_config().gateway.remote_access.key

            rotated = await call(ws, "remote.rotate")
            assert rotated["result"]["key"] not in (None, key)

            bad = await call(ws, "remote.set", {"enabled": "yes"})
            assert not bad["ok"] and bad["error"]["code"] == "bad_request"

            off = await call(ws, "remote.set", {"enabled": False})
            assert off["result"]["enabled"] is False and off["result"]["listening"] is False
            assert load_config().gateway.remote_access.enabled is False
    finally:
        await ch.stop()
        await task


async def test_remote_rpc_needs_an_authenticated_connection(bus, config_file) -> None:
    port = free_port()
    ch = _channel(bus, port)
    ch.config.websocket_requires_token = False
    task = asyncio.create_task(ch.start())
    await asyncio.sleep(0.3)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
            await ws.recv()  # ready
            await ws.send(json.dumps({"type": "rpc", "id": "1", "method": "remote.set",
                                      "params": {"enabled": True}}))
            reply = json.loads(await ws.recv())
            while reply.get("event") != "rpc_result":
                reply = json.loads(await ws.recv())
            assert not reply["ok"] and reply["error"]["code"] == "forbidden"
        assert load_config().gateway.remote_access.enabled is False
    finally:
        await ch.stop()
        await task


async def test_ping_gets_a_pong(bus, config_file) -> None:
    """Il battito della WebUI aperta da fuori: v. ``shared/ws-manager.js``."""
    port = free_port()
    ch = _channel(bus, port)
    task = asyncio.create_task(ch.start())
    await asyncio.sleep(0.3)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/?token=master-secret") as ws:
            await ws.recv()  # ready
            await ws.send(json.dumps({"type": "ping"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            while reply.get("event") != "pong":
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert reply == {"event": "pong"}
    finally:
        await ch.stop()
        await task
