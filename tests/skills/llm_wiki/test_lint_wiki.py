"""Test di regressione per lo script `lint_wiki.py` della skill llm-wiki.

Copre le correzioni introdotte dopo aver osservato due run reali della skill in
cui il linter (a) non capiva le liste YAML a blocco in frontmatter e (b) non
verificava alcune precondizioni del "Definition of done":

- `parse_frontmatter`: sintassi lista inline (`[a, b]`) E a blocco (`- a`).
- Pass 9: `sources:` non-vuoto su concept/entity + risoluzione in `raw/`.
- Pass 10: cross-link oltre `index.md`.
- Pass 11: ogni fonte in `raw/{articles,papers,notes}` ha un summary.

Gli script della skill non fanno parte del package `jenny` importabile, quindi
la dir `scripts/` viene aggiunta a `sys.path`.
"""

from __future__ import annotations

import importlib.util
import re
import unicodedata
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "jenny"
    / "skills"
    / "llm-wiki"
    / "scripts"
)


@pytest.fixture(scope="module")
def lint_wiki():
    """Carica `lint_wiki.py` come modulo (la dir non è un package importabile).

    Senza toccare ``sys.path``: ``spec_from_file_location`` non lo consulta, e
    per i moduli fratelli ci pensa lo script stesso — ``lint_wiki.py`` si inserisce
    la propria directory in testa prima di importarli. L'``insert`` che stava
    qui non serviva a nulla e restava in piedi per tutta la sessione.
    """
    spec = importlib.util.spec_from_file_location(
        "lint_wiki", _SCRIPTS_DIR / "lint_wiki.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── parse_frontmatter ────────────────────────────────────────────────────────


def test_parse_frontmatter_inline_list(lint_wiki):
    fm = lint_wiki.parse_frontmatter("---\ntitle: X\nsources: [a, b]\ntags: [t1]\n---\n")
    assert fm["sources"] == ["a", "b"]
    assert fm["tags"] == ["t1"]


def test_parse_frontmatter_block_list(lint_wiki):
    """La regressione principale: stile YAML a blocco per `sources:`."""
    text = (
        "---\n"
        'title: "Pagina"\n'
        "sources:\n"
        "  - raw/articles/x.md\n"
        "  - raw/articles/y.md\n"
        "tags: [t1]\n"
        "---\n"
    )
    fm = lint_wiki.parse_frontmatter(text)
    assert fm["sources"] == ["raw/articles/x.md", "raw/articles/y.md"]
    # la chiave dopo la lista a blocco deve continuare a essere letta
    assert fm["tags"] == ["t1"]


def test_parse_frontmatter_empty_key_stays_scalar(lint_wiki):
    """Una chiave vuota senza item a blocco resta stringa vuota (comportamento invariato)."""
    fm = lint_wiki.parse_frontmatter("---\ntitle: X\nsources:\ntags: [t1]\n---\n")
    assert fm["sources"] == ""
    assert fm["tags"] == ["t1"]


# ── helper per costruire una wiki minima ─────────────────────────────────────


def _make_wiki(root: Path, *, sources_style: str = "block", with_summary: bool = True,
               cross_link: bool = True) -> None:
    """Crea una wiki minima e ben formata sotto `root`.

    Parametrizzabile per innescare i singoli check negativi.
    """
    (root / "raw" / "articles").mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "wiki" / "summaries").mkdir(parents=True)
    (root / "log").mkdir()

    (root / "raw" / "articles" / "fonte.md").write_text("# Fonte\ntesto", encoding="utf-8")

    if sources_style == "block":
        src_fm = "sources:\n  - raw/articles/fonte.md\n"
    elif sources_style == "inline":
        src_fm = "sources: [raw/articles/fonte.md]\n"
    else:  # "missing"
        src_fm = ""

    concept_link = "\n\n## Correlati\n- [[entities/entita|Entità]]\n" if cross_link else ""
    entity_link = "\n\n[[concepts/concetto|Concetto]]" if cross_link else ""
    (root / "wiki" / "concepts" / "concetto.md").write_text(
        f"---\ntitle: Concetto\ntype: concept\n{src_fm}tags: [t]\n---\n\n# Concetto{concept_link}",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "entita.md").write_text(
        f"---\ntitle: Entità\ntype: entity\n{src_fm}tags: [t]\n---\n\n# Entità{entity_link}",
        encoding="utf-8",
    )

    if with_summary:
        (root / "wiki" / "summaries" / "fonte.md").write_text(
            "---\ntitle: summaries/fonte\ntype: summary\nsource_url: http://x\n---\n\n# Fonte",
            encoding="utf-8",
        )

    # index.md elenca tutte le pagine (usando gli slug con path relativo)
    (root / "wiki" / "index.md").write_text(
        "# Index\n\n## Concepts\n- [[concepts/concetto|Concetto]]\n\n"
        "## Entities\n- [[entities/entita|Entità]]\n\n"
        "## Summaries\n- [[summaries/fonte|Fonte]]\n",
        encoding="utf-8",
    )
    (root / "log" / "20260101.md").write_text(
        "# 2026-01-01\n\n## [10:00] ingest | fonte — prova (touched 2 pages)\n",
        encoding="utf-8",
    )


# ── lint() end-to-end ────────────────────────────────────────────────────────


def test_lint_clean_wiki_block_sources(lint_wiki, tmp_path, capsys):
    """Una wiki ben formata con `sources:` a blocco non deve produrre falsi positivi."""
    _make_wiki(tmp_path, sources_style="block")
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "no issues found" in out


def test_lint_clean_wiki_inline_sources(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, sources_style="inline")
    rc = lint_wiki.lint(str(tmp_path))
    assert rc == 0, capsys.readouterr().out


def test_lint_flags_missing_sources(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, sources_style="missing")
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "non-empty `sources:` frontmatter" in out


def test_lint_flags_missing_summary(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, with_summary=False)
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "without a wiki/summaries/ page" in out


def test_lint_flags_isolated_pages(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, cross_link=False)
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "not cross-linked beyond index.md" in out

# ── T5: le due strutture, e il diario che vale per entrambe ──────────────────
#
# Passo **T5** di ``roadmap/taccuino-passi.md``. Due layout esistono nel mondo e
# **nessun flag li distingue**: la struttura su disco è la dichiarazione, e il
# lint la legge come tutti gli altri consumatori.
#
# Il gruppo che conta è quello sull'append-only del diario, e la ragione è che
# quel controllo è l'unico che guarda **il passato**: il diario è la sola
# registrazione di quel che è stato detto, e una riga già promossa che cambia
# lascia in giro una pagina che non poggia più su niente — con il file intatto e
# il cursore del giardiniere perfettamente plausibile.


_JOURNAL_DAY = "raw/journal/20260823.md"


def _notebook(
    root: Path,
    *,
    state: str | None = "open",
    links: bool = True,
    source: str | None = _JOURNAL_DAY,
) -> Path:
    """Una wiki nel formato taccuino: pagine piatte, nessuna tassonomia.

    Le pagine dichiarano ``source:`` come il giardiniere le scrive (T6.7): è il
    default perché una fixture "sana" deve restare sana quando si aggiunge un
    controllo, altrimenti ogni test che asserisce l'assenza di *un* difetto
    misura anche tutti gli altri.
    """
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(
        "---\ntitle: Orto\n---\n\n# Orto\n\n## Pages\n\n- [[semine]]\n- [[terreno]]\n",
        encoding="utf-8",
    )
    src = f"source: {source}\n" if source else ""
    head = f"state: {state}\n" if state is not None else "title: Semine\n"
    body = "\n# Semine\n\nPomodori a fine aprile." + (" Vedi [[terreno]]." if links else "")
    (root / "wiki" / "semine.md").write_text(
        f"---\n{head}{src}---\n{body}\n", encoding="utf-8"
    )
    # ``open`` e non ``decided``, e non e' indifferente: dal passo 19 uno stato che
    # rivendica una decisione chiede di **chi** siano le parole, e la ``source:``
    # di default e' un giorno nudo — cioe' questa pagina diventerebbe un reperto
    # giallo dentro ogni test che asserisce l'assenza di un altro difetto. Il
    # principio e' quello scritto qui sopra; lo stato di questa pagina non e'
    # il soggetto di nessun test (quelli sullo stato usano ``semine.md``, che il
    # parametro ``state`` pilota).
    (root / "wiki" / "terreno.md").write_text(
        f"---\nstate: open\n{src}---\n\n# Terreno\n\nArgilloso. Vedi [[semine]].\n",
        encoding="utf-8",
    )
    return root


def _library(root: Path) -> Path:
    """Una wiki di ricerca: le cartelle del pattern document-first."""
    for sub in ("concepts", "entities", "summaries"):
        (root / "wiki" / sub).mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Ricerca\n\n- [[Ada]]\n", encoding="utf-8")
    (root / "wiki" / "entities" / "Ada.md").write_text(
        "---\nsources: [nota]\n---\n\n# Ada\n\nVedi [[index]].\n", encoding="utf-8"
    )
    (root / "raw" / "nota.md").write_text("grezzo\n", encoding="utf-8")
    return root


def _journal(root: Path, *entries: str, day: str = "20260823") -> Path:
    page = root / "raw" / "journal" / f"{day}.md"
    page.write_text(
        "# 2026-08-23\n\n" + "".join(f"- 09:0{i} \u2014 {e}\n" for i, e in enumerate(entries)),
        encoding="utf-8",
    )
    return page


def _run(lint_wiki, root: Path, capsys) -> str:
    lint_wiki.lint(str(root))
    return capsys.readouterr().out


# ── Il discriminante ─────────────────────────────────────────────────────────


def test_the_layout_is_read_from_the_folder_not_from_a_flag(lint_wiki, tmp_path):
    assert lint_wiki.is_research_layout(_library(tmp_path / "b") / "wiki") is True
    assert lint_wiki.is_research_layout(_notebook(tmp_path / "t") / "wiki") is False


def test_a_library_does_not_get_the_notebook_checks(lint_wiki, tmp_path, capsys):
    """**Le vecchie wiki non diventano cittadini di serie B.** Nessuna di loro ha
    ``state:``, e chiederglielo trasformerebbe otto wiki sane in otto wiki piene
    di errori — che è il modo più rapido di far ignorare un lint."""
    root = _library(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "state:" not in out
    assert "no link in or out" not in out


def test_a_library_still_gets_the_journal_check(lint_wiki, tmp_path, capsys):
    """Il diario è universale — ogni wiki l'ha guadagnato e la cattura ci scrive
    indipendentemente dal layout — quindi il suo controllo non è per formato."""
    root = _library(tmp_path)
    _journal(root, "un fatto")

    assert "Journal baseline recorded" in _run(lint_wiki, root, capsys)


# ── Il formato taccuino ──────────────────────────────────────────────────────


def test_a_healthy_notebook_is_quiet(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Every page declares a valid state" in out
    assert "no valid `state:`" not in out


def test_a_page_without_a_state_is_an_error(lint_wiki, tmp_path, capsys):
    """Una pagina vale quanto il suo stato dice: senza, un'ipotesi appuntata di
    passaggio si rilegge fra un mese come un fatto stabilito."""
    root = _notebook(tmp_path, state=None)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "no valid `state:`" in out and "semine.md" in out


def test_a_state_outside_the_vocabulary_is_an_error(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path, state="quasi-deciso")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "quasi-deciso" in out


def test_a_page_nobody_links_is_flagged(lint_wiki, tmp_path, capsys):
    """Essere elencata nella mappa non è un link: è quel che rende questa cosa
    una wiki invece di una cartella (R5, misurato)."""
    root = _notebook(tmp_path)
    (root / "wiki" / "isolata.md").write_text(
        "---\nstate: open\n---\n\n# Isolata\n\nNiente link.\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    assert "no link in or out" in _run(lint_wiki, root, capsys)


def test_a_dead_link_in_the_map_is_already_caught(lint_wiki, tmp_path, capsys):
    """Non un controllo nuovo: il passo 1 lo copriva già, e T3 ha misurato quanto
    costa — **quattro `list_dir` a ogni domanda a freddo**, perché il prompt dice
    di leggere le pagine che la mappa indica. Il test c'è perché quel costo ora è
    noto e nessuno deve poter allentare il controllo per sbaglio."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[fantasma]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" in out and "fantasma" in out


# ── La mappa entra in ogni turno ─────────────────────────────────────────────


def test_a_map_over_the_ceiling_is_flagged(lint_wiki, tmp_path, capsys):
    """Il tetto non è scelto dal lint: è la soglia oltre la quale il blocco di
    progetto smette di iniettare la mappa intera (``_PROJECT_MAP_MAX_CHARS``).
    Oltre, il resto della mappa esiste e l'agente non lo vede."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n" + ("x" * 2100), encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "The map is" in out and f"over {lint_wiki.MAP_MAX_CHARS}" in out


def test_the_ceiling_matches_the_one_the_prompt_uses(lint_wiki):
    """Due numeri che devono restare uguali vivono in due file: qui il confronto,
    così il giorno che uno cambia il test lo dice invece di lasciare un lint che
    avvisa alla soglia sbagliata."""
    source = (
        Path(__file__).resolve().parents[3] / "jenny" / "agent" / "context.py"
    ).read_text(encoding="utf-8")

    assert f"_PROJECT_MAP_MAX_CHARS = {lint_wiki.MAP_MAX_CHARS}" in source


# ── E le pagine pure, con una differenza ─────────────────────────────────────
#
# Passo **T3.3**. La mappa oltre il tetto entra troncata; una pagina oltre il
# tetto non entra affatto — nessuna pagina entra a metà, quindi viene saltata
# intera a ogni turno, e la selezione è alfabetica: non c'è domanda dell'utente
# che possa richiamarla. Sulle otto wiki vere sono 23 pagine su 188.


def _page_of(root: Path, chars: int, name: str = "lunga.md") -> None:
    """Una pagina piatta, ben formata, il cui **testo strippato** è ``chars``."""
    head = "---\nstate: open\n---\n\n# Lunga\n\nVedi [[semine]]. "
    (root / "wiki" / name).write_text(head + "x" * (chars - len(head)), encoding="utf-8")


def test_a_page_over_the_ceiling_is_flagged(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _page_of(root, lint_wiki.PAGE_MAX_CHARS + 1)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "too long to be injected" in out
    assert f"wiki/lunga.md — {lint_wiki.PAGE_MAX_CHARS + 1} characters" in out


def test_a_page_exactly_at_the_ceiling_is_not_flagged(lint_wiki, tmp_path, capsys):
    """Il confine è ``>``, lo stesso operatore della mappa: le due regole sono una
    famiglia e devono leggersi allo stesso modo. Sul corpus vero la scelta non
    sposta nulla — nessuna delle 188 pagine misura esattamente il tetto."""
    root = _notebook(tmp_path)
    _page_of(root, lint_wiki.PAGE_MAX_CHARS)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "too long to be injected" not in out


def test_a_page_under_the_ceiling_is_not_flagged(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _page_of(root, lint_wiki.PAGE_MAX_CHARS - 1)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "too long to be injected" not in out


def test_the_count_is_the_one_the_injector_makes(lint_wiki, tmp_path, capsys):
    """``strip()`` come l'iniettore. Una pagina al tetto più una coda di righe
    vuote non è una pagina fuori tetto, e un lint che contasse il grezzo
    discuterebbe di un numero che il prompt non usa."""
    root = _notebook(tmp_path)
    _page_of(root, lint_wiki.PAGE_MAX_CHARS)
    (root / "wiki" / "lunga.md").write_text(
        (root / "wiki" / "lunga.md").read_text(encoding="utf-8") + "\n\n\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "too long to be injected" not in out


def test_a_summary_is_not_a_page_for_this_rule(lint_wiki, tmp_path, capsys):
    """L'insieme è quello che l'iniettore guarda (``iter_wiki_pages``), e
    ``summaries/`` non ne fa parte: un summary lungo non entra nel blocco perché
    non entra affatto, quindi segnalarlo sarebbe un lavoro inventato."""
    root = _library(tmp_path)
    (root / "wiki" / "summaries" / "lungo.md").write_text(
        "# Lungo\n\n" + "x" * (lint_wiki.PAGE_MAX_CHARS + 500), encoding="utf-8"
    )

    out = _run(lint_wiki, root, capsys)

    assert "too long to be injected" not in out


def test_an_over_long_map_is_reported_as_a_map_not_as_a_page(lint_wiki, tmp_path, capsys):
    """``index.md`` ha già il suo passo, e ha un dopo-soglia diverso: entra
    troncata. Contarla due volte direbbe «due problemi» per uno."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n" + "x" * (lint_wiki.PAGE_MAX_CHARS + 100),
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "The map is" in out
    assert "too long to be injected" not in out


def test_the_page_ceiling_matches_the_budget_the_prompt_has(lint_wiki):
    """Come per la mappa: due numeri che devono restare uguali vivono in due
    file, e il confronto sta qui perché un lint che avvisa alla soglia sbagliata
    è un lint che manda a spezzare pagine che entravano."""
    source = (
        Path(__file__).resolve().parents[3] / "jenny" / "agent" / "context.py"
    ).read_text(encoding="utf-8")

    assert f"_PROJECT_PAGES_MAX_CHARS = {lint_wiki.PAGE_MAX_CHARS}" in source


# ── Il diario ────────────────────────────────────────────────────────────────


def test_a_malformed_journal_line_is_flagged(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    page = _journal(root, "un fatto")
    page.write_text(page.read_text(encoding="utf-8") + "riga senza forma\n", encoding="utf-8")

    out = _run(lint_wiki, root, capsys)

    assert "not in `- HH:MM" in out and "riga senza forma" in out


def test_a_journal_filename_that_is_not_a_day_is_flagged(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    (root / "raw" / "journal" / "appunti.md").write_text("# x\n", encoding="utf-8")

    assert "unexpected name" in _run(lint_wiki, root, capsys)


def test_the_first_run_records_a_baseline_instead_of_claiming_anything(
    lint_wiki, tmp_path, capsys
):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    assert "baseline recorded" in _run(lint_wiki, root, capsys)
    assert (root / ".jenny" / "lint_journal.json").is_file()


def test_the_state_lives_in_the_hidden_folder(lint_wiki, tmp_path, capsys):
    """Macchinario, non materiale dell'utente: come il cursore del giardiniere, e
    per la stessa ragione — sotto ``.jenny/`` viste e grafo non
    lo vedono."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    _run(lint_wiki, root, capsys)

    assert not list((root / "wiki").glob("*.json"))
    assert (root / ".jenny" / "lint_journal.json").is_file()


def test_appending_a_line_is_not_a_violation(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)

    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 \u2014 secondo\n")

    assert "append-only since the last lint" in _run(lint_wiki, root, capsys)


def test_changing_a_line_already_written_is_a_violation(lint_wiki, tmp_path, capsys):
    """Il caso che un digest sul file intero **non** distingue da una crescita, e
    che un controllo sulla sola dimensione manca del tutto: la riga cambia e il
    file resta della stessa lunghezza."""
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)

    page.write_text(
        page.read_text(encoding="utf-8").replace("primo", "PRIMO"), encoding="utf-8"
    )
    out = _run(lint_wiki, root, capsys)

    assert "no longer append-only" in out
    assert "already-written line was changed" in out


def test_truncating_the_journal_is_a_violation(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    page = _journal(root, "primo", "secondo")
    _run(lint_wiki, root, capsys)

    page.write_text("# 2026-08-23\n", encoding="utf-8")
    out = _run(lint_wiki, root, capsys)

    assert "no longer append-only" in out and "truncated" in out


def test_a_wiki_without_a_journal_says_nothing_about_one(lint_wiki, tmp_path, capsys):
    """Una wiki più vecchia della migrazione può non avere il diario: silenzio,
    non un errore."""
    root = _notebook(tmp_path)

    out = _run(lint_wiki, root, capsys)

    assert "Journal" not in out
    assert not (root / ".jenny").exists()

def test_a_link_the_app_resolves_is_not_reported_dead(lint_wiki, tmp_path, capsys):
    """**Misurato sul telefono il 23/08.** Chi risolve i link davvero
    (``webui/wiki.py::resolve_wikilink``) prova esatto, poi ``.md``, poi
    **case-insensitive**, poi lo stem. Il lint confrontava con `==` sensibile
    alle maiuscole, quindi segnalava come morto un link che l'app apre.

    Non è un caso limite: il giardiniere scrive `[[Rondine]]` per una pagina
    `rondine.md` — i nomi propri li scrive maiuscoli, come una persona — e la
    prima mappa che ha prodotto sul telefono conteneva esattamente questo. Un
    lint che grida al lupo su un link sano è un lint che si impara a ignorare.
    """
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[Semine]]\n- [[TERRENO]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" not in out
    assert "No dead wikilinks" in out


def test_a_genuinely_missing_page_is_still_reported(lint_wiki, tmp_path, capsys):
    """Il controllo di tenuta del test sopra: tollerare le maiuscole non deve
    diventare tollerare tutto."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[fantasma]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" in out and "fantasma" in out


# ── Una sola risoluzione del wikilink, usata da tutti i passi ────────────────
#
# Il passo 3 ("Pages missing from index.md") confrontava sottostringhe:
# `f"[[{p.stem}]]" not in index_text`. Sbagliava in entrambi i versi — una
# mappa scritta `- [[Semine]]` dichiarava `semine.md` fuori indice, e una mappa
# senza un solo link la dichiarava indicizzata perché il nome compariva nella
# prosa. Ora i link della mappa si risolvono con gli stessi due helper del
# passo 1, e `page_for_link` normalizza come l'app (slug + link a cartella).


def _page(root: Path, rel: str, body: str, *, state: str = "open") -> None:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nstate: {state}\n---\n\n{body}\n", encoding="utf-8")


def test_the_map_is_read_by_resolving_its_links_not_by_substring(lint_wiki, tmp_path, capsys):
    """(a) `- [[Semine]]` indicizza `semine.md`: è il link che l'app segue."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n## Pages\n\n- [[Semine]]\n- [[TERRENO]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "missing from index.md" not in out
    assert "All pages in index.md" in out


def test_the_page_name_in_the_prose_is_not_an_index_entry(lint_wiki, tmp_path, capsys):
    """(b) Il verso opposto, ed è il più grave: una mappa senza un solo link
    passava il controllo perché le parole c'erano nel testo."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\nQuest'anno parliamo di semine e di terreno, senza altro.\n",
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Pages missing from index.md (2)" in out
    assert "wiki/semine.md" in out and "wiki/terreno.md" in out


def test_an_accented_link_resolves_to_its_slug(lint_wiki, tmp_path, capsys):
    """(c) `[[Città]]` apre `citta.md`: l'app normalizza il bersaglio con
    `_slugify` (NFKD → ASCII) prima di confrontarlo con i file su disco."""
    root = _notebook(tmp_path)
    _page(root, "citta.md", "# Città\n\nVedi [[semine]].")
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[Città]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "No dead wikilinks" in out
    assert "missing from index.md" not in out


def test_the_same_name_in_two_unicode_forms_is_one_page(lint_wiki, tmp_path, capsys):
    """(d) Nome del file in NFD, link scritto in NFC: due sequenze di codepoint
    diverse per lo stesso nome. Lo slug passa da NFKD → ASCII, quindi le due
    forme collassano sulla stessa chiave."""
    root = _notebook(tmp_path)
    nfd = unicodedata.normalize("NFD", "città")
    _page(root, f"{nfd}.md", "# Città\n\nVedi [[semine]].")
    assert [p.stem for p in (root / "wiki").glob("citt*.md")] == [nfd]  # il fs l'ha tenuto NFD
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- "
        + f"[[{unicodedata.normalize('NFC', 'Città')}]]\n",
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "No dead wikilinks" in out
    assert "missing from index.md" not in out


def test_a_folder_split_concept_resolves_to_its_index(lint_wiki, tmp_path, capsys):
    """(e) `[[concepts/transformers]]` apre `concepts/transformers/index.md`.
    La forma esplicita `[[concepts/transformers/index]]` risolveva già; questa,
    l'abbreviazione che l'app apre uguale, era un link morto per concetto su
    ogni wiki con pagine divise in sottopagine."""
    root = _notebook(tmp_path)
    _page(root, "concepts/transformers/index.md", "# Transformers\n\nVedi [[semine]].")
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[concepts/transformers]]\n",
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "No dead wikilinks" in out
    assert "missing from index.md" not in out


def test_normalising_the_target_does_not_mean_matching_anything(lint_wiki, tmp_path, capsys):
    """(f) Il controllo di tenuta: `citta.md` esiste, `[[Città Vecchia]]` no —
    slug diverso, link morto. E una pagina che la mappa non linka resta fuori
    indice."""
    root = _notebook(tmp_path)
    _page(root, "citta.md", "# Città\n\nVedi [[semine]].")
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[Città Vecchia]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" in out and "Città Vecchia" in out
    assert "Pages missing from index.md (1)" in out and "wiki/citta.md" in out


# ── T6.5: il lint smette di gridare al lupo ──────────────────────────────────
#
# Sette falsi positivi, sei dei quali a 🔴 e tutti su file **corretti**. Il costo
# non è il rumore: è che un 🔴 su un file sano insegna a saltare i 🔴, e fra
# quelli ci sono le 23 pagine vere che il prompt non riesce a iniettare.
#
# Per ognuno, la coppia: la forma che dev'essere pulita, e il guasto vero
# accanto — perché una correzione che zittisce il vero insieme al falso è
# peggio del difetto.

_BODY = "\n# Semine\n\nPomodori a fine aprile. Vedi [[terreno]].\n"


def _semine(root: Path, text: str) -> Path:
    """Sostituisce `wiki/semine.md` di un taccuino sano col testo dato."""
    (root / "wiki" / "semine.md").write_text(text, encoding="utf-8")
    _journal(root, "un fatto")
    return root


@pytest.mark.parametrize(
    "page_text",
    [
        pytest.param("\n---\nstate: open\n---\n" + _BODY, id="riga-vuota-prima-del---"),
        pytest.param("---\nstate: open\n---", id="solo-frontmatter-senza-newline"),
        pytest.param("---\nstate: open  # da confermare\n---\n" + _BODY, id="commento-in-linea"),
        pytest.param("---\nstate: Open\n---\n" + _BODY, id="stato-con-la-maiuscola"),
        pytest.param("﻿---\nstate: open\n---\n" + _BODY, id="bom-utf8"),
    ],
)
def test_a_frontmatter_a_person_would_write_declares_its_state(
    lint_wiki, tmp_path, capsys, page_text
):
    """Cinque forme che una persona (o un editor) scrive senza pensarci e che il
    lint leggeva come «nessuno stato»: `FRONTMATTER_RE` era ancorata a `^---\\n`,
    il valore arrivava col commento attaccato, e il vocabolario era sensibile
    alle maiuscole a tre righe da un confronto sui nomi che non lo è."""
    root = _semine(_notebook(tmp_path), page_text)

    out = _run(lint_wiki, root, capsys)

    assert "no valid `state:`" not in out, out
    assert "Every page declares a valid state" in out


@pytest.mark.parametrize(
    ("page_text", "reported"),
    [
        pytest.param("---\ntitle: Semine\n---\n" + _BODY, "(missing)", id="nessuno-stato"),
        pytest.param(
            "---\nstate:  # da decidere\n---\n" + _BODY, "(missing)", id="solo-il-commento"
        ),
        pytest.param(
            "---\nstate: quasi-deciso\n---\n" + _BODY, "quasi-deciso", id="fuori-vocabolario"
        ),
        pytest.param(
            "---\nstate: Quasi-Deciso  # boh\n---\n" + _BODY,
            "Quasi-Deciso",
            id="fuori-vocabolario-con-maiuscole-e-commento",
        ),
        pytest.param(
            "﻿# Semine\n\nVedi [[terreno]].\n", "(missing)", id="bom-e-nessun-frontmatter"
        ),
    ],
)
def test_a_page_that_really_has_no_state_is_still_reported(
    lint_wiki, tmp_path, capsys, page_text, reported
):
    """Il controllo di tenuta dei cinque casi sopra: tollerare la forma non è
    tollerare l'assenza. Il valore riportato resta quello scritto sul file — chi
    legge deve poterlo cercare così com'è."""
    root = _semine(_notebook(tmp_path), page_text)

    out = _run(lint_wiki, root, capsys)

    assert "no valid `state:`" in out
    assert f"wiki/semine.md — {reported}" in out


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("open  # da confermare", "open", id="commento-vero"),
        pytest.param("https://x/y#z", "https://x/y#z", id="frammento-di-url"),
        pytest.param('"a # b"', "a # b", id="cancelletto-fra-virgolette"),
        pytest.param('"x"  # nota', "x", id="commento-dopo-lo-scalare-quotato"),
        pytest.param("it's fine  # nota", "it's fine", id="apostrofo-non-e-una-virgoletta"),
        pytest.param("open#x", "open#x", id="senza-spazio-non-e-un-commento"),
    ],
)
def test_only_a_real_inline_comment_is_stripped(lint_wiki, value, expected):
    """La regola è quella di YAML: il commento comincia a un `#` **preceduto da
    uno spazio** e **fuori** dallo scalare quotato. Senza la prima metà si
    troncherebbe ogni `source:` con un frammento di URL; senza la seconda, ogni
    titolo che contiene un cancelletto."""
    fm = lint_wiki.parse_frontmatter(f"---\nk: {value}\n---\n")

    assert fm["k"] == expected


def test_a_page_documenting_the_wikilink_syntax_has_no_dead_links(
    lint_wiki, tmp_path, capsys
):
    """Quel che sta dentro un blocco recintato è **mostrato**, non scritto. Una
    pagina che spiega come si scrive un wikilink produceva un 🔴 per esempio, e
    l'esempio ripetuto tre volte diventava anche «linkato spesso e senza
    pagina» — due passi diversi, la stessa causa, quindi una sola correzione:
    dentro `extract_wikilinks`."""
    root = _notebook(tmp_path)
    _page(
        root,
        "sintassi.md",
        "# Sintassi\n\nSi scrive così:\n\n```\n[[Nome Pagina]]\n[[Nome Pagina]]\n"
        "[[Nome Pagina]]\n```\n\nO in linea: `[[Altro]]`. Vedi [[terreno]].",
        state="done",
    )
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[sintassi]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "No dead wikilinks" in out
    assert "No frequently-linked missing pages" in out
    assert "Nome Pagina" not in out and "Altro" not in out


def test_a_dead_link_outside_the_fence_is_still_reported(lint_wiki, tmp_path, capsys):
    """Il controllo di tenuta: lo stesso bersaglio, la stessa pagina, fuori dai
    backtick. Tre volte, così anche il passo 4 deve continuare a vederlo."""
    root = _notebook(tmp_path)
    _page(
        root,
        "sintassi.md",
        "# Sintassi\n\nVedi [[Nome Pagina]], poi [[Nome Pagina]] e [[Nome Pagina]].\n"
        "Vedi [[terreno]].",
        state="done",
    )
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[sintassi]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" in out and "Nome Pagina" in out
    assert "Frequently linked but no page" in out and "mentioned 3x" in out


def test_a_subfolder_hub_does_not_have_to_declare_a_state(lint_wiki, tmp_path, capsys):
    """`state:` si chiedeva a ogni `index.md` tranne quello alla radice, mentre i
    passi 8/9/10 esentano `index` sempre. Una cartella con il suo hub è una forma
    prevista, quindi era un 🔴 fisso per sottocartella su una struttura che la
    skill stessa insegna: un `index.md` è una mappa, non una pagina."""
    root = _notebook(tmp_path)
    hub = root / "wiki" / "orto"
    hub.mkdir()
    (hub / "index.md").write_text("# Orto\n\n- [[semine]]\n", encoding="utf-8")
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[orto]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "no valid `state:`" not in out, out
    assert "Every page declares a valid state" in out


def test_a_real_page_inside_a_subfolder_still_needs_a_state(lint_wiki, tmp_path, capsys):
    """Il controllo di tenuta: esentare gli hub non esenta la sottocartella."""
    root = _notebook(tmp_path)
    hub = root / "wiki" / "orto"
    hub.mkdir()
    (hub / "index.md").write_text("# Orto\n\n- [[orto/pomodori]]\n", encoding="utf-8")
    (hub / "pomodori.md").write_text(
        "---\ntitle: Pomodori\n---\n\n# Pomodori\n\nVedi [[semine]].\n", encoding="utf-8"
    )
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[orto]]\n- [[orto/pomodori]]\n",
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "no valid `state:`" in out
    assert "wiki/orto/pomodori.md — (missing)" in out
    assert "wiki/orto/index.md" not in out


# ── T6.2: il modo lo decidono le pagine, e il lint lo dice ───────────────────
#
# Il modo era deciso dalle **cartelle**: bastava che ``wiki/concepts/`` esistesse,
# vuota, perché il controllo su ``state:`` — il 🔴 su cui poggia tutto il formato
# taccuino — uscisse dall'output senza una riga che lo dicesse. E il top-up dello
# scaffold di questa skill, che SKILL.md consiglia come sicuro da rilanciare,
# creava esattamente quelle tre cartelle. Il difetto sotto il difetto è il
# silenzio: un report più corto si legge come una wiki più sana.


def test_an_empty_taxonomy_folder_does_not_make_it_a_library(lint_wiki, tmp_path, capsys):
    """Il caso misurato: le tre cartelle esistono, vuote, e le pagine sono piatte.

    Con la regola vecchia questo output non conteneva più il controllo su
    ``state:``; con quella nuova la pagina senza stato è ancora un 🔴.
    """
    root = _notebook(tmp_path, state=None)
    for sub in ("concepts", "entities", "summaries"):
        (root / "wiki" / sub).mkdir()
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert lint_wiki.is_research_layout(root / "wiki") is False
    assert "no valid `state:`" in out and "semine.md" in out


def test_one_page_under_the_taxonomy_is_the_declaration(lint_wiki, tmp_path, capsys):
    """L'altro verso: una pagina *dentro* ``concepts/`` dichiara la biblioteca, e
    le vecchie wiki non diventano cittadini di serie B per una cartella."""
    root = _notebook(tmp_path, state=None)
    (root / "wiki" / "concepts").mkdir()
    (root / "wiki" / "concepts" / "Concetto.md").write_text(
        "---\nsources: [nota]\n---\n\n# Concetto\n\nVedi [[semine]].\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert lint_wiki.is_research_layout(root / "wiki") is True
    assert "no valid `state:`" not in out


def test_the_lint_names_the_mode_it_chose(lint_wiki, tmp_path, capsys):
    """Un controllo che sparisce in silenzio è il difetto sotto questo, quindi il
    modo si stampa sempre — e con il perché, così sistemarlo è spostare una
    pagina invece di indovinare una regola."""
    notebook = _run(lint_wiki, _notebook(tmp_path / "t"), capsys)
    library = _run(lint_wiki, _library(tmp_path / "b"), capsys)

    assert "Layout: notebook" in notebook
    assert "no page under concepts/, entities/, summaries/" in notebook
    assert "Layout: research" in library
    assert "a page lives under wiki/entities/" in library
    # E il modo ricerca non nomina il controllo che non applica con la stringa
    # che i test del passo 13 cercano: quel silenzio lì è voluto.
    assert "state:" not in library


# ── T6.3: lo stato del lint è onesto e durevole ──────────────────────────────


def _lint_state(root: Path) -> Path:
    return root / ".jenny" / "lint_journal.json"


def test_the_third_run_still_reports_the_line_that_changed(lint_wiki, tmp_path, capsys):
    """**Il lavaggio.** ``write_lint_state`` girava sempre, quindi la sequenza che
    SKILL.md prescrive — «lint, correggi, rilancia finché è pulito» — era anche
    la sequenza che cancella la registrazione: run 1 la base, run 2 il 🔴, run 3
    il verde su una riga che nessuno ha rimesso a posto.
    """
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)  # run 1: la base

    page.write_text(
        page.read_text(encoding="utf-8").replace("primo", "PRIMO"), encoding="utf-8"
    )
    second = _run(lint_wiki, root, capsys)  # run 2: il 🔴
    third = _run(lint_wiki, root, capsys)  # run 3: deve dire la stessa cosa

    assert "already-written line was changed" in second
    assert "already-written line was changed" in third, third
    assert "append-only since the last lint" not in third


def test_restoring_the_line_clears_the_violation(lint_wiki, tmp_path, capsys):
    """Il rovescio, e la ragione per cui la scelta è «non sovrascrivere
    l'impronta» invece di «tenere una lista di violazioni nello stato»: una lista
    andrebbe dichiarata risolta e il lint non ha un comando per farlo, quindi
    resterebbe rossa anche dopo la riparazione. L'impronta ferma si spegne da sé
    nel solo caso in cui deve."""
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)
    before = page.read_text(encoding="utf-8")

    page.write_text(before.replace("primo", "PRIMO"), encoding="utf-8")
    _run(lint_wiki, root, capsys)
    page.write_text(before, encoding="utf-8")
    out = _run(lint_wiki, root, capsys)

    assert "no longer append-only" not in out, out
    assert "append-only since the last lint" in out


def test_a_baseline_that_was_not_written_is_not_announced(lint_wiki, tmp_path, capsys):
    """**La promessa falsa.** ``write_lint_state`` ingoiava ogni ``OSError`` e il
    chiamante annunciava la base comunque. In un turno in sola lettura
    ``ReadOnlyTurnError`` *è* un ``OSError``: il lint dichiarava un controllo che
    al run dopo non esiste. Qui la cartella nascosta è occupata da un file, che è
    lo stesso ``OSError`` per una via che non dipende dai permessi."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    (root / ".jenny").write_text("non sono una cartella\n", encoding="utf-8")

    out = _run(lint_wiki, root, capsys)

    assert "baseline recorded" not in out, out
    assert "Could not record the journal state" in out


def test_a_failed_write_leaves_the_previous_state_intact(lint_wiki, tmp_path, capsys):
    """La scrittura è atomica: temp file più ``os.replace``, copia locale di
    ``jenny/utils/path.py::atomic_write``. Qui il temp è occupato da una
    cartella, quindi la scrittura muore *prima* di toccare il bersaglio — con un
    ``write_text`` secco lo stato di ieri sarebbe già troncato."""
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)
    baseline = _lint_state(root).read_bytes()

    (root / ".jenny" / "lint_journal.json.tmp").mkdir()
    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 — secondo\n")
    out = _run(lint_wiki, root, capsys)

    assert "Could not record the journal state" in out
    assert _lint_state(root).read_bytes() == baseline


def test_a_successful_write_leaves_no_temp_file(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    _run(lint_wiki, root, capsys)

    assert sorted(p.name for p in (root / ".jenny").iterdir()) == ["lint_journal.json"]


def test_digests_that_are_not_a_dict_do_not_crash_the_run(lint_wiki, tmp_path, capsys):
    """``read_lint_state`` validava l'oggetto esterno e non ``digests``, quindi
    ``{"digests": [1, 2, 3]}`` — un file sul disco dell'utente, che può essere
    qualunque cosa — dava un ``AttributeError`` in mezzo al passo 12: zero
    risultati stampati, che è l'esatto contrario dello scopo del lint."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    state = _lint_state(root)
    state.parent.mkdir(parents=True)
    state.write_text('{"version": 1, "digests": [1, 2, 3]}', encoding="utf-8")

    assert lint_wiki.read_lint_state(root) == {}

    out = _run(lint_wiki, root, capsys)  # con il difetto: AttributeError qui

    assert "baseline recorded" in out
    assert "Wiki is healthy" in out
    # E la base è ripartita da un file sano, non dalla lista.
    assert list(lint_wiki.read_lint_state(root)) == ["raw/journal/20260823.md"]


def test_a_journal_page_nobody_can_read_is_reported_as_such(lint_wiki, tmp_path, capsys):
    """Un file illeggibile era un ``continue`` muto: fuori da ogni controllo —
    forma, append-only, base — e l'output non se ne accorgeva."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    (root / "raw" / "journal" / "20260824.md").mkdir()

    out = _run(lint_wiki, root, capsys)

    assert "could not be read" in out and "20260824.md" in out


def test_an_unreadable_head_is_not_reported_as_a_changed_line(
    lint_wiki, tmp_path, capsys, monkeypatch
):
    """``head_digest`` che torna ``None`` perché il file non si legge veniva
    riportato come «una riga già scritta è cambiata»: un fatto diverso, che manda
    a cercare una riscrittura dove c'è un permesso o una corsa con chi scrive."""
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)

    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 — secondo\n")
    monkeypatch.setattr(lint_wiki, "head_digest", lambda path, size: None)
    out = _run(lint_wiki, root, capsys)

    assert "could not be read" in out
    assert "already-written line was changed" not in out, out


def test_an_unreadable_head_does_not_overwrite_the_baseline(
    lint_wiki, tmp_path, capsys, monkeypatch
):
    """Il corollario: se non si è potuto verificare, la base di ieri resta. Farla
    avanzare vorrebbe dire che un run illeggibile lava quel che nasconde."""
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)
    baseline = _lint_state(root).read_text(encoding="utf-8")

    page.write_text(
        page.read_text(encoding="utf-8").replace("primo", "PRIMO"), encoding="utf-8"
    )
    monkeypatch.setattr(lint_wiki, "head_digest", lambda path, size: None)
    _run(lint_wiki, root, capsys)
    monkeypatch.undo()

    assert _lint_state(root).read_text(encoding="utf-8") == baseline
    assert "already-written line was changed" in _run(lint_wiki, root, capsys)


# ── T6.6: elenchi con un tetto, esiti che si distinguono ─────────────────────
#
# L'output di questo script torna al modello attraverso ``python_exec``, che
# oltre 10.000 caratteri tiene la testa e la coda e **butta il mezzo**. Un
# elenco senza tetto su una wiki vera si porta via i passi che stavano in mezzo,
# e quel che resta è un report tagliato da un marcatore generico: si vede che
# manca qualcosa, non cosa. Il tetto lo sceglie il lint, e dice quanto ha tolto.

_PYTHON_EXEC_DEFAULT_CEILING = 10_000


def _many_broken_pages(root: Path, n: int) -> Path:
    """*n* pagine piatte, ognuna con un link morto e nessuno stato."""
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Mappa\n", encoding="utf-8")
    for i in range(n):
        (root / "wiki" / f"pagina-{i:03d}.md").write_text(
            f"# Pagina {i}\n\nVedi [[fantasma-{i:03d}]].\n", encoding="utf-8"
        )
    return root


def test_a_long_list_is_capped_and_names_what_it_left_out(lint_wiki, tmp_path, capsys):
    root = _many_broken_pages(tmp_path, 40)

    out = _run(lint_wiki, root, capsys)

    # Il totale in testa resta quello vero: il tetto tocca l'elenco, non il conto.
    assert "🔴 Dead wikilinks (40):" in out
    assert out.count("→ [[fantasma-") == lint_wiki.LIST_MAX_ENTRIES
    assert "…and 20 more (of 40" in out


def test_the_whole_report_stays_under_the_ceiling_that_drops_the_middle(
    lint_wiki, tmp_path, capsys
):
    """Il test che conta: 120 pagine rotte facevano un report di ~14 kB, e quel
    che il modello riceveva era la testa e la coda con il mezzo buttato — cioè
    interi passi spariti in silenzio, non un elenco accorciato."""
    root = _many_broken_pages(tmp_path, 120)

    out = _run(lint_wiki, root, capsys)

    assert len(out) < _PYTHON_EXEC_DEFAULT_CEILING, f"{len(out)} caratteri"
    # E i passi in coda ci sono ancora, che è tutto il punto.
    assert "no valid `state:`" in out
    assert "issue(s) found" in out


def test_a_path_typo_is_not_a_clean_wiki(lint_wiki, tmp_path, capsys):
    """``lint()`` scriveva questo errore su **stderr** e tornava 1. Il chiamante
    vero (``python_exec_builtins.wiki_lint``) cattura solo stdout e butta il
    codice, quindi restituiva la stringa "No output": un errore di battitura nel
    percorso era indistinguibile da una wiki pulita."""
    rc = lint_wiki.lint(str(tmp_path / "wikis" / "typo"))
    captured = capsys.readouterr()

    assert rc == lint_wiki.EXIT_UNUSABLE
    assert "wiki/ directory not found" in captured.out
    assert captured.err == ""
    assert "nothing was linted" in captured.out


def test_the_three_outcomes_have_three_codes(lint_wiki, tmp_path, capsys):
    """«Non ho controllato niente» e «ho controllato e ci sono problemi» erano
    entrambi 1: il primo esito non ha un elenco da leggere, e confonderlo col
    secondo è l'unico errore che un controllo di salute non può permettersi."""
    clean = _notebook(tmp_path / "sana")
    _journal(clean, "un fatto")
    assert lint_wiki.lint(str(clean)) == lint_wiki.EXIT_OK

    broken = _notebook(tmp_path / "rotta", state=None)
    _journal(broken, "un fatto")
    assert lint_wiki.lint(str(broken)) == lint_wiki.EXIT_ISSUES

    assert lint_wiki.lint(str(tmp_path / "inesistente")) == lint_wiki.EXIT_UNUSABLE
    capsys.readouterr()


def test_the_builtin_the_model_calls_no_longer_says_no_output(lint_wiki, tmp_path):
    """Il difetto si vede solo componendo i due strati, quindi il test li compone:
    stesso ``redirect_stdout`` del builtin, stessa stringa di ripiego."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        lint_wiki.lint(str(tmp_path / "wikis" / "typo"))

    captured = buf.getvalue()
    assert captured, "stdout vuoto: il builtin ricadrebbe sulla stringa di ripiego"
    assert (captured or "No output") != "No output"


# ── T6.7: `source:`, cioè se una pagina è verificabile ───────────────────────
#
# ``templates/agent/gardener.md`` prescrive ``source:`` accanto a ``state:`` e
# dice perché: è «il sentiero da una pagina alla frase che l'ha causata, ed è
# quel che rende una pagina sbagliata correggibile invece che solo sbagliata».
# Il lint chiedeva lo stato e del sentiero non guardava niente.


def test_a_page_with_no_source_is_unverifiable_not_wrong(lint_wiki, tmp_path, capsys):
    """🟡 e non 🔴, e la differenza è argomentata: una pagina senza ``state:``
    *dice una cosa falsa* (un'ipotesi si rilegge come un fatto), una pagina senza
    ``source:`` non dice niente di falso — non si può verificare."""
    root = _notebook(tmp_path, source=None)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "🟡 Pages with no `source:`" in out
    assert "wiki/semine.md" in out
    assert "unverifiable" in out
    assert "🔴 Pages with no `source:`" not in out


def test_a_source_that_resolves_is_quiet(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "source:" not in out.replace("`source:`", "")
    assert "Wiki is healthy" in out


def test_a_source_pointing_nowhere_is_a_different_finding(lint_wiki, tmp_path, capsys):
    """Un giorno di diario potato non è «manca il campo»: il sentiero è stato
    scritto e ora non porta da nessuna parte, e la causa da cercare è un'altra."""
    root = _notebook(tmp_path, source="raw/journal/20250101.md")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "names a file that is not there" in out
    assert "raw/journal/20250101.md" in out
    assert "Pages with no `source:`" not in out


def test_a_source_on_the_web_is_not_looked_for_on_disk(lint_wiki, tmp_path, capsys):
    """Una fonte esterna è legittima e nessun controllo di percorso la risolve:
    segnalarla sarebbe il falso positivo che insegna a ignorare i 🟡."""
    root = _notebook(tmp_path, source="https://example.org/pagina")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "names a file that is not there" not in out
    assert "Pages with no `source:`" not in out


def test_a_library_is_not_asked_for_a_source(lint_wiki, tmp_path, capsys):
    """Il campo è del formato taccuino: nelle biblioteche la provenienza la porta
    ``sources:`` (passo 9), e chiedere entrambi vorrebbe dire due 🟡 per un fatto."""
    root = _library(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Pages with no `source:`" not in out
    assert "names a file that is not there" not in out


def test_the_map_is_not_asked_for_a_source(lint_wiki, tmp_path, capsys):
    """La mappa non è una pagina — non ha uno stato e non ha una provenienza —
    e ogni ``index.md`` è una mappa, non solo quello alla radice (T6.5)."""
    root = _notebook(tmp_path)
    (root / "wiki" / "orto").mkdir()
    (root / "wiki" / "orto" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n", encoding="utf-8"
    )
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[orto]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "orto/index.md" not in out
    assert "Wiki is healthy" in out


# ── T6.8/H18: due pagine con lo stesso nome sono due pagine ──────────────────
#
# Le mappe dei link entranti e uscenti erano indicizzate per ``p.stem``, quindi
# ``wiki/a/nota.md`` e ``wiki/b/nota.md`` erano *una* identità: un link alla
# prima faceva passare la seconda per collegata, e la seconda non compariva mai
# fra le orfane. Ora la chiave è il percorso — gli stessi ``Path`` che
# ``page_for_link`` restituisce — e la tolleranza sui nomi (maiuscole, slug,
# link a cartella) resta tutta nella risoluzione, dove deve stare.


def _namesake(root: Path, folder: str, body: str = "") -> None:
    (root / "wiki" / folder).mkdir(parents=True, exist_ok=True)
    (root / "wiki" / folder / "nota.md").write_text(
        f"---\nstate: open\nsource: {_JOURNAL_DAY}\n---\n\n# Nota {folder}\n\n{body}\n",
        encoding="utf-8",
    )


def test_a_link_to_one_namesake_does_not_adopt_the_other(lint_wiki, tmp_path, capsys):
    """La mappa nomina solo ``a/nota``: ``b/nota`` non ha un solo link entrante,
    e prima non compariva fra le orfane perché il link all'omonima contava per
    entrambe."""
    root = tmp_path
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    _namesake(root, "a")
    _namesake(root, "b")
    (root / "wiki" / "index.md").write_text("# Mappa\n\n- [[a/nota]]\n", encoding="utf-8")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    orphans = out.split("Orphan pages")[1].split("\n\n")[0]
    assert "🟡 Orphan pages (1)" in out
    assert "wiki/b/nota.md" in orphans
    assert "wiki/a/nota.md" not in orphans


def test_two_namesakes_do_not_share_their_inbound_links(lint_wiki, tmp_path, capsys):
    """Il passo del taccuino: ``semine`` linka ``a/nota``, e quel link faceva
    passare per collegata anche ``b/nota``, che non linka e non è linkata."""
    root = _notebook(tmp_path)
    (root / "wiki" / "semine.md").write_text(
        f"---\nstate: open\nsource: {_JOURNAL_DAY}\n---\n\n# Semine\n\nVedi [[a/nota]].\n",
        encoding="utf-8",
    )
    _namesake(root, "a")
    _namesake(root, "b")
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n- [[a/nota]]\n- [[b/nota]]\n",
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "no link in or out (1)" in out
    unlinked = out.split("no link in or out")[1].split("(being listed")[0]
    assert "wiki/b/nota.md" in unlinked
    assert "wiki/a/nota.md" not in unlinked


def test_two_namesakes_do_not_share_their_outbound_links(lint_wiki, tmp_path, capsys):
    """L'altro verso, sul passo 10: il conteggio dei link **uscenti** era comune
    ai due, quindi la pagina isolata si nascondeva dietro l'omonima che linka."""
    root = tmp_path
    (root / "raw").mkdir(parents=True)
    for folder in ("x", "y"):
        (root / "wiki" / "concepts" / folder).mkdir(parents=True)
        (root / "wiki" / "concepts" / folder / "nota.md").write_text(
            "---\nsources: [nota]\n---\n\n# Nota " + folder + "\n"
            + ("\nVedi [[index]].\n" if folder == "x" else ""),
            encoding="utf-8",
        )
    (root / "raw" / "nota.md").write_text("grezzo\n", encoding="utf-8")
    (root / "wiki" / "index.md").write_text(
        "# Mappa\n\n- [[concepts/x/nota]]\n- [[concepts/y/nota]]\n", encoding="utf-8"
    )

    out = _run(lint_wiki, root, capsys)

    assert "not cross-linked beyond index.md (1)" in out
    isolated = out.split("not cross-linked beyond index.md")[1].split("(add a")[0]
    assert "wiki/concepts/y/nota.md" in isolated
    assert "wiki/concepts/x/nota.md" not in isolated


# ── T6.6: il builtin che il modello chiama davvero ───────────────────────────
#
# Il difetto viveva fra i due strati e nessuno dei due lo mostrava da solo: lo
# script scriveva l'errore su **stderr**, il builtin cattura **solo stdout** e
# butta il codice di uscita, quindi ``wiki_lint("wikis/typo")`` restituiva la
# stringa letterale "No output" — cioè un percorso sbagliato indistinguibile da
# una wiki pulita. Il test compone i due strati veri, perché è l'unico modo di
# vederlo.


def test_the_builtin_returns_the_error_and_not_the_words_no_output(tmp_path, monkeypatch):
    import shutil
    from typing import Any

    from jenny.agent.tools import python_exec_builtins as builtins_mod

    workspace = tmp_path / "ws"
    scripts = workspace / "skills" / "llm-wiki" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("lint_wiki.py", "reindex_wikis.py"):
        shutil.copy(_SCRIPTS_DIR / name, scripts / name)
    monkeypatch.setattr(builtins_mod, "get_workspace_path", lambda: workspace)

    class _Recorder:
        def __init__(self) -> None:
            self.functions: dict[str, Any] = {}

        def register_function(self, name: str, func: Any) -> None:
            self.functions[name] = func

    recorder = _Recorder()
    builtins_mod._register_builtin_functions(
        recorder,  # type: ignore[arg-type]
        workspace=str(workspace),
        restrict_to_workspace=True,
    )

    out = recorder.functions["wiki_lint"]("wikis/typo")

    assert out != "No output"
    assert "wiki/ directory not found" in out
    assert "nothing was linted" in out


def test_skill_md_documents_the_exit_codes(lint_wiki):
    """I codici erano scritti nel docstring dello script, che il modello non
    legge: quel che legge è SKILL.md. Due posti che devono restare d'accordo,
    quindi il confronto sta in un test."""
    doc = (_SCRIPTS_DIR.parent / "SKILL.md").read_text(encoding="utf-8")

    for code in (lint_wiki.EXIT_OK, lint_wiki.EXIT_ISSUES, lint_wiki.EXIT_UNUSABLE):
        assert f"| `{code}` |" in doc, code
    assert "nothing was linted" in doc
    # E il tetto degli elenchi, che spiega il «…and N more» nell'output.
    assert f"capped at {lint_wiki.LIST_MAX_ENTRIES} entries" in doc


# ── T6.4: un file illeggibile costa un file ──────────────────────────────────
#
# Il passo 12 gestiva ``UnicodeDecodeError`` per il diario; ogni altra lettura era
# un ``read_text(encoding="utf-8")`` nudo. Un solo ``.md`` in latin-1 sotto
# ``wiki/`` faceva scoppiare il **passo 1**, cioè: traceback, zero risultati
# stampati, e attraverso ``wiki_lint`` — che cattura stdout e lo restituisce solo
# al ritorno normale — anche i risultati già calcolati buttati. In
# ``lint_workspace`` l'eccezione portava via tutte le wiki dopo quella guasta.


def _latin1_page(root: Path, name: str = "cattiva") -> Path:
    """Una pagina ben formata **salvata in latin-1**: solo l'encoding è sbagliato."""
    page = root / "wiki" / f"{name}.md"
    page.write_bytes(
        f"---\nstate: open\nsource: {_JOURNAL_DAY}\n---\n\n"
        f"# {name.capitalize()} perch\xe8\n\nVedi [[semine]].\n".encode("latin-1")
    )
    # La mappa la elenca, così l'unico difetto della wiki è l'encoding.
    index = root / "wiki" / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + f"- [[{name}]]\n", encoding="utf-8"
    )
    return page


def test_a_latin1_page_does_not_cost_the_whole_report(lint_wiki, tmp_path, capsys):
    """Il difetto era di quantità: **una** pagina guasta, **zero** risultati.

    L'asserzione portante è che i passi che vengono dopo hanno girato: senza il
    ``errors="replace"`` in ``read_md`` il passo 1 solleva e non si stampa nulla
    oltre la riga del layout.
    """
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    _latin1_page(root)

    rc = lint_wiki.lint(str(root))
    out = capsys.readouterr().out

    # Il file guasto è **nominato**, con il perché.
    assert "not UTF-8" in out
    assert "wiki/cattiva.md" in out
    # E i passi successivi hanno girato per davvero: questi sono i loro esiti.
    assert "✅ No dead wikilinks" in out
    assert "✅ Every page declares a valid state:" in out
    assert "issue(s) found" in out
    assert rc == lint_wiki.EXIT_ISSUES


def test_the_broken_page_is_still_checked_by_the_passes_after_it(
    lint_wiki, tmp_path, capsys
):
    """``errors="replace"`` non è «salta la pagina»: la pagina si legge, con un
    ``\\ufffd`` al posto del byte guasto, quindi i suoi link e la sua frontmatter
    restano controllabili. Qui il link morto sta **dentro** la pagina latin-1."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    page = _latin1_page(root)
    page.write_bytes(
        f"---\nstate: open\nsource: {_JOURNAL_DAY}\n---\n\n"
        "# Cattiva perch\xe8\n\nVedi [[pagina-che-non-esiste]].\n".encode("latin-1")
    )

    out = _run(lint_wiki, root, capsys)

    assert "not UTF-8" in out
    assert "[[pagina-che-non-esiste]]" in out


def test_a_utf8_page_is_not_reported_as_broken(lint_wiki, tmp_path, capsys):
    """La guardia dal verso opposto: accenti e BOM **sono** UTF-8 valido, e un
    passo 0 che li segnalasse insegnerebbe a scorrere oltre i 🔴 veri."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    (root / "wiki" / "semine.md").write_text(
        f"---\nstate: open\nsource: {_JOURNAL_DAY}\n---\n\n"
        "# Semine perché\n\nVedi [[terreno]].\n",
        encoding="utf-8-sig",  # col BOM
    )

    out = _run(lint_wiki, root, capsys)

    assert "not UTF-8" not in out


def test_a_map_that_is_not_utf8_does_not_abort_the_run(lint_wiki, tmp_path, capsys):
    """Il passo 15 leggeva ``index.md`` in ``utf-8`` stretto e senza rete: la
    mappa è l'unico file che quel passo apre, ed era l'ultimo ``read_text`` nudo
    capace di portarsi via il passo 16 e il riepilogo. La mappa qui sfonda anche
    il tetto dei caratteri, e quel risultato **non** compare: giusto così, il
    numero non si conosce — quel che si conosce, e che si stampa, è che il file
    non decodifica."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    (root / "wiki" / "index.md").write_bytes(
        ("# Orto\n\n- [[semine]]\n- [[terreno]]\n" + "perch\xe8 " * 400).encode("latin-1")
    )

    rc = lint_wiki.lint(str(root))
    out = capsys.readouterr().out

    assert "not UTF-8" in out
    assert "wiki/index.md" in out
    assert "The map is" not in out  # v. il docstring: il numero non si conosce
    assert "issue(s) found" in out  # il riepilogo è stato raggiunto
    assert rc == lint_wiki.EXIT_ISSUES


def test_one_bad_wiki_in_a_workspace_costs_one_wiki(lint_wiki, tmp_path, capsys):
    """L'isolamento per wiki, con l'eccezione iniettata invece che provocata: il
    punto del test è il ``try/except`` del ciclo, non quale difetto lo innesca —
    e in ordine alfabetico ``a-rotta`` viene prima, quindi senza la rete
    ``z-sana`` non veniva lintata affatto.
    """
    wikis = tmp_path / "wikis"
    for name in ("a-rotta", "z-sana"):
        root = _notebook(wikis / name)
        _journal(root, "un fatto")
    (wikis / "_index.md").write_text("# Wikis\n", encoding="utf-8")

    real_lint = lint_wiki.lint

    def _exploding_lint(root: str) -> int:
        if root.endswith("a-rotta"):
            raise RuntimeError("boom")
        return real_lint(root)

    lint_wiki.lint = _exploding_lint
    try:
        rc = lint_wiki.lint_workspace(str(wikis))
    finally:
        lint_wiki.lint = real_lint
    out = capsys.readouterr().out

    assert "the lint crashed on this wiki: RuntimeError: boom" in out
    # E la wiki dopo è stata lintata: questa riga viene dal suo report.
    assert "z-sana" in out
    assert out.count("✅ No dead wikilinks") == 1
    assert rc == lint_wiki.EXIT_ISSUES


def test_a_registry_that_cannot_be_read_does_not_erase_the_reports(
    lint_wiki, tmp_path, capsys
):
    """L'ultimo anello: ``check_index`` legge ``_index.md`` in ``utf-8`` stretto e
    sta in ``reindex_wikis.py``, che è un checkout dell'utente. Se scoppia lì,
    ogni report già calcolato sparisce dal buffer di chi cattura stdout."""
    wikis = tmp_path / "wikis"
    root = _notebook(wikis / "orto")
    _journal(root, "un fatto")
    (wikis / "_index.md").write_bytes("# Wikis perch\xe8\n".encode("latin-1"))

    rc = lint_wiki.lint_workspace(str(wikis))
    out = capsys.readouterr().out

    assert "the registry check crashed: UnicodeDecodeError" in out
    assert "✅ No dead wikilinks" in out  # il report della wiki è ancora lì
    assert rc == lint_wiki.EXIT_ISSUES


def test_the_builtin_returns_the_findings_it_already_had(tmp_path, monkeypatch):
    """La decisione di T6.4: ``wiki_lint`` torna il buffer **parziale** più un 🔴
    che dice che è parziale.

    Le due alternative sono peggiori in modi opposti. Lasciar salire l'eccezione
    dà al modello un traceback e zero risultati — anche quelli dei passi conclusi
    prima del crollo, che non sono dubbi. Tornare il parziale in silenzio è il
    difetto che T6.6 ha chiuso: un report senza riepilogo si legge come un
    report, perché nessuno conta i passi che si aspettava.
    """
    import shutil
    from typing import Any

    from jenny.agent.tools import python_exec_builtins as builtins_mod

    workspace = tmp_path / "ws"
    scripts = workspace / "skills" / "llm-wiki" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("lint_wiki.py", "reindex_wikis.py"):
        shutil.copy(_SCRIPTS_DIR / name, scripts / name)
    # Uno script che stampa dei risultati e **poi** scoppia: è la forma esatta
    # del difetto, e iniettarla è l'unico modo di non dipendere da quale bug
    # sopravvive nello script vero.
    (scripts / "lint_wiki.py").write_text(
        "def lint(root):\n"
        "    print('🔴 Dead wikilinks (2):')\n"
        "    print('   wiki/a.md → [[b]]')\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(builtins_mod, "get_workspace_path", lambda: workspace)

    class _Recorder:
        def __init__(self) -> None:
            self.functions: dict[str, Any] = {}

        def register_function(self, name: str, func: Any) -> None:
            self.functions[name] = func

    recorder = _Recorder()
    builtins_mod._register_builtin_functions(
        recorder,  # type: ignore[arg-type]
        workspace=str(workspace),
        restrict_to_workspace=True,
    )

    out = recorder.functions["wiki_lint"]("wikis/orto")

    # I risultati già calcolati ci sono…
    assert "Dead wikilinks (2)" in out
    assert "wiki/a.md" in out
    # …e la riga che vieta di leggerlo come un report finito.
    assert "crashed before it finished: RuntimeError: boom" in out
    assert "not the total" in out


# ── L'accordo fra il lint e l'app ───────────────────────────────────────────


def _split_wiki(root: Path) -> Path:
    """Una wiki con un concetto diviso in cartella, come la guida lo descrive."""
    (root / "wiki" / "concepts" / "Transformers" / "training").mkdir(parents=True)
    (root / "wiki" / "concepts" / "Other").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    pages = {
        "index.md": "# Ricerca\n\n- [[concepts/Transformers/index|Transformers]]\n",
        "concepts/Transformers/index.md": "# Transformers\n\n- [[Transformers/attention]]\n",
        "concepts/Transformers/attention.md": "# Attention\n",
        "concepts/Transformers/training/index.md": "# Training\n",
        "concepts/Other/attention.md": "# Attention altrove\n",
    }
    for rel, text in pages.items():
        (root / "wiki" / rel).write_text(text, encoding="utf-8")
    return root


# Le forme che contano, con il verdetto **atteso** scritto a mano: senza questa
# colonna il test confronterebbe due funzioni fra loro e passerebbe anche se
# entrambe rispondessero `None` a tutto.
_LINK_CASES = [
    ("Transformers/attention", "concepts/Transformers/attention.md"),
    ("transformers/ATTENTION", "concepts/Transformers/attention.md"),
    ("concepts/Transformers/attention", "concepts/Transformers/attention.md"),
    ("Transformers/training", "concepts/Transformers/training/index.md"),
    ("concepts/Transformers", "concepts/Transformers/index.md"),
    ("Transformers/index", "concepts/Transformers/index.md"),
    ("Other/attention", "concepts/Other/attention.md"),
    ("attention", "concepts/Other/attention.md"),
    ("Altro/attention", None),
    ("Transformers/nessuna", None),
    ("a/b/c/d", None),
]


def test_the_lint_and_the_app_resolve_a_link_the_same_way(lint_wiki, tmp_path):
    """**Il punto di T6.10.** La guida raccomanda `[[<Topic>/<aspect>]]` dentro un
    `index.md` diviso in cartella: l'app non lo apriva e il lint lo dava per
    vivo, quindi l'unico strumento che poteva accorgersi del link morto era
    quello che diceva che andava bene. Qui i due verdetti si confrontano forma
    per forma, con l'esito atteso scritto in `_LINK_CASES`.
    """
    from jenny.webui.wiki import resolve_wikilink

    root = _split_wiki(tmp_path)
    pages_dir = root / "wiki"
    pages = lint_wiki.load_pages(pages_dir)

    for link, expected in _LINK_CASES:
        app = resolve_wikilink(root, link)
        lint = lint_wiki.page_for_link(pages, link)
        app_rel = app.relative_to(pages_dir).as_posix() if app else None
        lint_rel = lint.relative_to(pages_dir).as_posix() if lint else None
        assert app_rel == expected, f"[[{link}]]: l'app risolve {app_rel}"
        assert lint_rel == expected, f"[[{link}]]: il lint risolve {lint_rel}"


def test_the_two_order_rules_are_the_same_rule(lint_wiki):
    """`suffix_rank` è copiato in due file perché lo script non può importare il
    package. Se uno dei due cambia, l'accordo si rompe in silenzio su una wiki
    con due pagine omonime — un caso che nessuna fixture piccola incontra."""
    from jenny.webui.wiki import _suffix_rank

    for p in (Path("wiki/a/nota.md"), Path("wiki/nota.md"), Path("wiki/b/z/nota.md")):
        assert lint_wiki.suffix_rank(p) == _suffix_rank(p)


def test_a_folder_relative_link_is_not_reported_dead(lint_wiki, tmp_path, capsys):
    root = _split_wiki(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" not in out
    assert "No dead wikilinks" in out


def test_a_multi_segment_link_the_app_cannot_open_is_reported_dead(lint_wiki, tmp_path, capsys):
    """La direzione che il lint taceva. `[[Altro/attention]]` non esiste, ma
    esisteva *una* pagina chiamata `attention`, e il ripiego per stem la
    accettava: un link morto sul telefono e un lint verde.
    """
    root = _split_wiki(tmp_path)
    (root / "wiki" / "concepts" / "Transformers" / "index.md").write_text(
        "# Transformers\n\n- [[Transformers/attention]]\n- [[Altro/attention]]\n",
        encoding="utf-8",
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks (1)" in out
    assert "[[Altro/attention]]" in out
    assert "[[Transformers/attention]]" not in out


_ARTICLE_GUIDE = (
    Path(__file__).resolve().parents[3]
    / "jenny" / "skills" / "llm-wiki" / "references" / "article-guide.md"
)


def _guide_split_example_links() -> list[str]:
    """I wikilink del blocco di codice che la guida dà da copiare per un
    `index.md` diviso in cartella — solo quelli, non la prosa intorno (che cita
    di proposito anche le forme *sbagliate*), e senza i segnaposto `...`."""
    text = _ARTICLE_GUIDE.read_text(encoding="utf-8")
    section = text.split("## Divide and conquer", 1)[1]
    block = section.split("```markdown", 1)[1].split("```", 1)[0]
    links = re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", block)
    return [link.replace("<", "").replace(">", "") for link in links if "..." not in link]


def test_the_form_the_guide_gives_to_copy_is_a_form_both_sides_open(lint_wiki, tmp_path):
    """La guida è quel che il modello legge prima di scrivere una pagina, quindi
    l'esempio che le dà da copiare deve risolvere in *entrambi* i lati. Prima non
    risolveva nell'app: la forma raccomandata rendeva un link morto sul telefono,
    e il lint — l'unico che poteva accorgersene — la accettava per stem.
    """
    from jenny.webui.wiki import resolve_wikilink

    links = _guide_split_example_links()
    assert links, "il blocco d'esempio della guida non contiene più wikilink"

    root = tmp_path / "w"
    for rel in ("index.md", "concepts/Topic/index.md",
                "concepts/Topic/aspect-1.md", "concepts/Topic/aspect-2.md"):
        page = root / "wiki" / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# x\n", encoding="utf-8")
    pages = lint_wiki.load_pages(root / "wiki")

    for link in links:
        assert resolve_wikilink(root, link) is not None, f"l'app non apre [[{link}]]"
        assert lint_wiki.page_for_link(pages, link) is not None, f"il lint dà [[{link}]] per morto"


def test_a_link_that_leaves_the_pages_dir_is_dead_and_the_lint_says_how_to_fix_it(
    lint_wiki, tmp_path, capsys
):
    """**La forma che il telefono ha davvero.** Un summary che cita il suo grezzo
    con `[[raw/notes/<slug>]]`: l'app non esce da `wiki/` — il contenimento è
    voluto — quindi il link è morto, e il ripiego per stem lo teneva in vita nel
    lint perché *esisteva* una pagina con quello stem (il summary stesso). Venti
    link di questa forma stanno sulle wiki vere. Ora si segnalano, con il rimedio.
    """
    from jenny.webui.wiki import resolve_wikilink

    root = _library(tmp_path)
    (root / "raw" / "notes").mkdir(parents=True)
    (root / "raw" / "notes" / "seaman.md").write_text("grezzo\n", encoding="utf-8")
    (root / "wiki" / "summaries").mkdir(exist_ok=True)
    (root / "wiki" / "summaries" / "seaman.md").write_text(
        "---\nsources: [nota]\n---\n\n# Seaman\n\nFonte: [[raw/notes/seaman]]\n",
        encoding="utf-8",
    )
    (root / "wiki" / "index.md").write_text(
        "# Ricerca\n\n- [[Ada]]\n- [[summaries/seaman]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    assert resolve_wikilink(root, "raw/notes/seaman") is None

    out = _run(lint_wiki, root, capsys)

    assert "[[raw/notes/seaman]]" in out
    assert "a link only reaches pages under wiki/" in out


# ── T9.5: una definizione di «pagina», e due copie che devono restare pari ───


def test_the_lint_and_the_injector_agree_on_what_a_page_is(lint_wiki, tmp_path):
    """``is_injected_page`` è una copia dichiarata di ``is_wiki_page_rel``.

    Il lint non può importare ``jenny`` — è un checkout della skill che gira
    anche fuori dall'app — quindi la copia resta, e quel che tiene le due parti
    uguali è questo confronto **funzionale**: si costruisce una wiki con un
    esemplare di ogni caso limite e si guarda se i due insiemi coincidono. È la
    stessa disciplina della coppia ``MAP_MAX_CHARS``/``_PROJECT_MAP_MAX_CHARS``
    (T3.12), un gradino più su: là si confrontano due numeri, qui due regole.
    """
    from jenny.utils.wiki_paths import iter_wiki_pages

    pages_dir = tmp_path / "wiki"
    for rel in (
        "index.md",
        "semine.md",
        "concepts/loop.md",
        "concepts/Topic/index.md",
        "summaries/doc.md",
        "summaries.md",
        ".bozza.md",
        ".bozze/nota.md",
    ):
        target = pages_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# x\n", encoding="utf-8")

    dal_lint = {
        p.relative_to(pages_dir).as_posix()
        for p in pages_dir.rglob("*.md")
        if lint_wiki.is_injected_page(p.relative_to(pages_dir))
    }

    assert dal_lint == set(iter_wiki_pages(pages_dir, titles=False))
    # E non è vuoto per caso: se lo fosse, il confronto sarebbe verde per il
    # motivo sbagliato.
    assert "concepts/Topic/index.md" in dal_lint


def test_a_page_in_a_hidden_folder_is_not_reported_as_too_long(
    lint_wiki, tmp_path, capsys
):
    """Il caso che la copia sbagliata segnalava: il passo 16 guardava solo il
    nome del **file**, quindi una ``wiki/.bozze/lunga.md`` veniva dichiarata
    «troppo lunga per entrare in un prompt» in cui non entrava comunque. Un
    lavoro inventato, che è il modo in cui un lint perde credito.
    """
    root = _notebook(tmp_path)
    (root / "wiki" / ".bozze").mkdir()
    _page_of(root, lint_wiki.PAGE_MAX_CHARS + 1, name=".bozze/lunga.md")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "too long to be injected" not in out


# ── T7.12: l'elenco di pagine dentro AGENTS.md ──────────────────────────────


def _agents_md(root: Path, body: str) -> None:
    (root / "AGENTS.md").write_text(
        f"---\nsummary: l'orto di casa\n---\n\n# Orto\n\n## Scope\n\n{body}",
        encoding="utf-8",
    )


def _page_list(entries: int, *, prefix: str = "") -> str:
    return "## Current articles\n\n" + "".join(
        f"- [[{prefix}pagina-{i:02d}]] — una riga di riassunto che descrive la pagina\n"
        for i in range(entries)
    )


def test_a_page_list_inside_agents_md_is_reported(lint_wiki, tmp_path, capsys):
    """Il difetto misurato: 4.381 caratteri su 7.161 (61%) del file più grande
    erano un elenco di pagine, mentre la mappa vera di quel progetto arriva al
    modello **troncata** a 2.000. Due indici, e quello senza tetto è quello che
    nessuno cura.
    """
    root = _notebook(tmp_path)
    _agents_md(root, _page_list(20))
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "AGENTS.md carries a page list" in out
    assert "20 entries" in out
    # Dice dove va, e nomina il file che l'utente deve aprire.
    assert "wiki/index.md" in out


def test_the_page_list_finding_is_yellow_and_counts_once(lint_wiki, tmp_path, capsys):
    """**La severità, con l'argomento del passo 17.** Il rosso è per il
    contenuto che *inganna*; un indice duplicato non dice niente di falso, dice
    due volte una cosa vera. Ed è **un** rilievo — una decisione di
    collocazione — non venti righe sbagliate: contarlo per riga farebbe di un
    file da sistemare in cinque minuti il progetto più malato del workspace.
    """
    root = _notebook(tmp_path)
    _agents_md(root, _page_list(20))
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    row = next(line for line in out.splitlines() if "carries a page list" in line)
    assert row.startswith("🟡"), row
    assert "1 issue(s) found" in out
    # E questa è metà dell'argomento del messaggio, provata invece che
    # asserita: le venti voci puntano a pagine che non esistono, e **nessun
    # passo lo dice** — il passo 1 cammina ``wiki/**`` e ``AGENTS.md`` sta
    # fuori. Nella mappa quegli stessi link li controllerebbe già lui.
    assert "🔴 Dead wikilinks" not in out


def test_the_message_argues_against_the_fix_that_looks_obvious(
    lint_wiki, tmp_path, capsys
):
    """T7.10 aveva rifiutato un tetto su ``AGENTS.md`` e T7.12 ha mostrato
    perché un taglio sarebbe *l'errore peggiore*: la coda di quel file è «Open
    research questions», la sola parte azionabile, e un troncamento terrebbe
    l'indice duplicato buttando quella. Un avviso che non lo dice invita
    esattamente a quello.
    """
    root = _notebook(tmp_path)
    _agents_md(root, _page_list(20))
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "do NOT trim the tail" in out


def test_a_short_list_stays_quiet(lint_wiki, tmp_path, capsys):
    """Un «parti da qui» di tre voci non è un secondo indice, ed è la ragione
    per cui il tetto non è zero: un avviso su cui non c'è niente da spostare è
    un avviso che si impara a scorrere.
    """
    root = _notebook(tmp_path)
    _agents_md(root, _page_list(3))
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "carries a page list" not in out


def test_prose_that_merely_names_a_file_is_not_an_index(lint_wiki, tmp_path, capsys):
    """Il verso in cui questo controllo deve sbagliare: **tacere**. Un bullet di
    istruzioni che nomina un file (`wiki/index.md`, `raw/journal/`) non è una
    voce d'indice, e venti righe così sono un `AGENTS.md` fatto bene.
    """
    root = _notebook(tmp_path)
    _agents_md(root, "".join(
        f"- Regola numero {i:02d}: prima di scrivere una pagina rileggi il file "
        "wiki/index.md e il diario del giorno\n"
        for i in range(20)
    ))
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "carries a page list" not in out


def test_a_template_shown_in_a_fence_is_not_an_index(lint_wiki, tmp_path, capsys):
    """``references/schema-guide.md`` mostra il template intero dentro un blocco
    recintato, elenco compreso: chi se lo incolla dentro sta *mostrando* una
    forma, non dichiarando venti pagine. Stessa regola di ``extract_wikilinks``.
    """
    root = _notebook(tmp_path)
    _agents_md(root, "```markdown\n" + _page_list(20) + "```\n")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "carries a page list" not in out


def test_the_placeholders_a_scaffolder_writes_do_not_count(lint_wiki, tmp_path, capsys):
    """L'`AGENTS.md` appena scaffoldato ha già le sue righe d'esempio
    (``- [[<Concept Title>]] — one-line summary``) e non ha ancora nessuna
    pagina. Segnalarlo alla nascita insegnerebbe a ignorare il passo prima che
    abbia mai avuto ragione.
    """
    root = _notebook(tmp_path)
    _agents_md(root, "## Current articles\n\n" + "".join(
        f"- [[<Concept Title {i:02d}>]] — one-line summary of what this page holds\n"
        for i in range(20)
    ))
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "carries a page list" not in out


def test_the_ceiling_is_derived_from_the_map_ceiling(lint_wiki):
    """Il numero non è scelto: è metà di quel che l'iniettore concede all'indice
    **vero**. Scritto come test perché è l'unico posto in cui si vede che i due
    numeri sono una famiglia — la stessa disciplina della coppia di T3.12.
    """
    assert lint_wiki.AGENTS_LIST_MAX_CHARS == lint_wiki.MAP_MAX_CHARS // 2


# ── Passo 19: di chi sono le parole su cui una pagina si dichiara decisa ─────
#
# Il difetto (D1): il 24/08 una pagina è nata `state: decided` su un fatto che
# l'utente non aveva detto — era l'opzione B di una domanda che l'assistente
# aveva fatto lui — ed è finita sotto «Decided» nella mappa, che entra in ogni
# turno. La guardia in `gardener._provenance_guard` chiude il lato scrittura;
# questo passo è l'unico che raggiunge quel che è **già** sul disco.


def _attributed_notebook(root: Path, *, state: str, anchor: str, marked: str) -> Path:
    """Un taccuino la cui unica pagina decide su una riga marcata *marked*.

    ``marked=""`` è una riga com'era prima che i marcatori esistessero.
    """
    _notebook(root, state=None, source=None)
    (root / "raw" / "journal" / "20260823.md").write_text(
        "# 2026-08-23\n\n"
        f"- 09:12 — {marked + ' ' if marked else ''}Si semina a maggio.\n",
        encoding="utf-8",
    )
    (root / "wiki" / "semine.md").write_text(
        f"---\nstate: {state}\nsource: raw/journal/20260823.md{anchor}\n---\n\n"
        "# Semine\n\nA maggio. Vedi [[terreno]].\n",
        encoding="utf-8",
    )
    return root


def test_a_decision_on_an_inferred_line_is_red(lint_wiki, tmp_path, capsys):
    """🔴 perché la pagina **dice una cosa falsa**: il diario stesso dice che
    l'ha concluso l'assistente, quindi l'utente quella cosa non l'ha decisa. È la
    riga di confine del passo 17 applicata qui — rosso per il contenuto che
    inganna, giallo per quello che non si può verificare."""
    _attributed_notebook(tmp_path, state="decided", anchor="#09:12", marked="[inferred]")
    lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out

    assert "🔴 Pages claiming a decision the journal attributes to the assistant (1)" in out
    assert "wiki/semine.md" in out


def test_a_decision_on_a_said_line_is_quiet(lint_wiki, tmp_path, capsys):
    """Il test che tiene onesto il precedente: senza, «rifiuta tutto» passerebbe.

    È l'errore in cui è caduta la terza variante scartata del disegno, che
    bocciava la fabbricazione **e** la decisione vera, perché parafrasata.
    """
    _attributed_notebook(tmp_path, state="decided", anchor="#09:12", marked="[said]")
    lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out

    assert "claiming a decision" not in out


def test_a_recovered_line_is_quiet_too(lint_wiki, tmp_path, capsys):
    """Una passata recupera solo fatti che l'utente ha detto: è il contratto del
    suo prompt, non una scelta che le si concede."""
    _attributed_notebook(tmp_path, state="decided", anchor="#09:12", marked="[recovered]")
    lint_wiki.lint(str(tmp_path))

    assert "claiming a decision" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("anchor", "marked", "case"),
    [
        ("", "[said]", "nessuna ora: la source punta a N righe"),
        ("#23:59", "[said]", "un'ora che nel file non c'è"),
        ("#09:12", "", "una riga scritta prima che i marcatori esistessero"),
    ],
)
def test_what_cannot_be_attributed_is_yellow(lint_wiki, tmp_path, capsys, anchor, marked, case):
    """🟡 e non 🔴: inverificabile, non falso.

    È anche la ragione di campo che il passo 17 spiega — le pagine nate prima dei
    marcatori non ce l'hanno, e un 🔴 su ognuna trasformerebbe otto wiki sane in
    muri rossi, che è il modo più rapido di far ignorare un lint.
    """
    _attributed_notebook(tmp_path, state="decided", anchor=anchor, marked=marked)
    lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out

    assert "🟡 Pages claiming a decision on a line nobody attributed (1)" in out, case
    assert "🔴 Pages claiming a decision" not in out, case


@pytest.mark.parametrize("state", ["open", "hypothesis"])
def test_a_page_that_claims_nothing_is_not_asked_whose_words(lint_wiki, tmp_path, capsys, state):
    _attributed_notebook(tmp_path, state=state, anchor="", marked="")
    lint_wiki.lint(str(tmp_path))

    assert "claiming a decision" not in capsys.readouterr().out


def test_the_anchor_is_not_part_of_the_path(lint_wiki, tmp_path, capsys):
    """La regressione che il passo 19 introdurrebbe se il passo 17 non lo sapesse:
    `raw/journal/20260823.md#09:12` non esiste **come percorso**, quindi senza il
    taglio del frammento ogni pagina ancorata bene finirebbe fra le `source:` che
    non portano da nessuna parte — la modifica che rende la provenienza
    verificabile diventerebbe un muro giallo."""
    _attributed_notebook(tmp_path, state="decided", anchor="#09:12", marked="[said]")
    lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out

    assert "names a file that is not there" not in out


# ── passo 12: il vocabolario delle operazioni del `log/` ─────────────────────
#
# **D14.** Il passo sul `log/` ha un elenco chiuso di operazioni e boccia quel che
# non ci sta. Ma due scrittori legittimi non c'erano: il giardiniere, che la sua
# riga la scrive **da codice**, e `reindex`, che la tabella della guida autorizza.
# Su ogni progetto con una passata alle spalle il risultato era un 🟡 permanente
# che non segnalava niente di vero — e il danno di un controllo così non è il
# falso allarme, è che insegna a scorrere l'elenco senza leggerlo.
#
# I due test qui sotto non riscrivono l'elenco: lo confrontano con le **due
# fonti** che lo devono contenere, cioè il codice che scrive e il documento che
# autorizza. Un elenco riscritto a mano nel test resterebbe verde il giorno che
# uno dei due cambia, che è precisamente come questo difetto è arrivato fino a
# qui.


def test_the_op_the_gardener_actually_writes_is_a_known_op(lint_wiki, tmp_path):
    """L'operazione la prende da ``log_pass``, non da un letterale.

    Guardare la riga vera è il punto: il difetto non era «manca la parola
    *gardener*», era che nessuno aveva confrontato l'unico scrittore che non passa
    da un prompt con il controllo che lo giudica. Un test con la stringa scritta a
    mano proverebbe l'accordo fra sé stesso e l'elenco, non fra l'elenco e il
    codice.
    """
    from jenny.agent.gardener import GardenerStore
    from jenny.agent.gardener_state import JournalDelta

    project = tmp_path / "wikis" / "casa"
    (project / "wiki").mkdir(parents=True)
    (project / "wiki" / "index.md").write_text("# Casa\n", encoding="utf-8")
    store = GardenerStore.for_project(tmp_path, "casa")
    assert store is not None

    store.log_pass(JournalDelta(), elapsed=1.0, writes=1, timezone="Europe/Rome")

    logs = sorted((project / "log").glob("*.md"))
    assert logs, "senza riga scritta questo test non prova niente"
    ops = [
        match.group(1)
        for line in logs[0].read_text(encoding="utf-8").splitlines()
        if (match := lint_wiki.LOG_ENTRY_RE.match(line))
    ]
    assert ops, "la riga della passata deve avere la forma che il passo 12 riconosce"
    assert set(ops) <= lint_wiki.VALID_LOG_OPS


def test_every_op_the_guide_authorises_is_a_known_op(lint_wiki):
    """Fra un riferimento che autorizza e un controllo che boccia, vince il primo.

    ``references/log-guide.md`` è il documento da cui il modello copia la forma
    della riga prima di scriverla: un'operazione elencata **là** e assente **qui**
    è un 🟡 su una riga scritta seguendo le istruzioni. Era il caso di
    ``reindex``, che col giardiniere non c'entra niente — trovato di lato, stessa
    classe.
    """
    guide = _SCRIPTS_DIR.parent / "references" / "log-guide.md"
    table = guide.read_text(encoding="utf-8").split("## Ops allowed in the log", 1)[1]
    documented = set(re.findall(r"^\|\s*`(\w+)`", table.split("\n## ", 1)[0], re.MULTILINE))

    assert len(documented) >= 8, "la tabella non è stata letta: l'asserzione sotto sarebbe vuota"
    assert documented <= lint_wiki.VALID_LOG_OPS


def test_an_op_nobody_authorised_is_still_flagged(lint_wiki, tmp_path, capsys):
    """La metà negativa, e mancava: allargare un elenco non deve poter svuotarlo.

    Trovata da una mutazione — neutralizzato il confronto del passo 12
    (``not in VALID_LOG_OPS`` → mai vero) i due test qui sopra restavano **verdi**,
    perché asseriscono entrambi che certe operazioni *passano*. Cioè misuravano il
    vocabolario e non il controllo: la stessa forma di difetto che la correzione
    D14 ripara, un piano più in su.
    """
    _notebook(tmp_path)
    (tmp_path / "log" / "20260823.md").write_text(
        "# 2026-08-23\n\n## [09:12] frobnicate | qualcosa\n", encoding="utf-8"
    )
    lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out

    assert "unknown op 'frobnicate'" in out


# ── passo 19: la stessa domanda, due implementazioni ─────────────────────────
#
# **D13.** L'ancoraggio di `source:` è al minuto, e un minuto può tenere più righe.
# Prima del 25/08 le due implementazioni della stessa domanda — «di chi è la riga
# che questa pagina cita?» — sbagliavano entrambe e **in modi opposti**: la guardia
# di `gardener.py` leggeva la prima riga del minuto, questo lint teneva un
# dizionario `minuto → marcatore` e quindi leggeva l'ultima. Due copie dello stesso
# difetto che non erano nemmeno d'accordo su quale riga guardare, cioè una pagina
# potevano giudicarla in modo diverso.
#
# Questo script non importa `jenny` (gira anche fuori dall'app), quindi il codice
# resta duplicato per forza. Quel che non deve restare duplicato è il *giudizio*: il
# test qui sotto fa girare gli stessi casi nelle due implementazioni e chiede che
# arrivino alla stessa conclusione. È la stessa forma di
# `test_the_ceiling_matches_the_one_the_prompt_uses`, applicata a un
# comportamento invece che a un numero.


_MIXED_JOURNAL = (
    "# 2026-08-24\n"
    "\n"
    "- 09:12 — [said] Il furgone è un Ducato.\n"
    "- 09:12 — [inferred] Quindi il letto sta in fondo.\n"
    "- 09:13 — [said] Si parte il 12.\n"
    "- 09:13 — [recovered] Rientro il 20.\n"
    "- 09:14 — [inferred] Meglio partire di notte.\n"
)


@pytest.mark.parametrize(
    ("anchor", "said", "case"),
    [
        ("#09:12", False, "minuto misto: non si sa quale riga"),
        ("#09:12.1", True, "l'ordinale indica la riga detta"),
        ("#09:12.2", False, "l'ordinale indica la riga dedotta"),
        ("#09:13", True, "due righe, entrambe dell'utente: il minuto basta"),
        ("#09:14", False, "una riga sola, dedotta"),
        ("#09:12.3", False, "ordinale fuori range"),
        ("#09:12.0", False, "gli ordinali contano da 1"),
        ("#23:59", False, "un minuto che nel file non c'è"),
        ("", False, "nessun ancoraggio"),
    ],
)
def test_the_lint_and_the_gardener_read_the_same_line(lint_wiki, tmp_path, anchor, said, case):
    """Stessa domanda, stessa risposta — misurata, non asserita due volte a mano.

    ``said`` è l'unico esito che *passa*: sotto ci sono quattro modi di non sapere
    e la severità li distingue nel messaggio, non nel verdetto. Il test confronta
    quindi la sola cosa su cui le due implementazioni non possono divergere.
    """
    # Il gemello vive in ``wiki_provenance`` dal 26/08 (estratto da ``gardener``
    # perché ora lo monta anche ``_FsTool``): il giardiniere resta un suo
    # consumatore, non più la sua casa.
    from jenny.agent.wiki_provenance import _journal_line_provenance

    (tmp_path / "raw" / "journal").mkdir(parents=True)
    (tmp_path / "raw" / "journal" / "20260824.md").write_text(_MIXED_JOURNAL, encoding="utf-8")
    source = f"raw/journal/20260824.md{anchor}"

    gardener = _journal_line_provenance(tmp_path.resolve(), source) == "said"
    _, _, only_anchor = source.partition("#")
    marker = lint_wiki._journal_line_marker(
        tmp_path, "raw/journal/20260824.md", only_anchor, {}
    )
    lint = marker in lint_wiki._SAID_MARKERS

    assert gardener is said, f"gardener.py: {case}"
    assert lint is said, f"lint_wiki.py: {case}"


def test_a_mixed_minute_is_yellow_and_says_what_to_add(lint_wiki, tmp_path, capsys):
    """🟡 con la frase giusta: il minuto è ambiguo, non introvabile.

    Il primo taglio di questa correzione riportava un minuto misto come «line not
    found» — mandava a cercare una riga che c'è, cioè un avviso su cui non si può
    agire. Il difetto vero è che l'ancoraggio è **incompleto**, e la riparazione è
    un'aggiunta.
    """
    _notebook(tmp_path, state=None, source=None)
    (tmp_path / "raw" / "journal" / "20260824.md").write_text(_MIXED_JOURNAL, encoding="utf-8")
    (tmp_path / "wiki" / "semine.md").write_text(
        "---\nstate: decided\nsource: raw/journal/20260824.md#09:12\n---\n\n"
        "# Semine\n\nA maggio. Vedi [[terreno]].\n",
        encoding="utf-8",
    )
    lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out

    assert "🟡 Pages claiming a decision on a line nobody attributed (1)" in out
    assert "#HH:MM.2" in out
    assert "line not found" not in out


# ── passo 19, il lato muto: chi non potrà mai dichiararsi deciso ──────────────
#
# Il passo 19 guarda chi *si dichiara* deciso. Il 25/08 il caso di campo era
# l'opposto: `wikis/viaggio-pazzo/wiki/viaggio-pazzo.md`, a `open`, ancorata al
# **giorno intero** del 24/08 — e le righe di quel giorno sono anteriori ai
# marcatori. Quella pagina non potrà mai essere marcata `decided`: la guardia in
# scrittura la rifiuterebbe. Ma la guardia parla solo quando una passata *prova* a
# promuovere, e in due giorni di lavoro nessuno ci ha provato — quindi il tetto è
# rimasto invisibile, mentre l'utente si chiedeva perché la wiki non si muovesse.
#
# Il rischio di questo controllo è di diventare il muro che questo file evita per
# principio: su una wiki scritta prima dei marcatori *quasi ogni* pagina a `open`
# ricade qui. Da cui la divisione — si **nomina** quel che si ripara editando
# `source:`, si **conta** quel che non si ripara affatto — che è la proprietà
# vera di questa correzione e ha un test suo.


_MARKED_JOURNAL = (
    "# 2026-08-24\n"
    "\n"
    "- 09:12 — [said] Il furgone è un Ducato.\n"
    "- 09:13 — [said] Si parte il 12.\n"
)


def test_a_page_capped_by_a_fixable_source_is_named(lint_wiki, tmp_path, capsys):
    """Manca l'ora, ma il giorno ha marcatori da leggere: si ripara, quindi si nomina."""
    root = _notebook(tmp_path, source="raw/journal/20260824.md")
    (root / "raw" / "journal" / "20260824.md").write_text(_MARKED_JOURNAL, encoding="utf-8")

    out = _run(lint_wiki, root, capsys)

    assert "whose `source:` can be fixed (2)" in out
    assert "wiki/semine.md (state: open)" in out
    assert "no #time" in out


def test_a_page_capped_by_history_is_counted_and_not_named(lint_wiki, tmp_path, capsys):
    """Nessuna riga di quel giorno è attribuibile: aggiungere l'ora non cambia nulla.

    Nominarle sarebbe mandare a una riparazione che non esiste, una pagina alla
    volta, su tutta la wiki. Il conteggio dice che la situazione c'è; l'elenco
    resta per chi può agire.
    """
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "2 more page(s) rest on a journal line that cannot be attributed" in out
    assert "wiki/semine.md (state: open)" not in out
    assert "can be fixed" not in out


def test_a_page_anchored_at_a_said_line_is_not_reported_at_all(lint_wiki, tmp_path, capsys):
    """Il contro-limite: una ``source:`` che *regge* un ``decided`` non è un tetto.

    Senza questa asserzione il controllo potrebbe elencare ogni pagina a ``open``
    del mondo e i due test qui sopra resterebbero verdi.
    """
    root = _notebook(tmp_path, source="raw/journal/20260824.md#09:12")
    (root / "raw" / "journal" / "20260824.md").write_text(_MARKED_JOURNAL, encoding="utf-8")

    out = _run(lint_wiki, root, capsys)

    assert "can be fixed" not in out
    assert "cannot be attributed" not in out


def test_a_page_sourced_at_a_document_is_counted_apart_and_never_asked_for_a_time(
    lint_wiki, tmp_path, capsys
):
    """**Il difetto del 26/08, e la ragione del terzo conteggio.**

    Misurato su ``wikis/salute`` vero: cinque pagine su cinque con
    ``source: raw/research/<documento>.md`` — la forma che ``project.md`` chiede
    per il materiale che arriva da fuori — e il lint le mandava tutte e cinque ad
    «aggiungi un ``#HH:MM``», *nominate fra le riparabili*. Su un documento quel
    minuto non esiste e non esisterà: l'avviso mandava a una riparazione
    impossibile, che è esattamente quel che il resto di questo passo evita.

    Tre asserzioni, e servono tutte e tre: che non chieda l'ora, che non finisca
    fra le riparabili, e che **non finisca nemmeno nel conteggio della storia** —
    quella frase parla di righe anteriori ai marcatori e manderebbe a cercare
    pagine vecchie, dove non c'è niente di vecchio.
    """
    root = _notebook(tmp_path, source="raw/research/documento.md")
    (root / "raw" / "research").mkdir(parents=True)
    (root / "raw" / "research" / "documento.md").write_text(
        "# Documento\n\nCopiato verbatim da fuori.\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    rc = lint_wiki.lint(str(root))
    out = capsys.readouterr().out

    assert "2 more page(s) rest on a document copied into `raw/`" in out
    assert "#HH:MM" not in out
    assert "can be fixed" not in out
    assert "cannot be attributed" not in out
    # Il codice d'uscita, come per il gemello qui sotto: è l'unica cosa che una
    # mutazione ``issues += capped_by_document`` non può lasciare verde, e una
    # fonte documentale è la forma *prescritta*, non un difetto.
    assert rc == 0
    assert "Wiki is healthy" in out


def test_an_anchor_written_on_a_document_is_the_same_finding(lint_wiki, tmp_path, capsys):
    """Il corollario: l'ancora **c'è** ma il file non ha righe di diario.

    Prima usciva come «that line is not in the journal — check the anchor»,
    riparabile: mandava a correggere un'ancora che non ha nessun minuto da
    indovinare. È lo stesso fatto del test sopra, e senza questa asserzione la
    correzione poteva chiudere solo il ramo senza ancora.
    """
    root = _notebook(tmp_path, source="raw/research/documento.md#09:12")
    (root / "raw" / "research").mkdir(parents=True)
    (root / "raw" / "research" / "documento.md").write_text(
        "# Documento\n\nCopiato verbatim da fuori.\n", encoding="utf-8"
    )

    out = _run(lint_wiki, root, capsys)

    assert "rest on a document copied into `raw/`" in out
    assert "check the anchor" not in out


def test_a_capped_page_does_not_make_the_wiki_unhealthy(lint_wiki, tmp_path, capsys):
    """**La proprietà, e il motivo per cui è ℹ️ e non 🟡.**

    ``open`` su una fonte debole non è un difetto: è il valore giusto. Contarlo
    fra i problemi trasformerebbe ogni wiki nata prima dei marcatori in un muro,
    che questo file chiama «il modo più rapido di far ignorare un lint». Il test
    guarda il **codice d'uscita**, non la stampa: è l'unica cosa che una
    mutazione (``issues += len(capped)``) non può lasciare verde.
    """
    root = _notebook(tmp_path, source="raw/journal/20260824.md")
    (root / "raw" / "journal" / "20260824.md").write_text(_MARKED_JOURNAL, encoding="utf-8")

    rc = lint_wiki.lint(str(root))
    out = capsys.readouterr().out

    assert "can be fixed (2)" in out, "il caso deve essersi presentato davvero"
    assert rc == 0, "un tetto informativo non rende la wiki malata"
    assert "Wiki is healthy" in out
