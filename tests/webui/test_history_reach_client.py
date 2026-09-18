"""Raggiungere la pagina precedente quando la chat non si può scorrere.

Lo scorrimento infinito chiede la pagina più vecchia su un evento `scroll` con
`scrollTop === 0`. Un contenitore che non trabocca non emette nessun evento
`scroll`: la pagina esiste, il client *sa* che esiste (`hasMore` è vero, il
cursore ce l'ha), e non c'è gesto che possa chiederla.

Finché la prima pagina era lunga il caso non si vedeva. Da quando il confine di
`/new` è il pavimento della cronologia visibile è lo **stato normale subito dopo
un reset**: tre righe a schermo e la conversazione di prima irraggiungibile —
cioè la stessa cancellazione apparente che quel disegno esiste per non fare, e
una smentita di quel che la conferma del comando promette («si rilegge scorrendo
in su»).

Il bottone compare solo in quello stato e sparisce da solo quando il gesto torna
possibile: è un rimedio all'assenza dell'evento, non un secondo modo di fare la
stessa cosa.

**Il codice esercitato è quello condiviso** (`shared/history-pager.js`): da
quando i gusci sono due — la casa e l'officina — questa macchina a stati è una
sola, e il test la importa davvero invece di ritagliarne il testo da un guscio.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
PAGER_JS = ASSETS / "shared" / "history-pager.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


_HARNESS = """
import assert from 'node:assert/strict';

let nodes = [];
/* `document` sul globale e non una const locale: il bottone lo crea il modulo,
   che vede i globali e non le variabili di questo file. */
globalThis.document = {
  createElement() {
    const el = { className: '', textContent: '', type: '', disabled: false, handlers: {} };
    el.addEventListener = (type, fn) => { el.handlers[type] = fn; };
    el.remove = () => {
      const i = nodes.indexOf(el);
      if (i !== -1) nodes.splice(i, 1);
    };
    return el;
  },
};
const withClass = (cls) =>
  nodes.filter((n) => String(n.className).split(/\\s+/).includes(cls));

/* Import dinamico e non statico: gli import sono issati, e `globalThis.document`
   deve esistere prima che il modulo giri. */
const { HistoryPager } = await import('__PAGER_URL__');

function makePager({ scrollHeight, clientHeight, hasMore }) {
  nodes = [];
  const state = { loadedMore: 0 };
  /* Lo scroller è il documento, non l'area della chat (v. `_scroller` in
     mobile-chat.js): le misure stanno lì, il DOM resta sull'area. */
  const scroller = { scrollHeight, clientHeight, scrollTop: 0 };
  const pager = new HistoryPager({
    scroller: () => scroller,
    listenOn: { addEventListener() {} },
    container: () => ({
      querySelector(selector) {
        return withClass(selector.replace('.', ''))[0] || null;
      },
    }),
    pageSize: 120,
    begin: () => null,
    prepend() {},
    /* Quel che il guscio fa per mettere un nodo in cima. `insertBefore` *sposta*
       un nodo già attaccato invece di duplicarlo: senza questo distacco il
       doppio non saprebbe dire la differenza fra riancorare e stampare due
       volte. */
    mount(node) {
      const i = nodes.indexOf(node);
      if (i !== -1) nodes.splice(i, 1);
      nodes.unshift(node);
    },
    label: () => 'i18n:chat.loadPrevious',
  });
  pager.hasMore = hasMore;
  pager.scroller = scroller;
  pager.state = state;
  // Il tocco sul bottone chiama `loadMore`: qui interessa che lo chiami, non
  // cosa carichi — la pagina vera ha il suo test in `test_history_pager_client`.
  pager.loadMore = async () => { state.loadedMore++; };
  return pager;
}

const rows = () => withClass('chat-history-more');
"""


def _harness() -> str:
    return _HARNESS.replace("__PAGER_URL__", PAGER_JS.as_uri())


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_the_button_appears_when_there_is_more_but_nothing_to_scroll() -> None:
    """Lo stato subito dopo `/new`: tre righe a schermo, una sessione sopra."""
    _run_js("""
      const pager = makePager({ scrollHeight: 400, clientHeight: 400, hasMore: true });
      pager.ensureReach();
      assert.equal(rows().length, 1, 'senza appiglio la pagina precedente è irraggiungibile');
      assert.equal(rows()[0].textContent, 'i18n:chat.loadPrevious',
                   'la riga non passa da i18n');
    """)


def test_no_button_when_the_chat_can_already_be_scrolled() -> None:
    """Con il gesto disponibile il bottone sarebbe un secondo modo di fare lo stesso."""
    _run_js("""
      const pager = makePager({ scrollHeight: 2000, clientHeight: 400, hasMore: true });
      pager.ensureReach();
      assert.equal(rows().length, 0);
    """)


def test_no_button_when_there_is_nothing_before() -> None:
    _run_js("""
      const pager = makePager({ scrollHeight: 400, clientHeight: 400, hasMore: false });
      pager.ensureReach();
      assert.equal(rows().length, 0);
    """)


def test_it_disappears_once_the_chat_has_grown() -> None:
    """Sparisce da sé: è il rimedio a un'assenza, e l'assenza è finita."""
    _run_js("""
      const pager = makePager({ scrollHeight: 400, clientHeight: 400, hasMore: true });
      pager.ensureReach();
      assert.equal(rows().length, 1);
      pager.scroller.scrollHeight = 3000;
      pager.ensureReach();
      assert.equal(rows().length, 0, 'il bottone è rimasto dopo che scorrere è tornato possibile');
    """)


def test_it_is_not_stacked_twice() -> None:
    """`ensureReach` gira dopo ogni pagina: due righe identiche sono un difetto."""
    _run_js("""
      const pager = makePager({ scrollHeight: 400, clientHeight: 400, hasMore: true });
      pager.ensureReach();
      pager.ensureReach();
      pager.ensureReach();
      assert.equal(rows().length, 1);
    """)


def test_tapping_it_asks_for_the_older_page() -> None:
    _run_js("""
      const pager = makePager({ scrollHeight: 400, clientHeight: 400, hasMore: true });
      pager.ensureReach();
      const btn = rows()[0];
      assert.ok(btn.handlers.click, 'il bottone non ascolta il tocco');
      await btn.handlers.click();
      assert.equal(pager.state.loadedMore, 1);
      assert.equal(btn.disabled, false, 'resta disabilitato dopo un giro finito');
    """)


def test_it_stays_on_top_of_the_page_it_just_loaded() -> None:
    """Il difetto visto sul telefono: il bottone finiva **sotto** la pagina caricata.

    `loadMore` incolla la pagina in cima a tutto quel che c'è — bottone
    compreso. Se la pagina è corta (due `/new` di fila: un separatore e basta)
    la chat non trabocca ancora, quindi il bottone resta, e resta **in mezzo**:
    fra la conversazione appena tirata su e quella corrente, dicendo «mostra la
    conversazione precedente» mentre quella precedente è già stampata sopra di
    lui. Indica la direzione sbagliata, ed è l'unica cosa a schermo che dica
    dove si va.
    """
    _run_js("""
      const pager = makePager({ scrollHeight: 400, clientHeight: 400, hasMore: true });
      pager.ensureReach();
      const btn = rows()[0];
      // La pagina precedente entra in cima, come fa il `prepend` del guscio.
      nodes.unshift({ className: 'chat-session-boundary' });
      pager.ensureReach();
      assert.equal(rows().length, 1, "il bottone è stato duplicato invece che spostato");
      assert.equal(nodes[0], btn, "il bottone è rimasto sotto la pagina che ha caricato");
    """)


def test_both_shells_ask_for_the_same_words() -> None:
    """`label` è un appiglio, quindi la chiave la nomina ogni guscio per conto suo.

    È il prezzo di non importare `i18n` nel modulo (che al caricamento legge
    `localStorage` e lo renderebbe inesercitabile fuori da un browser), e questo
    è ciò che impedisce alle due di divergere: due bottoni con due parole
    diverse per la stessa cosa.
    """
    key = "i18n.t('chat.loadPrevious')"
    for shell in ("mobile-chat.js", "casa-chat.js"):
        src = (ASSETS / shell).read_text(encoding="utf-8")
        assert key in src, f"{shell} non nomina {key}"
