"""Il tool ``board`` dell'editore dei quaderni.

Sta in uno scope suo (``notebook``) e solo lì: né la chat né ``operator`` lo
ereditano. Lavora per la chat di un quaderno, mai in sola lettura, e ogni
scrittura avvisa la bacheca aperta. Il giro che conta è quello del 09/10/2026:
una scheda che raccoglie più piante diventa una scheda per pianta, coi cassetti
scritti.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.board import BoardTool
from jenny.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.registry import ToolRegistry
from jenny.board import model, notify
from jenny.board.store import mutate_board, read_board, read_card_summary
from jenny.config.schema import ToolsConfig
from jenny.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace" / "wikis" / "piante"
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    for name in ("Ginkgo", "Fico", "Prunus"):
        (root / "wiki" / "entities" / f"{name}.md").write_text(f"# {name}\n\nArrivato l'8.\n")

    def apply(board):
        board["skeleton"] = ["Identità", "Vaso"]
        board["cards"].append({
            "id": "nuove", "title": "Nuove piante del balcone",
            "pages": ["entities/Ginkgo.md", "entities/Fico.md", "entities/Prunus.md"],
            "pos": {"x": 5, "y": 6, "by": "jenny"},
        })

    mutate_board(root, apply)
    return root


@pytest.fixture
def notebook_chat(root: Path):
    scope = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    req = bind_request_context(RequestContext(channel="websocket", chat_id="project:piante",
                                              session_key="project:piante"))
    try:
        yield root
    finally:
        reset_request_context(req)
        reset_workspace_scope(scope)


@pytest.fixture
def announced(monkeypatch):
    seen: list[str] = []

    async def collect(name: str) -> None:
        seen.append(name)

    notify.set_notifier(collect)
    yield seen
    notify.set_notifier(None)


@pytest.mark.parametrize("scope", ["core", "orchestrator", "subagent", "apps", "remote"])
def test_no_other_agent_inherits_the_board_tool(scope: str, tmp_path: Path) -> None:
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path), file_states=FileStates())
    assert "board" not in ToolLoader().load(ctx, ToolRegistry(), scope=scope)
    assert "board" in ToolLoader().load(ctx, ToolRegistry(), scope="notebook")


async def test_one_card_per_plant_with_its_drawer(notebook_chat: Path, announced) -> None:
    root = notebook_chat
    tool = BoardTool()
    shown = await tool.execute(action="show")
    assert "`nuove` «Nuove piante del balcone»" in shown and "never written" in shown

    out = await tool.execute(action="add_card", title="Ginkgo", pages=["entities/Ginkgo.md"],
                             glyph="tree", near="nuove")
    assert "Drawers to rewrite" in out and "`ginkgo`" in out and "`nuove`" in out
    out = await tool.execute(action="remove_card", card="Nuove piante del balcone")
    assert "in the trash" in out
    out = await tool.execute(action="write_drawer", card="ginkgo", brief="Maschio, innestato.",
                             sections=[{"title": "Identità", "text": "Ginkgo maschio.",
                                        "sources": ["entities/Ginkgo.md"]}])
    assert "1 sections" in out, out

    board = read_board(root)
    ginkgo = model.card_by_id(board, "ginkgo")
    assert ginkgo["titleBy"] == "user" and ginkgo["pages"] == ["entities/Ginkgo.md"]
    assert [r["card"]["id"] for r in board["removedCards"]] == ["nuove"]
    assert read_card_summary(root, "ginkgo")["brief"] == "Maschio, innestato."
    assert "up to date" in await tool.execute(action="show")
    assert announced == ["piante"] * 3, "ogni scrittura ridisegna la bacheca aperta"


async def test_a_drawer_cites_only_its_own_pages(notebook_chat: Path) -> None:
    out = await BoardTool().execute(action="write_drawer", card="nuove", brief="x", sections=[
        {"title": "Identità", "text": "y", "sources": ["entities/Altro.md"]}])
    assert out.startswith("Drawer not written") and read_card_summary(notebook_chat, "nuove") is None


async def test_only_for_a_notebook_chat(root: Path) -> None:
    req = bind_request_context(RequestContext(channel="websocket", chat_id="unified:default",
                                              session_key="unified:default"))
    try:
        out = await BoardTool().execute(action="remove_card", card="nuove")
    finally:
        reset_request_context(req)
    assert out.startswith("Not from here")
    assert model.card_by_id(read_board(root), "nuove") is not None


async def test_not_in_a_read_only_turn(notebook_chat: Path) -> None:
    scope = bind_workspace_scope(
        build_workspace_scope(notebook_chat, "restricted").without_write_access())
    try:
        out = await BoardTool().execute(action="remove_card", card="nuove")
        shown = await BoardTool().execute(action="show")
    finally:
        reset_workspace_scope(scope)
    assert "read-only" in out.lower() or "read only" in out.lower(), out
    assert "Nuove piante" in shown
    assert model.card_by_id(read_board(notebook_chat), "nuove") is not None


async def test_the_editor_works_for_the_chat_it_came_from(root: Path) -> None:
    """Dentro il giro dell'editore la sessione di richiesta e' la sua: conta quella d'origine."""
    scope = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    req = bind_request_context(RequestContext(channel="internal", chat_id="x",
                                              session_key="subagent:abc"))
    try:
        out = await BoardTool(origin_session_key="project:piante").execute(
            action="rename_card", card="nuove", title="Alberi")
    finally:
        reset_request_context(req)
        reset_workspace_scope(scope)
    assert out == "Renamed."
    assert model.card_by_id(read_board(root), "nuove")["title"] == "Alberi"


async def test_a_card_restored_without_pages_says_so(notebook_chat: Path) -> None:
    """Sull'emulatore l'editore ha girato a vuoto quindici chiamate cercando di
    scrivere il cassetto di una scheda tornata vuota: lo strumento non lo diceva."""
    tool = BoardTool()
    await tool.execute(action="move_pages", card="nuove", pages=["entities/Ginkgo.md"])
    await tool.execute(action="add_card", title="Altre", pages=["entities/Fico.md",
                                                                 "entities/Prunus.md"])
    await tool.execute(action="move_pages", card="altre", pages=["entities/Ginkgo.md"])
    await tool.execute(action="remove_card", card="nuove")
    out = await tool.execute(action="restore_card", card="nuove")
    assert "with no pages" in out and "move_pages" in out
    assert "none (no pages to write it from)" in await tool.execute(action="show")


async def test_the_editor_is_told_what_a_removal_paused(notebook_chat: Path) -> None:
    """Sull'emulatore l'editore ha letto ``cron/jobs.json`` a mano per saperlo."""
    import asyncio

    class _Cron:
        def __init__(self) -> None:
            self.paused: dict[str, bool] = {}

        def set_paused(self, job_id: str, paused: bool) -> str:
            asyncio.get_running_loop()  # come il servizio vero: solo sul loop
            self.paused[job_id] = paused
            return "paused" if paused else "resumed"

        def get_job(self, job_id: str) -> object:
            return object()

    mutate_board(notebook_chat, lambda b: model.card_by_id(b, "nuove")["userSections"].append(
        {"id": "s-cron", "kind": "cron", "job": "j1", "name": "meteo"}))
    cron = _Cron()
    tool = BoardTool(cron=cron)
    out = await tool.execute(action="remove_card", card="nuove")
    assert "1 cron job(s), 0 micro app(s) — is paused until restore_card" in out, out
    out = await tool.execute(action="restore_card", card="nuove")
    assert "is running again" in out and cron.paused == {"j1": False}


def test_the_editor_built_by_the_manager_has_the_cron_and_the_time_zone(tmp_path: Path) -> None:
    """Il ctx dei subagent non portava il servizio cron: sull'emulatore l'editore
    toglieva Prunus e il suo promemoria restava armato (09/10/2026)."""
    from jenny.agent.agent_types import get_agent_type
    from tests.agent.test_subagent_send import _manager

    cron = object()
    sm = _manager(tmp_path, cron_service=cron, timezone="Europe/Rome")
    registry = sm._build_tools(agent_type=get_agent_type("notebook-editor"),
                               origin_session_key="project:piante", lineage_id="lin1")
    tool = registry.get("board")
    assert tool is not None and tool._cron is cron and tool._timezone == "Europe/Rome"
    assert registry.get("cron") is None, "il tool cron resta fuori dai subagent"
