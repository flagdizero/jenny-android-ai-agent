"""Officina: aprire e chiudere la chat sopra una vista, con i metodi veri.

``openChatOver``, ``closeChatOver``, ``_leaveChatOverClasses``,
``chatVisible`` e ``isChatOnScreen`` di ``mobile-app.js``, ritagliati dentro
un guscio finto con un DOM minimo: classi su ``<html>``, viste con il loro
``display``, ``inert``. Si misura quel che conta per l'utente: la vista sotto
resta quella (``currentMode`` non cambia, quindi ``ui_view`` la descrive), si
vede, non si tocca; la chat si attiva una volta; tutto torna com'era.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
APP = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")

pytestmark = requires_node

_HARNESS = """
import assert from 'node:assert/strict';
const classes = () => {
  const s = new Set();
  return { add: (...c) => c.forEach((x) => s.add(x)), remove: (...c) => c.forEach((x) => s.delete(x)),
           contains: (c) => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c)), has: (c) => s.has(c) };
};
const view = (id) => ({ id, style: { display: 'none' }, classList: classes(), inert: false });
const views = { chat: view('view-chat'), settings: view('view-settings') };
const VIEW_OF = { brain: 'settings', hands: 'settings', memory: 'settings' };
const viewElement = (mode) => views[VIEW_OF[mode] || mode];
let frame = null;
const root = { classList: classes() };
globalThis.document = {
  documentElement: root,
  activeElement: null,
  querySelector: (sel) => (sel === '.app-frame-overlay' ? frame : null),
  querySelectorAll: (sel) => (sel === '.view-beneath'
    ? Object.values(views).filter((v) => v.classList.has('view-beneath')) : []),
};
let cleared = 0;
const clearSelection = () => { cleared += 1; };
const states = [];
const AppState = { set: (k, v) => states.push([k, v]) };
const sessionManager = { currentKey: 'websocket:default', personalKey: 'websocket:default' };
class Shell {
  constructor() {
    this.currentMode = 'memory';
    views.settings.style.display = 'flex';
    this.activated = 0;
    this.deactivated = 0;
    this.revalidated = 0;
    const chat = { activate: () => { this.activated += 1; }, deactivate: () => { this.deactivated += 1; },
                   input: { blur() {} } };
    this.controllers = { chat, memory: { revalidate: () => { this.revalidated += 1; } } };
    this.launcher = { close() {} };
    this.drawer = { closed: 0, closeAll() { this.closed += 1; } };
    this.header = { painted: [], paintOver(m) { this.painted.push(m); }, clearOver() {} };
  }
  ensureController(mode) { return this.controllers[mode] || null; }
  __OPEN__
  __CLOSE__
  __LEAVE__
  __VISIBLE__
  __ON_SCREEN__
}
"""


def _run(body: str) -> None:
    script = (
        _HARNESS.replace("__OPEN__", member(APP, "openChatOver"))
        .replace("__CLOSE__", member(APP, "closeChatOver"))
        .replace("__LEAVE__", member(APP, "_leaveChatOverClasses"))
        .replace("__VISIBLE__", member(APP, "chatVisible"))
        .replace("__ON_SCREEN__", member(APP, "isChatOnScreen"))
    )
    run_js(script + body)


def test_opening_shows_the_chat_over_the_view_that_stays() -> None:
    _run("""
      const app = new Shell();
      assert.equal(app.openChatOver(), true);
      assert.equal(app.currentMode, 'memory', 'la vista sotto non e\\u2019 piu\\u2019 quella: ui_view mentirebbe');
      assert.equal(views.chat.style.display, 'flex');
      assert.equal(views.settings.style.display, 'flex', 'la vista sotto e\\u2019 sparita');
      assert.ok(views.settings.classList.has('view-beneath'));
      assert.equal(views.settings.inert, true);
      assert.ok(root.classList.has('mode-chat') && root.classList.has('chat-over'));
      assert.equal(app.activated, 1);
      assert.equal(app.drawer.closed, 1, 'un drawer di sotto e\\u2019 rimasto aperto');
      assert.deepEqual(app.header.painted, ['chat']);
      assert.equal(app.chatVisible(), true);
      assert.equal(app.isChatOnScreen(), true, 'gli avvisi letti nella chat sopra non si cancellano');
      assert.equal(app.openChatOver(), false, 'aperta due volte');
    """)


def test_closing_puts_everything_back() -> None:
    _run("""
      const app = new Shell();
      app.openChatOver();
      assert.equal(app.closeChatOver(), true);
      assert.equal(views.chat.style.display, 'none');
      assert.ok(!views.settings.classList.has('view-beneath'));
      assert.equal(views.settings.inert, false);
      for (const c of ['mode-chat', 'chat-over', 'chat-over-app']) assert.ok(!root.classList.has(c), c);
      assert.equal(app.deactivated, 1);
      assert.equal(app.revalidated, 1, 'la vista sotto non rilegge quel che Jenny ha cambiato');
      assert.equal(app.chatVisible(), false);
      assert.equal(app.closeChatOver(), false);
      assert.deepEqual(states, [['chatOver', true], ['chatOver', false]]);
    """)


def test_over_a_mini_app_it_says_so_and_the_app_goes_inert() -> None:
    _run("""
      frame = { inert: false };
      const app = new Shell();
      app.openChatOver();
      assert.ok(root.classList.has('chat-over-app'));
      assert.equal(app._chatOverApp, true);
      assert.equal(frame.inert, true, 'la mini-app sotto si tocca ancora');
      app.closeChatOver();
      assert.equal(frame.inert, false);
    """)


def test_on_the_console_there_is_nothing_to_open() -> None:
    _run("""
      const app = new Shell();
      app.currentMode = 'chat';
      assert.equal(app.openChatOver(), false);
      assert.equal(app.activated, 0);
    """)


def test_a_notebook_conversation_over_the_view_is_not_the_alerts_chat() -> None:
    _run("""
      const app = new Shell();
      sessionManager.currentKey = 'project:orto';
      app.openChatOver();
      assert.equal(app.isChatOnScreen(), false);
    """)
