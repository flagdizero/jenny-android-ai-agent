/** Dopo un aggiornamento del gateway, la pagina si ricarica da sola.
 *
 *  Una pagina aperta prima di un aggiornamento continuava col JS vecchio: il
 *  socket si ricollega da solo quando il gateway riparte, la pagina no. Il
 *  21/10/2026 il browser del Mac, collegato da remoto al Titan, mostrava la
 *  bacheca senza le etichette dei cron che il telefono, ripartito col processo,
 *  aveva già.
 *
 *  A ogni riconnessione si rilegge `/webui/bootstrap` e si confronta con quello
 *  con cui la pagina è partita (v. `jenny/webui/ui_version.py`):
 *  - `ui_version` diversa: i file sono cambiati, si ricarica;
 *  - uguale: è solo ripartito, niente;
 *  - la pagina era partita prima che l'impronta fosse pronta (`""`): se il
 *    processo è lo stesso (`boot`) la impara adesso; se è un altro non può
 *    sapere cosa è cambiato, e ricarica.
 *
 *  Mai sotto le mani: con del testo scritto in un campo, il dito sullo schermo,
 *  un dialogo aperto o un turno che gira, la ricarica aspetta che sia tutto fermo. Un gateway di
 *  prima di questa versione non manda le due voci, e non succede niente.
 */

import { api } from './api-client.js';
import { wsManager } from './ws-manager.js';

/** Ogni quanto si riguarda se la pagina è ferma, con una ricarica in attesa. */
const IDLE_POLL_MS = 2000;
/** Dopo la riapertura, prima di decidere: il gateway rimanda `goal_status:
 *  running` dei turni ancora vivi quando la pagina si riaggancia, e una
 *  ricarica decisa prima di saperlo cadrebbe nel mezzo di un turno. */
const OPEN_SETTLE_MS = 1000;
/** Un processo nuovo ancora senza impronta: si richiede, poche volte. */
const PENDING_RETRY_MS = 2000;
const PENDING_RETRIES = 5;

const TEXT_FIELDS = 'textarea, input:not([type]), input[type="text"], input[type="search"], '
  + 'input[type="url"], input[type="email"], input[type="number"], [contenteditable="true"]';

/** C'è del testo scritto da qualche parte? Ricaricare lo butterebbe via. */
function typedSomewhere(doc = document) {
  for (const el of doc.querySelectorAll(TEXT_FIELDS)) {
    const text = el.isContentEditable ? el.textContent : el.value;
    if (text && text.trim()) return true;
  }
  return false;
}

export class UpdateWatch {
  /** @param opts `{ busy, reload }`: un'altra ragione del guscio per non
   *  ricaricare adesso, e il gesto della ricarica (di serie `api.reload()`,
   *  che si porta dietro il segreto). */
  constructor({ busy = () => false, reload = () => api.reload() } = {}) {
    this._busy = busy;
    this._reload = reload;
    this._loaded = null;
    this._running = new Set();
    this._pressed = 0;
    this._pending = false;
    this._timer = null;
    this._settle = null;
  }

  /** Si mette in ascolto. Va chiamata dopo il primo bootstrap della pagina. */
  start() {
    const info = api.getBootstrapInfo() || {};
    this._loaded = { version: info.ui_version || '', boot: info.boot || '' };
    wsManager.addEventListener('chat:open', () => {
      clearTimeout(this._settle);
      this._settle = setTimeout(() => this.check(), OPEN_SETTLE_MS);
    });
    // Un socket caduto non sa più quali turni girano: li ridice il gateway alla
    // riapertura (v. `OPEN_SETTLE_MS`).
    wsManager.addEventListener('chat:close', () => this._running.clear());
    wsManager.addEventListener('chat:message', (e) => this._readRun(e.detail));
    document.addEventListener('pointerdown', () => { this._pressed += 1; }, true);
    const lift = () => { this._pressed = Math.max(0, this._pressed - 1); };
    document.addEventListener('pointerup', lift, true);
    document.addEventListener('pointercancel', lift, true);
    return this;
  }

  /** `goal_status` dice quali conversazioni hanno un turno che gira. */
  _readRun(msg) {
    if (msg?.event !== 'goal_status') return;
    const key = msg.chat_id || '';
    if (msg.status === 'running') this._running.add(key);
    else this._running.delete(key);
  }

  /** Il gateway di adesso serve altri file? Allora, appena si può, si ricarica. */
  async check(retries = PENDING_RETRIES) {
    if (this._pending || !this._loaded) return;
    const fresh = await api.freshBootstrapInfo();
    if (!fresh || !fresh.boot) return;
    const loaded = this._loaded;
    const version = fresh.ui_version || '';
    if (loaded.version && version) {
      if (version !== loaded.version) this._reloadWhenIdle();
      // Gli stessi file da un processo ripartito: e' lui, d'ora in poi.
      else loaded.boot = fresh.boot;
      return;
    }
    if (fresh.boot === loaded.boot) {
      // Lo stesso processo con cui la pagina è partita: i suoi file sono i nostri.
      loaded.version ||= version;
      return;
    }
    if (!version && retries > 0) {
      // Un processo nuovo che sta ancora calcolando l'impronta.
      setTimeout(() => this.check(retries - 1), PENDING_RETRY_MS);
      return;
    }
    // Un processo nuovo, e non si sa con che file la pagina era partita.
    this._reloadWhenIdle();
  }

  /** Niente da perdere: nessun turno che gira, nessun dito giù, nessun testo
   *  scritto, nessun dialogo aperto (un foglio a metà), e il guscio d'accordo. */
  _idle() {
    return !this._running.size && !this._pressed && !typedSomewhere()
      && !document.querySelector('dialog[open]') && !this._busy();
  }

  _reloadWhenIdle() {
    this._pending = true;
    clearTimeout(this._timer);
    if (this._idle()) {
      this._reload();
      return;
    }
    this._timer = setTimeout(() => this._reloadWhenIdle(), IDLE_POLL_MS);
  }
}
