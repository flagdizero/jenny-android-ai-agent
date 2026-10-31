"""Il recupero dal tier freddo: cosa deve valere perché "non è perso" sia vero.

La fase 2 ha mantenuto la sua promessa sul disco. Questi test tengono ferma la
parte che riguarda ciò che Jenny *sa*: che l'archivio si possa interrogare senza
corrispondenze testuali, e che quando l'elenco non ci sta tutto lo dica invece di
accorciarsi in silenzio — che è il modo in cui ``grep`` falliva.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_entry,
    list_archived,
    read_archived,
)
from jenny.agent.tools.memory_recall import MemoryRecallTool


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


def _archive(store: MemoryStore, text: str, *, when: date, ident: str, **kw) -> None:
    archive_entry(
        store.memory_dir,
        ArchivedEntry(id=ident, text=text, source=kw.pop("source", "USER.md"), **kw),
        when=when,
    )


class TestReadingBackWhatWasWritten:
    def test_an_archived_entry_survives_the_round_trip(self, tmp_path):
        store = _store(tmp_path)
        _archive(
            store, "- Il ficus sta sul balcone", when=date(2026, 3, 1), ident="aaaa1111",
            heading="Preferences", retention="permanent",
        )
        [entry] = list_archived(store.memory_dir)
        assert entry.id == "aaaa1111"
        # Il trattino del bullet appartiene al file di provenienza, non al fatto.
        assert entry.text == "Il ficus sta sul balcone"
        assert entry.source == "USER.md"
        assert entry.heading == "Preferences"
        assert entry.demoted == "2026-03-01"

    def test_a_file_someone_simplified_by_hand_is_still_readable(self, tmp_path):
        """La cartella è visibile nel file browser, quindi verrà aperta a mano.

        Perdere il fatto perché mancano i metadati sarebbe il fallimento esatto
        che l'archivio esiste per impedire.
        """
        store = _store(tmp_path)
        path = store.memory_dir / "archive" / "2026-03-02-bbbb2222.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("il fatto e basta\n", encoding="utf-8")
        entry = read_archived(path)
        assert entry is not None
        assert entry.text == "il fatto e basta"
        assert entry.id == "bbbb2222"

    def test_the_filename_id_wins_over_a_hand_edited_header(self, tmp_path):
        """Chi chiede un id deve riavere la voce che quell'id nomina."""
        store = _store(tmp_path)
        path = store.memory_dir / "archive" / "2026-03-03-cccc3333.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: zzzz9999\n---\n\nun fatto\n", encoding="utf-8")
        entry = read_archived(path)
        assert entry is not None and entry.id == "cccc3333"

    def test_newest_first_because_that_is_who_survives_a_cut(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "vecchio", when=date(2026, 1, 1), ident="old00000")
        _archive(store, "recente", when=date(2026, 8, 1), ident="new00000")
        assert [e.id for e in list_archived(store.memory_dir)] == ["new00000", "old00000"]


class TestTheListIsNeverFiltered:
    async def test_every_entry_is_listed_whatever_language_it_is_in(self, tmp_path):
        """Il difetto vero di ``grep``: questa memoria è bilingue.

        Nessuna corrispondenza per sottostringa da nessuna parte, quindi un
        fatto scritto in italiano è raggiungibile da una domanda in inglese —
        perché a scegliere è il modello, non un confronto di caratteri.
        """
        store = _store(tmp_path)
        _archive(store, "La pianta sul balcone si chiama Brindolo", when=date(2026, 5, 1), ident="it000000")
        _archive(store, "The user prefers short replies", when=date(2026, 5, 2), ident="en000000")
        out = await MemoryRecallTool(tmp_path).execute()
        assert "Brindolo" in out and "short replies" in out

    async def test_an_empty_archive_says_nothing_was_lost(self, tmp_path):
        out = await MemoryRecallTool(tmp_path).execute()
        assert "empty" in out.lower()
        assert "lost" in out.lower()


class TestOpeningEntries:
    async def test_an_id_returns_the_fact_with_where_it_came_from(self, tmp_path):
        store = _store(tmp_path)
        _archive(
            store, "una decisione presa a marzo", when=date(2026, 3, 4), ident="dddd4444",
            source="memory/MEMORY.md", heading="Project Context",
        )
        out = await MemoryRecallTool(tmp_path).execute(ids=["dddd4444"])
        assert "una decisione presa a marzo" in out
        assert "memory/MEMORY.md" in out and "Project Context" in out

    async def test_an_unknown_id_is_reported_not_dropped(self, tmp_path):
        """Restituire "solo le altre" farebbe concludere che quel fatto non c'è."""
        store = _store(tmp_path)
        _archive(store, "presente", when=date(2026, 3, 5), ident="eeee5555")
        out = await MemoryRecallTool(tmp_path).execute(ids=["eeee5555", "ffff6666"])
        assert "presente" in out
        assert "ffff6666" in out


class TestTruncationIsNeverSilent:
    async def test_a_long_list_comes_in_pages_and_says_so(self, tmp_path, monkeypatch):
        import jenny.agent.tools.memory_recall as mod

        monkeypatch.setattr(mod, "_INDEX_MAX_CHARS", 120)
        store = _store(tmp_path)
        for i in range(12):
            _archive(
                store, f"fatto numero {i} con abbastanza testo da occupare spazio",
                when=date(2026, 4, 1 + i), ident=f"id{i:06d}",
            )
        tool = MemoryRecallTool(tmp_path)
        first = await tool.execute()
        # Il totale vero è sempre nell'intestazione, anche quando la pagina è corta.
        assert "12 facts" in first
        assert "Page 1 of 12" in first
        assert "id000011" in first  # la più recente in prima pagina
        last = await tool.execute(page=12)
        assert "id000000" in last
        assert "Page 12 of 12" in last
        # Una pagina oltre la fine dà l'ultima, non un elenco vuoto.
        assert "id000000" in await tool.execute(page=99)

    async def test_a_page_fits_in_a_tool_result(self):
        """Oltre ``max_tool_result_chars`` (16.000) il risultato finisce in un file e
        il modello ne vede l'anteprima: successo sull'emulatore il 12/10/2026."""
        import jenny.agent.tools.memory_recall as mod

        assert mod._INDEX_MAX_CHARS < 16_000


class TestSearch:
    """Con ``query``: le parole del modello, in tutte e due le lingue."""

    async def test_it_finds_a_fact_past_the_first_page(self, tmp_path, monkeypatch):
        import jenny.agent.tools.memory_recall as mod

        monkeypatch.setattr(mod, "_INDEX_MAX_CHARS", 200)
        store = _store(tmp_path)
        _archive(store, "Il nonno aveva una barca a vela chiamata Brezza",
                 when=date(2026, 1, 1), ident="0a0a0a0a")
        for i in range(30):
            _archive(store, f"Un altro fatto qualunque, numero {i}",
                     when=date(2026, 5, 1 + i % 28), ident=f"ff{i:06d}")
        tool = MemoryRecallTool(tmp_path)
        assert "Brezza" not in await tool.execute()
        out = await tool.execute(query=["grandfather", "boat", "nonno", "barca"])
        assert "Brezza" in out
        assert "0a0a0a0a" in out
        assert out.index("0a0a0a0a") < out.index("\n\n") + 40  # in testa

    async def test_words_are_matched_inside_longer_words(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Il veterinario del gatto è il dottor Rossi",
                 when=date(2026, 1, 1), ident="0b0b0b0b")
        out = await MemoryRecallTool(tmp_path).execute(query=["vet"])
        assert "Rossi" in out

    async def test_accents_and_case_do_not_matter(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Città preferita: Kyōto", when=date(2026, 1, 1), ident="0c0c0c0c")
        assert "0c0c0c0c" in await MemoryRecallTool(tmp_path).execute(query=["CITTA kyoto"])

    async def test_no_match_never_reads_as_never_said(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Un fatto", when=date(2026, 1, 1), ident="0d0d0d0d")
        out = await MemoryRecallTool(tmp_path).execute(query=["dentista", "dentist"])
        assert "Nothing matched" in out
        assert "does not mean it was never said" in out

    async def test_a_query_of_short_words_says_so(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Un fatto", when=date(2026, 1, 1), ident="0e0e0e0e")
        out = await MemoryRecallTool(tmp_path).execute(query=["di", "a"])
        assert "three letters" in out


class TestChains:
    """Una riga per fatto, non per formulazione."""

    async def test_versions_collapse_to_the_newest(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Lavora al bar Aurora", when=date(2026, 8, 1),
                 ident="aaaa0001", superseded_by="aaaa0002")
        _archive(store, "Lavora al bar Aurora, di mattina", when=date(2026, 8, 5),
                 ident="aaaa0002", superseded_by="bbbb0000")
        out = await MemoryRecallTool(tmp_path).execute()
        assert "1 facts" in out
        assert "aaaa0002" in out and "aaaa0001" not in out
        assert "(+1 earlier wording)" in out
        assert "replaced by a newer wording" in out

    async def test_opening_shows_every_wording_and_the_live_one(self, tmp_path):
        from jenny.agent.tools.memory_entries import entry_id

        (tmp_path / "USER.md").write_text("- Lavora al forno Stella\n", encoding="utf-8")
        live = entry_id("- Lavora al forno Stella")
        store = _store(tmp_path)
        _archive(store, "Lavora al bar Aurora", when=date(2026, 8, 1),
                 ident="aaaa0001", superseded_by="aaaa0002")
        _archive(store, "Lavora al bar Aurora, di mattina", when=date(2026, 8, 5),
                 ident="aaaa0002", superseded_by=live)
        out = await MemoryRecallTool(tmp_path).execute(ids=["aaaa0001"])
        assert "Current wording, in USER.md: Lavora al forno Stella" in out
        assert out.index("di mattina") < out.index("Earlier wordings")
        assert "aaaa0001" in out and "aaaa0002" in out

    async def test_a_search_finds_a_word_only_an_old_wording_had(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Suona il basso nei Lupi", when=date(2026, 8, 1),
                 ident="aaaa0001", superseded_by="aaaa0002")
        _archive(store, "Suona in una band il giovedì", when=date(2026, 8, 5),
                 ident="aaaa0002")
        out = await MemoryRecallTool(tmp_path).execute(query=["Lupi"])
        assert "aaaa0002" in out

    async def test_a_cycle_does_not_loop(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Versione A", when=date(2026, 8, 1), ident="aaaa0001",
                 superseded_by="aaaa0002")
        _archive(store, "Versione B", when=date(2026, 8, 2), ident="aaaa0002",
                 superseded_by="aaaa0001")
        out = await MemoryRecallTool(tmp_path).execute()
        assert "Versione" in out

    async def test_a_retracted_fact_says_it_was_never_true(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "Suona il contrabbasso il giovedì", when=date(2026, 9, 17),
                 ident="cccc0001", retracted=True)
        tool = MemoryRecallTool(tmp_path)
        assert "RETRACTED" in await tool.execute()
        assert "RETRACTED" in await tool.execute(query=["contrabbasso"])
        assert "RETRACTED" in await tool.execute(ids=["cccc0001"])


class TestWhereItIsMounted:
    def test_the_main_agent_gets_it(self):
        from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

        assert "memory_recall" in _HARDCODED_TOOL_MODULES
        assert "orchestrator" in MemoryRecallTool._scopes

    def test_dream_does_not_get_it(self, tmp_path):
        """Il prompt di Dream si è già dimostrato sensibile a ciò che gli si aggiunge.

        Non è un divieto di principio: è che non c'è ancora una misura che dica
        che gli serve, e la superficie in più lì è costata run interi.
        """
        tools = _store(tmp_path).build_dream_tools()
        assert "recall" not in tools.tool_names

    def test_it_is_read_only(self, tmp_path):
        assert MemoryRecallTool(tmp_path).read_only is True


class TestThePromptStopsPointingAtGrep:
    def test_the_archive_line_names_the_tool(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "qualcosa", when=date(2026, 6, 1), ident="gggg7777")
        line = store.get_archive_context()
        assert "`recall`" in line
        # ``grep`` resta nominato solo per dire perché non si usa.
        assert "not `grep`" in line

    def test_the_line_still_disappears_on_an_empty_archive(self, tmp_path):
        assert _store(tmp_path).get_archive_context() == ""
