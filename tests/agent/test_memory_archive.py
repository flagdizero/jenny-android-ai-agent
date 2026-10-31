"""Il tier freddo: dove va una voce quando lascia i file caldi.

Il formato è il contratto di questa fase, perché è ciò che un lettore fra sei mesi
— umano o modello — dovrà riuscire a interrogare. I test qui sotto tengono ferme
le tre proprietà su cui si regge: un file per voce, il fatto separato dai
metadati, e un nome che si ordina da solo.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jenny.agent import memory as memory_module
from jenny.agent.memory import MemoryStore
from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_dir,
    archive_entry,
    archive_filename,
    find_archived,
    list_archived,
    render_archived,
)
from jenny.agent.tools.memory_entries import (
    entry_id,
    make_entry_archiver,
    parse_entries,
)
from jenny.agent.tools.memory_recall import MemoryRecallTool

_ENTRY = ArchivedEntry(
    id="a1b2c3d4",
    text="- Preferisce risposte brevi e concrete",
    source="USER.md",
    heading="Preferences",
    retention="durable",
)


class TestTheFileName:
    def test_it_sorts_by_time_on_its_own(self):
        names = [
            archive_filename("ffff0000", date(2026, 8, 18)),
            archive_filename("0000ffff", date(2026, 1, 2)),
            archive_filename("aaaa1111", date(2026, 12, 31)),
        ]

        assert sorted(names) == [
            "2026-01-02-0000ffff.md",
            "2026-08-18-ffff0000.md",
            "2026-12-31-aaaa1111.md",
        ]

    def test_it_carries_the_content_id(self):
        """Lo stesso hash del tool per voci: una voce degradata si ritrova a
        partire dal suo testo, senza aprire niente."""
        text = "- Preferisce risposte brevi e concrete"

        assert entry_id(text) in archive_filename(entry_id(text), date(2026, 8, 18))


class TestTheFileBody:
    def test_the_fact_is_the_body_and_nothing_else(self):
        out = render_archived(_ENTRY)
        body = out.split("---\n\n", 1)[1]

        assert body.strip() == "Preferisce risposte brevi e concrete"

    def test_the_bullet_dash_does_not_come_along(self):
        """Il trattino apparteneva all'elenco di provenienza: qui è una frase."""
        assert "\n- Preferisce" not in render_archived(_ENTRY)

    def test_the_metadata_says_where_it_came_from(self):
        out = render_archived(_ENTRY)

        assert "source: USER.md" in out
        assert "heading: Preferences" in out
        assert "retention: durable" in out

    def test_an_unknown_field_is_omitted_not_left_empty(self):
        """Una riga ``retention:`` vuota direbbe che l'informazione è stata cercata
        e persa. La verità è che non c'era: i tag vengono tolti dal testo prima
        che arrivi nei file."""
        out = render_archived(
            ArchivedEntry(id="x", text="- Un fatto", source="USER.md"),
        )

        assert "retention:" not in out
        assert "heading:" not in out

    def test_it_is_greppable_for_the_fact_itself(self):
        """L'agente cerca qui con ``grep``: la frase deve stare su una riga sua,
        senza etichette davanti."""
        lines = render_archived(_ENTRY).splitlines()

        assert "Preferisce risposte brevi e concrete" in lines

    def test_it_ends_with_a_newline(self):
        assert render_archived(_ENTRY).endswith("\n")


class TestWriting:
    def test_it_creates_the_directory_under_memory(self, tmp_path: Path):
        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        assert path.parent == archive_dir(tmp_path)
        assert path.parent.name == "archive"
        assert path.name == "2026-08-18-a1b2c3d4.md"

    def test_the_fact_is_on_disk_and_readable(self, tmp_path: Path):
        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        assert "Preferisce risposte brevi e concrete" in path.read_text(encoding="utf-8")

    def test_one_file_per_entry(self, tmp_path: Path):
        """Non un registro che cresce: ``grep`` salta i file grandi in silenzio, e
        quel falso negativo si legge come "non l'ho mai saputo"."""
        for i in range(3):
            archive_entry(
                tmp_path,
                ArchivedEntry(id=f"id{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )

        assert len(list(archive_dir(tmp_path).glob("*.md"))) == 3

    def test_the_demoted_date_is_stamped_when_not_given(self, tmp_path: Path):
        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        assert "demoted: 2026-08-18" in path.read_text(encoding="utf-8")

    def test_an_explicit_demoted_date_is_kept(self, tmp_path: Path):
        entry = ArchivedEntry(
            id="b2", text="- Un fatto", source="USER.md", demoted="2026-01-01",
        )

        path = archive_entry(tmp_path, entry, when=date(2026, 8, 18))

        assert "demoted: 2026-01-01" in path.read_text(encoding="utf-8")


class TestItIsASetOfFactsNotALogOfEvents:
    """La stessa voce tolta due volte è lo stesso fatto.

    ``remove`` chiamerà questa funzione ogni volta che degrada, e un fatto può
    essere riaggiunto e ritolto. Due file con lo stesso testo e date diverse
    sarebbero solo rumore per chi cerca.
    """

    def test_re_archiving_does_not_make_a_second_file(self, tmp_path: Path):
        first = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        second = archive_entry(tmp_path, _ENTRY, when=date(2026, 9, 30))

        assert first == second
        assert len(list(archive_dir(tmp_path).glob("*.md"))) == 1

    def test_the_first_demotion_keeps_its_date(self, tmp_path: Path):
        archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        again = archive_entry(tmp_path, _ENTRY, when=date(2026, 9, 30))

        assert again.name.startswith("2026-08-18")
        assert "demoted: 2026-08-18" in again.read_text(encoding="utf-8")

    def test_find_returns_nothing_before_the_first_demotion(self, tmp_path: Path):
        assert find_archived(tmp_path, "a1b2c3d4") is None

    def test_find_survives_a_missing_directory(self, tmp_path: Path):
        assert find_archived(tmp_path / "nope", "a1b2c3d4") is None

    def test_find_matches_by_id_not_by_date(self, tmp_path: Path):
        archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        found = find_archived(tmp_path, "a1b2c3d4")

        assert found is not None and found.name == "2026-08-18-a1b2c3d4.md"


class TestLineage:
    """``superseded_by`` e ``retracted``: chi ha preso il posto, e cosa era falso."""

    def test_the_fields_round_trip(self, tmp_path: Path):
        from dataclasses import replace

        from jenny.agent.memory_archive import read_archived

        path = archive_entry(
            tmp_path, replace(_ENTRY, superseded_by="deadbeef", retracted=True),
            when=date(2026, 8, 18),
        )
        back = read_archived(path)
        assert back is not None
        assert back.superseded_by == "deadbeef"
        assert back.retracted

    def test_an_old_file_reads_as_unlinked(self, tmp_path: Path):
        from jenny.agent.memory_archive import read_archived

        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        back = read_archived(path)
        assert back is not None
        assert back.superseded_by == "" and not back.retracted
        assert "superseded_by" not in path.read_text(encoding="utf-8")

    def test_a_second_archiving_adds_the_link_it_lacked(self, tmp_path: Path):
        """La rete al confine del file e ``replace`` archiviano la stessa voce:
        chi arriva primo non deve decidere se il legame resta."""
        from dataclasses import replace

        first = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        again = archive_entry(
            tmp_path, replace(_ENTRY, superseded_by="deadbeef"), when=date(2026, 9, 30),
        )
        assert again == first
        text = first.read_text(encoding="utf-8")
        assert "superseded_by: deadbeef" in text
        assert "demoted: 2026-08-18" in text
        assert text.rstrip().endswith("Preferisce risposte brevi e concrete")

    def test_a_link_already_there_is_not_overwritten(self, tmp_path: Path):
        from dataclasses import replace

        archive_entry(tmp_path, replace(_ENTRY, superseded_by="aaaaaaaa"))
        path = archive_entry(tmp_path, replace(_ENTRY, superseded_by="bbbbbbbb"))
        text = path.read_text(encoding="utf-8")
        assert "superseded_by: aaaaaaaa" in text
        assert "bbbbbbbb" not in text

    def test_a_hand_edited_header_keeps_its_own_keys(self, tmp_path: Path):
        from dataclasses import replace

        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        path.write_text(
            path.read_text(encoding="utf-8").replace("---\n\n", "note: mine\n---\n\n", 1),
            encoding="utf-8",
        )
        archive_entry(tmp_path, replace(_ENTRY, retracted=True))
        text = path.read_text(encoding="utf-8")
        assert "note: mine" in text and "retracted: yes" in text

    def test_a_headerless_file_gets_a_header_with_the_link(self, tmp_path: Path):
        from dataclasses import replace

        from jenny.agent.memory_archive import read_archived

        directory = archive_dir(tmp_path)
        directory.mkdir(parents=True)
        path = directory / "2026-08-18-a1b2c3d4.md"
        path.write_text("Preferisce risposte brevi e concrete\n", encoding="utf-8")
        archive_entry(tmp_path, replace(_ENTRY, superseded_by="deadbeef"))
        back = read_archived(path)
        assert back is not None
        assert back.text == "Preferisce risposte brevi e concrete"
        assert back.superseded_by == "deadbeef"


class TestItRoundTripsWithTheEntryTool:
    """L'archivio e i file caldi devono parlare la stessa lingua, o rimettere a
    posto una voce diventa un lavoro di traduzione."""

    def test_an_entry_parsed_from_a_file_archives_by_its_own_id(self, tmp_path: Path):
        source = "# User Profile\n\n## Preferences\n\n- Preferisce risposte brevi\n"
        entry = parse_entries(source)[0]

        path = archive_entry(
            tmp_path,
            ArchivedEntry(
                id=entry.id, text=entry.text, source="USER.md", heading=entry.heading,
            ),
            when=date(2026, 8, 18),
        )

        assert path.name == f"2026-08-18-{entry.id}.md"
        assert find_archived(tmp_path, entry.id) == path

    def test_the_archived_body_can_go_back_as_an_entry(self, tmp_path: Path):
        """Il corpo, rimesso come bullet, ritrova lo stesso id: la degradazione è
        reversibile senza perdere l'identità della voce."""
        source = "## Preferences\n\n- Preferisce risposte brevi\n"
        entry = parse_entries(source)[0]
        path = archive_entry(
            tmp_path,
            ArchivedEntry(id=entry.id, text=entry.text, source="USER.md"),
            when=date(2026, 8, 18),
        )

        body = path.read_text(encoding="utf-8").split("---\n\n", 1)[1].strip()

        assert entry_id(f"- {body}") == entry.id


_HOT = """# User Profile

## Preferences

- Preferisce risposte brevi
- Non vuole report formali

## Work

- Lavora su un progetto open source
"""


class TestTheFileBoundaryArchiver:
    """La difesa che copre **anche** il review pass.

    ``memory remove`` degrada, ma il review pass non lo usa: pota con
    ``apply_patch`` e ``edit_file`` sui file interi, perché è ciò che gli serve
    per ristrutturare. Ed è proprio lui lo scrittore i cui errori sono definitivi
    — il 2026-08-18 un secondo passaggio consecutivo ha tolto cinque voci vere da
    ``USER.md``, recuperabili solo da uno snapshot. Quindi la rete sta al confine
    del file: qualunque cosa riscriva quei due file ci passa.
    """

    def _archiver(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_HOT, encoding="utf-8")
        return make_entry_archiver(tmp_path), tmp_path / "USER.md"

    def test_an_entry_dropped_by_a_whole_file_rewrite_is_saved(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)
        pruned = _HOT.replace("- Non vuole report formali\n", "")

        archiver(hot, pruned)

        archived = list(archive_dir(tmp_path / "memory").glob("*.md"))
        assert len(archived) == 1
        assert "Non vuole report formali" in archived[0].read_text(encoding="utf-8")

    def test_a_pass_that_drops_several_saves_them_all(self, tmp_path: Path):
        """Il caso misurato: cinque voci in un colpo solo."""
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, "# User Profile\n\n## Preferences\n\n- Preferisce risposte brevi\n")

        assert len(list(archive_dir(tmp_path / "memory").glob("*.md"))) == 2

    def test_surviving_entries_are_not_archived(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _HOT)

        assert not archive_dir(tmp_path / "memory").exists()

    def test_it_keeps_the_section_the_entry_lived_in(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _HOT.replace("- Lavora su un progetto open source\n", ""))

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "heading: Work" in text
        assert "source: USER.md" in text

    def test_a_reworded_entry_keeps_its_old_version(self, tmp_path: Path):
        """L'id è l'hash del contenuto, quindi riscrivere una voce la fa sparire
        come voce. Un po' di rumore in archivio in cambio del fatto che nessuna
        formulazione precedente sia irrecuperabile: il verso giusto dello scambio.
        """
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _HOT.replace("- Preferisce risposte brevi", "- Preferisce risposte brevissime"))

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "Preferisce risposte brevi" in text

    def test_files_that_are_not_memory_are_ignored(self, tmp_path: Path):
        archiver, _ = self._archiver(tmp_path)
        other = tmp_path / "skills" / "x" / "SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_text("## S\n\n- una voce\n", encoding="utf-8")

        archiver(other, "## S\n")

        assert not archive_dir(tmp_path / "memory").exists()

    def test_the_memory_file_is_covered_too(self, tmp_path: Path):
        hot = tmp_path / "memory" / "MEMORY.md"
        hot.parent.mkdir(parents=True)
        hot.write_text("## Project Context\n- Il progetto X è attivo\n", encoding="utf-8")
        archiver = make_entry_archiver(tmp_path)

        archiver(hot, "## Project Context\n")

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "source: memory/MEMORY.md" in text

    def test_a_file_that_does_not_exist_yet_archives_nothing(self, tmp_path: Path):
        archiver = make_entry_archiver(tmp_path)

        archiver(tmp_path / "USER.md", "## S\n\n- prima voce\n")

        assert not archive_dir(tmp_path / "memory").exists()

    def test_it_never_raises_and_so_never_blocks_a_write(self, tmp_path: Path, monkeypatch):
        """Qui la degradazione è una rete, non una condizione: un archivio che non
        si scrive non deve impedire una scrittura legittima. L'ordine forte —
        archivia, e solo allora togli — sta in ``memory remove``, dove la voce da
        salvare si conosce con certezza."""
        archiver, hot = self._archiver(tmp_path)
        monkeypatch.setattr(
            "jenny.agent.tools.memory_entries.archive_entry",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disco pieno")),
        )

        archiver(hot, "# User Profile\n")  # non deve sollevare


_PROSE = """# User Profile

Questo file raccoglie quello che so dell'utente.
Si aggiorna da solo, non si riscrive a mano.

## Preferences

- Preferisce risposte brevi
Odia le riunioni del venerdì.

## History

1. Ha cominciato con un Titan 2
2. Poi ci ha messo Jenny
"""


class TestItProtectsProseAndNotOnlyBullets:
    """La maglia larga: la rete guardava solo i bullet.

    Il prompt del review pass chiede *esplicitamente* di cancellare prosa —
    "l'introduzione che spiega a cosa serve il file" — e finché l'archiviazione
    passava solo da ``parse_entries`` quella cancellazione non lasciava copia da
    nessuna parte. Misurato su HEAD: di un paragrafo introduttivo, due bullet e
    una riga sciolta, una riscrittura del file intero archiviava il solo bullet.
    """

    def _archiver(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_PROSE, encoding="utf-8")
        return make_entry_archiver(tmp_path), tmp_path / "USER.md"

    def _bodies(self, tmp_path: Path) -> list[str]:
        return [
            p.read_text(encoding="utf-8")
            for p in archive_dir(tmp_path / "memory").glob("*.md")
        ]

    def test_a_prose_line_dropped_by_a_whole_file_rewrite_is_saved(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _PROSE.replace("Odia le riunioni del venerdì.\n", ""))

        assert any("Odia le riunioni del venerdì." in b for b in self._bodies(tmp_path))

    def test_a_numbered_item_is_saved_too(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _PROSE.replace("2. Poi ci ha messo Jenny\n", ""))

        assert any("Poi ci ha messo Jenny" in b for b in self._bodies(tmp_path))

    def test_a_heading_is_structure_and_is_not_archived(self, tmp_path: Path):
        """Riorganizzare le sezioni è il mestiere del review pass. Un
        ``## History`` in archivio sarebbe un "fatto" che non dice niente."""
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _PROSE.replace("## History\n", ""))

        assert self._bodies(tmp_path) == []

    def test_a_paragraph_stays_one_fragment(self, tmp_path: Path):
        """Cinque file per un paragrafo non sono un paragrafo recuperabile: sono
        cinque frasi orfane, e chi le rilegge non sa più in che ordine stavano."""
        archiver, hot = self._archiver(tmp_path)
        intro = (
            "Questo file raccoglie quello che so dell'utente.\n"
            "Si aggiorna da solo, non si riscrive a mano.\n"
        )

        archiver(hot, _PROSE.replace(intro, ""))

        bodies = self._bodies(tmp_path)
        assert len(bodies) == 1
        assert "raccoglie quello che so" in bodies[0]
        assert "non si riscrive a mano" in bodies[0]

    def test_prose_promoted_to_a_bullet_is_not_a_loss(self, tmp_path: Path):
        """Il testo è ancora nel file. Archiviarlo riempirebbe l'archivio di roba
        che non se n'è andata, cioè lo renderebbe rumore."""
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _PROSE.replace(
            "Odia le riunioni del venerdì.", "- Odia le riunioni del venerdì.",
        ))

        assert not archive_dir(tmp_path / "memory").exists()

    def test_prose_moved_to_another_section_is_not_a_loss(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)
        moved = _PROSE.replace("Odia le riunioni del venerdì.\n", "")
        moved = moved.replace("## History\n", "## History\nOdia le riunioni del venerdì.\n")

        archiver(hot, moved)

        assert not archive_dir(tmp_path / "memory").exists()

    def test_a_rule_is_not_content(self, tmp_path: Path):
        """``---``, recinzioni di codice, separatori di tabella: niente
        caratteri di parola, niente da perdere."""
        (tmp_path / "USER.md").write_text("## S\n\n---\n\n- una voce\n", encoding="utf-8")
        archiver = make_entry_archiver(tmp_path)

        archiver(tmp_path / "USER.md", "## S\n\n- una voce\n")

        assert not archive_dir(tmp_path / "memory").exists()

    def test_the_same_prose_removed_twice_stays_one_file(self, tmp_path: Path):
        """La deduplica per contenuto vale anche per i frammenti: l'archivio è un
        insieme di testi passati di qui, non un registro di quante volte."""
        archiver, hot = self._archiver(tmp_path)
        pruned = _PROSE.replace("Odia le riunioni del venerdì.\n", "")

        archiver(hot, pruned)
        hot.write_text(_PROSE, encoding="utf-8")
        archiver(hot, pruned)

        assert len(self._bodies(tmp_path)) == 1

    def test_a_fragment_does_not_come_back_as_a_fact(self, tmp_path: Path):
        """La forma con cui ``recall`` lo rende. Un paragrafo di prosa riaperto
        senza qualifica si leggerebbe come qualcosa che qualcuno ha affermato,
        mentre è il testo che stava *intorno* ai fatti."""
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _PROSE.replace("Odia le riunioni del venerdì.\n", ""))

        entry = next(
            e for e in list_archived(tmp_path / "memory") if "Odia" in e.text
        )
        assert "not an entry" in entry.heading
        assert "Preferences" in entry.heading  # la sezione resta l'indirizzo

    async def test_recall_shows_the_qualifier_when_it_opens_one(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)
        archiver(hot, _PROSE.replace("Odia le riunioni del venerdì.\n", ""))
        entry = next(
            e for e in list_archived(tmp_path / "memory") if "Odia" in e.text
        )

        out = await MemoryRecallTool(tmp_path).execute(ids=[entry.id])

        assert "not an entry" in out
        assert "Odia le riunioni del venerdì." in out

    def test_the_entry_path_is_untouched(self, tmp_path: Path):
        """Un bullet resta una voce: id dal contenuto, nessuna qualifica, e la
        sezione pulita. Il frammento è una strada in più, non una che sostituisce."""
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _PROSE.replace("- Preferisce risposte brevi\n", ""))

        entry = next(
            e for e in list_archived(tmp_path / "memory") if "Preferisce" in e.text
        )
        assert entry.heading == "Preferences"
        assert entry.id == entry_id("- Preferisce risposte brevi")


class TestTheModelIsToldTheArchiveExists:
    """Un indice che nessuno sa esistere non viene mai aperto.

    È la stessa ragione per cui l'elenco delle wiki sta nel prompt. Un archivio
    invisibile al modello è, dal suo punto di vista, indistinguibile da una
    cancellazione — e allora tanto varrebbe cancellare.
    """

    def test_an_empty_archive_costs_nothing(self, tmp_path: Path):
        """Un'installazione nuova non paga token per una cartella che non c'è."""
        assert MemoryStore(tmp_path).get_archive_context() == ""

    def test_it_appears_once_there_is_something_to_find(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        archive_entry(store.memory_dir, _ENTRY, when=date(2026, 8, 18))

        out = store.get_archive_context()

        assert out.startswith("## Archive")
        assert "memory/archive/" in out

    def test_it_is_flat_in_the_size_of_the_archive(self, tmp_path: Path):
        """Un abstract per voce spenderebbe il budget caldo che questa fase esiste
        per proteggere. Qui c'è un numero, e non cresce."""
        store = MemoryStore(tmp_path)
        for i in range(2):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"a{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )
        two = store.get_archive_context()
        for i in range(2, 40):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"a{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )
        forty = store.get_archive_context()

        assert len(two.splitlines()) == len(forty.splitlines()) == 2
        assert abs(len(forty) - len(two)) <= 2  # solo la cifra del conteggio

    def test_it_counts_what_is_in_there(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        for i in range(3):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"b{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )

        assert "(3 so far)" in store.get_archive_context()

    def test_it_says_the_directory_is_not_writable_by_the_model(self, tmp_path: Path):
        """Il percorso finisce anche nel prompt di Dream, la cui allowlist non lo
        comprende — e per lui una scrittura rifiutata non è un tentativo a vuoto
        ma un run intero che non commette niente."""
        store = MemoryStore(tmp_path)
        archive_entry(store.memory_dir, _ENTRY, when=date(2026, 8, 18))

        out = store.get_archive_context()

        assert "you never write to that directory" in out

    def test_it_says_how_to_look(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        archive_entry(store.memory_dir, _ENTRY, when=date(2026, 8, 18))

        assert "grep" in store.get_archive_context()



class TestTheArchiveGrowthIsObservable:
    """L'archivio non ha ritenzione: quello che manca è accorgersene.

    ``entry_id`` è un hash del contenuto, quindi ogni riformulazione dello stesso
    fatto deposita un file in più: la crescita segue il churn del review pass e
    non le rimozioni vere. A 41 voci è gratis, e il difetto è che niente lo dice
    quando smette di esserlo.

    La seconda metà di questa classe pinna la decisione **contro la cache** su
    ``get_archive_context``: contare le dirent costa ~44 µs a 41 voci contro un
    turno che spende secondi dal provider, e una cache stantia a 0 farebbe
    sparire il blocco — cioè un modello che non sa che l'archivio esiste, che è
    l'unico guasto che questa riga esiste per impedire.
    """

    @staticmethod
    def _fill(store: MemoryStore, count: int, *, start: int = 0) -> None:
        for i in range(start, start + count):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"{i:08x}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )

    def _capture(self, store: MemoryStore, caplog, calls: int = 1) -> list[str]:
        import logging

        from loguru import logger as loguru_logger

        handler_id = loguru_logger.add(caplog.handler, format="{message}", level="INFO")
        try:
            with caplog.at_level(logging.INFO):
                for _ in range(calls):
                    store.get_archive_context()
        finally:
            loguru_logger.remove(handler_id)
        return [r.getMessage() for r in caplog.records if "Memory archive holds" in r.getMessage()]

    def test_an_archive_under_the_threshold_stays_quiet(
        self, tmp_path: Path, caplog, monkeypatch
    ):
        """Un avviso che parte quando va tutto bene è un avviso che si ignora."""
        monkeypatch.setattr(memory_module, "_ARCHIVE_NOTABLE_ENTRIES", 3)
        store = MemoryStore(tmp_path)
        self._fill(store, 2)

        assert self._capture(store, caplog) == []

    def test_crossing_the_threshold_is_said_exactly_once(
        self, tmp_path: Path, caplog, monkeypatch
    ):
        """Il conteggio si passa a ogni system prompt: un avviso per turno è rumore.

        Stesso rate-limit per-istanza degli altri avvisi di ``MemoryStore``, e su
        un telefono il gateway riparte spesso — quindi "una volta per processo"
        resta una riga che si rivede.
        """
        monkeypatch.setattr(memory_module, "_ARCHIVE_NOTABLE_ENTRIES", 3)
        store = MemoryStore(tmp_path)
        self._fill(store, 3)

        lines = self._capture(store, caplog, calls=3)

        assert len(lines) == 1
        # Il numero vero e la soglia, non un "archivio grande": chi legge deve
        # poter decidere se è il momento della fase 7.2 senza aprire la cartella.
        assert "holds 3 entries" in lines[0]
        assert ">= 3" in lines[0]

    def test_the_count_is_rebuilt_on_every_call(self, tmp_path: Path):
        """Nessuna cache, e questa è la ragione — non una dimenticanza.

        Una cache stantia risponderebbe col conteggio di prima; a 0 farebbe
        sparire il blocco e il modello tornerebbe a trattare una degradazione
        come una cancellazione. Il risparmio sarebbe una ``scandir`` da decine di
        microsecondi.
        """
        store = MemoryStore(tmp_path)
        self._fill(store, 2)
        assert "(2 so far)" in store.get_archive_context()

        self._fill(store, 3, start=2)

        assert "(5 so far)" in store.get_archive_context()

    def test_an_archive_that_empties_out_makes_the_block_disappear_again(
        self, tmp_path: Path
    ):
        """L'altra direzione dello stesso invariante: il conteggio non è mai stantio."""
        store = MemoryStore(tmp_path)
        self._fill(store, 2)
        assert store.get_archive_context() != ""

        for path in archive_dir(store.memory_dir).glob("*.md"):
            path.unlink()

        assert store.get_archive_context() == ""


class TestTheArchiverIsWiredIntoTheRegistryDreamActuallyGets:
    """La rete esiste **e** è appesa: il registry vero, i tool veri, il disco vero.

    Le classi qui sopra chiamano ``make_entry_archiver`` a mano, e questo le
    lascia cieche sull'unica cosa che il 18/08 è andata storta: la giuntura.
    Misurato per mutazione il 23/08 — due sopravvissuti indipendenti, ciascuno
    con l'intera suite verde:

    * ``memory.py``: ``entry_archiver = make_entry_archiver(workspace)`` -> ``None``;
    * ``filesystem.py``: ``_archive_departing`` che esce sempre subito.

    Cioè: la degradazione si poteva staccare senza che niente lo dicesse. Da qui
    la forma di questi test — si passa da ``MemoryStore.build_dream_tools`` e si
    esegue il tool, perché è quello che fa il review pass. Ognuno dei tre
    scrittori ha il proprio test: il cablaggio è un ``entry_archiver=`` per
    ``register``, e uno solo che sparisse non si vedrebbe da un test sull'altro.
    """

    _HOT = (
        "# User Profile\n\n"
        "Odia le riunioni del venerdì.\n\n"
        "## Preferences\n\n"
        "- Preferisce risposte brevi\n"
        "- Non vuole report formali\n"
    )

    def _dream(self, tmp_path: Path):
        """USER.md e memory/MEMORY.md caldi, più il registry che Dream riceve."""
        store = MemoryStore(tmp_path)
        store.user_file.write_text(self._HOT, encoding="utf-8")
        store.memory_file.write_text(self._HOT.replace("User Profile", "Memory"), encoding="utf-8")
        return store, store.build_dream_tools()

    def _archived(self, tmp_path: Path) -> list[str]:
        return [
            p.read_text(encoding="utf-8")
            for p in archive_dir(tmp_path / "memory").glob("*.md")
        ]

    def _saved(self, tmp_path: Path, fact: str) -> bool:
        return any(fact in body for body in self._archived(tmp_path))

    async def test_apply_patch_pruning_user_md_leaves_the_entry_in_the_archive(
        self, tmp_path: Path
    ):
        """Lo scrittore del caso misurato: il review pass pota con ``apply_patch``."""
        store, tools = self._dream(tmp_path)

        await tools.get("apply_patch").execute(edits=[{
            "path": "USER.md",
            "action": "replace",
            "old_text": "- Non vuole report formali\n",
            "new_text": "",
        }])

        assert "Non vuole report formali" not in store.user_file.read_text(encoding="utf-8")
        assert self._saved(tmp_path, "Non vuole report formali")

    async def test_edit_file_pruning_user_md_leaves_the_entry_in_the_archive(
        self, tmp_path: Path
    ):
        store, tools = self._dream(tmp_path)

        await tools.get("edit_file").execute(
            path="USER.md", old_text="- Non vuole report formali\n", new_text="",
        )

        assert "Non vuole report formali" not in store.user_file.read_text(encoding="utf-8")
        assert self._saved(tmp_path, "Non vuole report formali")

    async def test_a_whole_file_write_of_user_md_saves_everything_it_drops(
        self, tmp_path: Path
    ):
        """``write_file`` è la terza strada, e la più distruttiva delle tre."""
        store, tools = self._dream(tmp_path)

        await tools.get("write_file").execute(
            path="USER.md",
            content="# User Profile\n\n## Preferences\n\n- Preferisce risposte brevi\n",
        )

        assert self._saved(tmp_path, "Non vuole report formali")
        assert self._saved(tmp_path, "Odia le riunioni del venerdì."), (
            "la prosa cancellata da una riscrittura intera non lascia copia"
        )

    async def test_the_memory_file_is_covered_by_the_same_wiring(self, tmp_path: Path):
        """L'altro file caldo a voci, e l'archivio dice da quale dei due viene."""
        store, tools = self._dream(tmp_path)

        await tools.get("apply_patch").execute(edits=[{
            "path": "memory/MEMORY.md",
            "action": "replace",
            "old_text": "- Non vuole report formali\n",
            "new_text": "",
        }])

        bodies = self._archived(tmp_path)
        assert any("Non vuole report formali" in b for b in bodies)
        assert any("source: memory/MEMORY.md" in b for b in bodies)

    async def test_a_prose_line_removed_through_the_registry_is_saved_too(
        self, tmp_path: Path
    ):
        """L'allargamento di T1.6 arriva fino ai tool, non si ferma all'helper."""
        _store, tools = self._dream(tmp_path)

        await tools.get("edit_file").execute(
            path="USER.md", old_text="Odia le riunioni del venerdì.\n", new_text="",
        )

        assert self._saved(tmp_path, "Odia le riunioni del venerdì.")

    async def test_only_the_entries_that_left_are_archived(self, tmp_path: Path):
        """Il verso opposto: senza questo, un archiver che salva *tutto* passerebbe.

        Una voce riscritta se ne va comunque nella sua versione vecchia (v.
        ``test_a_reworded_entry_keeps_its_old_version``); una che non è stata
        toccata no.
        """
        _store, tools = self._dream(tmp_path)

        await tools.get("edit_file").execute(
            path="USER.md",
            old_text="- Preferisce risposte brevi\n",
            new_text="- Preferisce risposte brevi e concrete\n",
        )

        assert self._saved(tmp_path, "Preferisce risposte brevi")
        assert not self._saved(tmp_path, "Non vuole report formali")
