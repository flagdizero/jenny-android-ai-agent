"""Il carosello dell'officina: da ogni linguetta, nei due versi.

Il 22/09/2026 lo scorrimento orizzontale era **morto su tre linguette su
quattro**. Il motivo stava in una riga che non sembrava niente:

    view = document.getElementById(`view-${this.currentMode}`);
    if (!view) return;

`brain`, `hands` e `memory` non hanno una vista propria — sono lo stesso
`view-settings` — quindi la ricerca tornava `null` e il gesto moriva alla prima
riga, in silenzio. Da fuori sembrava che il carosello non ci fosse.

E ai due capi mancava un verso: da Console non si andava a sinistra, da Memoria
non si andava a destra. Adesso il giro si chiude.

**Perche' in node su un DOM finto.** Il difetto non era nella fisica del gesto
(quella funzionava, ed e' pure tarata bene) ma in **chi sono i vicini** e **su
quale elemento** si lavora. Sono due domande a cui si risponde senza un browser,
e senza browser si possono fare tutte e sedici le coppie invece di quelle che un
dito ha voglia di provare.

Quel che questo banco **non** prova: che il dito ci arrivi davvero. Gli eventi
qui sono sintetici e scavalcano il hit-testing — v.
`driving-touch-gestures-over-adb` in memoria. Per quello c'e' la prova sul
telefono.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "mobile-app.js"
SETTINGS_JS = ASSETS / "mobile-settings.js"
GESTO_JS = ASSETS / "shared" / "horizontal-swipe.js"


pytestmark = requires_node

MODI = ["chat", "brain", "hands", "memory"]


def _corpo(src: str, start: int) -> str:
    """Dalla graffa aperta alla sua chiusa, saltando commenti e stringhe.

    I commenti vanno saltati sul serio: questo repo li scrive in italiano, e un
    `dell'` dentro `//` fa credere a un contatore ingenuo che sia cominciata una
    stringa — da li' in poi le graffe non si contano piu'. Costato una prima
    stesura di questo banco, rossa su codice sano (22/09/2026).
    """
    i = src.index("{", start)
    profondita, j, stringa = 0, i, None
    while j < len(src):
        c = src[j]
        due = src[j : j + 2]
        if stringa:
            if c == "\\":
                j += 2
                continue
            if c == stringa:
                stringa = None
        elif due == "//":
            j = src.index("\n", j)
            continue
        elif due == "/*":
            j = src.index("*/", j) + 2
            continue
        elif c in "\"'`":
            stringa = c
        elif c == "{":
            profondita += 1
        elif c == "}":
            profondita -= 1
            if profondita == 0:
                return src[i : j + 1]
        j += 1
    raise AssertionError("graffe sbilanciate")


def _metodo(source: str, name: str) -> str:
    """`name(parameters) { corpo }`, pronto da incollare in un oggetto letterale."""
    m = re.search(rf"\n  {re.escape(name)}\(", source)
    assert m, f"metodo {name} non trovato"
    apertura = source.index("{", m.end())
    return source[m.start() + 1 : apertura] + _corpo(source, m.end())


def _costante(source: str, name: str) -> str:
    """`export const NOME = ...;` su una riga, pronta da incollare."""
    m = re.search(rf"^export const {re.escape(name)} = .*$", source, re.M)
    assert m, f"const {name} non trovata"
    return m.group(0).replace("export ", "")


def _funzione(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


_HARNESS = """
import assert from 'node:assert/strict';

/* ── Il DOM finto ──────────────────────────────────────────────────────── */

const elementi = new Map();

function creaEl(id) {
  const ascolto = {};
  const el = {
    id,
    style: {},
    dataset: {},
    offsetWidth: 0,
    scrollWidth: 0,
    clientWidth: 400,
    scrollLeft: 0,
    parentElement: null,
    classList: { contains: () => false, toggle() {}, add() {}, remove() {} },
    addEventListener(tipo, fn) { ascolto[tipo] = fn; },
    setAttribute() {},
    removeAttribute() {},
    removeEventListener() {},
    appendChild() {},
    ascolto,
  };
  if (id) elementi.set(id, el);
  return el;
}

const main = creaEl('main');
const contenuto = creaEl('contenuto');
contenuto.parentElement = main;

/* Le viste che l'officina ha davvero: NON esistono view-cervello/mani/memoria.
   E' esattamente questo che faceva morire il gesto. */
for (const id of ['view-chat', 'view-settings', 'view-workspace', 'view-onboarding']) creaEl(id);

const VOCI_DOCK = __MODI__.map((m) => {
  const el = creaEl(null);
  el.dataset.mode = m;
  el.style = {};
  return el;
});

const radice = creaEl('html');

globalThis.document = {
  documentElement: radice,
  getElementById: (id) => elementi.get(id) || null,
  querySelector: (sel) => (sel === '.main' ? main : null),
  querySelectorAll: (sel) => (sel.includes('dock-item') ? VOCI_DOCK : []),
  createElement: () => creaEl(null),
  body: creaEl('body'),
};
globalThis.window = { innerWidth: 400 };
globalThis.setTimeout = globalThis.setTimeout;
globalThis.getComputedStyle = () => ({ overflowX: 'visible' });
function hasSelection() { return false; }

/* ── I pezzi veri, ritagliati dal sorgente ─────────────────────────────── */

__VISTA_DI__
__ELEMENTO_VISTA__

/* Il riconoscimento del gesto, dal modulo condiviso. */
__AXIS_THRESHOLD__
__SOGLIA_CONFERMA__
__VELOCITA__
__ELASTICO__
__HSCROLL__
__COMPONENTE__
__OSSERVA__

const modiVisti = [];
const animati = [];

const app = {
  __VISIBLE_MODES__,
  __SETUP_SWIPE__,
  __ANIMATE__,

  currentMode: 'chat',
  _firstRun: false,
  drawer: { activeDrawer: null },
  switchMode(m) { modiVisti.push(m); this.currentMode = m; },
};

const _animaVero = app._animateSlideIn.bind(app);
app._animateSlideIn = (vista, prev) => { animati.push(vista); _animaVero(vista, prev); };

app.setupSwipeNav();

/* ── Guidare il dito ───────────────────────────────────────────────────── */

const DESTRA = +1;   // dito verso destra → il vicino di sinistra (prev)
const SINISTRA = -1; // dito verso sinistra → il vicino di destra (next)

/* **Un dito porta due righelli**, come quello vero: `client` e' relativo alla
   finestra di chi ascolta, `screen` allo schermo. Qui sono sfalsati di una
   costante apposta — se qualcuno tornasse a misurare col primo, o peggio
   mescolasse i due, lo scarto salterebbe fuori invece di nascondersi. Il
   modulo condiviso legge **screen**, perche' dentro una Jenny App la finestra
   e' la cornice che la pista sta trascinando (v. la sua testata). */
const SFALSO_X = 1000;
const SFALSO_Y = 500;
function dito(x, y) {
  return { clientX: x, clientY: y, screenX: x + SFALSO_X, screenY: y + SFALSO_Y };
}

/** Un gesto completo. Torna il modo su cui si e' atterrati, o null. */
function scorri(da, direction, { corto = false } = {}) {
  app.currentMode = da;
  modiVisti.length = 0;
  animati.length = 0;
  const x0 = 200;
  // soglia = max(60, 400*0.22) = 88; corto resta sotto, lungo la supera
  const dx = direction * (corto ? 20 : 200);
  main.ascolto.touchstart({ touches: [dito(x0, 100)], target: contenuto });
  main.ascolto.touchmove({
    touches: [dito(x0 + dx, 100)],
    preventDefault() {},
  });
  main.ascolto.touchend({ changedTouches: [dito(x0 + dx, 100)] });
  return modiVisti.length ? modiVisti[modiVisti.length - 1] : null;
}
"""


def _harness() -> str:
    app = APP_JS.read_text(encoding="utf-8")
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    swipe = GESTO_JS.read_text(encoding="utf-8")
    vista_di = re.search(r"^export const VISTA_DI = .*$", settings, re.M)
    assert vista_di, "VISTA_DI non trovata"
    return (
        _HARNESS.replace("__MODI__", json.dumps(MODI))
        .replace("__VISTA_DI__", vista_di.group(0).replace("export ", ""))
        .replace("__ELEMENTO_VISTA__", _funzione(settings, "elementoVista"))
        .replace("__VISIBLE_MODES__", _metodo(app, "_visibleModes"))
        .replace("__AXIS_THRESHOLD__", _costante(swipe, "AXIS_THRESHOLD"))
        .replace("__VELOCITA__", _costante(swipe, "CONFIRM_SPEED"))
        .replace("__SOGLIA_CONFERMA__", _funzione(swipe, "confirmThreshold"))
        .replace("__ELASTICO__", _funzione(swipe, "elastic"))
        .replace("__HSCROLL__", _funzione(swipe, "insideHorizontalScrollable"))
        .replace(
            "__COMPONENTE__",
            "\n".join(
                _funzione(swipe, name)
                for name in (
                    "componentSwipe", "claimsHorizontal", "isCommand", "selectedText",
                    "lockVertical",
                )
            ),
        )
        .replace("__OSSERVA__", _funzione(swipe, "watchHorizontalSwipe"))
        .replace("__SETUP_SWIPE__", _metodo(app, "setupSwipeNav"))
        .replace("__ANIMATE__", _metodo(app, "_animateSlideIn"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Il difetto che ha aperto il giro ────────────────────────────────────────


def test_a_drawer_is_swipeable_at_all() -> None:
    """Da `brain` il gesto parte. Prima usciva alla prima riga.

    E' *il* difetto: `view-cervello` non esiste, la ricerca grezza tornava
    `null`, e `if (!view) return` chiudeva la faccenda senza dire niente.
    """
    _run_js("""
      assert.equal(scorri('brain', SINISTRA), 'hands');
      assert.equal(scorri('brain', DESTRA), 'chat');
    """)


def test_every_tab_moves_in_both_directions() -> None:
    """Tutte e otto le mosse: quattro linguette per due versi."""
    _run_js("""
      const atteso = {
        chat:     { destra: 'memory',  sinistra: 'brain' },
        brain: { destra: 'chat',     sinistra: 'hands' },
        hands:     { destra: 'brain', sinistra: 'memory' },
        memory:  { destra: 'hands',     sinistra: 'chat' },
      };
      for (const [da, versi] of Object.entries(atteso)) {
        assert.equal(scorri(da, DESTRA), versi.destra, `${da} verso destra`);
        assert.equal(scorri(da, SINISTRA), versi.sinistra, `${da} verso sinistra`);
      }
    """)


# ── I capi che si richiudono ────────────────────────────────────────────────


def test_the_ends_wrap_around() -> None:
    """Console a destra torna a Memoria, Memoria a sinistra torna a Console.

    Erano gli unici due punti in cui il gesto non faceva niente pur essendo
    vivo, ed e' quello che rendeva falsa la frase «di lato si cambia
    linguetta».
    """
    _run_js("""
      assert.equal(scorri('chat', DESTRA), 'memory', 'dal primo indietro si arriva in fondo');
      assert.equal(scorri('memory', SINISTRA), 'chat', 'dall ultimo avanti si torna in testa');
    """)


def test_one_tab_alone_has_nowhere_to_go() -> None:
    """Con una voce sola non c'e' nessun giro: il modulo non deve tornare su se'.

    Senza la guardia su `modes.length`, il resto della divisione porterebbe
    `prev` e `next` sulla linguetta stessa, e il gesto «cambierebbe» verso dove
    gia' si e'.
    """
    _run_js("""
      VOCI_DOCK.length = 1;
      assert.equal(scorri('chat', SINISTRA), null);
      assert.equal(scorri('chat', DESTRA), null);
    """)


# ── Fra due cassetti: stessa vista, contenuto diverso ───────────────────────


def test_between_two_drawers_the_animation_gets_a_real_element() -> None:
    """`brain → hands` e' **lo stesso nodo** che si ridisegna.

    Il gesto non scambia due viste: ne ridisegna una. Quel che conta e' che
    l'animazione d'arrivo riceva un elemento vero — se ricevesse `null`
    (com'era prima) il cambio non si vedrebbe affatto, e il difetto non
    romperebbe niente: semplicemente non succederebbe niente.
    """
    _run_js("""
      assert.equal(scorri('brain', SINISTRA), 'hands');
      assert.equal(animati.length, 1);
      assert.ok(animati[0], 'l animazione ha ricevuto null');
      assert.equal(animati[0].id, 'view-settings');
    """)


def test_all_four_tabs_animate_a_real_element() -> None:
    """E vale per tutte, non solo per quelle che hanno una vista propria."""
    _run_js("""
      for (const da of __MODI__) {
        for (const direction of [DESTRA, SINISTRA]) {
          scorri(da, direction);
          assert.equal(animati.length, 1, `${da}: nessuna animazione`);
          assert.ok(animati[0], `${da}: animazione su null`);
        }
      }
    """.replace("__MODI__", json.dumps(MODI)))


# ── Quel che il gesto non deve fare ─────────────────────────────────────────


def test_a_short_drag_springs_back() -> None:
    """Sotto soglia non si cambia linguetta: si torna al suo posto."""
    _run_js("""
      assert.equal(scorri('brain', SINISTRA, { corto: true }), null);
      assert.equal(animati.length, 0);
    """)


def test_an_open_drawer_owns_the_gesture() -> None:
    """Col cassetto aperto il carosello non si arma."""
    _run_js("""
      app.drawer.activeDrawer = 'qualcosa';
      assert.equal(scorri('brain', SINISTRA), null);
      app.drawer.activeDrawer = null;
      assert.equal(scorri('brain', SINISTRA), 'hands');
    """)


# ── L'ancoraggio dello scroll, contro cui la scivolata perdeva ──────────────


def test_the_slide_in_switches_scroll_anchoring_off_and_back_on() -> None:
    """Perche' in chat lo scroller e' il documento, e il browser lo «aiuta».

    Chromium tiene ferma la lettura correggendo `scrollTop` quando qualcosa
    sopra cambia. Una vista alta quanto la pagina che entra con un `translateX`
    gli sembra quel caso, e la sua correzione **disfa** il «vai in fondo» che la
    chat ha appena chiesto. Misurato fuori dall'app: 940px di scarto con
    l'ancoraggio acceso, zero con `overflow-anchor: none`.

    Spento **solo** per la durata della scivolata: fuori di li' serve, ed e'
    quel che tiene il segno quando la cronologia cresce sopra.
    """
    _run_js("""
      radice.style.overflowAnchor = '';
      scorri('memory', SINISTRA);
      assert.equal(radice.style.overflowAnchor, 'none', 'non spento durante la scivolata');
      // fine animazione
      const fine = animati[0].ascolto.transitionend;
      assert.ok(fine, 'nessun ascoltatore di fine transizione');
      fine();
      assert.equal(radice.style.overflowAnchor, '', 'non rimesso a fine corsa');
    """)


def test_the_anchor_comes_back_even_if_the_transition_never_ends() -> None:
    """La rete di sicurezza.

    `transitionend` non arriva se la vista sparisce a meta' corsa — un secondo
    gesto, il tasto Indietro. Senza il timeout l'ancoraggio resterebbe spento
    per tutto il resto della sessione, e il difetto tornerebbe al contrario:
    nessuno terrebbe piu' il segno quando la cronologia cresce.
    """
    _run_js("""
      radice.style.overflowAnchor = '';
      scorri('memory', SINISTRA);
      assert.equal(radice.style.overflowAnchor, 'none');
      await new Promise(r => setTimeout(r, 500));   // nessun transitionend, solo la rete
      assert.equal(radice.style.overflowAnchor, '', 'la rete di sicurezza non ha rimesso l ancoraggio');
    """)
