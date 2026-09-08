"""Il batch è atterrato, o il run ha solo scritto qualcosa?

Sono due domande, e fino al 2026-08-18 il sistema ne faceva una sola. Il run di
quel giorno alle 12:01, misurato sul Titan 2 con ``USER.md`` a 1.999/2.000
caratteri, ha ricevuto un batch di dieci fatti — una conversazione lunga della
sera prima, incluso un ``[permanent]`` — e ha fatto una sola ``edit_file``:
riscrivere una riga già presente 27 caratteri più corta. Zero fatti aggiunti,
``writes_ok == 1``, nessun rifiuto aperto, ``stuck`` a 0, cursore avanzato a 101.
Sano per ogni controllo esistente, e dieci voci perse per sempre.

Questi test coprono il predicato che chiude quel buco e — soprattutto — i due
modi in cui potrebbe chiuderlo *troppo*: un batch che non chiede niente, e un
livelock su un batch che il modello non vuole consolidare.
"""

from __future__ import annotations

import pytest

from jenny.agent.dream_cycle import (
    NOTHING_NEW_IS_NOTABLE,
    STUCK_IS_ALARMING,
    batch_carries_retained_facts,
    batch_was_not_consolidated,
    consolidation_landed,
    finish_dream_cycle,
    format_stuck_alarm,
)
from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import budget_report


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def _report(store, *, memory=3000, user=3000, soul=0):
    return budget_report(store, memory_chars=memory, user_chars=user, soul_chars=soul)


# Il batch vero del run di 12:01, ridotto a due voci: un fatto da conservare e la
# sua correzione. È la forma su cui il predicato deve dire "questo andava salvato".
_REAL_BATCH = (
    "[2026-08-18 11:02] - [durable] Ha appena cambiato ruolo e sta ancora prendendo le misure\n"
    "- [permanent] Preferisce le riunioni corte del mattino e rifiuta quelle del venerdì\n"
)


class TestBatchCarriesRetainedFacts:
    @pytest.mark.parametrize("tag", ["[durable]", "[permanent]", "[correction]"])
    def test_each_retention_tag_counts(self, tag):
        assert batch_carries_retained_facts(f"- {tag} un fatto qualunque")

    def test_a_batch_of_nothing_does_not(self):
        # I cursori 97 e 98 sul device sono letteralmente questo.
        assert not batch_carries_retained_facts("[2026-08-17 17:24] (nothing)")

    def test_a_skip_only_batch_does_not(self):
        assert not batch_carries_retained_facts(
            '- [skip] Item lista "shampo. deodorante." marcato come fatto'
        )

    def test_an_empty_batch_does_not(self):
        assert not batch_carries_retained_facts("")

    def test_the_real_batch_does(self):
        assert batch_carries_retained_facts(_REAL_BATCH)


class TestTheTemplateDoesNotAnswerForTheBatch:
    """Il trap in cui questo predicato cade se lo si costruisce sul prompt intero.

    Il template di Dream *nomina* tutti e tre i tag nella sua sezione "History
    attribute tags". Cercarli nel prompt completo li trova sempre, su qualunque
    batch: il predicato sarebbe vero per costruzione, cioè inutile, e il bug
    sarebbe invisibile perché il predicato "funziona" su ogni caso che si
    proverebbe a mano.
    """

    def test_the_template_alone_mentions_all_three_tags(self, store):
        store.append_history("(nothing)")
        result = store.build_dream_prompt()
        assert result is not None
        prompt = result.prompt

        # La premessa del trap: nel prompt intero i tag ci sono comunque.
        assert all(t in prompt for t in ("[durable]", "[permanent]", "[correction]"))

        # E il ritaglio è ciò che rende il predicato una domanda vera.
        assert not batch_carries_retained_facts(
            MemoryStore.dream_prompt_history(prompt)
        )

    def test_the_slice_keeps_the_batch(self, store):
        store.append_history("- [durable] il gateway gira su Android")
        result = store.build_dream_prompt()
        assert result is not None
        prompt = result.prompt

        history = MemoryStore.dream_prompt_history(prompt)
        assert "il gateway gira su Android" in history
        assert "memory consolidation engine" not in history  # niente template
        assert batch_carries_retained_facts(history)

    def test_a_prompt_without_the_header_slices_to_empty(self):
        assert MemoryStore.dream_prompt_history("nessun header qui") == ""


class TestConsolidationLanded:
    def test_a_grown_file_counts_as_landed(self, store):
        store.memory_file.write_text("x" * 100, encoding="utf-8")
        before = _report(store)

        store.memory_file.write_text("x" * 140, encoding="utf-8")

        assert consolidation_landed(before)

    def test_an_untouched_set_of_files_did_not_land(self, store):
        store.memory_file.write_text("x" * 100, encoding="utf-8")
        before = _report(store)

        assert not consolidation_landed(before)

    def test_a_shrunk_file_did_not_land(self, store):
        """Il run di 12:01, ridotto alla sua misura: il file è più corto di prima."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store)

        store.user_file.write_text("y" * 1972, encoding="utf-8")

        assert not consolidation_landed(before)

    def test_growth_anywhere_is_enough(self, store):
        # Dream può potare MEMORY.md e far atterrare il fatto in USER.md: è un
        # consolidamento riuscito, e guardare i file uno per uno lo vedrebbe come
        # metà mancato. Conta che *qualcosa* sia cresciuto.
        store.memory_file.write_text("x" * 500, encoding="utf-8")
        store.user_file.write_text("y" * 500, encoding="utf-8")
        before = _report(store)

        store.memory_file.write_text("x" * 400, encoding="utf-8")
        store.user_file.write_text("y" * 620, encoding="utf-8")

        assert consolidation_landed(before)

    def test_a_file_outside_the_report_is_not_consulted(self, store):
        # ``SOUL.md`` con budget 0 resta nel report (misurato, non applicato), ma
        # un file che nel report non c'è affatto non può far dichiarare atterrato
        # un run che non ha toccato la memoria.
        store.memory_file.write_text("x" * 100, encoding="utf-8")
        before = _report(store)

        (store.workspace / "altro.md").write_text("z" * 9999, encoding="utf-8")

        assert not consolidation_landed(before)


class TestBatchWasNotConsolidated:
    """Il predicato che i due percorsi di Dream chiamano davvero."""

    def test_the_1201_run_holds_the_cursor(self, store):
        """Il caso misurato: 99% di riempimento, scrive, accorcia, non aggiunge."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)  # 1.999/2.000 — il 99% del device
        store.user_file.write_text("y" * 1972, encoding="utf-8")

        assert batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, attempted=1
        )

    def test_a_run_that_added_something_advances(self, store):
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 2100, encoding="utf-8")

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0
        )

    def test_a_batch_with_nothing_to_save_advances(self, store):
        """Il freno che evita di bloccare un'installazione tranquilla.

        Senza di questo, ogni run su un batch `(nothing)` terrebbe fermo il
        cursore — e quel batch non cambia mai, quindi per sempre.
        """
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)

        assert not batch_was_not_consolidated(
            before=before, history_text="(nothing)", stuck=0
        )

    def test_it_gives_up_at_the_alarm_threshold(self, store):
        """Il freno all'autoblocco: oltre soglia si molla il batch e si avanza.

        Serve perché un modello che *non vuole* aggiungere non viene convinto da
        un replay: senza questo ramo lo stesso batch tornerebbe a ogni run per
        sempre, che è il livelock che il resto del modulo esiste per chiudere.
        """
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1972, encoding="utf-8")

        assert batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=STUCK_IS_ALARMING - 1, attempted=1
        )
        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=STUCK_IS_ALARMING, attempted=1
        )

    def test_the_hold_is_bounded_by_the_existing_scale(self):
        # Non un numero nuovo: il freno riusa la soglia dell'allarme, così la
        # finestra di replay finisce esattamente quando l'utente viene avvisato.
        assert STUCK_IS_ALARMING == 4


class TestTheAlarmNamesOneCauseAgain:
    """Questo test difendeva la vaghezza, ed era giusto finché le cause erano tre.

    Sostituisce ``TestTheAlarmNoLongerNamesOneCause``, che chiedeva l'opposto:
    con il caso di 12:01 — scrittura riuscita che non porta niente — una frase
    sui rifiuti mandava chi la legge a controllare la cosa sbagliata, quindi la
    frase era stata sfumata fino a coprire tutti e tre i casi. Vera e inutile.

    La fase 5 ha tolto il motivo: gli altri due casi salgono ora su
    ``nothing_new_runs``, che logga e non allarma, quindi qui arriva una causa
    sola. Una diagnosi che può essere precisa deve esserlo — indica un rimedio
    che esiste, alzare un tetto.
    """

    def test_it_blames_the_cap_because_now_only_the_cap_gets_here(self):
        alarm = format_stuck_alarm(4)

        assert "size cap" in alarm
        assert "4 runs" in alarm

    def test_the_hedge_is_gone(self):
        assert "are not landing" not in format_stuck_alarm(4)


class TestTheEvidenceIsNowCounted:
    """La stima è diventata una verifica, e la soglia se n'è andata con lei.

    Fino al 2026-08-18 "il batch è atterrato?" si rispondeva guardando crescere i
    file, e un batch di soli duplicati — la maggioranza, perché la Consolidator
    ri-estrae gli stessi fatti a ogni giro — era indistinguibile da un batch
    mancato. L'unico modo di non trattenerlo era escluderlo per statistica: sotto
    il 90% di riempimento si credeva al modello. Quella soglia stava su tre
    osservazioni di un modello solo. Ora il tool per voci risponde, e il numero
    magico non serve più.
    """

    def test_an_added_entry_settles_it(self, store):
        """Il segnale positivo che prima non esisteva: una voce è entrata."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1972, encoding="utf-8")  # il file è pure calato

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, added=1
        )

    def test_a_shrinking_add_still_counts_as_landed(self, store):
        """Il falso negativo dichiarato del vecchio metodo, ora chiuso.

        Una voce che entra mentre il file cala nello stesso turno era un
        consolidamento riuscito che la dimensione leggeva come mancato — e il
        costo di quel verso dell'errore è un fatto perso.
        """
        store.user_file.write_text("y" * 2000, encoding="utf-8")
        before = _report(store, user=3000)
        store.user_file.write_text("y" * 1500, encoding="utf-8")

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, added=1
        )

    def test_an_already_present_fact_settles_it_too(self, store):
        """Il caso delle 14:01, ora risolto guardando invece che indovinando.

        `MEMORY.md` al 79% e `USER.md` al 77%, batch di sei fatti già tutti su
        disco. Prima costava quattro run di replay più un review forzato su file
        che non avevano niente da liberare, e la difesa era una soglia. Ora il
        tool dice che il contenuto è già in memoria, che è la stessa conclusione
        senza il numero inventato.
        """
        store.memory_file.write_text("x" * 2381, encoding="utf-8")
        store.user_file.write_text("y" * 2316, encoding="utf-8")
        before = _report(store)

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, already_present=6
        )

    def test_a_bare_replace_does_not_settle_it(self, store):
        """Il fallimento misurato **è** una replace: una riga esistente accorciata
        senza il fatto nuovo. Contarla sempre riammetterebbe dalla finestra ciò
        che questo predicato esiste per prendere."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1972, encoding="utf-8")

        assert batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, replaced=1, attempted=1
        )

    def test_a_replace_settles_a_correction(self, store):
        """Ma quando il batch *chiede* una correzione, sostituire in place è la
        mossa giusta — il prompt la chiede esplicitamente — e lì la replace è il
        consolidamento."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1972, encoding="utf-8")
        correction = "[2026-08-18 11:02] - [correction] Vive a Roma, non a Milano\n"

        assert not batch_was_not_consolidated(
            before=before, history_text=correction, stuck=0, replaced=1
        )

    def test_a_correction_batch_with_no_replace_is_still_held(self, store):
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1972, encoding="utf-8")
        correction = "[2026-08-18 11:02] - [correction] Vive a Roma, non a Milano\n"

        assert batch_was_not_consolidated(
            before=before, history_text=correction, stuck=0, replaced=0, attempted=1
        )

    def test_the_threshold_is_gone(self):
        """Nessun numero tarato a occhio è sopravvissuto a questo passaggio."""
        import jenny.agent.dream_cycle as dc

        assert not hasattr(dc, "_PRESSURE_PCT")
        assert not hasattr(dc, "under_write_pressure")


class TestARunWithoutTheEntryTool:
    """I doppi di ``build_dream_tools`` nei test non espongono ``memory_entries``,
    e nemmeno un run vecchio in corso durante un aggiornamento."""

    def test_the_default_reads_as_zero_of_everything(self):
        from jenny.agent.dream_cycle import NO_ENTRIES

        assert NO_ENTRIES.entries_added == 0
        assert NO_ENTRIES.entries_replaced == 0
        assert NO_ENTRIES.entries_already_present == 0

    def test_which_leaves_the_size_net_in_charge(self, store):
        """Con zero voci il predicato ricade sulla crescita dei file, cioè
        esattamente il comportamento di prima: nessun run resta scoperto."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 2100, encoding="utf-8")

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0
        )


class TestARunThatNeverTriedToWrite:
    """Misurato sul Titan 2 il 2026-08-18 alle 18:10, ed è il freno che il device
    ha indicato.

    Il piano dava per scontato che un batch di duplicati si sarebbe dichiarato con
    un ``already_present``. Non succede, e non perché il modello preferisca
    ``list``: **non guarda affatto**. ``USER.md`` è iniettato nel suo prompt, così
    risponde dal contesto — "entrambi i fatti nel batch sono già presenti, non c'è
    nulla da scrivere" — con zero chiamate a tool e una sola iterazione.

    Un run così non ha *mancato* un consolidamento: ha deciso che non ce n'era da
    fare. Trattenerlo vuol dire rigiocare lo stesso batch davanti allo stesso
    modello con lo stesso contesto, che risponderà lo stesso — quattro volte, più
    un review forzato su file che non hanno niente da liberare.
    """

    def test_no_attempt_means_no_hold(self, store):
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, attempted=0
        )

    def test_the_1201_run_still_holds_because_it_wrote(self, store):
        """Il caso per cui la funzione esiste passa da qui: quel run una scrittura
        l'ha fatta — una ``edit_file`` che accorciava una riga — quindi il freno
        nuovo non lo lascia passare."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1972, encoding="utf-8")

        assert batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, attempted=1
        )

    def test_it_matches_the_reading_the_commit_gate_already_gives(self, store):
        """Non è una regola nuova: ``dream_should_advance_cursor`` legge già
        ``writes_attempted == 0`` come "non c'era niente da scrivere". Questo freno
        smette di contraddirla."""
        store.user_file.write_text("y" * 1999, encoding="utf-8")
        before = _report(store, user=2000)
        store.user_file.write_text("y" * 1900, encoding="utf-8")

        assert not batch_was_not_consolidated(
            before=before, history_text=_REAL_BATCH, stuck=0, attempted=0
        )


class TestTheCounterSplitInTwo:
    """Fase 5: ``stuck`` contava due cose con rimedi opposti.

    Un tetto che rifiuta una scrittura significa "manca spazio", e un review pass
    può liberarlo: forzarlo ha senso. Niente rifiutato e niente atterrato è
    un'altra bestia — non c'è spazio da liberare, e un review poterebbe file che
    non hanno da dare. Misurato sul Titan 2 il 2026-08-18: un review forzato su
    file al 77% e 79%, a vuoto, più quattro run di replay.
    """

    def test_a_refused_write_climbs_the_counter_that_forces_a_review(self, store):
        runs, stuck = finish_dream_cycle(
            store, advanced=False, runs_since_review=0, stuck=0, refused=1,
        )

        assert stuck == 1
        assert store.get_nothing_new_runs() == 0

    def test_nothing_refused_climbs_the_other_one(self, store):
        finish_dream_cycle(
            store, advanced=False, runs_since_review=0, stuck=0, refused=0,
        )

        assert store.get_review_state()[1] == 0
        assert store.get_nothing_new_runs() == 1

    def test_they_reset_together_when_the_cursor_moves(self, store):
        """A azzerarli è lo stesso evento, e uno rimasto su per omissione
        racconterebbe un blocco che non c'è."""
        finish_dream_cycle(
            store, advanced=False, runs_since_review=0, stuck=2, nothing_new=3, refused=1,
        )

        finish_dream_cycle(
            store, advanced=True, runs_since_review=1, stuck=3, nothing_new=3,
        )

        assert store.get_review_state()[1] == 0
        assert store.get_nothing_new_runs() == 0

    def test_a_run_with_no_history_moves_neither(self, store):
        """``advanced is None``: non c'era niente da consolidare, quindi non è
        stato mancato niente."""
        finish_dream_cycle(
            store, advanced=None, runs_since_review=0, stuck=2, nothing_new=1,
        )

        assert store.get_review_state()[1] == 2
        assert store.get_nothing_new_runs() == 1

    def test_the_quiet_counter_never_forces_anything(self, store):
        """Il punto della separazione: sale, si vede, e non fa partire un review
        che non avrebbe niente da liberare."""
        for _ in range(NOTHING_NEW_IS_NOTABLE + 2):
            runs, stuck = finish_dream_cycle(
                store, advanced=False, runs_since_review=0, stuck=0, refused=0,
                nothing_new=store.get_nothing_new_runs(),
            )
            assert stuck == 0

        assert store.get_nothing_new_runs() == NOTHING_NEW_IS_NOTABLE + 2

    def test_a_state_written_before_the_split_reads_as_zero(self, store):
        """Uno stato vecchio eredita il suo ``stuck_runs`` come *manca spazio*, che
        è il ramo che tiene armata la via d'uscita dal livelock."""
        store._review_state_file.write_text(
            '{"runs_since_review": 3, "stuck_runs": 2, "forced_at_stuck": 0}',
            encoding="utf-8",
        )

        assert store.get_review_state() == (3, 2)
        assert store.get_nothing_new_runs() == 0


class TestTheAlarmCanNameItsCauseAgain:
    """Con una causa sola l'allarme smette di coprirne tre.

    La prima stesura diceva "keep being refused" ed era sbagliata per due casi su
    tre; la seconda diceva "non stanno atterrando", vera e inutile. Ora questo
    allarme parte solo dal contatore dei rifiuti, quindi può dire cosa succede e
    indicare un rimedio che esiste.
    """

    def test_it_names_the_cap(self):
        text = format_stuck_alarm(STUCK_IS_ALARMING)

        assert "size cap" in text
        assert "refusing its writes" in text

    def test_it_still_says_how_long(self):
        assert str(STUCK_IS_ALARMING) in format_stuck_alarm(STUCK_IS_ALARMING)

    def test_it_no_longer_hedges(self):
        assert "are not landing" not in format_stuck_alarm(STUCK_IS_ALARMING)

