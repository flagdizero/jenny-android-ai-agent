"""Jenny in chat riscrive la memoria: quello che esce va in archivio.

Fino al 25/11/2026 gli strumenti di file dell'agente principale nascevano senza
``entry_archiver``: una voce che Jenny toglieva o cambiava in ``USER.md`` durante
una conversazione spariva senza lasciare traccia, mentre la stessa scrittura
fatta da Dream o dall'editor dell'officina finiva in ``memory/archive/``.

Nomi inventati: il repository è pubblico.
"""

from __future__ import annotations

from pathlib import Path

from jenny.agent.memory_archive import find_archived, read_archived
from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from jenny.agent.tools.memory_entries import entry_id
from jenny.config.schema import ToolsConfig

USER = """# User

## Basics

- Name: Alex Doe
- Lives in Cremona with Oreste
- Plays the cello on Tuesdays
"""


def _ctx(root: Path, states: FileStates) -> ToolContext:
    return ToolContext(config=ToolsConfig(), workspace=str(root), file_states=states)


async def _read(root: Path, states: FileStates, rel: str) -> None:
    await ReadFileTool.create(_ctx(root, states)).execute(path=rel)


def _archived(root: Path, text: str):
    found = find_archived(root / "memory", entry_id(text))
    return read_archived(found) if found is not None else None


async def test_a_changed_entry_is_archived_with_its_successor(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text(USER, encoding="utf-8")
    states = FileStates()
    await _read(tmp_path, states, "USER.md")
    out = await EditFileTool.create(_ctx(tmp_path, states)).execute(
        path="USER.md",
        old_text="- Lives in Cremona with Oreste",
        new_text="- Lives in Lodi with Oreste",
    )
    assert "Error" not in out, out
    old = _archived(tmp_path, "- Lives in Cremona with Oreste")
    assert old is not None, "la frase vecchia deve finire in archivio"
    assert old.superseded_by == entry_id("- Lives in Lodi with Oreste")
    assert not old.forgotten and not old.retracted


async def test_a_removed_entry_is_archived_but_not_forgotten(tmp_path: Path) -> None:
    """Non l'ha tolta l'utente: il richiamo per nome può ancora riportarla."""
    (tmp_path / "USER.md").write_text(USER, encoding="utf-8")
    states = FileStates()
    await _read(tmp_path, states, "USER.md")
    out = await WriteFileTool.create(_ctx(tmp_path, states)).execute(
        path="USER.md", content=USER.replace("- Plays the cello on Tuesdays\n", ""),
    )
    assert "Error" not in out, out
    gone = _archived(tmp_path, "- Plays the cello on Tuesdays")
    assert gone is not None
    assert not gone.forgotten and gone.superseded_by == ""


async def test_memory_md_is_covered_too(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text(
        "# Memory\n\n- The garage code changed in March\n", encoding="utf-8",
    )
    states = FileStates()
    await _read(tmp_path, states, "memory/MEMORY.md")
    await WriteFileTool.create(_ctx(tmp_path, states)).execute(
        path="memory/MEMORY.md", content="# Memory\n",
    )
    assert _archived(tmp_path, "- The garage code changed in March") is not None


async def test_other_files_are_left_alone(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("- Lives in Cremona with Oreste\n", encoding="utf-8")
    states = FileStates()
    await _read(tmp_path, states, "notes.md")
    await WriteFileTool.create(_ctx(tmp_path, states)).execute(path="notes.md", content="")
    assert not (tmp_path / "memory" / "archive").exists()
