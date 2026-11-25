"""Le ancore di un fatto: nomi propri e numeri riconosciuti dal codice.

I casi vengono dalla forma delle righe vere (voci di ``USER.md``, righe di
diario), con nomi inventati: il repository è pubblico.
"""

from __future__ import annotations

from jenny.agent.anchors import Vocabulary, delta, extract, words


class TestNames:
    def test_a_capitalised_word_inside_a_sentence_is_a_name(self):
        assert extract("Amici: Oreste, Tiziana (a Cremona, con lui si confida).") == {
            "oreste", "tiziana", "cremona",
        }

    def test_the_first_word_of_a_line_or_sentence_is_not_a_name_on_its_own(self):
        assert extract("Vuole un glicine. Ama il ciclo stagionale.") == frozenset()
        assert extract("- Piano: casa con radici") == frozenset()

    def test_a_vocabulary_that_saw_the_name_inside_a_sentence_recognises_it_at_the_start(self):
        line = "- 22:53 — [said] Oreste vive a Cremona."
        assert extract(line) == {"cremona"}
        vocab = Vocabulary.from_texts(["Amici: Tiziana e Oreste."])
        assert extract(line, vocab) == {"oreste", "cremona"}

    def test_accents_are_folded(self):
        assert extract("vive a Città di Castello") == {"citta", "castello"}

    def test_hyphenated_names_stay_one_anchor(self):
        assert extract("sul balcone c'è il Fico-Nero-Grande") == {"fico-nero-grande"}

    def test_short_all_caps_words_and_single_letters_are_not_names(self):
        assert extract("parla IT ed EN, I think, ma il TFR resta") == {"tfr"}

    def test_months_weekdays_and_the_app_s_own_words_are_not_anchors(self):
        assert extract("ci vediamo il Monday di July, lo dice Jenny") == frozenset()

    def test_extra_stop_drops_a_name_given_from_outside(self):
        assert extract("ne parla Pina con Oreste", extra_stop=["Pina"]) == {"oreste"}


class TestNumbers:
    def test_long_numbers_dates_and_numbers_with_letters_are_anchors(self):
        found = extract("ha 1.250 euro, stipendio 42k, dal 12/08 e dal 2026-11-24, in 3.000 passi")
        assert found == {"1.250", "42k", "12/08", "2026-11-24", "3.000"}

    def test_clock_times_and_short_bare_numbers_are_not(self):
        assert extract("alle 22:53 ne ha mangiati 3, il 12 e il 7") == frozenset()


class TestDelta:
    def test_a_shortening_that_drops_a_place_reports_it(self):
        old = "Piano di vita: mesi da Oreste a Cremona o in Islanda."
        new = "Piano: mesi da Oreste o in Islanda."
        assert delta(old, new) == ({"cremona"}, frozenset())

    def test_a_name_moved_to_the_start_of_the_sentence_is_not_dropped(self):
        old = "Il suo amico Oreste vive a Cremona."
        new = "Oreste vive a Cremona."
        assert delta(old, new) == (frozenset(), frozenset())

    def test_a_rewrite_that_adds_a_name_says_so(self):
        dropped, added = delta("Amici: Tiziana.", "Amici: Tiziana, Oreste.")
        assert dropped == frozenset()
        assert added == {"oreste"}

    def test_a_dropped_number_counts(self):
        assert delta("risparmia 1.250 al mese", "risparmia ogni mese")[0] == {"1.250"}


class TestVocabulary:
    def test_a_name_more_often_written_lowercase_is_a_common_word(self):
        vocab = Vocabulary.from_texts([
            "Ceno da Neve stasera.",
            "la neve è alta, la neve si scioglie, sotto la neve c'è il prato",
        ])
        assert "neve" in vocab.names
        assert vocab.is_common_word("neve")

    def test_a_name_never_written_lowercase_is_not(self):
        vocab = Vocabulary.from_texts(["Ceno da Oreste.", "domani da Oreste"])
        assert not vocab.is_common_word("oreste")


def test_words_finds_names_and_numbers_in_any_position():
    assert {"oreste", "1.250"} <= words("Oreste ha 1.250 euro")
