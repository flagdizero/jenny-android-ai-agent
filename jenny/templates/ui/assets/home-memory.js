/** La casa — «Cosa ricorda di te».
 *
 *  Quel che Jenny sa di te (`USER.md`, «Di te») e i suoi appunti
 *  (`memory/MEMORY.md`, «I suoi appunti»), come frasi.
 *
 *  **Un ritratto a temi, di solo testo.** Le frasi di «Di te» stanno sotto un
 *  tema (chi sei, persone, salute...), una scheda per tema; sulle righe non
 *  c'e' nessuna icona. Il tema non e' nel file: lo tiene `agent/memory_themes.py`
 *  e lo assegna il modello alle frasi che non l'hanno, in sottofondo, quando la
 *  stanza si apre (`sorting`: una riga lo dice, e la stanza si rilegge).
 *  Finche' nessuna frase ha un tema, stanno in fila.
 *
 *  Un tocco sulla frase apre il foglio, e li' c'e' tutto:
 *
 *  - **Correggere**: il testo e un solo «Salva». La frase di prima non torna da
 *    sola, e quella che hai scritto tu Jenny non la riscrive quando riordina
 *    (il lucchetto, che qui non si vede).
 *  - **Togliere**: «Togli questa frase», poi «Annulla» per qualche secondo, che
 *    la rimette com'era. Due tocchi, non uno: con un cestino su ogni riga una
 *    frase si toglieva anche per sbaglio, scorrendo.
 *  - **«Tolte da te»**: in fondo, chiusa, la lista di quel che hai tolto tu
 *    nelle ultime settimane, con «Rimetti».
 *
 *  **Cosa non c'e', di proposito.** Fino al 26/11/2026 la stanza mostrava anche
 *  i dettagli persi dalle riscritture, le formulazioni cambiate e quelle tolte
 *  da Jenny, un lucchetto e due tasti per dire perche' si corregge. Erano i
 *  meccanismi dell'archivio messi in vetrina: per meta' stime (un legame fra
 *  due frasi scelto per somiglianza), e nessuno li capiva. L'archivio continua
 *  a lavorare da solo — Jenny ripesca una formulazione vecchia quando la
 *  nomini — e qui resta solo quel che e' certo.
 *
 *  Un gesto rilegge e ridisegna la stanza, ma **la posizione resta**
 *  (`placeOf`/`restorePlace`). Tutto passa dai comandi `memory.*`
 *  (`webui/memory_commands.py`); una voce cambiata nel frattempo risponde
 *  `conflict`: si rilegge, invece di scrivere sopra.
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
/* Mentre le frasi prendono un tema, piu' spesso: sono pochi secondi. */
const SORT_POLL_MS = 2_500;

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

/** Il valore della riga di Impostazioni: quante cose sa di te. */
export function memorySummary(data) {
  const mine = (data?.files || []).find((f) => f.file === 'user');
  if (!mine) return '';
  const n = mine.entries.length;
  return n ? t('count', { n }) : t('nothingYet');
}

/* I temi, nell'ordine in cui si leggono (le chiavi di
   `agent/memory_themes.py`). `other` e le frasi ancora senza tema in fondo. */
const THEMES = ['identity', 'people', 'health', 'home', 'tastes', 'character', 'plans', 'answers'];

/** Le frasi raggruppate per tema, nell'ordine di {@link THEMES}; in fondo
 *  «Altro», con quelle che non ne hanno uno. Se nessuna ha un tema (il modello
 *  non le ha ancora ordinate) torna un gruppo solo senza nome: la stanza le
 *  mostra in fila, senza un «Altro» che le contiene tutte. */
export function byTheme(entries) {
  const groups = new Map(THEMES.map((key) => [key, []]));
  const rest = [];
  for (const entry of entries || []) {
    if (groups.has(entry.theme)) groups.get(entry.theme).push(entry);
    else rest.push(entry);
  }
  const out = THEMES.filter((key) => groups.get(key).length)
    .map((key) => ({ key, entries: groups.get(key) }));
  if (!out.length) return rest.length ? [{ key: '', entries: rest }] : [];
  if (rest.length) out.push({ key: 'other', entries: rest });
  return out;
}

/** Le frasi che hai tolto tu, per «Tolte da te». */
export function removedRows(data) {
  return (data?.recent || []).filter((row) => row.kind === 'removed');
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
    /* Le sezioni chiuse che hai aperto: restano aperte a un ridisegno, e si
       richiudono quando esci dalla stanza. */
    this._opened = new Set();
  }

  open() {
    this._open = true;
    this.render();
    this.refresh();
  }

  close() {
    this._open = false;
    this._opened.clear();
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
    const wait = this.data?.sorting ? SORT_POLL_MS : this.data?.dreaming ? DREAM_POLL_MS : 0;
    if (wait && this._open) this._timer = window.setTimeout(() => this.refresh(), wait);
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
    const files = this.data.files || [];
    const mine = files.find((f) => f.file === 'user');
    const notes = files.find((f) => f.file === 'memory');
    if (mine) parts.push(this._aboutYou(mine));
    if (notes?.entries.length) parts.push(this._folded('notes', notes));
    const removed = removedRows(this.data);
    if (removed.length) parts.push(this._folded('removed', null, removed));
    /* Vuota, la frase di sopra dice gia' come si riempie: niente piede. */
    if (mine?.entries.length) parts.push(el('p', 'home-memory-foot', t('foot', { name: botName.get() })));
    this.body.replaceChildren(...parts);
    restorePlace(this.body, place, top);
  }

  /** «Di te»: sempre aperta, e' la stanza. */
  _aboutYou(file) {
    const wrap = el('div', 'home-memory-section');
    wrap.dataset.file = file.file;
    const head = el('div', 'home-memory-sec');
    head.append(el('span', null, t('aboutYou')),
      el('span', 'home-memory-n', String(file.entries.length || '')));
    wrap.appendChild(head);
    if (!file.entries.length) {
      wrap.appendChild(el('p', 'home-memory-empty', t('emptyUser', { name: botName.get() })));
      return wrap;
    }
    wrap.appendChild(el('p', 'home-memory-hint', t('hint')));
    if (this.data.sorting) {
      wrap.appendChild(el('p', 'home-memory-sorting', t('sorting', { name: botName.get() })));
    }
    for (const group of byTheme(file.entries)) {
      if (!group.key) {
        wrap.appendChild(this._list(file.file, group.entries));
        continue;
      }
      const box = el('div', 'home-memory-theme');
      box.dataset.theme = group.key;
      const name = el('div', 'home-memory-theme-name');
      name.dataset.place = `theme:${group.key}`;
      name.append(el('span', null, t(`theme.${group.key}`)),
        el('span', 'home-memory-n', String(group.entries.length)));
      box.append(name, this._list(file.file, group.entries));
      wrap.appendChild(box);
    }
    return wrap;
  }

  /** Una sezione chiusa: «I suoi appunti» (`notes`) o «Tolte da te»
   *  (`removed`). La testata e' un tasto; aperta, mostra le sue righe. */
  _folded(kind, file, rows = null) {
    const wrap = el('div', `home-memory-section is-folded is-${kind}`);
    if (file) wrap.dataset.file = file.file;
    const open = this._opened.has(kind);
    const n = file ? file.entries.length : rows.length;
    const head = button('home-memory-fold', null, () => {
      if (this._opened.has(kind)) this._opened.delete(kind);
      else this._opened.add(kind);
      this.render();
    });
    head.dataset.place = `fold:${kind}`;
    head.setAttribute('aria-expanded', open ? 'true' : 'false');
    const label = el('span', 'home-memory-fold-label');
    label.append(el('span', null, t(kind === 'notes' ? 'herNotes' : 'removedByYou')),
      el('span', 'home-memory-n', String(n)));
    head.append(label, icon(open ? 'ti-chevron-up' : 'ti-chevron-down'));
    wrap.appendChild(head);
    if (!open) return wrap;
    wrap.appendChild(el('p', 'home-memory-hint',
      t(kind === 'notes' ? 'notesHint' : 'removedHint', { name: botName.get() })));
    wrap.appendChild(kind === 'notes' ? this._list(file.file, file.entries) : this._removed(rows));
    return wrap;
  }

  _list(fileKey, entries) {
    const list = el('div', 'home-rows home-memory-list');
    for (const entry of entries) list.appendChild(this._entry(fileKey, entry));
    return list;
  }

  _entry(fileKey, entry) {
    const row = el('div', 'home-row home-memory-row');
    row.dataset.id = entry.id;
    row.dataset.place = entry.id;
    const text = button('home-memory-text', null, () => this._edit(fileKey, entry));
    text.appendChild(el('span', 'home-memory-sentence', entry.text));
    row.appendChild(text);
    return row;
  }

  _removed(rows) {
    const list = el('div', 'home-rows home-memory-list');
    for (const row of rows) {
      const node = el('div', 'home-row home-memory-row is-removed');
      node.dataset.id = row.id;
      node.dataset.place = `removed:${row.id}`;
      const text = el('div', 'home-memory-text');
      text.appendChild(el('span', 'home-memory-sentence', row.text));
      node.appendChild(text);
      node.appendChild(button('home-memory-putback', t('putBack'), () => this._restore(row.id)));
      list.appendChild(node);
    }
    return list;
  }

  /* ── I gesti ─────────────────────────────────────────────────────────── */

  /** Il foglio di correzione: il testo e «Salva». */
  _edit(fileKey, entry) {
    const field = document.createElement('textarea');
    field.className = 'home-memory-field';
    field.value = entry.text;
    field.rows = 3;
    field.setAttribute('aria-label', t('fieldLabel'));
    const save = button('home-memory-save', t('save'),
      () => this._correct(fileKey, entry, field.value));
    const drop = button('home-memory-drop', null, () => {
      closeActionSheet(this.sheet);
      this._remove(fileKey, entry);
    });
    drop.append(icon('ti-trash'), el('span', null, t('removeThis')));
    openActionSheet(this.sheet, {
      closeLabel: t('close'),
      onClose: () => closeActionSheet(this.sheet),
      children: [
        ...sheetHead(t('editTitle'), t('editSub')),
        field,
        save,
        drop,
        el('p', 'home-plan-sheet-note', t('keepNote', { name: botName.get() })),
      ],
    });
    field.focus?.();
  }

  /* Una correzione fatta qui dice che la frase di prima non andava: non torna
     da sola col richiamo per nome (`reason: 'wrong'`). Un fatto che e' cambiato
     davvero si racconta in chat, e lo scrive Jenny. */
  async _correct(fileKey, entry, text) {
    if (!String(text || '').trim()) return;
    const ok = await this._call('correct', { file: fileKey, id: entry.id, text, reason: 'wrong' });
    if (ok) {
      closeActionSheet(this.sheet);
      showToast(t('saved'));
    }
  }

  /** Il cestino: via subito, e «Annulla» la rimette com'era — col lucchetto
   *  solo se lo aveva gia' (`lock`). */
  async _remove(fileKey, entry) {
    const ok = await this._call('remove', { file: fileKey, id: entry.id });
    if (!ok) return;
    showUndo(t('removed'), () => this._restore(entry.id, { lock: Boolean(entry.locked) }),
      { label: i18n.t('apps.undo') });
  }

  /** «Rimetti» da «Tolte da te»: la scegli tu, e Jenny non la riordina via di
   *  nuovo. «Annulla» passa *lock* com'era la voce. */
  async _restore(id, { lock = true } = {}) {
    await this._call('restore', lock ? { id } : { id, lock: false });
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
