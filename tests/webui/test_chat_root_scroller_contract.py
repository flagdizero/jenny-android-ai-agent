"""Lo scroller della chat è il documento: le condizioni statiche.

Al tocco di un manico di selezione Chromium ri-deriva l'estremo fermo con un
hit-test dalle sue coordinate di schermo (``OnDragBegin`` →
``SelectBetweenCoordinates``), e quel hit-test ignora **solo** il ritaglio del
viewport (``kIgnoreClipping`` allarga l'area al documento, ma
``PaintLayerClipper`` salta il clip soltanto per il root layer). Il testo
scrollato fuori da uno scroller interno è irraggiungibile: la base finiva sul
composer e la selezione si prendeva tutto. Misurato con tre pagine di prova in
``.agent/selection-rig``; ragionamento in ``.agent/chat-selection-root-plan.md``.

Le condizioni sono tutte proprietà del sorgente, quindi si verificano qui:
in ``mode-chat`` scorre ``html`` e nessun antenato del testo ritaglia; la chrome
sta ferma con ``sticky``; finché c'è una selezione la chrome esce dal hit-test;
selezionabile è solo il testo dei messaggi; lo scroll si legge da un punto solo.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"
APP_JS = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
CHAT_JS = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
PAGER_JS = (ASSETS / "shared" / "history-pager.js").read_text(encoding="utf-8")
CSS = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
SELECTION_JS = (ASSETS / "shared" / "selection.js").read_text(encoding="utf-8")
OFFICINA_HTML = (UI / "officina.html").read_text(encoding="utf-8")

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "param", "source", "track", "wbr"}


def _rule(selector: str) -> str:
    """Il corpo della regola CSS con esattamente questo selettore (o gruppo)."""
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"regola `{selector}` non trovata"
    return m.group(1)


def _declares(selector: str, prop: str, value: str) -> bool:
    return re.search(rf"{re.escape(prop)}\s*:\s*{re.escape(value)}\s*;", _rule(selector)) is not None


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async |get )?{name}\(([^)]*)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(2)


def _code_only(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


class _Ancestry(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str | None] = []
        self.ancestors: dict[str, list[str]] = {}
        self.attrs: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        node_id = a.get("id")
        if node_id:
            self.ancestors[node_id] = [x for x in self.stack if x]
            self.attrs[node_id] = a
        if tag not in _VOID:
            self.stack.append(node_id)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _VOID and self.stack:
            self.stack.pop()


def _parsed() -> _Ancestry:
    parser = _Ancestry()
    parser.feed(OFFICINA_HTML)
    return parser


# ── In chat scorre il documento ──────────────────────────────────────────────


def test_in_chat_mode_the_document_scrolls() -> None:
    assert _declares(":root.mode-chat", "overflow-y", "auto")
    assert _declares(":root.mode-chat body", "position", "static")


def test_no_ancestor_of_the_text_clips_in_chat_mode() -> None:
    """Il hit-test rispetta il clip di ogni scroller interno: uno solo che
    ritaglia, e il testo uscito dalla sua scatola torna irraggiungibile."""
    assert _declares(":root.mode-chat body", "overflow", "visible")
    assert _declares(":root.mode-chat .app", "overflow", "visible")
    assert _declares(":root.mode-chat .body,\n:root.mode-chat .main", "overflow", "visible")
    assert _declares(":root.mode-chat .chat-area", "overflow", "visible")


def test_the_shell_grows_with_the_chat_instead_of_clipping_it() -> None:
    app = _rule(":root.mode-chat .app")
    assert "height: auto" in app and "min-height: var(--vv-height" in app
    view = _rule(":root.mode-chat #view-chat")
    assert "height: auto" in view and "min-height:" in view


def test_the_view_height_is_no_longer_inline() -> None:
    """Un `height:100%` inline vincerebbe sulla regola di `mode-chat`."""
    view = _parsed().attrs["view-chat"]
    assert "height" not in (view.get("style") or "")
    assert "flex-direction: column" in _rule("#view-chat")


def test_the_viewport_height_travels_as_a_variable() -> None:
    setup = _method(APP_JS, "setupViewportHeight")
    assert "setProperty('--vv-height'" in setup
    assert "app.style.height" not in setup
    assert "height: var(--vv-height" in _rule(".app")


def test_the_reset_scroll_stays_out_of_chat_mode() -> None:
    """In chat lo scroll è la posizione di lettura: un resize non la azzera."""
    setup = _method(APP_JS, "setupViewportHeight")
    line = next(ln for ln in setup.splitlines() if "scrollTo(0, 0)" in ln)
    assert "mode-chat" in line


# ── La chrome sta ferma in flusso ────────────────────────────────────────────


def test_the_bottom_stack_and_the_dock_are_sticky() -> None:
    assert _declares(".chat-bottom", "position", "sticky")
    assert "bottom: var(--dock-height)" in _rule(".chat-bottom")
    assert _declares(".dock", "position", "sticky")


def test_the_bottom_stack_wraps_everything_that_stays_put() -> None:
    parsed = _parsed()
    for node_id in ("subagents", "attach-preview", "input-bar", "chat-scroll-fab"):
        assert "chat-bottom" in parsed.ancestors[node_id], node_id
    assert "view-chat" in parsed.ancestors["chat-bottom"]


def test_the_fab_follows_the_composer_without_measuring() -> None:
    fab = _rule(".chat-scroll-fab")
    assert "top: -" in fab and "bottom: 74px" not in fab


def test_viewport_anchored_surfaces_become_fixed_in_chat_mode() -> None:
    rule = _rule(
        ":root.mode-chat .jenny-duo,\n:root.mode-chat .drawer,\n"
        ":root.mode-chat .drawer-backdrop,\n:root.mode-chat .swipe-scrim"
    )
    assert "position: fixed" in rule


# ── La chrome esce dal hit-test finché c'è una selezione ─────────────────────


def test_chrome_is_hit_transparent_while_selecting() -> None:
    rule = _rule(
        ":root.has-selection .chat-bottom,\n:root.has-selection .dock,\n"
        ":root.has-selection .jenny-duo"
    )
    assert "pointer-events: none" in rule


def test_the_selection_state_is_exposed_and_taps_forwarded() -> None:
    assert "export const SELECTING_CLASS = 'has-selection';" in SELECTION_JS
    assert "export function exposeSelectionState(" in SELECTION_JS
    assert "export function forwardTapsThroughChrome(" in SELECTION_JS
    assert "exposeSelectionState();" in APP_JS
    assert "forwardTapsThroughChrome(['.chat-bottom', '.dock']);" in APP_JS


def test_nothing_writes_the_selection_from_js() -> None:
    """Ogni scrittura da JS congeda manici e barra di sistema: la selezione è
    del motore, e il motore la deve poter ri-derivare da solo."""
    code = _code_only(SELECTION_JS)
    for forbidden in ("setBaseAndExtent", "addRange(", "selectAllChildren"):
        assert forbidden not in code, forbidden
    assert "pinSelectionAnchor" not in SELECTION_JS


# ── Selezionabile è solo il testo dei messaggi ───────────────────────────────


def test_only_message_text_is_selectable() -> None:
    """Misurato con "Seleziona tutto": senza il `none` anche sulla chrome
    ferma si evidenziavano i chip del composer e il fiore del dock."""
    assert _declares(".chat-area", "user-select", "none")
    assert _declares(".chat-area .chat-content", "user-select", "text")
    assert _declares(".chat-bottom, .dock", "user-select", "none")
    assert _declares("#chat-input", "user-select", "text")


# ── Lo scroll si legge da un punto solo ──────────────────────────────────────


def test_the_scroller_is_the_document() -> None:
    getter = _method(CHAT_JS, "_scroller")
    assert "document.scrollingElement" in getter


def test_every_scroll_read_goes_through_the_getter() -> None:
    """Il guard di `_rememberScrollAnchor` è l'unica lettura legittima
    sull'area: è la scatola della chat a valere 0 a vista nascosta, non il
    viewport."""
    reads = re.findall(r"this\.chatArea\.(scrollTop|scrollHeight|clientHeight)", _code_only(CHAT_JS))
    assert reads == ["clientHeight"], reads
    remember = _method(CHAT_JS, "_rememberScrollAnchor")
    assert "this.chatArea.clientHeight" in remember


def test_scroll_and_touch_listeners_moved_with_the_scroller() -> None:
    listeners = _method(CHAT_JS, "setupEventListeners")
    assert "window.addEventListener('scroll'" in listeners
    assert "this.chatArea.addEventListener('scroll'" not in CHAT_JS
    assert "document.getElementById('view-chat')" in listeners
    assert "visualViewport?.addEventListener('resize'" in listeners
    # Lo scorrimento infinito è passato al modulo condiviso con la casa, ma il
    # contratto è lo stesso e qui resta la metà che riguarda l'officina: chi
    # *emette* l'evento è `window`, chi si *misura* è il documento. Sono due
    # oggetti diversi e scambiarli è il difetto che questo file esiste per
    # prendere — `.chat-area` non emette scroll e non ha le misure giuste.
    pager = _method(CHAT_JS, "constructor")
    assert "listenOn: window," in pager
    assert "scroller: () => this._scroller," in pager
    assert "this._host.listenOn.addEventListener('scroll'" in PAGER_JS
