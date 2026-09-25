"""La coda di una risposta in casa: il Copia e i secondi, su una riga sola.

Il 21/09/2026, guardando la casa sul telefono: «non c'è separazione tra i vari
messaggi di jenny, sembra un messaggione unico». È vero, e non è un difetto di
spaziatura. In casa Jenny non ha una bolla attorno al testo — è una scelta, sta
scritta in `home-style.css`: «quello che risponde Jenny non è in una scatola,
è testo sulla pagina, come una lettera». Quattro risposte di fila sono quindi
quattro gruppi di paragrafi separati da 10 px, e l'occhio le legge come una.

La riga in coda è il confine, e lo è con due cose che servono invece che con
una linea che non serve. Qui si misura che ci sia, che ci sia **una volta
sola**, e soprattutto che arrivi anche quando il turno non finisce con un
`turn_end` — che è esattamente il caso di due risposte una dopo l'altra, cioè
quello da cui è partita la segnalazione.

Come negli altri banchi client di questa cartella, i metodi si estraggono dal
sorgente e girano in node su un `this` finto: il DOM qui è ridotto a quel che
il disegno tocca.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
HOME_CHAT_JS = ASSETS / "home-chat.js"
HOME_CSS = ASSETS / "home-style.css"


pytestmark = requires_node

_METHODS = (
    "_tailOf",
    "_register",
    "_textOf",
    "_appendAssistant",
    "_resetTurn",
    "_turnEnd",
    "_ensureTurn",
)


_HARNESS = """
import assert from 'node:assert/strict';

function makeNode() {
  const node = {
    className: '', textContent: '', innerHTML: '', innerText: '',
    type: '', title: '', isConnected: true,
    children: [],
    appendChild(child) { node.children.push(child); return child; },
    insertBefore(child, before) {
      const i = before ? node.children.indexOf(before) : -1;
      if (i === -1) node.children.unshift(child);
      else node.children.splice(i, 0, child);
      return child;
    },
    get firstChild() { return node.children[0] || null; },
    setAttribute(k, v) { node[k] = v; },
    querySelectorAll(sel) {
      const cls = sel.replace('.', '');
      const out = [];
      for (const c of node.children) {
        if (String(c.className).split(' ').includes(cls)) out.push(c);
        out.push(...c.querySelectorAll(sel));
      }
      return out;
    },
    querySelector(sel) { return node.querySelectorAll(sel)[0] || null; },
  };
  return node;
}
globalThis.document = { createElement: makeNode, getElementById: () => null };

const renderMarkdown = (t) => t;
/* Formule e diagrammi: qui si misura la forma delle bolle, non il loro
   contenuto ricco. Il finto e' dichiarato invece che implicito perche' un
   finto dimenticato e' come un difetto e' passato inosservato il 21/09/2026
   (v. `test_vendor_contract.py`); che il chiamante esista lo misura
   `test_rich_surfaces_contract.py`. */
const renderRich = () => {};
const i18n = { t: (key) => key };

function makeChat() {
  return {
    el: makeNode(),
    _empty: false,
    _source: new WeakMap(),
    _seconds: null,
    turnNode: null,
    blockNode: null,
    buffer: '',
    turnId: null,
    syncEmpty() {},
    _follow() {},
    _appendMedia() {},
    _append(node) { this.el.appendChild(node); return node; },
    __METHODS__,
  };
}

/* Cosa c'e' nella coda di una bolla, nell'ordine: le classi dei pulsanti e il
   testo dei secondi. `null` se la coda non c'e' proprio. */
function tail(node) {
  const row = node.children.find((c) => c.className === 'home-tail');
  if (!row) return null;
  return row.children.map((c) => (
    c.className === 'home-seconds' ? c.textContent : c.className
  ));
}
const ultima = (chat) => chat.el.children[chat.el.children.length - 1];
"""


def _harness() -> str:
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    methods = ",\n    ".join(member(src, name) for name in _METHODS)
    return _HARNESS.replace("__METHODS__", methods)


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Quel che si vede in coda ─────────────────────────────────────────────────


def test_a_history_answer_gets_copy_then_the_seconds() -> None:
    """L'ordine è metà del contratto: icona, poi tempo. Come in officina."""
    _run_js("""
      const c = makeChat();
      c._appendAssistant('una risposta', [], false, 4000);
      assert.deepEqual(tail(ultima(c)), ['home-copy', '4.0s']);
    """)


def test_without_a_measured_turn_only_copy_remains() -> None:
    """Una consegna proattiva non ha un turno dietro: nessuno ha misurato
    niente, e inventare uno zero sarebbe peggio del vuoto."""
    _run_js("""
      const c = makeChat();
      c._appendAssistant('un avviso arrivato da solo', [], false, null);
      assert.deepEqual(tail(ultima(c)), ['home-copy']);
    """)


def test_a_silent_turn_gets_no_tail() -> None:
    """Un turno in cui Jenny ha solo lavorato non ha testo da copiare — e una
    coda sotto il nulla sarebbe un confine attorno a niente."""
    _run_js("""
      const c = makeChat();
      c._appendAssistant('', [], false, 4000);
      assert.equal(tail(ultima(c)), null);
    """)


def test_a_user_bubble_has_no_tail() -> None:
    """La coda è la fine di una **risposta**. Quel che hai scritto tu ha già la
    sua bolla con il suo bordo: è separato da sé."""
    _run_js("""
      const c = makeChat();
      const tua = makeNode();
      tua.className = 'home-msg home-msg-user';
      const block = makeNode();
      block.className = 'home-block';
      block.innerText = 'ciao';
      tua.appendChild(block);
      c._tailOf(tua, 4000);
      assert.equal(tail(tua), null);
    """)


# ── Quando arriva ────────────────────────────────────────────────────────────


def test_the_live_turn_gets_its_tail_at_turn_end() -> None:
    _run_js("""
      const c = makeChat();
      const bubble = c._ensureTurn();
      c._register(bubble, 'risposta dal vivo');
      c._turnEnd(21300);
      assert.deepEqual(tail(bubble), ['home-copy', '21.3s']);
    """)


def test_an_answer_cut_short_by_the_next_one_still_gets_its_tail() -> None:
    """**Il caso da cui è partita la segnalazione.**

    Due risposte di fila: la prima può non ricevere mai un `turn_end` — un
    frame del turno nuovo la scavalca (`_crossesTurn` chiama `_resetTurn`). Con
    la coda attaccata al solo `turn_end`, proprio le due che si volevano
    separare restavano attaccate.
    """
    _run_js("""
      const c = makeChat();
      const before = c._ensureTurn();
      c._register(before, 'la prima');
      c._resetTurn();                  // arriva un turno nuovo, niente turn_end
      const second = c._ensureTurn();
      c._register(second, 'la seconda');
      c._turnEnd(1500);
      assert.deepEqual(tail(before), ['home-copy'], 'la prima è rimasta senza confine');
      assert.deepEqual(tail(second), ['home-copy', '1.5s']);
    """)


def test_the_seconds_do_not_leak_into_the_next_answer() -> None:
    """I secondi di un turno sono di quel turno. Se restassero in mano al
    filo, la risposta dopo mostrerebbe il tempo di quella prima."""
    _run_js("""
      const c = makeChat();
      const before = c._ensureTurn();
      c._register(before, 'la prima');
      c._turnEnd(9000);
      const second = c._ensureTurn();
      c._register(second, 'la seconda');
      c._resetTurn();
      assert.deepEqual(tail(second), ['home-copy']);
    """)


def test_the_tail_is_written_once() -> None:
    _run_js("""
      const c = makeChat();
      const bubble = c._ensureTurn();
      c._register(bubble, 'risposta');
      c._tailOf(bubble, 4000);
      c._tailOf(bubble, 9999);
      const rows = bubble.children.filter((x) => x.className === 'home-tail');
      assert.equal(rows.length, 1);
      assert.deepEqual(tail(bubble), ['home-copy', '4.0s']);
    """)


# ── Cosa copia ───────────────────────────────────────────────────────────────


def test_copy_takes_the_markdown_source_not_the_rendering() -> None:
    """Le recinzioni dei blocchi di codice sono esattamente ciò che serve
    quando una risposta si incolla altrove."""
    _run_js("""
      const c = makeChat();
      const bubble = makeNode();
      const block = makeNode();
      block.className = 'home-block';
      block.innerText = 'Ecco:\\n\\nprint(1)';
      bubble.appendChild(block);
      c._register(bubble, 'Ecco:\\n\\n```python\\nprint(1)\\n```');
      assert.equal(c._textOf(bubble), 'Ecco:\\n\\n```python\\nprint(1)\\n```');
    """)


def test_inner_text_is_the_net_when_nothing_was_recorded() -> None:
    """Il sorgente non c'è per le bolle disegnate prima che questo esistesse,
    né se un percorso nuovo dimentica di registrarlo: meglio perdere le
    recinzioni che avere un Copia che non copia niente."""
    _run_js("""
      const c = makeChat();
      const bubble = makeNode();
      const block = makeNode();
      block.className = 'home-block';
      block.innerText = 'una risposta vecchia';
      bubble.appendChild(block);
      assert.equal(c._textOf(bubble), 'una risposta vecchia');
    """)


def test_a_turn_with_several_segments_copies_whole() -> None:
    """Testo → strumento → testo: una bolla, più blocchi, una copia sola."""
    _run_js("""
      const c = makeChat();
      const bubble = makeNode();
      c._register(bubble, 'primo');
      c._register(bubble, 'secondo');
      assert.equal(c._textOf(bubble), 'primo\\n\\nsecondo');
    """)


def test_the_source_is_recorded_wherever_the_text_is_complete() -> None:
    """Tre punti, e sono i tre in cui il testo di un segmento è definitivo:
    `stream_end` (lo stream si chiude), `_message` (arriva già intero) e
    `_appendAssistant` (dalla cronologia). Se ne manca uno, il Copia di quel
    percorso ripiega su `innerText` e perde le recinzioni — in silenzio.
    """
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    for method in ("_streamEnd", "_message", "_appendAssistant"):
        assert "_register(" in member(src, method), f"{method} non registra il sorgente"


# ── Il contorno ──────────────────────────────────────────────────────────────


def test_the_tail_is_one_row() -> None:
    """Una riga sola, chiesta esplicitamente: `display: flex` sulla coda, non
    due nodi impilati."""
    m = re.search(r"\.home-tail\s*\{([^}]*)\}", HOME_CSS.read_text(encoding="utf-8"))
    assert m, ".home-tail non ha stile: sarebbe due righe una sotto l'altra"
    assert "display: flex" in m.group(1)
    assert "align-items: center" in m.group(1)


def test_the_copy_click_is_delegated() -> None:
    """Un ascoltatore per bolla sono centinaia dopo tre pagine di storia; e la
    CSP del guscio è `script-src 'self'`, quindi niente `onclick` nel markup."""
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    assert "closest('.home-copy')" in src, "nessun aggancio delegato per il Copia"
    # Il markup, non il commento che spiega perche' li' non ci va.
    assert "onclick=" not in src
    home = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    assert "onclick=" not in home


def test_the_frame_carries_the_seconds() -> None:
    """`turn_end` porta `latency_ms`, ed è l'unica fonte: senza passarlo, la
    coda esce sempre senza tempo e nessuno se ne accorge."""
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    assert "this._turnEnd(msg.latency_ms)" in src


def test_history_keeps_the_seconds() -> None:
    """La cronologia li ha: se `_buildTurns` li butta, riaprire l'app toglie il
    tempo a tutto quello che è già stato detto."""
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    assert "msg.latencyMs" in member(src, "_buildTurns"), (
        "_buildTurns scarta i secondi: dopo una ricarica la coda resta muta"
    )
