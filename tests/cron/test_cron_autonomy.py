"""Jenny libera: svegliata da un job si mette quel che le serve, e lo firma.

Fino all'11/11/2026 da un turno di cron passava solo una zona «una volta» che
finiva entro una settimana; il resto era rifiutato, e una riga del mattino non
poteva mettersi l'avviso per il treno del pomeriggio. Adesso non c'è un divieto:
c'è una firma (``payload.autonomous``), un «fino a» per i ricorrenti, e il conto
dei risvegli che la casa mostra quando sono troppi.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.cron import service as service_module
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule
from jenny.webui.cron_api import WAKES_ALERT, webui_cron_payload

_STATION = {"name": "Stazione Centrale", "latitude": 45.4861, "longitude": 9.2042}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bound_chat() -> dict[str, str]:
    return {"session_key": "websocket:chat-1", "origin_channel": "websocket",
            "origin_chat_id": "chat-1"}


@pytest.fixture
def service(tmp_path) -> CronService:
    async def ring(job):
        return "detto"

    svc = CronService(tmp_path / "workspace", on_job=ring)
    svc._arm_timer = lambda: None  # type: ignore[method-assign]
    svc._running = True
    svc._load_store()
    return svc


@pytest.fixture
def tool(service, monkeypatch) -> CronTool:
    monkeypatch.setattr(CronTool, "_zone_refusal", staticmethod(lambda: None))
    t = CronTool(service, default_timezone="Europe/Rome")
    t.set_context(RequestContext(channel="websocket", chat_id="chat-1",
                                 session_key="unified:default"))
    return t


def _day(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


# ── svegliata da un job ──────────────────────────────────────────────────────


async def test_from_a_scheduled_run_she_sets_whatever_she_needs(tool, service) -> None:
    token = tool.set_cron_context(True)
    try:
        once = await tool.execute(action="add", name="treno", message="il treno delle 14:20",
                                  date=_day(1), time="13:50")
        check = await tool.execute(action="add", name="volo", message="ritardi e gate",
                                   every_seconds=900, mode="monitor", until_date=_day(1))
        place = await tool.execute(action="add", name="santuario", message="la storia",
                                   zone=dict(_STATION), every_arrival=True)
    finally:
        tool.reset_cron_context(token)
    assert once.startswith("Created job") and check.startswith("Created job")
    assert place.startswith("Created job")
    jobs = service.list_jobs()
    assert sorted(j.schedule.kind for j in jobs) == ["at", "every", "zone"]
    assert all(j.payload.autonomous for j in jobs)


async def test_what_the_user_asks_in_chat_is_not_signed(tool, service) -> None:
    await tool.execute(action="add", name="piante", message="annaffia", every_seconds=3600)
    [job] = service.list_jobs()
    assert job.payload.autonomous is False
    assert "set by you" not in await tool.execute(action="list")


async def test_the_signature_survives_the_store_and_shows_in_the_list(tool, service) -> None:
    token = tool.set_cron_context(True)
    try:
        await tool.execute(action="add", name="treno", message="x", date=_day(1), time="13:50")
    finally:
        tool.reset_cron_context(token)
    fresh = CronService(service.workspace)
    [job] = fresh.list_jobs()
    assert job.payload.autonomous is True
    assert "set by you during a scheduled run" in await tool.execute(action="list")
    [row] = webui_cron_payload(fresh, config=object())["jobs"]
    assert row["autonomous"] is True


# ── «fino a» su un ricorrente ────────────────────────────────────────────────


async def test_a_recurring_job_can_have_an_end(tool, service) -> None:
    out = await tool.execute(action="add", name="volo", message="ritardi e gate",
                             every_seconds=900, until_date=_day(1), until_time="10:35")
    assert out.startswith("Created job 'volo'") and "until" in out
    [job] = service.list_jobs()
    assert job.schedule.until_ms is not None and job.schedule.until_ms > _now_ms()
    assert "until" in await tool.execute(action="list")
    fresh = CronService(service.workspace)
    assert fresh.list_jobs()[0].schedule.until_ms == job.schedule.until_ms


async def test_an_end_before_the_first_run_is_refused(tool) -> None:
    out = await tool.execute(action="add", message="x", every_seconds=3 * 86_400,
                             until_date=_day(1))
    assert out.startswith("Error:") and "before the first run" in out


async def test_after_its_last_run_the_job_goes_away(service, monkeypatch) -> None:
    now = _now_ms()
    job = service.add_job(name="volo", message="x", **_bound_chat(),
                          schedule=CronSchedule(kind="every", every_ms=60_000,
                                                until_ms=now + 90_000))
    # Il primo giro è dentro la scadenza; il prossimo cadrebbe dopo.
    monkeypatch.setattr(service_module, "_now_ms", lambda: now + 61_000)
    await service._on_timer()
    assert service.get_job(job.id) is None


async def test_one_that_expired_while_paused_is_dropped(service, monkeypatch) -> None:
    now = _now_ms()
    job = service.add_job(name="volo", message="x", **_bound_chat(),
                          schedule=CronSchedule(kind="every", every_ms=60_000,
                                                until_ms=now + 90_000))
    service.set_paused(job.id, True)
    monkeypatch.setattr(service_module, "_now_ms", lambda: now + 200_000)
    assert service.set_paused(job.id, False) == "expired"
    assert await service.drop_expired_recurring(now + 200_000) == [job.id]
    assert service.list_jobs(include_disabled=True) == []


def test_a_job_without_an_end_is_stored_as_before(service) -> None:
    job = service.add_job(name="piante", message="x", **_bound_chat(),
                          schedule=CronSchedule(kind="every", every_ms=60_000))
    row = CronService._job_to_dict(job)
    assert "untilMs" not in row["schedule"] and "autonomous" not in row["payload"]


# ── i risvegli ───────────────────────────────────────────────────────────────


def test_wakes_are_counted_per_job_with_the_last_name(service) -> None:
    flight = service.add_job(name="volo", message="x", **_bound_chat(), autonomous=True,
                             schedule=CronSchedule(kind="every", every_ms=60_000))
    plants = service.add_job(name="piante", message="x", **_bound_chat(),
                             schedule=CronSchedule(kind="every", every_ms=60_000))
    for _ in range(3):
        service.note_wake(flight)
    service.note_wake(plants)
    rows = service.wakes(_now_ms() - 60_000)
    assert [(r["name"], r["count"], r["autonomous"]) for r in rows] == [
        ("volo", 3, True), ("piante", 1, False)]
    assert service.wakes(_now_ms() + 60_000) == []


def test_a_fired_one_time_job_still_counts(service) -> None:
    job = service.add_job(name="treno", message="x", **_bound_chat(),
                          schedule=CronSchedule(kind="at", at_ms=_now_ms() + 60_000))
    service.note_wake(job)
    service.remove_job(job.id)
    [row] = service.wakes(_now_ms() - 60_000)
    assert row["name"] == "treno" and row["count"] == 1


def test_the_payload_carries_the_count_and_the_threshold(service) -> None:
    job = service.add_job(name="volo", message="x", **_bound_chat(), autonomous=True,
                          schedule=CronSchedule(kind="every", every_ms=60_000))
    service.note_wake(job)
    service.note_wake(job)
    wakes = webui_cron_payload(service, config=object())["wakes"]
    assert wakes["total"] == 2 and wakes["threshold"] == WAKES_ALERT
    assert wakes["jobs"] == [{"id": job.id, "name": "volo", "autonomous": True, "count": 2,
                              "last_ms": wakes["jobs"][0]["last_ms"]}]


def test_a_cron_without_the_count_still_lists(service) -> None:
    class Old:
        def status(self):
            return {"enabled": True}

        def list_jobs(self, include_disabled=False):
            return []

    assert webui_cron_payload(Old(), config=object())["wakes"]["total"] == 0
