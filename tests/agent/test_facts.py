"""Il catalogo del richiamo per nome: quali formulazioni archiviate tornano, e quando.

Nomi inventati: il repository è pubblico. Il caso che l'ha chiesto è lo stesso
di ``test_memory_erosion.py``: una città sparita da una riga del profilo, e
l'utente che nomina l'amico senza nominarla.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.facts import (
    HEADER,
    MAX_BLOCK_CHARS,
    MAX_LINES,
    FactCatalogue,
    blocks_in_history,
)
from jenny.agent.memory_archive import ArchivedEntry, archive_entry

_LONG = "Piano di vita: mesi da Oreste a Cremona o in Islanda."
_SHORT = "Piano: mesi da Oreste o in Islanda."


def _archive(ws: Path, text: str, *, id: str, source: str = "USER.md",
             superseded_by: str = "", retracted: bool = False, demoted: str = "2026-10-10"):
    archive_entry(ws / "memory", ArchivedEntry(
        id=id, text=text, source=source, superseded_by=superseded_by,
        retracted=retracted, demoted=demoted,
    ))


def _catalogue(ws: Path, user: str = f"- {_SHORT}\n") -> FactCatalogue:
    (ws / "USER.md").write_text(f"# User\n\n{user}", encoding="utf-8")
    catalogue = FactCatalogue(ws)
    catalogue.refresh()
    return catalogue


def _recall(catalogue: FactCatalogue, ws: Path, message: str, **kw) -> list[str]:
    known = (ws / "USER.md").read_text(encoding="utf-8") + "\n" + message
    return catalogue.recall_lines(message, known=known, sources=("USER.md",), **kw)


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    _archive(tmp_path, _LONG, id="aaaa0001", superseded_by="live0001")
    return tmp_path


class TestTheLostDetailComesBack:
    def test_naming_the_friend_brings_back_the_city(self, ws):
        lines = _recall(_catalogue(ws), ws, "stasera sono dal mio amico oreste")
        assert len(lines) == 1
        assert "Cremona" in lines[0]
        assert lines[0].startswith("- 2026-10-10 · USER.md · ")

    def test_nothing_comes_back_when_the_live_file_already_says_it(self, ws):
        catalogue = _catalogue(ws, user=f"- {_LONG}\n")
        assert _recall(catalogue, ws, "stasera sono da oreste") == []

    def test_a_message_naming_nobody_recalls_nothing(self, ws):
        assert _recall(_catalogue(ws), ws, "che tempo fa domani?") == []

    def test_a_line_already_in_the_replayed_history_is_not_sent_again(self, ws):
        catalogue = _catalogue(ws)
        first = _recall(catalogue, ws, "sono da oreste")
        history = [{"role": "user", "content": "sono da oreste\n\n" + "\n".join(first)}]
        assert _recall(catalogue, ws, "ancora da oreste", history=history) == []

    def test_the_same_message_gives_the_same_lines(self, ws):
        catalogue = _catalogue(ws)
        assert _recall(catalogue, ws, "da oreste") == _recall(catalogue, ws, "da oreste")


class TestWhatDoesNotTravel:
    def test_a_retracted_wording_never_comes_back(self, tmp_path):
        _archive(tmp_path, _LONG, id="bbbb0001", retracted=True)
        assert _recall(_catalogue(tmp_path), tmp_path, "sono da oreste") == []

    def test_a_memory_md_wording_stays_out_of_a_notebook(self, tmp_path):
        _archive(tmp_path, "Progetto con Oreste: banco a Cremona.", id="cccc0001",
                 source="memory/MEMORY.md")
        catalogue = _catalogue(tmp_path, user="- Amici: Oreste.\n")
        assert _recall(catalogue, tmp_path, "sento oreste") == []
        personal = catalogue.recall_lines(
            "sento oreste", known="Amici: Oreste. sento oreste",
            sources=("USER.md", "memory/MEMORY.md"),
        )
        assert len(personal) == 1 and "Cremona" in personal[0]


class TestTriggers:
    def test_numbers_never_trigger(self, tmp_path):
        _archive(tmp_path, "Risparmia 1.250 al mese con Banca Lunare.", id="dddd0001")
        assert _catalogue(tmp_path).triggers("ho messo da parte 1.250") == []

    def test_short_words_never_trigger(self, tmp_path):
        _archive(tmp_path, "Amico Ugo, vive a Cremona.", id="eeee0001")
        assert _catalogue(tmp_path).triggers("ho visto ugo") == []

    def test_a_name_that_is_also_a_common_word_needs_its_capital(self, tmp_path):
        _archive(tmp_path, "Amica Neve, vive a Cremona.", id="ffff0001")
        (tmp_path / "memory" / "history.jsonl").write_text(
            '{"cursor": 1, "content": "la neve è alta e la neve cade, poca neve in città"}\n',
            encoding="utf-8",
        )
        catalogue = _catalogue(tmp_path, user="- Ama la montagna.\n")
        assert catalogue.triggers("quanta neve stanotte") == []
        assert catalogue.triggers("stasera esco con Neve") == ["neve"]

    def test_a_notebook_name_is_not_a_trigger(self, tmp_path):
        (tmp_path / "wikis" / "Cremona").mkdir(parents=True)
        catalogue = _catalogue(tmp_path)
        _archive(tmp_path, _LONG, id="gggg0001")
        catalogue.refresh()
        assert "cremona" not in catalogue.triggers("andiamo a cremona")

    def test_an_anchor_in_too_many_entries_does_not_trigger(self, tmp_path):
        for i in range(10):
            _archive(tmp_path, f"Con Rodolfo, cosa numero {i} a Lodi{i}.", id=f"hhhh{i:04d}")
        assert _catalogue(tmp_path).triggers("sento rodolfo") == []

    def test_at_most_two_triggers_the_rarest_first(self, tmp_path):
        _archive(tmp_path, "Oreste e Tiziana a Cremona.", id="iiii0001")
        _archive(tmp_path, "Oreste a Lodi.", id="iiii0002")
        _archive(tmp_path, "Oreste a Pisa con Corrado.", id="iiii0003")
        found = _catalogue(tmp_path).triggers("oreste tiziana corrado")
        assert found == ["corrado", "tiziana"]


class TestCaps:
    def test_lines_and_characters_are_capped(self, tmp_path):
        for i in range(8):
            _archive(tmp_path, f"Oreste {'parola ' * 20}a Luogo{i} con Persona{i}.",
                     id=f"jjjj{i:04d}")
        lines = _recall(_catalogue(tmp_path), tmp_path, "oreste")
        assert 0 < len(lines) <= MAX_LINES
        assert sum(len(line) for line in lines) <= MAX_BLOCK_CHARS

    def test_two_wordings_of_the_same_detail_bring_it_once(self, tmp_path):
        _archive(tmp_path, "Mesi da Oreste a Cremona.", id="kkkk0001")
        _archive(tmp_path, "Mesi da Oreste (amico, Cremona).", id="kkkk0002")
        assert len(_recall(_catalogue(tmp_path), tmp_path, "oreste")) == 1


class TestCatalogueLifecycle:
    def test_not_ready_until_built(self, ws):
        catalogue = FactCatalogue(ws)
        assert not catalogue.ready
        assert catalogue.recall_lines("oreste", known="", sources=("USER.md",)) == []

    def test_a_new_archive_entry_makes_it_stale(self, ws):
        catalogue = _catalogue(ws)
        assert not catalogue.stale()
        _archive(ws, "Amica Tiziana a Lodi.", id="llll0001")
        assert catalogue.stale()

    def test_an_installation_without_archive_builds_empty(self, tmp_path):
        catalogue = FactCatalogue(tmp_path)
        catalogue.refresh()
        assert catalogue.ready
        assert catalogue.triggers("oreste") == []


def test_blocks_in_history_counts_the_header():
    history = [{"role": "user", "content": f"a\n{HEADER}\n- x"},
               {"role": "user", "content": [{"type": "text", "text": HEADER}]}]
    assert blocks_in_history(history) == 2
