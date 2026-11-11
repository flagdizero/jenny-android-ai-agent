"""Le zone dal lato del modello: il tool ``cron`` con ``zone``.

Uno scheduler vero su un workspace temporaneo; il telefono finto (contesto
Android e posizione accesa) con un monkeypatch del rifiuto.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.cron.service import CronService

_STATION = {"name": "Stazione Centrale", "latitude": 45.4861, "longitude": 9.2042}


@pytest.fixture
def service(tmp_path) -> CronService:
    svc = CronService(tmp_path / "workspace")
    svc._arm_timer = lambda: None  # type: ignore[method-assign]
    return svc


@pytest.fixture
def tool(service, monkeypatch) -> CronTool:
    monkeypatch.setattr(CronTool, "_zone_refusal", staticmethod(lambda: None))
    t = CronTool(service, default_timezone="Europe/Rome")
    t.set_context(RequestContext(channel="websocket", chat_id="chat-1",
                                 session_key="unified:default"))
    return t


def _in_days(n: int) -> str:
    from datetime import date, timedelta

    return (date.today() + timedelta(days=n)).isoformat()


def _errors(tool: CronTool, **params: Any) -> list[str]:
    registry = ToolRegistry()
    registry.register(tool)
    return tool.validate_params({"action": "add", "message": "x", **params})


async def test_a_one_time_zone_is_armed_until_tonight(tool, service) -> None:
    out = await tool.execute(action="add", name="binario", message="dimmi il binario",
                             zone=dict(_STATION))
    assert out.startswith("Created job 'binario'")
    assert "on arrival at Stazione Centrale (within 200 m)" in out

    [job] = service.list_jobs()
    assert job.schedule.kind == "zone" and job.delete_after_run
    assert job.schedule.radius_m == 200
    assert job.schedule.until_ms is not None
    assert job.schedule.until_ms - int(time.time() * 1000) <= 2 * 86_400_000


async def test_every_arrival_may_never_expire(tool, service) -> None:
    await tool.execute(action="add", message="lista della spesa", zone=dict(_STATION),
                       every_arrival=True)
    [job] = service.list_jobs()
    assert not job.delete_after_run and job.schedule.until_ms is None


async def test_an_end_day_without_time_lasts_the_whole_day(tool, service) -> None:
    from datetime import date, timedelta

    day = (date.today() + timedelta(days=30)).isoformat()
    await tool.execute(action="add", message="x", zone=dict(_STATION), until_date=day)
    [job] = service.list_jobs()
    assert f"{day}T23:59" in tool._format_timing(job.schedule)


async def test_too_far_an_end_is_refused(tool) -> None:
    out = await tool.execute(action="add", message="x", zone=dict(_STATION),
                             until_date="2999-01-01")
    assert out.startswith("Error:") and "366" in out


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ({"every_seconds": 60}, "every_seconds"),
        ({"date": "2099-01-01"}, "date"),
        ({"mode": "monitor"}, "always speaks"),
        ({"until_time": "18:00"}, "until_time needs until_date"),
    ],
)
def test_a_zone_mixes_with_no_other_when(tool, extra, needle) -> None:
    errors = _errors(tool, zone=dict(_STATION), **extra)
    assert any(needle in e for e in errors), errors


def test_until_on_a_one_time_job_is_an_error(tool) -> None:
    assert any("ends by itself" in e for e in _errors(tool, date="2099-01-01",
                                                     until_date="2099-01-02"))
    assert any("every_arrival goes with a zone" in e
               for e in _errors(tool, every_seconds=600, every_arrival=True))


async def test_no_position_no_zone(service, monkeypatch) -> None:
    t = CronTool(service, default_timezone="UTC")
    t.set_context(RequestContext(channel="websocket", chat_id="chat-1",
                                 session_key="unified:default"))
    out = await t.execute(action="add", message="x", zone=dict(_STATION))
    assert out.startswith("Error:") and "not running on the phone" in out
    assert service.list_jobs() == []


async def test_the_reply_says_where_the_phone_is(tool, service, monkeypatch) -> None:
    from jenny.runtime import location

    here = location.LocationFix(45.4861, 9.2042, 10.0, int(time.time() * 1000))
    monkeypatch.setattr(location, "cached_location", lambda cfg: here)
    out = await tool.execute(action="add", message="x", zone=dict(_STATION))
    assert "already there" in out


async def test_list_tells_a_zone_apart(tool, service) -> None:
    await tool.execute(action="add", name="binario", message="x", zone=dict(_STATION))
    listing = await tool.execute(action="list")
    assert "on arrival at Stazione Centrale" in listing
    assert "Phone: not known yet" in listing
    assert "Next run" not in listing
