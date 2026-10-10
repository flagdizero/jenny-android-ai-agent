"""In casa il fuoco resta sul campo dove scrivi — ``home-focus.js`` sotto node.

Segnalato dall'utente il 24/09/2026, sul Titan 2: «se clicco da una parte si
toglie il focus e non posso più scrivere». Riprodotto sul telefono: tocco sul
filo, poi un tasto, e il tasto non arrivava da nessuna parte. La casa non aveva
né il type-ahead dell'officina né altro che rimettesse il fuoco sul campo.

Qui si misura la regola, non il browser: che un carattere nel vuoto rimetta il
fuoco sul campo, che un tocco sulla chat non glielo tolga — **solo** con la
tastiera fisica, perché altrove il fuoco è una tastiera a schermo alzata — e che
nessuna delle due cose succeda quando la chat non è a schermo o c'è qualcosa
sopra. Che ``preventDefault`` sul ``mousedown`` trattenga davvero il fuoco nella
WebView lo dice il telefono, non questo banco.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

FOCUS_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "home-focus.js"
)


pytestmark = requires_node


_HARNESS = """
import assert from 'node:assert/strict';
const { ComposerFocus, physicalKeyboard } = await import('__URL__');

function target() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    fire(type, e) { for (const fn of listeners[type] || []) fn(e); },
  };
}

function banco({ keyboard = true, active = true } = {}) {
  const doc = target();
  doc.activeElement = null;
  doc.visibilityState = 'visible';
  // Un campo con testo e cursore: `setSelectionRange` fa partire il
  // `selectionchange` come il browser.
  const input = Object.assign(target(), {
    tagName: 'TEXTAREA', focusCalls: [], value: '', selectionStart: 0, selectionEnd: 0,
  });
  input.setSelectionRange = (start, end) => {
    input.selectionStart = start;
    input.selectionEnd = end;
    input.fire('selectionchange', {});
  };
  input.focus = (opts) => {
    input.focusCalls.push(opts);
    doc.activeElement = input;
    input.fire('focus', {});
  };
  const chat = target();
  const state = { keyboard, active };
  const clock = { t: 1000 };
  const focus = new ComposerFocus({
    input, surfaces: [chat], doc,
    active: () => state.active,
    keyboard: () => state.keyboard,
    now: () => clock.t,
  });
  return { doc, input, chat, state, focus, clock };
}

function tap(el) {
  const e = { target: el, prevented: false, preventDefault() { e.prevented = true; } };
  return e;
}
"""


def _run_js(script: str) -> dict:
    source = _HARNESS.replace("__URL__", FOCUS_JS.as_uri()) + script
    return json.loads(run_js(source))


def test_a_character_typed_into_the_void_lands_in_the_field() -> None:
    """Il difetto segnalato: il fuoco era sul `body` e la «b» spariva."""
    out = _run_js(
        """
const b = banco();
b.doc.activeElement = { tagName: 'DIV' };
b.doc.fire('keydown', { key: 'b' });
assert.equal(b.doc.activeElement, b.input);
// Senza far scorrere niente: la pista puo' essere a meta' di una scivolata.
assert.deepEqual(b.input.focusCalls, [{ preventScroll: true }]);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_type_ahead_works_without_a_physical_keyboard_flag() -> None:
    """Un tasto nel vuoto esiste solo se una tastiera fisica c'è: non serve
    chiederlo, e un guscio vecchio senza il metodo nativo non lo perde."""
    out = _run_js(
        """
const b = banco({ keyboard: false });
b.doc.fire('keydown', { key: 'x' });
assert.equal(b.doc.activeElement, b.input);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_no_key_is_taken_when_the_chat_is_not_on_screen() -> None:
    """Sulla pagina App i tasti sono della ricerca del cassetto; con un foglio
    aperto sono del foglio. Il campo dietro non li prende."""
    out = _run_js(
        """
const b = banco({ active: false });
b.doc.fire('keydown', { key: 'a' });
assert.equal(b.doc.activeElement, null);
assert.equal(b.focus.restore(), false);
assert.equal(b.input.focusCalls.length, 0);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_someone_typing_elsewhere_keeps_their_keys() -> None:
    out = _run_js(
        """
const b = banco();
const other = { tagName: 'INPUT' };
b.doc.activeElement = other;
b.doc.fire('keydown', { key: 'a' });
assert.equal(b.doc.activeElement, other);
// Enter, frecce, combinazioni: non sono testo, restano a chi li aspetta.
b.doc.activeElement = null;
for (const e of [{ key: 'Enter' }, { key: 'ArrowUp' }, { key: 'a', ctrlKey: true }]) {
  b.doc.fire('keydown', e);
}
assert.equal(b.doc.activeElement, null);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_a_tap_on_the_chat_does_not_steal_the_focus() -> None:
    """Il `click` resta — il tasto manda manda — ma il fuoco non si sposta."""
    out = _run_js(
        """
const b = banco();
b.input.focus({ preventScroll: true });
b.input.focusCalls.length = 0;
const e = tap({ tagName: 'BUTTON' });
b.chat.fire('mousedown', e);
assert.equal(e.prevented, true);
assert.equal(b.doc.activeElement, b.input);
assert.equal(b.input.focusCalls.length, 0, 'ha rimesso un fuoco che aveva gia');
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_a_tap_on_the_chat_gives_back_a_lost_focus() -> None:
    out = _run_js(
        """
const b = banco();
const e = tap({ tagName: 'DIV' });
b.chat.fire('mousedown', e);
assert.equal(e.prevented, true);
assert.equal(b.doc.activeElement, b.input);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_with_an_on_screen_keyboard_a_tap_behaves_as_always() -> None:
    """Il fuoco sul campo sarebbe una tastiera alzata sopra la conversazione
    che stai leggendo: senza tastiera fisica il tocco fa quel che ha sempre
    fatto."""
    out = _run_js(
        """
const b = banco({ keyboard: false });
const e = tap({ tagName: 'DIV' });
b.chat.fire('mousedown', e);
assert.equal(e.prevented, false);
assert.equal(b.doc.activeElement, null);
assert.equal(b.focus.restore(), false);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_a_tap_on_another_field_moves_the_focus_there() -> None:
    out = _run_js(
        """
const b = banco();
for (const el of [{ tagName: 'INPUT' }, { tagName: 'TEXTAREA' }, { tagName: 'P', isContentEditable: true }]) {
  const e = tap(el);
  b.chat.fire('mousedown', e);
  assert.equal(e.prevented, false, el.tagName);
}
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_coming_back_to_the_app_puts_the_focus_back() -> None:
    out = _run_js(
        """
const b = banco();
b.doc.visibilityState = 'hidden';
b.doc.fire('visibilitychange', {});
assert.equal(b.doc.activeElement, null);
b.doc.visibilityState = 'visible';
b.doc.fire('visibilitychange', {});
assert.equal(b.doc.activeElement, b.input);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_the_physical_keyboard_question_goes_to_the_native_shell() -> None:
    """Il guscio sa se c'e' una tastiera fisica prima del primo tasto. Un
    guscio vecchio, senza il metodo, vale «no» — il comportamento di prima —
    e fuori dal guscio decide il puntatore."""
    out = _run_js(
        """
assert.equal(physicalKeyboard({ JennyNative: { hasHardwareKeyboard: () => true } }), true);
assert.equal(physicalKeyboard({ JennyNative: { hasHardwareKeyboard: () => false } }), false);
assert.equal(physicalKeyboard({ JennyNative: { hasHardwareKeyboard: () => { throw new Error('x'); } } }), false);
assert.equal(physicalKeyboard({ JennyNative: {}, matchMedia: () => ({ matches: true }) }), false);
assert.equal(physicalKeyboard({ matchMedia: () => ({ matches: true }) }), true);
assert.equal(physicalKeyboard({ matchMedia: () => ({ matches: false }) }), false);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


_CARET = """
const b = banco();
b.input.value = 'una bozza';
b.input.setSelectionRange(9, 9);
// La pagina cambia: il campo perde il fuoco.
b.doc.activeElement = null;
b.input.fire('focusout', {});
"""


def test_the_caret_comes_back_where_it_was() -> None:
    """Il difetto del 10/10/2026: tornando dalla pagina dei Quaderni il cursore
    era all'inizio, e il tasto dopo scriveva in testa alla bozza. Chromium lo
    sposta pochi ms dopo il fuoco: la guardia lo rimette."""
    out = _run_js(
        _CARET
        + """
b.focus.restore();
assert.equal(b.input.selectionStart, 9);
b.clock.t += 5;
b.input.selectionStart = b.input.selectionEnd = 0;
b.input.fire('selectionchange', {});
console.log(JSON.stringify({ start: b.input.selectionStart, end: b.input.selectionEnd }));
"""
    )
    assert out == {"start": 9, "end": 9}


def test_a_tap_on_the_field_places_the_caret_where_you_tap() -> None:
    """Un dito sul campo decide lui: niente cursore ricordato."""
    out = _run_js(
        _CARET
        + """
b.input.fire('pointerdown', {});
b.clock.t += 200;  // il fuoco del tocco arriva al mousedown, dopo il rilascio
b.input.selectionStart = b.input.selectionEnd = 2;
b.input.focus();
b.clock.t += 5;
b.input.fire('selectionchange', {});
console.log(JSON.stringify({ start: b.input.selectionStart }));
"""
    )
    assert out == {"start": 2}


def test_a_changed_draft_keeps_its_own_caret() -> None:
    """Una bozza cambiata mentre eri via (un invio, una frase pronta) non
    riceve il cursore di un altro testo."""
    out = _run_js(
        _CARET
        + """
b.input.value = '';
b.input.selectionStart = b.input.selectionEnd = 0;
b.focus.restore();
b.clock.t += 5;
b.input.fire('selectionchange', {});
console.log(JSON.stringify({ start: b.input.selectionStart }));
"""
    )
    assert out == {"start": 0}


def test_the_guard_ends_and_arrow_keys_move_the_caret_again() -> None:
    """Passata la guardia il cursore è tuo: le frecce lo spostano."""
    out = _run_js(
        _CARET
        + """
b.focus.restore();
b.clock.t += 500;
b.input.selectionStart = b.input.selectionEnd = 3;
b.input.fire('selectionchange', {});
console.log(JSON.stringify({ start: b.input.selectionStart }));
"""
    )
    assert out == {"start": 3}


def test_type_ahead_puts_the_character_where_you_were() -> None:
    """Col type-ahead il fuoco arriva dentro il keydown e il carattere entra
    subito dopo: il cursore dev'essere già al suo posto, sincrono."""
    out = _run_js(
        _CARET
        + """
b.input.selectionStart = b.input.selectionEnd = 0;  // com'era dopo il ritorno
b.doc.fire('keydown', { key: 'x' });
console.log(JSON.stringify({ start: b.input.selectionStart, focused: b.doc.activeElement === b.input }));
"""
    )
    assert out == {"start": 9, "focused": True}
