"""Il tool ``micro_app``: la prova prima del salvataggio, la riparazione, i recinti."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from jenny.agent.tools.micro_app import MicroAppTool
from jenny.board import micro as mc
from jenny.board.store import mutate_board, read_board
from jenny.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)

FIELDS = {"moisture": {"label": "Umidità", "unit": "%", "type": "number"},
          "online": {"label": "Sensore", "type": "bool"}}
CODE = "def run(ctx):\n    return {'moisture': 38, 'online': True, '_source': 'hub'}\n"


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch):
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero giapponese", "pages": [], "pos": {"x": 1, "y": 1}}))
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: ws)
    scope = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    req = bind_request_context(RequestContext(channel="websocket", chat_id="project:piante",
                                              session_key="project:piante"))
    try:
        yield ws, root
    finally:
        reset_request_context(req)
        reset_workspace_scope(scope)


def _section(root: Path) -> dict:
    return read_board(root)["cards"][0]["userSections"][0]


async def test_a_draft_runs_once_and_waits_in_the_drawer(notebook) -> None:
    ws, root = notebook
    out = await MicroAppTool().execute(action="draft", card="acero giapponese", title="Acerello",
                                       code=CODE, fields=FIELDS, primary="moisture", every=30)
    assert "Trial run OK" in out and "Umidità 38%" in out and "source: hub" in out
    section = _section(root)
    assert section["kind"] == "micro" and section["pending"] is True
    assert section["by"] == "jenny-on-request"
    assert not mc.is_approved(ws, root, mc.load_micro(root, section["micro"], draft=True))
    # [Tienilo]: viva e approvata.
    mc.keep(ws, root, section["micro"])
    assert _section(root)["pending"] is False
    assert mc.is_approved(ws, root, mc.load_micro(root, section["micro"]))


async def test_a_failed_trial_puts_nothing_in_the_drawer(notebook) -> None:
    _ws, root = notebook
    out = await MicroAppTool().execute(
        action="draft", card="acero", title="Acerello", fields=FIELDS,
        code="def run(ctx):\n    return {'moisture': 38}\n")
    assert "trial run failed" in out and "did not return `online`" in out
    assert read_board(root)["cards"][0]["userSections"] == []


async def test_a_fix_that_shows_the_same_runs_at_once(notebook) -> None:
    ws, root = notebook
    tool = MicroAppTool()
    await tool.execute(action="draft", card="acero", title="Acerello", code=CODE, fields=FIELDS)
    mid = _section(root)["micro"]
    mc.keep(ws, root, mid)
    out = await tool.execute(action="update", id=mid, code=CODE.replace("38", "40"))
    assert "Fixed and running again" in out
    assert _section(root)["pending"] is False
    live = mc.load_micro(root, mid)
    assert "40" in live.code and mc.is_approved(ws, root, live)
    assert any(r["kind"] == "micro_fixed" for r in read_board(root)["since"])


async def test_a_change_to_what_the_user_sees_goes_back_to_trial(notebook) -> None:
    ws, root = notebook
    tool = MicroAppTool()
    await tool.execute(action="draft", card="acero", title="Acerello", code=CODE, fields=FIELDS)
    mid = _section(root)["micro"]
    mc.keep(ws, root, mid)
    fields = {**FIELDS, "water": {"label": "Riserva", "type": "bool"}}
    code = "def run(ctx):\n    return {'moisture': 38, 'online': True, 'water': True}\n"
    out = await tool.execute(action="update", id=mid, code=code, fields=fields)
    assert "Trial run OK" in out
    assert _section(root)["pending"] is True
    # La versione viva, approvata, resta quella di prima finche' l'utente non tiene la nuova.
    assert "water" not in mc.load_micro(root, mid).spec["fields"]
    assert mc.is_approved(ws, root, mc.load_micro(root, mid))


async def test_not_outside_a_notebook(notebook) -> None:
    req = bind_request_context(RequestContext(channel="websocket", chat_id="default",
                                              session_key="unified:default"))
    try:
        out = await MicroAppTool().execute(action="draft", card="acero", code=CODE, fields=FIELDS)
    finally:
        reset_request_context(req)
    assert out.startswith("Not here")


async def test_three_tries_and_it_stops(notebook) -> None:
    tool = MicroAppTool()
    bad = "def run(ctx):\n    raise KeyError('soil')\n"
    for _ in range(3):
        out = await tool.execute(action="draft", card="acero", id="m-acerello", title="Acerello",
                                 code=bad, fields=FIELDS)
    assert "KeyError" in out
    out = await tool.execute(action="draft", card="acero", id="m-acerello", code=bad,
                             fields=FIELDS)
    assert out.startswith("Three tries")
