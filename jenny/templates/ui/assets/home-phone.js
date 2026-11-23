/* «Usa il telefono», nella stanza di Jenny: le mani sulle altre app.
 *
 * Tre cose, e sono i tre modi in cui si spegne o si tiene a bada:
 *
 *   - la modalita' — spenta, «chiede prima» (una volta per app, per
 *     richiesta), «non chiede» (i tasti che costano chiedono lo stesso);
 *   - le app su «Sempre», da togliere una alla volta;
 *   - il servizio di accessibilita' di Android, che si accende solo da li':
 *     qui lo si dice e ci si porta l'utente.
 *
 * Lo stato arriva dalla sezione `phone` di `/api/settings` (`phone_api.py`).
 * Dove il telefono non c'e' (`available` falso) la sezione non compare.
 */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { botName } from './shared/bot-name.js';

const MODES = ['off', 'confirm', 'free'];

export class HomePhone {
  constructor() {
    this.root = document.getElementById('home-jenny-phone');
    this.label = document.getElementById('home-jenny-phone-label');
    this.seg = document.getElementById('home-jenny-phone-mode');
    this.note = document.getElementById('home-jenny-phone-note');
    this.allow = document.getElementById('home-jenny-phone-allow');
    this.list = document.getElementById('home-jenny-phone-trusted');
    this.listTitle = document.getElementById('home-jenny-phone-trusted-title');
    this.section = null;
    this.seg?.addEventListener('click', (e) => {
      const btn = e.target.closest('.home-seg-btn');
      if (btn) this.pick(btn.dataset.mode);
    });
    this.allow?.addEventListener('click', () => this.openService());
    this.list?.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-forget]');
      if (btn) this.forget(btn.dataset.forget);
    });
    /* Il servizio si accende e si spegne in Android, fuori da qui: tornando
       nell'app lo stato si rilegge. Prima restava quello dell'apertura delle
       Impostazioni, e a servizio spento la pagina non lo diceva (Titan,
       23/11/2026). */
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') this.refresh();
    });
  }

  /** Rilegge la sezione dal server, se la pagina la sta mostrando. */
  async refresh() {
    if (!this.section?.available || !this.root || this.root.offsetParent === null) return;
    try {
      const payload = await api.getSettings();
      if (payload?.phone) this.set(payload.phone);
    } catch (err) {
      console.warn('home.phone: state not reread', err);
    }
  }

  /** La sezione `phone` di `/api/settings`, o `null` se non si sa. */
  set(section) {
    this.section = section;
    this.render();
  }

  async pick(mode) {
    if (!this.section || !MODES.includes(mode) || mode === this.section.mode) return;
    const before = this.section.mode;
    this.section = { ...this.section, mode };
    this.render();
    await this._update({ mode }, () => { this.section = { ...this.section, mode: before }; });
  }

  async forget(target) {
    if (!this.section) return;
    await this._update({ forget: target }, () => {});
  }

  async openService() {
    try {
      await api.openPhoneService();
    } catch (err) {
      console.warn('home.phone: accessibility settings not opened', err);
    }
  }

  async _update(params, undo) {
    try {
      const payload = await rpc.phone('update', params);
      if (payload?.phone) this.section = payload.phone;
    } catch (err) {
      console.warn('home.phone: not updated', err);
      undo();
    }
    this.render();
  }

  render() {
    if (!this.root) return;
    const s = this.section;
    this.root.hidden = !s?.available;
    if (!s?.available) return;
    const name = botName.get();
    if (this.label) this.label.textContent = i18n.t('settings.phone.title');
    if (this.seg) {
      if (!this.seg.children.length) {
        for (const mode of MODES) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'home-seg-btn';
          btn.dataset.mode = mode;
          btn.setAttribute('role', 'radio');
          this.seg.appendChild(btn);
        }
      }
      for (const btn of this.seg.children) {
        const on = btn.dataset.mode === s.mode;
        btn.textContent = i18n.t(`settings.phone.mode.${btn.dataset.mode}`);
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-checked', String(on));
      }
    }
    /* Una frase sola: cosa manca per farla funzionare, oppure cosa fa. */
    const needsService = s.mode !== 'off' && s.service === 'off';
    const unsupported = s.service === 'unsupported';
    if (this.note) {
      const key = unsupported ? 'settings.phone.unsupported'
        : needsService ? 'settings.phone.serviceOff'
        : `settings.phone.hint.${s.mode}`;
      this.note.textContent = i18n.t(key, { name });
      this.note.classList.toggle('is-warn', needsService || unsupported);
    }
    if (this.allow) {
      this.allow.hidden = !needsService;
      this.allow.textContent = i18n.t('settings.phone.openService');
    }
    const trusted = Array.isArray(s.trusted) ? s.trusted : [];
    if (this.listTitle) {
      this.listTitle.hidden = !trusted.length;
      this.listTitle.textContent = i18n.t('settings.phone.trusted', { name });
    }
    if (this.list) {
      this.list.hidden = !trusted.length;
      this.list.replaceChildren(...trusted.map((app) => {
        const row = document.createElement('div');
        row.className = 'home-row';
        const text = document.createElement('span');
        text.className = 'home-row-label';
        text.textContent = app.label || app.target;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'home-phone-forget';
        btn.dataset.forget = app.target;
        btn.textContent = i18n.t('settings.phone.forget');
        row.append(text, btn);
        return row;
      }));
    }
  }
}
