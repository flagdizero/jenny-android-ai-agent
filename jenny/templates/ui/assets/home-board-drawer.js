/** La casa — il cassetto di una scheda della bacheca.
 *
 *  Il retro di una polaroid: un `index.md` per quel soggetto, scritto da Jenny
 *  dalla wiki. In alto «in breve»; poi il **calendario di cura**, le attese
 *  della scheda una volta sola, per mese; poi le sezioni dello **scheletro del
 *  quaderno** (le stesse per ogni scheda, nello stesso ordine: l'utente sa
 *  sempre dove guardare), chiuse a fisarmonica con la prima riga in vista; poi
 *  le sezioni che hai aggiunto tu; poi i fili. Aggiungere passa dal «+»
 *  flottante accanto a Jenny, che si apre a ventaglio.
 *
 *  Fino al 12/10/2026 un'attesa compariva fino a quattro volte (il riquadro
 *  «Prossima cosa», una sezione «Promemoria» con Fatto/Togli/↑, e le date
 *  ripetute nel riassunto), e «+ Aggiungi sezione» era testo nudo in fondo.
 *
 *  **Le tue modifiche sono richieste, non scritture.** «Correggi» non riscrive
 *  la riga: apre una segnalazione sulla pagina da cui viene (`board.fix`), e la
 *  riga mostra il tuo testo in corsivo con la graffetta finche' il giardiniere
 *  non l'ha integrato nella wiki — poi «✓ Integrata», e il riassunto che si
 *  vede torna a essere quello rigenerato dalla wiki.
 *
 *  Le sezioni dell'utente invece sono sue: una nota resta come l'hai scritta
 *  (e diventa una riga di diario), un promemoria e' un'attesa, una mini app e'
 *  una finestra sui dati di un'app con la sua firma — ora e fonte —, una foto
 *  e' una foto.
 */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { showToast } from './shared/utils.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';
import { reminderGroups, whenLabel } from './home-board.js';
import { describeSchedule } from './shared/cron-view.js';
import { renderBlock } from './home-board-blocks.js';

/* Il lato lungo di una foto mandata al server: il telefono ne fa di 4000 px, e
   in un cassetto largo 590 ne servono meno di un terzo. */
const PHOTO_MAX_SIDE = 1600;

/** «oggi alle 04:12», «ieri», «3 ott»: quando e' stato riscritto il cassetto. */
export function relativeStamp(iso, now = new Date(), locale = i18n.locale) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return '';
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((day(now) - day(at)) / 86_400_000);
  const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(at);
  if (diff === 0) return i18n.t('board.drawer.today', { time });
  if (diff === 1) return i18n.t('board.drawer.yesterday', { time });
  return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(at);
}

/** Quanto manca a *date* (`AAAA-MM-GG`), a parole: «tra 3 settimane», «ieri».
 *  Giorni fino a due settimane, poi settimane fino a due mesi, poi mesi. */
export function untilLabel(date, today = new Date(), locale = i18n.locale) {
  const [y, m, d] = String(date || '').split('-').map(Number);
  if (!y || !m) return '';
  const from = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((new Date(y, m - 1, d || 1) - from) / 86_400_000);
  const abs = Math.abs(days);
  const [n, unit] = abs < 14 ? [days, 'day'] : abs < 60 ? [Math.round(days / 7), 'week']
    : [Math.round(days / 30), 'month'];
  try {
    return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(n, unit);
  } catch (_) {
    return '';
  }
}

/** Un andamento come una riga sola: niente assi, niente librerie. */
export function sparkline(values, width = 84, height = 18) {
  const nums = (values || []).filter((v) => Number.isFinite(v));
  if (nums.length < 2) return '';
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const span = hi - lo || 1;
  const step = width / (nums.length - 1);
  const points = nums
    .map((v, i) => `${(i * step).toFixed(1)},${(height - 1 - ((v - lo) / span) * (height - 2)).toFixed(1)}`)
    .join(' ');
  return `<svg class="home-drawer-spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true"><polyline points="${points}"/></svg>`;
}

/** Riduce una foto prima di mandarla: JPEG, lato lungo `PHOTO_MAX_SIDE`. */
async function shrinkPhoto(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
  const buffer = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (let i = 0; i < buffer.length; i += 0x8000) {
    binary += String.fromCharCode(...buffer.subarray(i, i + 0x8000));
  }
  return { mime: 'image/jpeg', data: btoa(binary) };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, text, onClick) {
  const b = el('button', className, text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

export class BoardDrawer {
  /**
   * @param onOpenPage   `(path, label, cardId)` — una pagina nel lettore.
   * @param onOpenCard   `(cardId, title)` — l'altro capo di un filo.
   * @param onAskJenny   `(text)` — «Altro…»: una frase gia' scritta in chat.
   * @param onOpenApp    `(slug)` — «Apri l'app».
   * @param onRenameCard `(cardId, title)` — «Rinomina», in fondo al cassetto.
   * @param onRemoveCard `(cardId, title)` — «Togli dalla bacheca», in fondo.
   * @param sheet        il foglio della bacheca, per i moduli e le azioni.
   */
  constructor({ onOpenPage, onOpenCard, onAskJenny, onOpenApp, onRenameCard, onRemoveCard,
    onAppCard, sheet } = {}) {
    this.el = document.getElementById('home-board-drawer');
    this.onOpenPage = onOpenPage;
    this.onOpenCard = onOpenCard;
    this.onAskJenny = onAskJenny;
    this.onOpenApp = onOpenApp;
    this.onRenameCard = onRenameCard;
    this.onRemoveCard = onRemoveCard;
    /** `(cardId)` — un'app e' nata accanto a questa scheda: si torna a vederla. */
    this.onAppCard = onAppCard;
    this.sheet = sheet;
    this.notebook = null;
    this.card = null;
    this.data = null;
    /* Dove eri nel cassetto: si legge dallo scroll mentre si vede (nascosto
       dietro una pagina lo scroll si azzera, e quel valore non vale). */
    this._top = 0;
    this.el?.addEventListener('scroll', () => {
      if (this.el.clientHeight) this._top = this.el.scrollTop;
    });
    /** La sezione che si sta correggendo, per titolo. */
    this.editing = null;
    /** Le sezioni dello scheletro aperte, per titolo: chiuse a ogni cassetto nuovo. */
    this.unfolded = new Set();
    this._token = 0;
    this._photoUrls = [];
    this._buildFab();
  }

  /* ── Il «+» flottante e il suo ventaglio ── */

  /** Il tondo e il ventaglio stanno nel guscio della casa, non nel cassetto che
   *  scorre: fermi sullo schermo, nell'angolo del + dei Quaderni. Niente
   *  `z-index` (in casa l'unico e' di Jenny): vengono dopo il cassetto nel DOM,
   *  e prima della mascotte, che col suo resta sopra anche al velo. Si vedono
   *  solo quando la stanza e' il cassetto (`home-style.css`). */
  _buildFab() {
    const shell = this.el?.closest('.home-shell');
    if (!shell) return;
    this.dial = el('div', 'home-drawer-dial');
    this.dial.hidden = true;
    this.dial.addEventListener('click', (e) => {
      if (e.target === this.dial) this.closeDial();
    });
    this.dial.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        this.closeDial();
        this.fab.focus();
      }
    });
    this.dialMenu = el('div', 'home-drawer-dial-menu');
    this.dialMenu.setAttribute('role', 'menu');
    this.dial.appendChild(this.dialMenu);
    /* Da tastiera (un click con `detail` 0) il fuoco va alla prima scelta; col
       dito no, o l'anello del fuoco resterebbe acceso su «Promemoria». */
    this.fab = button('home-drawer-fab', '', (e) => (this.dial.hidden
      ? this.openDial({ focus: e.detail === 0 }) : this.closeDial()));
    this.fab.setAttribute('aria-haspopup', 'menu');
    this.fab.setAttribute('aria-expanded', 'false');
    this.fab.innerHTML = '<i class="ti ti-plus" aria-hidden="true"></i>';
    this._labelFab();
    const mascot = shell.querySelector(':scope > .jenny-duo');
    shell.insertBefore(this.dial, mascot);
    shell.insertBefore(this.fab, mascot);
  }

  _labelFab() {
    if (!this.fab) return;
    const open = !this.dial.hidden;
    this.fab.setAttribute('aria-label', i18n.t(open ? 'board.section.close' : 'board.section.addFab'));
    this.fab.classList.toggle('is-open', open);
    this.fab.setAttribute('aria-expanded', String(open));
  }

  /** Le cose che si aggiungono a un cassetto: le stesse, con gli stessi moduli,
   *  del menu che c'era prima nel foglio. */
  _addChoices() {
    return [
      ['reminder', 'ti-clock', () => this._addReminder()],
      ['note', 'ti-pencil', () => this._addNote()],
      ['micro', 'ti-gauge', () => this._addMicro()],
      ['photo', 'ti-photo', () => this._addPhoto()],
      ['cron', 'ti-repeat', () => this.onAskJenny?.(i18n.t('board.cron.prompt', { card: this.data.title }))],
      ['other', 'ti-message', () => this.onAskJenny?.(i18n.t('board.section.otherPrompt', { card: this.data.title }))],
    ];
  }

  /** Apre il ventaglio: il cassetto si vela, le scelte salgono dal tondo. */
  openDial({ focus = true } = {}) {
    if (!this.dial || !this.data) return;
    this.dialMenu.innerHTML = '';
    /* Dal basso: la prima scelta e' la piu' vicina al pollice. */
    for (const [kind, icon, run] of this._addChoices().reverse()) {
      const row = button(`home-drawer-dial-row is-${kind}`, '', () => {
        this.closeDial();
        run();
      });
      row.setAttribute('role', 'menuitem');
      const badge = el('span', 'home-drawer-dial-icon');
      const i = el('i', `ti ${icon}`);
      i.setAttribute('aria-hidden', 'true');
      badge.appendChild(i);
      row.append(el('span', 'home-drawer-dial-name', i18n.t(`board.section.kind.${kind}`)), badge);
      this.dialMenu.appendChild(row);
    }
    this.dial.hidden = false;
    this._labelFab();
    if (focus) this.dialMenu.lastElementChild?.focus();
  }

  /** Chiude il ventaglio. Vero se era aperto (per Indietro). */
  closeDial() {
    if (!this.dial || this.dial.hidden) return false;
    this.dial.hidden = true;
    this._labelFab();
    return true;
  }

  get title() {
    return this.data?.title || '';
  }

  /** Apre il cassetto *card* del quaderno *notebook*.
   *
   *  *keepPlace*: si torna a un cassetto gia' aperto — da una sua pagina —
   *  e lo si ritrova com'era, dove eri, senza il lampo di «caricamento»: si
   *  rilegge sotto, e si ridisegna al suo posto. Fino al 10/10/2026 ogni
   *  ritorno svuotava il cassetto e lo riportava in cima. */
  async load(notebook, card, { keepPlace = false } = {}) {
    const token = ++this._token;
    const same = keepPlace && this.data && this.notebook === notebook && this.card === card;
    const top = same ? this._top : 0;
    this.notebook = notebook;
    this.card = card;
    this.editing = null;
    this.closeDial();
    if (!same) this.unfolded = new Set();
    if (same) {
      this.render();
      this.el.scrollTop = top;
    } else {
      this.el.innerHTML = '';
      this.el.appendChild(el('p', 'home-drawer-note', i18n.t('board.drawer.loading')));
    }
    let data;
    try {
      data = await api.getBoardCard(notebook, card);
    } catch (err) {
      if (token !== this._token) return null;
      console.warn('home.drawer: card failed', err);
      this.el.innerHTML = '';
      this.el.appendChild(el('p', 'home-drawer-note', i18n.t('board.drawer.failed')));
      return null;
    }
    if (token !== this._token) return null;
    this.data = data;
    this.render();
    this.el.scrollTop = top;
    /* Aperto adesso: il puntino blu della scheda e quelli delle sezioni si
       spengono alla prossima apertura della bacheca. */
    rpc.board('seen', notebook, { card }).catch((err) => console.warn('home.drawer: seen failed', err));
    return data.title;
  }

  /** Ricarica lo stesso cassetto, tenendo dove si era. */
  async reload() {
    const top = this.el.scrollTop;
    await this.load(this.notebook, this.card);
    this.el.scrollTop = top;
  }

  applyTranslations() {
    this._labelFab();
    if (this.data) this.render();
  }

  render() {
    for (const url of this._photoUrls) URL.revokeObjectURL(url);
    this._photoUrls = [];
    const d = this.data;
    const root = el('div', 'home-drawer');
    const head = el('div', 'home-drawer-head');
    head.appendChild(el('h2', 'home-drawer-title', d.title));
    const meta = [i18n.t(d.pages.length === 1 ? 'board.drawer.pagesOne' : 'board.drawer.pagesMany',
      { count: d.pages.length })];
    if (d.generated) meta.push(i18n.t('board.drawer.updated', { when: relativeStamp(d.generated) }));
    head.appendChild(el('div', 'home-drawer-meta', meta.join(' · ')));
    root.appendChild(head);

    if (d.brief) root.appendChild(el('p', 'home-drawer-brief', d.brief));
    for (const s of d.userSections) {
      const state = s.view?.state;
      if (s.kind === 'app' && (state === 'alarm' || state === 'stale')) {
        root.appendChild(el('div', 'home-drawer-alert',
          i18n.t(`board.app.alert.${state}`, { app: s.view.name || s.app })));
      }
    }
    if (d.reminders?.length) root.appendChild(this._calendar(d.reminders));

    const sections = el('div', 'home-drawer-sections');
    if (!d.sections.length) {
      sections.appendChild(el('p', 'home-drawer-note', i18n.t('board.drawer.noSummary')));
    }
    for (const section of d.sections) sections.appendChild(this._section(section));
    for (const section of d.userSections) {
      const node = this._userSection(section);
      if (node) sections.appendChild(node);
    }
    root.appendChild(sections);

    if (d.connections.length) {
      const threads = el('div', 'home-drawer-threads');
      threads.appendChild(el('h4', 'home-drawer-h4', i18n.t('board.drawer.threads')));
      for (const c of d.connections) {
        const line = el('div', 'home-drawer-thread-line');
        const row = button('home-drawer-thread', '', () => this.onOpenCard?.(c.to, c.title));
        row.append(el('span', 'home-drawer-thread-mark', '—'), el('span', 'home-drawer-thread-to', c.title),
          el('span', 'home-drawer-thread-why', c.label));
        /* Un filo lo toglie l'utente, sempre; Jenny mai (propone). */
        const cut = button('home-drawer-link is-cut', '×', () => this._cutThread(c));
        cut.setAttribute('aria-label', i18n.t('board.drawer.threadRemove', { card: c.title }));
        line.append(row, cut);
        threads.appendChild(line);
      }
      root.appendChild(threads);
    }

    if (d.pages.length) {
      const pages = el('details', 'home-drawer-pages');
      pages.appendChild(el('summary', '', i18n.t('board.drawer.allPages', { count: d.pages.length })));
      for (const p of d.pages) {
        pages.appendChild(button('home-drawer-chip', `→ ${p.title}`,
          () => this.onOpenPage?.(p.path, p.title, d.id)));
      }
      root.appendChild(pages);
    }
    /* Le stesse azioni della barra che sale tenendo premuta la scheda: chi non
       conosce il gesto le trova qui, in fondo, dove si arriva comunque. */
    const own = el('div', 'home-drawer-own');
    own.append(
      button('home-drawer-link', i18n.t('board.card.rename'), () => this.onRenameCard?.(d.id, d.title)),
      button('home-drawer-link', i18n.t('board.card.remove'), () => this.onRemoveCard?.(d.id, d.title)),
    );
    root.appendChild(own);
    root.appendChild(el('p', 'home-drawer-foot', i18n.t('board.drawer.foot')));
    this.el.innerHTML = '';
    this.el.appendChild(root);
    if (this.editing) {
      const area = this.el.querySelector('.home-drawer-fix-area');
      area?.focus();
      area?.setSelectionRange(area.value.length, area.value.length);
    }
  }

  /* ── Il calendario di cura ── */

  /** Le attese della scheda su una linea del tempo: quelle in ritardo, «oggi»,
   *  un gruppo per mese, quelle a condizione. La prima e' la carta gialla con
   *  «Fatto»; le altre sono righe, con ⋯ per correggerle o toglierle. */
  _calendar(rems) {
    const box = el('section', 'home-drawer-cal');
    box.setAttribute('aria-labelledby', 'home-drawer-cal-title');
    const title = el('h3', 'home-drawer-h4', i18n.t('board.drawer.calendar.title'));
    title.id = 'home-drawer-cal-title';
    box.appendChild(title);
    const line = el('div', 'home-drawer-cal-line');
    const today = new Date();
    const groups = reminderGroups(rems, today);
    const first = rems[0];
    const todayMark = () => el('div', 'home-drawer-cal-today',
      i18n.t('board.drawer.calendar.today', {
        date: new Intl.DateTimeFormat(i18n.locale, { day: 'numeric', month: 'short' }).format(today),
      }));
    if (groups[0]?.kind !== 'overdue') line.appendChild(todayMark());
    for (const group of groups) {
      line.appendChild(el('div', `home-drawer-cal-group is-${group.kind}`, group.kind === 'month'
        ? group.label : i18n.t(`board.drawer.calendar.${group.kind}`)));
      for (const rem of group.items) {
        line.appendChild(rem === first ? this._calNext(rem, group.kind) : this._calRow(rem, group.kind));
      }
      if (group.kind === 'overdue') line.appendChild(todayMark());
    }
    box.appendChild(line);
    return box;
  }

  /** La prossima attesa: la carta gialla, col tasto «Fatto» a portata. */
  _calNext(rem, kind) {
    const card = el('div', `home-drawer-cal-next${kind === 'overdue' ? ' is-late' : ''}`);
    const key = [whenLabel(rem.when)];
    if (rem.when?.kind === 'date' && rem.when.precision !== 'month') key.push(untilLabel(rem.when.date));
    card.append(el('div', 'home-drawer-cal-next-key', key.filter(Boolean).join(' · ')),
      el('p', 'home-drawer-cal-next-text', rem.text));
    const bar = el('div', 'home-drawer-cal-next-bar');
    bar.append(button('home-drawer-cal-done', i18n.t('board.reminder.done'), () => this._done(rem.id)),
      this._remMore(rem));
    card.appendChild(bar);
    return card;
  }

  /** Un'altra attesa: il giorno (il mese lo dice il gruppo), il testo, ⋯. */
  _calRow(rem, kind) {
    const row = el('div', `home-drawer-cal-row${kind === 'overdue' ? ' is-late' : ''}`);
    const when = rem.when || {};
    let day = whenLabel(when);
    if (kind === 'month') day = when.precision === 'month' ? '—' : String(Number(String(when.date).slice(8, 10)));
    row.append(el('span', 'home-drawer-cal-day', day), el('span', 'home-drawer-cal-text', rem.text),
      this._remMore(rem));
    return row;
  }

  _remMore(rem) {
    const more = button('home-drawer-cal-more', '', () => this._reminderActions(rem));
    more.setAttribute('aria-label', i18n.t('board.reminder.actions', { text: rem.text }));
    more.innerHTML = '<i class="ti ti-dots" aria-hidden="true"></i>';
    return more;
  }

  /** ⋯ di un'attesa: Correggi, Fatto, Togli — nel foglio, come ogni scelta. */
  _reminderActions(rem) {
    const menu = el('div', 'home-board-menu');
    const items = [
      ['edit', 'ti-pencil', () => this._reminderForm(rem)],
      ['done', 'ti-check', () => {
        this.sheet.close();
        this._done(rem.id);
      }],
      ['drop', 'ti-trash', async () => {
        this.sheet.close();
        if (!(await confirmDialog(i18n.t('board.reminder.dropConfirm', { text: rem.text })))) return;
        await this._write('reminder.drop', { id: rem.id });
      }],
    ];
    for (const [key, icon, run] of items) {
      const row = button('home-board-menu-row', '', run);
      const i = el('i', `ti ${icon}`);
      i.setAttribute('aria-hidden', 'true');
      row.append(i, el('span', 'home-board-menu-name', i18n.t(`board.reminder.${key}`)));
      menu.appendChild(row);
    }
    this.sheet.open(rem.text, menu);
  }

  /** Il modulo di un'attesa: nuova (`section.add`) o da correggere
   *  (`reminder.update`). Una a condizione si corregge solo nel testo: la
   *  soglia si cambia dalla sua app, dove il dato c'e'. */
  _reminderForm(rem = null) {
    const form = el('form', 'home-board-form');
    const what = el('input', 'home-board-input');
    what.placeholder = i18n.t('board.section.reminderWhat');
    what.setAttribute('aria-label', what.placeholder);
    if (rem) what.value = rem.text;
    const dated = !rem || rem.when?.kind !== 'condition';
    const when = el('input', 'home-board-input');
    when.type = 'date';
    when.setAttribute('aria-label', i18n.t('board.section.reminderWhen'));
    const month = el('label', 'home-board-check');
    const monthBox = el('input');
    monthBox.type = 'checkbox';
    month.append(monthBox, document.createTextNode(i18n.t('board.section.reminderMonth')));
    if (rem && dated) {
      when.value = rem.when?.date || '';
      monthBox.checked = rem.when?.precision === 'month';
    }
    const actions = el('div', 'home-board-actions');
    const save = el('button', 'home-board-btn is-primary', i18n.t('board.section.save'));
    save.type = 'submit';
    actions.append(button('home-board-btn', i18n.t('common.cancel'), () => this.sheet.close()), save);
    form.append(what, ...(dated ? [when, month] : []), actions);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!what.value.trim() || (dated && !when.value)) return;
      this.sheet.close();
      const date = dated ? { kind: 'date', date: when.value, precision: monthBox.checked ? 'month' : 'day' } : undefined;
      if (rem) {
        await this._write('reminder.update', { id: rem.id, text: what.value, ...(date ? { when: date } : {}) });
      } else {
        await this._write('section.add', { card: this.card, kind: 'reminder', text: what.value, when: date });
      }
    });
    this.sheet.open(rem ? i18n.t('board.reminder.edit') : i18n.t('board.section.kind.reminder'), form);
    what.focus();
  }

  /** Una sezione dello scheletro, a fisarmonica: chiusa mostra il titolo e la
   *  prima riga; aperta il testo, le fonti e «Correggi». Si apre da sola mentre
   *  la si corregge. */
  _section(section) {
    const open = this.unfolded.has(section.title) || this.editing === section.title;
    const row = el('div', `home-drawer-section is-fold${open ? ' is-open' : ''}`);
    const h3 = el('h3', 'home-drawer-section-title');
    const toggle = button('home-drawer-fold', '', () => {
      if (open) this.unfolded.delete(section.title);
      else this.unfolded.add(section.title);
      this.render();
      /* Ridisegnato, il fuoco resta sulla stessa sezione. */
      [...this.el.querySelectorAll('.home-drawer-fold')]
        .find((b) => b.dataset.title === section.title)?.focus();
    });
    toggle.dataset.title = section.title;
    toggle.setAttribute('aria-expanded', String(open));
    const name = el('span', 'home-drawer-fold-name');
    if (section.fresh) name.appendChild(el('span', 'home-drawer-fresh'));
    name.appendChild(document.createTextNode(section.title));
    const chevron = el('i', 'ti ti-chevron-down home-drawer-fold-icon');
    chevron.setAttribute('aria-hidden', 'true');
    toggle.append(name, el('span', 'home-drawer-fold-preview', open ? '' : section.text), chevron);
    h3.appendChild(toggle);
    row.appendChild(h3);
    if (!open) return row;
    const body = el('div', 'home-drawer-section-body');
    const fix = [...this.data.corrections].reverse().find((c) => c.section === section.title);
    const source = section.sources[0];
    if (this.editing === section.title) {
      const area = el('textarea', 'home-drawer-fix-area');
      area.value = section.text;
      area.setAttribute('aria-label', i18n.t('board.drawer.fixLabel'));
      body.appendChild(area);
      const bar = el('div', 'home-drawer-row');
      bar.appendChild(el('span', 'home-drawer-hint',
        i18n.t('board.drawer.fixTarget', { page: source?.title || '' })));
      const actions = el('span', 'home-drawer-actions');
      actions.append(
        button('home-board-btn', i18n.t('common.cancel'), () => { this.editing = null; this.render(); }),
        button('home-board-btn is-primary', i18n.t('board.drawer.fixSend'),
          () => this._sendFix(section, area.value)),
      );
      bar.appendChild(actions);
      body.appendChild(bar);
    } else {
      const pending = fix && fix.status === 'pending';
      body.appendChild(el('p', pending ? 'home-drawer-text is-mine' : 'home-drawer-text',
        pending ? fix.text : section.text));
      if (fix) {
        const tag = el('div', `home-drawer-tag${fix.status === 'done' ? ' is-done' : ''}`);
        const icon = el('i', `ti ${fix.status === 'done' ? 'ti-check' : 'ti-paperclip'}`);
        icon.setAttribute('aria-hidden', 'true');
        tag.append(icon, document.createTextNode(fix.status === 'done'
          ? i18n.t('board.drawer.fixDone')
          : i18n.t('board.drawer.fixPending', { page: source?.title || fix.target })));
        body.appendChild(tag);
      }
      const links = el('div', 'home-drawer-row');
      for (const src of section.sources) {
        if (!src.exists) continue;
        links.appendChild(button('home-drawer-chip', `→ ${src.title}`,
          () => this.onOpenPage?.(src.path, src.title, this.data.id)));
      }
      if (source?.exists) {
        links.appendChild(button('home-drawer-link is-fix', i18n.t('board.drawer.fix'), () => {
          this.editing = section.title;
          this.render();
        }));
      }
      body.appendChild(links);
    }
    row.appendChild(body);
    return row;
  }

  async _sendFix(section, text) {
    if (!text.trim() || text.trim() === section.text.trim()) {
      this.editing = null;
      this.render();
      return;
    }
    try {
      await rpc.board('fix', this.notebook, { card: this.card, section: section.title, text });
    } catch (err) {
      console.warn('home.drawer: fix failed', err);
      showToast(i18n.t('board.drawer.fixFailed'), 'error');
      return;
    }
    this.editing = null;
    await this.reload();
  }

  /* ── Le sezioni dell'utente ── */

  _userSection(section) {
    const row = el('div', 'home-drawer-section is-user');
    const h3 = el('h3', 'home-drawer-section-title');
    const body = el('div', 'home-drawer-section-body');
    const tools = el('div', 'home-drawer-row');
    const remove = button('home-drawer-link', i18n.t('board.section.remove'),
      () => this._remove(section));
    if (section.kind === 'note') {
      h3.append(i18n.t('board.section.kind.note'), el('span', 'home-drawer-owner', i18n.t('board.section.yours')));
      body.appendChild(el('p', 'home-drawer-text', section.text));
      tools.appendChild(el('span', `home-drawer-tag${section.status === 'integrated' ? ' is-done' : ''}`,
        i18n.t(section.status === 'integrated' ? 'board.section.noteIntegrated' : 'board.section.notePending')));
      tools.append(button('home-drawer-link', i18n.t('board.section.edit'), () => this._editNote(section)), remove);
    } else if (section.kind === 'app') {
      const v = section.view || {};
      h3.append(v.name || section.app, el('span', 'home-drawer-owner', i18n.t('board.section.kind.app')));
      if (section.slip?.blocks?.length) {
        /* La striscia intera, coi blocchi della bacheca: stessi dati, stessi
           controlli (qui sempre attivi), un solo modo di disegnarli. */
        const slip = el('div', 'home-drawer-slip');
        const ctx = {
          act: (req) => this._act(section, req),
          media: (name) => api.getAppMediaUrl(section.app, name).then((url) => {
            this._photoUrls.push(url);
            return url;
          }),
        };
        for (const b of section.slip.blocks) slip.appendChild(renderBlock(b, ctx));
        body.appendChild(slip);
      } else {
        /* Un'app col riassunto in una forma (chip, avanzamento, lista, tabella) o
           con un lettore ha la sua vista: chip, righe, la firma del lettore. */
        body.appendChild(v.view && (v.view !== 'value' || v.extras?.length || v.every)
          ? this._microView(v) : this._appView(v));
      }
      if (v.field && (v.state === 'ok' || v.state === 'alarm' || v.state === 'stale')) {
        tools.appendChild(button('home-drawer-link', i18n.t('board.app.remindWhen'),
          () => this._addCondition(section, v)));
      }
      if (!section.slip) {
        for (const verb of (v.verbs || []).slice(0, 2)) {
          tools.appendChild(button('home-drawer-link', verb, () => this._verb(section, verb)));
        }
      }
      if (v.state === 'trashed') {
        /* L'app e' nel cestino, non sparita: si rimette da qui, e la sezione
           torna viva con i suoi dati. */
        tools.appendChild(button('home-drawer-link', i18n.t('board.app.restore'),
          () => this._restoreApp(v)));
      } else if (v.state !== 'missing' && v.hasScreen !== false) {
        tools.appendChild(button('home-drawer-link', i18n.t('board.app.open'), () => this.onOpenApp?.(section.app)));
      }
      /* L'app della scheda-app non si toglie dal suo cassetto: si toglie la scheda. */
      if (!section.own) tools.appendChild(remove);
    } else if (section.kind === 'cron') {
      /* Un cron del quaderno: cosa fa, ogni quanto, l'ultimo esito, il prossimo
         giro. Lavora Jenny, quindi costa: lo dice la firma. */
      const job = section.job;
      const result = section.result;
      h3.append(job?.name || i18n.t('board.cron.gone'),
        el('span', 'home-drawer-owner', i18n.t('board.section.kind.cron')));
      if (!job) {
        body.appendChild(el('p', 'home-drawer-hint', i18n.t('board.cron.missing')));
        tools.appendChild(remove);
      } else {
        const tr = (key, vars) => i18n.t(key, vars);
        const sign = [describeSchedule(job.schedule, { tr, locale: i18n.locale })];
        if (job.paused) sign.push(i18n.t('board.cron.paused'));
        else if (job.nextRunAtMs) {
          sign.push(i18n.t('board.cron.next', { when: relativeStamp(new Date(job.nextRunAtMs).toISOString()) }));
        }
        sign.push(i18n.t('board.cron.costs'));
        if (job.message) body.appendChild(el('p', 'home-drawer-text', job.message));
        if (result?.text) {
          body.appendChild(el('p', 'home-drawer-proof',
            i18n.t('board.cron.last', { when: relativeStamp(result.textAt || result.at), text: result.text })));
        } else if (result?.at) {
          body.appendChild(el('p', 'home-drawer-hint',
            i18n.t('board.cron.quiet', { when: relativeStamp(result.at) })));
        }
        body.appendChild(el('div', 'home-drawer-hint', sign.join(' · ')));
        tools.append(
          button('home-drawer-link', i18n.t(job.paused ? 'board.micro.resume' : 'board.micro.pause'),
            () => this._write('cron.pause', { job: job.id, paused: !job.paused })),
          button('home-drawer-link', i18n.t('board.micro.change'),
            () => this.onAskJenny?.(i18n.t('board.cron.changePrompt', { card: this.data.title, name: job.name }))),
          remove,
        );
      }
    } else if (section.kind === 'photo') {
      h3.textContent = i18n.t('board.section.kind.photo');
      const img = el('img', 'home-drawer-photo');
      img.alt = section.caption || '';
      if (section.exists) {
        api.getBoardPhotoUrl(this.notebook, section.path).then((url) => {
          this._photoUrls.push(url);
          img.src = url;
        }).catch((err) => console.warn('home.drawer: photo failed', err));
        body.appendChild(img);
      }
      if (section.caption) body.appendChild(el('p', 'home-drawer-hint', section.caption));
      tools.appendChild(remove);
    } else {
      return null;
    }
    if (!section.own) {
      const up = button('home-drawer-link is-move', '↑', () => this._moveUp(section));
      up.setAttribute('aria-label', i18n.t('board.section.moveUp'));
      tools.appendChild(up);
    }
    body.appendChild(tools);
    row.append(h3, body);
    return row;
  }

  async _restoreApp(v) {
    try {
      await rpc.apps('restore', { id: v.trashId });
    } catch (err) {
      console.warn('home.drawer: restore app failed', err);
      showToast(i18n.t(err?.code === 'conflict' ? 'apps.restoreTaken' : 'apps.restoreFailed'), 'error');
      return;
    }
    showToast(i18n.t('apps.restored', { name: v.name || v.slug }), 'success');
    await this.reload();
  }

  /** Valore, stato e firma: un dato vivo dice sempre quando e da dove viene. */
  _appView(v) {
    const box = el('div', 'home-drawer-app');
    if (['missing', 'broken', 'no_section', 'trashed'].includes(v.state)) {
      box.appendChild(el('p', 'home-drawer-hint', i18n.t(`board.app.state.${v.state}`)));
      return box;
    }
    const value = el('div', 'home-drawer-app-value');
    value.append(el('span', 'home-drawer-app-label', v.label || ''),
      el('span', 'home-drawer-app-number', v.value === null || v.value === undefined ? '—' : `${v.value}${v.unit || ''}`));
    box.appendChild(value);
    const sign = el('div', `home-drawer-app-sign is-${v.state}`);
    sign.appendChild(el('span', 'home-drawer-app-dot'));
    const when = v.at ? relativeStamp(v.at) : '';
    sign.appendChild(document.createTextNode(i18n.t(`board.app.state.${v.state}`, { when }) +
      (v.hideSource ? '' : ` · ${i18n.t('board.app.source', { app: v.name || v.slug })}`)));
    box.appendChild(sign);
    const spark = sparkline(v.trend);
    if (spark) box.insertAdjacentHTML('beforeend', spark);
    return box;
  }

  /** Un'app in una forma o col suo lettore: il valore come una mini app, piu'
   *  la firma di chi lo legge — quando, ogni quanto, da dove. Il nome e' quello
   *  delle micro app, da cui la forma viene. */
  _microView(v) {
    const shown = { ...v };
    const codeDown = ['broken', 'quarantined', 'paused'].includes(v.state);
    if (codeDown) {
      shown.state = v.dataState === 'empty' ? 'empty' : 'ok';
    }
    /* La fonte la dice il codice (`_source`); senza, la firma «ogni N min · da …»
       qui sotto basta, e «fonte: Acerello» ripeterebbe il titolo. Un'app senza
       lettore firma col suo nome, come prima. */
    shown.name = v.source || (!v.every ? v.name : '');
    shown.hideSource = !shown.name;
    const shape = v.view || 'value';
    /* Cinque forme, tutte disegnate qui: la micro app dice quale, mai come. */
    const box = shape === 'value' ? this._appView(shown) : el('div', 'home-drawer-app');
    if (codeDown) box.classList.add(`is-${v.state}`);
    if (shape === 'progress') box.appendChild(this._microProgress(v));
    if (shape === 'list') box.appendChild(this._microList(v));
    if (shape === 'table') box.appendChild(this._microTable(v));
    if (v.extras?.length) {
      /* «chips»: una piastrella per valore, alla pari. Accanto a un valore
         grande, invece, i secondari sono pastiglie piccole. */
      const tiles = shape === 'chips';
      const chips = el('div', tiles ? 'home-drawer-tiles' : 'home-drawer-micro-extras');
      for (const x of v.extras) {
        const chip = el('div', tiles ? 'home-drawer-tile' : 'home-drawer-chip');
        chip.appendChild(el('span', tiles ? 'home-drawer-tile-label' : 'home-drawer-chip-label', x.label));
        const value = el('span', tiles ? 'home-drawer-tile-value' : 'home-drawer-chip-value',
          this._cell(x.value, x.type, x.unit));
        if (x.type === 'bool') value.classList.add(x.value ? 'is-yes' : 'is-no');
        chip.appendChild(value);
        chips.appendChild(chip);
      }
      if (shape === 'value') box.insertBefore(chips, box.children[1] || null);
      else box.appendChild(chips);
    }
    if (shape !== 'value') box.appendChild(this._microSign(shown));
    if (v.every) {
      const sign = [i18n.t('board.micro.every', { minutes: v.every })];
      if (v.uses?.length) sign.push(i18n.t('board.micro.via', { what: v.uses[0] }));
      else if (v.net?.length) sign.push(i18n.t('board.micro.via', { what: v.net[0] }));
      box.appendChild(el('div', 'home-drawer-hint', sign.join(' · ')));
    }
    if (codeDown) {
      box.appendChild(el('p', 'home-drawer-proof is-down', i18n.t(`board.micro.state.${v.state}`)));
    } else if (v.codeStatus === 'unreachable') {
      box.appendChild(el('p', 'home-drawer-hint', i18n.t('board.micro.unreachable')));
    }
    return box;
  }

  _cell(value, type, unit = '') {
    if (value === null || value === undefined) return '—';
    if (type === 'bool') return i18n.t(value ? 'board.micro.yes' : 'board.micro.no');
    if (type === 'number' && typeof value === 'number') {
      return `${new Intl.NumberFormat(i18n.locale).format(value)}${unit || ''}`;
    }
    return `${value}${unit || ''}`;
  }

  /** Lo stato e la firma, come sotto il valore di una mini app. */
  _microSign(v) {
    const sign = el('div', `home-drawer-app-sign is-${v.state}`);
    sign.appendChild(el('span', 'home-drawer-app-dot'));
    const when = v.at ? relativeStamp(v.at) : '';
    sign.appendChild(document.createTextNode(i18n.t(`board.app.state.${v.state}`, { when }) +
      (v.hideSource ? '' : ` · ${i18n.t('board.app.source', { app: v.name || v.slug })}`)));
    return sign;
  }

  /** Un valore verso il suo obiettivo: «41.200 / 80.000 parole» e una barra. */
  _microProgress(v) {
    const wrap = el('div', 'home-drawer-progress');
    const lo = Number(v.min) || 0;
    const hi = Number(v.max);
    const val = typeof v.value === 'number' ? v.value : lo;
    const pct = Number.isFinite(hi) && hi > lo ? Math.max(0, Math.min(1, (val - lo) / (hi - lo))) : 0;
    const head = el('div', 'home-drawer-app-value');
    head.append(el('span', 'home-drawer-app-label', v.label || ''),
      el('span', 'home-drawer-app-number',
        `${this._cell(v.value, 'number')} / ${this._cell(hi, 'number')}${v.unit ? ` ${v.unit}` : ''}`));
    const bar = el('div', 'home-drawer-progress-bar');
    const fill = el('div', 'home-drawer-progress-fill');
    fill.style.width = `${Math.round(pct * 100)}%`;
    bar.appendChild(fill);
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-valuenow', String(Math.round(pct * 100)));
    wrap.append(head, bar);
    return wrap;
  }

  /** Righe: la prima colonna e' il titolo, le altre la riga sotto. */
  _microList(v) {
    const list = el('ul', 'home-drawer-micro-list');
    const cols = v.columns || [];
    const rows = v.rows || [];
    if (!rows.length) list.appendChild(el('li', 'home-drawer-hint', i18n.t('board.micro.noRows')));
    for (const row of rows.slice(0, 8)) {
      const li = el('li', 'home-drawer-micro-row');
      const [first, ...rest] = cols;
      if (first) li.appendChild(el('span', 'home-drawer-micro-row-title', this._cell(row[first.field], first.type, first.unit)));
      const sub = rest.map((c) => this._cell(row[c.field], c.type, c.unit)).filter((x) => x !== '—');
      if (sub.length) li.appendChild(el('span', 'home-drawer-micro-row-sub', sub.join(' · ')));
      list.appendChild(li);
    }
    if (rows.length > 8) list.appendChild(el('li', 'home-drawer-hint', i18n.t('board.micro.moreRows', { count: rows.length - 8 })));
    return list;
  }

  /** Una tabella piccola: le colonne dichiarate, al piu' venti righe. */
  _microTable(v) {
    const table = el('table', 'home-drawer-micro-table');
    const cols = v.columns || [];
    const head = el('tr');
    for (const c of cols) head.appendChild(el('th', c.type === 'number' ? 'is-num' : '', c.label));
    table.appendChild(head);
    for (const row of v.rows || []) {
      const tr = el('tr');
      for (const c of cols) {
        tr.appendChild(el('td', c.type === 'number' ? 'is-num' : '', this._cell(row[c.field], c.type, c.unit)));
      }
      table.appendChild(tr);
    }
    if (!(v.rows || []).length) {
      const tr = el('tr');
      const td = el('td', 'home-drawer-hint', i18n.t('board.micro.noRows'));
      td.colSpan = Math.max(1, cols.length);
      tr.appendChild(td);
      table.appendChild(tr);
    }
    return table;
  }

  /** Un controllo della striscia nel cassetto: l'azione dell'app, poi si rilegge. */
  async _act(section, { action, params } = {}) {
    if (!action) return;
    try {
      await api.runAppAction(section.app, action, params || {});
    } catch (err) {
      console.warn('home.drawer: strip action failed', err);
      showToast(i18n.t('board.slip.failed'), 'error');
    }
    await this.reload();
  }

  async _verb(section, verb) {
    try {
      await api.runAppAction(section.app, verb, section.params || {});
    } catch (err) {
      console.warn('home.drawer: verb failed', err);
      showToast(String(err?.message || err), 'error');
      return;
    }
    await this.reload();
  }

  async _cutThread(conn) {
    if (!(await confirmDialog(i18n.t('board.drawer.threadRemoveConfirm', { card: conn.title })))) return;
    await this._write('connection.remove', { a: this.card, b: conn.to });
  }

  /** «Avvisami quando…»: un'attesa a condizione sul dato di una mini app. La
   *  controlla l'orologio del quaderno ogni ora, sul dato fresco. */
  _addCondition(section, view) {
    const form = el('form', 'home-board-form');
    const what = el('input', 'home-board-input');
    what.value = i18n.t('board.app.remindDefault', { app: view.name || section.app });
    what.setAttribute('aria-label', i18n.t('board.section.reminderWhat'));
    const row = el('div', 'home-board-row');
    const op = el('select', 'home-board-input is-op');
    for (const [value, key] of [['<', 'below'], ['>', 'above']]) {
      const option = el('option', '', i18n.t(`board.app.${key}`, { label: view.label || view.field }));
      option.value = value;
      op.appendChild(option);
    }
    const value = el('input', 'home-board-input is-value');
    value.type = 'number';
    value.step = 'any';
    value.setAttribute('aria-label', i18n.t('board.app.threshold'));
    if (typeof view.value === 'number') value.value = String(view.value);
    row.append(op, value);
    const actions = el('div', 'home-board-actions');
    const save = el('button', 'home-board-btn is-primary', i18n.t('board.section.save'));
    save.type = 'submit';
    actions.append(button('home-board-btn', i18n.t('common.cancel'), () => this.sheet.close()), save);
    form.append(what, row, actions);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!what.value.trim() || value.value === '') return;
      this.sheet.close();
      await this._write('section.add', {
        card: this.card, kind: 'reminder', text: what.value,
        when: { kind: 'condition', app: section.app, params: section.params || {},
          field: view.field, op: op.value, value: Number(value.value) },
      });
    });
    this.sheet.open(i18n.t('board.app.remindWhen'), form);
  }

  async _remove(section) {
    if (!(await confirmDialog(i18n.t('board.section.removeConfirm')))) return;
    await this._write('section.remove', { card: this.card, id: section.id });
  }

  async _editNote(section) {
    const text = await promptDialog(i18n.t('board.section.kind.note'), { initial: section.text });
    if (text === null || !text.trim() || text === section.text) return;
    await this._write('section.update', { card: this.card, id: section.id, text });
  }

  async _moveUp(section) {
    const ids = this.data.userSections.map((s) => s.id);
    const i = ids.indexOf(section.id);
    if (i <= 0) return;
    [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]];
    await this._write('section.reorder', { card: this.card, order: ids });
  }

  async _done(reminderId) {
    await this._write('reminder.done', { id: reminderId });
  }

  async _write(method, params) {
    try {
      await rpc.board(method, this.notebook, params);
    } catch (err) {
      console.warn(`home.drawer: ${method} failed`, err);
      showToast(i18n.t('board.section.failed'), 'error');
      return false;
    }
    await this.reload();
    return true;
  }

  /* ── Aggiungere: una domanda, mai un foglio bianco ── */

  async _addNote() {
    this.sheet.close();
    const text = await promptDialog(i18n.t('board.section.notePrompt'), {
      placeholder: i18n.t('board.section.notePlaceholder'),
    });
    if (!text || !text.trim()) return;
    await this._write('section.add', { card: this.card, kind: 'note', text });
  }

  /** «Cosa?» e «Quando?»: una data, oppure niente — una condizione su un dato
   *  vivo si aggancia da una mini app, dove il dato c'e'. */
  _addReminder() {
    this._reminderForm();
  }

  /** «Cosa vuoi vedere?»: una frase, e l'app la scrive Jenny nella chat del
   *  quaderno — e la mette sulla bacheca come polaroid sua, legata a questa
   *  scheda con un filo. Mettere accanto un'app che c'è già resta una riga
   *  sotto. */
  _addMicro() {
    const form = el('form', 'home-board-form');
    const what = el('input', 'home-board-input');
    what.placeholder = i18n.t('board.micro.whatPlaceholder');
    what.setAttribute('aria-label', i18n.t('board.micro.what'));
    const actions = el('div', 'home-board-actions');
    const go = el('button', 'home-board-btn is-primary', i18n.t('board.micro.prepare'));
    go.type = 'submit';
    actions.append(button('home-board-btn', i18n.t('common.cancel'), () => this.sheet.close()), go);
    const link = button('home-board-menu-row is-new', i18n.t('board.micro.linkApp'), () => this._addApp());
    form.append(el('p', 'home-board-muted', i18n.t('board.micro.explain')), what, actions, link);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!what.value.trim()) return;
      this.sheet.close();
      this.onAskJenny?.(i18n.t('board.micro.prompt', { card: this.data.title, what: what.value.trim() }));
    });
    this.sheet.open(i18n.t('board.micro.what'), form);
    what.focus();
  }

  async _addApp() {
    let apps = [];
    try {
      apps = (await api.getJennyApps()).apps || [];
    } catch (err) {
      console.warn('home.drawer: apps failed', err);
    }
    const menu = el('div', 'home-board-menu');
    for (const app of apps.filter((a) => a.section && !a.broken)) {
      const row = button('home-board-menu-row', '', () => this._attachApp(app));
      const i = el('i', `ti ${app.icon || 'ti-apps'}`);
      i.setAttribute('aria-hidden', 'true');
      row.append(i, el('span', 'home-board-menu-name', app.name), el('span', 'home-board-menu-hint', app.description || ''));
      menu.appendChild(row);
    }
    if (!menu.children.length) menu.appendChild(el('p', 'home-board-muted', i18n.t('board.section.noApps')));
    menu.appendChild(button('home-board-menu-row is-new', i18n.t('board.section.newApp'), () => {
      this.sheet.close();
      this.onAskJenny?.(i18n.t('board.section.newAppPrompt', { card: this.data.title }));
    }));
    this.sheet.open(i18n.t('board.section.kind.app'), menu);
  }

  /** Un'app accanto a questa scheda: una polaroid sua, legata da un filo —
   *  non una sezione del cassetto. Si torna alla bacheca a vederla nascere. */
  async _attachApp(app) {
    const params = {};
    for (const name of Object.keys(app.section?.params || {})) {
      const value = await promptDialog(i18n.t('board.section.appParam', { name, app: app.name }));
      if (value === null) return;
      params[name] = value.trim();
    }
    this.sheet.close();
    let out;
    try {
      out = await rpc.board('card.add', this.notebook, {
        app: { slug: app.slug, params }, link: this.card,
      });
    } catch (err) {
      console.warn('home.drawer: app card failed', err);
      showToast(i18n.t(err?.code === 'conflict' ? 'board.card.full' : 'board.section.failed'), 'error');
      return;
    }
    showToast(i18n.t('board.card.appPlaced', { name: app.name, card: this.data.title }), 'success');
    await this.onAppCard?.(out?.card?.id || null);
  }

  _addPhoto() {
    const input = el('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      if (!file) return;
      this.sheet.close();
      let photo;
      try {
        photo = await shrinkPhoto(file);
      } catch (err) {
        console.warn('home.drawer: photo unreadable', err);
        showToast(i18n.t('board.section.failed'), 'error');
        return;
      }
      await this._write('section.add', { card: this.card, kind: 'photo', ...photo });
    });
    input.click();
  }
}
