"""Il diario in testa a Jenny: chi vede quali righe, in due gradi, senza rompere la cache.

Il caso da cui viene (11/10/2026): un film visto e commentato in un quaderno,
scritto nel suo journal e specchiato in ``history.jsonl``; due ore dopo la chat
personale non lo sapeva, perché le voci di un quaderno non entravano in nessun
prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jenny.agent import context as context_module
from jenny.agent.context import ContextBuilder
from jenny.agent.diary import (
    DIARY_STATE_KEY,
    REFREEZE_AFTER_NEW,
    diary_turn,
    render_section,
    visible_rows,
)
from jenny.agent.memory import MemoryStore

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

NOW = datetime(2026, 10, 11, 20, 0)
PERSONAL = "unified:default"


def _row(cursor: int, hours_ago: float, content: str, *, about: str = "film",
         kind: str = "said", **extra) -> dict:
    when = NOW - timedelta(hours=hours_ago)
    return {"cursor": cursor, "timestamp": when.strftime("%Y-%m-%d %H:%M"),
            "content": content, "session_key": f"project:{about}", "kind": kind,
            "about": about, **extra}


# -- chi vede cosa ------------------------------------------------------------


def test_the_personal_chat_sees_every_notebook() -> None:
    rows = [_row(1, 2, "Lady Vendetta visto"), _row(2, 1, "Rinvasato il fico", about="piante")]
    assert [r["content"] for r in visible_rows(rows, PERSONAL)] == [
        "Lady Vendetta visto", "Rinvasato il fico"]


def test_a_consolidator_note_is_not_a_diary_row() -> None:
    note = {"cursor": 1, "timestamp": "2026-10-11 18:00", "content": "- [durable] x",
            "session_key": "project:film"}
    assert visible_rows([note], PERSONAL) == []


def test_an_untrusted_row_stays_in_its_own_conversation() -> None:
    rows = [_row(1, 1, "Il volo parte alle 7", untrusted=True)]
    assert visible_rows(rows, PERSONAL) == []
    assert visible_rows(rows, "project:film") == []  # la sua: ce l'ha nel journal


def test_a_notebook_sees_only_what_is_about_it_and_born_elsewhere() -> None:
    from_chat = {**_row(1, 1, "Vuole un film coreano"), "session_key": PERSONAL}
    own = _row(2, 1, "Lady Vendetta visto")
    other = _row(3, 1, "Rinvasato il fico", about="piante")
    assert visible_rows([from_chat, own, other], "project:film") == [from_chat]


def test_system_work_sees_no_diary() -> None:
    rows = [_row(1, 1, "Lady Vendetta visto")]
    assert visible_rows(rows, "cron:reminders") == []
    assert diary_turn(rows, "dream:20261011", {}, [], now=NOW) is None


# -- i due gradi ----------------------------------------------------------------


def test_three_days_line_by_line_then_a_trace_then_nothing() -> None:
    rows = [_row(1, 24 * 9, "troppo vecchio"), _row(2, 24 * 5, "Drive rivisto"),
            _row(3, 24 * 5 - 1, "Perfect Days"), _row(4, 24 * 4, "Rinvasato", about="piante"),
            _row(5, 2, "Lady Vendetta visto")]
    sharp, trace = render_section(rows, until_cursor=5, anchor=NOW)
    assert sharp == ["- Sun 11/10 18:00 · notebook film · the user said: Lady Vendetta visto"]
    assert trace == ["- Tue 06/10 · notebook film (2)", "- Wed 07/10 · notebook piante (1)"]
    assert not any("troppo vecchio" in line for line in sharp + trace)


def test_the_view_stops_at_its_cursor() -> None:
    sharp, _ = render_section([_row(1, 2, "prima"), _row(2, 1, "dopo")], until_cursor=1,
                              anchor=NOW)
    assert len(sharp) == 1 and "prima" in sharp[0]


# -- congelata, e le righe nuove una volta sola ------------------------------------


def test_new_rows_arrive_once_and_the_section_stays_frozen() -> None:
    meta: dict = {}
    rows = [_row(1, 2, "Lady Vendetta visto")]
    first = diary_turn(rows, PERSONAL, meta, [], now=NOW)
    assert first is not None and "Lady Vendetta visto" in first.section
    assert first.new_lines == []
    assert meta[DIARY_STATE_KEY]["cursor"] == 1

    rows.append(_row(2, 0, "Old Boy rivisto"))
    second = diary_turn(rows, PERSONAL, meta, [], now=NOW + timedelta(minutes=5))
    assert second is not None and second.section == first.section
    assert any("Old Boy rivisto" in line for line in second.new_lines)

    sent = [{"role": "user", "content": "ciao\n\n" + "\n".join(second.new_lines)}]
    third = diary_turn(rows, PERSONAL, meta, sent, now=NOW + timedelta(minutes=10))
    assert third is not None and third.section == first.section
    assert third.new_lines == []


def test_a_long_pause_refreezes_the_view() -> None:
    meta: dict = {}
    rows = [_row(1, 2, "Lady Vendetta visto")]
    diary_turn(rows, PERSONAL, meta, [], now=NOW)
    rows.append(_row(2, 0, "Old Boy rivisto"))
    later = diary_turn(rows, PERSONAL, meta, [], now=NOW + timedelta(hours=2))
    assert later is not None and "Old Boy rivisto" in later.section
    assert later.new_lines == []
    assert meta[DIARY_STATE_KEY]["cursor"] == 2


def test_too_many_new_rows_refreeze_the_view() -> None:
    meta: dict = {}
    rows = [_row(1, 2, "primo")]
    diary_turn(rows, PERSONAL, meta, [], now=NOW)
    rows += [_row(i, 0, f"riga {i}") for i in range(2, REFREEZE_AFTER_NEW + 3)]
    later = diary_turn(rows, PERSONAL, meta, [], now=NOW + timedelta(minutes=1))
    assert later is not None and later.new_lines == []
    assert f"riga {REFREEZE_AFTER_NEW + 2}" in later.section


# -- nel prompt -------------------------------------------------------------------


class _Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _store_with_film(workspace: Path) -> MemoryStore:
    store = MemoryStore(workspace)
    store.append_history("Lady Vendetta visto: più artistico di Old Boy, ma confuso",
                         session_key="project:film", kind="said", about="film",
                         ref="wikis/film/raw/journal/20261011.md:12")
    return store


def test_the_personal_chat_knows_what_a_notebook_wrote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(context_module, "datetime", _Clock)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _store_with_film(workspace)
    builder = ContextBuilder(workspace)
    meta: dict = {}
    messages = builder.build_messages(history=[], current_message="cosa vedo stasera?",
                                      session_key=PERSONAL, session_metadata=meta)
    system = messages[0]["content"]
    assert "# Diary" in system
    assert "notebook film · the user said: Lady Vendetta visto" in system
    # Le voci di un quaderno restano fuori dalla Recent History: entra la riga, non il blob.
    assert "# Recent History" not in system


def test_the_system_prompt_does_not_move_when_a_row_arrives(tmp_path: Path, monkeypatch) -> None:
    """Il prefisso in cache: la riga nuova va nel contesto del turno, non in testa."""
    monkeypatch.setattr(context_module, "datetime", _Clock)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store_with_film(workspace)
    builder = ContextBuilder(workspace)
    meta: dict = {}
    _Clock.current = NOW
    first = builder.build_messages(history=[], current_message="ciao",
                                   session_key=PERSONAL, session_metadata=meta)
    store.append_history("Old Boy rivisto", session_key="project:film", kind="said",
                         about="film")
    _Clock.current = NOW + timedelta(minutes=3)
    second = builder.build_messages(history=[], current_message="e quindi?",
                                    session_key=PERSONAL, session_metadata=meta)
    assert second[0]["content"] == first[0]["content"]
    assert "Old Boy rivisto" in second[-1]["content"]
    assert "Old Boy rivisto" not in second[0]["content"]


def test_a_notebook_does_not_see_another_notebook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(context_module, "datetime", _Clock)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _store_with_film(workspace)
    builder = ContextBuilder(workspace)
    messages = builder.build_messages(history=[], current_message="ciao",
                                      session_key="project:piante", session_metadata={})
    assert "Lady Vendetta" not in messages[0]["content"]
    assert "Lady Vendetta" not in messages[-1]["content"]


def test_a_diary_row_is_not_repeated_in_recent_history(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.append_history("- [durable] nota del consolidatore", session_key=PERSONAL)
    store.append_history("Vuole un film coreano", session_key=PERSONAL, kind="said")
    contents = [e["content"] for e in store.read_recent_history_for_prompt(
        since_cursor=0, session_key=PERSONAL)]
    assert contents == ["- [durable] nota del consolidatore"]
