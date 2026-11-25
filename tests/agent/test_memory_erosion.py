"""La limatura di ``USER.md`` non è più silenziosa.

Misurato sul telefono il 25/11/2026: le voci vive erano state riscritte in media
sette volte, e 67 dei 151 nomi e numeri passati per loro non erano più in nessun
file vivo — una città era sparita da una riga sul piano di vita. Qui si prova
che il codice se ne accorge: l'archiviatore lo scrive nel log, e
:func:`memory_erosion` lo conta. Nomi inventati: il repository è pubblico.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from jenny.agent.memory_archive import ArchivedEntry, archive_entry, erosion
from jenny.agent.tools.memory_entries import (
    entry_id,
    make_entry_archiver,
    memory_erosion,
)

_LONG = "Piano di vita: mesi da Oreste a Cremona o in Islanda."
_SHORT = "Piano: mesi da Oreste o in Islanda."
# L'id di una voce è l'hash della riga intera, trattino compreso.
_LONG_ID = entry_id(f"- {_LONG}")
_SHORT_ID = entry_id(f"- {_SHORT}")


@pytest.fixture()
def records():
    seen: list[str] = []
    handle = logger.add(lambda m: seen.append(str(m)), level="INFO", format="{message}")
    yield seen
    logger.remove(handle)


def _write_user(ws: Path, *bullets: str) -> Path:
    path = ws / "USER.md"
    path.write_text("# User\n\n" + "".join(f"- {b}\n" for b in bullets), encoding="utf-8")
    return path


class TestTheArchiverSaysWhatARewriteDropped:
    def test_a_shortened_entry_logs_the_place_it_lost(self, tmp_path, records):
        path = _write_user(tmp_path, _LONG, "Amici: Tiziana.")
        archiver = make_entry_archiver(tmp_path)
        archiver(path, "# User\n\n" + f"- {_SHORT}\n- Amici: Tiziana.\n")
        dropped = [r for r in records if "dropped" in r]
        assert len(dropped) == 1
        assert "cremona" in dropped[0]
        assert "USER.md" in dropped[0]

    def test_a_rewrite_that_keeps_every_name_logs_nothing(self, tmp_path, records):
        path = _write_user(tmp_path, "Il suo amico Oreste vive a Cremona.")
        make_entry_archiver(tmp_path)(path, "# User\n\n- Oreste vive a Cremona.\n")
        assert not [r for r in records if "dropped" in r]

    def test_the_old_wording_is_still_archived_and_linked(self, tmp_path):
        path = _write_user(tmp_path, _LONG)
        make_entry_archiver(tmp_path)(path, f"# User\n\n- {_SHORT}\n")
        archived = list((tmp_path / "memory" / "archive").glob("*.md"))
        assert len(archived) == 1
        text = archived[0].read_text(encoding="utf-8")
        assert "Cremona" in text
        assert f"superseded_by: {_SHORT_ID}" in text


class TestErosion:
    def test_a_name_left_out_of_a_living_entry_is_lost(self, tmp_path):
        memory = tmp_path / "memory"
        archive_entry(memory, ArchivedEntry(
            id=_LONG_ID, text=_LONG, source="USER.md", superseded_by=_SHORT_ID,
        ))
        _write_user(tmp_path, _SHORT)
        measured = memory_erosion(tmp_path)
        assert measured.lost == {"cremona"}
        assert {"oreste", "islanda", "cremona"} <= measured.passed

    def test_a_name_moved_to_another_entry_is_not_lost(self, tmp_path):
        memory = tmp_path / "memory"
        archive_entry(memory, ArchivedEntry(
            id=_LONG_ID, text=_LONG, source="USER.md", superseded_by=_SHORT_ID,
        ))
        _write_user(tmp_path, _SHORT, "Oreste vive a Cremona.")
        assert memory_erosion(tmp_path).lost == frozenset()

    def test_an_entry_removed_on_purpose_does_not_count(self, tmp_path):
        # Tolta senza successore: è Dream che ha deciso di togliere un fatto, non
        # una riscrittura che ne ha perso un pezzo.
        archive_entry(tmp_path / "memory", ArchivedEntry(
            id=_LONG_ID, text=_LONG, source="USER.md",
        ))
        _write_user(tmp_path, "Amici: Tiziana.")
        measured = memory_erosion(tmp_path)
        assert measured.lost == frozenset()
        assert measured.passed == frozenset()

    def test_an_empty_installation_measures_nothing(self, tmp_path):
        measured = memory_erosion(tmp_path)
        assert measured.lost == frozenset()
        assert measured.passed == frozenset()

    def test_a_chain_of_rewrites_counts_every_version(self):
        first = ArchivedEntry(id="a1", text="Amici: Tiziana (Cremona), Oreste (Lodi).",
                              source="USER.md", superseded_by="a2")
        second = ArchivedEntry(id="a2", text="Amici: Tiziana (Cremona), Oreste.",
                               source="USER.md", superseded_by="live")
        measured = erosion([second, first], ["- Amici: Tiziana, Oreste."], {"live"})
        assert measured.lost == {"cremona", "lodi"}
