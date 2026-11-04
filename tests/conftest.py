from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
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
    # Il rapporto vero/stima dei token e l'ultimo numero vero per sessione:
    # una prova con un provider finto che riporta l'usage cambierebbe la stima
    # di tutte quelle dopo.
    ("jenny.utils.token_calibration", "_factors"),
    ("jenny.utils.token_calibration", "_last_real"),
    # I tentativi recenti del tool ``app``, condivisi fra istanze: senza, il
    # tetto scatta in un file di prove che ne ha fatti pochi.
    ("jenny.agent.tools.app_tool", "_RECENT_FAILURES"),
    ("jenny.agent.tools.app_tool", "_BUILDERS"),
)


@pytest.fixture(autouse=True)
def _user_text_in_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le frasi per l'utente (``jenny.runtime.user_text``) in inglese, sempre.

    La lingua si legge da ``config.json`` a ogni frase, e il workspace della
    suite è condiviso: senza questo un test che scrive la config cambierebbe la
    lingua di quelli dopo. Chi prova un'altra lingua la passa a ``say`` o
    rifà questo patch; la lettura vera la prova ``tests/runtime/test_user_text.py``.
    """
    from jenny.runtime import user_text

    monkeypatch.setattr(user_text, "current_language", lambda: "en")


@pytest.fixture(autouse=True)
def _isolate_process_state() -> Generator[None, None, None]:
    """Ogni prova ritrova lo stato di processo come l'ha trovato la precedente.

    La suite passava solo in ordine alfabetico: invertita dava 31 rossi,
    mescolata 17, tutti da stato globale che una prova cambiava e non
    ripristinava — i campi di :class:`RuntimeContext` (``config_recovered_from``,
    …) e i registri in ``_MODULE_REGISTRIES``. Qui si
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


@pytest.fixture
def tmp_path(tmp_path: Path) -> Path:
    """Una cartella dentro quella della prova: il suo genitore e' solo di questa prova.

    Lo stato in SQLite sta nel genitore del workspace (``runtime_root_for``), e
    molte prove usano ``tmp_path`` stesso come workspace: con la ``tmp_path`` di
    pytest il genitore era la cartella base del processo, la stessa per tutte le
    prove. ``_fresh_shared_database`` la svuotava fra una prova e l'altra, ma un
    salvataggio rimasto in coda dalla prova prima la riscriveva a prova dopo gia'
    partita: il 04/11/2026, su 3.12, una sessione ``project:piante`` di un'altra
    prova faceva rispondere a una cancellazione che il quaderno c'era.
    """
    # Col nome della prova, come la cartella di pytest: chi cerca il nome del
    # genitore lo vuole riconoscibile, non una lettera che sta in ogni percorso.
    nested = tmp_path / tmp_path.name
    nested.mkdir()
    return nested


@pytest.fixture(autouse=True)
def _fresh_shared_database(tmp_path_factory: pytest.TempPathFactory) -> Generator[None, None, None]:
    """Il database di chi usa ``tmp_path`` stesso come workspace riparte vuoto.

    Lo stato in SQLite sta accanto al workspace (``<runtime_root>/db``, e il
    runtime root è il genitore del workspace): con ``tmp_path`` come workspace
    finisce nella cartella base della sessione, la stessa per tutte le prove di
    un processo. Senza questo, i job salvati da una prova comparirebbero nella
    bacheca di un'altra.
    """
    import shutil

    from jenny import db as jdb

    shared = tmp_path_factory.getbasetemp() / "db"
    shutil.rmtree(shared, ignore_errors=True)
    yield
    jdb.close_all()
    shutil.rmtree(shared, ignore_errors=True)
