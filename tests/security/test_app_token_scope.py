"""Il token di una Jenny App apre le route di quell'app, e nient'altro.

Fino a Sett 2026 la cornice di un'app riceveva in ``?token=`` il segreto del
gateway: lo stesso che apre ogni route ``/api/`` e la WebSocket. Un'app — o
un'iniezione dentro un'app — aveva l'intera API: le impostazioni dei provider,
l'RPC di scrittura, i prompt all'agente, che ha ``python_exec``. Il sandbox
dell'iframe la teneva fuori dal DOM della SPA, non fuori dal gateway.

Ora la cornice riceve ``app_token(secret, slug)``. Le caselle che contano sono
i rifiuti: su una route della SPA, sulle route di un'altra app, sulla
WebSocket, e sul punto che conia i token — altrimenti un'app se ne farebbe
coniare uno per un'altra.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from support.gateway_http import AUTH_SECRET, make_handler, make_request

from jenny.apps.token import app_token
from jenny.channels.http_utils import check_app_secret
from jenny.webui.ws_http import GatewayHTTPHandler

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

MANIFEST = {
    "name": "Note",
    "description": "Note veloci",
    "actions": [
        {"name": "list_notes", "description": "Elenca", "kind": "storage",
         "op": "query", "collection": "notes"},
    ],
}


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    for slug in ("note", "altra"):
        app = workspace / "apps" / slug
        (app / "app").mkdir(parents=True)
        (app / "data").mkdir()
        (app / "app.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (app / "app" / "index.html").write_text("<!DOCTYPE html>", encoding="utf-8")
    return workspace


@pytest.fixture()
def gateway(tmp_path: Path):
    handler: GatewayHTTPHandler = make_handler(tmp_path / "skills")
    apps_on = MagicMock()
    apps_on.apps.enabled = True
    with patch.object(handler, "_get_workspace_root", return_value=_workspace(tmp_path)), \
         patch("jenny.config.loader.load_config", return_value=apps_on):
        yield handler


NOTE = app_token(AUTH_SECRET, "note")


# ── Il token ─────────────────────────────────────────────────────────────────


def test_the_token_is_bound_to_slug_and_secret() -> None:
    assert app_token("s", "note") == app_token("s", "note")
    assert app_token("s", "note") != app_token("s", "altra")
    assert app_token("s", "note") != app_token("t", "note")
    assert app_token("s", "note") != "s"
    # Viaggia in una query string: niente da codificare.
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", app_token("s", "note"))


def test_the_check_accepts_the_secret_and_the_own_token_only() -> None:
    def ok(token: str, slug: str = "note") -> bool:
        return check_app_secret({}, f"/apps/{slug}/index.html?token={token}", "s", slug)

    assert ok("s")
    assert ok(app_token("s", "note"))
    assert not ok(app_token("s", "altra"))
    assert not ok(app_token("t", "note"))
    assert not ok("")
    assert not ok("%C3%A8")   # non ASCII: un rifiuto, non un'eccezione
    assert not check_app_secret({}, "/apps/note/?token=s", "", "note")


# ── Sulle route dell'app ─────────────────────────────────────────────────────


async def test_the_own_routes_accept_the_app_token(gateway) -> None:
    static = gateway.apps_routes._static(
        make_request("/apps/note/index.html", token=NOTE), "/apps/note/index.html"
    )
    assert static.status_code == 200
    action = await gateway.apps_routes._action(
        make_request("/api/apps/note/actions/list_notes?params=%7B%7D", token=NOTE),
        "note", "list_notes",
    )
    assert action.status_code == 200, action.body


async def test_another_apps_routes_refuse_it(gateway) -> None:
    static = gateway.apps_routes._static(
        make_request("/apps/altra/index.html", token=NOTE), "/apps/altra/index.html"
    )
    assert static.status_code == 401
    action = await gateway.apps_routes._action(
        make_request("/api/apps/altra/actions/list_notes?params=%7B%7D", token=NOTE),
        "altra", "list_notes",
    )
    assert action.status_code == 401


async def test_the_spa_secret_still_opens_the_app_routes(gateway) -> None:
    static = gateway.apps_routes._static(
        make_request("/apps/note/index.html"), "/apps/note/index.html"
    )
    assert static.status_code == 200


# ── Fuori dalle route dell'app ───────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/settings",
    "/api/webui/settings",
    "/api/webui/apps",
    "/api/sessions/websocket%3Adefault/webui-thread",
])
def test_the_app_token_is_worth_nothing_on_the_spa_routes(gateway, path) -> None:
    assert gateway.check_api_secret(make_request(path, token=NOTE)) is False
    assert gateway.check_api_secret(make_request(path)) is True


async def test_the_apps_list_refuses_it_end_to_end(gateway) -> None:
    response = await gateway._dispatch_resolved(
        MagicMock(), make_request("/api/webui/apps", token=NOTE), "/api/webui/apps"
    )
    assert response.status_code == 401


async def test_an_app_cannot_mint_a_token_for_another(gateway) -> None:
    """Il punto che conia i token vuole il segreto intero."""
    path = "/api/webui/apps/altra/token"
    refused = await gateway._dispatch_resolved(MagicMock(), make_request(path, token=NOTE), path)
    assert refused.status_code == 401
    minted = await gateway._dispatch_resolved(MagicMock(), make_request(path), path)
    assert minted.status_code == 200
    assert json.loads(minted.body)["token"] == app_token(AUTH_SECRET, "altra")
    assert minted.headers.get("Cache-Control") == "no-store"


def test_the_websocket_refuses_the_app_token(tmp_path, monkeypatch) -> None:
    from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
    from jenny.config.loader import save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context
    from jenny.webui.gateway_services import build_gateway_services

    workspace = tmp_path / "ws"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: workspace)
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    for requires in (True, False):
        cfg = {"enabled": True, "allowFrom": ["*"], "websocketRequiresToken": requires,
               "tokenIssueSecret": "s3cr3t"}
        gateway = build_gateway_services(
            config=WebSocketConfig.model_validate(cfg), bus=bus, session_manager=None,
            workspace_path=workspace, default_restrict_to_workspace=False,
            runtime_model_name=None,
        )
        channel = WebSocketChannel(cfg, bus, gateway=gateway)
        conn = MagicMock()
        conn.respond = MagicMock(return_value="401")
        verdict = channel._authorize_websocket_handshake(
            conn, {"token": [app_token("s3cr3t", "note")]}
        )
        assert verdict == "401", requires
        assert conn not in channel._conn_authed


# ── La SPA non passa più il segreto alla cornice ─────────────────────────────


def test_the_frame_carries_the_app_token_not_the_gateway_secret() -> None:
    actions = (ASSETS / "shared" / "apps-actions.js").read_text(encoding="utf-8")
    frame = actions[actions.index("export function frameForApp("):]
    frame = frame[: frame.index("\n}\n")]
    assert "getSecret" not in frame, "la cornice di un'app non riceve il segreto del gateway"
    assert "?token=${encodeURIComponent(token || '')}" in frame
    assert "await api.appToken(slug)" in actions
    assert "frameForApp(slug, { overlay: true, token })" in actions

    pages = (ASSETS / "home-pages.js").read_text(encoding="utf-8")
    assert "await api.appToken(page.ref)" in pages
    assert "frameForApp(page.ref, { token })" in pages

    client = (ASSETS / "shared" / "api-client.js").read_text(encoding="utf-8")
    assert "/api/webui/apps/${encodeURIComponent(slug)}/token" in client
