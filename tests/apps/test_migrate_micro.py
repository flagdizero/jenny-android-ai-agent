"""Le micro app dei cassetti diventano app generali, senza che l'utente perda niente."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jenny.apps.manifest import find_app
from jenny.apps.migrate_micro import migrate_micro_apps
from jenny.board import micro as mc
from jenny.board.sections import app_field_value, app_section_view
from jenny.board.store import mutate_board, mutate_reminders, read_board, read_reminders

FIELDS = {"moisture": {"label": "Umidità", "unit": "%", "type": "number"},
          "online": {"label": "Sensore", "type": "bool"}}
CODE = "def run(ctx):\n    return {'moisture': 38, 'online': True, '_source': 'hub'}\n"


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: ws)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero", "pages": [], "pos": {"x": 1, "y": 1}}))
    return ws, root


def _micro(ws: Path, root: Path, micro_id: str, *, approved: bool, pending: bool = False) -> None:
    spec = {"title": "Acerello", "fields": FIELDS, "primary": "moisture", "every": 30,
            "card": "acero", "alarm": {"field": "moisture", "op": "<", "value": 20}}
    # Scritta a mano, come la lasciava una versione di prima del 06/10/2026:
    # chi la scriveva non c'e' piu'.
    d = mc.micro_dir(root, micro_id, draft=pending)
    d.mkdir(parents=True)
    (d / mc.SPEC_FILE).write_text(json.dumps(spec), encoding="utf-8")
    (d / mc.CODE_FILE).write_text(CODE, encoding="utf-8")
    if not pending:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (d / mc.READINGS_FILE).write_text(json.dumps(
            {"id": "r1", "ts": stamp, "moisture": 41, "online": True}) + "\n", encoding="utf-8")
        (d / mc.STATE_FILE).write_text(json.dumps(
            {"status": "ok", "lastRunAt": "2026-10-06T08:00:00+00:00"}), encoding="utf-8")
        if approved:
            path = mc.approvals_path(ws)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(path.read_text()) if path.exists() else {}
            data[f"{root.name}/{micro_id}"] = {"codeHash": "sha256:x"}
            path.write_text(json.dumps(data), encoding="utf-8")
    mutate_board(root, lambda b: b["cards"][0]["userSections"].append(
        {"id": f"s-{micro_id}", "kind": "micro", "micro": micro_id, "pending": pending,
         "by": "jenny-on-request"}))


def test_an_approved_micro_app_becomes_a_live_placed_app(notebook) -> None:
    ws, root = notebook
    _micro(ws, root, "m-acerello", approved=True)
    mutate_reminders(root, lambda r: r["reminders"].append({
        "id": "r-secca", "card": "acero", "text": "Annaffia",
        "when": {"kind": "condition", "micro": "m-acerello", "field": "moisture",
                 "op": "<", "value": 30}}))
    assert migrate_micro_apps(ws) == 1

    app = find_app(ws, "acerello")
    assert app is not None and not app.broken, app and app.error
    assert app.manifest.reader["every"] == 30
    section = read_board(root)["cards"][0]["userSections"][0]
    assert section == {"id": "s-m-acerello", "kind": "app", "app": "acerello", "params": {},
                       "created": section["created"], "migratedFrom": "m-acerello",
                       "by": "jenny-on-request"}
    view = app_section_view(ws, "acerello", {})
    assert (view["value"], view["unit"], view["every"]) == (41, "%", 30)
    when = read_reminders(root)["reminders"][0]["when"]
    assert when["app"] == "acerello" and "micro" not in when
    assert app_field_value(ws, "acerello", {}, "moisture")[0] == 41
    # La cartella vecchia e' messa da parte, e una seconda passata non fa niente.
    assert mc.list_micros(root) == []
    assert json.loads(mc.approvals_path(ws).read_text()) == {}
    assert (root / "board" / "micro" / ".migrated" / "m-acerello" / "run.py").is_file()
    assert migrate_micro_apps(ws) == 0


def test_an_unapproved_or_pending_micro_app_becomes_an_app_too(notebook) -> None:
    """Le app non hanno una prova: anche una micro app in prova diventa un'app."""
    ws, root = notebook
    _micro(ws, root, "m-acerello", approved=False)
    _micro(ws, root, "m-nuovo", approved=False, pending=True)
    assert migrate_micro_apps(ws) == 2
    assert find_app(ws, "acerello") is not None and find_app(ws, "acerello-2") is not None
    kinds = [(s["kind"], s["app"]) for s in read_board(root)["cards"][0]["userSections"]]
    assert kinds == [("app", "acerello"), ("app", "acerello-2")]
    assert app_section_view(ws, "acerello-2", {})["state"] == "empty"


def test_the_generated_manifest_reads_back(notebook) -> None:
    ws, root = notebook
    _micro(ws, root, "m-acerello", approved=True)
    migrate_micro_apps(ws)
    raw = json.loads((ws / "apps" / "acerello" / "app.json").read_text())
    assert raw["summary"]["primary"] == "moisture"
    assert raw["summary"]["alarm"]["value"] == 20
