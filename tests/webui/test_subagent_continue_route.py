"""``GET /api/subagents/{id}/continue``: il tasto «fai continuare» della casa.

Per un aiutante fermato al budget il tasto «rimettila in piedi» rilanciava da
zero (``restart``), buttando quel che aveva letto e scritto. Questa rotta lo fa
continuare dalla sua conversazione (``manager.send``), con la stessa forma di
risposta e gli stessi errori di ``/restart``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.gateway_http import AUTH_SECRET, make_handler, make_request

from jenny.webui.subagent_routes import CONTINUE_AFTER_BUDGET_TEXT


# Nomi identici a quelli di jenny/agent/subagent.py: la rotta riconosce gli
# errori per nome di classe, senza importare l'agente.
class SubagentSendError(RuntimeError):
    pass


class SubagentRestartError(RuntimeError):
    pass


class SubagentConcurrencyLimitError(RuntimeError):
    pass


class FakeManager:
    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str]] = []
        self.send_error: Exception | None = None
        self._running = {"project:piante": [{"task_id": "live0001", "lineage_id": "lin00001"}]}
        self._records = {
            "project:piante": [SimpleNamespace(task_id="dead0002", lineage_id="lin00002")],
        }

    def status_snapshot(self, session_key=None):
        if session_key is None:
            return {"running": [e for v in self._running.values() for e in v], "recent": []}
        return {"running": self._running.get(session_key, []), "recent": []}

    def list_records(self, session_key=None):
        if session_key is None:
            return [r for v in self._records.values() for r in v]
        return self._records.get(session_key, [])

    async def send(self, target_id, message, **kwargs):
        self.send_calls.append((target_id, message))
        self.send_kwargs = kwargs
        if self.send_error is not None:
            raise self.send_error
        return SimpleNamespace(mode="resumed", text="ok", task_id="new00003")


async def _dispatch(handler, path_with_query: str, *, token: str | None = AUTH_SECRET):
    request = make_request(path_with_query, token, always_append=True)
    return await handler.subagent_routes.dispatch(request, path_with_query.split("?", 1)[0])


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


@pytest.fixture()
def env():
    manager = FakeManager()
    handler = make_handler(Path("/nonexistent"), get_subagent_manager=lambda: manager)
    return SimpleNamespace(handler=handler, manager=manager)


def test_the_text_is_the_contract() -> None:
    assert CONTINUE_AFTER_BUDGET_TEXT == (
        "Continue where you stopped: finish the job, then report what is done and what remains."
    )


async def test_continue_requires_a_token(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/dead0002/continue", token=None)
    assert response.status_code == 401
    assert env.manager.send_calls == []


async def test_continue_sends_the_text_to_the_lineage(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/dead0002/continue")

    assert response.status_code == 200
    assert _json(response) == {"continued": True, "task_id": "new00003"}
    # Il lineage, non il task id vecchio: un tentativo nuovo gia in corso riceve
    # il messaggio invece di partire una seconda volta.
    assert env.manager.send_calls == [("lin00002", CONTINUE_AFTER_BUDGET_TEXT)]
    # «Continua» non rilancia mai da zero: senza storia il manager rifiuta (409).
    assert env.manager.send_kwargs == {"allow_restart": False}


async def test_an_unknown_id_goes_to_send_as_it_is(env) -> None:
    await _dispatch(env.handler, "/api/subagents/lin00002/continue")
    assert env.manager.send_calls == [("lin00002", CONTINUE_AFTER_BUDGET_TEXT)]


@pytest.mark.parametrize(("error", "status"), [
    (SubagentSendError("the message is empty"), 409),
    (SubagentRestartError("attempt cap"), 409),
    (SubagentConcurrencyLimitError("pool full"), 429),
])
async def test_manager_errors_map_like_restart(env, error, status) -> None:
    env.manager.send_error = error
    response = await _dispatch(env.handler, "/api/subagents/dead0002/continue")
    assert response.status_code == status
    assert str(error) in response.body.decode("utf-8")


async def test_an_unexpected_error_is_a_generic_500(env) -> None:
    env.manager.send_error = ValueError("dettaglio interno")
    response = await _dispatch(env.handler, "/api/subagents/dead0002/continue")
    assert response.status_code == 500
    assert "dettaglio interno" not in response.body.decode("utf-8")


async def test_continue_without_a_manager_is_503() -> None:
    handler = make_handler(Path("/nonexistent"), get_subagent_manager=lambda: None)
    response = await _dispatch(handler, "/api/subagents/dead0002/continue")
    assert response.status_code == 503


async def test_another_conversations_helper_is_refused(env) -> None:
    response = await _dispatch(
        env.handler, "/api/subagents/dead0002/continue?session_key=websocket%3Adefault"
    )
    assert response.status_code == 404
    assert env.manager.send_calls == []


async def test_this_conversations_helper_goes_through(env) -> None:
    response = await _dispatch(
        env.handler, "/api/subagents/dead0002/continue?session_key=project%3Apiante"
    )
    assert response.status_code == 200


async def test_an_invalid_id_is_rejected(env) -> None:
    response = await _dispatch(env.handler, "/api/subagents/..%2Fetc/continue")
    assert response.status_code == 400
    assert env.manager.send_calls == []
