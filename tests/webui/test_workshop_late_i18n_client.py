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


def test_the_chat_identity_and_commands_are_translated_after_load() -> None:
    """La riga «Jenny · online» e il chip «Commands» la chat li scrive al primo
    ``activate()``, che puo' venire prima delle traduzioni. Fino al 05/10/2026
    le traduzioni arrivavano in tempo per caso (le chiedeva per prima la
    minichat); senza di lei sul telefono restavano ``chat.online`` e
    ``commands.label``. Ora il guscio, a traduzioni arrivate, le fa riscrivere."""
    chat = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    out = run_js(
        _I18N
        + "let chipRenders = 0;\nconst commandsChip = { render() { chipRenders += 1; } };\n"
        + "class C {\n"
        + member(chat, "applyTranslations")
        + "\n}\n"
        + """
const c = new C();
const on = new Set(['on']);
c.identityStatus = { classList: { contains: (k) => on.has(k) } };
c.identityLabel = { textContent: 'chat.online' };
await i18n.load();
c.applyTranslations();
assert.equal(c.identityLabel.textContent, 'T:chat.online');
assert.equal(chipRenders, 1);
on.clear();
c.applyTranslations();
assert.equal(c.identityLabel.textContent, 'T:chat.offline');
console.log('ok');
"""
    )
    assert out.strip() == "ok"
    load = app[app.index("i18n.load(i18n.locale).then(() => {"):]
    load = load[: load.index("});")]
    assert "this.controllers.chat?.applyTranslations?.();" in load
