"""Le parole scritte prima che arrivino le traduzioni si riscrivono dopo.

``MobileApp`` costruisce l'intestazione e la mascotte **prima** di
``i18n.load()``: in quel momento ``i18n.t`` torna le chiavi grezze. Chi scrive
una stringa nel costruttore la deve riscrivere quando le traduzioni arrivano.
Non lo facevano:

* l'intestazione riscriveva solo ``title`` di file aperto e impostazioni, e i
  titoli delle azioni (tooltip ed etichetta del lettore di schermo) restavano
  ``header.back`` e ``header.refresh``;
* la minichat rileggeva solo il placeholder: scrim, campo e bottone d'invio
  restavano con ``jenny.send`` come nome.

Qui i metodi veri girano in node con un ``i18n`` che traduce solo dopo il
``load``.
"""

from __future__ import annotations

from support.js_harness import ASSETS, function, member, requires_node, run_js

pytestmark = requires_node

HEADER = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
JENNY = (ASSETS / "mobile-jenny.js").read_text(encoding="utf-8")

_I18N = """
import assert from 'node:assert/strict';
let loaded = false;
const i18n = {
  locale: 'it',
  t: (k) => (loaded ? 'T:' + k : k),
  load: () => Promise.resolve().then(() => { loaded = true; }),
};
"""


def test_the_header_actions_are_translated_after_load() -> None:
    functions = "\n".join(
        function(HEADER, name)
        for name in ("homePill", "drawer", "consoleConfig", "openFile", "settings")
    )
    out = run_js(
        _I18N
        + "const VIEW_OF = { brain: 'settings', hands: 'settings', memory: 'settings' };\n"
        + functions
        + "\nclass H {\n"
        + member(HEADER, "constructor")
        + "\n"
        + member(HEADER, "_refreshTitles")
        + "\n  setMode() {}\n}\n"
        + """
const h = new H();
await i18n.load();
h._refreshTitles();
const titles = [];
for (const [mode, c] of Object.entries(h.modeConfigs)) {
  titles.push([mode, c.title]);
  for (const a of c.actions || []) titles.push([mode + ':' + a.action, a.title]);
}
const raw = titles.filter(([, t]) => !String(t).startsWith('T:'));
assert.deepEqual(raw, [], 'restano chiavi grezze: ' + JSON.stringify(raw));
assert.equal(h.modeConfigs.apps, undefined, 'la scheda «App» non esiste piu\\'');
console.log('ok');
"""
    )
    assert out.strip() == "ok"


def test_the_minichat_labels_are_translated_after_load() -> None:
    out = run_js(
        _I18N
        + """
function node() {
  const n = {
    attrs: {}, classList: { add() {} }, dataset: {}, placeholder: '',
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {},
    appendChild() {},
    children: {},
    querySelector(sel) { return (this.children[sel] ||= node()); },
  };
  return n;
}
globalThis.document = { createElement: () => node() };
class JennyMascot { _buildDom() {} }
class J extends JennyMascot {
  constructor() { super(); this.host = node(); }
"""
        + member(JENNY, "_buildDom")
        + """
  _setOut() {}
}
const j = new J();
j._buildDom();
await new Promise((r) => setTimeout(r, 0));
assert.equal(j.scrim.attrs['aria-label'], 'T:jenny.closeMinichat');
assert.equal(j.input.attrs['aria-label'], 'T:jenny.askJenny');
assert.equal(j.sendBtn.attrs['aria-label'], 'T:jenny.send');
assert.equal(j.input.placeholder, 'T:chat.placeholder');
console.log('ok');
"""
    )
    assert out.strip() == "ok"
