"""La riga di `/dream` che dice quanti nomi e numeri le riscritture hanno perso.

È il numero da guardare dopo «fatti fermi» (25/11/2026). Tre cose contano: c'è
quando c'è erosione, tace quando non ce n'è, e non costa mai la risposta.
"""

from __future__ import annotations

import pytest

from jenny.agent.memory_archive import ArchivedEntry, archive_entry
from jenny.agent.tools.memory_entries import entry_id
from jenny.command import builtin

_LONG = "Piano di vita: mesi da Oreste a Cremona o in Islanda."
_SHORT = "Piano: mesi da Oreste o in Islanda."


async def test_the_note_names_how_many_anchors_left_the_profile(tmp_path):
    archive_entry(tmp_path / "memory", ArchivedEntry(
        id=entry_id(f"- {_LONG}"), text=_LONG, source="USER.md",
        superseded_by=entry_id(f"- {_SHORT}"),
    ))
    (tmp_path / "USER.md").write_text(f"# User\n\n- {_SHORT}\n", encoding="utf-8")
    note = await builtin._format_erosion_note(tmp_path)
    # Una persa (Cremona) su tre passate (Oreste, Cremona, Islanda).
    assert "1" in note and "3" in note
    assert "memory/archive/" in note


async def test_no_erosion_no_note(tmp_path):
    (tmp_path / "USER.md").write_text("# User\n\n- Amici: Tiziana.\n", encoding="utf-8")
    assert await builtin._format_erosion_note(tmp_path) == ""


async def test_a_failing_measure_costs_only_the_note(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from jenny.agent.tools import memory_entries

    def boom(_workspace):
        raise OSError("disk gone")

    monkeypatch.setattr(memory_entries, "memory_erosion", boom)
    assert await builtin._format_erosion_note(tmp_path) == ""
