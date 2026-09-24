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
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")

_VICINI = {
    "api-client.js": "export const api = { getSecret() { return 'segreto'; } };\n",
    "utils.js": """
export function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
export function showToast(testo, tipo) { globalThis.toasts.push([testo, tipo]); }
""",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
    "i18n.js": "export const i18n = { t: (k) => k, locale: 'it' };\n",
    "theme.js": """
export function currentTheme() { return { scheme: 'dark', accent: '#b2543f', onAccent: '#fff' }; }
export function themeTokens() { return ''; }
""",
}

_PRELUDIO = """
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
const fonte = { onAppDataChanged() {}, jennyApps: [
  { slug: 'orto', name: 'Orto <b>' },
  { slug: 'meteo', name: 'Meteo', view_kind: 'external' },
] };
const azioni = new AppsActions(fonte, { sendChatPrompt() {} });
const veli = () => document.body.children;
const aspetta = (ms) => new Promise((r) => setTimeout(r, ms));
"""


def _run(corpo: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "apps-actions.js", radice / "shared" / "apps-actions.js")
        for nome, testo in _VICINI.items():
            (radice / "shared" / nome).write_text(testo, encoding="utf-8")
        entry = radice / "prova.mjs"
        entry.write_text(_PRELUDIO + textwrap.dedent(corpo), encoding="utf-8")
        proc = subprocess.run([str(_NODE), str(entry)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr or proc.stdout


def test_a_gateway_app_opens_in_a_veil_with_its_frame() -> None:
    _run(
        """
        await azioni.openApp('orto');
        assert.equal(veli().length, 1);
        const velo = veli()[0];
        assert.equal(velo.className, 'app-frame-overlay');
        assert.ok(velo.classList.contains('visible'));
        assert.ok(velo.innerHTML.includes('Orto &lt;b&gt;'), 'il nome passa da escapeHtml');
        assert.ok(velo.innerHTML.includes('apps.close'));
        const cornice = velo.children[0];
        assert.equal(cornice.tag, 'iframe');
        assert.equal(cornice.attrs.sandbox, 'allow-scripts');
        assert.ok(cornice.src.startsWith('/apps/orto/index.html?token=segreto'));
        assert.deepEqual(Object.keys(azioni._openApp).sort(), ['depth', 'iframe', 'overlay', 'slug']);
        assert.equal(azioni._openApp.slug, 'orto');
        assert.equal(azioni._openApp.overlay, velo);
        assert.equal(azioni._openApp.iframe, cornice);
        assert.equal(azioni._openApp.depth, 1);
        """
    )


def test_the_close_button_closes_the_veil() -> None:
    _run(
        """
        await azioni.openApp('orto');
        const velo = veli()[0];
        velo.close.on.click[0]();
        assert.equal(azioni._openApp, null);
        assert.equal(velo.classList.contains('visible'), false);
        await aspetta(250);
        assert.equal(velo.removed, true);
        assert.deepEqual(fetches, [], 'un\\'app del gateway non ha un proxy da chiudere');
        """
    )


def test_an_external_view_gets_the_wider_sandbox_and_its_proxy_closed() -> None:
    _run(
        """
        await azioni.openApp('meteo');
        assert.deepEqual(fetches, ['/api/webui/apps/meteo/view']);
        const velo = veli()[0];
        assert.equal(velo.className, 'app-frame-overlay');
        assert.ok(velo.innerHTML.includes('Meteo'));
        const cornice = velo.children[0];
        assert.equal(cornice.src, 'http://127.0.0.1:4555/');
        assert.equal(cornice.attrs.sandbox,
                     'allow-scripts allow-same-origin allow-forms allow-popups allow-modals');
        assert.equal(azioni._openApp.external, true);
        assert.equal(azioni._openApp.depth, 1);
        velo.close.on.click[0]();
        assert.deepEqual(fetches, ['/api/webui/apps/meteo/view', '/api/webui/apps/meteo/view/close']);
        """
    )


def test_opening_another_app_closes_the_first() -> None:
    _run(
        """
        await azioni.openApp('meteo');
        const primo = veli()[0];
        await azioni.openApp('orto');
        assert.equal(primo.classList.contains('visible'), false);
        assert.ok(fetches.includes('/api/webui/apps/meteo/view/close'),
                  'il proxy della vista chiusa va chiuso');
        assert.equal(azioni._openApp.slug, 'orto');
        assert.equal(veli().length, 2, 'il primo esce dopo la dissolvenza');
        await aspetta(250);
        assert.equal(primo.removed, true);
        """
    )


def test_a_failed_external_view_leaves_the_open_app_alone() -> None:
    """Il proxy che non risponde non chiude quel che c'è: prima si chiede la
    vista, poi si smonta la vecchia."""
    _run(
        """
        await azioni.openApp('orto');
        const aperto = azioni._openApp;
        globalThis.fetch = async () => ({ ok: false, status: 502, json: async () => ({}) });
        await azioni.openApp('meteo');
        assert.equal(azioni._openApp, aperto);
        assert.deepEqual(toasts, [['apps.viewProxyFailed', 'error']]);
        assert.equal(veli().length, 1);
        """
    )
