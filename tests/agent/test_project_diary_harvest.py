"""La raccolta del diario di un progetto: leggere senza accorciare.

**Fase D di ``.agent/project-memory-plan.md``, e la fase senza la quale la
corsia esisteva a vuoto.** Aperta la scrittura di ``append_history`` a una chiave
``project:``, il trasporto restava quello della compattazione — un riassunto lo
produce solo chi compatta — e la compattazione per inattivita' i progetti non li
tocca di proposito. Misurato sul telefono l'08/09/2026: **3 sessioni di progetto
su 9** erano mai state consolidate, e fra le sei escluse c'erano quelle da 54,
64, 78 e 80 messaggi, cioe' le conversazioni piu' ricche di fatti sulla persona.

Da cui la forma di questo lavoro, che e' l'opposto di una compattazione: legge i
messaggi nuovi, ne mette un riassunto nella coda **con la chiave del progetto**,
e lascia la sessione esattamente com'era. I due cancelli che difendono la
compattazione di un progetto — il recinto sull'inattivita' e
``_pages_carry_the_project`` — qui non c'entrano: difendono i messaggi
dall'essere buttati, e qui non si butta niente. Il recinto vive in
``test_autocompact_project_fence.py``, e le due meta' vanno lette insieme.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.autocompact import AutoCompact
from jenny.agent.memory import Consolidator, MemoryStore
from jenny.session.manager import SessionManager

PROJECT = "project:patreon"
PERSONAL = "unified:default"


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


@pytest.fixture
def consolidator(tmp_path: Path, store: MemoryStore) -> Consolidator:
    """Un ``Consolidator`` vero con un provider finto.

    Vero di proposito: il pezzo da provare e' che il riassunto **arrivi nella
    coda con la chiave giusta**, e quel percorso — ``archive`` →
    ``append_history`` — e' esattamente quel che un doppio nasconderebbe.
    """
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=MagicMock(content="- fatto sulla persona", finish_reason="stop")
    )
    return Consolidator(
        store=store,
        sessions=SessionManager(tmp_path),
        provider=provider,
        model="m",
        context_window_tokens=100_000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


@pytest.fixture
def autocompact(consolidator: Consolidator) -> AutoCompact:
    return AutoCompact(
        sessions=consolidator.sessions, consolidator=consolidator, session_ttl_minutes=30
    )


def _talked(
    autocompact: AutoCompact, key: str, turns: int = 3, *, stale: bool = True, tag: str = "a"
):
    session = autocompact.sessions.get_or_create(key)
    for i in range(turns):
        session.messages.append({"role": "user", "content": f"messaggio {tag}{i}"})
        session.messages.append({"role": "assistant", "content": f"risposta {tag}{i}"})
    if stale:
        session.updated_at = datetime.now() - timedelta(hours=6)
    autocompact.sessions.save(session)
    return session


class TestLaRaccoltaLegge:
    async def test_il_riassunto_finisce_nella_coda_con_la_chiave_del_progetto(
        self, autocompact, store
    ):
        _talked(autocompact, PROJECT)

        await autocompact._harvest_project_diary(PROJECT)

        entries = store.read_unprocessed_history(since_cursor=0)
        assert [e["content"] for e in entries] == ["- fatto sulla persona"]
        # La chiave e' meta' del confine: senza, la voce entrerebbe in ogni
        # prompt e Dream la estrarrebbe con le regole della conversazione
        # personale.
        assert entries[0]["session_key"] == PROJECT

    async def test_la_sessione_non_perde_un_messaggio(self, autocompact):
        """La differenza con la compattazione, detta come asserzione.

        Un progetto puo' stare fermo tre settimane e riprendere dove era: e' il
        suo mestiere. Se questo lavoro accorciasse la conversazione sarebbe una
        compattazione con un altro nome, e il recinto che la vieta sarebbe
        aggirato da qui.
        """
        _talked(autocompact, PROJECT)
        before = list(autocompact.sessions.get_or_create(PROJECT).messages)

        await autocompact._harvest_project_diary(PROJECT)

        assert autocompact.sessions.get_or_create(PROJECT).messages == before

    async def test_non_tocca_last_summary(self, autocompact):
        """``_last_summary`` e' il meccanismo della compattazione, non di questo.

        E' quel che ``prepare_session`` reinietta quando una sessione riparte:
        scriverlo qui direbbe alla conversazione del progetto che e' stata
        compattata quando non lo e' stata.
        """
        _talked(autocompact, PROJECT)

        await autocompact._harvest_project_diary(PROJECT)

        assert "_last_summary" not in autocompact.sessions.get_or_create(PROJECT).metadata


class TestLIndiceNonRilegge:
    async def test_una_seconda_passata_senza_messaggi_nuovi_non_fa_niente(
        self, autocompact, store
    ):
        _talked(autocompact, PROJECT)
        await autocompact._harvest_project_diary(PROJECT)

        await autocompact._harvest_project_diary(PROJECT)

        assert len(store.read_unprocessed_history(since_cursor=0)) == 1

    async def test_la_seconda_passata_riassume_solo_il_nuovo(
        self, autocompact, consolidator
    ):
        _talked(autocompact, PROJECT, turns=2, tag="vecchio")
        await autocompact._harvest_project_diary(PROJECT)
        _talked(autocompact, PROJECT, turns=1, tag="nuovo")

        await autocompact._harvest_project_diary(PROJECT)

        # Il secondo giro ha visto solo lo scambio nuovo: e' la sola prova che
        # l'indice serva a qualcosa invece di essere scritto e mai letto.
        payload = consolidator.provider.chat_with_retry.call_args_list[-1].kwargs[
            "messages"
        ][-1]["content"]
        assert "messaggio vecchio0" not in payload
        assert "messaggio nuovo0" in payload

    async def test_sotto_la_soglia_non_spende_una_chiamata(
        self, autocompact, consolidator
    ):
        """Una sessione senza niente di nuovo non deve costare un turno di LLM.

        Il giro TTL passa ogni minuto: senza questa soglia, ogni progetto fermo
        pagherebbe una chiamata a ogni finestra di inattivita'.
        """
        session = autocompact.sessions.get_or_create(PROJECT)
        session.messages.append({"role": "user", "content": "una riga sola"})
        autocompact.sessions.save(session)

        await autocompact._harvest_project_diary(PROJECT)

        consolidator.provider.chat_with_retry.assert_not_called()

    async def test_un_indice_oltre_la_fine_non_rilegge_tutto(self, autocompact, store):
        """Se qualcuno compatta in mezzo, l'indice resta indietro rispetto ai
        messaggi ma **avanti** rispetto a quel che e' rimasto.

        Ripartire da zero produrrebbe un doppione di quel che e' gia' nella coda;
        il ``min`` fa ripartire dalla fine, cioe' da niente.
        """
        session = _talked(autocompact, PROJECT, turns=3)
        session.metadata[AutoCompact._DIARY_HARVEST_KEY] = 999
        autocompact.sessions.save(session)

        await autocompact._harvest_project_diary(PROJECT)

        assert store.read_unprocessed_history(since_cursor=0) == []


class TestIlGiroPianificaLaRaccolta:
    def test_un_progetto_scaduto_viene_raccolto(self, autocompact):
        _talked(autocompact, PROJECT)

        scheduled: list = []
        autocompact.check_expired(scheduled.append)

        assert [getattr(c, "__name__", "") for c in scheduled] == ["_harvest_project_diary"]
        for coro in scheduled:
            coro.close()

    def test_un_progetto_ancora_attivo_non_viene_raccolto(self, autocompact):
        """La sessione su cui l'utente sta scrivendo adesso resta fuori: il
        riassunto di una conversazione a meta' e' una conversazione a meta'."""
        _talked(autocompact, PROJECT, stale=False)

        scheduled: list = []
        autocompact.check_expired(scheduled.append)

        assert scheduled == []

    def test_una_sessione_in_corso_di_turno_resta_fuori(self, autocompact):
        _talked(autocompact, PROJECT)

        scheduled: list = []
        autocompact.check_expired(scheduled.append, active_session_keys={PROJECT})

        assert scheduled == []

    def test_con_la_manopola_accesa_non_si_raccoglie_due_volte(self, consolidator):
        """Chi compatta riassume gia', e quel riassunto va nella stessa coda.

        Con ``compact_projects_when_idle`` acceso i due lavori si sovrapporrebbero
        sulla stessa materia: la raccolta si tira indietro e lascia fare alla
        compattazione.
        """
        compacting = AutoCompact(
            sessions=consolidator.sessions,
            consolidator=consolidator,
            session_ttl_minutes=30,
            compact_projects=True,
        )
        _talked(compacting, PROJECT)

        assert PROJECT not in compacting._diary_candidates()

    def test_con_la_manopola_spenta_il_progetto_e_un_candidato(self, autocompact):
        _talked(autocompact, PROJECT)

        assert autocompact._diary_candidates() == (PROJECT,)
        # E la conversazione personale non passa di qui: ha Dream che la legge
        # dalla sua coda, non un progetto da raccogliere.
        assert PERSONAL not in autocompact._diary_candidates()
