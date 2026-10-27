"""La scheda della copia su Drive nella pagina «Backup» della casa.

La pagina vera del core (`home-backup.js`) e la scheda vera dell'estensione
(`jenny_extensions/drive/ui/home.js`) girano insieme con un DOM finto e i flussi
finti; le regole (`cloudView`, `nightStrip`, `dayText`, `sizeText`) sono quelle
vere di `cloud-backup.js`, perché è lì che si decide cosa dice la pagina.

Cosa si misura: per ogni stato, il titolo giusto e il gesto giusto sul bottone
grande; le righe dell'estensione che compaiono e spariscono fra le «Altre
opzioni»; quando le «Altre opzioni» partono aperte; la riga di «Tu e Jenny».
"""

from __future__ import annotations

import json
import re

from support.js_harness import function, member, requires_node, run_js

from .ui_paths import CORE_ASSETS, EXT_UI, locale

ROOM_JS = (CORE_ASSETS / "home-backup.js").read_text(encoding="utf-8")
DRIVE_JS = (EXT_UI / "home.js").read_text(encoding="utf-8")
CLOUD_JS = (EXT_UI / "cloud-backup.js").read_text(encoding="utf-8")
WHEN_JS = (CORE_ASSETS / "shared" / "when.js").read_text(encoding="utf-8")
I18N_JS = (CORE_ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")
IT = locale("it")

pytestmark = requires_node


def _class(source: str, name: str) -> str:
    start = source.index(f"export class {name}")
    end = source.find("\nexport ", start + 1)
    return source[start:end if end != -1 else None].removeprefix("export ")


def _harness() -> str:
    late = re.search(r"export const CLOUD_LATE_MS = ([^;]+);", CLOUD_JS).group(1)
    blocking = re.search(r"export const CLOUD_BLOCKING = (\[[^\]]+\]);", CLOUD_JS).group(1)
    pure = "\n".join(
        function(CLOUD_JS, name)
        for name in ("_startOfDay", "calendarDays", "reasonKey", "cloudView", "nightStrip",
                     "dayText", "sizeText", "progressSizes")
    )
    drive_value = function(DRIVE_JS, "backupValue").replace(
        "function backupValue(", "function driveBackupValue(")
    drive_class = _class(DRIVE_JS, "DriveBackupCard").replace(
        "return backupValue(backup, this.cloud, now)", "return driveBackupValue(backup, this.cloud, now)")
    return (
        """
import assert from 'node:assert/strict';
const i18n = { locale: 'it', translations: { it: __IT__ }, __T__ };
__WHEN__
const CLOUD_LATE_MS = __LATE__;
const CLOUD_BLOCKING = __BLOCKING__;
const DAY_MS = 86400000;
__PURE__

function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', innerHTML: '', hidden: false, disabled: false,
    attrs: {}, listeners: {}, offsetParent: null,
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(t, fn) { (el.listeners[t] ||= []).push(fn); },
  };
  return el;
}
const nodi = {};
const document = { getElementById: (id) => (nodi[id] ||= makeEl('div')) };
const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const toasts = [];
const showToast = (m, type) => toasts.push([m, type || 'info']);
const flows = [];
const CLOUD = { status: null, native: true, fileNative: true, exportOutcome: true, choose: { ok: true } };
const cloudStatus = () => (CLOUD.status ? Promise.resolve(CLOUD.status) : Promise.reject(new Error('offline')));
const cloudAction = async (n) => { flows.push(`api:${n}`); return { ok: true }; };
const api = { getSnapshotHistory: async () => ({ snapshots: [] }) };
async function runSnapshotRestore(id) { flows.push(`restore:${id}`); }
function cloudNativeAvailable() { return CLOUD.native; }
function backupNativeAvailable() { return CLOUD.fileNative; }
function cloudErrorText(code) { return `ERR:${code}`; }
async function runExportFlow() { flows.push('export'); return CLOUD.exportOutcome; }
async function runImportFlow() { flows.push('import'); return true; }
async function copyNow() { flows.push('copyNow'); return { ok: true }; }
async function giveConsentBack() { flows.push('giveBack'); return { ok: true }; }
async function unlockHere() { flows.push('unlock'); return { ok: true }; }
async function findMyCopy() { flows.push('findCopy'); return { ok: false, error: 'no_backup' }; }
async function choosePassword({ askPassword }) { flows.push(`choose:${askPassword}`); return CLOUD.choose; }
async function turnOnDrive() { flows.push('turnOn'); return { ok: false, error: 'existing_backup' }; }
/* Il dialogo vero non si apre qui: `askCloudPassword(mode)` diventa il nome del
   modo, cosi' il flusso finto dice quale domanda avrebbe fatto. */
const askCloudPassword = (mode) => mode;
async function restoreCopy() { return { ok: true }; }
async function pickCloudCopy() { return null; }
const CONFIRM = { answer: true, asked: [] };
async function confirmDialog(text) { flows.push('confirm'); CONFIRM.asked.push(text); return CONFIRM.answer; }
const POLL_MS = 1500;
const HISTORY_FIRST = 8;
__SNAPSHOT_WHEN__
const icon = (name) => `<i class="ti ti-${name}" aria-hidden="true"></i>`;
__ROW_HTML__
__BACKUP_VALUE__
__DRIVE_VALUE__
__CLASS__
__DRIVE_CLASS__

const H = 3600 * 1000;
const NOW = Date.now();
/* «d giorni fa» come lo conta la scheda: giorni di calendario del telefono,
   alla stessa ora. Non `NOW - d * 24 * H`: nella settimana dopo un cambio
   d'ora, per un'ora al giorno, due di quei salti cadono nello stesso giorno
   (quello di 25 ore) e uno ne manca. Le prove fallivano lì. */
const daysAgo = (d) => { const t = new Date(NOW); t.setDate(t.getDate() - d); return t.getTime(); };
const notices = [];
async function room(status, backup = null) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  toasts.length = 0; flows.length = 0; notices.length = 0;
  CLOUD.status = status; CLOUD.native = true; CLOUD.fileNative = true; CLOUD.exportOutcome = true;
  const page = new HomeBackup({ onExported: () => notices.push('riga') });
  const drive = new DriveBackupCard(page);
  page.attachExtension(drive);
  page.setBackup(backup);
  await drive.refreshCloud();
  return { page, drive };
}
const card = () => nodi['home-backup-ext'].innerHTML;
const rows = () => nodi['home-backup-ext-rows'].innerHTML;
const act = (drive, action) => drive._onAction({ target: { closest: () => ({ dataset: { action }, disabled: false }) } });
const T = (k, p) => i18n.t(k, p);
const on = (extra = {}) => ({ available: true, provider: 'drive', enabled_at_ms: daysAgo(30), password_set_ms: 1, ...extra });
"""
        .replace("__IT__", json.dumps(IT, ensure_ascii=False))
        .replace("__T__", member(I18N_JS, "t"))
        .replace("__WHEN__", function(WHEN_JS, "whenText"))
        .replace("__LATE__", late)
        .replace("__BLOCKING__", blocking)
        .replace("__PURE__", pure)
        .replace("__SNAPSHOT_WHEN__", function(ROOM_JS, "snapshotWhen"))
        .replace("__ROW_HTML__", function(ROOM_JS, "rowHtml"))
        .replace("__BACKUP_VALUE__", function(ROOM_JS, "backupValue"))
        .replace("__DRIVE_VALUE__", drive_value)
        .replace("__CLASS__", _class(ROOM_JS, "HomeBackup"))
        .replace("__DRIVE_CLASS__", drive_class)
    )


def _run(script: str) -> None:
    run_js(_harness() + "\n" + script)


def test_safe_says_it_and_answers_the_three_questions() -> None:
    _run("""
      const copies = [0, 1, 2, 3, 4, 5, 6].map(daysAgo);
      const { page } = await room(on({ last_success_ms: NOW - H, recent_copies_ms: copies,
                                       stored_bytes: 8_400_000, last_verified_ms: daysAgo(2) }));
      const html = card();
      assert.equal(nodi['home-backup-ext'].hidden, false);
      assert.ok(html.includes(T('cloudBackup.safe.title')), 'manca «Sei al sicuro»');
      assert.ok(html.includes('8,4 MB'), 'manca il peso');
      assert.ok(html.includes(T('cloudBackup.facts.whereValue').slice(0, 20)), 'manca il dove');
      assert.equal((html.match(/home-cloud-dot is-ok/g) || []).length, 7, 'sette pallini pieni');
      assert.ok(html.includes('data-action="copyNow"'));
      assert.ok(html.includes(T('cloudBackup.promise')), 'manca la promessa');
      assert.equal(nodi['home-backup-more'].hidden, true, 'altre opzioni aperte da sole');
      assert.equal(nodi['home-backup-more-toggle'].hidden, false);
      assert.ok(!html.includes('data-action="setPassword"'), 'chiede una password gia scelta');
      assert.ok(!nodi['home-backup-more-toggle'].innerHTML.includes('<small>'),
        'la riga chiusa di «Altre opzioni» ha un sottotitolo');
      assert.ok(page.value().startsWith('al sicuro · oggi alle '), page.value());
      assert.deepEqual(notices.slice(-1), ['riga']);
    """)


def test_late_says_how_long_why_and_what_to_do() -> None:
    _run("""
      const copies = [4, 5, 6].map(daysAgo);
      const { page } = await room(on({ last_success_ms: daysAgo(4), recent_copies_ms: copies }));
      const html = card();
      assert.ok(html.includes(T('cloudBackup.late.title', { days: 4 })), html.slice(0, 300));
      assert.ok(html.includes(T('cloudBackup.reasons.waiting.todo')), 'manca cosa fare');
      assert.ok(html.includes('home-cloud-dot is-miss'));
      assert.ok(html.includes('data-action="copyNow"'));
      assert.ok(html.includes(T('cloudBackup.late.calm')));
      assert.ok(!html.includes(T('cloudBackup.late.calmAlerted')), 'dice «te l ho scritto» senza averlo fatto');
      assert.equal(page.value(), T('cloudBackup.row.late', { days: 4 }));
    """)


def test_late_mentions_the_chat_only_when_it_was_sent() -> None:
    _run("""
      await room(on({ last_success_ms: daysAgo(4), alerted_ms: NOW - H }));
      assert.ok(card().includes(T('cloudBackup.late.calmAlerted')));
    """)


def test_a_revoked_permission_is_red_and_offers_the_fix() -> None:
    _run("""
      const { page } = await room(on({ last_success_ms: NOW - 26 * H, last_failure_ms: NOW - H,
                                       last_error: 'consent_needed' }));
      const html = card();
      assert.ok(html.includes('home-cloud-badge is-err'));
      assert.ok(html.includes(T('cloudBackup.blocked.title')));
      assert.ok(html.includes('data-action="giveBack"'));
      assert.equal(page.value(), T('cloudBackup.row.consent'));
    """)


def test_a_missing_key_asks_for_the_password() -> None:
    _run("""
      const { drive } = await room(on({ last_success_ms: NOW - 26 * H, last_failure_ms: NOW - H, last_error: 'missing_key' }));
      assert.ok(card().includes('data-action="unlock"'));
      assert.ok(card().includes(T('cloudBackup.actions.unlock')));
      await act(drive, 'unlock');
      assert.ok(flows.includes('unlock'));
    """)


def test_running_shows_the_bar_and_no_button() -> None:
    _run("""
      const { page } = await room(on({ running: true, progress: { done_bytes: 5_100_000, total_bytes: 8_400_000 } }));
      const html = card();
      assert.ok(html.includes(T('cloudBackup.running.titleFirst')), 'prima copia');
      assert.ok(html.includes('5,1 MB di 8,4 MB'), html.slice(0, 400));
      assert.ok(html.includes('aria-valuenow="61"'));
      assert.ok(!html.includes('data-action='), 'un bottone durante la copia');
      assert.equal(page.value(), T('cloudBackup.row.running'));
      await room(on({ running: true, progress: { done_bytes: 0, total_bytes: 20_000_000 } }));
      assert.ok(card().includes('0 MB di 20 MB'), 'lo zero si scrive zero, nella stessa unita');
    """)


def test_running_polls_only_while_the_room_is_visible() -> None:
    _run("""
      const { page, drive } = await room(on({ running: true }));
      assert.equal(drive._pollTimer, null, 'interroga a stanza chiusa');
      nodi['home-backup-room'].offsetParent = {};
      page.refresh();
      assert.notEqual(drive._pollTimer, null, 'non interroga a stanza aperta');
      CLOUD.status = on({ running: false, last_success_ms: NOW });
      await drive.refreshCloud();
      assert.equal(drive._pollTimer, null, 'continua a copia finita');
    """)


def test_off_explains_the_risk_and_offers_to_turn_on() -> None:
    _run("""
      const { page } = await room({ available: true, provider: '' });
      const html = card();
      assert.ok(html.includes(T('cloudBackup.off.title')));
      assert.ok(html.includes('data-action="turnOn"'));
      assert.ok(!rows().includes('home-cloud-off'), 'spegni una copia gia spenta');
      assert.equal(page.value(), T('cloudBackup.row.onlyHere'));
    """)


def test_a_drive_with_another_phones_copy_offers_to_find_it() -> None:
    _run("""
      const { drive } = await room({ available: true, provider: '' });
      await act(drive, 'turnOn');
      assert.deepEqual(flows.slice(0, 1), ['turnOn']);
      assert.ok(card().includes('data-action="findCopy"'));
      assert.ok(card().includes(T('cloudBackup.errors.existing_backup').slice(0, 25)));
    """)


def test_without_google_the_file_is_the_way_and_is_open() -> None:
    _run("""
      await room({ available: false, provider: '' });
      assert.ok(card().includes(T('cloudBackup.unavailable.title')));
      assert.ok(!card().includes('data-action='));
      assert.equal(nodi['home-backup-more'].hidden, false, 'altre opzioni chiuse senza Google');
      assert.equal(nodi['home-backup-export'].hidden, false);
      /* Con l'estensione il file non e' l'unica strada: la frase lo dice. */
      assert.ok(nodi['home-backup-export'].innerHTML.includes(T('home.backup.more.fileSub')));
      assert.ok(T('home.backup.more.fileSub').includes('Google'), 'la frase del core non e stata corretta');
    """)


def test_outside_the_shell_the_page_is_the_core_one() -> None:
    """In un browser (Tailscale) il consenso non esiste: niente scheda, e le
    righe tornano la pagina, aperte e senza «Altre opzioni»."""
    _run("""
      const { page } = await room(on({ last_success_ms: NOW }));
      CLOUD.native = false;
      page.refresh();
      assert.equal(nodi['home-backup-ext'].hidden, true);
      assert.equal(nodi['home-backup-more-toggle'].hidden, true);
      assert.equal(nodi['home-backup-more'].hidden, false);
      assert.equal(rows(), '', 'righe che promettono un gesto che non c e');
    """)


def test_a_copy_without_a_password_says_so_and_offers_to_choose() -> None:
    """Le copie notturne vanno, ma un telefono nuovo non la riaprirebbe: la
    scheda lo dice in giallo, col gesto. Con la password non c'e' niente da dire."""
    _run("""
      const { page, drive } = await room(on({ last_success_ms: NOW, has_key: true, password_set_ms: 0 }));
      assert.ok(card().includes(T('cloudBackup.nopass.title')));
      assert.ok(card().includes('data-action="setPassword"'));
      assert.ok(card().includes('home-cloud-badge is-warn'));
      assert.equal(page.value(), T('cloudBackup.row.nopass'));
      await act(drive, 'setPassword');
      assert.ok(flows.includes('choose:choose'), 'senza password si sceglie, non si cambia');
      assert.ok(toasts.some(([m]) => m === T('cloudBackup.password.choose.done')));

      await room(on({ last_success_ms: NOW, has_key: true, password_set_ms: 5 }));
      assert.ok(!card().includes(T('cloudBackup.nopass.title')));
      assert.ok(!card().includes('data-action="setPassword"'));
    """)


def test_the_password_lives_in_more_options_and_changes_without_the_old_one() -> None:
    _run("""
      const { drive } = await room(on({ last_success_ms: NOW, has_key: true, password_set_ms: 5 }));
      assert.ok(rows().includes('id="home-cloud-password-row"'));
      assert.ok(rows().includes('data-action="setPassword"'));
      assert.ok(rows().includes(T('cloudBackup.more.password')));
      await act(drive, 'setPassword');
      assert.ok(flows.includes('choose:change'), 'con la password gia scelta si cambia');
      assert.ok(toasts.some(([m]) => m === T('cloudBackup.password.change.done')));

      /* Annullare non dice niente; un errore vero si'. */
      CLOUD.choose = { ok: false, error: 'cancelled' };
      toasts.length = 0;
      await act(drive, 'setPassword');
      assert.deepEqual(toasts, []);
      CLOUD.choose = { ok: true };

      await room({ available: true, provider: '' });
      assert.ok(!rows().includes('home-cloud-password-row'), 'password di una copia spenta');
      await room(on({ last_success_ms: NOW, has_key: false, last_failure_ms: NOW + 1, last_error: 'missing_key' }));
      assert.ok(!rows().includes('home-cloud-password-row'), 'cambiarla senza la chiave di qui');
      assert.ok(rows().includes('home-cloud-off'));
    """)


def test_a_damaged_copy_is_not_safe() -> None:
    _run("""
      const { page } = await room(on({ last_success_ms: NOW - 2 * H, last_failure_ms: NOW - H,
                                       last_error: 'verify_mismatch' }));
      const html = card();
      assert.ok(html.includes(T('cloudBackup.damaged.title')));
      assert.ok(!html.includes(T('cloudBackup.safe.title')));
      assert.ok(html.includes(T('cloudBackup.reasons.verify_mismatch.why')));
      assert.ok(html.includes('data-action="copyNow"'));
      assert.equal(page.value(), T('cloudBackup.row.damaged'));
    """)


def test_more_options_toggle_and_the_file_date() -> None:
    _run("""
      const { page } = await room(on({ last_success_ms: NOW }), { last_export_at: 0, snapshots_enabled: true });
      assert.equal(nodi['home-backup-more'].hidden, true);
      page._toggleMore();
      assert.equal(nodi['home-backup-more'].hidden, false);
      assert.equal(nodi['home-backup-more-toggle'].attrs['aria-expanded'], 'true');
      assert.ok(!nodi['home-backup-export'].innerHTML.includes('Ultimo file'));

      CLOUD.exportOutcome = false;
      await page.runExport();
      assert.ok(!nodi['home-backup-export'].innerHTML.includes('Ultimo file'), 'si muove su un annullamento');
      CLOUD.exportOutcome = true;
      await page.runExport();
      assert.ok(nodi['home-backup-export'].innerHTML.includes('Ultimo file: oggi alle'));
    """)


def test_turning_off_asks_first() -> None:
    _run("""
      const { drive } = await room(on({ last_success_ms: NOW }));
      await act(drive, 'turnOff');
      assert.deepEqual(flows.slice(0, 2), ['confirm', 'api:disable']);
      CONFIRM.answer = false;
      flows.length = 0;
      await act(drive, 'turnOff');
      assert.deepEqual(flows, ['confirm'], 'spenta senza il si');
    """)


def test_a_second_tap_while_working_does_nothing() -> None:
    _run("""
      const { drive } = await room(on({ last_success_ms: NOW }));
      const button = { dataset: { action: 'copyNow' }, disabled: false };
      const first = drive._onAction({ target: { closest: () => button } });
      await act(drive, 'copyNow');
      await first;
      assert.equal(flows.filter((f) => f === 'copyNow').length, 1);
      assert.equal(button.disabled, false, 'il bottone resta spento');
    """)


def test_the_local_history_names_the_copys_snapshots() -> None:
    """Lo snapshot che la copia fa prima di partire ha un nome suo nella storia
    locale: la traduzione arriva con l'estensione."""
    assert IT["backup"]["trigger"]["pre_cloud"]
    assert locale("en")["backup"]["trigger"]["pre_cloud"]


def test_mounting_registers_the_password_dialog_and_reads_the_state() -> None:
    mount = function(DRIVE_JS, "mountHome")
    _run(f"""
      const CLOUD_PASSWORD_DIALOG_ID = 'oc-cloud-password-dialog';
      {mount}
      for (const k of Object.keys(nodi)) delete nodi[k];
      CLOUD.status = on({{ last_success_ms: NOW }});
      const page = new HomeBackup({{}});
      const registered = [];
      mountHome({{ homeBackup: page, registerSharedDialog: (id) => registered.push(id) }});
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(registered, ['oc-cloud-password-dialog']);
      assert.ok(page.value().startsWith('al sicuro'), page.value());
      assert.equal(nodi['home-backup-ext'].hidden, false);
    """)
