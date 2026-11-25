"""«Annulla» dopo aver tolto un promemoria: ``CronService.restore_job``.

La stanza Promemoria della casa toglie un promemoria con il cestino e offre
«Annulla» per qualche secondo, come le app e le schede della bacheca. Fino al
25/11/2026 il servizio non sapeva rimettere un job tolto: l'unica strada era
chiederne uno nuovo a Jenny, con un altro id e senza la sua storia. Ora il job
tolto resta in memoria per ``RESTORE_WINDOW_MS`` e torna com'era.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from jenny.cron import service as cron_service
from jenny.cron import store as cron_store
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

_HOUR = 3_600_000


def _bound() -> dict[str, str]:
    return {
        "session_key": "websocket:chat-1",
        "origin_channel": "websocket",
        "origin_chat_id": "chat-1",
    }


@pytest.fixture
async def running(tmp_path):
    service = CronService(tmp_path / "workspace", on_job=lambda _: asyncio.sleep(0))
    service._running = True
    yield service
    service.stop()


async def test_a_removed_job_comes_back_with_its_id_and_history(running) -> None:
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.write_run_record("r1", {"job_id": job.id, "status": "ok"})
    assert service.remove_job(job.id) == "removed"
    assert service.get_job(job.id) is None
    assert cron_store.read_runs(service._db, job.id) == []

    assert service.restore_job(job.id) == "restored"

    back = service.get_job(job.id)
    assert back is not None
    assert back.name == "acqua"
    assert back.enabled is True
    assert [r["run_id"] for r in cron_store.read_runs(service._db, job.id)] == ["r1"]
    assert service.restore_job(job.id) == "not_found", "rimesso due volte"


async def test_a_restored_job_counts_from_now_and_does_not_catch_up(running) -> None:
    """Tolto e rimesso dopo un po', un «ogni ora» non scatta subito per
    recuperare: riparte fra un'ora, come dopo una pausa."""
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.remove_job(job.id)
    now = int(time.time() * 1000)
    assert service.restore_job(job.id) == "restored"
    nxt = service.get_job(job.id).state.next_run_at_ms
    assert now + _HOUR - 5_000 <= nxt <= now + _HOUR + 5_000


async def test_a_paused_job_comes_back_paused(running) -> None:
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.set_paused(job.id, True)
    service.remove_job(job.id)
    assert service.restore_job(job.id) == "restored"
    back = service.get_job(job.id)
    assert back.enabled is False
    assert back.paused_at_ms is not None


async def test_a_one_shot_that_passed_while_removed_comes_back_paused(running) -> None:
    """Rimetterlo acceso lo farebbe scattare subito, in ritardo."""
    service = running
    at = int(time.time() * 1000) + 60_000
    job = service.add_job(
        name="dentista", schedule=CronSchedule(kind="at", at_ms=at), message="vai", **_bound()
    )
    service.remove_job(job.id)
    kept = service._removed_recently[job.id][0]
    kept.schedule.at_ms = int(time.time() * 1000) - 1_000

    assert service.restore_job(job.id) == "restored"
    back = service.get_job(job.id)
    assert back.enabled is False
    assert back.paused_at_ms is not None


async def test_the_window_closes(running, monkeypatch) -> None:
    service = running
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.remove_job(job.id)
    later = int(time.time() * 1000) + cron_service.RESTORE_WINDOW_MS + 1_000
    monkeypatch.setattr(cron_service, "_now_ms", lambda: later)
    assert service.restore_job(job.id) == "not_found"
    assert service.get_job(job.id) is None


async def test_unknown_ids_are_not_found_and_a_live_id_is_not_overwritten(running) -> None:
    service = running
    assert service.restore_job("nope") == "not_found"
    job = service.add_job(
        name="acqua", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="bevi", **_bound()
    )
    service.remove_job(job.id)
    service._load_store().jobs.append(service._removed_recently[job.id][0])
    service._save_store()
    assert service.restore_job(job.id) == "taken"


async def test_only_the_last_twenty_are_kept(running) -> None:
    service = running
    ids = []
    for i in range(cron_service.RESTORE_KEEP + 3):
        job = service.add_job(
            name=f"j{i}", schedule=CronSchedule(kind="every", every_ms=_HOUR), message="x", **_bound()
        )
        ids.append(job.id)
        service.remove_job(job.id)
    assert len(service._removed_recently) == cron_service.RESTORE_KEEP
    assert service.restore_job(ids[-1]) == "restored"
