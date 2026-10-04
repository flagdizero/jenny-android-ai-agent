/** La casa — il cassetto di una scheda della bacheca.
 *
 *  Il retro di una polaroid: un `index.md` per quel soggetto, scritto da Jenny
 *  dalla wiki. In alto «in breve» e la prossima cosa da fare; poi le sezioni
 *  dello **scheletro del quaderno** (le stesse per ogni scheda, nello stesso
 *  ordine: l'utente sa sempre dove guardare), due righe ciascuna con i link alle
 *  pagine vere da cui vengono; poi le sezioni che hai aggiunto tu; poi i fili.
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
import { whenLabel } from './home-board.js';

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
   * @param sheet        il foglio della bacheca, per «Aggiungi sezione».
   */
  constructor({ onOpenPage, onOpenCard, onAskJenny, onOpenApp, sheet } = {}) {
    this.el = document.getElementById('home-board-drawer');
    this.onOpenPage = onOpenPage;
    this.onOpenCard = onOpenCard;
    this.onAskJenny = onAskJenny;
    this.onOpenApp = onOpenApp;
    this.sheet = sheet;
    this.notebook = null;
    this.card = null;
    this.data = null;
    /** La sezione che si sta correggendo, per titolo. */
    this.editing = null;
    this._token = 0;
    this._photoUrls = [];
  }

  get title() {
    return this.data?.title || '';
  }

  /** Apre il cassetto *card* del quaderno *notebook*. */
  async load(notebook, card) {
    const token = ++this._token;
    this.notebook = notebook;
    this.card = card;
    this.editing = null;
    this.el.innerHTML = '';
    this.el.appendChild(el('p', 'home-drawer-note', i18n.t('board.drawer.loading')));
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
    this.el.scrollTop = 0;
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
    if (d.next) root.appendChild(this._next(d.next));

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
    root.appendChild(button('home-drawer-add', i18n.t('board.section.add'), () => this.openAddMenu()));

    if (d.connections.length) {
      const threads = el('div', 'home-drawer-threads');
      threads.appendChild(el('h4', 'home-drawer-h4', i18n.t('board.drawer.threads')));
      for (const c of d.connections) {
        const row = button('home-drawer-thread', '', () => this.onOpenCard?.(c.to, c.title));
        row.append(el('span', 'home-drawer-thread-mark', '—'), el('span', 'home-drawer-thread-to', c.title),
          el('span', 'home-drawer-thread-why', c.label));
        threads.appendChild(row);
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
    root.appendChild(el('p', 'home-drawer-foot', i18n.t('board.drawer.foot')));
    this.el.innerHTML = '';
    this.el.appendChild(root);
    if (this.editing) {
      const area = this.el.querySelector('.home-drawer-fix-area');
      area?.focus();
      area?.setSelectionRange(area.value.length, area.value.length);
    }
  }

  _next(rem) {
    const box = el('div', 'home-drawer-next');
    box.append(el('span', 'home-drawer-next-key', i18n.t('board.drawer.next')),
      el('span', 'home-drawer-next-text', `${rem.text} · ${whenLabel(rem.when)}`));
    box.appendChild(button('home-drawer-link', i18n.t('board.reminder.done'), () => this._done(rem.id)));
    return box;
  }

  /** Una sezione dello scheletro: due righe, le fonti, e «Correggi». */
  _section(section) {
    const row = el('div', 'home-drawer-section');
    const h3 = el('h3', 'home-drawer-section-title');
    if (section.fresh) h3.appendChild(el('span', 'home-drawer-fresh'));
    h3.appendChild(document.createTextNode(section.title));
    row.appendChild(h3);
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
    } else if (section.kind === 'reminder') {
      const rem = section.reminderData;
      if (!rem || rem.state !== 'open') return null;
      h3.textContent = i18n.t('board.section.kind.reminder');
      body.appendChild(el('p', 'home-drawer-text', rem.text));
      tools.appendChild(el('span', 'home-drawer-tag', `◷ ${whenLabel(rem.when)}`));
      tools.append(button('home-drawer-link', i18n.t('board.reminder.done'), () => this._done(rem.id)), remove);
    } else if (section.kind === 'app') {
      const v = section.view || {};
      h3.append(v.name || section.app, el('span', 'home-drawer-owner', i18n.t('board.section.kind.app')));
      body.appendChild(this._appView(v));
      for (const verb of v.verbs || []) {
        tools.appendChild(button('home-drawer-link', verb, () => this._verb(section, verb)));
      }
      if (v.state !== 'missing') {
        tools.appendChild(button('home-drawer-link', i18n.t('board.app.open'), () => this.onOpenApp?.(section.app)));
      }
      tools.appendChild(remove);
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
    const up = button('home-drawer-link is-move', '↑', () => this._moveUp(section));
    up.setAttribute('aria-label', i18n.t('board.section.moveUp'));
    tools.appendChild(up);
    body.appendChild(tools);
    row.append(h3, body);
    return row;
  }

  /** Valore, stato e firma: un dato vivo dice sempre quando e da dove viene. */
  _appView(v) {
    const box = el('div', 'home-drawer-app');
    if (v.state === 'missing' || v.state === 'broken' || v.state === 'no_section') {
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
      ` · ${i18n.t('board.app.source', { app: v.name || v.slug })}`));
    box.appendChild(sign);
    const spark = sparkline(v.trend);
    if (spark) box.insertAdjacentHTML('beforeend', spark);
    return box;
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

  /* ── «Aggiungi sezione»: una domanda, mai un foglio bianco ── */

  openAddMenu() {
    const menu = el('div', 'home-board-menu');
    const items = [
      ['note', 'ti-pencil', () => this._addNote()],
      ['reminder', 'ti-clock', () => this._addReminder()],
      ['app', 'ti-apps', () => this._addApp()],
      ['photo', 'ti-photo', () => this._addPhoto()],
      ['other', 'ti-dots', () => {
        this.sheet.close();
        this.onAskJenny?.(i18n.t('board.section.otherPrompt', { card: this.data.title }));
      }],
    ];
    for (const [kind, icon, run] of items) {
      const row = button('home-board-menu-row', '', run);
      const i = el('i', `ti ${icon}`);
      i.setAttribute('aria-hidden', 'true');
      row.append(i, el('span', 'home-board-menu-name', i18n.t(`board.section.kind.${kind}`)),
        el('span', 'home-board-menu-hint', i18n.t(`board.section.hint.${kind}`)));
      menu.appendChild(row);
    }
    this.sheet.open(i18n.t('board.section.addTitle', { card: this.data.title }), menu);
  }

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
    const form = el('form', 'home-board-form');
    const what = el('input', 'home-board-input');
    what.placeholder = i18n.t('board.section.reminderWhat');
    what.setAttribute('aria-label', what.placeholder);
    const when = el('input', 'home-board-input');
    when.type = 'date';
    when.setAttribute('aria-label', i18n.t('board.section.reminderWhen'));
    const month = el('label', 'home-board-check');
    const monthBox = el('input');
    monthBox.type = 'checkbox';
    month.append(monthBox, document.createTextNode(i18n.t('board.section.reminderMonth')));
    const actions = el('div', 'home-board-actions');
    const save = el('button', 'home-board-btn is-primary', i18n.t('board.section.save'));
    save.type = 'submit';
    actions.append(button('home-board-btn', i18n.t('common.cancel'), () => this.sheet.close()), save);
    form.append(what, when, month, actions);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!what.value.trim() || !when.value) return;
      this.sheet.close();
      await this._write('section.add', {
        card: this.card, kind: 'reminder', text: what.value,
        when: { kind: 'date', date: when.value, precision: monthBox.checked ? 'month' : 'day' },
      });
    });
    this.sheet.open(i18n.t('board.section.kind.reminder'), form);
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

  async _attachApp(app) {
    const params = {};
    for (const name of Object.keys(app.section?.params || {})) {
      const value = await promptDialog(i18n.t('board.section.appParam', { name, app: app.name }));
      if (value === null) return;
      params[name] = value.trim();
    }
    this.sheet.close();
    await this._write('section.add', { card: this.card, kind: 'app', app: app.slug, params });
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
