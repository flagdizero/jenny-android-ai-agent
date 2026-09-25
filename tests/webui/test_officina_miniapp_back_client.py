"""Officina: Indietro e Home chiudono la mini-app, e i pacchetti arrivano al cassetto.

Il livello ``miniapp`` della catena di ``mobile-app.js`` parlava con
``this.controllers.apps``, cioe' con la scheda «App» che non esiste piu': nessuna
factory ``apps``, quindi ``undefined``. Il livello si dichiarava presente (c'e'
``.app-frame-overlay``), ``dismiss`` tornava ``false`` e Indietro scavalcava la
mini-app per andare a chiudere la vista sotto; Home non la smontava; i broadcast
del PackageManager finivano nel vuoto.

Qui la catena e ``onPackageChanged`` si eseguono in node, ritagliati dal sorgente
vero, con dentro le ``handleBack``/``closeApp`` vere di ``shared/apps-actions.js``.
"""

from __future__ import annotations

from support.js_harness import ASSETS, member, requires_node, run_js

pytestmark = requires_node

APP_SRC = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
ACTIONS_SRC = (ASSETS / "shared" / "apps-actions.js").read_text(encoding="utf-8")


def _script(body: str) -> str:
    return f"""
import assert from 'node:assert/strict';

let overlayInDom = false;
const removed = [];
globalThis.document = {{
  querySelector(sel) {{
    if (sel === '.app-frame-overlay') return overlayInDom ? {{}} : null;
    return null;
  }},
}};
globalThis.setTimeout = (fn) => {{ fn(); return 0; }};
const switched = [];
globalThis.window = {{
  mobileApp: {{
    currentMode: 'chat',
    switchMode: (m) => switched.push(m),
    launcher: {{ isOpen: () => false }},
  }},
}};

class Actions {{
{member(ACTIONS_SRC, "closeApp")}
{member(ACTIONS_SRC, "handleBack")}
}}

function openApp(actions, depth) {{
  const posted = [];
  actions._openApp = {{
    slug: 'demo', depth,
    iframe: {{ contentWindow: {{ postMessage: (m) => posted.push(m) }} }},
    overlay: {{
      classList: {{ remove() {{}} }},
      remove() {{ removed.push('demo'); overlayInDom = false; }},
    }},
  }};
  overlayInDom = true;
  return posted;
}}

class Shell {{
{member(APP_SRC, "_overlayLayers")}
{member(APP_SRC, "onPackageChanged")}
}}

const shell = new Shell();
shell.controllers = {{}};
shell.jenny = null;
shell.launcher = {{ isOpen: () => false }};
shell.drawer = {{ activeDrawer: null }};
const miniapp = () => shell._overlayLayers().find((l) => l.name === 'miniapp');

{body}
console.log('ok');
"""


def test_back_on_the_last_level_closes_the_open_miniapp() -> None:
    out = run_js(
        _script(
            """
shell._appsActions = new Actions();
openApp(shell._appsActions, 1);
assert.equal(miniapp().present(), true);
assert.equal(miniapp().dismiss(), true, 'Indietro deve consumare la pressione');
assert.deepEqual(removed, ['demo'], "l'app deve essere smontata");
assert.equal(shell._appsActions._openApp, null);
assert.deepEqual(switched, [], "nessuna scheda «App» da raggiungere");
"""
        )
    )
    assert out.strip() == "ok"


def test_back_inside_the_miniapp_goes_back_one_screen() -> None:
    out = run_js(
        _script(
            """
shell._appsActions = new Actions();
const posted = openApp(shell._appsActions, 2);
assert.equal(miniapp().dismiss(), true);
assert.deepEqual(posted, [{ type: 'jenny:go-back' }]);
assert.deepEqual(removed, []);
"""
        )
    )
    assert out.strip() == "ok"


def test_home_unmounts_the_miniapp_in_one_step() -> None:
    out = run_js(
        _script(
            """
shell._appsActions = new Actions();
openApp(shell._appsActions, 3);
miniapp().close();
assert.deepEqual(removed, ['demo'], 'Home smonta anche a profondita\\' 3');
"""
        )
    )
    assert out.strip() == "ok"


def test_without_actions_the_layer_lets_the_chain_go_on() -> None:
    out = run_js(
        _script(
            """
assert.equal(miniapp().dismiss(), false);
miniapp().close();
"""
        )
    )
    assert out.strip() == "ok"


def test_package_broadcasts_reach_the_apps_source() -> None:
    out = run_js(
        _script(
            """
shell.onPackageChanged('added', 'x.y');  // cassetto mai aperto: niente da fare
const seen = [];
shell._appsSource = { onPackageChanged: (k, p) => seen.push([k, p]) };
shell.onPackageChanged('removed', 'com.example');
assert.deepEqual(seen, [['removed', 'com.example']]);
"""
        )
    )
    assert out.strip() == "ok"
