"""Test delle route ``/api/subagents*`` (SubagentRoutes).

Stesso pattern di ``tests/webui/test_skills_routes.py``: un ``GatewayHTTPHandler``
reale con dipendenze finte, e il dispatch su ``handler.subagent_routes`` con il
path già ripulito dalla query (la query viene letta da ``request.path``).

Il manager è un doppio: queste route non devono conoscere ``jenny/agent``, e i
suoi errori sono riconosciuti per nome di classe — quindi il doppio solleva
eccezioni con quei nomi, senza importare nulla dall'agente.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler

_AUTH_SECRET = "test-secret"

_SNAPSHOT = {
    "running": [{
        "task_id": "d2ee4342",
        "lineage_id": "aa94c60b",
        "attempt": 1,
        "label": "fix parser",
        "task": "fix the parser so it accepts trailing commas",
        "agent_type": "coder",
        "state": "running",
        "phase": "awaiting_tools",
        "iteration": 2,
        "elapsed_s": 0.0,
        "idle_s": 0.0,
        "last_tool": "grep",
        "tool_events": [{"name": "grep", "status": "ok", "detail": "3 matches"}],
    }],
    "recent": [{
        "task_id": "822ead40",
        "lineage_id": "b202f4e6",
        "attempt": 1,
        "label": "price research",
        "task": "find the current price of a Titan 2",
        "agent_type": "researcher",
        "state": "failed",
        "stop_reason": "error",
        # Presente su ogni voce di ``recent``, anche non cancellata: la route
        # serve lo snapshot verbatim, quindi la forma va pinnata per intero.
        "cancel_reason": None,
        "result_summary": "page not reachable",
        "ended_at": 1785841304.462998,
        "can_restart": True,
    }],
}


# Nomi identici a quelli di jenny/agent/subagent.py: la mappa nome→status di
# SubagentRoutes è il contratto, e questi doppi lo esercitano senza importare
# l'agente (che è esattamente il vincolo di layering della route).
class SubagentRestartError(RuntimeError):
    pass


class SubagentConcurrencyLimitError(RuntimeError):
    pass


class FakeManager:
    """Doppio del SubagentManager con la sola superficie usata dalle route."""

    def __init__(self, *, snapshot=None) -> None:
        self._snapshot = _SNAPSHOT if snapshot is None else snapshot
        self.snapshot_calls: list = []
        self.restart_calls: list = []
        self.cancel_calls: list = []
        self.restart_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.cancel_result = True

    def status_snapshot(self, session_key=None):
        self.snapshot_calls.append(session_key)
        return self._snapshot

    async def restart(self, target_id, **kwargs):
        self.restart_calls.append((target_id, kwargs))
        if self.restart_error is not None:
            raise self.restart_error
        return "new1234"

    async def cancel_task(self, task_id):
        self.cancel_calls.append(task_id)
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_result


def _make_request(path_with_query: str, *, token: str | None = _AUTH_SECRET) -> WsRequest:
    if token is not None:
        sep = "&" if "?" in path_with_query else "?"
        path_with_query = f"{path_with_query}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path_with_query, headers=Headers())


async def _dispatch(handler, path_with_query: str, *, token: str | None = _AUTH_SECRET):
    clean_path = path_with_query.split("?", 1)[0]
    request = _make_request(path_with_query, token=token)
    return await handler.subagent_routes.dispatch(request, clean_path)


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _make_handler(get_manager) -> GatewayHTTPHandler:
    config = SimpleNamespace(
        workspace=SimpleNamespace(enabled=True),
        wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
        token_issue_secret=_AUTH_SECRET,
        verbose=False,
    )
    return GatewayHTTPHandler(
        config=config,
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=MagicMock(),
        skills_workspace_path=Path("/nonexistent"),
        get_subagent_manager=get_manager,
    )


@pytest.fixture()
def env():
    manager = FakeManager()
    handler = _make_handler(lambda: manager)
    return SimpleNamespace(handler=handler, manager=manager)


# -- routing -----------------------------------------------------------------


async def test_dispatch_ignores_unrelated_paths(env) -> None:
    assert await _dispatch(env.handler, "/api/other") is None
    assert await _dispatch(env.handler, "/api/subagents-extra") is None
    assert await _dispatch(env.handler, "/api/subagents/abc/unknown") is None


# -- auth --------------------------------------------------------------------


async def test_snapshot_requires_token(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents", token=None)
    assert response.status_code == 401
    assert env.manager.snapshot_calls == []


async def test_restart_requires_token(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/d2ee4342/restart", token=None)
    assert response.status_code == 401
    assert env.manager.restart_calls == []


async def test_cancel_requires_token(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/d2ee4342/cancel", token=None)
    assert response.status_code == 401
    assert env.manager.cancel_calls == []


async def test_wrong_token_is_rejected(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents", token="not-the-secret")
    assert response.status_code == 401


# -- GET /api/subagents ------------------------------------------------------


async def test_snapshot_is_served_verbatim(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents")
    assert response.status_code == 200
    # Una sola forma, due trasporti: nessuna riscrittura di chiavi o annidamenti.
    assert _json(response) == _SNAPSHOT


async def test_snapshot_defaults_to_no_session_filter(env) -> None:
    await _dispatch(env.handler, "/api/subagents")
    assert env.manager.snapshot_calls == [None]


async def test_snapshot_translates_webui_session_key(env) -> None:
    from jenny.session.keys import UNIFIED_SESSION_KEY

    await _dispatch(env.handler, "/api/subagents?session_key=websocket%3Adefault")
    assert env.manager.snapshot_calls == [UNIFIED_SESSION_KEY]


async def test_snapshot_passes_through_other_session_keys(env) -> None:
    await _dispatch(env.handler, "/api/subagents?session_key=cron%3Anightly")
    assert env.manager.snapshot_calls == ["cron:nightly"]


async def test_snapshot_without_manager_is_empty_not_an_error() -> None:
    handler = _make_handler(lambda: None)
    response = await _dispatch(handler, "/api/subagents")
    assert response.status_code == 200
    assert _json(response) == {"running": [], "recent": []}


async def test_snapshot_without_getter_is_empty() -> None:
    handler = _make_handler(None)
    response = await _dispatch(handler, "/api/subagents")
    assert response.status_code == 200
    assert _json(response) == {"running": [], "recent": []}


async def test_snapshot_failure_maps_to_500_generic(env) -> None:
    def boom(_session_key=None):
        raise RuntimeError("dettaglio interno")

    env.manager.status_snapshot = boom
    response = await _dispatch(env.handler, "/api/subagents")
    assert response.status_code == 500
    assert b"dettaglio interno" not in response.body


async def test_snapshot_of_wrong_type_degrades_to_empty(env) -> None:
    env.manager.status_snapshot = lambda _session_key=None: ["not", "a", "dict"]
    response = await _dispatch(env.handler, "/api/subagents")
    assert response.status_code == 200
    assert _json(response) == {"running": [], "recent": []}


# -- restart -----------------------------------------------------------------


async def test_restart_is_always_manual(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/822ead40/restart")
    assert response.status_code == 200
    assert _json(response) == {"restarted": True, "task_id": "new1234"}
    # manual=True: il tetto dei tentativi automatici non rifiuta mai un umano.
    assert env.manager.restart_calls == [("822ead40", {"manual": True})]


async def test_restart_accepts_a_lineage_id(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/b202f4e6/restart")
    assert response.status_code == 200
    assert env.manager.restart_calls[0][0] == "b202f4e6"


async def test_restart_error_is_a_clean_409(env) -> None:
    env.manager.restart_error = SubagentRestartError("unknown subagent or lineage: nope")
    response = await _dispatch(env.handler, "/api/subagents/nope/restart")
    assert response.status_code == 409
    assert b"unknown subagent or lineage" in response.body


async def test_concurrency_limit_is_a_clean_429(env) -> None:
    env.manager.restart_error = SubagentConcurrencyLimitError(
        "concurrency limit reached (5/5 running)"
    )
    response = await _dispatch(env.handler, "/api/subagents/822ead40/restart")
    assert response.status_code == 429
    assert b"concurrency limit reached" in response.body


async def test_unexpected_restart_error_maps_to_500_generic(env) -> None:
    env.manager.restart_error = ValueError("dettaglio interno")
    response = await _dispatch(env.handler, "/api/subagents/822ead40/restart")
    assert response.status_code == 500
    assert b"dettaglio interno" not in response.body


async def test_unmapped_runtime_error_maps_to_500_generic(env) -> None:
    # Un RuntimeError qualsiasi non è un errore del contratto subagent: 500.
    env.manager.restart_error = RuntimeError("dettaglio interno")
    response = await _dispatch(env.handler, "/api/subagents/822ead40/restart")
    assert response.status_code == 500
    assert b"dettaglio interno" not in response.body


async def test_restart_without_manager_is_503() -> None:
    handler = _make_handler(lambda: None)
    response = await _dispatch(handler, "/api/subagents/822ead40/restart")
    assert response.status_code == 503


# -- cancel ------------------------------------------------------------------


async def test_cancel_happy_path(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/d2ee4342/cancel")
    assert response.status_code == 200
    assert _json(response) == {"cancelled": True}
    assert env.manager.cancel_calls == ["d2ee4342"]


async def test_cancel_reports_a_miss_without_failing(env) -> None:
    env.manager.cancel_result = False
    response = await _dispatch(env.handler, "/api/subagents/d2ee4342/cancel")
    assert response.status_code == 200
    assert _json(response) == {"cancelled": False}


async def test_cancel_unexpected_error_maps_to_500_generic(env) -> None:
    env.manager.cancel_error = ValueError("dettaglio interno")
    response = await _dispatch(env.handler, "/api/subagents/d2ee4342/cancel")
    assert response.status_code == 500
    assert b"dettaglio interno" not in response.body


# -- validazione dell'id -----------------------------------------------------


@pytest.mark.parametrize("raw_id", ["a%2Fb", "a%5Cb", "with%20space", "a" * 65, "abc%0A"])
async def test_invalid_ids_are_rejected(env, raw_id: str) -> None:
    for action in ("restart", "cancel"):
        response = await _dispatch(env.handler, f"/api/subagents/{raw_id}/{action}")
        assert response.status_code == 400, (raw_id, action)
    assert env.manager.restart_calls == []
    assert env.manager.cancel_calls == []


# -- integrazione col dispatch principale ------------------------------------


async def test_routes_are_reachable_from_the_main_dispatch(env) -> None:
    request = _make_request("/api/subagents")
    response = await env.handler._dispatch_misc_routes(MagicMock(), request, "/api/subagents")
    assert response is not None and response.status_code == 200
    assert _json(response) == _SNAPSHOT
