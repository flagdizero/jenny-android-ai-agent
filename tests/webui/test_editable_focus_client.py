"""Il fuoco della pagina detto al guscio — ``shared/editable-focus.js`` sotto node.

Misurato il 09/10/2026 sul Titan 2: dopo uno swipe della pista il composer aveva
il fuoco e la tastiera (Pastiera) era legata a un ``TYPE_NULL``; si scriveva lo
stesso, ma la lettera accentata a pressione lunga non usciva più. Il guscio
rilega (``ImeBinding.kt``), e per farlo deve sapere dov'è il fuoco: lo dice
questo script, con ``JennyNative.setEditableFocus``.

Qui si misura cosa dice e quando: ``text`` per un campo, ``none`` per il resto,
``frame`` per l'iframe di una Jenny App; una volta sola per un ``blur`` seguito
da un ``focus`` nello stesso giro; di nuovo per ogni nuovo fuoco su un campo,
anche lo stesso. Che il guscio poi rileghi lo dice il telefono.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "editable-focus.js"
)

pytestmark = requires_node


_HARNESS = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const listeners = {};
const doc = {
  body: { tagName: 'BODY' },
  documentElement: { tagName: 'HTML' },
  activeElement: null,
  addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
};
const sent = [];
const timers = [];
const win = {
  JennyNative: __NATIVE__,
  setTimeout(fn) { timers.push(fn); },
};

new Function('window', 'document', readFileSync(__PATH__, 'utf8'))(win, doc);

function fire(type, target) {
  for (const fn of listeners[type] || []) fn({ target });
}
/** Finisce il giro: partono i timer a 0. */
function tick() {
  while (timers.length) timers.shift()();
}
function focus(el) {
  const before = doc.activeElement;
  if (before) fire('focusout', before);
  doc.activeElement = el;
  fire('focusin', el);
}
function blur() {
  const before = doc.activeElement;
  doc.activeElement = doc.body;
  if (before) fire('focusout', before);
}
const textarea = { tagName: 'TEXTAREA' };
"""


def _run_js(script: str, native: str = "{ setEditableFocus: (s) => sent.push(s) }") -> dict:
    source = (
        _HARNESS.replace("__PATH__", json.dumps(str(SCRIPT))).replace("__NATIVE__", native)
        + script
    )
    return json.loads(run_js(source))


def test_a_field_and_the_rest() -> None:
    out = _run_js(
        """
focus(textarea); tick();
blur(); tick();
focus({ tagName: 'BUTTON' }); tick();
focus({ tagName: 'IFRAME' }); tick();
focus({ tagName: 'DIV', isContentEditable: true }); tick();
console.log(JSON.stringify(sent));
"""
    )
    assert out == ["text", "none", "frame", "text"]


def test_which_inputs_are_text() -> None:
    """Solo quelli che chiamano la tastiera; un campo readonly o spento no."""
    out = _run_js(
        """
const seen = {};
for (const type of ['', 'text', 'search', 'email', 'url', 'tel', 'password', 'number',
                    'checkbox', 'range', 'color', 'date', 'file']) {
  sent.length = 0;
  blur(); tick(); sent.length = 0;
  focus({ tagName: 'INPUT', getAttribute: () => type }); tick();
  seen[type || '(none)'] = sent[0] || 'none';
}
sent.length = 0;
focus({ tagName: 'TEXTAREA', readOnly: true }); tick();
seen.readonly = sent[0] || 'none';
blur(); tick(); sent.length = 0;
focus({ tagName: 'INPUT', disabled: true, getAttribute: () => 'text' }); tick();
seen.disabled = sent[0] || 'none';
console.log(JSON.stringify(seen));
"""
    )
    texty = ("(none)", "text", "search", "email", "url", "tel", "password", "number")
    assert all(out[t] == "text" for t in texty), out
    assert all(out[t] == "none" for t in ("checkbox", "range", "color", "date", "file")), out
    assert out["readonly"] == "none" and out["disabled"] == "none"


def test_a_blur_and_a_focus_in_the_same_turn_are_one_focus() -> None:
    """Il type-ahead, il ritorno da una pagina: il guscio sente un fuoco solo."""
    out = _run_js(
        """
focus(textarea); tick();
sent.length = 0;
blur(); focus(textarea); tick();
console.log(JSON.stringify(sent));
"""
    )
    assert out == ["text"]


def test_every_new_focus_on_a_field_is_told_again() -> None:
    """Anche sullo stesso campo, anche senza un `none` in mezzo: per il guscio
    è un fuoco nuovo, con la sua rilegatura."""
    out = _run_js(
        """
focus(textarea); tick();
focus({ tagName: 'INPUT', getAttribute: () => 'search' }); tick();
fire('focusin', textarea); tick();
console.log(JSON.stringify(sent));
"""
    )
    assert out == ["text", "text", "text"]


def test_the_same_non_field_state_is_not_repeated() -> None:
    out = _run_js(
        """
focus({ tagName: 'BUTTON' }); tick();
focus({ tagName: 'A' }); tick();
console.log(JSON.stringify(sent));
"""
    )
    assert out == ["none"]


def test_outside_the_shell_it_does_nothing() -> None:
    """Nel browser del Mac, o con un guscio vecchio, nessun ascolto."""
    for native in ("undefined", "{}"):
        out = _run_js(
            """
console.log(JSON.stringify({ listeners: Object.keys(listeners) }));
""",
            native=native,
        )
        assert out == {"listeners": []}


def test_a_failing_shell_does_not_break_the_page() -> None:
    out = _run_js(
        """
focus(textarea); tick();
console.log(JSON.stringify({ ok: true }));
""",
        native="{ setEditableFocus: () => { throw new Error('gone'); } }",
    )
    assert out == {"ok": True}


def test_every_shell_loads_it_right_after_the_bridge() -> None:
    """Legge `window.JennyNative` come `native-bridge.js` lo lascia: dopo di lui,
    prima dei moduli — e in tutti e tre i gusci."""
    ui = SCRIPT.parents[2]
    bridge = '<script src="/html-mobile/assets/shared/native-bridge.js"></script>'
    mine = '<script src="/html-mobile/assets/shared/editable-focus.js"></script>'
    for shell in ("index.html", "workshop.html", "onboarding.html"):
        html = (ui / shell).read_text(encoding="utf-8")
        assert html.index(bridge) < html.index(mine) < html.index('type="module"'), shell
    manifest = (SCRIPT.parents[5] / "jenny" / "utils" / "android_assets.py").read_text("utf-8")
    assert '"assets/shared/editable-focus.js"' in manifest


_FIELD = """
const calls = [];
const field = {
  tagName: 'TEXTAREA', selectionStart: 4, selectionEnd: 4, selectionDirection: 'none',
  blur() { calls.push('blur'); blur(); field.selectionStart = field.selectionEnd = 0; },
  focus(opts) { calls.push(['focus', opts]); focus(field); },
  setSelectionRange(s, e) { calls.push(['select', s, e]); field.selectionStart = s; field.selectionEnd = e; },
};
"""


def test_refocus_gives_the_field_its_focus_back_where_you_were() -> None:
    """La cura che il guscio chiama dopo il tasto Home: togli e ridai il fuoco,
    nello stesso giro, senza far scorrere niente e col cursore al suo posto."""
    out = _run_js(
        _FIELD
        + """
focus(field); tick();
const done = win.JennyEditableFocus.refocus();
console.log(JSON.stringify({ done, calls, at: field.selectionStart, active: doc.activeElement === field }));
"""
    )
    assert out == {
        "done": True,
        "calls": ["blur", ["focus", {"preventScroll": True}], ["select", 4, 4]],
        "at": 4,
        "active": True,
    }


def test_refocus_is_not_told_to_the_shell_as_a_new_focus() -> None:
    """Contato, ridarebbe al guscio le sue cure: girerebbe in tondo."""
    out = _run_js(
        _FIELD
        + """
focus(field); tick();
sent.length = 0;
win.JennyEditableFocus.refocus(); tick();
console.log(JSON.stringify(sent));
"""
    )
    assert out == []


def test_refocus_leaves_anything_else_alone() -> None:
    out = _run_js(
        """
focus({ tagName: 'BUTTON', blur() { throw new Error('no'); } }); tick();
console.log(JSON.stringify({ done: win.JennyEditableFocus.refocus() }));
"""
    )
    assert out == {"done": False}
