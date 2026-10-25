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
from jenny.board.store import mutate_board, read_board, read_card_summary, read_reminders
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


async def test_show_names_the_pages_that_are_gone_and_remove_pages_takes_them_off(
    notebook_chat: Path, announced,
) -> None:
    """Una pagina cancellata resta sulla scheda finché qualcuno la toglie: ``show``
    la deve dire, e l'editore la deve poter togliere (il tool non sapeva farlo)."""
    root = notebook_chat
    (root / "wiki" / "entities" / "Fico.md").unlink()
    shown = await BoardTool().execute(action="show")
    assert "pages: entities/Ginkgo.md, entities/Prunus.md" in shown, shown
    assert "MISSING (no such file): entities/Fico.md" in shown
    assert "1 page(s) on cards no longer exist" in shown and "remove_pages" in shown

    out = await BoardTool().execute(action="write_drawer", card="nuove", brief="Due piante.",
                                    sections=[{"title": "Identità", "text": "Ginkgo e Prunus.",
                                               "sources": ["entities/Ginkgo.md"]}])
    assert "1 sections" in out, out
    out = await BoardTool().execute(action="remove_pages", pages=["entities/Fico.md"])
    assert "`nuove`: entities/Fico.md" in out and "Drawers to rewrite" not in out, out
    assert model.card_by_id(read_board(root), "nuove")["pages"] == [
        "entities/Ginkgo.md", "entities/Prunus.md"]
    assert "MISSING" not in await BoardTool().execute(action="show")
    assert announced == ["piante"] * 2

    out = await BoardTool().execute(action="remove_pages", pages=["entities/Prunus.md"],
                                    card="nuove")
    assert "Drawers to rewrite" in out and "`nuove`" in out, "una pagina vera cambia il cassetto"
    assert (root / "wiki" / "entities" / "Prunus.md").exists()
    out = await BoardTool().execute(action="remove_pages", pages=["entities/Prunus.md"])
    assert out.startswith("None of those pages was on any card"), out
    out = await BoardTool().execute(action="remove_pages", pages=["entities/Ginkgo.md"],
                                    card="Nuove piante del balcone")
    assert "`nuove`: entities/Ginkgo.md" in out
    out = await BoardTool().execute(action="remove_pages", pages=["entities/Ginkgo.md"],
                                    card="nuove")
    assert out.startswith("None of those pages was on that card"), out


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

        def list_jobs(self, include_disabled: bool = False) -> list:
            from types import SimpleNamespace

            link = {"notebook": notebook_chat.name, "card": "nuove", "section": "@auto"}
            return [SimpleNamespace(id="j1", payload=SimpleNamespace(board=link))]

    cron = _Cron()
    tool = BoardTool(cron=cron)
    out = await tool.execute(action="remove_card", card="nuove")
    assert "1 cron job(s) — is paused until restore_card" in out, out
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


async def test_the_board_shows_a_photo_with_its_path_and_note(notebook_chat: Path) -> None:
    mutate_board(notebook_chat, lambda b: model.card_by_id(b, "nuove")["userSections"].extend([
        {"id": "s-photo", "kind": "photo", "created": "2026-10-14T17:48:43+02:00",
         "path": "raw/photos/nuove-1.jpg", "caption": "Il fico ha messo due foglie"},
        {"id": "s-photo-2", "kind": "photo", "created": "2026-10-14T18:00:00+02:00",
         "path": "raw/photos/nuove-2.jpg", "caption": ""},
    ]))
    shown = await BoardTool().execute(action="show")
    assert "`s-photo` photo raw/photos/nuove-1.jpg «Il fico ha messo due foglie»" in shown
    # Senza nota, il percorso e basta.
    assert "`s-photo-2` photo raw/photos/nuove-2.jpg" in shown
    assert "nuove-2.jpg «" not in shown


async def test_show_lists_the_automations_of_cards_and_of_the_notebook(notebook_chat: Path,
                                                                       tmp_path: Path,
                                                                       monkeypatch) -> None:
    """L'editore vede anche i cron: quelli di una scheda e quelli di tutto il quaderno."""
    import json

    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "ab12cd34", "name": "Meteo", "enabled": True,
         "payload": {"board": {"notebook": notebook_chat.name, "card": "nuove",
                               "section": "@auto"}}},
        {"id": "ef56ab78", "name": "Gelo", "pausedAtMs": 1, "enabled": False,
         "payload": {"board": {"notebook": notebook_chat.name, "card": "@notebook",
                               "section": "@auto"}}},
    ]}), encoding="utf-8")
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: tmp_path)
    out = await BoardTool().execute(action="show")
    assert "- `ab12cd34` on `nuove`: Meteo" in out
    assert "- `ef56ab78` on the whole notebook: Gelo (paused)" in out


async def test_the_editor_can_add_a_reminder_for_the_whole_notebook(notebook_chat: Path,
                                                                     announced) -> None:
    out = await BoardTool().execute(action="add_reminder", text="Porta dentro le piante",
                                    date="2026-12-01")
    assert "for the whole notebook" in out
    rem = read_reminders(notebook_chat)["reminders"][-1]
    assert rem["card"] == "" and rem["by"] == "user"
    assert "on the whole notebook: Porta dentro le piante" in await BoardTool().execute(
        action="show")


async def test_update_reminder_sets_text_date_and_why(notebook_chat: Path, announced) -> None:
    """Il contratto del 24/10/2026: un'attesa corretta dall'editore porta anche la
    pagina che la spiega, e un ``why`` vuoto la stacca."""
    root = notebook_chat
    tool = BoardTool()
    added = await tool.execute(action="add_reminder", card="nuove", text="Rinvaso",
                               date="2099-03-01")
    rid = added.split("`")[1]
    out = await tool.execute(action="update_reminder", id=rid, why="entities/Fico.md",
                             date="2099-04-01")
    assert out == f"Reminder `{rid}` updated; why: entities/Fico.md."
    rem = read_reminders(root)["reminders"][0]
    assert rem["why"] == "entities/Fico.md" and rem["when"]["date"] == "2099-04-01"
    out = await tool.execute(action="update_reminder", id=rid, why="entities/Nessuno.md",
                             text="Non deve cambiare")
    assert out.startswith("Not done: no such page under wiki/"), out
    assert read_reminders(root)["reminders"][0]["text"] == "Rinvaso"
    # Un ``why`` vuoto non stacca niente: per togliere la pagina c'è clear_why.
    out = await tool.execute(action="update_reminder", id=rid, why="", text="Rinvaso grande")
    assert out == f"Reminder `{rid}` updated."
    rem = read_reminders(root)["reminders"][0]
    assert rem["why"] == "entities/Fico.md" and rem["text"] == "Rinvaso grande"
    out = await tool.execute(action="update_reminder", id=rid, clear_why=True)
    assert "no longer points to a page" in out
    assert read_reminders(root)["reminders"][0]["why"] == ""
    assert (await tool.execute(action="update_reminder", id=rid, why="")).startswith(
        "Not done: give")


async def test_update_reminder_writes_once(notebook_chat: Path, announced, monkeypatch) -> None:
    """Testo, data e pagina in una scrittura sola: due scritture lasciavano
    l'attesa a metà se la seconda falliva."""
    import jenny.board.edit as edit_mod

    tool = BoardTool()
    rid = (await tool.execute(action="add_reminder", card="nuove", text="Rinvaso",
                              date="2099-03-01")).split("`")[1]
    calls: list[int] = []
    real = edit_mod.mutate_reminders

    def counting(root, apply, **kw):
        calls.append(1)
        return real(root, apply, **kw)

    monkeypatch.setattr(edit_mod, "mutate_reminders", counting)
    await tool.execute(action="update_reminder", id=rid, text="Nuovo", date="2099-05-01",
                       why="entities/Fico.md")
    assert calls == [1]
    rem = read_reminders(notebook_chat)["reminders"][0]
    assert (rem["text"], rem["when"]["date"], rem["why"]) == (
        "Nuovo", "2099-05-01", "entities/Fico.md")


# ── Le schede orfane (25/10/2026) ────────────────────────────────────────────


async def test_a_card_with_no_pages_no_drawer_and_no_reminders_goes(notebook_chat: Path,
                                                                    announced) -> None:
    """L'orfano «dentro board.json»: la scheda c'è, il suo file in ``board/cards/``
    no, e non ha pagine né attese. Va nel cestino come le altre, e torna."""
    root = notebook_chat

    def apply(board):
        board["cards"].append({"id": "vuota", "title": "Vuota", "pages": [],
                               "pos": {"x": 300, "y": 6, "by": "jenny"}})

    mutate_board(root, apply)
    assert read_card_summary(root, "vuota") is None
    tool = BoardTool()
    out = await tool.execute(action="remove_card", card="vuota")
    assert out.startswith("«Vuota» is in the trash"), out
    board = read_board(root)
    assert model.card_by_id(board, "vuota") is None
    assert model.removed_card_ids(board) == {"vuota"}
    assert announced == ["piante"]
    assert "back on the board, with no pages" in await tool.execute(action="restore_card",
                                                                    card="vuota")


async def test_a_drawer_with_no_card_is_shown_and_removed(notebook_chat: Path,
                                                          announced) -> None:
    """L'orfano «su disco» della prova 1: ``board/cards/<id>.json`` senza la sua
    scheda. ``remove_card`` rispondeva «card not found» e i tool file non
    scrivono in ``board/``: non lo toglieva nessuno."""
    from jenny.board.store import write_card_summary

    root = notebook_chat
    write_card_summary(root, "acqua-e-ciclo", {"card": "acqua-e-ciclo", "brief": "Acqua."})
    tool = BoardTool()
    shown = await tool.execute(action="show")
    assert "Drawers with no card (remove_card deletes them): `acqua-e-ciclo`" in shown

    out = await tool.execute(action="remove_card", card="Acqua e ciclo")  # anche per titolo
    assert out == ("No card `acqua-e-ciclo` on the board: its leftover drawer "
                   "board/cards/acqua-e-ciclo.json is deleted. Nothing to restore.")
    assert not (root / "board" / "cards" / "acqua-e-ciclo.json").exists()
    assert announced == ["piante"]
    assert "Drawers with no card" not in await tool.execute(action="show")
    # La bacheca non cambia: nessuna voce di cestino per un file derivato.
    assert read_board(root)["removedCards"] == []
    out = await tool.execute(action="remove_card", card="acqua-e-ciclo")
    assert out.startswith("Not done: card not found: acqua-e-ciclo"), out


async def test_drawers_that_are_not_orphans_stay(notebook_chat: Path) -> None:
    """Non orfani: il cassetto di una scheda nel cestino (serve all'Annulla) e
    quelli di una prima bacheca ancora nella busta (la passata li scrive prima
    della risposta)."""
    from jenny.board.store import write_card_summary

    root = notebook_chat
    write_card_summary(root, "nuove", {"card": "nuove", "brief": "Tre piante."})
    write_card_summary(root, "proposta", {"card": "proposta", "brief": "Nella busta."})

    def propose(board):
        board["suggestions"].append({
            "id": "sg-board", "kind": "initial_board", "subject": "", "text": "Una bacheca.",
            "created": model.now_iso(), "skeleton": ["Identità"],
            "cards": [{"id": "proposta", "title": "Proposta", "glyph": "", "pages": []}],
        })

    mutate_board(root, propose)
    tool = BoardTool()
    await tool.execute(action="remove_card", card="nuove")
    assert "Drawers with no card" not in await tool.execute(action="show")
    for ref in ("nuove", "proposta"):
        out = await tool.execute(action="remove_card", card=ref)
        assert out.startswith("Not done: card not found"), out
    assert read_card_summary(root, "nuove") is not None
    assert read_card_summary(root, "proposta") is not None
