from __future__ import annotations

import dataclasses
import sys
from typing import Generator

import pytest

from jenny.config.paths import set_workspace_dir
from jenny.runtime.context import get_runtime_context
from jenny.utils.helpers import sync_workspace_templates


@pytest.fixture(scope="session", autouse=True)
def _configure_jenny_workspace(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Provide a temporary workspace for the entire test suite."""
    data_dir = tmp_path_factory.mktemp("jenny_data")
    workspace = data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(workspace, silent=True)

    set_workspace_dir(str(workspace))

    yield

    set_workspace_dir("")


# I registri di modulo che un test può lasciare pieni, come (modulo, attributo).
# Si guardano solo se il modulo è già importato: la fixture non deve tirare
# dentro niente che la prova non usi.
_MODULE_REGISTRIES: tuple[tuple[str, str], ...] = (
    # Un turno websocket segnato «in corso» e mai chiuso: il canale lo legge
    # all'attach e manda un `goal_status` che le prove dopo non si aspettano.
    ("jenny.session.webui_turns", "_WEBSOCKET_TURN_WALL_STARTED_AT"),
)


@pytest.fixture(autouse=True)
def _isolate_process_state() -> Generator[None, None, None]:
    """Ogni prova ritrova lo stato di processo come l'ha trovato la precedente.

    La suite passava solo in ordine alfabetico: invertita dava 31 rossi,
    mescolata 17, tutti da stato globale che una prova cambiava e non
    ripristinava — i campi di :class:`RuntimeContext` (``config_recovered_from``,
    ``cron_recovered_from``, …) e i registri in ``_MODULE_REGISTRIES``. Qui si
    fotografano prima e si rimettono dopo, per tutti, invece di affidarsi a
    ogni prova perché pulisca: il rosso compariva in un file che non c'entrava.
    """
    ctx = get_runtime_context()
    saved_ctx = {f.name: getattr(ctx, f.name) for f in dataclasses.fields(ctx)}
    saved_regs = {}
    for mod_name, attr in _MODULE_REGISTRIES:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            saved_regs[(mod_name, attr)] = dict(getattr(mod, attr))

    yield

    for name, value in saved_ctx.items():
        setattr(ctx, name, value)
    for mod_name, attr in _MODULE_REGISTRIES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        registry = getattr(mod, attr)
        registry.clear()
        registry.update(saved_regs.get((mod_name, attr), {}))
