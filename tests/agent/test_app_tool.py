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


async def test_an_update_never_copies_file_attributes(personal: Path, monkeypatch) -> None:
    # Sul telefono ``copystat`` copia anche ``security.selinux``, che un'app non
    # riscrive: EACCES. Il 06/10/2026 ogni edit di Albinella (che ha ``app/``)
    # tornava «Permission denied» dopo aver gia' copiato tutto. Sul Mac non si
    # vede: qui lo si fa fallire come sul telefono.
    import shutil

    def selinux(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    ws = personal
    tool = AppTool()
    await tool.execute(action="draft", slug="spesa", files={
        "app.json": json.dumps(SPESA), "app/index.html": "<p>spesa</p>",
        "app/css/s.css": "p{}"})
    monkeypatch.setattr(shutil, "copystat", selinux)
    monkeypatch.setattr(shutil, "copy2", selinux)
    out = await tool.execute(action="edit", slug="spesa", path="app/index.html",
                             old="<p>spesa</p>", new="<p>la spesa</p>")
    assert "`spesa` is updated" in out, out
    live = ws / "apps" / "spesa" / "app"
    assert (live / "index.html").read_text() == "<p>la spesa</p>"
    assert (live / "css" / "s.css").read_text() == "p{}"
    assert not (ws / "apps" / ".drafts" / "spesa").exists()


async def test_a_failed_promotion_does_not_blame_the_users_files(personal: Path,
                                                                 monkeypatch) -> None:
    # Il 06/10/2026 l'errore grezzo («[Errno 13] Permission denied» su due
    # cartelle) ha fatto chiedere a Jenny un chmod, e poi di cancellare l'app.
    ws = personal
    tool = AppTool()
    await tool.execute(action="draft", slug="spesa", files={"app.json": json.dumps(SPESA)})

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("jenny.apps.drafts.promote_draft", denied)
    out = await tool.execute(action="draft", slug="spesa",
                             files={"app/index.html": "<p>la spesa</p>"})
    assert "fault in Jenny" in out and "do not ask them to change permissions" in out, out
    assert "publishing it again" in out, out
    assert (ws / "apps" / ".drafts" / "spesa").is_dir()


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


# ── la prova dice la verita' (07/10/2026, il Portafoglio sul Titan) ──────────

PORTAFOGLIO = {
    "name": "Portafoglio", "description": "Value of the shares",
    "actions": [{"name": "set_quote", "description": "How many shares", "kind": "storage",
                 "op": "append", "collection": "quote",
                 "params": {"ticker": {"type": "string"}, "quote": {"type": "number"}}}],
    "reader": {"every": 720, "fields": {"totale": {"label": "Totale", "type": "number",
                                                   "unit": "€"}}},
    "summary": {"primary": "totale"},
}
SUM = ("def run(ctx):\n"
       "    return {'totale': sum(q['quote'] * 10 for q in ctx.data('quote'))}\n")


async def test_a_new_version_is_tried_on_the_real_data(personal: Path) -> None:
    """La prova di una versione nuova legge le quote vere: prima girava su una
    cartella vuota e rispondeva «0 €» o un valore preso da un campo vecchio."""
    ws = personal
    tool = AppTool()
    await tool.execute(action="draft", slug="portafoglio",
                       files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    await execute_action(ws, "portafoglio", "set_quote", {"ticker": "CSSPX", "quote": 28})
    live_quote = (ws / "apps" / "portafoglio" / "data" / "quote.jsonl").read_text()
    out = await tool.execute(action="edit", slug="portafoglio", path="run.py",
                             old="* 10", new="* 11")
    assert "Totale: 308€" in out, out
    assert "Check the values" in out and "Tell them" not in out
    assert (ws / "apps" / "portafoglio" / "data" / "quote.jsonl").read_text() == live_quote


async def test_a_first_real_reading_that_fails_is_said(personal: Path, monkeypatch) -> None:
    tool = AppTool()
    await tool.execute(action="draft", slug="portafoglio",
                       files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    import jenny.apps.reader as reader

    real = reader.run_reader

    async def live_fails(workspace, slug, **kw):
        state = await real(workspace, slug, **kw)
        if kw.get("app_dir") is None and state is not None:
            return {**state, "status": "error", "lastError": "RuntimeError: HTTP 429"}
        return state

    monkeypatch.setattr(reader, "run_reader", live_fails)
    out = await tool.execute(action="edit", slug="portafoglio", path="run.py",
                             old="* 10", new="* 12")
    assert "is published, but its first reading on the real data failed" in out, out
    assert "HTTP 429" in out and "is updated" not in out


async def test_run_shows_the_last_series(personal: Path) -> None:
    """`app run` di un lettore a serie: prima filtrava su parametri vuoti e
    rispondeva «no value yet» dopo un giro riuscito."""
    tool = AppTool()
    await tool.execute(action="draft", slug="waterbot",
                       files={"app.json": json.dumps(WATERBOT), "run.py": READER})
    out = await tool.execute(action="run", slug="waterbot")
    assert out.startswith("Read now — Moisture: 17%"), out


async def test_a_draft_says_what_is_wrong_instead_of_dropping_it(personal: Path) -> None:
    """Un host con lo schema veniva buttato in silenzio, e il lettore falliva
    solo girando («host 'x' is not in this reader's net list»)."""
    ws = personal
    manifest = {**PORTAFOGLIO, "reader": {**PORTAFOGLIO["reader"],
                                          "net": ["https://api.boerse-frankfurt.de/"]}}
    out = await AppTool().execute(action="draft", slug="portafoglio",
                                  files={"app.json": json.dumps(manifest), "run.py": SUM})
    assert "net takes host names only" in out and "is live" not in out, out
    # Un'app gia' installata con lo stesso manifest continua a caricarsi.
    live = ws / "apps" / "vecchia"
    live.mkdir(parents=True)
    (live / "app.json").write_text(json.dumps(manifest))
    (live / "run.py").write_text(SUM)
    assert not find_app(ws, "vecchia").broken


async def test_editing_a_stuck_reader_lets_it_run_again(personal: Path) -> None:
    from jenny.board import micro as mc

    tool = AppTool()
    await tool.execute(action="draft", slug="portafoglio",
                       files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    mc._stuck.update({"apps/apps/portafoglio", "apps/.drafts/portafoglio"})
    try:
        out = await tool.execute(action="edit", slug="portafoglio", path="run.py",
                                 old="* 10", new="* 13")
        assert "is updated" in out, out
        assert not ({"apps/apps/portafoglio", "apps/.drafts/portafoglio"} & mc._stuck)
    finally:
        mc._stuck.discard("apps/apps/portafoglio")
        mc._stuck.discard("apps/.drafts/portafoglio")


# ── le app le scrive l'app-builder, le pubblica l'agente principale ──────────


def _builder(origin: str | None = "project:piante"):
    from jenny.agent.tools.app_tool import AppBuildTool

    return AppBuildTool(origin_session_key=origin)


async def test_a_builder_tests_a_version_but_does_not_publish_it(ws: Path) -> None:
    """Il costruttore lavora per la chat da cui e' partito (l'origine), non per la
    sua sessione di subagent; la sua versione resta in bozza."""
    out = await _builder().execute(action="draft", slug="portafoglio",
                                   files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    assert "passed its checks" in out and "not live yet" in out and "Totale: 0€" in out, out
    assert find_app(ws, "portafoglio") is None
    assert (ws / "apps" / ".drafts" / "portafoglio" / "app.json").is_file()


async def test_the_main_agent_publishes_what_the_builder_tested(personal: Path) -> None:
    from jenny.agent.tools.app_tool import AppPublishTool

    ws = personal
    publisher = AppPublishTool()
    assert "app-builder writes and tests it first" in await publisher.execute(
        action="publish", slug="portafoglio")
    await _builder("unified:default").execute(
        action="draft", slug="portafoglio",
        files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    out = await publisher.execute(action="publish", slug="portafoglio")
    assert "`portafoglio` is live" in out, out
    # Ogni campo dell'ultima lettura, non solo quello della scheda.
    assert "[last reading: totale=0]" in out, out
    assert find_app(ws, "portafoglio") is not None
    assert not (ws / "apps" / ".drafts" / "portafoglio").exists()


async def test_a_version_that_failed_its_test_is_not_published(personal: Path) -> None:
    from jenny.agent.tools.app_tool import AppPublishTool

    broken = "def run(ctx):\n    raise RuntimeError('HTTP 429')\n"
    out = await _builder("unified:default").execute(
        action="draft", slug="portafoglio",
        files={"app.json": json.dumps(PORTAFOGLIO), "run.py": broken})
    assert "HTTP 429" in out and "not live" not in out, out
    out = await AppPublishTool().execute(action="publish", slug="portafoglio")
    assert "has not passed its test run" in out and "HTTP 429" in out, out


async def test_a_builder_from_a_scheduled_job_cannot_write(ws: Path) -> None:
    for origin in ("cron:job", None):
        out = await _builder(origin).execute(
            action="draft", slug="portafoglio", files={"app.json": json.dumps(PORTAFOGLIO)})
        assert out.startswith("Not from here"), out


async def test_set_changes_one_value_and_keeps_app_json_readable(ws: Path) -> None:
    b = _builder()
    await b.execute(action="draft", slug="portafoglio",
                    files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    out = await b.execute(action="set", slug="portafoglio", key="reader.every", value="1440")
    assert "passed its checks" in out, out
    text = (ws / "apps" / ".drafts" / "portafoglio" / "app.json").read_text()
    assert json.loads(text)["reader"]["every"] == 1440 and "\n  " in text
    out = await b.execute(action="set", slug="portafoglio", key="reader.net",
                          value='["https://api.example.com/"]')
    assert "net takes host names only" in out, out


async def test_an_abandoned_draft_is_not_the_next_builders_start(ws: Path) -> None:
    """Una bozza ferma da ore e' abbandonata: il costruttore dopo riparte pulito."""
    import os
    import time as _time

    stale = ws / "apps" / ".drafts" / "portafoglio"
    stale.mkdir(parents=True)
    (stale / "AGENT.md").write_text("old notes")
    old = _time.time() - 7 * 3600
    for p in (stale / "AGENT.md", stale):
        os.utime(p, (old, old))
    out = await _builder().execute(action="draft", slug="portafoglio",
                                   files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    assert "passed its checks" in out, out
    assert not (stale / "AGENT.md").exists()


async def test_a_second_builder_continues_the_draft_in_progress(ws: Path) -> None:
    """Il 07/10/2026 sul Titan: il secondo costruttore (la schermata da rifare)
    mandava solo ``app/index.html`` e cancellava ``app.json`` e ``run.py`` del
    primo; poi un ``assert`` arrivava al modello come «AssertionError:»."""
    await _builder().execute(action="draft", slug="portafoglio",
                             files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    out = await _builder().execute(action="draft", slug="portafoglio",
                                   files={"app/index.html": "<p>totale</p>"})
    assert "passed its checks" in out, out
    d = ws / "apps" / ".drafts" / "portafoglio"
    assert (d / "app.json").is_file() and (d / "run.py").is_file()


async def test_a_new_app_starts_with_its_manifest(ws: Path) -> None:
    out = await _builder().execute(action="draft", slug="volo",
                                   files={"app/index.html": "<p>volo</p>"})
    assert "send app.json in this call" in out, out
    assert not (ws / "apps" / ".drafts" / "volo").exists()
    # Una bozza rimasta senza manifest lo dice, invece di un ``assert``.
    d = ws / "apps" / ".drafts" / "volo"
    (d / "app").mkdir(parents=True)
    (d / "app" / "index.html").write_text("<p>volo</p>")
    assert "has no app.json" in await _builder().execute(
        action="edit", slug="volo", path="app/index.html", old="volo", new="Volo")
    assert "not an app yet" in await _builder().execute(action="show", slug="volo")


async def test_show_shows_the_version_being_built(personal: Path) -> None:
    """Chi ha appena cambiato una bozza la rivede con ``show``, con l'esito della prova."""
    from jenny.agent.tools.app_tool import AppPublishTool

    await AppTool().execute(action="draft", slug="portafoglio",
                            files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    b = _builder("unified:default")
    await b.execute(action="set", slug="portafoglio", key="reader.every", value="1440")
    out = await b.execute(action="show", slug="portafoglio")
    assert out.startswith("`portafoglio` — a new version of a live app, tested"), out
    assert '"every": 1440' in out and "; the main agent publishes it" in out, out
    assert "The live version still shows: Totale: 0€" in out, out
    out = await AppPublishTool().execute(action="show", slug="portafoglio")
    assert "; action=publish makes it live" in out, out


async def test_only_failed_tries_count_and_the_refusal_says_when(ws: Path) -> None:
    b = _builder()
    await b.execute(action="draft", slug="portafoglio",
                    files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    for every in range(60, 60 + 8):
        out = await b.execute(action="set", slug="portafoglio", key="reader.every",
                              value=str(every))
        assert "passed its checks" in out, out
    broken = "def run(ctx):\n    raise RuntimeError('HTTP 429')\n"
    for _ in range(6):
        assert "HTTP 429" in await b.execute(action="draft", slug="portafoglio",
                                              files={"run.py": broken})
    out = await _builder().execute(action="draft", slug="portafoglio", files={"run.py": SUM})
    assert out.startswith("Six failed tries") and "accepted after" in out, out


async def test_a_published_app_brings_its_tools_into_the_same_turn(personal: Path) -> None:
    from jenny.agent.tools.app_tool import AppPublishTool

    ws = personal
    registry = ToolRegistry()
    syncer = AppToolsSyncer(ws)

    async def sync() -> None:
        syncer.sync(registry)

    publisher = AppPublishTool()
    publisher.on_apps_changed = sync
    await _builder("unified:default").execute(
        action="draft", slug="portafoglio",
        files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    assert registry.get("portafoglio_set_quote") is None
    await publisher.execute(action="publish", slug="portafoglio")
    assert registry.get("portafoglio_set_quote") is not None


def test_startup_publishes_only_migrated_drafts(ws: Path) -> None:
    from jenny.apps import drafts

    for slug in ("costruttore", "migrata"):
        d = drafts.draft_dir(ws, slug)
        d.mkdir(parents=True)
        (d / "app.json").write_text(json.dumps(SPESA))
    (drafts.draft_dir(ws, "migrata") / drafts.MIGRATED_MARKER).write_text("")
    assert drafts.promote_valid_drafts(ws) == ["migrata"]
    assert drafts.draft_dir(ws, "costruttore").exists()


def test_each_registry_gets_its_face_of_the_tool(ws: Path) -> None:
    from types import SimpleNamespace

    from jenny.agent.tools.app_tool import AppBuildTool, AppPublishTool
    from jenny.agent.tools.loader import ToolLoader

    loader = ToolLoader()
    faces = {}
    for scope in ("core", "orchestrator", "apps"):
        registry = ToolRegistry()
        ctx = SimpleNamespace(workspace=str(ws), config=None, registry=registry)
        loader.load(ctx, registry, scope=scope, allow={"app"} if scope != "core" else None)
        faces[scope] = type(registry.get("app"))
    assert faces["core"] is AppTool
    assert faces["orchestrator"] is AppPublishTool
    assert faces["apps"] is AppBuildTool


# ── la schermata: chi la cambia la legge dalla bozza, e nessuno dice che va ──


async def test_show_reads_one_file_of_the_version_in_progress(personal: Path) -> None:
    """Il 08/10/2026 un costruttore leggeva ``app/index.html`` dall'app viva, gia'
    vecchia rispetto alla sua bozza, e i suoi ``edit`` non trovavano il testo."""
    await AppTool().execute(action="draft", slug="portafoglio", files={
        "app.json": json.dumps(PORTAFOGLIO), "run.py": SUM, "app/index.html": "<p>vecchia</p>"})
    b = _builder("unified:default")
    await b.execute(action="edit", slug="portafoglio", path="app/index.html",
                    old="vecchia", new="nuova")
    out = await b.execute(action="show", slug="portafoglio", path="app/index.html")
    assert "(the version in progress)" in out and "1| <p>nuova</p>" in out, out
    assert "of the version in progress; read one with action=show path=" in await b.execute(
        action="show", slug="portafoglio")
    for bad in ("../config.json", "/etc/passwd", "data/quote.jsonl"):
        assert "an app's files are" in await b.execute(action="show", slug="portafoglio",
                                                          path=bad)
    out = await b.execute(action="edit", slug="portafoglio", path="app/index.html",
                          old="vecchia", new="x")
    assert "occurs 0 times" in out and "action=show path=app/index.html" in out, out


async def test_publish_says_when_the_screen_changed(personal: Path) -> None:
    from jenny.agent.tools.app_tool import AppPublishTool

    await AppTool().execute(action="draft", slug="portafoglio", files={
        "app.json": json.dumps(PORTAFOGLIO), "run.py": SUM, "app/index.html": "<p>uno</p>"})
    b = _builder("unified:default")
    publisher = AppPublishTool()
    await b.execute(action="set", slug="portafoglio", key="reader.every", value="1440")
    out = await publisher.execute(action="publish", slug="portafoglio")
    assert "is updated" in out and "screen changed" not in out, out
    await b.execute(action="edit", slug="portafoglio", path="app/index.html",
                    old="uno", new="due")
    out = await publisher.execute(action="publish", slug="portafoglio")
    assert "Its screen changed, and nobody has run it" in out, out


async def test_undoing_the_builder_puts_the_published_app_back(personal: Path) -> None:
    """La bozza è dell'app-builder, la pubblica Jenny: annullare l'aiutante rimette
    anche l'app viva (``agent.subagent_journal``). Prima l'annulla diceva «rimesso»
    — la bozza non c'era più — e l'app restava cambiata."""
    from jenny.agent.subagent_journal import journal_for
    from jenny.agent.tools.app_tool import AppBuildTool, AppPublishTool
    from jenny.security.subagent_policy import (
        SubagentRunPolicy,
        enter_subagent_policy,
        settle_writes,
    )

    ws = personal
    await _builder("unified:default").execute(
        action="draft", slug="portafoglio",
        files={"app.json": json.dumps(PORTAFOGLIO), "run.py": SUM})
    await AppPublishTool().execute(action="publish", slug="portafoglio")
    live = ws / "apps" / "portafoglio"
    before = {p.relative_to(live).as_posix(): p.read_bytes()
              for p in live.rglob("*") if p.is_file()}

    policy = SubagentRunPolicy.for_run("t9", ws)
    policy.journal = journal_for(ws, "lin-b")
    policy.journal.begin("t9", 1)
    builder = AppBuildTool(origin_session_key="unified:default", lineage_id="lin-b")
    with enter_subagent_policy(policy):
        out = await builder.execute(action="set", slug="portafoglio", key="reader.every",
                                    value="1440")
        settle_writes("app")
    assert "passed its checks" in out, out
    out = await AppPublishTool().execute(action="publish", slug="portafoglio")
    assert "`portafoglio` is updated" in out, out
    assert json.loads((live / "app.json").read_text())["reader"]["every"] == 1440

    report = policy.journal.undo(by="jenny")
    assert report.kept == [], report.kept
    after = {p.relative_to(live).as_posix(): p.read_bytes()
             for p in live.rglob("*") if p.is_file()}
    assert after == before
    assert not (ws / "apps" / ".drafts" / "portafoglio").exists()
