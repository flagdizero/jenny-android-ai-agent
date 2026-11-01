/** Card condivisa per l'esenzione dall'ottimizzazione batteria (doze).
 *
 * Il doze di Android sospende rete e differisce le sveglie di ogni app che non
 * è nella whitelist della batteria: non è un problema del solo long-poll
 * Telegram — cron, Dream e promemoria slittano esattamente
 * allo stesso modo. La richiesta viveva solo dentro la card di pairing
 * Telegram, quindi chi Telegram non lo usa non se la vedeva chiedere mai: da
 * qui la stessa card si monta su tre superfici (onboarding, impostazioni,
 * card Telegram) con copy e prominenza diverse.
 *
 * Fuori dalla WebView Android il bridge nativo non esiste e non c'è nulla da
 * chiedere: tutto degrada a stringa vuota invece che a un errore.
 */

import { i18n } from './i18n.js';

/* Il bridge è iniettato da MainActivity: assente nel browser desktop, e
   presente ma incompleto se l'APK è più vecchio della UI (le due cose si
   aggiornano insieme, ma non è garantito che lo facciano nello stesso
   istante). Un solo punto di controllo per entrambi i casi. */
function _bridge() {
  const native = window.JennyNative;
  if (!native || typeof native.isBatteryExempt !== 'function') return null;
  return native;
}

/** True solo dentro la WebView Android, dove la richiesta ha un senso. */
export function batteryExemptionSupported() {
  return _bridge() !== null;
}

/** True quando siamo su Android E l'esenzione manca davvero. */
export function batteryExemptionNeeded() {
  const native = _bridge();
  if (!native) return false;
  try {
    return !native.isBatteryExempt();
  } catch (_) {
    // Bridge che solleva: meglio tacere che mostrare una richiesta che non
    // saprebbe portare da nessuna parte.
    return false;
  }
}

/* Gli aggiornamenti di sistema di Samsung e Xiaomi rimettono l'app fra quelle
   ottimizzate senza dirlo a nessuno: l'unico segnale lato app è la
   Build.FINGERPRINT cambiata, che il bridge confronta con quella dell'ultimo
   avvio. Metodo nuovo, quindi opzionale: su un APK vecchio si degrada al
   messaggio normale. */
function _systemUpdated() {
  const native = window.JennyNative;
  if (!native || typeof native.systemUpdatedSinceLastRun !== 'function') return false;
  try {
    return !!native.systemUpdatedSinceLastRun();
  } catch (_) {
    return false;
  }
}

/** HTML della card (stringa vuota se non serve o non si può chiedere).
 *
 * @param {{hintKey?: string, buttonKey?: string, grantedKey?: string,
 *          tone?: 'hint'|'notice'}} opts
 *   - `hintKey`/`buttonKey`: copy per ospite (Telegram tiene le sue stringhe).
 *   - `grantedKey`: se dato, a esenzione già concessa mostra la conferma
 *     invece di sparire — serve alla sezione delle impostazioni, che vuota
 *     sembrerebbe rotta.
 *   - `tone`: `hint` è la riga piccola e sbiadita della card Telegram,
 *     `notice` il riquadro con bordo delle superfici dove la richiesta è il
 *     contenuto principale.
 */
export function batteryExemptionHtml(opts = {}) {
  const {
    hintKey = 'settings.battery.hint',
    buttonKey = 'settings.battery.button',
    grantedKey = null,
    tone = 'hint',
  } = opts;
  if (!batteryExemptionSupported()) return '';
  if (!batteryExemptionNeeded()) {
    return grantedKey ? `<p class="onboarding-hint">${i18n.t(grantedKey)}</p>` : '';
  }
  // Dopo un aggiornamento di sistema il messaggio normale ("attiva
  // l'esenzione") è fuorviante: l'utente l'aveva già attivata. Va detto che
  // qualcuno gliel'ha tolta, e va detto forte.
  const updated = _systemUpdated();
  const text = updated ? i18n.t('settings.battery.otaHint') : i18n.t(hintKey);
  const body = updated || tone === 'notice'
    ? `<div class="settings-notice${updated ? ' settings-notice-strong' : ''}">
         <i class="ti ti-${updated ? 'alert-triangle' : 'battery-charging'}"></i>
         <div>${text}</div>
       </div>`
    : `<p class="onboarding-hint">${text}</p>`;
  return `${body}
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" data-battery-exempt>
          ${i18n.t(buttonKey)}
        </button>
      </div>`;
}

/** Aggancia il click su ogni bottone `data-battery-exempt` dentro `root`.
 *
 * Attributo e non id: la card Telegram e la sezione delle impostazioni possono
 * essere vive nello stesso DOM, e due id uguali ne lascerebbero uno morto. */
export function wireBatteryExemption(root) {
  if (!root) return;
  root.querySelectorAll('[data-battery-exempt]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const native = window.JennyNative;
      if (!native || typeof native.requestBatteryExemption !== 'function') return;
      try {
        native.requestBatteryExemption();
      } catch (_) { /* dialogo di sistema non apribile: nulla da fare qui */ }
    });
  });
}

/** Card montata su un contenitore proprio (impostazioni, onboarding).
 *
 * Chi la card la inietta dentro un template più grande — la card Telegram —
 * usa direttamente `batteryExemptionHtml()` + `wireBatteryExemption()`. */
export class BatteryExemptionCard {
  /**
   * @param {HTMLElement} container dove renderizzare
   * @param {object} opts stesse opzioni di `batteryExemptionHtml`
   */
  constructor(container, opts = {}) {
    this.el = container;
    this.opts = opts;
    // Il permesso si concede fuori dalla WebView, nel dialogo di sistema: al
    // ritorno la pagina non ha ricevuto nessun evento e resterebbe a mostrare
    // una richiesta già soddisfatta.
    this._onVisible = () => {
      if (document.visibilityState === 'visible') this.render();
    };
    document.addEventListener('visibilitychange', this._onVisible);
  }

  /** @returns {boolean} true se la card ha disegnato qualcosa. */
  render() {
    if (!this.el || !this.el.isConnected) return false;
    const html = batteryExemptionHtml(this.opts);
    this.el.innerHTML = html;
    wireBatteryExemption(this.el);
    return html !== '';
  }

  destroy() {
    document.removeEventListener('visibilitychange', this._onVisible);
  }
}
