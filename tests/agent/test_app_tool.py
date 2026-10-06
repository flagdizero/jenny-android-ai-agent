"""Il tool ``app`` e la prova per tutte le app.

Si guarda il giro che conta: un'app nuova nasce in prova e non ha tool né
schermo finché l'utente non la tiene (``apps.keep``); una correzione che non
allarga niente va subito; una che aggiunge un host torna in prova; un lettore
gira davvero e il riassunto mostra la sua lettura; da un quaderno si posa ma
non si cancella; e il mondo di prima (nessun file di approvazioni) non cambia.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.agent.tools.app_actions import AppToolsSyncer
from jenny.agent.tools.app_tool import AppTool
from jenny.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from jenny.agent.tools.registry import ToolRegistry
from jenny.apps import trust
from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import find_app
from jenny.apps.summary_view import summary_view
from jenny.board.placements import placements
from jenny.board.store import mutate_board, read_board
from jenny.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from jenny.webui.commands import CommandContext, dispatch_command

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
    # La prova e' accesa: il file delle approvazioni esiste (vuoto).
    assert trust.grandfather(workspace) == 0
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


async def test_a_new_app_waits_for_keep_with_no_tools_and_no_screen(personal: Path) -> None:
    ws = personal
    out = await AppTool().execute(action="draft", slug="spesa", files={
        "app.json": json.dumps(SPESA), "app/index.html": "<p>spesa</p>"})
    assert "ON TRIAL" in out and "writes items" in out, out
    assert find_app(ws, "spesa") is None
    registry = ToolRegistry()
    AppToolsSyncer(ws).sync(registry)
    assert registry.get("spesa_add") is None
    assert trust.app_trust_state(ws, "spesa")["state"] == "trial"

    await dispatch_command(_ctx(ws), "apps.keep", {"slug": "spesa"})
    assert trust.is_live(ws, "spesa")
    AppToolsSyncer(ws).sync(registry)
    assert registry.get("spesa_add") is not None
    await execute_action(ws, "spesa", "add", {"what": "latte", "done": False})
    view = summary_view(ws, find_app(ws, "spesa"), {})
    assert (view["value"], view["rows"]) == (1, [{"what": "latte"}])


async def test_code_changed_outside_jenny_stops_until_approved_again(personal: Path) -> None:
    ws = personal
    await AppTool().execute(action="draft", slug="spesa", files={"app.json": json.dumps(SPESA)})
    await dispatch_command(_ctx(ws), "apps.keep", {"slug": "spesa"})
    (ws / "apps" / "spesa" / "app" ).mkdir(exist_ok=True)
    (ws / "apps" / "spesa" / "app" / "index.html").write_text("<script>evil()</script>")
    assert not trust.is_live(ws, "spesa")
    with pytest.raises(AppActionError) as err:
        await execute_action(ws, "spesa", "add", {"what": "x"})
    assert err.value.status == 423
    assert trust.app_trust_state(ws, "spesa")["state"] == "unapproved"
    await dispatch_command(_ctx(ws), "apps.keep", {"slug": "spesa"})
    assert trust.is_live(ws, "spesa")


async def test_a_fix_goes_live_and_a_wider_change_goes_back_on_trial(personal: Path) -> None:
    ws = personal
    tool = AppTool()
    await tool.execute(action="draft", slug="spesa", files={"app.json": json.dumps(SPESA)})
    await dispatch_command(_ctx(ws), "apps.keep", {"slug": "spesa"})
    # Una correzione: solo il testo dello schermo.
    out = await tool.execute(action="draft", slug="spesa",
                             files={"app/index.html": "<p>la spesa</p>"})
    assert "live already" in out, out
    assert trust.is_live(ws, "spesa")
    assert (ws / "apps" / "spesa" / "app" / "index.html").read_text() == "<p>la spesa</p>"
    # Un server nuovo: torna in prova, e la versione viva resta quella di prima.
    wider = dict(SPESA, server={"baseUrl": "http://192.168.1.50:8080"})
    out = await tool.execute(action="draft", slug="spesa", files={"app.json": json.dumps(wider)})
    assert "ON TRIAL" in out and "host:192.168.1.50" in out, out
    state = trust.app_trust_state(ws, "spesa")
    assert state["state"] == "changed" and state["trial"]["reaches"] == ["192.168.1.50"]
    assert trust.is_live(ws, "spesa")
    assert "server" not in json.loads((ws / "apps" / "spesa" / "app.json").read_text())
    await dispatch_command(_ctx(ws), "apps.discard", {"slug": "spesa"})
    assert trust.app_trust_state(ws, "spesa")["state"] == "live"


async def test_a_stateful_app_without_a_summary_is_not_ready(personal: Path) -> None:
    bare = {k: v for k, v in SPESA.items() if k != "summary"}
    out = await AppTool().execute(action="draft", slug="spesa",
                                  files={"app.json": json.dumps(bare)})
    assert "must declare a `summary`" in out


async def test_a_reader_runs_once_on_trial_and_keeps_its_reading(personal: Path) -> None:
    ws = personal
    out = await AppTool().execute(action="draft", slug="waterbot", files={
        "app.json": json.dumps(WATERBOT), "run.py": READER})
    assert "first reading" in out, out
    await dispatch_command(_ctx(ws), "apps.keep", {"slug": "waterbot"})
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


async def test_before_trials_nothing_changes(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: workspace)
    req = bind_request_context(RequestContext(channel="websocket", chat_id="unified:default",
                                              session_key="unified:default"))
    try:
        out = await AppTool().execute(action="draft", slug="spesa",
                                      files={"app.json": json.dumps(SPESA)})
    finally:
        reset_request_context(req)
    assert "is live" in out
    assert trust.is_live(workspace, "spesa")


def test_grandfathering_approves_what_is_there(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    d = workspace / "apps" / "orto"
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({"name": "Orto", "description": "x"}))
    assert trust.grandfather(workspace) == 1
    assert trust.is_live(workspace, "orto")
    assert trust.read_approvals(workspace)["orto"]["grandfathered"] is True
    assert trust.grandfather(workspace) == 0
