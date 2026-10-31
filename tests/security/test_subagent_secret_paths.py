"""Un subagent non legge né scrive ``config.json`` e le sue copie.

Fino all'11/10/2026 un subagent nella chat personale poteva leggere
``config.json`` (chiave del provider, segreto dei token della WebUI, token del
bot Telegram) con ``read_file``, ``grep apiKey .`` o ``open()`` in
``python_exec``, e un subagent di quaderno con la lettura dell'installazione.
Ogni superficie qui sotto passa ora da ``security.subagent_policy``; fuori da un
subagent (Jenny principale) le stesse letture funzionano ancora.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.filesystem import ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.agent.tools.search import GrepTool
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.subagent_policy import SubagentRunPolicy, enter_subagent_policy
from jenny.security.workspace_access import (
    bind_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)

_KEY = "sk-SEGRETO-123"
_REFUSAL = "private configuration"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "memory").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "config.json").write_text(f'{{"apiKey": "{_KEY}"}}', encoding="utf-8")
    (root / "config.json.bak").write_text(f'{{"apiKey": "{_KEY}"}}', encoding="utf-8")
    snap = root / ".jenny" / "snapshots" / "objects"
    snap.mkdir(parents=True)
    (snap / "ab12").write_text(_KEY, encoding="utf-8")
    (root / "notes.md").write_text("niente di segreto", encoding="utf-8")
    return root


def _subagent(ws: Path):
    return enter_subagent_policy(SubagentRunPolicy(task_id="t1", install_root=ws))


def _exec_tool(ws: Path) -> PythonExecTool:
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    return tool


@pytest.mark.parametrize("name", [
    "config.json", "config.json.bak", ".jenny/snapshots/objects/ab12",
])
async def test_read_file_refuses_the_secrets_in_a_subagent(ws: Path, name: str) -> None:
    tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    with _subagent(ws):
        out = await tool.execute(path=str(ws / name))
        assert _REFUSAL in out
        assert _KEY not in out
        assert "niente di segreto" in await tool.execute(path=str(ws / "notes.md"))


async def test_the_main_agent_still_reads_config(ws: Path) -> None:
    tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    assert _KEY in await tool.execute(path=str(ws / "config.json"))


async def test_grep_from_the_root_skips_the_secrets(ws: Path) -> None:
    tool = GrepTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    assert _KEY in await tool.execute(pattern="sk-SEGRETO", path=".", output_mode="content")
    with _subagent(ws):
        out = await tool.execute(pattern="sk-SEGRETO", path=".", output_mode="content")
    assert _KEY not in out
    assert "config.json" not in out


async def test_write_file_refuses_config_and_system_code(ws: Path) -> None:
    tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    with _subagent(ws):
        out = await tool.execute(path=str(ws / "config.json"), content="{}")
        assert _REFUSAL in out
        out = await tool.execute(path=str(ws / "agent" / "identity.md"), content="x")
        assert "restored from the app package" in out
        ok = await tool.execute(path=str(ws / "out" / "report.md"), content="ok")
        assert "Successfully wrote" in ok
    assert _KEY in (ws / "config.json").read_text()
    assert not (ws / "agent" / "identity.md").exists()


@pytest.mark.parametrize("code", [
    "print(open({p!r}).read())",
    "import pathlib; print(pathlib.Path({p!r}).read_text())",
    "import os; fd = os.open({p!r}, os.O_RDONLY); print(os.read(fd, 100))",
    "print(read_file({p!r}))",
    "import shutil; shutil.copy({p!r}, 'copia.json'); print(open('copia.json').read())",
])
async def test_python_exec_refuses_reading_config(ws: Path, code: str) -> None:
    with _subagent(ws):
        out = await _exec_tool(ws).execute(code=code.format(p=str(ws / "config.json")))
    assert _KEY not in out, out
    assert _REFUSAL in out, out


@pytest.mark.parametrize("code", [
    "open({p!r}, 'w').write('{{}}')",
    "import os; os.remove({p!r})",
    "import os; os.rename({p!r}, 'via.json')",
    "write_file({p!r}, '{{}}')",
])
async def test_python_exec_refuses_writing_config(ws: Path, code: str) -> None:
    with _subagent(ws):
        out = await _exec_tool(ws).execute(code=code.format(p=str(ws / "config.json")))
    assert _REFUSAL in out, out
    assert _KEY in (ws / "config.json").read_text()


async def test_python_exec_outside_a_subagent_is_unchanged(ws: Path) -> None:
    out = await _exec_tool(ws).execute(code=f"print(open({str(ws / 'config.json')!r}).read())")
    assert _KEY in out


async def test_a_notebook_subagent_cannot_read_the_installation_config(ws: Path) -> None:
    """Il subagent di quaderno legge l'installazione in sola lettura: non config.json."""
    nb = ws / "wikis" / "piante"
    (nb / "wiki").mkdir(parents=True)
    scope = validate_workspace_scope_payload(
        {"project_path": str(nb), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        tool = ReadFileTool(workspace=nb, allowed_dir=nb, restrict_to_workspace=True)
        with _subagent(ws):
            out = await tool.execute(path=str(ws / "config.json"))
        assert _KEY not in out
    finally:
        reset_workspace_scope(token)
