"""La selezione di testo si chiude quando si esce dalla finestra, e prima di Home.

Sul Titan 2, uscendo verso un'altra app e tornando con Home (o dal selettore
file), la barra nativa Copia/Condividi/Seleziona tutto riappariva sopra il
composer senza niente di evidenziato, in casa e in officina. Chromium ripristina
la barra di una selezione che al rientro pensa ancora viva, mentre la pagina
nascosta aveva gia' cambiato vista (v. ``releaseSelectionOnBlur`` in
``shared/selection.js``).
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, member, requires_node, run_js

_SELECTION = ASSETS / "shared" / "selection.js"


@requires_node
def test_blur_clears_the_selection_and_can_be_detached() -> None:
    url = _SELECTION.as_uri()
    run_js(f"""
import assert from 'node:assert/strict';

let cleared = 0;
globalThis.document = {{ getSelection: () => ({{ removeAllRanges() {{ cleared += 1; }} }}) }};
const listeners = {{}};
const win = {{
  addEventListener(type, fn) {{ (listeners[type] ||= new Set()).add(fn); }},
  removeEventListener(type, fn) {{ listeners[type]?.delete(fn); }},
}};

const {{ releaseSelectionOnBlur }} = await import('{url}');
const detach = releaseSelectionOnBlur(win);
for (const fn of listeners.blur) fn();
assert.equal(cleared, 1);

detach();
assert.equal(listeners.blur.size, 0);
""")


def test_both_shells_arm_it_and_clear_before_going_home() -> None:
    """``goHome`` cambia vista e conversazione: la selezione va chiusa prima,
    finche' il DOM sotto di lei e' ancora quello a schermo."""
    for name, overlays in (("home-app.js", "_closeAllOverlays"), ("mobile-app.js", "_dismissAllOverlays")):
        source = (ASSETS / name).read_text(encoding="utf-8")
        assert "releaseSelectionOnBlur()" in source, name
        body = member(source, "goHome", body_only=True)
        first = re.search(r"^\s*(?!//)(\S.*)$", body, re.M)
        assert first and first.group(1).startswith("clearSelection()"), (name, body[:200])
        assert body.index("clearSelection()") < body.index(overlays), name
