"""Un giro del timer con piu' job dovuti: cosa arriva su disco, e quando.

``_on_timer`` calcola i job dovuti una volta sola e poi li esegue uno dopo
l'altro, e ogni esecuzione e' un turno d'agente che dura secondi o minuti. In
mezzo succedono cose: l'officina mette in pausa o elimina un job, lo
spegnimento ferma il servizio, Android uccide il processo.
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from pathlib import Path

from support.aio import wait_until

from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

_HOUR = 3_600_000
_wait_until = functools.partial(wait_until, timeout=2.0)


def _bound() -> dict[str, str]:
    return {
        "session_key": "websocket:chat-1",
        "origin_channel": "websocket",
        "origin_chat_id": "chat-1",
    }


def _now() -> int:
    return int(time.time() * 1000)


def _make_due(service: CronService, *job_ids: str) -> None:
    """Rende dovuti *job_ids* adesso e fa partire un giro del timer."""
    for job in service._store.jobs:
        if job.id in job_ids:
            job.state.next_run_at_ms = _now() - 1000
            if job.schedule.kind == "at":
                # Anche l'orario del promemoria: al riavvio un ``at`` si
                # riprogramma dal suo ``at_ms`` (v. ``_recompute_next_runs``).
                job.schedule.at_ms = job.state.next_run_at_ms
    service._save_store()
    service._arm_timer()


def _disk_jobs(path: Path) -> dict[str, dict]:
    return {j["name"]: j for j in json.loads(path.read_text(encoding="utf-8"))["jobs"]}


# -- Lo stato di ogni job arriva su disco appena il job finisce ----------------


async def test_a_job_that_ran_is_on_disk_before_the_next_one_starts(tmp_path) -> None:
    """Un kill durante il job B faceva ripartire A: lo store si salvava a fine giro."""
    path = tmp_path / "cron" / "jobs.json"
    disk_while_b_runs: dict[str, dict] = {}

    async def on_job(job):
        if job.name == "B":
            disk_while_b_runs.update(_disk_jobs(path))

    service = CronService(path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    try:
        a = service.add_job("A", CronSchedule(kind="at", at_ms=_now() + _HOUR), "remind", **_bound())
        b = service.add_job("B", CronSchedule(kind="every", every_ms=_HOUR), "b", **_bound())
        _make_due(service, a.id, b.id)
        await _wait_until(lambda: "B" in disk_while_b_runs)
    finally:
        service.stop()

    # Un singolo, dopo che è suonato, sparisce: e su disco è già sparito.
    assert "A" not in disk_while_b_runs


async def test_a_delivered_reminder_does_not_run_again_after_a_kill(tmp_path) -> None:
    """Il riavvio parte da quello che c'era su disco al momento del kill."""
    path = tmp_path / "cron" / "jobs.json"
    at_kill = tmp_path / "at-kill.json"
    ran: list[str] = []

    async def on_job(job):
        ran.append(job.name)
        if job.name == "B":
            at_kill.write_bytes(path.read_bytes())

    service = CronService(path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    try:
        a = service.add_job("A", CronSchedule(kind="at", at_ms=_now() + _HOUR), "remind", **_bound())
        b = service.add_job("B", CronSchedule(kind="every", every_ms=_HOUR), "b", **_bound())
        _make_due(service, a.id, b.id)
        await _wait_until(lambda: at_kill.exists())
    finally:
        service.stop()

    path.write_bytes(at_kill.read_bytes())
    ran.clear()
    restarted = CronService(path, on_job=on_job, max_sleep_ms=100)
    await restarted.start()
    try:
        await asyncio.sleep(0.3)
    finally:
        restarted.stop()
    assert "A" not in ran


# -- Prima di ogni job si guarda lo store di adesso, non quello d'inizio giro


async def _second_job_changed_while_first_runs(tmp_path, change) -> list[str]:
    path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    gate = asyncio.Event()
    ran: list[str] = []

    async def on_job(job):
        ran.append(job.name)
        if job.name == "A":
            started.set()
            await gate.wait()

    service = CronService(path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    try:
        a = service.add_job("A", CronSchedule(kind="every", every_ms=_HOUR), "a", **_bound())
        b = service.add_job("B", CronSchedule(kind="every", every_ms=_HOUR), "b", **_bound())
        _make_due(service, a.id, b.id)
        await asyncio.wait_for(started.wait(), 2)
        change(service, b.id)
        gate.set()
        await _wait_until(lambda: not service._timer_active)
    finally:
        service.stop()
    return ran


async def test_a_job_paused_while_the_previous_one_runs_does_not_run(tmp_path) -> None:
    def pause(service, job_id):
        assert service.set_paused(job_id, True) == "paused"

    assert await _second_job_changed_while_first_runs(tmp_path, pause) == ["A"]


async def test_a_job_removed_while_the_previous_one_runs_does_not_run(tmp_path) -> None:
    def remove(service, job_id):
        assert service.remove_job(job_id) == "removed"

    assert await _second_job_changed_while_first_runs(tmp_path, remove) == ["A"]


async def test_a_job_paused_and_resumed_meanwhile_waits_for_its_new_time(tmp_path) -> None:
    """Ripreso vuol dire «da adesso»: la scadenza vecchia non vale piu'."""
    def pause_and_resume(service, job_id):
        service.set_paused(job_id, True)
        assert service.set_paused(job_id, False) == "resumed"

    assert await _second_job_changed_while_first_runs(tmp_path, pause_and_resume) == ["A"]


# -- Una pausa arrivata mentre il job stesso gira ------------------------------


async def test_a_pause_during_the_job_own_run_leaves_no_next_run(tmp_path) -> None:
    path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    gate = asyncio.Event()

    async def on_job(job):
        started.set()
        await gate.wait()

    service = CronService(path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    try:
        job = service.add_job("x", CronSchedule(kind="every", every_ms=_HOUR), "m", **_bound())
        _make_due(service, job.id)
        await asyncio.wait_for(started.wait(), 2)
        assert service.set_paused(job.id, True) == "paused"
        gate.set()
        await _wait_until(lambda: not service._timer_active)

        live = service.get_job(job.id)
        assert live.enabled is False and live.paused_at_ms is not None
        assert live.state.next_run_at_ms is None
        assert live.state.last_status == "ok"
    finally:
        service.stop()
    assert _disk_jobs(path)["x"]["state"]["nextRunAtMs"] is None


# -- La cancellazione di ``stop()`` e il tetto al riavvio -----------------------


async def test_stop_during_a_job_cancels_it_instead_of_recording_an_error(tmp_path) -> None:
    """Il ``cancelling()`` di ``_execute_job``: lo spegnimento non e' un errore del job.

    Senza, la ``CancelledError`` di ``stop()`` veniva inghiottita come «error»,
    il job registrava un fallimento che non aveva avuto, e il task del timer
    proseguiva il giro a servizio fermo.
    """
    path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    ran: list[str] = []

    async def on_job(job):
        ran.append(job.name)
        if job.name == "A":
            started.set()
            await asyncio.Event().wait()

    service = CronService(path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    a = service.add_job("A", CronSchedule(kind="every", every_ms=_HOUR), "a", **_bound())
    b = service.add_job("B", CronSchedule(kind="every", every_ms=_HOUR), "b", **_bound())
    _make_due(service, a.id, b.id)
    await asyncio.wait_for(started.wait(), 2)
    timer = service._timer_task

    service.stop()
    await asyncio.wait([timer], timeout=2)

    assert timer.cancelled()
    assert ran == ["A"]
    interrupted = _disk_jobs(path)["A"]
    assert interrupted["state"]["lastStatus"] is None
    assert interrupted["state"]["runHistory"] == []
    # Resta dovuto: non ha finito, e al riavvio si rifa'.
    assert interrupted["state"]["nextRunAtMs"] <= _now()


async def test_a_saved_every_deadline_beyond_one_interval_is_capped_at_restart(
    tmp_path,
) -> None:
    """Un orologio saltato avanti (o un intervallo accorciato) non resta per sempre."""
    path = tmp_path / "cron" / "jobs.json"
    service = CronService(path)
    await service.start()
    job = service.add_job("x", CronSchedule(kind="every", every_ms=_HOUR), "m", **_bound())
    service._store.jobs[0].state.next_run_at_ms = _now() + 50 * _HOUR
    service._save_store()
    service.stop()

    restarted = CronService(path)
    before = _now()
    await restarted.start()
    try:
        next_run = restarted.get_job(job.id).state.next_run_at_ms
    finally:
        restarted.stop()
    assert before + _HOUR <= next_run <= _now() + _HOUR


async def test_a_saved_every_deadline_within_one_interval_is_kept_at_restart(tmp_path) -> None:
    path = tmp_path / "cron" / "jobs.json"
    service = CronService(path)
    await service.start()
    job = service.add_job("x", CronSchedule(kind="every", every_ms=_HOUR), "m", **_bound())
    saved = _now() + 10 * 60_000
    service._store.jobs[0].state.next_run_at_ms = saved
    service._save_store()
    service.stop()

    restarted = CronService(path)
    await restarted.start()
    try:
        assert restarted.get_job(job.id).state.next_run_at_ms == saved
    finally:
        restarted.stop()
