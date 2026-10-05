"""Una micro app nel cassetto: la vista, il segno ⟳, gli eventi, le attese."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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
