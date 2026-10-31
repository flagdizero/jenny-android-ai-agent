"""Il conto alla rovescia di un job ``every`` sopravvive al riavvio.

Prima di questi test ogni avvio rimetteva ``next_run_at_ms`` a ``now +
intervallo``: "ogni N ore" valeva solo con N ore di uptime ininterrotto, e su
Android un job lungo dodici ore poteva non scattare mai.
"""

import pytest

from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronPayload, CronSchedule

_HOUR_MS = 3_600_000


def _dream_job(interval_h: int = 12) -> CronJob:
    return CronJob(
        id="dream",
        name="dream",
        schedule=CronSchedule(kind="every", every_ms=interval_h * _HOUR_MS),
        payload=CronPayload(kind="system_event"),
    )


def _now_ms() -> int:
    from jenny.cron.service import _now_ms as impl

    return impl()


@pytest.mark.asyncio
async def test_restart_does_not_push_the_deadline_forward(tmp_path) -> None:
    """Due avvii ravvicinati non rimandano la scadenza di altre 12 ore."""
    path = tmp_path / "cron" / "jobs.json"

    first = CronService(path)
    first.register_system_job(_dream_job())
    await first.start()
    deadline = first.get_job("dream").state.next_run_at_ms
    first.stop()

    # Riavvio: stesso store su disco, stesso job di sistema.
    second = CronService(path)
    second.register_system_job(_dream_job())
    await second.start()
    try:
        assert second.get_job("dream").state.next_run_at_ms == deadline
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_missed_deadline_is_recovered_not_rescheduled(tmp_path) -> None:
    """Una scadenza passata a app spenta resta passata: la recupera il tick."""
    path = tmp_path / "cron" / "jobs.json"

    first = CronService(path)
    first.register_system_job(_dream_job())
    first.stop()

    overdue = _now_ms() - 60_000
    first.get_job("dream").state.next_run_at_ms = overdue
    first._save_store()

    second = CronService(path)
    second.register_system_job(_dream_job())
    await second.start()
    try:
        assert second.get_job("dream").state.next_run_at_ms == overdue
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_shorter_interval_applies_immediately(tmp_path) -> None:
    """Accorciare l'intervallo vale subito, non dopo la vecchia scadenza."""
    path = tmp_path / "cron" / "jobs.json"

    first = CronService(path)
    first.register_system_job(_dream_job(interval_h=12))
    await first.start()
    long_deadline = first.get_job("dream").state.next_run_at_ms
    first.stop()

    second = CronService(path)
    second.register_system_job(_dream_job(interval_h=2))
    await second.start()
    try:
        rescheduled = second.get_job("dream").state.next_run_at_ms
        assert rescheduled < long_deadline
        assert rescheduled <= _now_ms() + 2 * _HOUR_MS
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_user_recurring_job_also_keeps_its_deadline(tmp_path) -> None:
    """La correzione non riguarda solo i job di sistema."""
    path = tmp_path / "cron" / "jobs.json"

    first = CronService(path)
    await first.start()
    job = first.add_job(
        name="promemoria",
        schedule=CronSchedule(kind="every", every_ms=24 * _HOUR_MS),
        message="ricordati",
        session_key="websocket:chat-1",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    deadline = job.state.next_run_at_ms
    first.stop()

    second = CronService(path)
    await second.start()
    try:
        assert second.get_job(job.id).state.next_run_at_ms == deadline
    finally:
        second.stop()
