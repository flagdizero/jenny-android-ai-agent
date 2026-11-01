"""Le attese di ``board/reminders.json`` (2.0.0) diventano job dello scheduler.

Un'installazione che si aggiorna ha ancora quel file: la prima passata
dell'orologio lo converte, così le attese aperte non smettono di suonare in
silenzio (:mod:`jenny.board.legacy_reminders`).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jenny.board import model
from jenny.board.clock import CLOCK_EVERY_MS, run_reminders
from jenny.board.legacy_reminders import migrate_legacy_reminders
from jenny.board.store import mutate_board
from jenny.cron.service import CronService


def _notebook(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "travaso-acero.md").write_text("# travaso acero\n")
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero", "pos": {"x": 5, "y": 5}}))
    return ws, root


def _service(tmp_path: Path) -> CronService:
    service = CronService(tmp_path / "workspace" / "cron" / "jobs.json")
    service._arm_timer = lambda: None  # type: ignore[method-assign]
    service._running = True
    service._load_store()
    return service


def _legacy(root: Path, reminders: list[dict]) -> Path:
    path = root / "board" / "reminders.json"
    path.write_text(json.dumps({"version": 1, "reminders": reminders}))
    return path


def test_open_reminders_become_notebook_jobs_and_the_file_is_set_aside(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    cron = _service(tmp_path)
    path = _legacy(root, [
        {"id": "r-travaso", "card": "acero", "text": "Travaso dell'acero", "state": "open",
         "when": {"kind": "date", "date": "2030-03-10", "precision": "day"},
         "why": "travaso-acero.md", "by": "user"},
        {"id": "r-concime", "card": "sparita", "text": "Concime", "state": "open",
         "when": {"kind": "date", "date": "2030-04", "precision": "month"}, "by": "jenny"},
        {"id": "r-secco", "card": "acero", "text": "Terreno secco", "state": "open",
         "when": {"kind": "condition", "app": "waterbot", "params": {"sensor": "acero"},
                  "field": "moisture", "op": "<", "value": 15}},
        {"id": "r-fatto", "card": "acero", "text": "Fatto", "state": "done",
         "when": {"kind": "date", "date": "2030-01-01", "precision": "day"}},
    ])

    assert migrate_legacy_reminders(root, cron, timezone_name="Europe/Rome") == 3

    jobs = {j.name: j for j in cron.list_jobs()}
    assert set(jobs) == {"Travaso dell'acero", "Concime", "Terreno secco"}
    travaso = jobs["Travaso dell'acero"]
    assert travaso.schedule.kind == "at" and travaso.schedule.precision == "day"
    assert travaso.payload.board["notebook"] == "piante"
    assert travaso.payload.board["card"] == "acero"
    assert travaso.payload.why == "travaso-acero.md"
    assert travaso.payload.origin_metadata == {"by": "user", "legacyId": "r-travaso"}
    # La scheda che non c'è più: l'attesa passa a tutto il quaderno, e resta di Jenny.
    concime = jobs["Concime"]
    assert model.scope_state({"cards": []}, concime.payload.board["card"]) != model.SCOPE_CARD
    assert concime.schedule.precision == "month"
    assert concime.payload.origin_metadata["by"] == "jenny"
    # La condizione: un ricorrente al passo dell'orologio, col suo filtro.
    secco = jobs["Terreno secco"]
    assert secco.schedule.kind == "every" and secco.schedule.every_ms == CLOCK_EVERY_MS
    assert secco.payload.condition == {"app": "waterbot", "params": {"sensor": "acero"},
                                       "field": "moisture", "op": "<", "value": 15}
    # Il file non si rilegge, e resta da vedere.
    assert not path.exists()
    assert path.with_name("reminders.json.migrated").exists()
    assert migrate_legacy_reminders(root, cron) == 0
    assert len(cron.list_jobs()) == 3


def test_a_reminder_already_due_rings_at_the_first_run(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    cron = _service(tmp_path)
    _legacy(root, [
        {"id": "r-old", "card": "acero", "text": "Rinvaso", "state": "open",
         "when": {"kind": "date", "date": "2020-03", "precision": "month"},
         "notifiedAt": "2020-03-01T09:00:00+01:00"},
    ])
    now = datetime.now().astimezone()

    assert migrate_legacy_reminders(root, cron, now=now) == 1

    job = cron.list_jobs()[0]
    assert job.schedule.kind == "at" and job.schedule.precision == "month"
    assert job.schedule.at_ms is not None
    assert 0 < job.schedule.at_ms - int(now.timestamp() * 1000) <= 60 * 1000
    assert job.state.next_run_at_ms == job.schedule.at_ms


def test_without_a_scheduler_or_a_file_nothing_moves(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    path = _legacy(root, [
        {"id": "r-x", "card": "acero", "text": "Travaso", "state": "open",
         "when": {"kind": "date", "date": "2030-01-01", "precision": "day"}},
    ])
    assert migrate_legacy_reminders(root, None) == 0
    assert path.exists()
    cron = _service(tmp_path)
    path.unlink()
    assert migrate_legacy_reminders(root, cron) == 0
    assert cron.list_jobs() == []


def test_an_unreadable_file_is_set_aside_and_not_read_again(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    cron = _service(tmp_path)
    path = root / "board" / "reminders.json"
    path.write_text("{not json")
    assert migrate_legacy_reminders(root, cron) == 0
    assert not path.exists()
    assert path.with_name("reminders.json.unreadable").exists()


def test_a_condition_on_a_micro_app_of_before_is_left_out(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    cron = _service(tmp_path)
    _legacy(root, [
        {"id": "r-micro", "card": "acero", "text": "Umido", "state": "open",
         "when": {"kind": "condition", "micro": "sensore", "field": "um", "op": "<",
                  "value": 10}},
        {"id": "r-ok", "card": "acero", "text": "Travaso", "state": "open",
         "when": {"kind": "date", "date": "2030-01-01", "precision": "day"}},
    ])
    assert migrate_legacy_reminders(root, cron) == 1
    assert [j.name for j in cron.list_jobs()] == ["Travaso"]


async def test_the_clock_migrates_on_its_pass(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    cron = _service(tmp_path)
    _legacy(root, [
        {"id": "r-x", "card": "acero", "text": "Travaso", "state": "open",
         "when": {"kind": "date", "date": "2030-01-01", "precision": "day"}},
    ])
    line = await run_reminders(None, ws, cron=cron)
    assert line is not None and "1 reminders migrated" in line
    assert [j.name for j in cron.list_jobs()] == ["Travaso"]
    assert await run_reminders(None, ws, cron=cron) is None
