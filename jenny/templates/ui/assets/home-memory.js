/** La casa — «Cosa ricorda di te».
 *
 *  Quel che Jenny sa di te (`USER.md`, «Di te») e i suoi appunti
 *  (`memory/MEMORY.md`, «I suoi appunti»), come frasi. Fino al 25/11/2026 si
 *  vedevano solo in officina, come Markdown grezzo nell'editor di file, che li
 *  riscriveva senza lasciare traccia.
 *
 *  - **Correggere**: un tocco sulla voce apre un foglio col testo; si dice se
 *    *e' cambiato* (prima era vero: resta come passato) o se *non era vero*
 *    (non torna piu'), e la scelta e' il salvataggio. La voce corretta e'
 *    «sistemata da te»: Dream non la riordina (cede solo a una correzione che
 *    dici in chat), ma resta tua da cambiare o togliere. Fino al 25/11/2026
 *    aveva un lucchetto, e si leggeva «non si tocca piu'».
 *  - **Togliere**: il cestino toglie subito, con «Annulla», che la rimette
 *    com'era — sistemata da te solo se lo era gia'. Una voce tolta non torna
 *    quando la nomini.
 *  - **Le versioni di prima**: quando una riscrittura ha perso un nome o un
 *    numero che nessun file dice piu', sotto la voce c'e' una riga chiusa; si
 *    apre sulla **frase vecchia intera**, coi dettagli persi evidenziati, e
 *    «Rimetti». Fino al 25/11/2026 erano righe di parole sciolte («Una
 *    riscrittura ha perso: Lost, Translation»), che da sole non si capivano.
 *  - **«Dimenticato di recente»**: quel che non e' piu' nella lista — tolto, o
 *    non vero. Le versioni vecchie di una voce che c'e' ancora stanno sotto di
 *    lei (`memory_service._recent`).
 *
 *  Un gesto rilegge e ridisegna la stanza, ma **la posizione resta**: si
 *  ritrova la prima voce che si vedeva e la si rimette allo stesso punto
 *  (`keepPlace`). Prima un cestino in fondo alla lista poteva riportarti su.
 *
 *  Tutto passa dai comandi `memory.*` (`webui/memory_commands.py`), che
 *  archiviano ogni formulazione che se ne va. Una voce cambiata nel frattempo
 *  risponde `conflict`: si rilegge, invece di scrivere sopra.
 */

import { i18n } from './shared/i18n.js';
import { rpc } from './shared/rpc-client.js';
import { botName } from './shared/bot-name.js';
import { button, el, icon } from './shared/dom.js';
import { closeActionSheet, openActionSheet, sheetHead } from './shared/sheet.js';
import { showToast, showUndo } from './shared/utils.js';

const t = (key, params) => i18n.t(`home.memory.${key}`, params);
/* Mentre Dream lavora la stanza si rilegge da sola, con calma. */
const DREAM_POLL_MS = 15_000;

/** La riga di riferimento per non perdere il punto: la prima che si vede in
 *  cima a *scroller*, con quanto sta sotto il bordo. `null` se non ce n'e'. */
export function placeOf(scroller) {
  if (!scroller) return null;
  const top = scroller.getBoundingClientRect().top;
  for (const node of scroller.querySelectorAll('[data-place]')) {
    const box = node.getBoundingClientRect();
    if (box.bottom > top) return { key: node.dataset.place, offset: box.top - top };
  }
  return null;
}

/** Rimette *place* (da {@link placeOf}) allo stesso punto dopo un ridisegno.
 *  Se la sua riga non c'e' piu' (l'hai appena tolta) si tiene lo scroll di
 *  prima: le righe sopra non sono cambiate. */
export function restorePlace(scroller, place, fallbackTop) {
  if (!scroller) return;
  const node = place
    ? [...scroller.querySelectorAll('[data-place]')].find((n) => n.dataset.place === place.key)
    : null;
  if (!node) {
    scroller.scrollTop = fallbackTop;
    return;
  }
  const delta = node.getBoundingClientRect().top - scroller.getBoundingClientRect().top - place.offset;
  scroller.scrollTop += delta;
}

/** Una frase con le parole di *words* evidenziate (`<mark>`), senza HTML. */
export function markWords(text, words) {
  const span = el('span', 'home-memory-old-text');
  const wanted = (words || []).filter(Boolean)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!wanted.length) {
    span.textContent = text;
    return span;
  }
  const re = new RegExp(`(${wanted.join('|')})`, 'giu');
  let last = 0;
  for (const m of String(text).matchAll(re)) {
    if (m.index > last) span.appendChild(document.createTextNode(text.slice(last, m.index)));
    span.appendChild(el('mark', null, m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) span.appendChild(document.createTextNode(text.slice(last)));
  return span;
}

/** Il valore della riga di Impostazioni: quante cose sa di te. */
export function memorySummary(data) {
  const mine = (data?.files || []).find((f) => f.file === 'user');
  if (!mine) return '';
  const n = mine.entries.length;
  return n ? t('count', { n }) : t('nothingYet');
}

/** Le righe di «Dimenticato di recente», con quel che si puo' fare. */
export function recentRows(data) {
  return (data?.recent || []).map((row) => ({
    ...row,
    canPutBack: row.kind === 'removed',
  }));
}

export class HomeMemory {
  /** @param onChange  la riga di Impostazioni si riscrive. */
  constructor({ onChange } = {}) {
    this.body = document.getElementById('home-memory-body');
    this.sheet = document.getElementById('home-memory-sheet');
    this.onChange = onChange;
    this.data = null;
    this.busy = false;
    this._failed = false;
    this._timer = null;
    /* Le voci con le versioni di prima aperte: restano aperte a un ridisegno. */
    this._opened = new Set();
  }

  open() {
    this._open = true;
    this.render();
    this.refresh();
  }

  close() {
    this._open = false;
    window.clearTimeout(this._timer);
    this._timer = null;
    closeActionSheet(this.sheet);
  }

  value() {
    return memorySummary(this.data);
  }

  async refresh() {
    window.clearTimeout(this._timer);
    try {
      this.data = await rpc.memory('list');
      this._failed = false;
    } catch {
      this._failed = true;
    }
    this.render();
    this.onChange?.(this.data);
    if (this.data?.dreaming && this._open) {
      this._timer = window.setTimeout(() => this.refresh(), DREAM_POLL_MS);
    }
  }

  render() {
    if (!this.body) return;
    if (!this.data) {
      this.body.replaceChildren(el('div', 'home-card-note', t(this._failed ? 'unavailable' : 'loading')));
      return;
    }
    const place = placeOf(this.body);
    const top = this.body.scrollTop;
    const parts = [];
    if (this.data.dreaming) {
      parts.push(el('div', 'home-memory-dreaming', t('dreaming', { name: botName.get() })));
    }
    for (const file of this.data.files || []) {
      parts.push(this._section(file));
    }
    const recent = recentRows(this.data);
    if (recent.length) parts.push(this._recent(recent));
    parts.push(el('p', 'home-memory-foot', t('foot', { name: botName.get() })));
    this.body.replaceChildren(...parts);
    restorePlace(this.body, place, top);
  }

  _section(file) {
    const wrap = el('div', 'home-memory-section');
    wrap.dataset.file = file.file;
    wrap.appendChild(el('div', 'home-memory-sec',
      file.file === 'user' ? t('aboutYou') : t('herNotes', { name: botName.get() })));
    if (!file.entries.length) {
      wrap.appendChild(el('p', 'home-memory-empty',
        file.file === 'user' ? t('emptyUser', { name: botName.get() }) : t('emptyNotes')));
      return wrap;
    }
    const list = el('div', 'home-rows home-memory-list');
    for (const entry of file.entries) list.appendChild(this._entry(file.file, entry));
    wrap.appendChild(list);
    return wrap;
  }

  _entry(fileKey, entry) {
    const row = el('div', 'home-row home-memory-row');
    row.dataset.id = entry.id;
    row.dataset.place = entry.id;
    const text = button('home-memory-text', null, () => this._edit(fileKey, entry));
    text.appendChild(el('span', 'home-memory-sentence', entry.text));
    if (entry.locked) {
      const mine = el('span', 'home-memory-locked');
      mine.append(icon('ti-pencil-check'), document.createTextNode(t('locked')));
      text.appendChild(mine);
    }
    row.appendChild(text);
    const trash = button('home-memory-remove', null, () => this._remove(fileKey, entry));
    trash.setAttribute('aria-label', t('remove'));
    trash.appendChild(icon('ti-trash'));
    row.appendChild(trash);
    if (entry.lost?.length) row.appendChild(this._lost(entry));
    return row;
  }

  /* Sotto la voce: una riga chiusa, «una versione di prima diceva di piu'».
     Aperta, ogni frase vecchia intera coi dettagli persi evidenziati e il suo
     «Rimetti», che la rimette accanto a questa. */
  _lost(entry) {
    const box = el('div', 'home-memory-lost');
    const open = this._opened.has(entry.id);
    const n = entry.lost.length;
    const head = button('home-memory-lost-head', null, () => {
      if (this._opened.has(entry.id)) this._opened.delete(entry.id);
      else this._opened.add(entry.id);
      this.render();
    });
    head.setAttribute('aria-expanded', open ? 'true' : 'false');
    head.append(icon('ti-history'),
      el('span', null, n === 1 ? t('lostOne') : t('lostMany', { n })),
      icon(open ? 'ti-chevron-up' : 'ti-chevron-down'));
    box.appendChild(head);
    if (!open) return box;
    for (const detail of entry.lost) {
      const words = (detail.words || [detail.word]).filter(Boolean);
      const old = el('div', 'home-memory-old');
      const body = el('div', 'home-memory-old-body');
      body.appendChild(markWords(detail.text || words.join(', '), words));
      if (detail.date) body.appendChild(el('span', 'home-memory-why', t('oldDate', { date: detail.date })));
      old.appendChild(body);
      const put = button('home-memory-putback', t('putBack'),
        () => this._restore(detail.id, entry.id));
      put.setAttribute('aria-label', t('putBackDetail', { word: words.join(', ') }));
      old.appendChild(put);
      box.appendChild(old);
    }
    return box;
  }

  _recent(rows) {
    const wrap = el('div', 'home-memory-section is-recent');
    wrap.appendChild(el('div', 'home-memory-sec', t('recent')));
    const list = el('div', 'home-rows home-memory-list');
    for (const row of rows) {
      const node = el('div', `home-row home-memory-row is-${row.kind}`);
      node.dataset.id = row.id;
      node.dataset.place = `recent:${row.id}`;
      const text = el('div', 'home-memory-text');
      text.appendChild(el('span', 'home-memory-sentence', row.text));
      text.appendChild(el('span', 'home-memory-why', t(`kind.${row.kind}`)));
      node.appendChild(text);
      if (row.canPutBack) {
        node.appendChild(button('home-memory-putback', t('putBack'), () => this._restore(row.id)));
      }
      list.appendChild(node);
    }
    wrap.appendChild(list);
    return wrap;
  }

  /* ── I gesti ─────────────────────────────────────────────────────────── */

  /** Il foglio di correzione: il testo, e se e' cambiato o non era vero. */
  _edit(fileKey, entry) {
    const field = document.createElement('textarea');
    field.className = 'home-memory-field';
    field.value = entry.text;
    field.rows = 3;
    field.setAttribute('aria-label', t('fieldLabel'));
    const choose = (reason) => this._correct(fileKey, entry, field.value, reason);
    /* Due tasti che sono anche il salvataggio: ognuno dice cosa succede alla
       frase di prima, perche' e' l'unica differenza fra i due. */
    const choice = (cls, reason) => {
      const b = button(cls, null, () => choose(reason));
      b.append(el('span', 'home-memory-choice-title', t(reason)),
        el('span', 'home-memory-choice-sub', t(`${reason}Sub`)));
      return b;
    };
    const actions = el('div', 'home-memory-actions');
    actions.append(
      el('div', 'home-memory-why-label', t('whyLabel')),
      choice('home-memory-changed', 'changed'),
      choice('home-memory-wrong', 'wrong'),
    );
    openActionSheet(this.sheet, {
      closeLabel: t('close'),
      onClose: () => closeActionSheet(this.sheet),
      children: [
        ...sheetHead(t('editTitle'), t('editSub', { name: botName.get() })),
        field,
        actions,
        el('p', 'home-plan-sheet-note', t('lockNote', { name: botName.get() })),
      ],
    });
    field.focus?.();
  }

  async _correct(fileKey, entry, text, reason) {
    if (!String(text || '').trim()) return;
    const ok = await this._call('correct', { file: fileKey, id: entry.id, text, reason });
    if (ok) {
      closeActionSheet(this.sheet);
      showToast(t(reason === 'wrong' ? 'savedWrong' : 'savedChanged'));
    }
  }

  /** Il cestino: via subito, e «Annulla» la rimette com'era — sistemata da te
   *  solo se lo era gia' (`lock`). */
  async _remove(fileKey, entry) {
    const ok = await this._call('remove', { file: fileKey, id: entry.id });
    if (!ok) return;
    showUndo(t('removed'), () => this._restore(entry.id, null, { lock: Boolean(entry.locked) }),
      { label: i18n.t('apps.undo') });
  }

  /** «Rimetti» (una frase vecchia accanto a *after*, o una voce tolta): la
   *  scegli tu, e Dream non la riordina via di nuovo. «Annulla» passa *lock*. */
  async _restore(id, after = null, { lock = true } = {}) {
    const params = after ? { id, after } : { id };
    if (!lock) params.lock = false;
    await this._call('restore', params);
  }

  /* Un comando, poi si rilegge. `conflict`: la voce e' cambiata nel
     frattempo (Dream, la chat, l'editor) — si dice, e si mostra com'e' adesso. */
  async _call(method, params) {
    if (this.busy) return false;
    this.busy = true;
    let ok = true;
    try {
      await rpc.memory(method, params);
    } catch (err) {
      ok = false;
      showToast(t(err?.code === 'conflict' ? 'conflict' : 'failed'), 'error');
    }
    this.busy = false;
    await this.refresh();
    return ok;
  }
}
