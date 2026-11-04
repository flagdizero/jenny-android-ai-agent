"""La storia di una conversazione va sopra la risposta che arriva mentre si carica.

Lo stesso difetto che la casa aveva nel suo filo: aprire una
conversazione mentre Jenny ci sta rispondendo — un cambio di chat, una
riconnessione, un /new — svuota la chat e poi *aspetta* bootstrap e thread. In
quell'attesa i delta del turno in corso disegnano la loro bolla nella chat
vuota, e la storia arrivata dopo veniva accodata **sotto**: la risposta in cima,
la domanda a cui risponde in fondo.

Si esegue in node il ``loadInitialHistory`` vero, ritagliato dal sorgente, su
una chat finta i cui figli sono una lista: il DOM ridotto a quel che il difetto
guarda, cioe' l'ordine.
"""

from __future__ import annotations

from support.js_harness import ASSETS, member, requires_node, run_js

CHAT_JS = ASSETS / "mobile-chat.js"

pytestmark = requires_node

_HARNESS = """
import assert from 'node:assert/strict';

let onBootstrap = () => {};
const api = {
  bootstrap: async () => { await null; onBootstrap(); },
};
const sessionManager = {
  switchGeneration: 1,
  currentKey: 'websocket:default',
  async loadThread() {
    return { thread: { messages: [{ text: 'domanda' }, { text: 'risposta vecchia' }], page: {} }, scope: null };
  },
};
const scopeChip = { syncFromSession() {} };
const writeSwitch = { syncFromSession() {} };

function node(name) {
  return { name, remove() { const i = area.children.indexOf(this); if (i >= 0) area.children.splice(i, 1); } };
}
const area = {
  children: [],
  appendChild(n) { n.remove(); this.children.push(n); return n; },
};
const identity = node('identita');
area.children.push(identity);

const chat = {
  chatArea: area,
  identityEl: identity,
  _initialHistoryLoaded: false,
  _pager: { adopt() {} },
  _initRuntimeModelFromBootstrap() {},
  _clearHistoryError() {},
  _showHistoryError() { throw new Error('non doveva fallire'); },
  _ensureHistoryReach() {},
  scrollToBottom() {},
  _renderThreadMessages(msgs) { for (const m of msgs) area.appendChild(node(m.text)); },
  __LOAD__
};
"""


def _harness() -> str:
    src = CHAT_JS.read_text(encoding="utf-8")
    return _HARNESS.replace("__LOAD__", member(src, "loadInitialHistory"))


def test_history_lands_above_the_live_bubble() -> None:
    run_js(
        _harness()
        + """
      let bubble;
      onBootstrap = () => { bubble = area.appendChild(node('risposta in diretta')); };
      await chat.loadInitialHistory();
      assert.deepEqual(area.children.map((n) => n.name),
        ['identita', 'domanda', 'risposta vecchia', 'risposta in diretta']);
      assert.equal(area.children.at(-1), bubble, 'e\\' lo stesso nodo: lo stream ci scrive ancora');
    """
    )


def test_without_live_frames_nothing_moves() -> None:
    run_js(
        _harness()
        + """
      await chat.loadInitialHistory();
      assert.deepEqual(area.children.map((n) => n.name), ['identita', 'domanda', 'risposta vecchia']);
    """
    )
