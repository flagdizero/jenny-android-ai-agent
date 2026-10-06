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
   * @param sheet        il foglio della bacheca, per «Aggiungi sezione».
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
      if ((s.kind === 'app' || s.kind === 'micro') && (state === 'alarm' || state === 'stale')) {
        root.appendChild(el('div', 'home-drawer-alert',
          i18n.t(`board.app.alert.${state}`, { app: s.view.name || s.app })));
      } else if (s.kind === 'micro' && state === 'broken') {
        root.appendChild(el('div', 'home-drawer-alert',
          i18n.t('board.micro.alertBroken', { app: s.view.name })));
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
           con un lettore si disegna come una micro app: e' la stessa cosa. */
        body.appendChild(v.view && (v.view !== 'value' || v.extras?.length || v.every)
          ? this._microView(section, v, { app: true }) : this._appView(v));
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
    } else if (section.kind === 'micro') {
      const v = section.view || {};
      if (v.state === 'missing') return null;
      h3.append(v.name || section.micro, el('span', 'home-drawer-owner', i18n.t('board.section.kind.micro')));
      body.appendChild(this._microView(section, v));
      if (v.state === 'pending') {
        tools.append(
          button('home-board-btn is-primary', i18n.t('board.micro.keep'), () => this._micro('keep', v)),
          button('home-board-btn', i18n.t('board.micro.discard'), () => this._micro('discard', v)),
        );
      } else {
        if (v.state === 'broken' || v.state === 'quarantined') {
          tools.appendChild(button('home-board-btn is-primary', i18n.t('board.micro.fix'),
            () => this.onAskJenny?.(i18n.t('board.micro.fixPrompt', {
              card: this.data.title, app: v.name, error: (v.lastError || '').split('\n').filter(Boolean).pop() || '',
            }))));
        } else if (v.state === 'unapproved') {
          tools.appendChild(button('home-board-btn is-primary', i18n.t('board.micro.reapprove'),
            () => this._micro('keep', v)));
        } else if (v.state === 'paused') {
          tools.appendChild(button('home-board-btn', i18n.t('board.micro.resume'),
            () => this._micro('pause', v, { paused: false })));
        } else {
          const again = button('home-drawer-link is-icon', '', () => this._micro('run', v));
          const icon = el('i', 'ti ti-refresh');
          icon.setAttribute('aria-hidden', 'true');
          again.appendChild(icon);
          again.setAttribute('aria-label', i18n.t('board.micro.runNow'));
          tools.appendChild(again);
        }
        if (v.field && (v.type === 'number' || v.type === 'rows') && v.dataState !== 'empty') {
          tools.appendChild(button('home-drawer-link', i18n.t('board.app.remindWhen'),
            () => this._addCondition(section, v)));
        }
        tools.appendChild(button('home-drawer-link', i18n.t('board.micro.change'),
          () => this.onAskJenny?.(i18n.t('board.micro.changePrompt', { card: this.data.title, app: v.name }))));
        if (v.state !== 'paused' && v.state !== 'unapproved') {
          tools.appendChild(button('home-drawer-link', i18n.t('board.micro.pause'),
            () => this._micro('pause', v, { paused: true })));
        }
        tools.appendChild(remove);
      }
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

  /** Una micro app: il valore come una mini app, piu' la firma di chi lo
   *  legge — quando, ogni quanto, da dove — e, in prova, cosa toccherà. */
  _microView(section, v, { app = false } = {}) {
    const shown = { ...v };
    const codeDown = ['broken', 'quarantined', 'unapproved', 'paused'].includes(v.state);
    if (v.state === 'pending' || codeDown) {
      shown.state = v.dataState === 'empty' ? 'empty' : 'ok';
    }
    /* La fonte la dice il codice (`_source`); senza, la firma «ogni N min · da …»
       qui sotto basta, e «fonte: Acerello» ripeterebbe il titolo. Un'app senza
       lettore firma col suo nome, come prima. */
    shown.name = v.source || (app && !v.every ? v.name : '');
    shown.hideSource = !shown.name;
    const shape = v.view || 'value';
    /* Cinque forme, tutte disegnate qui: la micro app dice quale, mai come. */
    const box = shape === 'value' ? this._appView(shown) : el('div', 'home-drawer-app');
    if (codeDown || v.state === 'pending') box.classList.add(`is-${v.state}`);
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
    if (v.state === 'pending') {
      const reach = [];
      if (v.uses?.length) reach.push(i18n.t('board.micro.reachSkill', { skill: v.uses[0] }));
      if (v.net?.length) reach.push(i18n.t('board.micro.reachNet', { hosts: v.net.join(', ') }));
      box.appendChild(el('p', 'home-drawer-proof', [i18n.t('board.micro.trial'), ...reach].join(' ')));
    } else if (codeDown) {
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

  async _micro(action, v, extra = {}) {
    await this._write(`micro.${action}`, { micro: v.micro, ...extra });
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
    what.value = i18n.t('board.app.remindDefault', { app: view.name || section.app || section.micro });
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
      const source = section.kind === 'micro'
        ? { micro: section.micro }
        : { app: section.app, params: section.params || {} };
      await this._write('section.add', {
        card: this.card, kind: 'reminder', text: what.value,
        when: { kind: 'condition', ...source, field: view.field, op: op.value, value: Number(value.value) },
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

  /* ── «Aggiungi sezione»: una domanda, mai un foglio bianco ── */

  openAddMenu() {
    const menu = el('div', 'home-board-menu');
    const items = [
      ['note', 'ti-pencil', () => this._addNote()],
      ['reminder', 'ti-clock', () => this._addReminder()],
      ['micro', 'ti-gauge', () => this._addMicro()],
      ['cron', 'ti-repeat', () => {
        this.sheet.close();
        this.onAskJenny?.(i18n.t('board.cron.prompt', { card: this.data.title }));
      }],
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
