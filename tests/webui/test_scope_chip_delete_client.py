"""Il tasto per eliminare un progetto: visibile, e non sulla riga sbagliata.

La issue #11 nasce da «ho creato un progetto per sbaglio e non trovavo come si
cancella». La prima risposta era un long-press — cioè un altro comando
invisibile, che è la stessa forma del difetto. Ora la riga di un progetto porta
un cestino che si vede.

Due cose che questi test tengono ferme, e che a occhio non si distinguono:

- il tasto sta **solo** sui progetti. Sulla conversazione personale, su «nuovo
  progetto...» e sulle cartelle che non si aprono non c'è niente da cancellare,
  e un cestino lì sarebbe un bersaglio distruttivo senza oggetto;
- il click **non** arriva alla riga sotto. Entrare nel progetto un istante prima
  di chiedere se cancellarlo cambierebbe conversazione sotto la finestra di
  conferma — e la conferma resta, è `deleteProjectFlow` a farla.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import locale, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"
CSS = ASSETS / "mobile-style.css"


pytestmark = requires_node


def _chip() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


_HARNESS = """
import assert from 'node:assert/strict';

const { ConversationList } = await import('__LIST_URL__');

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = {
  locale: 'it',
  translations: TRANSLATIONS,
  __T__
};

function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', children: [], dataset: {}, attrs: {},
    handlers: {},
    appendChild(child) { el.children.push(child); return child; },
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { el.handlers[type] = fn; },
  };
  el.classList = { add(c) { el.className = (el.className + ' ' + c).trim(); }, remove() {} };
  return el;
}
const document = { createElement: (tag) => makeEl(tag) };

function byClass(el, cls) {
  const out = [];
  for (const child of el.children) {
    if (String(child.className).split(/\\s+/).includes(cls)) out.push(child);
    out.push(...byClass(child, cls));
  }
  return out;
}

/* Il flusso vero (domanda di conferma inclusa) sta in `project-delete.js`: qui
   è un doppio che dice sì o no, perché quel che si misura è chi lo chiama e con
   che nome. */
let deleteAnswer = true;
const deleted = [];
async function deleteProjectFlow(name) { deleted.push(name); return deleteAnswer; }
const toasts = [];
function showToast(text) { toasts.push(text); }

class Chip {
  constructor() {
    this.scope = { kind: 'personal', name: null };
    /* La cache dell'elenco sta in `conversation-list.js`: qui si importa
       quello, cosi' «l'elenco e' stato buttato» lo dice il codice vero. */
    this._list = new ConversationList(() => Promise.resolve({}));
    this.closed = 0;
    this.left = [];
    this.reloaded = 0;
  }
  close() { this.closed++; }
  leaveIfSelected(name) {
    const mine = this.scope.kind === 'project' && this.scope.name === name;
    if (mine) this.left.push(name);
    return mine;
  }
  async _loadProjects() { this.reloaded++; }
  __PROJECTS__
  __PROJECT_ROW__
}

/* Una riga finta al posto del bottone vero: `_projectRow` la avvolge e basta. */
function row(chip, name) {
  const item = makeEl('button');
  item.className = 'scope-menu-item';
  item.handlers.click = () => { item.entered = true; };
  return chip._projectRow(item, name);
}
"""


def _harness() -> str:
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": locale("it")}))
        .replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__PROJECTS__", member(_chip(), "_projects"))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__PROJECT_ROW__", member(_chip(), "_projectRow"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Il tasto c'è, e si vede ──────────────────────────────────────────────────


def test_a_project_row_carries_a_delete_button() -> None:
    it = locale("it")
    expected = it["scope"]["deleteProject"].replace("{name}", "patreon")
    _run_js(f"""
      const chip = new Chip();
      const r = row(chip, 'patreon');
      const del = byClass(r, 'scope-menu-del');
      assert.equal(del.length, 1, 'la riga non ha un tasto elimina');
      assert.equal(del[0].tag, 'button');
      assert.equal(del[0].attrs['aria-label'], {json.dumps(expected)},
                   'il tasto non si presenta a chi non vede l-icona');
      assert.equal(del[0].children[0].className, 'ti ti-trash');
    """)


def test_it_is_not_behind_a_hover() -> None:
    """Grep, non comportamento: su un touchscreen `:hover` non scatta mai.

    L'explorer del workspace aveva un `⋮` scritto così — `display: none` più
    `.ws-item:hover .ws-item-menu { display: flex }` — quindi presente nel
    markup e invisibile su un telefono. Lì la risposta è stata toglierlo e
    tenere la pressione lunga (una griglia fitta, un'icona per cella); qui il
    tasto c'è, e allora deve vedersi davvero.
    """
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"^\.scope-menu-del \{(.*?)^\}", css, re.S | re.M)
    assert rule, "regola .scope-menu-del non trovata"
    assert "display: none" not in rule.group(1)
    assert not re.search(r":hover\s+\.scope-menu-del", css), (
        "il tasto non deve dipendere da un hover"
    )


# ── Chi tocca cosa ──────────────────────────────────────────────────────────


def test_the_click_does_not_reach_the_row_underneath() -> None:
    """Entrare nel progetto mentre si chiede se cancellarlo cambia chat sotto la finestra."""
    _run_js("""
      const chip = new Chip();
      const r = row(chip, 'patreon');
      const del = byClass(r, 'scope-menu-del')[0];
      let stopped = false;
      await del.handlers.click({ stopPropagation: () => { stopped = true; } });
      assert.equal(stopped, true, 'il click scende alla riga e apre il progetto');
      assert.deepEqual(deleted, ['patreon']);
    """)


def test_a_refusal_changes_nothing() -> None:
    """«Annulla» sulla conferma: nessun toast, nessuna ricarica, nessuna uscita."""
    _run_js("""
      const chip = new Chip();
      deleteAnswer = false;
      const del = byClass(row(chip, 'patreon'), 'scope-menu-del')[0];
      await del.handlers.click({ stopPropagation() {} });
      assert.deepEqual(toasts, []);
      assert.equal(chip.reloaded, 0);
      assert.deepEqual(chip.left, []);
    """)


def test_deleting_the_open_project_leaves_its_scope() -> None:
    """Era la conversazione aperta: il chip deve smettere di nominarla."""
    _run_js("""
      const chip = new Chip();
      chip.scope = { kind: 'project', name: 'patreon' };
      const del = byClass(row(chip, 'patreon'), 'scope-menu-del')[0];
      await del.handlers.click({ stopPropagation() {} });
      assert.deepEqual(chip.left, ['patreon']);
      // Non serve ricaricare l'elenco: uscire dallo scope lo invalida già.
      assert.equal(chip.reloaded, 0);
    """)


def test_deleting_another_project_reloads_the_list() -> None:
    """Non era lo scope aperto: nessun cambio di chat, ma l'elenco è vecchio."""
    _run_js("""
      const chip = new Chip();
      chip.scope = { kind: 'project', name: 'altro' };
      const del = byClass(row(chip, 'patreon'), 'scope-menu-del')[0];
      await del.handlers.click({ stopPropagation() {} });
      assert.deepEqual(chip.left, []);
      assert.equal(chip.reloaded, 1, 'l-elenco continua a nominare un progetto che non c-è più');
      assert.equal(chip._projects, null);
      assert.equal(toasts.length, 1);
    """)


# ── E il gesto invisibile non c'è più ───────────────────────────────────────


def test_the_long_press_is_gone() -> None:
    """Grep: un gesto che non si vede non è una risposta a «non trovavo come si fa».

    Con esso se ne va anche la guardia sul tap sintetico che lo seguiva: senza
    pressione lunga nessuno posa più `dataset.longpress`, e un `if` che non può
    essere vero è solo una cosa in più da capire.
    """
    src = _chip()
    assert "setupLongPress" not in src
    assert "longpress" not in src
