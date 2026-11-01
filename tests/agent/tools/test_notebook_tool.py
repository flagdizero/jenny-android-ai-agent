"""Il tool ``notebook``: due facce con lo stesso nome, e ``remove_pages``.

Jenny ha le tre letture (``check``, ``map``, ``read_many``); l'editore dei
quaderni anche ``remove_pages``. Gli scope delle due classi devono restare
disgiunti: il loader rifiuta due tool con lo stesso nome nello stesso registro,
e l'editore carica ``subagent`` + ``notebook``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
from pathlib import Path

import pytest

from jenny.agent.agent_types import AGENT_TYPES
from jenny.agent.hook import ToolResultHookContext
from jenny.agent.loop_plan import _counts_as_read
from jenny.agent.subagent import split_allow_by_scope
from jenny.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.notebook import NotebookEditTool, NotebookReadTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.board import notify
from jenny.board.store import (
    mutate_board,
    read_board,
    write_card_summary,
)
from jenny.config.schema import ToolsConfig
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from jenny.security.workspace_policy import BoardWriteError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace" / "wikis" / "piante"
    _write(root / "wiki" / "index.md", "# Mappa\n\n- [[acero]]\n- [[fico]]\n- [[pino]]\n")
    _write(root / "wiki" / "acero.md", "---\nstate: open\n---\n# Acero\n\nSubstrato acido.\n")
    _write(root / "wiki" / "fico.md", "---\nstate: open\n---\n# Fico\n\nVedi [[acero]].\n")
    _write(root / "wiki" / "pino.md", "---\nstate: open\n---\n# Pino\n")

    def board(b):
        b["skeleton"] = ["Identità"]
        b["cards"].append({"id": "alberi", "title": "Alberi",
                           "pages": ["acero.md", "fico.md"], "pos": {"x": 1, "y": 1}})

    mutate_board(root, board)
    write_card_summary(root, "alberi", {"card": "alberi", "fingerprint": "vecchia",
                                        "sections": [{"title": "Identità", "text": "Due alberi.",
                                                      "sources": ["acero.md", "fico.md"]}]})

    return root


@pytest.fixture
async def cron(root: Path, monkeypatch):
    """Lo scheduler, con un promemoria dell'albero il cui ``why`` è ``acero.md``."""
    from jenny.board.cron_link import add_notebook_job
    from jenny.cron.dates import date_schedule
    from jenny.runtime.context import get_runtime_context
    from tests.support.cron import running_cron

    workspace = root.parents[1]
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", workspace)
    async with running_cron(workspace) as service:
        service.test_rinvaso = add_notebook_job(
            service, "piante", "alberi", name="Rinvaso", message="Rinvaso",
            schedule=date_schedule("2099-03-01", "Europe/Rome"), why="acero.md").id
        yield service


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
def announced():
    seen: list[str] = []

    async def collect(name: str) -> None:
        seen.append(name)

    notify.set_notifier(collect)
    yield seen
    notify.set_notifier(None)


# ── Le due facce ─────────────────────────────────────────────────────────────


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(config=ToolsConfig(), workspace=str(tmp_path), file_states=FileStates())


def _registries(tmp_path: Path) -> dict[str, ToolRegistry]:
    """Ogni registro vero che può avere ``notebook``: Jenny nei due modi, e
    l'editore (lo stesso registro serve anche la pianificazione, in sola lettura)."""
    out: dict[str, ToolRegistry] = {}
    for scope in ("orchestrator", "core"):
        registry = ToolRegistry()
        ToolLoader().load(_ctx(tmp_path), registry, scope=scope)
        out[scope] = registry
    registry, loader = ToolRegistry(), ToolLoader()
    for scope, allow in split_allow_by_scope(loader, AGENT_TYPES["notebook-editor"]).items():
        loader.load(_ctx(tmp_path), registry, scope=scope, allow=allow)
    out["notebook-editor"] = registry
    return out


def test_every_real_registry_has_exactly_one_notebook_face(tmp_path: Path) -> None:
    faces = {name: type(reg.get("notebook")) for name, reg in _registries(tmp_path).items()}
    assert faces == {"orchestrator": NotebookReadTool, "core": NotebookReadTool,
                     "notebook-editor": NotebookEditTool}
    assert not NotebookReadTool._scopes & NotebookEditTool._scopes


@pytest.mark.parametrize("scope", ["subagent", "apps", "remote"])
def test_no_other_scope_gets_it(scope: str, tmp_path: Path) -> None:
    assert "notebook" not in ToolLoader().load(_ctx(tmp_path), ToolRegistry(), scope=scope)


def test_jenny_has_no_remove_pages(notebook_chat: Path) -> None:
    tool = NotebookReadTool()
    assert tool.read_only and not NotebookEditTool().read_only
    assert "remove_pages" not in tool.parameters["properties"]["action"]["enum"]
    assert "pages" not in tool.parameters["properties"]
    assert "remove_pages" in NotebookEditTool().parameters["properties"]["action"]["enum"]


async def test_jenny_cannot_remove_pages(notebook_chat: Path) -> None:
    out = await NotebookReadTool().execute(action="remove_pages", pages=["wiki/pino.md"],
                                           reason="no")
    assert out.startswith("Error: unknown action")
    assert (notebook_chat / "wiki" / "pino.md").exists()


async def test_only_from_a_notebook_chat(root: Path) -> None:
    scope = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    req = bind_request_context(RequestContext(channel="websocket", chat_id="default",
                                              session_key="unified:default"))
    try:
        out = await NotebookReadTool().execute(action="check")
    finally:
        reset_request_context(req)
        reset_workspace_scope(scope)
    assert out.startswith("Error: not from here")


# ── Le letture ───────────────────────────────────────────────────────────────


async def test_check_answers_under_the_cap(notebook_chat: Path) -> None:
    _write(notebook_chat / "wiki" / "molti.md", "---\nstate: open\n---\n" + "".join(
        f"[[morto-{i}]] " * 3 + "\n" for i in range(1500)))
    for tool, cap in ((NotebookReadTool(), 12000), (NotebookEditTool(), 14000)):
        out = await tool.execute(action="check", limit=200)
        assert len(out) <= cap
        assert out.startswith("Notebook check: 4 pages")


async def test_map_and_check_from_the_tool(notebook_chat: Path) -> None:
    tool = NotebookReadTool()
    out = await tool.execute(action="map", subjects=[{"name": "Acero"}],
                             topics=[{"name": "substrato", "keywords": ["terriccio"]}])
    assert "| Acero (3) | 1 |" in out and "wiki/acero.md:6" in out
    assert (await tool.execute(action="map", subjects=[])).startswith("Error: map needs")
    out = await tool.execute(action="check")
    assert "No defects found." in out


async def test_read_many_records_reads_without_dedup(notebook_chat: Path) -> None:
    from jenny.agent.tools.filesystem import ReadFileTool

    states = FileStates()
    tool = NotebookEditTool(file_states=states)
    out = await tool.execute(action="read_many", paths=["wiki/acero.md", "fico.md"], mode="head",
                             lines=2)
    assert "=== wiki/acero.md (7 lines) ===\n---\nstate: open" in out
    acero = notebook_chat / "wiki" / "acero.md"
    assert states.check_read(acero) is None  # edit_file non avvisa più
    # Ma un read_file dopo dà il file, non «invariato dall'ultima lettura»: qui
    # se n'è vista solo la testa.
    reader = ReadFileTool(workspace=notebook_chat.parents[1], file_states=states)
    assert "Substrato acido." in await reader.execute(path=str(acero))
    too_many = await tool.execute(action="read_many", paths=[f"wiki/{i}.md" for i in range(31)])
    assert too_many.startswith("Error: at most 30 files")


# ── remove_pages ─────────────────────────────────────────────────────────────


async def test_remove_pages_deletes_traces_and_updates_board_and_reminders(
    notebook_chat: Path, announced: list[str], cron
) -> None:
    root = notebook_chat
    rid = cron.test_rinvaso
    first_lines = (root / "wiki" / "acero.md").read_text(encoding="utf-8")
    out = await NotebookEditTool(timezone="Europe/Rome", cron=cron).execute(
        action="remove_pages", pages=["wiki/acero.md", "nessuna.md"],
        reason="doppione di fico")
    assert not (root / "wiki" / "acero.md").exists()
    assert "Removed 1 page(s): wiki/acero.md." in out
    assert "Cards touched: 1 — `alberi`: acero.md" in out
    assert "Drawers to rewrite: 1." in out and "`alberi` «Alberi»: fico.md" in out
    assert "Reminders cleared: 1" in out and f"`{rid}` «Rinvaso» (was acero.md)" in out
    assert "Links that now point nowhere (2" in out
    assert "- wiki/index.md:3 [[acero]]" in out and "- wiki/fico.md:6 [[acero]]" in out
    assert "Not found, nothing to remove: nessuna.md." in out

    # La traccia sta in raw/removed/<giorno>.md: non in audit/, dove il passo 6 di
    # lint_wiki vuole voci di audit con il loro frontmatter.
    traces = list((root / "raw" / "removed").glob("*.md"))
    assert len(traces) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", traces[0].name)
    assert f"Trace (path, first lines, reason): raw/removed/{traces[0].name}" in out
    assert not (root / "audit").exists()
    trace = traces[0].read_text(encoding="utf-8")
    assert "Reason: doppione di fico" in trace and "### wiki/acero.md" in trace
    assert trace.startswith(f"# Pages removed — {traces[0].stem}")
    assert first_lines.split("\n")[3] in trace  # «# Acero»

    board = read_board(root)
    assert board is not None and board["cards"][0]["pages"] == ["fico.md"]
    # Cancellata prima della bacheca: non è un orfano «già visto».
    assert "acero.md" not in board["orphansSeen"]
    assert cron.get_job(rid).payload.why is None
    assert announced == ["piante"]

    # Una seconda rimozione lo stesso giorno appende alla stessa traccia.
    await NotebookEditTool(timezone="Europe/Rome").execute(
        action="remove_pages", pages=["pino.md"], reason="vuota")
    assert traces[0].read_text(encoding="utf-8").count("Reason:") == 2


@pytest.mark.parametrize("page,why", [
    ("wiki/index.md", "the map"),
    ("raw/journal/x.md", "only wiki pages"),
    ("board/board.json", "only wiki pages"),
    ("wiki/../../altro/wiki/x.md", "not under wiki/"),
])
async def test_remove_pages_refuses_what_is_not_a_page(notebook_chat: Path, page: str,
                                                      why: str) -> None:
    out = await NotebookEditTool().execute(action="remove_pages", pages=["wiki/pino.md", page],
                                           reason="x")
    assert out.startswith("Error: nothing was removed") and why in out
    assert (notebook_chat / "wiki" / "pino.md").exists(), "un rifiuto non toglie niente"


async def test_a_fence_refusing_one_page_removes_none(notebook_chat: Path, monkeypatch) -> None:
    import jenny.agent.tools.notebook as module

    def refuse(path):
        if Path(path).name == "fico.md":
            raise BoardWriteError(f"refused write to {path}")

    monkeypatch.setattr(module, "refuse_board_write", refuse)
    out = await NotebookEditTool().execute(action="remove_pages",
                                           pages=["wiki/pino.md", "wiki/fico.md"], reason="x")
    assert out.startswith("Error: nothing was removed")
    assert (notebook_chat / "wiki" / "pino.md").exists()
    assert not (notebook_chat / "raw" / "removed").exists()


async def test_the_trace_passes_the_same_fences(notebook_chat: Path, monkeypatch) -> None:
    """Anche la scrittura della traccia in raw/removed/ passa dai recinti: se uno
    la rifiuta, non si cancella niente."""
    import jenny.agent.tools.notebook as module

    seen: list[str] = []

    def refuse(path):
        seen.append(Path(path).parent.name)
        if Path(path).parent.name == "removed":
            raise BoardWriteError(f"refused write to {path}")

    monkeypatch.setattr(module, "check_subagent_write", refuse)
    out = await NotebookEditTool().execute(action="remove_pages", pages=["wiki/pino.md"],
                                           reason="x")
    assert out.startswith("Error: nothing was removed — the trace could not be written")
    assert seen == ["wiki", "removed"]
    assert (notebook_chat / "wiki" / "pino.md").exists()


async def test_remove_pages_is_refused_in_a_read_only_turn(root: Path) -> None:
    scope = bind_workspace_scope(build_workspace_scope(root, "restricted").without_write_access())
    req = bind_request_context(RequestContext(channel="websocket", chat_id="project:piante",
                                              session_key="project:piante"))
    try:
        out = await NotebookEditTool().execute(action="remove_pages", pages=["wiki/pino.md"],
                                               reason="x")
        check = await NotebookEditTool().execute(action="check")
    finally:
        reset_request_context(req)
        reset_workspace_scope(scope)
    assert out == READONLY_TOOL_REFUSAL
    assert (root / "wiki" / "pino.md").exists()
    assert check.startswith("Notebook check:"), "le letture restano aperte"


# ── Conta come lettura in pianificazione ─────────────────────────────────────


@pytest.mark.parametrize("action,result,counts", [
    ("check", "Notebook check: 4 pages", True),
    ("map", "Map of 4 text files", True),
    ("read_many", "=== wiki/a.md ===", True),
    ("remove_pages", READONLY_TOOL_REFUSAL, False),
    ("check", "Error: not from here", False),
])
def test_notebook_reads_count_as_planning_reads(action: str, result: str, counts: bool) -> None:
    ctx = ToolResultHookContext(name="notebook", call_id="c1", arguments={"action": action},
                                result=result)
    assert _counts_as_read(ctx) is counts


def _lint(root: Path) -> list[str]:
    """Le righe di problema (🔴/🟡) che ``lint_wiki.py`` stampa per *root*."""
    script = Path(__file__).resolve().parents[3] / "jenny" / "skills" / "llm-wiki" / "scripts"
    spec = importlib.util.spec_from_file_location("lint_wiki_trace", script / "lint_wiki.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        module.lint(str(root))
    return [line for line in buf.getvalue().split("\n") if line.startswith(("🔴", "🟡"))]


async def test_the_trace_adds_no_lint_issue(notebook_chat: Path) -> None:
    """La traccia di ``remove_pages`` non deve accendere il lint: in audit/ era un
    🔴 (frontmatter di audit mancante), in raw/notes/ un 🔴 del passo 11."""
    root = notebook_chat
    for folder in ("audit", "log", "raw/journal"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    out = await NotebookEditTool().execute(action="remove_pages", pages=["wiki/pino.md"],
                                           reason="vuota")
    assert "Removed 1 page(s)" in out
    trace = next((root / "raw" / "removed").glob("*.md"))
    with_trace = _lint(root)
    # Lo stesso quaderno senza la traccia: ogni differenza sarebbe colpa sua.
    trace.unlink()
    assert with_trace == _lint(root)


# ── Revisione del 24/10/2026 ─────────────────────────────────────────────────


@pytest.mark.parametrize("spelling", ["./index.md", "wiki/./index.md", "sub/../index.md",
                                      "wiki/sub/../index.md"])
async def test_the_map_is_never_removed_however_it_is_spelled(notebook_chat: Path,
                                                             spelling: str) -> None:
    (notebook_chat / "wiki" / "sub").mkdir()
    out = await NotebookEditTool().execute(action="remove_pages", pages=[spelling], reason="x")
    assert out.startswith("Error: nothing was removed") and "the map" in out
    assert (notebook_chat / "wiki" / "index.md").exists()


async def test_a_link_is_not_followed_to_delete(notebook_chat: Path) -> None:
    import os

    wiki = notebook_chat / "wiki"
    os.symlink(wiki / "index.md", wiki / "alias.md")
    (wiki / "cartella").symlink_to(wiki, target_is_directory=True)
    for page in ("alias.md", "cartella/pino.md"):
        out = await NotebookEditTool().execute(action="remove_pages", pages=[page], reason="x")
        assert out.startswith("Error: nothing was removed") and "is a link" in out, out
    assert (wiki / "index.md").exists() and (wiki / "pino.md").exists()
    assert (wiki / "alias.md").is_symlink()


async def test_a_board_failure_after_deleting_still_answers(
    notebook_chat: Path, announced: list[str], monkeypatch, cron
) -> None:
    import jenny.board.edit as edit_mod

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(edit_mod, "remove_pages", boom)
    out = await NotebookEditTool(cron=cron).execute(action="remove_pages", pages=["acero.md"],
                                                    reason="x")
    assert out.startswith("Removed 1 page(s): wiki/acero.md.")
    assert "Board not updated: [Errno 28] No space left on device" in out
    assert "board action=remove_pages (acero.md)" in out
    assert "Reminders cleared: 1" in out
    assert announced == ["piante"]


async def test_the_trace_says_which_pages_stayed(notebook_chat: Path, monkeypatch) -> None:
    real = Path.unlink

    def unlink(self, *a, **k):
        if self.name == "fico.md":
            raise OSError(13, "Permission denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    out = await NotebookEditTool().execute(action="remove_pages",
                                           pages=["pino.md", "fico.md"], reason="x")
    assert "Removed 1 page(s): wiki/pino.md." in out and "Not removed: wiki/fico.md" in out
    trace = next((notebook_chat / "raw" / "removed").glob("*.md")).read_text(encoding="utf-8")
    assert "Not removed (still in place): wiki/fico.md (Permission denied)" in trace
    assert trace.count("# Pages removed") == 1


async def test_a_removed_page_leaves_no_taint_behind(notebook_chat: Path) -> None:
    from jenny.security.subagent_policy import SubagentRunPolicy, enter_subagent_policy
    from jenny.security.taint_ledger import ledger_for

    install = notebook_chat.parents[2]
    rel = "workspace/wikis/piante/wiki/pino.md"
    ledger_for(install).record(rel, "web_fetch:example.com")
    policy = SubagentRunPolicy.for_run("t1", install, seed=("web_fetch:example.com",))
    with enter_subagent_policy(policy):
        out = await NotebookEditTool().execute(action="remove_pages", pages=["pino.md"],
                                               reason="x")
    assert "Removed 1 page(s)" in out
    assert ledger_for(install).source_of(rel) is None


async def test_read_many_that_reads_nothing_is_an_error(notebook_chat: Path) -> None:
    out = await NotebookReadTool().execute(action="read_many", paths=["../fuori.md"])
    assert out.startswith("Error: nothing was read.")
    ctx = ToolResultHookContext(name="notebook", call_id="c", arguments={"action": "read_many"},
                                result=out)
    assert _counts_as_read(ctx) is False


async def test_a_name_too_long_is_an_answer_not_a_crash(notebook_chat: Path) -> None:
    """Su Python 3.11 un nome oltre il limite del filesystem solleva ``OSError``
    dalla risoluzione: deve tornare come risposta, non come eccezione."""
    long = "x" * 400 + ".md"
    out = await NotebookEditTool().execute(action="remove_pages", pages=[long], reason="x")
    assert out.startswith(("None of those pages exists", "Error: nothing was removed")), out
    read = await NotebookReadTool().execute(action="read_many", paths=[long])
    assert read.startswith("Error: nothing was read.")


# ── Le cartelle rimaste vuote (25/10/2026) ───────────────────────────────────


async def test_remove_pages_removes_the_folders_it_leaves_empty(notebook_chat: Path) -> None:
    """Un riordino ne lasciava sette, vuote, in ``wiki/concepts/``: ora se ne
    vanno risalendo fino a ``wiki/`` escluso, e la risposta le elenca come le pagine."""
    root = notebook_chat
    wiki = root / "wiki"
    for rel in ("concepts/Acero/substrato.md", "concepts/Acero/potatura.md",
                "concepts/Fico/sub/esposizione.md", "concepts/Pino/a.md", "concepts/Pino/b.md",
                "concepts/Tenuta/c.md"):
        _write(wiki / rel, "---\nstate: open\n---\n# X\n")
    _write(wiki / "concepts" / "Tenuta" / ".gitkeep", "")
    _write(root / "raw" / "vuota" / ".keep", "")
    (root / "raw" / "vuota" / ".keep").unlink()  # una cartella vuota in raw/: resta

    out = await NotebookEditTool(timezone="Europe/Rome").execute(
        action="remove_pages", reason="riordino",
        pages=["concepts/Acero/substrato.md", "concepts/Acero/potatura.md",
               "wiki/concepts/Fico/sub/esposizione.md", "concepts/Pino/a.md",
               "concepts/Tenuta/c.md"])

    assert "Removed 5 page(s)" in out
    gone = ["wiki/concepts/Acero/", "wiki/concepts/Fico/sub/", "wiki/concepts/Fico/"]
    assert f"Removed 3 folder(s) left empty: {', '.join(gone)}." in out
    assert not (wiki / "concepts" / "Acero").exists()
    assert not (wiki / "concepts" / "Fico").exists()
    # Non vuote: una pagina rimasta, un file nascosto.
    assert (wiki / "concepts" / "Pino" / "b.md").is_file()
    assert (wiki / "concepts" / "Tenuta" / ".gitkeep").is_file()
    assert (wiki / "concepts").is_dir() and wiki.is_dir()
    assert (root / "raw" / "vuota").is_dir()
    trace = next((root / "raw" / "removed").glob("*.md")).read_text(encoding="utf-8")
    assert f"Empty folders removed: {', '.join(gone)}" in trace


async def test_wiki_itself_stays_when_its_last_page_goes(notebook_chat: Path) -> None:
    """La salita si ferma a ``wiki/``: mai la cartella stessa, mai la radice."""
    root = notebook_chat
    (root / "wiki" / "index.md").unlink()  # solo per questa prova: wiki/ resta vuota
    out = await NotebookEditTool().execute(action="remove_pages", reason="x",
                                           pages=["acero.md", "fico.md", "pino.md"])
    assert "Removed 3 page(s)" in out and "folder(s) left empty" not in out
    assert (root / "wiki").is_dir() and not any((root / "wiki").iterdir())
