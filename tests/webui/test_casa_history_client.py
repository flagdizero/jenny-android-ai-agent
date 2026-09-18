"""La pagina precedente in casa: quel che si vede nel filo, prima e dopo.

La macchina a stati è quella condivisa con l'officina e ha i suoi test
(`test_history_reach_client.py`, e i tre banchi che eseguono `loadMoreHistory`
per intero). Qui si misura la metà che la casa non condivide: **il disegno**.
Un turno dell'officina porta pensieri, strumenti e modifiche ai file; in casa ne
sopravvivono il testo e gli allegati, e una pagina di lavoro muto può non
lasciare niente a schermo.

Si contano le bolle, non le chiamate. Che `prependTurns` sia stata invocata non
dice nulla: il difetto che conta è una pagina incollata **sotto** invece che
sopra, o incollata al contrario — e quello si vede solo leggendo il filo.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CASA_CHAT_JS = ASSETS / "casa-chat.js"

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

/* Un DOM ridotto a quel che il disegno tocca: un nodo ha una classe, del testo
   e dei figli, e il filo è una lista ordinata. Basta a rispondere alla sola
   domanda che conta: cosa si legge, dall'alto verso il basso. */
function makeNode() {
  const node = {
    className: '', textContent: '', innerHTML: '', hidden: false,
    children: [],
    appendChild(child) { node.children.push(child); return child; },
    insertBefore(child, before) {
      const i = before ? node.children.indexOf(before) : -1;
      if (i === -1) node.children.unshift(child);
      else node.children.splice(i, 0, child);
      return child;
    },
    get firstChild() { return node.children[0] || null; },
  };
  return node;
}
globalThis.document = {
  createElement: makeNode,
  getElementById: () => null,
};

/* Il markdown non è oggetto di questi test: qui il testo resta testo. */
const renderMarkdown = (t) => t;
const i18n = { t: (key) => 'i18n:' + key };

function makeChat() {
  const chat = {
    el: makeNode(),
    _empty: true,
    _stick: true,
    syncEmpty() {},
    scrollToBottom() {},
    _originBadge() { return null; },
    _appendMedia(node, media) {
      for (const m of media) {
        const el = makeNode();
        el.className = 'casa-media';
        el.textContent = m.name || '';
        node.appendChild(el);
      }
    },
    __BUILD_TURNS__,
    __PREPEND__,
    __APPEND_USER__,
    __APPEND_ASSISTANT__,
    __APPEND_BOUNDARY__,
    __APPEND__,
  };
  return chat;
}

/* Quel che si legge nel filo, dall'alto verso il basso: una riga per bolla. */
const readThread = (chat) => chat.el.children.map((n) => {
  if (String(n.className).includes('casa-boundary')) return '---';
  const block = n.children.find((c) => c.className === 'casa-block');
  const who = String(n.className).includes('casa-msg-user') ? 'tu' : 'jenny';
  return who + ': ' + (block ? (block.textContent || block.innerHTML) : '');
});

const user = (text, turn) => ({ role: 'user', text, turnId: turn });
const said = (text, turn) => ({ role: 'assistant', text, turnId: turn });
const trace = (turn) => ({ role: 'tool', kind: 'trace', content: 'read_file: x', turnId: turn });
"""


def _harness() -> str:
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    return (
        _HARNESS.replace("__BUILD_TURNS__", _member(src, "_buildTurns"))
        .replace("__PREPEND__", _member(src, "prependTurns"))
        .replace("__APPEND_USER__", _member(src, "_appendUser"))
        .replace("__APPEND_ASSISTANT__", _member(src, "_appendAssistant"))
        .replace("__APPEND_BOUNDARY__", _member(src, "_appendBoundary"))
        .replace("__APPEND__", _member(src, "_append"))
    )


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_an_older_page_lands_above_what_was_already_there() -> None:
    """Il difetto da prendere: una pagina vecchia incollata in fondo."""
    _run_js("""
      const chat = makeChat();
      chat._appendUser('di oggi', null, []);
      chat._appendAssistant('risposta di oggi', []);
      chat.prependTurns([user('di ieri', 't1'), said('risposta di ieri', 't1')]);
      assert.deepEqual(readThread(chat), [
        'tu: di ieri',
        'jenny: risposta di ieri',
        'tu: di oggi',
        'jenny: risposta di oggi',
      ]);
    """)


def test_the_page_keeps_its_own_order() -> None:
    """Entrando una alla volta in cima, la pagina si rovescerebbe: non deve."""
    _run_js("""
      const chat = makeChat();
      chat.prependTurns([
        user('primo', 't1'), said('primo detto', 't1'),
        user('secondo', 't2'), said('secondo detto', 't2'),
        user('terzo', 't3'), said('terzo detto', 't3'),
      ]);
      assert.deepEqual(readThread(chat), [
        'tu: primo', 'jenny: primo detto',
        'tu: secondo', 'jenny: secondo detto',
        'tu: terzo', 'jenny: terzo detto',
      ]);
    """)


def test_two_pages_stack_oldest_on_top() -> None:
    """Scorrendo su due volte, la più vecchia deve finire sopra la meno vecchia."""
    _run_js("""
      const chat = makeChat();
      chat._appendUser('oggi', null, []);
      chat.prependTurns([user('ieri', 't2')]);
      chat.prependTurns([user('l\\'altro ieri', 't1')]);
      assert.deepEqual(readThread(chat), ["tu: l'altro ieri", 'tu: ieri', 'tu: oggi']);
    """)


def test_a_page_of_silent_work_draws_nothing_and_breaks_nothing() -> None:
    """Le tracce in casa non sono niente: una pagina di soli strumenti è vuota.

    Non è un caso di scuola — è la ragione per cui il bottone deve poter
    restare: una pagina che non disegna niente lascia il filo dove sta, quindi
    il gesto dello scorrimento continua a non esserci.
    """
    _run_js("""
      const chat = makeChat();
      chat._appendUser('oggi', null, []);
      chat.prependTurns([trace('t1'), trace('t1')]);
      assert.deepEqual(readThread(chat), ['tu: oggi']);
    """)


def test_a_boundary_comes_up_with_its_page() -> None:
    _run_js("""
      const chat = makeChat();
      chat._appendUser('dopo', null, []);
      chat.prependTurns([
        user('prima del reset', 't1'),
        { session_boundary: true },
      ]);
      assert.deepEqual(readThread(chat), ['tu: prima del reset', '---', 'tu: dopo']);
    """)


def test_an_older_page_does_not_wake_the_empty_state() -> None:
    """Lo stato vuoto è «non c'è conversazione», e una pagina vecchia è
    conversazione: se `_append` non lo spegnesse anche dall'alto, la casa
    disegnerebbe la storia con sopra scritto che non c'è niente."""
    _run_js("""
      const chat = makeChat();
      assert.equal(chat._empty, true);
      chat.prependTurns([user('vecchio', 't1')]);
      assert.equal(chat._empty, false);
    """)


# ── Il filo con cui la casa è legata al modulo condiviso ─────────────────────


def test_the_house_measures_and_listens_on_the_same_element() -> None:
    """In officina scroller ed emettitore sono due oggetti diversi (il documento
    e `window`); in casa il filo è entrambi. È la sola asimmetria fra i gusci, e
    scambiarla significa un bottone che non compare mai o non sparisce mai."""
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    ctor = _member(src, "constructor")
    assert "scroller: () => this.el," in ctor
    assert "listenOn: this.el," in ctor
    assert "container: () => this.el," in ctor


def test_the_house_asks_for_a_smaller_page_than_the_workshop() -> None:
    """50 contro 160, e il numero è uno solo: la prima pagina e quelle dopo.

    Il budget conta le ancore (`user`/`stream_end`/`message`), non le righe degli
    strumenti, quindi 50 sono ~25 scambi. A calare è il numero di turni scelti, e
    con loro i record che il gateway rigioca a ogni apertura.
    """
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    assert "const HISTORY_PAGE_SIZE = 50;" in src
    assert "loadThread(key, HISTORY_PAGE_SIZE)" in src
    assert "pageSize: HISTORY_PAGE_SIZE," in src


def test_the_first_page_is_drawn_before_the_button_is_measured() -> None:
    """`ensureReach` chiede se il filo trabocca: prima del disegno la risposta è
    sempre no, e il bottone comparirebbe su ogni apertura."""
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    load = _member(src, "load")
    order = [
        load.index("this._buildTurns(messages)"),
        load.index("this.pager.adopt("),
        load.index("this.pager.ensureReach()"),
    ]
    assert order == sorted(order), order
