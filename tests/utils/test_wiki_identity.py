"""L'identità di una wiki: chi la scrive, chi la legge, e cosa fa se è ambigua.

Passo **7.1** e **7.4** di ``roadmap/progetti-passi.md``, strada **B**.

L'id serve a **una** cosa: ritrovare la chat di una wiki dopo che la cartella ha
cambiato nome. Non è l'indirizzo di niente — quello resta il nome della cartella,
come deciso il 21/08 — e non finisce in nessun nome di file. È la ragione per cui
può essere opaco: se un domani diventasse l'indirizzo, i file di sessione si
chiamerebbero ``project_3f9a2c1b7e04.jsonl`` e ispezionarli con adb, che è come
abbiamo trovato metà dei difetti di questa settimana, diventerebbe una ricerca.

Il test che conta più degli altri è
``test_two_wikis_with_the_same_id_resolve_to_nothing``: ci si arriva **copiando
una cartella**, che è una cosa che si fa senza pensarci, e indovinare quale delle
due sia "quella giusta" metterebbe la storia di una chat sotto la wiki sbagliata.
La lezione del passo 6: si rifiuta invece di scegliere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils.wiki_migration import migrate_wikis
from jenny.utils.wiki_paths import (
    WIKI_ID_KEY,
    find_wiki_by_id,
    is_valid_wiki_id,
    new_wiki_id,
    wiki_id,
)


def _wiki(root: Path, name: str, schema: str | None = None, body: str = "") -> Path:
    project = root / name
    (project / "wiki").mkdir(parents=True)
    (project / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    if schema:
        (project / schema).write_text(body, encoding="utf-8")
    return project


@pytest.fixture
def wikis(tmp_path: Path) -> Path:
    d = tmp_path / "wikis"
    d.mkdir()
    return d


# ── La forma ─────────────────────────────────────────────────────────────


def test_a_new_id_is_valid_and_not_derived_from_anything() -> None:
    """Un id derivato dal nome cambierebbe insieme al nome, cioè non servirebbe."""
    ids = {new_wiki_id() for _ in range(50)}
    assert len(ids) == 50, "due id uguali in cinquanta è già una collisione"
    assert all(is_valid_wiki_id(i) for i in ids)


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "ABCDEF123456", "3f9a2c1b7e0", "3f9a2c1b7e045", None, 12, "3f9a2c1b7e0g"],
    ids=["vuoto", "corto", "maiuscolo", "11", "13", "none", "int", "non-hex"],
)
def test_anything_else_is_not_an_id(raw) -> None:
    """Stretto apposta: un valore mezzo giusto letto come id troverebbe la wiki sbagliata."""
    assert is_valid_wiki_id(raw) is False


# ── La lettura ───────────────────────────────────────────────────────────


def test_the_id_is_read_from_the_instructions_file(wikis: Path) -> None:
    project = _wiki(wikis, "patreon", "AGENTS.md", "---\nid: 3f9a2c1b7e04\n---\n\n# P\n")
    assert wiki_id(project) == "3f9a2c1b7e04"


def test_it_is_read_from_claude_md_too_while_that_exists(wikis: Path) -> None:
    """Una wiki non ancora migrata non deve perdere la propria identità."""
    project = _wiki(wikis, "vecchia", "CLAUDE.md", "---\nid: 3f9a2c1b7e04\n---\n\n# V\n")
    assert wiki_id(project) == "3f9a2c1b7e04"


@pytest.mark.parametrize(
    ("schema", "body"),
    [
        (None, ""),
        ("AGENTS.md", "# senza frontmatter\n"),
        ("AGENTS.md", "---\nsummary: x\n---\n\n# senza id\n"),
        ("AGENTS.md", "---\nid: non-un-id\n---\n\n# id storto\n"),
    ],
    ids=["nessun-file", "nessuna-frontmatter", "nessun-id", "id-storto"],
)
def test_a_missing_id_is_not_an_error(wikis: Path, schema: str | None, body: str) -> None:
    """Una wiki senza id funziona come prima del passo 7: solo, un rinomino la perde."""
    project = _wiki(wikis, "senza", schema, body)
    assert wiki_id(project) is None


def test_the_id_survives_a_frontmatter_that_yaml_cannot_parse(wikis: Path) -> None:
    """Un difetto vero, visto sul telefono il 22/08.

    La riga di scope è testo libero dell'utente e finisce *dentro* la
    frontmatter. Un due punti in mezzo — «Prova del passo 7: la chat segue» — la
    rende non parsabile, e ``yaml.safe_load`` non perde quella riga: perde
    **tutte** le altre. L'id serve a riparare una chat orfana, quindi doveva
    essere leggibile proprio nei file un po' storti — cioè quelli in cui serve.
    Da qui la regex invece del parser: la sua forma è fissa.
    """
    import yaml

    broken = "---\nid: 3f9a2c1b7e04\nsummary: Prova del passo 7: la chat segue\n---\n\n# P\n"
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken.split("---")[1])

    project = _wiki(wikis, "storta", "AGENTS.md", broken)
    assert wiki_id(project) == "3f9a2c1b7e04"


def test_a_seeded_scope_line_is_quoted_so_it_never_breaks_the_block(tmp_path: Path) -> None:
    """L'altra metà della correzione: chi *scrive* la riga la mette al sicuro.

    Leggere l'id con una regex salva l'id; non salva ``summary``, che è quello
    che ``read_wiki_scope`` cerca per comporre ``wikis/_index.md``. Quindi la
    riga si quota all'origine.
    """
    import yaml

    from jenny.webui.project_create import _yaml_scalar

    for seed in ("Prova del passo 7: la chat segue", 'con "virgolette"', "back\\slash", "# hash"):
        block = f"id: 3f9a2c1b7e04\nsummary: {_yaml_scalar(seed)}"
        assert yaml.safe_load(block)["summary"] == seed, f"seed non sopravvive: {seed!r}"


# I semi che rompono la frontmatter se qualcuno se ne dimentica uno. Il primo è
# **quello misurato** sul telefono il 22/08. L'emoji c'è perché la riga viaggia
# su RPC dalla WebUI, cioè può contenerne una, e perché è il caso in cui una
# codifica sbagliata si vede subito.
_SEEDS_THAT_BREAK_YAML = [
    "Prova del passo 7: la chat segue",
    'con "virgolette" dentro',
    "back\\slash",
    "# hash",
    "un progetto 🌍 che viaggia in RPC",
]


def _frontmatter(text: str) -> dict:
    """La frontmatter di *text*, parsata. Solleva se il blocco non è YAML valido."""
    import yaml

    parsed = yaml.safe_load(text.split("---", 2)[1])
    assert isinstance(parsed, dict), f"frontmatter non parsabile: {text!r}"
    return parsed


@pytest.mark.parametrize("seed", _SEEDS_THAT_BREAK_YAML)
def test_the_scope_line_that_create_project_writes_round_trips(
    tmp_path: Path, seed: str
) -> None:
    """La stessa proprietà, ma dal **chiamante** e non dall'helper.

    Il test qui sopra costruisce la riga YAML a mano, quindi misura
    ``_yaml_scalar`` e non lo scrittore: mutare il *call site* —
    ``project_create.create_project``, ``_yaml_scalar(seed)`` -> ``seed`` —
    passava tutta la suite (misurato il 23/08). Ed è quel call site il posto in
    cui il difetto del 22/08 è stato visto: un due punti nel seme dell'utente
    portava via l'intera frontmatter, quindi ``read_wiki_scope`` cadeva sul
    ripiego e l'id risultava assente.
    """
    from jenny.webui.project_create import create_project

    wikis = tmp_path / "wikis"
    wikis.mkdir()

    result = create_project(
        wikis_dir=wikis,
        # Nessuna skill nel workspace: ``reindex_wikis`` non c'è, e il registro
        # non aggiornato è per contratto un avviso, non un fallimento.
        scripts_dir=tmp_path / "senza-skill",
        name="prova",
        seed=seed,
    )

    assert result["seeded"] is True
    project = wikis / "prova"
    assert _frontmatter((project / "AGENTS.md").read_text(encoding="utf-8"))["summary"] == seed
    assert is_valid_wiki_id(wiki_id(project)), "una frontmatter rotta si porta via anche l'id"


@pytest.mark.parametrize("seed", _SEEDS_THAT_BREAK_YAML)
def test_the_scope_line_written_into_a_half_built_tree_round_trips_too(
    tmp_path: Path, seed: str
) -> None:
    """Il secondo call site, che è un ramo diverso e ha il proprio quoting.

    Su un albero rimasto a metà l'``AGENTS.md`` esiste già — l'ha scritto la
    migrazione dell'avvio, con ``summary:`` a segnaposto — quindi lo scaffolder
    lo lascia stare e la riga la scrive ``_seed_scope_if_placeholder``. È una
    seconda chiamata a ``_yaml_scalar``, e un test sul primo ramo la lascia
    scoperta.
    """
    from jenny.webui.project_create import create_project

    wikis = tmp_path / "wikis"
    (wikis / "morta-a-meta" / "wiki").mkdir(parents=True)
    migrate_wikis(wikis)  # come l'avvio: AGENTS.md minimo, summary a segnaposto
    project = wikis / "morta-a-meta"
    assert "<" in _frontmatter((project / "AGENTS.md").read_text(encoding="utf-8"))["summary"]

    result = create_project(
        wikis_dir=wikis,
        scripts_dir=tmp_path / "senza-skill",
        name="morta-a-meta",
        seed=seed,
    )

    assert result["seeded"] is True
    assert _frontmatter((project / "AGENTS.md").read_text(encoding="utf-8"))["summary"] == seed


# ── La ricerca, e l'ambiguità ────────────────────────────────────────────


def test_a_wiki_is_found_by_its_id(wikis: Path) -> None:
    _wiki(wikis, "altra", "AGENTS.md", "---\nid: aaaaaaaaaaaa\n---\n")
    target = _wiki(wikis, "cercata", "AGENTS.md", "---\nid: bbbbbbbbbbbb\n---\n")
    assert find_wiki_by_id(wikis, "bbbbbbbbbbbb") == target


def test_an_unknown_id_finds_nothing(wikis: Path) -> None:
    _wiki(wikis, "altra", "AGENTS.md", "---\nid: aaaaaaaaaaaa\n---\n")
    assert find_wiki_by_id(wikis, "cccccccccccc") is None


def test_two_wikis_with_the_same_id_resolve_to_nothing(wikis: Path) -> None:
    """Ci si arriva copiando una cartella, e indovinare è il guasto peggiore.

    Con due candidate, sceglierne una metterebbe la storia di una conversazione
    sotto la wiki sbagliata — irrecuperabile, perché nessuno se ne accorge. Una
    chat che resta orfana e lo dice è un guaio molto più piccolo.
    """
    _wiki(wikis, "originale", "AGENTS.md", "---\nid: dddddddddddd\n---\n")
    _wiki(wikis, "copia", "AGENTS.md", "---\nid: dddddddddddd\n---\n")
    assert find_wiki_by_id(wikis, "dddddddddddd") is None


def test_a_malformed_target_is_not_looked_up(wikis: Path) -> None:
    _wiki(wikis, "una", "AGENTS.md", "---\nid: aaaaaaaaaaaa\n---\n")
    assert find_wiki_by_id(wikis, "") is None
    assert find_wiki_by_id(wikis, "aaaa") is None


def test_a_folder_that_is_not_a_wiki_is_not_a_candidate(wikis: Path) -> None:
    """La definizione di wiki è una: contiene ``wiki/``. Vale anche qui."""
    stray = wikis / "non-una-wiki"
    stray.mkdir()
    (stray / "AGENTS.md").write_text("---\nid: eeeeeeeeeeee\n---\n", encoding="utf-8")
    assert find_wiki_by_id(wikis, "eeeeeeeeeeee") is None


# ── La migrazione (7.4) ──────────────────────────────────────────────────


def test_claude_md_becomes_agents_md_with_the_content_intact(wikis: Path) -> None:
    """Non è un ritiro di template: quel testo l'ha scritto l'utente."""
    body = "---\nsummary: la mia wiki\ntags: [a, b]\n---\n\n# Titolo\n\nroba mia\n"
    project = _wiki(wikis, "vecchia", "CLAUDE.md", body)

    migrate_wikis(wikis)

    assert not (project / "CLAUDE.md").exists()
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "roba mia" in text
    assert "summary: la mia wiki" in text
    assert "tags: [a, b]" in text, (
        "la frontmatter non si riserializza: `yaml.dump` riordinerebbe le chiavi, "
        "normalizzerebbe le virgolette e perderebbe i commenti di un file scritto a mano"
    )
    assert is_valid_wiki_id(wiki_id(project))


def test_a_wiki_with_both_files_is_left_alone(wikis: Path) -> None:
    """Sceglierne uno butterebbe l'altro. È lo stato che i lettori sanno leggere."""
    project = _wiki(wikis, "doppia", "AGENTS.md", "---\nsummary: nuovo\n---\n")
    (project / "CLAUDE.md").write_text("---\nsummary: vecchio\n---\n", encoding="utf-8")

    migrate_wikis(wikis)

    assert (project / "CLAUDE.md").read_text(encoding="utf-8").strip().endswith("---")
    assert "nuovo" in (project / "AGENTS.md").read_text(encoding="utf-8")


def test_a_wiki_with_no_instructions_file_gets_a_minimal_one(wikis: Path) -> None:
    """Minimo e non lo scaffold completo: `AGENTS.md` nasce quasi vuoto (21/08)."""
    project = _wiki(wikis, "adhd")

    migrate_wikis(wikis)

    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert is_valid_wiki_id(wiki_id(project))
    assert "# Adhd" in text
    assert "What this wiki covers" not in text, "lo scaffold pieno è mestiere di /init"
    # `summary` resta un segnaposto, non il nome della cartella: con il nome,
    # `wikis/_index.md` direbbe «adhd — adhd», che *sembra* una descrizione. La
    # voce di prima diceva «(no AGENTS.md)», cioè la verità.
    from jenny.utils.wiki_paths import read_wiki_scope

    assert read_wiki_scope(project) == "(no scope set)"


def test_a_file_without_frontmatter_gains_one_above_its_content(wikis: Path) -> None:
    project = _wiki(wikis, "nuda", "CLAUDE.md", "# Solo un titolo\n\ncontenuto\n")

    migrate_wikis(wikis)

    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("---\n" + WIKI_ID_KEY)
    assert "# Solo un titolo" in text and "contenuto" in text


def test_the_migration_is_idempotent(wikis: Path) -> None:
    """Gira a **ogni** avvio, come l'estrazione dei template: a regime deve costare zero."""
    _wiki(wikis, "a", "CLAUDE.md", "---\nsummary: x\n---\n")
    _wiki(wikis, "b")

    first = migrate_wikis(wikis)
    before = {p.name: p.read_text(encoding="utf-8") for p in wikis.rglob("AGENTS.md")}
    second = migrate_wikis(wikis)

    assert sorted(first["identified"]) == ["a", "b"]
    assert second == {"renamed": [], "identified": [], "journals": []}
    assert {p.name: p.read_text(encoding="utf-8") for p in wikis.rglob("AGENTS.md")} == before


def test_every_wiki_gets_its_journal(wikis: Path) -> None:
    """T1: il diario e' **universale**, non del formato nuovo.

    Una wiki di ricerca di mesi fa lo riceve come un progetto creato oggi,
    perche' ogni conversazione di progetto contiene fatti stabili e la politica
    che ci scrive dentro non guarda la forma delle cartelle.
    """
    _wiki(wikis, "vecchia", "CLAUDE.md", "---\nsummary: ricerca\n---\n")

    result = migrate_wikis(wikis)

    assert result["journals"] == ["vecchia"]
    journal = wikis / "vecchia" / "raw" / "journal"
    assert journal.is_dir()
    # **Solo la cartella.** La prima pagina la scrive la prima cattura: un file
    # creato qui sarebbe la pagina di un giorno in cui non e' stato detto niente.
    assert list(journal.iterdir()) == []


def test_the_journal_is_created_once(wikis: Path) -> None:
    """Il costo a regime resta zero, come per gli altri due punti."""
    _wiki(wikis, "a")
    migrate_wikis(wikis)

    assert migrate_wikis(wikis)["journals"] == []


# ── Chi scrive l'id lo rilegge come lo legge chi lo usa ──────────────────

_BROKEN = "---\nsummary: Prova del passo 7: la chat segue\n---\n\n# P\n"


def _id_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(f"{WIKI_ID_KEY}:")]


def test_four_boots_over_an_unparsable_frontmatter_write_one_id(wikis: Path) -> None:
    """Il difetto del 22/08, dal lato di **chi scrive**.

    ``_ensure_id`` chiedeva a ``yaml.safe_load`` se la wiki avesse già un id, e
    su una frontmatter con un due punti in una riga di scope quel parser non
    perde *quella* riga: perde **tutte** le chiavi. Quindi non vedeva l'id che
    c'era e ne scriveva un altro a ogni avvio, mentre ``wiki_id`` — che legge con
    una regex e prende il primo match — restituiva ogni volta un valore diverso.
    Esito: la chat della wiki diventava irrintracciabile a ogni riavvio, e il log
    diceva «1 identificate» per sempre. Quattro avvii, un solo id.
    """
    import yaml

    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(_BROKEN.split("---")[1])

    project = _wiki(wikis, "storta", "AGENTS.md", _BROKEN)

    seen = []
    identified = []
    for _ in range(4):
        identified.append(migrate_wikis(wikis)["identified"])
        seen.append(wiki_id(project))

    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert len(_id_lines(text)) == 1, f"un id per avvio invece di uno solo:\n{text}"
    assert len(set(seen)) == 1, f"l'identità della wiki cambia a ogni avvio: {seen}"
    assert is_valid_wiki_id(seen[0])
    assert identified == [["storta"], [], [], []], (
        "il log di avvio non deve dire «identificate» di una wiki che l'id ce l'ha già"
    )
    assert "summary: Prova del passo 7: la chat segue" in text


def test_a_hand_written_non_hex_id_gets_a_real_one_above_it(wikis: Path) -> None:
    """Scelta deliberata: la riga dell'utente **resta**, la nostra le va sopra.

    Le altre due strade erano peggiori. Riscrivere ``id: tesi-2024`` in loco
    cancella testo che l'utente ha scritto a mano, che è l'unica cosa che questa
    migrazione promette di non fare. Lasciar perdere la wiki (trattare *qualsiasi*
    riga ``id:`` come "ce l'ha") le toglie in silenzio e per sempre la sola cosa
    per cui l'id esiste: ritrovare la propria chat dopo un rinomino. Il lettore
    prende il primo match, cioè il nostro, quindi dal secondo avvio non si muove
    più niente.
    """
    project = _wiki(wikis, "tesi", "AGENTS.md", "---\nid: tesi-2024\nsummary: la mia tesi\n---\n")

    migrate_wikis(wikis)
    after_first = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert migrate_wikis(wikis)["identified"] == []
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == after_first

    assert "id: tesi-2024" in after_first, "la riga scritta a mano non si tocca"
    assert is_valid_wiki_id(wiki_id(project))
    assert _id_lines(after_first)[0] != "id: tesi-2024", "il nostro id va letto per primo"


def test_a_well_formed_frontmatter_with_an_id_is_not_touched(wikis: Path) -> None:
    body = "---\nid: 3f9a2c1b7e04\nsummary: x\ntags: [a, b]\n---\n\n# P\n\nroba mia\n"
    project = _wiki(wikis, "aposto", "AGENTS.md", body)

    assert migrate_wikis(wikis)["identified"] == []
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == body


def test_a_well_formed_frontmatter_without_an_id_gets_exactly_one(wikis: Path) -> None:
    project = _wiki(wikis, "senza", "AGENTS.md", "---\nsummary: x\n---\n\n# P\n")

    assert migrate_wikis(wikis)["identified"] == ["senza"]
    after_first = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert migrate_wikis(wikis)["identified"] == []

    assert (project / "AGENTS.md").read_text(encoding="utf-8") == after_first
    assert len(_id_lines(after_first)) == 1
    assert "summary: x" in after_first


def test_a_file_with_no_frontmatter_at_all_gets_one_id_and_keeps_its_body(wikis: Path) -> None:
    project = _wiki(wikis, "nuda", "AGENTS.md", "# Solo un titolo\n\ncontenuto\n")

    assert migrate_wikis(wikis)["identified"] == ["nuda"]
    after_first = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert migrate_wikis(wikis)["identified"] == []

    assert (project / "AGENTS.md").read_text(encoding="utf-8") == after_first
    assert len(_id_lines(after_first)) == 1
    assert after_first.startswith("---\n" + WIKI_ID_KEY)
    assert "# Solo un titolo" in after_first and "contenuto" in after_first


def test_a_broken_wiki_does_not_stop_the_others(wikis: Path) -> None:
    """Un avvio che muore su una cartella storta non migra nemmeno le altre."""
    _wiki(wikis, "sana")
    broken = _wiki(wikis, "rotta")
    (broken / "AGENTS.md").mkdir()  # una directory dove ci vuole un file

    result = migrate_wikis(wikis)

    assert "sana" in result["identified"]
    assert is_valid_wiki_id(wiki_id(wikis / "sana"))


def test_a_missing_wikis_dir_is_not_an_error(tmp_path: Path) -> None:
    assert migrate_wikis(tmp_path / "mai-esistita") == {
        "renamed": [], "identified": [], "journals": [],
    }
