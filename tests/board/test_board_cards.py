"""Le schede dell'utente: crearle, rinominarle, toglierle con un Annulla che rimette tutto."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jenny.board import model
from jenny.board.removed import purge_removed_cards
from jenny.board.store import (
    BoardOwnershipError,
    mutate_board,
    read_board,
    write_card_summary,
)
from jenny.board.view import build_board_view
from jenny.webui.commands import CommandError, dispatch_command


def _seed(notebook: Path) -> None:
    def apply(board):
        board["cards"].append({
            "id": "acero", "title": "Acero", "pages": ["travaso-acero.md"],
            "pos": {"x": 5, "y": 6, "by": "jenny"}, "glyph": "leaf",
        })
        board["cards"].append({
            "id": "monstera", "title": "Monstera", "pages": ["rinvaso-monstera.md"],
            "pos": {"x": 47, "y": 4, "by": "jenny"},
        })
        board["connections"].append({"a": "acero", "b": "monstera", "label": "vasi", "by": "jenny"})

    mutate_board(notebook, apply)


async def test_a_new_card_is_the_users_and_lands_near_the_finger(ctx, notebook: Path) -> None:
    _seed(notebook)
    out = await dispatch_command(ctx, "board.card.add", {
        "wiki": "piante", "title": "Fico", "near": {"x": 160, "y": 130},
    })
    card = model.card_by_id(read_board(notebook), out["card"]["id"])
    assert card["title"] == "Fico" and card["titleBy"] == "user"
    assert card["pos"]["by"] == "user" and card["pages"] == []
    # Al centro del dito: la scheda e' larga 20 e alta 40, quindi parte da 150, 110.
    assert abs(card["pos"]["x"] - 150) <= 20 and abs(card["pos"]["y"] - 110) <= 30


async def test_a_new_card_needs_a_name_and_room(ctx, notebook: Path) -> None:
    _seed(notebook)
    with pytest.raises(CommandError):
        await dispatch_command(ctx, "board.card.add", {"wiki": "piante", "title": "  "})

    def fill(board):
        for i in range(model.MAX_ITEMS - 2):
            board["cards"].append({"id": f"c{i}", "title": f"C{i}", "pos": {"x": i, "y": 300}})

    mutate_board(notebook, fill)
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "board.card.add", {"wiki": "piante", "title": "Una in più"})
    assert err.value.code == "conflict"


async def test_two_cards_with_the_same_name_get_two_ids(ctx, notebook: Path) -> None:
    _seed(notebook)
    a = await dispatch_command(ctx, "board.card.add", {"wiki": "piante", "title": "Acero"})
    assert a["card"]["id"] == "acero-2"


async def test_rename_makes_the_title_the_users(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.card.rename", {"wiki": "piante", "id": "acero",
                                                      "title": "Acerello"})
    card = model.card_by_id(read_board(notebook), "acero")
    assert (card["title"], card["titleBy"]) == ("Acerello", "user")
    # E da li' Jenny non lo riscrive.
    with pytest.raises(BoardOwnershipError):
        mutate_board(notebook, lambda b: b["cards"][0].update(title="Acero"), actor=model.JENNY)


async def test_remove_and_undo_put_back_place_threads_and_pages(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.card.remove", {"wiki": "piante", "id": "acero"})
    board = read_board(notebook)
    assert [c["id"] for c in board["cards"]] == ["monstera"]
    assert board["connections"] == []
    assert model.removed_card_ids(board) == {"acero"}
    assert [c["id"] for c in build_board_view(notebook)["cards"]] == ["monstera"]
    # Un'altra scheda non puo' prendere il suo id mentre l'Annulla vale.
    out = await dispatch_command(ctx, "board.card.add", {"wiki": "piante", "title": "Acero"})
    assert out["card"]["id"] == "acero-2"

    await dispatch_command(ctx, "board.card.restore", {"wiki": "piante", "id": "acero"})
    board = read_board(notebook)
    acero = model.card_by_id(board, "acero")
    assert acero["pos"] == {"x": 5.0, "y": 6.0, "by": "jenny"}
    assert acero["pages"] == ["travaso-acero.md"]
    assert board["connections"][0]["label"] == "vasi"
    assert board["removedCards"] == []
    with pytest.raises(CommandError):
        await dispatch_command(ctx, "board.card.restore", {"wiki": "piante", "id": "acero"})


async def test_jenny_cannot_touch_the_removed_cards(ctx, notebook: Path) -> None:
    _seed(notebook)
    await dispatch_command(ctx, "board.card.remove", {"wiki": "piante", "id": "acero"})
    with pytest.raises(BoardOwnershipError):
        mutate_board(notebook, lambda b: b.update(removedCards=[]), actor=model.JENNY)


async def test_a_removed_card_pauses_its_crons_and_undo_resumes_them(
    ctx, notebook: Path, tmp_path: Path,
) -> None:
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    _seed(notebook)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        name="Meteo", schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="guarda il meteo", session_key="project:piante", origin_channel="websocket",
        origin_chat_id="project:piante",
        board={"notebook": "piante", "card": "acero", "section": "s-cron"},
    )
    already = service.add_job(
        name="Spento", schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="x", session_key="project:piante", origin_channel="websocket",
        origin_chat_id="project:piante",
        board={"notebook": "piante", "card": "acero", "section": "s-cron2"},
    )
    service.set_paused(already.id, True)
    mutate_board(notebook, lambda b: b["cards"][0]["userSections"].extend([
        {"id": "s-cron", "kind": "cron", "job": job.id},
        {"id": "s-cron2", "kind": "cron", "job": already.id},
    ]))
    with_cron = dataclasses.replace(ctx, get_cron_service=lambda: service)

    await dispatch_command(with_cron, "board.card.remove", {"wiki": "piante", "id": "acero"})
    assert service.get_job(job.id).paused_at_ms is not None
    await dispatch_command(with_cron, "board.card.restore", {"wiki": "piante", "id": "acero"})
    assert service.get_job(job.id).paused_at_ms is None
    # Quello che l'utente aveva gia' fermato resta fermo.
    assert service.get_job(already.id).paused_at_ms is not None


async def test_a_week_later_the_removed_card_is_gone_for_good(
    ctx, notebook: Path, tmp_path: Path,
) -> None:
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    _seed(notebook)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        name="Meteo", schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="guarda il meteo", session_key="project:piante", origin_channel="websocket",
        origin_chat_id="project:piante",
        board={"notebook": "piante", "card": "acero", "section": "s-cron"},
    )
    mutate_board(notebook, lambda b: b["cards"][0]["userSections"].append(
        {"id": "s-cron", "kind": "cron", "job": job.id}))
    from jenny.board.cron_link import add_notebook_job
    from jenny.cron.dates import date_schedule

    reminder = add_notebook_job(service, "piante", "acero", name="Travaso", message="Travaso",
                                schedule=date_schedule("2099-03", "Europe/Rome"))
    write_card_summary(notebook, "acero", {"brief": "In vaso 14."})
    await dispatch_command(ctx, "board.card.remove", {"wiki": "piante", "id": "acero"})

    later = datetime.now().astimezone() + timedelta(days=model.REMOVED_KEEP_DAYS - 1)
    assert purge_removed_cards(notebook, service, now=later) == 0
    later = datetime.now().astimezone() + timedelta(days=model.REMOVED_KEEP_DAYS + 1)
    assert purge_removed_cards(notebook, service, now=later) == 1
    assert read_board(notebook)["removedCards"] == []
    assert service.get_job(job.id) is None
    # Un promemoria è un job della scheda: se ne va con lei.
    assert service.get_job(reminder.id) is None
    assert not (notebook / "board" / "cards" / "acero.json").exists()


async def test_the_reminders_of_a_removed_card_pause_and_come_back(
    cron_ctx, cron, notebook: Path,
) -> None:
    """«Togli» ferma quel che la scheda fa da sé: anche i suoi promemoria, che sono job."""
    from jenny.board.cron_link import add_notebook_job, notebook_jobs
    from jenny.cron.dates import date_schedule

    _seed(notebook)
    job = add_notebook_job(cron, "piante", "acero", name="Travaso", message="Travaso",
                           schedule=date_schedule("2099-10-06", "Europe/Rome"))
    await dispatch_command(cron_ctx, "board.card.remove", {"wiki": "piante", "id": "acero"})
    assert cron.get_job(job.id).paused_at_ms is not None
    view = build_board_view(notebook, cron_jobs=notebook_jobs(cron, "piante"))
    assert view["notes"] == [] and view["postits"] == []
    await dispatch_command(cron_ctx, "board.card.restore", {"wiki": "piante", "id": "acero"})
    assert cron.get_job(job.id).paused_at_ms is None

