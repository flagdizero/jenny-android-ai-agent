"""Una scheda ha l'ingombro della sua forma: polaroid, o striscia della sua taglia."""

from __future__ import annotations

import pytest

from jenny.board import model
from jenny.board.store import BoardOwnershipError, mutate_board, read_board
from jenny.board.view import build_board_view


def test_a_slip_takes_the_room_of_its_size() -> None:
    assert model.card_box({}) == model.CARD_BOX
    assert model.card_box({"size": "XL"}) == model.SLIP_BOX["XL"]
    assert model.card_box({"size": "huge"}) == model.CARD_BOX
    board = model.normalize_board({"cards": [
        {"id": "pf", "title": "Portafoglio", "pos": {"x": 2, "y": 3}, "size": "XL",
         "app": {"slug": "pf"}}]})
    assert board["cards"][0]["size"] == "XL"
    x, y = model.free_slot(board, near=(20, 20))
    w, h = model.SLIP_BOX["XL"]
    # Il posto trovato non cade sotto la striscia XL (larga 40, alta 66).
    assert not (2 <= x < 2 + w and 3 <= y < 3 + h and x + model.CARD_BOX[0] > 2)


def test_jenny_cannot_change_the_size(notebook) -> None:
    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "pf", "title": "Portafoglio", "pos": {"x": 1, "y": 1}, "size": "M",
        "app": {"slug": "pf", "params": {}}}))
    with pytest.raises(BoardOwnershipError):
        mutate_board(notebook, lambda b: b["cards"][0].update(size="XL"), actor=model.JENNY)
    assert read_board(notebook)["cards"][0]["size"] == "M"


def test_the_view_sends_each_card_its_box(notebook) -> None:
    mutate_board(notebook, lambda b: b["cards"].extend([
        {"id": "acero", "title": "Acero", "pos": {"x": 1, "y": 1}},
        {"id": "pf", "title": "Portafoglio", "pos": {"x": 30, "y": 1}, "size": "L",
         "app": {"slug": "pf", "params": {}}}]))
    view = build_board_view(notebook)
    boxes = {c["id"]: c["box"] for c in view["cards"]}
    assert boxes == {"acero": list(model.CARD_BOX), "pf": list(model.SLIP_BOX["L"])}
