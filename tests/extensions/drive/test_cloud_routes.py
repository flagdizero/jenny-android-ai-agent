"""Le route ``/api/backup/cloud*`` dell'estensione Drive, montate dal core.

Passano dal giro completo delle route della WebUI (``_dispatch_misc_routes``),
così si prova anche quel che il core aggiunge: il controllo del token.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.gateway_http import make_handler, make_request

from jenny import extensions
from jenny.config.schema import SnapshotConfig
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.service import SnapshotService

pytest.importorskip("cryptography")

_AUTH_SECRET = "test-secret"


def _request(path: str, payload: dict | None = None, token: str | None = _AUTH_SECRET):
    headers = None
    if payload is not None:
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        headers = [("X-Jenny-Backup-Data", encoded)]
    return make_request(path, token, headers)


def _json(response) -> dict:
    return json.loads(response.body)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    (workspace / "memory").mkdir(parents=True)

    from jenny.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)
    extensions.reset_for_tests(None)
    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    service = SnapshotService(engine, SnapshotConfig(pbkdf2_iterations=100_000))
    handler = make_handler(workspace / "skills", snapshot_service=service)

    async def call(path: str, payload: dict | None = None, *, token=_AUTH_SECRET):
        return await handler._dispatch_misc_routes(None, _request(path, payload, token), path)

    yield SimpleNamespace(handler=handler, call=call)
    extensions.reset_for_tests(None)


async def test_cloud_status_and_enable_without_google(env) -> None:
    # Sul Mac (niente contesto Android) Drive non c'è: lo stato lo dice, e
    # «accendi» risponde con un errore leggibile, non con un 500.
    status = await env.call("/api/backup/cloud")
    assert status.status_code == 200
    assert _json(status)["available"] is False
    assert _json(status)["provider"] == ""

    assert _json(await env.call("/api/backup/cloud/enable")) == {
        "ok": False, "error": "unavailable",
    }
    assert await env.call("/api/backup/cloud/nope") is None


async def test_the_core_checks_the_token(env) -> None:
    for path in ("/api/backup/cloud", "/api/backup/cloud/run"):
        response = await env.call(path, token=None)
        assert response.status_code == 401, path


async def test_cloud_password_actions_check_the_payload(env) -> None:
    # La password viaggia nell'header: senza, o vuota, è una richiesta sbagliata.
    for path in ("/api/backup/cloud/password", "/api/backup/cloud/unlock"):
        assert (await env.call(path)).status_code == 400
        assert (await env.call(path, {"password": ""})).status_code == 400
        assert (await env.call(path, {"password": 123})).status_code == 400
    # Troppo corta: lo dice il servizio, prima di toccare Drive.
    weak = await env.call("/api/backup/cloud/enable", {"password": "corta"})
    assert _json(weak) == {"ok": False, "error": "weak_password"}
    # Le vecchie strade della chiave non ci sono più.
    for gone in ("/api/backup/cloud/key/show", "/api/backup/cloud/key/set",
                 "/api/backup/cloud/recover-key", "/api/backup/cloud/key-saved"):
        assert await env.call(gone) is None, gone


async def test_the_switch_lives_in_the_extensions_section(env, tmp_path, monkeypatch) -> None:
    # Lo stato «acceso» sta in ``config.json`` sotto ``extensions.drive``: viaggia
    # negli snapshot, e il telefono nuovo ripristinato lo ritrova.
    from jenny.config.loader import load_config, save_config
    from jenny.config.schema import Config
    from jenny_extensions.drive import cloud_service

    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr("jenny.config.loader.get_config_path", lambda: path)

    service = cloud_service.cloud_service_for(env.handler._snapshot_service)
    assert service._get_provider() == ""
    await service._set_provider("drive")
    assert service._get_provider() == "drive"
    raw = json.loads(path.read_text("utf-8"))
    assert raw["extensions"] == {"drive": {"provider": "drive"}}
    assert load_config(path).extensions["drive"]["provider"] == "drive"
