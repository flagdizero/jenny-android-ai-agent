"""Un messaggio che non è entrato torna indietro, e lo dice a parole.

Due guasti della stessa famiglia, che in officina convivevano:

1. **Socket chiuso.** `sendMessage` disegnava la bolla, svuotava il campo,
   buttava gli allegati e *poi* provava a spedire. Quindi un filo caduto ti
   lasciava una bolla che sembrava partita, una riga d'errore di fianco, e il
   testo perduto. La casa questo l'aveva già risolto al contrario — prima si
   spedisce, poi si disegna — e la regola arriva da lì.

2. **Rifiuto del gateway.** Il messaggio parte, il gateway lo guarda e dice no
   (un allegato che non riesce ad aprire). La riga diceva
   «Errore: image_rejected» e la bolla restava dov'era.

Si misura quel che si legge nel filo e quel che resta nel campo, non quali
funzioni sono state chiamate: il difetto è tutto in quella differenza.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
WIRE_ERROR_JS = ASSETS / "shared" / "wire-error.js"
I18N = ASSETS / "i18n"

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

const WORDS = __WORDS__;
const i18n = {
  t: (key) => {
    const parts = key.split('.');
    let v = WORDS;
    for (const p of parts) { if (!v || typeof v !== 'object') return key; v = v[p]; }
    return typeof v === 'string' ? v : key;
  },
};
const { describeWireError } = await import('__WIRE_ERROR_URL__');

let sent = [];
let sendWorks = true;
const wsManager = { sendToChat(key, text, media) { if (!sendWorks) return false;
                                                    sent.push({ text, media }); return true; } };
const sessionManager = { currentKey: 'websocket:default', ensureAttached() {} };

function makeNode(className = '') {
  const node = {
    className, textContent: '', isConnected: false, children: [],
    appendChild(child) { child.isConnected = true; node.children.push(child); return child; },
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
let allNodes = [];
globalThis.document = { createElement: () => makeNode() };

function makeChat({ text = '', media = [] } = {}) {
  allNodes = [];
  sent = [];
  sendWorks = true;
  const chat = {
    input: { value: text, style: {}, focus() {} },
    imageHandler: {
      _media: media,
      getImages() { return this._media; },
      getAttachmentEntries() { return this._media; },
      clear() { this._media = []; },
    },
    _pendingSend: null,
    _autoScroll: false,
    _streamResets: 0,
    _resetStreamState() { chat._streamResets++; },
    _renderMediaAttachments() {},
    _setMessageSource() {},
    _appendMsgActions() {},
    _updateSendState() {},
    _updateActions() {},
    scrollToBottom() {},
    _belongsToOpenChat() { return true; },
    _applyTurnBoundary() { return false; },  // ci si ferma prima dello switch
    __SEND__,
    __SHOW_ERROR__,
    __HANDLE_ERROR__,
    __TAKE_BACK__,
    __DISPATCH__,
  };
  chat.chatArea = makeNode('chat-area');
  return chat;
}

/* Quel che si legge nel filo, dall'alto verso il basso. */
const thread = (chat) => chat.chatArea.children.map((n) => {
  if (String(n.className).includes('chat-error')) return 'errore: ' + n.textContent;
  const body = n.children.find((c) => String(c.className).includes('chat-content'));
  return 'tu: ' + (body ? body.textContent : '');
});
"""


def _harness_src() -> str:
    words = json.loads((I18N / "it.json").read_text(encoding="utf-8"))
    src = CHAT_JS.read_text(encoding="utf-8")
    return (
        _HARNESS.replace("__WORDS__", json.dumps({"common": words["common"], "chat": words["chat"]},
                                                 ensure_ascii=False))
        .replace("__WIRE_ERROR_URL__", WIRE_ERROR_JS.as_uri())
        .replace("__SEND__", _member(src, "sendMessage"))
        .replace("__SHOW_ERROR__", _member(src, "_showChatError"))
        .replace("__HANDLE_ERROR__", _member(src, "_handleError"))
        .replace("__TAKE_BACK__", _member(src, "_takeBackPendingSend"))
        .replace("__DISPATCH__", _member(src, "handleMessage"))
    )


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness_src() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── Socket chiuso ────────────────────────────────────────────────────────────


def test_a_message_that_never_left_draws_no_bubble() -> None:
    _run_js("""
      const chat = makeChat({ text: 'ci sei?' });
      sendWorks = false;
      await chat.sendMessage();
      assert.deepEqual(thread(chat), ['errore: WebSocket non connesso. In attesa di riconnessione...'],
                       'la bolla non deve comparire per un messaggio che non è partito');
    """)


def test_a_message_that_never_left_keeps_your_words() -> None:
    """Il difetto più caro dei due: il testo se ne andava e non tornava."""
    _run_js("""
      const chat = makeChat({ text: 'una cosa lunga che non voglio riscrivere' });
      sendWorks = false;
      await chat.sendMessage();
      assert.equal(chat.input.value, 'una cosa lunga che non voglio riscrivere');
    """)


def test_a_message_that_never_left_keeps_your_attachments() -> None:
    _run_js("""
      const chat = makeChat({ text: 'guarda', media: [{ name: 'foto.jpg' }] });
      sendWorks = false;
      await chat.sendMessage();
      assert.equal(chat.imageHandler.getAttachmentEntries().length, 1);
    """)


def test_a_message_that_left_does_draw_and_does_clear() -> None:
    """La guardia dell'altro verso: non deve smettere di funzionare il caso buono."""
    _run_js("""
      const chat = makeChat({ text: 'ciao', media: [{ name: 'foto.jpg' }] });
      await chat.sendMessage();
      assert.deepEqual(thread(chat), ['tu: ciao']);
      assert.equal(chat.input.value, '');
      assert.equal(chat.imageHandler.getAttachmentEntries().length, 0);
      assert.deepEqual(sent.map((s) => s.text), ['ciao']);
    """)


# ── Rifiuto del gateway ──────────────────────────────────────────────────────


def test_a_refused_message_comes_back() -> None:
    _run_js("""
      const chat = makeChat({ text: 'te la mando' });
      await chat.sendMessage();
      assert.deepEqual(thread(chat), ['tu: te la mando']);
      chat._handleError({ detail: 'image_rejected', reason: 'decode' });
      assert.deepEqual(thread(chat), ['errore: Non sono riuscita ad aprire questo file'],
                       'la bolla doveva sparire insieme al messaggio che non è entrato');
      assert.equal(chat.input.value, 'te la mando', 'il testo non è tornato nel campo');
    """)


def test_what_you_read_is_never_the_raw_code() -> None:
    """Il test che avrebbe preso «Errore: image_rejected»."""
    _run_js("""
      for (const reason of ['decode', 'size', 'malformed', 'too_many_images']) {
        const chat = makeChat({ text: 'x' });
        await chat.sendMessage();
        chat._handleError({ detail: 'image_rejected', reason });
        const line = thread(chat).join(' ');
        assert.ok(!line.includes('image_rejected'), reason + ' -> ' + line);
        assert.ok(!line.includes(reason), reason + ' -> ' + line);
      }
    """)


def test_new_words_in_the_box_are_never_overwritten() -> None:
    """Hai già ricominciato a scrivere: quel che avevi prima non torna sopra."""
    _run_js("""
      const chat = makeChat({ text: 'primo' });
      await chat.sendMessage();
      chat.input.value = 'sto scrivendo altro';
      chat._handleError({ detail: 'image_rejected', reason: 'decode' });
      assert.equal(chat.input.value, 'sto scrivendo altro');
      assert.deepEqual(thread(chat), ['errore: Non sono riuscita ad aprire questo file'],
                       'la bolla va via lo stesso: quel messaggio non è entrato');
    """)


def test_an_error_about_something_else_leaves_your_message_alone() -> None:
    """L'altra famiglia: non parla del tuo messaggio, non se lo prende."""
    _run_js("""
      const chat = makeChat({ text: 'ciao' });
      await chat.sendMessage();
      chat._handleError({ detail: 'invalid task_id', reason: 'invalid_task_id' });
      assert.deepEqual(thread(chat), ['tu: ciao', 'errore: Quel lavoro non esiste più']);
    """)


def test_once_the_gateway_answers_the_message_is_no_longer_in_doubt() -> None:
    """Come si sa *quale* bolla: quella in sospeso, e solo finché lo è.

    Appena arriva un frame che non è un rifiuto, il messaggio è entrato — e un
    errore successivo è un errore di altro, che non deve mangiarsi una bolla
    buona.
    """
    _run_js("""
      const chat = makeChat({ text: 'ciao' });
      await chat.sendMessage();
      chat.handleMessage({ event: 'delta', text: 'ri' });
      assert.equal(chat._pendingSend, null, 'la risposta dimostra che è entrato');
      chat._handleError({ detail: 'image_rejected', reason: 'decode' });
      assert.deepEqual(thread(chat), ['tu: ciao', 'errore: Non sono riuscita ad aprire questo file'],
                       'un rifiuto tardivo si è mangiato una bolla che era entrata');
    """)


def test_a_refusal_with_nothing_pending_still_says_something() -> None:
    """Rifiuto arrivato dopo un ricaricamento: niente da riprendere, resta la riga."""
    _run_js("""
      const chat = makeChat();
      chat._handleError({ detail: 'image_rejected', reason: 'size' });
      assert.deepEqual(thread(chat), ['errore: Il file è troppo grande']);
    """)
