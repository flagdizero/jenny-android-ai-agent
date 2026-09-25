"""La casa risponde a ``ui_query``: «cosa vedi?» funziona anche dal guscio di default.

Il gateway lega ogni messaggio alla connessione che l'ha mandato
(``metadata["conn_id"]`` in ``jenny/channels/websocket.py``) e ``ui_view``
interroga **quella**. Solo l'officina rispondeva: scrivendo dalla casa Jenny
aspettava sei secondi e poi diceva che l'app era in background.

Qui girano i moduli veri — ``home-ui-query.js`` e ``shared/ui-query.js`` — con
un ``ws-manager`` finto che registra cosa parte sul filo. ``DOMParser`` in node
non c'e', e la potatura ripiega sul testo com'e' (e' il suo ``catch``): qui si
misura cosa si descrive, non come si pota.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from support.js_harness import requires_node, run_module

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
pytestmark = requires_node

_FAKE_WS = """
export const sent = [];
export const listeners = [];
export const wsManager = {
  addEventListener(type, fn) { listeners.push([type, fn]); },
  sendUiResult(id, payload, error = null) { sent.push({ id, payload, error }); return true; },
};
"""

_PRELUDE = """
import assert from 'node:assert/strict';
import { sent, listeners } from './shared/ws-manager.js';

/* La finestra: basta un bus di `message`. */
const winListeners = new Set();
globalThis.window = {
  addEventListener(type, fn) { if (type === 'message') winListeners.add(fn); },
  removeEventListener(type, fn) { if (type === 'message') winListeners.delete(fn); },
};
const post = (source, data) => { for (const fn of [...winListeners]) fn({ source, data }); };

const nodes = {
  'home-reader': { outerHTML: '<section id="home-reader">una pagina</section>' },
};
globalThis.document = { getElementById: (id) => nodes[id] ?? null };

const { HomeUiQuery } = await import('./home-ui-query.js');

const panels = {
  chat: { outerHTML: '<div data-page="chat">il filo</div>' },
  notebooks: { outerHTML: '<div data-page="notebooks">i quaderni</div>' },
  s1: { outerHTML: '<div data-id="s1"><iframe></iframe></div>' },
};
const order = ['app', 'chat', 'notebooks', 's1'];
const entries = {
  app: { id: 'app', kind: 'drawer', fixed: true },
  chat: { id: 'chat', kind: 'chat', fixed: true },
  notebooks: { id: 'notebooks', kind: 'notebooks', fixed: true },
  s1: { id: 's1', kind: 'app', ref: 'spesa', fixed: false },
};
/* La cornice dell'app appesa: risponde come fa `jenny-sdk.js`. */
const frame = {
  answer: true,
  postMessage(msg) {
    if (msg.type !== 'jenny:ui-query' || !this.answer) return;
    queueMicrotask(() => post(frame, { type: 'jenny:ui-result', nonce: msg.nonce, html: '<ul><li>latte</li></ul>' }));
  },
};
const app = {
  view: 'chat',
  homePages: {
    index: 1,
    entry(i) { return entries[order[i]] ?? null; },
    panelOf(i) { return panels[order[i]] ?? null; },
    _pageWindow() { return frame; },
  },
  _appActions: null,
  appName: (slug) => ({ spesa: 'Spesa' })[slug] ?? null,
};

const responder = new HomeUiQuery(app);
const onMessage = listeners.find(([type]) => type === 'chat:message')?.[1];
assert.ok(onMessage, 'nessun ascolto sul filo della chat');

async function ask(id) {
  onMessage({ detail: { event: 'ui_query', correlation_id: id } });
  for (let i = 0; i < 50 && !sent.some((s) => s.id === id); i++) await new Promise((r) => setTimeout(r, 1));
  const out = sent.find((s) => s.id === id);
  assert.ok(out, `nessun ui_result per ${id}`);
  return out;
}
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "home-ui-query.js", root / "home-ui-query.js")
        shutil.copy(ASSETS / "shared" / "ui-query.js", root / "shared" / "ui-query.js")
        (root / "shared" / "ws-manager.js").write_text(_FAKE_WS, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + body + "\nconsole.log('ok');\n", encoding="utf-8")
        assert run_module(entry).strip().endswith("ok")


def test_the_chat_page_is_described() -> None:
    _run("""
      const { payload, error } = await ask('c1');
      assert.equal(error, null);
      assert.equal(payload.view, 'home:chat');
      assert.equal(payload.drawer, null);
      assert.match(payload.html, /il filo/);
      assert.equal(payload.app, undefined);
    """)


def test_the_page_on_screen_is_the_one_described() -> None:
    _run("""
      app.homePages.index = 2;
      const { payload } = await ask('c2');
      assert.equal(payload.view, 'home:notebooks');
      assert.match(payload.html, /i quaderni/);
    """)


def test_an_open_room_wins_over_the_page_under_it() -> None:
    _run("""
      app.view = 'reader';
      const { payload } = await ask('c3');
      assert.equal(payload.view, 'home:reader');
      assert.match(payload.html, /una pagina/);
    """)


def test_a_pinned_app_page_sends_its_own_dom() -> None:
    _run("""
      app.homePages.index = 3;
      const { payload } = await ask('c4');
      assert.equal(payload.view, 'home:s1');
      assert.deepEqual(payload.app, {
        slug: 'spesa', name: 'Spesa', responded: true, html: '<ul><li>latte</li></ul>',
      });
    """)


def test_the_app_over_everything_wins() -> None:
    _run("""
      app._appActions = {
        _openApp: { slug: 'note' },
        requestAppHtml: async () => '<p>nota</p>',
      };
      const { payload } = await ask('c5');
      assert.equal(payload.view, 'home:chat');
      assert.deepEqual(payload.app, { slug: 'note', name: 'note', responded: true, html: '<p>nota</p>' });
    """)


def test_a_failed_collect_is_said_on_the_wire() -> None:
    """Una raccolta che esplode non lascia Jenny ad aspettare sei secondi."""
    _run("""
      console.error = () => {};
      app.homePages.entry = () => { throw new Error('boom'); };
      const out = await ask('c6');
      assert.equal(out.error, 'collect_failed');
    """)


def test_a_frame_answer_is_taken_only_from_that_frame_and_nonce() -> None:
    _run("""
      const { requestFrameHtml } = await import('./shared/ui-query.js');
      const other = { postMessage() {} };
      const quiet = {
        postMessage(msg) {
          // Una risposta da un'altra finestra, e una col nonce sbagliato: niente.
          queueMicrotask(() => {
            post(other, { type: 'jenny:ui-result', nonce: msg.nonce, html: 'intruso' });
            post(quiet, { type: 'jenny:ui-result', nonce: 'altro', html: 'intruso' });
          });
        },
      };
      assert.equal(await requestFrameHtml(quiet, 30), null);
      assert.equal(winListeners.size, 0, 'l\\'ascolto non si stacca dopo il tempo scaduto');
      assert.equal(await requestFrameHtml(null, 30), null);
      assert.equal(await requestFrameHtml(frame, 500), '<ul><li>latte</li></ul>');
      assert.equal(winListeners.size, 0);
    """)
