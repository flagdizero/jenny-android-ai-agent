"""Il legame fra un cron e la bacheca: una scheda o tutto il quaderno.

L'ambito del quaderno, sul disco dei cron, è un segnaposto e mai la stringa vuota:
è quello che tiene un monitor di quaderno dentro il recinto del quaderno, anche
davanti a una versione che non conosce il quaderno come casa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.board import cron_link, model
from jenny.board.store import mutate_board
from jenny.cron.service import CronService, _load_board_link
from jenny.cron.types import CronSchedule


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace" / "wikis" / "casa"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "annunci", "title": "Annunci", "pages": [], "pos": {"x": 1, "y": 1}}))
    monkeypatch.setattr("jenny.board.cron_link.notebook_root",
                        lambda name: root if name == "casa" and (root / "wiki").is_dir() else None)
    return root


def test_the_link_codec_round_trips_both_scopes() -> None:
    card = cron_link.make_link("casa", "annunci")
    whole = cron_link.make_link("casa", model.NOTEBOOK)
    assert card == {"notebook": "casa", "card": "annunci", "section": cron_link.NO_SECTION}
    assert whole["card"] == cron_link.NOTEBOOK_CARD and whole["card"]
    assert cron_link.scope_of(card) == "annunci"
    assert cron_link.scope_of(whole) == model.NOTEBOOK
    # I legami nuovi non hanno una sezione del cassetto, né una chiave vecchia.
    assert cron_link.legacy_section(card) is None
    assert cron_link.legacy_result_key(whole) is None


def test_a_link_from_before_keeps_its_drawer_section() -> None:
    old = {"notebook": "casa", "card": "annunci", "section": "s-cron"}
    assert cron_link.scope_of(old) == "annunci"
    assert cron_link.legacy_section(old) == "s-cron"
    assert cron_link.legacy_result_key(old) == "annunci/s-cron"


def test_the_scope_of_a_card_off_the_board_is_hidden_never_the_notebook() -> None:
    board = model.normalize_board({"cards": [{"id": "annunci", "title": "Annunci"}]})
    assert model.scope_state(board, model.NOTEBOOK) == model.SCOPE_NOTEBOOK
    assert model.scope_state(board, "annunci") == model.SCOPE_CARD
    assert model.scope_state(board, "sparita") == model.SCOPE_HIDDEN


def test_a_link_is_alive_while_its_home_is(notebook) -> None:
    assert cron_link.link_alive(cron_link.make_link("casa", model.NOTEBOOK))
    assert cron_link.link_alive(cron_link.make_link("casa", "annunci"))
    assert not cron_link.link_alive(cron_link.make_link("casa", "sparita"))
    assert not cron_link.link_alive(cron_link.make_link("altrove", model.NOTEBOOK))


def test_a_card_in_the_trash_is_not_a_home(notebook) -> None:
    def trash(b):
        card = b["cards"].pop()
        b["removedCards"].append({"card": card, "at": model.now_iso()})

    mutate_board(notebook, trash)
    assert not cron_link.link_alive(cron_link.make_link("casa", "annunci"))
    # Il quaderno resta una casa anche senza schede.
    assert cron_link.link_alive(cron_link.make_link("casa", model.NOTEBOOK))


def test_the_notebook_placeholder_survives_the_store_loader() -> None:
    """Una versione vecchia caricherebbe il legame com'è: un cron del quaderno, non personale."""
    link = cron_link.make_link("casa", model.NOTEBOOK)
    assert _load_board_link(link) == link


def test_a_crooked_link_is_refused_at_add_not_turned_personal(tmp_path) -> None:
    service = CronService(tmp_path / "jobs.json")
    with pytest.raises(ValueError, match="invalid board link"):
        service.add_job("x", CronSchedule(kind="every", every_ms=60_000), "m",
                        session_key="project:casa", board={"notebook": "casa", "card": ""})
    assert service.list_jobs() == []


def test_a_paused_reminder_is_a_marked_row_and_nobody_s_next() -> None:
    """Un job in pausa resta sul post-it (l'utente l'ha fermato, non tolto), ma
    segnato: non è «il prossimo» e non si accende «a breve» — non suonerà
    finché non lo si riprende, e il diario già lo lasciava fuori."""
    from datetime import date, timedelta

    soon = date.today() + timedelta(days=3)
    at_ms = int(__import__("datetime").datetime.combine(
        soon, __import__("datetime").time(9, 0)).astimezone().timestamp() * 1000)
    snaps = {
        "p1": {"name": "Travaso", "enabled": False, "paused": True, "scope": "acero",
               "schedule": {"kind": "at", "at_ms": at_ms, "precision": "day"}},
        "r2": {"name": "Concime", "enabled": True, "paused": False, "scope": "acero",
               "schedule": {"kind": "at", "at_ms": at_ms + 86_400_000 * 20, "precision": "day"}},
    }
    notes = cron_link.notes_from_jobs(snaps)
    by_id = {n["id"]: n for n in notes}
    assert by_id["p1"]["paused"] is True
    assert "paused" not in by_id["r2"]
    assert model.note_is_soon(by_id["p1"]) is False
    assert model.next_note(notes, "acero")["id"] == "r2"
    # Senza la riga in pausa non c'è un prossimo.
    assert model.next_note([by_id["p1"]], "acero") is None
