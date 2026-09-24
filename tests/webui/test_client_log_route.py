"""Test per l'endpoint /api/client-log (inoltro errori WebUI nel log gateway)."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from loguru import logger
from support.gateway_http import AUTH_SECRET, make_handler, make_request
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler


def _make_request(path: str, token: str | None = AUTH_SECRET) -> WsRequest:
    return make_request(path, token)


def _make_handler(tmp_path: Path) -> GatewayHTTPHandler:
    return make_handler(tmp_path / "skills")


def test_client_log_writes_gateway_log(tmp_path):
    handler = _make_handler(tmp_path)
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        request = _make_request(
            "/api/client-log?level=warning&source=onboarding-model-list"
            "&message=" + urllib.parse.quote("list collapsed to 2px")
        )
        response = handler._handle_client_log(request)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200
    assert b'"ok": true' in response.body
    joined = "".join(records)
    assert "[webui-client]" in joined
    assert "onboarding-model-list" in joined
    assert "list collapsed to 2px" in joined


def test_client_log_requires_token(tmp_path):
    handler = _make_handler(tmp_path)
    request = _make_request("/api/client-log?level=error&message=x", token=None)
    response = handler._handle_client_log(request)
    assert response.status_code == 401


def test_client_log_sanitizes_level_and_truncates(tmp_path):
    handler = _make_handler(tmp_path)
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        # Livello fuori whitelist -> forzato a error; messaggio oltre 800 troncato.
        long_message = "x" * 2000
        request = _make_request(
            "/api/client-log?level=CRITICAL&source=" + "s" * 300
            + "&message=" + long_message
        )
        response = handler._handle_client_log(request)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200
    joined = "".join(records)
    assert "ERROR" in joined
    assert "x" * 800 in joined
    assert "x" * 801 not in joined
    assert "s" * 100 in joined
    assert "s" * 101 not in joined


def test_client_log_survives_missing_params(tmp_path):
    """Parametri assenti: 200, non 500.

    ``_query_first`` restituisce ``None`` quando il parametro manca, e
    affettare ``None`` solleva ``TypeError``. La rotta che esiste per
    *segnalare* i guasti del client falliva quindi con un 500 proprio sulla
    richiesta malformata — il caso in cui serve di più. ``level`` aveva già il
    suo default, ``source`` e ``message`` no.
    """
    handler = _make_handler(tmp_path)
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        # Nessun source, nessun message: solo il token di autenticazione.
        response = handler._handle_client_log(_make_request("/api/client-log"))
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200
    assert "unknown" in "".join(records)
