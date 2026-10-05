"""Una micro app nel cassetto: la vista, il segno ⟳, gli eventi, le attese."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jenny.board import micro as mc
from jenny.board.clock import run_reminders
from jenny.board.sections import alert_probe, section_resolver
from jenny.board.store import mutate_board, mutate_reminders, read_board, read_reminders
from jenny.board.view import build_board_view, build_card_view

SPEC = {
    "title": "Acerello", "card": "acero", "every": 15,
    "fields": {"moisture": {"label": "Umidità", "unit": "%", "type": "number"},
               "online": {"label": "Sensore", "type": "bool"}},
    "primary": "moisture",
    "alarm": {"field": "moisture", "op": "<", "value": 20},
}


class _Agent:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.sessions = None

    async def process_direct(self, prompt, **_):
        self.prompts.append(prompt)


def _notebook(tmp_path: Path, moisture: int, *, pending: bool = False) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    code = f"def run(ctx):\n    return {{'moisture': {moisture}, 'online': True}}\n"
    m = mc.write_micro(root, "m-acerello", SPEC, code, draft=pending)
    if not pending:
        mc.write_approval(ws, root, m)

    def apply(board):
        board["cards"].append({
            "id": "acero", "title": "Acero giapponese", "pages": [], "pos": {"x": 1, "y": 1},
            "userSections": [{"id": "s-micro-1", "kind": "micro", "micro": "m-acerello",
                              "pending": pending}],
        })

    mutate_board(root, apply)
    return ws, root


def _set_code(ws: Path, root: Path, moisture: int) -> None:
    code = f"def run(ctx):\n    return {{'moisture': {moisture}, 'online': True}}\n"
    m = mc.write_micro(root, "m-acerello", SPEC, code, draft=False)
    mc.write_approval(ws, root, m)


async def test_the_clock_runs_it_and_the_drawer_shows_it(tmp_path) -> None:
    ws, root = _notebook(tmp_path, 38)
    out = await run_reminders(_Agent(), ws)
    assert out and "1 micro runs" in out
    card = build_card_view(root, "acero", sections_resolver=section_resolver(ws, root))
    view = card["userSections"][0]["view"]
    assert (view["state"], view["value"], view["unit"]) == ("ok", 38, "%")
    board = build_board_view(root, alert_probe=alert_probe(ws, root))
    assert board["cards"][0]["auto"] is True
    assert board["cards"][0]["signal"] != "alert"


async def test_a_state_change_is_journaled_and_lights_the_corner(tmp_path) -> None:
    ws, root = _notebook(tmp_path, 38)
    await run_reminders(_Agent(), ws)
    _set_code(ws, root, 12)
    later = datetime.now(timezone.utc) + timedelta(minutes=20)
    await mc.run_micro(ws, root, "m-acerello", now=later, force=True)
    await run_reminders(_Agent(), ws)
    since = [r for r in read_board(root)["since"] if r["kind"] == "app_event"]
    assert since and since[-1]["state"] == "alarm"
    journal = "".join(p.read_text() for p in (root / "raw" / "journal").glob("*.md"))
    assert "Acerello on Acero giapponese: alarm" in journal
    assert alert_probe(ws, root)(read_board(root)["cards"][0]["userSections"][0])


async def test_a_condition_on_a_micro_app_fires(tmp_path) -> None:
    ws, root = _notebook(tmp_path, 18)
    mutate_reminders(root, lambda r: r["reminders"].append({
        "id": "r-annaffia", "card": "acero", "text": "Annaffia l'acero", "by": "user",
        "when": {"kind": "condition", "micro": "m-acerello", "field": "moisture",
                 "op": "<", "value": 25},
    }))
    agent = _Agent()
    await run_reminders(agent, ws)
    assert len(agent.prompts) == 1 and "observed: 18" in agent.prompts[0]
    assert read_reminders(root)["reminders"][0]["when"]["micro"] == "m-acerello"


async def test_a_pending_micro_app_does_not_run_and_shows_its_proof(tmp_path) -> None:
    ws, root = _notebook(tmp_path, 38, pending=True)
    draft = mc.load_micro(root, "m-acerello", draft=True)
    result = await mc.execute_micro(ws, root, draft)
    mc.record_run(draft, result, now=datetime.now(timezone.utc))
    out = await run_reminders(_Agent(), ws)
    assert out is None, "una bozza non gira dall'orologio"
    card = build_card_view(root, "acero", sections_resolver=section_resolver(ws, root))
    view = card["userSections"][0]["view"]
    assert view["state"] == "pending" and view["value"] == 38
    board = build_board_view(root, alert_probe=alert_probe(ws, root))
    assert board["cards"][0]["auto"] is False


# ── i comandi del cassetto ──────────────────────────────────────────────────

from jenny.webui.commands import CommandError, dispatch_command  # noqa: E402


async def _trial(workspace_root: Path, notebook: Path) -> None:
    code = "def run(ctx):\n    return {'moisture': 38, 'online': True}\n"
    draft = mc.write_micro(notebook, "m-acerello", SPEC, code, draft=True)
    result = await mc.execute_micro(workspace_root, notebook, draft)
    mc.record_run(draft, result, now=datetime.now(timezone.utc))
    mutate_board(notebook, lambda b: b["cards"].append({
        "id": "acero", "title": "Acero", "pages": [], "pos": {"x": 1, "y": 1}}))
    mc.set_section(notebook, "acero", "m-acerello", pending=True)


async def test_keep_approves_and_reads_at_once(ctx, workspace_root, notebook) -> None:
    await _trial(workspace_root, notebook)
    await dispatch_command(ctx, "board.micro.keep", {"wiki": "piante", "micro": "m-acerello"})
    live = mc.load_micro(notebook, "m-acerello")
    assert live is not None and mc.is_approved(workspace_root, notebook, live)
    assert read_board(notebook)["cards"][0]["userSections"][0]["pending"] is False
    assert len(mc.read_readings(live.dir)) == 2, "la prova e la prima lettura da viva"
    assert any(r["kind"] == "micro_kept" for r in read_board(notebook)["since"])
    await dispatch_command(ctx, "board.micro.pause", {"wiki": "piante", "micro": "m-acerello",
                                                      "paused": True})
    assert mc.read_state(live.dir)["status"] == "paused"


async def test_discard_a_first_trial_takes_the_section_away(ctx, workspace_root, notebook) -> None:
    await _trial(workspace_root, notebook)
    await dispatch_command(ctx, "board.micro.discard", {"wiki": "piante", "micro": "m-acerello"})
    assert read_board(notebook)["cards"][0]["userSections"] == []
    assert mc.load_micro(notebook, "m-acerello", draft=True) is None


async def test_removing_the_section_trashes_the_code(ctx, workspace_root, notebook) -> None:
    await _trial(workspace_root, notebook)
    await dispatch_command(ctx, "board.micro.keep", {"wiki": "piante", "micro": "m-acerello"})
    sid = read_board(notebook)["cards"][0]["userSections"][0]["id"]
    await dispatch_command(ctx, "board.section.remove", {"wiki": "piante", "card": "acero",
                                                         "id": sid})
    assert mc.load_micro(notebook, "m-acerello") is None
    assert list((mc.micros_dir(notebook) / ".trash").iterdir())
    assert mc.read_approval(workspace_root, notebook, "m-acerello") is None


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
