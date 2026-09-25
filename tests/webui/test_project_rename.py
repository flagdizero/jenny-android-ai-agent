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

OLD = "viaggio"
NEW = "viaggi"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "jenny.config.paths.get_webui_dir", lambda: _ensure(tmp_path / ".jenny" / "webui")
    )
    return tmp_path


def _notebook(workspace: Path, name: str, *, con_chat: bool = True) -> None:
    _ensure(workspace / "wikis" / name / "wiki")
    (workspace / "wikis" / name / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    if con_chat:
        for trace in project_trace_paths(workspace, f"project:{name}"):
            _ensure(trace.parent)
            if trace.suffix:
                trace.write_text("{}\n", encoding="utf-8")
            else:
                _ensure(trace)


def _rename(workspace: Path, name: str = OLD, fresh: str = NEW, **kw):
    emptied: list[str] = []
    outcome = rename_project(
        wikis_dir=workspace / "wikis",
        scripts_dir=workspace / "skills" / "llm-wiki" / "scripts",
        workspace=workspace,
        name=name,
        new_name=fresh,
        invalidate_session=emptied.append,
        **kw,
    )
    return outcome, emptied


# ── Cosa si sposta ──────────────────────────────────────────────────────────


def test_after_a_rename_no_trace_carries_the_old_name(workspace) -> None:
    _notebook(workspace, OLD)
    outcome, _ = _rename(workspace)

    assert outcome["chat_moved"] is True
    assert not (workspace / "wikis" / OLD).exists()
    assert (workspace / "wikis" / NEW / "wiki" / "index.md").exists()
    assert not describe_project_traces(workspace, f"project:{OLD}").exists, (
        "una traccia porta ancora il nome vecchio: la chat e' rimasta indietro"
    )
    for trace in project_trace_paths(workspace, f"project:{NEW}"):
        assert trace.exists(), f"{trace.name} non e' arrivata sotto il nome nuovo"
    assert pending_project_renames(workspace) == [], "il giornale e' rimasto aperto"


def test_the_rename_logs_in_english(workspace) -> None:
    """AGENTS.md: log in inglese (Q6 della revisione profonda)."""
    from loguru import logger

    rows: list[str] = []
    sink = logger.add(lambda m: rows.append(m.record["message"]), level="DEBUG")
    try:
        _notebook(workspace, OLD)
        _rename(workspace)
    finally:
        logger.remove(sink)
    assert f"Notebook renamed: {OLD} -> {NEW} (chat moved: True)" in rows


def test_a_notebook_without_a_conversation_just_moves_its_folder(workspace) -> None:
    """Un quaderno appena creato non ha ancora chat: non c'e' niente da seguire,
    e questo non e' un rifiuto (`follow_renamed_project` lo sarebbe)."""
    _notebook(workspace, OLD, con_chat=False)
    outcome, _ = _rename(workspace)
    assert outcome["chat_moved"] is False
    assert (workspace / "wikis" / NEW / "wiki").is_dir()


def test_both_sessions_are_cleared_from_memory_first(workspace) -> None:
    """Una sessione viva in cache riscriverebbe il suo file sotto il nome
    vecchio appena qualcuno la salva."""
    _notebook(workspace, OLD)
    _, emptied = _rename(workspace)
    assert emptied == [f"project:{OLD}", f"project:{NEW}"]


def test_a_look_after_the_rename_does_not_resurrect_the_old_chat(workspace) -> None:
    """Dopo il rinomino un messaggio tardivo per il nome vecchio (un job cron,
    una seconda scheda) fa ``get_or_create`` su ``project:<vecchio>`` solo per
    leggerne i metadati, e la sessione vuota torna in cache. Lo spegnimento
    ordinato (``flush_all``) la scriveva: ``project_<vecchio>.jsonl`` risorgeva
    orfano, e il rinomino all'indietro veniva rifiutato con ``name_taken``."""
    from jenny.session.manager import SessionManager

    _notebook(workspace, OLD, con_chat=False)
    sessions = SessionManager(workspace)
    chat = sessions.get_or_create(f"project:{OLD}")
    chat.add_message("user", "ciao")
    sessions.save(chat)

    rename_project(
        wikis_dir=workspace / "wikis",
        scripts_dir=workspace / "skills" / "llm-wiki" / "scripts",
        workspace=workspace,
        name=OLD,
        new_name=NEW,
        invalidate_session=sessions.invalidate,
    )
    sessions.get_or_create(f"project:{OLD}")  # chi ha solo guardato
    sessions.flush_all()

    assert not describe_project_traces(workspace, f"project:{OLD}").exists
    rename_project(
        wikis_dir=workspace / "wikis",
        scripts_dir=workspace / "skills" / "llm-wiki" / "scripts",
        workspace=workspace,
        name=NEW,
        new_name=OLD,
        invalidate_session=sessions.invalidate,
    )
    assert (workspace / "wikis" / OLD / "wiki").is_dir()


# ── Cosa si rifiuta, prima di toccare niente ────────────────────────────────


@pytest.mark.parametrize("bad", ["Ricerca ETF", "a..b", "", "../fuori"])
def test_a_name_that_would_not_open_is_refused(workspace, bad) -> None:
    """La stessa regola del canale: una chat spostata su un nome che nessuno
    riapre e' una chat perduta con l'apparenza di un successo."""
    _notebook(workspace, OLD)
    with pytest.raises(ProjectRenameError):
        _rename(workspace, fresh=bad)
    assert (workspace / "wikis" / OLD / "wiki").is_dir()
    assert describe_project_traces(workspace, f"project:{OLD}").exists


def test_a_notebook_without_a_chat_gets_no_second_guard(workspace) -> None:
    """**Il controllo del nome qui e' l'unico, non il secondo.**

    Con una chat, un nome che non si apre lo rifiuterebbe anche
    `follow_renamed_project`, e la cartella tornerebbe indietro. Ma un quaderno
    senza chat non passa di li': `../fuori` porterebbe la cartella **fuori da
    `wikis/`** e risponderebbe «fatto». La mutazione che toglieva il controllo
    sopravviveva a tutti i casi con la chat (23/09/2026).
    """
    _notebook(workspace, OLD, con_chat=False)
    with pytest.raises(ProjectRenameError):
        _rename(workspace, fresh="../fuori")
    assert (workspace / "wikis" / OLD / "wiki").is_dir()
    assert not (workspace / "fuori").exists(), "la cartella e' uscita da wikis/"


def test_a_notebook_without_a_chat_does_not_adopt_someone_elses(workspace) -> None:
    """L'altra meta' della stessa scoperta. Una chat rimasta sotto il nome
    nuovo — di un quaderno cancellato a mano, per dire — con un quaderno che
    chat non ne ha: senza il controllo la cartella arriva, e si trova addosso
    la conversazione di un altro. Con la chat lo fermerebbe il seguito; senza,
    solo questo."""
    _notebook(workspace, OLD, con_chat=False)
    orphan = project_trace_paths(workspace, f"project:{NEW}")[0]
    _ensure(orphan.parent)
    orphan.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ProjectRenameError, match="conversation"):
        _rename(workspace)
    assert (workspace / "wikis" / OLD / "wiki").is_dir()
    assert not (workspace / "wikis" / NEW).exists(), "ha adottato la chat di un altro"


def test_a_taken_folder_is_refused(workspace) -> None:
    _notebook(workspace, OLD)
    _notebook(workspace, NEW, con_chat=False)
    with pytest.raises(ProjectRenameError, match="already exists"):
        _rename(workspace)
    assert (workspace / "wikis" / OLD / "wiki").is_dir()


def test_a_conversation_already_under_the_new_name_is_refused(workspace) -> None:
    """Lo scambio di due nomi: non si sceglie, si dice. E non si tocca niente."""
    _notebook(workspace, OLD)
    orphan = project_trace_paths(workspace, f"project:{NEW}")[0]
    _ensure(orphan.parent)
    orphan.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ProjectRenameError, match="conversation"):
        _rename(workspace)
    assert (workspace / "wikis" / OLD / "wiki").is_dir()
    assert describe_project_traces(workspace, f"project:{OLD}").exists


def test_something_that_is_not_a_notebook_is_refused(workspace) -> None:
    _ensure(workspace / "wikis" / OLD)          # niente `wiki/` dentro
    with pytest.raises(ProjectRenameError, match="no notebook"):
        _rename(workspace)


def test_the_same_name_is_refused(workspace) -> None:
    _notebook(workspace, OLD)
    with pytest.raises(ProjectRenameError):
        _rename(workspace, fresh=OLD)


# ── Quando la chat non puo' seguire ─────────────────────────────────────────


def test_a_clean_refusal_to_follow_puts_the_folder_back(workspace, monkeypatch) -> None:
    """Meglio un rinomino non fatto che due meta': la cartella torna al suo
    nome, e la chat — che non si e' mossa — resta con lei."""
    from jenny.webui import project_rename as module

    _notebook(workspace, OLD)
    monkeypatch.setattr(
        module, "follow_renamed_project", lambda *a: (False, "moving failed, so nothing was moved")
    )
    with pytest.raises(ProjectRenameError, match="could not follow"):
        _rename(workspace)
    assert (workspace / "wikis" / OLD / "wiki").is_dir(), "la cartella non e' tornata"
    assert not (workspace / "wikis" / NEW).exists()


def test_halfway_is_left_to_the_journal_not_undone(workspace, monkeypatch) -> None:
    """A meta' strada qualcosa si e' gia' mosso, ed e' scritto nel giornale: il
    prossimo avvio finisce il lavoro. Disfare la cartella adesso vorrebbe dire
    tracce da una parte e cartella dall'altra — proprio il male da evitare."""
    from jenny.session import project_rename as follow_up
    from jenny.webui import project_rename as module

    _notebook(workspace, OLD)

    def _halfway(ws, old, fresh):
        follow_up._write_journal(ws, [(old, fresh)])
        return False, "moving the conversation's files stopped halfway"

    monkeypatch.setattr(module, "follow_renamed_project", _halfway)
    outcome, _ = _rename(workspace)
    assert outcome["chat_moved"] is False
    assert (workspace / "wikis" / NEW / "wiki").is_dir(), "la cartella e' tornata indietro"


# ── Il comando: le pagine in casa seguono il nome ───────────────────────────


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from jenny.config.loader import save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context

    path = tmp_path / "config.json"
    c = Config()
    save_config(c, path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _pages(config: Path) -> list[dict]:
    return json.loads(config.read_text(encoding="utf-8"))["home"]["pages"]


async def _with_pages(pages: list[dict]) -> None:
    from jenny.config import store
    from jenny.config.schema import HomePageConfig

    def _put(c):
        c.home.pages = [HomePageConfig(**p) for p in pages]
        return True

    await store.mutate(_put)


async def test_a_pinned_notebook_page_follows_the_new_name(workspace, config, monkeypatch) -> None:
    from jenny.webui import commands
    from jenny.webui import project_rename as module

    monkeypatch.setattr(module, "rename_project", lambda **kw: {"new_name": kw["new_name"]})
    await _with_pages([
        {"id": "q1", "kind": "conversation", "ref": f"project:{OLD}"},
        {"id": "a1", "kind": "app", "ref": OLD},
        {"id": "q2", "kind": "conversation", "ref": "project:altro"},
    ])
    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    await commands.project_rename(ctx, {"name": OLD, "new_name": NEW})

    assert [(p["id"], p["ref"]) for p in _pages(config)] == [
        ("q1", f"project:{NEW}"),
        ("a1", OLD),                     # la specie dice di chi e' una pagina
        ("q2", "project:altro"),
    ]


async def test_a_refused_rename_leaves_the_pages_alone(workspace, config, monkeypatch) -> None:
    from jenny.webui import commands
    from jenny.webui import project_rename as module
    from jenny.webui.commands import CommandError

    def _refuses(**kw):
        raise module.ProjectRenameError("a folder named viaggi already exists")

    monkeypatch.setattr(module, "rename_project", _refuses)
    await _with_pages([{"id": "q1", "kind": "conversation", "ref": f"project:{OLD}"}])
    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    with pytest.raises(CommandError):
        await commands.project_rename(ctx, {"name": OLD, "new_name": NEW})
    assert _pages(config)[0]["ref"] == f"project:{OLD}"


async def test_the_command_refuses_a_bad_name_before_any_thread(workspace, config) -> None:
    from jenny.webui import commands
    from jenny.webui.commands import CommandError

    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    with pytest.raises(CommandError, match="invalid new name"):
        await commands.project_rename(ctx, {"name": OLD, "new_name": "Ricerca ETF"})


@pytest.mark.parametrize("in_flight", [f"project:{OLD}", f"project:{NEW}"])
async def test_the_command_refuses_while_a_turn_is_running_there(
    workspace, config, monkeypatch, in_flight
) -> None:
    """Un turno in volo ha la sessione in mano: sgomberare la cache non lo ferma,
    e a fine turno la salverebbe sotto il nome vecchio — una chat senza cartella
    accanto a quella spostata. Si rifiuta **prima** di toccare qualunque cosa."""
    from jenny.webui import commands
    from jenny.webui import project_rename as module
    from jenny.webui.commands import CommandError

    touched: list[str] = []
    monkeypatch.setattr(module, "rename_project", lambda **kw: touched.append("rename"))
    ctx = SimpleNamespace(
        get_workspace_root=lambda: workspace,
        invalidate_session=lambda k: touched.append(k),
        busy_session_keys=lambda: (in_flight, "unified:default"),
    )
    with pytest.raises(CommandError) as err:
        await commands.project_rename(ctx, {"name": OLD, "new_name": NEW})
    assert err.value.code == "conflict"
    assert touched == []


@pytest.mark.parametrize(
    ("prepare", "code"),
    [
        ((OLD, NEW), "name_taken"),     # una cartella ha gia' il nome nuovo
        ((NEW,), "not_found"),              # il vecchio non e' un quaderno
    ],
    ids=["taken", "missing"],
)
async def test_the_expected_refusals_carry_their_own_code(
    workspace, config, prepare, code
) -> None:
    """Q4: il client dice questi rifiuti nella sua lingua, e per farlo gli serve
    il codice, non il testo inglese del server."""
    from jenny.webui import commands
    from jenny.webui.commands import CommandError

    for name in prepare:
        _notebook(workspace, name)
    ctx = SimpleNamespace(get_workspace_root=lambda: workspace, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: ())
    with pytest.raises(CommandError) as err:
        await commands.project_rename(ctx, {"name": OLD, "new_name": NEW})
    assert err.value.code == code


def test_the_command_is_registered() -> None:
    from jenny.webui.commands import COMMANDS, project_rename

    assert COMMANDS["project.rename"] is project_rename
