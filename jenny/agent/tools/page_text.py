"""Il testo di un articolo, senza la pagina intorno.

Un tampone. ``web_fetch`` convertiva tutto l'HTML, menu, categorie e piè di
pagina compresi: nelle 45 pagine lette da una ricerca del 04/11/2026 il 53% del
testo passato al modello erano link, il 37% solo indirizzi. Qui si tiene il
corpo — ``<article>``, ``<main>`` o il blocco con più paragrafi, come fa
Readability — e dei link le sole parole, più un elenco corto in fondo per
andare avanti (pagina 2, una scheda prodotto).

Pensato per gli articoli. Una pagina che non ne ha uno torna intera, ma senza
menu, testata e piè di pagina. Tutto passa da :func:`extract`, che restituisce
``""`` quando non sa cosa fare: chi lo chiama ripiega sul convertitore di
prima, e chi lo sostituirà cambia solo questo modulo. Solo libreria standard.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

# Oltre questo l'HTML non si analizza: il parser è Python puro, e sul telefono
# una pagina da qualche MB costerebbe secondi.
_MAX_HTML_CHARS = 2_000_000
_MAX_LINKS = 20

_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
# Mai testo da leggere, ovunque stiano.
_NEVER = frozenset({
    "head", "script", "style", "noscript", "svg", "template", "iframe", "canvas",
    "video", "audio", "object", "select", "textarea", "button", "form", "dialog",
})
# Il contorno della pagina: via anche dentro il corpo scelto. ``header`` no:
# dentro un articolo porta il titolo e la data, e fuori dal corpo resta fuori.
_CHROME = frozenset({"nav", "aside", "footer"})
_CHROME_ROLES = frozenset({
    "navigation", "banner", "contentinfo", "search", "complementary", "dialog",
})
# Classi e id del contorno. Corta di proposito: «promo» o «banner» staccano
# anche il prezzo di una scheda prodotto.
_JUNK_TOKEN = re.compile(
    r"(?:^|[-_])(?:cookies?|consent|gdpr|newsletter|share|sharing|social|related|"
    r"breadcrumbs?|comments?|sidebar|menu|navbar|nav|footer|popup|modal|advert|"
    r"subscribe)(?:[-_]|$)",
    re.I,
)
# Un contenitore marcato come contorno ma con tanta prosa dentro è un involucro
# («has-sidebar» sul div della pagina): non si butta.
_JUNK_MAX_PROSE = 300
# Paragrafi più corti non contano per scegliere il corpo.
_MIN_PARAGRAPH = 25

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCKS = frozenset({
    "p", "div", "section", "article", "main", "header", "ul", "ol", "dl", "dt", "dd",
    "blockquote", "figure", "figcaption", "address", "details", "summary", "hr",
})
# Un tag che si apre mentre è aperto uno uguale lo chiude (``<p>a<p>b``).
_SELF_CLOSING_SIBLINGS = {
    "p": {"p"}, "li": {"li"}, "tr": {"tr"}, "td": {"td", "th"}, "th": {"td", "th"},
    "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "option": {"option"},
}


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    """Un albero tollerante: un tag chiuso che non è aperto si ignora."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.stack = [self.root]
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        closes = _SELF_CLOSING_SIBLINGS.get(tag)
        if closes and self.stack[-1].tag in closes:
            self.stack.pop()
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag == "title":
            self._in_title = True
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(_Node(tag, {k: v or "" for k, v in attrs}, self.stack[-1]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self.stack[-1].children.append(data)


def _elements(node: _Node):
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _elements(child)


def _is_chrome(node: _Node) -> bool:
    if node.tag in _CHROME or node.attrs.get("role", "").lower() in _CHROME_ROLES:
        return True
    if node.attrs.get("aria-hidden", "").lower() == "true" or "hidden" in node.attrs:
        return True
    names = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}".split()
    return any(_JUNK_TOKEN.search(n) for n in names) and _prose_len(node) < _JUNK_MAX_PROSE


def _prose_len(node: _Node) -> int:
    """I caratteri dentro i ``<p>`` del sottoalbero."""
    return sum(len(_plain(p)) for p in _elements(node) if p.tag == "p")


def _plain(node: _Node) -> str:
    parts: list[str] = []

    def walk(n: _Node) -> None:
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            elif c.tag not in _NEVER:
                walk(c)

    walk(node)
    return " ".join("".join(parts).split())


def _drop_never(node: _Node) -> None:
    node.children = [
        c for c in node.children if isinstance(c, str) or c.tag not in _NEVER
    ]
    for c in node.children:
        if isinstance(c, _Node):
            _drop_never(c)


def _choose_body(root: _Node) -> _Node:
    """Il corpo della pagina: l'articolo, il main, o il blocco con più prosa."""
    elements = list(_elements(root))
    body = next((e for e in elements if e.tag == "body"), root)

    articles = [e for e in elements if e.tag == "article" or e.attrs.get("itemprop") == "articleBody"]
    if articles:
        sizes = [_prose_len(a) for a in articles]
        best = max(range(len(articles)), key=sizes.__getitem__)
        # Tre o più articoli dove nessuno domina: un elenco di anteprime, non un articolo.
        if sizes[best] >= 500 and not (len(articles) >= 3 and sizes[best] < 0.4 * sum(sizes)):
            return articles[best]
    for e in elements:
        if e.tag == "main" or e.attrs.get("role", "").lower() == "main":
            return e

    # Come Readability: ogni paragrafo dà punti al genitore e metà al nonno.
    scores: dict[int, float] = {}
    nodes: dict[int, _Node] = {}
    for p in elements:
        if p.tag != "p" or p.parent is None:
            continue
        text = _plain(p)
        if len(text) < _MIN_PARAGRAPH:
            continue
        points = 1 + text.count(",") + min(len(text) / 100, 3)
        for ancestor, share in ((p.parent, 1.0), (p.parent.parent, 0.5)):
            if ancestor is None or ancestor is root:
                continue
            scores[id(ancestor)] = scores.get(id(ancestor), 0) + points * share
            nodes[id(ancestor)] = ancestor
    if not scores:
        return body
    best_node = nodes[max(scores, key=scores.__getitem__)]
    # Un blocco che ha meno di un terzo della prosa della pagina non è il corpo:
    # meglio la pagina intera ripulita che un pezzo.
    if _prose_len(best_node) < _prose_len(body) / 3:
        return body
    return best_node


class _Renderer:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.out: list[str] = []
        self.links: dict[str, str] = {}

    def block(self, node: _Node) -> None:
        for child in node.children:
            if isinstance(child, str):
                self.out.append(child)
                continue
            tag = child.tag
            if tag in _NEVER or _is_chrome(child):
                continue
            if tag in _HEADINGS or tag == "li":
                # Vuoti si saltano: gli elenchi delle schede prodotto ne hanno a decine.
                text = self.inline(child)
                if text and tag == "li":
                    self.out.append(f"\n- {text}\n")
                elif text:
                    self.out.append(f"\n\n{'#' * int(tag[1])} {text}\n\n")
            elif tag == "table":
                self.out.append(f"\n\n{self.table(child)}\n\n")
            elif tag == "pre":
                self.out.append(f"\n\n{_plain(child)}\n\n")
            elif tag == "br":
                self.out.append("\n")
            elif tag == "a":
                self.note_link(child)
                self.block(child)
            elif tag in _BLOCKS:
                self.out.append("\n\n")
                self.block(child)
                self.out.append("\n\n")
            else:
                self.block(child)

    def inline(self, node: _Node) -> str:
        saved, self.out = self.out, []
        self.block(node)
        text, self.out = "".join(self.out), saved
        return " ".join(text.split())

    def table(self, node: _Node) -> str:
        rows = []
        for tr in _elements(node):
            if tr.tag != "tr":
                continue
            cells = [
                self.inline(c) for c in tr.children
                if isinstance(c, _Node) and c.tag in ("td", "th")
            ]
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    def note_link(self, node: _Node) -> None:
        href = node.attrs.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return
        url = urljoin(self.base_url, href) if self.base_url else href
        if urlsplit(url).scheme not in ("http", "https") or url in self.links:
            return
        text = _plain(node)
        if len(text) >= 2 and len(self.links) < _MAX_LINKS:
            self.links[url] = text[:80]


def extract(html_content: str, base_url: str = "") -> str:
    """Il corpo della pagina in markdown leggero; ``""`` se non c'è niente da dire."""
    if not html_content or len(html_content) > _MAX_HTML_CHARS:
        return ""
    builder = _TreeBuilder()
    builder.feed(html_content)
    builder.close()
    root = builder.root
    _drop_never(root)
    body = _choose_body(root)

    renderer = _Renderer(base_url)
    renderer.block(body)
    lines = [" ".join(line.split()) for line in "".join(renderer.out).split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return ""

    title = " ".join(builder.title.split())
    if title and not text.startswith("# "):
        text = f"# {title}\n\n{text}"
    if renderer.links:
        listed = "\n".join(f"- {t}: {u}" for u, t in renderer.links.items())
        text = f"{text}\n\nLinks:\n{listed}"
    return text
