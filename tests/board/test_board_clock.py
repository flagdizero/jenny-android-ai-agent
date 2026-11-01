"""L'orologio dei quaderni: i lettori delle app, gli eventi delle sezioni, le schede tolte.

Fino al 31/10/2026 annunciava anche le attese di ``board/reminders.json``; ora un
promemoria è un job dello scheduler, e l'orologio non lo guarda più.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jenny.board import clock
from jenny.board.clock import run_reminders, watch_app_sections
from jenny.board.store import mutate_board, read_board


class _Agent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def process_direct(self, prompt: str, **_kw) -> None:
        self.prompts.append(prompt)


def _notebook(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "wiki" / "travaso-acero.md").write_text(
        "# travaso acero\n\nDal vaso 14 al 20, in dormienza perche' a fine estate lo stresserebbe.\n"
    )
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero", "pos": {"x": 5, "y": 5}}))
    return ws, root


async def test_the_clock_announces_nothing_any_more(tmp_path) -> None:
    """Un ``reminders.json`` rimasto sul disco non fa partire nessun turno."""
    ws, root = _notebook(tmp_path)
    (root / "board" / "reminders.json").write_text(json.dumps({"reminders": [
        {"id": "r-x", "card": "acero", "text": "Travaso", "state": "open",
         "when": {"kind": "date", "date": "2020-01-01", "precision": "day"}},
    ]}))
    agent = _Agent()
    assert await run_reminders(agent, ws) is None
    assert agent.prompts == []
    assert read_board(root)["since"] == []


def test_the_reminder_machinery_is_gone() -> None:
    for name in ("due_by_date", "check_conditions", "ConditionPass", "REMINDER_SESSION_KEY",
                 "REARM_AFTER_FALSE", "MIN_HOURS_BETWEEN_ANNOUNCES", "_announce",
                 "_announce_prompt", "_stamp"):
        assert not hasattr(clock, name), name


def _waterbot(ws: Path, readings: list[dict]) -> None:
    app = ws / "apps" / "waterbot"
    (app / "data").mkdir(parents=True)
    (app / "app").mkdir()
    (app / "app" / "index.html").write_text("<p>hi</p>")
    (app / "app.json").write_text(json.dumps({
        "name": "WaterBot", "description": "Sensori",
        "section": {
            "params": {"sensor": {"type": "string"}},
            "value": {"collection": "readings", "where": {"sensor": "$sensor"},
                      "field": "moisture", "unit": "%"},
            "status": {"stale_after_minutes": 120,
                       "alarm": {"field": "moisture", "op": "<", "value": 20}},
            "trend": {"days": 7},
        },
    }))
    (app / "data" / "readings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in readings))


def test_an_app_event_is_journaled_once(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _waterbot(ws, [{"sensor": "albinella", "moisture": 40, "ts": fresh}])
    mutate_board(root, lambda b: b["cards"][0]["userSections"].append(
        {"id": "s-app", "kind": "app", "app": "waterbot", "params": {"sensor": "albinella"}}))
    # La prima volta si registra e basta.
    assert watch_app_sections(ws, root) == 0
    assert read_board(root)["appStates"] == {"acero/s-app": "ok"}
    assert not list((root / "raw" / "journal").glob("*.md"))
    # Va in allarme: una riga di diario, una di «dall'ultima volta».
    readings = ws / "apps" / "waterbot" / "data" / "readings.jsonl"
    later = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(timespec="seconds")
    readings.write_text(readings.read_text() + json.dumps(
        {"sensor": "albinella", "moisture": 12, "ts": later}) + "\n")
    assert watch_app_sections(ws, root) == 1
    journal = next((root / "raw" / "journal").glob("*.md")).read_text()
    assert "[inferred] WaterBot on Acero: alarm" in journal
    assert read_board(root)["since"][-1]["kind"] == "app_event"
    # Fermo nello stesso stato: niente di nuovo.
    assert watch_app_sections(ws, root) == 0
    assert journal.count("WaterBot") == 1
