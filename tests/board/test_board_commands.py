"""I comandi ``board.*`` e le viste che la bacheca e il cassetto ricevono."""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

import pytest

from jenny.board import model
from jenny.board.corrections import list_corrections, open_corrections, resolve_correction
from jenny.board.links import page_membership
from jenny.board.store import (
    mutate_board,
    mutate_reminders,
    read_board,
    read_reminders,
    write_card_summary,
)
from jenny.board.view import build_board_view, build_card_view
from jenny.webui.commands import CommandError, dispatch_command


def _seed(notebook: Path) -> None:
    def apply(board):
        board["skeleton"] = ["Vaso e travaso", "Acqua"]
        board["cards"].append({
            "id": "acero", "title": "Acero", "pages": [
                "travaso-acero.md", "vasi-settembre.md", "irrigazione-acero.md",
            ], "pos": {"x": 5, "y": 6, "by": "jenny"}, "glyph": "leaf",
            "created": "2026-09-01T10:00:00+02:00",
        })
        board["cards"].append({
            "id": "monstera", "title": "Monstera", "pages": ["rinvaso-monstera.md"],
            "pos": {"x": 47, "y": 4, "by": "jenny"},
        })
        board["connections"].append(
            {"a": "acero", "b": "monstera", "label": "vasi dello stesso ordine", "by": "jenny"}
        )

    mutate_board(notebook, apply)
    write_card_summary(notebook, "acero", {
        "generated": "2026-10-04T04:12:00+02:00",
        "brief": "In vaso 14, sano.",
        "sections": [
            {"title": "Acqua", "text": "Quando i primi 2 cm sono asciutti.",
             "sources": ["irrigazione-acero.md"], "changed": "2026-10-04T04:12:00+02:00"},
            {"title": "Vaso e travaso", "text": "Vaso 20 comprato il 04/09.",
             "sources": ["travaso-acero.md", "vasi-settembre.md"],
             "changed": "2026-10-04T04:12:00+02:00"},
        ],
    })


def test_board_view_without_a_board(notebook: Path) -> None:
    view = build_board_view(notebook)
    assert view["exists"] is False
    # Tutte le pagine sono fuori bacheca; l'indice no, e' la mappa.
    assert view["offboard"] == len(list((notebook / "wiki").glob("*.md"))) - 1


def test_board_view_signals_and_offboard(notebook: Path) -> None:
    _seed(notebook)
    mutate_reminders(notebook, lambda r: r["reminders"].append({
        "id": "r-concime", "card": "monstera", "text": "Concime",
        "when": {"kind": "date", "date": "2026-10-10"},
    }))
    view = build_board_view(notebook, today=date(2026, 10, 4))
    signals = {c["id"]: c["signal"] for c in view["cards"]}
    # L'acero ha un cassetto cambiato dopo un'apertura mai avvenuta: puntino.
    assert signals == {"acero": "new", "monstera": "soon"}
    assert [n["id"] for n in view["notes"]] == ["r-concime"]
    # Fuori bacheca: dormienza e le tre del ginkgo.
    assert view["offboard"] == 4
    assert view["connections"][0]["label"] == "vasi dello stesso ordine"


def test_card_view_orders_sections_by_the_skeleton(notebook: Path) -> None:
    _seed(notebook)
    view = build_card_view(notebook, "acero")
    assert [s["title"] for s in view["sections"]] == ["Vaso e travaso", "Acqua"]
    assert view["sections"][0]["sources"][0] == {
        "path": "travaso-acero.md", "title": "travaso acero", "exists": True,
    }
    assert view["connections"] == [
        {"to": "monstera", "title": "Monstera", "label": "vasi dello stesso ordine", "by": "jenny"}
    ]
    assert build_card_view(notebook, "nope") is None


def test_page_membership_classifies_links(notebook: Path) -> None:
    _seed(notebook)
    html = (
        '<a class="wikilink" href="/?wiki=piante&page=vasi-settembre.md">a</a>'
        '<a class="wikilink" href="/?wiki=piante&page=dormienza.md">b</a>'
        '<a class="wikilink" href="/?wiki=piante&page=rinvaso-monstera.md">c</a>'
        '<a class="wikilink" href="/?wiki=altro&page=x.md">d</a>'
        # Un wikilink che il renderer non ha risolto arriva col nome nudo.
        '<a class="wikilink" href="/?wiki=piante&page=irrigazione-acero">e</a>'
    )
    info = page_membership(notebook, "piante", "travaso-acero.md", html)
    assert info["card"] == "acero"
    assert info["links"] == {
        "vasi-settembre.md": {"card": "acero", "title": "Acero"},
        "dormienza.md": None,
        "rinvaso-monstera.md": {"card": "monstera", "title": "Monstera"},
        "irrigazione-acero": {"card": "acero", "title": "Acero"},
    }


async def test_move_makes_the_position_the_users(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "card", "id": "acero", "x": 61.5, "y": 40,
    })
    card = model.card_by_id(read_board(notebook), "acero")
    assert card["pos"] == {"x": 61.5, "y": 40.0, "by": "user"}


async def test_seen_marks_the_board_and_a_card(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.seen", {"wiki": "piante"})
    await dispatch_command(ctx, "board.seen", {"wiki": "piante", "card": "acero"})
    board = read_board(notebook)
    assert board["lastSeen"]
    assert model.card_by_id(board, "acero")["lastOpened"]
    view = build_board_view(notebook)
    assert {c["id"]: c["signal"] for c in view["cards"]}["acero"] is None


async def test_accepting_a_card_places_it_in_a_free_spot(ctx, notebook: Path) -> None:
    _seed(notebook)
    mutate_board(notebook, lambda b: b["suggestions"].append({
        "id": "sg-ginkgo", "kind": "add_card", "subject": "Ginkgo", "glyph": "plant",
        "pages": ["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md"],
        "text": "Gli faccio un posto?",
    }), actor="jenny")
    await dispatch_command(ctx, "board.suggestion.respond", {
        "wiki": "piante", "id": "sg-ginkgo", "answer": "yes",
    })
    board = read_board(notebook)
    ginkgo = model.card_by_id(board, "ginkgo")
    assert ginkgo["pages"] == ["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md"]
    assert ginkgo["pos"]["by"] == "jenny"
    assert (ginkgo["pos"]["x"], ginkgo["pos"]["y"]) not in {(5, 6), (47, 4)}
    assert board["suggestions"] == []
    assert board["since"][-1]["kind"] == "card_added"


async def test_a_no_is_remembered_and_later_is_deferred(ctx, notebook: Path) -> None:
    _seed(notebook)

    def add(board):
        board["suggestions"] += [
            {"id": "sg-a", "kind": "remove_card", "subject": "Monstera", "card": "monstera"},
            {"id": "sg-b", "kind": "add_card", "subject": "Ginkgo", "pages": ["ginkgo.md"]},
        ]

    mutate_board(notebook, add, actor="jenny")
    await dispatch_command(ctx, "board.suggestion.respond", {"wiki": "piante", "id": "sg-a",
                                                            "answer": "no"})
    await dispatch_command(ctx, "board.suggestion.respond", {"wiki": "piante", "id": "sg-b",
                                                            "answer": "later"})
    board = read_board(notebook)
    assert board["declined"][0]["kind"] == "remove_card"
    assert board["later"][0]["untilPass"] == model.LATER_PASSES
    assert model.card_by_id(board, "monstera") is not None


async def test_fix_files_a_board_audit_on_the_first_source(ctx, notebook: Path) -> None:
    _seed(notebook)
    result = await dispatch_command(ctx, "board.fix", {
        "wiki": "piante", "card": "acero", "section": "Vaso e travaso",
        "text": "Il vaso 20 l'ho comprato il 05/09, non il 04.",
    })
    assert result["target"] == "travaso-acero.md"
    pending = list_corrections(notebook, "acero")
    assert pending[0]["status"] == "pending"
    assert pending[0]["section"] == "Vaso e travaso"
    opened = open_corrections(notebook)
    assert opened[0]["sources"] == ["travaso-acero.md", "vasi-settembre.md"]
    # La card mostra la graffetta; chiusa la correzione, la ✓ fino alla prossima apertura.
    assert build_card_view(notebook, "acero", corrections=pending)["corrections"]
    assert resolve_correction(notebook, opened[0]["id"], "Fixed the date in travaso-acero.")
    done = list_corrections(notebook, "acero")
    assert done[0]["status"] == "done"
    assert (notebook / "audit" / "resolved").glob("*.md")
    assert open_corrections(notebook) == []


async def test_fix_audit_passes_the_wiki_lint_shape(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.fix", {
        "wiki": "piante", "card": "acero", "section": "Acqua", "text": "Ogni tre giorni.",
    })
    audit = next((notebook / "audit").glob("*.md")).read_text()
    for key in ("id:", "target:", "target_lines:", "anchor_before:", "anchor_text:",
                "anchor_after:", "author:", "source: board", "created:", "status: open"):
        assert key in audit


async def test_note_section_is_journaled_as_said(ctx, notebook: Path) -> None:
    _seed(notebook)
    out = await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "note",
        "text": "Lo tengo sul davanzale sud solo d'inverno.",
    })
    card = model.card_by_id(read_board(notebook), "acero")
    note = card["userSections"][0]
    assert note["id"] == out["id"]
    journal = next((notebook / "raw" / "journal").glob("*.md")).read_text()
    assert "[said] Lo tengo sul davanzale sud" in journal
    assert note["journalLine"].startswith("raw/journal/")


async def test_reminder_section_creates_a_user_reminder(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "reminder", "text": "Potatura leggera",
        "when": {"kind": "date", "date": "2027-02-15"},
    })
    rem = read_reminders(notebook)["reminders"][0]
    assert rem["by"] == "user" and rem["card"] == "acero"
    # Niente sezione nel cassetto: il calendario di cura la deriva da ``reminders.json``.
    assert model.card_by_id(read_board(notebook), "acero")["userSections"] == []
    await dispatch_command(ctx, "board.reminder.drop", {"wiki": "piante", "id": rem["id"]})
    assert read_reminders(notebook)["reminders"][0]["state"] == "dropped"


async def test_a_reminder_can_be_for_the_whole_notebook(ctx, notebook: Path) -> None:
    """Dal post-it libero, o dal «+» della bacheca: niente scheda, tutto il quaderno."""
    _seed(notebook)
    out = await dispatch_command(ctx, "board.reminder.add", {
        "wiki": "piante", "card": "", "text": "Porta dentro tutte le piante",
        "when": {"kind": "date", "date": "2026-12-01"},
    })
    rem = read_reminders(notebook)["reminders"][0]
    assert out["id"] == rem["id"] and rem["card"] == "" and rem["by"] == "user"
    view = build_board_view(notebook)
    assert [n["id"] for n in view["notes"] if n["card"] == ""] == [rem["id"]]
    # Un post-it libero, senza scheda, dopo quelli delle schede.
    assert view["postits"][-1]["card"] == "" and view["postits"][-1]["pos"]["by"] == "jenny"
    with pytest.raises(CommandError):
        await dispatch_command(ctx, "board.reminder.add", {
            "wiki": "piante", "card": "Fico!", "text": "x",
            "when": {"kind": "date", "date": "2026-12-01"}})


async def test_photo_section_lands_in_raw_photos(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "photo", "mime": "image/png",
        "data": base64.b64encode(b"\x89PNG fake").decode(), "caption": "Settembre",
    })
    section = model.card_by_id(read_board(notebook), "acero")["userSections"][0]
    assert section["path"].startswith("raw/photos/acero-")
    assert (notebook / section["path"]).read_bytes() == b"\x89PNG fake"


async def test_reminder_done_becomes_a_journal_fact(ctx, notebook: Path) -> None:
    _seed(notebook)
    mutate_reminders(notebook, lambda r: r["reminders"].append({
        "id": "r-concime", "card": "monstera", "text": "Concime",
        "when": {"kind": "date", "date": "2026-10-10"},
    }))
    await dispatch_command(ctx, "board.reminder.done", {"wiki": "piante", "id": "r-concime"})
    assert read_reminders(notebook)["reminders"][0]["state"] == "done"
    journal = next((notebook / "raw" / "journal").glob("*.md")).read_text()
    assert "Done: Concime" in journal


async def test_offboard_want_only_for_real_pages(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.offboard.want", {
        "wiki": "piante", "page": "dormienza.md", "from": "acero",
    })
    assert read_board(notebook)["offboardWants"][0]["page"] == "dormienza.md"
    # Il lettore manda il bersaglio del link com'e' scritto, senza ``.md``.
    await dispatch_command(ctx, "board.offboard.want", {"wiki": "piante", "page": "ginkgo"})
    assert read_board(notebook)["offboardWants"][1]["page"] == "ginkgo.md"
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "board.offboard.want", {"wiki": "piante", "page": "../x"})
    assert exc.value.code == "not_found"


async def test_unknown_notebook_is_a_bad_request(ctx, notebook: Path) -> None:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "board.seen", {"wiki": "nessuno"})
    assert exc.value.code == "bad_request"


async def test_a_removed_thread_is_a_no_to_that_thread(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.connection.remove", {"wiki": "piante", "a": "monstera", "b": "acero"})
    board = read_board(notebook)
    assert board["connections"] == []
    subjects = {d["subject"] for d in board["declined"] if d["kind"] == "add_connection"}
    assert subjects == {"Acero — Monstera", "Monstera — Acero"}


async def test_a_condition_reminder_from_a_drawer(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "reminder", "text": "Annaffia",
        "when": {"kind": "condition", "app": "waterbot", "params": {"sensor": "albinella"},
                 "field": "moisture", "op": "<", "value": 30},
    })
    rem = read_reminders(notebook)["reminders"][0]
    assert rem["when"]["kind"] == "condition" and rem["when"]["value"] == 30


async def test_previews_are_counted_by_real_page(ctx, notebook: Path) -> None:
    _seed(notebook)
    for _ in range(2):
        await dispatch_command(ctx, "board.preview", {"wiki": "piante", "page": "dormienza"})
    assert read_board(notebook)["previews"] == {"dormienza.md": 2}


def test_the_wall_takes_positions_past_the_first_screen(notebook) -> None:
    """La bacheca e' un muro che si scorre: una scheda sul terzo schermo resta li'."""
    _seed(notebook)

    def far(board):
        board["cards"][0]["pos"] = {"x": 250.0, "y": 180.5, "by": "user"}
        board["cards"][1]["pos"] = {"x": -140.0, "y": -60.0, "by": "user"}

    mutate_board(notebook, far)
    from jenny.board.store import read_board as _rb

    cards = _rb(notebook)["cards"]
    assert (cards[0]["pos"]["x"], cards[0]["pos"]["y"]) == (250.0, 180.5)
    # L'origine e' al centro: si va anche a sinistra e in alto.
    assert (cards[1]["pos"]["x"], cards[1]["pos"]["y"]) == (-140.0, -60.0)


def test_a_full_first_screen_places_new_things_below() -> None:
    board = {"cards": [
        {"id": f"c{i}", "pos": {"x": float(x), "y": float(y)}}
        for i, (x, y) in enumerate((x, y) for y in range(0, 100, 30) for x in range(0, 100, 20))
    ]}
    x, y = model.free_slot(board, None)
    assert y >= 100, "con il primo schermo pieno il posto nuovo e' sotto"


def test_a_new_thing_is_placed_near_where_the_user_looks() -> None:
    board = {"cards": [{"id": "a", "pos": {"x": 0.0, "y": 0.0}}]}
    x, y = model.free_slot(board, None, near=(-180.0, 250.0))
    assert abs(x + 180) <= 4 and abs(y - 250) <= 4
    # Occupato proprio li': il posto libero piu' vicino, non lo schermo dell'origine.
    board["cards"].append({"id": "b", "pos": {"x": -180.0, "y": 250.0}})
    x, y = model.free_slot(board, None, near=(-180.0, 250.0))
    assert -320 <= x <= -40 and 140 <= y <= 360
    assert (x, y) != (-180.0, 250.0)


async def test_an_accepted_card_lands_near_the_view(ctx, notebook) -> None:
    _seed(notebook)

    def propose(board):
        board["suggestions"].append({"id": "sg-ginkgo", "kind": "add_card", "subject": "Ginkgo",
                                     "pages": ["ginkgo.md"], "text": "?"})

    mutate_board(notebook, propose)
    await dispatch_command(ctx, "board.suggestion.respond", {
        "wiki": "piante", "id": "sg-ginkgo", "answer": "yes", "near": {"x": 300, "y": -150}})
    from jenny.board.store import read_board as _rb

    card = next(c for c in _rb(notebook)["cards"] if c["title"] == "Ginkgo")
    assert 250 <= card["pos"]["x"] <= 320 and -200 <= card["pos"]["y"] <= -130


# ── L'agenda delle attese e il calendario di cura ────────────────────────────


def _three_reminders(notebook: Path) -> None:
    def apply(rems):
        rems["reminders"] += [
            {"id": "r-poltiglia", "card": "acero", "text": "Poltiglia",
             "when": {"kind": "date", "date": "2026-11-02"}},
            {"id": "r-umido", "card": "acero", "text": "Annaffia",
             "when": {"kind": "condition", "app": "waterbot", "params": {},
                      "field": "moisture", "op": "<", "value": 30}},
            {"id": "r-concime", "card": "monstera", "text": "Concime",
             "when": {"kind": "date", "date": "2026-10-10"}},
            {"id": "r-marzo", "card": "acero", "text": "Rinvaso",
             "when": {"kind": "date", "date": "2027-03-01", "precision": "month"}},
        ]

    mutate_reminders(notebook, apply)


def test_each_card_with_reminders_has_its_own_postit(notebook: Path) -> None:
    _seed(notebook)
    _three_reminders(notebook)
    view = build_board_view(notebook, today=date(2026, 10, 4))
    # Dalla più vicina; quelle a condizione, senza giorno, in fondo.
    assert [n["id"] for n in view["notes"]] == ["r-concime", "r-poltiglia", "r-marzo", "r-umido"]
    assert all("pos" not in n for n in view["notes"])
    # Un post-it per scheda, non uno per attesa: due schede, due post-it.
    assert [p["card"] for p in view["postits"]] == ["acero", "monstera"]
    assert view["postitRows"] == model.POSTIT_ROWS
    # Nati accanto alla loro scheda, senza coprire le schede né fra loro.
    rects = [(p["pos"]["x"], p["pos"]["y"], p["pos"]["x"] + model.POSTIT_BOX[0],
              p["pos"]["y"] + model.POSTIT_BOX[1]) for p in view["postits"]]
    assert all(p["pos"]["by"] == "jenny" for p in view["postits"])
    assert model._overlap(rects[0], rects[1]) == 0
    for rect in rects:
        for card in view["cards"]:
            box = (card["pos"]["x"], card["pos"]["y"],
                   card["pos"]["x"] + card["box"][0], card["pos"]["y"] + card["box"][1])
            assert model._overlap(rect, box) == 0


def test_no_open_reminders_no_postit(notebook: Path) -> None:
    _seed(notebook)
    assert build_board_view(notebook)["postits"] == []
    assert model.items_on_board(read_board(notebook), read_reminders(notebook)) == 2
    _three_reminders(notebook)
    # Quattro attese su due schede: due post-it.
    assert model.items_on_board(read_board(notebook), read_reminders(notebook)) == 4


async def test_moving_a_postit_makes_its_place_the_users(ctx, notebook: Path) -> None:
    _seed(notebook)
    _three_reminders(notebook)
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "postit", "id": "acero", "x": 70, "y": 12.5,
    })
    card = model.card_by_id(read_board(notebook), "acero")
    assert card["postitPos"] == {"x": 70.0, "y": 12.5, "by": "user"}
    view = build_board_view(notebook)
    assert next(p for p in view["postits"] if p["card"] == "acero")["pos"]["by"] == "user"
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "board.move", {
            "wiki": "piante", "kind": "note", "id": "r-concime", "x": 1, "y": 1,
        })
    assert exc.value.code == "bad_request"


def test_jenny_does_not_move_a_postit() -> None:
    card = {"id": "acero", "title": "Acero", "pos": {"x": 1, "y": 1}}
    before = model.normalize_board({"cards": [{**card, "postitPos": {"x": 70, "y": 10}}]})
    after = model.normalize_board({"cards": [{**card, "postitPos": {"x": 10, "y": 10}}]})
    assert "card acero: postitPos changed" in model.ownership_violations(before, after)


async def test_the_label_and_the_notebook_objects_move_like_a_postit(ctx, notebook: Path) -> None:
    """L'etichetta dei cron di una scheda, e post-it ed etichetta di tutto il quaderno."""
    _seed(notebook)
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "auto", "id": "acero", "x": 50, "y": 20})
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "postit", "id": "", "x": 5, "y": 60})
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "auto", "id": "", "x": 40, "y": 60})
    board = read_board(notebook)
    assert model.card_by_id(board, "acero")["autoPos"] == {"x": 50.0, "y": 20.0, "by": "user"}
    assert board["notebookPostitPos"] == {"x": 5.0, "y": 60.0, "by": "user"}
    assert board["notebookAutoPos"] == {"x": 40.0, "y": 60.0, "by": "user"}
    # Le altre cose appese a una scheda non hanno una versione di tutto il quaderno.
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "board.move", {
            "wiki": "piante", "kind": "notes", "id": "", "x": 1, "y": 1})
    assert exc.value.code == "bad_request"


def test_jenny_does_not_move_a_label_nor_the_notebook_objects() -> None:
    card = {"id": "acero", "title": "Acero", "pos": {"x": 1, "y": 1}}
    before = model.normalize_board({"cards": [{**card, "autoPos": {"x": 70, "y": 10}}],
                                    "notebookPostitPos": {"x": 5, "y": 60},
                                    "notebookAutoPos": {"x": 40, "y": 60}})
    after = model.normalize_board({"cards": [{**card, "autoPos": {"x": 10, "y": 10}}],
                                   "notebookPostitPos": {"x": 6, "y": 60}})
    problems = model.ownership_violations(before, after)
    assert "card acero: autoPos changed" in problems
    assert "notebookPostitPos changed" in problems and "notebookAutoPos changed" in problems


def test_the_notebook_postit_counts_and_its_place_is_kept_free() -> None:
    board = model.normalize_board({"cards": [{"id": "acero", "title": "Acero",
                                              "pos": {"x": 2, "y": 3}}],
                                   "notebookPostitPos": {"x": 40, "y": 3},
                                   "notebookAutoPos": {"x": 70, "y": 3}})
    reminders = {"reminders": [{"id": "r-1", "card": "", "text": "Porta dentro le piante",
                                "state": "open", "when": {"kind": "date", "date": "2026-12-01"}}]}
    assert model.items_on_board(board, reminders) == 2
    boxes = model._boxes(board, reminders)
    assert (40.0, 3.0, 40.0 + model.POSTIT_BOX[0], 3.0 + model.POSTIT_BOX[1]) in boxes
    assert (70.0, 3.0, 70.0 + model.AUTO_BOX[0], 3.0 + model.AUTO_BOX[1]) in boxes
    # Una posizione storta resta com'è sul disco (le chiavi si conservano), ma non
    # occupa il muro: ``free_slot`` non la vede.
    junk = model.normalize_board({"notebookAutoPos": "qui"})
    assert model._boxes(junk, {}) == []


async def test_a_reminder_of_jenny_is_edited_and_dropped_by_id(ctx, notebook: Path) -> None:
    _seed(notebook)
    _three_reminders(notebook)
    await dispatch_command(ctx, "board.reminder.update", {
        "wiki": "piante", "id": "r-poltiglia", "text": "Poltiglia bordolese",
        "when": {"kind": "date", "date": "2026-11-05"},
    })
    rem = next(r for r in read_reminders(notebook)["reminders"] if r["id"] == "r-poltiglia")
    assert rem["text"] == "Poltiglia bordolese" and rem["when"]["date"] == "2026-11-05"
    assert rem["by"] == "user"
    await dispatch_command(ctx, "board.reminder.drop", {"wiki": "piante", "id": "r-poltiglia"})
    rem = next(r for r in read_reminders(notebook)["reminders"] if r["id"] == "r-poltiglia")
    assert rem["state"] == "dropped"
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, "board.reminder.drop", {"wiki": "piante", "id": "r-poltiglia"})
    assert exc.value.code == "not_found"


async def test_dropping_a_drawer_reminder_takes_its_section_too(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "reminder", "text": "Potatura",
        "when": {"kind": "date", "date": "2027-02-15"},
    })
    rid = read_reminders(notebook)["reminders"][0]["id"]
    await dispatch_command(ctx, "board.reminder.drop", {"wiki": "piante", "id": rid})
    assert model.card_by_id(read_board(notebook), "acero")["userSections"] == []


async def test_the_drawer_shows_a_reminder_once(ctx, notebook: Path) -> None:
    _seed(notebook)
    _three_reminders(notebook)
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "reminder", "text": "Potatura",
        "when": {"kind": "date", "date": "2027-02-15"},
    })
    view = build_card_view(notebook, "acero")
    assert "next" not in view
    # Nel calendario, in ordine; non anche come sezione dell'utente.
    assert [r["text"] for r in view["reminders"]] == [
        "Poltiglia", "Potatura", "Rinvaso", "Annaffia",
    ]
    assert all(s["kind"] != "reminder" for s in view["userSections"])


def test_the_drawer_takes_the_notes_out_of_the_sections(notebook: Path) -> None:
    """Le note e le foto sono i due mazzetti sotto il brief, non sezioni fra le altre."""
    from jenny.board.sections import section_resolver

    _seed(notebook)

    def apply(board):
        model.card_by_id(board, "acero")["userSections"] += [
            {"id": "s-note-1", "kind": "note", "created": "2026-10-03T09:00:00+02:00",
             "text": "Le gemme tonde sono a fiore."},
            {"id": "s-photo-1", "kind": "photo", "created": "2026-10-05T09:00:00+02:00",
             "path": "raw/photos/acero.jpg", "caption": ""},
            {"id": "s-note-2", "kind": "note", "created": "2026-10-12T09:00:00+02:00",
             "text": "Il vaso bianco in cantina."},
        ]

    mutate_board(notebook, apply)
    view = build_card_view(notebook, "acero",
                           sections_resolver=section_resolver(notebook.parent.parent, notebook))
    # Dalla più recente, con lo stato verso la wiki (qui senza riga di diario: in attesa).
    assert [n["id"] for n in view["notes"]] == ["s-note-2", "s-note-1"]
    assert view["notes"][0] == {"id": "s-note-2", "text": "Il vaso bianco in cantina.",
                                "created": "2026-10-12T09:00:00+02:00", "status": "pending"}
    assert view["userSections"] == []
    # La foto (qui il file non c'è, e non ha nota: nessuno stato verso il diario).
    assert view["photos"] == [{"id": "s-photo-1", "path": "raw/photos/acero.jpg", "caption": "",
                               "created": "2026-10-05T09:00:00+02:00", "exists": False,
                               "status": "none"}]


# ── Le foto: la galleria, la loro nota nel diario; le note sulla bacheca ─────


def _photo(notebook: Path, name: str, created: str, caption: str = "") -> dict:
    (notebook / "raw" / "photos").mkdir(parents=True, exist_ok=True)
    (notebook / "raw" / "photos" / name).write_bytes(b"\xff\xd8 fake")
    return {"id": f"s-photo-{name[:-4]}", "kind": "photo", "created": created,
            "path": f"raw/photos/{name}", "caption": caption}


def _journal(notebook: Path) -> str:
    return "".join(p.read_text() for p in sorted((notebook / "raw" / "journal").glob("*.md")))


async def test_a_photo_with_a_note_is_a_said_line_without_one_none(ctx, notebook: Path) -> None:
    _seed(notebook)
    data = base64.b64encode(b"\xff\xd8 fake").decode()
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "photo", "mime": "image/jpeg",
        "data": data, "caption": "Il germoglio nuovo è rosso!",
    })
    first = model.card_by_id(read_board(notebook), "acero")["userSections"][0]
    assert first["journalLine"].startswith("raw/journal/")
    assert f"[said] Il germoglio nuovo è rosso! (photo: {first['path']})" in _journal(notebook)
    # Senza nota non c'è niente da portare nella wiki: nessuna riga.
    before = _journal(notebook)
    await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "monstera", "kind": "photo", "mime": "image/jpeg",
        "data": data, "caption": "  ",
    })
    second = model.card_by_id(read_board(notebook), "monstera")["userSections"][0]
    assert "journalLine" not in second and second["caption"] == ""
    assert _journal(notebook) == before


async def test_correcting_a_photo_note_writes_a_new_line(ctx, notebook: Path) -> None:
    from jenny.board.sections import section_resolver

    _seed(notebook)

    def apply(board):
        model.card_by_id(board, "acero")["userSections"].append(
            _photo(notebook, "a.jpg", "2026-10-14T17:48:43+02:00"))

    mutate_board(notebook, apply)
    resolver = section_resolver(notebook.parent.parent, notebook)
    assert build_card_view(notebook, "acero", sections_resolver=resolver)["photos"][0]["status"] \
        == "none"
    await dispatch_command(ctx, "board.section.update", {
        "wiki": "piante", "card": "acero", "id": "s-photo-a", "text": "Prime punte rosse",
    })
    photo = model.card_by_id(read_board(notebook), "acero")["userSections"][0]
    assert photo["caption"] == "Prime punte rosse" and photo["journalLine"]
    assert "[said] Prime punte rosse (photo: raw/photos/a.jpg)" in _journal(notebook)
    view = build_card_view(notebook, "acero", sections_resolver=resolver)
    assert view["photos"][0]["status"] == "pending" and view["photos"][0]["exists"] is True
    # Svuotata: la foto resta senza nota, e la riga di prima resta nel diario.
    before = _journal(notebook)
    await dispatch_command(ctx, "board.section.update", {
        "wiki": "piante", "card": "acero", "id": "s-photo-a", "text": "",
    })
    photo = model.card_by_id(read_board(notebook), "acero")["userSections"][0]
    assert photo["caption"] == "" and "journalLine" not in photo
    assert _journal(notebook) == before
    # Togliere la foto toglie la sezione, non il file: ``raw/`` è il grezzo.
    await dispatch_command(ctx, "board.section.remove", {
        "wiki": "piante", "card": "acero", "id": "s-photo-a",
    })
    assert model.card_by_id(read_board(notebook), "acero")["userSections"] == []
    assert (notebook / "raw" / "photos" / "a.jpg").is_file()


def _notes_and_photos(notebook: Path) -> None:
    def apply(board):
        model.card_by_id(board, "acero")["userSections"] += [
            {"id": "s-note-1", "kind": "note", "created": "2026-10-02T20:30:00+02:00",
             "text": "Il vaso nuovo è da 20 cm."},
            {"id": "s-note-2", "kind": "note", "created": "2026-10-14T17:55:00+02:00",
             "text": "Rossa senza essere una cultivar."},
            {"id": "s-note-3", "kind": "note", "created": "2026-10-10T09:10:00+02:00",
             "text": "Niente concime fino a marzo."},
            _photo(notebook, "p1.jpg", "2026-09-20T10:15:00+02:00", "Appena rientrato"),
            _photo(notebook, "p2.jpg", "2026-10-01T09:30:00+02:00", "Prime punte rosse"),
            _photo(notebook, "p3.jpg", "2026-10-08T17:40:00+02:00"),
            _photo(notebook, "p4.jpg", "2026-10-14T17:48:43+02:00", "Il germoglio è rosso!"),
            {"id": "s-photo-gone", "kind": "photo", "created": "2026-10-15T08:00:00+02:00",
             "path": "raw/photos/gone.jpg", "caption": "il file non c'è più"},
        ]

    mutate_board(notebook, apply)


def test_the_board_has_a_lilac_postit_and_a_gallery_per_card(notebook: Path) -> None:
    _seed(notebook)
    _three_reminders(notebook)
    _notes_and_photos(notebook)
    view = build_board_view(notebook, today=date(2026, 10, 14))
    # Solo l'acero ha note e foto.
    assert [n["card"] for n in view["notePostits"]] == ["acero"]
    lilac = view["notePostits"][0]
    assert lilac["count"] == 3
    assert [r["text"] for r in lilac["rows"]] == [
        "Rossa senza essere una cultivar.", "Niente concime fino a marzo.",
    ]
    gallery = view["galleries"][0]
    # Una foto il cui file non c'è più non conta e non si mostra.
    assert gallery["card"] == "acero" and gallery["count"] == 4
    assert [p["path"] for p in gallery["photos"]] == [
        "raw/photos/p4.jpg", "raw/photos/p3.jpg", "raw/photos/p2.jpg",
    ]
    assert gallery["photos"][0]["caption"] == "Il germoglio è rosso!"
    # Nati accanto alla scheda senza coprire niente: schede, gialli, fra loro.
    rects = [(p["pos"]["x"], p["pos"]["y"], p["pos"]["x"] + model.POSTIT_BOX[0],
              p["pos"]["y"] + model.POSTIT_BOX[1]) for p in view["postits"]]
    rects.append((lilac["pos"]["x"], lilac["pos"]["y"], lilac["pos"]["x"] + model.NOTES_BOX[0],
                  lilac["pos"]["y"] + model.NOTES_BOX[1]))
    rects.append((gallery["pos"]["x"], gallery["pos"]["y"],
                  gallery["pos"]["x"] + model.GALLERY_BOX[0],
                  gallery["pos"]["y"] + model.GALLERY_BOX[1]))
    for card in view["cards"]:
        rects.append((card["pos"]["x"], card["pos"]["y"],
                      card["pos"]["x"] + card["box"][0], card["pos"]["y"] + card["box"][1]))
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert model._overlap(a, b) == 0, (a, b)
    # I gialli restano dove nascevano prima che il lilla e la galleria esistessero.
    plain = build_board_view(notebook, today=date(2026, 10, 14))
    assert [p["pos"] for p in plain["postits"]] == [p["pos"] for p in view["postits"]]
    # Il lilla e la galleria non contano nei 40 oggetti.
    assert model.items_on_board(read_board(notebook), read_reminders(notebook)) == 4


def test_a_card_without_notes_or_photos_has_neither(notebook: Path) -> None:
    _seed(notebook)
    view = build_board_view(notebook)
    assert view["notePostits"] == [] and view["galleries"] == []


async def test_moving_the_lilac_and_the_gallery_makes_their_place_the_users(
    ctx, notebook: Path,
) -> None:
    _seed(notebook)
    _notes_and_photos(notebook)
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "notes", "id": "acero", "x": 30, "y": 60,
    })
    await dispatch_command(ctx, "board.move", {
        "wiki": "piante", "kind": "photos", "id": "acero", "x": 70, "y": 5.5,
    })
    card = model.card_by_id(read_board(notebook), "acero")
    assert card["notesPos"] == {"x": 30.0, "y": 60.0, "by": "user"}
    assert card["photosPos"] == {"x": 70.0, "y": 5.5, "by": "user"}
    view = build_board_view(notebook)
    assert view["notePostits"][0]["pos"] == card["notesPos"]
    assert view["galleries"][0]["pos"] == card["photosPos"]
    # Dove li ha messi l'utente, occupano il muro: una scheda nuova non ci va sopra.
    x, y = model.free_slot(read_board(notebook), read_reminders(notebook),
                           near=(70.0, 5.5))
    assert model._overlap((x, y, x + model.CARD_BOX[0], y + model.CARD_BOX[1]),
                          (70.0, 5.5, 70.0 + model.GALLERY_BOX[0],
                           5.5 + model.GALLERY_BOX[1])) == 0


def test_jenny_does_not_move_the_lilac_or_the_gallery() -> None:
    card = {"id": "acero", "title": "Acero", "pos": {"x": 1, "y": 1}}
    for key in ("notesPos", "photosPos"):
        before = model.normalize_board({"cards": [{**card, key: {"x": 70, "y": 10}}]})
        after = model.normalize_board({"cards": [{**card, key: {"x": 10, "y": 10}}]})
        assert f"card acero: {key} changed" in model.ownership_violations(before, after)
