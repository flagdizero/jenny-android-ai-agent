"""Jenny c'e' sempre: «Mostra la mascotte» non esiste piu'.

Dal 05/10/2026 la mascotte e' la porta della chat (il tocco la porta su sopra
la pagina), e una porta che si spegne lascia la chat senza strada. La
preferenza e' andata via tutta: il getter, la riga nella stanza Jenny, il
primo fotogramma di ``bootstrap.js``, le regole che la nascondevano. La chiave
in ``localStorage`` e' fra quelle morte, cosi' un telefono che l'aveva spenta
la ritrova accesa e pulita.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"


def test_the_visibility_keys_are_dead_keys() -> None:
    src = (ASSETS / "shared" / "mascot.js").read_text(encoding="utf-8")
    dead = re.search(r"const DEAD_KEYS = \[(.*?)\];", src, re.S).group(1)
    for key in ("'jenny-mascot-visible'", "'jenny-mascotte-visible'"):
        assert key in dead, f"{key} non e' fra le chiavi morte"
    assert "export function mascotVisible" not in src
    assert "export function setMascotVisible" not in src


def test_nothing_can_hide_her_any_more() -> None:
    """Nessuna regola la nasconde per preferenza. Resta solo la regola del
    paesaggio basso dell'officina, che e' geometria, non una scelta."""
    for name in ("mobile-style.css", "home-style.css", "bootstrap.js", "mobile-jenny.js",
                 "shared/jenny-mascot.js", "shared/jenny-gap.js", "home-jenny.js"):
        text = (ASSETS / name).read_text(encoding="utf-8")
        for word in ("hidden-mode", "data-mascot-hidden", "mascotVisible"):
            assert word not in text, f"{name} parla ancora di {word}"
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "home-jenny-visible" not in html, "la riga «Mostra la mascotte» e' ancora nella stanza"
