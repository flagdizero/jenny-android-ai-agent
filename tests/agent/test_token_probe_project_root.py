"""La sonda dei token misura il prompt che il turno costruira' davvero.

Passo **T3.8** delle correzioni al taccuino.

``Consolidator.estimate_session_prompt_tokens`` chiamava ``build_messages``
**senza** ``workspace=``: per una sessione ``project:*`` il prompt di prova
veniva costruito sulla radice dell'installazione, dove ``is_wiki_root`` e' falso
— quindi il blocco di progetto (mappa, pagine, i due conteggi, l'``AGENTS.md``
del progetto) restava fuori dalla stima.

Non era un numero curioso. Quel numero ha **due** consumatori:

1. ``maybe_consolidate_by_tokens``, cioe' la decisione di compattare;
2. ``/status``, cioe' quel che l'utente legge.

Entrambi leggevano un valore sistematicamente basso proprio sulle sessioni il
cui prompt e' cresciuto di piu' — le sessioni di progetto, quelle che T3/T6.4
hanno riempito d'ufficio. La compattazione arrivava tardi dove serviva prima.

La radice si chiede a ``WorkspaceScopeResolver.for_project``, la stessa chiamata
che ``AgentLoop`` fa per le sessioni di progetto: la sonda deve misurare il
prompt del turno, e due strade per la stessa cartella divergono.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest

from jenny.agent.consolidator import Consolidator
from jenny.agent.context import ContextBuilder
from jenny.agent.memory import MemoryStore
from jenny.security.workspace_access import WorkspaceScopeResolver
from jenny.session.manager import SessionManager
from jenny.utils.helpers import estimate_prompt_tokens_chain

PROJECT = "casa"
PROJECT_KEY = f"project:{PROJECT}"
PERSONAL_KEY = "unified:default"

# Una mappa e due pagine grosse: il blocco vero misura migliaia di caratteri, e
# un blocco piccolo non distinguerebbe "conta" da "non conta".
_MAP = "# casa\n\n## Pagine\n\n- [[furgone]] — il Ducato\n- [[tetto]] — i coppi\n"
_PAGES = {
    "furgone.md": "---\nstate: open\n---\n\n# Furgone\n\n" + ("Ducato 2011, turbo da cambiare. " * 90),
    "tetto.md": "---\nstate: decided\n---\n\n# Tetto\n\n" + ("Coppi rotti sul lato nord. " * 90),
}


def _empty_project(root: pathlib.Path) -> pathlib.Path:
    """Una wiki senza mappa e senza pagine: e' una wiki (ha ``wiki/``), muta."""
    project = root / "wikis" / PROJECT
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    return project


def _fill(project: pathlib.Path) -> None:
    (project / "wiki" / "index.md").write_text(_MAP, encoding="utf-8")
    for name, body in _PAGES.items():
        (project / "wiki" / name).write_text(body, encoding="utf-8")


def _consolidator(root: pathlib.Path) -> tuple[Consolidator, ContextBuilder, SessionManager]:
    provider = MagicMock()
    provider.generation.max_tokens = 4096
    builder = ContextBuilder(root)
    sessions = SessionManager(root)
    consolidator = Consolidator(
        store=MemoryStore(root),
        provider=provider,
        model="test-model",
        sessions=sessions,
        context_window_tokens=100_000,
        build_messages=builder.build_messages,
        get_tool_definitions=lambda: [],
        max_completion_tokens=4096,
    )
    return consolidator, builder, sessions


def _session(sessions: SessionManager, key: str):
    session = sessions.get_or_create(key)
    session.messages = [
        {"role": "user", "content": "ciao", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "ciao a te", "timestamp": "2026-01-01T00:00:01"},
    ]
    return session


def _block_chars(builder: ContextBuilder, project: pathlib.Path) -> int:
    """I caratteri di contenuto che il blocco inietta: mappa piu' pagine."""
    block = builder._project_block_vars(project)
    return len(block["project_map"]) + len(block["project_pages"])


# ── (a) la stima cresce col blocco ────────────────────────────────────────


def test_the_estimate_grows_with_the_project_block(tmp_path) -> None:
    """**Cresce col blocco**, e non di un numero fisso da pinnare: quel che si
    verifica e' che i caratteri che il blocco spedisce siano nel conto.

    La sessione e' la stessa nelle due misure — cambia solo il disco — quindi la
    differenza *e'* il blocco e nient'altro.
    """
    project = _empty_project(tmp_path)
    consolidator, builder, sessions = _consolidator(tmp_path)
    session = _session(sessions, PROJECT_KEY)

    lean, _ = consolidator.estimate_session_prompt_tokens(session)
    _fill(project)
    full, _ = consolidator.estimate_session_prompt_tokens(session)

    injected = _block_chars(builder, project)
    assert injected > 4_000, "blocco troppo piccolo: il test non misurerebbe niente"
    # L'euristica di ``estimate_prompt_tokens`` vale circa un token ogni quattro
    # caratteri; il margine tiene largo perche' qui si misura il verso, non la
    # taratura del contatore.
    assert full - lean >= injected // 5, (
        f"il blocco misura {injected} caratteri e la stima e' cresciuta di "
        f"{full - lean} token: la sonda non lo sta contando"
    )


def test_the_probe_builds_the_prompt_on_the_root_the_turn_uses(tmp_path) -> None:
    """La stima *e'* quella del prompt del turno, non una sua approssimazione.

    La radice viene dallo stesso ``for_project`` che ``AgentLoop`` chiama per le
    sessioni di progetto: se la sonda ne ricavasse una sua, questa uguaglianza
    sarebbe il primo posto a rompersi.
    """
    project = _empty_project(tmp_path)
    _fill(project)
    consolidator, builder, sessions = _consolidator(tmp_path)
    session = _session(sessions, PROJECT_KEY)

    expected_root = WorkspaceScopeResolver(
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    ).for_project(PROJECT_KEY).project_path
    assert consolidator._probe_workspace(PROJECT_KEY) == expected_root
    assert expected_root.resolve() == project.resolve()

    estimated, _ = consolidator.estimate_session_prompt_tokens(session)
    turn_messages = builder.build_messages(
        history=consolidator._full_unconsolidated_history(session),
        current_message="[token-probe]",
        channel="project",
        chat_id=PROJECT,
        sender_id=None,
        session_summary=None,
        session_metadata=session.metadata,
        session_key=PROJECT_KEY,
        workspace=expected_root,
    )
    expected, _ = estimate_prompt_tokens_chain(
        consolidator.provider, consolidator.model, turn_messages, []
    )
    assert estimated == expected


def test_a_configured_wikis_dir_is_where_the_probe_looks(tmp_path) -> None:
    """La sottocartella e' quella iniettata, non ``"wikis"``.

    Stesso difetto silenzioso di ``_project_dump_dir``: col default al posto
    della ``config.wiki.wikis_dir`` configurata la sonda misurerebbe una cartella
    che non c'e', cioe' tornerebbe al numero basso senza dirlo.
    """
    project = tmp_path / "progetti" / PROJECT
    (project / "wiki").mkdir(parents=True)
    _fill(project)
    provider = MagicMock()
    provider.generation.max_tokens = 4096
    consolidator = Consolidator(
        store=MemoryStore(tmp_path),
        provider=provider,
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=100_000,
        build_messages=ContextBuilder(tmp_path).build_messages,
        get_tool_definitions=lambda: [],
        max_completion_tokens=4096,
        projects_subdir="progetti",
    )

    assert consolidator._probe_workspace(PROJECT_KEY).resolve() == project.resolve()


# ── (b) la sessione personale non cambia ──────────────────────────────────


def test_a_personal_session_is_measured_exactly_as_before(tmp_path) -> None:
    """Fuori dai progetti la radice resta ``None``, cioe' il default di
    ``build_messages``: la sonda non ha imparato a misurare *altro*, ha imparato
    a misurare la cartella giusta."""
    project = _empty_project(tmp_path)
    _fill(project)
    consolidator, builder, sessions = _consolidator(tmp_path)
    session = _session(sessions, PERSONAL_KEY)

    assert consolidator._probe_workspace(PERSONAL_KEY) is None

    estimated, _ = consolidator.estimate_session_prompt_tokens(session)
    unscoped = builder.build_messages(
        history=consolidator._full_unconsolidated_history(session),
        current_message="[token-probe]",
        channel="unified",
        chat_id="default",
        sender_id=None,
        session_summary=None,
        session_metadata=session.metadata,
        session_key=PERSONAL_KEY,
    )
    expected, _ = estimate_prompt_tokens_chain(
        consolidator.provider, consolidator.model, unscoped, []
    )
    assert estimated == expected


def test_a_project_growing_does_not_move_the_personal_estimate(tmp_path) -> None:
    """Il controfattuale del test (a): le stesse pagine che fanno crescere la
    stima del progetto non devono muovere quella della chat personale, altrimenti
    il blocco sarebbe entrato dove non c'entra."""
    project = _empty_project(tmp_path)
    consolidator, _builder, sessions = _consolidator(tmp_path)
    session = _session(sessions, PERSONAL_KEY)

    before, _ = consolidator.estimate_session_prompt_tokens(session)
    _fill(project)
    after, _ = consolidator.estimate_session_prompt_tokens(session)

    assert before == after


def test_an_internal_session_keeps_the_installation_root(tmp_path) -> None:
    """Cron e Dream non sono sessioni di progetto: la chiave non ha il
    prefisso e la sonda non deve inventarne uno."""
    consolidator, _builder, _sessions = _consolidator(tmp_path)

    for key in ("internal:cron", "internal:dream", "telegram:42"):
        assert consolidator._probe_workspace(key) is None


# ── (c) /status legge la cifra grande ─────────────────────────────────────


@pytest.mark.asyncio
async def test_status_reports_the_project_aware_figure(tmp_path) -> None:
    """``/status`` e la decisione di compattare leggono lo **stesso** numero, e
    finche' la sonda ignorava la cartella l'utente vedeva "7k" per un prompt da
    "9k". Il test passa dal vero ``cmd_status``, non dalla sonda: e' il consumo
    che conta.

    Le due cifre attese si calcolano **fuori** dalla sonda, dalle due radici
    possibili: cosi' l'asserzione dice quale delle due la riga porta, e non
    ripete quel che la sonda ha risposto.
    """
    from jenny.agent.loop import AgentLoop
    from jenny.bus.events import InboundMessage
    from jenny.bus.queue import MessageBus
    from jenny.command.builtin import cmd_status
    from jenny.command.router import CommandContext

    project = _empty_project(tmp_path)
    _fill(project)
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=200_000,
    )
    session = _session(loop.sessions, PROJECT_KEY)
    loop.sessions.save(session)

    def _figure(root: pathlib.Path | None) -> int:
        messages = loop.context.build_messages(
            history=loop.consolidator._full_unconsolidated_history(session),
            current_message="[token-probe]",
            channel="project",
            chat_id=PROJECT,
            sender_id=None,
            session_summary=None,
            session_metadata=session.metadata,
            session_key=PROJECT_KEY,
            workspace=root,
        )
        tokens, _ = estimate_prompt_tokens_chain(
            provider, "test-model", messages, loop.consolidator._get_tool_definitions()
        )
        return tokens

    project_aware = _figure(project)
    blind = _figure(None)  # la cifra di prima di T3.8: la radice dell'installazione
    assert project_aware // 1000 > blind // 1000, (
        "le due cifre si arrotondano allo stesso migliaio: il test non le distingue"
    )

    msg = InboundMessage(
        channel="websocket", chat_id="ui", content="/status", sender_id=None
    )
    out = await cmd_status(
        CommandContext(msg=msg, session=session, key=PROJECT_KEY, raw="/status", loop=loop)
    )

    context_line = next(line for line in out.content.splitlines() if "Context:" in line)
    assert f"{project_aware // 1000}k/" in context_line
    assert f"{blind // 1000}k/" not in context_line
