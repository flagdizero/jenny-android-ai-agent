"""Chi sveglia la coda delle altre Jenny: il giro del cron e Tailscale che torna."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.channels.ws_remote import RemoteListener
from jenny.cron.purposes import SYSTEM_JOB_PURPOSES
from jenny.cron.types import CronJob, CronPayload, CronSchedule
from jenny.runtime.cron_dispatch import PEERS_JOB_ID, CronDispatcher


def _peers_job() -> CronJob:
    return CronJob(
        id=PEERS_JOB_ID,
        name=PEERS_JOB_ID,
        schedule=CronSchedule(kind="every", every_ms=900_000),
        payload=CronPayload(kind="system_event"),
    )


async def test_the_peers_job_wakes_the_queue_without_a_model():
    woken: list[bool] = []
    dispatcher = CronDispatcher(
        get_agent=lambda: None,  # nessun provider: il giro non lo chiede
        config=MagicMock(),
        cron=SimpleNamespace(monitor_followup=None),
        wake_peers=lambda: woken.append(True),
    )
    assert await dispatcher._dispatch(_peers_job()) is None
    assert woken == [True]


def test_the_peers_job_can_present_itself():
    assert PEERS_JOB_ID in SYSTEM_JOB_PURPOSES


async def test_the_listener_says_when_it_listens_again():
    calls: list[bool] = []

    async def serve(hosts):
        return SimpleNamespace(sockets=None, close=lambda: None)

    listener = RemoteListener(serve=serve, port=18790, detect=lambda: ("100.64.0.5",),
                              poll_s=3600)
    listener.on_listening = lambda: calls.append(True)
    await listener.apply(enabled=False, key="", peers=True)
    try:
        assert listener.enabled and not listener.webui_enabled
        assert calls == [True]
    finally:
        listener._server = None
        await listener.stop()
