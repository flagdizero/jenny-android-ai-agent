"""Il kit delle app arma lo scorrimento laterale solo in una pagina della casa.

Il kit si prende il gesto laterale in esclusiva: quando diventa suo, l'app
riceve un annullo e perde il dito fino al rilascio. In una pagina della casa e'
giusto — il guscio ne fa un cambio di pagina. Nel velo a tutto schermo (la
mini-app aperta, in casa e in officina) quel gesto non lo ascolta nessuno, e
l'app perdeva il dito a ogni movimento di lato, per niente.

La cornice del velo porta ``overlay=1`` (``frameForApp(slug, {overlay: true})``);
il kit vero gira qui in node e si guarda se va a cercare il modulo del gesto.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, requires_node, run_js

pytestmark = requires_node

SDK = (ASSETS / "apps" / "jenny-sdk.js").read_text(encoding="utf-8")
ACTIONS = (ASSETS / "shared" / "apps-actions.js").read_text(encoding="utf-8")


def _kit_cerca_il_gesto(query: str) -> bool:
    out = run_js(
        f"""
const cercati = [];
const VeroURL = URL;
globalThis.URL = class extends VeroURL {{
  constructor(u, base) {{ super(u, base); cercati.push(String(u)); }}
}};
globalThis.location = {{
  search: {json.dumps(query)}, pathname: '/apps/spesa/index.html',
  href: 'http://127.0.0.1:8080/apps/spesa/index.html' + {json.dumps(query)},
}};
const root = {{ style: {{ setProperty() {{}} }}, setAttribute() {{}}, lang: '' }};
globalThis.document = {{
  documentElement: root,
  addEventListener() {{}},
  querySelectorAll: () => [],
}};
globalThis.MutationObserver = class {{ observe() {{}} }};
globalThis.window = globalThis;
globalThis.addEventListener = () => {{}};
globalThis.parent = {{ postMessage() {{}} }};
{SDK}
await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify(cercati.some((u) => u.endsWith('horizontal-swipe.js'))));
"""
    )
    return json.loads(out.strip().splitlines()[-1])


def test_a_casa_page_arms_the_swipe() -> None:
    assert _kit_cerca_il_gesto("?token=t&theme=dark") is True


def test_the_full_screen_overlay_does_not() -> None:
    assert _kit_cerca_il_gesto("?token=t&theme=dark&overlay=1") is False


def test_only_the_overlay_frame_says_so() -> None:
    """Il velo lo dice, la pagina della casa no: il default e' la pagina, cosi'
    ``home-pages.js`` non deve passare niente."""
    assert "frameForApp(slug, { overlay: true })" in ACTIONS
    home = (ASSETS / "home-pages.js").read_text(encoding="utf-8")
    assert "overlay" not in home
