"""Un testo già dipinto in chat non può essere sovrascritto da ciò che arriva dopo.

Un `message` è un testo **completo** — la consegna del tool `message`, un avviso
proattivo, la proiezione di un turno di un altro canale — mentre i `delta` sono un
testo che cresce. Il client li rendeva nello stesso contenitore: `_handleMessage`
riusava `_currentContent` se c'era e ce lo lasciava appeso, e `_flushRender`
riscrive `innerHTML` col buffer dei delta a ogni frame.

Misurato sul dispositivo il 27/08/2026, cron `chiusura-giornata` delle 20:00. La
sequenza vera dal transcript WebUI, tutta sotto lo stesso `turn_id`
(`cron:5f42046f:52c0f8…`), quindi nessun confine di turno e nessun
`_resetStreamState` fra le parti:

    seq 1-2   reasoning_delta / reasoning_end
    seq 3     stream_end        (stream …:0 — iterazione di soli tool, zero delta)
    seq 4     message           "ciao papi 😏 sono le 20:00 — ora di mollare tutto…"
    seq 5     message           text vuoto + tool_events del tool `message`
    seq 6-28  delta             "L'ho chiamato. Ora aspetto la sua risposta…"
    seq 29-30 stream_end, turn_end

In chat è comparso **solo** il testo di servizio: i delta hanno riscritto il blocco
dell'avviso. La notifica Android e il transcript avevano quello vero, ed è per questo
che un reload "riparava" la chat (`_buildTurns` concatena, non sovrascrive).

I metodi si estraggono dal sorgente e si eseguono in node su un `this` finto, come in
``test_live_turn_boundary_client.py``: qui però il DOM serve — è il posto dove il
testo si perde — e c'è un doppio minimo di `document`/`requestAnimationFrame`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")

# I metodi veri sotto misura: il rendering di un testo completo, quello di un testo
# che cresce, e la chiusura di segmento che i due condividono.
_METHODS = (
    "_ensureAiMessage",
    "_handleDelta",
    "_handleMessage",
    "_handleStreamEnd",
    "_scheduleFlush",
    "_flushRender",
    "_cancelPendingFrame",
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
   Solo ciò che questi metodi toccano: className, appendChild, innerHTML. Basta
   a rendere osservabile "il testo è stato sovrascritto", che è tutto il punto. */
function el(tag) {
  return {
    tag,
    className: '',
    innerHTML: '',
    children: [],
    appendChild(child) { this.children.push(child); return child; },
    querySelector() { return null; },
  };
}
const document = { createElement: el };

/* Frame a mano: il browser ne esegue uno fra un delta e l'altro, e il test fa
   lo stesso chiamando runFrames(). */
let _rafSeq = 0;
const _rafs = new Map();
const requestAnimationFrame = (cb) => { _rafSeq += 1; _rafs.set(_rafSeq, cb); return _rafSeq; };
const cancelAnimationFrame = (id) => { _rafs.delete(id); };
function runFrames() {
  const pending = [..._rafs.values()];
  _rafs.clear();
  for (const cb of pending) cb();
}

/* Nessuna selezione aperta: il guard di `_flushRender` non congela niente e il
   rendering si comporta come in una sessione di sola lettura. */
const selectionInside = () => false;

const renderMarkdown = (text) => text;
/* Formule e diagrammi: il banco guarda le bolle, non il loro contenuto ricco.
   **E questo finto e' il motivo per cui un difetto e' passato**: quando le
   librerie sono state cancellate lasciando i chiamanti, qui dentro non e'
   cambiato niente. Che il chiamante e la libreria stiano insieme lo misura
   `test_vendor_contract.py`, e come disegnano `test_rich_content_client.py`. */
const renderRichContent = () => {};

function makeChat() {
  return {
    chatArea: el('div'),
    _currentMsg: null,
    _currentContent: null,
    _currentThinking: null,
    _deltaBuffer: '',
    _deltaDirty: false,
    _reasoningDirty: false,
    _pendingFrame: null,
    _toolStates: {},
    _fileEditPaths: new Map(),
    unread: 0,
    // Fuori misura: rendono, contano o scrollano altro.
    _makeFilePathsClickable() {},
    _renderToolEvents() {},
    _renderTraceRow() {},
    _renderMediaAttachments() {},
    _appendLatency() {},
    // Registro del sorgente e riga di azioni: fuori misura qui, dove si guarda
    // solo quale blocco di testo finisce dipinto.
    _setMessageSource() {},
    _appendMsgActions() {},
    _appendSessionBoundary() {},
    _renderReasoningBody() {},
    _bumpUnread() { this.unread += 1; },
    scrollToBottom() {},
    __METHODS__,
  };
}

/* I blocchi di testo della bolla, nell'ordine in cui sono stati aperti. */
function blocks(chat) {
  const bubble = chat.chatArea.children[chat.chatArea.children.length - 1];
  if (!bubble) return [];
  return bubble.children.filter((c) => c.className === 'chat-content').map((c) => c.innerHTML);
}

/* I delta del testo di servizio, un pezzo per frame come in produzione. */
function stream(chat, text) {
  for (const chunk of text.split(' ')) {
    chat._handleDelta(chunk === text.split(' ')[0] ? chunk : ' ' + chunk);
    runFrames();
  }
}
""".replace("__METHODS__", methods)


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


AVVISO = "ciao papi, sono le 20:00 — ora di mollare tutto"
SERVIZIO = "L'ho chiamato. Ora aspetto la sua risposta"


def test_the_real_2000_sequence_keeps_the_alert() -> None:
    """La sequenza del 27/08: l'avviso deve essere ancora là alla fine del turno."""
    _run_js(f"""
      const chat = makeChat();
      // seq 3: l'iterazione di soli tool chiude il proprio segmento a vuoto.
      chat._handleStreamEnd();
      // seq 4: la consegna del tool `message`.
      chat._handleMessage({{ text: {AVVISO!r} }});
      // seq 5: i chip del tool, senza testo.
      chat._handleMessage({{ text: '', tool_events: [{{ phase: 'end', call_id: 'c1' }}], kind: 'progress' }});
      // seq 6-28: la narrazione del modello, nello stesso turno.
      stream(chat, {SERVIZIO!r});
      // seq 29: chiusura del segmento.
      chat._handleStreamEnd();

      const painted = blocks(chat);
      assert.ok(
        painted.includes({AVVISO!r}),
        "l'avviso consegnato è stato sovrascritto: " + JSON.stringify(painted),
      );
      assert.deepEqual(painted, [{AVVISO!r}, {SERVIZIO!r}]);
    """)


def test_two_messages_in_one_turn_are_two_blocks() -> None:
    """Due consegne nello stesso turno: la prima non viene cancellata dalla seconda.

    Il tetto di un avviso per ciclo vale sui soli turni silenziosi
    (``message.py``), quindi due ``message`` in un turno visibile sono un caso
    normale — e prima ne sopravviveva solo l'ultimo.
    """
    _run_js("""
      const chat = makeChat();
      chat._handleMessage({ text: 'primo' });
      chat._handleMessage({ text: 'secondo' });
      assert.deepEqual(blocks(chat), ['primo', 'secondo']);
    """)


def test_a_plain_streamed_turn_stays_one_block() -> None:
    """Una risposta normale non va spezzata: i delta di un segmento restano uniti."""
    _run_js("""
      const chat = makeChat();
      stream(chat, 'una risposta lunga come tante');
      chat._handleStreamEnd();
      assert.deepEqual(blocks(chat), ['una risposta lunga come tante']);
    """)


def test_a_message_does_not_lose_the_tail_of_an_open_stream() -> None:
    """Un `message` che arriva a segmento aperto lo chiude senza perderne la coda.

    Difensivo: in produzione lo ``stream_end`` dell'iterazione precede
    l'esecuzione dei tool (era il ``turn_seq 3`` del caso reale), quindi il
    segmento è già chiuso. Se quell'ordine cambiasse, il testo in volo non deve
    finire nel cestino insieme al frame pendente.
    """
    _run_js(f"""
      const chat = makeChat();
      chat._handleDelta('testo in volo');  // nessun frame eseguito: buffer sporco
      chat._handleMessage({{ text: {AVVISO!r} }});
      assert.deepEqual(blocks(chat), ['testo in volo', {AVVISO!r}]);
    """)
