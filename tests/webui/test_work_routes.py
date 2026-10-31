"""``GET /api/work``: lo snapshot della bolla di lavoro per chi si ricollega.

Lo stesso payload del frame ``work_status`` (``jenny/runtime/work_status.py``),
per conversazione, con le due forme di chiave che manda la home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.gateway_http import AUTH_SECRET, make_handler, make_request

from jenny.runtime import work_status
from jenny.runtime.work_status import WorkReporter
from jenny.webui.work_routes import chat_id_for_session_key


class _Bus:
    def try_publish_outbound(self, msg) -> bool:
        return True


@pytest.fixture(autouse=True)
def _clean_registry():
    work_status.reset_registry()
    yield
    work_status.reset_registry()


async def _get(path_with_query: str, *, token: str | None = AUTH_SECRET):
    handler = make_handler(Path("/nonexistent"))
    request = make_request(path_with_query, token, always_append=True)
    return await handler.work_routes.dispatch(request, path_with_query.split("?", 1)[0])


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


async def test_other_paths_are_not_its_business() -> None:
    assert await _get("/api/workspace") is None
    assert await _get("/api/work/extra") is None


async def test_it_wants_the_token() -> None:
    response = await _get("/api/work?session_key=websocket%3Adefault", token="wrong")
    assert response.status_code == 401


async def test_it_serves_the_work_of_the_conversation_it_is_asked_about() -> None:
    here = WorkReporter(_Bus(), kind="gardener", chat_id="project:orto", phases=("pages",))
    WorkReporter(_Bus(), kind="dream", chat_id="default", phases=())

    body = _json(await _get("/api/work?session_key=project%3Aorto"))

    assert [w["id"] for w in body["work"]] == [here.id]
    assert body["work"][0] == here.payload()


async def test_the_personal_chat_is_default_on_the_wire() -> None:
    dream = WorkReporter(_Bus(), kind="dream", chat_id="default", phases=())

    body = _json(await _get("/api/work?session_key=websocket%3Adefault"))

    assert [w["id"] for w in body["work"]] == [dream.id]


async def test_no_conversation_means_nothing_to_show() -> None:
    assert _json(await _get("/api/work")) == {"work": []}
    assert _json(await _get("/api/work?session_key=telegram%3A42")) == {"work": []}


def test_the_two_keys_the_home_sends() -> None:
    assert chat_id_for_session_key("websocket:default") == "default"
    assert chat_id_for_session_key("project%3Aorto") == "project:orto"
    assert chat_id_for_session_key("project:") is None
    assert chat_id_for_session_key("") is None
