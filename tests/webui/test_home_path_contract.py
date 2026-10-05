"""Il nome del quaderno nel percorso non si riduce a una lettera.

Misurato in Chrome sulle pagine di un quaderno dal nome lungo: a 360 px del
nome restavano 15 px («U.»), a 320 niente; l'interruttore «Chat | Pagine 1»
era largo 172 px e non cedeva. Dopo: 90 px a 360 (otto lettere e i puntini),
50 a 320; a 590, la larghezza del Titan 2, niente cambia (239 px, 172 px
l'interruttore).
"""

from __future__ import annotations

import re
from pathlib import Path

from support import css_levels

CSS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "home-style.css"


def _rules(selector: str):
    for selectors, body, context in css_levels.rules(CSS.read_text(encoding="utf-8")):
        if selector in {" ".join(s.split()) for s in selectors.split(",")}:
            yield body, context


def test_where_you_are_keeps_a_minimum_width() -> None:
    body = "\n".join(b for b, ctx in _rules(".home-path-here") if not ctx)
    m = re.search(r"min-width:\s*([\d.]+)em", body)
    assert m and float(m.group(1)) >= 4, "il nome del quaderno puo' tornare a zero"
