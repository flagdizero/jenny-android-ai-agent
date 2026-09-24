"""La chrome esce dal hit-test finché c'è una selezione, e il tap le torna.

`shared/selection.js` gira qui per intero in node, su un `document` finto:
`exposeSelectionState` tiene la classe `has-selection` su `<html>` allineata
alla selezione; `forwardTapsThroughChrome` riconosce un tap caduto sulla chrome
resa trasparente, annulla il click che finirebbe sotto, chiude la selezione e
consegna il tap al bersaglio vero — e non fa niente in tutti gli altri casi.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
SELECTION_JS = ASSETS / "shared" / "selection.js"


pytestmark = requires_node

_HARNESS = """
import assert from 'node:assert/strict';

/* Un DOM minimo: elementi con `closest` per classe/tag, un documento con
   listener, selezione e elementFromPoint pilotabili dal test. */
function el(tag, classes = [], parent = null) {
  const node = {
    tag, classes, parent, focused: 0, clicks: 0,
    focus() { this.focused++; },
    click() { this.clicks++; },
    closest(selector) {
      const wants = selector.split(',').map((s) => s.trim());
      for (let n = this; n; n = n.parent) {
        for (const w of wants) {
          if (w.startsWith('.') && n.classes.includes(w.slice(1))) return n;
          if (w === n.tag) return n;
          if (w === 'a[href]' && n.tag === 'a') return n;
          if (w === '[contenteditable]' && n.editable) return n;
          if (w === '[role="button"]' && n.role === 'button') return n;
        }
      }
      return null;
    },
  };
  return node;
}

const listeners = {};
let selectionRange = null;
let underPoint = null;
let removedRanges = 0;
const root = {
  cls: new Set(),
  classList: {
    toggle: (c, on) => { on ? root.cls.add(c) : root.cls.delete(c); },
    add: (c) => root.cls.add(c),
    remove: (c) => root.cls.delete(c),
    contains: (c) => root.cls.has(c),
  },
};
globalThis.document = {
  documentElement: root,
  activeElement: null,
  addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
  removeEventListener() {},
  getSelection() {
    return {
      rangeCount: selectionRange ? 1 : 0,
      isCollapsed: !selectionRange,
      getRangeAt: () => selectionRange,
      removeAllRanges() { removedRanges++; selectionRange = null; },
    };
  },
  elementFromPoint() { return underPoint; },
};
const fire = (type, event) => (listeners[type] || []).forEach((fn) => fn(event));
const touch = (x, y) => ({ clientX: x, clientY: y });
const select = () => { selectionRange = { commonAncestorContainer: {} }; fire('selectionchange'); };
const deselect = () => { selectionRange = null; fire('selectionchange'); };

__MODULE__

/* La chrome: composer con textarea, dock con una voce. */
const bottom = el('div', ['chat-bottom']);
const textarea = el('textarea', [], bottom);
const dock = el('nav', ['dock']);
const dockItem = el('div', ['dock-item'], dock);
const bubble = el('div', ['chat-content']);
"""


def _harness() -> str:
    module = SELECTION_JS.read_text(encoding="utf-8").replace("export ", "")
    return _HARNESS.replace("__MODULE__", module)


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


def test_the_root_class_follows_the_selection() -> None:
    _run_js("""
      exposeSelectionState();
      assert.equal(root.cls.has('has-selection'), false);
      select();
      assert.equal(root.cls.has('has-selection'), true);
      deselect();
      assert.equal(root.cls.has('has-selection'), false);
    """)


def test_a_tap_on_the_composer_during_a_selection_focuses_it() -> None:
    _run_js("""
      exposeSelectionState();
      forwardTapsThroughChrome(['.chat-bottom', '.dock']);
      select();
      underPoint = textarea;
      let prevented = 0;
      fire('touchstart', { touches: [touch(100, 900)] });
      fire('touchend', { changedTouches: [touch(102, 903)], preventDefault: () => prevented++ });
      assert.equal(textarea.focused, 1, 'il campo prende il focus');
      assert.equal(prevented, 1, 'il click sintetico verso il testo sotto è annullato');
      assert.equal(removedRanges, 1, 'la selezione si chiude');
      assert.equal(root.cls.has('has-selection'), true, 'la classe torna su dopo il hit-test');
    """)


def test_a_tap_on_a_dock_item_clicks_the_item() -> None:
    _run_js("""
      exposeSelectionState();
      forwardTapsThroughChrome(['.chat-bottom', '.dock']);
      select();
      underPoint = dockItem;
      fire('touchstart', { touches: [touch(300, 1400)] });
      fire('touchend', { changedTouches: [touch(300, 1400)], preventDefault() {} });
      assert.equal(dockItem.clicks, 1);
    """)


def test_a_drag_stays_a_scroll() -> None:
    _run_js("""
      forwardTapsThroughChrome(['.chat-bottom', '.dock']);
      root.classList.add('has-selection');
      underPoint = textarea;
      let prevented = 0;
      fire('touchstart', { touches: [touch(100, 900)] });
      fire('touchend', { changedTouches: [touch(100, 700)], preventDefault: () => prevented++ });
      assert.equal(textarea.focused, 0);
      assert.equal(prevented, 0, 'un trascinamento non viene toccato');
    """)


def test_without_a_selection_nothing_is_intercepted() -> None:
    _run_js("""
      forwardTapsThroughChrome(['.chat-bottom', '.dock']);
      underPoint = textarea;
      let prevented = 0;
      fire('touchstart', { touches: [touch(100, 900)] });
      fire('touchend', { changedTouches: [touch(100, 900)], preventDefault: () => prevented++ });
      assert.equal(textarea.focused, 0);
      assert.equal(prevented, 0);
    """)


def test_a_tap_on_the_text_itself_is_left_to_the_browser() -> None:
    _run_js("""
      forwardTapsThroughChrome(['.chat-bottom', '.dock']);
      root.classList.add('has-selection');
      underPoint = bubble;
      let prevented = 0;
      fire('touchstart', { touches: [touch(100, 500)] });
      fire('touchend', { changedTouches: [touch(100, 500)], preventDefault: () => prevented++ });
      assert.equal(prevented, 0);
      assert.equal(removedRanges, 0);
    """)
