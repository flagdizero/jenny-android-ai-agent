"""L'orologio dei quaderni: quando un'attesa e' dovuta, e cosa succede quando lo e'."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from jenny.board import model
from jenny.board.clock import due_by_date, run_reminders, watch_app_sections
from jenny.board.store import mutate_board, mutate_reminders, read_board, read_reminders

ROME = timezone(timedelta(hours=1))


def _rem(rid: str, day: str, *, precision: str = "day", by: str = "jenny", **extra):
    return {"id": rid, "card": "acero", "text": rid, "by": by,
            "when": {"kind": "date", "date": day, "precision": precision}, **extra}


def test_a_month_is_due_at_nine_on_its_first_day() -> None:
    rems = model.normalize_reminders({"reminders": [_rem("travaso", "2027-03-01", precision="month")]})
    assert due_by_date(rems, datetime(2027, 2, 28, 23, 0, tzinfo=ROME)) == []
    assert due_by_date(rems, datetime(2027, 3, 1, 8, 59, tzinfo=ROME)) == []
    assert [r["id"] for r in due_by_date(rems, datetime(2027, 3, 1, 9, 0, tzinfo=ROME))] == ["travaso"]
    # Un'attesa gia' annunciata non torna; una passata da giorni si', una volta.
    rems["reminders"][0]["notifiedAt"] = "2027-03-01T09:00:00+01:00"
    assert due_by_date(rems, datetime(2027, 3, 5, 10, 0, tzinfo=ROME)) == []


class _Agent:
    def __init__(self):
        self.prompts: list[str] = []
        self.sessions = SimpleNamespace(
            get_or_create=lambda key: SimpleNamespace(retain_recent_legal_suffix=lambda n: None),
            save=lambda s: None,
        )

    async def process_direct(self, prompt, **kwargs):
        self.prompts.append(prompt)
        assert kwargs["session_key"] == "cron:reminders"
        assert kwargs["chat_id"] == "default"
        return None


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


async def test_a_due_reminder_is_announced_once_with_its_why(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    mutate_reminders(root, lambda r: r["reminders"].extend([
        _rem("r-travaso", "2027-03-01", precision="month", why="travaso-acero.md"),
        _rem("r-potatura", "2027-02-15", by="user"),
        _rem("r-dopo", "2027-04-01"),
    ]))
    agent = _Agent()
    now = datetime(2027, 3, 2, 10, 0, tzinfo=ROME)
    out = await run_reminders(agent, ws, now=now)
    assert out == "reminders: 2 announced, 0 app events, 0 micro runs"
    assert len(agent.prompts) == 2
    travaso = next(p for p in agent.prompts if "r-travaso" in p)
    assert "Notebook: piante" in travaso
    assert "stresserebbe" in travaso, "l'avviso non porta il perche'"
    rems = {r["id"]: r for r in read_reminders(root)["reminders"]}
    assert rems["r-travaso"]["notifiedAt"] and rems["r-potatura"]["notifiedAt"]
    assert rems["r-potatura"]["by"] == "user", "il timbro non cambia di chi e' l'attesa"
    assert "notifiedAt" not in rems["r-dopo"]
    since = [row for row in read_board(root)["since"] if row["kind"] == "reminder_due"]
    assert len(since) == 2
    # Il giro dopo non annuncia di nuovo.
    assert await run_reminders(agent, ws, now=now + timedelta(hours=1)) is None
    assert len(agent.prompts) == 2


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


async def test_a_condition_fires_on_fresh_data_and_rearms(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _waterbot(ws, [{"sensor": "albinella", "moisture": 25, "ts": fresh}])
    mutate_reminders(root, lambda r: r["reminders"].append({
        "id": "r-annaffia", "card": "acero", "text": "Annaffia", "by": "user",
        "when": {"kind": "condition", "app": "waterbot", "params": {"sensor": "albinella"},
                 "field": "moisture", "op": "<", "value": 30},
    }))
    agent = _Agent()
    await run_reminders(agent, ws)
    assert len(agent.prompts) == 1 and "observed: 25" in agent.prompts[0]
    await run_reminders(agent, ws)
    assert len(agent.prompts) == 1, "una condizione gia' annunciata non si ripete"
    # Torna sopra la soglia: si riarma.
    readings = ws / "apps" / "waterbot" / "data" / "readings.jsonl"
    later = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(timespec="seconds")
    readings.write_text(readings.read_text() + json.dumps(
        {"sensor": "albinella", "moisture": 45, "ts": later}) + "\n")
    await run_reminders(agent, ws)
    rem = read_reminders(root)["reminders"][0]
    assert rem["notifiedAt"] and rem["falseStreak"] == 1, "un solo controllo falso non riarma"
    await run_reminders(agent, ws)
    rem = read_reminders(root)["reminders"][0]
    assert "notifiedAt" not in rem and "falseStreak" not in rem
    assert rem["by"] == "user", "i timbri dell'orologio non cambiano di chi e' l'attesa"
    # Torna sotto la soglia subito: dodici ore fra due annunci della stessa attesa.
    latest = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(timespec="seconds")
    readings.write_text(readings.read_text() + json.dumps(
        {"sensor": "albinella", "moisture": 22, "ts": latest}) + "\n")
    await run_reminders(agent, ws)
    assert len(agent.prompts) == 1, "un sensore che balla sulla soglia non riannuncia"


async def test_stale_data_never_fires_a_condition(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(timespec="seconds")
    _waterbot(ws, [{"sensor": "albinella", "moisture": 5, "ts": old}])
    mutate_reminders(root, lambda r: r["reminders"].append({
        "id": "r-annaffia", "card": "acero", "text": "Annaffia",
        "when": {"kind": "condition", "app": "waterbot", "params": {"sensor": "albinella"},
                 "field": "moisture", "op": "<", "value": 30},
    }))
    agent = _Agent()
    await run_reminders(agent, ws)
    assert agent.prompts == []


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
