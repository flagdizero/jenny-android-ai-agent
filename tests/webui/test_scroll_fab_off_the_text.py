"""Il «↓» della chat non sta sopra il testo.

Il 25/10/2026 sul telefono il tondo, 12 px sopra il composer e al centro, copriva
due righe della colonna del testo. Provate due posizioni sulla casa vera: nel
margine sinistro copriva l'inizio delle righe di Jenny; a cavallo del bordo alto
del composer copre al più metà dell'ultima riga, che il bordo taglia comunque.
Qui si fissa la seconda: il fondo del tondo scende sotto il bordo di metà della
sua altezza.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "home-style.css"


def _px(rule: str, prop: str) -> str:
    found = re.search(rf"(?<![-\w]){prop}:\s*([^;]+);", rule)
    assert found, prop
    return found.group(1).strip()


def test_the_scroll_button_straddles_the_composer_edge() -> None:
    text = _CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.home-scroll-fab\s*\{([^}]*)\}", text).group(1)
    height = int(_px(rule, "height").removesuffix("px"))
    bottom = _px(rule, "bottom")
    assert "var(--home-composer-h" in bottom and "var(--home-plan-hint-h" in bottom
    assert bottom.endswith(f"- {height // 2}px)"), bottom
