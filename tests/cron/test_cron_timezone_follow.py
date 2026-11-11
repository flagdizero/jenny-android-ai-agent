"""I promemoria seguono il fuso del telefono.

Fino all'11/11/2026 il fuso si leggeva una volta all'avvio, e un ricorrente lo
portava scritto dal giorno in cui era nato: atterrati a Tokyo, «ogni giorno alle
7» suonava alle 7 di Roma, cioè alle 15. Adesso ogni ora scritta su un job è
un'ora dell'orologio dell'utente, e quando il telefono cambia fuso tutti i job
lo seguono, senza eccezioni.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.cron import store as cron_store
from jenny.cron.service import CronService, _same_wall_clock
from jenny.cron.types import CronSchedule

ROME = "Europe/Rome"
TOKYO = "Asia/Tokyo"
LISBON = "Europe/Lisbon"


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _wall(ms: int, tz: str) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz)).replace(tzinfo=None)


def _in(days: int, hour: int, minute: int, tz: str) -> datetime:
    """Fra *days* giorni a quell'ora, nel fuso *tz*; il giorno è lo stesso per ogni fuso."""
    day = datetime.now(ZoneInfo(ROME)).date() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(tz))


def _service(tmp_path, tz: str | None = ROME) -> CronService:
    async def ring(job):
        return "detto"

    svc = CronService(tmp_path / "workspace", on_job=ring)
    svc._arm_timer = lambda: None  # type: ignore[method-assign]
    svc._running = True
    svc._load_store()
    if tz:
        svc.follow_timezone(tz)
    return svc


@pytest.fixture
def service(tmp_path) -> CronService:
    return _service(tmp_path)


def _bound() -> dict[str, str]:
    return {"session_key": "websocket:chat-1", "origin_channel": "websocket",
            "origin_chat_id": "chat-1"}


# ── la prima volta ───────────────────────────────────────────────────────────


def test_the_first_time_it_only_takes_note(tmp_path) -> None:
    svc = _service(tmp_path, tz=None)
    svc.add_job("sveglia", CronSchedule(kind="cron", expr="0 7 * * *", tz=ROME), "su", **_bound())

    assert svc.follow_timezone(TOKYO) == 0

    assert svc.list_jobs()[0].schedule.tz == ROME
    assert cron_store.read_meta(svc._db, cron_store.META_TIMEZONE) == TOKYO
    assert svc.timezone == TOKYO


def test_the_same_timezone_moves_nothing(service) -> None:
    at = _ms(_in(3, 9, 0, ROME))
    service.add_job("visita", CronSchedule(kind="at", at_ms=at), "vai", **_bound())

    assert service.follow_timezone(ROME) == 0
    assert service.list_jobs()[0].schedule.at_ms == at


def test_no_timezone_is_not_a_change(service) -> None:
    assert service.follow_timezone(None) == 0
    assert service.follow_timezone("  ") == 0
    assert service.timezone == ROME


# ── il telefono cambia fuso ──────────────────────────────────────────────────


def test_a_daily_job_rings_at_the_same_hour_of_the_new_place(service) -> None:
    job = service.add_job("sveglia", CronSchedule(kind="cron", expr="0 7 * * *", tz=ROME), "su",
                          **_bound())

    assert service.follow_timezone(TOKYO) == 1

    moved = service.get_job(job.id)
    assert moved.schedule.tz == TOKYO
    nxt = _wall(moved.state.next_run_at_ms, TOKYO)
    assert (nxt.hour, nxt.minute) == (7, 0)


def test_a_timezone_named_on_the_job_follows_too(service) -> None:
    # Nessuna eccezione: anche il job nato con un fuso suo segue il telefono.
    job = service.add_job("borsa", CronSchedule(kind="cron", expr="30 9 * * *",
                                                tz="America/New_York"), "apre", **_bound())

    service.follow_timezone(TOKYO)

    assert service.get_job(job.id).schedule.tz == TOKYO


def test_a_one_time_job_keeps_its_wall_clock(service) -> None:
    job = service.add_job("museo", CronSchedule(kind="at", at_ms=_ms(_in(5, 9, 0, ROME))),
                          "ingresso alle 9", **_bound())

    assert service.follow_timezone(TOKYO) == 1

    moved = service.get_job(job.id)
    assert moved.schedule.at_ms == _ms(_in(5, 9, 0, TOKYO))
    assert moved.state.next_run_at_ms == moved.schedule.at_ms


def test_a_one_time_job_that_lands_in_the_past_rings_late(service) -> None:
    # Fra due ore a Roma: a Tokyo quell'ora d'orologio è già passata da sei.
    soon = datetime.now(ZoneInfo(ROME)) + timedelta(hours=2)
    job = service.add_job("chiama", CronSchedule(kind="at", at_ms=_ms(soon)), "l'hotel", **_bound())

    service.follow_timezone(TOKYO)

    moved = service.get_job(job.id)
    assert moved.enabled
    assert moved.state.next_run_at_ms == moved.schedule.at_ms
    assert moved.schedule.at_ms < _ms(datetime.now(ZoneInfo(ROME)))


def test_an_interval_is_not_a_clock_time_but_its_end_is(service) -> None:
    end = _ms(_in(1, 23, 59, ROME))
    job = service.add_job("ritardi", CronSchedule(kind="every", every_ms=300_000, until_ms=end),
                          "guarda", mode="monitor", **_bound())
    before = service.get_job(job.id).state.next_run_at_ms

    service.follow_timezone(LISBON)

    moved = service.get_job(job.id)
    assert moved.schedule.every_ms == 300_000
    assert moved.state.next_run_at_ms == before
    assert moved.schedule.until_ms == _ms(_in(1, 23, 59, LISBON))


def test_a_paused_job_moves_too(service) -> None:
    job = service.add_job("museo", CronSchedule(kind="at", at_ms=_ms(_in(5, 9, 0, ROME))),
                          "ingresso", **_bound())
    service.set_paused(job.id, True)

    service.follow_timezone(TOKYO)

    moved = service.get_job(job.id)
    assert moved.paused_at_ms is not None
    assert moved.schedule.at_ms == _ms(_in(5, 9, 0, TOKYO))


def test_going_and_coming_back_puts_the_hours_back(service) -> None:
    at = _ms(_in(5, 9, 0, ROME))
    job = service.add_job("museo", CronSchedule(kind="at", at_ms=at), "ingresso", **_bound())

    service.follow_timezone(TOKYO)
    service.follow_timezone(ROME)

    assert service.get_job(job.id).schedule.at_ms == at


# ── a gateway spento ─────────────────────────────────────────────────────────


async def test_a_change_while_the_gateway_was_down_is_followed_at_start(tmp_path) -> None:
    first = _service(tmp_path)
    job = first.add_job("sveglia", CronSchedule(kind="cron", expr="0 7 * * *", tz=ROME), "su",
                        **_bound())

    second = CronService(tmp_path / "workspace")
    second._arm_timer = lambda: None  # type: ignore[method-assign]
    second.timezone = TOKYO
    await second.start()
    try:
        moved = second.get_job(job.id)
        assert moved.schedule.tz == TOKYO
        assert cron_store.read_meta(second._db, cron_store.META_TIMEZONE) == TOKYO
    finally:
        second.stop()


# ── lo strumento ─────────────────────────────────────────────────────────────


async def test_the_tool_reads_a_time_in_the_timezone_of_now(service) -> None:
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(RequestContext(channel="websocket", chat_id="chat-1",
                                    session_key="unified:default"))
    assert tool._default_timezone == ROME

    service.follow_timezone(TOKYO)

    assert tool._default_timezone == TOKYO
    assert TOKYO in tool.description
    day = _in(2, 8, 30, TOKYO)
    await tool.execute(action="add", name="treno", message="parte",
                       date=day.date().isoformat(), time="08:30")
    assert service.list_jobs()[0].schedule.at_ms == _ms(day)


def test_the_tool_without_a_live_timezone_keeps_its_own(tmp_path) -> None:
    svc = CronService(tmp_path / "workspace")

    assert CronTool(svc, default_timezone="Asia/Shanghai")._default_timezone == "Asia/Shanghai"


# ── il conto ─────────────────────────────────────────────────────────────────


def test_same_wall_clock() -> None:
    rome_nine = datetime(2027, 4, 14, 9, 0, tzinfo=ZoneInfo(ROME))

    moved = _same_wall_clock(_ms(rome_nine), ROME, TOKYO)

    assert _wall(moved, TOKYO) == datetime(2027, 4, 14, 9, 0)
    assert moved == _ms(rome_nine) - 7 * 3600 * 1000
