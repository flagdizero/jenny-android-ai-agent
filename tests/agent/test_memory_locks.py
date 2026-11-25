"""Le voci che l'utente ha corretto sopravvivono a Dream.

Il modello è ``test_soul_rules.py``: la verità sta fuori da dove Dream scrive
(qui una riga di ``state.sqlite``), e dopo ogni passata si riproietta. I banchi
qui sotto simulano le tre strade da cui Dream tocca una voce — lo strumento per
voci, una riscrittura a file intero, il review pass — e le due da cui la tocca
qualcun altro (chat, editor, stanza), che invece si rispettano sempre.

Nomi inventati: il repository è pubblico.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent import memory_locks
from jenny.agent.memory import MemoryStore
from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_entry,
    find_archived,
    read_archived,
)
from jenny.agent.memory_budget import budget_report
from jenny.agent.memory_locks import (
    NO_SUCCESSOR,
    MemoryLocks,
    budget_exemptions,
    reassert,
    reconcile,
)
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.memory_entries import (
    MemoryEntryTool,
    entry_id,
    make_entry_archiver,
    parse_entries,
)

USER = """# User

## Basics

- Name: Alex Doe
- Lives in Cremona with Oreste

## Preferences

- Short answers
"""

LOCKED = "- Lives in Cremona with Oreste"
LOCKED_ID = entry_id(LOCKED)
SOURCE = "USER.md"


def _entry(text: str):
    return next(e for e in parse_entries(USER) if e.text == text)


def _ids(ws: Path) -> list[str]:
    return [e.id for e in parse_entries((ws / "USER.md").read_text(encoding="utf-8"))]


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "USER.md").write_text(USER, encoding="utf-8")
    MemoryLocks(tmp_path).lock(SOURCE, _entry(LOCKED), at=1_700_000_000)
    return tmp_path


def _user(ws: Path) -> str:
    return (ws / "USER.md").read_text(encoding="utf-8")


# ── La tabella ─────────────────────────────────────────────────────────────


class TestTheTable:
    def test_a_lock_round_trips(self, ws):
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.entry_id == LOCKED_ID
        assert row.text == LOCKED
        assert row.heading == "Basics"
        assert row.displaced_by == ""
        assert MemoryLocks(ws).locked_ids(SOURCE) == {LOCKED_ID}
        assert MemoryLocks(ws).locked_ids("memory/MEMORY.md") == frozenset()

    def test_move_keeps_the_date_of_the_correction(self, ws):
        new = parse_entries("## Basics\n- Lives in Lodi with Oreste\n")[0]
        assert MemoryLocks(ws).move(SOURCE, LOCKED_ID, new) is True
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.entry_id == new.id
        assert row.locked_at == 1_700_000_000

    def test_move_and_unlock_of_an_unknown_id_do_nothing(self, ws):
        new = parse_entries("- Altro\n")[0]
        assert MemoryLocks(ws).move(SOURCE, "deadbeef", new) is False
        assert MemoryLocks(ws).unlock(SOURCE, "deadbeef") is False
        assert MemoryLocks(ws).locked_ids(SOURCE) == {LOCKED_ID}

    def test_it_lives_outside_the_workspace(self, ws):
        """Fuori dal workspace: nessuno strumento di Dream ci arriva."""
        assert not list(ws.rglob("*.sqlite"))
        assert (ws.parent / "db" / "state.sqlite").exists()


# ── Lo strumento per voci ──────────────────────────────────────────────────


class TestTheEntryToolRespectsTheLock:
    def _tool(self, ws: Path, *, yield_locks: bool = False) -> MemoryEntryTool:
        return MemoryEntryTool(
            ws, file_states=FileStates(), locks=MemoryLocks(ws), yield_locks=yield_locks,
            entry_archiver=make_entry_archiver(
                ws, locks=MemoryLocks(ws), yield_locks=yield_locks,
            ),
        )

    async def test_replace_is_refused_before_any_write_is_counted(self, ws):
        tool = self._tool(ws)
        out = await tool.execute(
            action="replace", file="user", target=LOCKED_ID, text="Lives with Oreste",
        )
        assert "locked" in out and "corrected it themselves" in out
        assert _user(ws) == USER
        assert tool.entries_locked == 1
        assert tool.entries_replaced == 0
        assert tool._file_states.writes_attempted == 0

    async def test_remove_is_refused_too(self, ws):
        tool = self._tool(ws)
        out = await tool.execute(action="remove", file="user", target="Cremona")
        assert "locked" in out
        assert _user(ws) == USER
        assert find_archived(ws / "memory", LOCKED_ID) is None
        assert tool.entries_locked == 1

    async def test_other_entries_are_untouched_by_the_lock(self, ws):
        tool = self._tool(ws)
        await tool.execute(action="replace", file="user", target="Short", text="Brief answers")
        assert "Brief answers" in _user(ws)
        assert tool.entries_locked == 0

    async def test_the_list_marks_locked_entries(self, ws):
        out = await self._tool(ws).execute(action="list", file="user")
        assert f"{LOCKED_ID} [locked]" in out
        assert out.count("[locked]") == 1

    async def test_a_correction_moves_the_lock_to_the_new_wording(self, ws):
        tool = self._tool(ws, yield_locks=True)
        await tool.execute(
            action="replace", file="user", target=LOCKED_ID,
            text="Lives in Lodi with Oreste",
        )
        new_id = entry_id("- Lives in Lodi with Oreste")
        assert new_id in _ids(ws)
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.entry_id == new_id
        assert row.displaced_by == ""
        assert tool.entries_replaced == 1
        # E la passata dopo non la disfa.
        assert reassert(ws) == 0
        assert new_id in _ids(ws)

    async def test_a_correction_that_removes_drops_the_lock(self, ws):
        tool = self._tool(ws, yield_locks=True)
        await tool.execute(action="remove", file="user", target=LOCKED_ID, wrong=True)
        assert LOCKED_ID not in _ids(ws)
        assert MemoryLocks(ws).rows() == []
        archived = read_archived(find_archived(ws / "memory", LOCKED_ID))
        assert archived is not None and archived.retracted


# ── La rete al confine del file ────────────────────────────────────────────


class TestTheArchiverNotesWhatDreamMoved:
    def test_a_rewritten_locked_entry_is_marked_displaced_by_its_successor(self, ws):
        archiver = make_entry_archiver(ws, locks=MemoryLocks(ws))
        archiver(ws / "USER.md", USER.replace(LOCKED, "- Lives with Oreste"))
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.displaced_by == entry_id("- Lives with Oreste")

    def test_a_dropped_locked_entry_is_marked_with_no_successor(self, ws):
        archiver = make_entry_archiver(ws, locks=MemoryLocks(ws))
        archiver(ws / "USER.md", USER.replace(LOCKED + "\n", ""))
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.displaced_by == NO_SUCCESSOR

    def test_a_second_rewrite_moves_the_mark_to_the_newest_wording(self, ws):
        archiver = make_entry_archiver(ws, locks=MemoryLocks(ws))
        first = USER.replace(LOCKED, "- Lives with Oreste")
        archiver(ws / "USER.md", first)
        (ws / "USER.md").write_text(first, encoding="utf-8")
        archiver(ws / "USER.md", first.replace("- Lives with Oreste", "- Lives with O."))
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.displaced_by == entry_id("- Lives with O.")

    def test_in_a_correction_batch_the_lock_follows(self, ws):
        archiver = make_entry_archiver(ws, locks=MemoryLocks(ws), yield_locks=True)
        archiver(ws / "USER.md", USER.replace(LOCKED, "- Lives in Lodi with Oreste"))
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.entry_id == entry_id("- Lives in Lodi with Oreste")
        assert row.displaced_by == ""

    def test_it_never_raises(self, ws, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("database gone")

        monkeypatch.setattr(MemoryLocks, "rows", boom)
        archiver = make_entry_archiver(ws, locks=MemoryLocks(ws))
        archiver(ws / "USER.md", USER.replace(LOCKED + "\n", ""))
        # La voce è comunque passata dall'archivio: la rete fa il suo mestiere.
        assert find_archived(ws / "memory", LOCKED_ID) is not None


# ── reassert ───────────────────────────────────────────────────────────────


class TestReassert:
    def _dream_rewrites(self, ws: Path, new_text: str) -> None:
        """Una riscrittura a file intero di Dream, rete compresa."""
        make_entry_archiver(ws, locks=MemoryLocks(ws))(ws / "USER.md", new_text)
        (ws / "USER.md").write_text(new_text, encoding="utf-8")

    def test_a_replaced_entry_goes_back_in_its_place(self, ws):
        self._dream_rewrites(ws, USER.replace(LOCKED, "- Lives with Oreste"))
        assert reassert(ws) == 1
        assert _user(ws) == USER
        # La formulazione di Dream è in archivio, legata alla voce dell'utente.
        dream = read_archived(find_archived(ws / "memory", entry_id("- Lives with Oreste")))
        assert dream is not None and dream.superseded_by == LOCKED_ID
        # La voce dell'utente è di nuovo viva: niente copia in archivio.
        assert find_archived(ws / "memory", LOCKED_ID) is None
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.displaced_by == ""

    def test_a_dropped_entry_goes_back_under_its_heading(self, ws):
        self._dream_rewrites(ws, USER.replace(LOCKED + "\n", ""))
        assert reassert(ws) == 1
        entries = parse_entries(_user(ws))
        back = next(e for e in entries if e.id == LOCKED_ID)
        assert back.heading == "Basics"

    def test_a_dropped_section_comes_back_with_it(self, ws):
        self._dream_rewrites(ws, "# User\n\n## Preferences\n\n- Short answers\n")
        reassert(ws)
        back = next(e for e in parse_entries(_user(ws)) if e.id == LOCKED_ID)
        assert back.heading == "Basics"
        assert "Name: Alex Doe" not in _user(ws)  # il resto lo decide Dream

    def test_it_is_idempotent(self, ws):
        self._dream_rewrites(ws, USER.replace(LOCKED, "- Lives with Oreste"))
        reassert(ws)
        once = _user(ws)
        assert reassert(ws) == 0
        assert _user(ws) == once

    def test_an_entry_dream_wrote_back_identical_needs_nothing(self, ws):
        self._dream_rewrites(ws, USER.replace(LOCKED + "\n", ""))
        (ws / "USER.md").write_text(USER, encoding="utf-8")
        assert reassert(ws) == 1  # la riga si chiude
        assert _user(ws) == USER
        assert MemoryLocks(ws).rows(SOURCE)[0].displaced_by == ""

    def test_an_untouched_lock_does_nothing(self, ws):
        assert reassert(ws) == 0
        assert _user(ws) == USER

    def test_a_tombstoned_entry_dream_re_added_is_dropped(self, ws):
        removed = USER.replace("- Short answers\n", "")
        (ws / "USER.md").write_text(removed, encoding="utf-8")
        archive_entry(ws / "memory", ArchivedEntry(
            id=entry_id("- Short answers"), text="- Short answers", source=SOURCE,
            forgotten=True,
        ))
        self._dream_rewrites(ws, USER)  # Dream la rimette con uno strumento di file
        assert "Short answers" in _user(ws)
        assert reassert(ws) == 1
        assert "Short answers" not in _user(ws)
        assert _ids(ws) == [e.id for e in parse_entries(removed)]

    def test_the_same_sentence_put_back_from_chat_stays(self, ws):
        """Rimessa fuori da Dream: è una scelta dell'utente, e non si disfa."""
        archive_entry(ws / "memory", ArchivedEntry(
            id=entry_id("- Short answers"), text="- Short answers", source=SOURCE,
            forgotten=True,
        ))
        assert reassert(ws) == 0
        assert "Short answers" in _user(ws)

    def test_it_never_raises(self, ws, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("database gone")

        monkeypatch.setattr(MemoryLocks, "rows", boom)
        assert reassert(ws) == 0
        assert _user(ws) == USER

    def test_the_review_pass_tools_are_covered_end_to_end(self, ws):
        """Il review pass riscrive con ``write_file``: la rete annota, reassert rimette."""
        import asyncio

        tools = MemoryStore(ws).build_dream_tools()
        rewritten = USER.replace(LOCKED, "- Lives with Oreste")
        asyncio.run(tools.get("write_file").execute(path="USER.md", content=rewritten))
        assert LOCKED_ID not in _ids(ws)
        reassert(ws)
        assert _user(ws) == USER


# ── reconcile ──────────────────────────────────────────────────────────────


class TestReconcile:
    """Un cambio fatto fuori da Dream non si disfa mai: il lucchetto lo segue."""

    def test_it_follows_the_archive_link(self, ws):
        archive_entry(ws / "memory", ArchivedEntry(
            id=LOCKED_ID, text=LOCKED, source=SOURCE, heading="Basics",
            superseded_by=entry_id("- Moved to Lodi"),
        ))
        (ws / "USER.md").write_text(USER.replace(LOCKED, "- Moved to Lodi"), encoding="utf-8")
        assert reconcile(ws) == 1
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.entry_id == entry_id("- Moved to Lodi")
        assert reassert(ws) == 0
        assert "- Moved to Lodi" in _user(ws)

    def test_it_follows_the_most_similar_wording(self, ws):
        changed = USER.replace(LOCKED, "- Lives in Lodi with Oreste since 2025")
        (ws / "USER.md").write_text(changed, encoding="utf-8")
        reconcile(ws)
        [row] = MemoryLocks(ws).rows(SOURCE)
        assert row.entry_id == entry_id("- Lives in Lodi with Oreste since 2025")
        assert _user(ws) == changed

    def test_it_drops_a_lock_with_no_successor(self, ws):
        changed = USER.replace(LOCKED + "\n", "")
        (ws / "USER.md").write_text(changed, encoding="utf-8")
        assert reconcile(ws) == 1
        assert MemoryLocks(ws).rows() == []
        assert _user(ws) == changed

    def test_it_drops_a_retracted_one_even_with_a_similar_entry(self, ws):
        archive_entry(ws / "memory", ArchivedEntry(
            id=LOCKED_ID, text=LOCKED, source=SOURCE, retracted=True,
        ))
        (ws / "USER.md").write_text(
            USER.replace(LOCKED, "- Lives in Lodi with Oreste"), encoding="utf-8",
        )
        reconcile(ws)
        assert MemoryLocks(ws).rows() == []

    def test_it_leaves_dreams_displacements_to_reassert(self, ws):
        MemoryLocks(ws).mark_displaced(SOURCE, LOCKED_ID, NO_SUCCESSOR)
        (ws / "USER.md").write_text(USER.replace(LOCKED + "\n", ""), encoding="utf-8")
        assert reconcile(ws) == 0
        assert MemoryLocks(ws).locked_ids(SOURCE) == {LOCKED_ID}

    def test_a_live_entry_needs_nothing(self, ws):
        assert reconcile(ws) == 0
        assert MemoryLocks(ws).locked_ids(SOURCE) == {LOCKED_ID}

    def test_it_never_raises(self, ws, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("database gone")

        monkeypatch.setattr(MemoryLocks, "rows", boom)
        assert reconcile(ws) == 0


# ── Il budget e il ciclo ───────────────────────────────────────────────────


class TestTheBudgetSkipsLockedEntries:
    def test_locked_entries_are_not_counted(self, ws):
        store = MemoryStore(ws)
        plain = budget_report(store, memory_chars=0, user_chars=1000, soul_chars=0)
        exempt = budget_report(
            store, memory_chars=0, user_chars=1000, soul_chars=0,
            locked=budget_exemptions(ws),
        )
        user_plain = next(i for i in plain if i.label == "USER.md")
        user_exempt = next(i for i in exempt if i.label == "USER.md")
        assert user_plain.chars - user_exempt.chars == len(LOCKED) + 1
        assert user_exempt.measure_now() == user_exempt.chars

    def test_a_long_locked_entry_does_not_make_dream_refuse(self, tmp_path):
        from jenny.agent.memory_budget import make_write_size_guard

        long_fact = "- " + "Una correzione lunga dell'utente. " * 10
        text = f"# User\n\n## Basics\n\n{long_fact}\n"
        (tmp_path / "USER.md").write_text(text, encoding="utf-8")
        MemoryLocks(tmp_path).lock(SOURCE, parse_entries(text)[0])
        report = budget_report(
            MemoryStore(tmp_path), memory_chars=0, user_chars=100, soul_chars=0,
            locked=budget_exemptions(tmp_path),
        )
        guard = make_write_size_guard(report)
        assert guard(tmp_path / "USER.md", text + "- Plays chess\n") is None

    def test_an_unreadable_database_exempts_nothing(self, ws, monkeypatch):
        import sqlite3

        def boom(*_a, **_k):
            raise sqlite3.OperationalError("locked")

        monkeypatch.setattr(MemoryLocks, "rows", boom)
        assert budget_exemptions(ws) == {}


class TestTheCycle:
    def test_a_lock_refusal_does_not_hold_the_batch(self):
        from jenny.agent.dream_cycle import batch_was_not_consolidated

        assert batch_was_not_consolidated(
            before=[], history_text="- [durable] Lives in Lodi", stuck=0,
            attempted=1, locked=1,
        ) is False

    def test_cycle_in_flight_follows_the_claim(self):
        from jenny.agent.dream_cycle import (
            claim_dream_cycle,
            cycle_in_flight,
            release_dream_cycle,
        )

        async def run():
            assert not cycle_in_flight()
            assert claim_dream_cycle()
            try:
                assert cycle_in_flight()
            finally:
                release_dream_cycle()
            assert not cycle_in_flight()

        import asyncio

        asyncio.run(run())

    def test_finish_puts_the_locked_entry_back(self, ws):
        from jenny.agent.dream_cycle import finish_dream_cycle

        store = MemoryStore(ws)
        make_entry_archiver(ws, locks=MemoryLocks(ws))(
            ws / "USER.md", USER.replace(LOCKED + "\n", ""),
        )
        (ws / "USER.md").write_text(USER.replace(LOCKED + "\n", ""), encoding="utf-8")
        finish_dream_cycle(store, advanced=True, runs_since_review=0, stuck=0)
        assert LOCKED_ID in _ids(ws)

    async def test_begin_reconciles_first_and_reasserts_after_the_review(
        self, ws, monkeypatch,
    ):
        from types import SimpleNamespace

        from jenny.agent import dream_cycle, dream_review

        calls: list[str] = []
        monkeypatch.setattr(memory_locks, "reconcile", lambda _w: calls.append("reconcile"))
        monkeypatch.setattr(memory_locks, "reassert", lambda _w: calls.append("reassert"))

        async def review(*_a, **_k):
            calls.append("review")
            return SimpleNamespace(status="completed", freed=0)

        monkeypatch.setattr(dream_review, "run_dream_review", review)
        store = MemoryStore(ws)
        store.set_review_state(runs_since_review=99, stuck_runs=0)
        cfg = SimpleNamespace(
            memory_budget_chars=0, user_budget_chars=0, soul_budget_chars=0,
            review_every_runs=1,
        )
        await dream_cycle.begin_dream_cycle(SimpleNamespace(), store=store, cfg=cfg)
        assert calls == ["reconcile", "review", "reassert"]


class TestTheDreamToolsCarryTheLocks:
    async def test_by_default_the_lock_holds(self, ws):
        tools = MemoryStore(ws).build_dream_tools()
        out = await tools.get("memory").execute(
            action="replace", file="user", target=LOCKED_ID, text="Lives with Oreste",
        )
        assert "locked" in out
        assert tools.memory_entries.entries_locked == 1

    async def test_a_correction_batch_yields(self, ws):
        tools = MemoryStore(ws).build_dream_tools(user_correction=True)
        await tools.get("memory").execute(
            action="replace", file="user", target=LOCKED_ID,
            text="Lives in Lodi with Oreste",
        )
        assert MemoryLocks(ws).locked_ids(SOURCE) == {entry_id("- Lives in Lodi with Oreste")}
