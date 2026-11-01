"""Un singolo se ne va quando ha suonato, non quando ha girato.

Fino al 01/11/2026 ``_execute_job`` toglieva ogni job ``at`` dopo il giro,
anche quando il giro era *saltato* (l'agente non c'è ancora: il boot,
l'onboarding) o finito in errore del provider: il promemoria era perso, con un
WARNING nel log per tutta traccia. Ora resta nello store e riprova, finché non
suona o finché la sua ora non è passata da più di un giorno.
"""

from __future__ import annotations

import time

from support.cron import reschedule_job

from jenny.cron.service import (
    _ONE_SHOT_RETRY_MS,
    _PAST_GRACE_MS,
    CronJobSkippedError,
    CronService,
)
from jenny.cron.types import CronSchedule


def _bound_chat() -> dict[str, str]:
    return {"session_key": "websocket:chat-1", "origin_channel": "websocket",
            "origin_chat_id": "chat-1"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _service(tmp_path, on_job) -> CronService:
    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job)
    service._arm_timer = lambda: None  # type: ignore[method-assign]
    service._running = True
    service._load_store()
    return service


async def test_a_skipped_one_shot_stays_and_retries(tmp_path) -> None:
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)
        raise CronJobSkippedError("no provider configured")

    service = _service(tmp_path, on_job)
    job = service.add_job(name="dentista", schedule=CronSchedule(kind="at", at_ms=_now_ms() - 1),
                          message="chiama il dentista", **_bound_chat())

    await service._on_timer()

    kept = service.get_job(job.id)
    assert calls == [job.id]
    assert kept is not None and kept.enabled
    assert kept.state.last_status == "skipped"
    assert kept.state.next_run_at_ms is not None
    assert 0 < kept.state.next_run_at_ms - _now_ms() <= _ONE_SHOT_RETRY_MS
    # Non riparte al tick dopo: aspetta il suo turno.
    await service._on_timer()
    assert calls == [job.id]


async def test_a_one_shot_whose_provider_failed_rings_on_the_retry(tmp_path) -> None:
    attempts: list[str] = []

    async def on_job(job):
        attempts.append(job.id)
        if len(attempts) == 1:
            raise RuntimeError("provider down")
        return "detto"

    service = _service(tmp_path, on_job)
    job = service.add_job(name="dentista", schedule=CronSchedule(kind="at", at_ms=_now_ms() - 1),
                          message="chiama il dentista", **_bound_chat())

    await service._on_timer()
    kept = service.get_job(job.id)
    assert kept is not None and kept.state.last_status == "error"
    assert kept.state.next_run_at_ms is not None

    # Il momento del nuovo tentativo è arrivato (su disco: il tick rilegge lo store).
    reschedule_job(service, job.id, kept.schedule, _now_ms() - 1)
    await service._on_timer()

    assert attempts == [job.id, job.id]
    assert service.get_job(job.id) is None, "suonato: se ne va come ogni singolo"


async def test_a_one_shot_that_rang_still_goes_away(tmp_path) -> None:
    async def on_job(job):
        return "detto"

    service = _service(tmp_path, on_job)
    job = service.add_job(name="dentista", schedule=CronSchedule(kind="at", at_ms=_now_ms() - 1),
                          message="chiama il dentista", **_bound_chat())
    await service._on_timer()
    assert service.get_job(job.id) is None


async def test_a_one_shot_a_day_late_that_never_rang_is_given_up(tmp_path) -> None:
    async def on_job(job):
        raise CronJobSkippedError("no provider configured")

    service = _service(tmp_path, on_job)
    job = service.add_job(name="dentista", schedule=CronSchedule(kind="at", at_ms=_now_ms() - 1),
                          message="chiama il dentista", **_bound_chat())
    # La sua ora è passata da più di un giorno (come la lascerebbe un telefono
    # rimasto senza provider): suonerebbe a un giorno di distanza.
    reschedule_job(service, job.id,
                   CronSchedule(kind="at", at_ms=_now_ms() - _PAST_GRACE_MS - 60_000),
                   _now_ms() - 1)

    await service._on_timer()

    assert service.get_job(job.id) is None
