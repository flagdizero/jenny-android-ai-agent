"""Il chip si monta una volta sola, e disegna anche su un index incompleto.

Due difetti d'igiene, nessuno dei due raggiungibile oggi (`_initSessions` chiama
`init()` una volta sola, e l'index ha i due span): il punto è che i fratelli di
questo modulo si difendono da entrambi e lui no.

1. `init()` non aveva un latch (`sessionManager._initialized`,
   `ChatController._wsListenersBound` ce l'hanno). I due listener su `document`
   sono chiusure anonime, quindi non rimovibili: una seconda `init` li lascerebbe
   iscritti due volte, e ogni tap fuori chiuderebbe la tendina due volte.
2. `render()` dereferenziava `.scope-chip-mark` e `.scope-chip-path` senza
   guardia, dove `WriteSwitch.render()` guarda entrambi. Un `null` lì non
   toglierebbe un'icona: solleverebbe, e con lei se ne andrebbe il resto del
   disegno — `syncPlaceholder()` compreso, cioè la riga che dice dove sta
   andando il prossimo messaggio.

I metodi si estraggono dal sorgente e girano in node, come in
``test_scope_pin_and_create_client.py``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
STATE_JS = ASSETS / "shared" / "state.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _source() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get |static )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^const {re.escape(name)} = .*;$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0)


_HARNESS = """
import assert from 'node:assert/strict';

// Costanti e helper del modulo, dal sorgente: riscriverli qui vorrebbe dire
// misurare una copia.
__NAME_IN_PLACEHOLDER__
__SHORT__

/* Il costruttore vero monta un `ConversationList`, che e' dove sono finiti i
   campi dell'elenco: si importa quello, non se ne fa una sagoma. La rete non
   viene mai toccata da questi test — `init` aggancia e basta — ma la fetch va
   passata lo stesso, perche' e' il costruttore vero a chiederla. */
const { ConversationList } = await import('__LIST_URL__');
const api = { listProjects: () => Promise.resolve({}) };

const i18n = {
  t: (key, vars) => 'i18n:' + key + (vars ? ':' + Object.values(vars).join(',') : ''),
};

const AppState = {
  readonlyTurn: false,
  composeMenu: null,
  subs: [],
  on(key, fn) { this.subs.push([key, fn]); },
  set(key, value) { AppState[key] = value; },
};
/* «Una sola tendina aperta» passa dallo stesso canale, e per questo test è
   un'iscrizione come le altre: quel che si misura qui è che `init` non ne
   registri due. Il comportamento sta in `test_compose_menus_client.py`.
   `armComposeMenu` è quella vera di `state.js`, dal sorgente. */
function claimComposeMenu(id) { AppState.set('composeMenu', id); }
function onOtherComposeMenu(id, close) {
  AppState.on('composeMenu', (who) => { if (who !== id) close(); });
}
__ARM_COMPOSE_MENU__

/* Un elemento ridotto a quel che `render()` e `init()` toccano. `inner` decide
   quali figli esistono: `null` è il caso dell'index a cui manca lo span. */
function makeEl(id, inner = {}) {
  return {
    id,
    dataset: {},
    attrs: {},
    listeners: 0,
    children: [],
    // `innerHTML = ''` svuota davvero, come nel DOM vero: `render()` conta su
    // quello per non impilare due volte i crumb.
    get innerHTML() { return ''; },
    set innerHTML(value) { if (!value) this.children.length = 0; },
    classList: { add() {}, remove() {} },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener() { this.listeners++; },
    appendChild(node) { this.children.push(node); },
    querySelector(sel) { return sel in inner ? inner[sel] : null; },
  };
}

function makeSpan() {
  return { className: '', textContent: '' };
}

let chipEl = null;
let menuEl = null;
let inputEl = null;
let docListeners = 0;

const document = {
  getElementById(id) {
    if (id === 'scope-chip') return chipEl;
    if (id === 'scope-menu') return menuEl;
    if (id === 'chat-input') return inputEl;
    return null;
  },
  addEventListener() { docListeners++; },
  createElement() { return makeSpan(); },
};

class ScopeChip {
  __CTOR__
  __DIR__
  __INIT__
  __PERSONAL_LABEL__
  __PATH_SEGMENTS__
  __RENDER__
  __SYNC_PLACEHOLDER__
  // Non sono oggetto di questi test: `init` li aggancia, non li esegue.
  toggle() {}
  close() {}
}

/* `complete = false` = l'index senza i due span dentro il chip. */
function mount(complete = true) {
  const mark = makeSpan();
  const path = makeEl('path');
  chipEl = makeEl('scope-chip', complete
    ? { '.scope-chip-mark': mark, '.scope-chip-path': path }
    : {});
  chipEl.mark = mark;
  chipEl.path = path;
  menuEl = makeEl('scope-menu');
  inputEl = { placeholder: '' };
  docListeners = 0;
  AppState.subs.length = 0;
  AppState.readonlyTurn = false;
  return new ScopeChip();
}
"""


def _harness() -> str:
    src = _source()
    return (
        _HARNESS.replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__NAME_IN_PLACEHOLDER__", _const(src, "NAME_IN_PLACEHOLDER"))
        .replace("__DIR__", _member(src, "_dir"))
        .replace("__SHORT__", _function(src, "_short"))
        .replace("__CTOR__", _member(src, "constructor"))
        .replace("__INIT__", _member(src, "init"))
        .replace("__PERSONAL_LABEL__", _member(src, "personalLabel"))
        .replace("__PATH_SEGMENTS__", _member(src, "pathSegments"))
        .replace("__RENDER__", _member(src, "render"))
        .replace("__SYNC_PLACEHOLDER__", _member(src, "syncPlaceholder"))
        .replace(
            "__ARM_COMPOSE_MENU__",
            _function(STATE_JS.read_text(encoding="utf-8").replace("export function", "function"),
                      "armComposeMenu"),
        )
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── 1. Un solo montaggio ──────────────────────────────────────────────────


def test_a_second_init_registers_nothing() -> None:
    """I due listener su `document` sono chiusure anonime: non si smontano."""
    _run_js("""
      const chip = mount();
      chip.init();
      const after = {
        doc: docListeners, chip: chipEl.listeners, menu: menuEl.listeners,
        state: AppState.subs.length,
      };
      assert.equal(after.doc, 2, 'click fuori ed Escape: due, non di più');

      chip.init();
      chip.init();
      assert.equal(docListeners, after.doc,
                   'ogni tap fuori chiuderebbe la tendina una volta per init');
      assert.equal(chipEl.listeners, after.chip);
      assert.equal(menuEl.listeners, after.menu);
      assert.equal(AppState.subs.length, after.state);
    """)


def test_the_latch_does_not_swallow_the_first_init() -> None:
    """Il latch è una difesa, non un cambio: la prima `init` fa tutto il suo."""
    _run_js("""
      const chip = mount();
      chip.init();
      assert.equal(chip._initialized, true);
      assert.equal(docListeners, 2);
      assert.deepEqual(AppState.subs.map(([key]) => key), ['composeMenu', 'readonlyTurn']);
      // E `init` disegna: il chip nomina la personale già prima di ogni rete.
      assert.equal(chipEl.dataset.scope, 'personal');
      assert.equal(inputEl.placeholder, 'i18n:chat.placeholder');
    """)


def test_a_chip_without_its_block_does_not_mount_at_all() -> None:
    """Chat e onboarding condividono l'index: senza il blocco il modulo tace."""
    _run_js("""
      mount();
      chipEl = null;
      const chip = new ScopeChip();
      assert.equal(chip.enabled, false);
      chip.init();
      assert.equal(docListeners, 0);
      assert.equal(chip._initialized, false, 'un modulo spento non deve nemmeno armare il latch');
    """)


# ── 2. Il disegno non cade su un figlio che non c'è ───────────────────────


def test_the_drawing_survives_a_missing_span() -> None:
    """La parte che conta è l'ultima riga: il placeholder dice dove va il messaggio."""
    _run_js("""
      const chip = mount(false);         // nessun `.scope-chip-mark`, nessun `.scope-chip-path`
      chip.scope = { kind: 'project', name: 'bordi' };
      chip.render();                     // non solleva
      assert.equal(chipEl.dataset.scope, 'project');
      assert.equal(chipEl.attrs['aria-label'], 'i18n:scope.change');
      assert.equal(inputEl.placeholder, 'i18n:scope.askAbout:bordi',
                   'il disegno è caduto prima del placeholder');
    """)


def test_the_drawing_is_unchanged_when_the_spans_are_there() -> None:
    """Le guardie non devono costare il disegno vero."""
    _run_js("""
      const chip = mount();
      chip.scope = { kind: 'project', name: 'bordi' };
      chip.render();
      assert.equal(chipEl.mark.className, 'scope-chip-mark ti ti-folder');
      assert.equal(chipEl.mark.textContent, '');
      // `wikis › bordi`: due crumb e il separatore in mezzo.
      assert.deepEqual(chipEl.path.children.map((n) => n.textContent),
                       ['wikis', '›', 'bordi']);

      chip.scope = { kind: 'personal', name: null };
      chip.render();
      assert.equal(chipEl.mark.textContent, '✿');
      assert.equal(chipEl.mark.className, 'scope-chip-mark');
      assert.deepEqual(chipEl.path.children.map((n) => n.textContent), ['i18n:scope.personal']);
    """)
