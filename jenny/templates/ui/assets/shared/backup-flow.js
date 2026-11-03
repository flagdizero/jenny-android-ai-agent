/** Flusso condiviso di backup/ripristino — usato da Settings e Onboarding.
 *
 * Il lato nativo (MainActivity) espone su window.JennyNative:
 *   exportBackup(stagedPath, suggestedName) → picker SAF CreateDocument
 *   importBackup()                          → picker SAF OpenDocument
 *   restartApp()                            → riavvio completo del processo
 * e risponde in modo asincrono chiamando window.jennyBackup.onExportDone(ok)
 * / onImportPicked(ok) via evaluateJavascript.
 */

import { api } from './api-client.js';
import { i18n } from './i18n.js';
import { showToast, sizeText } from './utils.js';
import { formDialog } from './dialog.js';

// ── Callback dal nativo ─────────────────────────────────────────────────
const _pending = { export: null, import: null };

// Mutua esclusione dei flussi: gli slot _pending e lo stato nativo
// (pendingExportPath, import.jbk in staging) sono singleton — un doppio tap
// avvierebbe due flussi che si rubano i callback e i file a vicenda.
let _busy = false;
async function _exclusive(fn) {
  if (_busy) return false;
  _busy = true;
  try { return await fn(); } finally { _busy = false; }
}

window.jennyBackup = {
  onExportDone(ok) {
    const cb = _pending.export; _pending.export = null;
    if (cb) cb(!!ok);
  },
  onImportPicked(ok) {
    const cb = _pending.import; _pending.import = null;
    if (cb) cb(!!ok);
  },
};

/* Quanto si aspetta la risposta del nativo **dopo** essere tornati alla
   pagina. Il picker e' una schermata di sistema: finche' e' davanti la pagina
   e' nascosta (`MainActivity.onPause` mette in pausa la WebView), e li' si puo'
   restare quanto si vuole. Tornati, la risposta arriva subito o dopo la copia
   del file (`copyImportFromUri`, un thread): un minuto basta a un backup
   grande. */
export const NATIVE_ANSWER_GRACE_MS = 60000;

/** La risposta del nativo a *start()*, per lo slot *slot* di `_pending`.
 *
 *  **Con una cintura**, come i dialoghi di questo file:
 *  se la risposta non arriva mai — il picker ucciso, un
 *  `evaluateJavascript` perso — la Promise restava appesa, e con lei `_busy`:
 *  export, import e restore morivano in silenzio fino al ricaricamento della
 *  pagina. Ora, tornati visibili dopo il picker, se entro
 *  `NATIVE_ANSWER_GRACE_MS` non ha risposto nessuno, e' un annullo. Una
 *  risposta tardiva trova lo slot vuoto e non fa niente. */
function _awaitNative(slot, start) {
  return new Promise((resolve) => {
    let timer = null;
    let hidden = document.visibilityState === 'hidden';
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hidden = true;
        clearTimeout(timer);
        return;
      }
      if (!hidden) return;
      clearTimeout(timer);
      timer = setTimeout(() => finish(false), NATIVE_ANSWER_GRACE_MS);
    };
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      if (_pending[slot] === finish) _pending[slot] = null;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      resolve(!!ok);
    };
    _pending[slot] = finish;
    document.addEventListener('visibilitychange', onVisibility);
    try {
      start();
    } catch (err) {
      console.error('Backup picker failed to start:', err);
      finish(false);
    }
  });
}

export function backupNativeAvailable() {
  const n = window.JennyNative;
  return !!(n && n.exportBackup && n.importBackup && n.restartApp);
}

export function restartApp() {
  // Il boot post-ripristino riparte in chat: lo garantisce il lato nativo
  // (SharedPreferences in restartApp → ?mode=chat nell'URL della WebView);
  // il localStorage non sopravvivrebbe al killProcess (persistenza asincrona).
  if (window.JennyNative?.restartApp) window.JennyNative.restartApp();
}

/** «manuale · prima di un ripristino · 42 file»: la riga sotto la data di uno
 *  snapshot, la stessa in casa e in officina (che la scrivevano ciascuna a
 *  modo suo). *size*: anche quanto pesa, dove c'e' posto (l'officina). */
export function snapshotDetail(s, { size = false } = {}) {
  const parts = [i18n.tOr(`backup.trigger.${s.trigger}`, s.trigger)];
  if (s.label) parts.push(s.label);
  parts.push(`${s.file_count} ${i18n.t('backup.files')}`);
  if (size && s.total_bytes != null) parts.push(sizeText(s.total_bytes, i18n.locale));
  return parts.join(' · ');
}

// ── Dialog passphrase ───────────────────────────────────────────────────

/** Chiede la passphrase; con confirm=true richiede la doppia digitazione.
 *  Risolve con la stringa o null se annullato. L'id lo cercano i banchi;
 *  Indietro la trova da se', come ogni `<dialog>` aperto
 *  (`dialog.js::dismissTopDialog`), e ogni chiusura risolve: una Promise
 *  appesa lascerebbe `_busy` a true, ed export, import e restore morti in
 *  silenzio per tutta la vita della pagina. */
export function promptPassphrase({ confirm = false } = {}) {
  return formDialog({
    id: 'oc-backup-passphrase-dialog',
    title: i18n.t('backup.passphraseTitle'),
    okText: i18n.t('dialog.confirm'),
    cancelText: i18n.t('common.cancel'),
    bodyHtml: `
      ${confirm ? `<p class="oc-dialog-note">${i18n.t('backup.passphraseHint')}</p>` : ''}
      <div class="settings-field">
        <label class="settings-label">${i18n.t('backup.passphrase')}</label>
        <input type="password" class="settings-input" id="bk-pass" autocomplete="off" />
      </div>
      ${confirm ? `
      <div class="settings-field">
        <label class="settings-label">${i18n.t('backup.passphraseConfirm')}</label>
        <input type="password" class="settings-input" id="bk-pass2" autocomplete="off" />
      </div>` : ''}`,
    setup: (dialog) => setTimeout(() => dialog.querySelector('#bk-pass')?.focus(), 0),
    onOk: (dialog) => {
      const pass = dialog.querySelector('#bk-pass').value;
      if (!pass) {
        showToast(i18n.t('backup.passphraseRequired'), 'error');
        return undefined;
      }
      if (confirm && pass !== dialog.querySelector('#bk-pass2').value) {
        showToast(i18n.t('backup.passphraseMismatch'), 'error');
        return undefined;
      }
      return pass;
    },
  });
}

/** Dialog finale non annullabile: il restore è pronto, serve riavviare.
 *  Stesso id-contratto della passphrase: Indietro lo trova, e la pressione e'
 *  consumata senza chiudere niente. */
export function showRestartDialog() {
  formDialog({
    id: 'oc-backup-restart-dialog',
    title: i18n.t('backup.restartTitle'),
    bodyHtml: `<p class="oc-dialog-note">${i18n.t('backup.restartDesc')}</p>`,
    okText: i18n.t('backup.restartNow'),
    cancelText: null,
    onOk: () => { restartApp(); return undefined; },
  });
}

// ── Flussi completi ─────────────────────────────────────────────────────

/** Export: passphrase → gateway cifra → picker SAF. Risolve true se salvato.
 *  Un flusso già in corso (doppio tap) risolve subito false. */
export function runExportFlow() {
  return _exclusive(async () => {
    if (!backupNativeAvailable()) {
      showToast(i18n.t('backup.androidOnly'), 'error');
      return false;
    }
    const passphrase = await promptPassphrase({ confirm: true });
    if (passphrase == null) return false;

    showToast(i18n.t('backup.exporting'));
    let staged;
    try {
      staged = await api.exportBackup(passphrase);
    } catch (e) {
      showToast(e.message, 'error');
      return false;
    }
    const ok = await _awaitNative('export', () => {
      window.JennyNative.exportBackup(staged.staged_path, staged.suggested_filename);
    });
    showToast(ok ? i18n.t('backup.exportSuccess') : i18n.t('backup.exportCancelled'),
              ok ? undefined : 'error');
    /* Il momento in cui si sa che il file c'è davvero, e l'unico: fra
       `exportBackup` e questa risposta c'è una schermata di sistema che
       si può annullare. Segnarlo prima vorrebbe dire scrivere «ultimo
       backup: adesso» su un backup che non è stato salvato.
       Se la scrittura del record fallisce non si dice niente: il backup
       è fatto, ed è quello che conta — a mancare sarebbe la data. */
    if (ok) api.noteBackupExported().catch(() => {});
    return ok;
  });
}

/** Import: picker SAF → passphrase → staging sul gateway → dialog riavvio.
 *  Risolve true se il restore è stato preparato (l'app sta per riavviarsi).
 *  Un flusso già in corso (doppio tap) risolve subito false. */
export function runImportFlow() {
  return _exclusive(async () => {
    if (!backupNativeAvailable()) {
      showToast(i18n.t('backup.androidOnly'), 'error');
      return false;
    }
    const picked = await _awaitNative('import', () => window.JennyNative.importBackup());
    if (!picked) return false; // annullato dal picker o copia fallita

    const passphrase = await promptPassphrase();
    if (passphrase == null) return false;

    try {
      await api.importBackup({ passphrase });
    } catch (e) {
      const msg = /invalid_passphrase_or_corrupt/.test(e.message)
        ? i18n.t('backup.invalidPassphrase')
        : e.message;
      showToast(msg, 'error');
      return false;
    }
    showRestartDialog();
    return true;
  });
}

/** Restore da uno snapshot della storia locale → dialog riavvio. */
export function runSnapshotRestore(snapshotId) {
  return _exclusive(async () => {
    try {
      await api.restoreSnapshot(snapshotId);
    } catch (e) {
      showToast(e.message, 'error');
      return false;
    }
    showRestartDialog();
    return true;
  });
}
