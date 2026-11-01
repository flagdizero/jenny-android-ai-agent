"""«Coming up in your notebooks»: le date dei quaderni nel diario della chat personale.

Dal 31/10/2026 un promemoria del quaderno è un singolo dello scheduler che,
scattato, se ne va: il vecchio «Waiting on» non aveva più niente da dire. Al suo
posto, la chat personale sa cosa sta per suonare nei quaderni — dallo scheduler
vivo, mai da ``jobs.json``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jenny.agent import context as context_module
from jenny.agent.context import ContextBuilder
from jenny.agent.diary import (
    DIARY_STATE_KEY,
    MAX_COMING_UP,
    diary_turn,
    upcoming_dates,
)
from jenny.board import model
from jenny.board.cron_link import add_notebook_job
from jenny.board.store import mutate_board
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

PERSONAL = "unified:default"
# Un'ora vera e non una finta: lo scheduler conta il «prossimo giro» sull'orologio.
NOW = datetime.now().replace(second=0, microsecond=0)
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# In inglese a mano, come nel diario: ``%B`` segue il locale del processo.
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def _at(delta: timedelta, *, precision: str | None = None) -> CronSchedule:
    when = NOW + delta
    return CronSchedule(kind="at", at_ms=int(when.timestamp() * 1000), precision=precision)


def _day(when: datetime) -> str:
    return f"{_DAYS[when.weekday()]} {when.strftime('%d/%m')}"


@pytest.fixture
def wikis(tmp_path: Path) -> Path:
    wikis = tmp_path / "workspace" / "wikis"
    root = wikis / "orto"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "pomodori", "title": "Pomodori", "pages": [], "pos": {"x": 1, "y": 1}}))
    return wikis


@pytest.fixture
def cron(tmp_path: Path) -> CronService:
    return CronService(tmp_path / "jobs.json")


def _roots(wikis: Path) -> dict[str, Path]:
    return {"orto": wikis / "orto"}


def _add(cron: CronService, name: str, schedule: CronSchedule, scope: str = "pomodori", **kw):
    return add_notebook_job(cron, "orto", scope, name=name, message=name, schedule=schedule, **kw)


def test_the_rows_come_from_the_live_service(cron, wikis) -> None:
    _add(cron, "copper treatment", _at(timedelta(days=1, hours=2)))
    _add(cron, "order seeds", _at(timedelta(days=2), precision="day"), scope=model.NOTEBOOK)
    dates = upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None)
    assert [(d.notebook, d.card, d.text) for d in dates] == [
        ("orto", "Pomodori", "copper treatment"), ("orto", None, "order seeds")]
    first, second = dates[0], dates[1]
    when = NOW + timedelta(days=1, hours=2)
    assert first.time == when.strftime("%H:%M")
    assert (second.precision, second.time) == ("day", None)
    # Niente file: un job che lo scheduler non ha non c'è.
    assert not (wikis.parent / "cron" / "jobs.json").exists()


def test_what_is_not_a_date_coming_up_stays_out(cron, wikis) -> None:
    _add(cron, "ok", _at(timedelta(hours=3)))
    _add(cron, "every morning", CronSchedule(kind="every", every_ms=86_400_000))
    _add(cron, "when dry", CronSchedule(kind="every", every_ms=3_600_000),
         condition={"app": "meteo", "field": "umidita", "op": "<", "value": 15})
    _add(cron, "next month", _at(timedelta(days=8)))
    _add(cron, "on a card that is gone", _at(timedelta(hours=4)), scope="sparita")
    cron.add_job("personal", _at(timedelta(hours=5)), "personal", session_key=PERSONAL)
    paused = _add(cron, "paused", _at(timedelta(hours=6)))
    assert cron.set_paused(paused.id, True) == "paused"
    assert [d.text for d in upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None)] == ["ok"]


def test_a_removed_card_takes_its_dates_away(cron, wikis) -> None:
    """Togliere una scheda la mette nel cestino e ne mette in pausa i job."""
    from jenny.board.removed import card_jobs

    _add(cron, "copper treatment", _at(timedelta(days=1)))

    def trash(b):
        card = b["cards"].pop()
        b["removedCards"].append({"card": card, "at": model.now_iso()})

    mutate_board(wikis / "orto", trash)
    for job_id in card_jobs(cron, "orto", "pomodori"):
        cron.set_paused(job_id, True)
    assert upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None) == []


def test_at_most_eight_nearest_first(cron, wikis) -> None:
    for i in reversed(range(MAX_COMING_UP + 2)):
        _add(cron, f"job {i}", _at(timedelta(hours=2 + i)))
    dates = upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None)
    assert [d.text for d in dates] == [f"job {i}" for i in range(MAX_COMING_UP + 2)]
    meta: dict = {}
    turn = diary_turn([], PERSONAL, meta, [], now=NOW, dates=dates)
    assert turn is not None
    shown = [line for line in turn.section.splitlines() if "notebook orto" in line]
    assert [line.rsplit("· ", 1)[1] for line in shown] == [
        f"job {i}" for i in range(MAX_COMING_UP)]


def test_each_date_keeps_its_precision(cron, wikis) -> None:
    exact = NOW + timedelta(days=1, hours=1)
    day = NOW + timedelta(days=2)
    month = NOW + timedelta(days=3)
    _add(cron, "copper treatment", _at(timedelta(days=1, hours=1)))
    _add(cron, "order seeds", _at(timedelta(days=2), precision="day"), scope=model.NOTEBOOK)
    _add(cron, "prune", _at(timedelta(days=3), precision="month"))
    dates = upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None)
    turn = diary_turn([], PERSONAL, {}, [], now=NOW, dates=dates)
    assert turn is not None
    assert "## Coming up in your notebooks" in turn.section
    assert (f"- {_day(exact)} {exact.strftime('%H:%M')} · notebook orto, card Pomodori · "
            "copper treatment") in turn.section
    assert f"- {_day(day)} (no set time) · notebook orto · order seeds" in turn.section
    assert (f"- sometime in {_MONTHS[month.month - 1]} {month.year} · notebook orto, card Pomodori"
            " · prune") in turn.section
    # Un mese resta un mese: nessun giorno inventato.
    assert _day(month) not in turn.section


def test_no_dates_no_block() -> None:
    meta: dict = {}
    turn = diary_turn([], PERSONAL, meta, [], now=NOW, dates=[])
    assert turn is not None and turn.section == "" and turn.new_lines == []
    assert "dates" not in meta[DIARY_STATE_KEY]


def test_only_the_personal_chat_sees_them(cron, wikis) -> None:
    _add(cron, "copper treatment", _at(timedelta(days=1)))
    dates = upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None)
    turn = diary_turn([], "project:orto", {}, [], now=NOW, dates=dates)
    assert turn is not None and "copper treatment" not in turn.section


def test_a_new_date_arrives_as_a_new_line_and_a_rung_one_says_so(cron, wikis) -> None:
    """Congelate con il resto: il prefisso in cache non si muove."""
    first_job = _add(cron, "copper treatment", _at(timedelta(days=1)))
    meta: dict = {}
    first = diary_turn([], PERSONAL, meta, [], now=NOW,
                       dates=upcoming_dates(cron, _roots(wikis), now=NOW, tz_name=None))
    _add(cron, "order seeds", _at(timedelta(days=2)))
    cron.remove_job(first_job.id)
    later = NOW + timedelta(minutes=5)
    second = diary_turn([], PERSONAL, meta, [], now=later,
                        dates=upcoming_dates(cron, _roots(wikis), now=later, tz_name=None))
    assert first is not None and second is not None
    assert second.section == first.section
    news = "\n".join(second.new_lines)
    assert "coming up in a notebook:" in news and "order seeds" in news
    assert "no longer coming up" in news and "copper treatment" in news


def test_the_personal_chat_prompt_carries_the_block(cron, wikis, monkeypatch) -> None:
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return NOW

    monkeypatch.setattr(context_module, "datetime", _Clock)
    _add(cron, "copper treatment", _at(timedelta(days=1)))
    builder = ContextBuilder(wikis.parent, cron_service=cron)
    personal = builder.build_messages(history=[], current_message="ciao",
                                      session_key=PERSONAL, session_metadata={})
    assert "## Coming up in your notebooks" in personal[0]["content"]
    assert "notebook orto, card Pomodori · copper treatment" in personal[0]["content"]
    notebook = builder.build_messages(history=[], current_message="ciao",
                                      session_key="project:orto", session_metadata={})
    assert "Coming up" not in notebook[0]["content"]
    # Senza scheduler (test, comandi) il blocco non c'è, e il resto del diario sì.
    bare = ContextBuilder(wikis.parent).build_messages(
        history=[], current_message="ciao", session_key=PERSONAL, session_metadata={})
    assert "Coming up" not in bare[0]["content"]
