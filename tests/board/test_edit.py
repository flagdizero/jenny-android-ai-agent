"""Le scritture dell'utente sulla bacheca (``jenny/board/edit.py``).

Le fanno l'interfaccia e l'editore dei quaderni, e valgono come dell'utente:
il giardiniere, che scrive come ``jenny``, non può disfarle. Il caso da cui è
nato tutto è del 09/10/2026: una scheda che raccoglieva tre piante, da dividere
in una scheda per pianta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenny.board import edit, model
from jenny.board.store import (
    BoardOwnershipError,
    mutate_board,
    read_board,
    read_card_summary,
    write_card_summary,
)


def _seed(notebook: Path) -> None:
    def apply(board):
        board["skeleton"] = ["Vaso", "Acqua"]
        board["cards"].append({
            "id": "nuove", "title": "Nuove piante", "pages": [
                "ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md", "dormienza.md",
            ], "pos": {"x": 5, "y": 6, "by": "jenny"},
        })
        board["cards"].append({
            "id": "acero", "title": "Acero", "pages": ["travaso-acero.md"],
            "pos": {"x": 40, "y": 6, "by": "jenny"},
            "userSections": [{"id": "s-cron", "kind": "cron", "job": "j1", "name": "meteo"}],
        })

    mutate_board(notebook, apply)
    write_card_summary(notebook, "nuove", {"generated": "2026-10-08T10:00:00+02:00",
                                           "brief": "Tre piante.", "sections": []})


class _Cron:
    """Come il servizio vero, vuole il loop: riarma il suo timer lì, e da un
    thread solleva. Il 09/10/2026 sull'emulatore una «Togli» lo chiamava da
    ``asyncio.to_thread``, e falliva dopo aver già messo in pausa il cron."""

    def __init__(self) -> None:
        self.paused: dict[str, bool] = {}

    def set_paused(self, job_id: str, paused: bool) -> str:
        asyncio.get_running_loop()
        self.paused[job_id] = paused
        return "paused" if paused else "resumed"

    def remove_job(self, job_id: str) -> str:
        asyncio.get_running_loop()
        self.paused.pop(job_id, None)
        return "removed"

    def get_job(self, job_id: str) -> object:
        return object()

    def list_jobs(self, include_disabled: bool = False) -> list:
        """Il cron del cassetto dell'Acero: chi lo ferma lo trova dal job, non dalla sezione."""
        from types import SimpleNamespace

        link = {"notebook": "piante", "card": "acero", "section": "s-cron"}
        return [SimpleNamespace(id="j1", payload=SimpleNamespace(board=link))]


def test_a_card_born_with_pages_takes_them_from_the_card_that_had_them(notebook: Path) -> None:
    _seed(notebook)
    card, redraw = edit.add_card(notebook, "Ginkgo", pages=["ginkgo.md", "ginkgo-talea.md"])
    board = read_board(notebook)
    assert card["titleBy"] == "user" and card["pos"]["by"] == "user"
    assert model.card_by_id(board, "ginkgo")["pages"] == ["ginkgo.md", "ginkgo-talea.md"]
    assert model.card_by_id(board, "nuove")["pages"] == ["ginkgo-vaso.md", "dormienza.md"]
    assert redraw == ["ginkgo", "nuove"]


def test_a_drawer_left_without_pages_is_deleted(notebook: Path) -> None:
    _seed(notebook)
    redraw = edit.move_pages(notebook, ["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md",
                                        "dormienza.md"], "acero")
    assert redraw == ["acero"], "la scheda rimasta vuota non ha un cassetto da riscrivere"
    assert read_card_summary(notebook, "nuove") is None
    assert model.card_by_id(read_board(notebook), "nuove")["pages"] == []


def test_pages_come_from_off_the_board_too(notebook: Path) -> None:
    _seed(notebook)
    assert edit.move_pages(notebook, ["irrigazione-acero.md"], "Acero") == ["acero"]
    assert model.card_by_id(read_board(notebook), "acero")["pages"] == [
        "travaso-acero.md", "irrigazione-acero.md"]


def test_a_page_that_does_not_exist_is_refused(notebook: Path) -> None:
    _seed(notebook)
    with pytest.raises(edit.BoardEditError) as err:
        edit.move_pages(notebook, ["Ginkgo"], "acero")
    assert err.value.code == "not_found" and "relative to wiki/" in err.value.message


def _seal(notebook: Path, card_id: str) -> None:
    """Il cassetto scritto adesso, con l'impronta delle pagine com'è sul disco."""
    from jenny.board.drawers import page_text, pages_fingerprint
    from jenny.board.view import wiki_titles

    card = model.card_by_id(read_board(notebook), card_id)
    now = pages_fingerprint(card["pages"], wiki_titles(notebook),
                            lambda p: page_text(notebook, p))
    write_card_summary(notebook, card_id, {"generated": "2026-10-08T10:00:00+02:00",
                                           "brief": "x", "sections": [], "fingerprint": now})


def test_a_reference_to_a_deleted_page_comes_off_every_card(notebook: Path) -> None:
    """Il caso della prova di ottobre: un riordino cancella pagine e la bacheca
    resta coi riferimenti morti. Togliere un riferimento morto non rende vecchio
    il cassetto: l'impronta conta solo le pagine vere."""
    _seed(notebook)
    (notebook / "wiki" / "dormienza.md").unlink()
    _seal(notebook, "nuove")
    taken, redraw = edit.remove_pages(notebook, ["dormienza.md"])
    assert taken == {"nuove": ["dormienza.md"]} and redraw == []
    assert "dormienza.md" not in model.card_by_id(read_board(notebook), "nuove")["pages"]
    assert read_card_summary(notebook, "nuove") is not None


def test_redraw_follows_the_stored_fingerprint(notebook: Path) -> None:
    """Da riscrivere è il cassetto la cui impronta salvata non torna più — non
    «una scheda che ha perso una pagina vera»: quello vecchio già prima lo è
    anche se si toglie solo un riferimento morto."""
    _seed(notebook)
    (notebook / "wiki" / "dormienza.md").unlink()
    # Mai sigillato: l'impronta salvata non c'è, il cassetto va scritto.
    assert edit.remove_pages(notebook, ["dormienza.md"])[1] == ["nuove"]
    _seal(notebook, "nuove")
    assert edit.remove_pages(notebook, ["ginkgo.md"])[1] == ["nuove"]


def test_a_page_taken_off_is_not_put_back_by_the_gardener(notebook: Path) -> None:
    """Una pagina vera tolta da ogni scheda è un orfano; il giardiniere assegna gli
    orfani *nuovi* alla scheda a cui appartengono chiaramente — quella da cui
    l'utente l'ha appena tolta. Per lui deve essere un orfano già visto."""
    from jenny.agent.board_pass import BoardPass

    _seed(notebook)
    (notebook / "wiki" / "dormienza.md").unlink()
    edit.remove_pages(notebook, ["ginkgo.md", "dormienza.md"], "nuove")
    board = read_board(notebook)
    assert board["orphansSeen"] == ["ginkgo.md"], "un riferimento morto non è un orfano"
    assert "ginkgo.md" in BoardPass(notebook, notebook.parents[1]).orphans()
    assert "ginkgo.md" not in BoardPass(notebook, notebook.parents[1]).new_orphans()


def test_removing_a_real_page_redraws_or_empties_the_drawer(notebook: Path) -> None:
    _seed(notebook)
    taken, redraw = edit.remove_pages(notebook, ["ginkgo.md"], "Nuove piante")
    assert taken == {"nuove": ["ginkgo.md"]} and redraw == ["nuove"]
    assert (notebook / "wiki" / "ginkgo.md").exists(), "la pagina resta nella wiki"
    taken, redraw = edit.remove_pages(notebook, ["travaso-acero.md"], "acero")
    assert redraw == [], "un cassetto rimasto senza pagine si cancella, non si riscrive"
    assert model.card_by_id(read_board(notebook), "acero")["pages"] == []


def test_remove_pages_on_one_card_leaves_the_others(notebook: Path) -> None:
    _seed(notebook)
    before = (notebook / "board" / "board.json").read_bytes()
    taken, _ = edit.remove_pages(notebook, ["ginkgo.md"], "acero")
    assert taken == {}, "l'Acero non l'aveva: niente da togliere"
    assert (notebook / "board" / "board.json").read_bytes() == before, "e niente da scrivere"
    with pytest.raises(edit.BoardEditError) as err:
        edit.remove_pages(notebook, [], "acero")
    assert err.value.code == "bad_request"


def test_what_the_editor_does_the_gardener_cannot_undo(notebook: Path) -> None:
    """Il punto della separazione: le scritture su richiesta sono dell'utente."""
    _seed(notebook)
    edit.add_card(notebook, "Ginkgo", pages=["ginkgo.md"])

    def drop_card(board):
        board["cards"] = [c for c in board["cards"] if c["id"] != "ginkgo"]

    def take_page_back(board):
        model.card_by_id(board, "ginkgo")["pages"] = []

    for undo in (drop_card, take_page_back):
        with pytest.raises(BoardOwnershipError):
            mutate_board(notebook, undo, actor=model.JENNY)


async def test_a_removed_card_goes_to_the_trash_and_its_cron_pauses(notebook: Path) -> None:
    _seed(notebook)
    cron = _Cron()
    await edit.remove_card(notebook, "Acero", cron=cron)
    board = read_board(notebook)
    assert model.card_by_id(board, "acero") is None
    assert board["removedCards"][0]["paused"]["jobs"] == ["j1"]
    assert cron.paused == {"j1": True}
    await edit.restore_card(notebook, "acero", cron=cron)
    assert model.card_by_id(read_board(notebook), "acero") is not None
    assert cron.paused == {"j1": False}


async def test_an_accepted_removal_is_a_trash_with_undo_too(notebook: Path) -> None:
    """Fino al 09/10/2026 una proposta accettata cancellava la scheda per sempre
    e lasciava girare i cron del suo cassetto."""
    _seed(notebook)
    mutate_board(notebook, lambda b: b["suggestions"].append({
        "id": "sg-acero", "kind": "remove_card", "subject": "Acero", "card": "acero",
        "text": "Ferma da tempo."}), actor=model.JENNY)
    cron = _Cron()
    await edit.respond_suggestion(notebook, "sg-acero", "yes", cron=cron)
    board = read_board(notebook)
    assert [r["card"]["id"] for r in board["removedCards"]] == ["acero"]
    assert board["since"][-1]["kind"] == "card_removed"
    assert cron.paused == {"j1": True}
    await edit.restore_card(notebook, "acero", cron=cron)
    assert model.card_by_id(read_board(notebook), "acero") is not None


def test_a_users_thread_and_its_removal(notebook: Path) -> None:
    _seed(notebook)
    edit.connect(notebook, "acero", "Nuove piante", "stesso balcone")
    board = read_board(notebook)
    assert board["connections"] == [
        {"a": "acero", "b": "nuove", "label": "stesso balcone", "by": "user"}]
    with pytest.raises(edit.BoardEditError):
        edit.connect(notebook, "nuove", "acero")
    assert edit.disconnect(notebook, "Acero", "nuove") is True
    board = read_board(notebook)
    assert board["connections"] == []
    assert {d["subject"] for d in board["declined"]} == {
        "Acero — Nuove piante", "Nuove piante — Acero"}


def test_a_card_next_to_another(notebook: Path) -> None:
    _seed(notebook)
    edit.move_card(notebook, "nuove", "acero")
    board = read_board(notebook)
    moved = model.card_by_id(board, "nuove")["pos"]
    other = model.card_by_id(board, "acero")["pos"]
    assert moved["by"] == "user"
    assert abs(moved["x"] - other["x"]) <= model.NEAR_SPAN_X


async def test_a_reminder_is_a_notebook_job_and_done_takes_it_away(notebook: Path, cron) -> None:
    """Un promemoria è un singolo dello scheduler, legato al quaderno e alla scheda."""
    from jenny.board.cron_link import scope_of

    _seed(notebook)
    rid = await edit.add_reminder(notebook, "acero", "Travaso",
                                  {"kind": "date", "date": "2027-03-01", "precision": "month"},
                                  cron=cron, timezone_name="Europe/Rome")
    job = cron.get_job(rid)
    assert job.name == "Travaso" and job.payload.message == "Travaso"
    assert job.schedule.kind == "at" and job.schedule.precision == "month"
    assert job.payload.board["notebook"] == "piante" and scope_of(job.payload.board) == "acero"
    assert job.payload.session_key == "project:piante"
    # Niente sezione: il calendario di cura e il post-it la derivano dal job.
    sections = model.card_by_id(read_board(notebook), "acero")["userSections"]
    assert not any(s.get("kind") == "reminder" for s in sections)
    assert not (notebook / "board" / "reminders.json").exists()
    whole = await edit.add_reminder(notebook, "", "Porta dentro le piante",
                                    {"kind": "date", "date": "2026-12-01", "precision": "day"},
                                    cron=cron)
    assert scope_of(cron.get_job(whole).payload.board) == model.NOTEBOOK
    assert await edit.close_reminder(notebook, rid, cron=cron) == "Travaso"
    assert cron.get_job(rid) is None
    journal = "".join(p.read_text() for p in (notebook / "raw" / "journal").glob("*.md"))
    assert "Done: Travaso" in journal
    await edit.drop_reminder(notebook, whole, cron=cron)
    assert cron.get_job(whole) is None


async def test_a_condition_reminder_repeats_at_the_users_pace(notebook: Path, cron) -> None:
    _seed(notebook)
    when = {"kind": "condition", "app": "waterbot", "params": {"sensor": "a"},
            "field": "moisture", "op": "<", "value": 20}
    with pytest.raises(edit.BoardEditError) as err:
        await edit.add_reminder(notebook, "acero", "Annaffia", when, cron=cron)
    assert "every_ms" in err.value.message
    with pytest.raises(edit.BoardEditError):
        await edit.add_reminder(notebook, "acero", "Annaffia", when, cron=cron, every_ms=60_000)
    rid = await edit.add_reminder(notebook, "acero", "Annaffia", when, cron=cron,
                                  every_ms=6 * 3_600_000)
    job = cron.get_job(rid)
    assert job.schedule.kind == "every" and job.schedule.every_ms == 6 * 3_600_000
    assert job.payload.condition == {"app": "waterbot", "params": {"sensor": "a"},
                                     "field": "moisture", "op": "<", "value": 20}
    await edit.update_reminder(notebook, rid, cron=cron, every_ms=3_600_000)
    assert cron.get_job(rid).schedule.every_ms == 3_600_000
    assert cron.get_job(rid).payload.condition["value"] == 20


async def test_the_same_date_keeps_an_exact_time(notebook: Path, cron) -> None:
    """Correggere solo il testo dal calendario rimanda la stessa data: l'ora resta."""
    from jenny.board.cron_link import add_notebook_job
    from jenny.cron.dates import date_schedule

    _seed(notebook)
    job = add_notebook_job(cron, "piante", "acero", name="Concime", message="Concime",
                           schedule=date_schedule("2027-03-10T18:30", "Europe/Rome"))
    at = job.schedule.at_ms
    await edit.update_reminder(notebook, job.id, cron=cron, text="Concime liquido",
                               when={"kind": "date", "date": "2027-03-10", "precision": "day"},
                               timezone_name="Europe/Rome")
    assert cron.get_job(job.id).schedule.at_ms == at
    assert cron.get_job(job.id).name == "Concime liquido"
    await edit.update_reminder(notebook, job.id, cron=cron,
                               when={"kind": "date", "date": "2027-03-11", "precision": "day"},
                               timezone_name="Europe/Rome")
    moved = cron.get_job(job.id).schedule
    assert moved.at_ms != at and moved.precision == "day"


async def test_a_cron_that_repeats_is_not_a_reminder(notebook: Path, cron) -> None:
    from jenny.board.cron_link import add_notebook_job
    from jenny.cron.types import CronSchedule

    _seed(notebook)
    job = add_notebook_job(cron, "piante", "acero", name="Meteo", message="m",
                           schedule=CronSchedule(kind="every", every_ms=3_600_000))
    with pytest.raises(edit.BoardEditError) as err:
        await edit.drop_reminder(notebook, job.id, cron=cron)
    assert err.value.code == "bad_request"
    assert "recurring job, not a reminder" in err.value.message
    assert cron.get_job(job.id) is not None


def test_an_unknown_card_names_the_ones_there_are(notebook: Path) -> None:
    _seed(notebook)
    with pytest.raises(edit.BoardEditError) as err:
        edit.rename_card(notebook, "Fico", "Fico bianco")
    assert "«Acero» (acero)" in err.value.message


def test_cards_asked_one_next_to_the_other_go_right_not_off_the_screen(notebook: Path) -> None:
    """Sull'emulatore il 09/10/2026 tre schede «accanto» finivano a x = 2, −18, −38."""
    _seed(notebook)
    first, _ = edit.add_card(notebook, "Ginkgo")
    second, _ = edit.add_card(notebook, "Fico", near_card="ginkgo")
    third, _ = edit.add_card(notebook, "Prunus", near_card="fico")
    assert first["pos"]["x"] < second["pos"]["x"] < third["pos"]["x"]
    assert second["pos"]["x"] >= 0


async def test_removing_a_cron_section_removes_its_job_on_the_loop(notebook: Path) -> None:
    _seed(notebook)
    cron = _Cron()
    cron.paused["j1"] = False
    assert await edit.remove_section(notebook, "acero", "s-cron", cron=cron) is True
    assert "j1" not in cron.paused
    assert model.card_by_id(read_board(notebook), "acero")["userSections"] == []


# ── Il «perché» di un promemoria (contratto del 24/10/2026) ─────────────────


async def _reminder(notebook: Path, cron, why: str | None = None) -> str:
    return await edit.add_reminder(notebook, "acero", "Rinvaso",
                                   {"kind": "date", "date": "2099-03-01", "precision": "day"},
                                   cron=cron, why=why)


async def test_why_points_to_a_real_page_or_to_nothing(notebook: Path, cron) -> None:
    _seed(notebook)
    rid = await _reminder(notebook, cron)
    done = await edit.update_reminder(notebook, rid, cron=cron, why="wiki/travaso-acero.md")
    assert done["why"] == "travaso-acero.md"
    assert cron.get_job(rid).payload.why == "travaso-acero.md"
    with pytest.raises(edit.BoardEditError) as err:
        await edit.update_reminder(notebook, rid, cron=cron, why="sparita.md")
    assert err.value.code == "not_found"
    assert cron.get_job(rid).payload.why == "travaso-acero.md"
    assert (await edit.update_reminder(notebook, rid, cron=cron, clear_why=True))["why"] == ""
    assert cron.get_job(rid).payload.why is None
    with pytest.raises(edit.BoardEditError):
        await edit.update_reminder(notebook, "nessuno", cron=cron, text="x")
    with pytest.raises(edit.BoardEditError):
        await _reminder(notebook, cron, why="sparita.md")


async def test_clear_job_whys_after_removed_pages(notebook: Path, cron) -> None:
    from jenny.board.cron_link import clear_job_whys

    _seed(notebook)
    rid = await _reminder(notebook, cron, why="dormienza.md")
    touched = await clear_job_whys(cron, "piante", ["dormienza.md", "altra.md"])
    assert touched == [{"id": rid, "text": "Rinvaso", "why": "dormienza.md"}]
    assert cron.get_job(rid).payload.why is None
    assert await clear_job_whys(cron, "piante", ["dormienza.md"]) == []


async def test_attach_reminder_moves_it_to_another_card(notebook: Path, cron) -> None:
    """Un promemoria sta su una scheda sola: attaccarlo altrove lo sposta (con un
    id nuovo: lo scheduler non cambia il legame di un job), rifarlo non scrive."""
    from jenny.board.cron_link import scope_of

    _seed(notebook)
    rid = await _reminder(notebook, cron, why="dormienza.md")
    done = await edit.attach_reminder(notebook, "Nuove piante", rid, cron=cron)
    assert done["changed"] and done["from"] == "acero"
    new = done["reminder"]["id"]
    assert cron.get_job(rid) is None
    job = cron.get_job(new)
    assert scope_of(job.payload.board) == "nuove" and job.payload.why == "dormienza.md"
    assert job.name == "Rinvaso" and job.schedule.kind == "at"
    again = await edit.attach_reminder(notebook, "nuove", new, cron=cron)
    assert again["changed"] is False and cron.get_job(new) is not None
    with pytest.raises(edit.BoardEditError) as err:
        await edit.attach_reminder(notebook, "acero", rid, cron=cron)
    assert err.value.code == "not_found"


async def test_without_the_scheduler_a_reminder_is_refused(notebook: Path) -> None:
    _seed(notebook)
    with pytest.raises(edit.BoardEditError) as err:
        await edit.add_reminder(notebook, "acero", "x",
                                {"kind": "date", "date": "2099-03-01"}, cron=None)
    assert err.value.code == "unavailable"
