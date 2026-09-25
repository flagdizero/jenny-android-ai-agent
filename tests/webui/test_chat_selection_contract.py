"""Selezionare testo in chat: le condizioni che lo rendono possibile.

Il long-press non è un gesto nostro. Chromium lo emette dopo ~500 ms di tenuta
entro il proprio touch slop (~8 dp, cioè 20-24 px reali), ma lo **scarta** se la
pagina chiama ``preventDefault()`` su un ``touchmove`` di quella sequenza.
``setupSwipeNav`` lo faceva dopo 10 px — meno della metà — quindi la SPA
decideva "è uno swipe" mentre Android stava ancora decidendo "è una pressione
ferma", e la selezione non si apriva quasi mai.

Le altre due condizioni sono di scrittura: una bolla riscritta a ogni frame
(``_flushRender``) e un autoscroll che riparte sul ``touchend``
(``scrollToBottom``) portano via una selezione appena nata.

Asserzioni sul sorgente, come ``test_back_navigation_contract.py``: la WebUI non
ha un runner con DOM, e queste sono tutte proprietà statiche del file.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"
APP_JS = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
CHAT_JS = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
SELECTION_JS = (ASSETS / "shared" / "selection.js").read_text(encoding="utf-8")
# Il riconoscimento del gesto e' uscito da mobile-app.js il 22/09/2026: la
# soglia e la dominanza vivono nel modulo condiviso, che li tiene per tutti e
# due i gusci (v. test_gesto_orizzontale_contract.py).
GESTO_JS = (ASSETS / "shared" / "horizontal-swipe.js").read_text(encoding="utf-8")
OFFICINA_HTML = (UI / "officina.html").read_text(encoding="utf-8")
ANDROID_ASSETS = (ROOT / "jenny" / "utils" / "android_assets.py").read_text(encoding="utf-8")

# Elementi HTML senza tag di chiusura: senza questo elenco lo stack del parser
# non tornerebbe mai indietro e ogni dialog sembrerebbe annidato in un `<meta>`.
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "param", "source", "track", "wbr"}


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\(([^)]*)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(2)


class _Ancestry(HTMLParser):
    """Gli id degli antenati di ogni elemento con un id, per nome di tag."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str | None] = []
        self.ancestors: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node_id = dict(attrs).get("id")
        if node_id:
            self.ancestors[node_id] = [a for a in self.stack if a]
        if tag not in _VOID:
            self.stack.append(node_id)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _VOID and self.stack:
            self.stack.pop()


def _ancestor_ids(node_id: str) -> list[str]:
    parser = _Ancestry()
    parser.feed(OFFICINA_HTML)
    assert node_id in parser.ancestors, f"#{node_id} non esiste in officina.html"
    return parser.ancestors[node_id]


# ── Il modulo ────────────────────────────────────────────────────────────────


def test_selection_module_exports_the_three_questions() -> None:
    for name in ("hasSelection", "selectionInside", "onSelectionChange"):
        assert f"export function {name}(" in SELECTION_JS, name


def _code_only(source: str) -> str:
    """Il sorgente senza commenti: qui i commenti *nominano* ciò che vietano."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


def test_selection_never_stringifies_the_range() -> None:
    """`toString()` su una selezione è O(n), e questo gira a ogni frame."""
    assert ".toString()" not in _code_only(SELECTION_JS)
    assert "isCollapsed" in SELECTION_JS


def test_a_selection_in_the_composer_does_not_count() -> None:
    """Il composer è una `<textarea>`: bloccherebbe il rendering a tempo indeterminato."""
    for field in ("INPUT", "TEXTAREA", "isContentEditable"):
        assert field in SELECTION_JS, field


def test_selection_module_is_shipped_to_android() -> None:
    """Un asset fuori da `_UI_MANIFEST` su Android non viene estratto: 404 muto."""
    assert '"assets/shared/selection.js"' in ANDROID_ASSETS


# ── Passo 2: il gesto torna alla piattaforma ─────────────────────────────────


def test_swipe_nav_stands_down_when_something_is_selected() -> None:
    """La guardia sta fra le condizioni per partire, non dopo.

    Adesso il gesto e' condiviso e il guscio dice la sua in `canStart`: se
    quella guardia scivolasse piu' in basso, trascinare per aggiustare i manici
    della selezione farebbe scivolare la vista sotto le dita.
    """
    nav = _method(APP_JS, "setupSwipeNav")
    puo_iniziare = nav.split("canStart:", 1)[1].split("onHorizontal:", 1)[0]
    assert "if (hasSelection()) return false;" in puo_iniziare


def test_horizontal_slop_clears_the_android_touch_slop() -> None:
    slop = re.search(r"const AXIS_THRESHOLD = (\d+);", GESTO_JS)
    assert slop, "AXIS_THRESHOLD non trovata"
    assert int(slop.group(1)) >= 20, "sotto il touch slop di sistema il long-press muore"


def test_a_diagonal_drag_no_longer_arms_the_swipe() -> None:
    assert "Math.abs(dx) <= Math.abs(dy) * 1.5" in GESTO_JS


# ── Passo 3: non si scrive sotto le dita ─────────────────────────────────────


def test_the_streaming_rewrite_is_guarded_by_the_selection() -> None:
    flush = _method(CHAT_JS, "_flushRender")
    assert flush.count("selectionInside(") == 2, "testo e ragionamento, entrambi"
    assert "this._deltaDirty = false;" in flush


def test_autoscroll_yields_to_a_live_selection() -> None:
    scroll = _method(CHAT_JS, "scrollToBottom")
    exit_line = next(ln for ln in scroll.splitlines() if "if (!force" in ln)
    assert "hasSelection()" in exit_line, "deve stare nella stessa uscita di _userTouching"


def test_a_frozen_render_is_re_armed_when_the_selection_drops() -> None:
    assert "onSelectionChange((active)" in CHAT_JS
    assert "this._scheduleFlush()" in CHAT_JS


# ── Passo 4-5: l'affordance ──────────────────────────────────────────────────


def test_the_actions_row_reaches_history_too() -> None:
    """Tre percorsi. Con il solo `_handleTurnEnd`, riaprire l'app lascia zero Copia."""
    for owner in ("_handleTurnEnd", "_flushPersistedTurn", "_handleMessage"):
        assert "_appendMsgActions(" in _method(CHAT_JS, owner), owner


def test_the_actions_row_is_idempotent_and_last() -> None:
    body = _method(CHAT_JS, "_ensureMsgActions")
    assert "':scope > .chat-msg-actions'" in body
    assert "msg.appendChild(row)" in body, "la riga non viene rimessa in coda"


def test_the_message_sheet_is_gone_with_its_button() -> None:
    """Il `⋯` e il foglio «Copia testo / Copia come Markdown» se ne vanno
    insieme: era l'unica cosa che quel pulsante apriva, e l'unico modo di
    aprirla.

    Resta un Copia solo, e copia il **sorgente** — che era la voce «Copia come
    Markdown», cioè quella per cui il foglio era stato scritto.
    """
    for sparito in ("chat-msg-more", "_showMessageSheet", "_messagePlain",
                    "chat-msg-sheet", "ti-dots"):
        assert sparito not in CHAT_JS, f"{sparito} è ancora in mobile-chat.js"
    assert "chat-msg-sheet" not in OFFICINA_HTML
    body = _method(CHAT_JS, "_copyMessage")
    assert "markdown" not in body, "_copyMessage ha ancora la scelta che il foglio le dava"


def test_no_inline_handlers_were_added() -> None:
    """La CSP della shell è `script-src 'self'`: un `onclick=` inline non gira."""
    assert "onclick=" not in OFFICINA_HTML


def test_the_sheet_strings_left_with_the_sheet() -> None:
    """Tre chiavi che nessuno legge più sono tre traduzioni da mantenere per
    niente — e il posto in cui una stringa morta torna a schermo."""
    morte = ("messageActions", "copyPlain", "copyMarkdown")
    for lang in ("it", "en"):
        chat = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))["chat"]
        for key in morte:
            assert key not in chat, f"{lang}.chat.{key} è rimasta orfana"
        # E quella che resta c'è ancora, in tutte e due.
        for key in ("copy", "copied", "copyFailed"):
            assert chat.get(key), f"{lang}.chat.{key}"


def test_the_select_sheet_and_the_anchor_pin_are_gone() -> None:
    """Il foglio era uno scroller interno e riproduceva il difetto al suo
    interno; il pin era un'euristica in JS su un difetto del motore. La radice
    sta in `test_chat_root_scroller_contract.py`."""
    assert "chat-select-sheet" not in OFFICINA_HTML
    assert "_showSelectSheet" not in CHAT_JS
    assert "pinSelectionAnchor" not in SELECTION_JS and "pinSelectionAnchor" not in APP_JS
    assert "setBaseAndExtent" not in _code_only(SELECTION_JS), "nessuna scrittura della selezione da JS"
    for lang in ("it", "en"):
        chat = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))["chat"]
        assert "selectText" not in chat and "selectAll" not in chat, lang
