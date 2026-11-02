"""Un tocco non lascia il bottone del colore del browser.

Su un telefono `:hover` resta acceso dopo un tocco. L'onboarding lo «annullava»
con `revert` sotto `@media (hover: none)`, ma `revert` torna al foglio
dell'agente utente, non allo stile di base del bottone: misurato in Chrome con
il tocco emulato, dopo un tocco «Avanti» passava da `--accent` a
`rgb(107, 107, 107)` con il testo bianco, e restava ingrandito a 1,03. Dopo la
correzione lo stile toccato e' identico a quello a riposo.
"""

from __future__ import annotations

import re

from support import css_levels
from support.js_harness import ASSETS

SHEETS = (ASSETS / "mobile-style.css", ASSETS / "home-style.css", ASSETS / "apps" / "jenny-kit.css")


def test_no_rule_reverts_to_the_browser_sheet() -> None:
    offenders = []
    for sheet in SHEETS:
        for selectors, body, _ in css_levels.rules(sheet.read_text(encoding="utf-8")):
            if re.search(r":\s*revert(-layer)?\s*(;|$)", body):
                offenders.append(f"{sheet.name}: {' '.join(selectors.split())}")
    assert not offenders, (
        f"`revert` torna al foglio del browser, non allo stile di base: {offenders}"
    )


def test_the_onboarding_hover_only_exists_where_there_is_a_mouse() -> None:
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    hovers = [
        (" ".join(selectors.split()), context)
        for selectors, _, context in css_levels.rules(css)
        if ".onboarding-btn" in selectors and ":hover" in selectors
    ]
    assert len(hovers) >= 3, hovers
    for selector, context in hovers:
        assert "@media (hover: hover)" in context, (
            f"`{selector}` si accende anche al tocco, e sul telefono resta acceso"
        )
