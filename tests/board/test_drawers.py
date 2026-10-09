"""Il cassetto di una scheda: una regola sola per il giardiniere e per l'editore."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.board_pass import BoardPass
from jenny.board import model
from jenny.board.drawers import DrawerError, write_drawer
from jenny.board.store import mutate_board, read_board, write_card_summary


def _card(notebook: Path, pages: list[str]) -> dict:
    def apply(board):
        board["skeleton"] = ["Vaso", "Acqua"]
        board["cards"].append({"id": "ginkgo", "title": "Ginkgo", "pages": pages,
                               "pos": {"x": 1, "y": 1}})

    mutate_board(notebook, apply)
    return model.card_by_id(read_board(notebook), "ginkgo")


def _write(notebook: Path, card: dict, sections: list[dict]) -> tuple:
    return write_drawer(notebook, "ginkgo", pages=card["pages"], skeleton=["Vaso", "Acqua"],
                        brief="Arrivato a settembre.", sections=sections)


def test_a_drawer_written_by_the_editor_is_up_to_date_for_the_gardener(notebook: Path) -> None:
    """Una sola impronta: fino al 09/10/2026 lo strumento la calcolava anche sulle
    pagine sparite, e la passata no — la scheda restava «da riscrivere» per sempre."""
    card = _card(notebook, ["ginkgo.md", "ginkgo-vaso.md", "sparita.md"])
    _write(notebook, card, [{"title": "Vaso", "text": "Non registrato.",
                             "sources": ["ginkgo-vaso.md"]}])
    assert BoardPass(notebook, notebook.parent.parent).stale_cards() == []


def test_sections_outside_the_skeleton_or_the_card_are_skipped(notebook: Path) -> None:
    card = _card(notebook, ["ginkgo.md", "ginkgo-vaso.md"])
    out, changed, problems = _write(notebook, card, [
        {"title": "Vaso", "text": "Non registrato.", "sources": ["ginkgo-vaso.md"]},
        {"title": "Storia", "text": "x", "sources": ["ginkgo.md"]},
        {"title": "Acqua", "text": "y", "sources": ["dormienza.md"]},
    ])
    assert [s["title"] for s in out] == ["Vaso"] and changed == 1
    assert len(problems) == 2
    with pytest.raises(DrawerError):
        _write(notebook, card, [{"title": "Storia", "text": "x", "sources": ["ginkgo.md"]}])


def test_an_unchanged_section_keeps_its_date(notebook: Path) -> None:
    card = _card(notebook, ["ginkgo.md", "ginkgo-vaso.md"])
    section = {"title": "Vaso", "text": "Non registrato.", "sources": ["ginkgo-vaso.md"]}
    write_card_summary(notebook, "ginkgo", {"generated": "2026-10-01T09:00:00+02:00",
                                            "brief": "x", "sections": [
                                                {**section, "changed": "2026-10-01T09:00:00+02:00"}]})
    out, changed, _ = _write(notebook, card, [section, {"title": "Acqua", "text": "Poca.",
                                                        "sources": ["ginkgo.md"]}])
    assert out[0]["changed"] == "2026-10-01T09:00:00+02:00" and changed == 1
