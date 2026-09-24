"""La coda di una risposta in casa: il Copia e i secondi, su una riga sola.

Il 21/09/2026, guardando la casa sul telefono: «non c'è separazione tra i vari
messaggi di jenny, sembra un messaggione unico». È vero, e non è un difetto di
spaziatura. In casa Jenny non ha una bolla attorno al testo — è una scelta, sta
scritta in `casa-style.css`: «quello che risponde Jenny non è in una scatola,
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
CASA_CHAT_JS = ASSETS / "casa-chat.js"
CASA_CSS = ASSETS / "casa-style.css"


pytestmark = requires_node

_METODI = (
    "_codaDi",
    "_registra",
    "_testoDi",
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
    _sorgente: new WeakMap(),
    _secondi: null,
    turnNode: null,
    blockNode: null,
    buffer: '',
    turnId: null,
    syncEmpty() {},
    _follow() {},
    _appendMedia() {},
    _append(node) { this.el.appendChild(node); return node; },
    __METODI__,
  };
}

/* Cosa c'e' nella coda di una bolla, nell'ordine: le classi dei pulsanti e il
   testo dei secondi. `null` se la coda non c'e' proprio. */
function coda(node) {
  const riga = node.children.find((c) => c.className === 'casa-coda');
  if (!riga) return null;
  return riga.children.map((c) => (
    c.className === 'casa-secondi' ? c.textContent : c.className
  ));
}
const ultima = (chat) => chat.el.children[chat.el.children.length - 1];
"""


def _harness() -> str:
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    metodi = ",\n    ".join(member(src, nome) for nome in _METODI)
    return _HARNESS.replace("__METODI__", metodi)


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Quel che si vede in coda ─────────────────────────────────────────────────


def test_a_history_answer_gets_copy_then_the_seconds() -> None:
    """L'ordine è metà del contratto: icona, poi tempo. Come in officina."""
    _run_js("""
      const c = makeChat();
      c._appendAssistant('una risposta', [], false, 4000);
      assert.deepEqual(coda(ultima(c)), ['casa-copia', '4.0s']);
    """)


def test_without_a_measured_turn_only_copy_remains() -> None:
    """Una consegna proattiva non ha un turno dietro: nessuno ha misurato
    niente, e inventare uno zero sarebbe peggio del vuoto."""
    _run_js("""
      const c = makeChat();
      c._appendAssistant('un avviso arrivato da solo', [], false, null);
      assert.deepEqual(coda(ultima(c)), ['casa-copia']);
    """)


def test_a_silent_turn_gets_no_tail() -> None:
    """Un turno in cui Jenny ha solo lavorato non ha testo da copiare — e una
    coda sotto il nulla sarebbe un confine attorno a niente."""
    _run_js("""
      const c = makeChat();
      c._appendAssistant('', [], false, 4000);
      assert.equal(coda(ultima(c)), null);
    """)


def test_a_user_bubble_has_no_tail() -> None:
    """La coda è la fine di una **risposta**. Quel che hai scritto tu ha già la
    sua bolla con il suo bordo: è separato da sé."""
    _run_js("""
      const c = makeChat();
      const tua = makeNode();
      tua.className = 'casa-msg casa-msg-user';
      const blocco = makeNode();
      blocco.className = 'casa-block';
      blocco.innerText = 'ciao';
      tua.appendChild(blocco);
      c._codaDi(tua, 4000);
      assert.equal(coda(tua), null);
    """)


# ── Quando arriva ────────────────────────────────────────────────────────────


def test_the_live_turn_gets_its_tail_at_turn_end() -> None:
    _run_js("""
      const c = makeChat();
      const bolla = c._ensureTurn();
      c._registra(bolla, 'risposta dal vivo');
      c._turnEnd(21300);
      assert.deepEqual(coda(bolla), ['casa-copia', '21.3s']);
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
      const prima = c._ensureTurn();
      c._registra(prima, 'la prima');
      c._resetTurn();                  // arriva un turno nuovo, niente turn_end
      const seconda = c._ensureTurn();
      c._registra(seconda, 'la seconda');
      c._turnEnd(1500);
      assert.deepEqual(coda(prima), ['casa-copia'], 'la prima è rimasta senza confine');
      assert.deepEqual(coda(seconda), ['casa-copia', '1.5s']);
    """)


def test_the_seconds_do_not_leak_into_the_next_answer() -> None:
    """I secondi di un turno sono di quel turno. Se restassero in mano al
    filo, la risposta dopo mostrerebbe il tempo di quella prima."""
    _run_js("""
      const c = makeChat();
      const prima = c._ensureTurn();
      c._registra(prima, 'la prima');
      c._turnEnd(9000);
      const seconda = c._ensureTurn();
      c._registra(seconda, 'la seconda');
      c._resetTurn();
      assert.deepEqual(coda(seconda), ['casa-copia']);
    """)


def test_the_tail_is_written_once() -> None:
    _run_js("""
      const c = makeChat();
      const bolla = c._ensureTurn();
      c._registra(bolla, 'risposta');
      c._codaDi(bolla, 4000);
      c._codaDi(bolla, 9999);
      const righe = bolla.children.filter((x) => x.className === 'casa-coda');
      assert.equal(righe.length, 1);
      assert.deepEqual(coda(bolla), ['casa-copia', '4.0s']);
    """)


# ── Cosa copia ───────────────────────────────────────────────────────────────


def test_copy_takes_the_markdown_source_not_the_rendering() -> None:
    """Le recinzioni dei blocchi di codice sono esattamente ciò che serve
    quando una risposta si incolla altrove."""
    _run_js("""
      const c = makeChat();
      const bolla = makeNode();
      const blocco = makeNode();
      blocco.className = 'casa-block';
      blocco.innerText = 'Ecco:\\n\\nprint(1)';
      bolla.appendChild(blocco);
      c._registra(bolla, 'Ecco:\\n\\n```python\\nprint(1)\\n```');
      assert.equal(c._testoDi(bolla), 'Ecco:\\n\\n```python\\nprint(1)\\n```');
    """)


def test_inner_text_is_the_net_when_nothing_was_recorded() -> None:
    """Il sorgente non c'è per le bolle disegnate prima che questo esistesse,
    né se un percorso nuovo dimentica di registrarlo: meglio perdere le
    recinzioni che avere un Copia che non copia niente."""
    _run_js("""
      const c = makeChat();
      const bolla = makeNode();
      const blocco = makeNode();
      blocco.className = 'casa-block';
      blocco.innerText = 'una risposta vecchia';
      bolla.appendChild(blocco);
      assert.equal(c._testoDi(bolla), 'una risposta vecchia');
    """)


def test_a_turn_with_several_segments_copies_whole() -> None:
    """Testo → strumento → testo: una bolla, più blocchi, una copia sola."""
    _run_js("""
      const c = makeChat();
      const bolla = makeNode();
      c._registra(bolla, 'primo');
      c._registra(bolla, 'secondo');
      assert.equal(c._testoDi(bolla), 'primo\\n\\nsecondo');
    """)


def test_the_source_is_recorded_wherever_the_text_is_complete() -> None:
    """Tre punti, e sono i tre in cui il testo di un segmento è definitivo:
    `stream_end` (lo stream si chiude), `_message` (arriva già intero) e
    `_appendAssistant` (dalla cronologia). Se ne manca uno, il Copia di quel
    percorso ripiega su `innerText` e perde le recinzioni — in silenzio.
    """
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    for metodo in ("_streamEnd", "_message", "_appendAssistant"):
        assert "_registra(" in member(src, metodo), f"{metodo} non registra il sorgente"


# ── Il contorno ──────────────────────────────────────────────────────────────


def test_the_tail_is_one_row() -> None:
    """Una riga sola, chiesta esplicitamente: `display: flex` sulla coda, non
    due nodi impilati."""
    m = re.search(r"\.casa-coda\s*\{([^}]*)\}", CASA_CSS.read_text(encoding="utf-8"))
    assert m, ".casa-coda non ha stile: sarebbe due righe una sotto l'altra"
    assert "display: flex" in m.group(1)
    assert "align-items: center" in m.group(1)


def test_the_copy_click_is_delegated() -> None:
    """Un ascoltatore per bolla sono centinaia dopo tre pagine di storia; e la
    CSP del guscio è `script-src 'self'`, quindi niente `onclick` nel markup."""
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    assert "closest('.casa-copia')" in src, "nessun aggancio delegato per il Copia"
    # Il markup, non il commento che spiega perche' li' non ci va.
    assert "onclick=" not in src
    casa = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    assert "onclick=" not in casa


def test_the_frame_carries_the_seconds() -> None:
    """`turn_end` porta `latency_ms`, ed è l'unica fonte: senza passarlo, la
    coda esce sempre senza tempo e nessuno se ne accorge."""
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    assert "this._turnEnd(msg.latency_ms)" in src


def test_history_keeps_the_seconds() -> None:
    """La cronologia li ha: se `_buildTurns` li butta, riaprire l'app toglie il
    tempo a tutto quello che è già stato detto."""
    src = CASA_CHAT_JS.read_text(encoding="utf-8")
    assert "msg.latencyMs" in member(src, "_buildTurns"), (
        "_buildTurns scarta i secondi: dopo una ricarica la coda resta muta"
    )
