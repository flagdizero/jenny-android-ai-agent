"""Le sezioni del cassetto che fanno da sé: il cron, e la micro app che non nasce più."""

from __future__ import annotations

import pytest

from jenny.board.sections import section_resolver
from jenny.board.store import mutate_board
from jenny.board.view import build_board_view, build_card_view
from jenny.webui.commands import CommandError, dispatch_command


async def test_a_micro_section_cannot_be_added_by_hand(ctx, notebook) -> None:
    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "acero", "title": "Acero", "pages": [], "pos": {"x": 1, "y": 1}}))
    with pytest.raises(CommandError):
        await dispatch_command(ctx, "board.section.add", {"wiki": "piante", "card": "acero",
                                                          "kind": "micro", "micro": "m-x"})


async def test_a_cron_section_shows_its_job_and_goes_with_it(ctx, workspace_root, notebook,
                                                            tmp_path) -> None:
    import dataclasses

    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    service = CronService(tmp_path / "jobs.json")
    mutate_board(notebook, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero", "pages": [], "pos": {"x": 1, "y": 1}}))
    job = service.add_job(
        name="Meteo", schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="guarda il meteo", session_key="project:piante", origin_channel="websocket",
        origin_chat_id="project:piante",
        board={"notebook": "piante", "card": "acero", "section": "s-cron"},
    )
    mutate_board(notebook, lambda b: b["cards"][0]["userSections"].append(
        {"id": "s-cron", "kind": "cron", "job": job.id}))
    from jenny.board.cron_link import job_snapshot

    card = build_card_view(notebook, "acero", sections_resolver=section_resolver(workspace_root,
                                                                               notebook),
                           cron_jobs={job.id: job_snapshot(job)})
    shown = card["userSections"][0]
    assert shown["job"]["name"] == "Meteo" and shown["job"]["schedule"]["every_ms"] == 3_600_000
    assert build_board_view(notebook)["cards"][0]["auto"] is True

    with_cron = dataclasses.replace(ctx, get_cron_service=lambda: service)
    await dispatch_command(with_cron, "board.cron.pause", {"wiki": "piante", "job": job.id,
                                                           "paused": True})
    assert service.get_job(job.id).paused_at_ms is not None
    await dispatch_command(with_cron, "board.section.remove", {"wiki": "piante", "card": "acero",
                                                               "id": "s-cron"})
    assert service.get_job(job.id) is None
