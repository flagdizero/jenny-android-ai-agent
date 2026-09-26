"""Le route ``/api/webui/cron``: token, thread, e i codici di stato.

Stesso pattern di ``test_skills_routes.py`` e ``test_backup_routes.py``: si
costruisce un ``GatewayHTTPHandler`` vero con dipendenze finte e si dispatcha una
``websockets.http11.Request``. Il payload lo prova ``test_cron_api.py``; qui si
prova soltanto il trasporto.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from support.gateway_http import AUTH_SECRET, make_handler, make_request
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler

_PATH = "/api/webui/cron"


def _make_handler(workspace: Path, *, get_cron_service=None) -> GatewayHTTPHandler:
    return make_handler(workspace, get_cron_service=get_cron_service)


def _request(path: str = _PATH, *, token: str | None = AUTH_SECRET) -> WsRequest:
    return make_request(path, token, always_append=True)


def _dispatch(handler: GatewayHTTPHandler, path: str = _PATH, *, token=AUTH_SECRET):
    return asyncio.run(handler.cron_routes.dispatch(_request(path, token=token), path))


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "data" / "workspace"
    ws.mkdir(parents=True)
    return ws


def test_a_request_without_a_token_is_refused(workspace):
    handler = _make_handler(workspace, get_cron_service=lambda: MagicMock())

    response = _dispatch(handler, token=None)

    assert response.status_code == 401


def test_a_request_with_the_wrong_token_is_refused(workspace):
    handler = _make_handler(workspace, get_cron_service=lambda: MagicMock())

    response = _dispatch(handler, token="non-e-il-segreto")

    assert response.status_code == 401


def test_another_path_is_left_to_the_families_mounted_after_this_one(workspace):
    """``dispatch`` deve ritornare ``None``, non una risposta.

    Mangiare il dispatch fermerebbe le route montate dopo — un difetto che si
    manifesta in *altre* funzionalita', non in questa.
    """
    handler = _make_handler(workspace, get_cron_service=lambda: MagicMock())

    assert _dispatch(handler, "/api/webui/skills") is None
    assert _dispatch(handler, "/api/webui/cron/extra") is None


def test_without_a_service_it_answers_not_available_instead_of_failing(workspace):
    """Durante l'onboarding la WebUI e' servita e il cron non c'e' ancora."""
    handler = _make_handler(workspace, get_cron_service=None)

    response = _dispatch(handler)

    assert response.status_code == 200
    assert json.loads(response.body)["available"] is False


def test_a_getter_that_raises_is_treated_as_no_service(workspace):
    def _boom():
        raise RuntimeError("container a meta' costruzione")

    handler = _make_handler(workspace, get_cron_service=_boom)

    response = _dispatch(handler)

    assert response.status_code == 200
    assert json.loads(response.body)["available"] is False


def test_a_real_service_comes_back_as_json(workspace, monkeypatch):
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob, CronPayload, CronSchedule

    cron = CronService(workspace / "cron" / "jobs.json")
    cron.register_system_job(CronJob(
        id="dream", name="dream",
        schedule=CronSchedule(kind="every", every_ms=3_600_000),
        payload=CronPayload(kind="system_event"),
    ))

    import jenny.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(
        workspace_path=workspace,
        agents=SimpleNamespace(defaults=SimpleNamespace(
            timezone="Europe/Rome", dream=SimpleNamespace(enabled=True),
            gardener=SimpleNamespace(enabled=True),
        )),
        gateway=SimpleNamespace(heartbeat=SimpleNamespace(enabled=True)),
        updates=SimpleNamespace(enabled=True),
    ))

    handler = _make_handler(workspace, get_cron_service=lambda: cron)
    response = _dispatch(handler)

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["available"] is True
    assert [j["id"] for j in payload["jobs"]] == ["dream"]


def test_a_failing_payload_becomes_a_500_and_not_a_traceback(workspace, monkeypatch):
    import jenny.webui.cron_routes as routes

    def _boom(_cron):
        raise OSError("disco")

    monkeypatch.setattr(routes, "webui_cron_payload", _boom)
    handler = _make_handler(workspace, get_cron_service=lambda: MagicMock())

    response = _dispatch(handler)

    assert response.status_code == 500


def test_the_payload_is_built_off_the_event_loop(workspace, monkeypatch):
    """Legge lo store sotto il lock del file: sul loop bloccherebbe la chat.

    La spia e' su ``asyncio.to_thread``, non sul thread reale: quel che va
    fissato e' la scelta, non l'implementazione di asyncio.
    """
    import jenny.webui.cron_routes as routes

    seen: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy(fn, *args, **kwargs):
        seen.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", _spy)
    monkeypatch.setattr(routes, "webui_cron_payload", lambda _cron: {"available": True})
    handler = _make_handler(workspace, get_cron_service=lambda: MagicMock())

    response = _dispatch(handler)

    assert response.status_code == 200
    assert seen, "il payload e' stato costruito sul loop del gateway"


# ── pausa, ripresa, eliminazione dall'officina ──────────────────────────────

from jenny.cron.service import CronService  # noqa: E402
from jenny.cron.types import CronJob, CronPayload, CronSchedule  # noqa: E402

_BOUND = {"session_key": "websocket:chat-1", "origin_channel": "websocket", "origin_chat_id": "chat-1"}


@pytest.fixture()
def service(tmp_path: Path) -> CronService:
    # Fermo: le scritture passano dal giornale delle azioni, e il timer non parte.
    return CronService(tmp_path / "cron" / "jobs.json")


def _job(service: CronService, **kwargs) -> str:
    schedule = kwargs.pop("schedule", CronSchedule(kind="every", every_ms=3_600_000))
    return service.add_job("gocce", schedule, "ricordamelo", **_BOUND, **kwargs).id


def _act(handler, job_id: str, action: str, *, token=AUTH_SECRET):
    response = _dispatch(handler, f"/api/webui/cron/{job_id}/{action}", token=token)
    body = json.loads(response.body.decode("utf-8")) if response.status_code == 200 else None
    return response.status_code, body


def test_an_action_without_a_token_is_refused(workspace, service):
    handler = _make_handler(workspace, get_cron_service=lambda: service)
    job_id = _job(service)

    status, _ = _act(handler, job_id, "pause", token=None)

    assert status == 401
    assert service.get_job(job_id).paused_at_ms is None


def test_an_action_without_a_service_is_unavailable(workspace):
    handler = _make_handler(workspace, get_cron_service=lambda: None)

    assert _act(handler, "abc12345", "pause")[0] == 503


def test_pause_resume_and_remove_go_through_the_service(workspace, service):
    handler = _make_handler(workspace, get_cron_service=lambda: service)
    job_id = _job(service)

    assert _act(handler, job_id, "pause") == (200, {"result": "paused"})
    assert service.get_job(job_id).paused_at_ms is not None
    assert _act(handler, job_id, "pause") == (200, {"result": "unchanged"})
    assert _act(handler, job_id, "resume") == (200, {"result": "resumed"})
    assert service.get_job(job_id).enabled is True
    assert _act(handler, job_id, "remove") == (200, {"result": "removed"})
    assert service.get_job(job_id) is None


def test_the_service_s_refusals_become_status_codes(workspace, service):
    import time

    handler = _make_handler(workspace, get_cron_service=lambda: service)
    service.register_system_job(CronJob(
        id="dream", name="dream",
        schedule=CronSchedule(kind="every", every_ms=7_200_000),
        payload=CronPayload(kind="system_event"),
    ))
    at = int(time.time() * 1000) + 300
    one_shot = _job(service, schedule=CronSchedule(kind="at", at_ms=at))
    service.set_paused(one_shot, True)
    time.sleep(0.4)

    assert _act(handler, "dream", "pause")[0] == 403
    assert _act(handler, "dream", "remove")[0] == 403
    assert _act(handler, "nessuno1", "pause")[0] == 404
    assert _act(handler, one_shot, "resume")[0] == 409


def test_an_id_that_is_not_an_id_is_refused_before_the_service(workspace):
    called = MagicMock()
    handler = _make_handler(workspace, get_cron_service=lambda: called)

    for bad in ("..", "a%2Fb", "x" * 65):
        assert _act(handler, bad, "pause")[0] in (400, 404), bad
    assert not called.method_calls


def test_run_now_is_still_not_a_route(workspace, service):
    """Il pannello resta senza «esegui adesso»: accoda un turno d'agente, cioe'
    spende token e puo' consegnare un messaggio. V. il cappello di cron_routes."""
    handler = _make_handler(workspace, get_cron_service=lambda: service)
    job_id = _job(service)

    assert _dispatch(handler, f"/api/webui/cron/{job_id}/run") is None
