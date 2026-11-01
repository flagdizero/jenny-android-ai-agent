"""L'etichetta di un job: quando (data a parole), filtro, perché — e i singoli che spariscono.

Un contenitore solo per il «più tardi» (31/10/2026): una data senza ora diventa
un ``at`` alle 9, un mese il primo alle 9; una condizione è un ricorrente con un
filtro che si guarda nel codice, prima del modello; un singolo, dopo che è
suonato, sparisce sempre.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from support.sessions import FakeSessions

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage
from jenny.cron.bound_runner import run_bound_cron_job
from jenny.cron.condition import ConditionReading
from jenny.cron.dates import date_schedule, schedule_date
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

_TZ = "Europe/Rome"
_COND = {"app": "serra", "params": {}, "field": "umidita", "op": "<", "value": 15}


def test_a_day_rings_at_nine_and_a_month_on_the_first() -> None:
    day = date_schedule("2026-11-12", _TZ)
    month = date_schedule("2027-03", _TZ)
    exact = date_schedule("2026-11-12T18:30", _TZ)

    def local(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(_TZ))

    assert (day.kind, day.precision) == ("at", "day")
    assert local(day.at_ms).timetuple()[:5] == (2026, 11, 12, 9, 0)
    assert local(month.at_ms).timetuple()[:5] == (2027, 3, 1, 9, 0)
    assert month.precision == "month"
    assert local(exact.at_ms).timetuple()[:5] == (2026, 11, 12, 18, 30)
    assert schedule_date(month, _TZ) == "2027-03"
    assert schedule_date(day, _TZ) == "2026-11-12"
    with pytest.raises(ValueError):
        date_schedule("verso marzo", _TZ)


class _Agent:
    def __init__(self) -> None:
        self.tools = ToolRegistry()
        self.sessions = FakeSessions()
        self.prompts: list[str] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        return TurnOutcome.silent(final_text="ok")


def _service(tmp_path: Path, agent: _Agent) -> CronService:
    service = CronService(tmp_path / "cron" / "jobs.json")

    async def on_job(job: Any) -> str | None:
        return await run_bound_cron_job(job, agent=agent, cron=service)

    service.on_job = on_job
    return service


def _add(service: CronService, schedule: CronSchedule, **kw: Any):
    return service.add_job(
        name="serra", schedule=schedule, message="avvisami se la serra è secca",
        session_key="unified:default", origin_channel="websocket", origin_chat_id="default",
        **kw,
    )


async def test_a_condition_costs_nothing_while_false_and_speaks_once_when_true(
    tmp_path, monkeypatch,
) -> None:
    readings = iter([
        ConditionReading(False, 30), ConditionReading(True, 12), ConditionReading(True, 11),
        ConditionReading(False, 20), ConditionReading(True, 13),
    ])

    async def fake_read(condition, **_kw):
        assert condition == _COND
        return next(readings)

    monkeypatch.setattr("jenny.cron.bound_runner.read_condition", fake_read)
    agent = _Agent()
    service = _service(tmp_path, agent)
    job = _add(service, CronSchedule(kind="every", every_ms=6 * 3_600_000), condition=_COND)

    turns = []
    for _ in range(5):
        await service.run_job(job.id)
        turns.append(len(agent.prompts))

    # falsa: nessun turno; vera: un turno; ancora vera: niente; falsa: riarma; vera: un turno.
    assert turns == [0, 1, 1, 1, 2]
    assert "umidita < 15" in agent.prompts[0] and "12" in agent.prompts[0]
    assert service.get_job(job.id).state.condition_notified is True


async def test_an_unreadable_app_skips_the_run_without_a_turn(tmp_path, monkeypatch) -> None:
    async def fake_read(condition, **_kw):
        return ConditionReading(False, error="no fresh value from the app serra")

    monkeypatch.setattr("jenny.cron.bound_runner.read_condition", fake_read)
    agent = _Agent()
    service = _service(tmp_path, agent)
    job = _add(service, CronSchedule(kind="every", every_ms=3_600_000), condition=_COND)

    await service.run_job(job.id)

    assert agent.prompts == []
    state = service.get_job(job.id).state
    assert state.last_status == "skipped"
    assert "no fresh value" in (state.last_error or "")


def test_a_condition_on_a_one_time_job_is_refused(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    with pytest.raises(ValueError, match="recurring"):
        _add(service, date_schedule("2026-11-12", _TZ), condition=_COND)


def test_the_label_survives_a_restart(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    job = _add(service, CronSchedule(kind="every", every_ms=3_600_000), condition=_COND)
    one = _add(service, date_schedule("2027-03", _TZ))

    reloaded = CronService(tmp_path / "cron" / "jobs.json")
    assert reloaded.get_job(job.id).payload.condition == _COND
    assert reloaded.get_job(one.id).schedule.precision == "month"


async def test_a_one_time_job_disappears_once_it_has_fired(tmp_path) -> None:
    agent = _Agent()
    service = _service(tmp_path, agent)
    job = _add(service, CronSchedule(kind="at", at_ms=int(datetime.now().timestamp() * 1000) + 3_600_000))
    assert job.delete_after_run is True

    await service.run_job(job.id, force=True)

    assert service.get_job(job.id) is None
    assert len(agent.prompts) == 1


def test_update_job_changes_text_and_when(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    job = _add(service, date_schedule("2026-11-12", _TZ))

    updated = service.update_job(job.id, name="ciliegio", message="dividi il ciliegio",
                                 schedule=date_schedule("2026-12", _TZ))

    assert updated is not None
    assert updated.name == "ciliegio"
    assert updated.schedule.precision == "month"
    assert updated.state.next_run_at_ms == updated.schedule.at_ms
    assert service.update_job("missing", name="x") is None


def _tool(tmp_path: Path, key: str = "unified:default") -> CronTool:
    tool = CronTool(CronService(tmp_path / "cron" / "jobs.json"), default_timezone=_TZ)
    chat = "default" if key == "unified:default" else key
    tool.set_context(RequestContext(channel="websocket", chat_id=chat, session_key=key))
    return tool


@pytest.fixture
def casa(tmp_path: Path, monkeypatch) -> Path:
    """Un quaderno senza bacheca: un job sul quaderno intero si accetta lo stesso."""
    root = tmp_path / "workspace" / "wikis" / "casa"
    (root / "wiki").mkdir(parents=True)
    monkeypatch.setattr("jenny.board.cron_link.notebook_root",
                        lambda name: root if name == "casa" else None)
    return root


async def test_a_notebook_job_from_the_personal_chat_runs_and_delivers_in_the_notebook(
    tmp_path, casa,
) -> None:
    tool = _tool(tmp_path)

    out = await tool.execute(action="add", notebook="casa", name="ciliegio",
                             message="ricorda di dividere il ciliegio", date="2026-11-12")

    assert out.startswith("Created job"), out
    job = tool._cron.list_jobs(include_disabled=True)[0]
    assert job.payload.board["notebook"] == "casa"
    assert job.payload.session_key == "project:casa"
    assert job.payload.origin_chat_id == "project:casa"
    assert job.schedule.precision == "day"


async def test_a_notebook_chat_cannot_schedule_for_another_notebook(tmp_path, casa) -> None:
    tool = _tool(tmp_path, key="project:casa")
    out = await tool.execute(action="add", notebook="orto", message="x", every_seconds=3600)
    assert out.startswith("Not from here")


async def test_a_condition_without_a_cadence_is_refused(tmp_path) -> None:
    tool = _tool(tmp_path)
    errors = tool.validate_params({"action": "add", "message": "x", "condition": _COND})
    assert any("the cadence it is checked at" in e for e in errors)


async def test_pause_and_resume_from_the_tool(tmp_path) -> None:
    tool = _tool(tmp_path)
    await tool.execute(action="add", message="x", every_seconds=3600)
    job = tool._cron.list_jobs()[0]

    assert (await tool.execute(action="pause", job_id=job.id)).startswith("Paused")
    assert tool._cron.get_job(job.id).paused_at_ms is not None
    assert (await tool.execute(action="resume", job_id=job.id)).startswith("Resumed")



def test_the_hour_is_a_field_of_its_own(tmp_path) -> None:
    """Il 01/11/2026, su jenny_s22, «il 20 novembre ricordami…» diventava
    ``at="…T09:00:00"``: con l'ora dentro la data il modello doveva inventarla
    per riempire un ISO. Ora l'ora è ``time``, che si omette; ``at`` non c'è."""
    tool = CronTool(CronService(tmp_path / "cron" / "jobs.json"), default_timezone=_TZ)
    props = tool.parameters["properties"]
    assert "at" not in props
    assert "never pick an hour" in props["time"]["description"]


async def test_a_day_without_time_is_a_day_and_with_time_is_exact(tmp_path) -> None:
    tool = _tool(tmp_path)
    day = await tool.execute(action="add", message="bici", date="2026-11-20")
    nine = await tool.execute(action="add", message="dentista", date="2026-11-20", time="09:00")
    by_msg = {j.payload.message: j.schedule for j in tool._cron.list_jobs()}
    assert by_msg["bici"].precision == "day"
    # Chi dice davvero «alle 9» ha le 9: niente più tappo che le scambia per un giorno.
    assert by_msg["dentista"].precision == "exact"
    assert by_msg["bici"].at_ms == by_msg["dentista"].at_ms
    # La risposta dice quando, e che un giorno non ha un'ora.
    assert "once, on 2026-11-20 (no time" in day
    assert "at 2026-11-20T09:00" in nine


@pytest.mark.parametrize(("params", "needle"), [
    ({"at": "2026-11-20T09:00:00"}, "`at` does not exist"),
    ({"time": "18:30"}, "time needs a date"),
    ({"date": "2026-11-20", "every_seconds": 3600}, "not together with every_seconds"),
])
def test_the_old_and_the_mixed_shapes_are_refused(tmp_path, params, needle) -> None:
    tool = _tool(tmp_path)
    errors = tool.validate_params({"action": "add", "message": "x", **params})
    assert any(needle in e for e in errors), errors


@pytest.mark.parametrize(("date", "time_of_day", "needle"), [
    ("2026-11-20T09:00", None, "put the hour in time"),
    ("2027-03", "09:00", "not with a month"),
    ("2026-11-20", "9", "use HH:MM"),
])
async def test_a_time_hidden_in_the_date_or_on_a_month_is_refused(tmp_path, date, time_of_day,
                                                                  needle) -> None:
    tool = _tool(tmp_path)
    out = await tool.execute(action="add", message="x", date=date, time=time_of_day)
    assert out.startswith("Error:") and needle in out, out
    assert tool._cron.list_jobs() == []


def test_a_one_time_date_already_past_is_refused_everywhere(tmp_path) -> None:
    """Il 01/11/2026, su jenny_s22, la passata della bacheca ha scritto «verso
    marzo» come ``2026-03``: il job è nato scaduto ed è suonato subito, otto mesi
    prima. Ora ``CronService`` lo rifiuta, per chiunque scriva."""
    service = CronService(tmp_path / "cron" / "jobs.json")
    last_march = date_schedule(f"{datetime.now().year - 1}-03", _TZ)
    with pytest.raises(ValueError, match="already past"):
        _add(service, last_march)
    today = date_schedule(datetime.now().strftime("%Y-%m-%d"), _TZ)
    assert _add(service, today).enabled  # oggi, anche oltre le 9, suona
    job = _add(service, date_schedule(f"{datetime.now().year + 1}-03", _TZ))
    with pytest.raises(ValueError, match="already past"):
        service.update_job(job.id, schedule=last_march)
