"""Wiki backend logic — renderer, tree, graph, audit.

Provides the Python-native replacement for the Node/Express wiki server.
Supports multi-wiki: each wiki lives under workspace/wikis/{name}/wiki/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from typing import override  # Python 3.12+
except ImportError:
    from typing_extensions import override  # backport 3.11
from typing import Any, Callable
from urllib.parse import quote as url_quote

from markdown import Markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

from jenny.security.workspace_policy import is_path_within
from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import extract_title as _extract_title
from jenny.utils.wiki_paths import is_wiki_page_rel, resolve_wikilink, slugify, strip_wiki_prefix
from jenny.utils.wiki_paths import strip_frontmatter as _strip_frontmatter
from jenny.webui.audit import (
    AuditEntry,
    compute_anchor,
    filename_for,
    make_id,
    to_markdown,
)

# ── Constants ────────────────────────────────────────────────────────────────

_WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")


def _top_group(rel: str) -> str:
    """Gruppo di primo livello di un path relativo alla pages-dir ``wiki/``."""
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else "other"

# ── Public dataclasses ───────────────────────────────────────────────────────


@dataclass
class RenderedPage:
    html: str
    frontmatter: dict[str, Any] | None
    raw_markdown: str
    title: str | None


@dataclass
class GraphNode:
    id: str
    label: str
    path: str
    group: str
    degree: int
    title: str | None


@dataclass
class GraphEdge:
    source: str
    target: str


@dataclass
class GraphData:
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@dataclass(frozen=True)
class PageSource:
    """Una pagina letta dal disco, con i metadati che se ne ricavano.

    È la materia prima *condivisa* di grafo e indice full-text: entrambi
    partono dallo stesso testo, e leggerlo due volte significherebbe pagare due
    volte l'unica parte davvero cara della costruzione (l'I/O su flash, con
    centinaia di file). Chi vuole solo il grafo usa :func:`build_graph`, che
    incapsula la lettura; chi vuole entrambi legge una volta con
    :func:`read_pages` e passa la lista a tutti i costruttori.
    """

    rel: str  # path relativo alla pages-dir, es. "concepts/foo.md"
    node_id: str  # id di nodo nel grafo, es. "wiki/concepts/foo.md"
    stem: str
    group: str
    title: str
    text: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _split_wikilink(raw: str) -> tuple[str, str | None]:
    # Markdown tables require escaping the pipe as \|; normalize it so that
    # [[target\|label]] is parsed the same as [[target|label]].
    normalized = raw.replace("\\|", "|")
    parts = normalized.split("|", 1)
    if len(parts) >= 2:
        return (parts[0].strip(), parts[1].strip())
    return (parts[0].strip(), None)


# ── Graph ────────────────────────────────────────────────────────────────────


def iter_page_files(pages_dir: Path) -> list[tuple[str, Path]]:
    """``(rel, path)`` delle pagine che entrano nel grafo, in ordine stabile.

    Unico punto in cui si decide *quali* file contano. Il fingerprint della
    cache (``wiki_search``) e la lettura vera (:func:`read_pages`) devono
    guardare esattamente lo stesso insieme: se divergessero, o si
    ricostruirebbe l'indice per una modifica in ``summaries/`` che non lo
    tocca, oppure — molto peggio — una pagina cambierebbe senza muovere il
    fingerprint e la ricerca resterebbe ferma al contenuto vecchio.

    L'ordine è per rel-path perché quello di ``rglob`` dipende dal filesystem:
    da questa lista discende l'indice numerico dei nodi, che è la chiave delle
    postings dell'indice full-text.

    **Quali file** lo decide :func:`jenny.utils.wiki_paths.is_wiki_page_rel`,
    la stessa regola dell'iniettore e dell'albero (T9.5): fuori i
    ``summaries/``, fuori i nascosti a ogni livello. I nascosti prima entravano
    — un ``.draft.md`` sotto ``wiki/`` non arrivava al modello e non compariva
    nell'albero, ma era un nodo del grafo e un risultato di ricerca.

    L'indice invece **e' una pagina**, qui, e la differenza e' voluta: per il
    prompt ``wiki/index.md`` e' la mappa e ha un blocco suo, per chi navighera'
    e' il nodo centrale, e cercare dentro la mappa e' la cosa piu' ovvia del
    mondo.
    """
    if not pages_dir.exists():
        return []
    out: list[tuple[str, Path]] = []
    for f in pages_dir.rglob("*.md"):
        rel = f.relative_to(pages_dir)
        if not is_wiki_page_rel(rel):
            continue
        out.append((rel.as_posix(), f))
    out.sort(key=lambda item: item[0])
    return out


def read_pages(pages_dir: Path) -> list[PageSource]:
    """Legge una volta sola tutte le pagine indicizzabili di una wiki.

    Un file illeggibile (permessi, byte non-UTF-8) viene saltato con un
    WARNING: è una pagina in meno nel grafo, non un 500 su tutta la wiki.
    """
    pages: list[PageSource] = []
    for rel, f in iter_page_files(pages_dir):
        try:
            text = f.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            import logging

            logging.getLogger("wiki").warning("skipping unreadable page %s: %s", rel, err)
            continue
        pages.append(
            PageSource(
                rel=rel,
                node_id=f"wiki/{rel}",
                stem=f.stem,
                group=_top_group(rel),
                title=_extract_title(text) or f.stem,
                text=text,
            )
        )
    return pages


def build_graph(wiki_root: Path) -> GraphData:
    """Build a graph of wiki pages and their wikilink connections for one wiki."""
    return build_graph_from_pages(read_pages(wiki_root / "wiki"))


def build_graph_from_pages(pages: list[PageSource]) -> GraphData:
    """Grafo dei wikilink a partire da pagine già lette (v. :func:`read_pages`)."""
    if not pages:
        return GraphData(nodes=[], edges=[])

    by_key: dict[str, str] = {}
    nodes: dict[str, GraphNode] = {}
    # Le chiavi rel-path sono univoche per file; le chiavi per stem possono
    # collidere (es. concepts/foo.md vs entities/foo.md). Raccogliamo i candidati
    # per stem e registriamo la chiave solo se punta a un unico nodo, così i
    # wikilink per stem ambiguo non risolvono in modo non deterministico.
    stem_candidates: dict[str, set[str]] = {}

    for page in pages:
        nodes[page.node_id] = GraphNode(
            id=page.node_id,
            label=page.stem,
            path=page.rel,
            group=page.group,
            degree=0,
            title=page.title,
        )
        # Chiave rel-path: sempre univoca, ha precedenza.
        by_key[page.rel.removesuffix(".md")] = page.node_id
        stem_candidates.setdefault(page.stem, set()).add(page.node_id)
        if page.stem.lower() != page.stem:
            stem_candidates.setdefault(page.stem.lower(), set()).add(page.node_id)

    for key, ids in stem_candidates.items():
        if len(ids) == 1 and key not in by_key:
            by_key[key] = next(iter(ids))

    edges: list[GraphEdge] = []
    seen_pairs: set[frozenset[str]] = set()

    for page in pages:
        src_id = page.node_id
        for match in _WIKILINK_RE.finditer(page.text):
            inner = match.group(1).strip()
            target, _label = _split_wikilink(inner)
            if not target or target.startswith("#"):
                continue
            tgt_id = (
                by_key.get(target)
                or by_key.get(target.removesuffix(".md"))
                or by_key.get(target.lower())
            )
            if not tgt_id or tgt_id == src_id:
                continue
            # Dedup non orientato: A↔B è un unico arco e conta una sola volta
            # nel degree di ciascun estremo.
            pair = frozenset({src_id, tgt_id})
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append(GraphEdge(source=src_id, target=tgt_id))
            nodes[src_id].degree += 1
            nodes[tgt_id].degree += 1

    return GraphData(nodes=list(nodes.values()), edges=edges)


# ── Renderer ─────────────────────────────────────────────────────────────────


class _WikilinksExtension(Extension):
    def __init__(self, wiki_root: Path, current_wiki: str | None = None, wikis_map: dict[str, Path] | None = None):
        self.wiki_root = wiki_root
        self.current_wiki = current_wiki
        self.wikis_map = wikis_map
        super().__init__()

    @override
    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(
            _WikilinksPreprocessor(md, self.wiki_root, self.current_wiki, self.wikis_map),
            "wikilinks",
            29,
        )


class _WikilinksPreprocessor(Preprocessor):
    def __init__(self, md: Markdown, wiki_root: Path, current_wiki: str | None = None, wikis_map: dict[str, Path] | None = None):
        self.wiki_root = wiki_root
        self.current_wiki = current_wiki
        self.wikis_map = wikis_map or {}
        super().__init__(md)

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)

        def replace(match: re.Match[str]) -> str:
            inner = match.group(1).strip()
            target, label = _split_wikilink(inner)
            href = self._resolve_target(target)
            display = label or target
            return f'<a class="wikilink" href="{href}">{display}</a>'

        text = _WIKILINK_RE.sub(replace, text)
        return text.split("\n")

    def _resolve_target(self, target: str) -> str:
        anchor = None
        if "#" in target:
            idx = target.index("#")
            anchor = target[idx + 1 :]
            target = target[:idx]

        if not target and anchor:
            return f"#{slugify(anchor)}"

        # Cross-wiki colon syntax: wiki:page
        if ":" in target:
            wiki_name, _, page_ref = target.partition(":")
            if wiki_name in self.wikis_map:
                wiki_path = self.wikis_map[wiki_name]
                page_ref = strip_wiki_prefix(page_ref)
                resolved = resolve_wikilink(wiki_path, page_ref)
                if resolved:
                    rel = resolved.relative_to(wiki_path / "wiki").as_posix()
                    href = f"/?wiki={wiki_name}&page={url_quote(rel, safe='/')}"
                else:
                    href = f"/?wiki={wiki_name}&page={url_quote(page_ref)}"
                return _append_anchor(href, anchor)

        # Cross-wiki path syntax: other/wiki/page
        parts = target.split("/")
        if len(parts) >= 2 and parts[0] in self.wikis_map:
            wiki_name = parts[0]
            wiki_path = self.wikis_map[wiki_name]
            page_ref = strip_wiki_prefix("/".join(parts[1:]))
            resolved = resolve_wikilink(wiki_path, page_ref)
            if resolved:
                rel = resolved.relative_to(wiki_path / "wiki").as_posix()
                href = f"/?wiki={wiki_name}&page={url_quote(rel, safe='/')}"
            else:
                href = f"/?wiki={wiki_name}&page={url_quote(page_ref)}"
            return _append_anchor(href, anchor)

        # Same-wiki resolution
        if self.current_wiki and self.current_wiki in self.wikis_map:
            wiki_path = self.wikis_map[self.current_wiki]
            resolved = resolve_wikilink(wiki_path, strip_wiki_prefix(target))
            if resolved:
                rel = resolved.relative_to(wiki_path / "wiki").as_posix()
                href = f"/?wiki={self.current_wiki}&page={url_quote(rel, safe='/')}"
                return _append_anchor(href, anchor)
            href = f"/?wiki={self.current_wiki}&page={url_quote(target)}"
            return _append_anchor(href, anchor)

        return _append_anchor("#", anchor)


def _append_anchor(href: str, anchor: str | None) -> str:
    if anchor:
        return f"{href}#{slugify(anchor)}"
    return href


class _MermaidPreserveExtension(Extension):
    @override
    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(_MermaidPreservePreprocessor(md), "mermaid_preserve", 30)


class _MermaidPreservePreprocessor(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        in_mermaid = False
        buffer: list[str] = []
        for line in lines:
            if line.strip().startswith("```mermaid"):
                in_mermaid = True
                buffer = [line]
                continue
            if in_mermaid and line.strip() == "```":
                buffer.append(line)
                content = "\n".join(buffer[1:-1])
                escaped = (
                    content.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                out.append(f'<pre class="mermaid-block"><code class="language-mermaid">{escaped}</code></pre>')
                in_mermaid = False
                buffer = []
                continue
            if in_mermaid:
                buffer.append(line)
            else:
                out.append(line)
        if in_mermaid:
            out.extend(buffer)
        return out


# Le estensioni Python-Markdown delle pagine wiki. Fisse: erano esposte come
# ``wiki.extensions`` nella config, ma nessuno passava quel valore fin qui, e
# un'estensione scelta dall'utente sarebbe codice da validare (ritirata il
# 24/09/2026).
_WIKI_EXTENSIONS = ("fenced_code", "tables", "toc", "wikilinks", "mermaid")


def create_renderer(
    wiki_root: Path,
    current_wiki: str | None = None,
    wikis_map: dict[str, Path] | None = None,
) -> Callable[[str], RenderedPage]:
    """Create a markdown renderer configured for wiki pages.

    wiki_root: the wiki root directory (parent of wiki/ and audit/).
    """
    md_extensions: list[Extension | str] = []
    for ext in _WIKI_EXTENSIONS:
        if ext == "wikilinks":
            md_extensions.append(_WikilinksExtension(wiki_root, current_wiki, wikis_map))
        elif ext == "mermaid":
            md_extensions.append(_MermaidPreserveExtension())
        else:
            md_extensions.append(ext)

    def render(raw_markdown: str) -> RenderedPage:
        frontmatter, body, title = _strip_frontmatter(raw_markdown)
        md = Markdown(extensions=md_extensions)
        html = md.convert(body)
        return RenderedPage(
            html=html,
            frontmatter=frontmatter,
            raw_markdown=raw_markdown,
            title=title,
        )

    return render


# ── Audit helpers ───────────────────────────────────────────────────────────


def create_audit(
    wiki_root: Path,
    target: str,
    raw_markdown: str,
    sel_start: int,
    sel_end: int,
    comment: str,
    author: str,
) -> dict[str, Any]:
    """Create a new audit entry under wiki_root / audit/."""
    pages_dir = wiki_root / "wiki"
    # Il percorso grezzo: lo risolve ``is_path_within``, che tratta anche un loop
    # di symlink (``RuntimeError`` su Python 3.11) come fuori.
    target_full = pages_dir / target
    if not is_path_within(target_full, pages_dir):
        raise FileNotFoundError(f"target file not found: {target}")
    if not target_full.is_file():
        raise FileNotFoundError(f"target file not found: {target}")

    anchor = compute_anchor(raw_markdown, sel_start, sel_end)
    audit_id = make_id()
    slug = " ".join(comment.strip().split()[:5])
    filename = filename_for(audit_id, slug)
    audit_dir = wiki_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out_path = audit_dir / filename

    entry = AuditEntry(
        id=audit_id,
        target=target,
        target_lines=anchor.target_lines,
        anchor_before=anchor.anchor_before,
        anchor_text=anchor.anchor_text,
        anchor_after=anchor.anchor_after,
        author=author or "anonymous",
        source="web-viewer",
        created=datetime.now().isoformat(),
        status="open",
        body=f"# Comment\n\n{comment.strip()}\n\n# Resolution\n\n<!-- filled in when the audit is processed -->\n",
    )
    atomic_write(out_path, to_markdown(entry))
    return {
        "id": audit_id,
        "filename": filename,
        "path": out_path.relative_to(wiki_root).as_posix(),
    }
