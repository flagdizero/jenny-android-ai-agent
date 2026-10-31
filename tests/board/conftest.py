"""Un quaderno «piante» con qualche pagina, e la config che accende la wiki."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui.commands import CommandContext

PAGES = {
    "travaso-acero.md": (
        "# travaso acero\n\nDeciso il 04/09: dal vaso 14 al vaso 20 ([[vasi-settembre]]).\n"
        "Si aspetta la [[dormienza]]. Per il vaso della monstera v. [[rinvaso-monstera]].\n"
    ),
    "vasi-settembre.md": "# vasi di settembre\n\nVaso 20 per l'acero.\n",
    "irrigazione-acero.md": "# irrigazione acero\n\nQuando i primi 2 cm sono asciutti.\n",
    "rinvaso-monstera.md": "# rinvaso monstera\n\nIn primavera.\n",
    "dormienza.md": "# dormienza\n\nIl riposo invernale.\n",
    "ginkgo.md": "# ginkgo\n\nArrivato a settembre.\n",
    "ginkgo-talea.md": "# talea di ginkgo\n\nIn acqua dal 20/09.\n",
    "ginkgo-vaso.md": "# vaso del ginkgo\n\nNon registrato.\n",
}


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture()
def notebook(workspace_root: Path) -> Path:
    root = workspace_root / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "audit" / "resolved").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Piante\n\n- [[travaso-acero]]\n")
    for name, text in PAGES.items():
        (root / "wiki" / name).write_text(text)
    return root


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


@pytest.fixture()
def ctx(workspace_root: Path, config_path: Path) -> CommandContext:
    return CommandContext(
        get_workspace_root=lambda: workspace_root,
        invalidate_session=lambda _key: None,
        busy_session_keys=lambda: (),
    )
