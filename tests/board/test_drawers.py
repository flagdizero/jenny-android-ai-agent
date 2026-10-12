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


# ── Niente [[link]] nel cassetto (11/10/2026) ───────────────────────────────


def test_a_link_in_a_drawer_becomes_the_page_title(notebook: Path) -> None:
    """Il cassetto si disegna come testo: sull'emulatore mostrava «[[Ginkgo]]» alla
    lettera. In scrittura il link diventa il titolo della pagina, che si trova."""
    (notebook / "wiki" / "concepts").mkdir()
    (notebook / "wiki" / "concepts" / "Coltivazione-Ginkgo.md").write_text(
        "---\ntitle: Coltivazione del ginkgo\n---\n\n# Coltivazione del ginkgo\n")
    card = _card(notebook, ["ginkgo.md", "concepts/Coltivazione-Ginkgo.md"])
    out, _, _ = write_drawer(
        notebook, "ginkgo", pages=card["pages"], skeleton=["Vaso", "Acqua"],
        brief="Affidato a [[Coltivazione-Ginkgo]].",
        sections=[{"title": "Vaso", "text": "Vedi [[concepts/Coltivazione-Ginkgo.md|la scheda]] "
                   "e [[sparita]].", "sources": ["concepts/Coltivazione-Ginkgo.md"]}])
    assert out[0]["text"] == "Vedi la scheda e sparita."
    from jenny.board.store import read_card_summary

    assert read_card_summary(notebook, "ginkgo")["brief"] == "Affidato a Coltivazione del ginkgo."


def test_a_drawer_written_before_the_rule_reads_without_links(notebook: Path) -> None:
    """I cassetti gia' scritti si riscrivono solo quando cambiano le loro pagine:
    fino ad allora la pulizia la fa la lettura, senza titoli (il nome basta)."""
    from jenny.board.store import read_card_summary

    write_card_summary(notebook, "ginkgo", {
        "generated": "2026-10-09T19:32:29+02:00",
        "brief": "Tre alberi — [[Ginkgo]], [[Fico]] e [[Prunus]] — in vaso.",
        "sections": [{"title": "Vaso", "text": "Affidato a [[concepts/Coltivazione-Fico.md#Vaso]].",
                      "sources": ["ginkgo.md"]}]})
    summary = read_card_summary(notebook, "ginkgo")
    assert summary["brief"] == "Tre alberi — Ginkgo, Fico e Prunus — in vaso."
    assert summary["sections"][0]["text"] == "Affidato a Coltivazione-Fico."


@pytest.mark.parametrize("raw, plain", [
    ("nessun link", "nessun link"),
    ("[[a|Etichetta]]", "Etichetta"),
    ("[[cartella/pagina.md]]", "pagina"),
    ("[[pagina#titolo]]", "pagina"),
    ("[[x]] e [[y]]", "x e y"),
    (None, None),
])
def test_plain_wikilinks(raw, plain) -> None:
    assert model.plain_wikilinks(raw) == plain


def test_a_long_section_is_cut_at_a_word_with_an_ellipsis(notebook: Path) -> None:
    """Il cassetto tagliava al carattere: «la divisione in du»."""
    from jenny.board.drawers import MAX_SECTION_CHARS

    card = _card(notebook, ["ginkgo.md", "ginkgo-vaso.md"])
    text = ("Comprato a inizio ottobre e arrivato con il fico, quasi spoglio; " * 8).strip()
    out, _, _ = _write(notebook, card, [{"title": "Vaso", "text": text,
                                         "sources": ["ginkgo-vaso.md"]}])
    clipped = out[0]["text"]
    assert len(clipped) <= MAX_SECTION_CHARS and clipped.endswith("…")
    assert text.startswith(clipped[:-1]) and text[len(clipped) - 1] == " "
    short = "Vaso da 20."
    out, _, _ = _write(notebook, card, [{"title": "Vaso", "text": short,
                                         "sources": ["ginkgo-vaso.md"]}])
    assert out[0]["text"] == short


def test_the_drawer_writers_are_told_not_to_repeat_reminder_dates() -> None:
    """Il cassetto mostra le attese nel suo calendario: il riassunto non le ripete."""
    from jenny.agent.board_tools import BoardWriteSummaryTool

    templates = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "agent"
    assert "Never restate a scheduled\nreminder" in (templates / "board_pass.md").read_text()
    assert "care calendar" in (templates / "types" / "notebook-editor.md").read_text()
    assert "care calendar" in BoardWriteSummaryTool.description.fget(None)
