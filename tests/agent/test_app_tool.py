"""Il tool ``app``: un'app esiste appena funziona.

Si guarda il giro che conta: un'app valida è subito nella pagina App, coi suoi
tool (niente prova da tenere, tolta il 06/10/2026); una versione nuova che non
funziona non tocca quella viva; un lettore gira davvero e il riassunto mostra
la sua lettura; da un quaderno si posa ma non si cancella.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.agent.tools.app_actions import AppToolsSyncer
from jenny.agent.tools.app_tool import AppTool
from jenny.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from jenny.agent.tools.registry import ToolRegistry
from jenny.apps.executor import execute_action
from jenny.apps.manifest import find_app
from jenny.apps.summary_view import summary_view
from jenny.board.placements import placements
from jenny.board.store import mutate_board, read_board
from jenny.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from jenny.webui.commands import CommandContext

SPESA = {
    "name": "Spesa", "description": "Shopping list",
    "actions": [{"name": "add", "description": "Add an item", "kind": "storage",
                 "op": "append", "collection": "items",
                 "params": {"what": {"type": "string"}, "done": {"type": "boolean"}}}],
    "summary": {"collection": "items", "view": "list", "where": {"done": False},
                "label": "to buy", "columns": {"what": {"label": "What"}}},
}
WATERBOT = {
    "name": "WaterBot", "description": "Moisture from the hub",
    "reader": {"every": 30, "key": "sensor", "fields": {
        "sensor": {"label": "Sensor", "type": "text"},
        "moisture": {"label": "Moisture", "type": "number", "unit": "%"},
    }},
    "summary": {"primary": "moisture", "params": {"sensor": {"type": "string"}},
                "where": {"sensor": "$sensor"}},
}
READER = ("def run(ctx):\n"
          "    return [{'sensor': 'acerello', 'moisture': 38}, {'sensor': 'monstera', 'moisture': 17}]\n")


@pytest.fixture
def ws(tmp_path: Path, monkeypatch) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    root = workspace / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acerello", "pages": [], "pos": {"x": 1, "y": 1}}))
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: workspace)
    return workspace


@pytest.fixture
def personal(ws: Path):
    req = bind_request_context(RequestContext(channel="websocket", chat_id="unified:default",
                                              session_key="unified:default"))
    try:
        yield ws
    finally:
        reset_request_context(req)


@pytest.fixture
def notebook_chat(ws: Path):
    root = ws / "wikis" / "piante"
    scope = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    req = bind_request_context(RequestContext(channel="websocket", chat_id="project:piante",
                                              session_key="project:piante"))
    try:
        yield ws, root
    finally:
        reset_request_context(req)
        reset_workspace_scope(scope)


def _ctx(ws: Path) -> CommandContext:
    return CommandContext(get_workspace_root=lambda: ws, invalidate_session=lambda _k: None,
                          busy_session_keys=lambda: ())


async def test_a_new_app_is_live_at_once_with_its_tools(personal: Path) -> None:
    ws = personal
    out = await AppTool().execute(action="draft", slug="spesa", files={
        "app.json": json.dumps(SPESA), "app/index.html": "<p>spesa</p>"})
    assert "`spesa` is live" in out and "action=place" in out, out
    # La lista di prima e' una striscia: tabella e conteggio.
    assert "On a board it is a strip L (table)." in out, out
    assert find_app(ws, "spesa") is not None
    assert not (ws / "apps" / ".drafts" / "spesa").exists()
    registry = ToolRegistry()
    AppToolsSyncer(ws).sync(registry)
    assert registry.get("spesa_add") is not None
    await execute_action(ws, "spesa", "add", {"what": "latte", "done": False})
    view = summary_view(ws, find_app(ws, "spesa"), {})
    assert (view["value"], view["rows"]) == (1, [{"what": "latte"}])


async def test_a_broken_new_version_leaves_the_live_app_alone(personal: Path) -> None:
    ws = personal
    tool = AppTool()
    await tool.execute(action="draft", slug="spesa", files={"app.json": json.dumps(SPESA)})
    out = await tool.execute(action="draft", slug="spesa",
                             files={"app/index.html": "<p>la spesa</p>"})
    assert "`spesa` is updated" in out, out
    assert (ws / "apps" / "spesa" / "app" / "index.html").read_text() == "<p>la spesa</p>"
    await execute_action(ws, "spesa", "add", {"what": "latte", "done": False})
    # Un manifest rotto: niente cambia, e la bozza resta da correggere.
    out = await tool.execute(action="draft", slug="spesa", files={"app.json": "{not json"})
    assert "is not valid" in out and "nothing changed" in out, out
    assert json.loads((ws / "apps" / "spesa" / "app.json").read_text())["name"] == "Spesa"
    assert (ws / "apps" / ".drafts" / "spesa").is_dir()
    # La correzione la promuove, e i dati scritti restano.
    out = await tool.execute(action="draft", slug="spesa", files={"app.json": json.dumps(
        dict(SPESA, description="La spesa"))})
    assert "`spesa` is updated" in out, out
    assert json.loads((ws / "apps" / "spesa" / "app.json").read_text())["description"] == \
        "La spesa"
    assert summary_view(ws, find_app(ws, "spesa"), {})["value"] == 1


async def test_a_stateful_app_without_a_summary_is_not_ready(personal: Path) -> None:
    bare = {k: v for k, v in SPESA.items() if k != "summary"}
    out = await AppTool().execute(action="draft", slug="spesa",
                                  files={"app.json": json.dumps(bare)})
    assert "must declare a `summary`" in out


async def test_a_reader_runs_once_and_keeps_its_reading(personal: Path) -> None:
    ws = personal
    out = await AppTool().execute(action="draft", slug="waterbot", files={
        "app.json": json.dumps(WATERBOT), "run.py": READER})
    assert "`waterbot` is live — Moisture: 17%" in out, out
    app = find_app(ws, "waterbot")
    assert summary_view(ws, app, {"sensor": "monstera"})["value"] == 17
    assert summary_view(ws, app, {"sensor": "acerello"})["value"] == 38


async def test_a_credential_in_the_code_is_refused(personal: Path) -> None:
    out = await AppTool().execute(action="draft", slug="waterbot", files={
        "app.json": json.dumps(WATERBOT),
        "run.py": READER + "TOKEN = 'sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789'\n"})
    assert "credential" in out


async def test_from_a_notebook_jenny_places_but_does_not_delete(notebook_chat) -> None:
    ws, root = notebook_chat
    tool = AppTool()
    await tool.execute(action="draft", slug="waterbot", files={
        "app.json": json.dumps(WATERBOT), "run.py": READER})
    out = await tool.execute(action="place", slug="waterbot", card="acerello",
                             params={"sensor": "acerello"})
    assert "as the card «WaterBot», tied to «Acerello»" in out, out
    out = await tool.execute(action="place", slug="waterbot", card="acerello",
                             params={"sensor": "acerello"})
    assert "already on this board" in out, out
    places = placements(root)
    assert [(p["app"], p["section"]) for p in places] == [("waterbot", None)]
    board = read_board(root)
    card = next(c for c in board["cards"] if c.get("app"))
    assert card["pos"]["by"] == "user" and card["appBy"] == "jenny-on-request"
    acerello = next(c for c in board["cards"] if c["title"] == "Acerello")
    assert [{c["a"], c["b"]} for c in board["connections"]] == [{card["id"], acerello["id"]}]
    out = await tool.execute(action="trash", slug="waterbot")
    assert "Not from a notebook" in out


async def test_only_user_conversations_write_apps(ws: Path) -> None:
    req = bind_request_context(RequestContext(channel="cron", chat_id="x",
                                              session_key="cron:reminders"))
    try:
        out = await AppTool().execute(action="draft", slug="spesa",
                                      files={"app.json": json.dumps(SPESA)})
    finally:
        reset_request_context(req)
    assert out.startswith("Not from here")


async def test_a_strip_bigger_than_xl_is_refused_with_what_to_do(personal: Path) -> None:
    ws = personal
    tall = dict(SPESA, summary={"collection": "items", "blocks": [
        {"kind": "table", "columns": {"what": {}}, "max": 5},
        {"kind": "table", "columns": {"what": {}}, "max": 5}]})
    out = await AppTool().execute(action="draft", slug="spesa",
                                  files={"app.json": json.dumps(tall)})
    assert "a strip holds at most 9 (XL)" in out and "nothing changed" in out, out
    assert find_app(ws, "spesa") is None
