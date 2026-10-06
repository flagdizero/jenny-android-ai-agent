"""Le app sulla bacheca: la scheda che è un'app, la copertina, le sezioni con i loro parametri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.board import model
from jenny.board.sections import alert_probe, cover_of, short_value
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


async def test_a_card_that_is_an_app_has_its_value_on_the_cover(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    out = await dispatch_command(ctx, "board.card.add", {
        "wiki": "piante", "near": {"x": 150, "y": 50},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}})
    card_id = out["card"]["id"]
    card = model.card_by_id(read_board(notebook), card_id)
    assert card["app"] == {"slug": "waterbot", "params": {"sensor": "acerello"}}
    assert card["cover"] == "@card" and card["title"] == "WaterBot"
    view = build_board_view(notebook, workspace=waterbot,
                            alert_probe=alert_probe(waterbot, notebook))
    shown = next(c for c in view["cards"] if c["id"] == card_id)
    assert shown["cover"]["short"] == "38" and shown["cover"]["unit"] == "%"
    assert shown["cover"]["state"] == "ok"
    assert shown["coverOptions"] == [{"id": "@card", "name": "WaterBot"}]
    # Una seconda scheda, sul sensore in allarme: l'angolo rosso.
    out = await dispatch_command(ctx, "board.card.add", {
        "wiki": "piante", "app": {"slug": "waterbot", "params": {"sensor": "monstera"}}})
    view = build_board_view(notebook, workspace=waterbot,
                            alert_probe=alert_probe(waterbot, notebook))
    red = next(c for c in view["cards"] if c["id"] == out["card"]["id"])
    assert red["signal"] == "alert" and red["cover"]["state"] == "alarm"


async def test_a_section_can_be_the_cover_and_the_photo_comes_back(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    out = await dispatch_command(ctx, "board.section.add", {
        "wiki": "piante", "card": "acero", "kind": "app", "app": "waterbot",
        "params": {"sensor": "acerello"}})
    view = build_board_view(notebook, workspace=waterbot)
    assert "cover" not in view["cards"][0]
    await dispatch_command(ctx, "board.card.cover", {"wiki": "piante", "id": "acero",
                                                     "cover": out["id"]})
    view = build_board_view(notebook, workspace=waterbot)
    assert view["cards"][0]["cover"]["short"] == "38"
    await dispatch_command(ctx, "board.card.cover", {"wiki": "piante", "id": "acero", "cover": ""})
    assert "cover" not in build_board_view(notebook, workspace=waterbot)["cards"][0]
    with pytest.raises(CommandError):
        await dispatch_command(ctx, "board.card.cover", {"wiki": "piante", "id": "acero",
                                                         "cover": "@card"})


async def test_a_section_must_name_a_real_app_and_its_params(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.section.add", {
            "wiki": "piante", "card": "acero", "kind": "app", "app": "mai-vista"})
    assert err.value.code == "not_found"
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.section.add", {
            "wiki": "piante", "card": "acero", "kind": "app", "app": "waterbot",
            "params": {"pianta": "acero"}})
    assert "unknown params" in str(err.value)


def test_jenny_cannot_change_a_cover_or_a_card_app(notebook) -> None:
    from jenny.board.store import BoardOwnershipError

    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {}}, "cover": "@card"}))
    with pytest.raises(BoardOwnershipError):
        mutate_board(notebook, lambda b: b["cards"][0].pop("cover"), actor=model.JENNY)


def test_the_cover_is_short() -> None:
    assert short_value(12430.2) == "12,4k"
    assert short_value(38) == "38"
    assert short_value(True) == "✓"
    assert short_value("latte e uova") == "latte…"
    assert cover_of({"state": "empty", "value": None}) is None
    assert cover_of({"state": "stale", "value": 3, "unit": "items"})["unit"] == ""


async def test_jenny_proposes_an_app_card_and_yes_places_it(ctx, notebook, waterbot) -> None:
    _seed(notebook)
    mutate_board(notebook, lambda b: b["suggestions"].append({
        "id": "sg-wb", "kind": "add_app_card", "subject": "WaterBot", "app": "waterbot",
        "params": {"sensor": "monstera"}, "text": "Ne faccio una scheda?"}))
    await dispatch_command(ctx, "board.suggestion.respond", {
        "wiki": "piante", "id": "sg-wb", "answer": "yes", "near": {"x": 60, "y": 60}})
    card = next(c for c in read_board(notebook)["cards"] if c.get("app"))
    assert card["app"] == {"slug": "waterbot", "params": {"sensor": "monstera"}}
    assert card["cover"] == "@card" and card["title"] == "WaterBot"


def test_a_card_app_changing_state_is_journaled_once(notebook, waterbot) -> None:
    from jenny.board.clock import watch_app_sections

    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "wb", "title": "WaterBot", "pos": {"x": 1, "y": 1},
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}, "cover": "@card"}))
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
        "app": {"slug": "waterbot", "params": {"sensor": "acerello"}}, "cover": "@card"}))
    card = read_board(notebook)["cards"][0]
    assert "WaterBot 38%" in drawer_text(notebook, card) or "moisture 38%" in drawer_text(
        notebook, card)
    assert "WaterBot 38%" in board_block(notebook)
