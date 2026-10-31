"""Route HTTP ``/api/work``: i lavori lunghi di una conversazione.

Serve **verbatim** i payload di ``jenny/runtime/work_status.py``, gli stessi che
il canale WebSocket manda live come frame ``work_status``: una sola forma, due
trasporti, come ``/api/subagents``. Il GET e' cio' che fa sopravvivere la bolla
di lavoro della home a un reload o a una riconnessione: su Android il processo
della WebView muore spesso, e una passata del giardiniere dura minuti.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import http_error, http_json_response, parse_query, query_first
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID


def chat_id_for_session_key(raw: str | None) -> str | None:
    """Il ``chat_id`` del WebSocket per la chiave che manda la home.

    Le due forme di ``shared/session-manager.js``: ``websocket:default`` (la
    chat personale, che sul filo e' ``default``) e ``project:<nome>``, che sul
    filo e' se stessa. Qualunque altra cosa non e' una conversazione della home.
    """
    key = unquote(raw or "").strip()
    if not key:
        return None
    if key == f"websocket:{WEBUI_DEFAULT_CHAT_ID}":
        return WEBUI_DEFAULT_CHAT_ID
    if key.startswith("project:") and len(key) > len("project:"):
        return key
    return None


class WorkRoutes:
    """Route ``GET /api/work?session_key=``."""

    def __init__(self, *, check_api_token: Callable[[WsRequest], bool], log: Any = logger) -> None:
        self._check_api_token = check_api_token
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path != "/api/work":
            return None
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        chat_id = chat_id_for_session_key(query_first(parse_query(request.path), "session_key"))
        if chat_id is None:
            # Senza conversazione non c'e' nessuna bolla da rimettere: lista vuota,
            # non un errore (la home si limita a non disegnare niente).
            return http_json_response({"work": []})
        try:
            from jenny.runtime.work_status import work_snapshot

            return http_json_response({"work": work_snapshot(chat_id)})
        except Exception:
            self._log.exception("Work snapshot failed")
            return http_error(500, "work snapshot failed")
