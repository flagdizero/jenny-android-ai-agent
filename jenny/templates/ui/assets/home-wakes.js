/** La casa — «troppi risvegli».
 *
 *  Jenny e l'utente programmano quel che vogliono: nessun limite, nessuna
 *  approvazione (dall'11/11/2026 anche un job puo' mettersi altri job). L'unico
 *  freno e' questo avviso. Ogni volta che un job sveglia il modello il gateway
 *  lo segna (`CronService.note_wake`); quando nelle ultime 24 ore sono piu' di
 *  `wakes.threshold`, sopra il campo compare una pastiglia, e un tocco apre il
 *  foglio che dice **chi** la sveglia di piu', con la pausa accanto.
 *
 *  **Una pastiglia sopra il campo, non un fumetto di Jenny**: scelta dall'utente
 *  fra tre varianti disegnate sulla casa vera. Sta dove sta lo stato del filo
 *  (`.home-wire`), allineata a sinistra perche' a destra c'e' lei.
 *
 *  **«Va bene cosi'» non spegne l'avviso per sempre**: ricorda a che numero l'hai
 *  accettato, e torna solo se i risvegli raddoppiano. Sotto la soglia il ricordo
 *  si cancella, cosi' la prossima salita si rivede. Sta in `localStorage`: e'
 *  una comodita' di chi guarda, non uno stato del gateway.
 */

import { api } from './shared/api-client.js';
import { i18n, tr } from './shared/i18n.js';
import { botName } from './shared/bot-name.js';
import { describeSchedule } from './shared/cron-view.js';
import { button, el, icon } from './shared/dom.js';
import { closeActionSheet, openActionSheet, sheetHead } from './shared/sheet.js';

export const ACK_KEY = 'jenny.wakes.ack';
/** Dopo un «Va bene cosi'» l'avviso torna quando i risvegli sono questo multiplo. */
const ACK_GROWTH = 2;
/** Quante righe mostra il foglio: chi sveglia di piu', non l'elenco intero. */
const MAX_ROWS = 6;
const REFRESH_MS = 5 * 60_000;

function readAck() {
  try {
    const ack = JSON.parse(window.localStorage.getItem(ACK_KEY) || 'null');
    return ack && Number(ack.total) > 0 ? ack : null;
  } catch { return null; }
}

function writeAck(ack) {
  try {
    if (ack) window.localStorage.setItem(ACK_KEY, JSON.stringify(ack));
    else window.localStorage.removeItem(ACK_KEY);
  } catch { /* senza memoria l'avviso torna al prossimo giro: va bene */ }
}

/** Se l'avviso va mostrato: sopra la soglia, e non gia' accettato a quel livello. */
export function shouldAlert(wakes, ack) {
  const total = Number(wakes?.total) || 0;
  const threshold = Number(wakes?.threshold) || 0;
  if (!threshold || total < threshold) return false;
  const accepted = Number(ack?.total) || 0;
  return !accepted || total >= accepted * ACK_GROWTH;
}

/** Le righe del foglio: i risvegli per job, dal piu' frequente, col ritmo e i
 *  gesti di quelli che ci sono ancora. Un singolo gia' scattato resta col suo
 *  nome, senza gesti (`gone`). */
export function wakeRows(payload, { locale } = {}) {
  const jobs = new Map((payload?.jobs || []).map((job) => [job.id, job]));
  return (payload?.wakes?.jobs || []).slice(0, MAX_ROWS).map((wake) => {
    const job = jobs.get(wake.id) || null;
    const actions = job?.actions || [];
    return {
      id: wake.id,
      name: job?.name || wake.name || '',
      count: Number(wake.count) || 0,
      autonomous: Boolean(wake.autonomous),
      gone: !job,
      schedule: job
        ? describeSchedule(job.schedule, { tr, locale, timeZone: job.display_timezone })
        : '',
      paused: Boolean(job?.paused_at_ms),
      canPause: actions.includes('pause'),
      canResume: actions.includes('resume'),
      canRemove: actions.includes('remove'),
    };
  });
}

/** La frase sotto il numero: chi fa il grosso, se c'e' un primo netto. */
export function wakesNote(rows, total) {
  const top = rows[0];
  const base = i18n.t('home.wakes.note');
  if (!top || top.count * 2 < total) return base;
  return `${base} ${i18n.t(top.autonomous ? 'home.wakes.noteOwn' : 'home.wakes.noteYours')}`;
}

export class HomeWakes {
  /** @param pill   `#home-wakes`, la pastiglia sopra il campo.
   *  @param sheet  `#home-wakes-sheet`, il foglio.
   *  @param onAll  «Tutti i promemoria»: apre l'elenco intero (l'officina). */
  constructor({ pill, sheet, onAll = null } = {}) {
    this.pill = pill || document.getElementById('home-wakes');
    this.sheet = sheet || document.getElementById('home-wakes-sheet');
    this.onAll = onAll;
    this.payload = null;
    this.open = null;      // l'id della riga aperta (mostra «Togli»)
    this.busy = false;
    this.pill?.addEventListener('click', () => this.show());
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'hidden') this.refresh();
    });
    window.setInterval(() => {
      if (document.visibilityState !== 'hidden') this.refresh();
    }, REFRESH_MS);
  }

  /** Rilegge il conto dal gateway e ridisegna pastiglia e foglio. */
  async refresh() {
    try {
      this.payload = await api.getCron();
    } catch {
      return;   // il gateway non risponde: la pastiglia resta com'era
    }
    this._draw();
  }

  _draw() {
    const wakes = this.payload?.wakes || null;
    const total = Number(wakes?.total) || 0;
    const threshold = Number(wakes?.threshold) || 0;
    /* Tornati sotto la soglia: il «Va bene cosi'» di prima non vale piu'. */
    if (threshold && total < threshold) writeAck(null);
    const alert = shouldAlert(wakes, readAck());
    if (this.pill) {
      this.pill.hidden = !alert;
      if (alert) {
        const count = el('b', null, i18n.t('home.wakes.pillCount', { n: total }));
        const text = el('span', 'home-wakes-text');
        text.append(count, ` ${i18n.t('home.wakes.pillWhen')}`);
        this.pill.replaceChildren(icon('ti-alarm'), text,
          el('span', 'home-wakes-go', i18n.t('home.wakes.pillGo')));
      }
    }
    if (this.sheet?.open) this._fill();
  }

  /** Apre il foglio (dalla pastiglia). */
  show() {
    this.open = null;
    this._fill();
    this.refresh();
  }

  _fill() {
    const total = Number(this.payload?.wakes?.total) || 0;
    const rows = wakeRows(this.payload, { locale: i18n.locale });
    const big = el('div', 'home-wakes-count', String(total));
    big.appendChild(el('small', null, i18n.t('home.wakes.countLabel')));
    const list = el('div', 'home-wakes-list');
    const max = Math.max(1, ...rows.map((row) => row.count));
    rows.forEach((row, index) => list.appendChild(this._row(row, max, index === 0)));
    const foot = el('div', 'home-wakes-foot');
    foot.append(
      button('home-wakes-link', i18n.t('home.wakes.all'), () => {
        closeActionSheet(this.sheet);
        this.onAll?.();
      }),
      button('home-wakes-ok', i18n.t('home.wakes.ok'), () => this._accept()),
    );
    openActionSheet(this.sheet, {
      closeLabel: i18n.t('home.wakes.close'),
      onClose: () => closeActionSheet(this.sheet),
      children: [
        ...sheetHead(i18n.t('home.wakes.title')),
        big,
        el('p', 'home-plan-sheet-note home-wakes-note', wakesNote(rows, total)),
        list,
        foot,
      ],
    });
  }

  _row(row, max, first) {
    const node = el('div', `home-wakes-row${row.paused ? ' is-paused' : ''}`);
    node.dataset.job = row.id || '';
    /* Il testo e' un bottone solo se c'e' qualcosa da aprire: «Togli». */
    const text = row.canRemove
      ? button('home-wakes-row-text', null, () => {
        this.open = this.open === row.id ? null : row.id;
        this._fill();
      })
      : el('div', 'home-wakes-row-text');
    text.appendChild(el('span', 'home-wakes-row-name', row.name));
    const when = el('span', 'home-wakes-row-when',
      row.gone ? i18n.t('home.wakes.gone')
        : row.paused ? `${row.schedule} · ${i18n.t('board.cron.paused')}` : row.schedule);
    if (row.autonomous) {
      when.appendChild(el('span', 'home-wakes-sign', i18n.t('cron.job.by', { name: botName.get() })));
    }
    text.appendChild(when);
    const bar = el('span', `home-wakes-bar${first ? ' is-top' : ''}`);
    const fill = el('i');
    fill.style.width = `${Math.max(3, Math.round((100 * row.count) / max))}%`;
    bar.appendChild(fill);
    text.appendChild(bar);
    node.appendChild(text);
    node.appendChild(el('span', `home-wakes-row-n${first ? ' is-top' : ''}`, `${row.count}×`));
    if (row.canPause || row.canResume) {
      const act = button('home-wakes-act', null,
        () => this._act(row.id, row.canResume ? 'resume' : 'pause'));
      act.setAttribute('aria-label',
        i18n.t(row.canResume ? 'board.micro.resume' : 'board.micro.pause'));
      act.appendChild(icon(row.canResume ? 'ti-player-play' : 'ti-player-pause'));
      node.appendChild(act);
    } else {
      node.appendChild(el('span', 'home-wakes-act is-none'));
    }
    if (row.canRemove && this.open === row.id) {
      node.appendChild(button('home-wakes-remove', i18n.t('home.wakes.remove'),
        () => this._act(row.id, 'remove')));
    }
    return node;
  }

  async _act(id, action) {
    if (this.busy || !id) return;
    this.busy = true;
    try {
      await api.cronJobAction(id, action);
    } catch { /* il rifiuto lo dice il prossimo `refresh`: la riga resta com'era */ }
    this.busy = false;
    this.open = null;
    await this.refresh();
  }

  /** «Va bene cosi'»: ricorda a che numero, e chiude. */
  _accept() {
    const total = Number(this.payload?.wakes?.total) || 0;
    if (total > 0) writeAck({ total, at: Date.now() });
    closeActionSheet(this.sheet);
    this._draw();
  }
}
