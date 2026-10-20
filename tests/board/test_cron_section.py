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


def _meteo(service, notebook, link):
    from jenny.cron.types import CronSchedule

    mutate_board(notebook, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero", "pages": [], "pos": {"x": 1, "y": 1}}))
    return service.add_job(
        name="Meteo", schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="guarda il meteo", session_key="project:piante", origin_channel="websocket",
        origin_chat_id="project:piante", board=link,
    )


async def test_a_card_cron_is_shown_from_its_job_and_goes_with_it(ctx, workspace_root, notebook,
                                                                  tmp_path) -> None:
    """Il cassetto e il ⟳ vengono dal job, senza una sezione da tenere allineata."""
    import dataclasses

    from jenny.board.cron_link import job_snapshot, make_link
    from jenny.cron.service import CronService

    service = CronService(tmp_path / "jobs.json")
    job = _meteo(service, notebook, make_link("piante", "acero"))
    jobs = {job.id: job_snapshot(job)}
    card = build_card_view(notebook, "acero", sections_resolver=section_resolver(workspace_root,
                                                                               notebook),
                           cron_jobs=jobs)
    shown = card["automations"][0]
    assert shown["name"] == "Meteo" and shown["schedule"]["every_ms"] == 3_600_000
    assert "legacySection" not in shown and card["userSections"] == []
    assert build_board_view(notebook, cron_jobs=jobs)["cards"][0]["auto"] is True

    with_cron = dataclasses.replace(ctx, get_cron_service=lambda: service)
    await dispatch_command(with_cron, "board.cron.pause", {"wiki": "piante", "job": job.id,
                                                           "paused": True})
    assert service.get_job(job.id).paused_at_ms is not None
    await dispatch_command(with_cron, "board.cron.remove", {"wiki": "piante", "job": job.id})
    assert service.get_job(job.id) is None


async def test_board_cron_remove_refuses_a_job_of_elsewhere(ctx, notebook, tmp_path) -> None:
    import dataclasses

    from jenny.board.cron_link import make_link
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    service = CronService(tmp_path / "jobs.json")
    other = _meteo(service, notebook, make_link("altrove", "acero"))
    personal = service.add_job("sveglia", CronSchedule(kind="every", every_ms=60_000), "m",
                               session_key="unified:default")
    with_cron = dataclasses.replace(ctx, get_cron_service=lambda: service)
    for job in (other, personal):
        with pytest.raises(CommandError):
            await dispatch_command(with_cron, "board.cron.remove", {"wiki": "piante",
                                                                    "job": job.id})
        assert service.get_job(job.id) is not None


async def test_a_cron_section_from_before_still_shows_once_and_goes_with_it(
        ctx, workspace_root, notebook, tmp_path) -> None:
    """Un cron scritto prima del 20/10/2026 ha ancora la sua sezione: si vede una volta sola."""
    import dataclasses

    from jenny.board.cron_link import job_snapshot
    from jenny.cron.service import CronService

    service = CronService(tmp_path / "jobs.json")
    job = _meteo(service, notebook, {"notebook": "piante", "card": "acero", "section": "s-cron"})
    mutate_board(notebook, lambda b: (
        b["cards"][0]["userSections"].append({"id": "s-cron", "kind": "cron", "job": job.id}),
        b.__setitem__("cronResults", {"acero/s-cron": {"at": "2026-10-01T09:00:00+02:00",
                                                      "status": "ok", "text": "sole"}})))
    card = build_card_view(notebook, "acero", sections_resolver=section_resolver(workspace_root,
                                                                               notebook),
                           cron_jobs={job.id: job_snapshot(job)})
    assert card["userSections"] == []
    assert [a["id"] for a in card["automations"]] == [job.id]
    assert card["automations"][0]["result"]["text"] == "sole"

    with_cron = dataclasses.replace(ctx, get_cron_service=lambda: service)
    await dispatch_command(with_cron, "board.section.remove", {"wiki": "piante", "card": "acero",
                                                               "id": "s-cron"})
    assert service.get_job(job.id) is None
