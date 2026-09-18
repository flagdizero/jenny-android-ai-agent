"""Il lato dinamico di `_scroller`: i metodi della chat girano in node su uno
scroller finto, come se fosse `document.scrollingElement`.

Il contratto statico sta in `test_chat_root_scroller_contract.py`; qui si
verifica che le soglie, il guard a vista nascosta, l'ancora di lettura e la
compensazione di "carica altro" misurino lo scroller giusto e scrivano dove
devono.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
PAGER_JS = ASSETS / "shared" / "history-pager.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


_HARNESS = """
import assert from 'node:assert/strict';

/* rAF sincrono: il test legge subito dopo la chiamata. */
const rafQueue = [];
globalThis.requestAnimationFrame = (fn) => { rafQueue.push(fn); return rafQueue.length; };
const flushRaf = () => { while (rafQueue.length) rafQueue.shift()(); };

let selectionActive = false;
const hasSelection = () => selectionActive;

const windowListeners = {};
globalThis.window = {
  addEventListener(type, fn) { (windowListeners[type] ||= []).push(fn); },
};
const fire = (type) => (windowListeners[type] || []).forEach((fn) => fn());

/* Lo scorrimento infinito sta nel modulo condiviso (una macchina sola per la
   casa e per l'officina): `setupInfiniteScroll` ora e' il filo che lo lega a
   `window` e al documento, ed e' quel filo che si misura qui. */
const { HistoryPager } = await import('__PAGER_URL__');

function makeChat({ scrollTop = 0, scrollHeight = 3000, clientHeight = 1000, areaHeight = 900 } = {}) {
  const chat = {
    _scroller: { scrollTop, scrollHeight, clientHeight },
    chatArea: { clientHeight: areaHeight },
    _autoScroll: true,
    _userTouching: false,
    _scrollThreshold: 60,
    _scrollAnchor: null,
    _unreadCount: 3,
    _active: true,
    fabUpdates: 0,
    loadedMore: 0,
    _updateScrollFab() { this.fabUpdates++; },
    __NEAR__,
    __BOTTOM__,
    __REMEMBER__,
    __RESTORE__,
    __INFINITE__,
  };
  chat._pager = new HistoryPager({
    scroller: () => chat._scroller,
    listenOn: globalThis.window,
    container: () => ({ querySelector: () => null }),
    pageSize: 120,
    begin: () => null,
    prepend() {},
    mount() {},
    label: () => 'i18n:chat.loadPrevious',
  });
  chat._pager.loadMore = async () => { chat.loadedMore++; };
  Object.defineProperty(chat, 'hasMoreHistory', {
    get: () => chat._pager.hasMore,
    set: (v) => { chat._pager.hasMore = !!v; },
  });
  return chat;
}
"""


def _harness() -> str:
    src = CHAT_JS.read_text(encoding="utf-8")
    return (
        _HARNESS.replace("__NEAR__", _member(src, "_isNearBottom"))
        .replace("__BOTTOM__", _member(src, "scrollToBottom"))
        .replace("__REMEMBER__", _member(src, "_rememberScrollAnchor"))
        .replace("__RESTORE__", _member(src, "_restoreScrollAnchor"))
        .replace("__INFINITE__", _member(src, "setupInfiniteScroll"))
        .replace("__PAGER_URL__", PAGER_JS.as_uri())
    )


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_near_bottom_reads_the_document_scroller() -> None:
    _run_js("""
      const chat = makeChat({ scrollTop: 1950, scrollHeight: 3000, clientHeight: 1000 });
      assert.equal(chat._isNearBottom(), true);      // 50px dal fondo, sotto la soglia
      chat._scroller.scrollTop = 1900;
      assert.equal(chat._isNearBottom(), false);     // 100px: staccato
    """)


def test_scroll_to_bottom_writes_the_document_scroller() -> None:
    _run_js("""
      const chat = makeChat({ scrollTop: 0 });
      chat.scrollToBottom();
      flushRaf();
      assert.equal(chat._scroller.scrollTop, 3000);
      assert.equal(chat._unreadCount, 0);
    """)


def test_scroll_to_bottom_yields_to_a_finger_or_a_selection_unless_forced() -> None:
    _run_js("""
      let chat = makeChat();
      chat._userTouching = true;
      chat.scrollToBottom(); flushRaf();
      assert.equal(chat._scroller.scrollTop, 0, 'dito giù: niente scroll programmatico');

      chat = makeChat();
      selectionActive = true;
      chat.scrollToBottom(); flushRaf();
      assert.equal(chat._scroller.scrollTop, 0, 'selezione viva: niente scroll');
      chat.scrollToBottom(true); flushRaf();
      assert.equal(chat._scroller.scrollTop, 3000, 'force è un\\'intenzione esplicita');
      selectionActive = false;
    """)


def test_the_reading_anchor_is_measured_on_the_document_but_guarded_by_the_area() -> None:
    """A vista nascosta il viewport ha ancora un'altezza: è la scatola della
    chat che vale 0, e solo quella deve fermare la misura."""
    _run_js("""
      const chat = makeChat({ scrollTop: 1200, scrollHeight: 3000, clientHeight: 1000 });
      chat._rememberScrollAnchor();
      assert.equal(chat._scrollAnchor, 1800);

      chat.chatArea.clientHeight = 0;         // vista nascosta (display:none)
      chat._scroller.scrollTop = 0;           // il documento si è accorciato e riporta 0
      chat._rememberScrollAnchor();
      assert.equal(chat._scrollAnchor, 1800, 'un contenitore senza box non è una posizione');
    """)


def test_the_reading_anchor_is_restored_on_the_document() -> None:
    _run_js("""
      const chat = makeChat({ scrollTop: 0, scrollHeight: 5000, clientHeight: 1000 });
      chat._scrollAnchor = 1800;
      chat._restoreScrollAnchor();
      flushRaf();
      assert.equal(chat._scroller.scrollTop, 3200);
    """)


def test_infinite_scroll_asks_for_history_only_at_the_top_of_the_document() -> None:
    _run_js("""
      const chat = makeChat({ scrollTop: 400 });
      chat.setupInfiniteScroll();
      fire('scroll');
      assert.equal(chat.loadedMore, 0);
      chat._scroller.scrollTop = 0;
      fire('scroll');
      assert.equal(chat.loadedMore, 1);
      chat.hasMoreHistory = false;
      fire('scroll');
      assert.equal(chat.loadedMore, 1);
    """)
