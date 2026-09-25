"""Aprire un progetto: la cartella si deduce dalla chiave della sessione.

Il porto dello scope esisteva già ed era collaudato — risolto a ogni turno, i
subagent lo ereditano, le viste lo rispettano — ma **nessuno in produzione lo
scriveva**. Questo è il produttore, e la forma scelta è la più stretta possibile:
`project:patreon` → `<workspace>/wikis/patreon`, punto.

Il nome viaggia nel messaggio (come `chat_id`), la cartella la deduce il server.
Le alternative erano peggiori in modo istruttivo: se il client mandasse un
percorso, il validatore accetta *qualunque* directory esistente e assoluta; se il
server ricordasse "il progetto aperto", quel ricordo potrebbe divergere da quel
che il chip mostra — e un messaggio finito nello scope sbagliato è l'unico guasto
irrecuperabile di questo disegno. Deducendola dalla chiave, sessione e cartella
non *possono* divergere: non c'è un secondo dato da tenere allineato.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.security.workspace_access import WorkspaceScopeResolver
from jenny.session.keys import (
    is_valid_project_name,
    project_session_key,
    session_key_for_channel,
)

PERSONAL = "unified:default"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "wikis" / "patreon").mkdir(parents=True)
    (ws / "wikis" / "etf").mkdir(parents=True)
    return ws


@pytest.fixture
def resolver(workspace: Path) -> WorkspaceScopeResolver:
    return WorkspaceScopeResolver(
        default_workspace=workspace,
        default_restrict_to_workspace=True,
    )


def _turn(resolver: WorkspaceScopeResolver, key: str | None, channel: str = "websocket"):
    return resolver.for_turn(
        channel=channel,
        message_metadata=None,
        session_metadata=None,
        session_key=key,
    )


# ── dalla chiave alla cartella ───────────────────────────────────────────────


class TestTheFolderComesFromTheKey:
    def test_a_project_works_in_its_own_wiki(self, resolver, workspace):
        scope = _turn(resolver, project_session_key("patreon"))

        assert scope.project_path == (workspace / "wikis" / "patreon").resolve()
        assert scope.access_mode == "restricted"

    def test_two_projects_do_not_touch(self, resolver, workspace):
        assert _turn(resolver, project_session_key("patreon")).project_path != _turn(
            resolver, project_session_key("etf")
        ).project_path

    def test_the_personal_conversation_stays_on_the_root(self, resolver, workspace):
        assert _turn(resolver, PERSONAL).project_path == workspace.resolve()

    def test_respects_the_configured_folder(self, tmp_path: Path):
        """Deve essere la stessa che il picker elenca (`config.wiki.wikis_dir`).

        Se le due divergessero, il chip mostrerebbe progetti che lo scope non
        trova — cioè ogni progetto legato punterebbe a una cartella mancante.
        """
        ws = tmp_path / "workspace"
        (ws / "kb" / "patreon").mkdir(parents=True)
        resolver = WorkspaceScopeResolver(
            default_workspace=ws,
            default_restrict_to_workspace=True,
            projects_subdir="kb",
        )

        scope = _turn(resolver, project_session_key("patreon"))

        assert scope.project_path == (ws / "kb" / "patreon").resolve()


class TestWhenSomethingDoesNotAddUp:
    def test_a_vanished_folder_does_not_fall_back_to_the_personal_root(
        self, resolver, workspace, caplog
    ):
        """Meglio un progetto che non riesce a scrivere che uno che scrive a casa.

        Il fallback silenzioso metterebbe il lavoro di un progetto nel workspace
        personale. Qui lo scope resta puntato al posto che manca — le scritture
        falliscono tutte — e c'è un WARNING. Trasformarlo in un rifiuto detto a
        voce è il passo 6.
        """
        scope = _turn(resolver, project_session_key("mai-esistita"))

        assert scope.project_path == (workspace / "wikis" / "mai-esistita").resolve()
        assert scope.project_path != workspace.resolve()

    def test_a_climb_in_the_name_does_not_leave_the_projects_folder(
        self, resolver, workspace
    ):
        """Difesa in profondità: `session_key_for_channel` non lascerebbe mai
        passare un nome così, ma questa funzione non lo sa."""
        scope = _turn(resolver, "project:../..")

        assert scope.project_path == workspace.resolve()

    def test_a_channel_other_than_the_webui_has_no_projects(self, resolver, workspace):
        """Un progetto è una sessione di lavoro alla tastiera: la vita fuori di
        Jenny — Telegram, cron, avvisi — non ci entra."""
        scope = _turn(resolver, project_session_key("patreon"), channel="telegram")

        assert scope.project_path == workspace.resolve()

    def test_a_project_is_restricted_even_with_restriction_off(self, workspace):
        """La docstring di ``for_project`` dice «sempre ``restricted``, non c'è modo
        di chiedere il contrario» — e fino a T4.12 **nessun test lo provava**.

        Trovato per mutazione il 23/08: sostituire il letterale ``"restricted"``
        con ``default_access_mode(self.default_restrict_to_workspace)`` passava la
        suite intera, perché ogni test di questo albero costruisce il resolver con
        ``restrict_to_workspace=True`` e i due valori coincidono. Il mutante è
        vivo solo con la restrizione spenta, che è una configurazione che esiste
        (``config.security.restrict_to_workspace = false``): là un progetto
        avrebbe ereditato ``full``, cioè la scrittura di un progetto avrebbe
        smesso di stare nella sua cartella.
        """
        resolver = WorkspaceScopeResolver(
            default_workspace=workspace,
            default_restrict_to_workspace=False,
        )
        # Controprova che la restrizione è davvero spenta: la conversazione
        # personale, sullo stesso resolver, non è ristretta.
        assert resolver.default().access_mode == "full"
        assert resolver.default().restrict_to_workspace is False

        scope = resolver.for_project(project_session_key("patreon"))

        assert scope.project_path == (workspace / "wikis" / "patreon").resolve()
        assert scope.access_mode == "restricted"
        assert scope.restrict_to_workspace is True


# ── dal `chat_id` alla chiave ────────────────────────────────────────────────


class TestFromTheChatIdToTheKey:
    @pytest.mark.parametrize(
        ("chat_id", "expected"),
        [
            ("default", PERSONAL),
            ("project:patreon", "project:patreon"),
            ("project:etf-finance", "project:etf-finance"),
            # Forme che un client non deve poter usare per farsi creare una
            # sessione: cadono sulla conversazione personale, non su una nuova.
            ("project:../fuori", PERSONAL),
            ("project:a/b", PERSONAL),
            ("project:.nascosto", PERSONAL),
            ("project:", PERSONAL),
            ("inventato", PERSONAL),
            ("", PERSONAL),
        ],
    )
    def test_only_recognized_forms_open_a_session(self, chat_id, expected):
        assert session_key_for_channel("websocket", chat_id) == expected

    def test_telegram_does_not_open_projects(self):
        assert session_key_for_channel("telegram", "project:patreon") == PERSONAL

    @pytest.mark.parametrize(
        ("name", "ok"),
        [
            ("patreon", True),
            ("etf-finance", True),
            ("a_b.c", True),
            ("..", False),
            ("../x", False),
            (".hidden", False),
            ("con spazio", False),
            ("", False),
            ("a" * 65, False),
        ],
    )
    def test_a_name_has_only_one_form(self, name, ok):
        """La stessa funzione decide cosa si può creare e cosa si può aprire: un
        nome accettato alla creazione e rifiutato all'apertura darebbe un
        progetto che esiste e non si apre."""
        assert is_valid_project_name(name) is ok


# ── la catena intera ─────────────────────────────────────────────────────────


class TestTheChainIsReallyConnected:
    """I singoli anelli hanno i loro test; questo prova che si toccano.

    `chat_id` -> chiave di sessione -> scope del turno -> prompt. È il giro che
    un difetto di cablaggio romperebbe lasciando verdi tutti gli altri test.
    """

    def test_from_chat_id_to_prompt(self, resolver, workspace, monkeypatch):
        from jenny.agent.context import ContextBuilder
        from jenny.bus.events import InboundMessage

        (workspace / "SOUL.md").write_text("sono fatta così", encoding="utf-8")
        (workspace / "wikis" / "patreon" / "AGENTS.md").write_text(
            "qui si scrive di Patreon", encoding="utf-8"
        )

        msg = InboundMessage(
            channel="websocket",
            sender_id="me",
            chat_id="project:patreon",
            content="ciao",
        )
        assert msg.session_key == "project:patreon"

        scope = resolver.for_message(msg, session_metadata=None)
        assert scope.project_path == (workspace / "wikis" / "patreon").resolve()

        prompt = ContextBuilder(workspace).build_system_prompt(
            workspace=scope.project_path, session_key=msg.session_key
        )
        # Le istruzioni del progetto ci sono, l'identità pure...
        assert "qui si scrive di Patreon" in prompt
        assert "sono fatta così" in prompt
        # ...e la coda del diario personale no (il confine del 21/08).
        assert "# Recent History" not in prompt

    def test_the_same_round_for_the_personal_conversation_does_not_change(
        self, resolver, workspace
    ):
        from jenny.bus.events import InboundMessage

        msg = InboundMessage(
            channel="websocket", sender_id="me", chat_id="default", content="ciao"
        )

        assert msg.session_key == PERSONAL
        assert resolver.for_message(msg, session_metadata=None).project_path == (
            workspace.resolve()
        )


class TestTheLoopUsesTheMessageKey:
    """L'anello che mancava, e che solo il telefono ha mostrato.

    `AgentLoop._effective_session_key` aveva `UNIFIED_SESSION_KEY` cablato. Il
    chiamante confronta il suo valore con `msg.session_key` e, se differiscono,
    **riscrive il messaggio** con un override: quella costante non ignorava la
    chiave del messaggio, la sovrascriveva. Un messaggio mandato a
    `project:patreon` finiva nella conversazione personale — e tutti i test degli
    anelli restavano verdi, perché nessuno provava la catena.
    """

    def test_a_project_message_is_not_hijacked(self):
        from jenny.agent.loop import AgentLoop
        from jenny.bus.events import InboundMessage

        msg = InboundMessage(
            channel="websocket",
            sender_id="me",
            chat_id="project:patreon",
            content="ciao",
        )

        assert AgentLoop._effective_session_key(None, msg) == "project:patreon"

    def test_the_personal_conversation_stays_where_it_was(self):
        from jenny.agent.loop import AgentLoop
        from jenny.bus.events import InboundMessage

        msg = InboundMessage(
            channel="websocket", sender_id="me", chat_id="default", content="ciao"
        )

        assert AgentLoop._effective_session_key(None, msg) == PERSONAL

    def test_an_explicit_override_still_wins(self):
        """È così che cron e Dream si portano la propria sessione."""
        from jenny.agent.loop import AgentLoop
        from jenny.bus.events import InboundMessage

        msg = InboundMessage(
            channel="websocket",
            sender_id="me",
            chat_id="project:patreon",
            content="ciao",
            session_key_override="cron:job-1",
        )

        assert AgentLoop._effective_session_key(None, msg) == "cron:job-1"


class TestTheChannelReadsTheChatIdFromTheFrame:
    """Il secondo anello mancante, trovato dallo stesso test sul telefono.

    `WebSocketChannel._dispatch_envelope` sostituiva il `chat_id` del frame con
    la costante `default`: era così che la "sessione unica" era implementata, e
    va benissimo finché di conversazioni ce n'è una. Con i progetti diventa il
    punto in cui un messaggio mandato a `project:patreon` finisce nella chat
    personale — e dal lato client sembra partito, quindi non lo dice nessuno.
    """

    @pytest.mark.parametrize(
        ("frame_chat_id", "expected"),
        [
            ("default", "default"),
            ("project:patreon", "project:patreon"),
            # Forme che non nominano nessun progetto: la chat personale è la sola
            # risposta sensata, perché *è* la conversazione e non c'è niente da
            # rifiutare.
            ("qualsiasi-cosa", "default"),
            (None, "default"),
            (12, "default"),
            # Nella forma `project:` ma con un nome impossibile: `None`, cioè
            # «rifiuta il frame». Un `chat_id` è anche il nome del file di thread
            # su disco, quindi non può passare; ma il client ha detto *quale*
            # conversazione vuole, e dargliene un'altra in silenzio era il difetto.
            ("project:../fuori", None),
            ("project:a/b", None),
            ("project:Ricerca ETF", None),
            ("project:università", None),
            ("project:progetto (2026)", None),
            ("project:.nascosto", None),
            ("project:", None),
            (f"project:{'x' * 65}", None),
        ],
    )
    def test_only_a_valid_project_changes_conversation(self, frame_chat_id, expected):
        from jenny.channels.websocket import WebSocketChannel

        envelope = {"type": "message", "content": "ciao"}
        if frame_chat_id is not None:
            envelope["chat_id"] = frame_chat_id

        assert WebSocketChannel._envelope_chat_id(envelope) == expected
