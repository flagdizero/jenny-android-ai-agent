"""Le freccine delle testate del turno restano tenui.

``.chat-thinking-header i, .tool-events-header i`` (specificità 0,1,1) dava
colore accento e 12px all'icona della testata, ma prendeva anche la freccina in
fondo, e vinceva su ``.chat-thinking-chevron`` / ``.tool-events-chevron``
(0,1,0) che la vogliono tenue a 11px. Visto dalla revisione finale della
pulizia (25/09); il confronto degli stili calcolati nel browser ha mostrato
che la correzione cambia solo le due freccine.

Si guardano **tutte** le regole dei due fogli che la chat carica, non la prima
che somiglia: un ``i`` nudo in una regola più in basso (o nel foglio della
casa) riporterebbe il difetto con la regola dell'icona ancora a posto.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS

_SHEETS = ("mobile-style.css", "casa-style.css")

# Le testate e la classe della loro freccina.
_HEADERS = {
    ".chat-thinking-header": "chat-thinking-chevron",
    ".tool-events-header": "tool-events-chevron",
}


def _rules() -> list[tuple[str, list[str], str]]:
    """(foglio, selettori, dichiarazioni) di ogni regola, anche dentro ``@media``.

    Senza commenti: quello sopra la regola dell'icona ne cita i selettori.
    ``[^{}]+`` non attraversa una graffa, quindi di un ``@media { … { … } }``
    si prendono le regole interne con il loro solo selettore.
    """
    out = []
    for name in _SHEETS:
        css = re.sub(r"/\*.*?\*/", "", (ASSETS / name).read_text(encoding="utf-8"), flags=re.S)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selectors = [" ".join(s.split()) for s in m.group(1).split(",")]
            out.append((name, selectors, m.group(2)))
    return out


def _sets_color_or_size(declarations: str) -> bool:
    return bool(re.search(r"(?:^|;)\s*(?:color|font-size)\s*:", declarations))


def _reaches_the_chevron(selector: str) -> bool:
    """Un ``<header> i`` che non esclude la freccina della sua testata."""
    for header, chevron in _HEADERS.items():
        m = re.search(rf"{re.escape(header)} i(\S*)$", selector)
        if m and f":not(.{chevron})" not in m.group(1):
            return True
    return False


def test_no_rule_gives_the_header_icon_style_to_the_chevrons() -> None:
    offenders = [
        (sheet, sel)
        for sheet, selectors, decl in _rules()
        if _sets_color_or_size(decl)
        for sel in selectors
        if _reaches_the_chevron(sel)
    ]
    assert not offenders, offenders


def test_the_header_icon_rule_is_still_there_and_leaves_the_chevrons_out() -> None:
    icon_rules = [
        selectors
        for _, selectors, decl in _rules()
        if re.search(r"font-size:\s*12px", decl) and re.search(r"color:\s*var\(--accent\)", decl)
        and any(s.startswith(tuple(_HEADERS)) for s in selectors)
    ]
    assert icon_rules, "regola dell'icona delle testate non trovata"
    selectors = [s for rule in icon_rules for s in rule]
    assert ".chat-thinking-header i:not(.chat-thinking-chevron)" in selectors, selectors
    assert ".tool-events-header i:not(.tool-events-chevron)" in selectors, selectors
