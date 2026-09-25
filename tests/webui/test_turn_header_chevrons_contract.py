"""Le freccine delle testate del turno restano tenui.

``.chat-thinking-header i, .tool-events-header i`` (specificità 0,1,1) dava
colore accento e 12px all'icona della testata, ma prendeva anche la freccina in
fondo, e vinceva su ``.chat-thinking-chevron`` / ``.tool-events-chevron``
(0,1,0) che la vogliono tenue a 11px. Visto dalla revisione finale della
pulizia (25/09); il confronto degli stili calcolati nel browser ha mostrato
che la correzione cambia solo le due freccine.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS

# Senza commenti: quello sopra la regola ne cita i selettori.
CSS = re.sub(r"/\*.*?\*/", "", (ASSETS / "mobile-style.css").read_text(encoding="utf-8"), flags=re.S)


def test_the_header_icon_rule_leaves_the_chevrons_out() -> None:
    m = re.search(r"^([^{}\n/][^{}]*?)\{\s*font-size:\s*12px;\s*color:\s*var\(--accent\);", CSS, re.M)
    assert m, "regola dell'icona delle testate non trovata"
    selectors = [s.strip() for s in m.group(1).split(",")]
    assert ".chat-thinking-header i:not(.chat-thinking-chevron)" in selectors, selectors
    assert ".tool-events-header i:not(.tool-events-chevron)" in selectors, selectors
    assert not any(s in (".chat-thinking-header i", ".tool-events-header i") for s in selectors)
