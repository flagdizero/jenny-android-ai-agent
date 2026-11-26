"""La logica della stanza «Cosa ricorda di te» (``agent/memory_service.py``).

Ogni gesto passa dall'archivio e lascia il segno giusto: una correzione lega la
formulazione vecchia alla nuova (``superseded_by``, piu' ``retracted`` se non
era vera), una voce tolta e' ``forgotten`` e il richiamo per nome non la
riporta, «Rimetti» la rimette viva e col lucchetto. E un id che non c'e' piu' e'
un ``conflict``: non si scrive sopra una voce che l'utente non ha visto.

I dati sono inventati (il repo e' pubblico).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jenny.agent import memory_service as svc
from jenny.agent.facts import FactCatalogue
from jenny.agent.memory_archive import find_archived, list_archived, read_archived
from jenny.agent.memory_locks import MemoryLocks
from jenny.agent.tools.memory_entries import entry_id, parse_entries

USER = """# User

## Basics

- Name: Alex Doe
- Lives in Cremona with Oreste

## Preferences

- Short answers
"""

NOTES = """# Notes

- The garden project uses the Tuesday market list
"""

CREMONA = entry_id("- Lives in Cremona with Oreste")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "USER.md").write_text(USER, encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text(NOTES, encoding="utf-8")
    return tmp_path


def _user(ws: Path) -> str:
    return (ws / "USER.md").read_text(encoding="utf-8")


def _live(ws: Path) -> dict[str, str]:
    return {e.id: e.text for e in parse_entries(_user(ws))}


def test_the_list_has_both_files_as_sentences(ws) -> None:
    out = svc.list_memory(ws)
    assert out["ok"] is True and out["dreaming"] is False
    files = {f["file"]: f["entries"] for f in out["files"]}
    assert [e["text"] for e in files["user"]] == [
        "Name: Alex Doe", "Lives in Cremona with Oreste", "Short answers",
    ]
    assert files["user"][1]["heading"] == "Basics"
    assert [e["text"] for e in files["memory"]] == ["The garden project uses the Tuesday market list"]
    assert out["recent"] == []


def test_it_changed_archives_the_old_wording_linked_and_locks_the_new(ws) -> None:
    out = svc.correct(ws, "user", CREMONA, "Lives in Lodi with Oreste", "changed")

    new_id = out["id"]
    assert "- Lives in Lodi with Oreste" in _user(ws)
    assert CREMONA not in _live(ws)
    old = read_archived(find_archived(ws / "memory", CREMONA))
    assert old.superseded_by == new_id
    assert old.retracted is False
    assert MemoryLocks(ws).locked_ids("USER.md") == {new_id}
    # Resta sotto la sua intestazione, al suo posto.
    assert [e.heading for e in parse_entries(_user(ws)) if e.id == new_id] == ["Basics"]


def test_it_was_never_true_is_retracted_and_recall_by_name_skips_it(ws) -> None:
    svc.correct(ws, "user", CREMONA, "Lives in Lodi with Oreste", "wrong")
    old = read_archived(find_archived(ws / "memory", CREMONA))
    assert old.retracted is True
    # Una frase smentita non e' «tolta da te»: non sta fra quelle da rimettere.
    assert svc.list_memory(ws)["recent"] == []


def test_a_correction_moves_an_existing_lock_and_keeps_its_date(ws) -> None:
    entry = next(e for e in parse_entries(USER) if e.id == CREMONA)
    MemoryLocks(ws).lock("USER.md", entry, at=1_700_000_000)
    new_id = svc.correct(ws, "user", CREMONA, "Lives in Lodi with Oreste", "changed")["id"]
    [row] = MemoryLocks(ws).rows("USER.md")
    assert row.entry_id == new_id and row.locked_at == 1_700_000_000


def test_a_stale_id_is_a_conflict_and_writes_nothing(ws) -> None:
    before = _user(ws)
    with pytest.raises(svc.MemoryEditError) as err:
        svc.correct(ws, "user", "deadbeef", "x", "changed")
    assert err.value.code == "conflict"
    with pytest.raises(svc.MemoryEditError) as err:
        svc.remove(ws, "user", "deadbeef")
    assert err.value.code == "conflict"
    assert _user(ws) == before


def test_bad_input_is_refused(ws) -> None:
    for text in ("", "   ", 3):
        with pytest.raises(svc.MemoryEditError) as err:
            svc.correct(ws, "user", CREMONA, text, "changed")
        assert err.value.code == "bad_request"
    with pytest.raises(svc.MemoryEditError) as err:
        svc.correct(ws, "user", CREMONA, "x" * (svc.MAX_ENTRY_CHARS + 1), "changed")
    assert err.value.code == "too_large"
    with pytest.raises(svc.MemoryEditError):
        svc.correct(ws, "user", CREMONA, "ok", "maybe")
    with pytest.raises(svc.MemoryEditError):
        svc.correct(ws, "nope", CREMONA, "ok", "changed")


def test_removed_is_forgotten_and_does_not_come_back_by_name(ws) -> None:
    svc.remove(ws, "user", CREMONA)

    assert CREMONA not in _live(ws)
    old = read_archived(find_archived(ws / "memory", CREMONA))
    assert old.forgotten is True
    catalogue = FactCatalogue(ws)
    catalogue.refresh()
    lines = catalogue.recall_lines("Do I still live in Cremona?", known=_user(ws), sources={"USER.md"})
    assert not any("Cremona" in line for line in lines), lines
    rows = svc.list_memory(ws)["recent"]
    assert [(r["kind"], r["text"]) for r in rows] == [("removed", "Lives in Cremona with Oreste")]


def test_undo_of_the_trash_puts_it_back_in_place_and_locked(ws) -> None:
    svc.remove(ws, "user", CREMONA)
    out = svc.restore(ws, CREMONA)

    assert out["file"] == "user"
    assert CREMONA in _live(ws)
    assert [e.heading for e in parse_entries(_user(ws)) if e.id == CREMONA] == ["Basics"]
    assert find_archived(ws / "memory", CREMONA) is None, "l'archivio dice ancora che non e' viva"
    assert CREMONA in MemoryLocks(ws).locked_ids("USER.md")
    with pytest.raises(svc.MemoryEditError) as err:
        svc.restore(ws, CREMONA)
    assert err.value.code == "not_found"



def test_recent_skips_old_rows(ws) -> None:
    svc.remove(ws, "user", CREMONA)
    later = date.today().replace(year=date.today().year + 1)
    assert svc.list_memory(ws, today=later)["recent"] == []


def test_a_hand_edit_in_the_workshop_archives_and_marks_deletions(ws) -> None:
    edited = USER.replace("- Lives in Cremona with Oreste\n", "").replace(
        "- Short answers", "- Short answers, in Italian")
    svc.archive_hand_edit(ws, ws / "USER.md", edited)

    gone = read_archived(find_archived(ws / "memory", CREMONA))
    assert gone.forgotten is True, "cancellata a mano: tolta dall'utente"
    short = read_archived(find_archived(ws / "memory", entry_id("- Short answers")))
    assert short.forgotten is False and short.superseded_by, "riscritta: e' cambiata, non tolta"
    assert {e.id for e in list_archived(ws / "memory")} >= {CREMONA}


def test_a_hand_edit_of_another_file_is_ignored(ws) -> None:
    (ws / "notes.md").write_text("- a\n", encoding="utf-8")
    svc.archive_hand_edit(ws, ws / "notes.md", "")
    assert list_archived(ws / "memory") == []



def test_undo_with_lock_false_puts_it_back_as_it_was(ws) -> None:
    """L'«Annulla» del cestino su una voce che non era sistemata dall'utente la
    rimette senza lucchetto: prima ogni voce rimessa lo prendeva (visto sul
    Titan il 25/11/2026)."""
    svc.remove(ws, "user", CREMONA)
    svc.restore(ws, CREMONA, lock=False)

    assert CREMONA in _live(ws)
    assert MemoryLocks(ws).locked_ids("USER.md") == set()


def test_recent_leaves_out_a_change_whose_new_wording_is_still_there(ws) -> None:
    """La frase vecchia di una voce che c'e' ancora sta sotto quella voce, non
    anche in «Dimenticato di recente»; se la voce nuova se ne va, c'e' lei,
    come tolta, e non anche tutte le sue versioni di prima."""
    new_id = svc.correct(ws, "user", CREMONA, "Lives in Lodi with Oreste", "changed")["id"]
    assert svc.list_memory(ws)["recent"] == []

    svc.remove(ws, "user", new_id)
    rows = svc.list_memory(ws)["recent"]
    assert [(r["kind"], r["id"]) for r in rows] == [("removed", new_id)]


def test_removed_by_you_leaves_out_what_dream_dropped(ws) -> None:
    """«Tolte da te» ha solo quel che ha tolto l'utente. Una frase che Dream
    toglie finisce in archivio senza ``forgotten``, e qui non compare: prima
    stava nella stessa lista, con la stessa etichetta."""
    from jenny.agent.memory_archive import ArchivedEntry, archive_entry

    archive_entry(ws / "memory", ArchivedEntry(
        id=entry_id("- Old plan"), text="- Old plan", source="USER.md"))
    svc.remove(ws, "user", CREMONA)

    rows = svc.list_memory(ws)["recent"]
    assert [(r["kind"], r["id"]) for r in rows] == [("removed", CREMONA)]


def test_the_list_carries_each_sentence_theme_and_counts_the_unsorted(ws) -> None:
    from jenny.agent.memory_themes import MemoryThemes

    out = svc.list_memory(ws)
    assert out["unsorted"] == 3
    MemoryThemes(ws).set_many({CREMONA: "identity"})

    out = svc.list_memory(ws)
    mine = {e["id"]: e for e in out["files"][0]["entries"]}
    assert mine[CREMONA]["theme"] == "identity"
    assert out["unsorted"] == 2
    # Gli appunti non hanno temi: non contano fra quelli da ordinare.
    assert "theme" not in out["files"][1]["entries"][0]


def test_a_corrected_sentence_stays_in_its_theme(ws) -> None:
    from jenny.agent.memory_themes import MemoryThemes

    MemoryThemes(ws).set_many({CREMONA: "identity"})
    new_id = svc.correct(ws, "user", CREMONA, "Lives in Lodi with Oreste", "wrong")["id"]
    assert MemoryThemes(ws).all()[new_id] == "identity"
