"""Di lato e' scorrimento, un tocco e' un tocco — tranne sopra chi si trascina.

La regola e' dell'utente (23/09/2026): «se swipe orizzontale e' swipe, se click
e' click. Se pero' ci sono eventi di swipe sulla pagina vince lo swipe sul
componente e non il cambio di scheda». Prima c'era una sola eccezione, lo
scorrevole nativo: tutto quel che una Jenny App faceva col suo codice non si
vedeva, e il dito muoveva l'app **e** la pagina.

Il difetto opposto l'ha trovato l'utente prima che lo scrivessi: «se il bottone
mi ruba lo swipe non potro' mai swipare?». Per questo il banco prova anche che i
bottoni — pure quelli con `touch-action: none`, come i − e + di Life Counter —
**non** si tengono il gesto.

Il modulo vero, in node, su un DOM finto: quel che si prova e' la decisione, non
che il dito ci arrivi (quello l'ha detto il telefono).
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
MODULO = ROOT / "jenny" / "templates" / "ui" / "assets" / "shared" / "horizontal-swipe.js"

pytestmark = requires_node

_HARNESS = """
import assert from 'node:assert/strict';

/* ── Il DOM finto ──────────────────────────────────────────────────────── */

function el(tag = 'DIV', { touchAction = 'auto', overflowX = 'visible', overflowY = 'visible',
                           role = null, type = null, parent = null } = {}) {
  const e = {
    tagName: tag, type, parentElement: parent, touchAction, overflowX, overflowY,
    scrollWidth: 0, clientWidth: 400, scrollLeft: 0, scrollHeight: 0, clientHeight: 400,
    style: {},
    ricevuti: [],
    getAttribute: (n) => (n === 'role' ? role : null),
    dispatchEvent(ev) { this.ricevuti.push(ev); lancia(ev.type, ev); return true; },
  };
  return e;
}

globalThis.document = { body: el('BODY') };
globalThis.getComputedStyle = (e) => ({
  overflowX: e.overflowX, overflowY: e.overflowY, touchAction: e.touchAction,
});
let selezione = '';
globalThis.getSelection = () => ({ isCollapsed: !selezione, toString: () => selezione });

class FintoEvento {
  constructor(type, init = {}) { this.type = type; Object.assign(this, init); }
}
globalThis.PointerEvent = class extends FintoEvento {};
globalThis.TouchEvent = class extends FintoEvento {};

/* La finestra: ascolti in discesa e in risalita, nell'ordine del browser. */
const ascolti = [];
globalThis.window = {
  innerWidth: 400,
  addEventListener(type, fn, opz = {}) { ascolti.push({ type, fn, cattura: !!opz.capture }); },
  removeEventListener(type, fn) {
    const i = ascolti.findIndex((a) => a.type === type && a.fn === fn);
    if (i >= 0) ascolti.splice(i, 1);
  },
};

function lancia(type, ev) {
  let fermato = false;
  ev.type ||= type;
  ev.stopImmediatePropagation = () => { fermato = true; };
  ev.preventDefault ||= () => { ev.defaultPrevented = true; };
  for (const a of ascolti.filter((x) => x.type === type && x.cattura)) {
    a.fn(ev); if (fermato) return 'fermato';
  }
  ev.primaDiNoi?.(ev);  // l'app, che ascolta sotto di noi
  for (const a of ascolti.filter((x) => x.type === type && !x.cattura)) {
    a.fn(ev); if (fermato) return 'fermato';
  }
  return 'passato';
}

const M = await import(process.env.MODULO);

/* ── Guidare il dito ───────────────────────────────────────────────────── */

const dito = (x, y = 100) => ({ screenX: x, screenY: y });

function osserva(opz = {}) {
  const visto = [];
  const detach = M.watchHorizontalSwipe(window, {
    onHorizontal: () => visto.push('start'),
    onDrag: (dx) => visto.push(['move', dx]),
    onEnd: ({ direction }) => visto.push(['end', direction]),
    onCancel: () => visto.push('cancel'),
    ...opz,
  });
  return { visto, detach };
}

/** Un dito che parte su `target` e va a sinistra di 150px in tre passi. */
function scorri(target, { app = null, end = true } = {}) {
  lancia('pointerdown', { target: target, pointerId: 7, isPrimary: true });
  lancia('touchstart', { target: target, touches: [dito(300)] });
  const esiti = [];
  for (const x of [290, 250, 150]) {
    lancia('pointermove', { target: target, pointerId: 7 });
    esiti.push(lancia('touchmove', { target: target, touches: [dito(x)], primaDiNoi: app }));
  }
  if (end) {
    lancia('pointerup', { target: target, pointerId: 7 });
    lancia('touchend', { target: target, changedTouches: [dito(150)] });
  }
  return esiti;
}

const root = el('HTML');
document.body.parentElement = root;
document.scrollingElement = root;
const inside = (tag, opz = {}) => el(tag, { parent: document.body, ...opz });
"""


def _run_js(script: str) -> None:
    run_js(_HARNESS + "\n" + script, env={"MODULO": MODULO.as_uri(), "PATH": "/usr/bin:/bin"})


def test_a_plain_area_swipes_the_page() -> None:
    _run_js(
        """
const { visto } = osserva();
scorri(inside('DIV'));
assert.equal(visto[0], 'start');
assert.deepEqual(visto.at(-1), ['end', 'next']);
"""
    )


def test_a_button_never_steals_the_swipe() -> None:
    """Life Counter: `touch-action: none` sui suoi − e +, e restano bottoni."""
    _run_js(
        """
const { visto, detach } = osserva();
const meno = inside('BUTTON', { touchAction: 'none' });
const mark = el('SPAN', { parent: meno });
scorri(mark);
assert.equal(visto[0], 'start', 'il bottone si e\\' tenuto lo scorrimento');
detach();

for (const [tag, role] of [['A', null], ['LABEL', null], ['DIV', 'button'],
                           ['DIV', 'switch'], ['DIV', 'tab']]) {
  const { visto: v2, detach: s2 } = osserva();
  scorri(inside(tag, { touchAction: 'none', role }));
  assert.equal(v2[0], 'start', `${tag} role=${role} e\\' un comando anche lui`);
  s2();
}
"""
    )


def test_a_component_that_drags_sideways_keeps_the_swipe() -> None:
    _run_js(
        """
for (const [name, target] of [
  ['una mappa con touch-action none', inside('DIV', { touchAction: 'none' })],
  ['un carosello con pan-y', el('IMG', { parent: inside('DIV', { touchAction: 'pan-y' }) })],
  ['un cursore a slitta', inside('INPUT', { type: 'range' })],
]) {
  const { visto, detach } = osserva();
  scorri(target);
  assert.deepEqual(visto, [], name + ': la pagina ha scorso lo stesso');
  detach();
}

/* Un gioco che si prende tutto lo schermo lo dice sul `body` vero: dentro una
   app il confine e' la finestra, quindi la risalita ci deve arrivare. */
document.body.touchAction = 'none';
const { visto } = osserva();
scorri(inside('CANVAS'));
assert.deepEqual(visto, [], 'il gioco a tutto schermo: la pagina ha scorso lo stesso');
"""
    )


def test_values_that_leave_sideways_to_the_browser_do_not_claim() -> None:
    _run_js(
        """
for (const value of ['auto', 'manipulation', 'pan-x', 'pan-x pan-y', 'pan-left pinch-zoom']) {
  const { visto, detach } = osserva();
  scorri(inside('DIV', { touchAction: value }));
  assert.equal(visto[0], 'start', value + ' si e\\' tenuto lo scorrimento');
  detach();
}
"""
    )


def test_what_the_component_does_while_moving_decides() -> None:
    """Un carosello scritto a mano: nessuna dichiarazione, ma blocca il browser."""
    _run_js(
        """
const { visto } = osserva();
scorri(inside('DIV'), { app: (e) => e.preventDefault() });
assert.deepEqual(visto, []);
"""
    )


def test_a_sideways_scroller_keeps_the_swipe_even_at_its_edge() -> None:
    """La striscia dei temi in Impostazioni, come l'ha registrata l'utente.

    La striscia sta all'inizio e il dito va prima a destra: «di la' non c'e'
    niente», e fino al 23/09/2026 il gesto passava alla pagina — che poi seguiva
    il dito anche quando tornava a sinistra.
    """
    _run_js(
        """
const striscia = inside('DIV', { overflowX: 'auto' });
striscia.scrollWidth = 900;
const tema = el('BUTTON', { parent: striscia });
for (const [where, scrollLeft] of [['all inizio', 0], ['alla fine', 500], ['a meta', 200]]) {
  striscia.scrollLeft = scrollLeft;
  for (const direction of [-1, +1]) {
    const { visto, detach } = osserva();
    lancia('touchstart', { target: tema, touches: [dito(700)] });
    for (const step of [10, 50, 150]) {
      lancia('touchmove', { target: tema, touches: [dito(700 + direction * step)] });
    }
    lancia('touchend', { target: tema, changedTouches: [dito(700 + direction * 150)] });
    assert.deepEqual(visto, [], `striscia ${where}, dito verso ${direction}: ha scorso la pagina`);
    detach();
  }
}
"""
    )


def test_a_container_where_everything_fits_keeps_nothing() -> None:
    """Una tabella stretta in chat: `overflow-x: auto`, ma non sfora."""
    _run_js(
        """
const tabella = inside('DIV', { overflowX: 'auto' });
tabella.scrollWidth = 400;
const { visto } = osserva();
scorri(el('TD', { parent: tabella }));
assert.equal(visto[0], 'start');
"""
    )


def test_selected_text_and_a_second_finger_stop_the_page() -> None:
    _run_js(
        """
selezione = 'ciao';
const { visto, detach } = osserva();
scorri(inside('P'));
assert.deepEqual(visto, [], 'con del testo selezionato la pagina ha scorso');
detach();
selezione = '';

const { visto: v2 } = osserva();
const b = inside('DIV');
scorri(b, { end: false });
lancia('touchmove', { target: b, touches: [dito(140), dito(20)] });
assert.equal(v2.at(-1), 'cancel', 'il pizzico non ha annullato lo scorrimento');
"""
    )


def test_exclusive_cancels_the_app_and_keeps_the_finger() -> None:
    """Dentro una app: la pagina vince, e l'app riceve l'annullo e non sente piu' niente."""
    _run_js(
        """
const { visto } = osserva({ exclusive: true });
const vita = inside('BUTTON', { touchAction: 'none' });
const esiti = scorri(vita, { end: false });

const tipi = vita.ricevuti.map((e) => e.type);
assert.deepEqual(tipi, ['pointercancel', 'touchcancel'], 'l\\'app non ha saputo di aver perso il dito');
assert.equal(vita.ricevuti[0].pointerId, 7);
assert.ok(!visto.includes('cancel'), 'il nostro annullo ha annullato noi');

// prima di diventare nostro il dito passava; dopo, si ferma sulla finestra
assert.deepEqual(esiti, ['passato', 'passato', 'fermato']);
assert.equal(lancia('pointermove', { target: vita, pointerId: 7 }), 'fermato');
assert.equal(lancia('pointerup', { target: vita, pointerId: 7 }), 'fermato');
// ...ma il riconoscimento continua a vederlo
lancia('touchend', { target: vita, changedTouches: [dito(150)] });
assert.deepEqual(visto.at(-1), ['end', 'next']);
// e a rilascio avvenuto il dito dopo arriva di nuovo all'app
assert.equal(lancia('pointermove', { target: vita, pointerId: 8 }), 'passato');
"""
    )


def test_when_the_component_wins_the_app_is_left_alone() -> None:
    _run_js(
        """
const { visto } = osserva({ exclusive: true });
const mappa = inside('DIV', { touchAction: 'none' });
const esiti = scorri(mappa);
assert.deepEqual(visto, []);
assert.deepEqual(mappa.ricevuti, []);
assert.deepEqual(esiti, ['passato', 'passato', 'passato']);
"""
    )


def test_the_shells_are_not_exclusive() -> None:
    """Officina e casa ascoltano il proprio contenuto: niente annulli, niente ascolti in discesa."""
    _run_js(
        """
const before = ascolti.length;
const { detach } = osserva();
assert.ok(ascolti.slice(before).every((a) => !a.cattura));
detach();
const { detach: s2 } = osserva({ exclusive: true });
s2();
assert.equal(ascolti.length, before, 'staccare ha lasciato ascolti appesi');
"""
    )


def test_while_the_page_swipes_nothing_scrolls_up_and_down() -> None:
    """«Se ho lo swipe destra sinistra in corso non posso fare anche scroll su giu».

    Il browser comincia a scorrere in verticale prima che l'asse sia deciso, e
    da li' il `preventDefault` non vale: si blocca lo scorrevole stesso.
    """
    _run_js(
        """
root.scrollHeight = 2000;
const thread = inside('DIV', { overflowY: 'auto' });
thread.scrollHeight = 3000;
thread.style.overflowY = 'scroll';        // un valore suo, da restituire
const short = el('DIV', { overflowY: 'auto', parent: thread });
short.scrollHeight = 100;               // non scorre: non si tocca
const alto = el('DIV', { parent: short });  // sfora ma non scorre: `hidden` lo taglierebbe
alto.scrollHeight = 5000;
const row = el('P', { parent: alto });

const { visto } = osserva();
scorri(row, { end: false });
assert.equal(visto[0], 'start');
assert.equal(thread.style.overflowY, 'hidden', 'il filo scorre ancora su e giu');
assert.equal(root.style.overflowY, 'hidden', 'la pagina intera scorre ancora');
assert.equal(short.style.overflowY, undefined, 'bloccato uno che non scorre');
assert.equal(alto.style.overflowY, undefined, 'tagliato un elemento che non e\\' uno scorrevole');

lancia('touchend', { target: row, changedTouches: [dito(150)] });
assert.equal(thread.style.overflowY, 'scroll', 'il filo non e\\' tornato com\\'era');
assert.equal(root.style.overflowY, undefined);

// annullato dal sistema a meta': si libera lo stesso
scorri(row, { end: false });
assert.equal(thread.style.overflowY, 'hidden');
lancia('touchcancel', { target: row });
assert.equal(thread.style.overflowY, 'scroll');

// e un gesto che resta al componente, o verticale, non blocca niente
const striscia = el('DIV', { overflowX: 'auto', parent: thread });
striscia.scrollWidth = 900;
scorri(el('BUTTON', { parent: striscia }), { end: false });
assert.equal(thread.style.overflowY, 'scroll');
lancia('touchend', { target: row, changedTouches: [dito(150)] });
lancia('touchstart', { target: row, touches: [dito(300, 100)] });
lancia('touchmove', { target: row, touches: [dito(302, 180)] });
assert.equal(thread.style.overflowY, 'scroll');
"""
    )


def test_a_second_finger_mid_swipe_cancels_it_out_loud() -> None:
    """M19: un secondo dito che scende a scorrimento gia' orizzontale arriva
    come ``touchstart`` con due tocchi. Lo azzerava senza ``onCancel``, e il
    guscio restava con la vista (o la pista) ferma a meta'."""
    _run_js(
        """
const { visto } = osserva();
const b = inside('DIV');
scorri(b, { end: false });
assert.equal(visto[0], 'start');
lancia('touchstart', { target: b, touches: [dito(150), dito(200)] });
assert.equal(visto.at(-1), 'cancel', 'il gesto e\\' sparito senza onCancel');
// E il rilascio dopo non chiude un gesto che non c'e' piu'.
lancia('touchend', { target: b, changedTouches: [dito(150)] });
assert.equal(visto.filter((v) => Array.isArray(v) && v[0] === 'end').length, 0);
"""
    )


def test_a_new_touch_before_the_axis_is_decided_is_silent() -> None:
    """``onCancel`` arriva solo se l'asse era stato deciso."""
    _run_js(
        """
const { visto } = osserva();
const b = inside('DIV');
lancia('touchstart', { target: b, touches: [dito(300)] });
lancia('touchstart', { target: b, touches: [dito(300), dito(200)] });
assert.deepEqual(visto, []);
"""
    )
