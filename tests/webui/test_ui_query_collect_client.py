"""Lo strumento ``ui_view`` vede davvero lo schermo, anche nei tre cassetti.

``UiQueryResponder._collect`` cercava ``getElementById('view-' + view)``: per
``brain``, ``hands`` e ``memory`` quell'id non esiste (sono tutti e tre
``view-settings``), quindi Jenny riceveva un HTML vuoto proprio dove l'utente le
chiede «cosa vedi?». E la mini-app aperta si cercava in ``controllers.apps`` con
``view === 'apps'``: la scheda «App» che non esiste piu', quindi l'app non le
arrivava mai.

Il metodo vero gira in node, con l'``viewElement`` vero di mobile-settings.js.
"""

from __future__ import annotations

from support.js_harness import ASSETS, function, member, requires_node, run_js

pytestmark = requires_node

QUERY_SRC = (ASSETS / "mobile-ui-query.js").read_text(encoding="utf-8")
SETTINGS_SRC = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")


def _script(body: str) -> str:
    vista_di = next(
        line for line in SETTINGS_SRC.splitlines() if line.startswith("export const VIEW_OF")
    ).replace("export ", "")
    return f"""
import assert from 'node:assert/strict';

{vista_di}
const views = {{
  'view-settings': {{ outerHTML: '<div id="view-settings">cassetto</div>' }},
  'view-chat': {{ outerHTML: '<div id="view-chat">chat</div>' }},
}};
globalThis.document = {{ getElementById: (id) => views[id] ?? null }};
{function(SETTINGS_SRC, "viewElement")}
const AppState = {{ currentMode: 'chat' }};
globalThis.window = {{ mobileApp: {{ drawer: {{ activeDrawer: null }} }} }};

class Responder {{
  _pruneHtml(s) {{ return s; }}
{member(QUERY_SRC, "_collect")}
}}
const r = new Responder();

{body}
console.log('ok');
"""


def test_each_drawer_sends_the_settings_view() -> None:
    out = run_js(
        _script(
            """
for (const mode of ['brain', 'hands', 'memory']) {
  AppState.currentMode = mode;
  const p = await r._collect();
  assert.equal(p.view, mode);
  assert.match(p.html, /cassetto/, `${mode}: HTML vuoto`);
}
AppState.currentMode = 'chat';
assert.match((await r._collect()).html, /chat/);
"""
        )
    )
    assert out.strip() == "ok"


def test_an_open_miniapp_is_described_over_any_view() -> None:
    out = run_js(
        _script(
            """
AppState.currentMode = 'hands';
window.mobileApp._appsSource = { jennyApps: [{ slug: 'spesa', name: 'Spesa' }] };
window.mobileApp._appsActions = {
  _openApp: { slug: 'spesa' },
  requestAppHtml: async () => '<ul><li>latte</li></ul>',
};
const p = await r._collect();
assert.deepEqual(p.app, {
  slug: 'spesa', name: 'Spesa', responded: true, html: '<ul><li>latte</li></ul>',
});
window.mobileApp._appsActions._openApp = null;
assert.equal((await r._collect()).app, undefined);
"""
        )
    )
    assert out.strip() == "ok"
