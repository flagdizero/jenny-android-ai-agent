"""Il controllo SSRF risolve i nomi fuori dal loop.

Le tre policy di ``security/network.py`` chiamano ``socket.getaddrinfo``, che è
bloccante, e tutti i chiamanti asincroni le chiamavano sul thread del loop: un
DNS lento (rete mobile, un resolver che non risponde) fermava WebSocket, cron e
ogni altro turno per la durata della risoluzione. Qui il resolver è finto e
dorme davvero (``time.sleep``); un ticker da 50 ms conta i propri giri mentre il
chiamante vero aspetta la risposta. Sul loop, i giri sono zero o uno.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from types import SimpleNamespace

import httpx
import pytest

from jenny.security import network

_DELAY_S = 0.4
_MIN_TICKS = 4  # 400 ms a 50 ms fanno ~8 giri


@pytest.fixture
def slow_dns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Ogni nome risolve a un indirizzo pubblico, dopo _DELAY_S di sonno vero."""
    asked: list[str] = []

    def _getaddrinfo(host, *args, **kwargs):
        asked.append(host)
        time.sleep(_DELAY_S)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    fake = SimpleNamespace(
        getaddrinfo=_getaddrinfo,
        AF_UNSPEC=socket.AF_UNSPEC,
        SOCK_STREAM=socket.SOCK_STREAM,
        gaierror=socket.gaierror,
    )
    monkeypatch.setattr(network, "socket", fake)
    return asked


async def _ticks_during(coro):
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.05)
            ticks += 1

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    try:
        out = await coro
    finally:
        stop.set()
        await task
    return ticks, out


async def test_the_async_validators(slow_dns) -> None:
    for coro in (
        network.validate_url_target_async("https://box.example/"),
    ):
        ticks, out = await _ticks_during(coro)
        assert out == (True, ""), out
        assert ticks >= _MIN_TICKS, f"il loop è rimasto fermo: {ticks} giri"


async def test_the_validated_stream_of_downloads_and_updates(slow_dns) -> None:
    from jenny.security.fetch import open_validated_stream

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok"))

    async def _get() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            async with open_validated_stream(client, "https://box.example/f") as (resp, final):
                return final

    ticks, final = await _ticks_during(_get())
    assert final == "https://box.example/f"
    assert ticks >= _MIN_TICKS, ticks


async def test_web_fetch(slow_dns, monkeypatch) -> None:
    from jenny.agent.tools import android_web

    async def _fetch(ctx, url, timeout=None):
        return "<html><body><h1>ciao</h1></body></html>", "https://box.example/"

    async def _no_direct(url):
        return None

    monkeypatch.setattr(android_web, "_bridge_fetch", _fetch)
    monkeypatch.setattr(android_web, "_direct_fetch", _no_direct)
    tool = android_web.AndroidWebFetchTool(android_context=object(), timeout=5)
    ticks, out = await _ticks_during(tool.execute("https://box.example/"))
    assert "ciao" in json.loads(out)["text"]
    # Due risoluzioni: prima del fetch e sull'URL finale.
    assert len(slow_dns) == 2 and ticks >= 2 * _MIN_TICKS, (ticks, slow_dns)


async def test_browser_open(slow_dns, monkeypatch) -> None:
    from jenny.agent.tools import browser
    from jenny.agent.tools.browser import BrowserOpenTool
    from tests.agent.tools.test_browser import _install, _tool

    browser.reset_browser_state()
    try:
        _install(monkeypatch)
        ticks, out = await _ticks_during(_tool(BrowserOpenTool).execute(url="https://box.example/"))
        assert "Error" not in out.splitlines()[0], out
        assert ticks >= _MIN_TICKS, ticks
    finally:
        browser.reset_browser_state()


async def test_an_app_http_action(slow_dns, monkeypatch) -> None:
    import jenny.apps.http as apps_http
    from jenny.apps.manifest import AppAction, AppManifest

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(
        apps_http.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw),
    )
    manifest = AppManifest(name="Piante", description="x", server_base_url="http://box.example:8080")
    action = AppAction(name="a", description="t", kind="http", method="GET", path="/plants")

    ticks, out = await _ticks_during(apps_http.execute_http_action("piante", manifest, action, {}))
    assert out, out
    assert ticks >= _MIN_TICKS, ticks


async def test_the_app_view_proxy(slow_dns) -> None:
    from jenny.apps.proxy import AppViewProxy

    proxy = AppViewProxy("piante", "http://box.example:8080")
    try:
        ticks, _ = await _ticks_during(proxy.start())
        assert ticks >= _MIN_TICKS, ticks
    finally:
        await proxy.close()


async def test_an_ssh_tool(slow_dns, monkeypatch) -> None:
    from jenny.agent.tools.ssh import SshExecTool
    from jenny.config import loader as loader_mod
    from jenny.config.schema import Config
    from jenny.config.tool_schemas import SshHostConfig

    config = Config()
    config.tools.ssh.enable = True
    config.tools.ssh.hosts = [SshHostConfig(alias="lab", host="box.example", username="u")]
    monkeypatch.setattr(loader_mod, "load_config", lambda *a, **k: config)

    ticks, out = await _ticks_during(SshExecTool().execute(host="lab", command="uptime"))
    assert slow_dns == ["box.example"], out
    assert ticks >= _MIN_TICKS, (ticks, out)
