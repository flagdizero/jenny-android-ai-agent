"""Un quaderno tenuto da un piano a lista si scrive solo dal piano.

Fra la foto A e la foto B tutto quel che cambia nel quaderno dev'essere del
piano: «Torna a prima» disfa quel che trova. Le RPC dell'utente rispondono già
``plan_running``; qui si prova l'altra porta, i tool di Jenny. La chat personale
(e i suoi aiutanti) arriva ai quaderni attraverso la radice del workspace, e una
pagina scritta da lì durante il piano sparirebbe col ritorno. Scrive solo un
turno il cui scope è il quaderno stesso: i passi e gli aiutanti che lanciano.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.filesystem import WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.plan.driver import PlanDriver
from jenny.plan.store import PlanStore
from jenny.security.workspace_access import (
    enter_workspace_scope,
    set_held_notebook_probe,
    validate_workspace_scope_payload,
)
from tests.support.plan import KEY, STEPS, FakeBus, FakeHost, write_file


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    write_file(ws / "wikis" / "piante", "wiki/index.md", "# piante")
    write_file(ws / "wikis" / "ricette", "wiki/index.md", "# ricette")
    return ws


@pytest.fixture()
async def held(workspace: Path):
    host = FakeHost(workspace)
    driver = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), host)
    await driver.propose(KEY, "Rifare la wiki", STEPS)
    plan = driver.store.open_plan_for("piante")
    assert plan is not None
    await driver.approve(plan.id, plan.version, [])
    set_held_notebook_probe(driver.held_notebook_roots)
    try:
        yield driver, plan
    finally:
        set_held_notebook_probe(None)


def _scope(ws: Path, root: Path):
    return validate_workspace_scope_payload(
        {"project_path": str(root), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )


def _exec_tool(ws: Path) -> PythonExecTool:
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    return tool


async def test_the_personal_chat_cannot_write_into_a_held_notebook(
    workspace: Path, held
) -> None:
    target = workspace / "wikis" / "piante" / "wiki" / "acero.md"
    tool = WriteFileTool(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True)
    with enter_workspace_scope(_scope(workspace, workspace)):
        out = await tool.execute(path=str(target), content="# acero\n")
        assert "The notebook piante is running a plan" in out, out
        assert not target.exists()
        # Un altro quaderno, e il resto del workspace, restano scrivibili.
        other = await tool.execute(
            path=str(workspace / "wikis" / "ricette" / "wiki" / "pane.md"), content="pane"
        )
        assert "Successfully wrote" in other
        assert "Successfully wrote" in await tool.execute(
            path=str(workspace / "note.md"), content="x"
        )


@pytest.mark.parametrize("code", [
    "open({p!r}, 'w').write('x')",
    "write_file({p!r}, 'x')",
])
async def test_python_exec_from_the_personal_chat_is_refused(
    workspace: Path, held, code: str
) -> None:
    target = workspace / "wikis" / "piante" / "wiki" / "acero.md"
    with enter_workspace_scope(_scope(workspace, workspace)):
        out = await _exec_tool(workspace).execute(code=code.format(p=str(target)))
    assert "running a plan" in out, out
    assert not target.exists()


async def test_the_plans_own_turns_and_helpers_still_write(workspace: Path, held) -> None:
    root = workspace / "wikis" / "piante"
    tool = WriteFileTool(workspace=root, allowed_dir=root, restrict_to_workspace=True)
    with enter_workspace_scope(_scope(workspace, root)):
        out = await tool.execute(path=str(root / "wiki" / "acero.md"), content="# acero\n")
        assert "Successfully wrote" in out, out
        py = await _exec_tool(root).execute(
            code=f"open({str(root / 'wiki' / 'ginkgo.md')!r}, 'w').write('g')"
        )
    assert "running a plan" not in py
    assert (root / "wiki" / "ginkgo.md").read_text() == "g"


async def test_the_fence_lifts_when_the_plan_stops(workspace: Path, held) -> None:
    driver, plan = held
    await driver.stop(plan.id, "keep")
    target = workspace / "wikis" / "piante" / "wiki" / "acero.md"
    tool = WriteFileTool(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True)
    with enter_workspace_scope(_scope(workspace, workspace)):
        out = await tool.execute(path=str(target), content="# acero\n")
    assert "Successfully wrote" in out, out
