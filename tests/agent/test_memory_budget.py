"""Tests for the long-term memory budget — report, gauge and write-size guard."""

import pytest

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import (
    FileBudget,
    budget_report,
    make_write_size_guard,
    render_gauge,
)
from jenny.config.schema import DreamConfig


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path)
    s.memory_file.write_text("# Memory\n- Project X active", encoding="utf-8")
    s.user_file.write_text("# User\n- Lives in Italy", encoding="utf-8")
    s.soul_file.write_text("# Soul\n- Helpful", encoding="utf-8")
    return s


def _report(store, *, memory=0, user=0, soul=0):
    return budget_report(store, memory_chars=memory, user_chars=user, soul_chars=soul)


class TestBudgetReport:
    def test_orders_memory_user_soul(self, store):
        report = _report(store)
        assert [item.label for item in report] == ["MEMORY.md", "USER.md", "SOUL.md"]
        assert [item.path for item in report] == [
            store.memory_file, store.user_file, store.soul_file,
        ]

    def test_counts_characters_of_existing_files(self, store):
        store.memory_file.write_text("x" * 120, encoding="utf-8")
        report = _report(store, memory=6000)
        assert report[0].chars == 120
        assert report[0].budget == 6000

    def test_missing_file_reports_zero_chars(self, tmp_path):
        # Nessuno dei tre file esiste: il report deve essere comunque costruibile,
        # perché gira anche al primo avvio su un workspace vuoto.
        empty = MemoryStore(tmp_path)
        report = _report(empty, memory=6000, user=2000, soul=1000)
        assert [item.chars for item in report] == [0, 0, 0]

    def test_zero_budget_still_measured(self, store):
        store.soul_file.write_text("y" * 42, encoding="utf-8")
        soul = _report(store, memory=6000)[2]
        assert soul.budget == 0
        assert soul.enforced is False
        assert soul.chars == 42
        assert soul.over is False

    def test_pct_is_none_when_not_enforced(self, store):
        report = _report(store)
        assert all(item.pct is None for item in report)

    def test_pct_truncates(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=4021, budget=6000)
        assert item.pct == 67
        assert item.over is False

    def test_over_when_above_budget(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=6001, budget=6000)
        assert item.over is True


class TestRenderGauge:
    def test_enforced_line(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=4021, budget=6000)
        assert "MEMORY.md [67% — 4,021/6,000 chars]" in render_gauge([item])

    def test_unenforced_line(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=4021, budget=0)
        assert "MEMORY.md [4,021 chars — no budget]" in render_gauge([item])

    def test_empty_report_renders_empty_string(self):
        assert render_gauge([]) == ""

    def test_head_promises_no_refusal_when_nothing_is_enforced(self, store):
        # I default di produzione hanno i tre budget a 0: con questo report
        # nessuna scrittura può essere rifiutata, e la testa non deve insegnare
        # una regola che il runtime non applicherà mai.
        head = render_gauge(_report(store)).splitlines()[0]
        assert "refused" not in head
        assert "80%" not in head

    def test_head_promises_a_refusal_as_soon_as_one_budget_is_set(self, store):
        # Basta un file enforced perché il rifiuto sia una cosa che può davvero
        # succedere: da lì in poi la regola va detta.
        head = render_gauge(_report(store, memory=6000)).splitlines()[0]
        assert "refused" in head

    def test_review_head_drops_the_budget_stop_signal_when_nothing_is_enforced(self, store):
        # "già sotto il proprio budget" è l'unico segnale di stop del review
        # pass: senza budget non è valutabile, e va sostituito da uno che lo sia.
        head = render_gauge(_report(store), for_review=True).splitlines()[0]
        assert "does not need to shrink further" not in head
        assert "criteria" in head

    def test_review_head_keeps_the_budget_stop_signal_when_one_is_set(self, store):
        head = render_gauge(_report(store, user=3000), for_review=True).splitlines()[0]
        assert "does not need to shrink further" in head

    @pytest.mark.parametrize("for_review", [False, True])
    def test_the_per_file_lines_do_not_depend_on_the_head(self, store, for_review):
        # La testa cambia; le misure no, in nessuna delle due varianti.
        unenforced = render_gauge(_report(store), for_review=for_review).splitlines()[1:]
        assert unenforced == [
            f"{item.label} [{item.chars:,} chars — no budget]" for item in _report(store)
        ]
        mixed = _report(store, memory=6000)
        assert render_gauge(mixed, for_review=for_review).splitlines()[1:] == [
            f"MEMORY.md [{mixed[0].pct}% — {mixed[0].chars:,}/6,000 chars]",
            f"USER.md [{mixed[1].chars:,} chars — no budget]",
            f"SOUL.md [{mixed[2].chars:,} chars — no budget]",
        ]

    def test_mentions_the_80_percent_rule(self, store):
        gauge = render_gauge(_report(store, memory=6000))
        assert "80%" in gauge
        # Una riga di istruzioni + una per file: deve restare compatto, finisce
        # in ogni prompt di Dream.
        assert len(gauge.splitlines()) == 4


class TestWriteSizeGuard:
    def test_rejects_growing_write_over_budget(self, store):
        guard = make_write_size_guard(_report(store, memory=100))
        result = guard(store.memory_file, "z" * 150)
        assert result is not None
        assert "MEMORY.md" in result
        assert "150" in result and "100" in result

    def test_accepts_shrinking_write_still_over_budget(self, store):
        # Il test che protegge dall'autoblocco: un file già fuori misura può
        # essere potato solo scrivendoci sopra. Se il guard guardasse soltanto
        # "risultato > budget", la prima potatura verrebbe rifiutata e nessun
        # file oltre budget potrebbe più rientrare — cioè proprio lo stato in cui
        # la feature si trova al primo avvio sul device.
        store.memory_file.write_text("z" * 500, encoding="utf-8")
        guard = make_write_size_guard(_report(store, memory=100))
        assert guard(store.memory_file, "z" * 400) is None

    def test_rejects_equal_size_write_over_budget(self, store):
        store.memory_file.write_text("z" * 500, encoding="utf-8")
        guard = make_write_size_guard(_report(store, memory=100))
        # Stessa dimensione non è potatura: non fa progresso e va rifiutata.
        assert guard(store.memory_file, "w" * 500) is not None

    def test_rejects_oversized_write_to_missing_file(self, tmp_path):
        empty = MemoryStore(tmp_path)
        guard = make_write_size_guard(_report(empty, memory=100))
        assert guard(empty.memory_file, "z" * 150) is not None

    def test_accepts_write_within_budget(self, store):
        guard = make_write_size_guard(_report(store, memory=100))
        assert guard(store.memory_file, "z" * 100) is None

    def test_zero_budget_never_rejects(self, store):
        guard = make_write_size_guard(_report(store, memory=0))
        assert guard(store.memory_file, "z" * 100_000) is None

    def test_path_outside_report_passes(self, store, tmp_path):
        guard = make_write_size_guard(_report(store, memory=100, user=100, soul=100))
        assert guard(tmp_path / "notes" / "other.md", "z" * 5000) is None

    def test_matches_through_a_symlinked_parent(self, tmp_path, store):
        # Analogo di /data/user/0/<pkg> vs /data/data/<pkg> su Android: il tool
        # può presentare il path in una forma non canonica. Un guard che
        # confronta forme diverse non matcha mai, e non matchare mai equivale a
        # non esistere — qui il rifiuto deve scattare lo stesso.
        alias = tmp_path.parent / f"{tmp_path.name}-alias"
        try:
            alias.symlink_to(tmp_path, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported here")
        guard = make_write_size_guard(_report(store, memory=100))
        assert guard(alias / "memory" / "MEMORY.md", "z" * 150) is not None

    def test_rejection_leaves_the_file_untouched(self, store):
        store.memory_file.write_text("original", encoding="utf-8")
        before_mtime = store.memory_file.stat().st_mtime_ns
        guard = make_write_size_guard(_report(store, memory=10))
        assert guard(store.memory_file, "z" * 500) is not None
        assert store.memory_file.read_text(encoding="utf-8") == "original"
        assert store.memory_file.stat().st_mtime_ns == before_mtime

    def test_rejection_message_is_actionable(self, store):
        guard = make_write_size_guard(_report(store, memory=100))
        result = guard(store.memory_file, "z" * 150)
        assert result is not None
        lowered = result.lower()
        assert "same turn" in lowered
        assert "delete" in lowered
        assert "smaller" in lowered


class TestTheShippedCapOnTheFileItWillMeet:
    """Il tetto di spedizione contro la dimensione vera del file, non contro numeri finti.

    ``DreamConfig.memory_budget_chars`` vale 3.000 e sul Titan 2 ``MEMORY.md``
    misura 3.943 caratteri senza review: il tetto è vincolante dal primo run, il
    131% della soglia. La misura è quella *non potata* di proposito — 3.019 era
    il valore dopo due passaggi di review, cioè il pavimento che il file toccava
    solo appena compresso, e tarare un tetto su quello è l'errore che ha portato
    ``USER.md`` a vivere al 99%. Su questa combinazione l'unica cosa che tiene la
    feature viva è la clausola "sta rimpicciolendo" del guard — senza, il file
    non potrebbe più essere potato e nessuna scrittura passerebbe mai più. Gli
    altri test di questa classe la provano su numeri di comodo; qui si prova sui
    due che esistono davvero.
    """

    _DEVICE_CHARS = 3943

    @pytest.fixture
    def over_budget(self, store):
        store.memory_file.write_text("z" * self._DEVICE_CHARS, encoding="utf-8")
        return make_write_size_guard(_report(store, memory=DreamConfig().memory_budget_chars))

    def test_the_shipped_cap_is_binding_on_the_measured_file(self, store, over_budget):
        assert DreamConfig().memory_budget_chars == 3000
        assert self._DEVICE_CHARS > 3000

    def test_a_growing_write_is_refused(self, store, over_budget):
        assert over_budget(store.memory_file, "z" * (self._DEVICE_CHARS + 40)) is not None

    def test_a_write_of_the_same_size_is_refused(self, store, over_budget):
        # Riscrivere altrettanti caratteri non fa progresso: se passasse, un run
        # potrebbe rimpiazzare il contenuto all'infinito senza mai rientrare.
        assert over_budget(store.memory_file, "w" * self._DEVICE_CHARS) is not None

    def test_a_shrinking_write_passes_while_still_over_budget(self, store, over_budget):
        # La via d'uscita, e l'unica: 3.600 caratteri sono ancora il 120% del
        # tetto e la scrittura passa lo stesso, perché è potatura. Questa è la
        # regola che T1.1 non ha toccato — il commit del run è cambiato, il
        # guard no — ed è ciò che impedisce al livelock di essere definitivo.
        assert over_budget(store.memory_file, "z" * 3600) is None

    def test_a_write_that_lands_under_the_cap_passes(self, store, over_budget):
        assert over_budget(store.memory_file, "z" * 2999) is None


class TestUserRulesDoNotCountAgainstSoul:
    """Le regole dell'utente, proiettate in SOUL.md dall'app, non sono di Dream.

    Il budget di SOUL.md esiste per limitare ciò che Dream scrive. Contare il
    blocco dell'utente vorrebbe dire che un testo lungo nella casella «Tu e
    Jenny» porta il file oltre il tetto, e da lì ogni scrittura di Dream viene
    rifiutata — ``stuck`` sale e parte un review forzato per righe non sue.
    """

    RULES = "Regola dell'utente, lunga. " * 40  # ~1.100 caratteri

    def _with_rules(self, store):
        from jenny.agent.soul_rules import save_rules

        save_rules(store.soul_file.parent, self.RULES)
        return store.soul_file.read_text(encoding="utf-8")

    def test_the_report_leaves_the_block_out(self, store):
        text = self._with_rules(store)
        soul = _report(store, soul=500)[2]
        assert len(text) > 1000
        # Il testo di lei più gli a capo attorno al blocco: nessuna riga sua.
        assert soul.chars == len("# Soul\n- Helpful\n\n\n"), soul
        assert soul.over is False

    def test_the_guard_does_not_refuse_dream_because_of_the_rules(self, store):
        text = self._with_rules(store)
        guard = make_write_size_guard(_report(store, soul=500))
        grown = text.replace("- Helpful", "- Helpful\n- Brief")
        assert guard(store.soul_file, grown) is None

    def test_dreams_own_text_still_counts(self, store):
        text = self._with_rules(store)
        guard = make_write_size_guard(_report(store, soul=500))
        grown = text.replace("- Helpful", "- Helpful\n" + "- x" * 300)
        refusal = guard(store.soul_file, grown)
        assert refusal is not None and "SOUL.md" in refusal

    def test_other_files_count_everything(self, store):
        from jenny.agent.soul_rules import MARK_END, MARK_START

        store.user_file.write_text(MARK_START + "\nabc\n" + MARK_END, encoding="utf-8")
        assert _report(store)[1].chars == len(MARK_START + "\nabc\n" + MARK_END)
