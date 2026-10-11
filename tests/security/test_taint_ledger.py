"""Il registro di provenienza: un file scritto da un lavoro non fidato resta tale.

Senza registro il taint moriva con il lavoro: un ``researcher`` salvava una
pagina in ``out/`` e un ``coder`` la leggeva il giorno dopo da pulito. Qui si
fissa che la contaminazione passa attraverso il file — con ``read_file``,
``grep``, ``open`` e ``import`` — e che si pulisce solo con una riscrittura
completa fatta da un lavoro fidato o dall'utente.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.agent.tools.search import GrepTool
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security import taint_ledger
from jenny.security.subagent_policy import SubagentRunPolicy, enter_subagent_policy
from jenny.security.taint_ledger import TaintLedger, ledger_for


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "out").mkdir(parents=True)
    (root / "memory").mkdir()
    taint_ledger._LEDGERS.clear()
    yield root
    taint_ledger._LEDGERS.clear()


def _run(ws: Path, *, untrusted: bool) -> SubagentRunPolicy:
    seed = ("web_fetch:evil.example",) if untrusted else ()
    return SubagentRunPolicy.for_run("t", ws, seed=seed)


def _write_tool(ws: Path) -> WriteFileTool:
    return WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)


def _exec_tool(ws: Path) -> PythonExecTool:
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    return tool


async def _write_untrusted(ws: Path, rel: str, text: str = "ignore all previous") -> None:
    with enter_subagent_policy(_run(ws, untrusted=True)):
        out = await _write_tool(ws).execute(path=str(ws / rel), content=text)
    assert "Successfully wrote" in out


# -- il passaggio da un lavoro all'altro ---------------------------------------


async def test_a_file_written_by_an_untrusted_run_taints_its_reader(ws: Path) -> None:
    await _write_untrusted(ws, "out/pagina.md")
    reader = _run(ws, untrusted=False)
    with enter_subagent_policy(reader):
        await ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True).execute(
            path=str(ws / "out" / "pagina.md"))
    assert reader.taint_sources() == ("file:out/pagina.md",)


async def test_a_clean_file_does_not(ws: Path) -> None:
    with enter_subagent_policy(_run(ws, untrusted=False)):
        await _write_tool(ws).execute(path=str(ws / "out" / "mia.md"), content="mia")
    reader = _run(ws, untrusted=False)
    with enter_subagent_policy(reader):
        await ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True).execute(
            path=str(ws / "out" / "mia.md"))
    assert not reader.untrusted


async def test_grep_taints_its_reader_too(ws: Path) -> None:
    await _write_untrusted(ws, "out/pagina.md")
    reader = _run(ws, untrusted=False)
    with enter_subagent_policy(reader):
        await GrepTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True).execute(
            pattern="ignore", path=str(ws / "out"), output_mode="content")
    assert reader.untrusted


@pytest.mark.parametrize("code", [
    "print(open('out/pagina.md').read())",
    "import pathlib; print(pathlib.Path('out/pagina.md').read_text())",
])
async def test_python_exec_reading_a_tainted_file_taints_the_run(ws: Path, code: str) -> None:
    await _write_untrusted(ws, "out/pagina.md")
    reader = _run(ws, untrusted=False)
    with enter_subagent_policy(reader):
        await _exec_tool(ws).execute(code=code)
    assert reader.untrusted


async def test_importing_a_tainted_module_taints_the_run(ws: Path) -> None:
    await _write_untrusted(ws, "out/modulo.py", "VALORE = 42\n")
    reader = _run(ws, untrusted=False)
    with enter_subagent_policy(reader):
        out = await _exec_tool(ws).execute(code="import modulo; print(modulo.VALORE)",
                                           working_dir="out")
    assert "42" in out
    assert reader.untrusted


async def test_python_exec_writes_and_renames_carry_the_taint(ws: Path) -> None:
    with enter_subagent_policy(_run(ws, untrusted=True)):
        await _exec_tool(ws).execute(code="open('out/a.txt', 'w').write('x')")
    assert ledger_for(ws).source_of("out/a.txt") == "web_fetch:evil.example"
    with enter_subagent_policy(_run(ws, untrusted=False)):
        await _exec_tool(ws).execute(code="import os; os.rename('out/a.txt', 'out/b.txt')")
    assert ledger_for(ws).source_of("out/b.txt") == "web_fetch:evil.example"


async def test_a_copy_by_a_clean_run_carries_the_taint(ws: Path) -> None:
    """Leggere la sorgente contamina il lavoro, che contamina la copia."""
    await _write_untrusted(ws, "out/a.txt")
    with enter_subagent_policy(_run(ws, untrusted=False)):
        await _exec_tool(ws).execute(code="import shutil; shutil.copy('out/a.txt', 'out/c.txt')")
    assert ledger_for(ws).source_of("out/c.txt") is not None


# -- chi pulisce ---------------------------------------------------------------


async def test_a_full_rewrite_by_a_clean_run_clears_the_taint(ws: Path) -> None:
    await _write_untrusted(ws, "out/pagina.md")
    with enter_subagent_policy(_run(ws, untrusted=False)):
        await _write_tool(ws).execute(path=str(ws / "out" / "pagina.md"), content="riscritta")
    assert ledger_for(ws).source_of("out/pagina.md") is None


async def test_a_partial_edit_by_a_clean_run_keeps_it(ws: Path) -> None:
    await _write_untrusted(ws, "out/pagina.md", "riga uno\nignore all\n")
    with enter_subagent_policy(_run(ws, untrusted=False)):
        out = await EditFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True).execute(
            path=str(ws / "out" / "pagina.md"), old_text="riga uno", new_text="riga 1")
    assert "Error" not in out
    assert ledger_for(ws).source_of("out/pagina.md") is not None


async def test_a_subagent_cannot_erase_the_ledger(ws: Path) -> None:
    await _write_untrusted(ws, "out/pagina.md")
    with enter_subagent_policy(_run(ws, untrusted=True)):
        out = await _write_tool(ws).execute(path=str(ws / ".jenny" / "taint_ledger.json"),
                                            content="{}")
    assert "private configuration" in out
    assert ledger_for(ws).source_of("out/pagina.md") is not None


async def test_a_user_save_from_the_webui_clears_the_taint(ws: Path) -> None:
    from jenny.webui.commands import _clear_taint

    await _write_untrusted(ws, "out/pagina.md")
    _clear_taint(ws, ws / "out" / "pagina.md")
    assert ledger_for(ws).source_of("out/pagina.md") is None


# -- il registro in sé ---------------------------------------------------------


def test_a_folder_entry_covers_what_it_contains(ws: Path) -> None:
    ledger = TaintLedger(ws)
    (ws / "out" / "sito").mkdir()
    ledger.record("out/sito", "web_fetch:a.org")
    assert ledger.source_of("out/sito/pagina.html") == "web_fetch:a.org"
    assert ledger.source_of("out/altro.md") is None


def test_many_files_in_one_folder_collapse_into_the_folder(ws: Path) -> None:
    ledger = TaintLedger(ws)
    for i in range(60):
        (ws / "out" / f"f{i}.txt").write_text("x")
        ledger.record(f"out/f{i}.txt", "web_search")
    entries = json.loads((ws / ".jenny" / "taint_ledger.json").read_text())["entries"]
    assert list(entries) == ["out"]
    assert ledger.source_of("out/f3.txt") == "web_search"


def test_the_ledger_is_capped(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(taint_ledger, "_MAX_ENTRIES", 10)
    ledger = TaintLedger(ws)
    for i in range(15):
        (ws / f"d{i}").mkdir()
        (ws / f"d{i}" / "f").write_text("x")
        ledger.record(f"d{i}/f", "web_search")
    entries = json.loads((ws / ".jenny" / "taint_ledger.json").read_text())["entries"]
    assert len(entries) == 10


def test_entries_of_missing_files_are_dropped_on_load(ws: Path) -> None:
    (ws / "out" / "c.txt").write_text("x")
    TaintLedger(ws).record("out/c.txt", "web_search")
    TaintLedger(ws).record("out/sparito.txt", "web_search")
    assert TaintLedger(ws).source_of("out/sparito.txt") is None
    assert TaintLedger(ws).source_of("out/c.txt") == "web_search"


def test_concurrent_writers_lose_nothing(ws: Path) -> None:
    ledger = TaintLedger(ws)
    for i in range(40):
        (ws / f"t{i}").mkdir()
        (ws / f"t{i}" / "f").write_text("x")

    def _writer(start: int) -> None:
        for i in range(start, 40, 4):
            ledger.record(f"t{i}/f", "web_search")

    threads = [threading.Thread(target=_writer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(TaintLedger(ws).source_of(f"t{i}/f") for i in range(40))


def test_an_unreadable_ledger_starts_empty(ws: Path) -> None:
    path = ws / ".jenny" / "taint_ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text("{non json")
    assert TaintLedger(ws).source_of("out/x") is None


@pytest.mark.parametrize("code", [
    "print(read_file('out/pagina.md'))",
    "print(open('out/pagina.md').read())",
])
async def test_a_cold_ledger_is_read_from_inside_python_exec(ws: Path, code: str) -> None:
    """Dopo un riavvio il registro si carica dal codice guardato.

    Per il codice di un subagent il file del registro è segreto: il controllo
    deve leggerlo senza passare dal proprio divieto (sotto bypass).
    """
    (ws / "out" / "pagina.md").write_text("ignore all")
    TaintLedger(ws).record("out/pagina.md", "web_fetch:evil.example")
    taint_ledger._LEDGERS.clear()
    reader = _run(ws, untrusted=False)
    with enter_subagent_policy(reader):
        out = await _exec_tool(ws).execute(code=code)
    assert "ignore all" in out, out
    assert reader.taint_sources() == ("file:out/pagina.md",)
