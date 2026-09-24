"""Il motore di ricerca del client, eseguito davvero contro l'indice del server.

Compagno di ``test_wiki_search.py``, che verifica solo il lato Python. Qui il
formato impacchettato fa il giro completo: lo costruisce ``wiki_search.py``, lo
smonta ``assets/shared/wiki-search.js`` sotto node, e le due metà devono
raccontare la stessa cosa.

È l'unico posto dove si nota la classe di bug più insidiosa di questa
funzionalità: i due tokenizzatori che divergono. Sono due implementazioni della
stessa regola in due linguaggi — NFKD, via i diacritici, minuscolo,
``[0-9a-z]+`` — e quando smettono di coincidere non si rompe niente in modo
visibile: il client cerca semplicemente termini che il server non ha mai
scritto nel dizionario, e la ricerca "non trova" senza dire perché.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

from jenny.webui.wiki import build_graph, read_pages
from jenny.webui.wiki_search import SearchIndex, pack_index, tokenize

SEARCH_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "wiki-search.js"
)


pytestmark = requires_node


def _run_js(script: str) -> str:
    source = SEARCH_JS.read_text(encoding="utf-8") + "\nimport assert from 'node:assert/strict';\n" + script
    return run_js(source)


def _wire(tmp_path: Path, pages: dict[str, str]) -> tuple[dict, list[str]]:
    """Costruisce una wiki, la indicizza, e ritorna ``(wire, id dei nodi)``."""
    pages_dir = tmp_path / "wikis" / "main" / "wiki"
    for rel, content in pages.items():
        full = pages_dir / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    docs = read_pages(pages_dir)
    graph = build_graph(pages_dir.parent)
    wire = pack_index(SearchIndex.from_pages(docs, graph), "v1")
    return wire, [n.id for n in graph.nodes]


def _query_js(wire: dict, queries: list[str]) -> list[dict]:
    """Esegue le query nel motore JS e ritorna ``[{count, nodes}]`` per ognuna."""
    script = f"""
      const wire = {json.dumps(wire)};
      const queries = {json.dumps(queries)};
      const index = WikiSearchIndex.from(wire);
      const out = queries.map(q => {{
        const r = index.query(q);
        if (!r) return null;  // nessun vincolo
        const nodes = [];
        for (let i = 0; i < r.mask.length; i++) if (r.mask[i]) nodes.push(i);
        return {{ count: r.count, nodes }};
      }});
      console.log(JSON.stringify(out));
    """
    return json.loads(_run_js(script))


# ── Parità dei tokenizzatori ────────────────────────────────────────────────


def test_the_two_tokenizers_agree() -> None:
    samples = [
        "Però l'anno è finito",
        "Doze-mode e wake_lock",
        "CITTÀ, perché? Così!",
        "Android 14 / API 34",
        "  spazi   multipli  ",
        "già è ù ò à ì",
        "e-mail non_ascii… fine.",
        "",
        "UI/UX",
    ]
    expected = [tokenize(s) for s in samples]
    script = f"""
      const samples = {json.dumps(samples)};
      // Il client applica il filtro sulla lunghezza minima dentro query();
      // qui si confronta il tokenizzatore nudo, con lo stesso filtro a valle.
      const got = samples.map(s => tokenize(s).filter(t => t.length >= 2));
      console.log(JSON.stringify(got));
    """
    assert json.loads(_run_js(script)) == expected


def test_folding_matches_the_server() -> None:
    from jenny.webui.wiki_search import fold

    samples = ["Perché", "CITTÀ", "Straße", "ÅNGSTRÖM", "naïve"]
    expected = [fold(s) for s in samples]
    script = f"""
      console.log(JSON.stringify({json.dumps(samples)}.map(foldText)));
    """
    assert json.loads(_run_js(script)) == expected


# ── Pipeline completa ───────────────────────────────────────────────────────


def test_a_word_lights_exactly_the_pages_that_contain_it(tmp_path: Path) -> None:
    wire, ids = _wire(tmp_path, {
        "doze.md": "# Doze\n\nil sonno profondo di Android",
        "wakelock.md": "# Wakelock\n\ntenere sveglia la CPU",
        "cron.md": "# Cron\n\npianificazione dei lavori",
    })
    (result,) = _query_js(wire, ["sonno "])
    assert result["nodes"] == [ids.index("wiki/doze.md")]
    assert result["count"] == 1


def test_accents_do_not_have_to_be_typed(tmp_path: Path) -> None:
    wire, ids = _wire(tmp_path, {
        "a.md": "# Città\n\nun testo qualunque",
        "b.md": "# Altro\n\nun testo qualunque",
    })
    (result,) = _query_js(wire, ["citta "])
    assert result["nodes"] == [ids.index("wiki/a.md")]


def test_the_last_word_matches_by_prefix_while_typing(tmp_path: Path) -> None:
    # È ciò che fa accendere i nodi *mentre* si scrive, senza aspettare la
    # parola intera: ogni tappa di "illuminazione" deve trovarla.
    wire, ids = _wire(tmp_path, {
        "luce.md": "# Luce\n\nilluminazione della stanza",
        "buio.md": "# Buio\n\nassenza di luce",
    })
    target = ids.index("wiki/luce.md")
    for prefix in ["il", "ill", "illum", "illuminazione"]:
        (result,) = _query_js(wire, [prefix])
        assert result["nodes"] == [target], f"prefisso '{prefix}'"


def test_a_trailing_space_makes_the_last_word_exact(tmp_path: Path) -> None:
    wire, ids = _wire(tmp_path, {
        "a.md": "# A\n\ncasa",
        "b.md": "# B\n\ncasalinga",
    })
    (as_prefix, as_exact) = _query_js(wire, ["casa", "casa "])
    assert sorted(as_prefix["nodes"]) == sorted([ids.index("wiki/a.md"), ids.index("wiki/b.md")])
    assert as_exact["nodes"] == [ids.index("wiki/a.md")]


def test_multiple_words_are_an_intersection(tmp_path: Path) -> None:
    wire, ids = _wire(tmp_path, {
        "a.md": "# A\n\nalfa beta",
        "b.md": "# B\n\nalfa gamma",
        "c.md": "# C\n\nbeta gamma",
    })
    (result,) = _query_js(wire, ["alfa beta "])
    assert result["nodes"] == [ids.index("wiki/a.md")]


def test_an_unknown_word_finds_nothing_and_says_so(tmp_path: Path) -> None:
    # Zero risultati e "nessun vincolo" sono stati diversi: il primo spegne
    # tutto il grafo, il secondo lo lascia acceso. Confonderli fa sembrare
    # rotta la ricerca.
    wire, _ids = _wire(tmp_path, {"a.md": "# A\n\ncontenuto"})
    (result,) = _query_js(wire, ["inesistente "])
    assert result is not None
    assert result["count"] == 0
    assert result["nodes"] == []


def test_an_empty_query_imposes_no_constraint(tmp_path: Path) -> None:
    wire, _ids = _wire(tmp_path, {"a.md": "# A\n\ncontenuto"})
    assert _query_js(wire, ["", "   ", "!!!"]) == [None, None, None]


def test_a_word_present_everywhere_imposes_no_constraint(tmp_path: Path) -> None:
    # Il server omette le postings dei termini quasi-universali; il client deve
    # leggere quel range vuoto come "non filtra", non come "zero risultati".
    pages = {f"p{i:03d}.md": f"# Pagina {i}\n\nubiquo specifico{i}" for i in range(50)}
    wire, _ids = _wire(tmp_path, pages)
    assert wire["universal"] >= 1
    (universal, mixed) = _query_js(wire, ["ubiquo ", "ubiquo specifico7 "])
    assert universal is None, "un termine ovunque non deve spegnere niente"
    # In una congiunzione il termine universale sparisce e restano gli altri.
    assert mixed["count"] == 1


def test_postings_stay_inside_the_node_range(tmp_path: Path) -> None:
    # L'invariante che tiene insieme indice e disegno: la maschera è lunga
    # quanto l'array dei nodi, e ogni bit acceso è un nodo che esiste.
    pages = {f"p{i}.md": f"# P{i}\n\ncomune parola{i}" for i in range(12)}
    wire, ids = _wire(tmp_path, pages)
    script = f"""
      const index = WikiSearchIndex.from({json.dumps(wire)});
      const r = index.query('comune ');
      assert.equal(r.mask.length, {len(ids)});
      assert.equal(r.count, {len(ids)});
      console.log('ok');
    """
    assert _run_js(script).strip() == "ok"


def test_scores_rank_a_title_hit_above_a_body_hit(tmp_path: Path) -> None:
    wire, ids = _wire(tmp_path, {
        "titolo.md": "# Batteria\n\ntesto neutro",
        "corpo.md": "# Altro\n\nqui si parla di batteria in fondo",
    })
    script = f"""
      const index = WikiSearchIndex.from({json.dumps(wire)});
      const r = index.query('batteria ');
      console.log(JSON.stringify(Array.from(r.scores)));
    """
    scores = json.loads(_run_js(script))
    assert scores[ids.index("wiki/titolo.md")] > scores[ids.index("wiki/corpo.md")]


def test_the_result_buffer_is_reused_across_queries(tmp_path: Path) -> None:
    # Il motore non deve allocare per keystroke: è ciò che evita la GC mentre
    # si scrive. Il rovescio della medaglia — la maschera vale fino alla query
    # dopo — è contrattuale, e questo test lo fissa.
    wire, _ids = _wire(tmp_path, {"a.md": "# A\n\nalfa", "b.md": "# B\n\nbeta"})
    script = f"""
      const index = WikiSearchIndex.from({json.dumps(wire)});
      const first = index.query('alfa ');
      const second = index.query('beta ');
      assert.equal(first.mask, second.mask, 'il buffer va riusato, non riallocato');
      console.log('ok');
    """
    assert _run_js(script).strip() == "ok"
