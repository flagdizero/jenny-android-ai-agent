"""Il comando ``cron.restore``: l'«Annulla» della stanza Promemoria.

Una scrittura, quindi un comando del WebSocket (``CONTRIBUTING.md``): le route
``/api/webui/cron/<id>/…`` sono un elenco chiuso
(``test_http_write_routes_inventory.py``). Si passa da ``dispatch_command``,
cosi' si prova anche che la famiglia ``cron`` si carica.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule
from jenny.webui.commands import CommandError, dispatch_command


@pytest.fixture
async def service(tmp_path):
    svc = CronService(tmp_path / "workspace", on_job=lambda _: asyncio.sleep(0))
    svc._running = True
    yield svc
    svc.stop()


def _ctx(service):
    return SimpleNamespace(get_cron_service=lambda: service)


def _job(service):
    return service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=3_600_000), message="bevi",
        session_key="websocket:chat-1", origin_channel="websocket", origin_chat_id="chat-1",
    ).id


async def test_a_removed_job_is_restored_once(service) -> None:
    job_id = _job(service)
    service.remove_job(job_id)

    out = await dispatch_command(_ctx(service), "cron.restore", {"id": job_id})

    assert out == {"ok": True, "result": "restored"}
    assert service.get_job(job_id) is not None
    with pytest.raises(CommandError) as err:
        await dispatch_command(_ctx(service), "cron.restore", {"id": job_id})
    assert err.value.code == "not_found"


async def test_a_bad_id_or_no_scheduler_is_refused(service) -> None:
    for bad in (None, "", "../x", "x" * 65):
        with pytest.raises(CommandError) as err:
            await dispatch_command(_ctx(service), "cron.restore", {"id": bad})
        assert err.value.code == "bad_request"
    with pytest.raises(CommandError) as err:
        await dispatch_command(SimpleNamespace(get_cron_service=lambda: None), "cron.restore", {"id": "abc"})
    assert err.value.code == "unavailable"
