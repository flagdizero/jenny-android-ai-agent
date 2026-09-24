"""Tests for the Jenny Apps gateway routes (list, actions, static)."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support.gateway_http import AUTH_SECRET, make_handler, make_request
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler

NOTE_MANIFEST = {
    "name": "Note",
    "description": "Note veloci",
    "icon": "ti-notes",
    "actions": [
        {"name": "add_note", "description": "Aggiunge", "kind": "storage",
         "op": "append", "collection": "notes",
         "params": {"testo": {"type": "string"}}, "required": ["testo"]},
        {"name": "list_notes", "description": "Elenca", "kind": "storage",
         "op": "query", "collection": "notes"},
    ],
}


def _make_request(path: str, token: str | None = AUTH_SECRET) -> WsRequest:
    return make_request(path, token)


def _make_handler(tmp_path: Path) -> GatewayHTTPHandler:
    return make_handler(tmp_path / "skills")


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    app_dir = workspace / "apps" / "note"
    (app_dir / "app").mkdir(parents=True)
    (app_dir / "data").mkdir()
    (app_dir / "app.json").write_text(json.dumps(NOTE_MANIFEST), encoding="utf-8")
    (app_dir / "app" / "index.html").write_text(
        "<!DOCTYPE html><body>note</body>", encoding="utf-8"
    )
    (app_dir / "data" / "notes.jsonl").write_text('{"id": "x", "testo": "segreta"}\n')
    broken_dir = workspace / "apps" / "rotta"
    broken_dir.mkdir(parents=True)
    (broken_dir / "app.json").write_text("{nope", encoding="utf-8")
    return workspace


def _action_path(slug: str, action: str, params: dict | None = None) -> str:
    raw = urllib.parse.quote(json.dumps(params or {}))
    return f"/api/apps/{slug}/actions/{action}?params={raw}"


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class TestAppsList:
    async def test_list_includes_valid_and_broken(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = handler.apps_routes._list(_make_request("/api/webui/apps"))
        assert response.status_code == 200
        apps = {a["slug"]: a for a in _body(response)["apps"]}
        assert apps["note"]["name"] == "Note"
        assert apps["note"]["broken"] is False
        assert apps["rotta"]["broken"] is True
        assert "invalid JSON" in apps["rotta"]["error"]

    async def test_list_requires_token(self, tmp_path):
        handler = _make_handler(tmp_path)
        response = handler.apps_routes._list(_make_request("/api/webui/apps", token=None))
        assert response.status_code == 401


class TestAppAction:
    async def test_action_executes_and_sets_cors(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = await handler.apps_routes._action(
                _make_request(_action_path("note", "add_note", {"testo": "ciao"})),
                "note", "add_note",
            )
        assert response.status_code == 200
        assert response.headers.get("Access-Control-Allow-Origin") == "*"
        assert response.headers.get("Cache-Control") == "no-store"
        payload = _body(response)
        assert payload["ok"] is True
        assert payload["record"]["testo"] == "ciao"
        stored = (workspace / "apps" / "note" / "data" / "notes.jsonl").read_text()
        assert "ciao" in stored

    async def test_errors_carry_cors_headers(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            unknown_app = await handler.apps_routes._action(
                _make_request(_action_path("manca", "x")), "manca", "x")
            unknown_action = await handler.apps_routes._action(
                _make_request(_action_path("note", "nope")), "note", "nope")
            bad_params = await handler.apps_routes._action(
                _make_request("/api/apps/note/actions/add_note?params=%7Bnot"),
                "note", "add_note")
            invalid_params = await handler.apps_routes._action(
                _make_request(_action_path("note", "add_note", {})), "note", "add_note")
        assert unknown_app.status_code == 404
        assert unknown_action.status_code == 404
        assert bad_params.status_code == 400
        assert invalid_params.status_code == 400
        for response in (unknown_app, unknown_action, bad_params, invalid_params):
            assert response.headers.get("Access-Control-Allow-Origin") == "*"
            assert _body(response)["ok"] is False

    async def test_action_401_without_token(self, tmp_path):
        handler = _make_handler(tmp_path)
        response = await handler.apps_routes._action(
            _make_request(_action_path("note", "add_note"), token=None), "note", "add_note")
        assert response.status_code == 401
        assert response.headers.get("Access-Control-Allow-Origin") == "*"

    async def test_action_400_on_bad_slug_or_action(self, tmp_path):
        handler = _make_handler(tmp_path)
        r1 = await handler.apps_routes._action(
            _make_request("/api/apps/Bad_Slug/actions/x?params=%7B%7D"), "Bad_Slug", "x")
        r2 = await handler.apps_routes._action(
            _make_request("/api/apps/note/actions/Bad-Action?params=%7B%7D"),
            "note", "Bad-Action")
        assert r1.status_code == 400
        assert r2.status_code == 400

    async def test_action_413_on_oversized_params(self, tmp_path):
        handler = _make_handler(tmp_path)
        big = urllib.parse.quote(json.dumps({"testo": "x" * 7000}))
        response = await handler.apps_routes._action(
            _make_request(f"/api/apps/note/actions/add_note?params={big}"),
            "note", "add_note")
        assert response.status_code == 413

    async def test_broken_app_action_is_structured_error(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = await handler.apps_routes._action(
                _make_request(_action_path("rotta", "x")), "rotta", "x")
        assert response.status_code == 409
        assert "broken" in _body(response)["error"]


class TestAppStatic:
    async def test_serves_index_with_no_store(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = handler.apps_routes._static(
                _make_request(f"/apps/note/index.html?token={AUTH_SECRET}"), "/apps/note/index.html")
        assert response.status_code == 200
        assert b"note" in response.body
        assert response.headers.get("Cache-Control") == "no-store"
        assert "text/html" in response.headers.get("Content-Type", "")

    async def test_default_document_is_index(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = handler.apps_routes._static(_make_request("/apps/note"), "/apps/note")
        assert response.status_code == 200

    async def test_static_requires_token(self, tmp_path):
        handler = _make_handler(tmp_path)
        response = handler.apps_routes._static(
            _make_request("/apps/note/index.html", token=None), "/apps/note/index.html")
        assert response.status_code == 401

    async def test_traversal_blocked(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        (workspace / "secret.txt").write_text("s")
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = handler.apps_routes._static(
                _make_request("/apps/note/../../secret.txt"), "/apps/note/../../secret.txt")
        assert response.status_code == 403

    async def test_no_spa_fallback_under_apps(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = handler.apps_routes._static(
                _make_request("/apps/note/missing.js"), "/apps/note/missing.js")
        assert response.status_code == 404

    async def test_manifest_agentmd_and_data_not_web_served(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            for rel in ("app.json", "AGENT.md", "data/notes.jsonl"):
                response = handler.apps_routes._static(
                    _make_request(f"/apps/note/{rel}"), f"/apps/note/{rel}")
                assert response.status_code == 404, rel

    async def test_invalid_slug_rejected(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = handler.apps_routes._static(
                _make_request("/apps/Bad_Slug/index.html"), "/apps/Bad_Slug/index.html")
        assert response.status_code == 400


class TestAppsGateFailsClosed:
    """Un errore di config non deve scavalcare una feature dichiarata spenta.

    ``_check_apps_enabled`` faceva ``except Exception: pass``, e quel gate
    protegge anche ``_static``, che serve byte arbitrari da
    ``workspace/apps/<slug>/app/``. Il gemello in
    ``workspace_routes._require_workspace_flag`` risponde 503 nello stesso caso,
    con una docstring che spiega perché deve.
    """

    async def test_a_broken_config_refuses_instead_of_passing(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)

        def boom():
            raise ValueError("config.json is not valid JSON")

        with patch("jenny.config.loader.load_config", side_effect=boom), \
                patch.object(handler, "_get_workspace_root", return_value=workspace):
            static = handler.apps_routes._static(
                _make_request("/apps/note/index.html"), "/apps/note/index.html")
            listing = handler.apps_routes._list(_make_request("/api/webui/apps"))

        assert static.status_code == 503
        assert listing.status_code == 503
        # Il messaggio dell'eccezione non deve finire nel corpo.
        assert b"not valid JSON" not in static.body

    async def test_the_disabled_flag_still_says_disabled(self, tmp_path):
        """Il caso spento resta distinguibile da quello illeggibile."""
        handler = _make_handler(tmp_path)
        apps_off = SimpleNamespace(enabled=False, http_timeout_s=20.0, max_collection_bytes=1)

        with patch("jenny.config.loader.load_config",
                   return_value=SimpleNamespace(apps=apps_off)):
            response = handler.apps_routes._list(_make_request("/api/webui/apps"))

        assert response.status_code == 503
        assert b"disabled" in response.body


VIEW_MANIFEST = {
    "name": "Telecomando",
    "description": "Il telecomando di hps",
    "icon": "ti-device-tv",
    "server": {"baseUrl": "http://hps:8091"},
    "view": {"kind": "external"},
}


def _add_view_app(workspace: Path, slug: str = "telecomando", manifest=VIEW_MANIFEST) -> None:
    """Una app a vista esterna: nessun app/index.html, e' il punto."""
    app_dir = workspace / "apps" / slug
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")


class TestExternalView:
    """La route che apre il proxy su loopback per una vista esterna.

    Il proxy vero e proprio ha i suoi test in ``tests/apps/test_proxy.py``; qui
    si guarda solo il contratto della route e il ciclo di vita del listener.
    """

    async def test_view_returns_a_loopback_url(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        _add_view_app(workspace)
        with patch.object(handler, "_get_workspace_root", return_value=workspace), \
             patch("jenny.apps.proxy.validate_app_server_target", return_value=(True, "")):
            response = await handler.apps_routes._view(
                _make_request("/api/webui/apps/telecomando/view"), "telecomando")
            try:
                assert response.status_code == 200
                url = _body(response)["url"]
                # L'URL deve stare su loopback: e' l'intero motivo del proxy —
                # la policy cleartext dell'APK permette solo questo.
                assert url.startswith("http://127.0.0.1:")
                # ...e portare la capability nel path della prima navigazione.
                assert url.rstrip("/").rsplit("/", 1)[-1]
            finally:
                await handler.apps_routes._view_close(
                    _make_request("/api/webui/apps/telecomando/view/close"), "telecomando")

    async def test_reopening_reuses_the_same_listener(self, tmp_path):
        """Riaprire non deve lasciare un listener per apertura."""
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        _add_view_app(workspace)
        with patch.object(handler, "_get_workspace_root", return_value=workspace), \
             patch("jenny.apps.proxy.validate_app_server_target", return_value=(True, "")):
            try:
                first = _body(await handler.apps_routes._view(
                    _make_request("/api/webui/apps/telecomando/view"), "telecomando"))["url"]
                second = _body(await handler.apps_routes._view(
                    _make_request("/api/webui/apps/telecomando/view"), "telecomando"))["url"]
                assert first == second
                assert len(handler.apps_routes._view_proxies) == 1
            finally:
                await handler.apps_routes._view_close(
                    _make_request("/api/webui/apps/telecomando/view/close"), "telecomando")

    async def test_close_drops_the_proxy(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        _add_view_app(workspace)
        with patch.object(handler, "_get_workspace_root", return_value=workspace), \
             patch("jenny.apps.proxy.validate_app_server_target", return_value=(True, "")):
            await handler.apps_routes._view(
                _make_request("/api/webui/apps/telecomando/view"), "telecomando")
            assert handler.apps_routes._view_proxies
            response = await handler.apps_routes._view_close(
                _make_request("/api/webui/apps/telecomando/view/close"), "telecomando")
        assert response.status_code == 200
        assert handler.apps_routes._view_proxies == {}

    async def test_a_normal_app_has_no_external_view(self, tmp_path):
        """`note` non dichiara `view`: la route non deve aprirle un proxy."""
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = await handler.apps_routes._view(
                _make_request("/api/webui/apps/note/view"), "note")
        assert response.status_code == 400
        assert handler.apps_routes._view_proxies == {}

    async def test_blocked_target_is_refused_not_bound(self, tmp_path):
        """Il cancello SSRF vero: nessun listener per un target rifiutato."""
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        _add_view_app(workspace, "loop", {
            **VIEW_MANIFEST, "server": {"baseUrl": "http://127.0.0.1:9"},
        })
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            response = await handler.apps_routes._view(
                _make_request("/api/webui/apps/loop/view"), "loop")
        assert response.status_code == 403
        assert b"blocked server target" in response.body
        assert handler.apps_routes._view_proxies == {}

    async def test_view_requires_a_token(self, tmp_path):
        handler = _make_handler(tmp_path)
        response = await handler.apps_routes._view(
            _make_request("/api/webui/apps/telecomando/view", token=None), "telecomando")
        assert response.status_code == 401

    async def test_view_is_routed_by_dispatch(self, tmp_path):
        """Le due route devono essere raggiungibili, non solo esistere."""
        handler = _make_handler(tmp_path)
        workspace = _make_workspace(tmp_path)
        _add_view_app(workspace)
        with patch.object(handler, "_get_workspace_root", return_value=workspace), \
             patch("jenny.apps.proxy.validate_app_server_target", return_value=(True, "")):
            try:
                opened = await handler.apps_routes.dispatch(
                    _make_request("/api/webui/apps/telecomando/view"),
                    "/api/webui/apps/telecomando/view")
                assert opened is not None and opened.status_code == 200
                closed = await handler.apps_routes.dispatch(
                    _make_request("/api/webui/apps/telecomando/view/close"),
                    "/api/webui/apps/telecomando/view/close")
                assert closed is not None and closed.status_code == 200
            finally:
                await handler.apps_routes._view_close(
                    _make_request("/api/webui/apps/telecomando/view/close"), "telecomando")
