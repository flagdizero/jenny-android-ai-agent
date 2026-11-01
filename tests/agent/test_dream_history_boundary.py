"""Chi entra nel diario personale, e chi sta in ``history.jsonl`` senza entrarci.

``history.jsonl`` non e' il diario: e' la coda di lavoro da cui Dream *ricava* il
diario. Le due cose sono state confuse anche nel piano delle sessioni-progetto,
che dava per scontato che una voce interna in quel file fosse di per se' una
falla e proponeva di chiudere la scrittura. Non lo e', ed e' il contrario:

- le voci di una sessione interna sono in quel file **di proposito** — e' cosi'
  che un job cron rilegge i propri run precedenti
  (``read_recent_history_for_prompt``, e i test in
  ``test_context_prompt_cache.py``), cioe' la cura dell'amnesia
  dell'escalation dei monitor;
- l'unico consumatore che trasforma quel file in memoria permanente e' Dream,
  che scriveva ``MEMORY.md`` leggendo **tutto**.

Quindi il confine non sta sulla scrittura ma sulla lettura di Dream, ed e' una
whitelist: solo la conversazione personale. Questi test tengono ferme le due
meta' insieme, perche' chiudere la prima e' esattamente il modo di rompere la
seconda.
"""

from __future__ import annotations

import pytest

from jenny.agent.memory import MemoryStore

# Chiavi di comodo. La forma con suffisso e' quella vera in produzione
# (``cron:<job_id>``, ``dream:<timestamp>``).
PERSONAL = "unified:default"
CRON = "cron:job-1"
DREAM = "dream:20260821-1200"
PROJECT = "project:acme"


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def _dream_batch(store: MemoryStore) -> str:
    """La sola parte di storia del prompt di Dream, o stringa vuota."""
    result = store.build_dream_prompt()
    if result is None:
        return ""
    return MemoryStore.dream_prompt_history(result[0])


class TestDreamReadsOnlyThePersonalConversation:
    def test_an_internal_entry_never_reaches_the_prompt(self, store):
        store.append_history("l'utente ha detto una cosa su di se'", session_key=PERSONAL)
        store.append_history("il job ha girato e non ha trovato niente", session_key=CRON)
        store.append_history("il run di Dream si e' loggato da solo", session_key=DREAM)
        store.append_history("il turno interno ha escalato", session_key="internal:direct")

        batch = _dream_batch(store)
        assert "una cosa su di se'" in batch
        assert "il job ha girato" not in batch
        assert "si e' loggato da solo" not in batch
        assert "ha escalato" not in batch

    def test_an_unattributed_entry_still_counts_as_personal(self, store):
        """Il campo e' opzionale: le voci scritte prima dell'attribuzione restano."""
        store.append_history("voce vecchia, senza chiave")

        assert "voce vecchia" in _dream_batch(store)

    def test_a_queue_of_only_internal_entries_produces_no_run(self, store):
        store.append_history("solo lavoro interno", session_key=CRON)

        assert store.build_dream_prompt() is None

    def test_the_cursor_stops_at_the_last_admitted_entry(self, store):
        """Mai oltre: una voce interna in coda lascia il cursore indietro.

        E' il compromesso conservativo — quelle voci si rileggono e si riscartano
        al run seguente — e va tenuto fermo, perche' l'alternativa (saltare alla
        fine del file) consumerebbe voci personali mai lette il giorno in cui
        l'ordine sul disco cambia.
        """
        personal_cursor = store.append_history("fatto dell'utente", session_key=PERSONAL)
        store.append_history("coda interna", session_key=CRON)

        result = store.build_dream_prompt()
        assert result is not None
        assert result[1] == personal_cursor

    def test_the_cap_counts_admitted_entries_not_raw_ones(self, store):
        """Il tetto del batch si applica *dopo* il filtro.

        Contarlo prima darebbe batch dimezzati senza motivo su un'installazione
        con molto lavoro interno: dieci voci lette, cinque scartate, cinque
        consolidate, e il tetto speso per meta' su roba che non entra.
        """
        cursors = {}
        for i in range(5):
            cursors[i] = store.append_history(f"personale-{i}", session_key=PERSONAL)
            store.append_history(f"interna-{i}", session_key=CRON)

        result = store.build_dream_prompt(max_entries=2)
        assert result is not None
        batch = MemoryStore.dream_prompt_history(result[0])

        assert "personale-0" in batch and "personale-1" in batch
        assert "personale-2" not in batch
        assert not any(f"interna-{i}" in batch for i in range(5))
        assert result[1] == cursors[1]

    def test_it_is_the_read_that_filters_and_not_the_write(self, store):
        """La voce interna resta sul disco: e' la meta' che regge la continuita'.

        Se un domani qualcuno chiude la scrittura invece della lettura, questo
        test cade — ed e' l'unico posto che dice perche' non va chiusa.
        """
        cursor = store.append_history("il job ha girato", session_key=CRON)
        assert cursor > 0

        on_disk = store.read_unprocessed_history(since_cursor=0)
        assert [entry["content"] for entry in on_disk] == ["il job ha girato"]

        # E la sessione che l'ha scritta se la ritrova nel proprio prompt.
        own = store.read_recent_history_for_prompt(0, session_key=CRON)
        assert [entry["content"] for entry in own] == ["il job ha girato"]

        # Quella personale no.
        assert store.read_recent_history_for_prompt(0, session_key=PERSONAL) == []


class TestKnownFactsFollowsTheSameVisibility:
    """Il blocco "gia' registrato" del Consolidator obbedisce alla stessa regola.

    Sta nel prompt di un consolidamento, che appartiene a una sessione precisa:
    mostrare la coda di un'altra sessione sarebbe lo stesso errore di categoria
    del prompt di turno, con in piu' il costo in token.
    """

    def test_a_personal_consolidation_does_not_see_internal_pending(self, store):
        store.append_history("- [durable] fatto personale", session_key=PERSONAL)
        store.append_history("- [durable] fatto interno", session_key=CRON)

        block = store.get_known_facts_context(session_key=PERSONAL)
        assert "fatto personale" in block
        assert "fatto interno" not in block

    def test_an_internal_consolidation_sees_its_own_and_the_personal_one(self, store):
        store.append_history("- [durable] fatto personale", session_key=PERSONAL)
        store.append_history("- [durable] fatto mio", session_key=CRON)
        store.append_history("- [durable] fatto di un altro job", session_key="cron:job-2")

        block = store.get_known_facts_context(session_key=CRON)
        assert "fatto personale" in block
        assert "fatto mio" in block
        assert "fatto di un altro job" not in block

    def test_without_a_key_nothing_is_filtered(self, store):
        """Chiamate diritte e test: comportamento identico a prima."""
        store.append_history("- [durable] fatto interno", session_key=CRON)

        assert "fatto interno" in store.get_known_facts_context()

    def test_a_project_consolidation_gets_no_block_at_all(self, store):
        """E non e' una questione di token: e' il profilo personale.

        La coda era gia' chiusa a un progetto, i due file caldi no — quindi
        ``USER.md`` e ``memory/MEMORY.md`` finivano nel prompt di consolidamento
        di un progetto insieme all'istruzione che chiede di estrarre un
        ``[correction]`` verso quei fatti. Quella correzione non ha dove andare:
        il riassunto di un progetto si ferma in ``_last_summary`` e
        ``append_history`` non lo scrive, quindi Dream non la vede mai. Un blocco
        che invita a produrre qualcosa che il percorso poi butta e' peggio di un
        blocco assente.
        """
        store.user_file.write_text(
            "# User\n- Va a nuoto il martedi'\n", encoding="utf-8",
        )
        store.memory_file.write_text(
            "# Memory\n- Piano stipendio: rinegoziare a settembre\n", encoding="utf-8",
        )
        store.append_history("- [durable] fatto personale", session_key=PERSONAL)

        assert store.get_known_facts_context(session_key=PROJECT) == ""
        # Controprova: gli stessi file, la stessa coda, la chiave personale.
        personal = store.get_known_facts_context(session_key=PERSONAL)
        assert "nuoto" in personal
        assert "rinegoziare a settembre" in personal

    def test_a_project_gets_no_block_even_with_an_empty_queue(self, store):
        """Il gate sta prima delle letture, non dentro il filtro della coda.

        Senza coda il blocco sarebbe stato composto dai soli file — cioe' il caso
        peggiore, tutto profilo e nessun contesto del progetto.
        """
        store.user_file.write_text("# User\n- Fatto personalissimo\n", encoding="utf-8")

        assert store.get_known_facts_context(session_key=PROJECT) == ""
