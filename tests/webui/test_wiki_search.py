"""Indice full-text della wiki: tokenizzazione, impacchettamento, cache.

Il contratto che questi test difendono non è "la ricerca trova le cose" — è che
il *formato* spedito al client sia decodificabile alla lettera e che le sue
postings puntino ai nodi giusti dello stesso grafo servito nella stessa
risposta. Una deriva lì non fallisce da nessuna parte: accende semplicemente i
nodi sbagliati.
"""

from __future__ import annotations

import base64
import json
import struct
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.wiki import build_graph, read_pages
from jenny.webui.wiki_search import (
    SearchIndex,
    WikiSearchService,
    fingerprint,
    fold,
    pack_index,
    tokenize,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def wikis_dir(tmp_path: Path) -> Path:
    d = tmp_path / "wikis"
    d.mkdir()
    return d


def _make_wiki(wikis_dir: Path, name: str, pages: dict[str, str]) -> Path:
    """Crea una wiki e ritorna la sua pages-dir ``wikis/<name>/wiki``."""
    pages_dir = wikis_dir / name / "wiki"
    for rel, content in pages.items():
        full = pages_dir / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return pages_dir


def _index_for(pages_dir: Path):
    pages = read_pages(pages_dir)
    graph = build_graph(pages_dir.parent)
    return pages, graph, SearchIndex.from_pages(pages, graph)


def _unpack(wire: dict) -> dict[str, list[int]]:
    """Rilegge il formato packed come lo farebbe il client, per termine."""
    terms = wire["terms"].split("\n") if wire["terms"] else []
    offsets = _int32(wire["offsets"])
    postings = _postings(wire)
    return {t: postings[offsets[i] : offsets[i + 1]] for i, t in enumerate(terms)}


def _int32(b64: str) -> list[int]:
    raw = base64.b64decode(b64)
    return list(struct.unpack(f"<{len(raw) // 4}i", raw))


def _postings(wire: dict) -> list[int]:
    raw = base64.b64decode(wire["postings"])
    fmt, width = ("H", 2) if wire["bits"] == 16 else ("i", 4)
    return list(struct.unpack(f"<{len(raw) // width}{fmt}", raw))


def _uint8(b64: str) -> list[int]:
    return list(base64.b64decode(b64))


# ── Tokenizzazione ──────────────────────────────────────────────────────────


class TestTokenize:
    def test_folds_accents(self):
        # "però" e "pero" devono cadere sullo stesso termine: cercare senza
        # accento è la norma su una tastiera del telefono.
        assert tokenize("Però") == ["pero"]
        assert fold("Città È Perché") == "citta e perche"

    def test_splits_on_punctuation_and_underscore(self):
        assert tokenize("doze-mode, wake_lock!") == ["doze", "mode", "wake", "lock"]

    def test_drops_single_char_tokens(self):
        # Token da un carattere: quasi solo articoli, gonfiano il dizionario
        # senza distinguere niente.
        assert tokenize("a e i o u ok") == ["ok"]

    def test_keeps_digits(self):
        assert tokenize("Android 14 API 34") == ["android", "14", "api", "34"]

    def test_empty_text(self):
        assert tokenize("") == []


# ── Costruzione dell'indice ─────────────────────────────────────────────────


class TestSearchIndex:
    def test_postings_are_node_indices(self, wikis_dir: Path):
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {
                "alpha.md": "# Alpha\n\nparola unica alfa",
                "beta.md": "# Beta\n\nparola unica beta",
            },
        )
        _pages, graph, index = _index_for(pages_dir)
        ids = [n.id for n in graph.nodes]
        assert index.postings["alfa"] == {ids.index("wiki/alpha.md"): 1}
        assert set(index.postings["parola"]) == {0, 1}

    def test_title_weighs_more_than_body(self, wikis_dir: Path):
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {
                "a.md": "# Doze\n\ntesto neutro",
                "b.md": "# Altro\n\nqui si parla di doze in fondo",
            },
        )
        _pages, graph, index = _index_for(pages_dir)
        ids = [n.id for n in graph.nodes]
        weights = index.postings["doze"]
        assert weights[ids.index("wiki/a.md")] > weights[ids.index("wiki/b.md")]

    def test_path_is_searchable(self, wikis_dir: Path):
        # Il nome del file conta: una pagina si cerca anche col suo path,
        # non solo col titolo che l'agente le ha messo.
        pages_dir = _make_wiki(
            wikis_dir, "main", {"concepts/doze-mode.md": "# Sonno profondo\n\ntesto"}
        )
        _pages, _graph, index = _index_for(pages_dir)
        assert 0 in index.postings["doze"]
        assert 0 in index.postings["concepts"]

    def test_frontmatter_tags_are_indexed_but_keys_are_not(self, wikis_dir: Path):
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {"a.md": "---\ntitle: Pagina\ntags: [energia, batteria]\ncreated: 2026-01-01\n---\n\ncorpo"},
        )
        _pages, _graph, index = _index_for(pages_dir)
        assert 0 in index.postings["energia"]
        assert 0 in index.postings["batteria"]
        # Le *chiavi* del frontmatter comparirebbero in ogni pagina: sarebbero
        # rumore che matcha tutto.
        assert "created" not in index.postings
        assert "tags" not in index.postings

    def test_repeated_occurrences_do_not_inflate_weight(self, wikis_dir: Path):
        # Una pagina prolissa non deve battere una pagina pertinente solo
        # perché ripete la parola.
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {
                "corta.md": "# Batteria\n\nbatteria",
                "lunga.md": "# Altro\n\n" + "batteria " * 200,
            },
        )
        _pages, graph, index = _index_for(pages_dir)
        ids = [n.id for n in graph.nodes]
        weights = index.postings["batteria"]
        assert weights[ids.index("wiki/corta.md")] > weights[ids.index("wiki/lunga.md")]

    def test_summaries_are_not_indexed(self, wikis_dir: Path):
        # Fuori dal grafo → fuori dalla ricerca: un risultato senza nodo da
        # accendere non è un risultato.
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {"index.md": "# Home", "summaries/src.md": "# Src\n\ntermineraro"},
        )
        _pages, _graph, index = _index_for(pages_dir)
        assert "termineraro" not in index.postings

    def test_doc_count_matches_graph_nodes(self, wikis_dir: Path):
        pages_dir = _make_wiki(
            wikis_dir, "main", {"a.md": "# A", "b.md": "# B", "sub/c.md": "# C"}
        )
        _pages, graph, index = _index_for(pages_dir)
        assert index.doc_count == len(graph.nodes) == 3


# ── Formato di trasporto ────────────────────────────────────────────────────


class TestPackIndex:
    def test_roundtrip(self, wikis_dir: Path):
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {"a.md": "# Alfa\n\nuno due", "b.md": "# Beta\n\ndue tre"},
        )
        _pages, graph, index = _index_for(pages_dir)
        wire = pack_index(index, "v1")

        by_term = _unpack(wire)
        ids = [n.id for n in graph.nodes]
        assert by_term["due"] == [0, 1]
        assert by_term["tre"] == [ids.index("wiki/b.md")]
        assert wire["docs"] == 2
        assert wire["version"] == "v1"

    def test_terms_are_sorted(self, wikis_dir: Path):
        # L'ordinamento non è cosmetico: il client cerca il range di prefisso
        # per bisezione, e senza ordine la ricerca incrementale non esiste.
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# Z\n\nzulu alfa mike"})
        _pages, _graph, index = _index_for(pages_dir)
        terms = pack_index(index, "v1")["terms"].split("\n")
        assert terms == sorted(terms)

    def test_offsets_have_one_extra_entry(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A\n\nuno due tre"})
        _pages, _graph, index = _index_for(pages_dir)
        wire = pack_index(index, "v1")
        offsets = _int32(wire["offsets"])
        terms = wire["terms"].split("\n")
        assert len(offsets) == len(terms) + 1
        assert offsets[0] == 0
        assert offsets[-1] == len(_postings(wire))

    def test_weights_are_parallel_to_postings(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A\n\nuno", "b.md": "# B\n\nuno"})
        _pages, _graph, index = _index_for(pages_dir)
        wire = pack_index(index, "v1")
        assert len(_uint8(wire["weights"])) == len(_postings(wire))
        assert all(0 < w <= 255 for w in _uint8(wire["weights"]))

    def test_postings_are_16_bit_for_a_normal_wiki(self, wikis_dir: Path):
        # L'array dominante del payload: 2 byte per posting, non 4.
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A\n\nuno"})
        _pages, _graph, index = _index_for(pages_dir)
        assert pack_index(index, "v1")["bits"] == 16

    def test_postings_sorted_within_each_term(self, wikis_dir: Path):
        pages = {f"p{i:02d}.md": f"# P{i}\n\ncomune specifico{i}" for i in range(12)}
        pages_dir = _make_wiki(wikis_dir, "main", pages)
        _pages, _graph, index = _index_for(pages_dir)
        by_term = _unpack(pack_index(index, "v1"))
        assert by_term["comune"] == sorted(by_term["comune"])

    def test_universal_terms_ship_empty_postings(self, wikis_dir: Path):
        # Un termine in quasi tutti i documenti non restringe niente: resta nel
        # dizionario con range vuoto, che il client legge come "nessun vincolo".
        # Sotto la soglia assoluta di documenti la regola non deve scattare.
        pages = {f"p{i:03d}.md": f"# P{i}\n\nubiquo testo{i}" for i in range(50)}
        pages["raro.md"] = "# Raro\n\ntermineraro"
        pages_dir = _make_wiki(wikis_dir, "main", pages)
        _pages, _graph, index = _index_for(pages_dir)
        wire = pack_index(index, "v1")
        by_term = _unpack(wire)

        assert by_term["ubiquo"] == [], "termine quasi-universale: postings da omettere"
        assert wire["universal"] >= 1
        assert len(by_term["termineraro"]) == 1

    def test_small_wiki_has_no_universal_terms(self, wikis_dir: Path):
        # Su cinque pagine "il 60%" sono tre: senza la soglia assoluta parole
        # normalissime diventerebbero universali e smetterebbero di filtrare.
        pages = {f"p{i}.md": f"# P{i}\n\ncomune testo{i}" for i in range(5)}
        pages_dir = _make_wiki(wikis_dir, "main", pages)
        _pages, _graph, index = _index_for(pages_dir)
        wire = pack_index(index, "v1")
        assert wire["universal"] == 0
        assert len(_unpack(wire)["comune"]) == 5

    def test_empty_wiki_packs_cleanly(self, wikis_dir: Path):
        pages_dir = wikis_dir / "main" / "wiki"
        pages_dir.mkdir(parents=True)
        _pages, _graph, index = _index_for(pages_dir)
        wire = pack_index(index, "v1")
        assert wire["terms"] == ""
        assert _int32(wire["offsets"]) == [0]
        assert _postings(wire) == []


# ── Cache ───────────────────────────────────────────────────────────────────


class TestFingerprint:
    def test_stable_when_nothing_changes(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A"})
        assert fingerprint(pages_dir) == fingerprint(pages_dir)

    def test_moves_when_a_page_changes(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A"})
        before = fingerprint(pages_dir)
        (pages_dir / "a.md").write_text("# A modificata", encoding="utf-8")
        assert fingerprint(pages_dir) != before

    def test_moves_when_a_page_appears_or_disappears(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A"})
        before = fingerprint(pages_dir)
        (pages_dir / "b.md").write_text("# B", encoding="utf-8")
        after_add = fingerprint(pages_dir)
        assert after_add != before
        (pages_dir / "b.md").unlink()
        assert fingerprint(pages_dir) == before

    def test_ignores_hidden_groups(self, wikis_dir: Path):
        # I summaries non entrano nel grafo: toccarli non deve costare una
        # ricostruzione di grafo e indice.
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A"})
        before = fingerprint(pages_dir)
        summaries = pages_dir / "summaries"
        summaries.mkdir()
        (summaries / "src.md").write_text("# Src", encoding="utf-8")
        assert fingerprint(pages_dir) == before


class TestWikiSearchService:
    def test_unchanged_wiki_returns_the_same_bundle(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A\n\ntesto"})
        service = WikiSearchService()
        first = service.bundle(pages_dir)
        second = service.bundle(pages_dir)
        assert second is first, "wiki immutata: nessuna rilettura, stesso oggetto"

    def test_no_file_is_read_on_a_cache_hit(self, wikis_dir: Path, monkeypatch):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A\n\ntesto"})
        service = WikiSearchService()
        service.bundle(pages_dir)

        def boom(*_args, **_kwargs):
            raise AssertionError("cache hit: nessuna pagina va riletta")

        monkeypatch.setattr("jenny.webui.wiki_search.read_pages", boom)
        assert service.bundle(pages_dir).search["docs"] == 1

    def test_changed_page_rebuilds(self, wikis_dir: Path):
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A\n\nvecchio"})
        service = WikiSearchService()
        first = service.bundle(pages_dir)
        assert "vecchio" in first.search["terms"].split("\n")

        (pages_dir / "a.md").write_text("# A\n\nnuovissimo", encoding="utf-8")
        second = service.bundle(pages_dir)
        assert second is not first
        assert second.version != first.version
        terms = second.search["terms"].split("\n")
        assert "nuovissimo" in terms
        assert "vecchio" not in terms

    def test_bundle_version_matches_the_packed_index(self, wikis_dir: Path):
        # Il client usa la version per sapere se l'indice che ha in mano
        # descrive il grafo che sta disegnando.
        pages_dir = _make_wiki(wikis_dir, "main", {"a.md": "# A"})
        bundle = WikiSearchService().bundle(pages_dir)
        assert bundle.search["version"] == bundle.version

    def test_postings_index_into_the_bundled_graph(self, wikis_dir: Path):
        # L'invariante centrale del formato: ogni posting è un indice valido
        # nell'array ``nodes`` servito nella stessa risposta.
        pages_dir = _make_wiki(
            wikis_dir,
            "main",
            {"a.md": "# Alfa\n\nuno", "sub/b.md": "# Beta\n\ndue", "c.md": "# Gamma\n\ntre"},
        )
        bundle = WikiSearchService().bundle(pages_dir)
        by_term = _unpack(bundle.search)
        node_count = len(bundle.graph.nodes)
        assert bundle.search["docs"] == node_count
        for term, docs in by_term.items():
            for idx in docs:
                assert 0 <= idx < node_count, f"posting fuori range per '{term}'"

    def test_eviction_keeps_the_cache_bounded(self, wikis_dir: Path):
        # Più wiki aperte non devono far crescere la memoria all'infinito: la
        # meno usata di recente esce, e alla richiesta successiva si ricostruisce.
        service = WikiSearchService(max_wikis=2)
        dirs = [_make_wiki(wikis_dir, f"w{i}", {"a.md": f"# W{i}"}) for i in range(3)]

        oldest = service.bundle(dirs[0])
        service.bundle(dirs[1])
        service.bundle(dirs[2])

        assert len(service._entries) == 2
        assert str(dirs[0]) not in service._entries
        rebuilt = service.bundle(dirs[0])
        assert rebuilt is not oldest
        assert rebuilt.version == oldest.version  # stesso contenuto, altro oggetto


# ── Route /api/graph ────────────────────────────────────────────────────────

_AUTH_SECRET = "test-secret"


@pytest.fixture
def handler(tmp_path: Path, monkeypatch):
    """GatewayHTTPHandler reale su un workspace di tmp_path (v. test_skills_routes)."""
    from jenny.config import paths as paths_mod
    from jenny.webui.ws_http import GatewayHTTPHandler

    workspace = tmp_path / "data" / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)

    return GatewayHTTPHandler(
        config=SimpleNamespace(
            workspace=SimpleNamespace(enabled=True),
            wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
            token_issue_secret=_AUTH_SECRET,
            verbose=False,
        ),
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=MagicMock(),
        skills_workspace_path=workspace,
    )


async def _graph_response(handler, wiki: str | None):
    path = "/api/graph"
    if wiki:
        path += f"?wiki={urllib.parse.quote(wiki)}"
    sep = "&" if "?" in path else "?"
    request = WsRequest(path=f"{path}{sep}token={_AUTH_SECRET}", headers=Headers())
    return await handler.wiki_routes.dispatch(request, "/api/graph")


async def _get_graph(handler, wiki: str) -> dict:
    response = await _graph_response(handler, wiki)
    assert response is not None and response.status_code == 200
    return json.loads(response.body.decode("utf-8"))


class TestGraphRoute:
    async def test_wiki_graph_carries_the_search_index(self, handler, monkeypatch):
        workspace = handler._get_workspace_root()
        _make_wiki(
            workspace / "wikis",
            "main",
            {"index.md": "# Home\n\nVedi [[Doze]].", "doze.md": "# Doze\n\nsonno profondo"},
        )
        payload = await _get_graph(handler, "main")

        assert {n["id"] for n in payload["nodes"]} == {"wiki/index.md", "wiki/doze.md"}
        search = payload["search"]
        assert search is not None
        by_term = _unpack(search)
        ids = [n["id"] for n in payload["nodes"]]
        assert by_term["profondo"] == [ids.index("wiki/doze.md")]

    async def test_without_a_wiki_name_the_route_refuses(self, handler):
        """Senza ``wiki=`` non c'è più una vista: è un 400.

        Qui c'era il grafo a stella di *tutte* le wiki, tolto il 22/09. Lo
        stato conta: un 404 direbbe «quel quaderno non c'è» e manderebbe a
        cercare un nome, mentre il difetto è che il nome non è stato mandato.
        E il 400 deve arrivare **prima** di leggere il disco — con una wiki
        vera sotto, la risposta è la stessa.
        """
        workspace = handler._get_workspace_root()
        _make_wiki(workspace / "wikis", "main", {"index.md": "# Home"})
        response = await _graph_response(handler, None)
        assert response is not None
        assert response.status_code == 400, response.status_code

    async def test_index_follows_an_edit(self, handler):
        workspace = handler._get_workspace_root()
        pages_dir = _make_wiki(workspace / "wikis", "main", {"a.md": "# A\n\nprima"})
        assert "prima" in (await _get_graph(handler, "main"))["search"]["terms"].split("\n")

        (pages_dir / "a.md").write_text("# A\n\ndopo", encoding="utf-8")
        terms = (await _get_graph(handler, "main"))["search"]["terms"].split("\n")
        assert "dopo" in terms and "prima" not in terms
