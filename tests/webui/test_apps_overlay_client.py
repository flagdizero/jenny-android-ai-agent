"""Il velo di una mini-app: come si apre, cosa porta, come si chiude.

Una Jenny App si apre sopra tutto in un velo (``.app-frame-overlay``) con la
sua testata e la cornice. Le strade sono due — l'app servita dal gateway
(``openApp``) e la vista esterna dietro il proxy (``_openExternalView``) — e
montavano il velo ognuna con la sua copia. Qui si fissa cosa monta ciascuna, e
la differenza vera fra le due: il sandbox della cornice e la chiusura del
proxy.

In node, col modulo vero (``shared/apps-actions.js``) e un DOM ridotto a
quel che il velo tocca.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_NEIGHBORS = {
    "api-client.js": "export const api = { getSecret() { return 'segreto'; } };\n",
    "utils.js": """
export function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
export function showToast(text, type) { globalThis.toasts.push([text, type]); }
""",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
    "i18n.js": "export const i18n = { t: (k) => k, locale: 'it' };\n",
    "theme.js": """
export function currentTheme() { return { scheme: 'dark', accent: '#b2543f', onAccent: '#fff' }; }
export function themeTokens() { return ''; }
""",
}

_PRELUDE = """
import assert from 'node:assert/strict';
globalThis.toasts = [];
globalThis.fetches = [];
globalThis.window = { addEventListener() {} };
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.MutationObserver = class { observe() {} };

function el(tag) {
  const e = {
    tag, attrs: {}, children: [], on: {}, removed: false, className: '', src: '',
    _html: '', close: null,
    classes: new Set(),
  };
  e.classList = {
    add: (c) => e.classes.add(c), remove: (c) => e.classes.delete(c),
    contains: (c) => e.classes.has(c),
  };
  e.setAttribute = (k, v) => { e.attrs[k] = v; };
  e.appendChild = (c) => { e.children.push(c); };
  e.addEventListener = (t, fn) => { (e.on[t] ||= []).push(fn); };
  e.remove = () => { e.removed = true; };
  Object.defineProperty(e, 'innerHTML', {
    get: () => e._html,
    set: (v) => { e._html = v; e.close = v.includes('app-frame-close') ? el('button') : null; },
  });
  e.querySelector = (sel) => (sel === '.app-frame-close' ? e.close : null);
  return e;
}
globalThis.document = {
  documentElement: { lang: 'it' },
  body: el('body'),
  createElement: (tag) => el(tag),
};
globalThis.fetch = async (url, opts) => {
  fetches.push(url);
  if (url.endsWith('/view')) {
    return { ok: true, status: 200, json: async () => ({ url: 'http://127.0.0.1:4555/' }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

const { AppsActions } = await import('./shared/apps-actions.js');
const source = { onAppDataChanged() {}, jennyApps: [
  { slug: 'orto', name: 'Orto <b>' },
  { slug: 'meteo', name: 'Meteo', view_kind: 'external' },
] };
const actions = new AppsActions(source, { sendChatPrompt() {} });
const veils = () => document.body.children;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "apps-actions.js", root / "shared" / "apps-actions.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
        run_module(entry)


def test_a_gateway_app_opens_in_a_veil_with_its_frame() -> None:
    _run(
        """
        await actions.openApp('orto');
        assert.equal(veils().length, 1);
        const veil = veils()[0];
        assert.equal(veil.className, 'app-frame-overlay');
        assert.ok(veil.classList.contains('visible'));
        assert.ok(veil.innerHTML.includes('Orto &lt;b&gt;'), 'il nome passa da escapeHtml');
        assert.ok(veil.innerHTML.includes('apps.close'));
        const frame = veil.children[0];
        assert.equal(frame.tag, 'iframe');
        assert.equal(frame.attrs.sandbox, 'allow-scripts');
        assert.ok(frame.src.startsWith('/apps/orto/index.html?token=segreto'));
        assert.deepEqual(Object.keys(actions._openApp).sort(), ['depth', 'iframe', 'overlay', 'slug']);
        assert.equal(actions._openApp.slug, 'orto');
        assert.equal(actions._openApp.overlay, veil);
        assert.equal(actions._openApp.iframe, frame);
        assert.equal(actions._openApp.depth, 1);
        """
    )


def test_the_close_button_closes_the_veil() -> None:
    _run(
        """
        await actions.openApp('orto');
        const veil = veils()[0];
        veil.close.on.click[0]();
        assert.equal(actions._openApp, null);
        assert.equal(veil.classList.contains('visible'), false);
        await sleep(250);
        assert.equal(veil.removed, true);
        assert.deepEqual(fetches, [], 'un\\'app del gateway non ha un proxy da chiudere');
        """
    )


def test_an_external_view_gets_the_wider_sandbox_and_its_proxy_closed() -> None:
    _run(
        """
        await actions.openApp('meteo');
        assert.deepEqual(fetches, ['/api/webui/apps/meteo/view']);
        const veil = veils()[0];
        assert.equal(veil.className, 'app-frame-overlay');
        assert.ok(veil.innerHTML.includes('Meteo'));
        const frame = veil.children[0];
        assert.equal(frame.src, 'http://127.0.0.1:4555/');
        assert.equal(frame.attrs.sandbox,
                     'allow-scripts allow-same-origin allow-forms allow-popups allow-modals');
        assert.equal(actions._openApp.external, true);
        assert.equal(actions._openApp.depth, 1);
        veil.close.on.click[0]();
        assert.deepEqual(fetches, ['/api/webui/apps/meteo/view', '/api/webui/apps/meteo/view/close']);
        """
    )


def test_opening_another_app_closes_the_first() -> None:
    _run(
        """
        await actions.openApp('meteo');
        const first = veils()[0];
        await actions.openApp('orto');
        assert.equal(first.classList.contains('visible'), false);
        assert.ok(fetches.includes('/api/webui/apps/meteo/view/close'),
                  'il proxy della vista chiusa va chiuso');
        assert.equal(actions._openApp.slug, 'orto');
        assert.equal(veils().length, 2, 'il primo esce dopo la dissolvenza');
        await sleep(250);
        assert.equal(first.removed, true);
        """
    )


def test_a_failed_external_view_leaves_the_open_app_alone() -> None:
    """Il proxy che non risponde non chiude quel che c'è: prima si chiede la
    vista, poi si smonta la vecchia."""
    _run(
        """
        await actions.openApp('orto');
        const open = actions._openApp;
        globalThis.fetch = async () => ({ ok: false, status: 502, json: async () => ({}) });
        await actions.openApp('meteo');
        assert.equal(actions._openApp, open);
        assert.deepEqual(toasts, [['apps.viewProxyFailed', 'error']]);
        assert.equal(veils().length, 1);
        """
    )
