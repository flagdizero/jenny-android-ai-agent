"""Rinominare un quaderno da Jenny: la cartella, la chat, le pagine in casa.

La meta' *all'indietro* c'era gia' — una wiki rinominata a mano, e la chat che
al turno dopo la ritrova per id (`session/project_rename.py`) — e ha i suoi
banchi. Qui si prova la strada in avanti, che fa **nello stesso ordine** quel
che succede quando il rinomino lo fa una mano: prima la cartella, poi le tracce
della chat. Su cartelle vere, non su finti.

Il banco portante e' il primo: **dopo il rinomino nessuna traccia porta piu' il
nome vecchio, e tutte portano il nuovo**. Si accorge da solo della prossima
traccia il giorno che nasce, come l'invariante della cancellazione.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.session.project_rename import pending_project_renames
from jenny.session.project_traces import describe_project_traces, project_trace_paths
from jenny.webui.project_rename import ProjectRenameError, rename_project

VECCHIO = "viaggio"
NUOVO = "viaggi"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "jenny.config.paths.get_webui_dir", lambda: _ensure(tmp_path / ".jenny" / "webui")
    )
    return tmp_path


def _quaderno(workspace: Path, nome: str, *, con_chat: bool = True) -> None:
    _ensure(workspace / "wikis" / nome / "wiki")
    (workspace / "wikis" / nome / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    if con_chat:
        for traccia in project_trace_paths(workspace, f"project:{nome}"):
            _ensure(traccia.parent)
            if traccia.suffix:
                traccia.write_text("{}\n", encoding="utf-8")
            else:
                _ensure(traccia)


def _rinomina(workspace: Path, nome: str = VECCHIO, nuovo: str = NUOVO, **kw):
    svuotate: list[str] = []
    esito = rename_project(
        wikis_dir=workspace / "wikis",
        scripts_dir=workspace / "skills" / "llm-wiki" / "scripts",
        workspace=workspace,
        name=nome,
        new_name=nuovo,
        invalidate_session=svuotate.append,
        **kw,
    )
    return esito, svuotate


# ── Cosa si sposta ──────────────────────────────────────────────────────────


def test_after_a_rename_no_trace_carries_the_old_name(workspace) -> None:
    _quaderno(workspace, VECCHIO)
    esito, _ = _rinomina(workspace)

    assert esito["chat_moved"] is True
    assert not (workspace / "wikis" / VECCHIO).exists()
    assert (workspace / "wikis" / NUOVO / "wiki" / "index.md").exists()
    assert not describe_project_traces(workspace, f"project:{VECCHIO}").exists, (
        "una traccia porta ancora il nome vecchio: la chat e' rimasta indietro"
    )
    for traccia in project_trace_paths(workspace, f"project:{NUOVO}"):
        assert traccia.exists(), f"{traccia.name} non e' arrivata sotto il nome nuovo"
    assert pending_project_renames(workspace) == [], "il giornale e' rimasto aperto"


def test_a_notebook_without_a_conversation_just_moves_its_folder(workspace) -> None:
    """Un quaderno appena creato non ha ancora chat: non c'e' niente da seguire,
    e questo non e' un rifiuto (`follow_renamed_project` lo sarebbe)."""
    _quaderno(workspace, VECCHIO, con_chat=False)
    esito, _ = _rinomina(workspace)
    assert esito["chat_moved"] is False
    assert (workspace / "wikis" / NUOVO / "wiki").is_dir()


def test_both_sessions_are_cleared_from_memory_first(workspace) -> None:
    """Una sessione viva in cache riscriverebbe il suo file sotto il nome
    vecchio appena qualcuno la salva."""
    _quaderno(workspace, VECCHIO)
    _, svuotate = _rinomina(workspace)
    assert svuotate == [f"project:{VECCHIO}", f"project:{NUOVO}"]


# ── Cosa si rifiuta, prima di toccare niente ────────────────────────────────


@pytest.mark.parametrize("cattivo", ["Ricerca ETF", "a..b", "", "../fuori"])
def test_a_name_that_would_not_open_is_refused(workspace, cattivo) -> None:
    """La stessa regola del canale: una chat spostata su un nome che nessuno
    riapre e' una chat perduta con l'apparenza di un successo."""
    _quaderno(workspace, VECCHIO)
    with pytest.raises(ProjectRenameError):
        _rinomina(workspace, nuovo=cattivo)
    assert (workspace / "wikis" / VECCHIO / "wiki").is_dir()
    assert describe_project_traces(workspace, f"project:{VECCHIO}").exists


def test_a_notebook_without_a_chat_gets_no_second_guard(workspace) -> None:
    """**Il controllo del nome qui e' l'unico, non il secondo.**

    Con una chat, un nome che non si apre lo rifiuterebbe anche
    `follow_renamed_project`, e la cartella tornerebbe indietro. Ma un quaderno
    senza chat non passa di li': `../fuori` porterebbe la cartella **fuori da
    `wikis/`** e risponderebbe «fatto». La mutazione che toglieva il controllo
    sopravviveva a tutti i casi con la chat (23/09/2026).
    """
    _quaderno(workspace, VECCHIO, con_chat=False)
    with pytest.raises(ProjectRenameError):
        _rinomina(workspace, nuovo="../fuori")
    assert (workspace / "wikis" / VECCHIO / "wiki").is_dir()
    assert not (workspace / "fuori").exists(), "la cartella e' uscita da wikis/"


def test_a_notebook_without_a_chat_does_not_adopt_someone_elses(workspace) -> None:
    """L'altra meta' della stessa scoperta. Una chat rimasta sotto il nome
    nuovo — di un quaderno cancellato a mano, per dire — con un quaderno che
    chat non ne ha: senza il controllo la cartella arriva, e si trova addosso
    la conversazione di un altro. Con la chat lo fermerebbe il seguito; senza,
    solo questo."""
    _quaderno(workspace, VECCHIO, con_chat=False)
    orfana = project_trace_paths(workspace, f"project:{NUOVO}")[0]
    _ensure(orfana.parent)
    orfana.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ProjectRenameError, match="conversation"):
        _rinomina(workspace)
    assert (workspace / "wikis" / VECCHIO / "wiki").is_dir()
    assert not (workspace / "wikis" / NUOVO).exists(), "ha adottato la chat di un altro"


def test_a_taken_folder_is_refused(workspace) -> None:
    _quaderno(workspace, VECCHIO)
    _quaderno(workspace, NUOVO, con_chat=False)
    with pytest.raises(ProjectRenameError, match="already exists"):
        _rinomina(workspace)
    assert (workspace / "wikis" / VECCHIO / "wiki").is_dir()


def test_a_conversation_already_under_the_new_name_is_refused(workspace) -> None:
    """Lo scambio di due nomi: non si sceglie, si dice. E non si tocca niente."""
    _quaderno(workspace, VECCHIO)
    orfana = project_trace_paths(workspace, f"project:{NUOVO}")[0]
    _ensure(orfana.parent)
    orfana.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ProjectRenameError, match="conversation"):
        _rinomina(workspace)
    assert (workspace / "wikis" / VECCHIO / "wiki").is_dir()
    assert describe_project_traces(workspace, f"project:{VECCHIO}").exists


def test_something_that_is_not_a_notebook_is_refused(workspace) -> None:
    _ensure(workspace / "wikis" / VECCHIO)          # niente `wiki/` dentro
    with pytest.raises(ProjectRenameError, match="no notebook"):
        _rinomina(workspace)


def test_the_same_name_is_refused(workspace) -> None:
    _quaderno(workspace, VECCHIO)
    with pytest.raises(ProjectRenameError):
        _rinomina(workspace, nuovo=VECCHIO)


# ── Quando la chat non puo' seguire ─────────────────────────────────────────


def test_a_clean_refusal_to_follow_puts_the_folder_back(workspace, monkeypatch) -> None:
    """Meglio un rinomino non fatto che due meta': la cartella torna al suo
    nome, e la chat — che non si e' mossa — resta con lei."""
    from jenny.webui import project_rename as modulo

    _quaderno(workspace, VECCHIO)
    monkeypatch.setattr(
        modulo, "follow_renamed_project", lambda *a: (False, "moving failed, so nothing was moved")
    )
    with pytest.raises(ProjectRenameError, match="could not follow"):
        _rinomina(workspace)
    assert (workspace / "wikis" / VECCHIO / "wiki").is_dir(), "la cartella non e' tornata"
    assert not (workspace / "wikis" / NUOVO).exists()


def test_halfway_is_left_to_the_journal_not_undone(workspace, monkeypatch) -> None:
    """A meta' strada qualcosa si e' gia' mosso, ed e' scritto nel giornale: il
    prossimo avvio finisce il lavoro. Disfare la cartella adesso vorrebbe dire
    tracce da una parte e cartella dall'altra — proprio il male da evitare."""
    from jenny.session import project_rename as seguito
    from jenny.webui import project_rename as modulo

    _quaderno(workspace, VECCHIO)

    def _a_meta(ws, vecchia, nuova):
        seguito._write_journal(ws, [(vecchia, nuova)])
        return False, "moving the conversation's files stopped halfway"

    monkeypatch.setattr(modulo, "follow_renamed_project", _a_meta)
    esito, _ = _rinomina(workspace)
    assert esito["chat_moved"] is False
    assert (workspace / "wikis" / NUOVO / "wiki").is_dir(), "la cartella e' tornata indietro"


# ── Il comando: le pagine in casa seguono il nome ───────────────────────────


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from jenny.config.loader import save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context

    percorso = tmp_path / "config.json"
    c = Config()
    save_config(c, percorso)
    monkeypatch.setattr(get_runtime_context(), "config_path", percorso)
    return percorso


def _pagine(config: Path) -> list[dict]:
    return json.loads(config.read_text(encoding="utf-8"))["casa"]["schermate"]


async def _con_pagine(pagine: list[dict]) -> None:
    from jenny.config import store
    from jenny.config.schema import SchermataConfig

    def _metti(c):
        c.casa.schermate = [SchermataConfig(**p) for p in pagine]
        return True

    await store.mutate(_metti)


async def test_a_pinned_notebook_page_follows_the_new_name(workspace, config, monkeypatch) -> None:
    from jenny.webui import commands
    from jenny.webui import project_rename as modulo

    monkeypatch.setattr(modulo, "rename_project", lambda **kw: {"new_name": kw["new_name"]})
    await _con_pagine([
        {"id": "q1", "kind": "conversazione", "ref": f"project:{VECCHIO}"},
        {"id": "a1", "kind": "app", "ref": f"project:{VECCHIO}"},
        {"id": "q2", "kind": "conversazione", "ref": "project:altro"},
    ])
    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    await commands.project_rename(ctx, {"name": VECCHIO, "new_name": NUOVO})

    assert [(p["id"], p["ref"]) for p in _pagine(config)] == [
        ("q1", f"project:{NUOVO}"),
        ("a1", f"project:{VECCHIO}"),        # la specie dice di chi e' una pagina
        ("q2", "project:altro"),
    ]


async def test_a_refused_rename_leaves_the_pages_alone(workspace, config, monkeypatch) -> None:
    from jenny.webui import commands
    from jenny.webui import project_rename as modulo
    from jenny.webui.commands import CommandError

    def _rifiuta(**kw):
        raise modulo.ProjectRenameError("a folder named viaggi already exists")

    monkeypatch.setattr(modulo, "rename_project", _rifiuta)
    await _con_pagine([{"id": "q1", "kind": "conversazione", "ref": f"project:{VECCHIO}"}])
    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    with pytest.raises(CommandError):
        await commands.project_rename(ctx, {"name": VECCHIO, "new_name": NUOVO})
    assert _pagine(config)[0]["ref"] == f"project:{VECCHIO}"


async def test_the_command_refuses_a_bad_name_before_any_thread(workspace, config) -> None:
    from jenny.webui import commands
    from jenny.webui.commands import CommandError

    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    with pytest.raises(CommandError, match="invalid new name"):
        await commands.project_rename(ctx, {"name": VECCHIO, "new_name": "Ricerca ETF"})


@pytest.mark.parametrize("in_volo", [f"project:{VECCHIO}", f"project:{NUOVO}"])
async def test_the_command_refuses_while_a_turn_is_running_there(
    workspace, config, monkeypatch, in_volo
) -> None:
    """Un turno in volo ha la sessione in mano: sgomberare la cache non lo ferma,
    e a fine turno la salverebbe sotto il nome vecchio — una chat senza cartella
    accanto a quella spostata. Si rifiuta **prima** di toccare qualunque cosa."""
    from jenny.webui import commands
    from jenny.webui import project_rename as modulo
    from jenny.webui.commands import CommandError

    toccato: list[str] = []
    monkeypatch.setattr(modulo, "rename_project", lambda **kw: toccato.append("rename"))
    ctx = SimpleNamespace(
        get_workspace_root=lambda: workspace,
        invalidate_session=lambda k: toccato.append(k),
        busy_session_keys=lambda: (in_volo, "unified:default"),
    )
    with pytest.raises(CommandError) as err:
        await commands.project_rename(ctx, {"name": VECCHIO, "new_name": NUOVO})
    assert err.value.code == "conflict"
    assert toccato == []


def test_the_command_is_registered() -> None:
    from jenny.webui.commands import COMMANDS, project_rename

    assert COMMANDS["project.rename"] is project_rename
