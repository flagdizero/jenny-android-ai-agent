/** I dialoghi della copia su Drive: quale copia ripristinare, e la password.
 *
 *  Separati da `cloud-backup.js` perché quello è logica pura (lo provano i
 *  test senza DOM), mentre questi aprono modali, che la casa e l'onboarding
 *  hanno entrambi.
 */

import { i18n } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/utils.js';
import { whenText } from '../../shared/when.js';
import { detailDialog } from '../../shared/dialog.js';
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

/** L'id della finestra: la casa lo elenca fra i dialoghi che Indietro chiude
 *  (`SHARED_DIALOGS` in home-app.js), come quello della passphrase. */
export const CLOUD_PASSWORD_DIALOG_ID = 'oc-cloud-password-dialog';

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
export function promptCloudPassword({ mode = 'choose', submit }) {
  return new Promise((resolve) => {
    const two = mode !== 'type';
    const t = (key, params) => escapeHtml(i18n.t(`cloudBackup.password.${mode}.${key}`, params));
    const label = (text) => `<label class="settings-label">${text}</label>`;
    const field = (id, auto) => `<input type="password" class="settings-input" id="${id}"
        autocomplete="${auto}" autocapitalize="off" autocorrect="off" spellcheck="false" />`;
    const dialog = document.createElement('dialog');
    dialog.className = 'oc-dialog';
    dialog.id = CLOUD_PASSWORD_DIALOG_ID;
    dialog.innerHTML = `
      <div class="oc-dialog-inner">
        <h3 style="margin:0 0 8px;font-size:15px;font-weight:600">${t('title')}</h3>
        <p style="margin:0 0 12px;font-size:13px;line-height:1.45;color:var(--text-muted)">${t('text', { n: CLOUD_PASSWORD_MIN })}</p>
        <div class="settings-field">
          ${label(t('field', { n: CLOUD_PASSWORD_MIN }))}
          ${field('cloud-pw', two ? 'new-password' : 'current-password')}
        </div>
        ${two ? `<div class="settings-field">${label(escapeHtml(i18n.t('cloudBackup.password.repeat')))}${field('cloud-pw2', 'new-password')}</div>` : ''}
        <label style="display:flex;gap:8px;align-items:center;margin:4px 0 0;font-size:13px;color:var(--text-muted)">
          <input type="checkbox" id="cloud-pw-show" /> ${escapeHtml(i18n.t('cloudBackup.password.show'))}
        </label>
        <p id="cloud-pw-error" role="alert" hidden style="margin:10px 0 0;font-size:13px;color:var(--error)"></p>
        <div class="oc-dialog-buttons" style="margin-top:16px">
          <button class="oc-btn oc-btn-cancel" id="cloud-pw-cancel" type="button">${escapeHtml(i18n.t('common.cancel'))}</button>
          <button class="oc-btn oc-btn-confirm" id="cloud-pw-ok" type="button">${t('ok')}</button>
        </div>
      </div>`;
    document.body.appendChild(dialog);

    const $ = (id) => dialog.querySelector(`#${id}`);
    const first = $('cloud-pw');
    const second = $('cloud-pw2');
    const okBtn = $('cloud-pw-ok');
    const cancelBtn = $('cloud-pw-cancel');
    const errorEl = $('cloud-pw-error');
    let busy = false;
    let settled = false;

    const showError = (text) => {
      errorEl.textContent = text || '';
      errorEl.hidden = !text;
    };
    const done = (value) => {
      if (settled) return;
      settled = true;
      // La password non resta nel DOM una volta chiusa la finestra.
      first.value = '';
      if (second) second.value = '';
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    const onOk = async () => {
      if (busy) return;
      const problem = passwordProblem(mode, first.value, second?.value);
      if (problem) {
        showError(problem);
        first.focus();
        return;
      }
      busy = true;
      okBtn.disabled = true;
      cancelBtn.disabled = true;
      showError('');
      const okText = okBtn.textContent;
      okBtn.textContent = i18n.t('cloudBackup.password.working');
      let result;
      try {
        result = await submit(first.value);
      } catch (err) {
        console.error('Cloud password submit failed:', err);
        result = { ok: false, error: 'generic' };
      }
      busy = false;
      okBtn.disabled = false;
      cancelBtn.disabled = false;
      okBtn.textContent = okText;
      if (!result?.ok && PASSWORD_RETRY.includes(result?.error)) {
        showError(cloudErrorText(result.error));
        first.select();
        return;
      }
      done(result);
    };

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', () => { if (!busy) done(null); });
    $('cloud-pw-show').addEventListener('change', (e) => {
      const type = e.target.checked ? 'text' : 'password';
      first.type = type;
      if (second) second.type = type;
    });
    for (const input of [first, second].filter(Boolean)) {
      input.addEventListener('input', () => showError(''));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); onOk(); }
      });
    }
    // Indietro / Esc: si rinuncia, salvo a meta' di una risposta del gateway.
    dialog.addEventListener('cancel', (e) => {
      e.preventDefault();
      if (!busy) done(null);
    });
    // Cintura, come per la passphrase: ogni chiusura risolve la Promise.
    dialog.addEventListener('close', () => done(null));
    dialog.showModal();
    first.focus();
  });
}

/** Il pezzo da passare ai flussi di `cloud-backup.js` come `askPassword`. */
export const askCloudPassword = (mode) => (submit) => promptCloudPassword({ mode, submit });
