"""Tests for the multi-wiki backend module."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.webui.wiki import (
    _split_wikilink,
    build_graph,
    create_audit,
    create_renderer,
    discover_wikis,
    resolve_wikilink,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def wikis_dir(tmp_path: Path) -> Path:
    """Create a temporary wikis directory."""
    d = tmp_path / "wikis"
    d.mkdir()
    return d


def _make_wiki(wikis_dir: Path, name: str, pages: dict[str, str]) -> Path:
    """Create a wiki with pages.  pages is {rel_path: content}.

    Returns the wiki *root* (parent of wiki/ and audit/ directories).
    """
    wiki_root = wikis_dir / name
    for rel, content in pages.items():
        full = wiki_root / "wiki" / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return wiki_root


# ── Discovery ───────────────────────────────────────────────────────────────


class TestDiscoverWikis:
    def test_discover_single_wiki(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        result = discover_wikis(wikis_dir)
        assert result == {"main": wikis_dir / "main" / "wiki"}

    def test_discover_multiple_wikis(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        (wikis_dir / "loops" / "wiki").mkdir(parents=True)
        result = discover_wikis(wikis_dir)
        assert set(result.keys()) == {"main", "loops"}

    def test_discover_skips_non_wiki_dirs(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        (wikis_dir / "not-a-wiki").mkdir()
        result = discover_wikis(wikis_dir)
        assert "not-a-wiki" not in result

    def test_discover_finds_wikis_that_have_pages(self, wikis_dir: Path):
        """Quel che misurava il grafo a stella, tolto il 22/09.

        Le sue due prove dicevano «ogni quaderno compare», e lo dicevano su
        quaderni costruiti con pagine dentro invece che con una ``mkdir``
        nuda: e' l'unica parte che non era gia' coperta qui sopra. Il caso
        della cartella vuota lo dice gia'
        ``test_discover_returns_empty_for_empty_dir``.
        """
        _make_wiki(wikis_dir, "main", {"index.md": "# Main"})
        _make_wiki(wikis_dir, "loops", {"index.md": "# Loops"})
        result = discover_wikis(wikis_dir)
        assert set(result) == {"main", "loops"}
        assert result["main"] == wikis_dir / "main" / "wiki"

    def test_discover_returns_empty_for_missing_dir(self, tmp_path: Path):
        result = discover_wikis(tmp_path / "nonexistent")
        assert result == {}

    def test_discover_returns_empty_for_empty_dir(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        result = discover_wikis(d)
        assert result == {}


# ── Graph ───────────────────────────────────────────────────────────────────


class TestBuildGraph:
    def test_single_wiki_graph(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "concepts/page.md": "# Concept",
        })
        graph = build_graph(wikis_dir / "main")
        node_ids = {n.id for n in graph.nodes}
        assert "wiki/index.md" in node_ids
        assert "wiki/concepts/page.md" in node_ids

    def test_graph_with_wikilinks(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\n\nVedi [[Target]].",
            "target.md": "# Target",
        })
        graph = build_graph(wikis_dir / "main")
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("wiki/index.md", "wiki/target.md") in edges

    def test_graph_self_link_ignored(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\n\nVedi [[index]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert len(graph.edges) == 0

    def test_graph_anchor_link_ignored(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\n\nVedi [[#Anchor]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert len(graph.edges) == 0

    def test_graph_empty_wiki(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        graph = build_graph(wikis_dir / "main")
        assert graph.nodes == []
        assert graph.edges == []

    def test_mutual_wikilinks_produce_single_edge(self, wikis_dir: Path):
        # A↔B: un solo arco non orientato, degree contato una volta per lato.
        _make_wiki(wikis_dir, "main", {
            "a.md": "# A\n\nVedi [[b]].",
            "b.md": "# B\n\nVedi [[a]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert len(graph.edges) == 1
        degrees = {n.id: n.degree for n in graph.nodes}
        assert degrees["wiki/a.md"] == 1
        assert degrees["wiki/b.md"] == 1

    def test_ambiguous_stem_does_not_resolve(self, wikis_dir: Path):
        # Due file con lo stesso stem in cartelle diverse: un link per stem
        # nudo non deve risolvere (ambiguo → nessun arco non deterministico).
        _make_wiki(wikis_dir, "main", {
            "concepts/foo.md": "# Foo concept",
            "entities/foo.md": "# Foo entity",
            "index.md": "# Home\n\nVedi [[foo]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert graph.edges == []

    def test_summaries_excluded_from_graph(self, wikis_dir: Path):
        # I summaries sono livello di citazione: fuori dal grafo (nodi e archi).
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "concepts/foo.md": "# Foo\n\nFonte [[summaries/src]].",
            "summaries/src.md": "# Src summary",
        })
        graph = build_graph(wikis_dir / "main")
        node_ids = {n.id for n in graph.nodes}
        assert "wiki/summaries/src.md" not in node_ids
        assert "wiki/concepts/foo.md" in node_ids
        # Nessun arco verso il summary escluso.
        for e in graph.edges:
            assert "summaries" not in e.source
            assert "summaries" not in e.target

    def test_relpath_link_resolves_unambiguously(self, wikis_dir: Path):
        # Con stem ambiguo, il link per rel-path completo risolve al nodo giusto.
        _make_wiki(wikis_dir, "main", {
            "concepts/foo.md": "# Foo concept",
            "entities/foo.md": "# Foo entity",
            "index.md": "# Home\n\nVedi [[concepts/foo]].",
        })
        graph = build_graph(wikis_dir / "main")
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("wiki/index.md", "wiki/concepts/foo.md") in edges
        assert len(graph.edges) == 1


# ── Renderer ────────────────────────────────────────────────────────────────


class TestCreateRenderer:
    def test_basic_page_render(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        renderer = create_renderer(wiki_root)
        result = renderer("# Hello")
        assert "<h1" in result.html
        assert "Hello" in result.html
        assert result.title == "Hello"

    def test_frontmatter_title(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root)
        raw = "---\ntitle: My Page\n---\n\nContent"
        result = renderer(raw)
        assert result.title == "My Page"
        assert result.frontmatter == {"title": "My Page"}

    def test_same_wiki_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "target.md": "# Target",
        })
        renderer = create_renderer(wiki_root, current_wiki="main", wikis_map={"main": wiki_root})
        result = renderer("Vedi [[Target]].")
        assert 'href="/?wiki=main' in result.html
        assert "page=" in result.html

    def test_cross_wiki_wikilink(self, wikis_dir: Path):
        main_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        other_root = _make_wiki(wikis_dir, "other", {"page.md": "# Other"})
        wikis_map = {"main": main_root, "other": other_root}
        renderer = create_renderer(main_root, current_wiki="main", wikis_map=wikis_map)
        result = renderer("Vedi [[other:page]].")
        assert 'href="/?wiki=other&page=page.md"' in result.html

    def test_anchor_only_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root, current_wiki="main")
        result = renderer("Vedi [[#Open Questions]].")
        assert 'href="#open-questions"' in result.html

    def test_dead_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root, current_wiki="main", wikis_map={"main": wiki_root})
        result = renderer("Vedi [[Nonexistent]].")
        assert '/?wiki=main&page=Nonexistent' in result.html

    def test_escaped_pipe_in_table_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        renderer = create_renderer(wiki_root, current_wiki="main", wikis_map={"main": wiki_root})
        result = renderer("| Link |\n|------|\n| [[index\\|Apri]] |")
        assert 'href="/?wiki=main&page=index.md"' in result.html
        assert ">Apri</a>" in result.html

    def test_cross_wiki_strips_wiki_prefix(self, wikis_dir: Path):
        main_root = _make_wiki(wikis_dir, "main", {"page.md": "# Page"})
        wikis_map = {"main": main_root}
        renderer = create_renderer(wikis_dir, current_wiki=None, wikis_map=wikis_map)
        result = renderer("Vedi [[main/wiki/page|main/page]].")
        assert 'href="/?wiki=main&page=page.md"' in result.html

    def test_cross_wiki_directory_index(self, wikis_dir: Path):
        main_root = _make_wiki(wikis_dir, "main", {"concepts/page/index.md": "# Page"})
        wikis_map = {"main": main_root}
        renderer = create_renderer(wikis_dir, current_wiki=None, wikis_map=wikis_map)
        result = renderer("Vedi [[main/concepts/page|main/page]].")
        assert 'href="/?wiki=main&page=concepts/page/index.md"' in result.html

    def test_mermaid_preserved(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root)
        raw = "```mermaid\ngraph TD\nA-->B\n```"
        result = renderer(raw)
        assert 'class="mermaid-block"' in result.html

    def test_raw_markdown_preserved(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root)
        raw = "# Hello\n\nWorld"
        result = renderer(raw)
        assert result.raw_markdown == raw


# ── Audit ───────────────────────────────────────────────────────────────────
#
# **Si scrivono e non si rileggono**, dal 22/09/2026. Le rotte che li elencavano
# e il comando che li chiudeva sono usciti con lo stesso giro: dal telefono una
# segnalazione si apre e basta, e chi la legge e' Jenny — con i suoi strumenti
# file e ``llm-wiki/scripts/audit_review.py``, che ha il suo analizzatore. Quindi
# qui si guarda **il disco**, che e' l'unica cosa che entrambe le parti vedono.


def _audit_files(wiki_root: Path) -> list[Path]:
    """I file di audit aperti, dal disco. Niente ``load_audits``: non c'e' piu'."""
    return sorted((wiki_root / "audit").glob("*.md"))


class TestAudit:
    def test_create_audit_writes_one_anchored_file(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home\ncontent here"})
        result = create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=7,
            sel_end=14,
            comment="typo",
            author="test",
        )
        assert "id" in result
        assert result["filename"]
        # Il path che torna e' **relativo alla radice della wiki**: mai assoluto,
        # o esporrebbe il layout del filesystem dell'host.
        assert not result["path"].startswith("/")
        assert result["path"].startswith("audit/")

        scritti = _audit_files(wiki_root)
        assert len(scritti) == 1
        text = scritti[0].read_text(encoding="utf-8")
        # L'ancora e' il punto di tutto: senza, il commento parla della pagina e
        # non del punto, che e' quel che gli audit esistono per fare.
        assert "anchor_text: content" in text
        assert "target: index.md" in text
        assert "status: open" in text
        assert "typo" in text

    def test_create_audit_missing_target(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        with pytest.raises(FileNotFoundError):
            create_audit(
                wiki_root=wiki_root,
                target="nonexistent.md",
                raw_markdown="# Home",
                sel_start=0,
                sel_end=5,
                comment="c",
                author="test",
            )

    def test_failed_write_leaves_no_half_written_audit(
        self, wikis_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Una scrittura fallita non deve lasciare un audit troncato sul disco.

        Un audit a meta' occuperebbe il suo id restando illeggibile: il rename
        finale dell'helper atomico rende il file visibile solo completo.
        """
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home\ncontent here"})

        def boom(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr("jenny.webui.wiki.atomic_write", boom)
        with pytest.raises(OSError):
            create_audit(
                wiki_root=wiki_root,
                target="index.md",
                raw_markdown="# Home\ncontent here",
                sel_start=8,
                sel_end=15,
                comment="typo",
                author="test",
            )
        assert _audit_files(wiki_root) == []


# ── Wikilink Resolution ─────────────────────────────────────────────────


class TestResolveWikilink:
    def test_exact_match(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"target.md": "# Target"})
        result = resolve_wikilink(wiki_root, "target.md")
        assert result is not None
        assert result.name == "target.md"

    def test_case_insensitive(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"Target.md": "# Target"})
        result = resolve_wikilink(wiki_root, "target.md")
        assert result is not None

    def test_slug_match(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"sviluppo-idee.md": "# Sviluppo Idee"})
        result = resolve_wikilink(wiki_root, "Sviluppo Idee")
        assert result is not None
        assert result.name == "sviluppo-idee.md"

    def test_subdir_resolution(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"concepts/page.md": "# Page"})
        result = resolve_wikilink(wiki_root, "concepts/page.md")
        assert result is not None

    def test_no_match_returns_none(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        result = resolve_wikilink(wiki_root, "nonexistent.md")
        assert result is None

    def test_strips_wiki_prefix(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"page.md": "# Page"})
        result = resolve_wikilink(wiki_root, "wiki/page")
        assert result is not None
        assert result.name == "page.md"

    def test_directory_with_index(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"concepts/page/index.md": "# Page"})
        result = resolve_wikilink(wiki_root, "concepts/page")
        assert result is not None
        assert result.name == "index.md"

    def test_folder_relative_link_from_a_split_index(self, wikis_dir: Path):
        """La forma che `references/article-guide.md` raccomanda dentro un
        `index.md` diviso in cartella: `[[<Topic>/<aspect>]]`, senza il prefisso
        `concepts/`. Era morta nell'app — nessuno stem contiene una barra — e
        viva nel lint, quindi la guida raccomandava un link che il telefono non
        apriva e l'unico strumento che poteva accorgersene taceva.
        """
        wiki_root = _make_wiki(wikis_dir, "main", {
            "concepts/Transformers/index.md": "# Transformers",
            "concepts/Transformers/attention.md": "# Attention",
        })
        result = resolve_wikilink(wiki_root, "Transformers/attention")
        assert result is not None
        assert result.relative_to(wiki_root / "wiki").as_posix() == (
            "concepts/Transformers/attention.md"
        )

    def test_folder_relative_link_to_a_nested_split(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "concepts/Transformers/training/index.md": "# Training",
        })
        result = resolve_wikilink(wiki_root, "Transformers/training")
        assert result is not None
        assert result.relative_to(wiki_root / "wiki").as_posix() == (
            "concepts/Transformers/training/index.md"
        )

    def test_a_multi_segment_link_is_not_resolved_by_its_last_name(self, wikis_dir: Path):
        """**Il confine.** Il ramo per suffisso chiede che il path della pagina
        *finisca* per il link: `[[Altro/attention]]` non deve trovare
        `concepts/Transformers/attention.md` solo perché il nome finale esiste
        da qualche parte. È la differenza fra risolvere un link più preciso e
        buttare via i segmenti che lo rendono preciso.
        """
        wiki_root = _make_wiki(wikis_dir, "main", {
            "concepts/Transformers/attention.md": "# Attention",
        })
        assert resolve_wikilink(wiki_root, "Altro/attention") is None
        assert resolve_wikilink(wiki_root, "Transformers/nessuna") is None

    def test_two_pages_with_the_same_name_resolve_deterministically(self, wikis_dir: Path):
        """Due `nota.md` in cartelle diverse esistono davvero. Vinceva quella che
        `rglob` restituiva per prima — l'ordine della directory — quindi lo stesso
        `[[nota]]` poteva aprire pagine diverse su due telefoni, e il lint non
        poteva concordare con nessuna delle due. Vince la più vicina alla radice.
        """
        wiki_root = _make_wiki(wikis_dir, "main", {
            "concepts/deep/nota.md": "# Deep",
            "concepts/nota.md": "# Vicina",
        })
        result = resolve_wikilink(wiki_root, "nota")
        assert result is not None
        assert result.relative_to(wiki_root / "wiki").as_posix() == "concepts/nota.md"


# ── Wikilink Splitting ─────────────────────────────────────────────────────


class TestSplitWikilink:
    def test_plain_separator(self):
        assert _split_wikilink("target|label") == ("target", "label")

    def test_escaped_pipe_separator(self):
        assert _split_wikilink("target\\|label") == ("target", "label")

    def test_no_separator(self):
        assert _split_wikilink("target") == ("target", None)


# ── La gravita', misurata assente ──────────────────────────────────────────


class TestNessunaGravita:
    """Tolta dal formato il 22/09/2026, e va misurata **assente**.

    Erano quattro livelli che chi segnalava sceglieva prima di scrivere — un
    campo da coda di smistamento in un posto dove chi segnala e chi corregge
    sono la stessa persona. Toglierla dalla tendina non bastava: sarebbe rimasta
    una riga fissa in ogni file futuro, piu' il codice che la valida e la ordina
    avendo un valore solo.
    """

    def test_the_written_file_carries_no_severity(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home\ncontent here"})
        created = create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=8,
            sel_end=15,
            comment="typo",
            author="test",
        )
        text = (wiki_root / created["path"]).read_text(encoding="utf-8")
        assert "severity" not in text
        # …e il resto del frontmatter c'e' ancora: il taglio e' uno solo.
        for key in ("id:", "target:", "anchor_text:", "author:", "status:"):
            assert key in text, key


# ── Frontmatter allowlist (/api/page privacy) ───────────────────────────────


class TestFrontmatterAllowlist:
    def test_drops_internal_keys(self):
        from jenny.webui.wiki_routes import _filter_frontmatter

        fm = {
            "title": "Foo",
            "tags": ["a", "b"],
            "type": "concept",
            "source_url": "https://internal/secret",
            "provenance": "scaffold-run-42",
            "draft": True,
        }
        filtered = _filter_frontmatter(fm)
        assert filtered == {"title": "Foo", "tags": ["a", "b"], "type": "concept"}
        assert "source_url" not in filtered
        assert "provenance" not in filtered
        assert "draft" not in filtered

    def test_none_and_non_dict_passthrough(self):
        from jenny.webui.wiki_routes import _filter_frontmatter

        assert _filter_frontmatter(None) is None
        assert _filter_frontmatter("not a dict") == "not a dict"
