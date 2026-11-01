"""Il cron deve restare utilizzabile anche senza database tzdata (Android).

Regressione: su Android/Chaquopy senza il wheel ``tzdata`` persino
``ZoneInfo("UTC")`` solleva, e il tool rifiutava ogni job con
"unknown timezone 'UTC'". Qui l'assenza di tzdata è simulata
monkeypatchando ``jenny.utils.helpers.ZoneInfo``.
"""

import time
from zoneinfo import ZoneInfoNotFoundError

import pytest

import jenny.utils.helpers as helpers
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.cron.service import CronService, _compute_next_run, _validate_schedule_for_add
from jenny.cron.types import CronSchedule


def _make_tool_with_tz(tmp_path, tz: str) -> CronTool:
    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service, default_timezone=tz)
    tool.set_context(
        RequestContext(channel="websocket", chat_id="chat-1", session_key="websocket:chat-1")
    )
    return tool


@pytest.fixture
def broken_tzdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula l'assenza totale del database tzdata (anche 'UTC' fallisce)."""

    def _raise(key: str) -> None:
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(helpers, "ZoneInfo", _raise)


async def test_add_naive_at_job_without_tzdata(tmp_path, broken_tzdata: None) -> None:
    tool = _make_tool_with_tz(tmp_path, "Europe/Rome")

    result = await tool.execute(action="add", message="Reminder", date="2030-03-25", time="08:00")

    assert result.startswith("Created job"), result
    job = tool._cron.list_jobs()[0]
    assert job.schedule.at_ms is not None and job.schedule.at_ms > 0


async def test_add_cron_expr_job_with_tz_without_tzdata(tmp_path, broken_tzdata: None) -> None:
    tool = _make_tool_with_tz(tmp_path, "Europe/Rome")

    result = await tool.execute(action="add", message="Standup", cron_expr="0 8 * * *", tz="Europe/Rome")

    assert result.startswith("Created job"), result


def test_service_validate_and_next_run_without_tzdata(broken_tzdata: None) -> None:
    schedule = CronSchedule(kind="cron", expr="0 8 * * *", tz="Europe/Rome")

    _validate_schedule_for_add(schedule)  # non deve sollevare

    next_run = _compute_next_run(schedule, int(time.time() * 1000))
    assert next_run is not None and next_run > 0


async def test_unknown_tz_still_rejected_with_tzdata(tmp_path) -> None:
    # Controllo (senza patch): la validazione stretta resta attiva su host.
    tool = _make_tool_with_tz(tmp_path, "Europe/Rome")

    result = await tool.execute(action="add", message="Standup", cron_expr="0 8 * * *", tz="Not/AZone")
    assert result == "Error: unknown timezone 'Not/AZone'"

    with pytest.raises(ValueError, match="unknown timezone 'Not/AZone'"):
        _validate_schedule_for_add(CronSchedule(kind="cron", expr="0 8 * * *", tz="Not/AZone"))
