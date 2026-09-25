"""Il pulsante Copia: cosa copia, e su quali bolle compare.

Il testo di una bolla non si ricostruisce da ``innerText`` — perde le recinzioni
dei blocchi di codice e il loro linguaggio, che è esattamente ciò che si vuole
quando si copia una risposta per incollarla altrove. Il sorgente si registra
dove la bolla nasce (cinque punti) e si legge da una ``WeakMap``; ``innerText``
resta come rete, così un pulsante Copia non copia mai il vuoto.

Due cose che una asserzione sul sorgente non vedrebbe, e che qui girano davvero:
una bolla con **più** ``.chat-content`` (turno testo → tool → testo) si copia
intera, e la riga in coda — Copia **e** i secondi, dal 21/09/2026 una sola —
resta una anche quando i due pezzi arrivano da chiamanti diversi e in ordine
diverso.

I metodi si estraggono dal sorgente e si eseguono in node su un `this` finto,
come in ``test_message_bubble_client.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"


pytestmark = requires_node

_METHODS = (
    "_setMessageSource",
    "_messageText",
    "_buildMsgActionButton",
    "_ensureMsgActions",
    "_appendMsgActions",
    "_appendLatency",
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
   Solo ciò che questi metodi toccano. `appendChild` **sposta** un figlio che è
   già dentro, come quello vero: è il meccanismo su cui poggia l'idempotenza
   della riga di azioni. */
function el(tag) {
  const node = {
    tag,
    className: '',
    innerHTML: '',
    innerText: '',
    textContent: '',
    title: '',
    attrs: {},
    children: [],
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(child) {
      const at = this.children.indexOf(child);
      if (at !== -1) this.children.splice(at, 1);
      this.children.push(child);
      return child;
    },
    insertBefore(child, before) {
      const at = this.children.indexOf(child);
      if (at !== -1) this.children.splice(at, 1);
      const i = before ? this.children.indexOf(before) : -1;
      if (i === -1) this.children.push(child);
      else this.children.splice(i, 0, child);
      return child;
    },
    get firstChild() { return this.children[0] || null; },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    /* Ricorsivo, e `:scope >` si legge come "solo i figli": la riga in coda
       ospita ora la `.chat-meta`, quindi cercarla solo fra i figli diretti
       della bolla non la troverebbe piu'. */
    querySelectorAll(sel) {
      const direct = sel.startsWith(':scope > ');
      const want = sel.replace(':scope > ', '').replace('.', '');
      const out = [];
      for (const c of this.children) {
        if (c.className.split(' ').includes(want)) out.push(c);
        if (!direct) out.push(...c.querySelectorAll(sel));
      }
      return out;
    },
  };
  node.classList = { contains: (c) => node.className.split(' ').includes(c) };
  return node;
}
const document = { createElement: el };
const i18n = { t: (key) => key };

function bubble(role, ...texts) {
  const msg = el('div');
  msg.className = `chat-msg chat-msg-${role}`;
  for (const text of texts) {
    const content = el('div');
    content.className = 'chat-content';
    content.innerText = text;
    msg.appendChild(content);
  }
  return msg;
}

function chat() {
  return {
    _msgSource: new WeakMap(),
    __METHODS__,
  };
}

/* Cosa c'e' nella riga in coda, **nell'ordine**: le classi dei pulsanti e,
   per i secondi, il testo che mostrano. L'ordine e' meta' del contratto. */
function actions(msg) {
  const row = msg.children.find((c) => c.className.split(' ').includes('chat-msg-actions'));
  if (!row) return null;
  return row.children.map((b) => (
    b.className === 'chat-meta' ? b.textContent : b.className.split(' ')[1]
  ));
}
""".replace("__METHODS__", methods)


def _run_js(script: str) -> None:
    run_js(_harness() + script)


FENCED = "Ecco:\n\n```python\nprint(1)\n```"


def test_the_recorded_source_wins_over_inner_text() -> None:
    """Il sorgente porta le recinzioni; `innerText` le avrebbe perse."""
    _run_js(f"""
      const c = chat();
      const msg = bubble('ai', 'Ecco:\\n\\nprint(1)');
      c._setMessageSource(msg, {FENCED!r});
      assert.equal(c._messageText(msg), {FENCED!r});
    """)


def test_inner_text_is_the_net_when_nothing_was_recorded() -> None:
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'una risposta dallo storico');
      assert.equal(c._messageText(msg), 'una risposta dallo storico');
    """)


def test_a_turn_with_several_segments_copies_whole() -> None:
    """Testo → tool → testo: una bolla, due `.chat-content`, una copia sola."""
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'primo', 'secondo');
      c._setMessageSource(msg, 'primo');
      c._setMessageSource(msg, 'secondo');
      assert.equal(c._messageText(msg), 'primo\\n\\nsecondo');
    """)


def test_the_actions_row_is_added_once_and_stays_last() -> None:
    """Due chiamate (blocco `message` e poi `turn_end`) lasciano una riga sola,
    con un Copia solo dentro.

    E **in coda**: fra le due chiamate la bolla può allungarsi — `_handleMessage`
    posa la riga e poi rende gli allegati, `_flushPersistedTurn` li rende prima.
    Qui in mezzo arriva un'immagine, che è il caso vero.
    """
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'risposta');
      c._appendMsgActions(msg);
      const media = el('div');
      media.className = 'chat-media';
      msg.appendChild(media);
      c._appendLatency(msg, 4000);
      c._appendMsgActions(msg);

      const rows = msg.children.filter((x) => x.className.split(' ').includes('chat-msg-actions'));
      assert.equal(rows.length, 1, 'la riga è stata duplicata');
      assert.equal(msg.children[msg.children.length - 1], rows[0], 'la riga non è in coda');
      assert.deepEqual(actions(msg), ['chat-msg-copy', '4.0s'], 'il Copia è doppio');
    """)


def test_the_row_is_one_line() -> None:
    """Una riga sola, chiesta esplicitamente: i secondi erano un nodo impilato
    sopra i pulsanti, e in coda a ogni risposta occupavano due righe."""
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    m = re.search(r"\.chat-msg-actions\s*\{([^}]*)\}", css)
    assert m, ".chat-msg-actions non ha stile"
    assert "display: flex" in m.group(1)
    assert "align-items: center" in m.group(1)


def test_the_seconds_share_the_row_with_copy() -> None:
    """**Una riga sola.** Erano due nodi impilati: i secondi sopra, il Copia
    sotto, due righe in coda a ogni risposta per due dati che si leggono
    insieme."""
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'risposta');
      c._appendMsgActions(msg);
      c._appendLatency(msg, 4000);
      assert.deepEqual(actions(msg), ['chat-msg-copy', '4.0s']);
      const outside = msg.children.filter((x) => x.className === 'chat-meta');
      assert.equal(outside.length, 0, 'i secondi sono ancora un nodo a sé');
    """)


def test_the_order_holds_whichever_arrives_first() -> None:
    """Nel vivo i secondi arrivano col `turn_end`, **prima** del Copia; nello
    storico i due si posano insieme. La riga si legge icona-poi-tempo in tutti
    e due i casi, o cambia forma a seconda di come sei arrivato lì."""
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'risposta');
      c._appendLatency(msg, 21300);   // prima i secondi
      c._appendMsgActions(msg);
      assert.deepEqual(actions(msg), ['chat-msg-copy', '21.3s']);
    """)


def test_the_seconds_are_written_once() -> None:
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'risposta');
      c._appendLatency(msg, 4000);
      c._appendLatency(msg, 9999);
      assert.deepEqual(actions(msg), ['4.0s']);
    """)


def test_a_user_bubble_gets_no_row_at_all() -> None:
    """Le bolle utente sono corte: basta la selezione nativa.

    Prima ci arrivava il `⋯`, che era la loro unica azione — apriva il foglio
    «Copia testo / Copia come Markdown». Il foglio non c'è più, quindi la riga
    resterebbe vuota: non si disegna.
    """
    _run_js("""
      const c = chat();
      const msg = bubble('user', 'ciao');
      c._appendMsgActions(msg);
      assert.equal(actions(msg), null);
    """)


def test_an_answer_gets_copy_and_nothing_else() -> None:
    _run_js("""
      const c = chat();
      const msg = bubble('ai', 'risposta');
      c._appendMsgActions(msg);
      assert.deepEqual(actions(msg), ['chat-msg-copy']);
    """)


def test_a_tools_only_turn_offers_nothing_to_copy() -> None:
    """Un turno di soli tool non ha testo: nessun Copia, nessun pulsante muto.

    I secondi però ci arrivano lo stesso — il turno è durato — e da soli si
    posano nella riga senza bisogno di nessuno che la crei prima.
    """
    _run_js("""
      const c = chat();
      const msg = bubble('ai');
      c._appendMsgActions(msg);
      assert.equal(actions(msg), null);
      c._appendLatency(msg, 1500);
      assert.deepEqual(actions(msg), ['1.5s']);
    """)
