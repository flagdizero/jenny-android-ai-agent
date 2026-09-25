"""La chat della casa non rifà il lavoro a ogni pezzetto.

Due costi che crescevano col quadrato, misurati contando le chiamate:

* **la storia**: ogni ``_append`` chiamava ``gap.refresh()``, che legge il
  rettangolo di **ogni** ``.home-msg`` del filo (``shared/jenny-gap.js``); e
  ``load()``/``prependTurns()`` appendono un turno alla volta. Una pagina di N
  turni costava N letture di N rettangoli, ognuna dopo una scrittura — layout
  forzato a ogni giro. Adesso il margine si ricalcola una volta a pagina finita;
* **lo stream**: ogni ``delta`` ridisegnava in modo sincrono il markdown del
  buffer intero. L'officina raccoglie i delta e disegna una volta per
  fotogramma (``_scheduleFlush``); adesso anche la casa, e la resa finale la fa
  ``stream_end`` senza aspettare il fotogramma.

I metodi si ritagliano da ``home-chat.js`` e girano in node su un filo finto.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
HOME_CHAT_JS = ASSETS / "home-chat.js"

pytestmark = requires_node

_METHODS = (
    "load",
    "prependTurns",
    "_inBatch",
    "_buildTurns",
    "_append",
    "_appendBoundary",
    "_delta",
    "_scheduleRender",
    "_flushDelta",
    "_cancelRender",
    "_streamEnd",
    "_ensureBlock",
    "_ensureTurn",
    "_resetTurn",
)

_HARNESS = """
import assert from 'node:assert/strict';

function makeNode() {
  const node = {
    className: '', innerHTML: '', isConnected: true, children: [],
    appendChild(c) { node.children.push(c); return c; },
    insertBefore(c) { node.children.unshift(c); return c; },
    get firstChild() { return node.children[0] || null; },
  };
  return node;
}
globalThis.document = { createElement: makeNode, getElementById: () => null };

let renders = 0;
const renderMarkdown = (t) => { renders += 1; return t; };
const renderRich = () => {};

/* I fotogrammi: si scaricano a mano, come farebbe il browser al suo giro. */
const frames = new Map();
let nextFrame = 1;
globalThis.requestAnimationFrame = (fn) => { const id = nextFrame++; frames.set(id, fn); return id; };
globalThis.cancelAnimationFrame = (id) => { frames.delete(id); };
const paint = () => { const due = [...frames.values()]; frames.clear(); due.forEach((fn) => fn()); };

/* Costante di modulo di `home-chat.js`: il ritaglio del metodo non la porta. */
const HISTORY_PAGE_SIZE = 50;
let turns = [];
const sessionManager = {
  currentKey: 'websocket:default',
  loadThread: async () => ({ thread: { messages: turns, page: null }, stale: false }),
};

let refreshes = 0;
function makeChat() {
  return {
    el: makeNode(),
    gap: { refresh: () => { refreshes += 1; } },
    pager: { adopt() {}, ensureReach() {} },
    turnNode: null, blockNode: null, buffer: '', turnId: null,
    _empty: false, _seconds: null, _frame: null, _batching: false,
    syncEmpty() {}, scrollToBottom() {}, _follow() {}, _tailOf() {}, _register() {},
    _appendUser(text, origin, media, toTop = false) {
      const n = makeNode(); n.className = 'home-msg home-msg-user'; n.innerHTML = text;
      return this._append(n, toTop);
    },
    _appendAssistant(content, media, toTop = false) {
      const n = makeNode(); n.className = 'home-msg home-msg-jenny'; n.innerHTML = content;
      return this._append(n, toTop);
    },
    __METHODS__,
  };
}

const exchange = (n) => {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ role: 'user', text: 'domanda ' + i });
    out.push({ role: 'assistant', text: 'risposta ' + i, turnId: 't' + i });
  }
  return out;
};
"""


def _run(script: str) -> None:
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    methods = ",\n    ".join(member(src, name) for name in _METHODS)
    run_js(_HARNESS.replace("__METHODS__", methods) + "\n" + script)


def test_a_history_page_measures_the_gap_once() -> None:
    _run("""
      const chat = makeChat();
      turns = exchange(25);                  // 50 bolle
      await chat.load();
      assert.equal(chat.el.children.length, 50);
      assert.equal(refreshes, 1, `margine ricalcolato ${refreshes} volte per una pagina`);
    """)


def test_an_older_page_measures_the_gap_once() -> None:
    _run("""
      const chat = makeChat();
      chat.prependTurns(exchange(20));
      assert.equal(chat.el.children.length, 40);
      assert.equal(refreshes, 1, `margine ricalcolato ${refreshes} volte per una pagina`);
    """)


def test_a_live_message_still_measures_the_gap_right_away() -> None:
    """Fuori dalla storia niente cambia: il messaggio che arriva si scansa
    subito, non alla prossima pagina."""
    _run("""
      const chat = makeChat();
      chat._ensureTurn();
      assert.equal(refreshes, 1);
    """)


def test_deltas_in_one_frame_cost_one_render() -> None:
    _run("""
      const chat = makeChat();
      for (let i = 0; i < 40; i++) chat._delta('x');
      assert.equal(renders, 0, `${renders} rese sincrone per 40 delta`);
      paint();
      assert.equal(renders, 1);
      assert.equal(chat.blockNode.innerHTML, 'x'.repeat(40));
      chat._delta('y');
      paint();
      assert.equal(renders, 2);
    """)


def test_stream_end_renders_at_once_and_drops_the_pending_frame() -> None:
    _run("""
      const chat = makeChat();
      chat._delta('ciao ');
      chat._delta('mondo');
      const block = chat.blockNode;
      chat._streamEnd();
      assert.equal(block.innerHTML, 'ciao mondo', 'la resa finale ha aspettato il fotogramma');
      const before = renders;
      paint();
      assert.equal(renders, before, 'dopo stream_end un fotogramma ha ridisegnato ancora');
      assert.equal(frames.size, 0);
    """)


def test_a_turn_closed_without_stream_end_keeps_its_last_deltas() -> None:
    """Un turno nuovo che scavalca quello aperto chiude la bolla: i delta in
    coda per il fotogramma dopo vanno disegnati prima, o la risposta resta
    tronca a schermo."""
    _run("""
      const chat = makeChat();
      chat._delta('fino a ');
      paint();
      chat._delta('qui');
      const block = chat.blockNode;
      chat._resetTurn();
      assert.equal(block.innerHTML, 'fino a qui');
      assert.equal(frames.size, 0);
    """)
