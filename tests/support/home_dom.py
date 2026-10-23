"""La casa intera in jsdom: l'``index.html`` vero, i moduli veri, rete e socket finti.

Gli altri banchi della casa ritagliano un metodo e lo mettono in una classe
finta: bastano quando la domanda riguarda un metodo. Non bastano quando il
difetto sta **fra** i pezzi — una lettura del filo che arriva dopo i frame
vivi, due riletture che si incrociano, un dialogo condiviso che resta aperto
sopra un'altra pagina — perche' la classe finta e' proprio il posto in cui
quel legame si riscrive a mano. Qui ``home-app.js`` si importa com'e', e
costruisce la casa sul markup vero.

Finti restano i confini: ``fetch`` (una tabella di rotte, piu' un gancio per
fallire o rallentare una rotta), il ``WebSocket`` (ricorda cosa gli si manda,
e ``frame()`` gli fa arrivare un frame), e ``showModal`` dove jsdom non l'ha.
Gli script che la casa carica su richiesta (``ensureVendor``) arrivano dalla
cartella della UI, come dall'APK.

jsdom **non** e' una dipendenza del repo: senza, questi test si saltano con
:data:`requires_jsdom`. Si trova con ``NODE_PATH`` (``require`` lo rispetta,
``import`` no: per questo il banco lo carica con ``createRequire``).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from support.js_harness import NODE, ROOT

UI = ROOT / "jenny" / "templates" / "ui"


def _has_jsdom() -> bool:
    if NODE is None:
        return False
    proc = subprocess.run(
        [NODE, "-e", "require.resolve('jsdom')"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    return proc.returncode == 0


requires_jsdom = pytest.mark.skipif(not _has_jsdom(), reason="jsdom non disponibile")


_BOOT = r"""
import { createRequire } from 'node:module';
import fs from 'node:fs';
const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');

const UI = __UI__;
const html = fs.readFileSync(UI + '/index.html', 'utf8').replace(/<script[^>]*><\/script>/g, '');
export const dom = new JSDOM(html, {
  url: 'http://127.0.0.1:18790/html-mobile/index.html#bs=secret',
  pretendToBeVisual: true,
});
const w = dom.window;
globalThis.window = w;
for (const k of Object.getOwnPropertyNames(w)) {
  if (k in globalThis) continue;
  try { globalThis[k] = w[k]; } catch {}
}
for (const k of ['document', 'navigator', 'location', 'history', 'localStorage',
  'sessionStorage', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame',
  'CustomEvent', 'Event', 'EventTarget', 'HTMLElement', 'MouseEvent', 'KeyboardEvent']) {
  try { Object.defineProperty(globalThis, k, { value: w[k], configurable: true, writable: true }); } catch {}
}
globalThis.CSS = w.CSS || { escape: (s) => String(s) };
if (!w.HTMLDialogElement.prototype.showModal) {
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () {
    if (!this.hasAttribute('open')) return;
    this.removeAttribute('open');
    this.dispatchEvent(new w.Event('close'));
  };
}
w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
globalThis.matchMedia = w.matchMedia;

/* Uno `<script src>` messo nella testa (`ensureVendor`) si carica dalla
   cartella della UI: jsdom senza risorse non lo caricherebbe mai, e non
   direbbe nemmeno di aver fallito. Un UMD si appoggia su `globalThis`, che qui
   e' la finestra; un file che non c'e' e' un `onerror`, come nella WebView. */
const appendToHead = w.document.head.appendChild.bind(w.document.head);
w.document.head.appendChild = (el) => {
  const src = el.tagName === 'SCRIPT' ? el.getAttribute('src') || '' : '';
  const node = appendToHead(el);
  if (src.startsWith('/html-mobile/')) {
    setTimeout(() => {
      let code;
      try {
        code = fs.readFileSync(UI + src.slice('/html-mobile'.length), 'utf8');
      } catch {
        el.onerror?.();
        return;
      }
      new Function('window', 'self', 'globalThis', code)(w, w, w);
      el.onload?.();
    }, 0);
  }
  return node;
};

export const locales = {
  it: JSON.parse(fs.readFileSync(UI + '/assets/i18n/it.json', 'utf8')),
  en: JSON.parse(fs.readFileSync(UI + '/assets/i18n/en.json', 'utf8')),
};

/* Le rotte: il corpo che torna, per percorso. Chi vuole fallire o rallentare
   una rotta mette un gancio in `hooks.fetch`, che vede la richiesta prima
   della tabella e puo' rispondere lui (tornando qualcosa) o lasciarla passare
   (tornando `undefined`). */
export const calls = [];
export const routes = {
  '/assets/i18n/it.json': locales.it,
  '/assets/i18n/en.json': locales.en,
  '/webui/bootstrap': { ok: true },
  '/api/home/pages': { pages: [], order: ['app', 'chat', 'notebooks', 'settings'],
    fixed: ['app', 'chat', 'notebooks', 'settings'], max: 8 },
  '/api/settings': { agent: { bot_name: 'Jenny', model: 'm1' }, providers: [],
    default_provider: null, version: {}, backup: {} },
  '/api/projects': { projects: [{ name: 'orto', modified: 1, pages: 3 }], unopenable: [] },
};
/* La storia di una conversazione, per chiave. */
export const threads = {};
export const hooks = { fetch: null };
export const ok = (body) => ({
  ok: true, status: 200,
  json: async () => JSON.parse(JSON.stringify(body)),
  text: async () => JSON.stringify(body),
});
export const failed = (status = 503) => ({
  ok: false, status, json: async () => ({}), text: async () => '',
});
globalThis.fetch = async (url, init) => {
  const u = new URL(url, 'http://127.0.0.1:18790');
  calls.push(u.pathname + u.search);
  if (hooks.fetch) {
    const answer = await hooks.fetch(u, init);
    if (answer !== undefined) return answer;
  }
  if (u.pathname.endsWith('/webui-thread')) {
    const key = decodeURIComponent(u.pathname.split('/')[3]);
    return ok(threads[key] || { messages: [] });
  }
  const body = routes[u.pathname];
  return body === undefined ? failed(404) : ok(body);
};

/* Il socket: ricorda cosa gli si manda, e si apre al giro dopo. Un comando
   (`rpc`) il cui metodo sta in `rpcAnswers` riceve la risposta al giro dopo,
   col risultato che la funzione calcola dai parametri; gli altri restano
   senza, come un gateway che non risponde. Una funzione che torna
   `rpcFailed(...)` fa rispondere un rifiuto (`ok: false`), come un comando
   che il gateway non ha potuto eseguire. */
export const sent = [];
export const rpcAnswers = {
  /* La pagina App chiede il cestino ogni volta che si accende: un gateway vero
     risponde sempre, e senza risposta la richiesta aspetterebbe il suo
     timeout tenendo vivo node per venti secondi a test. */
  'apps.trash': () => ({ ok: true, trash: [] }),
  /* Le bozze da approvare si rileggono a ogni apertura del socket: stessa
     ragione, una casa senza bozze. */
  'staged.list': () => ({ ok: true, staged: [] }),
  /* I piani a lista, idem: si rileggono a ogni apertura (il lucchetto e il
     bollino dei quaderni) e a ogni cambio di conversazione. Una casa senza piani. */
  'plan.list': () => ({ ok: true, plans: [] }),
  'plan.get': () => ({ ok: true, plan: null }),
};
const RPC_FAILED = Symbol('rpcFailed');
export const rpcFailed = (code = 'unavailable', message = 'failed') => ({ [RPC_FAILED]: { code, message } });
export class FakeWS {
  constructor(u) {
    this.url = u;
    this.readyState = 0;
    FakeWS.last = this;
    setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0);
  }
  send(d) {
    const msg = JSON.parse(d);
    sent.push(msg);
    const answer = msg.type === 'rpc' ? rpcAnswers[msg.method] : null;
    if (answer) {
      const out = answer(msg.params || {});
      const reply = out && out[RPC_FAILED]
        ? { event: 'rpc_result', id: msg.id, ok: false, error: out[RPC_FAILED] }
        : { event: 'rpc_result', id: msg.id, ok: true, result: out };
      setTimeout(() => this.onmessage?.({ data: JSON.stringify(reply) }), 0);
    }
  }
  close() {}
}
FakeWS.OPEN = 1;
FakeWS.CONNECTING = 0;
globalThis.WebSocket = FakeWS;
w.WebSocket = FakeWS;
export const frame = (m) => FakeWS.last.onmessage({ data: JSON.stringify(m) });

export const unhandled = [];
process.on('unhandledRejection', (e) => unhandled.push(String((e && e.message) || e)));

export const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
export const $ = (id) => document.getElementById(id);
/* I testi degli avvisi a schermo (`showToast`). */
export const toasts = () => [...document.querySelectorAll('.mobile-toast')].map((t) => t.textContent);

/* Il filo com'e' a schermo, dall'alto: chi parla e cosa dice. */
export function thread() {
  return [...$('home-thread').children]
    .filter((n) => n.classList.contains('home-msg') || n.classList.contains('home-note'))
    .map((n) => {
      if (n.classList.contains('home-note')) return 'note: ' + n.textContent.trim();
      const who = n.classList.contains('home-msg-user') ? 'you' : 'jenny';
      const text = [...n.querySelectorAll('.home-block')].map((b) => b.textContent.trim()).join(' ');
      return `${who}: ${text}`;
    });
}

export async function boot() {
  await import(UI + '/assets/home-app.js');
  await tick(50);
  return w.mobileApp;
}
"""


def run_home(script: str, *, timeout: float = 60) -> str:
    """Esegue *script* (un modulo ES che importa da ``./boot.mjs``) e ne
    ritorna lo stdout. ``assert`` di node che fallisce fa fallire il test."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "boot.mjs").write_text(
            _BOOT.replace("__UI__", json.dumps(str(UI))), encoding="utf-8"
        )
        entry = root / "case.mjs"
        entry.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [str(NODE), str(entry)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout
