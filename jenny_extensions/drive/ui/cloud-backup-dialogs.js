/** I dialoghi della copia su Drive: quale copia ripristinare, e la password.
 *
 *  Separati da `cloud-backup.js` perché quello è logica pura (lo provano i
 *  test senza DOM), mentre questi aprono modali, che la casa e l'onboarding
 *  hanno entrambi.
 */

import { i18n } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/utils.js';
import { whenText } from '../../shared/when.js';
import { detailDialog, formDialog } from '../../shared/dialog.js';
import { CLOUD_PASSWORD_MIN, PASSWORD_RETRY, cloudErrorText } from './cloud-backup.js';

/** Quale copia ripristinare. Risolve con l'id scelto, o `null`.
 *
 *  Di solito c'è una copia sola che conta, la più recente: è la prima e ha il
 *  bottone pieno. Le altre servono a chi sa di voler tornare più indietro. */
export function pickCloudCopy(snapshots) {
  if (!snapshots.length) return Promise.resolve(null);
  return detailDialog({
    title: i18n.t('cloudBackup.restore.question'),
    bodyHtml: `<p>${escapeHtml(i18n.t('cloudBackup.restore.pick'))}</p>`,
    actions: snapshots.map((snap, index) => ({
      id: snap.id,
      label: index === 0
        ? `${whenText(snap.created_at_ms)} · ${i18n.t('cloudBackup.restore.latest')}`
        : whenText(snap.created_at_ms),
      variant: index === 0 ? 'primary' : 'secondary',
    })),
  });
}

/** L'id della finestra. Indietro la chiude come ogni `<dialog>` aperto
 *  (`shared/dialog.js::dismissTopDialog`), senza che nessuno la elenchi. */
const CLOUD_PASSWORD_DIALOG_ID = 'oc-cloud-password-dialog';

/** Le regole che si controllano prima di mandare: lunghezza e, scegliendo, le
 *  due uguali. `null` se va, altrimenti la frase. Pura, per i test. */
export function passwordProblem(mode, first, second) {
  if (String(first || '').length < CLOUD_PASSWORD_MIN) {
    return i18n.t('cloudBackup.password.tooShort', { n: CLOUD_PASSWORD_MIN });
  }
  if (mode !== 'type' && first !== second) return i18n.t('cloudBackup.password.mismatch');
  return null;
}

/** La password della copia. *mode*: `choose` (accendendo), `change` (da «Altre
 *  opzioni») o `type` (telefono nuovo). *submit(password)* la manda al gateway
 *  e risolve con la sua risposta.
 *
 *  Il dialogo resta aperto finché la risposta è una che si corregge scrivendo
 *  (`PASSWORD_RETRY`: sbagliata, troppo corta): l'errore compare sotto, quel
 *  che si è scritto resta. Con ogni altra risposta si chiude e la restituisce;
 *  rinunciando risolve `null`. Mentre il gateway lavora (PBKDF2, Drive) i
 *  bottoni sono fermi: chiudere a metà lascerebbe un'accensione a metà. */
function promptCloudPassword({ mode = 'choose', submit }) {
  const two = mode !== 'type';
  const t = (key, params) => escapeHtml(i18n.t(`cloudBackup.password.${mode}.${key}`, params));
  const label = (text) => `<label class="settings-label">${text}</label>`;
  const field = (id, auto) => `<input type="password" class="settings-input" id="${id}"
      autocomplete="${auto}" autocapitalize="off" autocorrect="off" spellcheck="false" />`;
  let first;
  let second;
  let errorEl;
  const showError = (text) => {
    errorEl.textContent = text || '';
    errorEl.hidden = !text;
  };
  return formDialog({
    id: CLOUD_PASSWORD_DIALOG_ID,
    title: i18n.t(`cloudBackup.password.${mode}.title`),
    okText: i18n.t(`cloudBackup.password.${mode}.ok`),
    cancelText: i18n.t('common.cancel'),
    busyText: i18n.t('cloudBackup.password.working'),
    bodyHtml: `
      <p class="oc-dialog-note" style="font-size:13px;line-height:1.45;color:var(--text-muted)">${t('text', { n: CLOUD_PASSWORD_MIN })}</p>
      <div class="settings-field">
        ${label(t('field', { n: CLOUD_PASSWORD_MIN }))}
        ${field('cloud-pw', two ? 'new-password' : 'current-password')}
      </div>
      ${two ? `<div class="settings-field">${label(escapeHtml(i18n.t('cloudBackup.password.repeat')))}${field('cloud-pw2', 'new-password')}</div>` : ''}
      <label style="display:flex;gap:8px;align-items:center;margin:4px 0 0;font-size:13px;color:var(--text-muted)">
        <input type="checkbox" id="cloud-pw-show" /> ${escapeHtml(i18n.t('cloudBackup.password.show'))}
      </label>
      <p id="cloud-pw-error" role="alert" hidden style="margin:10px 0 0;font-size:13px;color:var(--error)"></p>`,
    setup: (dialog) => {
      first = dialog.querySelector('#cloud-pw');
      second = dialog.querySelector('#cloud-pw2');
      errorEl = dialog.querySelector('#cloud-pw-error');
      const ok = dialog.querySelector('[data-form-ok]');
      dialog.querySelector('#cloud-pw-show').addEventListener('change', (e) => {
        const type = e.target.checked ? 'text' : 'password';
        first.type = type;
        if (second) second.type = type;
      });
      for (const input of [first, second].filter(Boolean)) {
        input.addEventListener('input', () => showError(''));
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); ok.click(); }
        });
      }
      setTimeout(() => first.focus(), 0);
    },
    onOk: async () => {
      const problem = passwordProblem(mode, first.value, second?.value);
      if (problem) {
        showError(problem);
        first.focus();
        return undefined;
      }
      showError('');
      let result;
      try {
        result = await submit(first.value);
      } catch (err) {
        console.error('Cloud password submit failed:', err);
        result = { ok: false, error: 'generic' };
      }
      if (!result?.ok && PASSWORD_RETRY.includes(result?.error)) {
        showError(cloudErrorText(result.error));
        first.select();
        return undefined;
      }
      return result ?? null;
    },
  });
}

/** Il pezzo da passare ai flussi di `cloud-backup.js` come `askPassword`. */
export const askCloudPassword = (mode) => (submit) => promptCloudPassword({ mode, submit });
