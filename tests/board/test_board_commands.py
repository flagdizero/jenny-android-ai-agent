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
    section = model.card_by_id(read_board(notebook), "acero")["userSections"][0]
    assert section["reminder"] == rem["id"]
    await dispatch_command(ctx, "board.section.remove", {
        "wiki": "piante", "card": "acero", "id": section["id"],
    })
    assert read_reminders(notebook)["reminders"][0]["state"] == "dropped"


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


async def test_setup_records_the_users_skeleton_and_reminder_wish(ctx, notebook: Path) -> None:
    out = await dispatch_command(ctx, "board.setup", {
        "wiki": "piante", "skeleton": "Vaso e travaso, Acqua; Acqua, Storia",
        "remind": "i travasi e le concimazioni",
    })
    assert out["skeleton"] == ["Vaso e travaso", "Acqua", "Storia"]
    board = read_board(notebook)
    assert board["skeleton"] == ["Vaso e travaso", "Acqua", "Storia"]
    assert board["remindHint"] == "i travasi e le concimazioni"
    assert board["cards"] == []


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
