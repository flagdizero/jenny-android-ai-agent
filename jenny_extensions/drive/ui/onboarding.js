/** «Ci conosciamo già?» nel primo passo dell'onboarding: la copia su Google
 *  Drive del telefono di prima.
 *
 *  Il primo passo e' del core (`onboarding-wizard.js`): questa estensione ci si
 *  aggancia con `addWelcomeExtension`, mette la domanda e il bottone sopra il
 *  ripristino da file e porta quel ripristino dentro lo stesso blocco — le due
 *  strade una sotto l'altra, la copia su Drive per prima.
 */

import { i18n } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/utils.js';
import { cloudErrorText, cloudNativeAvailable, findMyCopy, restoreCopy } from './cloud-backup.js';
import { askCloudPassword, pickCloudCopy } from './cloud-backup-dialogs.js';

/** Disegna il blocco in *slot* e ci sposta dentro il ripristino da file. */
export function renderWelcome(slot, { fileButton, fileHint } = {}) {
  if (!slot) return;
  const t = (key) => escapeHtml(i18n.t(key));
  slot.className = 'onboarding-met';
  slot.innerHTML = `
    <h3 class="onboarding-met-question">${t('cloudBackup.restore.question')}</h3>
    <p class="onboarding-desc onboarding-met-hint">${t('cloudBackup.restore.hint')}</p>
    ${cloudNativeAvailable() ? `
    <button id="btn-find-copy" class="onboarding-btn onboarding-met-btn" type="button">
      <i class="ti ti-cloud-download" aria-hidden="true"></i> ${t('cloudBackup.restore.yes')}
    </button>` : ''}
    <p class="onboarding-desc onboarding-met-status" id="onboarding-met-status" hidden></p>`;
  if (fileButton) {
    fileButton.removeAttribute('style');
    fileButton.classList.add('onboarding-met-btn', 'onboarding-met-file');
    slot.appendChild(fileButton);
  }
  if (fileHint) {
    fileHint.removeAttribute('style');
    fileHint.classList.add('onboarding-met-hint');
    slot.appendChild(fileHint);
  }
  slot.querySelector('#btn-find-copy')
    ?.addEventListener('click', (e) => findCloudCopy(slot, e.currentTarget));
}

/** «Sì»: permesso di Google → la password della copia → quale copia →
 *  scaricata in staging → riavvio. Come per il file, al riavvio la config
 *  ripristinata ha già i provider, quindi il guscio riparte dalla casa e il
 *  wizard non si rivede. Ogni intoppo è una frase sotto il bottone, che resta
 *  lì per riprovare. */
export async function findCloudCopy(slot, btn) {
  if (btn.disabled) return;
  const status = slot.querySelector('#onboarding-met-status');
  const say = (text) => {
    if (!status) return;
    status.textContent = text || '';
    status.hidden = !text;
  };
  btn.disabled = true;
  try {
    say(i18n.t('cloudBackup.restore.searching'));
    const found = await findMyCopy({ askPassword: askCloudPassword('type') });
    if (!found.ok) {
      say(found.error === 'cancelled' ? '' : cloudErrorText(found.error));
      return;
    }
    say('');
    const chosen = await pickCloudCopy(found.snapshots || []);
    if (!chosen) {
      say('');
      return;
    }
    say(i18n.t('cloudBackup.restore.restoring'));
    const staged = await restoreCopy(chosen);
    if (!staged.ok) say(cloudErrorText(staged.error));
  } catch (err) {
    console.error('Cloud restore failed:', err);
    say(cloudErrorText('generic'));
  } finally {
    btn.disabled = false;
  }
}

export function mountOnboarding({ onboarding }) {
  onboarding?.addWelcomeExtension(renderWelcome);
}
