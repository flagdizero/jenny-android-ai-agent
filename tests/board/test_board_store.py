"""L'imbuto della bacheca: rilettura dentro il lock, ``.bak``, chiavi ignote, proprietà."""

from __future__ import annotations

import json
import threading

import pytest

from jenny.board import model
from jenny.board.store import (
    BoardOwnershipError,
    board_path,
    mutate_board,
    mutate_reminders,
    read_board,
    read_card_summary,
    read_reminders,
    reminders_path,
    write_card_summary,
)


def _card(card_id: str, **extra):
    base = {"id": card_id, "title": card_id.title(), "pos": {"x": 10, "y": 10, "by": "jenny"}}
    base.update(extra)
    return base


def test_a_missing_board_reads_as_none_and_the_first_write_creates_it(tmp_path):
    assert read_board(tmp_path) is None
    mutate_board(tmp_path, lambda b: b["cards"].append(_card("acero")))
    board = read_board(tmp_path)
    assert board is not None
    assert [c["id"] for c in board["cards"]] == ["acero"]
    assert board["version"] == model.BOARD_VERSION


def test_returning_false_writes_nothing(tmp_path):
    mutate_board(tmp_path, lambda b: False)
    assert not board_path(tmp_path).exists()


def test_an_exception_in_the_callback_writes_nothing(tmp_path):
    mutate_board(tmp_path, lambda b: b["cards"].append(_card("acero")))
    before = board_path(tmp_path).read_text()

    def boom(board):
        board["cards"].clear()
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        mutate_board(tmp_path, boom)
    assert board_path(tmp_path).read_text() == before


def test_a_ui_write_during_a_gardener_write_is_not_lost(tmp_path):
    """Il caso per cui l'imbuto esiste: il giardiniere ha letto, l'utente
    trascina, il giardiniere salva. Con la rilettura dentro il lock il
    trascinamento resta."""
    mutate_board(tmp_path, lambda b: b["cards"].append(_card("acero")))
    inside = threading.Event()
    release = threading.Event()

    def slow_gardener(board):
        inside.set()
        release.wait(5)
        board["cards"][0]["glyph"] = "leaf"

    def user_drag(board):
        card = model.card_by_id(board, "acero")
        card["pos"] = {"x": 60, "y": 40, "by": "user"}

    gardener = threading.Thread(target=lambda: mutate_board(tmp_path, slow_gardener, actor="jenny"))
    gardener.start()
    assert inside.wait(5)
    user = threading.Thread(target=lambda: mutate_board(tmp_path, user_drag))
    user.start()
    release.set()
    gardener.join(5)
    user.join(5)

    card = model.card_by_id(read_board(tmp_path), "acero")
    assert card["glyph"] == "leaf"
    assert card["pos"] == {"x": 60.0, "y": 40.0, "by": "user"}


def test_unknown_keys_survive_a_rewrite(tmp_path):
    path = board_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1,
        "futureTopLevel": {"a": 1},
        "cards": [{**_card("acero"), "futureCardField": [1, 2]}],
    }))
    mutate_board(tmp_path, lambda b: b.__setitem__("lastSeen", "2026-10-04T10:00:00+02:00"))
    data = json.loads(path.read_text())
    assert data["futureTopLevel"] == {"a": 1}
    assert data["cards"][0]["futureCardField"] == [1, 2]


def test_a_corrupt_board_recovers_from_the_backup(tmp_path):
    mutate_board(tmp_path, lambda b: b["cards"].append(_card("acero")))
    mutate_board(tmp_path, lambda b: b["cards"].append(_card("fico")))
    path = board_path(tmp_path)
    path.write_text("{ not json")
    board = read_board(tmp_path)
    # Il .bak e' la versione prima dell'ultima scrittura: c'e' l'acero.
    assert [c["id"] for c in board["cards"]] == ["acero"]
    # Il file rotto e' messo da parte, non cancellato.
    assert list(path.parent.glob("board.corrupt-*.json"))


def test_jenny_cannot_move_add_or_remove_cards(tmp_path):
    mutate_board(tmp_path, lambda b: b["cards"].append(_card("acero")))

    def move(board):
        board["cards"][0]["pos"]["x"] = 90

    def add(board):
        board["cards"].append(_card("fico"))

    def remove(board):
        board["cards"].clear()

    for apply in (move, add, remove):
        with pytest.raises(BoardOwnershipError):
            mutate_board(tmp_path, apply, actor="jenny")
    # Lo stesso gesto, fatto dall'utente, passa.
    mutate_board(tmp_path, move)
    assert read_board(tmp_path)["cards"][0]["pos"]["x"] == 90


def test_jenny_never_touches_what_the_user_owns(tmp_path):
    def seed(board):
        board["skeleton"] = ["Acqua", "Storia"]
        board["cards"].append(_card(
            "acero", title="Il mio acero", titleBy="user", pages=["travaso-acero"],
            userSections=[{"id": "s-1", "kind": "note", "text": "davanzale sud"}],
        ))
        board["cards"].append(_card("fico", pages=["fico"]))
        board["connections"].append({"a": "acero", "b": "fico", "label": "vicini", "by": "user"})

    mutate_board(tmp_path, seed)
    forbidden = [
        lambda b: b["cards"][0].__setitem__("title", "Acero"),
        lambda b: b["cards"][0]["userSections"].clear(),
        lambda b: b["cards"][0].__setitem__("pinned", True),
        lambda b: b["cards"][0]["pages"].clear(),
        lambda b: b["connections"].clear(),
        lambda b: b["skeleton"].reverse(),
        lambda b: b.__setitem__("cards", list(reversed(b["cards"]))),
    ]
    for apply in forbidden:
        with pytest.raises(BoardOwnershipError):
            mutate_board(tmp_path, apply, actor="jenny")

    # Quel che e' di Jenny, invece, lo cambia: il titolo suo, una pagina in piu',
    # una proposta nella busta.
    def allowed(board):
        board["cards"][1]["title"] = "Fico d'India"
        board["cards"][1]["pages"].append("fico-talea")
        board["suggestions"].append({"id": "sg-1", "kind": "add_card", "subject": "ginkgo"})

    mutate_board(tmp_path, allowed, actor="jenny")
    board = read_board(tmp_path)
    assert board["cards"][1]["title"] == "Fico d'India"
    assert board["cards"][1]["pages"] == ["fico", "fico-talea"]
    assert board["suggestions"][0]["id"] == "sg-1"


def test_a_page_belongs_to_one_card_at_most(tmp_path):
    def seed(board):
        board["cards"].append(_card("acero", pages=["vasi"]))
        board["cards"].append(_card("monstera", pages=["vasi", "monstera"]))

    board = mutate_board(tmp_path, seed)
    assert board["cards"][1]["pages"] == ["monstera"]
    assert model.page_owners(board) == {"vasi": "acero", "monstera": "monstera"}


def test_reminders_of_the_user_are_off_limits_to_jenny(tmp_path):
    def seed(rems):
        rems["reminders"].append({
            "id": "r-mine", "card": "acero", "text": "Potatura", "by": "user",
            "when": {"kind": "date", "date": "2027-02-15"},
        })
        rems["reminders"].append({
            "id": "r-jenny", "card": "acero", "text": "Travaso", "by": "jenny",
            "when": {"kind": "date", "date": "2027-03-01", "precision": "month"},
            "pos": {"x": 25, "y": 37, "by": "jenny"},
        })

    mutate_reminders(tmp_path, seed)
    with pytest.raises(BoardOwnershipError):
        mutate_reminders(
            tmp_path, lambda r: r["reminders"][0].__setitem__("state", "done"), actor="jenny"
        )
    with pytest.raises(BoardOwnershipError):
        mutate_reminders(tmp_path, lambda r: r["reminders"].pop(), actor="jenny")
    with pytest.raises(BoardOwnershipError):
        mutate_reminders(
            tmp_path, lambda r: r["reminders"][1]["pos"].__setitem__("x", 80), actor="jenny"
        )
    mutate_reminders(
        tmp_path, lambda r: r["reminders"][1].__setitem__("state", "done"), actor="jenny"
    )
    assert read_reminders(tmp_path)["reminders"][1]["state"] == "done"
    assert reminders_path(tmp_path).exists()


def test_bad_reminders_are_dropped_not_fatal(tmp_path):
    path = reminders_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"reminders": [
        {"id": "ok", "text": "Concime", "when": {"kind": "date", "date": "2026-10-10"}},
        {"id": "no-date", "text": "x", "when": {"kind": "date", "date": "presto"}},
        {"id": "bad op", "text": "x", "when": {"kind": "date", "date": "2026-10-10"}},
        {"id": "cond", "text": "Annaffia", "when": {
            "kind": "condition", "app": "waterbot", "field": "moisture", "op": "<", "value": 30,
        }},
    ]}))
    ids = [r["id"] for r in read_reminders(tmp_path)["reminders"]]
    assert ids == ["ok", "cond"]


def test_card_summaries_round_trip_and_reject_bad_ids(tmp_path):
    write_card_summary(tmp_path, "acero", {
        "brief": "In vaso 14, sano.",
        "fingerprint": "sha1:x",
        "sections": [
            {"title": "Acqua", "text": "Quando i primi 2 cm sono asciutti.",
             "sources": ["irrigazione-acero"]},
            {"title": "", "text": "senza titolo: scartata"},
        ],
    })
    summary = read_card_summary(tmp_path, "acero")
    assert summary["brief"] == "In vaso 14, sano."
    assert [s["title"] for s in summary["sections"]] == ["Acqua"]
    assert read_card_summary(tmp_path, "../evil") is None
    with pytest.raises(ValueError):
        write_card_summary(tmp_path, "../evil", {})


def test_month_precision_is_due_at_the_start_of_the_month():
    rem = {"when": {"kind": "date", "date": "2027-03-20", "precision": "month"}}
    assert model.reminder_due(rem).isoformat() == "2027-03-01"


def test_free_slot_avoids_what_is_already_there():
    board = model.empty_board()
    board["cards"] = [model._normalize_card(_card("a", pos={"x": 4, "y": 5}))]
    slot = model.free_slot(board)
    assert slot != (4, 5)
