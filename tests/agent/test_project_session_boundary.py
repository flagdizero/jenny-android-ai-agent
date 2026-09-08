"""La terza categoria di sessione, e il confine che la tiene fuori dal diario.

Fino a ieri le sessioni erano due — lavoro interno e conversazione con l'utente —
e "chi non e' interno" bastava come definizione di "personale". Con le sessioni
progetto ne esiste una terza che e' conversazione con l'utente e **non** deve
alimentare la memoria di lungo periodo, quindi quella definizione diventa
silenziosamente sbagliata: una chiave ``project:`` non e' interna, e per la
vecchia regola risultava percio' personale, cioe' sarebbe finita in ``MEMORY.md``.

Questi test arrivano **prima** che qualcosa produca una chiave di progetto, e
usano chiavi sintetiche di proposito: il confine deve essere una proprieta della
chiave e non della UI che la sceglie, altrimenti legare la cartella alla sessione
sarebbe un cambiamento che accende una falla invece di una funzione.

Le due meta vanno lette insieme, e l'asimmetria con le sessioni interne e'
voluta: per un job cron scrivere in quella coda e' la cura di un difetto vero
(rilegge i propri run passati), per un progetto e' una perdita. Chi tocca uno dei
due rami legga ``test_dream_history_boundary.py``, che tiene ferma l'altra meta.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from jenny.agent.context import ContextBuilder
from jenny.agent.memory import Consolidator, MemoryStore
from jenny.session.keys import (
    is_internal_session_key,
    is_personal_session_key,
    is_project_session_key,
    normalize_user_session_key,
    project_session_key,
    session_kind,
)
from jenny.session.manager import Session

PERSONAL = "unified:default"
PROJECT = "project:patreon"
OTHER_PROJECT = "project:etf-finance"
CRON = "cron:job-1"
OTHER_CRON = "cron:job-2"


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def _inject_raw(store: MemoryStore, content: str, session_key: str) -> None:
    """Scrive una voce nella coda scavalcando il gate.

    Serve perche' il gate sulla scrittura rende impossibile creare una voce di
    progetto passando dall'API: senza questo, i test della *lettura* verificherebbero
    solo che una cosa che non puo' esistere non si vede. Una voce del genere puo'
    arrivare da una versione precedente del codice o da un file modificato a mano,
    ed e' esattamente il caso che il secondo giro di chiave deve coprire.
    """
    cursor = store._next_cursor()
    record = {
        "cursor": cursor,
        "timestamp": "2026-08-21 12:00",
        "content": content,
        "session_key": session_key,
    }
    with open(store.history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── la classificazione ───────────────────────────────────────────────────────


class TestLaClassificazioneETernaria:
    @pytest.mark.parametrize(
        ("key", "kind"),
        [
            (PERSONAL, "personal"),
            ("websocket:qualunque", "personal"),
            (PROJECT, "project"),
            (OTHER_PROJECT, "project"),
            (CRON, "internal"),
            ("dream:20260821-1200", "internal"),
            ("subagent:abc", "internal"),
            ("gardener:orto-20260821", "internal"),
            ("internal:direct", "internal"),
            ("heartbeat", "internal"),
        ],
    )
    def test_ogni_chiave_cade_in_una_categoria_sola(self, key, kind):
        assert session_kind(key) == kind
        # E i tre predicati sono d'accordo con lei: sono la stessa funzione.
        assert is_personal_session_key(key) is (kind == "personal")
        assert is_project_session_key(key) is (kind == "project")
        assert is_internal_session_key(key) is (kind == "internal")

    @pytest.mark.parametrize(
        "key",
        ["api:vision", "system", "review:20260823", "qualcosa-di-nuovo", "websocketx:y"],
    )
    def test_un_prefisso_non_registrato_non_e_personale(self, key):
        """**T4.10.** Il residuo cade nel bucket prudente, non nel diario.

        Prima di oggi ``session_kind`` era: interna se il prefisso e' registrato,
        progetto se e' ``project:``, **personale tutto il resto**. Cioe' un kind
        nuovo il cui prefisso qualcuno si dimenticasse di registrare finiva nel
        solo bucket che Dream consuma (``MemoryStore.build_dream_prompt`` filtra
        su ``is_personal_session_key``): il suo contenuto entrava in
        ``MEMORY.md``, senza che nessuna riga dica da dove viene.

        Ora la terza categoria e' una whitelist vera e il residuo e' ``internal``,
        che e' il bucket "lavoro del sistema": non alimenta la memoria di lungo
        periodo e non compare negli elenchi user-facing. Un kind legittimo nuovo
        classificato cosi' si rompe **a vista** al primo giro; uno classificato
        personale non si rompe affatto, scrive nel diario e non lo si scopre.
        """
        assert not is_personal_session_key(key)
        assert session_kind(key) == "internal"

    def test_la_whitelist_personale_e_la_conversazione_e_le_chiavi_di_prima(self):
        """Chi *puo'* alimentare ``MEMORY.md``: la sessione unica e le legacy.

        Le ``<canale>:<chat_id>`` non sono piu' sessioni, ma stanno scritte nelle
        voci di ``history.jsonl`` di prima della sessione unica ed erano la
        conversazione con l'utente: tenerle fuori dalla whitelist renderebbe
        invisibile a Dream la storia gia' sul disco.
        """
        assert is_personal_session_key(PERSONAL)
        assert is_personal_session_key("websocket:qualunque")
        assert is_personal_session_key("telegram:12345")

    def test_una_chiave_non_classificata_lo_dice(self):
        """Fail-closed **e** ad alta voce: il silenzio era metà del difetto.

        Il rifiuto non e' un'eccezione di proposito: ``session_kind`` gira anche
        sul campo ``session_key`` delle voci di ``history.jsonl``, scritte da
        versioni precedenti e modificabili a mano, e un'eccezione la' farebbe
        cadere Dream e l'autocompaction su una riga vecchia. Resta il log.
        """
        from jenny.session import keys as keys_mod

        keys_mod._UNCLASSIFIED_WARNED.discard("zzsconosciuto")
        messages: list[str] = []
        sink = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            assert session_kind("zzsconosciuto:1") == "internal"
        finally:
            logger.remove(sink)
        assert any("zzsconosciuto:1" in m for m in messages), messages

    def test_un_progetto_non_e_ne_interno_ne_personale(self):
        """Il difetto che questa categoria esiste per chiudere.

        Prima, ``is_personal_session_key`` era ``not is_internal_session_key``, e
        una chiave di progetto passava quel filtro: la conversazione di un
        progetto sarebbe entrata nel diario personale.
        """
        assert not is_internal_session_key(PROJECT)
        assert not is_personal_session_key(PROJECT)

    def test_la_chiave_si_compone_da_un_punto_solo(self):
        assert project_session_key("patreon") == PROJECT
        assert is_project_session_key(project_session_key("qualunque-cosa"))

    def test_la_migrazione_delle_chiavi_legacy_non_tocca_un_progetto(self):
        """``<canale>:<chat_id>`` collassa sulla conversazione unica, un progetto no.

        E' il motivo per cui quell'elenco e' chiuso e non un pattern: collassare
        ``project:<id>`` sulla chat personale ci farebbe girare dentro il lavoro
        di un progetto.
        """
        assert normalize_user_session_key("websocket:default") == PERSONAL
        assert normalize_user_session_key(PROJECT) == PROJECT


# ── la scrittura ─────────────────────────────────────────────────────────────


class TestLaScritturaPassaConLaSuaChiave:
    """**Cambiato l'08/09/2026.** Fino a quel giorno questa classe si chiamava
    ``TestLaScritturaEChiusa`` e provava il contrario: un progetto non scriveva
    affatto in questa coda, e l'isolamento era un'*assenza*.

    Il cancello guardava l'asse sbagliato. La riga dichiarata e' «chi sei viaggia,
    dove altro lavori no» — una regola sulla **categoria del fatto** — e quello era
    un cancello sull'**origine della sessione**: un fatto identitario detto dentro
    un progetto e' identita', cioe' la classe autorizzata a viaggiare, e veniva
    fermato lo stesso. Misurato sul telefono: 39 righe su 72 dei journal di
    progetto erano fatti sulla persona, e 18 su 23 campionati non stavano in
    nessun file di memoria (``.agent/project-memory-plan.md``).

    L'isolamento adesso e' **una chiave piu' una destinazione**, ed e' piu'
    stretto e non piu' largo: la chiave tiene la voce fuori da ogni prompt (la
    classe qui sotto), la destinazione riduce la cassetta di Dream a ``USER.md``
    (``TestLaCassettaDiUnRunDiProgetto``). Chi togliesse una delle due deve
    rimettere il cancello.
    """

    def test_un_progetto_scrive_con_la_propria_chiave(self, store):
        cursor = store.append_history("cosa detta dentro il progetto", session_key=PROJECT)

        assert cursor > 0
        record = json.loads(store.history_file.read_text(encoding="utf-8").splitlines()[-1])
        assert record["content"] == "cosa detta dentro il progetto"
        # **La chiave e' meta' della garanzia.** Una voce di progetto scritta senza
        # la sua chiave sarebbe indistinguibile da una personale: entrerebbe in
        # ogni prompt e Dream la estrarrebbe con le regole sbagliate.
        assert record["session_key"] == PROJECT

    def test_il_cursore_e_uno_solo_e_avanza_per_tutti(self, store):
        """Una filigrana sola, e non una per tipo.

        Il batch resta omogeneo scegliendo *quali* voci prendere
        (``TestUnBatchNonMescolaIDueTipi``), non tenendo due cursori: due
        filigrane su un file append-only che si puo' correggere a mano possono
        divergere, e una divergenza li' si paga in voci mai lette.
        """
        personal_cursor = store.append_history("fatto personale", session_key=PERSONAL)

        project_cursor = store.append_history("cosa di progetto", session_key=PROJECT)

        assert project_cursor == personal_cursor + 1

    def test_anche_il_dump_grezzo_passa(self, store):
        """``raw_archive`` e' il ramo di fallback quando la chiamata LLM fallisce.

        Passa dallo stesso imbuto, ed e' la ragione per cui la decisione sta in
        ``append_history`` e non in ``Consolidator.archive``: quando il cancello
        c'era, aprire solo il ramo felice avrebbe lasciato chiuso quello che
        scatta quando le cose vanno male — e vale identico adesso, in tutti e due
        i versi.
        """
        store.raw_archive(
            [{"role": "user", "content": "una cosa personale detta in un progetto"}],
            session_key=PROJECT,
        )

        record = json.loads(store.history_file.read_text(encoding="utf-8").splitlines()[-1])
        assert record["session_key"] == PROJECT

    def test_una_sessione_interna_scrive_ancora(self, store):
        """L'asimmetria, fissata: non e' una dimenticanza da "sistemare".

        Un job cron rilegge le proprie voci in questa coda — e' cosi che si
        ricorda dei run passati. Chiudere anche questa scrittura romperebbe la
        cura dell'amnesia dell'heartbeat.
        """
        cursor = store.append_history("il job ha girato", session_key=CRON)

        assert cursor > 0
        assert "il job ha girato" in store.history_file.read_text(encoding="utf-8")

    def test_una_voce_senza_chiave_scrive_ancora(self, store):
        """Il campo e' opzionale: il gate non deve trasformare l'assenza in un rifiuto."""
        assert store.append_history("voce senza attribuzione") > 0


# ── la lettura ───────────────────────────────────────────────────────────────


class TestLaLetturaEUnAssenza:
    def test_un_progetto_non_legge_la_coda(self, store):
        store.append_history("fatto personale", session_key=PERSONAL)
        store.append_history("voce di un job", session_key=CRON)

        assert store.read_recent_history_for_prompt(0, session_key=PROJECT) == []

    def test_nemmeno_le_proprie_voci(self, store):
        """Non "niente di altrui": proprio niente.

        Un progetto non condivide la finestra — il cursore di Dream — quindi non
        ha senso che ci legga dentro nemmeno le voci che porterebbero la sua
        chiave. La continuita di un progetto vive nella sua sessione e nei suoi
        file.
        """
        _inject_raw(store, "vecchia voce del progetto", PROJECT)

        assert store.read_recent_history_for_prompt(0, session_key=PROJECT) == []

    def test_una_sessione_personale_non_vede_una_voce_di_progetto(self, store):
        """Il secondo giro di chiave: la whitelist, non la negazione.

        Con la vecchia condizione (``not e' interna``) una voce di progetto
        finita nel file — da una versione precedente, o scritta a mano — sarebbe
        entrata nel prompt di *ogni* sessione.
        """
        store.append_history("fatto personale", session_key=PERSONAL)
        _inject_raw(store, "roba del progetto", PROJECT)

        entries = store.read_recent_history_for_prompt(0, session_key=PERSONAL)

        contents = [e["content"] for e in entries]
        assert "fatto personale" in contents
        assert "roba del progetto" not in contents

    def test_una_sessione_interna_non_vede_una_voce_di_progetto(self, store):
        store.append_history("voce mia", session_key=CRON)
        _inject_raw(store, "roba del progetto", PROJECT)

        contents = [
            e["content"] for e in store.read_recent_history_for_prompt(0, session_key=CRON)
        ]
        assert "voce mia" in contents
        assert "roba del progetto" not in contents

    def test_nemmeno_una_passata_del_giardiniere_vede_un_progetto(self, store):
        """Il quarto ramo, ed e' quello a cui una voce di progetto somiglia di piu'.

        Una passata gira **su** un progetto, quindi e' l'unico lettore per cui
        "voce di quel progetto" potrebbe sembrare materiale suo. Non lo e': la sua
        materia sono il diario del progetto, la mappa e l'inventario delle pagine,
        e questa coda e' contabilita di un altro deposito. Il ramo ``own_only`` la
        tiene fuori senza che nessuno debba ricordarsene.
        """
        store.append_history("conversazione personale", session_key=PERSONAL)
        store.append_history("roba del progetto", session_key=PROJECT)
        gardener = "gardener:patreon-20260908"
        store.append_history("la mia passata", session_key=gardener)

        contents = [
            e["content"]
            for e in store.read_recent_history_for_prompt(0, session_key=gardener)
        ]
        assert contents == ["la mia passata"]

    def test_la_regola_ternaria_di_una_sessione_interna_resta(self, store):
        """Le proprie voci *piu* la conversazione personale, e non quelle di un altro job."""
        store.append_history("conversazione personale", session_key=PERSONAL)
        store.append_history("voce mia", session_key=CRON)
        store.append_history("voce di un altro job", session_key=OTHER_CRON)

        contents = [
            e["content"] for e in store.read_recent_history_for_prompt(0, session_key=CRON)
        ]
        assert "conversazione personale" in contents
        assert "voce mia" in contents
        assert "voce di un altro job" not in contents

    def test_senza_chiave_si_legge_tutto(self, store):
        """``session_key=None`` e' l'accesso non filtrato di chi non e' un turno."""
        store.append_history("personale", session_key=PERSONAL)
        store.append_history("interna", session_key=CRON)

        assert len(store.read_recent_history_for_prompt(0, session_key=None)) == 2


# ── Dream ────────────────────────────────────────────────────────────────────


class TestDreamNonVedeUnProgetto:
    def test_un_batch_non_mescola_i_due_tipi(self, store):
        """Un batch, un tipo solo — e il primo arrivato decide quale.

        Non e' ordine estetico: i due tipi hanno prompt diversi, cassette diverse
        e destinazioni diverse. Un batch misto costringerebbe a scegliere quale
        delle due regole applicare a materiale dell'altra, e la scelta sbagliata
        e' quella che porta inventario di progetto in ``memory/MEMORY.md``.
        """
        store.append_history("- [durable] fatto personale", session_key=PERSONAL)
        store.append_history("- [durable] fatto detto in un progetto", session_key=PROJECT)

        first = store.build_dream_prompt()
        assert first is not None
        assert first.scope == "personal"
        batch = MemoryStore.dream_prompt_history(first.prompt)
        assert "fatto personale" in batch
        assert "detto in un progetto" not in batch

        # E il secondo giro prende l'altro tipo: nessuna voce resta indietro,
        # costa un run in piu' e non una perdita.
        store.set_last_dream_cursor(first.cursor)
        second = store.build_dream_prompt()
        assert second is not None
        assert second.scope == "project"
        assert "detto in un progetto" in MemoryStore.dream_prompt_history(second.prompt)

    def test_una_coda_di_sole_voci_di_progetto_fa_partire_un_run_di_progetto(self, store):
        """Il rovescio del vecchio ``...non_fa_partire_dream``.

        Prima una coda di sole voci di progetto lasciava Dream senza input; ora
        parte, con il **suo** template e il **suo** scope. Lo scope non e'
        un'etichetta: e' quel che ``build_dream_tools`` legge per ridurre la
        cassetta, quindi un batch che lo dichiarasse "personal" per sbaglio
        aprirebbe ``memory/MEMORY.md`` a materiale di progetto.
        """
        store.append_history("solo roba di progetto", session_key=PROJECT)

        result = store.build_dream_prompt()

        assert result is not None
        assert result.scope == "project"
        assert "only what is true of the **person**" in result.prompt
        assert "solo roba di progetto" in MemoryStore.dream_prompt_history(result.prompt)


# ── il prompt di un turno ────────────────────────────────────────────────────


class TestIlPromptDiUnProgetto:
    pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

    def test_non_ha_nessun_blocco_recent_history(self, tmp_path):
        """Non un blocco filtrato: nessun blocco.

        E' la forma che il piano chiede, e la ragione e' che un'assenza non si
        puo' sbagliare mentre un filtro si.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        builder = ContextBuilder(workspace)
        builder.memory.append_history("storia personale", session_key=PERSONAL)
        builder.memory.append_history("storia di un job", session_key=CRON)

        prompt = builder.build_system_prompt(session_key=PROJECT)

        assert "# Recent History" not in prompt
        assert "storia personale" not in prompt
        assert "storia di un job" not in prompt

    def test_la_conversazione_personale_ce_l_ha_ancora(self, tmp_path):
        """Controllo: il blocco non e' scomparso per tutti."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        builder = ContextBuilder(workspace)
        builder.memory.append_history("storia personale", session_key=PERSONAL)

        prompt = builder.build_system_prompt(session_key=PERSONAL)

        assert "# Recent History" in prompt
        assert "storia personale" in prompt

# ── la compattazione, che deve continuare a funzionare ───────────────────────


@pytest.fixture
def consolidator(store):
    """Consolidator con un provider finto: il riassunto e' una costante."""
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=MagicMock(content="riassunto della conversazione", finish_reason="stop")
    )
    return Consolidator(
        store=store,
        provider=provider,
        model="test-model",
        sessions=MagicMock(save=MagicMock()),
        context_window_tokens=100_000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


class TestLaCompattazioneDiUnProgettoFunziona:
    """La meta che si rompe se si chiude la cosa sbagliata.

    Un progetto **si compatta** — e' il modello di Claude Code, una
    conversazione sola che quando diventa lunga viene riassunta e va avanti. Quel
    che non deve fare e' mandare il riassunto nella coda del diario. Se qualcuno
    chiudesse la compattazione invece della scrittura, un progetto riaperto dopo
    mesi non avrebbe piu niente in mano, e nessun test lo direbbe: sarebbero solo
    dei messaggi in meno.
    """

    async def test_il_riassunto_viene_prodotto_e_la_coda_lo_riceve_con_la_chiave(
        self, consolidator, store
    ):
        """Il rovescio del vecchio ``...ma_la_coda_non_lo_riceve``.

        E' il riassunto della compattazione a diventare la materia prima da cui
        Dream estrae i fatti sulla persona: senza questo passaggio la corsia
        esiste e non trasporta niente.
        """
        summary = await consolidator.archive(
            [{"role": "user", "content": "una lunga conversazione sul progetto"}],
            session_key=PROJECT,
        )

        assert summary == "riassunto della conversazione"
        record = json.loads(store.history_file.read_text(encoding="utf-8").splitlines()[-1])
        assert record["content"] == "riassunto della conversazione"
        assert record["session_key"] == PROJECT

    async def test_il_riassunto_finisce_nei_metadati_della_sessione(
        self, consolidator, store
    ):
        """E' da lì che il turno dopo lo rilegge, non dalla coda.

        Per questo chiudere la scrittura non costa niente alla compattazione:
        ``_last_summary`` e la sessione sono la stessa cosa, e la coda non c'entra.
        """
        session = Session(key=PROJECT)

        summary = await consolidator.archive(
            [{"role": "user", "content": "conversazione"}], session_key=session.key
        )
        consolidator._persist_last_summary(session, summary)

        assert session.metadata["_last_summary"]["text"] == "riassunto della conversazione"

    async def test_per_la_conversazione_personale_la_coda_lo_riceve(
        self, consolidator, store
    ):
        """Controllo: il consolidamento non ha smesso di scrivere per tutti."""
        await consolidator.archive(
            [{"role": "user", "content": "una conversazione"}], session_key=PERSONAL
        )

        assert "riassunto della conversazione" in store.history_file.read_text("utf-8")


# ── la cassetta di un run di progetto ────────────────────────────────────────


class TestLaCassettaDiUnRunDiProgetto:
    """**L'altra meta' del confine, e quella che non si puo' pregare.**

    Aperta la scrittura nella coda, la regola «da un progetto puo' uscire
    identita', mai inventario» ha bisogno di stare da qualche parte. Il prompt di
    ``agent/dream_project.md`` la dice, ma un paragrafo non e' una garanzia: e'
    una richiesta a un modello. Queste asserzioni sono la garanzia.
    """

    def test_scrive_solo_su_user_md(self, store):
        tools = store.build_dream_tools(scope="project")

        memory_tool = tools.get("memory")
        assert memory_tool is not None
        assert memory_tool.parameters["properties"]["file"]["enum"] == ["user"]

    def test_non_ha_nessuno_scrittore_di_file_interi(self, store):
        """I tre tool che riscrivono un file per intero non ci sono.

        Ridurre soltanto la loro allowlist a ``USER.md`` lascerebbe comunque tre
        strade per riscriverla tutta, quando la strada giusta e' una voce alla
        volta. ``read_file`` resta: leggere non e' scrivere.
        """
        names = set(store.build_dream_tools(scope="project").tool_names)

        assert names == {"memory", "read_file"}
        # E il ramo personale non cambia di una riga: e' la meta' che questo
        # piano non doveva toccare.
        assert set(store.build_dream_tools().tool_names) == {
            "memory", "read_file", "edit_file", "apply_patch", "write_file",
        }

    async def test_una_scrittura_su_memory_md_viene_rifiutata(self, store):
        """Rifiutata, non ignorata — e con un messaggio che non suggerisce vie.

        Un rifiuto che dicesse "prova con un altro tool" e' un vicolo con
        l'uscita disegnata sopra: il modello la prende, spende il turno e il
        cursore resta fermo. Questo dice che il fatto non ha un'altra casa.
        """
        tool = store.build_dream_tools(scope="project").get("memory")

        out = await tool.execute(action="add", file="memory", text="- un fatto di progetto")

        assert "Cannot write memory/MEMORY.md in this run" in out
        assert not (store.workspace / "memory" / "MEMORY.md").exists()

    async def test_una_scrittura_su_user_md_passa(self, store):
        """Il controllo positivo: il rifiuto deve essere selettivo, non totale.

        Senza, un tool che rifiuta *tutto* passerebbe il test qui sopra e
        renderebbe la corsia inutile senza che niente lo dica.
        """
        tool = store.build_dream_tools(scope="project").get("memory")

        out = await tool.execute(action="add", file="user", text="- un fatto sulla persona")

        assert "1 added" in out
        assert "un fatto sulla persona" in (store.workspace / "USER.md").read_text()

    async def test_il_ramo_personale_scrive_ancora_su_memory_md(self, store):
        tool = store.build_dream_tools().get("memory")

        out = await tool.execute(action="add", file="memory", text="- inventario")

        assert "1 added" in out


# ── la finestra di replay ────────────────────────────────────────────────────


class TestLaFinestraDiReplay:
    """Perche' un run di progetto rilegge quel che ha gia' letto.

    Misurato l'08/09/2026: su materiale ambiguo — quello in cui ogni fatto e'
    vestito da progetto — quattro estrazioni identiche hanno dato sottoinsiemi
    **diversi** (1, 2, 3 e 2 fatti su 4, intersezione vuota, unione completa),
    mentre su materiale non ambiguo la stessa estrazione era stabile. Con un
    cursore che avanza una volta sola, quella meta' persa e' persa per sempre.
    """

    pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

    def test_le_voci_gia_consumate_tornano_nel_prompt(self, store):
        first = store.append_history("prima cosa di progetto", session_key=PROJECT)
        store.set_last_dream_cursor(first)
        store.append_history("seconda cosa di progetto", session_key=PROJECT)

        result = store.build_dream_prompt()

        assert result is not None
        history = MemoryStore.dream_prompt_history(result.prompt)
        assert "seconda cosa di progetto" in history
        assert "prima cosa di progetto" in history, "la finestra non ha rimostrato niente"
        assert "Already processed, shown again" in history

    def test_il_cursore_avanza_lo_stesso(self, store):
        """La differenza fra questo e un cursore che arretra.

        Arretrare di N su un batch di N o meno vuol dire non avanzare mai —
        livelock, e silenzioso. Qui la finestra e' contesto in piu' dentro un
        batch che resta quello nuovo.
        """
        first = store.append_history("prima", session_key=PROJECT)
        store.set_last_dream_cursor(first)
        second = store.append_history("seconda", session_key=PROJECT)

        result = store.build_dream_prompt()

        assert result is not None
        assert result.cursor == second

    def test_una_coda_di_sole_voci_gia_lette_non_fa_partire_niente(self, store):
        """La finestra accompagna un batch nuovo, non ne inventa uno.

        Senza questo, ogni giro di Dream troverebbe "qualcosa da fare" fintanto
        che esiste una voce di progetto sul disco.
        """
        cursor = store.append_history("gia\' letta", session_key=PROJECT)
        store.set_last_dream_cursor(cursor)

        assert store.build_dream_prompt() is None

    def test_la_finestra_non_tocca_un_batch_personale(self, store):
        cursor = store.append_history("vecchia personale", session_key=PERSONAL)
        store.set_last_dream_cursor(cursor)
        store.append_history("nuova personale", session_key=PERSONAL)

        result = store.build_dream_prompt()

        assert result is not None
        history = MemoryStore.dream_prompt_history(result.prompt)
        assert "vecchia personale" not in history
        assert "Already processed" not in history
