"""Il margine che i messaggi lasciano a Jenny, eseguito davvero sotto node.

`shared/jenny-gap.js` esiste per una misura: i messaggi avevano
`max-width: 88%`, e quel tetto serviva a non finire dietro la mascotte. Solo
che lei sta **in un angolo** — 87,6 px CSS in fondo a destra sul Titan 2 — e il
tetto lo pagavano *tutti* i messaggi, anche quelli in cima dove non c'e'
nessuno: **82,6 px CSS su ogni riga, il 14% dello schermo** (misurato il
20/09/2026, viewport 574,4 px CSS a DPR 2,500).

La parte che conta e' **geometrica e pura**, e sta qui sotto: dove comincia la
figura dentro il suo quadrato, e quali messaggi la toccano. Il resto —
leggere i rettangoli, mettere una classe — e' DOM e non si prova qui.

**Il numero da non confondere**, ed e' un errore gia' fatto una volta in questo
progetto (v. il commento su `.jenny-duo`): il personaggio occupa il **45% in
larghezza** e il **73% in altezza** del canvas quadrato. Scansare il *quadrato*
invece della *figura* vorrebbe dire lasciare 33 px di buco dove non c'e'
nessuno — cioe' rifare, piu' piccolo, il difetto che si stava correggendo.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
GAP_JS = ASSETS / "shared" / "jenny-gap.js"
MASCOT_JS = ASSETS / "shared" / "mascot.js"


pytestmark = requires_node


def _run_js(script: str) -> str:
    """`jenny-gap.js` importa da `mascot.js`, che al caricamento tocca
    `localStorage`: sotto node non esiste. Si stura con un finto prima
    dell'import, che e' meno invasivo che spezzare il modulo in due."""
    source = (
        "globalThis.localStorage = { getItem: () => null, setItem: () => {} };\n"
        "globalThis.document = { documentElement: { style: { setProperty: () => {} } } };\n"
        + GAP_JS.read_text(encoding="utf-8").replace(
            "import { ART_HEIGHT_RATIO } from './mascot.js';",
            "const ART_HEIGHT_RATIO = 0.73;",
        )
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    return run_js(source)


def test_the_ratios_still_say_what_this_module_assumes() -> None:
    """Il 45% non e' scritto qui per caso: viene da `mascot.js`.

    `SIDE_MARGIN` lo ricalcola da quel numero. Se un giorno l'arte
    cambiasse e il rapporto con lei, questo banco lo dice invece di lasciare il
    modulo a scansare il posto sbagliato in silenzio.
    """
    mascot = MASCOT_JS.read_text(encoding="utf-8")
    assert "export const ART_HEIGHT_RATIO = 0.73;" in mascot, (
        "il rapporto in altezza e' cambiato: rivedere la banda di jenny-gap.js"
    )
    assert "il 45% centrale del canvas quadrato" in mascot, (
        "il rapporto in larghezza e' cambiato: rivedere SIDE_MARGIN"
    )
    gap = GAP_JS.read_text(encoding="utf-8")
    assert "export const SIDE_MARGIN = (1 - 0.45) / 2;" in gap


def test_the_figure_is_not_the_square_it_sits_in() -> None:
    """**La casella che vale il file.**

    Col quadrato di serie (120 px, ancorata «fuori» su un viewport da 574,4) i
    due bordi non coincidono: il riquadro comincia a 484,4, la figura a 517,4.
    Trentatre pixel di differenza — scansare il riquadro li regalerebbe al
    nulla.
    """
    out = _run_js("""
// --jenny-size 120, OUT_RATIO 0.25, viewport 574.4:
// il quadrato sborda di 30 a destra, quindi left = 574.4 + 30 - 120.
const side = 120;
const square = { left: 484.4, right: 604.4, top: 100, bottom: 220 };
const f = figureOf(square, side);
assert.ok(Math.abs(f.left - 517.4) < 0.01, 'left = ' + f.left);
// In altezza: i piedi appoggiano sul fondo del quadrato meno i margini, e la
// figura e' alta il 73% -> il suo bordo alto sta 87,6 sopra il fondo.
assert.ok(Math.abs(f.top - (220 - 87.6)) < 0.01, 'top = ' + f.top);
console.log('ok');
""")
    assert "ok" in out


def test_only_the_messages_in_her_corner_are_marked() -> None:
    """Servono **tutti e due** gli assi.

    Un messaggio alto che le passa sopra non va scansato, e nemmeno uno che sta
    alla sua altezza ma finisce tutto a sinistra. Con un asse solo si
    rimetterebbe il tetto di prima — largo su tutta la colonna, o alto su tutta
    la pagina.
    """
    out = _run_js("""
const figure = { left: 517.4, top: 132.4 };
const cases = [
  // [right, bottom, atteso, perche]
  [556, 220, true,  "in basso e a destra: e il suo angolo"],
  [556, 120, false, "largo ma sopra di lei"],
  [500, 220, false, "in basso ma si ferma prima"],
  [500, 120, false, "nessuno dei due assi"],
  [517.4, 220, false, "tocca il bordo esatto: non si sovrappone"],
];
for (const [right, bottom, expected, why] of cases) {
  const actual = needsDodge({ right, bottom }, figure);
  assert.equal(actual, expected, `${why}: atteso ${expected}, avuto ${actual}`);
}
console.log('ok');
""")
    assert "ok" in out


def test_the_margin_is_the_distance_to_her_and_never_negative() -> None:
    """Col telefono vero fa 39 px, non gli 82,6 che il tetto buttava via.

    E se lei fosse tutta fuori dallo schermo (messa via sul bordo, o taglia
    minuscola) il margine e' zero: un numero negativo entrerebbe nel CSS come
    `padding-right: -39px`, che il browser ignora — un difetto che non si
    vedrebbe finche' qualcuno non misura.
    """
    out = _run_js("""
// destra del contenuto del filo = 574.4 - 18 di padding
assert.equal(marginFrom({ left: 517.4 }, 556.4), 39);
// tutta fuori: niente margine, e mai un numero negativo
assert.equal(marginFrom({ left: 600 }, 556.4), 0);
console.log('ok');
""")
    assert "ok" in out


def test_the_thread_keeps_no_blanket_cap_any_more() -> None:
    """L'altra meta' della correzione: il tetto se n'e' andato davvero.

    Senza questa riga si potrebbe rimettere `max-width` su `.home-msg-jenny` e
    tutti i banchi qui sopra resterebbero verdi — misurerebbero un margine
    giusto sopra una larghezza sbagliata.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    block = css.split(".home-msg-jenny {")[1].split("}")[0]
    # `max-width: 100%` e' la colonna, non un tetto: serve perche' un `<pre>`
    # lungo non allarghi il messaggio oltre il filo (09f43fc). Un tetto e'
    # qualunque valore piu' stretto della colonna.
    caps = [
        v.strip() for v in re.findall(r"max-width\s*:\s*([^;]+);", block)
        if v.strip() != "100%"
    ]
    assert not caps, (
        f"il tetto e' tornato ({caps}): il margine condizionale non serve piu' a niente"
    )
    assert ".home-msg-jenny.is-under-jenny" in css, "manca la regola del margine"


# ── Chi si scansa: tutti e due i lati della conversazione ──────────────────


def _con_dom(script: str) -> str:
    """`refresh()` con un DOM finto, che e' l'unico modo di provare *quali*
    nodi la classe la prendono. La geometria qui sopra si prova pura; questo
    invece e' l'aggancio, ed e' dove stava il difetto."""
    return _run_js(
        """
function node(classes, rect) {
  const set = new Set(classes.split(' '));
  return {
    classes: set,
    classList: {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      contains: (c) => set.has(c),
    },
    getBoundingClientRect: () => rect,
  };
}
globalThis.getComputedStyle = () => ({ paddingRight: '18px' });
globalThis.window = { innerWidth: 574.4 };
const mascot = {
  hidden: false,
  getBoundingClientRect: () => (
    { left: 484.4, right: 604.4, top: 100, bottom: 220, width: 120 }),
};
function threadWith(nodi, shift = 0) {
  return {
    style: { setProperty: (k, v) => { threadWith.written = [k, v]; } },
    getBoundingClientRect: () => ({ left: shift, right: 574.4 + shift }),
    querySelectorAll: (sel) => nodi.filter((n) => sel
      .split(',').map((s) => s.trim())
      .some((s) => n.classes.has(s.slice(1)))),
  };
}
"""
        + script
    )


def test_a_bubble_of_ours_in_her_corner_dodges_too() -> None:
    """Il difetto vero: si scansavano solo le risposte.

    Le bolle di chi scrive sono `align-self: flex-end` — incollate al bordo
    destro, che e' la colonna di Jenny — e la piu' recente e' anche la piu' in
    basso. Cioe' l'unica cosa che lei copriva sempre era **quello che hai
    appena scritto tu**. Con il selettore vecchio (`.home-msg-jenny`) questo
    banco e' rosso.
    """
    out = _con_dom("""
const reply = node('home-msg home-msg-jenny', { right: 540, bottom: 200 });
const mine      = node('home-msg home-msg-user',  { right: 556.4, bottom: 300 });
const old  = node('home-msg home-msg-user',  { right: 556.4, bottom: 90 });
const thread = threadWith([old, reply, mine]);
new JennyGap(thread, mascot).refresh();
assert.ok(mine.classes.has(CLASS), 'la bolla nel suo angolo non si e scansata');
assert.ok(reply.classes.has(CLASS), 'la risposta nel suo angolo non si e scansata');
assert.ok(!old.classes.has(CLASS), 'una bolla sopra di lei non deve scansarsi');
assert.deepEqual(threadWith.written, ['--jenny-gap', '39px']);
console.log('ok');
""")
    assert "ok" in out


def test_a_bubble_that_stops_dodging_gets_cleaned_up() -> None:
    """Scorri, e chi era nel suo angolo non ci sta piu'.

    Senza il giro di `remove` la bolla si porterebbe dietro il margine per
    sempre: uno scalino a destra su un messaggio in mezzo al filo, dove non
    c'e' nessuno da scansare.
    """
    out = _con_dom("""
const mine = node('home-msg home-msg-user is-under-jenny', { right: 556.4, bottom: 90 });
new JennyGap(threadWith([mine]), mascot).refresh();
assert.ok(!mine.classes.has(CLASS), 'il margine e rimasto attaccato');
console.log('ok');
""")
    assert "ok" in out


def test_our_bubble_moves_aside_it_does_not_hollow_out() -> None:
    """Le due forme si scansano in modo diverso, e non e' un dettaglio.

    La risposta di Jenny non ha sfondo: stringerle il testo con `padding` non
    si vede. La bolla ce l'ha — con `padding` si allungherebbe fin sotto di
    lei con dentro il vuoto, cioe' il testo si sposta e la pelle della bolla
    resta coperta lo stesso. Deve muoversi tutta intera: `margin`.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert ".home-msg-user.is-under-jenny" in css, "le bolle non si scansano affatto"
    block = css.split(".home-msg-user.is-under-jenny {")[1].split("}")[0]
    assert "margin-right: var(--jenny-gap" in block, block
    assert "padding-right" not in block, (
        "con padding la bolla si svuota a destra invece di spostarsi"
    )


# ── A pista in movimento ────────────────────────────────────────────────────


def test_a_thread_off_its_page_is_not_measured() -> None:
    """Il difetto del 27/09/2026 sul Titan 2: una lettera per riga.

    Tornando alla chat dalla pagina App, il filo parte una pagina **a destra**
    mentre lei resta ferma sullo schermo. Misurato li', il margine veniva la
    larghezza di una pagina piu' 39, gli ultimi messaggi prendevano la classe,
    e il `padding-right` li stringeva a una colonna larga una lettera. Fuori
    posto non si tocca niente: ne' la classe, ne' `--jenny-gap`.
    """
    out = _con_dom("""
for (const shift of [574.4, 300, -574.4, -2]) {
  threadWith.written = null;
  const reply = node('home-msg home-msg-jenny', { right: 540 + shift, bottom: 200 });
  const kept = node('home-msg home-msg-user is-under-jenny', { right: 556.4 + shift, bottom: 300 });
  new JennyGap(threadWith([reply, kept], shift), mascot).refresh();
  assert.equal(threadWith.written, null, `shift ${shift}: gap written off-page`);
  assert.ok(!reply.classes.has(CLASS), `shift ${shift}: marked off-page`);
  assert.ok(kept.classes.has(CLASS), `shift ${shift}: last good mark dropped`);
}
// Tollerato l'arrotondamento: un pixel non e' «fuori posto».
assert.ok(atRest({ left: -0.5, right: 575 }, 574.4));
assert.ok(!atRest({ left: 0, right: 576 }, 574.4));
console.log('ok');
""")
    assert "ok" in out


def test_the_gap_is_measured_again_once_the_track_stops() -> None:
    """L'altra meta': se a meta' scorrimento non si misura, qualcuno deve
    misurare a scorrimento finito, o un messaggio arrivato mentre eri altrove
    resta dietro di lei fino al primo scorrimento del dito."""
    app = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    block = app.split("  onPageChanged(index, entry, { animated = false } = {}) {")[1].split("\n  }\n")[0]
    assert "gap?.settleAfter(SLIDE_MS" in block, block
    pages = (ASSETS / "home-pages.js").read_text(encoding="utf-8")
    assert "transform ${SLIDE_MS}ms" in pages, "la durata della pista non e' piu' SLIDE_MS"


# ── Lo scorrimento che il ricalcolo non deve rubare ─────────────────────────


def _scrolling_thread(script: str) -> str:
    """Un filo che si comporta come quello vero dove conta: l'ultima risposta
    va a capo di una riga in piu' quando ha il margine, e leggere un
    rettangolo (cioe' forzare il layout) riporta `scrollTop` dentro il massimo
    del momento — che e' quel che fa il browser."""
    return _con_dom(
        """
const LINE = 22;
const last = node('home-msg home-msg-jenny is-under-jenny', { right: 540, bottom: 300 });
const clamp = (t) => { t._top = Math.min(t._top, t.scrollHeight - t.clientHeight); };
const thread = {
  ...threadWith([last]),
  clientHeight: 496,
  _top: 0,
  get scrollHeight() { return 7289 + (last.classes.has(CLASS) ? LINE : 0); },
  get scrollTop() { return this._top; },
  set scrollTop(v) { this._top = Math.max(0, Math.min(v, this.scrollHeight - this.clientHeight)); },
};
const measure = last.getBoundingClientRect;
last.getBoundingClientRect = () => { clamp(thread); return measure(); };
"""
        + script
    )


def test_a_refresh_at_the_bottom_leaves_the_thread_at_the_bottom() -> None:
    """Il difetto del 27/09/2026 sul Titan 2, in un quaderno: la coda
    dell'ultima risposta sotto il composer, e il dito che non ci arrivava.

    Per misurare senza il proprio margine `refresh()` toglie la classe: la
    risposta perde la riga in piu', il contenuto si accorcia, e il browser
    tira `scrollTop` dentro il massimo nuovo. Rimessa la classe il filo
    ricresce, ma lo scorrimento resta una riga sopra il fondo — e siccome il
    ricalcolo parte a ogni scorrimento fermo, ci torna ogni volta.
    """
    out = _scrolling_thread("""
thread.scrollTop = thread.scrollHeight;          // in fondo: 7311 - 496
assert.equal(thread.scrollTop, 6815);
const gap = new JennyGap(thread, mascot);
gap.refresh();
assert.ok(last.classes.has(CLASS), 'la risposta nel suo angolo deve restare scansata');
assert.equal(thread.scrollTop, 6815, 'il ricalcolo ha tirato su il filo di una riga');
console.log('ok');
""")
    assert "ok" in out


def test_a_refresh_mid_thread_does_not_move_what_you_are_reading() -> None:
    out = _scrolling_thread("""
thread.scrollTop = 3000;
new JennyGap(thread, mascot).refresh();
assert.equal(thread.scrollTop, 3000);
console.log('ok');
""")
    assert "ok" in out


def test_the_scroll_a_refresh_makes_does_not_start_another_refresh() -> None:
    """Il ritocco di `refresh()` produce il suo evento di scorrimento. Se
    ripartisse il timer, in fondo al filo si ricalcolerebbe ogni `QUIET_MS`
    per sempre, due layout del filo intero a giro."""
    out = _scrolling_thread("""
thread.scrollTop = thread.scrollHeight;
const gap = new JennyGap(thread, mascot);
gap.refresh();
gap.scrolling();                     // l'evento del ritocco
assert.equal(gap._timer, null, 'il proprio ritocco ha rimesso in moto il ricalcolo');
thread.scrollTop = 6000;             // il dito, invece, si'
gap.scrolling();
assert.notEqual(gap._timer, null, 'uno scorrimento vero deve ricalcolare');
clearTimeout(gap._timer);
console.log('ok');
""")
    assert "ok" in out
