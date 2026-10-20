"""I nomi accessibili e la tastiera dei due gusci.

Struttura, non comportamento: il markup di `index.html` e `workshop.html` letto
come un albero. La misura vera — i nomi che l'albero AX di Chrome calcola, e il
Tab con la tastiera fisica — e' nel rapporto della correzione; qui ci sono le
cose che la tengono in piedi e che si rompono in silenzio:

- una `<label>` che avvolge un bottone presta al campo il nome del bottone
  (il composer della casa si chiamava «Allega» per TalkBack);
- una `<label>` senza testo toglie al campo il nome che il segnaposto gli dava;
- un'icona Tabler e' un glifo nell'area privata di Unicode: se non e' nascosta,
  entra nel nome del bottone (« Jenny») o *e'* il nome (`#btn-send`);
- le voci del dock erano `<div>`: la tastiera del Titan 2 non ci arrivava.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"
SHELLS = ("index.html", "workshop.html", "onboarding.html")

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: _Node | None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node] = []
        self.text = ""

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Node(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text += data


def _tree(shell: str) -> _Node:
    t = _Tree()
    t.feed((UI / shell).read_text(encoding="utf-8"))
    return t.root


def _by_id(root: _Node, ident: str) -> _Node:
    found = [n for n in root.walk() if n.attrs.get("id") == ident]
    assert len(found) == 1, f"#{ident}: {len(found)} elementi"
    return found[0]


def _i18n(lang: str) -> dict:
    return json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def _has_key(d: dict, key: str) -> bool:
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return False
        d = d[part]
    return isinstance(d, str) and bool(d.strip())


# ── La label del composer ──────────────────────────────────────────────


def test_no_label_wraps_a_button() -> None:
    """Una `<label>` non puo' contenere un bottone (HTML non valido), e se lo
    contiene il nome del bottone entra in quello del campo."""
    offenders = []
    for shell in SHELLS:
        for label in (n for n in _tree(shell).walk() if n.tag == "label"):
            inner = [n for n in label.walk() if n is not label
                     and n.tag in ("button", "select", "textarea", "input")]
            if any(n.tag == "button" for n in inner) or len(inner) > 1:
                offenders.append(f"{shell}: label.{'.'.join(label.classes())}")
    assert not offenders, offenders


def test_the_home_composer_takes_its_name_from_its_placeholder() -> None:
    """Il campo della casa non ha piu' una `<label>` attorno: il nome e' il
    segnaposto, che `home-app.js` scrive da i18n (nessun `aria-label` italiano
    fisso che la lingua inglese non riscriverebbe)."""
    root = _tree("index.html")
    field = _by_id(root, "home-input")
    assert not any(a.tag == "label" for a in field.ancestors())
    assert "home-field" in field.parent.classes() and field.parent.tag == "div"
    assert "aria-label" not in field.attrs
    app = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    assert re.search(r"this\.input\.placeholder\s*=\s*i18n\.t\(", app), (
        "home-app.js non scrive piu' il segnaposto del campo: il campo resta senza nome"
    )


def test_a_tap_on_the_composer_margin_still_lands_in_the_field() -> None:
    """La `<label>` serviva a una cosa: un tocco sul margine sinistro della
    pastiglia metteva il cursore nel campo. Senza label il margine deve stare
    sul campo stesso, non sulla pastiglia."""
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    field = re.search(r"\n\.home-field \{([^}]*)\}", css).group(1)
    assert re.search(r"padding:\s*0 4px 0 0;", field), field
    inp = re.search(r"\n\.home-input \{([^}]*)\}", css).group(1)
    assert re.search(r"padding:\s*10px 0 10px 14px;", inp), inp


def test_the_board_search_has_a_name() -> None:
    """La lente della bacheca e' un bottone con un'icona sola: il nome glielo
    da' il guscio, tradotto; e il campo che apre porta il segnaposto anche
    come nome, perche' TalkBack non legge un segnaposto da solo."""
    root = _tree("index.html")
    lens = _by_id(root, "home-board-search-open")
    assert lens.tag == "button"
    app = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    assert "this.boardSearchBtn?.setAttribute('aria-label', i18n.t('board.search.title'))" in app
    # Anche il + della bacheca, e i nomi si danno appena i bottoni ci sono.
    assert _by_id(root, "home-board-add").tag == "button"
    assert "this.boardAddBtn?.setAttribute('aria-label', i18n.t('board.add.title'))" in app
    board = (ASSETS / "home-board.js").read_text(encoding="utf-8")
    assert "input.setAttribute('aria-label', i18n.t('board.search.placeholder'))" in board


# ── Icone e nomi ──────────────────────────────────────────────────────


def test_every_tabler_icon_is_hidden_from_the_accessibility_tree() -> None:
    offenders = []
    for shell in SHELLS:
        for n in _tree(shell).walk():
            if n.tag == "i" and "ti" in n.classes() and n.attrs.get("aria-hidden") != "true":
                offenders.append(f"{shell}: i.{'.'.join(n.classes())}")
    assert not offenders, offenders


_WORKSHOP_NAMED = {
    "btn-new-chat": "chat.newChat",
    "btn-attach": "chat.attach",
    "btn-send": "chat.send",
}


def test_the_workshop_icon_buttons_carry_a_translated_name() -> None:
    """Un `title` non basta: per un bottone il nome dal contenuto vince sul
    `title`, e il contenuto era il glifo. `aria-label` + `data-i18n-aria`, che
    `_applyStaticTranslations()` di `mobile-app.js` riscrive nella lingua."""
    root = _tree("workshop.html")
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    assert "querySelectorAll('[data-i18n-aria]')" in app
    for ident, key in _WORKSHOP_NAMED.items():
        node = _by_id(root, ident)
        assert node.attrs.get("aria-label"), ident
        assert node.attrs.get("data-i18n-aria") == key, ident
        for lang in ("it", "en"):
            assert _has_key(_i18n(lang), key), f"{lang}.json: manca {key}"


_HOME_LABELLED = {
    "home-name": "home-name-label",
    "home-jenny-floating": "home-jenny-floating-label",
}


def test_the_home_fields_are_named_by_the_label_they_sit_next_to() -> None:
    """Il nome del campo e' l'etichetta gia' scritta accanto (tradotta dal JS
    della stanza): `aria-labelledby` la segue in ogni lingua da solo."""
    root = _tree("index.html")
    for ident, label in _HOME_LABELLED.items():
        node = _by_id(root, ident)
        assert node.attrs.get("aria-labelledby") == label, ident
        _by_id(root, label)


# ── Il dock dalla tastiera ─────────────────────────────────────────────


def test_the_dock_items_are_buttons() -> None:
    root = _tree("workshop.html")
    dock = next(n for n in root.walk() if n.tag == "nav" and "dock" in n.classes())
    items = [n for n in dock.walk() if "dock-item" in n.classes()]
    # Quattro: la quinta, «Setup», era l'onboarding, che dal 27/09/2026 ha un
    # documento suo.
    assert len(items) == 4
    for n in items:
        assert n.tag == "button" and n.attrs.get("type") == "button", n.attrs
        assert n.attrs.get("data-mode"), n.attrs
    # Il fiore della Console e' testo: fuori dal nome, che e' «Console».
    flower = next(n for n in dock.walk() if "dock-flower" in n.classes())
    assert flower.attrs.get("aria-hidden") == "true"


def test_the_dock_listeners_still_find_the_items() -> None:
    """`mobile-app.js` aggancia il click per classe e `data-mode`, non per
    tag: il bottone li porta tutti e due, e Invio/Spazio su un bottone
    arrivano come lo stesso `click`."""
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    hook = re.search(
        r"querySelectorAll\('\.dock-item\[data-mode\]'\)\.forEach\(item => \{\s*"
        r"item\.addEventListener\('click', \(\) => this\.switchMode\(item\.dataset\.mode\)\);",
        app,
    )
    assert hook, "il click del dock non si aggancia piu' a `.dock-item[data-mode]`"
    assert not re.search(r"querySelectorAll\('div\.dock-item", app)


def test_the_dock_button_wears_no_default_dress() -> None:
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    body = re.search(r"\n\.dock-item \{([^}]*)\}", css).group(1)
    for decl in ("border: none", "background: none", "font: inherit", "margin: 0"):
        assert decl in body, decl


# ── Il FAB nascosto fuori dal Tab ─────────────────────────────────────


def _css_rule(css: str, selector: str) -> str:
    m = re.search(r"\n" + re.escape(selector) + r" \{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_the_hidden_scroll_button_is_out_of_the_tab_order() -> None:
    """A opacita' 0 il bottone «Vai in fondo» restava raggiungibile: misurato
    con Shift+Tab dal campo in Chrome, il fuoco ci si fermava sopra senza
    mostrare niente. `visibility: hidden` lo toglie; la transizione la fa
    scattare dopo la dissolvenza, non prima."""
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    hidden = _css_rule(css, ".chat-scroll-fab")
    shown = _css_rule(css, ".chat-scroll-fab.visible")
    assert "opacity: 0;" in hidden and "visibility: hidden;" in hidden
    assert re.search(r"transition:[^;]*visibility 0s linear 0\.15s", hidden), (
        "senza il ritardo la dissolvenza in uscita non si vede piu'"
    )
    assert "visibility: visible;" in shown
    assert re.search(r"transition:[^;]*visibility 0s[;,]", shown)
