"""La coda di una sessione-progetto non si perde mai senza una copia.

``compact_idle_session`` troncava la sessione anche quando ``archive()`` era
degradata. Sulla conversazione personale il ripiego di ``archive()`` scrive il
dump grezzo in ``history.jsonl``, quindi i messaggi rimossi restano da qualche
parte; per una sessione-progetto ``append_history`` non scriveva affatto, quindi
quel dump non esisteva e la troncatura li cancellava.

**Dall'08/09/2026 il dump viene scritto anche per un progetto** (v.
``.agent/project-memory-plan.md``), e la copia dentro il progetto serve lo
stesso: sono due depositi per due lettori. Il dump in ``history.jsonl`` e' la
coda da cui Dream estrae i fatti *sulla persona*, e da un prompt di progetto non
e' raggiungibile — il filtro di ``read_recent_history_for_prompt`` lo esclude. La
copia invece resta **dentro il progetto**, l'unico posto dove chi ci lavora puo'
ritrovare quella conversazione. Percio' i test qui sotto non chiedono piu' che la
coda sia vuota: chiedono che quel che ci finisce porti la **chiave del progetto**,
che e' la cosa che lo tiene fuori dai prompt.

Questi test fissano i quattro esiti: progetto degradato con la cartella al suo
posto, progetto degradato senza cartella, progetto in salute, e la conversazione
personale — che non deve cambiare di una riga.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.memory import Consolidator, MemoryStore
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import SessionManager

PROJECT_NAME = "patreon"
PROJECT_KEY = f"project:{PROJECT_NAME}"

def _diary(store: MemoryStore) -> list[dict]:
    """Le voci finite nella coda, con la garanzia che ognuna sia attribuita.

    La chiave e' meta' del confine (l'altra meta' e' la cassetta ridotta di
    ``build_dream_tools(scope="project")``): una voce di progetto scritta senza
    ``session_key`` sarebbe indistinguibile da una personale, entrerebbe in ogni
    prompt e Dream la estrarrebbe con le regole sbagliate. Chiederlo qui, dove le
    scritture nascono, e' piu' utile che chiederlo dove si leggono.
    """
    entries = store.read_unprocessed_history(since_cursor=0)
    assert all(e.get("session_key") == PROJECT_KEY for e in entries), entries
    return entries



@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock()
    return provider


@pytest.fixture
def consolidator(store, mock_provider):
    return Consolidator(
        store=store,
        provider=mock_provider,
        model="test-model",
        sessions=SessionManager(store.workspace),
        context_window_tokens=1000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


@pytest.fixture
def project_root(tmp_path):
    """Una wiki vera: ``discover_wiki_roots`` riconosce solo chi ha ``wiki/``."""
    root = tmp_path / "wikis" / PROJECT_NAME
    (root / "wiki").mkdir(parents=True)
    return root


def _fill(consolidator, key, count=10):
    session = consolidator.sessions.get_or_create(key)
    for i in range(count):
        session.add_message("user", f"user msg {i}")
        session.add_message("assistant", f"assistant msg {i}")
    consolidator.sessions.save(session)
    return session


def _copies(project_root):
    directory = project_root / "raw" / "compacted"
    return sorted(directory.glob("*.jsonl")) if directory.is_dir() else []


class TestProjectDegradedCompaction:
    async def test_llm_failure_copies_the_tail_into_the_project(
        self, consolidator, mock_provider, project_root, store
    ):
        """(a) LLM giù: i messaggi rimossi finiscono nella cartella del progetto."""
        mock_provider.chat_with_retry.side_effect = RuntimeError("LLM unavailable")
        _fill(consolidator, PROJECT_KEY)

        result = await consolidator.compact_idle_session(PROJECT_KEY, max_suffix=4)
        assert result is None

        copies = _copies(project_root)
        assert len(copies) == 1
        recovered = [
            json.loads(line) for line in copies[0].read_text(encoding="utf-8").splitlines()
        ]
        # Ogni messaggio rimosso è recuperabile, e nella forma delle righe di un
        # file di sessione: rimetterlo dentro è un innesto.
        assert [m["content"] for m in recovered][:2] == ["user msg 0", "assistant msg 0"]

        reloaded = consolidator.sessions.get_or_create(PROJECT_KEY)
        assert len(reloaded.messages) <= 4
        kept = {m["content"] for m in reloaded.messages}
        assert len(recovered) + len(kept) == 20
        assert not kept & {m["content"] for m in recovered}

        # Il dump e' anche nella coda, e porta la chiave del progetto: e' la
        # materia da cui Dream estrarra' i fatti sulla persona, e la chiave e'
        # quel che gli impedisce di comparire in un prompt.
        assert len(_diary(store)) == 1

    async def test_llm_failure_without_a_project_folder_keeps_the_tail(
        self, consolidator, mock_provider, tmp_path, store
    ):
        """(a) Nessuna copia possibile => non si tronca: i messaggi restano vivi."""
        mock_provider.chat_with_retry.side_effect = RuntimeError("LLM unavailable")
        _fill(consolidator, PROJECT_KEY)  # nessuna cartella wikis/patreon

        result = await consolidator.compact_idle_session(PROJECT_KEY, max_suffix=4)
        assert result is None

        reloaded = consolidator.sessions.get_or_create(PROJECT_KEY)
        assert [m["content"] for m in reloaded.messages][0] == "user msg 0"
        assert len(reloaded.messages) == 20
        assert reloaded.last_consolidated == 0
        # Il dump grezzo c'e' — ``archive()`` lo scrive prima che la copia sia
        # anche solo tentata — ma **non basta a lasciar troncare**: sta in una coda
        # che il progetto non puo' leggere, e la sessione e' l'unico posto dove
        # quei messaggi sono ancora raggiungibili da chi ci lavora.
        assert len(_diary(store)) == 1
        assert list(tmp_path.glob("wikis/**/*.jsonl")) == []

    async def test_aborted_compaction_leaves_the_session_expired(
        self, consolidator, mock_provider
    ):
        """Rinuncia, non consuma: ``updated_at`` non viene rinfrescato, quindi la
        finestra di inattività successiva riprova."""
        from datetime import datetime, timedelta

        mock_provider.chat_with_retry.side_effect = RuntimeError("LLM unavailable")
        session = _fill(consolidator, PROJECT_KEY)
        old = datetime.now() - timedelta(hours=3)
        session.updated_at = old
        consolidator.sessions.save(session)

        await consolidator.compact_idle_session(PROJECT_KEY, max_suffix=4)

        reloaded = consolidator.sessions.get_or_create(PROJECT_KEY)
        assert reloaded.updated_at == old


class TestProjectHealthyCompaction:
    async def test_successful_summary_is_unchanged(
        self, consolidator, mock_provider, project_root, store
    ):
        """(b) LLM in salute: come prima — si tronca, il riassunto sta nei
        metadati, e non si scrive nessuna copia."""
        mock_provider.chat_with_retry.return_value = MagicMock(
            content="Summary of old conversation.", finish_reason="stop"
        )
        _fill(consolidator, PROJECT_KEY)

        result = await consolidator.compact_idle_session(PROJECT_KEY, max_suffix=4)
        assert result == "Summary of old conversation."

        reloaded = consolidator.sessions.get_or_create(PROJECT_KEY)
        assert len(reloaded.messages) <= 4
        assert reloaded.last_consolidated == 0
        assert reloaded.metadata["_last_summary"]["text"] == "Summary of old conversation."
        assert _copies(project_root) == []
        # Ramo felice: nella coda ci va il **riassunto**, non il dump, ed e' la
        # forma buona — Dream estrae meglio da un riassunto che da una
        # trascrizione.
        diary = _diary(store)
        assert [e["content"] for e in diary] == ["Summary of old conversation."]

    async def test_nothing_summary_is_unchanged(
        self, consolidator, mock_provider, project_root
    ):
        """(d) ``(nothing)`` è un riassunto riuscito: si tronca, niente copia,
        niente ``_last_summary``."""
        mock_provider.chat_with_retry.return_value = MagicMock(
            content="(nothing)", finish_reason="stop"
        )
        _fill(consolidator, PROJECT_KEY)

        result = await consolidator.compact_idle_session(PROJECT_KEY, max_suffix=4)
        assert result == "(nothing)"

        reloaded = consolidator.sessions.get_or_create(PROJECT_KEY)
        assert len(reloaded.messages) <= 4
        assert "_last_summary" not in reloaded.metadata
        assert _copies(project_root) == []


class TestPersonalSessionUnchanged:
    async def test_llm_failure_raw_dumps_to_history_and_truncates(
        self, consolidator, mock_provider, project_root, store, tmp_path
    ):
        """(c) La conversazione personale non cambia: dump in ``history.jsonl``,
        sessione troncata, e nessuna copia dentro un progetto."""
        mock_provider.chat_with_retry.side_effect = RuntimeError("LLM unavailable")
        _fill(consolidator, UNIFIED_SESSION_KEY)

        result = await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=4)
        assert result is None

        entries = store.read_unprocessed_history(since_cursor=0)
        raw = "\n".join(entry["content"] for entry in entries)
        assert "[RAW]" in raw
        assert "user msg 0" in raw
        assert entries[0]["session_key"] == UNIFIED_SESSION_KEY

        reloaded = consolidator.sessions.get_or_create(UNIFIED_SESSION_KEY)
        assert len(reloaded.messages) <= 4
        assert _copies(project_root) == []
        assert list(tmp_path.glob("wikis/**/*.jsonl")) == []


class TestTheProjectsSubdirIsInjected:
    """La cartella dei progetti arriva da ``config.wiki.wikis_dir``, non dal default.

    Senza questo, su un'installazione che ha spostato i progetti la ricerca non
    trova mai la cartella: la compattazione di un progetto viene rifiutata a ogni
    finestra e nessuno se ne accorge, perché il rifiuto *è* il comportamento
    prudente. Un difetto silenzioso travestito da prudenza.
    """

    async def test_a_configured_wikis_dir_is_where_the_copy_lands(
        self, store, mock_provider, tmp_path
    ):
        root = tmp_path / "progetti" / PROJECT_NAME
        (root / "wiki").mkdir(parents=True)
        consolidator = Consolidator(
            store=store,
            provider=mock_provider,
            model="test-model",
            sessions=SessionManager(store.workspace),
            context_window_tokens=1000,
            build_messages=MagicMock(return_value=[]),
            get_tool_definitions=MagicMock(return_value=[]),
            max_completion_tokens=100,
            projects_subdir="progetti",
        )
        mock_provider.chat_with_retry.side_effect = RuntimeError("LLM unavailable")
        _fill(consolidator, PROJECT_KEY)

        await consolidator.compact_idle_session(PROJECT_KEY, max_suffix=4)

        copies = _copies(root)
        assert len(copies) == 1, "la copia non è finita nella wikis_dir configurata"
        lines = copies[0].read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(lines[0])["content"] == "user msg 0"
        assert list((tmp_path / "wikis").glob("**/*.jsonl")) == []

    def test_the_loop_passes_the_configured_subdir_through(self):
        """Il cablaggio, non il comportamento: se ``AgentLoop`` smette di passarlo,
        il test sopra resta verde e il difetto torna in produzione."""
        import inspect

        from jenny.agent.loop import AgentLoop

        source = inspect.getsource(AgentLoop.__init__)
        consolidator_call = source.split("self.consolidator = Consolidator(")[1]
        consolidator_call = consolidator_call.split(")")[0]
        assert "projects_subdir=projects_subdir" in consolidator_call


def test_the_loop_passes_the_configured_subdir_to_autocompact():
    """Stesso cablaggio, secondo consumatore.

    ``AutoCompact`` cerca la cartella del progetto per sapere se il giardiniere ha
    già promosso quel che la conversazione ha detto. Col default al posto della
    ``wikis_dir`` configurata non la troverebbe mai: rimanderebbe la compattazione
    per sempre — esito prudente, quindi invisibile — e nel log si leggerebbe una
    scelta e non un guasto.
    """
    import inspect

    from jenny.agent.loop import AgentLoop

    source = inspect.getsource(AgentLoop.__init__)
    call = source.split("self.auto_compact = AutoCompact(")[1].split(")")[0]
    assert "projects_subdir=projects_subdir" in call
