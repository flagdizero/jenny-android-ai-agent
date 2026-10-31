"""«Backup» in casa: la data che prima non esisteva, due cose che si somigliano,
e il gancio per un'estensione.

Il giro — passphrase, cifratura, i due picker SAF, il riavvio — sta in
`shared/backup-flow.js` e lo usano gia' l'officina e l'onboarding. Qui si
misura solo cio' che e' di questa stanza:

**«Mai fatto» si dice.** E' l'informazione piu' utile che quella riga possa
portare, ed e' anche l'unico momento in cui serve davvero leggerla: chi un
backup lo fa ogni settimana non ha bisogno che glielo si ricordi.

**La riga si muove solo su un export riuscito.** Fra il container cifrato e il
file su disco c'e' una schermata di sistema che si puo' annullare: scrivere
«ultimo backup: adesso» dopo un annullamento sarebbe la bugia peggiore di
questa pagina.

**La storia locale non e' un backup**, e somiglia abbastanza da essere
scambiata per uno: vive sullo stesso telefono, quindi di un telefono perso non
salva niente. La sua riga lo dice; e aperta si sfoglia e si ripristina qui,
senza passare dall'officina.

**Un'estensione si aggancia, il core non la conosce.** Senza estensioni (la
build ``foss``) le righe sono la pagina, tutte aperte, e «Altre opzioni» non
c'e'. Con una scheda sopra le righe scendono sotto «Altre opzioni», chiuse. La
copia su Drive vera si prova in ``tests/extensions/drive/``.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
ROOM_JS = ASSETS / "home-backup.js"
WHEN_JS = ASSETS / "shared" / "when.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', hidden: false, disabled: false,
    attrs: {}, children: [], listeners: {},
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(t, fn) { (el.listeners[t] ||= []).push(fn); },
    appendChild(c) { el.children.push(c); return c; },
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
const document = { createElement: (t) => makeEl(t), getElementById: (id) => (nodi[id] ||= makeEl('div')) };

/* Il flusso condiviso, finto: quel che conta qui e' l'esito, non il giro. */
let exportOutcome = true;
let importOutcome = true;
let nativePresent = true;
const actions = [];
function runExportFlow() { actions.push('export'); return Promise.resolve(exportOutcome); }
function runImportFlow() { actions.push('import'); return Promise.resolve(importOutcome); }
function backupNativeAvailable() { return nativePresent; }
const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const icon = (name) => `<i class="ti ti-${name}" aria-hidden="true"></i>`;
const HISTORY_FIRST = 8;
const DAY_MS = 86400000;
const SNAPS = { list: [] };
const api = {
  getSnapshotHistory: async () => {
    actions.push('api:history');
    if (!SNAPS.list) throw new Error('offline');
    return { snapshots: SNAPS.list };
  },
};
const CONFIRM = { answer: true, asked: [] };
async function confirmDialog(text) { CONFIRM.asked.push(text); return CONFIRM.answer; }
async function runSnapshotRestore(id) { actions.push(`restore:${id}`); }
__SNAPSHOT_WHEN__

__WHEN_TEXT__
__BACKUP_VALUE__
__ROW_HTML__

class HomeBackup {
  __CTOR__
  __ATTACH__
  __REFRESH__
  __SET_BACKUP__
  __OPEN__
  __VALUE__
  __APPLY_TRANSLATIONS__
  __RUN_EXPORT__
  __RUN_IMPORT__
  __TOGGLE_HISTORY__
  __HISTORY_CLICK__
  __HISTORY_HTML__
  __TOGGLE_MORE__
  __IS_MORE_OPEN__
  __PAINT__
}

const notices = [];
const day = 86400000;
function room(backup) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  exportOutcome = true;
  importOutcome = true;
  nativePresent = true;
  actions.length = 0;
  notices.length = 0;
  const s = new HomeBackup({ onExported: () => notices.push('riga riscritta') });
  s.setBackup(backup);
  return s;
}
"""


def _harness() -> str:
    room = ROOM_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__WHEN_TEXT__", function(WHEN_JS.read_text(encoding="utf-8"), "whenText"))
        .replace("__BACKUP_VALUE__", function(room, "backupValue"))
        .replace("__ROW_HTML__", function(room, "rowHtml"))
        .replace("__CTOR__", member(room, "constructor"))
        .replace("__ATTACH__", member(room, "attachExtension"))
        .replace("__REFRESH__", member(room, "refresh"))
        .replace("__TOGGLE_MORE__", member(room, "_toggleMore"))
        .replace("__IS_MORE_OPEN__", member(room, "_isMoreOpen"))
        .replace("__SET_BACKUP__", member(room, "setBackup"))
        .replace("__OPEN__", member(room, "open"))
        .replace("__VALUE__", member(room, "value"))
        .replace("__APPLY_TRANSLATIONS__", member(room, "applyTranslations"))
        .replace("__RUN_EXPORT__", member(room, "runExport"))
        .replace("__RUN_IMPORT__", member(room, "runImport"))
        .replace("__TOGGLE_HISTORY__", member(room, "_toggleHistory"))
        .replace("__HISTORY_CLICK__", member(room, "_onHistoryClick"))
        .replace("__HISTORY_HTML__", member(room, "_historyHtml"))
        .replace("__SNAPSHOT_WHEN__", function(room, "snapshotWhen"))
        .replace("__PAINT__", member(room, "_paint"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


def test_never_having_made_one_is_said_out_loud() -> None:
    """E' l'informazione piu' utile che quella riga possa portare, ed e' anche
    l'unico momento in cui serve leggerla."""
    _run_js("""
      assert.equal(backupValue(null), i18n.t('home.backup.never'));
      assert.equal(backupValue({ last_export_at: 0 }), i18n.t('home.backup.never'));

      /* Nella riga del file, senza un file fatto, nessuna data inventata. */
      room(null);
      const row = nodi['home-backup-export'].innerHTML;
      assert.ok(row.includes(i18n.t('home.backup.more.file')));
      const last = i18n.t('home.backup.more.fileLast', { when: '@@' }).split('@@')[0];
      assert.ok(!row.includes(last), 'la riga del file dice una data che non esiste');
    """)


def test_the_row_says_when_like_a_person_would() -> None:
    """La stessa frase dell'ultimo controllo aggiornamenti: e' la stessa
    domanda, e la risposta la scrive un posto solo (`shared/when.js`)."""
    _run_js("""
      const now = Date.now() / 1000;
      assert.ok(backupValue({ last_export_at: now }).startsWith('oggi alle '),
        backupValue({ last_export_at: now }));

      /* I secondi epoch del config diventano millisecondi: sbagliare la
         conversione darebbe «gennaio 1970», che a schermo sembra un guasto. */
      const threeDays = (Date.now() - 3 * day) / 1000;
      assert.ok(!backupValue({ last_export_at: threeDays }).includes('1970'),
        backupValue({ last_export_at: threeDays }));

      room({ last_export_at: now });
      assert.ok(nodi['home-backup-export'].innerHTML.includes('oggi alle '),
        nodi['home-backup-export'].innerHTML);
    """)


def test_a_cancelled_export_does_not_move_the_row() -> None:
    """Fra il container cifrato e il file su disco c'e' una schermata di
    sistema che si puo' annullare."""
    _run_js("""
      const s = room(null);
      exportOutcome = false;
      await s.runExport();

      assert.deepEqual(actions, ['export']);
      assert.equal(s.value(), i18n.t('home.backup.never'), 'la riga si e mossa su un annullamento');
      assert.deepEqual(notices, [], 'ha avvisato «Tu e Jenny» di un backup che non c e');
      assert.equal(nodi['home-backup-export'].disabled, false, 'il bottone e rimasto spento');
    """)


def test_a_finished_export_moves_the_row_at_once() -> None:
    """Senza, la data comparirebbe solo alla prossima apertura della pagina —
    cioe' proprio dopo il gesto con cui l'hai fatta."""
    _run_js("""
      const s = room(null);
      await s.runExport();

      assert.notEqual(s.value(), i18n.t('home.backup.never'));
      assert.ok(s.value().startsWith('oggi alle '), s.value());
      assert.deepEqual(notices, ['riga riscritta']);
    """)


def test_a_second_tap_while_exporting_does_nothing() -> None:
    """Il flusso condiviso ha gia' la sua mutua esclusione; qui il bottone non
    deve nemmeno sembrare premibile, o si preme due volte e si aprono due
    dialoghi della passphrase."""
    _run_js("""
      const s = room(null);
      const first = s.runExport();               // non atteso: e' ancora in volo
      assert.equal(nodi['home-backup-export'].disabled, true, 'si puo premere di nuovo');
      await s.runExport();                       // il secondo tocco
      await first;
      assert.deepEqual(actions, ['export'], 'due giri di export insieme: ' + actions.join(','));
      assert.equal(nodi['home-backup-export'].disabled, false, 'il bottone e rimasto spento');
    """)


def test_without_the_native_bridge_the_rows_are_not_there() -> None:
    """Fuori dall'APK i due picker non esistono, e una riga che non fa niente
    e' peggio di una riga che manca. La storia locale resta: non li usa."""
    _run_js("""
      for (const k of Object.keys(nodi)) delete nodi[k];
      nativePresent = false;
      const s = new HomeBackup({});
      s.setBackup(null);
      assert.equal(nodi['home-backup-export'].hidden, true);
      assert.equal(nodi['home-backup-import'].hidden, true);
      assert.equal(nodi['home-backup-history'].hidden, false);
    """)


def test_without_an_extension_the_rows_are_the_page() -> None:
    """La build senza estensioni: niente scheda sopra, quindi niente «Altre
    opzioni» — le righe sono aperte e il bottone per aprirle non c'e'."""
    _run_js("""
      const s = room(null);
      assert.equal(nodi['home-backup-ext'].hidden, true);
      assert.equal(nodi['home-backup-more-toggle'].hidden, true);
      assert.equal(nodi['home-backup-more'].hidden, false);
      assert.equal(nodi['home-backup-ext-rows'].innerHTML, '');
      assert.equal(s.value(), i18n.t('home.backup.never'));
    """)


_EXTENSION = """
const ext = {
  shown: true, openByDefault: false, opened: 0,
  paintCard(el) { if (this.shown) el.innerHTML = '<p>scheda</p>'; return this.shown; },
  paintRows(el) { el.innerHTML = '<button>spegni</button>'; },
  value() { return 'al sicuro'; },
  moreOpenByDefault() { return this.openByDefault; },
  open() { this.opened += 1; },
};
"""


def test_an_extension_card_closes_the_rows_under_more_options() -> None:
    """Con una scheda sopra le righe sono «altre»: chiuse, dietro un tocco. La
    riga di «Tu e Jenny» la dice l'estensione, e le sue righe stanno in fondo."""
    _run_js(_EXTENSION + """
      const s = room(null);
      s.attachExtension(ext);
      assert.equal(nodi['home-backup-ext'].hidden, false);
      assert.equal(nodi['home-backup-ext'].innerHTML, '<p>scheda</p>');
      assert.equal(nodi['home-backup-more-toggle'].hidden, false);
      assert.equal(nodi['home-backup-more'].hidden, true, 'le altre opzioni partono aperte');
      assert.equal(nodi['home-backup-more-toggle'].attrs['aria-expanded'], 'false');
      assert.equal(nodi['home-backup-ext-rows'].innerHTML, '<button>spegni</button>');
      assert.equal(s.value(), 'al sicuro');
      assert.deepEqual(notices, ['riga riscritta'], 'la riga di «Tu e Jenny» non si riscrive');

      for (const fn of nodi['home-backup-more-toggle'].listeners.click) fn();
      assert.equal(nodi['home-backup-more'].hidden, false);

      s.open();
      assert.equal(ext.opened, 1, 'la stanza aperta non avvisa l estensione');
    """)


def test_an_extension_can_keep_more_options_open() -> None:
    """Quando la sua scheda non offre un gesto (qui la copia non si puo' fare),
    il file e' la strada, e non deve stare dietro un tocco in piu'. E senza
    scheda le righe tornano la pagina."""
    _run_js(_EXTENSION + """
      const s = room(null);
      ext.openByDefault = true;
      s.attachExtension(ext);
      assert.equal(nodi['home-backup-more'].hidden, false);

      ext.shown = false;
      s.refresh();
      assert.equal(nodi['home-backup-ext'].hidden, true);
      assert.equal(nodi['home-backup-more-toggle'].hidden, true);
      assert.equal(nodi['home-backup-more'].hidden, false);
    """)


def test_the_local_history_is_told_apart_from_a_backup() -> None:
    """Somiglia abbastanza da essere scambiata per un backup: e' automatica, e
    rimette a posto una cosa cancellata per sbaglio. Ma vive su questo
    telefono, quindi di un telefono perso non salva niente."""
    _run_js("""
      room({ snapshots_enabled: true });
      const active = nodi['home-backup-history'].innerHTML;
      assert.ok(active.includes(i18n.t('home.backup.history.title')));
      assert.ok(active.includes(i18n.t('home.backup.history.sub')));
      assert.ok(i18n.t('home.backup.history.sub').length > 40, 'la frase non spiega niente');

      room({ snapshots_enabled: false });
      assert.ok(nodi['home-backup-history'].innerHTML.includes(i18n.t('home.backup.history.off')));
      assert.notEqual(i18n.t('home.backup.history.sub'), i18n.t('home.backup.history.off'),
        'spenta e accesa si leggono uguali');
    """)


_TAP = """
const tap = (dataset) => ({ target: { closest: () => ({ dataset }) } });
const click = async (id, ev) => { for (const fn of nodi[id].listeners.click || []) await fn(ev); };
const H = 3600 * 1000;
const NOW = Date.now();
const snap = (i, extra = {}) => ({
  id: `s${i}`, created_at_ms: NOW - i * H, trigger: 'auto', label: '', file_count: 500 + i, ...extra,
});
"""


def test_local_history_opens_here_and_restores_after_a_confirmation() -> None:
    """Non porta in officina: l'elenco si apre sotto la riga, e un tocco su uno
    snapshot chiede conferma e poi fa lo stesso ripristino dell'officina."""
    _run_js(_TAP + """
      SNAPS.list = [snap(1), snap(2, { trigger: 'pre_gardener' }), snap(3, { trigger: 'boh', label: 'prima del trasloco' })];
      room({ snapshots_enabled: true });
      const list = nodi['home-backup-history-list'];
      assert.equal(list.hidden, true, 'storia aperta da sola');
      await click('home-backup-history');
      assert.equal(list.hidden, false);
      assert.equal(nodi['home-backup-history'].attrs['aria-expanded'], 'true');
      assert.equal((list.innerHTML.match(/data-snapshot=/g) || []).length, 3);
      assert.ok(list.innerHTML.includes(i18n.t('backup.trigger.pre_gardener')), 'giardiniere senza nome');
      assert.ok(list.innerHTML.includes('boh · prima del trasloco'), 'motivo sconosciuto o etichetta persi');
      assert.ok(list.innerHTML.includes(`501 ${i18n.t('backup.files')}`));

      await click('home-backup-history-list', tap({ snapshot: 's2', when: 'oggi alle 9:40' }));
      assert.deepEqual(CONFIRM.asked, [i18n.t('backup.snapshotRestoreConfirm', { date: 'oggi alle 9:40' })]);
      assert.ok(actions.includes('restore:s2'));

      CONFIRM.answer = false;
      await click('home-backup-history-list', tap({ snapshot: 's3', when: 'ieri' }));
      assert.ok(!actions.includes('restore:s3'), 'ripristina senza il si');

      await click('home-backup-history');
      assert.equal(list.hidden, true, 'il secondo tocco non la richiude');
    """)


def test_local_history_shows_eight_then_the_others() -> None:
    _run_js(_TAP + """
      SNAPS.list = Array.from({ length: 11 }, (_, i) => snap(i + 1));
      room({ snapshots_enabled: true });
      await click('home-backup-history');
      const list = nodi['home-backup-history-list'];
      assert.equal((list.innerHTML.match(/data-snapshot=/g) || []).length, 8);
      assert.ok(list.innerHTML.includes(i18n.t('home.backup.history.showAll', { n: 3 })));
      await click('home-backup-history-list', tap({ historyAll: '' }));
      assert.equal((list.innerHTML.match(/data-snapshot=/g) || []).length, 11);
      assert.ok(!actions.some((a) => a.startsWith('restore:')), 'mostra gli altri ha ripristinato');
    """)


def test_local_history_says_when_it_is_empty_or_unreadable() -> None:
    _run_js(_TAP + """
      SNAPS.list = [];
      room({ snapshots_enabled: true });
      await click('home-backup-history');
      assert.ok(nodi['home-backup-history-list'].innerHTML.includes(i18n.t('backup.snapshotHistoryEmpty')));
      SNAPS.list = null;
      room({ snapshots_enabled: false });
      await click('home-backup-history');
      assert.ok(nodi['home-backup-history-list'].innerHTML.includes(i18n.t('backup.snapshotHistoryUnavailable')));
    """)


def test_snapshot_dates_stay_apart_past_a_week() -> None:
    """Oltre la settimana «3 ott» non basta: due snapshot dello stesso giorno
    devono restare distinguibili, quindi c'e' anche l'ora."""
    _run_js(_TAP + """
      assert.ok(snapshotWhen(NOW - 60000, NOW, 'it').startsWith('oggi alle '));
      const old = snapshotWhen(NOW - 20 * 24 * H, NOW, 'it');
      assert.match(old, /\\d{1,2}:\\d{2}/, old);
    """)


