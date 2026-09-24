"""In casa, un messaggio rifiutato torna indietro e la riga lo dice.

Prima la casa i rifiuti non li ascoltava affatto: il telefono disegnava la
bolla, il gateway diceva no, e non succedeva più niente. Da fuori era identico a
Jenny che ti ignora.

Le parole e la famiglia le decide il modulo condiviso con l'officina, che ha i
suoi test; qui si misura la parte che la casa fa per conto suo — la bolla che se
ne va, la riga che resta nel filo, e il testo che torna a chi lo deve rimettere
nel campo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
CASA_CHAT_JS = ASSETS / "casa-chat.js"
CASA_APP_JS = ASSETS / "casa-app.js"
WIRE_ERROR_JS = ASSETS / "shared" / "wire-error.js"
I18N = ASSETS / "i18n"


pytestmark = requires_node


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

const WORDS = __WORDS__;
const i18n = {
  t: (key) => {
    let v = WORDS;
    for (const p of key.split('.')) { if (!v || typeof v !== 'object') return key; v = v[p]; }
    return typeof v === 'string' ? v : key;
  },
};
const { describeWireError } = await import('__WIRE_ERROR_URL__');
const sessionManager = { currentChatId: 'default' };

let allNodes = [];
function makeNode() {
  const node = {
    className: '', textContent: '', innerHTML: '', isConnected: false, children: [],
    appendChild(child) { child.isConnected = true; node.children.push(child); return child; },
    insertBefore(child) { child.isConnected = true; node.children.unshift(child); return child; },
    get firstChild() { return node.children[0] || null; },
    remove() {
      node.isConnected = false;
      for (const parent of allNodes) {
        const i = parent.children.indexOf(node);
        if (i !== -1) parent.children.splice(i, 1);
      }
    },
  };
  allNodes.push(node);
  return node;
}
globalThis.document = { createElement: makeNode, getElementById: () => null };

function makeChat() {
  allNodes = [];
  const chat = {
    el: makeNode(),
    turnId: null,
    _empty: true,
    _pendingSend: null,
    onSendRejected: null,
    returned: [],
    syncEmpty() {},
    scrollToBottom() {},
    _resetTurn() {},
    _originBadge() { return null; },
    _appendMedia() {},
    _delta() {}, _streamEnd() {}, _message() {}, _externalUser() {}, _turnEnd() {},
    __APPEND_OWN__,
    __ERROR__,
    __TAKE_BACK__,
    __NOTE__,
    __APPEND_USER__,
    __APPEND__,
    __BELONGS__,
    __CROSSES__,
    __HANDLE_FRAME__,
  };
  chat.onSendRejected = (text) => chat.returned.push(text);
  return chat;
}

/* Quel che si legge nel filo, dall'alto verso il basso. */
const thread = (chat) => chat.el.children.map((n) => {
  if (String(n.className).includes('casa-note')) return 'nota: ' + n.textContent;
  const block = n.children.find((c) => c.className === 'casa-block');
  return 'tu: ' + (block ? block.textContent : '');
});
"""


def _harness_src() -> str:
    words = json.loads((I18N / "it.json").read_text(encoding="utf-8"))
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    return (
        _HARNESS.replace("__WORDS__", json.dumps({"common": words["common"]}, ensure_ascii=False))
        .replace("__WIRE_ERROR_URL__", WIRE_ERROR_JS.as_uri())
        .replace("__APPEND_OWN__", _member(src, "appendOwn"))
        .replace("__ERROR__", _member(src, "_error"))
        .replace("__TAKE_BACK__", _member(src, "_takeBackPendingSend"))
        .replace("__NOTE__", _member(src, "_appendNote"))
        .replace("__APPEND_USER__", _member(src, "_appendUser"))
        .replace("__APPEND__", _member(src, "_append"))
        .replace("__BELONGS__", _member(src, "_belongsHere"))
        .replace("__CROSSES__", _member(src, "_crossesTurn"))
        .replace("__HANDLE_FRAME__", _member(src, "handleFrame"))
    )


def _run_js(script: str) -> None:
    run_js(_harness_src() + "\n" + script)


def test_a_refused_message_leaves_the_thread_and_says_why() -> None:
    _run_js("""
      const chat = makeChat();
      chat.appendOwn('te la mando', []);
      assert.deepEqual(thread(chat), ['tu: te la mando']);
      chat.handleFrame({ event: 'error', detail: 'image_rejected', reason: 'decode' });
      assert.deepEqual(thread(chat), ['nota: Non sono riuscita ad aprire questo file']);
      assert.deepEqual(chat.returned, ['te la mando'], 'il testo non è tornato a chi ha il campo');
    """)


def test_the_note_never_shows_the_raw_code() -> None:
    _run_js("""
      for (const reason of ['decode', 'size', 'malformed', 'too_many_files']) {
        const chat = makeChat();
        chat.appendOwn('x', []);
        chat.handleFrame({ event: 'error', detail: 'image_rejected', reason });
        const line = thread(chat).join(' ');
        assert.ok(!line.includes('image_rejected'), line);
        assert.ok(!line.includes(reason), line);
        assert.ok(!line.includes('common.wireError'), 'chiave non tradotta: ' + line);
      }
    """)


def test_an_error_about_something_else_leaves_your_message_alone() -> None:
    _run_js("""
      const chat = makeChat();
      chat.appendOwn('ciao', []);
      chat.handleFrame({ event: 'error', detail: 'invalid task_id', reason: 'invalid_task_id' });
      assert.deepEqual(thread(chat), ['tu: ciao', 'nota: Quel lavoro non esiste più']);
      assert.deepEqual(chat.returned, []);
    """)


def test_once_the_gateway_answers_the_message_is_no_longer_in_doubt() -> None:
    _run_js("""
      const chat = makeChat();
      chat.appendOwn('ciao', []);
      chat.handleFrame({ event: 'delta', text: 'ri' });
      assert.equal(chat._pendingSend, null);
      chat.handleFrame({ event: 'error', detail: 'image_rejected', reason: 'decode' });
      assert.deepEqual(thread(chat), ['tu: ciao', 'nota: Non sono riuscita ad aprire questo file'],
                       'un rifiuto tardivo si è mangiato una bolla che era entrata');
    """)


def test_a_refusal_with_nothing_pending_still_says_something() -> None:
    _run_js("""
      const chat = makeChat();
      chat.handleFrame({ event: 'error', detail: 'image_rejected', reason: 'size' });
      assert.deepEqual(thread(chat), ['nota: Il file è troppo grande']);
      assert.deepEqual(chat.returned, []);
    """)


def test_the_empty_state_goes_away_for_a_note_too() -> None:
    """Una nota è contenuto: lasciare «non c'è conversazione» sopra sarebbe la
    stessa svista dello stato vuoto sopra la storia, già vista una volta."""
    _run_js("""
      const chat = makeChat();
      chat.handleFrame({ event: 'error', reason: 'unknown_type' });
      assert.equal(chat._empty, false);
    """)


def test_the_box_keeps_what_you_are_writing_now() -> None:
    """La regola sta nel guscio, che è chi possiede il campo."""
    src = CASA_APP_JS.read_text(encoding="utf-8")
    hook = re.search(r"this\.chat\.onSendRejected = \(text\) => \{(.*?)\n    \};", src, re.S)
    assert hook, "la casa non riprende il testo di un messaggio rifiutato"
    body = hook.group(1)
    assert "this.input.value.trim()" in body, "sovrascrive il campo senza guardare cosa c'è dentro"
    assert "this.input.value = text" in body


def test_the_thread_keeps_its_bottom_when_the_rows_below_it_grow() -> None:
    """Il filo è `flex: 1`: ogni riga che compare sotto gliela toglie.

    Misurato sul telefono allegando due video: la nota che spiegava il rifiuto
    finiva **sotto il bordo**, cioè fuori schermo proprio nel momento in cui
    serviva leggerla. La striscia degli allegati è *fratello* del composer, non
    figlio, quindi il `ResizeObserver` che c'era — messo solo sul composer per
    la geometria di Jenny — non la vedeva.

    Vale anche per l'ultimo messaggio quando alleghi una foto, e c'era da
    sempre: la nota l'ha solo reso visibile.
    """
    src = CASA_APP_JS.read_text(encoding="utf-8")
    block = re.search(r"if \(window\.ResizeObserver\) \{(.*?)\n    \}", src, re.S)
    assert block, "nessun osservatore delle altezze"
    body = block.group(1)
    for sel in (".casa-composer", ".casa-pending", ".casa-activity", ".casa-wire"):
        assert sel in body, f"{sel} non è osservato: il filo perderà il fondo"
    assert "keepBottom()" in body, "l'osservatore misura e basta, non riaggancia il fondo"

    chat = CASA_CHAT_JS.read_text(encoding="utf-8")
    keep = _member(chat, "keepBottom")
    assert "_follow()" in keep, (
        "riagganciare deve rispettare chi sta rileggendo più su: `_follow` "
        "scorre solo se ci si era"
    )
