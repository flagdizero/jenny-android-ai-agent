"""Da un altro dispositivo la WebUI non comanda il telefono, e i suoi file si ricontrollano.

Due regole, misurate il 15/10/2026 (``.agent/`` ha il piano, che non e' nel repo):

- le route che agiscono **sul telefono** (aprire o disinstallare un'app Android,
  Info app, Accessibilita', la mascotte flottante, l'installazione di un
  aggiornamento, la vista esterna di una mini-app) da fuori rispondono 403
  ``phone_only`` **prima** di toccare il ponte nativo; l'elenco delle app
  Android torna vuoto; la pagina lo sa dal bootstrap (``remote``);
- i file statici portano un'impronta (``ETag``) e una richiesta che la ripete
  riceve 304 senza corpo; da fuori i file di testo arrivano compressi.

Il canale e' vero, con l'ascoltatore remoto su loopback e il controllo «e' del
tailnet?» allargato a ``127.0.0.1``, come in ``tests/channels/test_ws_remote.py``.
"""

from __future__ import annotations

import asyncio
import gzip
import inspect
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from jenny.channels import ws_remote
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.channels.ws_remote import RemoteListener
from jenny.webui import apps_routes, settings_routes, static_cache, ws_http
from jenny.webui.gateway_services import build_gateway_services
from jenny.webui.phone_only import PHONE_ONLY_ROUTES

ROOT = Path(__file__).resolve().parents[2]

# Il generatore di porte libere sta in `tests/channels` (pytest mette nel path
# solo la cartella del test): lo stesso dei banchi del canale.
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "channels"))
from port_alloc import free_port  # noqa: E402


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


@pytest.fixture()
def rig(bus, monkeypatch):
    """Un canale vero: ``local`` e' il telefono (loopback), ``remote`` il Mac."""
    monkeypatch.setattr(ws_remote, "is_tailnet_ip", lambda host: host == "127.0.0.1")
    static_cache.reset_static_cache()
    port, remote_port = free_port(), free_port()
    cfg = WebSocketConfig.model_validate({
        "enabled": True, "allowFrom": ["*"], "host": "127.0.0.1", "port": port, "path": "/",
        "tokenIssueSecret": "master-secret", "websocketRequiresToken": True,
    })
    gateway = build_gateway_services(
        config=cfg, bus=bus, session_manager=None, workspace_path=Path.cwd(),
        default_restrict_to_workspace=False, runtime_model_name=None,
    )
    ch = WebSocketChannel(cfg, bus, gateway=gateway)
    ch._remote = RemoteListener(
        serve=ch._serve_remote, port=remote_port, detect=lambda: ("127.0.0.1",), poll_s=3600,
    )

    class Rig:
        local = f"http://127.0.0.1:{port}"
        remote = f"http://127.0.0.1:{remote_port}"

        async def get(self, base: str, path: str, key: str | None = None, **headers: str):
            if key:
                headers["Authorization"] = f"Bearer {key}"
            async with httpx.AsyncClient() as client:
                return await client.get(base + path, headers=headers)

    async def start():
        task = asyncio.create_task(ch.start())
        await asyncio.sleep(0.3)
        await ch._remote.apply(enabled=True, key="remote-key")
        return task

    r = Rig()
    r.start = start
    r.channel = ch
    return r


async def _with(rig, body) -> None:
    task = await rig.start()
    try:
        await body()
    finally:
        await rig.channel.stop()
        await task


def _spy(monkeypatch, module: Any, name: str, result: Any) -> list[tuple]:
    calls: list[tuple] = []

    async def fake(*args, **kwargs):
        calls.append(args)
        return result

    monkeypatch.setattr(module, name, fake)
    return calls


# -- Azioni sul telefono -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "module", "name", "result"),
    [
        ("/api/webui/android-apps/com.example/launch", ws_http, "launch_android_app", True),
        ("/api/webui/android-apps/com.example/uninstall", ws_http, "uninstall_android_app", True),
        ("/api/webui/android-apps/com.example/app-info", ws_http, "open_android_app_info", True),
        ("/api/settings/floating/update?enabled=1", settings_routes, "update_floating_settings", {}),
        ("/api/updates/install", settings_routes, "start_update_install", {}),
    ],
)
async def test_phone_actions_are_refused_from_remote_and_still_work_locally(
    rig, monkeypatch, path, module, name, result,
) -> None:
    calls = _spy(monkeypatch, module, name, result)

    async def body() -> None:
        r = await rig.get(rig.remote, path, "remote-key")
        assert r.status_code == 403 and r.json() == {"error": "phone_only"}
        assert calls == [], "da fuori il ponte nativo e' stato chiamato"
        r = await rig.get(rig.local, path, "master-secret")
        assert r.status_code == 200
        assert len(calls) == 1

    await _with(rig, body)


async def test_accessibility_settings_are_refused_from_remote(rig, monkeypatch) -> None:
    from jenny.runtime import screen

    calls = _spy(monkeypatch, screen, "open_screen_settings", True)

    async def body() -> None:
        r = await rig.get(rig.remote, "/api/settings/floating/screen-settings", "remote-key")
        assert r.status_code == 403 and r.json() == {"error": "phone_only"}
        assert calls == []

    await _with(rig, body)


async def test_the_overlay_permission_page_is_refused_from_remote(rig, monkeypatch) -> None:
    from jenny.runtime import floating

    calls = _spy(monkeypatch, floating, "open_permission_settings", True)

    async def body() -> None:
        path = "/api/settings/floating/permission-settings"
        r = await rig.get(rig.remote, path, "remote-key")
        assert r.status_code == 403 and r.json() == {"error": "phone_only"}
        assert calls == []
        r = await rig.get(rig.local, path, "master-secret")
        assert r.status_code == 200 and r.json() == {"opened": True}
        assert len(calls) == 1

    await _with(rig, body)


async def test_the_android_app_list_is_empty_from_remote(rig, monkeypatch) -> None:
    calls = _spy(monkeypatch, ws_http, "webui_android_apps_payload", {"apps": [{"packageName": "x"}]})

    async def body() -> None:
        r = await rig.get(rig.remote, "/api/webui/android-apps", "remote-key")
        assert r.status_code == 200 and r.json() == {"apps": []}
        assert calls == []
        r = await rig.get(rig.local, "/api/webui/android-apps", "master-secret")
        assert r.json() == {"apps": [{"packageName": "x"}]}

    await _with(rig, body)


async def test_an_unauthenticated_remote_call_is_still_a_401(rig) -> None:
    """Il rifiuto ``phone_only`` viene dopo la chiave: non dice niente a chi non ce l'ha."""

    async def body() -> None:
        r = await rig.get(rig.remote, "/api/webui/android-apps/com.example/launch")
        assert r.status_code == 401

    await _with(rig, body)


async def test_the_page_knows_it_is_remote(rig) -> None:
    async def body() -> None:
        r = await rig.get(rig.remote, "/webui/bootstrap", **{"X-Jenny-Auth": "remote-key"})
        assert r.json()["remote"] is True
        r = await rig.get(rig.local, "/webui/bootstrap", **{"X-Jenny-Auth": "master-secret"})
        assert r.json()["remote"] is False

    await _with(rig, body)


async def test_the_floating_mascot_is_not_available_from_remote(rig, monkeypatch) -> None:
    from jenny.webui import settings_api

    monkeypatch.setattr(settings_api, "_android_context", lambda: object())
    monkeypatch.setattr(settings_routes, "_enrich_floating", AsyncMock())

    async def body() -> None:
        r = await rig.get(rig.remote, "/api/settings", "remote-key")
        assert r.json()["floating"]["available"] is False
        r = await rig.get(rig.local, "/api/settings", "master-secret")
        assert r.json()["floating"]["available"] is True

    await _with(rig, body)


def test_every_route_that_reaches_a_phone_action_refuses_remote_callers() -> None:
    """La prossima route che apre qualcosa sul telefono non passa da fuori per
    dimenticanza: ogni metodo che chiama una di queste funzioni controlla prima."""
    actions = (
        "launch_android_app", "uninstall_android_app", "open_android_app_info",
        "update_floating_settings", "open_screen_settings", "open_permission_settings",
        "start_update_install",
        "AppViewProxy(",
    )
    found = []
    for module in (ws_http, settings_routes, apps_routes):
        source = inspect.getsource(module)
        for m in re.finditer(r"\n    async def (\w+)\(.*?(?=\n    (?:async )?def |\Z)", source, re.S):
            body = m.group(0)
            if any(a in body for a in actions):
                found.append(m.group(1))
                assert "refuse_if_remote()" in body, f"{module.__name__}.{m.group(1)} non rifiuta da fuori"
    assert len(found) >= 8, found
    assert len(PHONE_ONLY_ROUTES) == 8


# -- File statici: impronta, 304, compressione -----------------------------------------


async def test_static_files_carry_an_etag_and_answer_304(rig) -> None:
    async def body() -> None:
        r = await rig.get(rig.local, "/html-mobile/assets/home-app.js")
        assert r.status_code == 200 and r.content
        etag = r.headers["etag"]
        assert r.headers["cache-control"] == "no-cache"
        assert "content-encoding" not in r.headers, "sul telefono niente compressione"
        again = await rig.get(rig.local, "/html-mobile/assets/home-app.js")
        assert again.headers["etag"] == etag

        r = await rig.get(rig.local, "/html-mobile/assets/home-app.js", **{"If-None-Match": etag})
        assert r.status_code == 304 and r.content == b""
        assert r.headers["etag"] == etag

        r = await rig.get(rig.local, "/html-mobile/assets/home-app.js", **{"If-None-Match": '"altro"'})
        assert r.status_code == 200 and r.content

        shell = await rig.get(rig.local, "/html-mobile/")
        assert "content-security-policy" in shell.headers and shell.headers["etag"]

    await _with(rig, body)


async def test_remote_text_files_arrive_compressed(rig) -> None:
    async def body() -> None:
        plain = await rig.get(rig.local, "/html-mobile/assets/home-app.js")
        r = await rig.get(rig.remote, "/html-mobile/assets/home-app.js", **{"Accept-Encoding": "gzip"})
        assert r.headers["content-encoding"] == "gzip"
        assert r.headers["vary"] == "Accept-Encoding"
        assert r.headers["etag"] != plain.headers["etag"] and r.headers["etag"].endswith('-gz"')
        # httpx decomprime da se': il contenuto e' lo stesso file.
        assert r.content == plain.content
        assert int(r.headers["content-length"]) < len(plain.content) / 2

        r304 = await rig.get(rig.remote, "/html-mobile/assets/home-app.js",
                             **{"Accept-Encoding": "gzip", "If-None-Match": r.headers["etag"]})
        assert r304.status_code == 304

        # Senza gzip fra gli encoding accettati, il file in chiaro.
        r = await rig.get(rig.remote, "/html-mobile/assets/home-app.js", **{"Accept-Encoding": "identity"})
        assert "content-encoding" not in r.headers

    await _with(rig, body)


def test_accept_encoding_parsing() -> None:
    assert static_cache.accepts_gzip("gzip, deflate, br")
    assert static_cache.accepts_gzip("br;q=1.0, gzip;q=0.8")
    assert not static_cache.accepts_gzip("gzip;q=0")
    assert not static_cache.accepts_gzip("identity")
    assert not static_cache.accepts_gzip(None)
    assert static_cache.matches('W/"abc", "def"', '"abc"')
    assert static_cache.matches("*", '"abc"')
    assert not static_cache.matches('"abd"', '"abc"')


def test_a_gzipped_body_is_the_file() -> None:
    body = b"x" * 5000
    tag, packed = static_cache.gzip_variant('"t"', body)
    assert tag == '"t-gz"' and gzip.decompress(packed) == body
    assert static_cache.gzip_variant('"s"', b"tiny") is None
