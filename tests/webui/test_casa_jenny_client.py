"""«Jenny»: com'e' fatta — la taglia, se si vede, se sta sopra le altre app.

Due cose si misurano qui, e nessuna delle due si vedrebbe guardando lo schermo
una volta sola.

**`enabled` e `active` non sono la stessa cosa.** La finestra flottante vuole
``SYSTEM_ALERT_WINDOW``, che si concede da una schermata di sistema: `enabled`
e' quel che hai chiesto, `active` quel che Android ha concesso. A permesso
negato l'interruttore resta acceso e spiega. Se rimbalzasse su spento da solo,
chi l'ha toccato vedrebbe un interruttore che si rifiuta senza dire perche'.

**«Nascosta» vince sulla taglia.** Dire «media» di una mascotte che non si vede
e' vero e inutile; la finestra flottante invece si somma anche a quella, perche'
sono due posti diversi e lei puo' stare sopra le altre app mentre dentro la casa
non c'e'.

I membri si ritagliano dal sorgente e girano in node su un DOM finto.
``MASCOT_SIZES`` invece e' **vera**: le taglie che la casa offre e quelle che
l'arte ha devono essere le stesse, e ricopiarle qui vorrebbe dire misurare la
propria copia.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
JENNY_JS = ASSETS / "casa-jenny.js"
MASCOT_JS = ASSETS / "shared" / "mascot.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


def _const(source: str, name: str) -> str:
    """Una costante di modulo, su una riga o su molte."""
    m = re.search(
        rf"(?ms)^export const {re.escape(name)} = (?:\{{.*?^\}}|\[.*?^\]|.+?);$", source
    )
    assert m, f"const {name} non trovata"
    return m.group(0).removeprefix("export ")


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    hidden: false,
    dataset: {},
    attrs: {},
    children: [],
    listeners: {},
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
    appendChild(child) { el.children.push(child); return child; },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  return el;
}

const nodi = {};
const document = {
  createElement: (tag) => makeEl(tag),
  getElementById: (id) => (nodi[id] ||= makeEl('div')),
};

/* Le taglie sono vere; il resto delle preferenze no — stanno nel
   `localStorage`, che qui non c'e'. */
__SIZES__
let visibile = true;
let taglia = 'sm';
function mascotVisible() { return visibile; }
function setMascotVisible(on) { visibile = on; return on; }
function mascotSize() { return taglia; }
function setMascotSize(size) { taglia = size; return size; }

/* Quel che il server risponde all'interruttore, deciso dal banco. */
let risposta = null;
let errore = null;
const chiamate = [];
const api = {
  updateFloating(params) {
    chiamate.push(params);
    if (errore) return Promise.reject(errore);
    return Promise.resolve(risposta);
  },
};

__JENNY_VALUE__
__SIZE_LIST__
__SIZE_KEYS__

class CasaJenny {
  __CTOR__
  __OPEN__
  __SET_FLOATING__
  __APPLY_TRANSLATIONS__
  __VALUE__
  __TOGGLE_VISIBLE__
  __PICK_SIZE__
  __TOGGLE_FLOATING__
  __PAINT_SIZES__
  __MARK__
  __SWITCH__
  __SAY_FLOATING__
}

let cambi = 0;
function stanza(floating) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  visibile = true;
  taglia = 'sm';
  risposta = null;
  errore = null;
  chiamate.length = 0;
  cambi = 0;
  const lei = new CasaJenny({ onChange: () => { cambi += 1; } });
  lei.open();
  if (floating !== undefined) lei.setFloating(floating);
  return lei;
}
"""


def _harness() -> str:
    src = JENNY_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", _member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__SIZES__", _const(MASCOT_JS.read_text(encoding="utf-8"), "MASCOT_SIZES"))
        .replace("__JENNY_VALUE__", _function(src, "jennyValue"))
        .replace("__SIZE_LIST__", _const(src, "SIZES"))
        .replace("__SIZE_KEYS__", _const(src, "SIZE_KEYS"))
        .replace("__CTOR__", _member(src, "constructor"))
        .replace("__OPEN__", _member(src, "open"))
        .replace("__SET_FLOATING__", _member(src, "setFloating"))
        .replace("__APPLY_TRANSLATIONS__", _member(src, "applyTranslations"))
        .replace("__VALUE__", _member(src, "value"))
        .replace("__TOGGLE_VISIBLE__", _member(src, "toggleVisible"))
        .replace("__PICK_SIZE__", _member(src, "pickSize"))
        .replace("__TOGGLE_FLOATING__", _member(src, "toggleFloating"))
        .replace("__PAINT_SIZES__", _member(src, "_paintSizes"))
        .replace("__MARK__", _member(src, "_mark"))
        .replace("__SWITCH__", _member(src, "_switch"))
        .replace("__SAY_FLOATING__", _member(src, "_sayFloating"))
    )


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── La riga che si legge senza entrare ──────────────────────────────────────


def test_hidden_wins_over_the_size() -> None:
    """Dire «media» di una mascotte che non si vede e' vero e inutile."""
    _run_js("""
      assert.equal(jennyValue({ visible: true, size: 'md', floating: false }),
                   i18n.t('settings.mascotSizeMedium').toLowerCase());
      assert.equal(jennyValue({ visible: false, size: 'md', floating: false }),
                   i18n.t('casa.jenny.hidden').toLowerCase());
    """)


def test_the_window_adds_itself_even_to_a_hidden_one() -> None:
    """Sono due posti diversi: lei puo' stare sopra le altre app mentre dentro
    la casa non c'e'."""
    _run_js("""
      const con = jennyValue({ visible: false, size: 'sm', floating: true });
      assert.ok(con.includes(i18n.t('casa.jenny.hidden').toLowerCase()));
      assert.ok(con.includes(i18n.t('casa.jenny.floatingShort').toLowerCase()));
      assert.ok(con.includes('\\u00b7'), 'le due cose non sono separate: ' + con);

      const senza = jennyValue({ visible: true, size: 'sm', floating: false });
      assert.ok(!senza.includes('\\u00b7'), 'un separatore senza niente dopo: ' + senza);
    """)


def test_the_room_reads_the_preferences_that_are_live() -> None:
    """Il valore non e' una copia tenuta a mano: lo compone leggendo le stesse
    preferenze che disegnano lei."""
    _run_js("""
      const lei = stanza({ available: true, enabled: true, active: true });
      assert.equal(lei.value(), jennyValue({ visible: true, size: 'sm', floating: true }));
      lei.pickSize('lg');
      assert.equal(lei.value(), jennyValue({ visible: true, size: 'lg', floating: true }));
      lei.toggleVisible();
      assert.equal(lei.value(), jennyValue({ visible: false, size: 'lg', floating: true }));
      assert.ok(cambi >= 2, 'la riga di «Tu e Jenny» non viene avvisata');
    """)


# ── Le taglie ───────────────────────────────────────────────────────────────


def test_the_sizes_offered_are_the_ones_the_art_has() -> None:
    """Una taglia aggiunta in `shared/mascot.js` e non qui sarebbe una taglia
    che la casa non sa offrire — e una qui che la' non c'e' ricadrebbe sulla
    piccola senza dirlo."""
    _run_js("""
      assert.deepEqual(SIZES, Object.keys(MASCOT_SIZES));
      for (const size of SIZES) assert.ok(SIZE_KEYS[size], 'la taglia ' + size + ' non ha parola');
    """)


def test_picking_a_size_marks_exactly_one() -> None:
    _run_js("""
      const lei = stanza({ available: true });
      assert.equal(lei.sizeEl.children.length, SIZES.length);
      lei.pickSize('md');
      const accese = lei.sizeEl.children.filter((b) => b.classList.contains('is-on'));
      assert.equal(accese.length, 1);
      assert.equal(accese[0].dataset.size, 'md');
      for (const btn of lei.sizeEl.children) {
        assert.equal(btn.attrs['aria-checked'], String(btn.dataset.size === 'md'));
      }
    """)


# ── La finestra ─────────────────────────────────────────────────────────────


def test_the_row_is_not_there_where_the_window_cannot_exist() -> None:
    """Fuori da Android `available` e' falso: un interruttore che non fa niente
    e' peggio di una riga che manca."""
    _run_js("""
      const lei = stanza({ available: false, enabled: false });
      assert.equal(lei.floatingRow.hidden, true);
      assert.equal(lei.floatingNote.hidden, true, 'la spiegazione di una cosa che non c\\u2019e\\u2019');

      lei.setFloating({ available: true, enabled: false, active: false });
      assert.equal(lei.floatingRow.hidden, false);
      assert.equal(lei.floatingNote.textContent, i18n.t('settings.floatingHint'));
    """)


def test_a_window_android_refused_says_so_instead_of_bouncing_back() -> None:
    """A permesso negato la config resta accesa e il payload lo dice. Se
    l'interruttore tornasse indietro da solo, chi l'ha toccato lo vedrebbe
    rifiutarsi senza dire perche'."""
    _run_js("""
      const lei = stanza({ available: true, enabled: true, active: false });
      assert.equal(lei.floatingBtn.classList.contains('is-on'), true,
                   'l\\u2019interruttore e\\u2019 rimbalzato su spento');
      assert.equal(lei.floatingNote.textContent, i18n.t('settings.floatingBlocked'));
      assert.ok(lei.floatingNote.classList.contains('is-warn'),
                'la frase e\\u2019 una cosa da fare, e non si vede che lo e\\u2019');
    """)


def test_the_switch_moves_before_the_server_answers_and_takes_its_word_after() -> None:
    """Il giro passa da `store.mutate` e da un ponte verso Kotlin: un
    interruttore che aspetta mezzo secondo prima di muoversi sembra rotto. Ma
    la verita' resta quella del server — qui accende, e il server risponde che
    Android non l'ha lasciata aprire."""
    _run_js("""
      const lei = stanza({ available: true, enabled: false, active: false });
      risposta = { floating: { available: true, enabled: true, active: false } };
      const giro = lei.toggleFloating();
      assert.equal(lei.floatingBtn.classList.contains('is-on'), true, 'non si e\\u2019 mosso subito');
      await giro;
      assert.deepEqual(chiamate, [{ enabled: true }]);
      assert.equal(lei.floatingNote.textContent, i18n.t('settings.floatingBlocked'),
                   'la risposta del server non e\\u2019 stata ascoltata');
    """)


def test_a_call_that_failed_puts_the_switch_back() -> None:
    """Un interruttore acceso su una finestra che nessuno ha acceso sarebbe una
    bugia che dura fino al prossimo avvio."""
    _run_js("""
      const lei = stanza({ available: true, enabled: false, active: false });
      errore = new Error('gateway giu');
      await lei.toggleFloating();
      assert.equal(lei.floatingBtn.classList.contains('is-on'), false);
      assert.equal(lei.value(), jennyValue({ visible: true, size: 'sm', floating: false }));
    """)


def test_the_words_come_back_when_the_language_changes() -> None:
    _run_js("""
      const lei = stanza({ available: true, enabled: false, active: false });
      lei.visibleLabel.textContent = '';
      lei.sizeLabel.textContent = '';
      lei.floatingLabel.textContent = '';
      for (const btn of lei.sizeEl.children) btn.textContent = '';
      lei.applyTranslations();
      assert.equal(lei.visibleLabel.textContent, i18n.t('settings.mascotVisible'));
      assert.equal(lei.sizeLabel.textContent, i18n.t('settings.mascotSize'));
      assert.equal(lei.floatingLabel.textContent, i18n.t('settings.floatingEnabled'));
      for (const btn of lei.sizeEl.children) {
        assert.equal(btn.textContent, i18n.t(SIZE_KEYS[btn.dataset.size]));
      }
    """)
