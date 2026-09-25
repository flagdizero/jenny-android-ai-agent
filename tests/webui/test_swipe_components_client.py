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
    received: [],
    getAttribute: (n) => (n === 'role' ? role : null),
    dispatchEvent(ev) { this.received.push(ev); fire(ev.type, ev); return true; },
  };
  return e;
}

globalThis.document = { body: el('BODY') };
globalThis.getComputedStyle = (e) => ({
  overflowX: e.overflowX, overflowY: e.overflowY, touchAction: e.touchAction,
});
let selection = '';
globalThis.getSelection = () => ({ isCollapsed: !selection, toString: () => selection });

class FakeEvent {
  constructor(type, init = {}) { this.type = type; Object.assign(this, init); }
}
globalThis.PointerEvent = class extends FakeEvent {};
globalThis.TouchEvent = class extends FakeEvent {};

/* La finestra: ascolti in discesa e in risalita, nell'ordine del browser. */
const listeners = [];
globalThis.window = {
  innerWidth: 400,
  addEventListener(type, fn, opts = {}) { listeners.push({ type, fn, capture: !!opts.capture }); },
  removeEventListener(type, fn) {
    const i = listeners.findIndex((a) => a.type === type && a.fn === fn);
    if (i >= 0) listeners.splice(i, 1);
  },
};

function fire(type, ev) {
  let stopped = false;
  ev.type ||= type;
  ev.stopImmediatePropagation = () => { stopped = true; };
  ev.preventDefault ||= () => { ev.defaultPrevented = true; };
  for (const a of listeners.filter((x) => x.type === type && x.capture)) {
    a.fn(ev); if (stopped) return 'fermato';
  }
  ev.beforeUs?.(ev);  // l'app, che ascolta sotto di noi
  for (const a of listeners.filter((x) => x.type === type && !x.capture)) {
    a.fn(ev); if (stopped) return 'fermato';
  }
  return 'passato';
}

const M = await import(process.env.MODULO);

/* ── Guidare il dito ───────────────────────────────────────────────────── */

const finger = (x, y = 100) => ({ screenX: x, screenY: y });

function watch(opts = {}) {
  const visto = [];
  const detach = M.watchHorizontalSwipe(window, {
    onHorizontal: () => visto.push('start'),
    onDrag: (dx) => visto.push(['move', dx]),
    onEnd: ({ direction }) => visto.push(['end', direction]),
    onCancel: () => visto.push('cancel'),
    ...opts,
  });
  return { visto, detach };
}

/** Un dito che parte su `target` e va a sinistra di 150px in tre passi. */
function scroll(target, { app = null, end = true } = {}) {
  fire('pointerdown', { target: target, pointerId: 7, isPrimary: true });
  fire('touchstart', { target: target, touches: [finger(300)] });
  const outcomes = [];
  for (const x of [290, 250, 150]) {
    fire('pointermove', { target: target, pointerId: 7 });
    outcomes.push(fire('touchmove', { target: target, touches: [finger(x)], beforeUs: app }));
  }
  if (end) {
    fire('pointerup', { target: target, pointerId: 7 });
    fire('touchend', { target: target, changedTouches: [finger(150)] });
  }
  return outcomes;
}

const root = el('HTML');
document.body.parentElement = root;
document.scrollingElement = root;
const inside = (tag, opts = {}) => el(tag, { parent: document.body, ...opts });
"""


def _run_js(script: str) -> None:
    run_js(_HARNESS + "\n" + script, env={"MODULO": MODULO.as_uri(), "PATH": "/usr/bin:/bin"})


def test_a_plain_area_swipes_the_page() -> None:
    _run_js(
        """
const { visto } = watch();
scroll(inside('DIV'));
assert.equal(visto[0], 'start');
assert.deepEqual(visto.at(-1), ['end', 'next']);
"""
    )


def test_a_button_never_steals_the_swipe() -> None:
    """Life Counter: `touch-action: none` sui suoi − e +, e restano bottoni."""
    _run_js(
        """
const { visto, detach } = watch();
const minus = inside('BUTTON', { touchAction: 'none' });
const mark = el('SPAN', { parent: minus });
scroll(mark);
assert.equal(visto[0], 'start', 'il bottone si e\\' held lo scroll');
detach();

for (const [tag, role] of [['A', null], ['LABEL', null], ['DIV', 'button'],
                           ['DIV', 'switch'], ['DIV', 'tab']]) {
  const { visto: v2, detach: s2 } = watch();
  scroll(inside(tag, { touchAction: 'none', role }));
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
  const { visto, detach } = watch();
  scroll(target);
  assert.deepEqual(visto, [], name + ': la pagina ha scorso lo stesso');
  detach();
}

/* Un gioco che si prende tutto lo schermo lo dice sul `body` vero: dentro una
   app il confine e' la finestra, quindi la risalita ci deve arrivare. */
document.body.touchAction = 'none';
const { visto } = watch();
scroll(inside('CANVAS'));
assert.deepEqual(visto, [], 'il gioco a tutto schermo: la pagina ha scorso lo stesso');
"""
    )


def test_values_that_leave_sideways_to_the_browser_do_not_claim() -> None:
    _run_js(
        """
for (const value of ['auto', 'manipulation', 'pan-x', 'pan-x pan-y', 'pan-left pinch-zoom']) {
  const { visto, detach } = watch();
  scroll(inside('DIV', { touchAction: value }));
  assert.equal(visto[0], 'start', value + ' si e\\' held lo scroll');
  detach();
}
"""
    )


def test_what_the_component_does_while_moving_decides() -> None:
    """Un carosello scritto a mano: nessuna dichiarazione, ma blocca il browser."""
    _run_js(
        """
const { visto } = watch();
scroll(inside('DIV'), { app: (e) => e.preventDefault() });
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
const strip = inside('DIV', { overflowX: 'auto' });
strip.scrollWidth = 900;
const theme = el('BUTTON', { parent: strip });
for (const [where, scrollLeft] of [['all inizio', 0], ['alla fine', 500], ['a meta', 200]]) {
  strip.scrollLeft = scrollLeft;
  for (const direction of [-1, +1]) {
    const { visto, detach } = watch();
    fire('touchstart', { target: theme, touches: [finger(700)] });
    for (const step of [10, 50, 150]) {
      fire('touchmove', { target: theme, touches: [finger(700 + direction * step)] });
    }
    fire('touchend', { target: theme, changedTouches: [finger(700 + direction * 150)] });
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
const table = inside('DIV', { overflowX: 'auto' });
table.scrollWidth = 400;
const { visto } = watch();
scroll(el('TD', { parent: table }));
assert.equal(visto[0], 'start');
"""
    )


def test_selected_text_and_a_second_finger_stop_the_page() -> None:
    _run_js(
        """
selection = 'ciao';
const { visto, detach } = watch();
scroll(inside('P'));
assert.deepEqual(visto, [], 'con del testo selezionato la pagina ha scorso');
detach();
selection = '';

const { visto: v2 } = watch();
const b = inside('DIV');
scroll(b, { end: false });
fire('touchmove', { target: b, touches: [finger(140), finger(20)] });
assert.equal(v2.at(-1), 'cancel', 'il pizzico non ha annullato lo scorrimento');
"""
    )


def test_exclusive_cancels_the_app_and_keeps_the_finger() -> None:
    """Dentro una app: la pagina vince, e l'app riceve l'annullo e non sente piu' niente."""
    _run_js(
        """
const { visto } = watch({ exclusive: true });
const life = inside('BUTTON', { touchAction: 'none' });
const outcomes = scroll(life, { end: false });

const types = life.received.map((e) => e.type);
assert.deepEqual(types, ['pointercancel', 'touchcancel'], 'l\\'app non ha known di aver lost il finger');
assert.equal(life.received[0].pointerId, 7);
assert.ok(!visto.includes('cancel'), 'il nostro annullo ha annullato noi');

// prima di diventare nostro il dito passava; dopo, si ferma sulla finestra
assert.deepEqual(outcomes, ['passato', 'passato', 'fermato']);
assert.equal(fire('pointermove', { target: life, pointerId: 7 }), 'fermato');
assert.equal(fire('pointerup', { target: life, pointerId: 7 }), 'fermato');
// ...ma il riconoscimento continua a vederlo
fire('touchend', { target: life, changedTouches: [finger(150)] });
assert.deepEqual(visto.at(-1), ['end', 'next']);
// e a rilascio avvenuto il dito dopo arriva di nuovo all'app
assert.equal(fire('pointermove', { target: life, pointerId: 8 }), 'passato');
"""
    )


def test_when_the_component_wins_the_app_is_left_alone() -> None:
    _run_js(
        """
const { visto } = watch({ exclusive: true });
const map = inside('DIV', { touchAction: 'none' });
const outcomes = scroll(map);
assert.deepEqual(visto, []);
assert.deepEqual(map.received, []);
assert.deepEqual(outcomes, ['passato', 'passato', 'passato']);
"""
    )


def test_the_shells_are_not_exclusive() -> None:
    """Officina e casa ascoltano il proprio contenuto: niente annulli, niente ascolti in discesa."""
    _run_js(
        """
const before = listeners.length;
const { detach } = watch();
assert.ok(listeners.slice(before).every((a) => !a.capture));
detach();
const { detach: s2 } = watch({ exclusive: true });
s2();
assert.equal(listeners.length, before, 'staccare ha lasciato ascolti appesi');
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

const { visto } = watch();
scroll(row, { end: false });
assert.equal(visto[0], 'start');
assert.equal(thread.style.overflowY, 'hidden', 'il filo scorre ancora su e giu');
assert.equal(root.style.overflowY, 'hidden', 'la pagina intera scorre ancora');
assert.equal(short.style.overflowY, undefined, 'bloccato uno che non scorre');
assert.equal(alto.style.overflowY, undefined, 'tagliato un elemento che non e\\' one scrollable');

fire('touchend', { target: row, changedTouches: [finger(150)] });
assert.equal(thread.style.overflowY, 'scroll', 'il filo non e\\' returned com\\'era');
assert.equal(root.style.overflowY, undefined);

// annullato dal sistema a meta': si libera lo stesso
scroll(row, { end: false });
assert.equal(thread.style.overflowY, 'hidden');
fire('touchcancel', { target: row });
assert.equal(thread.style.overflowY, 'scroll');

// e un gesto che resta al componente, o verticale, non blocca niente
const strip = el('DIV', { overflowX: 'auto', parent: thread });
strip.scrollWidth = 900;
scroll(el('BUTTON', { parent: strip }), { end: false });
assert.equal(thread.style.overflowY, 'scroll');
fire('touchend', { target: row, changedTouches: [finger(150)] });
fire('touchstart', { target: row, touches: [finger(300, 100)] });
fire('touchmove', { target: row, touches: [finger(302, 180)] });
assert.equal(thread.style.overflowY, 'scroll');
"""
    )


def test_a_second_finger_mid_swipe_cancels_it_out_loud() -> None:
    """M19: un secondo dito che scende a scorrimento gia' orizzontale arriva
    come ``touchstart`` con due tocchi. Lo azzerava senza ``onCancel``, e il
    guscio restava con la vista (o la pista) ferma a meta'."""
    _run_js(
        """
const { visto } = watch();
const b = inside('DIV');
scroll(b, { end: false });
assert.equal(visto[0], 'start');
fire('touchstart', { target: b, touches: [finger(150), finger(200)] });
assert.equal(visto.at(-1), 'cancel', 'il gesto e\\' gone without onCancel');
// E il rilascio dopo non chiude un gesto che non c'e' piu'.
fire('touchend', { target: b, changedTouches: [finger(150)] });
assert.equal(visto.filter((v) => Array.isArray(v) && v[0] === 'end').length, 0);
"""
    )


def test_a_new_touch_before_the_axis_is_decided_is_silent() -> None:
    """``onCancel`` arriva solo se l'asse era stato deciso."""
    _run_js(
        """
const { visto } = watch();
const b = inside('DIV');
fire('touchstart', { target: b, touches: [finger(300)] });
fire('touchstart', { target: b, touches: [finger(300), finger(200)] });
assert.deepEqual(visto, []);
"""
    )
