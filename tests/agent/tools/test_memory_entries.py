"""Scrittura per voce sui file di memoria (``jenny/agent/tools/memory_entries.py``).

Il campione ``_REAL_SHAPE`` riproduce la forma **misurata** di ``USER.md`` sul
Titan 2 il 2026-08-18 — titolo ``#``, sezioni ``##``, bullet di una riga, alcuni
con ``**Chiave**: valore`` — con contenuti inventati. La forma è il contratto:
questi file non hanno uno schema, hanno un'abitudine, e un parser che va bene
solo sulla forma che gli inventa il test non prova niente.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.memory_archive import archive_dir, find_archived
from jenny.agent.memory_budget import FileBudget, make_write_size_guard
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.memory_entries import (
    MEMORY_TARGETS,
    MemoryEntryTool,
    add_entry,
    entry_id,
    find_entry,
    parse_entries,
    remove_entry,
    render_entries,
    replace_entry,
)

_REAL_SHAPE = """# User Profile

## Basic Information

- **Name**: Alex Doe
- **Language**: English

## Preferences

- Short answers, no formal reports
- Prefers delegating technical work to subagents
"""


class TestParseEntries:
    def test_it_finds_the_bullets_with_their_sections(self):
        entries = parse_entries(_REAL_SHAPE)

        assert [e.heading for e in entries] == [
            "Basic Information",
            "Basic Information",
            "Preferences",
            "Preferences",
        ]
        assert entries[0].text == "- **Name**: Alex Doe"

    def test_the_file_title_is_not_a_section(self):
        """``# User Profile`` è il nome del file, non una sezione.

        Trattarlo da intestazione metterebbe ogni voce sotto un nome che nel file
        non indicizza niente, e un ``add`` con quel nome creerebbe un secondo
        titolo a metà pagina.
        """
        entries = parse_entries("# Title\n\n- solo\n")

        assert entries[0].heading == ""

    def test_a_section_without_a_blank_line_still_parses(self):
        """``MEMORY.md`` sul device attacca i bullet all'intestazione senza riga
        vuota, ``USER.md`` no. Entrambi esistono, quindi entrambi valgono."""
        entries = parse_entries("# T\n\n## Project Context\n- primo\n- secondo\n")

        assert len(entries) == 2
        assert all(e.heading == "Project Context" for e in entries)

    def test_prose_and_blank_lines_are_not_entries(self):
        entries = parse_entries("# T\n\nUn paragrafo qualsiasi.\n\n## S\n\n- vero\n")

        assert [e.text for e in entries] == ["- vero"]

    def test_an_indented_line_belongs_to_the_bullet_above(self):
        text = "## S\n\n- primo\n  continua qui\n- secondo\n"

        entries = parse_entries(text)

        assert len(entries) == 2
        assert entries[0].text == "- primo\n  continua qui"

    def test_a_nested_bullet_belongs_to_its_parent(self):
        """Staccarlo produrrebbe una voce orfana che nessuno può né leggere né
        rimuovere: il sotto-elenco senza la riga che lo introduce non è un fatto."""
        entries = parse_entries("## S\n\n- padre\n  - figlio\n- altro\n")

        assert len(entries) == 2
        assert "figlio" in entries[0].text

    def test_an_empty_file_has_no_entries(self):
        assert parse_entries("") == []


class TestEntryId:
    def test_it_is_stable_across_calls(self):
        assert entry_id("- un fatto") == entry_id("- un fatto")

    def test_edge_whitespace_does_not_change_it(self):
        """Un salvataggio dal browser Workspace può aggiungere un a-capo: è la
        stessa voce, e un id che cambiasse manderebbe un ``remove`` a vuoto."""
        assert entry_id("- un fatto") == entry_id("  - un fatto  \n")

    def test_different_text_gets_a_different_id(self):
        assert entry_id("- un fatto") != entry_id("- un altro fatto")

    def test_it_is_not_positional(self):
        """L'id vive nel contenuto: la stessa voce in due file diversi, o spostata
        di sezione, resta sé stessa."""
        first = parse_entries("## A\n- x\n- y\n")[1]
        second = parse_entries("## B\n\n- y\n")[0]

        assert first.id == second.id


class TestEntryIdDiscriminatesInTheOtherDirection:
    """L'id è un *discriminante*, e i suoi due errori non costano lo stesso.

    Tre consumatori leggono "stesso id" come "stesso fatto", e nessuno dei tre
    chiede conferma: ``add_entry`` rifiuta la scrittura con "already present",
    l'archiviatore di ``_make_archiver`` calcola ``surviving`` per id e quindi
    **non** archivia la forma vecchia, e il blocco "Already recorded" di
    ``MemoryStore`` la deduplica via.

    Da qui l'asimmetria che questi test tengono ferma. Un id che *distingue di
    troppo* costa un file d'archivio in più — recuperabile, visibile, gratis. Un
    id che *confonde* costa una correzione rifiutata in silenzio e una voce che
    se ne va senza copia, cioè esattamente il guasto che la fase 2 esiste per
    impedire. Quindi si normalizza **solo** ciò che un salvataggio può cambiare
    senza che nessuno l'abbia voluto (v. ``TestEntryId`` sopra), e tutto ciò che
    una persona ha scritto diverso è una voce diversa.

    La direzione positiva era già coperta; questa no — misurato il 23/08 con una
    mutazione (``line.rstrip()`` → ``line.strip().lower()``) sopravvissuta alla
    suite intera.
    """

    def test_case_counts(self):
        """Maiuscole e minuscole distinguono, perché distinguono nei fatti.

        Un percorso, un comando, un nome proprio: correggerne la cassa è una
        correzione, e con un id insensibile alla cassa ``add_entry`` risponde
        "already present" e la correzione non arriva su disco.
        """
        assert entry_id("- Enzo") != entry_id("- enzo")

    def test_inner_spacing_counts(self):
        """Gli spazi *interni* non sono spazi di bordo: nessun salvataggio li
        introduce da sé, quindi una differenza qui è una differenza voluta."""
        assert entry_id("- a  b") != entry_id("- a b")

    def test_punctuation_counts(self):
        assert entry_id("- non è vero") != entry_id("- non è vero.")

    def test_the_indentation_of_a_continuation_line_counts(self):
        """Il rientro è ciò che tiene un sotto-elenco attaccato alla sua voce
        (v. ``_is_continuation``): perderlo nell'id renderebbe indistinguibili
        due voci che il parser legge in modo diverso."""
        assert entry_id("- padre\n  - figlio") != entry_id("- padre\n- figlio")

    def test_but_a_trailing_space_on_an_inner_line_still_does_not(self):
        """L'altra metà del contratto, sulle voci di più righe.

        ``text.strip()`` da solo pulisce i due bordi del testo intero; gli spazi
        in coda a una riga *interna* li toglie il ``rstrip()`` per riga, e sono
        proprio quelli che un editor lascia dietro di sé.
        """
        assert entry_id("- padre  \n  - figlio") == entry_id("- padre\n  - figlio")


class TestFindEntry:
    def test_by_id(self):
        entries = parse_entries(_REAL_SHAPE)

        found, why = find_entry(entries, entries[2].id)

        assert why == ""
        assert found is not None and found.text == entries[2].text

    def test_by_a_fragment_of_its_text(self):
        entries = parse_entries(_REAL_SHAPE)

        found, why = find_entry(entries, "no formal reports")

        assert why == ""
        assert found is not None and "Short answers" in found.text

    def test_the_fragment_is_case_insensitive(self):
        entries = parse_entries(_REAL_SHAPE)

        found, _ = find_entry(entries, "SHORT ANSWERS")

        assert found is not None

    def test_an_ambiguous_fragment_is_refused_and_lists_the_candidates(self):
        """Indovinare qui significa cancellare la voce sbagliata. Gli id sono già
        in mano al modello, quindi il rifiuto è risolvibile senza rileggere."""
        entries = parse_entries("## S\n\n- il gatto dorme\n- il gatto mangia\n")

        found, why = find_entry(entries, "il gatto")

        assert found is None
        assert "2 entries match" in why
        assert entries[0].id in why and entries[1].id in why

    def test_a_miss_says_so(self):
        found, why = find_entry(parse_entries(_REAL_SHAPE), "niente del genere")

        assert found is None
        assert "no entry matches" in why

    def test_an_empty_target_is_refused(self):
        found, why = find_entry(parse_entries(_REAL_SHAPE), "   ")

        assert found is None
        assert "empty target" in why


class TestAddEntry:
    def test_it_appends_at_the_end_of_its_section(self):
        out, why = add_entry(_REAL_SHAPE, "Wakes up late", heading="Basic Information")

        assert why == ""
        entries = parse_entries(out)
        in_section = [e.text for e in entries if e.heading == "Basic Information"]
        assert in_section[-1] == "- Wakes up late"

    def test_not_at_the_end_of_the_file(self):
        """Una voce finita sotto la sezione sbagliata è peggio di una mancante:
        si legge come vera in un contesto che non è il suo."""
        out, _ = add_entry(_REAL_SHAPE, "Wakes up late", heading="Basic Information")

        assert out.index("Wakes up late") < out.index("## Preferences")

    def test_a_missing_section_is_created_at_the_end(self):
        out, why = add_entry(_REAL_SHAPE, "Plays chess", heading="Topics of Interest")

        assert why == ""
        assert out.rstrip().endswith("- Plays chess")
        assert "## Topics of Interest" in out

    def test_a_created_section_keeps_a_blank_line_before_it(self):
        out, _ = add_entry(_REAL_SHAPE, "Plays chess", heading="Topics")

        assert "\n\n## Topics\n" in out

    def test_without_a_heading_it_goes_at_the_end_of_the_file(self):
        out, why = add_entry(_REAL_SHAPE, "Un fatto senza casa")

        assert why == ""
        assert out.rstrip().endswith("- Un fatto senza casa")

    def test_the_leading_dash_is_optional(self):
        with_dash, _ = add_entry(_REAL_SHAPE, "- Con trattino")
        without, _ = add_entry(_REAL_SHAPE, "Con trattino")

        assert with_dash == without

    def test_a_duplicate_is_reported_and_changes_nothing(self):
        """Il fatto *è* in memoria, che è il risultato voluto — ma dirlo dà al
        consolidatore l'informazione per smettere di riproporlo (D5)."""
        out, why = add_entry(_REAL_SHAPE, "Short answers, no formal reports")

        assert out == _REAL_SHAPE
        assert why.startswith("already present as")

    def test_an_empty_entry_is_refused(self):
        out, why = add_entry(_REAL_SHAPE, "   ")

        assert out == ""
        assert "empty entry" in why

    def test_the_rest_of_the_file_is_untouched(self):
        out, _ = add_entry(_REAL_SHAPE, "Nuovo", heading="Preferences")

        for line in _REAL_SHAPE.splitlines():
            assert line in out.splitlines()

    def test_it_works_on_an_empty_file(self):
        out, why = add_entry("", "Il primo fatto", heading="Basic Information")

        assert why == ""
        assert parse_entries(out)[0].heading == "Basic Information"


class TestReplaceEntry:
    def test_it_keeps_the_position(self):
        entries = parse_entries(_REAL_SHAPE)
        out, why = replace_entry(_REAL_SHAPE, entries[0].id, "**Name**: Alex Roe")

        assert why == ""
        after = parse_entries(out)
        assert after[0].text == "- **Name**: Alex Roe"
        assert after[0].heading == "Basic Information"
        assert len(after) == len(entries)

    def test_a_multi_line_entry_is_replaced_whole(self):
        text = "## S\n\n- primo\n  continua\n- secondo\n"

        out, why = replace_entry(text, "primo", "solo una riga")

        assert why == ""
        assert "continua" not in out
        assert len(parse_entries(out)) == 2

    def test_an_unknown_target_changes_nothing(self):
        out, why = replace_entry(_REAL_SHAPE, "inesistente", "x")

        assert out == ""
        assert "no entry matches" in why

    def test_replacing_with_nothing_is_refused(self):
        out, why = replace_entry(_REAL_SHAPE, "Short answers", "  ")

        assert out == ""
        assert "empty one" in why


class TestRemoveEntry:
    def test_it_takes_the_entry_out(self):
        out, why, entry = remove_entry(_REAL_SHAPE, "Short answers")

        assert why == ""
        assert entry is not None and "Short answers" in entry.text
        assert "Short answers" not in out
        assert len(parse_entries(out)) == 3

    def test_it_gives_the_entry_back(self):
        """Nella fase 2 ``remove`` non cancella più, degrada: la voce deve
        arrivare all'archivio prima di sparire dal file, e questa firma è il
        gancio per quel passo."""
        _, _, entry = remove_entry(_REAL_SHAPE, "Short answers")

        assert entry is not None
        assert entry.heading == "Preferences"
        assert entry.id == entry_id(entry.text)

    def test_a_multi_line_entry_goes_whole(self):
        text = "## S\n\n- primo\n  continua\n- secondo\n"

        out, _, _ = remove_entry(text, "primo")

        assert "continua" not in out
        assert parse_entries(out)[0].text == "- secondo"

    def test_an_unknown_target_changes_nothing(self):
        out, why, entry = remove_entry(_REAL_SHAPE, "inesistente")

        assert out == "" and entry is None
        assert "no entry matches" in why


class TestRenderEntries:
    def test_it_groups_by_section_and_shows_ids(self):
        out = render_entries(parse_entries(_REAL_SHAPE))

        assert "## Basic Information" in out
        assert "## Preferences" in out
        for entry in parse_entries(_REAL_SHAPE):
            assert entry.id in out

    def test_an_empty_file_says_so_instead_of_nothing(self):
        assert render_entries([]) == "(no entries)"


class TestTheTool:
    @pytest.fixture
    def tool(self, tmp_path: Path) -> MemoryEntryTool:
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        return MemoryEntryTool(tmp_path)

    async def test_add_reaches_the_file(self, tool, tmp_path):
        out = await tool.execute(action="add", file="user", text="Plays chess")

        assert out.startswith("1 added.")
        assert "Plays chess" in (tmp_path / "USER.md").read_text(encoding="utf-8")

    async def test_every_answer_carries_the_usage_and_the_ids(self, tool):
        """Il round-trip in meno che Hermes si compra con la stessa scelta: dopo
        una chiamata il modello sa già cosa c'è e come si chiama."""
        out = await tool.execute(action="add", file="user", text="Plays chess")

        assert "USER.md:" in out and "chars" in out and "entries" in out
        assert "## Preferences" in out
        assert all(e.id in out for e in parse_entries(_REAL_SHAPE))

    async def test_list_reads_without_writing(self, tool, tmp_path):
        before = (tmp_path / "USER.md").read_bytes()

        out = await tool.execute(action="list", file="user")

        assert (tmp_path / "USER.md").read_bytes() == before
        assert "entries" in out

    async def test_remove_then_add_round_trips(self, tool, tmp_path):
        """Torna il contenuto, non l'ordine: la voce riaggiunta va in coda alla
        sua sezione. Pretendere il byte-per-byte vorrebbe dire ricordare dove
        stava, cioè una posizione — l'unica cosa che questo modulo non conserva
        di proposito."""
        await tool.execute(action="remove", file="user", target="Short answers")
        await tool.execute(
            action="add", file="user", text="Short answers, no formal reports",
            heading="Preferences",
        )

        after = parse_entries((tmp_path / "USER.md").read_text(encoding="utf-8"))
        before = parse_entries(_REAL_SHAPE)
        assert {(e.heading, e.id) for e in after} == {(e.heading, e.id) for e in before}

    async def test_replace_by_id_from_a_previous_answer(self, tool, tmp_path):
        listing = await tool.execute(action="list", file="user")
        wanted = parse_entries(_REAL_SHAPE)[0].id
        assert wanted in listing

        await tool.execute(action="replace", file="user", target=wanted, text="**Name**: Alex Roe")

        assert "Alex Roe" in (tmp_path / "USER.md").read_text(encoding="utf-8")

    async def test_the_memory_file_is_created_under_its_directory(self, tool, tmp_path):
        await tool.execute(action="add", file="memory", text="Il progetto X è attivo")

        target = tmp_path / MEMORY_TARGETS["memory"]
        assert target.exists()
        assert "Il progetto X" in target.read_text(encoding="utf-8")

    async def test_a_missing_file_is_not_an_error(self, tmp_path):
        """Stato normale di un'installazione nuova: la prima ``add`` lo crea."""
        out = await MemoryEntryTool(tmp_path).execute(action="list", file="user")

        assert "(no entries)" in out

    async def test_an_unknown_file_is_refused_without_touching_disk(self, tool, tmp_path):
        before = (tmp_path / "USER.md").read_bytes()

        out = await tool.execute(action="add", file="soul", text="x")

        assert "unknown file" in out
        assert (tmp_path / "USER.md").read_bytes() == before

    async def test_soul_is_not_a_target(self):
        """Prosa con una strutura, non un elenco: ridurlo è mestiere del review
        pass, che legge prima di decidere."""
        assert "soul" not in MEMORY_TARGETS

    async def test_an_unknown_action_says_which_ones_exist(self, tool):
        out = await tool.execute(action="rewrite", file="user", text="x")

        assert "unknown action" in out
        assert "add, replace, remove or list" in out

    async def test_a_failed_remove_leaves_the_file_alone(self, tool, tmp_path):
        before = (tmp_path / "USER.md").read_bytes()

        out = await tool.execute(action="remove", file="user", target="inesistente")

        assert "Cannot remove" in out
        assert (tmp_path / "USER.md").read_bytes() == before

    async def test_an_ambiguous_remove_names_the_ids(self, tmp_path):
        (tmp_path / "USER.md").write_text(
            "## S\n\n- il gatto dorme\n- il gatto mangia\n", encoding="utf-8",
        )
        tool = MemoryEntryTool(tmp_path)

        out = await tool.execute(action="remove", file="user", target="il gatto")

        assert "entries match" in out
        assert "il gatto dorme" in (tmp_path / "USER.md").read_text(encoding="utf-8")

    async def test_it_takes_no_path_at_all(self, tool):
        """Nessun parametro di path significa nessuna superficie di traversal: i
        due file li risolve il tool, non il chiamante."""
        assert "path" not in tool.parameters["properties"]
        assert set(tool.parameters["properties"]["file"]["enum"]) == set(MEMORY_TARGETS)


class TestItPlaysByDreamsWriteRules:
    """I due contratti che ``build_dream_tools`` si aspetta da chi scrive.

    Non è simmetria estetica: ``dream_should_advance_cursor`` decide se il
    cursore avanza leggendo ``writes_attempted``/``writes_ok``, e il budget è
    ancora vincolante per Dream finché la fase 3 non lo rende consultivo. Un
    secondo percorso di scrittura che sfuggisse a uno dei due farebbe avanzare il
    cursore su un fatto rimasto fuori, oppure aprirebbe un buco nel tetto per
    distrazione invece che per scelta.
    """

    @pytest.fixture
    def states(self) -> FileStates:
        return FileStates()

    def _tool(self, tmp_path: Path, states: FileStates, guard=None) -> MemoryEntryTool:
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        return MemoryEntryTool(tmp_path, file_states=states, write_size_guard=guard)

    async def test_a_successful_add_counts_as_a_write(self, tmp_path, states):
        tool = self._tool(tmp_path, states)

        await tool.execute(action="add", file="user", text="Plays chess")

        assert states.writes_attempted == 1
        assert states.writes_ok == 1

    async def test_list_counts_as_nothing(self, tmp_path, states):
        """Un run che ha solo guardato non ha tentato niente, ed è la differenza
        fra "non c'era niente da salvare" e "non ci sono riuscito"."""
        tool = self._tool(tmp_path, states)

        await tool.execute(action="list", file="user")

        assert states.writes_attempted == 0
        assert states.writes_ok == 0

    async def test_a_refused_add_counts_the_attempt_and_not_the_write(self, tmp_path, states):
        tool = self._tool(tmp_path, states, guard=lambda path, text: "Refused: over budget.")

        out = await tool.execute(action="add", file="user", text="Plays chess")

        assert "Refused" in out
        assert states.writes_attempted == 1
        assert states.writes_ok == 0

    async def test_a_refused_add_leaves_the_file_alone(self, tmp_path, states):
        tool = self._tool(tmp_path, states, guard=lambda path, text: "Refused: over budget.")

        await tool.execute(action="add", file="user", text="Plays chess")

        assert (tmp_path / "USER.md").read_text(encoding="utf-8") == _REAL_SHAPE

    async def test_a_refusal_stays_open_until_the_fact_lands(self, tmp_path, states):
        """Il rifiuto si chiude solo quando il contenuto arriva su disco, non alla
        prima scrittura qualunque: è ciò che distingue un modello che pota e
        riscrive portandosi dentro il fatto da uno che pota e basta."""
        refusing = self._tool(tmp_path, states, guard=lambda path, text: "Refused.")
        await refusing.execute(action="add", file="user", text="Plays chess")
        assert states.unrecovered_refusals == 1

        allowing = MemoryEntryTool(tmp_path, file_states=states)
        await allowing.execute(action="add", file="user", text="Plays chess")

        assert states.unrecovered_refusals == 0

    async def test_the_guard_sees_the_whole_file_not_the_entry(self, tmp_path, states):
        """Il tetto è sulla dimensione del file: al guard va il testo finale, che è
        ciò che il budget misura. Passargli la voce direbbe sempre "ci sta"."""
        seen: list[str] = []
        tool = self._tool(tmp_path, states, guard=lambda path, text: seen.append(text) or None)

        await tool.execute(action="add", file="user", text="Plays chess")

        assert len(seen) == 1
        assert "# User Profile" in seen[0] and "Plays chess" in seen[0]

    async def test_without_a_guard_nothing_is_refused(self, tmp_path, states):
        tool = self._tool(tmp_path, states)

        out = await tool.execute(action="add", file="user", text="Plays chess")

        assert out.startswith("1 added.")


class TestItIsMountedOnDream:
    async def test_dream_gets_it_with_the_run_scoped_state_and_guard(self, tmp_path):
        """Stesso ``FileStates`` degli altri tool del run, altrimenti i contatori
        raccontano due run diversi; e stesso guard, altrimenti il tetto ha una
        porta di servizio."""
        from jenny.agent.memory import MemoryStore

        store = MemoryStore(tmp_path)
        refusals: list[str] = []
        tools = store.build_dream_tools(
            write_size_guard=lambda path, text: refusals.append(path.name) or "Refused.",
        )

        assert "memory" in tools.tool_names
        out = await tools.execute("memory", {"action": "add", "file": "user", "text": "x"})

        assert "Refused" in out
        assert refusals == ["USER.md"]
        assert tools.file_states.writes_attempted == 1
        assert tools.file_states.writes_ok == 0


class TestAddTakesAWholeBatch:
    """Il rimedio a D10, trovato sul Titan 2 il 2026-08-18.

    Con la sola ``add`` di un fatto per volta il modello non la usa per scoprire
    se un fatto c'è già: chiama ``list``, che risponde per tutto il file in una
    chiamata, e filtra da sé. Sceglie bene — una ``list`` contro N ``add`` è
    aritmetica, non preferenza — ma così un batch di soli duplicati non produce
    nessuna evidenza per voce e viene trattenuto pur essendo consolidato. Qui
    cambia l'aritmetica: la mossa economica e quella che produce evidenza
    diventano la stessa chiamata.
    """

    @pytest.fixture
    def tool(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        return MemoryEntryTool(tmp_path, file_states=FileStates())

    async def test_several_facts_land_in_one_call(self, tool, tmp_path):
        out = await tool.execute(
            action="add", file="user", heading="Preferences",
            texts=["Plays chess", "Wakes up late", "Drinks tea"],
        )

        assert out.startswith("3 added.")
        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        for fact in ("Plays chess", "Wakes up late", "Drinks tea"):
            assert fact in body

    async def test_one_write_for_the_whole_batch(self, tool, tmp_path):
        """Una scrittura sola: N scritture su un file che il browser Workspace può
        avere aperto sono N occasioni di trovarlo a metà."""
        await tool.execute(
            action="add", file="user", texts=["Uno", "Due", "Tre"],
        )

        assert tool._file_states.writes_ok == 1

    async def test_a_batch_of_duplicates_reports_each_one(self, tool):
        """Il caso esatto che sul device teneva fermo il cursore."""
        out = await tool.execute(
            action="add", file="user",
            texts=["Short answers, no formal reports",
                   "Prefers delegating technical work to subagents"],
        )

        assert out.startswith("2 already present.")
        assert tool.entries_already_present == 2
        assert tool.entries_added == 0

    async def test_a_duplicate_batch_writes_nothing(self, tool, tmp_path):
        before = (tmp_path / "USER.md").read_bytes()

        await tool.execute(
            action="add", file="user", texts=["Short answers, no formal reports"],
        )

        assert (tmp_path / "USER.md").read_bytes() == before
        assert tool._file_states.writes_attempted == 0

    async def test_a_mixed_batch_counts_both_kinds(self, tool):
        out = await tool.execute(
            action="add", file="user",
            texts=["Short answers, no formal reports", "Plays chess"],
        )

        assert "1 added" in out and "1 already present" in out
        assert tool.entries_added == 1
        assert tool.entries_already_present == 1

    async def test_a_removal_is_counted(self, tool):
        """Per Dream una correzione che smentisce si applica così: senza il
        conto, il batch che la porta resterebbe trattenuto."""
        out = await tool.execute(action="remove", file="user", target="Short answers")

        assert out.startswith("Removed")
        assert tool.entries_removed == 1

    async def test_a_failed_removal_is_not_counted(self, tool):
        await tool.execute(action="remove", file="user", target="nothing like this")

        assert tool.entries_removed == 0

    async def test_the_answer_names_each_fact(self, tool):
        out = await tool.execute(
            action="add", file="user",
            texts=["Short answers, no formal reports", "Plays chess"],
        )

        assert "+ Plays chess" in out
        assert "= Short answers, no formal reports (already there)" in out

    async def test_a_single_text_still_works(self, tool, tmp_path):
        out = await tool.execute(action="add", file="user", text="Plays chess")

        assert out.startswith("1 added.")
        assert "Plays chess" in (tmp_path / "USER.md").read_text(encoding="utf-8")

    async def test_duplicates_inside_the_same_batch_are_caught(self, tool):
        """Il secondo esemplare è già presente per via del primo, nella stessa
        chiamata: i fatti si applicano uno per uno proprio per questo."""
        out = await tool.execute(
            action="add", file="user", texts=["Plays chess", "Plays chess"],
        )

        assert "1 added" in out and "1 already present" in out

    async def test_what_fits_is_saved_and_the_rest_is_declared(self, tmp_path):
        """Tutto-o-niente sotto pressione perderebbe anche i fatti che ci stavano.

        Il guard qui accetta finché il file resta sotto una soglia, come quello
        vero: i primi fatti entrano, il primo che sfora si ferma e viene
        dichiarato.
        """
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        limit = len(_REAL_SHAPE) + 40
        states = FileStates()
        tool = MemoryEntryTool(
            tmp_path,
            file_states=states,
            write_size_guard=lambda p, t: None if len(t) <= limit else "Refused: over budget.",
        )

        out = await tool.execute(
            action="add", file="user", texts=["Corto", "x" * 60, "Mai visto"],
        )

        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "Corto" in body
        assert "1 added" in out and "1 refused" in out
        # Il rifiuto resta aperto: quel fatto non è su disco e il cursore non deve
        # avanzare finché non ci arriva.
        assert states.unrecovered_refusals == 1

    async def test_an_empty_list_says_so_instead_of_writing(self, tool, tmp_path):
        before = (tmp_path / "USER.md").read_bytes()

        out = await tool.execute(action="add", file="user", texts=[])

        assert "no text given" in out
        assert (tmp_path / "USER.md").read_bytes() == before


class TestRemovingIsDemoting:
    """Da qui in poi togliere una voce è uno **spostamento**.

    È la riga su cui poggiano tre cose che il piano si aspetta più avanti: far
    spazio riesce sempre, quindi nessuna scrittura ha più bisogno di essere
    rifiutata; il review pass non può più perdere niente, solo ricollocarlo; e il
    pavimento del "non cancellare mai" smette di poter bloccare un run, perché non
    blocca un trasloco.
    """

    @pytest.fixture
    def tool(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        return MemoryEntryTool(tmp_path, file_states=FileStates())

    async def test_the_fact_survives_in_the_archive(self, tool, tmp_path):
        await tool.execute(action="remove", file="user", target="Short answers")

        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "Short answers" not in body
        archived = list(archive_dir(tmp_path / "memory").glob("*.md"))
        assert len(archived) == 1
        assert "Short answers, no formal reports" in archived[0].read_text(encoding="utf-8")

    async def test_it_records_where_the_fact_lived(self, tool, tmp_path):
        """Senza l'indirizzo di provenienza, una voce riletta fra sei mesi è una
        frase senza contesto e rimetterla al suo posto è un indovinello."""
        await tool.execute(action="remove", file="user", target="Short answers")

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "source: USER.md" in text
        assert "heading: Preferences" in text

    async def test_the_archive_is_written_before_the_file_shrinks(self, tool, tmp_path):
        """L'ordine è la garanzia. Dei due esiti di un fallimento a metà, "il fatto
        è in due posti" si ripara guardandolo; "in nessuno dei due" no."""
        entry = next(e for e in parse_entries(_REAL_SHAPE) if "Short answers" in e.text)

        await tool.execute(action="remove", file="user", target=entry.id)

        assert find_archived(tmp_path / "memory", entry.id) is not None

    async def test_a_failed_archive_leaves_the_entry_alone(self, tmp_path, monkeypatch):
        """Se l'archivio non si può scrivere, la rimozione non avviene: meglio un
        fatto ancora al suo posto che un fatto in nessun posto."""
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        tool = MemoryEntryTool(tmp_path, file_states=FileStates())
        monkeypatch.setattr(
            "jenny.agent.tools.memory_entries.archive_entry",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disco pieno")),
        )

        out = await tool.execute(action="remove", file="user", target="Short answers")

        assert "could not" in out and "left where it is" in out
        assert (tmp_path / "USER.md").read_text(encoding="utf-8") == _REAL_SHAPE

    async def test_the_answer_says_the_fact_was_kept(self, tool):
        """Il percorso non glielo diciamo — lo scrive il runtime — ma che la voce
        non sia persa sì: è ciò che rende potare una scelta reversibile."""
        out = await tool.execute(action="remove", file="user", target="Short answers")

        assert "Kept in the archive" in out
        assert "memory/archive" not in out

    async def test_removing_the_same_fact_twice_keeps_one_copy(self, tool, tmp_path):
        await tool.execute(action="remove", file="user", target="Short answers")
        await tool.execute(
            action="add", file="user", text="Short answers, no formal reports",
            heading="Preferences",
        )
        await tool.execute(action="remove", file="user", target="Short answers")

        assert len(list(archive_dir(tmp_path / "memory").glob("*.md"))) == 1

    async def test_a_failed_remove_archives_nothing(self, tool, tmp_path):
        await tool.execute(action="remove", file="user", target="inesistente")

        assert not archive_dir(tmp_path / "memory").exists()

    async def test_the_memory_file_demotes_to_the_same_place(self, tool, tmp_path):
        await tool.execute(action="add", file="memory", text="Il progetto X è attivo")
        await tool.execute(action="remove", file="memory", target="progetto X")

        archived = list(archive_dir(tmp_path / "memory").glob("*.md"))
        assert len(archived) == 1
        assert "source: memory/MEMORY.md" in archived[0].read_text(encoding="utf-8")


class TestMakingRoomAlwaysSucceeds:
    """La proprietà su cui poggiano la fase 3 e la fase 6.

    Prima, "fare spazio" poteva fallire in due modi e ne bastava uno: la voce da
    togliere era protetta dal pavimento del *never delete*, oppure toglierla la
    perdeva per sempre. Con la degradazione nessuno dei due esiste più —
    rimuovere è uno spostamento, e uno spostamento non cancella niente.

    Il seguito è quello che il piano si aspetta: se far posto riesce sempre,
    nessuna scrittura ha più *bisogno* di essere rifiutata (fase 3), e un review
    che pota troppo ha ricollocato invece di distrutto (fase 6).
    """

    def _guard(self, path: Path, budget: int):
        """Il guard vero, non un doppio: è il suo comportamento che conta qui."""
        return make_write_size_guard([
            FileBudget(label="USER.md", path=path, chars=budget + 999, budget=budget),
        ])

    @pytest.fixture
    def full(self, tmp_path: Path):
        """Un ``USER.md`` già oltre il tetto, cioè lo stato in cui Dream si
        paralizzava: 96–99% e un fatto nuovo che non entra."""
        hot = tmp_path / "USER.md"
        hot.write_text(_REAL_SHAPE, encoding="utf-8")
        budget = len(_REAL_SHAPE) + 10
        tool = MemoryEntryTool(
            tmp_path, file_states=FileStates(), write_size_guard=self._guard(hot, budget),
        )
        return tool, hot

    async def test_the_new_fact_does_not_fit(self, full):
        """Il punto di partenza: senza far posto, la scrittura viene rifiutata."""
        tool, _ = full

        out = await tool.execute(
            action="add", file="user", text="Un fatto nuovo abbastanza lungo da non entrarci",
        )

        assert "refused" in out.lower()

    async def test_a_removal_is_never_refused(self, full):
        """Una rimozione rimpicciolisce, e il guard lascia sempre passare chi
        rimpicciolisce — o un file sopra soglia non potrebbe mai essere potato."""
        tool, _ = full

        out = await tool.execute(action="remove", file="user", target="Short answers")

        assert "Removed" in out

    async def test_and_it_costs_nothing_because_the_fact_moves(self, full, tmp_path):
        tool, _ = full

        await tool.execute(action="remove", file="user", target="Short answers")

        archived = list(archive_dir(tmp_path / "memory").glob("*.md"))
        assert len(archived) == 1
        assert "Short answers, no formal reports" in archived[0].read_text(encoding="utf-8")

    async def test_remove_then_add_lands_the_fact(self, full, tmp_path):
        """Il ciclo intero, che è ciò che il modello non riusciva a completare: far
        posto e poi scrivere. Qui si verifica che il *runtime* lo permetta —
        che sia il modello a compierlo è un'altra questione, ed è la fase 6."""
        tool, hot = full
        new_fact = "Un fatto nuovo abbastanza lungo da non entrarci"

        await tool.execute(action="remove", file="user", target="Short answers")
        await tool.execute(
            action="remove", file="user", target="Prefers delegating technical work",
        )
        out = await tool.execute(action="add", file="user", text=new_fact, heading="Preferences")

        assert "1 added" in out
        assert new_fact in hot.read_text(encoding="utf-8")

    async def test_and_nothing_was_lost_along_the_way(self, full, tmp_path):
        """La verifica che conta davvero: alla fine della sequenza ogni fatto che
        c'era all'inizio è ancora leggibile, o nel file caldo o nell'archivio."""
        tool, hot = full
        before = {e.id: e.text for e in parse_entries(_REAL_SHAPE)}

        await tool.execute(action="remove", file="user", target="Short answers")
        await tool.execute(
            action="remove", file="user", target="Prefers delegating technical work",
        )
        await tool.execute(
            action="add", file="user", text="Un fatto nuovo abbastanza lungo da non entrarci",
        )

        surviving = {e.id for e in parse_entries(hot.read_text(encoding="utf-8"))}
        for entry_key, text in before.items():
            if entry_key in surviving:
                continue
            archived = find_archived(tmp_path / "memory", entry_key)
            assert archived is not None, f"perso: {text}"
            assert text.lstrip("- ") in archived.read_text(encoding="utf-8")


class TestReplacingAlsoKeepsTheOldVersion:
    """La maglia larga trovata sul Titan 2 il 2026-08-19.

    Il review pass ha riformulato una voce di ``USER.md`` con ``memory replace``,
    e la versione vecchia è sparita senza passare da nessuna parte: ``remove``
    degradava, la rete al confine del file copriva ``apply_patch``, e in mezzo
    restava il tool per voci, che scrive per conto suo. Tre voci di quel
    passaggio si sono salvate e la quarta no — una difesa vale la sua maglia più
    larga.
    """

    @pytest.fixture
    def tool(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        from jenny.agent.tools.memory_entries import make_entry_archiver

        return MemoryEntryTool(
            tmp_path,
            file_states=FileStates(),
            entry_archiver=make_entry_archiver(tmp_path),
        )

    async def test_the_reworded_entry_leaves_its_old_form_behind(self, tool, tmp_path):
        await tool.execute(
            action="replace", file="user",
            target="Short answers",
            text="Short answers",
        )

        archived = list(archive_dir(tmp_path / "memory").glob("*.md"))
        assert len(archived) == 1
        assert "no formal reports" in archived[0].read_text(encoding="utf-8")

    async def test_the_new_version_is_in_the_hot_file(self, tool, tmp_path):
        await tool.execute(
            action="replace", file="user", target="Short answers", text="Short answers",
        )

        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "- Short answers\n" in body
        assert "no formal reports" not in body

    async def test_an_add_archives_nothing(self, tool, tmp_path):
        """Aggiungere non fa sparire niente, quindi non c'è niente da salvare."""
        await tool.execute(action="add", file="user", text="Plays chess")

        assert not archive_dir(tmp_path / "memory").exists()

    async def test_remove_still_archives_exactly_once(self, tool, tmp_path):
        """Due percorsi arrivano all'archivio su una ``remove`` — quello esplicito
        con l'ordine forte e il gancio in ``_commit`` — e l'idempotenza fa sì che
        non si pestino."""
        await tool.execute(action="remove", file="user", target="Short answers")

        assert len(list(archive_dir(tmp_path / "memory").glob("*.md"))) == 1


class TestTheArchiveKnowsWhatReplacedWhat:
    """Il legame fra la versione archiviata e quella che ne ha preso il posto.

    Sul telefono, il 12/10/2026, 271 file d'archivio su 479 erano versioni di 65
    fatti, senza un segno di quale sostituisse quale: per chi rilegge, sette
    riformulazioni sono sette fatti, e nessuna dice di essere vecchia.
    """

    @pytest.fixture
    def tool(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        from jenny.agent.tools.memory_entries import make_entry_archiver

        return MemoryEntryTool(
            tmp_path,
            file_states=FileStates(),
            entry_archiver=make_entry_archiver(tmp_path),
        )

    @staticmethod
    def _archived(tmp_path: Path, text: str):
        from jenny.agent.memory_archive import read_archived

        path = find_archived(tmp_path / "memory", entry_id(text))
        assert path is not None, text
        return read_archived(path)

    async def test_replace_links_the_old_version_to_the_new(self, tool, tmp_path):
        await tool.execute(
            action="replace", file="user", target="Short answers",
            text="Very short answers, never a report",
        )
        old = self._archived(tmp_path, "- Short answers, no formal reports")
        assert old.superseded_by == entry_id("- Very short answers, never a report")

    async def test_replace_links_even_a_wholly_different_wording(self, tool, tmp_path):
        """Da ``replace`` il legame è certo, somiglianza o no."""
        await tool.execute(
            action="replace", file="user", target="Short answers", text="Brevity first",
        )
        old = self._archived(tmp_path, "- Short answers, no formal reports")
        assert old.superseded_by == entry_id("- Brevity first")

    async def test_a_refused_replace_archives_nothing(self, tmp_path):
        from jenny.agent.tools.memory_entries import make_entry_archiver

        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        tool = MemoryEntryTool(
            tmp_path,
            file_states=FileStates(),
            entry_archiver=make_entry_archiver(tmp_path),
            write_size_guard=lambda path, text: "over budget",
        )
        out = await tool.execute(
            action="replace", file="user", target="Short answers", text="x" * 50,
        )
        assert "over budget" in out
        assert not archive_dir(tmp_path / "memory").exists()

    async def test_replace_with_the_same_text_archives_nothing(self, tool, tmp_path):
        await tool.execute(
            action="replace", file="user", target="Short answers",
            text="Short answers, no formal reports",
        )
        assert not archive_dir(tmp_path / "memory").exists()

    async def test_remove_wrong_marks_the_fact_retracted(self, tool, tmp_path):
        await tool.execute(action="remove", file="user", target="delegating", wrong=True)
        gone = self._archived(tmp_path, "- Prefers delegating technical work to subagents")
        assert gone.retracted
        assert gone.superseded_by == ""

    async def test_a_plain_remove_is_not_a_retraction(self, tool, tmp_path):
        await tool.execute(action="remove", file="user", target="delegating")
        assert not self._archived(
            tmp_path, "- Prefers delegating technical work to subagents",
        ).retracted

    def test_a_whole_file_rewrite_pairs_by_similarity(self, tmp_path):
        """``apply_patch`` porta solo il prima e il dopo: il legame si ricava."""
        from jenny.agent.tools.memory_entries import make_entry_archiver

        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        after = _REAL_SHAPE.replace(
            "- Short answers, no formal reports",
            "- Short answers in plain Italian, no formal reports",
        ).replace(
            "- Prefers delegating technical work to subagents\n",
            "- Plays the double bass on Thursdays\n",
        )
        make_entry_archiver(tmp_path)(tmp_path / "USER.md", after)

        reworded = self._archived(tmp_path, "- Short answers, no formal reports")
        assert reworded.superseded_by == entry_id(
            "- Short answers in plain Italian, no formal reports",
        )
        # Un fatto tolto e uno diverso aggiunto nella stessa scrittura non sono
        # la stessa cosa riscritta.
        unrelated = self._archived(tmp_path, "- Prefers delegating technical work to subagents")
        assert unrelated.superseded_by == ""


class TestPairSuccessors:
    @staticmethod
    def _entries(*texts: str):
        return parse_entries("\n".join(f"- {t}" for t in texts))

    def test_each_entry_is_paired_at_most_once(self):
        from jenny.agent.tools.memory_entries import pair_successors

        gone = self._entries("Lives in Rome, Pigneto", "Lives in Rome")
        arrived = self._entries("Lives in Rome, Marranella")
        # Due candidate sopra soglia, una voce comparsa sola: un solo legame, alla
        # più simile ({lives, rome} su tre radici contro due su quattro).
        assert pair_successors(gone, arrived) == {gone[1].id: arrived[0].id}

    def test_two_rewrites_pair_crosswise_correctly(self):
        from jenny.agent.tools.memory_entries import pair_successors

        gone = self._entries("Plays Magic the Gathering on Fridays", "Allergic to dust mites")
        arrived = self._entries(
            "Allergic to dust mites and pollen", "Plays Magic the Gathering on Saturdays",
        )
        assert pair_successors(gone, arrived) == {
            gone[0].id: arrived[1].id,
            gone[1].id: arrived[0].id,
        }

    def test_below_the_threshold_nothing_is_paired(self):
        from jenny.agent.tools.memory_entries import pair_successors

        gone = self._entries("Prefers short answers")
        arrived = self._entries("Has a brother who moved in last month")
        assert pair_successors(gone, arrived) == {}



# ---------------------------------------------------------------------------
# T7.9 — igiene: l'argomento non coercito, e il ``create()`` senza difese
# ---------------------------------------------------------------------------


class TestHeadingIsCoerced:
    """``heading`` era l'unico argomento che arrivava crudo a ``.strip()``.

    ``action`` e ``file`` passano da ``str(...)``; ``heading`` no, quindi un
    provider che serializza ``{"heading": 3}`` faceva ``AttributeError`` dentro
    ``add_entry``. Non porta via il run — è raccolto come errore soft del tool —
    ma spende uno slot di ``ToolErrorBudget`` per una conversione mancante.
    """

    @pytest.fixture
    def tool(self, tmp_path: Path) -> MemoryEntryTool:
        (tmp_path / "USER.md").write_text(_REAL_SHAPE, encoding="utf-8")
        return MemoryEntryTool(tmp_path)

    async def test_a_numeric_heading_does_not_raise(self, tool, tmp_path):
        out = await tool.execute(
            action="add", file="user", text="Plays chess", heading=3,
        )

        assert out.startswith("1 added.")
        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        # L'intenzione si conserva: la sezione "3" nasce come qualunque altra.
        assert "## 3" in body
        assert body.index("## 3") < body.index("Plays chess")

    async def test_a_numeric_heading_that_exists_is_matched_as_text(self, tool, tmp_path):
        (tmp_path / "USER.md").write_text(
            "# User Profile\n\n## 2026\n\n- Primo\n\n## Altro\n\n- Fuori\n",
            encoding="utf-8",
        )

        await tool.execute(action="add", file="user", text="Secondo", heading=2026)

        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert body.count("## 2026") == 1
        assert body.index("Secondo") < body.index("## Altro")

    async def test_a_blank_heading_does_not_open_a_nameless_section(self, tool, tmp_path):
        """Con ``""`` grezzo ``add_entry`` cercava la sezione senza titolo e, non
        trovandola, scriveva ``## `` — un'intestazione senza nome, in un file che
        il modello poi rilegge."""
        await tool.execute(
            action="add", file="user", text="Plays chess", heading="   ",
        )

        body = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "## \n" not in body
        assert not any(line.strip() == "##" for line in body.splitlines())
        # Il caso documentato "non si sa": in fondo al file, facile da spostare.
        assert body.rstrip().endswith("- Plays chess")


class TestCreateRefusesToBuildUnprotected:
    """Il tool ha tre dipendenze e ``ToolContext`` non porta le due che contano.

    Percorso irraggiungibile oggi (nessun ``TOOLS``, ``_plugin_discoverable`` a
    False): il test esiste per il giorno in cui il modulo entra in
    ``_HARDCODED_TOOL_MODULES``. Un ``create()`` che solleva non aborta il boot
    (``ToolLoader`` lo registra in ``failures`` e logga a ERROR), mentre un
    ``memory replace`` costruito senza ``entry_archiver`` perde in silenzio la
    formulazione precedente di una voce di ``USER.md``.
    """

    def test_it_raises_instead_of_returning_an_unprotected_tool(self, tmp_path):
        from jenny.agent.tools.context import ToolContext

        ctx = ToolContext(config=None, workspace=str(tmp_path))

        with pytest.raises(RuntimeError) as excinfo:
            MemoryEntryTool.create(ctx)

        message = str(excinfo.value)
        # Il messaggio è l'unica cosa che il log mostrerà: deve nominare cosa manca.
        assert "write_size_guard" in message
        assert "entry_archiver" in message

    def test_the_explicit_construction_is_still_the_way(self, tmp_path):
        """Il rifiuto vale per il percorso del contesto, non per il tool."""
        tool = MemoryEntryTool(
            tmp_path,
            file_states=FileStates(),
            entry_archiver=lambda _path, _text: None,
        )

        assert tool.name == "memory"
