"""Il cassetto delle app come **pagina**: la pagina App della casa (23/09/2026).

Lo stesso `LauncherController` dell'officina, con `incorporato: true` (v.
`.agent/pagine-in-alto-plan.md`). Le cose di un foglio che sale sopra la chat —
velo, sfondo inerte, trascinamento per chiudere, geometria della tastiera, fuoco
spostato all'apertura — qui non devono esserci; e «aperto» vuol dire **la
pagina che guardi**: i tasti sono suoi solo in quel mentre, e sul Titan quei
tasti sono una tastiera fisica, cioe' il modo in cui si cercano le app.

L'officina non lo passa: l'ultimo caso prova che il suo foglio e' rimasto com'era.

In node sul modulo vero, con classifica e type-ahead veri e il resto finto.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_VICINI = {
    "i18n.js": "export const i18n = { t: (k) => k };\n",
    "launcher-usage-store.js": """
export function usageStore() {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) };
}
""",
    "longpress.js": "export function setupLongPress() {}\n",
}

_FINTO_DOM = """
function creaEl(id) {
  const el = {
    id, className: '', value: '', dataset: {}, style: {}, attrs: {}, children: [], ascolto: {},
    hidden: false, tagName: 'DIV',
    get classList() {
      const e = this;
      const parole = () => (e.className || '').split(' ').filter(Boolean);
      return {
        add(c) { if (!parole().includes(c)) e.className = [...parole(), c].join(' '); },
        remove(c) { e.className = parole().filter((x) => x !== c).join(' '); },
        toggle(c, on) { if (on) this.add(c); else this.remove(c); },
        contains(c) { return parole().includes(c); },
      };
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, fn) { (this.ascolto[t] = this.ascolto[t] || []).push(fn); },
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    replaceChildren(...cs) { this.children = cs; },
    querySelector(sel) { return sel === '.launcher-head' ? testa : null; },
    contains(n) { let x = n; while (x) { if (x === this) return true; x = x.parent; } return false; },
    focus() { document.activeElement = this; this.focalizzato = (this.focalizzato || 0) + 1; },
    blur() { if (document.activeElement === this) document.activeElement = document.body; },
    scrollIntoView() {},
  };
  return el;
}
const perId = new Map();
for (const id of ['launcher-sheet', 'launcher-list', 'launcher-search', 'launcher-title',
                  'launcher-search-clear', 'launcher-status', 'launcher-status-retry']) {
  perId.set(id, creaEl(id));
}
if (globalThis.CON_FOGLIO) {
  for (const id of ['launcher-scrim', 'launcher-close', 'launcher-handle-row']) perId.set(id, creaEl(id));
}
const foglio = perId.get('launcher-sheet');
const cerca = perId.get('launcher-search');
cerca.tagName = 'INPUT';
cerca.parent = foglio;
const testa = creaEl('head');
const guscio = creaEl('shell');
const html = creaEl('html');
const docAscolto = {};
globalThis.document = {
  body: creaEl('body'),
  activeElement: null,
  documentElement: Object.assign(html, { style: { setProperty() {} } }),
  getElementById: (id) => perId.get(id) || null,
  querySelector: (sel) => (sel === '.casa-shell' ? guscio : null),
  createElement: () => creaEl(null),
  addEventListener(t, fn) { (docAscolto[t] = docAscolto[t] || []).push(fn); },
};
document.activeElement = document.body;
const finAscolto = {};
globalThis.window = {
  innerHeight: 566, innerWidth: 590,
  addEventListener(t, fn) { (finAscolto[t] = finAscolto[t] || []).push(fn); },
};
function tasto(key) {
  const e = { key, preventDefault() {}, metaKey: false, ctrlKey: false, altKey: false };
  for (const fn of docAscolto.keydown || []) fn(e);
}
const fonte = {
  addChangeListener() {}, ensureLoaded() {}, launcherEntries: () => [], listsFailed: () => false,
  isLoadingLists: () => false, retryFailedLists() {},
};
const app = { appsSource: () => fonte, appsActions: () => null };
"""


def _run(corpo: str, *, con_foglio: bool = False) -> None:
    script = (
        "import assert from 'node:assert/strict';\n"
        + f"globalThis.CON_FOGLIO = {'true' if con_foglio else 'false'};\n"
        + _FINTO_DOM
        + "const { LauncherController } = await import('./mobile-launcher.js');\n"
        + textwrap.dedent(corpo)
    )
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "mobile-launcher.js", radice / "mobile-launcher.js")
        for nome in ("launcher-rank.js", "type-ahead.js"):
            shutil.copy(ASSETS / "shared" / nome, radice / "shared" / nome)
        for nome, testo in _VICINI.items():
            (radice / "shared" / nome).write_text(testo, encoding="utf-8")
        entry = radice / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


def test_a_page_is_not_a_layer_to_close() -> None:
    """Indietro e il gesto fra le pagine chiedono «c'e' un foglio aperto?», e
    la risposta e' no anche mentre la pagina App e' quella che guardi."""
    _run("""
      const c = new LauncherController(app, { incorporato: true });
      c.open();
      assert.equal(c.isOpen(), false);
    """)


def test_the_keys_are_its_own_only_while_you_look_at_it() -> None:
    """Il primo carattere scritto sulla pagina App va nella ricerca — il
    type-ahead del Titan. Prima di arrivarci, o dopo averla lasciata, i tasti
    non sono suoi: finirebbero in una ricerca che non si vede."""
    _run("""
      const c = new LauncherController(app, { incorporato: true });
      tasto('t');
      assert.equal(cerca.focalizzato, undefined, 'prima di arrivarci ha preso un tasto');
      c.open();
      tasto('t');
      assert.equal(cerca.focalizzato, 1, 'sulla pagina App scrivere non cerca');
      c.close();
      assert.notEqual(document.activeElement, cerca, 'lasciando la pagina il fuoco e restato nella ricerca');
      tasto('x');
      assert.equal(cerca.focalizzato, 1, 'dopo averla lasciata ha preso un tasto');
    """)


def test_a_sheet_above_takes_the_keys_back() -> None:
    """Sopra la pagina puo' aprirsi la scheda di un'app: i tasti sono suoi."""
    _run("""
      app.hasOverlayAbove = () => true;
      const c = new LauncherController(app, { incorporato: true });
      c.open();
      tasto('t');
      assert.equal(cerca.focalizzato, undefined);
    """)


def test_a_page_leaves_the_rest_of_the_home_alone() -> None:
    """Niente sfondo inerte e il fuoco dov'e': una pagina non copre niente, e
    spostare il fuoco farebbe scorrere la vetrina della pista mentre la pagina
    sta ancora entrando."""
    _run("""
      const c = new LauncherController(app, { incorporato: true });
      c.open();
      assert.notEqual(guscio.inert, true, 'la casa e diventata inerte sotto una pagina');
      assert.equal(foglio.focalizzato, undefined, 'il fuoco e saltato sulla pagina');
    """)


def test_a_page_does_not_drag_or_follow_the_keyboard() -> None:
    """Il trascinamento per chiudere e la geometria della tastiera sono di un
    foglio: una pagina ha l'altezza della pagina."""
    _run("""
      new LauncherController(app, { incorporato: true });
      assert.equal((testa.ascolto.pointerdown || []).length, 0, 'la testa della pagina si trascina');
      assert.equal((finAscolto.resize || []).length, 0, 'la pagina segue la tastiera');
    """)


def test_every_visit_starts_from_an_empty_search() -> None:
    _run("""
      const c = new LauncherController(app, { incorporato: true });
      c.open();
      cerca.value = 'tel';
      c.close();
      c.open();
      assert.equal(cerca.value, '');
    """)


def test_the_workshop_sheet_is_still_a_sheet() -> None:
    """Senza `incorporato` e' il foglio di sempre: aperto e' aperto, e sotto la
    casa — qui l'officina — diventa inerte."""
    _run("""
      const c = new LauncherController(app);
      c.open();
      assert.equal(c.isOpen(), true);
      assert.equal(guscio.inert, true);
      assert.ok((testa.ascolto.pointerdown || []).length > 0, 'il foglio non si trascina piu');
      c.close();
      assert.equal(c.isOpen(), false);
      assert.equal(guscio.inert, false);
    """, con_foglio=True)
