"""Le crocette piccole hanno un bersaglio da almeno 24 px.

Misurato in Chrome con `elementFromPoint` dal centro verso i quattro lati: la
crocetta dei cassetti e la pulizia della ricerca rispondevano su 22×22 px, la
rimozione di un allegato della casa su 20×20 — sotto i 24 di WCAG 2.5.8. Dopo:
30×30, 32×32 e 32×28 (quest'ultima tagliata in alto dalla fila che scorre, e
sopra soglia lo stesso). Il disegno non cambia: l'area in piu' e' un `::after`
trasparente attorno al bottone.
"""

from __future__ import annotations

import re

from support import css_levels
from support.js_harness import ASSETS

_TARGETS = (
    ("mobile-style.css", ".drawer-close"),
    ("mobile-style.css", ".launcher-close"),
    ("mobile-style.css", ".file-preview-close"),
    ("mobile-style.css", ".session-info-close"),
    ("mobile-style.css", ".launcher-search-clear"),
    ("home-style.css", ".home-pending-x"),
)


def _body(sheet: str, selector: str) -> str:
    out = []
    for selectors, body, context in css_levels.rules((ASSETS / sheet).read_text(encoding="utf-8")):
        if context:
            continue
        if selector in {" ".join(s.split()) for s in selectors.split(",")}:
            out.append(body)
    return "\n".join(out)


def _px(body: str, prop: str) -> float | None:
    m = re.search(rf"(?:^|[;\s]){prop}:\s*(-?[\d.]+)px", body)
    return float(m.group(1)) if m else None


def test_every_small_cross_reaches_24px_of_touch() -> None:
    problems = []
    for sheet, selector in _TARGETS:
        base = _body(sheet, selector)
        side = min(x for x in (_px(base, "width"), _px(base, "height")) if x is not None)
        assert side, (sheet, selector)
        halo = _body(sheet, f"{selector}::after")
        inset = _px(halo, "inset")
        if not halo or inset is None or "position: absolute" not in halo:
            problems.append(f"{sheet}: `{selector}` e' {side:.0f} px e non ha un'area di tocco in piu'")
            continue
        if side - 2 * inset < 24:
            problems.append(f"{sheet}: `{selector}` arriva a {side - 2 * inset:.0f} px")
        if not re.search(r"position:\s*(relative|absolute)", base):
            problems.append(f"{sheet}: `{selector}` non contiene il suo `::after`")
    assert not problems, problems
