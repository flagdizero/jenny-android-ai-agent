"""In chat le formule in riga ``$…$`` restano spente.

«Costa $5, forse $10» non è matematica. ``renderRich`` le accende solo con
``{ inlineDollar: true }``, che il lettore delle pagine wiki passa e la chat no.
Fino al 24/09/2026 lo garantiva un involucro in ``mobile-chat.js`` con una sola
firma; tolto l'involucro, lo garantisce questo banco: ogni chiamata ha un solo
argomento.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = ROOT / "jenny" / "templates" / "ui" / "assets" / "mobile-chat.js"


def _calls(source: str) -> list[str]:
    """Gli argomenti di ogni ``renderRich(...)``, con le parentesi bilanciate."""
    out = []
    for m in re.finditer(r"\brenderRich\(", source):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        out.append(source[m.end():i - 1])
    return out


def test_every_chat_render_passes_only_the_container() -> None:
    calls = _calls(CHAT.read_text(encoding="utf-8"))
    assert len(calls) >= 6, "le chiamate non si trovano piu'"
    for args in calls:
        assert "," not in args and "inlineDollar" not in args, args
