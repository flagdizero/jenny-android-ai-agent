"""Officina: la chat sopra una vista non porta un secondo scroller.

Dal 05/10/2026 toccare Jenny fuori dalla Console porta su la chat a tutto
schermo sopra la vista (``MobileApp.openChatOver``). Il rischio e' uno solo e
grosso: il difetto dei manici di selezione, chiuso facendo del **documento**
lo scroller della chat (v. ``test_chat_root_scroller_contract.py``). Una chat
in un pannello fisso con il suo scroller lo riporterebbe. Qui si tiene ferma la
forma che lo evita: ``mode-chat`` resta su ``<html>``, la vista sotto si fissa
allo schermo, e nessuna regola di ``chat-over`` ritaglia la chat.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import css_levels

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
APP = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
CSS = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")


def _method(src: str, name: str) -> str:
    m = re.search(rf"\n  {re.escape(name)}\([^)]*\) \{{(.*?)\n  \}}\n", src, re.S)
    assert m, f"{name} non trovato"
    return m.group(1)


def _chat_over_rules() -> list[tuple[str, str]]:
    return [(sel, body) for sel, body, _ctx in css_levels.rules(CSS) if ".chat-over" in sel]


def test_the_overlay_keeps_the_document_as_the_chat_scroller() -> None:
    body = _method(APP, "openChatOver")
    assert "root.classList.add('mode-chat', 'chat-over')" in body
    # Classe e `display` nello stesso blocco, prima che la chat si attivi:
    # una misura presa in mezzo vedrebbe una pagina che non scorre.
    shown = body.index("viewElement('chat').style.display = 'flex'")
    classes = body.index("root.classList.add('mode-chat', 'chat-over')")
    assert 0 < classes - shown < 200
    assert classes < body.index("chat.activate()")


def test_closing_takes_display_and_classes_away_together() -> None:
    body = _method(APP, "closeChatOver")
    hidden = body.index("viewElement('chat').style.display = 'none'")
    assert hidden < body.index("this._leaveChatOverClasses()") < body.index("root.classList.remove('mode-chat')")


def test_no_chat_over_rule_clips_the_chat_or_its_ancestors() -> None:
    clipping = re.compile(r"\b(contain|clip-path|mask)\s*:|overflow(-[xy])?\s*:\s*(?!visible)")
    for sel, body in _chat_over_rules():
        if ".view-beneath" in sel:
            continue  # la vista sotto non contiene la chat
        assert not clipping.search(body), f"{sel} ritaglia: {body.strip()}"
        if "#view-chat" in sel:
            assert not re.search(r"position\s*:\s*(fixed|absolute)", body), f"{sel} sposta la chat fuori dal flusso"


def test_the_view_beneath_is_pinned_inert_and_unselectable() -> None:
    rule = next(body for sel, body in _chat_over_rules() if ".view-beneath" in sel)
    assert "position: fixed" in rule
    assert "pointer-events: none" in rule
    assert "user-select: none" in rule


def test_the_levels_stay_under_her_and_in_order() -> None:
    levels = dict(css_levels.levels(CSS))
    over = {k: v for k, v in levels.items() if "chat-over" in k}
    assert over and all(v < 120 for v in over.values()), f"sopra Jenny: {over}"
    chat = levels[":root.chat-over #view-chat"]
    assert chat < levels[":root.chat-over .dock"] < levels[":root.chat-over .drawer-backdrop"] \
        < levels[":root.chat-over .drawer"]
    app_chat = levels[":root.chat-over-app #view-chat"]
    assert 110 < app_chat < 115, "sopra una mini-app la chat sta fra l'app e la lightbox"


def test_the_layer_sits_where_the_chat_really_is() -> None:
    body = _method(APP, "_overlayLayers")
    assert "...(overApp ? [chatOver] : [])" in body
    assert "...(overApp ? [] : [chatOver])" in body
    # Sopra una mini-app prima dell'app; sopra una vista dopo le tendine.
    assert body.index("...(overApp ? [chatOver] : [])") < body.index("name: 'miniapp'")
    assert body.index("name: 'composeMenu'") < body.index("...(overApp ? [] : [chatOver])")


def test_the_carousel_does_not_move_the_view_under_the_chat() -> None:
    assert "if (this._chatOver === true) return false;" in APP


def test_a_drawer_closing_does_not_wake_the_view_beneath() -> None:
    drawer = (ASSETS / "mobile-drawer.js").read_text(encoding="utf-8")
    assert "child.inert = on || child.classList.contains('view-beneath');" in drawer
