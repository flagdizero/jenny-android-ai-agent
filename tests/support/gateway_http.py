"""Un ``GatewayHTTPHandler`` coi collaboratori finti, e le richieste per lui.

I test delle route ``/api/`` costruivano l'handler con le stesse quindici righe
— una ``config`` minima, bus, media e workspace finti, un modello qualunque —
e la richiesta con il token accodato alla query. Qui una volta; ogni test
passa quel che il suo gruppo di route vuole in più (``get_cron_service``,
``get_subagent_manager``, ``disabled_skills``…).
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler

AUTH_SECRET = "test-secret"


def make_request(
    path: str,
    token: str | None = AUTH_SECRET,
    headers: list[tuple[str, str]] | None = None,
    *,
    always_append: bool = False,
) -> WsRequest:
    """La richiesta per *path*, col token in query.

    Di norma il token non si accoda se il path ne porta già uno (così un test
    può mandarne uno sbagliato); con *always_append* si accoda comunque, come
    facevano i test delle route skill e subagent.
    """
    if token is not None and (always_append or "token=" not in path):
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path, headers=Headers(headers or []))


def make_handler(skills_workspace_path: Path, **extra: Any) -> GatewayHTTPHandler:
    """L'handler con la ``config`` minima dei test e i collaboratori finti.

    *extra* aggiunge collaboratori o **sostituisce** quelli finti: chi vuole il
    ``WebUIWorkspaceController`` vero passa ``workspaces=...``.
    """
    config = SimpleNamespace(
        workspace=SimpleNamespace(enabled=True),
        wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
        token_issue_secret=AUTH_SECRET,
        verbose=False,
    )
    kwargs: dict[str, Any] = {
        "config": config,
        "session_manager": None,
        "runtime_model_name": lambda: "test-model",
        "bus": MagicMock(),
        "media": MagicMock(),
        "workspaces": MagicMock(),
        "skills_workspace_path": skills_workspace_path,
    }
    kwargs.update(extra)
    return GatewayHTTPHandler(**kwargs)
