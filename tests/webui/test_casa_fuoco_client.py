"""In casa il fuoco resta sul campo dove scrivi — ``casa-fuoco.js`` sotto node.

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
import shutil
import subprocess
from pathlib import Path

import pytest

FUOCO_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "casa-fuoco.js"
)

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


_HARNESS = """
import assert from 'node:assert/strict';
const { FuocoComposer, tastieraFisica } = await import('__URL__');

function target() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    fire(type, e) { for (const fn of listeners[type] || []) fn(e); },
  };
}

function banco({ tastiera = true, attivo = true } = {}) {
  const doc = target();
  doc.activeElement = null;
  doc.visibilityState = 'visible';
  const input = { tagName: 'TEXTAREA', focusCalls: [] };
  input.focus = (opts) => { input.focusCalls.push(opts); doc.activeElement = input; };
  const chat = target();
  const stato = { tastiera, attivo };
  const fuoco = new FuocoComposer({
    input, superfici: [chat], doc,
    attivo: () => stato.attivo,
    tastiera: () => stato.tastiera,
  });
  return { doc, input, chat, stato, fuoco };
}

function tocco(el) {
  const e = { target: el, prevented: false, preventDefault() { e.prevented = true; } };
  return e;
}
"""


def _run_js(script: str) -> dict:
    source = _HARNESS.replace("__URL__", FUOCO_JS.as_uri()) + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


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
const b = banco({ tastiera: false });
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
const b = banco({ attivo: false });
b.doc.fire('keydown', { key: 'a' });
assert.equal(b.doc.activeElement, null);
assert.equal(b.fuoco.rimetti(), false);
assert.equal(b.input.focusCalls.length, 0);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_someone_typing_elsewhere_keeps_their_keys() -> None:
    out = _run_js(
        """
const b = banco();
const altro = { tagName: 'INPUT' };
b.doc.activeElement = altro;
b.doc.fire('keydown', { key: 'a' });
assert.equal(b.doc.activeElement, altro);
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
const e = tocco({ tagName: 'BUTTON' });
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
const e = tocco({ tagName: 'DIV' });
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
const b = banco({ tastiera: false });
const e = tocco({ tagName: 'DIV' });
b.chat.fire('mousedown', e);
assert.equal(e.prevented, false);
assert.equal(b.doc.activeElement, null);
assert.equal(b.fuoco.rimetti(), false);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True


def test_a_tap_on_another_field_moves_the_focus_there() -> None:
    out = _run_js(
        """
const b = banco();
for (const el of [{ tagName: 'INPUT' }, { tagName: 'TEXTAREA' }, { tagName: 'P', isContentEditable: true }]) {
  const e = tocco(el);
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
assert.equal(tastieraFisica({ JennyNative: { hasHardwareKeyboard: () => true } }), true);
assert.equal(tastieraFisica({ JennyNative: { hasHardwareKeyboard: () => false } }), false);
assert.equal(tastieraFisica({ JennyNative: { hasHardwareKeyboard: () => { throw new Error('x'); } } }), false);
assert.equal(tastieraFisica({ JennyNative: {}, matchMedia: () => ({ matches: true }) }), false);
assert.equal(tastieraFisica({ matchMedia: () => ({ matches: true }) }), true);
assert.equal(tastieraFisica({ matchMedia: () => ({ matches: false }) }), false);
console.log(JSON.stringify({ ok: true }));
"""
    )
    assert out["ok"] is True
