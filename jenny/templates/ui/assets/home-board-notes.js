/** La casa — le note dell'utente nel cassetto di una scheda.
 *
 *  Una nota e' un post-it **lilla** (il giallo e' delle attese), e le note di
 *  una scheda sono un **mazzetto** appuntato accanto al titolo del cassetto:
 *  il tocco sfila la nota davanti e porta la prossima, «Tutte ›» le apre nel
 *  foglio della bacheca, in griglia. Li' una nota mostra al massimo quattro
 *  righe; il tocco la apre per esteso, e da aperta si corregge o si toglie.
 *  Sulla bacheca le note non ci sono mai.
 *
 *  I dati non cambiano: una nota resta una sezione `note` della scheda (una
 *  riga `[said]` del diario, che il giardiniere porta nella wiki), e si scrive
 *  con le RPC delle sezioni. La vista del cassetto le consegna gia' a parte,
 *  dalla piu' recente (`jenny/board/view.py`, `notes`).
 *
 *  Fino al 13/10/2026 una nota era una sezione «Nota · tua» in fondo al
 *  cassetto, scritta e corretta con un `promptDialog`: non si vedeva, e non
 *  sembrava una nota.
 */

import { i18n } from './shared/i18n.js';
import { confirmDialog } from './shared/dialog.js';

/* Le carte del mazzetto che si vedono; le altre aspettano sotto la terza. */
const DECK_SLOTS = 3;
/* Quanto resta fuori, di lato, la carta sfilata prima di passare sotto: a meta'
   della sua corsa, cosi' uscire e rientrare sono un movimento solo. */
const OUT_MS = 170;

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

/** «12 ott»: il giorno in cui la nota e' nata. */
function noteDate(iso, locale = i18n.locale) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return '';
  return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(at);
}

function reducedMotion() {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

export class DrawerNotes {
  /**
   * @param sheet  il foglio condiviso della bacheca (`HomeBoardSheet`).
   * @param write  `(method, params)` — una scrittura del cassetto (`BoardDrawer._write`),
   *               che dopo ricarica il cassetto e quindi richiama `render`.
   */
  constructor({ sheet, write }) {
    this.sheet = sheet;
    this.write = write;
    this.card = null;
    this.title = '';
    this.notes = [];
    /** La nota davanti al mazzetto, per indice. */
    this.front = 0;
    this._busy = false;
    /** Nel foglio: la nota aperta per esteso, quella in correzione ('new' per
     *  una nota nuova), e il testo che si sta scrivendo. */
    this.openId = null;
    this.editId = null;
    this.draft = '';
    this._list = null;
  }

  /** Il blocco in testa al cassetto: titoletto, «Tutte ›» e il mazzetto.
   *  `null` senza note (si aggiungono dal «+»). Se il foglio mostra la lista,
   *  la ridisegna dai dati nuovi. */
  render(data) {
    const changed = this.card !== data.id
      || this.notes.map((n) => n.id).join() !== (data.notes || []).map((n) => n.id).join();
    this.card = data.id;
    this.title = data.title;
    this.notes = data.notes || [];
    if (changed) this.front = 0;
    if (this._listShown()) {
      /* Dopo una scrittura la nota tolta o salvata torna a riposo. */
      if (!this.notes.some((n) => n.id === this.openId)) this.openId = null;
      if (this.editId !== 'new' && !this.notes.some((n) => n.id === this.editId)) this.editId = null;
      this._drawList();
      this._afterDraw();
    }
    if (!this.notes.length) return null;
    const block = el('section', 'home-notes');
    block.setAttribute('aria-label', i18n.t('board.notes.title'));
    const head = el('div', 'home-notes-head');
    head.append(el('h3', 'home-notes-title', `${i18n.t('board.notes.title')} · ${this.notes.length}`),
      button('home-notes-all', i18n.t('board.notes.all'), () => this.openList()));
    block.append(head, this._deck());
    return block;
  }

  /* ── Il mazzetto ── */

  _deck() {
    const deck = button('home-notes-deck', '', () => this._next());
    this.cards = this.notes.map((note, i) => {
      const card = el('span', 'home-notes-card');
      card.setAttribute('aria-hidden', 'true');
      const foot = el('span', 'home-notes-card-foot');
      foot.append(el('span', '', noteDate(note.created)), el('span', '', `${i + 1}/${this.notes.length}`));
      card.append(el('span', 'home-notes-pin'), el('span', 'home-notes-card-text', note.text), foot);
      deck.appendChild(card);
      return card;
    });
    this.deck = deck;
    this._layout(null);
    return deck;
  }

  /** Ogni carta al suo posto (una classe: posizione, colore e ombra sono del
   *  CSS). L'ordine di sovrapposizione si scrive qui, in linea: il mazzetto ha
   *  `isolation: isolate`, quindi resta dentro di lui e non puo' salire sopra
   *  Jenny o sopra il foglio — e il foglio di stile della casa resta senza
   *  `z-index`, come vuole la regola (l'unico e' di Jenny). */
  _layout(flyer) {
    const n = this.notes.length;
    this.cards.forEach((card, i) => {
      const k = (i - this.front + n) % n;
      const slot = i === flyer ? 'is-out' : k < DECK_SLOTS ? `is-slot-${k}` : 'is-hidden';
      card.className = `home-notes-card ${slot}`;
      card.style.zIndex = String(i === flyer ? DECK_SLOTS + 2 : DECK_SLOTS - Math.min(k, DECK_SLOTS));
    });
    const note = this.notes[this.front];
    this.deck.setAttribute('aria-label', i18n.t('board.notes.deck', {
      n: this.front + 1, count: n, text: note.text,
    }));
  }

  /** La nota davanti si sfila di lato mentre le altre avanzano di un posto;
   *  a meta' passa sotto e rientra in fondo. Un tocco durante il movimento
   *  non conta. */
  _next() {
    const n = this.notes.length;
    if (n < 2 || this._busy) return;
    const flyer = this.front;
    this.front = (this.front + 1) % n;
    if (reducedMotion()) {
      this._layout(null);
      return;
    }
    this._busy = true;
    this._layout(flyer);
    setTimeout(() => {
      this._busy = false;
      if (this.cards[flyer]?.isConnected) this._layout(null);
    }, OUT_MS);
  }

  /* ── Tutte le note, nel foglio ── */

  /** Apre il foglio con tutte le note; con `compose` gia' su una nota nuova. */
  openList({ compose = false } = {}) {
    this.openId = null;
    this.editId = compose ? 'new' : null;
    this.draft = '';
    this._list = el('div', 'home-notes-list');
    this._drawList();
    this.sheet.open(this._sheetTitle(), this._list);
    this._afterDraw();
  }

  _listShown() {
    return Boolean(this._list && this.sheet?.isOpen && this.sheet.body?.contains(this._list));
  }

  _sheetTitle() {
    return `${i18n.t('board.notes.title')} · ${this.notes.length}`;
  }

  _drawList() {
    const list = this._list;
    list.innerHTML = '';
    if (this.sheet?.isOpen && this.sheet.titleEl) this.sheet.titleEl.textContent = this._sheetTitle();
    list.appendChild(el('p', 'home-notes-sub', `${this.title} · ${i18n.t('board.notes.hint')}`));
    const grid = el('div', 'home-notes-grid');
    grid.appendChild(this.editId === 'new' ? this._editor(null)
      : button('home-notes-new', i18n.t('board.notes.new'), () => this._show({ editId: 'new', draft: '' })));
    for (const note of this.notes) {
      if (note.id === this.editId) grid.appendChild(this._editor(note));
      else if (note.id === this.openId) grid.appendChild(this._opened(note));
      else grid.appendChild(this._tile(note));
    }
    list.appendChild(grid);
  }

  /** Ridisegna la griglia in un altro stato, e rimette il fuoco dove serve. */
  _show(state) {
    Object.assign(this, { openId: null, editId: null }, state);
    this._drawList();
    this._afterDraw();
  }

  _afterDraw() {
    const area = this._list?.querySelector('.home-notes-area');
    if (area) {
      area.focus();
      area.setSelectionRange(area.value.length, area.value.length);
    }
    /* «Leggi tutto» solo dove il testo e' davvero tagliato: si misura dopo il
       disegno, non si contano i caratteri (dipende dalla larghezza). */
    requestAnimationFrame(() => {
      for (const tile of this._list?.querySelectorAll('.home-notes-tile') || []) {
        const text = tile.querySelector('.home-notes-tile-text');
        tile.querySelector('.home-notes-more').hidden = text.scrollHeight <= text.clientHeight + 1;
      }
    });
  }

  /** Una nota a riposo: quattro righe, la data, e «Leggi tutto» se serve. */
  _tile(note) {
    const tile = button('home-notes-tile', '', () => this._show({ openId: note.id }));
    tile.setAttribute('aria-label', `${note.text}, ${noteDate(note.created)}`);
    const foot = el('span', 'home-notes-tile-foot');
    const more = el('span', 'home-notes-more', `${i18n.t('board.notes.readMore')} ›`);
    more.hidden = true;
    foot.append(el('span', '', noteDate(note.created)), more);
    tile.append(el('span', 'home-notes-pin'), el('span', 'home-notes-tile-text', note.text), foot);
    return tile;
  }

  /** Una nota aperta: il testo intero, quando e' nata, se e' gia' nella wiki. */
  _opened(note) {
    const box = el('div', 'home-notes-open');
    const status = note.status === 'integrated' ? 'integrated' : 'pending';
    const bar = el('div', 'home-notes-bar');
    bar.append(
      el('span', 'home-notes-when', `${noteDate(note.created)} · ${i18n.t(`board.notes.${status}`)}`),
      button('home-notes-btn is-primary', i18n.t('board.notes.edit'),
        () => this._show({ editId: note.id, draft: note.text })),
      button('home-notes-btn', i18n.t('board.notes.close'), () => this._show({})),
    );
    box.append(el('span', 'home-notes-pin'), el('p', 'home-notes-open-text', note.text), bar);
    return box;
  }

  /** Una nota in scrittura: nuova (`note` null) o da correggere. */
  _editor(note) {
    const box = el('div', 'home-notes-open is-editing');
    const area = el('textarea', 'home-notes-area');
    area.value = this.draft;
    area.placeholder = i18n.t('board.notes.placeholder');
    area.setAttribute('aria-label', i18n.t('board.notes.title'));
    /* Alto quanto il testo, fino al tetto del CSS: una nota lunga si corregge
       vedendola, non scorrendo in una scatola di tre righe. */
    const fit = () => {
      area.style.height = 'auto';
      area.style.height = `${area.scrollHeight + 2}px`;
    };
    area.addEventListener('input', () => {
      this.draft = area.value;
      fit();
    });
    requestAnimationFrame(fit);
    const bar = el('div', 'home-notes-bar');
    bar.append(
      button('home-notes-btn is-primary', i18n.t('board.section.save'), () => this._save(note)),
      button('home-notes-btn', i18n.t('common.cancel'),
        () => this._show(note ? { openId: note.id } : {})),
    );
    if (note) {
      bar.append(el('span', 'home-notes-gap'),
        button('home-notes-btn is-danger', i18n.t('board.section.remove'), () => this._remove(note)));
    }
    box.append(area, bar);
    return box;
  }

  async _save(note) {
    const text = this.draft.trim();
    if (!text || text === note?.text) {
      this._show(note ? { openId: note.id } : {});
      return;
    }
    /* La scrittura ricarica il cassetto, e `render` ridisegna la lista gia'
       a riposo; se non riesce (l'avviso lo da' il cassetto) si resta a
       scrivere, col testo. */
    this.openId = null;
    this.editId = null;
    const ok = note
      ? await this.write('section.update', { card: this.card, id: note.id, text })
      : await this.write('section.add', { card: this.card, kind: 'note', text });
    if (!ok) this._show({ editId: note ? note.id : 'new', draft: text });
  }

  async _remove(note) {
    if (!(await confirmDialog(i18n.t('board.notes.removeConfirm')))) return;
    /* Tolta, `render` la trova sparita e chiude il suo editor. */
    await this.write('section.remove', { card: this.card, id: note.id });
  }
}
