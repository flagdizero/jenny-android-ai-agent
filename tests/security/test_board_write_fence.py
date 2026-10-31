"""La bacheca di un quaderno si scrive solo dai suoi imbuti.

``board/board.json`` porta le cose dell'utente (schede, posizioni, sezioni) e
``jenny.board.store`` le protegge: lucchetto, ``.bak``, normalizzazione e il
controllo di proprieta'. Una scrittura con gli strumenti generici saltava tutto
— e, con le micro app, un ``run.py`` riscritto a mano girerebbe senza
approvazione. Ogni superficie di scrittura deve quindi rifiutare ``board/``,
e la lettura deve restare aperta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.filesystem import ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    bind_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)
from jenny.security.workspace_policy import is_board_path


@pytest.fixture
def notebook(tmp_path: Path):
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "board").mkdir()
    (root / "board" / "board.json").write_text('{"cards": []}', encoding="utf-8")
    scope = validate_workspace_scope_payload(
        {"project_path": str(root), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        yield ws, root
    finally:
        reset_workspace_scope(token)


def _exec_tool(ws: Path) -> PythonExecTool:
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    return tool


def test_a_board_is_recognised_by_its_wiki_next_to_it(tmp_path: Path) -> None:
    (tmp_path / "q" / "wiki").mkdir(parents=True)
    (tmp_path / "altro").mkdir()
    assert is_board_path(tmp_path / "q" / "board" / "board.json")
    assert is_board_path(tmp_path / "q" / "board")
    assert is_board_path(tmp_path / "q" / "board" / "micro" / "m-a" / "run.py")
    assert not is_board_path(tmp_path / "altro" / "board" / "x.json")
    assert not is_board_path(tmp_path / "q" / "wiki" / "board.md")


async def test_file_tools_refuse_the_board_and_still_read_it(notebook) -> None:
    ws, root = notebook
    tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    out = await tool.execute(path=str(root / "board" / "board.json"), content="{}")
    assert "board's own tools" in out
    assert (root / "board" / "board.json").read_text() == '{"cards": []}'
    ok = await tool.execute(path=str(root / "wiki" / "acero.md"), content="# acero\n")
    assert "Successfully wrote" in ok
    read = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    assert '"cards"' in await read.execute(path=str(root / "board" / "board.json"))


@pytest.mark.parametrize("code", [
    "open({p!r}, 'w').write('x')",
    "import os; os.remove({p!r})",
    "import os; os.rename({p!r}, {p!r} + '.x')",
    "import shutil; shutil.rmtree({d!r})",
    "write_file({p!r}, 'x')",
])
async def test_python_exec_refuses_the_board(notebook, code: str) -> None:
    ws, root = notebook
    target = root / "board" / "board.json"
    out = await _exec_tool(ws).execute(code=code.format(p=str(target), d=str(root / "board")))
    assert "board's own tools" in out, out
    assert target.read_text() == '{"cards": []}'


async def test_python_exec_still_reads_the_board(notebook) -> None:
    ws, root = notebook
    out = await _exec_tool(ws).execute(
        code=f"open({str(root / 'board' / 'board.json')!r}).read()")
    assert "cards" in out
