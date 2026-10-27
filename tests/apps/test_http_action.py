"""Tests for the Jenny Apps http action executor (proxy leg)."""

from __future__ import annotations

import pytest

import jenny.apps.http as apps_http
from jenny.apps.http import (
    HttpActionError,
    build_request,
    execute_http_action,
)
from jenny.apps.manifest import AppAction, AppManifest


def _manifest(base_url="http://192.168.1.50:8080", auth=None) -> AppManifest:
    return AppManifest(name="Piante", description="x",
                       server_base_url=base_url, server_auth=auth)


def _http_action(method="GET", path="/plants", params=None, required=None) -> AppAction:
    return AppAction(name="a", description="t", kind="http", method=method, path=path,
                     params=params or {}, required=required or [])


class TestBuildRequest:
    def test_placeholders_are_url_encoded(self):
        action = _http_action(path="/plants/{id}/humidity",
                              params={"id": {"type": "string"}})
        method, url, query, body = build_request(
            _manifest(), action, {"id": "basilico verde/1"}
        )
        assert url == "http://192.168.1.50:8080/plants/basilico%20verde%2F1/humidity"
        assert query == {}
        assert body is None

    def test_get_leftovers_become_query(self):
        action = _http_action(params={"q": {"type": "string"}})
        _, _, query, body = build_request(_manifest(), action, {"q": "rose"})
        assert query == {"q": "rose"}
        assert body is None

    def test_post_leftovers_become_body(self):
        action = _http_action(method="POST", path="/plants/{id}/water",
                              params={"id": {"type": "string"},
                                      "ml": {"type": "integer"}})
        method, url, query, body = build_request(
            _manifest(), action, {"id": "b1", "ml": 200}
        )
        assert method == "POST"
        assert url.endswith("/plants/b1/water")
        assert query == {}
        assert body == {"ml": 200}

    def test_missing_placeholder_param(self):
        action = _http_action(path="/plants/{id}", params={"id": {"type": "string"}})
        with pytest.raises(HttpActionError):
            build_request(_manifest(), action, {})

    def test_base_url_trailing_slash(self):
        _, url, _, _ = build_request(_manifest("http://x.lan:1/"), _http_action(), {})
        assert url == "http://x.lan:1/plants"


class _FakeResponse:
    def __init__(self, chunks, status=200, content_type="application/json"):
        self._chunks = chunks
        self.status_code = status
        self.is_success = 200 <= status < 300
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeStream:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    last = None

    def __init__(self, **kwargs):
        _FakeClient.last = self
        self.kwargs = kwargs
        self.calls = []
        self.response = _FakeResponse([b'{"plants": []}'])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, params=None, json=None, headers=None):
        self.calls.append((method, url, params, json))
        self.headers = headers
        return _FakeStream(self.response)


class TestExecuteHttpAction:
    async def test_blocked_target_never_connects(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            apps_http, "validate_app_server_target",
            lambda url: (seen.append(url), (False, "blocked test"))[1],
        )
        monkeypatch.setattr(apps_http.httpx, "AsyncClient", _FakeClient)
        _FakeClient.last = None
        with pytest.raises(HttpActionError) as e:
            await execute_http_action("piante", _manifest(), _http_action(), {})
        assert e.value.status == 403
        assert seen  # SSRF check ran before any client was built
        assert _FakeClient.last is None

    async def test_success_parses_json(self, monkeypatch):
        monkeypatch.setattr(apps_http, "validate_app_server_target", lambda url: (True, ""))
        monkeypatch.setattr(apps_http.httpx, "AsyncClient", _FakeClient)
        result = await execute_http_action("piante", _manifest(), _http_action(), {})
        assert result == {"ok": True, "status": 200, "data": {"plants": []}}
        assert _FakeClient.last.kwargs["follow_redirects"] is False
        # Un nome con recapito: Wikimedia risponde 403 a ``python-httpx``.
        assert _FakeClient.last.headers["User-Agent"].startswith("Jenny/")

    async def test_response_size_cap(self, monkeypatch):
        monkeypatch.setattr(apps_http, "validate_app_server_target", lambda url: (True, ""))
        monkeypatch.setattr(apps_http.httpx, "AsyncClient", _FakeClient)

        original_init = _FakeClient.__init__

        def big_init(self, **kwargs):
            original_init(self, **kwargs)
            self.response = _FakeResponse([b"x" * (600 * 1024)], content_type="text/plain")

        monkeypatch.setattr(_FakeClient, "__init__", big_init)
        with pytest.raises(HttpActionError) as e:
            await execute_http_action("piante", _manifest(), _http_action(), {})
        assert e.value.status == 502

    async def test_declared_auth_is_refused_fail_closed(self, monkeypatch):
        """Fase 4.3: un manifest che dichiara server.auth viene RIFIUTATO (501)
        finché non c'è un credential store — niente chiamata anonima."""
        called = {"n": 0}

        class _TrackingClient(_FakeClient):
            def __init__(self, **kwargs):
                called["n"] += 1
                super().__init__(**kwargs)

        monkeypatch.setattr(apps_http, "validate_app_server_target", lambda url: (True, ""))
        monkeypatch.setattr(apps_http.httpx, "AsyncClient", _TrackingClient)
        manifest = _manifest(auth={"secretRef": "tok"})
        with pytest.raises(HttpActionError) as e:
            await execute_http_action("conauth", manifest, _http_action(), {})
        assert e.value.status == 501
        assert called["n"] == 0  # nessuna richiesta di rete effettuata

    async def test_declared_auth_refused_before_target_check(self, monkeypatch):
        """Il rifiuto auth precede (e non dipende da) la validazione SSRF."""
        monkeypatch.setattr(
            apps_http, "validate_app_server_target", lambda url: (False, "blocked test")
        )
        monkeypatch.setattr(apps_http.httpx, "AsyncClient", _FakeClient)
        manifest = _manifest(auth={"secretRef": "tok"})
        with pytest.raises(HttpActionError) as e:
            await execute_http_action("blockedauth", manifest, _http_action(), {})
        assert e.value.status == 501
