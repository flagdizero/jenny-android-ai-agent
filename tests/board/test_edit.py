"""Le scritture dell'utente sulla bacheca (``jenny/board/edit.py``).

Le fanno l'interfaccia e l'editore dei quaderni, e valgono come dell'utente:
il giardiniere, che scrive come ``jenny``, non può disfarle. Il caso da cui è
nato tutto è del 09/10/2026: una scheda che raccoglieva tre piante, da dividere
in una scheda per pianta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenny.board import edit, model
from jenny.board.store import (
    BoardOwnershipError,
    mutate_board,
    read_board,
    read_card_summary,
    read_reminders,
    write_card_summary,
)


def _seed(notebook: Path) -> None:
    def apply(board):
        board["skeleton"] = ["Vaso", "Acqua"]
        board["cards"].append({
            "id": "nuove", "title": "Nuove piante", "pages": [
                "ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md", "dormienza.md",
            ], "pos": {"x": 5, "y": 6, "by": "jenny"},
        })
        board["cards"].append({
            "id": "acero", "title": "Acero", "pages": ["travaso-acero.md"],
            "pos": {"x": 40, "y": 6, "by": "jenny"},
            "userSections": [{"id": "s-cron", "kind": "cron", "job": "j1", "name": "meteo"}],
        })

    mutate_board(notebook, apply)
    write_card_summary(notebook, "nuove", {"generated": "2026-10-08T10:00:00+02:00",
                                           "brief": "Tre piante.", "sections": []})


class _Cron:
    """Come il servizio vero, vuole il loop: riarma il suo timer lì, e da un
    thread solleva. Il 09/10/2026 sull'emulatore una «Togli» lo chiamava da
    ``asyncio.to_thread``, e falliva dopo aver già messo in pausa il cron."""

    def __init__(self) -> None:
        self.paused: dict[str, bool] = {}

    def set_paused(self, job_id: str, paused: bool) -> str:
        asyncio.get_running_loop()
        self.paused[job_id] = paused
        return "paused" if paused else "resumed"

    def remove_job(self, job_id: str) -> str:
        asyncio.get_running_loop()
        self.paused.pop(job_id, None)
        return "removed"

    def get_job(self, job_id: str) -> object:
        return object()


def test_a_card_born_with_pages_takes_them_from_the_card_that_had_them(notebook: Path) -> None:
    _seed(notebook)
    card, redraw = edit.add_card(notebook, "Ginkgo", pages=["ginkgo.md", "ginkgo-talea.md"])
    board = read_board(notebook)
    assert card["titleBy"] == "user" and card["pos"]["by"] == "user"
    assert model.card_by_id(board, "ginkgo")["pages"] == ["ginkgo.md", "ginkgo-talea.md"]
    assert model.card_by_id(board, "nuove")["pages"] == ["ginkgo-vaso.md", "dormienza.md"]
    assert redraw == ["ginkgo", "nuove"]


def test_a_drawer_left_without_pages_is_deleted(notebook: Path) -> None:
    _seed(notebook)
    redraw = edit.move_pages(notebook, ["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md",
                                        "dormienza.md"], "acero")
    assert redraw == ["acero"], "la scheda rimasta vuota non ha un cassetto da riscrivere"
    assert read_card_summary(notebook, "nuove") is None
    assert model.card_by_id(read_board(notebook), "nuove")["pages"] == []


def test_pages_come_from_off_the_board_too(notebook: Path) -> None:
    _seed(notebook)
    assert edit.move_pages(notebook, ["irrigazione-acero.md"], "Acero") == ["acero"]
    assert model.card_by_id(read_board(notebook), "acero")["pages"] == [
        "travaso-acero.md", "irrigazione-acero.md"]


def test_a_page_that_does_not_exist_is_refused(notebook: Path) -> None:
    _seed(notebook)
    with pytest.raises(edit.BoardEditError) as err:
        edit.move_pages(notebook, ["Ginkgo"], "acero")
    assert err.value.code == "not_found" and "relative to wiki/" in err.value.message


def test_what_the_editor_does_the_gardener_cannot_undo(notebook: Path) -> None:
    """Il punto della separazione: le scritture su richiesta sono dell'utente."""
    _seed(notebook)
    edit.add_card(notebook, "Ginkgo", pages=["ginkgo.md"])

    def drop_card(board):
        board["cards"] = [c for c in board["cards"] if c["id"] != "ginkgo"]

    def take_page_back(board):
        model.card_by_id(board, "ginkgo")["pages"] = []

    for undo in (drop_card, take_page_back):
        with pytest.raises(BoardOwnershipError):
            mutate_board(notebook, undo, actor=model.JENNY)


async def test_a_removed_card_goes_to_the_trash_and_its_cron_pauses(notebook: Path) -> None:
    _seed(notebook)
    cron = _Cron()
    await edit.remove_card(notebook, "Acero", cron=cron)
    board = read_board(notebook)
    assert model.card_by_id(board, "acero") is None
    assert board["removedCards"][0]["paused"]["jobs"] == ["j1"]
    assert cron.paused == {"j1": True}
    await edit.restore_card(notebook, "acero", cron=cron)
    assert model.card_by_id(read_board(notebook), "acero") is not None
    assert cron.paused == {"j1": False}


async def test_an_accepted_removal_is_a_trash_with_undo_too(notebook: Path) -> None:
    """Fino al 09/10/2026 una proposta accettata cancellava la scheda per sempre
    e lasciava girare i cron del suo cassetto."""
    _seed(notebook)
    mutate_board(notebook, lambda b: b["suggestions"].append({
        "id": "sg-acero", "kind": "remove_card", "subject": "Acero", "card": "acero",
        "text": "Ferma da tempo."}), actor=model.JENNY)
    cron = _Cron()
    await edit.respond_suggestion(notebook, "sg-acero", "yes", cron=cron)
    board = read_board(notebook)
    assert [r["card"]["id"] for r in board["removedCards"]] == ["acero"]
    assert board["since"][-1]["kind"] == "card_removed"
    assert cron.paused == {"j1": True}
    await edit.restore_card(notebook, "acero", cron=cron)
    assert model.card_by_id(read_board(notebook), "acero") is not None


def test_a_users_thread_and_its_removal(notebook: Path) -> None:
    _seed(notebook)
    edit.connect(notebook, "acero", "Nuove piante", "stesso balcone")
    board = read_board(notebook)
    assert board["connections"] == [
        {"a": "acero", "b": "nuove", "label": "stesso balcone", "by": "user"}]
    with pytest.raises(edit.BoardEditError):
        edit.connect(notebook, "nuove", "acero")
    assert edit.disconnect(notebook, "Acero", "nuove") is True
    board = read_board(notebook)
    assert board["connections"] == []
    assert {d["subject"] for d in board["declined"]} == {
        "Acero — Nuove piante", "Nuove piante — Acero"}


def test_a_card_next_to_another(notebook: Path) -> None:
    _seed(notebook)
    edit.move_card(notebook, "nuove", "acero")
    board = read_board(notebook)
    moved = model.card_by_id(board, "nuove")["pos"]
    other = model.card_by_id(board, "acero")["pos"]
    assert moved["by"] == "user"
    assert abs(moved["x"] - other["x"]) <= model.NEAR_SPAN_X


def test_a_reminder_is_the_users_and_sits_in_the_drawer(notebook: Path) -> None:
    _seed(notebook)
    sid = edit.add_reminder(notebook, "acero", "Travaso",
                            {"kind": "date", "date": "2027-03-01", "precision": "month"})
    rem = read_reminders(notebook)["reminders"][0]
    assert rem["by"] == "user" and rem["card"] == "acero"
    sections = model.card_by_id(read_board(notebook), "acero")["userSections"]
    assert sections[-1] == {"id": sid, "kind": "reminder", "created": sections[-1]["created"],
                            "reminder": rem["id"]}
    edit.close_reminder(notebook, rem["id"])
    assert read_reminders(notebook)["reminders"][0]["state"] == "done"


def test_an_unknown_card_names_the_ones_there_are(notebook: Path) -> None:
    _seed(notebook)
    with pytest.raises(edit.BoardEditError) as err:
        edit.rename_card(notebook, "Fico", "Fico bianco")
    assert "«Acero» (acero)" in err.value.message


def test_cards_asked_one_next_to_the_other_go_right_not_off_the_screen(notebook: Path) -> None:
    """Sull'emulatore il 09/10/2026 tre schede «accanto» finivano a x = 2, −18, −38."""
    _seed(notebook)
    first, _ = edit.add_card(notebook, "Ginkgo")
    second, _ = edit.add_card(notebook, "Fico", near_card="ginkgo")
    third, _ = edit.add_card(notebook, "Prunus", near_card="fico")
    assert first["pos"]["x"] < second["pos"]["x"] < third["pos"]["x"]
    assert second["pos"]["x"] >= 0


async def test_removing_a_cron_section_removes_its_job_on_the_loop(notebook: Path) -> None:
    _seed(notebook)
    cron = _Cron()
    cron.paused["j1"] = False
    assert await edit.remove_section(notebook, "acero", "s-cron", cron=cron) is True
    assert "j1" not in cron.paused
    assert model.card_by_id(read_board(notebook), "acero")["userSections"] == []
