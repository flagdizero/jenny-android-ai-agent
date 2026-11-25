/** La casa — «Promemoria».
 *
 *  Tutto quel che Jenny fara' dopo, nella chat personale: un orario, un ritmo,
 *  un arrivo in un posto, un riepilogo chiesto a un'altra Jenny. Fino al
 *  25/11/2026 in casa si vedevano solo quando erano troppi (la pastiglia dei
 *  risvegli, `home-wakes.js`), e il suo «Tutti i promemoria» portava in
 *  officina.
 *
 *  Due gruppi: quelli **chiesti da te** e quelli **messi da Jenny** da sola,
 *  svegliata da un altro promemoria (`payload.autonomous`). Ognuno con
 *  l'interruttore della pausa e il cestino; il cestino toglie subito e offre
 *  «Annulla» (`CronService.restore_job`), senza domande prima.
 *
 *  **Cosa resta fuori.** I promemoria dei quaderni, che stanno sulle loro
 *  bacheche; i lavori di sistema (Dream, il giardiniere), che non sono
 *  dell'utente. Un promemoria nuovo si chiede in chat: questa stanza serve a
 *  vederli e a fermarli.
 *
 *  I dati sono gli stessi della pastiglia (`/api/webui/cron`), e le righe le
 *  costruisce la stessa vista condivisa dell'officina (`shared/cron-view.js`).
 */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { i18n, tr } from './shared/i18n.js';
import { botName } from './shared/bot-name.js';
import { buildCronView } from './shared/cron-view.js';
import { button, el, icon } from './shared/dom.js';
import { showToast, showUndo } from './shared/utils.js';

/** I promemoria che questa stanza mostra: dell'utente, della chat personale. */
export function isPersonalReminder(job) {
  return Boolean(job) && job.kind !== 'system' && !job.place;
}

/** Quanti sono attivi (accesi, non in pausa, non gia' conclusi): il numero
 *  sulla riga di Impostazioni. `null` se non si sa. */
export function activeReminders(payload) {
  if (!payload || payload.available === false || !Array.isArray(payload.jobs)) return null;
  return payload.jobs
    .filter(isPersonalReminder)
    .filter((job) => job.enabled !== false && !job.paused_at_ms).length;
}

/** Le due sezioni della stanza, dalle righe della vista condivisa. */
export function reminderSections(payload, { locale } = {}) {
  const view = buildCronView(payload, { tr, locale, keep: isPersonalReminder });
  const enabled = new Map((payload?.jobs || []).map((job) => [job.id, job.enabled !== false]));
  const rows = view.rows.map((row) => ({
    ...row,
    paused: row.pausedAtMs != null,
    /* Un singolo gia' suonato: spento ma non in pausa. Resta finche' non lo
       togli, senza interruttore. */
    done: !enabled.get(row.id) && row.pausedAtMs == null,
    canToggle: row.actions.includes('pause') || row.actions.includes('resume'),
    canRemove: row.actions.includes('remove'),
  }));
  return {
    available: view.available,
    mine: rows.filter((row) => !row.autonomous),
    hers: rows.filter((row) => row.autonomous),
  };
}

/** La riga sotto il nome: quando, e cosa succede adesso. */
export function reminderWhen(row) {
  if (row.paused) return `${row.schedule} · ${i18n.t('board.cron.paused')}`;
  if (row.done) return `${row.schedule} · ${i18n.t('home.reminders.done')}`;
  if (row.next) return `${row.schedule} · ${i18n.t('home.reminders.next', { when: row.next })}`;
  return row.schedule;
}

export class HomeReminders {
  /** @param onChange  quel che conta e' cambiato: la riga di Impostazioni si
   *                   riscrive (il numero degli attivi). */
  constructor({ onChange } = {}) {
    this.body = document.getElementById('home-reminders-body');
    this.onChange = onChange;
    this.payload = null;
    this.busy = false;
    this._failed = false;
  }

  /** La stanza si apre: si rilegge, e intanto si mostra quel che si sapeva. */
  open() {
    this.render();
    this.refresh();
  }

  /** Il payload arrivato da un altro lettore (la pastiglia rilegge ogni 5
   *  minuti): niente seconda richiesta per la riga di Impostazioni. */
  setPayload(payload) {
    this.payload = payload || null;
    this._failed = false;
    this.render();
  }

  /** Il valore della riga di Impostazioni: «5 attivi», «nessuno». */
  value() {
    const n = activeReminders(this.payload);
    if (n == null) return '';
    return n ? i18n.t('home.reminders.active', { n }) : i18n.t('home.reminders.none');
  }

  async refresh() {
    try {
      this.payload = await api.getCron();
      this._failed = false;
    } catch {
      this._failed = true;
    }
    this.render();
    this.onChange?.(this.payload);
  }

  render() {
    if (!this.body) return;
    if (!this.payload) {
      this.body.replaceChildren(el('div', 'home-card-note',
        i18n.t(this._failed ? 'home.reminders.unavailable' : 'home.reminders.loading')));
      return;
    }
    const { available, mine, hers } = reminderSections(this.payload, { locale: i18n.locale });
    if (!available) {
      this.body.replaceChildren(el('div', 'home-card-note', i18n.t('home.reminders.unavailable')));
      return;
    }
    const parts = [];
    if (!mine.length && !hers.length) {
      const empty = el('div', 'home-reminders-empty');
      empty.append(icon('ti-alarm'), el('p', null, i18n.t('home.reminders.empty')));
      parts.push(empty);
    }
    if (mine.length) parts.push(this._section(i18n.t('home.reminders.mine'), mine));
    if (hers.length) {
      parts.push(this._section(i18n.t('home.reminders.hers', { name: botName.get() }), hers));
    }
    /* Vuota, la frase di sopra dice gia' come chiederne uno: sotto resta solo
       dove stanno quelli dei quaderni (visto su jenny_s22 il 25/11/2026). */
    const empty = !mine.length && !hers.length;
    parts.push(el('p', 'home-reminders-foot',
      i18n.t(empty ? 'home.reminders.notebooksElsewhere' : 'home.reminders.howToAdd')));
    this.body.replaceChildren(...parts);
  }

  _section(title, rows) {
    const wrap = el('div', 'home-reminders-section');
    wrap.appendChild(el('div', 'home-reminders-sec', title));
    const list = el('div', 'home-rows home-reminders-list');
    for (const row of rows) list.appendChild(this._row(row));
    wrap.appendChild(list);
    return wrap;
  }

  _row(row) {
    const node = el('div', `home-row home-reminders-row${row.paused || row.done ? ' is-off' : ''}`);
    node.dataset.job = row.id;
    node.appendChild(icon(row.zoneWatch ? 'ti-map-pin' : row.monitor ? 'ti-eye' : 'ti-alarm'));
    const text = el('span', 'home-row-label');
    text.appendChild(el('span', 'home-reminders-name', row.name));
    text.appendChild(el('span', 'home-reminders-when', reminderWhen(row)));
    node.appendChild(text);
    if (row.canToggle) {
      const on = !row.paused;
      const sw = button(`home-switch${on ? ' is-on' : ''}`, null,
        () => this._act(row.id, on ? 'pause' : 'resume'));
      sw.setAttribute('role', 'switch');
      sw.setAttribute('aria-checked', on ? 'true' : 'false');
      sw.setAttribute('aria-label', i18n.t('home.reminders.toggle', { name: row.name }));
      sw.appendChild(el('span', 'home-switch-knob'));
      node.appendChild(sw);
    }
    if (row.canRemove) {
      const trash = button('home-reminders-remove', null, () => this._remove(row));
      trash.setAttribute('aria-label', i18n.t('home.reminders.remove', { name: row.name }));
      trash.appendChild(icon('ti-trash'));
      node.appendChild(trash);
    }
    return node;
  }

  async _act(id, action) {
    if (this.busy || !id) return false;
    this.busy = true;
    let ok = true;
    try {
      await api.cronJobAction(id, action);
    } catch (err) {
      ok = false;
      /* 409 su una ripresa: un singolo la cui ora e' passata in pausa. Si
         puo' solo togliere, e la frase lo dice. */
      showToast(i18n.t(err?.status === 409 ? 'cron.action.expired' : 'cron.action.failed'), 'error');
    }
    this.busy = false;
    await this.refresh();
    return ok;
  }

  /** Il cestino: via subito, e «Annulla» lo rimette com'era — lo stesso
   *  job, con il suo id e la sua storia (`cron.restore`). */
  async _remove(row) {
    if (!(await this._act(row.id, 'remove'))) return;
    showUndo(i18n.t('home.reminders.removed', { name: row.name }),
      () => this._restore(row.id),
      { label: i18n.t('apps.undo') });
  }

  async _restore(id) {
    try {
      await rpc.cron('restore', { id });
    } catch {
      showToast(i18n.t('cron.action.failed'), 'error');
    }
    await this.refresh();
  }
}
