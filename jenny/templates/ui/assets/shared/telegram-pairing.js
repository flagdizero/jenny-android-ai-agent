/** Widget condiviso per collegare il bot Telegram (onboarding + impostazioni).
 *
 * Stati: non configurato (input token) → in pairing (codice + polling dello
 * stato ogni 2.5s) → accoppiato (✓ + scollega/disabilita in modalità settings).
 * Le stringhe vivono sotto `settings.telegram.*` così le due superfici restano
 * allineate.
 */

import { api } from './api-client.js';
import { escapeHtml, showToast } from './utils.js';
import { i18n } from './i18n.js';
import { batteryExemptionHtml, wireBatteryExemption } from './battery-exemption.js';

const POLL_MS = 2500;

export class TelegramPairingWidget {
  /**
   * @param {HTMLElement} container dove renderizzare
   * @param {{mode?: 'onboarding'|'settings', onPaired?: Function, onStatus?: Function}} opts
   *   `onStatus(status)` a ogni stato nuovo che il widget disegna: serve a chi
   *   ne mostra un riassunto fuori dal widget (la riga in cassetto).
   */
  constructor(container, opts = {}) {
    this.el = container;
    this.mode = opts.mode || 'settings';
    this.onPaired = opts.onPaired || null;
    this.onStatus = opts.onStatus || null;
    this.status = null;
    this._pollTimer = null;
    this._busy = false;
    /* Lapide del widget. `destroy()` fermava solo il timer *già acceso*: se il
       widget veniva congedato mentre `refresh()` era sospeso sulla sua fetch,
       la continuazione ripartiva dopo, chiamava `render()` e accendeva il
       polling **dopo** che il proprietario aveva lasciato cadere il
       riferimento. Da lì in poi l'oggetto viveva solo per la closure
       dell'intervallo — irraggiungibile, non più fermabile — e interrogava il
       gateway ogni 2,5 s per sempre, uno per ogni giro di andata e ritorno
       nelle impostazioni. */
    this._destroyed = false;
  }

  destroy() {
    this._destroyed = true;
    this._stopPolling();
  }

  async refresh() {
    let status;
    try {
      status = await api.getTelegramStatus();
    } catch (e) {
      if (this._destroyed) return;
      this.el.innerHTML = `<div class="onboarding-hint">${escapeHtml(e.message || 'error')}</div>`;
      return;
    }
    if (this._destroyed) return;
    this.status = status;
    this.render();
  }

  /* L'ordine dei rami è la correzione, non un dettaglio: `paired` stava per
     primo e `enabled` non veniva letto in quel ramo, quindi un canale spento ma
     accoppiato mostrava «✓ Collegato» — lo stato in cui il telefono è rimasto
     dal 29/08 al 02/09 senza che niente lo dicesse. Uno stato spento non ha
     alcuna rappresentazione se non si guarda `enabled` prima di tutto il resto. */
  render() {
    if (this._destroyed) return;
    this._stopPolling();
    const s = this.status;
    if (!s) return;
    /* Ogni cambio di stato passa di qui — lettura, accoppiamento, token,
       interruttore, disaccoppia — quindi e' qui che lo si racconta. */
    this.onStatus?.(s);
    if (s.configured && !s.enabled) {
      this._renderDisabled();
    } else if (s.paired) {
      this._renderPaired();
    } else if (s.enabled && s.configured && s.pairing_code) {
      this._renderPairing();
      this._startPolling();
    } else {
      this._renderTokenForm();
    }
  }

  /* La riga del toggle, sullo stampo di `_renderSshBlock` in mobile-settings.js:
     stesso markup e stesse classi, così le sezioni delle impostazioni si
     somigliano invece di avere ognuna il suo interruttore. */
  _toggleRowHtml(checked) {
    return `
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.telegram.enable')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="tg-enabled-toggle" ${checked ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>`;
  }

  _wireToggle() {
    const el = this.el.querySelector('#tg-enabled-toggle');
    if (el) el.addEventListener('change', () => this._setEnabled(el.checked));
  }

  // ── Stato: non configurato ──────────────────────────────────────────

  _renderTokenForm() {
    const s = this.status;
    const hint = s.token_hint
      ? `<span class="onboarding-hint">${i18n.t('settings.telegram.currentToken')}: ${escapeHtml(s.token_hint)}</span>`
      : '';
    this.el.innerHTML = `
      <p class="onboarding-desc">${i18n.t('settings.telegram.intro')}</p>
      <ol class="tg-steps">
        <li>${i18n.t('settings.telegram.step1')}</li>
        <li>${i18n.t('settings.telegram.step2')}</li>
        <li>${i18n.t('settings.telegram.step3')}</li>
      </ol>
      <div class="onboarding-field">
        <label class="onboarding-label" for="tg-token">${i18n.t('settings.telegram.token')}</label>
        <input type="password" class="onboarding-input" id="tg-token"
               placeholder="123456789:AA..." autocomplete="off" data-lpignore="true">
        ${hint}
      </div>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-primary" id="tg-connect" disabled>
          ${i18n.t('settings.telegram.connect')}
        </button>
      </div>`;
    const input = this.el.querySelector('#tg-token');
    const btn = this.el.querySelector('#tg-connect');
    input.addEventListener('input', () => { btn.disabled = !input.value.trim(); });
    btn.addEventListener('click', () => this._saveToken(input.value.trim()));
  }

  async _saveToken(token) {
    if (this._busy || !token) return;
    this._busy = true;
    const btn = this.el.querySelector('#tg-connect');
    if (btn) { btn.disabled = true; btn.textContent = i18n.t('settings.telegram.validating'); }
    try {
      this.status = await api.saveTelegramToken(token);
      this.render();
    } catch (e) {
      showToast(e.message || i18n.t('settings.telegram.saveFailed'), 'error');
      this.render();
    } finally {
      this._busy = false;
    }
  }

  // ── Stato: in attesa di pairing ─────────────────────────────────────

  _renderPairing() {
    const s = this.status;
    const botLink = s.bot_username
      ? `<a href="https://t.me/${escapeHtml(s.bot_username)}" target="_blank" rel="noopener" class="tg-bot-link">@${escapeHtml(s.bot_username)}</a>`
      : i18n.t('settings.telegram.yourBot');
    this.el.innerHTML = `
      <p class="onboarding-desc">${i18n.t('settings.telegram.sendCode', { bot: botLink })}</p>
      <div class="tg-pairing-code" aria-live="polite">${escapeHtml(s.pairing_code)}</div>
      <p class="onboarding-hint tg-waiting">
        <span class="onboarding-spinner tg-spinner-inline"></span>
        ${i18n.t('settings.telegram.waiting')}
      </p>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="tg-change-token">
          ${i18n.t('settings.telegram.changeToken')}
        </button>
      </div>`;
    this.el.querySelector('#tg-change-token').addEventListener('click', () => {
      this._stopPolling();
      /* Si azzera **solo** `pairing_code`, che è quanto basta a far cadere
         `render()` sul form del token. Prima qui si scriveva anche
         `enabled: false`, una bugia locale sullo stato del server che era
         innocua finché nessun ramo guardava `enabled`: ora ci sarebbe la card
         del canale spento, per un canale che è accesissimo. */
      this.status = { ...this.status, configured: true, pairing_code: null };
      this._renderTokenForm();
    });
  }

  _startPolling() {
    // Ultimo cancello prima di accendere un timer che nessuno potrebbe più
    // spegnere: `render()` può arrivare da una continuazione nata prima del
    // congedo.
    if (this._destroyed) return;
    this._pollTimer = setInterval(async () => {
      if (this._destroyed) { this._stopPolling(); return; }
      try {
        const s = await api.getTelegramStatus();
        if (this._destroyed) { this._stopPolling(); return; }
        if (s.paired) {
          this.status = s;
          this._stopPolling();
          this.render();
          if (this.onPaired) this.onPaired(s);
        }
      } catch (_) { /* transitorio: si ritenta al prossimo tick */ }
    }, POLL_MS);
  }

  _stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  // ── Stato: accoppiato ───────────────────────────────────────────────

  _renderPaired() {
    const s = this.status;
    const who = s.paired_username ? `@${escapeHtml(s.paired_username)}` : i18n.t('settings.telegram.aChat');
    const bot = s.bot_username ? ` (@${escapeHtml(s.bot_username)})` : '';
    /* Un solo bottone, e il toggle sopra. Prima erano due bottoni identici
       affiancati — «Scollega» e «Disattiva», stessa classe, nessuna conferma —
       ed è così che il canale è stato spento per sbaglio: su un telefono in mano
       la distanza fra i due è un pollice. Il toggle sta in una riga sua, dice lo
       stato invece di un'azione, e se lo sfiori si ri-tocca per rimediare
       invece di dover rifare il pairing. */
    this.el.innerHTML = `
      ${this.mode === 'settings' ? this._toggleRowHtml(true) : ''}
      <div class="tg-paired">
        <i class="ti ti-circle-check"></i>
        ${i18n.t('settings.telegram.paired', { who })}${bot}
      </div>
      ${this._batteryHtml()}
      ${this.mode === 'settings' ? `
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="tg-unpair">
          ${i18n.t('settings.telegram.unpair')}
        </button>
      </div>` : ''}`;
    const unpairBtn = this.el.querySelector('#tg-unpair');
    if (unpairBtn) unpairBtn.addEventListener('click', () => this._unpair());
    this._wireToggle();
    wireBatteryExemption(this.el);
  }

  // ── Stato: configurato ma spento ────────────────────────────────────

  /* Lo stato che prima non esisteva. Il toggle c'è in *entrambe* le modalità,
     non solo in `settings`: è l'unica via d'uscita da qui, e nasconderlo
     riprodurrebbe il vicolo cieco che questa card viene a chiudere. */
  _renderDisabled() {
    const s = this.status;
    const bot = s.bot_username ? ` (@${escapeHtml(s.bot_username)})` : '';
    const who = s.paired_username ? `@${escapeHtml(s.paired_username)}` : null;
    const keeps = who
      ? i18n.t('settings.telegram.disabledPaired', { who })
      : i18n.t('settings.telegram.disabledUnpaired');
    this.el.innerHTML = `
      ${this._toggleRowHtml(false)}
      <div class="tg-disabled">
        <i class="ti ti-circle-off"></i>
        ${i18n.t('settings.telegram.disabledTitle')}${bot}
      </div>
      <p class="onboarding-hint">${keeps}</p>`;
    this._wireToggle();
  }

  /* Stessa card condivisa delle impostazioni e dell'onboarding, ma con il
     copy specifico del canale: qui il doze si vede come risposta che tarda,
     non come cron che slitta. Tono `hint` = la riga piccola di prima. */
  _batteryHtml() {
    return batteryExemptionHtml({
      hintKey: 'settings.telegram.batteryHint',
      buttonKey: 'settings.telegram.batteryButton',
    });
  }

  async _unpair() {
    if (this._busy) return;
    this._busy = true;
    try {
      this.status = await api.unpairTelegram();
      this.render();
    } catch (e) {
      showToast(e.message || i18n.t('settings.telegram.saveFailed'), 'error');
    } finally {
      this._busy = false;
    }
  }

  /* Il toggle è già visivamente girato quando arriviamo qui: il browser cambia
     il checkbox da sé, prima di `change`. Quindi un rifiuto del server non basta
     segnalarlo col toast — va anche rimessa a posto la levetta, altrimenti resta
     a dichiarare uno stato che non è stato salvato. Da qui il `render()` anche
     nel ramo d'errore, che ridisegna dallo `status` vecchio, quello vero. */
  async _setEnabled(enabled) {
    if (this._busy) return;
    this._busy = true;
    const el = this.el.querySelector('#tg-enabled-toggle');
    if (el) el.disabled = true;
    try {
      this.status = await api.setTelegramEnabled(enabled);
      showToast(
        i18n.t(enabled ? 'settings.telegram.enabled' : 'settings.telegram.disabled'),
        'info',
      );
      this.render();
    } catch (e) {
      showToast(e.message || i18n.t('settings.telegram.saveFailed'), 'error');
      this.render();
    } finally {
      this._busy = false;
    }
  }
}

/** Telegram in una riga sola, per il riepilogo in cassetto.
 *
 *  Le quattro combinazioni che contano — spento, acceso senza token, acceso
 *  col token ma non collegato, collegato — rispondono tutte alla stessa
 *  domanda: «posso scriverle da fuori, adesso?». La riga dice quella, e il
 *  resto si apre col tocco.
 *
 *  Funzione e non metodo del widget: la riga vive **fuori** dal widget, in un
 *  cassetto che il widget non ha ancora disegnato.
 */
export function telegramSummary(status) {
  if (!status) return i18n.t('settings.telegram.summaryUnknown');
  if (!status.enabled) return i18n.t('settings.telegram.summaryOff');
  if (!status.configured) return i18n.t('settings.telegram.summaryNoToken');
  if (!status.paired) return i18n.t('settings.telegram.summaryNotPaired');
  const who = status.paired_username ? `@${status.paired_username}` : i18n.t('settings.telegram.aChat');
  return i18n.t('settings.telegram.summaryPaired', { who });
}
