"""Il posto di un piano: un quaderno o la chat personale, e i suoi tratti."""

from __future__ import annotations

from jenny.plan.place import (
    PERSONAL_PLACE,
    notebook_place,
    place_from_id,
    place_of_session,
)


def test_a_session_has_the_place_of_its_plans() -> None:
    nb = place_of_session("project:piante")
    assert nb is not None and nb.id == "nb:piante" and nb.notebook == "piante"
    assert nb.traits.holds and nb.traits.machine_check and nb.traits.answer_by_chat
    assert place_of_session("unified:default") is PERSONAL_PLACE
    assert not PERSONAL_PLACE.traits.holds and not PERSONAL_PLACE.traits.answer_by_chat
    assert place_of_session("cron:x") is None and place_of_session(None) is None
    assert place_from_id("nb:piante") == nb and place_from_id("personal") is PERSONAL_PLACE
    assert place_from_id("nb:") is None and place_from_id("boh") is None


def test_groups_shown_paths_and_the_diary_in_a_notebook() -> None:
    nb = notebook_place("piante")
    assert nb.shown("wikis/piante/wiki/acero.md") == "wiki/acero.md"
    assert nb.group_of("wikis/piante/wiki/acero.md") == "pages"
    assert nb.group_of("wikis/piante/raw/research/x.md") == "sources"
    assert nb.group_of("wikis/piante/board/board.json") == "board"
    assert nb.group_of("wikis/piante/AGENTS.md") == "other"
    assert nb.never("wikis/piante/raw/journal/2026-11-03.md")
    assert not nb.never("wikis/piante/raw/notes/open-gaps.md")
    assert nb.keepable("sources") and not nb.keepable("journal") and not nb.keepable("ricette")


def test_groups_in_the_personal_chat_are_the_notebooks_touched() -> None:
    assert PERSONAL_PLACE.group_of("wikis/ricette/wiki/pane.md") == "ricette"
    assert PERSONAL_PLACE.group_of("apps/spesa/app.json") == "apps"
    assert PERSONAL_PLACE.group_of("USER.md") == "other"
    assert PERSONAL_PLACE.shown("wikis/ricette/wiki/pane.md") == "wikis/ricette/wiki/pane.md"
    assert PERSONAL_PLACE.never("wikis/ricette/raw/journal/2026-11-03.md")
    assert PERSONAL_PLACE.keepable("ricette") and not PERSONAL_PLACE.keepable("journal")
