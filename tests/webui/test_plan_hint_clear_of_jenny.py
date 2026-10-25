"""«Annulla il piano» non sta sotto Jenny.

Il 25/10 sul telefono il tocco sul link chiudeva la chat invece di annullare: la
riga del suggerimento si fermava prima della *figura* di Jenny contata con
l'ancoraggio «al bordo», ma il tocco lo prende tutto il suo riquadro, e sopra la
chat lei sta «fuori» (un quarto oltre il bordo). Misura sul WebView: col margine
vecchio il link andava da x 276 a 374 e il riquadro cominciava a 321; col nuovo,
215-317. Qui si fissa che il margine conta dal riquadro.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "home-style.css"


def _rule(selector: str) -> str:
    text = _CSS.read_text(encoding="utf-8")
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    assert found, selector
    return found.group(1)


def test_the_hint_row_stops_before_jennys_whole_box() -> None:
    rule = _rule(".home-shell .home-plan-hint")
    assert "var(--jenny-size" in rule
    # Il riquadro intero che sporge nella pagina, non la figura dentro di lui.
    assert "(1 - var(--jenny-out" in rule
    assert "0.275" not in rule and "--jenny-dock" not in rule


def test_the_plan_card_takes_back_jennys_dodge() -> None:
    """La scheda del piano si riprende lo spazio che il posto vivo lascia alla testa
    di Jenny: larga quanto la colonna, non stretta per tutta la sua altezza."""
    rule = _rule(".home-live > .home-plan")
    assert "width: calc(100% + var(--jenny-dodge" in rule
    assert "margin-right: calc(-1 * var(--jenny-dodge" in rule
