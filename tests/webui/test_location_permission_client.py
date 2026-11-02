"""«Share my location» acceso con il permesso di Android negato.

Dal collaudo del 27/09/2026: l'interruttore diceva solo la preferenza. Acceso
con il permesso negato, la posizione non arrivava, riaccenderlo non chiedeva
niente e niente lo diceva. Ora Mani mette accanto all'interruttore un avviso
con «Allow location», e accenderlo chiede il permesso nello stesso tocco. Il
guscio risponde con l'evento ``jenny-location-permission``.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, member, requires_node, run_js
from support.kotlin_source import ANDROID_SRC, read_source

SETTINGS_JS = ASSETS / "mobile-settings.js"
MAIN_ACTIVITY = ANDROID_SRC / "MainActivity.kt"


@requires_node
def test_the_notice_shows_only_when_the_switch_is_on_and_android_says_no() -> None:
    src = SETTINGS_JS.read_text(encoding="utf-8")
    run_js(
        "import assert from 'node:assert/strict';\n"
        "const els = {\n"
        "  '#location-enabled-toggle': { checked: true },\n"
        "  '#location-permission': { hidden: true },\n"
        "  '#location-permission-nav': { hidden: true },\n"
        "};\n"
        "let granted = false; const asked = [];\n"
        "globalThis.window = { JennyNative: {\n"
        "  hasLocationPermission: () => granted,\n"
        "  requestLocationPermission: () => asked.push(1),\n"
        "} };\n"
        "class S {\n"
        "  constructor() { this.contentEl = { querySelector: (q) => els[q] || null }; }\n"
        f"{member(src, '_syncLocationPermission')}\n"
        f"{member(src, '_askLocationPermission')}\n"
        "}\n"
        """
const s = new S();
s._syncLocationPermission();
assert.equal(els['#location-permission'].hidden, false, 'acceso e negato: nessun avviso');
assert.equal(els['#location-permission-nav'].hidden, false, 'nessun tasto per chiederlo');
s._askLocationPermission();
assert.equal(asked.length, 1);

granted = true;
s._syncLocationPermission();
assert.equal(els['#location-permission'].hidden, true, 'concesso: l avviso resta');
s._askLocationPermission();
assert.equal(asked.length, 1, 'con il permesso concesso lo si chiede di nuovo');

granted = false;
els['#location-enabled-toggle'].checked = false;
s._syncLocationPermission();
assert.equal(els['#location-permission'].hidden, true, 'spento: l avviso non serve');

// Fuori da Android non c'e' un permesso: nessun avviso.
globalThis.window = {};
els['#location-enabled-toggle'].checked = true;
s._syncLocationPermission();
assert.equal(els['#location-permission'].hidden, true);
"""
    )


def test_switching_it_on_asks_and_the_answer_redraws() -> None:
    src = SETTINGS_JS.read_text(encoding="utf-8")
    assert "if (enabled) this._askLocationPermission();" in src
    assert "window.addEventListener('jenny-location-permission', this._onLocationPermission)" in src
    assert "document.addEventListener('visibilitychange', this._onLocationPermission)" in src
    kotlin = read_source(MAIN_ACTIVITY)
    assert "new Event('jenny-location-permission')" in kotlin
    assert "ActivityResultContracts.RequestMultiplePermissions()" in kotlin
    ensure = re.search(r"private fun ensureLocationPermission\(\) \{(.*?)\n    \}", kotlin, re.S).group(1)
    assert "hasLocationPermission()" in ensure, "con la sola approssimativa la si richiede a ogni avvio"
    launch = re.search(r"private fun launchLocationRequest\(\) \{(.*?)\n    \}", kotlin, re.S).group(1)
    assert "ACCESS_FINE_LOCATION" in launch and "ACCESS_COARSE_LOCATION" in launch
