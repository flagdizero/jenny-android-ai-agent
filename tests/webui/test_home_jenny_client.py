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
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
JENNY_JS = ASSETS / "home-jenny.js"
MASCOT_JS = ASSETS / "shared" / "mascot.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


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
    value: '',
    placeholder: '',
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
let isVisible = true;
let currentSize = 'sm';
function mascotVisible() { return isVisible; }
function setMascotVisible(on) { isVisible = on; return on; }
function mascotSize() { return currentSize; }
function setMascotSize(size) { currentSize = size; return size; }

/* Quel che il server risponde all'interruttore, deciso dal banco. */
let reply = null;
let error = null;
const calls = [];
/* E quel che c'e' su disco: `null` = il file non c'e' (404), una stringa = c'e'.
   `brokenRead` e' l'altra cosa: la risposta non e' arrivata affatto. */
let onDisk = null;
let brokenRead = false;
const saves = [];
let brokenSave = false;
const toasts = [];
const savedNames = [];
let brokenName = false;
const api = {
  updateSettings(params) {
    savedNames.push(params);
    return brokenName
      ? Promise.reject(new Error('rifiutato'))
      : Promise.resolve({});
  },
  updateFloating(params) {
    calls.push(params);
    if (error) return Promise.reject(error);
    return Promise.resolve(reply);
  },
  readWorkspaceFile(path) {
    if (brokenRead) {
      const err = new Error('gateway giu');
      err.status = 500;
      return Promise.reject(err);
    }
    if (onDisk === null) {
      const err = new Error('not found');
      err.status = 404;
      return Promise.reject(err);
    }
    return Promise.resolve({ content: onDisk, path });
  },
};
const rpc = {
  writeSoulRules(content) {
    saves.push(content);
    return brokenSave ? Promise.reject(new Error('rifiutato')) : Promise.resolve({});
  },
};
function showToast(msg, type) { toasts.push([msg, type]); }

__JENNY_VALUE__
__SIZE_LIST__
__SIZE_KEYS__
__RULES_PATH__

class HomeJenny {
  __CTOR__
  __OPEN__
  __SET_NAME__
  __MARK_NAME__
  __SAVE_NAME__
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
  __LOAD_RULES__
  __MARK_RULES__
  __SAVE_RULES__
}

let changes = 0;
/* Cosa la stanza ha detto alla casa della finestra flottante (v. `onFloating`). */
const floatingCalls = [];
/* I nomi che la stanza ha detto al guscio dopo un salvataggio. */
const spokenNames = [];
/* `disk` e' quel che il file delle regole contiene: `undefined` = non c'e'
   (404). `broken` e' l'altro caso, quello che conta: la lettura non e' arrivata
   affatto. Si passano alla costruzione perche' la stanza legge all'apertura. */
function room(floating, disk, broken) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  isVisible = true;
  currentSize = 'sm';
  reply = null;
  error = null;
  calls.length = 0;
  onDisk = disk === undefined ? null : disk;
  brokenRead = !!broken;
  saves.length = 0;
  brokenSave = false;
  savedNames.length = 0;
  brokenName = false;
  toasts.length = 0;
  changes = 0;
  floatingCalls.length = 0;
  spokenNames.length = 0;
  const she = new HomeJenny({
    onChange: () => { changes += 1; },
    onFloating: (f) => { floatingCalls.push(f); },
    onName: (n) => { spokenNames.push(n); },
  });
  she.open();
  if (floating !== undefined) she.setFloating(floating);
  return she;
}
"""


def _harness() -> str:
    src = JENNY_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__SIZES__", _const(MASCOT_JS.read_text(encoding="utf-8"), "MASCOT_SIZES"))
        .replace("__JENNY_VALUE__", function(src, "jennyValue"))
        .replace("__SIZE_LIST__", _const(src, "SIZES"))
        .replace("__SIZE_KEYS__", _const(src, "SIZE_KEYS"))
        .replace("__CTOR__", member(src, "constructor"))
        .replace("__OPEN__", member(src, "open"))
        .replace("__SET_NAME__", member(src, "setName"))
        .replace("__MARK_NAME__", member(src, "_markName"))
        .replace("__SAVE_NAME__", member(src, "saveName"))
        .replace("__SET_FLOATING__", member(src, "setFloating"))
        .replace("__APPLY_TRANSLATIONS__", member(src, "applyTranslations"))
        .replace("__VALUE__", member(src, "value"))
        .replace("__TOGGLE_VISIBLE__", member(src, "toggleVisible"))
        .replace("__PICK_SIZE__", member(src, "pickSize"))
        .replace("__TOGGLE_FLOATING__", member(src, "toggleFloating"))
        .replace("__PAINT_SIZES__", member(src, "_paintSizes"))
        .replace("__MARK__", member(src, "_mark"))
        .replace("__SWITCH__", member(src, "_switch"))
        .replace("__SAY_FLOATING__", member(src, "_sayFloating"))
        .replace("__LOAD_RULES__", member(src, "_loadRules"))
        .replace("__MARK_RULES__", member(src, "_markRules"))
        .replace("__SAVE_RULES__", member(src, "saveRules"))
        .replace("__RULES_PATH__", _const(src, "RULES_PATH"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── La riga che si legge senza entrare ──────────────────────────────────────


def test_hidden_wins_over_the_size() -> None:
    """Dire «media» di una mascotte che non si vede e' vero e inutile."""
    _run_js("""
      assert.equal(jennyValue({ visible: true, size: 'md', floating: false }),
                   i18n.t('settings.mascotSizeMedium').toLowerCase());
      assert.equal(jennyValue({ visible: false, size: 'md', floating: false }),
                   i18n.t('home.jenny.hidden').toLowerCase());
    """)


def test_the_window_adds_itself_even_to_a_hidden_one() -> None:
    """Sono due posti diversi: lei puo' stare sopra le altre app mentre dentro
    la casa non c'e'."""
    _run_js("""
      const con = jennyValue({ visible: false, size: 'sm', floating: true });
      assert.ok(con.includes(i18n.t('home.jenny.hidden').toLowerCase()));
      assert.ok(con.includes(i18n.t('home.jenny.floatingShort').toLowerCase()));
      assert.ok(con.includes('\\u00b7'), 'le due cose non sono separate: ' + con);

      const without = jennyValue({ visible: true, size: 'sm', floating: false });
      assert.ok(!without.includes('\\u00b7'), 'un separatore senza niente dopo: ' + without);
    """)


def test_the_room_reads_the_preferences_that_are_live() -> None:
    """Il valore non e' una copia tenuta a mano: lo compone leggendo le stesse
    preferenze che disegnano lei."""
    _run_js("""
      const she = room({ available: true, enabled: true, active: true });
      assert.equal(she.value(), jennyValue({ visible: true, size: 'sm', floating: true }));
      she.pickSize('lg');
      assert.equal(she.value(), jennyValue({ visible: true, size: 'lg', floating: true }));
      she.toggleVisible();
      assert.equal(she.value(), jennyValue({ visible: false, size: 'lg', floating: true }));
      assert.ok(changes >= 2, 'la riga di «Tu e Jenny» non viene avvisata');
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
      const she = room({ available: true });
      assert.equal(she.sizeEl.children.length, SIZES.length);
      she.pickSize('md');
      const active = she.sizeEl.children.filter((b) => b.classList.contains('is-on'));
      assert.equal(active.length, 1);
      assert.equal(active[0].dataset.size, 'md');
      for (const btn of she.sizeEl.children) {
        assert.equal(btn.attrs['aria-checked'], String(btn.dataset.size === 'md'));
      }
    """)


# ── La finestra ─────────────────────────────────────────────────────────────


def test_the_row_is_not_there_where_the_window_cannot_exist() -> None:
    """Fuori da Android `available` e' falso: un interruttore che non fa niente
    e' peggio di una riga che manca."""
    _run_js("""
      const she = room({ available: false, enabled: false });
      assert.equal(she.floatingRow.hidden, true);
      assert.equal(she.floatingNote.hidden, true, 'la spiegazione di una cosa che non c\\u2019e\\u2019');

      she.setFloating({ available: true, enabled: false, active: false });
      assert.equal(she.floatingRow.hidden, false);
      assert.equal(she.floatingNote.textContent, i18n.t('settings.floatingHint'));
    """)


def test_a_window_android_refused_says_so_instead_of_bouncing_back() -> None:
    """A permesso negato la config resta accesa e il payload lo dice. Se
    l'interruttore tornasse indietro da solo, chi l'ha toccato lo vedrebbe
    rifiutarsi senza dire perche'."""
    _run_js("""
      const she = room({ available: true, enabled: true, active: false });
      assert.equal(she.floatingBtn.classList.contains('is-on'), true,
                   'l\\u2019interruttore e\\u2019 rimbalzato su spento');
      assert.equal(she.floatingNote.textContent, i18n.t('settings.floatingBlocked'));
      assert.ok(she.floatingNote.classList.contains('is-warn'),
                'la frase e\\u2019 una cosa da fare, e non si vede che lo e\\u2019');
    """)


def test_the_switch_moves_before_the_server_answers_and_takes_its_word_after() -> None:
    """Il giro passa da `store.mutate` e da un ponte verso Kotlin: un
    interruttore che aspetta mezzo secondo prima di muoversi sembra rotto. Ma
    la verita' resta quella del server — qui accende, e il server risponde che
    Android non l'ha lasciata aprire."""
    _run_js("""
      const she = room({ available: true, enabled: false, active: false });
      /* Il server risponde il **contrario** dell'ipotesi: il permesso c'e' e
         la finestra e' su. Con una risposta uguale a quel che la stanza aveva
         gia' indovinato, buttarla via non si vedrebbe da nessuna parte. */
      reply = { floating: { available: true, enabled: true, active: true } };
      const tick = she.toggleFloating();
      assert.equal(she.floatingBtn.classList.contains('is-on'), true, 'non si e\\u2019 mosso subito');
      assert.equal(she.floatingNote.textContent, i18n.t('settings.floatingBlocked'),
                   'prima della risposta la stanza sa solo quel che sapeva');
      await tick;
      assert.deepEqual(calls, [{ enabled: true }]);
      assert.equal(she.floating.active, true, 'il permesso concesso non e arrivato');
      assert.equal(she.floatingNote.textContent, i18n.t('settings.floatingHint'),
                   'la risposta del server non e\\u2019 stata ascoltata');
    """)


def test_a_call_that_failed_puts_the_switch_back() -> None:
    """Un interruttore acceso su una finestra che nessuno ha acceso sarebbe una
    bugia che dura fino al prossimo avvio."""
    _run_js("""
      const she = room({ available: true, enabled: false, active: false });
      error = new Error('gateway giu');
      await she.toggleFloating();
      assert.equal(she.floatingBtn.classList.contains('is-on'), false);
      assert.equal(she.value(), jennyValue({ visible: true, size: 'sm', floating: false }));
    """)


def test_the_house_hears_what_the_switch_ended_up_as() -> None:
    """La casa tiene in cache il payload di `/api/settings`, e a ogni apertura
    delle Impostazioni lo ripassa a `setFloating`. Se nessuno le dice com'e'
    finito l'interruttore, rimette quello letto la prima volta: acceso da qui,
    alla riapertura si ridisegnava spento con la finestra accesa (visto sul
    telefono il 25/09). Si dice lo stato finale — quello del server, o quello
    rimesso a posto se la chiamata e' fallita — non l'ipotesi ottimista."""
    _run_js("""
      const she = room({ available: true, enabled: false, active: false });
      reply = { floating: { available: true, enabled: true, active: true } };
      await she.toggleFloating();
      assert.deepEqual(floatingCalls.at(-1), { available: true, enabled: true, active: true });

      const lui = room({ available: true, enabled: false, active: false });
      error = new Error('gateway giu');
      await lui.toggleFloating();
      assert.equal(floatingCalls.at(-1).enabled, false, 'una chiamata fallita non ha acceso niente');
    """)


def test_the_words_come_back_when_the_language_changes() -> None:
    _run_js("""
      const she = room({ available: true, enabled: false, active: false });
      she.visibleLabel.textContent = '';
      she.sizeLabel.textContent = '';
      she.floatingLabel.textContent = '';
      for (const btn of she.sizeEl.children) btn.textContent = '';
      she.applyTranslations();
      assert.equal(she.visibleLabel.textContent, i18n.t('settings.mascotVisible'));
      assert.equal(she.sizeLabel.textContent, i18n.t('settings.mascotSize'));
      assert.equal(she.floatingLabel.textContent, i18n.t('settings.floatingEnabled'));
      for (const btn of she.sizeEl.children) {
        assert.equal(btn.textContent, i18n.t(SIZE_KEYS[btn.dataset.size]));
      }
    """)


# ── Le regole che le hai dato tu ────────────────────────────────────────────


def test_what_is_on_disk_lands_in_the_box() -> None:
    """E «Salva» non c'e' finche' non c'e' niente da salvare: un bottone acceso
    su un campo che nessuno ha toccato invita a toccarlo per vedere cosa fa."""
    _run_js("""
      const she = room({ available: false }, 'Chiamami per nome.\\n');
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(she.rulesEl.value, 'Chiamami per nome.');
      assert.equal(she.rulesSave.hidden, true);

      she.rulesEl.value = 'Chiamami per nome. Niente emoji.';
      she._markRules();
      assert.equal(she.rulesSave.hidden, false);
    """)


def test_no_rules_yet_is_not_an_error() -> None:
    """404 vuol dire «non ne ha ancora scritte», ed e' lo stato normale del
    primo giorno."""
    _run_js("""
      const she = room({ available: false });
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(she.rulesEl.value, '');
      assert.equal(she.rulesSave.hidden, true);
      assert.deepEqual(toasts, [], 'ha detto che qualcosa non andava');

      she.rulesEl.value = 'Dammi del tu.';
      she._markRules();
      assert.equal(she.rulesSave.hidden, false, 'le prime regole non si possono salvare');
    """)


def test_a_reading_that_failed_cannot_wipe_what_is_there() -> None:
    """La differenza che conta fra «non ce n'erano» e «non si e' riuscito a
    leggerle»: nel secondo caso il campo e' vuoto ma **non** e' la verita', e
    uno spazio battuto per sbaglio manderebbe una casella vuota sopra le regole
    che ci sono. «Salva» resta via finche' non si sa cosa c'e'."""
    _run_js("""
      const she = room({ available: false }, undefined, true);
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(she.rulesEl.value, '');
      she.rulesEl.value = ' ';
      she._markRules();
      assert.equal(she.rulesSave.hidden, true, 'si puo\\u2019 salvare sopra quel che non si e\\u2019 letto');
    """)


def test_saving_sends_the_trimmed_text_and_remembers_it() -> None:
    _run_js("""
      const she = room({ available: false }, 'Chiamami per nome.');
      await new Promise((r) => setTimeout(r, 0));
      she.rulesEl.value = '  Dammi del tu.  ';
      she._markRules();
      await she.saveRules();
      assert.deepEqual(saves, ['Dammi del tu.']);
      assert.equal(she.rulesSave.hidden, true, '«Salva» e\\u2019 rimasto dopo aver salvato');
      assert.equal(toasts.length, 1);
      assert.equal(toasts[0][0], i18n.t('home.jenny.rulesSaved'));
    """)


def test_a_save_that_failed_keeps_the_button_and_says_so() -> None:
    """Sparire il bottone dopo un salvataggio fallito vorrebbe dire dire che e'
    andata bene."""
    _run_js("""
      const she = room({ available: false }, 'Chiamami per nome.');
      await new Promise((r) => setTimeout(r, 0));
      she.rulesEl.value = 'Dammi del tu.';
      she._markRules();
      brokenSave = true;
      await she.saveRules();
      assert.equal(she.rulesSave.hidden, false);
      assert.equal(toasts[0][0], i18n.t('home.jenny.rulesFailed'));
      assert.equal(toasts[0][1], 'error');
    """)


def test_reopening_the_room_does_not_overwrite_what_you_are_writing() -> None:
    """Stessa regola della bozza della chat, un attimo piu' tardi: il testo
    vivo vince sempre su quello vecchio."""
    _run_js("""
      const she = room({ available: false }, 'Chiamami per nome.');
      await new Promise((r) => setTimeout(r, 0));
      she.rulesEl.value = 'Sto ancora scrivendo';
      she.open();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(she.rulesEl.value, 'Sto ancora scrivendo');
    """)


def test_the_path_is_the_one_the_server_writes() -> None:
    """La casa legge il file; a scriverlo e' un comando, perche' salvarlo vuol
    dire anche rifare la copia dentro `SOUL.md`. Le due meta' devono guardare
    lo stesso posto."""
    _run_js("""
      assert.equal(RULES_PATH, '.jenny/soul_rules.md');
    """)


# ── Come si chiama ──────────────────────────────────────────────────────────
#
# Il nome era l'unica voce della «Personalizzazione» dell'officina che in casa
# non esistesse gia'. Il 21/09/2026 quel gruppo e' sparito e il nome e' venuto
# qui: senza, si sarebbe potuto scegliere soltanto al primo avvio.


def test_the_save_button_only_shows_when_there_is_something_to_save() -> None:
    """Un «Salva» sempre acceso su un campo che nessuno ha toccato invita a
    toccarlo per vedere cosa fa — e un nome vuoto non e' qualcosa da salvare:
    il server ripiegherebbe su «Jenny» senza dirlo."""
    _run_js("""
      const she = room();
      assert.equal(nodi['home-name-save'].hidden, true, 'nascosto finche\u2019 non si sa il nome');

      she.setName('Ada');
      assert.equal(nodi['home-name'].value, 'Ada', 'il campo non porta il nome del server');
      assert.equal(nodi['home-name-save'].hidden, true, 'niente da salvare: e\u2019 lo stesso nome');

      nodi['home-name'].value = 'Ada Lovelace';
      nodi['home-name'].listeners.input[0]();
      assert.equal(nodi['home-name-save'].hidden, false, 'il nome e\u2019 cambiato e non si puo\u2019 salvare');

      nodi['home-name'].value = '   ';
      nodi['home-name'].listeners.input[0]();
      assert.equal(nodi['home-name-save'].hidden, true, 'un nome vuoto non si salva');
    """)


def test_a_name_being_typed_is_never_overwritten() -> None:
    """Stesso patto delle regole: la risposta del server arriva quando arriva,
    e non deve mai riscrivere quel che la persona sta scrivendo."""
    _run_js("""
      const she = room();
      nodi['home-name'].value = 'Vera';
      she.setName('Jenny');
      assert.equal(nodi['home-name'].value, 'Vera', 'la risposta ha scritto sopra');
    """)


def test_saving_the_name_goes_through_the_settings_call() -> None:
    """La stessa chiamata con cui la casa salva il modello: `updateSettings`.
    Il server la gestisce gia\u2019, e un secondo percorso di scrittura per un
    campo solo sarebbe un secondo posto da tenere allineato."""
    _run_js("""
      const she = room();
      she.setName('Jenny');
      nodi['home-name'].value = 'Ada';
      await she.saveName();
      assert.deepEqual(savedNames, [{ bot_name: 'Ada' }]);
      assert.equal(nodi['home-name-save'].hidden, true, 'salvato, e il bottone resta li\u2019');
    """)


def test_a_refused_save_says_so_and_keeps_the_button() -> None:
    """Se il salvataggio non e\u2019 andato, dirlo e lasciare il bottone: un
    bottone che sparisce dopo un errore racconta che il nome e\u2019 cambiato."""
    _run_js("""
      const she = room();
      she.setName('Jenny');
      nodi['home-name'].value = 'Ada';
      nodi['home-name'].listeners.input[0]();
      brokenName = true;
      await she.saveName();
      assert.equal(nodi['home-name-save'].hidden, false, 'il bottone e\u2019 sparito su un errore');
      assert.equal(toasts.length, 1, 'l\u2019errore non l\u2019ha detto');
      assert.equal(toasts[0][1], 'error');
    """)


def test_a_saved_name_is_told_to_the_shell() -> None:
    """La fila e i Quaderni scrivono il nome di lei: il guscio deve saperlo
    appena e' salvato, o direbbero il nome vecchio fino al riavvio. Un
    salvataggio rifiutato non dice niente."""
    _run_js("""
      const she = room();
      she.setName('Jenny');
      nodi['home-name'].value = 'Ada';
      brokenName = true;
      await she.saveName();
      assert.deepEqual(spokenNames, [], 'un nome non salvato e\u2019 arrivato al guscio');
      brokenName = false;
      await she.saveName();
      assert.deepEqual(spokenNames, ['Ada']);
    """)


def test_a_name_that_could_not_be_read_is_not_an_empty_name() -> None:
    """Lettura fallita: `null` e' «non lo so». Con `''` al suo posto «Salva»
    compariva al primo tasto, confrontando quel che scrivi con un nome vuoto
    che nessuno ha mai scelto."""
    _run_js("""
      const she = room();
      she.setName(null);
      nodi['home-name'].value = 'Ada';
      nodi['home-name'].listeners.input[0]();
      assert.equal(nodi['home-name-save'].hidden, true, 'si salva un nome confrontato col nulla');
      she.setName('Jenny');
      assert.equal(nodi['home-name'].value, 'Ada', 'la risposta ha scritto sopra');
      assert.equal(nodi['home-name-save'].hidden, false);
    """)
