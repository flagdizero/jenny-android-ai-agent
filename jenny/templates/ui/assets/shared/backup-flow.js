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
import { showToast } from './utils.js';

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

// ── Dialog passphrase ───────────────────────────────────────────────────

/** Chiede la passphrase; con confirm=true richiede la doppia digitazione.
 *  Risolve con la stringa o null se annullato. */
export function promptPassphrase({ confirm = false } = {}) {
  return new Promise((resolve) => {
    const dialog = document.createElement('dialog');
    dialog.className = 'oc-dialog';
    dialog.innerHTML = `
      <div class="oc-dialog-inner">
        <h3 style="margin:0 0 8px;font-size:15px;font-weight:600">${i18n.t('backup.passphraseTitle')}</h3>
        ${confirm ? `<p style="margin:0 0 12px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.passphraseHint')}</p>` : ''}
        <div class="settings-field">
          <label class="settings-label">${i18n.t('backup.passphrase')}</label>
          <input type="password" class="settings-input" id="bk-pass" autocomplete="off" />
        </div>
        ${confirm ? `
        <div class="settings-field">
          <label class="settings-label">${i18n.t('backup.passphraseConfirm')}</label>
          <input type="password" class="settings-input" id="bk-pass2" autocomplete="off" />
        </div>` : ''}
        <div class="oc-dialog-buttons" style="margin-top:16px">
          <button class="oc-btn oc-btn-cancel" id="bk-cancel">${i18n.t('common.cancel')}</button>
          <button class="oc-btn oc-btn-confirm" id="bk-ok">${i18n.t('dialog.confirm')}</button>
        </div>
      </div>`;
    document.body.appendChild(dialog);

    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    dialog.querySelector('#bk-cancel').addEventListener('click', () => done(null));
    dialog.addEventListener('cancel', () => done(null));
    // Cintura: qualunque chiusura che non passi da `cancel` — un close()
    // diretto, un form method="dialog", una rimozione dal DOM da parte di
    // terzi — deve comunque risolvere la Promise. Una Promise che resta
    // appesa lascia `_busy` a true per tutta la vita della pagina, ed export,
    // import e restore muoiono in silenzio da lì in avanti. È la stessa
    // cintura che confirmDialog/detailDialog/promptDialog hanno già.
    dialog.addEventListener('close', () => done(null));
    dialog.querySelector('#bk-ok').addEventListener('click', () => {
      const pass = dialog.querySelector('#bk-pass').value;
      if (!pass) {
        showToast(i18n.t('backup.passphraseRequired'), 'error');
        return;
      }
      if (confirm) {
        const pass2 = dialog.querySelector('#bk-pass2').value;
        if (pass !== pass2) {
          showToast(i18n.t('backup.passphraseMismatch'), 'error');
          return;
        }
      }
      done(pass);
    });
    dialog.showModal();
    dialog.querySelector('#bk-pass').focus();
  });
}

/** Dialog finale non annullabile: il restore è pronto, serve riavviare. */
export function showRestartDialog() {
  const dialog = document.createElement('dialog');
  dialog.className = 'oc-dialog';
  dialog.innerHTML = `
    <div class="oc-dialog-inner">
      <h3 style="margin:0 0 8px;font-size:15px;font-weight:600">${i18n.t('backup.restartTitle')}</h3>
      <p style="margin:0 0 12px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.restartDesc')}</p>
      <div class="oc-dialog-buttons">
        <button class="oc-btn oc-btn-confirm" id="bk-restart">${i18n.t('backup.restartNow')}</button>
      </div>
    </div>`;
  document.body.appendChild(dialog);
  dialog.addEventListener('cancel', (e) => e.preventDefault());
  dialog.querySelector('#bk-restart').addEventListener('click', () => restartApp());
  dialog.showModal();
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
    return await new Promise((resolve) => {
      _pending.export = (ok) => {
        showToast(ok ? i18n.t('backup.exportSuccess') : i18n.t('backup.exportCancelled'),
                  ok ? undefined : 'error');
        /* Il momento in cui si sa che il file c'è davvero, e l'unico: fra
           `exportBackup` e questa risposta c'è una schermata di sistema che
           si può annullare. Segnarlo prima vorrebbe dire scrivere «ultimo
           backup: adesso» su un backup che non è stato salvato.
           Se la scrittura del record fallisce non si dice niente: il backup
           è fatto, ed è quello che conta — a mancare sarebbe la data. */
        if (ok) api.noteBackupExported().catch(() => {});
        resolve(ok);
      };
      window.JennyNative.exportBackup(staged.staged_path, staged.suggested_filename);
    });
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
    const picked = await new Promise((resolve) => {
      _pending.import = resolve;
      window.JennyNative.importBackup();
    });
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
