"""Le app sulla bacheca: la polaroid che è un'app, col suo valore al posto della foto.

Un'app non entra nel cassetto di un'altra scheda: è una scheda sua, legata con
un filo alla scheda con cui c'entra. La scheda-soggetto tiene la sua foto.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.board import model
from jenny.board.sections import alert_probe
from jenny.board.store import mutate_board, read_board
from jenny.board.view import build_board_view
from jenny.webui.commands import CommandError, dispatch_command

WATERBOT = {
    "name": "WaterBot", "description": "Moisture",
    "summary": {"collection": "readings", "field": "moisture", "unit": "%",
                "params": {"sensor": {"type": "string"}}, "where": {"sensor": "$sensor"},
                "alarm": {"field": "moisture", "op": "<", "value": 20}, "trendDays": 7},
}


def _app(workspace: Path, records: list[dict]) -> None:
    d = workspace / "apps" / "waterbot"
    (d / "data").mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(WATERBOT))
    (d / "data" / "readings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def _seed(notebook: Path) -> None:
    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "acero", "title": "Acero", "pages": [], "pos": {"x": 5, "y": 6, "by": "jenny"}}))


@pytest.fixture
def waterbot(workspace_root: Path) -> Path:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _app(workspace_root, [
        {"id": "1", "ts": now, "sensor": "acerello", "moisture": 38},
        {"id": "2", "ts": now, "sensor": "monstera", "moisture": 12},
    ])
    return workspace_root


async def test_a_card_that_is_an_app_is_a_strip_of_its_size(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    out = await dispatch_command(ctx, "board.card.add", {
        "wiki": "piante", "near": {"x": 150, "y": 50},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}})
    card_id = out["card"]["id"]
    card = model.card_by_id(read_board(notebook), card_id)
    assert card["app"] == {"slug": "waterbot", "params": {"sensor": "acerello"}}
    # Il riassunto di prima (un valore) e' una striscia S, e la taglia sta sulla scheda.
    assert card["title"] == "WaterBot" and card["size"] == "S"
    view = build_board_view(notebook, workspace=waterbot,
                            alert_probe=alert_probe(waterbot))
    shown = next(c for c in view["cards"] if c["id"] == card_id)
    slip = shown["slip"]
    assert slip["size"] == "S" and slip["state"] == "ok" and shown["box"] == list(
        model.SLIP_BOX["S"])
    assert slip["blocks"][0]["kind"] == "value" and slip["blocks"][0]["value"] == 38
    assert slip["blocks"][0]["unit"] == "%"
    # Il tocco: senza schermo apre il cassetto, viva.
    assert shown["app"] == {"slug": "waterbot", "params": {"sensor": "acerello"},
                            "hasScreen": False, "state": "live", "icon": shown["app"]["icon"]}
    # La scheda-soggetto tiene la sua foto.
    acero = next(c for c in view["cards"] if c["id"] == "acero")
    assert "slip" not in acero and acero["box"] == list(model.CARD_BOX)
    # Una seconda scheda, sul sensore in allarme: l'angolo rosso.
    out = await dispatch_command(ctx, "board.card.add", {
        "wiki": "piante", "app": {"slug": "waterbot", "params": {"sensor": "monstera"}}})
    view = build_board_view(notebook, workspace=waterbot,
                            alert_probe=alert_probe(waterbot))
    red = next(c for c in view["cards"] if c["id"] == out["card"]["id"])
    assert red["signal"] == "alert" and red["slip"]["state"] == "alarm"


def test_a_new_version_resizes_the_cards_that_show_it(notebook, waterbot) -> None:
    from jenny.board.place import refresh_app_cards

    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}}))
    assert refresh_app_cards(waterbot) == 1
    assert read_board(notebook)["cards"][0]["size"] == "S"
    manifest = waterbot / "apps" / "waterbot" / "app.json"
    grown = json.loads(manifest.read_text())
    grown["summary"] = {**WATERBOT["summary"], "blocks": [
        {"kind": "value", "field": "moisture", "unit": "%"},
        {"kind": "chart", "field": "moisture", "days": 7},
        {"kind": "table", "columns": {"moisture": {"label": "%"}}, "max": 2}]}  # 2+2+2: L
    manifest.write_text(json.dumps(grown))
    assert refresh_app_cards(waterbot, "waterbot") == 1
    assert read_board(notebook)["cards"][0]["size"] == "L"
    assert refresh_app_cards(waterbot) == 0


async def test_an_app_next_to_a_card_is_its_own_card_tied_by_a_thread(
        ctx, notebook, waterbot) -> None:
    _seed(notebook)
    out = await dispatch_command(ctx, "board.card.add", {
        "wiki": "piante", "app": {"slug": "waterbot", "params": {"sensor": "acerello"}},
        "link": "acero"})
    board = read_board(notebook)
    new = out["card"]["id"]
    assert new != "acero" and model.card_by_id(board, new)["app"]["slug"] == "waterbot"
    assert {frozenset((c["a"], c["b"])) for c in board["connections"]} == {
        frozenset(("acero", new))}
    assert model.card_by_id(board, "acero")["userSections"] == []
    assert "tied to «Acero»" in out["where"]
    view = build_board_view(notebook, workspace=waterbot)
    acero = next(c for c in view["cards"] if c["id"] == "acero")
    assert "cover" not in acero and "app" not in acero
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.card.add", {
            "wiki": "piante", "app": {"slug": "waterbot"}, "link": "mai-vista"})
    assert err.value.code == "conflict"


async def test_an_app_never_goes_inside_another_cards_drawer(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.section.add", {
            "wiki": "piante", "card": "acero", "kind": "app", "app": "waterbot"})
    assert "card of its own" in str(err.value)
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.card.add", {
            "wiki": "piante", "app": {"slug": "mai-vista"}})
    assert err.value.code == "not_found"
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.card.add", {
            "wiki": "piante", "app": {"slug": "waterbot", "params": {"pianta": "acero"}}})
    assert "unknown params" in str(err.value)


def test_jenny_cannot_change_a_card_app(notebook) -> None:
    from jenny.board.store import BoardOwnershipError

    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {}}}))
    with pytest.raises(BoardOwnershipError):
        mutate_board(notebook, lambda b: b["cards"][0].pop("app"), actor=model.JENNY)


async def test_jenny_proposes_an_app_card_and_yes_places_it(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    mutate_board(notebook, lambda b: b["suggestions"].append({
        "id": "sg-wb", "kind": "add_app_card", "subject": "WaterBot", "app": "waterbot",
        "params": {"sensor": "monstera"}, "card": "acero", "text": "Ne faccio una scheda?"}))
    await dispatch_command(ctx, "board.suggestion.respond", {
        "wiki": "piante", "id": "sg-wb", "answer": "yes", "near": {"x": 60, "y": 60}})
    board = read_board(notebook)
    card = next(c for c in board["cards"] if c.get("app"))
    assert card["app"] == {"slug": "waterbot", "params": {"sensor": "monstera"}}
    assert card["title"] == "WaterBot"
    assert [{c["a"], c["b"]} for c in board["connections"]] == [{card["id"], "acero"}]


async def test_an_old_section_proposal_becomes_an_app_card(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    mutate_board(notebook, lambda b: b["suggestions"].append({
        "id": "sg-old", "kind": "add_section", "subject": "Acero", "card": "acero",
        "app": "waterbot", "params": {"sensor": "acerello"}, "text": "Metto il sensore?"}))
    await dispatch_command(ctx, "board.suggestion.respond", {
        "wiki": "piante", "id": "sg-old", "answer": "yes"})
    board = read_board(notebook)
    assert model.card_by_id(board, "acero")["userSections"] == []
    card = next(c for c in board["cards"] if c.get("app"))
    assert card["title"] == "WaterBot"
    assert [{c["a"], c["b"]} for c in board["connections"]] == [{card["id"], "acero"}]


def test_a_card_app_changing_state_is_journaled_once(notebook, waterbot) -> None:
    from jenny.board.clock import watch_app_sections

    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}}))
    assert watch_app_sections(waterbot, notebook) == 0
    rec = waterbot / "apps" / "waterbot" / "data" / "readings.jsonl"
    from datetime import datetime, timedelta, timezone

    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    rec.write_text(rec.read_text() + json.dumps({
        "id": "3", "ts": later.isoformat(timespec="seconds"),
        "sensor": "acerello", "moisture": 10}) + "\n")
    assert watch_app_sections(waterbot, notebook) == 1
    assert read_board(notebook)["since"][-1]["kind"] == "app_event"


def test_the_board_block_names_the_app_of_a_card(notebook, waterbot, monkeypatch) -> None:
    from jenny.board.context_block import board_block, drawer_text

    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: waterbot)
    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}}))
    card = read_board(notebook)["cards"][0]
    assert "WaterBot 38%" in drawer_text(notebook, card) or "moisture 38%" in drawer_text(
        notebook, card)
    # Nel prompt di sistema il nome, non il valore (che cambia a ogni lettura).
    assert "[auto: WaterBot]" in board_block(notebook)
    assert "38%" not in board_block(notebook)


def test_the_drawer_of_an_app_card_starts_from_the_app(notebook, waterbot) -> None:
    from jenny.board.sections import section_resolver
    from jenny.board.view import build_card_view

    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}}))
    view = build_card_view(notebook, "wb", sections_resolver=section_resolver(waterbot, notebook))
    own = view["userSections"][0]
    assert (own["id"], own["kind"], own["own"]) == ("@card", "app", True)
    assert own["slip"]["blocks"][0]["value"] == 38
    # Non e' una sezione dell'utente: la bacheca su disco non la conosce.
    assert read_board(notebook)["cards"][0]["userSections"] == []
