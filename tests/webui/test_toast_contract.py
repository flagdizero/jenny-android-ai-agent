"""I toast: opachi, leggibili, e uno sotto l'altro.

Misurato in Chrome, tre toast di fila (successo, errore, info), prima:

- tutti e tre a `top: 50px`: il secondo copriva il primo, il terzo il secondo;
- l'errore bianco su `--error`: 3,61:1 nel tema di serie;
- il successo su `rgba(--ok, 0.10)`: il colore di cio' che aveva sotto, 4,12:1
  in Y2K sulla pagina vuota — e meno sopra una bolla o sopra lei.

Dopo: 50, 95, 155 px, nessuna sovrapposizione, tutti sopra 4,5:1 in Chanel,
Y2K e Fumetto; e quando il primo se ne va gli altri risalgono (50, 95).
Il contrasto dei token (`--on-error` su `--error`) lo misura
`test_home_you_contract.py` in tutti i temi.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import css_levels
from support.js_harness import ASSETS

SHOP = ASSETS / "mobile-style.css"
HOME = ASSETS / "home-style.css"


def _body(path: Path, selector: str, at: str | None = None) -> str:
    out = []
    for selectors, body, context in css_levels.rules(path.read_text(encoding="utf-8")):
        if at is not None and not any(at in c for c in context):
            continue
        if selector in {" ".join(s.split()) for s in selectors.split(",")}:
            out.append(body)
    return "\n".join(out)


def test_toasts_stack_instead_of_covering_each_other() -> None:
    base = _body(SHOP, ".mobile-toast")
    assert "anchor-name: --toast;" in base
    anchored = _body(SHOP, ".mobile-toast", at="@supports (anchor-name")
    assert "position-anchor: --toast;" in anchored
    assert re.search(r"top:\s*calc\(anchor\(bottom,\s*42px\)\s*\+\s*8px\)", anchored), (
        "il primo toast deve restare a 50 px (42 + 8) quando non ha nessuno sopra"
    )


def test_the_success_toast_is_opaque() -> None:
    body = _body(SHOP, ".mobile-toast.success")
    background = re.search(r"background:\s*([^;]+);", body).group(1)
    assert "rgba" not in background and "transparent" not in background, background


def test_nothing_writes_white_on_the_error_colour() -> None:
    for path, selector in ((SHOP, ".mobile-toast.error"), (HOME, ".home-save.is-critical")):
        body = _body(path, selector)
        assert "background: var(--error)" in body, (path.name, selector)
        assert "color: var(--on-error)" in body, (path.name, selector)
        assert not re.search(r"color:\s*(#fff\b|#ffffff\b|white\b)", body), (path.name, selector)
