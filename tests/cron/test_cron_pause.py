"""Un job dell'utente si mette in pausa e si riprende dall'officina.

Fino al 26/09/2026 l'unico modo di fermare un promemoria che dava fastidio era
convincere Jenny a cancellarlo. La pausa e' un terzo stato accanto ai due in cui
un job e' gia' ``enabled = False`` — un ``at`` eseguito e un job senza sessione —
e per questo ha un campo suo, ``paused_at_ms``: senza, «in pausa» e «concluso»
sarebbero indistinguibili, e «Riprendi» su un promemoria gia' consegnato lo
rifarebbe partire.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronPayload, CronSchedule

_HOUR = 3_600_000


def _bound() -> dict[str, str]:
    return {
        "session_key": "websocket:chat-1",
        "origin_channel": "websocket",
        "origin_chat_id": "chat-1",
    }


@pytest.fixture
async def running(tmp_path):
    """Un servizio che «gira» senza avviare il timer: come i test di ``run_job``.
    ``_arm_timer`` vuole un loop, quindi i test che lo usano sono ``async``."""
    service = CronService(tmp_path / "cron" / "jobs.json", on_job=lambda _: asyncio.sleep(0))
    service._running = True
    yield service
    service.stop()


async def test_pause_stops_the_job_and_says_since_when(running) -> None:
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    before = int(time.time() * 1000)

    assert service.set_paused(job.id, True) == "paused"

    paused = service.get_job(job.id)
    assert paused.enabled is False
    assert paused.state.next_run_at_ms is None
    assert paused.paused_at_ms is not None and paused.paused_at_ms >= before
    assert service.set_paused(job.id, True) == "unchanged"


async def test_resume_counts_from_now_and_does_not_catch_up(running) -> None:
    """Un «ogni ora» ripreso dopo tre ore di pausa non recupera le tre saltate:
    riparte fra un'ora."""
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.set_paused(job.id, True)
    now = int(time.time() * 1000)

    assert service.set_paused(job.id, False) == "resumed"

    resumed = service.get_job(job.id)
    assert resumed.enabled is True
    assert resumed.paused_at_ms is None
    assert now + _HOUR - 5_000 <= resumed.state.next_run_at_ms <= now + _HOUR + 5_000
    assert service.set_paused(job.id, False) == "unchanged"


async def test_a_one_shot_that_expired_during_the_pause_cannot_resume(running) -> None:
    """Riprenderlo lo farebbe scattare subito, in ritardo: una sorpresa. Resta
    da eliminare."""
    service = running
    at = int(time.time() * 1000) + 60_000
    job = service.add_job(
        name="dentista", schedule=CronSchedule(kind="at", at_ms=at), message="vai", **_bound()
    )
    service.set_paused(job.id, True)
    service.get_job(job.id).schedule.at_ms = int(time.time() * 1000) - 1_000
    service.persist_job_state()

    assert service.set_paused(job.id, False) == "expired"
    assert service.get_job(job.id).paused_at_ms is not None


async def test_a_one_shot_resumed_in_time_keeps_its_hour(running) -> None:
    service = running
    at = int(time.time() * 1000) + 10 * _HOUR
    job = service.add_job(
        name="dentista", schedule=CronSchedule(kind="at", at_ms=at), message="vai", **_bound()
    )
    service.set_paused(job.id, True)

    assert service.set_paused(job.id, False) == "resumed"
    assert service.get_job(job.id).state.next_run_at_ms == at


async def test_a_finished_one_shot_is_not_a_paused_job(running) -> None:
    """``enabled = False`` senza ``paused_at_ms`` e' un promemoria gia'
    consegnato: «Riprendi» non ha niente da riprendere."""
    service = running
    job = service.add_job(
        name="fatto", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="x", **_bound()
    )
    live = service.get_job(job.id)
    live.enabled = False
    live.state.next_run_at_ms = None
    service.persist_job_state()

    assert service.set_paused(job.id, False) == "unchanged"
    assert service.get_job(job.id).enabled is False


async def test_system_jobs_are_protected_and_unknown_ids_are_not_found(running) -> None:
    service = running
    service.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=CronSchedule(kind="every", every_ms=2 * _HOUR),
        payload=CronPayload(kind="system_event"),
    ))

    assert service.set_paused("dream", True) == "protected"
    assert service.get_job("dream").enabled is True
    assert service.set_paused("nessuno", True) == "not_found"


async def test_the_pause_survives_a_restart(tmp_path, running) -> None:
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.set_paused(job.id, True)
    stamp = service.get_job(job.id).paused_at_ms

    reloaded = CronService(tmp_path / "cron" / "jobs.json")
    again = reloaded.get_job(job.id)
    assert again.enabled is False
    assert again.paused_at_ms == stamp


async def test_a_store_written_before_the_pause_reads_as_not_paused(tmp_path, running) -> None:
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    path = tmp_path / "cron" / "jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for j in data["jobs"]:
        j.pop("pausedAtMs", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    assert CronService(path).get_job(job.id).paused_at_ms is None


async def test_a_stopped_service_journals_the_pause(tmp_path, running) -> None:
    """Da fermo il servizio non salva ``jobs.json``: accoda al giornale, come
    ``remove_job``, e il primo caricamento lo riapplica."""
    path = tmp_path / "cron" / "jobs.json"
    live = running
    job = live.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    stopped = CronService(path)

    assert stopped.set_paused(job.id, True) == "paused"
    assert CronService(path).get_job(job.id).paused_at_ms is not None


async def test_a_paused_job_never_fires(tmp_path) -> None:
    called: list[str] = []

    async def on_job(job) -> None:
        called.append(job.id)

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job)
    job = service.add_job(
        name="spesso", schedule=CronSchedule(kind="every", every_ms=200), message="x", **_bound()
    )
    service.set_paused(job.id, True)
    await service.start()
    try:
        await asyncio.sleep(0.5)
        assert called == []
    finally:
        service.stop()
