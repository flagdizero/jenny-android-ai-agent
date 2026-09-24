"""Un tool fallito si apre e si chiude come un tool riuscito.

Nel chip di un tool il corpo ha sempre avuto due canali diversi a seconda dell'esito.
Il risultato (`phase: "end"`) veniva solo parcheggiato in `dataset.result`, e finiva nel
DOM soltanto al tocco, dentro un `<pre class="tool-result-text">` che un secondo tocco
rimuove. L'errore (`phase: "error"`) veniva invece appeso subito come
`<div class="chat-tool-error">`: non passava da `dataset.result`, quindi
`_toggleToolResult` non lo vedeva nemmeno — era espanso di default **e** non
richiudibile, e su un `call_id` che ripassava (una lista di eventi resa due volte) ne
compariva una copia in piu'.

Ora l'errore e' il corpo del chip come lo e' un risultato: stesso `dataset.result`, piu'
un `dataset.errored` che ne decide solo il colore. Il comportamento "chiuso di default,
si apre al tocco" non e' riscritto, e' lo stesso codice.

I metodi si estraggono dal sorgente e girano in node su un `this` finto, come in
``test_message_bubble_client.py``. Qui il DOM serve per davvero — il punto e'
esattamente cosa sta appeso al chip e quando — quindi lo stub tiene `dataset`,
`remove()` e un matcher per i tre soli selettori che questi metodi usano.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
STYLE_CSS = ASSETS / "mobile-style.css"


pytestmark = requires_node

_METHODS = (
    "_renderToolEvents",
    "_createToolElement",
    "_markToolExpandable",
    "_toggleToolResult",
)


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\(([^)]*)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return f"{name}({body.group(1)}) {{{body.group(2)}\n  }}"


def _harness() -> str:
    chat = CHAT_JS.read_text(encoding="utf-8")
    methods = ",\n    ".join(_method(chat, name) for name in _METHODS)
    return """
import assert from 'node:assert/strict';

/* ── DOM minimo ──────────────────────────────────────────────────────────────
   Solo cio' che questi metodi toccano. `dataset` e `remove()` sono il cuore della
   misura: il primo e' il canale del corpo, il secondo e' la chiusura. */
function matches(node, sel) {
  if (sel.startsWith('.')) return String(node.className).split(/\\s+/).includes(sel.slice(1));
  const attr = sel.match(/^\\[([\\w-]+)="(.*)"\\]$/);
  if (attr) {
    const camel = attr[1].replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    return String(node.dataset[camel]) === attr[2];
  }
  throw new Error('selettore non supportato dallo stub: ' + sel);
}

function el(tag) {
  return {
    tag,
    className: '',
    textContent: '',
    dataset: {},
    style: {},
    children: [],
    parent: null,
    _handlers: {},
    appendChild(child) { child.parent = this; this.children.push(child); return child; },
    remove() {
      if (!this.parent) return;
      this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
    },
    addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); },
    click() { for (const fn of this._handlers.click || []) fn(); },
    querySelector(sel) {
      for (const child of this.children) {
        if (matches(child, sel)) return child;
        const deep = child.querySelector(sel);
        if (deep) return deep;
      }
      return null;
    },
    querySelectorAll(sel) {
      const out = [];
      for (const child of this.children) {
        if (matches(child, sel)) out.push(child);
        out.push(...child.querySelectorAll(sel));
      }
      return out;
    },
  };
}
const document = { createElement: el };

const TOOL_ICONS = { start: 'ti-loader', end: 'ti-check', error: 'ti-alert-triangle' };

function makeChat(msg) {
  return {
    _currentMsg: msg,
    _toolStates: {},
    // Fuori misura: la meta-row e' un contenitore, non un comportamento.
    _ensureMetaRow(node) { return node; },
    __METHODS__,
  };
}

/* Il chip di un tool, per `call_id`. */
function chip(msg, callId) {
  return msg.querySelector('[data-call-id="' + callId + '"]');
}

/* Il corpo aperto del chip, se c'e'. */
function body(tool) {
  return tool.querySelector('.tool-result-text');
}

const START = { phase: 'start', call_id: 'c1', name: 'read_file', result: null, error: null };
const BOOM = {
  phase: 'error',
  call_id: 'c1',
  name: 'read_file',
  result: null,
  error: "FileNotFoundError: /workspace/manca.md",
};
""".replace("__METHODS__", methods)


def _run_js(script: str) -> None:
    run_js(_harness() + script)


def test_a_failed_tool_starts_collapsed() -> None:
    """Sequenza viva `start` → `error`: il testo dell'errore non e' nel DOM."""
    _run_js("""
const msg = el('div');
const chat = makeChat(msg);
chat._renderToolEvents([START, BOOM]);

const tool = chip(msg, 'c1');
assert.ok(tool, 'il chip del tool deve esistere');
assert.equal(body(tool), null, "l'errore non deve essere gia' aperto");
assert.equal(msg.querySelectorAll('.chat-tool-error').length, 0, 'niente blocco fuori dal toggle');
assert.equal(tool.dataset.result, BOOM.error, "l'errore passa dal canale del risultato");
assert.equal(tool.style.cursor, 'pointer', 'il chip deve dichiararsi apribile');
""")


def test_a_tap_opens_the_error_and_a_second_one_closes_it() -> None:
    """Stesso gesto del risultato riuscito, e in piu' il corpo aperto e' rosso."""
    _run_js("""
const msg = el('div');
const chat = makeChat(msg);
chat._renderToolEvents([START, BOOM]);
const tool = chip(msg, 'c1');

tool.click();
const opened = body(tool);
assert.ok(opened, 'il tocco deve aprire il corpo');
assert.equal(opened.textContent, BOOM.error);
assert.ok(String(opened.className).split(/\\s+/).includes('is-error'), 'corpo in errore = colore errore');

tool.click();
assert.equal(body(tool), null, 'il secondo tocco deve richiuderlo');
""")


def test_the_replay_shape_behaves_like_the_live_one() -> None:
    """Dopo un reload `_merge_tool_events` consegna un solo evento `error`, senza `start`."""
    _run_js("""
const msg = el('div');
const chat = makeChat(msg);
chat._renderToolEvents([BOOM]);

const tool = chip(msg, 'c1');
assert.ok(tool, 'il chip nasce anche senza aver visto lo start');
assert.equal(body(tool), null, 'chiuso come nel live');
assert.equal(tool.style.cursor, 'pointer');

tool.click();
assert.equal(body(tool).textContent, BOOM.error);
""")


def test_the_same_error_twice_is_still_one_body() -> None:
    """Un `call_id` che ripassa non deve accumulare copie del corpo."""
    _run_js("""
const msg = el('div');
const chat = makeChat(msg);
chat._renderToolEvents([START, BOOM]);
chat._renderToolEvents([BOOM]);

const tool = chip(msg, 'c1');
tool.click();
assert.equal(tool.querySelectorAll('.tool-result-text').length, 1, 'un corpo solo');
""")


def test_the_dead_channel_is_gone() -> None:
    """`chat-tool-error` era il canale che restava espanso: non deve poter rientrare."""
    assert "chat-tool-error" not in CHAT_JS.read_text(encoding="utf-8")
    assert "chat-tool-error" not in STYLE_CSS.read_text(encoding="utf-8")
