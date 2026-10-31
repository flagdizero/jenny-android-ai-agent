"""Il codice di un'app si scrive solo dal tool ``app``, che lo controlla.

Il tool valida l'app e gira il lettore prima di salvarla
(``jenny/apps/drafts.py``): un ``app.json`` o un ``run.py`` riscritti con gli
strumenti generici salterebbero quei controlli, e un'app rotta o senza
riassunto finirebbe in produzione. Quindi il recinto: ogni superficie di
scrittura rifiuta il codice, e lascia aperti dati e note.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.filesystem import WriteFileTool
from jenny.security.workspace_policy import is_app_code_path
from tests.security.test_board_write_fence import _exec_tool


@pytest.fixture
def installation(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "skills").mkdir(parents=True)
    app = ws / "apps" / "orto"
    (app / "app").mkdir(parents=True)
    (app / "data").mkdir()
    (app / "app.json").write_text('{"name": "Orto", "description": "x"}')
    (app / "app" / "index.html").write_text("<p>orto</p>")
    return ws


def test_code_is_fenced_data_and_notes_are_not(installation: Path) -> None:
    app = installation / "apps" / "orto"
    for fenced in (app / "app.json", app / "run.py", app / "app" / "index.html", app,
                   installation / "apps", installation / "apps" / ".drafts" / "orto" / "app.json",
                   installation / "apps" / "nuova" / "app.json"):
        assert is_app_code_path(fenced), fenced
    for free in (app / "data" / "letture.jsonl", app / "AGENT.md",
                 installation / "wikis" / "q" / "apps" / "x.md"):
        assert not is_app_code_path(free), free


async def test_file_tools_refuse_app_code_and_keep_data_open(installation: Path) -> None:
    tool = WriteFileTool(workspace=installation, allowed_dir=installation, restrict_to_workspace=True)
    app = installation / "apps" / "orto"
    out = await tool.execute(path=str(app / "app.json"), content="{}")
    assert "`app` tool" in out, out
    assert "Orto" in (app / "app.json").read_text()
    out = await tool.execute(path=str(installation / "apps" / "nuova" / "app.json"), content="{}")
    assert "`app` tool" in out, out
    ok = await tool.execute(path=str(app / "AGENT.md"), content="# Orto\n")
    assert "Successfully wrote" in ok
    ok = await tool.execute(path=str(app / "data" / "note.jsonl"), content="")
    assert "Successfully wrote" in ok


@pytest.mark.parametrize("code", [
    "open({p!r}, 'w').write('x')",
    "import os; os.remove({p!r})",
    "import shutil; shutil.rmtree({d!r})",
])
async def test_python_exec_refuses_app_code(installation: Path, code: str) -> None:
    app = installation / "apps" / "orto"
    target = app / "app.json"
    out = await _exec_tool(installation).execute(code=code.format(p=str(target), d=str(app)))
    assert "`app` tool" in out, out
    assert "Orto" in target.read_text()
