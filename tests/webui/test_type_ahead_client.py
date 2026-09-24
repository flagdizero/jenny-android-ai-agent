"""La guardia del type-ahead, eseguita davvero sotto node.

``shared/type-ahead.js`` è l'unica copia delle quattro condizioni che decidono
se un tasto "nel vuoto" è testo, ed è condivisa fra la chat
(``_maybeTypeAheadFocus``) e il cassetto (casella **4.1**). Era stata tarata su
questo hardware e viveva in un metodo solo: adesso che i chiamanti sono due,
riscriverla a occhio nel secondo sarebbe stato il modo più diretto di perdere
la taratura sul caso che l'ha motivata.

Quel caso è il primo test qui sotto: le tastiere fisiche del Titan 2 (via
bbkeyboard) emettono ``keydown`` con ``key`` **undefined**, e senza il guard
``length === 1`` finirebbero per mettere a fuoco un campo a ogni tasto morto —
frecce comprese, cioè proprio i tasti con cui nel cassetto si sceglie.

Il modulo prende ``activeElement`` come argomento invece di leggerlo da
``document``: è quello che lo rende eseguibile qui senza un DOM finto, come
``launcher-rank.js``. Stesso idioma di ``test_launcher_rank_client.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

TYPE_AHEAD_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "type-ahead.js"
)


pytestmark = requires_node


def _run_js(script: str) -> str:
    source = (
        TYPE_AHEAD_JS.read_text(encoding="utf-8")
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    return run_js(source)


def test_a_physical_keyboard_keydown_without_key_is_not_text() -> None:
    """Il caso per cui la guardia esiste: bbkeyboard manda `key` undefined."""
    out = _run_js(
        """
assert.equal(isTypeAheadKey({}, null), false, 'key undefined è passato per testo');
assert.equal(isTypeAheadKey({ key: undefined }, null), false);
assert.equal(isTypeAheadKey(null, null), false);
console.log(JSON.stringify({ok: true}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_only_single_printable_characters_pass() -> None:
    """I nomi di tasto lunghi — Enter, Escape, ArrowDown — hanno un significato
    proprio nel cassetto: se passassero di qui, ⏎ e le frecce metterebbero a
    fuoco il campo invece di aprire e di scegliere."""
    out = _run_js(
        """
const yes = ['a', 'Z', '7', 'è', '.'];
const no = ['Enter', 'Escape', 'ArrowDown', 'ArrowUp', 'Tab', 'Backspace', 'F5'];
for (const key of yes) assert.equal(isTypeAheadKey({ key }, null), true, key);
for (const key of no) assert.equal(isTypeAheadKey({ key }, null), false, key);
console.log(JSON.stringify({ok: true}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_space_never_starts_a_typing() -> None:
    """Nel cassetto lo spazio ha un uso proprio — attiva la riga che ha il
    fuoco — e nella chat un messaggio non comincia mai con uno spazio."""
    out = _run_js(
        """
assert.equal(isTypeAheadKey({ key: ' ' }, null), false);
console.log(JSON.stringify({ok: true}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_modifier_combos_are_shortcuts_not_text() -> None:
    out = _run_js(
        """
assert.equal(isTypeAheadKey({ key: 'a', metaKey: true }, null), false);
assert.equal(isTypeAheadKey({ key: 'a', ctrlKey: true }, null), false);
assert.equal(isTypeAheadKey({ key: 'a', altKey: true }, null), false);
// Shift no: ⇧A è una lettera maiuscola, cioè testo.
assert.equal(isTypeAheadKey({ key: 'A', shiftKey: true }, null), true);
console.log(JSON.stringify({ok: true}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_nothing_is_stolen_from_someone_already_typing() -> None:
    """La condizione che rende innocuo mettere l'ascoltatore sul documento."""
    out = _run_js(
        """
const e = { key: 'a' };
for (const tagName of ['INPUT', 'TEXTAREA', 'SELECT']) {
  assert.equal(isTypeAheadKey(e, { tagName }), false, tagName);
}
assert.equal(isTypeAheadKey(e, { tagName: 'DIV', isContentEditable: true }), false);
// Un `div` qualunque non sta scrivendo: è il caso normale del cassetto, dove
// il fuoco all'apertura sta sul foglio (D6: mai sul campo).
assert.equal(isTypeAheadKey(e, { tagName: 'DIV' }), true);
assert.equal(isTypeAheadKey(e, { tagName: 'BODY' }), true);
console.log(JSON.stringify({ok: true}));
"""
    )
    assert json.loads(out)["ok"] is True
