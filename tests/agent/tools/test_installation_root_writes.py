"""Le scritture che usavano la radice dell'installazione invece di quella del turno.

Passo **6** del piano dei progetti.

Sono una famiglia, non due casi isolati: un tool che si porta la destinazione
scritta dentro — ``<workspace>/downloads/``, ``apps/<nome>/data/`` — non passa da
``resolve_allowed_path``, quindi **il confine di scrittura non ha niente da
guardare**. Da dentro un progetto scrivevano fuori, mentre il prompt di quel
progetto diceva «scrivi solo dentro questa cartella»: la stessa classe di difetto
che il passo 2 ha sistemato nel prompt, qui sul lato dei file.

Sono venute a galla facendo il passo 4: **fare la sola lettura obbliga a trovare
tutte le scritture.** La Todo il 22/08 sul telefono (chiedendo a Jenny di
aggirare il divieto), ``downloads/`` leggendo il codice subito dopo.

Le due si chiudono in modo diverso, e la differenza è una decisione sul
prodotto (22/08):

- ``downloads/`` **segue il turno**: un file scaricato lavorando a un progetto è
  materiale di quel progetto;
- le mini-app sono **personali**: da un progetto si leggono e non si scrivono. Una
  Todo per progetto darebbe sette liste vuote invece di quella che usi, e
  dichiararle condivise sarebbe costato una riga di prompt a ogni turno per
  raccontare l'eccezione.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.tools.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from jenny.agent.tools.download import DOWNLOADS_SUBDIR, DownloadFileTool
from jenny.apps.manifest import AppAction
from jenny.apps.storage import StorageError, execute_storage_action
from jenny.security.workspace_access import (
    WorkspaceScopeResolver,
    build_workspace_scope,
    enter_workspace_scope,
)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Un'installazione con un progetto vero dentro (``wiki/`` compresa)."""
    (tmp_path / "wikis" / "palestra" / "wiki").mkdir(parents=True)
    (tmp_path / "apps" / "todo" / "data").mkdir(parents=True)
    return tmp_path


def _project(install: Path) -> Path:
    return install / "wikis" / "palestra"


def _action(op: str) -> AppAction:
    return AppAction(name=op, description="", kind="storage", collection="todos", op=op)


# ── downloads/ segue il turno ────────────────────────────────────────────


def test_downloads_lands_inside_the_project(install: Path) -> None:
    project = _project(install)
    tool = DownloadFileTool(str(install))
    with enter_workspace_scope(build_workspace_scope(project, "restricted")):
        root = tool._downloads_root()
    assert root == project, (
        "prima del passo 6 era la radice dell'installazione, cioè una scrittura fuori "
        "dal progetto che il confine non vedeva passare"
    )
    assert (root / DOWNLOADS_SUBDIR).parent == project


def test_the_personal_chat_still_downloads_into_the_installation(install: Path) -> None:
    tool = DownloadFileTool(str(install))
    with enter_workspace_scope(build_workspace_scope(install, "full")):
        assert tool._downloads_root() == install


def test_without_a_bound_scope_it_falls_back_to_the_constructor(install: Path) -> None:
    """Sessioni interne e test: là la radice del turno *è* quella del costruttore."""
    assert DownloadFileTool(str(install))._downloads_root() == install


async def test_a_download_is_refused_in_read_only_before_the_network(install: Path) -> None:
    """Controprova incrociata col passo 4: le due chiusure convivono."""
    client = MagicMock()
    client.get = MagicMock(side_effect=AssertionError("la rete non va toccata"))
    scope = build_workspace_scope(_project(install), "restricted").without_write_access()
    with enter_workspace_scope(scope):
        result = await DownloadFileTool(str(install), client=client).execute(
            url="https://example.invalid/a.jpg"
        )
    assert "read-only" in result


# ── le mini-app sono personali ───────────────────────────────────────────


@pytest.mark.parametrize("op", ["append", "set", "update", "delete"])
async def test_a_project_cannot_change_the_personal_app_data(install: Path, op: str) -> None:
    """La scrittura vista sul telefono il 22/08: la Todo personale, da una chat di lavoro."""
    with enter_workspace_scope(build_workspace_scope(_project(install), "restricted")):
        with pytest.raises(StorageError) as err:
            await execute_storage_action(
                install / "apps" / "todo", _action(op), {"text": "x", "id": "1"}
            )
    text = str(err.value)
    assert "personal" in text.lower()
    # Dal 06/10/2026 dice anche l'altra strada: posarla sulla bacheca.
    assert "board" in text.lower() and "place" in text.lower(), text


async def test_an_app_placed_on_the_board_is_writable_from_that_notebook(install: Path) -> None:
    """La regola della posa: posare un'app in un quaderno e' darle il permesso.

    «Comprato 4 VWCE» detto in Soldi deve finire nell'app ETF posata in Soldi; da
    un altro quaderno, dove non e' posata, resta il rifiuto di prima.
    """
    from jenny.board.store import mutate_board

    mutate_board(_project(install), lambda b: b["cards"].append({
        "id": "lista", "title": "Lista", "pos": {"x": 1, "y": 1},
        "app": {"slug": "todo", "params": {}}}))
    with enter_workspace_scope(build_workspace_scope(_project(install), "restricted")):
        result = await execute_storage_action(
            install / "apps" / "todo", _action("append"), {"text": "x"}
        )
    assert result["ok"] is True
    other = install / "wikis" / "soldi"
    (other / "wiki").mkdir(parents=True)
    with enter_workspace_scope(build_workspace_scope(other, "restricted")):
        with pytest.raises(StorageError):
            await execute_storage_action(
                install / "apps" / "todo", _action("append"), {"text": "y"}
            )


async def test_a_project_can_still_read_them(install: Path) -> None:
    """``query`` resta aperta: leggere la propria lista non è cambiarla."""
    app = install / "apps" / "todo"
    (app / "data" / "todos.jsonl").write_text('{"id":"1","text":"ciao"}\n', encoding="utf-8")
    with enter_workspace_scope(build_workspace_scope(_project(install), "restricted")):
        result = await execute_storage_action(app, _action("query"), {})
    assert result.get("ok") is not False


async def test_the_personal_chat_writes_them_as_always(install: Path) -> None:
    app = install / "apps" / "todo"
    with enter_workspace_scope(build_workspace_scope(install, "full")):
        result = await execute_storage_action(app, _action("append"), {"text": "x"})
    assert result["ok"] is True


async def test_a_bound_scope_that_is_not_a_wiki_is_not_a_project(install: Path) -> None:
    """La domanda è «sono in un progetto», e un progetto **è una wiki**.

    Si chiede alla cartella e non alla chiave di sessione, per la stessa ragione
    del blocco di prompt del 2.1: chi ha bisogno di questa risposta è anche il
    subagent, che la chiave non ce l'ha mai. Uno scope scelto a mano su una
    cartella qualunque non è un progetto e non deve inciampare qui.
    """
    plain = install / "altro"
    plain.mkdir()
    with enter_workspace_scope(build_workspace_scope(plain, "restricted")):
        result = await execute_storage_action(
            install / "apps" / "todo", _action("append"), {"text": "x"}
        )
    assert result["ok"] is True


# ── ma la domanda è sul *turno*, non sulla cartella (T4.9) ───────────────


async def test_a_project_whose_folder_is_missing_is_still_a_project(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il difetto del 23/08: una cartella che manca non contiene ``wiki/``.

    ``WorkspaceScopeResolver.for_project`` tiene di proposito il percorso che non
    esiste — le scritture falliscono, ma il lavoro di un progetto non finisce nel
    workspace personale. Solo che ``is_wiki_root`` su quella cartella dice
    ``False``, quindi il gate si apriva: dentro una chat di progetto la Todo
    personale tornava scrivibile, cioè esattamente la scrittura che il 22/08 è
    stata chiusa. La chiave del turno lo sa, e la cartella no.
    """
    resolver = WorkspaceScopeResolver(
        default_workspace=install, default_restrict_to_workspace=True
    )
    key = "project:mai-creato"
    scope = resolver.for_project(key)
    assert scope.project_path == install / "wikis" / "mai-creato"
    assert not (scope.project_path / "wiki").exists(), "la cartella deve mancare davvero"

    token = bind_request_context(RequestContext(channel="websocket", chat_id=key, session_key=key))
    try:
        with enter_workspace_scope(scope):
            with pytest.raises(StorageError) as err:
                await execute_storage_action(
                    install / "apps" / "todo", _action("append"), {"text": "x"}
                )
    finally:
        reset_request_context(token)
    assert "personal" in str(err.value).lower()


async def test_a_personal_root_that_happens_to_contain_a_wiki_is_not_a_project(
    install: Path,
) -> None:
    """Il rovescio della stessa medaglia: la forma da sola sbaglia anche in su.

    Un workspace personale che un giorno si trovasse una ``wiki/`` in radice —
    una cartella creata a mano, uno scaffold andato nel posto sbagliato —
    diventerebbe "un progetto" per il solo test strutturale, e **ogni** scrittura
    personale sulle mini-app verrebbe rifiutata. Il turno però è la chat
    personale, e l'app vive dentro la radice che questo turno può cambiare.
    """
    (install / "wiki").mkdir()
    with enter_workspace_scope(build_workspace_scope(install, "restricted")):
        result = await execute_storage_action(
            install / "apps" / "todo", _action("append"), {"text": "x"}
        )
    assert result["ok"] is True


async def test_a_subagent_inside_a_project_has_no_key_and_is_still_refused(
    install: Path,
) -> None:
    """La ragione per cui la forma della cartella resta il secondo ramo.

    Un subagent lanciato dentro un progetto **eredita lo scope** ma la sua chiave
    è ``subagent:<lineage>``, che non è una chiave di progetto: la sorgente
    autorevole non c'è, e la cartella è il solo segnale. Togliere il secondo ramo
    riaprirebbe qui la scrittura del 22/08.
    """
    key = "subagent:abc123"
    token = bind_request_context(RequestContext(channel="internal", chat_id="x", session_key=key))
    try:
        with enter_workspace_scope(build_workspace_scope(_project(install), "restricted")):
            with pytest.raises(StorageError):
                await execute_storage_action(
                    install / "apps" / "todo", _action("append"), {"text": "x"}
                )
    finally:
        reset_request_context(token)


# ── e il guardiano dell'inventario copre entrambe ────────────────────────


def test_both_are_still_in_the_write_inventory() -> None:
    """Chiuse, non sparite: restano tool che scrivono senza passare dal cancello.

    Toglierle dall'inventario del 4.1 significherebbe smettere di controllare
    che continuino a chiedere.
    """
    from tests.security.test_readonly_write_surfaces import _ASKS_FOR_ITSELF

    # Chiavi per percorso e non per basename: da T4.7 l'inventario guarda anche
    # fuori da ``agent/tools/``, e lì un ``store.py`` da solo non dice quale
    # (``config/store.py`` e ``snapshot/store.py`` esistono entrambi).
    assert "agent/tools/download.py" in _ASKS_FOR_ITSELF
    assert "apps/storage.py" in _ASKS_FOR_ITSELF
