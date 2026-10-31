"""L'id di un quaderno non si toglie scrivendo il suo ``AGENTS.md``.

Il 26/10 sul Titan un aiutante, riscrivendo il layout di Piante, ha «pulito» i
due ``id:`` della frontmatter tenendo quello sbagliato. La bozza e' arrivata
all'utente senza dirlo, e' stata installata, e al riavvio la migrazione ha dato
al quaderno un id nuovo: da li' ogni turno della sua chat rifiutato.

Gli strumenti dell'agente **rifiutano** (al modello il rifiuto insegna, e la
bozza non nasce); l'editor della WebUI e l'installazione di una bozza
**rimettono** l'id (il file e' dell'utente, e un cancello li' non protegge
niente).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent import staged_writes
from jenny.agent.tools.apply_patch import ApplyPatchTool
from jenny.agent.tools.filesystem import EditFileTool, WriteFileTool
from jenny.security import taint_ledger
from jenny.security.subagent_policy import SubagentRunPolicy, enter_subagent_policy
from jenny.utils.wiki_paths import wiki_id
from jenny.webui.staged_commands import staged_install, staged_list
from jenny.webui.workspace_files import write_file as webui_write_file

ID = "097d8ba49406"
AGENTS = f"---\nid: {ID}\nscope: piante di casa\n---\n\n# Piante\n\nLayout research.\n"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    notebook = root / "wikis" / "piante"
    (notebook / "wiki").mkdir(parents=True)
    (notebook / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    (notebook / "AGENTS.md").write_text(AGENTS, encoding="utf-8")
    taint_ledger._LEDGERS.clear()
    staged_writes.clear_change_listeners()
    yield root
    taint_ledger._LEDGERS.clear()
    staged_writes.clear_change_listeners()


def _agents(ws: Path) -> Path:
    return ws / "wikis" / "piante" / "AGENTS.md"


def _tool(cls, ws: Path):
    return cls(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)


def _untrusted(ws: Path) -> SubagentRunPolicy:
    return SubagentRunPolicy.for_run("t1", ws, seed=("web_fetch:evil.example",))


# -- gli strumenti dell'agente: rifiuto ----------------------------------------


@pytest.mark.parametrize("content", [
    "---\nscope: piante di casa\n---\n\n# Piante\n",  # tolto
    "---\nid: piante\nscope: piante di casa\n---\n\n# Piante\n",  # sostituito a mano
    "---\nid: aaaaaaaaaaaa\n---\n\n# Piante\n",  # un altro id valido
    "# Piante, senza frontmatter\n",
])
async def test_write_file_cannot_take_the_id_away(ws: Path, content: str) -> None:
    out = await _tool(WriteFileTool, ws).execute(path=str(_agents(ws)), content=content)
    assert out.startswith("Error: not written"), out
    assert f"id: {ID}" in out
    assert _agents(ws).read_text(encoding="utf-8") == AGENTS


async def test_edit_file_cannot_take_the_id_away(ws: Path) -> None:
    out = await _tool(EditFileTool, ws).execute(
        path=str(_agents(ws)), old_text=f"id: {ID}\n", new_text="")
    assert out.startswith("Error: not written"), out
    assert wiki_id(_agents(ws).parent) == ID


async def test_apply_patch_as_the_helper_wrote_it_on_the_titan(ws: Path) -> None:
    """La patch vera di ``a893a59e``, sul file con i due ``id:`` di allora."""
    _agents(ws).write_text(AGENTS.replace(f"id: {ID}\n", f"id: {ID}\nid: piante\n"))
    edits = [
        {"path": str(_agents(ws)), "action": "replace",
         "old_text": f"id: {ID}\nid: piante", "new_text": "id: piante"},
        {"path": str(_agents(ws)), "action": "replace",
         "old_text": "Layout research.", "new_text": "Una cartella per pianta."},
    ]
    out = await _tool(ApplyPatchTool, ws).execute(edits=edits)
    assert out.startswith("Error: not written"), out
    assert "Layout research." in _agents(ws).read_text(encoding="utf-8"), "tutto o niente"


async def test_a_dry_run_tells_the_truth_about_the_id(ws: Path) -> None:
    """Al collaudo su jenny_s22 l'aiutante ha notato che il dry-run diceva
    «succeeded» per una patch che la scrittura vera rifiutava."""
    edits = [{"path": str(_agents(ws)), "action": "replace",
              "old_text": f"id: {ID}\n", "new_text": ""}]
    out = await _tool(ApplyPatchTool, ws).execute(edits=edits, dry_run=True)
    assert out.startswith("Error: not written"), out
    ok = [{"path": str(_agents(ws)), "action": "replace",
           "old_text": "Layout research.", "new_text": "Per pianta."}]
    assert (await _tool(ApplyPatchTool, ws).execute(edits=ok, dry_run=True)).startswith(
        "Patch dry-run succeeded")


async def test_any_other_change_to_the_file_goes_through(ws: Path) -> None:
    out = await _tool(EditFileTool, ws).execute(
        path=str(_agents(ws)), old_text="Layout research.", new_text="Una cartella per pianta.")
    assert "Error" not in out, out
    assert "Una cartella per pianta." in _agents(ws).read_text(encoding="utf-8")
    assert wiki_id(_agents(ws).parent) == ID


async def test_a_notebook_without_an_id_has_nothing_to_guard(ws: Path) -> None:
    _agents(ws).write_text("---\nscope: x\n---\n", encoding="utf-8")
    out = await _tool(WriteFileTool, ws).execute(path=str(_agents(ws)), content="# P\n")
    assert out.startswith("Successfully wrote"), out


async def test_an_agents_md_outside_a_notebook_is_not_fenced(ws: Path) -> None:
    out = await _tool(WriteFileTool, ws).execute(path=str(ws / "AGENTS.md"), content="# Jenny\n")
    assert out.startswith("Successfully wrote"), out


async def test_an_untrusted_helper_is_refused_before_any_draft(ws: Path) -> None:
    """Il caso del Titan: ``AGENTS.md`` e' un file CONTROL, e la scrittura di un
    aiutante non fidato andava in bozza. Il rifiuto arriva prima: nessuna scheda
    da approvare che tolga l'id senza dirlo."""
    with enter_subagent_policy(_untrusted(ws)):
        out = await _tool(WriteFileTool, ws).execute(
            path=str(_agents(ws)), content="---\nid: piante\n---\n\n# Piante\n")
    assert out.startswith("Error: not written"), out
    assert staged_writes.list_staged(ws) == []


# -- le scritture dell'utente: l'id si rimette ---------------------------------


def test_the_webui_editor_saves_with_the_id_put_back(ws: Path) -> None:
    webui_write_file(_agents(ws), "---\nid: piante\nscope: mie\n---\n\n# Piante mie\n")
    text = _agents(ws).read_text(encoding="utf-8")
    assert text == f"---\nid: {ID}\nprevious_id: piante\nscope: mie\n---\n\n# Piante mie\n"


def test_the_webui_editor_leaves_other_files_as_written(ws: Path) -> None:
    page = ws / "wikis" / "piante" / "wiki" / "index.md"
    webui_write_file(page, "---\nid: piante\n---\n")
    assert page.read_text(encoding="utf-8") == "---\nid: piante\n---\n"


async def test_a_draft_made_before_the_fence_installs_with_the_id(ws: Path) -> None:
    """Una bozza nata prima di questo recinto (o prima dell'id): l'utente l'ha
    approvata, quindi si installa — con l'id."""
    staged_writes.stage(
        ws, "wikis/piante/AGENTS.md", b"---\nscope: nuova\n---\n\n# Piante\n",
        sources=("web_fetch:x",), task_id="t1",
    )
    ctx = MagicMock()
    ctx.get_workspace_root.return_value = ws
    [item] = (await staged_list(ctx, {}))["staged"]
    await staged_install(ctx, {"id": item["id"], "target_sha256": item["target_sha256"]})
    text = _agents(ws).read_text(encoding="utf-8")
    assert text == f"---\nid: {ID}\nscope: nuova\n---\n\n# Piante\n"
