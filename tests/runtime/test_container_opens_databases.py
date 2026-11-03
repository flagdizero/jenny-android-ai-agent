"""Il boot apre lo stato in SQLite prima di chiunque lo usi, anche senza agente.

Con l'onboarding non finito il provider non c'è e l'agente nasce dopo, quando il
cron gira già (``GatewayContainer._wait_and_create_agent``): i database devono
essere aperti da ``build`` comunque.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jenny import db
from jenny.config import paths as paths_mod
from jenny.config.schema import Config
from jenny.runtime.container import GatewayContainer


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    previous = paths_mod.get_workspace_path()
    root = tmp_path / "workspace"
    root.mkdir()
    paths_mod.set_workspace_dir(str(root))
    try:
        yield root
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")
        db.close_all()


def test_build_opens_the_databases_beside_the_workspace(workspace: Path) -> None:
    container = GatewayContainer(Config())
    container.build()
    assert container.provider is None  # il ramo dell'onboarding rinviato
    for name in db.DATABASE_NAMES:
        assert (workspace.parent / "db" / f"{name}.sqlite").is_file()
        assert not db.database(workspace.parent, name).closed
