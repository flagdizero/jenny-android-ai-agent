"""Le route ``/api/webui/cron``: token, thread, e i codici di stato.

Stesso pattern di ``test_skills_routes.py`` e ``test_backup_routes.py``: si
costruisce un ``GatewayHTTPHandler`` vero con dipendenze finte e si dispatcha una
``websockets.http11.Request``. Il payload lo prova ``test_cron_api.py``; qui si
prova soltanto il trasporto.
"""

from __future__ import annotations

import asyncio
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
_PATH = "/api/webui/cron"


def _make_handler(workspace: Path, *, get_cron_service=None) -> GatewayHTTPHandler:
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
        skills_workspace_path=workspace,
        get_cron_service=get_cron_service,
    )


def _request(path: str = _PATH, *, token: str | None = _AUTH_SECRET) -> WsRequest:
    if token is None:
        return WsRequest(path=path, headers=Headers())
    sep = "&" if "?" in path else "?"
    return WsRequest(path=f"{path}{sep}token={urllib.parse.quote(token)}", headers=Headers())


def _dispatch(handler: GatewayHTTPHandler, path: str = _PATH, *, token=_AUTH_SECRET):
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
