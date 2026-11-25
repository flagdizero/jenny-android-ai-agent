/** La casa — «Cosa ricorda di te».
 *
 *  Quel che Jenny sa di te (`USER.md`, «Di te») e i suoi appunti
 *  (`memory/MEMORY.md`, «I suoi appunti»), come frasi. Fino al 25/11/2026 si
 *  vedevano solo in officina, come Markdown grezzo nell'editor di file, che li
 *  riscriveva senza lasciare traccia.
 *
 *  - **Correggere**: un tocco sulla voce apre un foglio col testo; si dice se
 *    *e' cambiato* o se *non era vero*. La voce corretta prende il lucchetto:
 *    Dream non la riordina (cede solo a una correzione che dici in chat).
 *  - **Togliere**: il cestino toglie subito, con «Annulla». Una voce tolta non
 *    torna quando la nomini.
 *  - **I dettagli persi**: un nome o un numero che una riscrittura ha perso, e
 *    che nessun file dice piu', sta sotto la voce, con «Rimetti».
 *  - **«Dimenticato di recente»**: quel che e' uscito nelle ultime settimane.
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
    const text = button('home-memory-text', null, () => this._edit(fileKey, entry));
    text.appendChild(el('span', 'home-memory-sentence', entry.text));
    if (entry.locked) {
      const lock = el('span', 'home-memory-locked');
      lock.append(icon('ti-lock'), document.createTextNode(t('locked')));
      text.appendChild(lock);
    }
    row.appendChild(text);
    const trash = button('home-memory-remove', null, () => this._remove(fileKey, entry));
    trash.setAttribute('aria-label', t('remove'));
    trash.appendChild(icon('ti-trash'));
    row.appendChild(trash);
    if (entry.lost?.length) row.appendChild(this._lost(entry));
    return row;
  }

  /* Sotto la voce: cosa una riscrittura ha perso, una riga per frase vecchia
     (con tutte le sue parole) e un «Rimetti» per riga. */
  _lost(entry) {
    const box = el('div', 'home-memory-lost');
    for (const detail of entry.lost) {
      const words = (detail.words || [detail.word]).filter(Boolean).join(', ');
      const line = el('div', 'home-memory-lost-line');
      line.appendChild(el('span', null, t('lost', { word: words })));
      const put = button('home-memory-putback', t('putBack'),
        () => this._restore(detail.id, entry.id));
      put.setAttribute('aria-label', t('putBackDetail', { word: words }));
      line.appendChild(put);
      box.appendChild(line);
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
    const actions = el('div', 'home-memory-actions');
    actions.append(
      button('home-memory-changed', t('changed'), () => choose('changed')),
      button('home-memory-wrong', t('wrong'), () => choose('wrong')),
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

  /** Il cestino: via subito, e «Annulla» la rimette dov'era. */
  async _remove(fileKey, entry) {
    const ok = await this._call('remove', { file: fileKey, id: entry.id });
    if (!ok) return;
    showUndo(t('removed'), () => this._restore(entry.id), { label: i18n.t('apps.undo') });
  }

  async _restore(id, after = null) {
    await this._call('restore', after ? { id, after } : { id });
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
