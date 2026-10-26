"""Il ciclo di vita di un progetto: creare, cancellare, e non lasciare niente dietro.

Difetto trovato sul telefono il **24/08/2026** e riprodotto dall'interfaccia:
cancellando `wikis/viaggio` dal file manager e creando un progetto con lo
stesso nome, la conversazione vecchia riappariva intera. L'md5 del file di
sessione era identico prima e dopo — non era stato ripristinato, non era mai
stato toccato.

La causa non era la cancellazione: era che **la creazione aveva un padrone e la
cancellazione no**. Un progetto vive in due domini — l'albero sotto
``wikis/<nome>/`` e le quattro tracce della sua conversazione, che stanno altrove
— e l'unico gesto disponibile ne raggiungeva uno solo, liberando il *nome* senza
liberare la chat.

Il test portante e' il primo: **dopo una cancellazione, nessun percorso del
workspace porta piu' quel nome**. E' un invariante e non una checklist — si
accorge da solo della quinta traccia il giorno che nasce, che e' esattamente il
modo in cui questo difetto tornerebbe.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.session.project_traces import (
    PROJECT_WIKI_ID_KEY,
    delete_project_traces,
    describe_project_traces,
    project_trace_paths,
)
from jenny.utils.wiki_paths import wiki_id
from jenny.webui.commands import _project_delete_refusal
from jenny.webui.project_create import create_project
from jenny.webui.project_delete import ProjectDeleteError, delete_project

NAME = "viaggio"
KEY = f"project:{NAME}"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "jenny.config.paths.get_webui_dir", lambda: _ensure(tmp_path / ".jenny" / "webui")
    )
    return tmp_path


@pytest.fixture
def loop(workspace: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=workspace, model="test-model"
    )


@pytest.fixture
def published(loop: AgentLoop) -> list[str]:
    sent: list[str] = []

    async def capture(message) -> None:
        sent.append(message.content)

    loop.bus.publish_outbound = capture  # type: ignore[assignment]
    return sent


def _make(workspace: Path, name: str = NAME, **kwargs) -> dict:
    """Crea il progetto come lo crea il chip."""
    return create_project(
        wikis_dir=workspace / "wikis",
        scripts_dir=workspace / "skills" / "llm-wiki" / "scripts",
        workspace=workspace,
        name=name,
        seed="un viaggio inventato",
        **kwargs,
    )


def _remove(workspace: Path, name: str = NAME, **kwargs) -> dict:
    return delete_project(
        wikis_dir=workspace / "wikis",
        scripts_dir=workspace / "skills" / "llm-wiki" / "scripts",
        workspace=workspace,
        name=name,
        invalidate_session=kwargs.pop("invalidate_session", lambda _key: None),
        **kwargs,
    )


def _live_chat(workspace: Path, key: str = KEY, messages: int = 3) -> list[Path]:
    """Scrive a mano le tracce di una chat vissuta, come farebbe un turno vero."""
    made: list[Path] = []
    for index, path in enumerate(project_trace_paths(workspace, key)):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".segments":
            path.mkdir(exist_ok=True)
            (path / "0001.jsonl").write_text("{}\n", encoding="utf-8")
        elif index == 0:
            lines = ['{"_type": "metadata", "key": "%s", "metadata": {}}' % key]
            lines += ['{"role": "user", "content": "ciao"}'] * messages
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            path.write_text("{}\n", encoding="utf-8")
        made.append(path)
    return made


def _paths_naming(workspace: Path, name: str) -> list[Path]:
    """Ogni percorso sotto *workspace* che porta *name* nel nome di un file."""
    return [p for p in workspace.rglob("*") if name in p.name]


# ── L'invariante ─────────────────────────────────────────────────────────


def test_deleting_a_project_leaves_no_path_carrying_its_name(workspace: Path) -> None:
    """Il test portante. Cammina l'albero invece di controllare una lista.

    Una lista di percorsi attesi si aggiorna a mano, e il difetto del 24/08 e'
    nato proprio da una lista non aggiornata. Camminare l'albero si accorge da
    solo di una traccia che nessuno aveva previsto.
    """
    _make(workspace)
    _live_chat(workspace)
    assert _paths_naming(workspace, NAME), "il caso di prova non ha creato niente"

    _remove(workspace)

    leftover = _paths_naming(workspace, NAME)
    assert leftover == [], f"restano tracce del progetto cancellato: {leftover}"


def test_the_traces_removed_are_the_ones_the_enumeration_names(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La cancellazione **usa** l'enumerazione, non una sua copia.

    Il giorno che nasce una quinta traccia, chi la aggiunge la aggiunge in un
    posto solo. Qui se ne inventa una e si pretende che sparisca senza aver
    toccato la cancellazione.
    """
    import jenny.session.project_traces as traces

    real = traces.project_trace_paths
    invented = workspace / "sessions" / f"project_{NAME}.inventata"

    def with_a_fifth(ws: Path, key: str) -> list[Path]:
        return [*real(ws, key), invented]

    monkeypatch.setattr(traces, "project_trace_paths", with_a_fifth)

    _make(workspace)
    invented.parent.mkdir(parents=True, exist_ok=True)
    invented.write_text("x\n", encoding="utf-8")

    _remove(workspace)
    assert not invented.exists()


# ── La riproduzione del difetto, come test ───────────────────────────────


def test_create_delete_create_starts_with_an_empty_conversation(workspace: Path) -> None:
    """Il difetto del 24/08, girato al contrario."""
    _make(workspace)
    _live_chat(workspace, messages=68)
    _remove(workspace)

    result = _make(workspace)

    assert result["status"] == "created"
    assert describe_project_traces(workspace, KEY).files == 0


# ── La porta di servizio ─────────────────────────────────────────────────


def test_the_file_manager_refuses_to_delete_a_project(workspace: Path) -> None:
    _make(workspace)
    refusal = _project_delete_refusal(workspace, workspace / "wikis" / NAME)
    assert refusal is not None
    # Un rifiuto che dice **dove**: e' la forma degli altri rifiuti di questo
    # codice, e su un telefono e' la differenza fra un divieto e un vicolo cieco.
    assert "file browser" in refusal


def test_it_refuses_the_wiki_folder_inside_a_project_too(workspace: Path) -> None:
    """Stesso guasto, altra porta: senza ``wiki/`` il progetto sparisce dal picker
    e la chat resta attaccata al nome."""
    _make(workspace)
    assert _project_delete_refusal(workspace, workspace / "wikis" / NAME / "wiki") is not None


def test_it_does_not_refuse_an_ordinary_folder(workspace: Path) -> None:
    ordinary = workspace / "output" / "qualcosa"
    (ordinary / "wiki").mkdir(parents=True)
    assert _project_delete_refusal(workspace, ordinary) is None


# ── La scelta al momento della creazione ─────────────────────────────────


def test_creating_over_a_leftover_conversation_asks_instead_of_choosing(
    workspace: Path,
) -> None:
    _live_chat(workspace, messages=7)

    result = _make(workspace)

    assert result["status"] == "conversation_exists"
    assert result["conversation"]["messages"] == 7
    # Non ha toccato il disco: la domanda non e' un mezzo passo.
    assert not (workspace / "wikis" / NAME).exists()
    assert describe_project_traces(workspace, KEY).files > 0


def test_discard_starts_the_project_clean(workspace: Path) -> None:
    _live_chat(workspace)
    result = _make(workspace, conversation="discard")
    assert result["status"] == "created"
    assert describe_project_traces(workspace, KEY).files == 0


def test_keep_adopts_the_id_the_conversation_remembers(workspace: Path) -> None:
    """Riprendere una chat vuol dire dire che questo *e'* quel progetto.

    Senza l'adozione dell'id, il primo turno della chat ripresa verrebbe
    rifiutato da ``_refuse_reincarnated_project`` — giustamente.
    """
    from jenny.utils.wiki_paths import wiki_id

    _live_chat(workspace)
    session_file = project_trace_paths(workspace, KEY)[0]
    session_file.write_text(
        '{"_type": "metadata", "key": "%s", "metadata": {"%s": "abc123abc123"}}\n'
        '{"role": "user", "content": "ciao"}\n' % (KEY, PROJECT_WIKI_ID_KEY),
        encoding="utf-8",
    )

    _make(workspace, conversation="keep")

    assert wiki_id(workspace / "wikis" / NAME) == "abc123abc123"
    assert describe_project_traces(workspace, KEY).files > 0


# ── L'orfano, e come si ripulisce ────────────────────────────────────────


def test_an_orphan_conversation_can_be_deleted_without_its_folder(workspace: Path) -> None:
    """Cartella assente e chat presente e' quel che il difetto lasciava dietro.

    Su un telefono non c'e' un altro modo di ripulirlo: e' un caso da servire.
    """
    _live_chat(workspace)
    result = _remove(workspace)
    assert result["orphan"] is True
    assert describe_project_traces(workspace, KEY).files == 0


def test_deleting_a_name_that_is_nothing_at_all_is_an_error(workspace: Path) -> None:
    with pytest.raises(ProjectDeleteError):
        _remove(workspace, "mai-esistito")


def test_a_folder_that_is_not_a_project_is_not_deleted_by_this_operation(
    workspace: Path,
) -> None:
    intruder = workspace / "wikis" / NAME
    intruder.mkdir(parents=True)
    (intruder / "roba.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ProjectDeleteError):
        _remove(workspace)
    assert (intruder / "roba.txt").exists()


def test_the_cached_session_is_dropped_before_its_files_go(workspace: Path) -> None:
    """Una sessione viva in memoria riscriverebbe il file appena tolto."""
    _make(workspace)
    _live_chat(workspace)
    dropped: list[str] = []
    _remove(workspace, invalidate_session=dropped.append)
    assert dropped == [KEY]


# ── Il rilevatore: la cartella c'e', ma e' un'altra ──────────────────────


def _wiki_with_id(workspace: Path, name: str, ident: str | None) -> Path:
    project = workspace / "wikis" / name
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    (project / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    head = f"---\nid: {ident}\n---\n\n" if ident else "---\nsummary: x\n---\n\n"
    (project / "AGENTS.md").write_text(head + f"# {name}\n", encoding="utf-8")
    return project


def _msg() -> InboundMessage:
    return InboundMessage(
        channel="websocket", chat_id=NAME, sender_id="u", content="ciao"
    )


async def test_a_replaced_folder_refuses_the_turn(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    _wiki_with_id(workspace, NAME, "aaaaaaaaaaaa")
    loop._remember_project_id(KEY)
    # La cartella viene sostituita: stesso nome, anima diversa.
    _wiki_with_id(workspace, NAME, "bbbbbbbbbbbb")

    assert await loop._refuse_reincarnated_project(_msg(), KEY) is True
    assert "different" in published[0]


async def test_a_matching_id_lets_the_turn_through(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    _wiki_with_id(workspace, NAME, "aaaaaaaaaaaa")
    loop._remember_project_id(KEY)
    assert await loop._refuse_reincarnated_project(_msg(), KEY) is False
    assert published == []


async def test_a_wiki_without_an_id_never_refuses(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    """Un id assente non e' un errore: le wiki fatte a mano non ce l'hanno."""
    _wiki_with_id(workspace, NAME, None)
    loop._remember_project_id(KEY)
    assert await loop._refuse_reincarnated_project(_msg(), KEY) is False
    assert published == []
    assert wiki_id(workspace / "wikis" / NAME) is None, "nessun id da rimettere"


async def test_an_id_that_vanished_is_put_back_and_the_turn_goes_on(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    """L'id sparito mentre Jenny gira (un aiutante, adb) non e' un'altra cartella.

    Il turno passa, come prima — ma l'id si rimette subito: lasciarlo mancare
    voleva dire che il prossimo avvio ne coniava uno nuovo e chiudeva fuori la
    chat, che e' quel che e' successo a Piante sul Titan il 26/10.
    """
    _wiki_with_id(workspace, NAME, "aaaaaaaaaaaa")
    loop._remember_project_id(KEY)
    _wiki_with_id(workspace, NAME, None)

    assert await loop._refuse_reincarnated_project(_msg(), KEY) is False
    assert published == []
    assert wiki_id(workspace / "wikis" / NAME) == "aaaaaaaaaaaa"


async def test_a_vanished_id_another_folder_declares_is_left_to_it(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    _wiki_with_id(workspace, NAME, "aaaaaaaaaaaa")
    loop._remember_project_id(KEY)
    _wiki_with_id(workspace, "viaggio-copia", "aaaaaaaaaaaa")
    _wiki_with_id(workspace, NAME, None)

    assert await loop._refuse_reincarnated_project(_msg(), KEY) is False
    assert wiki_id(workspace / "wikis" / NAME) is None


async def test_a_missing_folder_is_not_this_guard_s_business(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    """La cartella che manca ha gia' il suo rifiuto, e quello sa anche inseguire."""
    _wiki_with_id(workspace, NAME, "aaaaaaaaaaaa")
    loop._remember_project_id(KEY)
    import shutil

    shutil.rmtree(workspace / "wikis" / NAME)
    assert await loop._refuse_reincarnated_project(_msg(), KEY) is False


async def test_the_personal_chat_is_never_touched(
    loop: AgentLoop, published: list[str]
) -> None:
    assert await loop._refuse_reincarnated_project(_msg(), "unified:default") is False


# ── Il conto che la conferma mostra ──────────────────────────────────────


def test_the_report_counts_messages_and_not_metadata(workspace: Path) -> None:
    _live_chat(workspace, messages=12)
    assert describe_project_traces(workspace, KEY).messages == 12


def test_an_unreadable_session_reports_no_number_rather_than_a_wrong_one(
    workspace: Path,
) -> None:
    """Un numero sbagliato in una conferma distruttiva e' peggio di nessun numero."""
    assert describe_project_traces(workspace, KEY).messages is None


def test_removing_traces_that_are_not_there_is_not_an_error(workspace: Path) -> None:
    assert delete_project_traces(workspace, KEY) == []


async def test_a_kept_conversation_is_not_refused_at_the_first_turn(
    loop: AgentLoop, workspace: Path, published: list[str]
) -> None:
    """La proprieta' per cui l'adozione dell'id esiste.

    Senza di essa la chat ripresa sarebbe la chat di un'altra wiki, e
    ``_refuse_reincarnated_project`` la fermerebbe — giustamente. Verificare
    l'id scritto nel file non basta: quel che conta e' che il turno passi.
    """
    _live_chat(workspace)
    project_trace_paths(workspace, KEY)[0].write_text(
        '{"_type": "metadata", "key": "%s", "metadata": {"%s": "abc123abc123"}}\n'
        '{"role": "user", "content": "ciao"}\n' % (KEY, PROJECT_WIKI_ID_KEY),
        encoding="utf-8",
    )
    _make(workspace, conversation="keep")

    assert await loop._refuse_reincarnated_project(_msg(), KEY) is False
    assert published == []


def test_an_unopenable_wiki_is_not_protected_by_the_refusal(workspace: Path) -> None:
    """Una cartella il cui nome non puo' essere una conversazione non ha una chat
    da orfanare — e ``project.delete`` la rifiuterebbe per il nome. Rifiutare
    anche la strada generica la renderebbe incancellabile da ogni porta."""
    odd = workspace / "wikis" / "Ricerca ETNA"
    (odd / "wiki").mkdir(parents=True)
    assert _project_delete_refusal(workspace, odd) is None


def test_deleting_a_project_takes_its_interrupted_turn_journal(workspace: Path) -> None:
    """Il diario di un turno interrotto sta accanto alla sessione: se restasse,
    il prossimo quaderno con lo stesso nome riprenderebbe il turno di un altro."""
    from jenny.session.manager import SessionManager

    _make(workspace)
    _live_chat(workspace)
    journal = SessionManager(workspace).turn_journal_path(KEY)
    journal.write_text('{"_type": "turn_journal", "stamp": "x"}\n', encoding="utf-8")

    _remove(workspace)

    assert not journal.exists(), "il diario del turno e' rimasto orfano"
