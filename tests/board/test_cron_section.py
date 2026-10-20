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


def test_a_cards_recurring_crons_are_one_label_and_a_dated_one_is_not(notebook, tmp_path) -> None:
    """Un'etichetta per scheda coi suoi cron che si ripetono; un «una volta sola» è una data."""
    from jenny.board.cron_link import job_snapshot, make_link
    from jenny.board.store import mutate_reminders
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    service = CronService(tmp_path / "jobs.json")
    first = _meteo(service, notebook, make_link("piante", "acero"))
    origin = {"session_key": "project:piante", "origin_channel": "websocket",
              "origin_chat_id": "project:piante"}
    second = service.add_job("Gelo", CronSchedule(kind="cron", expr="0 7 * * *", tz="Europe/Rome"),
                             "guarda se gela", board=make_link("piante", "acero"), **origin)
    once = service.add_job("Vivaio", CronSchedule(kind="at", at_ms=4_102_444_800_000), "chiama",
                           board=make_link("piante", "acero"), **origin)
    mutate_reminders(notebook, lambda r: r["reminders"].append({
        "id": "r-1", "card": "acero", "text": "Rinvaso", "state": "open", "by": "user",
        "when": {"kind": "date", "date": "2099-03-01", "precision": "day"}}))
    before = build_board_view(notebook)
    jobs = {j.id: job_snapshot(j) for j in (first, second, once)}
    view = build_board_view(notebook, cron_jobs=jobs)
    [label] = view["autoLabels"]
    assert label["card"] == "acero" and label["pos"]["by"] == "jenny"
    assert {r["id"] for r in label["rows"]} == {first.id, second.id}
    assert label["rows"][0]["schedule"].get("shape") or label["rows"][1]["schedule"].get("shape")
    # L'etichetta arriva dopo: il post-it resta dov'era.
    assert view["postits"] == before["postits"]
    # Il cron «una volta sola» è una riga del post-it della scheda, con la sua ora.
    dated = [n for n in view["notes"] if n.get("job") == once.id]
    assert dated and dated[0]["card"] == "acero" and dated[0]["when"]["time"]
    # Una scheda che non è più sulla bacheca non ha etichetta.
    mutate_board(notebook, lambda b: b.__setitem__("cards", []))
    assert build_board_view(notebook, cron_jobs=jobs)["autoLabels"] == []


def test_the_whole_notebook_label_is_free_even_without_cards(notebook, tmp_path) -> None:
    from jenny.board import model
    from jenny.board.cron_link import job_snapshot, make_link
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    mutate_board(notebook, lambda b: b.__setitem__("cards", []))
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job("Gelo", CronSchedule(kind="every", every_ms=3_600_000), "guarda",
                          session_key="project:piante", origin_channel="websocket",
                          origin_chat_id="project:piante",
                          board=make_link("piante", model.NOTEBOOK))
    jobs = {job.id: job_snapshot(job)}
    [label] = build_board_view(notebook, cron_jobs=jobs)["autoLabels"]
    assert label["card"] == "" and [r["id"] for r in label["rows"]] == [job.id]
    mutate_board(notebook, lambda b: b.__setitem__("notebookAutoPos", {"x": 40, "y": 60}))
    [label] = build_board_view(notebook, cron_jobs=jobs)["autoLabels"]
    assert label["pos"] == {"x": 40.0, "y": 60.0, "by": "user"}


def test_a_whole_notebook_once_cron_is_a_row_of_the_free_postit(notebook, tmp_path) -> None:
    """«Ricordami domani alle 9» detto nel quaderno: una data sul post-it libero, non un'etichetta."""
    from jenny.board import model
    from jenny.board.cron_link import job_snapshot, make_link
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    mutate_board(notebook, lambda b: None)
    service = CronService(tmp_path / "jobs.json")
    once = service.add_job("Chiama il vivaio", CronSchedule(kind="at", at_ms=4_102_444_800_000),
                           "chiama", session_key="project:piante", origin_channel="websocket",
                           origin_chat_id="project:piante",
                           board=make_link("piante", model.NOTEBOOK))
    view = build_board_view(notebook, cron_jobs={once.id: job_snapshot(once)})
    assert view["autoLabels"] == []
    [row] = [n for n in view["notes"] if n.get("job") == once.id]
    assert row["card"] == "" and row["text"] == "Chiama il vivaio"
    assert view["postits"][-1]["card"] == ""


def test_a_label_that_does_not_fit_hangs_beside_its_card(notebook, tmp_path) -> None:
    """Fra i due post-it non c'è un varco: l'etichetta va accanto alla sua scheda.

    Il caso visto sul telefono il 20/10/2026: misurata da un punto sotto la scheda,
    finiva in fondo al muro col filo che lo attraversava. Né più in alto della
    scheda: il filo arriva al chiodino in cima e le passerebbe sopra le righe.
    """
    from jenny.board import model
    from jenny.board.cron_link import job_snapshot, make_link
    from jenny.board.store import mutate_reminders
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    mutate_board(notebook, lambda b: b.__setitem__("cards", [
        {"id": "salvia", "title": "Salvia", "pages": [], "pos": {"x": 10, "y": 20}},
        {"id": "fico", "title": "Fico", "pages": [], "pos": {"x": 55, "y": 20}}]))
    mutate_reminders(notebook, lambda r: r["reminders"].extend(
        {"id": f"r-{card}", "card": card, "text": "Concime", "state": "open", "by": "user",
         "when": {"kind": "date", "date": "2099-03-01", "precision": "day"}}
        for card in ("salvia", "fico")))
    service = CronService(tmp_path / "jobs.json")
    origin = {"session_key": "project:piante", "origin_channel": "websocket",
              "origin_chat_id": "project:piante"}
    jobs = {}
    for card in ("salvia", "fico"):
        job = service.add_job(f"Acqua {card}", CronSchedule(kind="every", every_ms=86_400_000),
                              "annaffia", board=make_link("piante", card), **origin)
        jobs[job.id] = job_snapshot(job)
    before = build_board_view(notebook)
    view = build_board_view(notebook, cron_jobs=jobs)
    assert view["postits"] == before["postits"]
    postits = [(p["pos"]["x"], p["pos"]["y"], p["pos"]["x"] + model.POSTIT_BOX[0],
                p["pos"]["y"] + model.POSTIT_BOX[1]) for p in view["postits"]]
    [label] = [lab for lab in view["autoLabels"] if lab["card"] == "fico"]
    w, h = model.auto_box(len(label["rows"]))
    x, y = label["pos"]["x"], label["pos"]["y"]
    rect = (x, y, x + w, y + h)
    assert y >= 20, "never above its card"
    assert model._gap(rect, (55, 20, 55 + model.CARD_BOX[0], 20 + model.CARD_BOX[1])) <= 4 ** 2
    assert not any(model._overlap(rect, p) for p in postits)


def test_a_labels_box_grows_with_its_rows_up_to_the_shown_ones() -> None:
    from jenny.board import model

    one, two, three, many = (model.auto_box(n) for n in (1, 2, 3, 9))
    assert one[1] < two[1] < three[1] < many[1]
    assert model.auto_box(0) == one and model.auto_box(4) == many
    assert two == model.AUTO_BOX, "a label placed by the user is sized as two crons"
