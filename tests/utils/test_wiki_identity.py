"""L'identità di una wiki: chi la scrive, chi la legge, e cosa fa se è ambigua.

Passo **7.1** e **7.4** del piano dei progetti, strada **B**.

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

import json
from pathlib import Path

import pytest

from jenny.utils.wiki_migration import migrate_wikis
from jenny.utils.wiki_paths import (
    WIKI_ID_KEY,
    find_wiki_by_id,
    is_valid_wiki_id,
    new_wiki_id,
    settle_wiki_id_lines,
    wiki_id,
    wiki_id_in_text,
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
    project = _wiki(wikis, "palestra", "AGENTS.md", "---\nid: 3f9a2c1b7e04\n---\n\n# P\n")
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
    project = _wiki(wikis, "acquari")

    migrate_wikis(wikis)

    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert is_valid_wiki_id(wiki_id(project))
    assert "# Acquari" in text
    assert "What this wiki covers" not in text, "lo scaffold pieno è mestiere di /init"
    # `summary` resta un segnaposto, non il nome della cartella: con il nome,
    # `wikis/_index.md` direbbe «acquari — acquari», che *sembra* una descrizione. La
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


def test_a_hand_written_non_hex_id_becomes_previous_id_under_a_real_one(wikis: Path) -> None:
    """La riga dell'utente **resta**, col suo valore; cambia solo la chiave.

    Fino al 26/10 restava ``id: tesi-2024`` sotto il nostro: due ``id:`` nella
    stessa frontmatter, che a un modello sembrano un doppione da pulire — e su
    Piante (Titan) un aiutante ha pulito via quello giusto, chiudendo fuori la
    chat del quaderno. Riscrivere il valore cancellerebbe testo dell'utente;
    lasciar perdere la wiki le toglierebbe l'id. ``previous_id:`` tiene
    entrambe le promesse.
    """
    project = _wiki(wikis, "tesi", "AGENTS.md", "---\nid: tesi-2024\nsummary: la mia tesi\n---\n")

    migrate_wikis(wikis)
    after_first = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert migrate_wikis(wikis)["identified"] == []
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == after_first

    assert "previous_id: tesi-2024" in after_first.splitlines(), after_first
    assert len(_id_lines(after_first)) == 1, after_first
    assert is_valid_wiki_id(wiki_id(project))
    assert "summary: la mia tesi" in after_first


def test_a_wiki_left_with_two_ids_keeps_the_real_one_and_renames_the_other(
    wikis: Path,
) -> None:
    """Lo stato in cui la migrazione di prima ha lasciato Piante: si sistema da se'.

    Non e' un id scritto (``identified`` resta vuoto): l'id c'era e non cambia.
    """
    body = "---\nid: 097d8ba49406\nid: piante\nscope: x\n---\n\n# Piante\n"
    project = _wiki(wikis, "piante", "AGENTS.md", body)

    assert migrate_wikis(wikis)["identified"] == []
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text == "---\nid: 097d8ba49406\nprevious_id: piante\nscope: x\n---\n\n# Piante\n"
    migrate_wikis(wikis)
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == text


def test_a_valid_id_under_a_hand_written_one_is_found_not_replaced(wikis: Path) -> None:
    """``wiki_id`` legge il primo ``id:``: uno valido **sotto** uno storto non lo
    vedeva, e la migrazione ne coniava un terzo. Ora lo storto passa a
    ``previous_id:`` e quello valido torna a essere l'id."""
    body = "---\nid: piante\nid: 097d8ba49406\n---\n\n# Piante\n"
    project = _wiki(wikis, "piante", "AGENTS.md", body)
    assert wiki_id(project) is None

    assert migrate_wikis(wikis)["identified"] == []
    assert wiki_id(project) == "097d8ba49406"


# ── Un id perso si rimette, non si conia ─────────────────────────────────


def _chat_remembers(workspace: Path, name: str, ident: str) -> None:
    """La sessione di progetto come la lascia ``AgentLoop._remember_project_id``."""
    from jenny.session.project_traces import PROJECT_WIKI_ID_KEY, project_session_file

    path = project_session_file(workspace, f"project:{name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"_type": "metadata", "key": f"project:{name}",
            "metadata": {PROJECT_WIKI_ID_KEY: ident}}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")


def test_a_lost_id_comes_back_from_the_chat_that_remembers_it(tmp_path: Path) -> None:
    """Piante sul Titan, il 26/10: l'id tolto da un aiutante, e al riavvio la
    migrazione ne coniava uno nuovo — da li' ogni turno della chat rifiutato."""
    wikis = tmp_path / "wikis"
    project = _wiki(wikis, "piante", "AGENTS.md", "---\nid: piante\nscope: x\n---\n\n# P\n")
    _chat_remembers(tmp_path, "piante", "097d8ba49406")

    assert migrate_wikis(wikis, tmp_path)["identified"] == ["piante"]
    assert wiki_id(project) == "097d8ba49406"
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert _id_lines(text) == ["id: 097d8ba49406"]
    assert "previous_id: piante" in text


def test_a_lost_instructions_file_comes_back_with_the_remembered_id(tmp_path: Path) -> None:
    wikis = tmp_path / "wikis"
    project = _wiki(wikis, "piante")
    _chat_remembers(tmp_path, "piante", "097d8ba49406")

    migrate_wikis(wikis, tmp_path)
    assert wiki_id(project) == "097d8ba49406"


def test_an_id_another_folder_declares_is_not_taken(tmp_path: Path) -> None:
    """L'id e' altrove: la chat e' di quella cartella (rinominata o copiata), e
    questa e' un'altra. Si conia, e il rifiuto del turno dira' cos'e' successo."""
    wikis = tmp_path / "wikis"
    _wiki(wikis, "piante-vecchie", "AGENTS.md", "---\nid: 097d8ba49406\n---\n")
    project = _wiki(wikis, "piante", "AGENTS.md", "---\nscope: x\n---\n")
    _chat_remembers(tmp_path, "piante", "097d8ba49406")

    migrate_wikis(wikis, tmp_path)
    assert is_valid_wiki_id(wiki_id(project))
    assert wiki_id(project) != "097d8ba49406"


def test_without_a_chat_or_a_workspace_a_new_id_is_minted(tmp_path: Path) -> None:
    wikis = tmp_path / "wikis"
    alone = _wiki(wikis, "sola", "AGENTS.md", "---\nscope: x\n---\n")
    blind = _wiki(wikis, "cieca", "AGENTS.md", "---\nscope: x\n---\n")
    _chat_remembers(tmp_path, "cieca", "097d8ba49406")

    migrate_wikis(wikis)  # nessun workspace: non si sa dove guardare
    assert is_valid_wiki_id(wiki_id(alone))
    assert wiki_id(blind) != "097d8ba49406"


# ── settle_wiki_id_lines, da sola ───────────────────────────────────────


@pytest.mark.parametrize("text", [
    "---\nid: 3f9a2c1b7e04\nsummary: x\n---\n\n# P\n",
    "# niente frontmatter\n",
    "---\nsummary: x\n---\n",
])
def test_a_settled_text_comes_back_identical(text: str) -> None:
    assert settle_wiki_id_lines(text) == text


def test_the_wanted_id_goes_first_and_any_other_moves_aside() -> None:
    text = "---\nsummary: x\nid: \"bbbbbbbbbbbb\"\n---\n\nid: nel corpo\n"
    out = settle_wiki_id_lines(text, "aaaaaaaaaaaa")
    assert out == (
        "---\nid: aaaaaaaaaaaa\nsummary: x\nprevious_id: \"bbbbbbbbbbbb\"\n---\n\n"
        "id: nel corpo\n"
    ), "il corpo non e' frontmatter: non si tocca"


def test_a_text_without_frontmatter_gets_one() -> None:
    out = settle_wiki_id_lines("# P\n", "aaaaaaaaaaaa")
    assert out == "---\nid: aaaaaaaaaaaa\n---\n\n# P\n"
    assert wiki_id_in_text(out) == "aaaaaaaaaaaa"


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
