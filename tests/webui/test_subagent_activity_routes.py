"""Route di risincronizzazione: ``/api/subagents/{id}/{activity,digest}``.

Perché esistono, e cosa questi test difendono: su Android il processo della
WebView muore spesso, e alla riapertura il client ha un cursore ma non gli
eventi. La route deve restituire la finestra **verbatim** — stessi nomi di campo
del frame WebSocket — con ``since`` onorato e ``gap`` che sopravvive al filo:
senza quest'ultimo il percorso pensato per chiudere un buco ne aprirebbe uno.

Stesso pattern di ``test_subagent_routes.py``: handler reale, manager doppio, e
nessun import di ``jenny/agent`` dalla parte della route. Il log invece è quello
vero, perché è lui a produrre ``seq``, ``dropped`` e ``gap``.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from support.gateway_http import AUTH_SECRET, make_handler, make_request
from websockets.http11 import Request as WsRequest

from jenny.agent.subagent_activity import SubagentActivityLog, build_digest
from jenny.channels.subagent_activity_wire import MAX_HTTP_EVENTS
from jenny.webui.ws_http import GatewayHTTPHandler

_TASK = "d2ee4342"


class FakeDigestStore:
    """Doppio di ``SubagentDigestStore``: solo ``load``, come la route usa."""

    def __init__(self, events: Any = None) -> None:
        self.events = [] if events is None else events
        self.calls: list[str] = []
        self.error: Exception | None = None

    def load(self, task_id: str) -> Any:
        self.calls.append(task_id)
        if self.error is not None:
            raise self.error
        return self.events


class FakeManager:
    """Manager con la superficie di telemetria delle fasi 1-2."""

    def __init__(self, *, activity: Any = None, digests: Any = None) -> None:
        if activity is not None:
            self.activity = activity
        if digests is not None:
            self.digests = digests

    def status_snapshot(self, session_key: Any = None) -> dict:
        return {"running": [], "recent": []}


def _make_request(path_with_query: str, *, token: str | None = AUTH_SECRET) -> WsRequest:
    return make_request(path_with_query, token, always_append=True)


def _make_handler(get_manager: Any) -> GatewayHTTPHandler:
    return make_handler(Path("/nonexistent"), get_subagent_manager=get_manager)


async def _dispatch(handler, path_with_query: str, *, token: str | None = AUTH_SECRET):
    clean_path = path_with_query.split("?", 1)[0]
    request = _make_request(path_with_query, token=token)
    return await handler.subagent_routes.dispatch(request, clean_path)


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _log(*summaries: str, task_id: str = _TASK, capacity: int | None = None) -> SubagentActivityLog:
    log = SubagentActivityLog() if capacity is None else SubagentActivityLog(capacity=capacity)
    for summary in summaries:
        log.append(task_id, "tool_start", summary=summary, name="grep")
    return log


@pytest.fixture()
def env():
    log = _log("reading a.py", "grepping for foo")
    digests = FakeDigestStore()
    manager = FakeManager(activity=log, digests=digests)
    handler = _make_handler(lambda: manager)
    return SimpleNamespace(handler=handler, manager=manager, log=log, digests=digests)


# -- auth: identica ai vicini -------------------------------------------------


@pytest.mark.parametrize("resource", ["activity", "digest"])
async def test_reads_require_a_token(env, resource: str) -> None:
    response = await _dispatch(env.handler, f"/api/subagents/{_TASK}/{resource}", token=None)
    assert response.status_code == 401
    assert env.digests.calls == []


@pytest.mark.parametrize("resource", ["activity", "digest"])
async def test_a_wrong_token_is_rejected(env, resource: str) -> None:
    response = await _dispatch(
        env.handler, f"/api/subagents/{_TASK}/{resource}", token="not-the-secret"
    )
    assert response.status_code == 401


# -- routing ------------------------------------------------------------------


async def test_unrelated_subresources_are_not_claimed(env) -> None:
    assert await _dispatch(env.handler, f"/api/subagents/{_TASK}/other") is None
    assert await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity/extra") is None


async def test_the_routes_are_reachable_from_the_main_dispatch(env) -> None:
    path = f"/api/subagents/{_TASK}/activity"
    response = await env.handler._dispatch_misc_routes(MagicMock(), _make_request(path), path)
    assert response is not None and response.status_code == 200
    assert _json(response)["task_id"] == _TASK


@pytest.mark.parametrize("raw_id", ["a%2Fb", "a%5Cb", "with%20space", "a" * 65])
@pytest.mark.parametrize("resource", ["activity", "digest"])
async def test_invalid_ids_are_rejected(env, raw_id: str, resource: str) -> None:
    response = await _dispatch(env.handler, f"/api/subagents/{raw_id}/{resource}")
    assert response.status_code == 400


# -- GET .../activity ---------------------------------------------------------


async def test_the_window_is_served_verbatim(env) -> None:
    response = await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity")
    assert response.status_code == 200
    body = _json(response)
    expected = env.log.tail_window(_TASK, since_seq=0).to_dict()
    # Nessuna riscrittura di forma: gli stessi nomi di campo del frame WS, più
    # il task_id che l'URL porta e il body deve confermare.
    assert body == {"task_id": _TASK, **expected}


async def test_since_is_honoured(env) -> None:
    response = await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity?since=1")
    body = _json(response)
    assert [e["seq"] for e in body["events"]] == [2]
    assert body["since_seq"] == 1
    assert body["gap"] is False
    assert body["latest_seq"] == 2


async def test_a_cursor_past_the_end_is_an_empty_window_not_a_gap(env) -> None:
    body = _json(await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity?since=99"))
    assert body["events"] == []
    assert body["gap"] is False
    assert body["latest_seq"] == 2


async def test_the_gap_flag_survives_the_wire() -> None:
    """Ring da 4 e 9 eventi: chi riparte da 0 ha perso l'inizio, e lo deve sapere."""
    log = SubagentActivityLog(capacity=4)
    for index in range(9):
        log.append(_TASK, "iteration", summary=f"iteration {index}")
    handler = _make_handler(lambda: FakeManager(activity=log))

    body = _json(await _dispatch(handler, f"/api/subagents/{_TASK}/activity"))

    assert body["gap"] is True
    assert body["dropped"] == 5
    assert body["first_seq"] == 6
    assert body["latest_seq"] == 9


async def test_the_response_is_capped_at_the_ring_size() -> None:
    log = SubagentActivityLog(capacity=MAX_HTTP_EVENTS + 50)
    for index in range(MAX_HTTP_EVENTS + 50):
        log.append(_TASK, "iteration", summary=f"iteration {index}")
    handler = _make_handler(lambda: FakeManager(activity=log))

    body = _json(await _dispatch(handler, f"/api/subagents/{_TASK}/activity"))

    assert len(body["events"]) == MAX_HTTP_EVENTS
    assert body["gap"] is True


async def test_an_unknown_task_is_an_empty_window(env) -> None:
    body = _json(await _dispatch(env.handler, "/api/subagents/nosuchtask/activity"))
    assert body == {
        "task_id": "nosuchtask", "events": [], "since_seq": 0, "first_seq": 0,
        "last_seq": 0, "latest_seq": 0, "dropped": 0, "gap": False,
    }


@pytest.mark.parametrize("raw", ["abc", "-1", "1.5", "1e3", "9" * 13])
async def test_a_malformed_since_is_a_400(env, raw: str) -> None:
    response = await _dispatch(
        env.handler, f"/api/subagents/{_TASK}/activity?since={urllib.parse.quote(raw)}"
    )
    assert response.status_code == 400


async def test_an_empty_since_means_zero(env) -> None:
    body = _json(await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity?since="))
    assert body["since_seq"] == 0
    assert len(body["events"]) == 2


async def test_without_an_agent_the_read_degrades_instead_of_failing() -> None:
    """Onboarding: nessun manager. Una lettura degrada, un'azione darebbe 503."""
    handler = _make_handler(lambda: None)
    response = await _dispatch(handler, f"/api/subagents/{_TASK}/activity?since=4")
    assert response.status_code == 200
    assert _json(response) == {
        "task_id": _TASK, "events": [], "since_seq": 4, "first_seq": 0,
        "last_seq": 0, "latest_seq": 0, "dropped": 0, "gap": False,
    }


async def test_a_manager_without_the_activity_log_degrades() -> None:
    """Fase 2 non ancora cablata: la route non deve dare 500."""
    handler = _make_handler(lambda: FakeManager())
    response = await _dispatch(handler, f"/api/subagents/{_TASK}/activity")
    assert response.status_code == 200
    assert _json(response)["events"] == []


async def test_a_broken_log_is_a_generic_500(env) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("dettaglio interno")

    env.manager.activity.tail_window = boom
    response = await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity")
    assert response.status_code == 500
    assert b"dettaglio interno" not in response.body


async def test_a_window_of_the_wrong_type_degrades_to_empty(env) -> None:
    env.manager.activity.tail_window = lambda *a, **k: ["not", "a", "window"]
    response = await _dispatch(env.handler, f"/api/subagents/{_TASK}/activity")
    assert response.status_code == 200
    assert _json(response)["events"] == []


# -- GET .../digest -----------------------------------------------------------


async def test_the_persisted_digest_wins(env) -> None:
    env.digests.events = build_digest([
        {"seq": 1, "ts": 1.0, "kind": "tool_start", "name": "grep", "summary": "grepping"},
        {"seq": 2, "ts": 2.0, "kind": "tool_end", "name": "grep",
         "status": "ok", "summary": "3 matches", "duration_ms": 12},
    ])

    body = _json(await _dispatch(env.handler, f"/api/subagents/{_TASK}/digest"))

    assert body["task_id"] == _TASK
    assert body["source"] == "digest"
    assert body["count"] == 1
    # La coppia start/end è già collassata dal produttore: la route non rimonta.
    assert body["events"][0]["kind"] == "tool"
    assert body["events"][0]["status"] == "ok"
    assert env.digests.calls == [_TASK]


async def test_a_running_subagent_gets_a_live_preview(env) -> None:
    """Nessun file ancora: la condensa si ricava dal ring, marcata ``live``."""
    body = _json(await _dispatch(env.handler, f"/api/subagents/{_TASK}/digest"))

    assert body["source"] == "live"
    assert [e["summary"] for e in body["events"]] == [
        "reading a.py (no result recorded)", "grepping for foo (no result recorded)",
    ]


async def test_a_subagent_with_no_activity_is_not_a_404(env) -> None:
    body = _json(await _dispatch(env.handler, "/api/subagents/nosuchtask/digest"))
    assert body == {"task_id": "nosuchtask", "events": [], "count": 0, "source": "none"}


async def test_an_unreadable_digest_falls_back_instead_of_failing(env) -> None:
    env.digests.error = OSError("disk gone")
    response = await _dispatch(env.handler, f"/api/subagents/{_TASK}/digest")
    assert response.status_code == 200
    assert _json(response)["source"] == "live"


async def test_a_digest_of_the_wrong_type_is_ignored(env) -> None:
    env.digests.events = {"not": "a list"}
    body = _json(await _dispatch(env.handler, f"/api/subagents/{_TASK}/digest"))
    assert body["source"] == "live"


async def test_without_an_agent_the_digest_is_empty() -> None:
    handler = _make_handler(lambda: None)
    response = await _dispatch(handler, f"/api/subagents/{_TASK}/digest")
    assert response.status_code == 200
    assert _json(response)["source"] == "none"
