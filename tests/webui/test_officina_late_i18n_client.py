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
let caricato = false;
const i18n = {
  locale: 'it',
  t: (k) => (caricato ? 'T:' + k : k),
  load: () => Promise.resolve().then(() => { caricato = true; }),
};
"""


def test_the_header_actions_are_translated_after_load() -> None:
    funzioni = "\n".join(
        function(HEADER, name)
        for name in ("pillCasa", "cassetto", "consolle", "fileAperto", "settings")
    )
    out = run_js(
        _I18N
        + "const VISTA_DI = { brain: 'settings', hands: 'settings', memory: 'settings' };\n"
        + funzioni
        + "\nclass H {\n"
        + member(HEADER, "constructor")
        + "\n"
        + member(HEADER, "_refreshTitles")
        + "\n  setMode() {}\n}\n"
        + """
const h = new H();
await i18n.load();
h._refreshTitles();
const titoli = [];
for (const [modo, c] of Object.entries(h.modeConfigs)) {
  titoli.push([modo, c.title]);
  for (const a of c.actions || []) titoli.push([modo + ':' + a.action, a.title]);
}
const grezze = titoli.filter(([, t]) => !String(t).startsWith('T:'));
assert.deepEqual(grezze, [], 'restano chiavi grezze: ' + JSON.stringify(grezze));
assert.equal(h.modeConfigs.apps, undefined, 'la scheda «App» non esiste piu\\'');
console.log('ok');
"""
    )
    assert out.strip() == "ok"


def test_the_minichat_labels_are_translated_after_load() -> None:
    out = run_js(
        _I18N
        + """
function nodo() {
  const n = {
    attrs: {}, classList: { add() {} }, dataset: {}, placeholder: '',
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {},
    appendChild() {},
    figli: {},
    querySelector(sel) { return (this.figli[sel] ||= nodo()); },
  };
  return n;
}
globalThis.document = { createElement: () => nodo() };
class JennyMascot { _buildDom() {} }
class J extends JennyMascot {
  constructor() { super(); this.host = nodo(); }
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
