/** La casa — le foto di una scheda: la galleria.
 *
 *  Una foto e' una **polaroid** bianca fissata con lo scotch (la carta delle
 *  note e' lilla, quella delle attese gialla), e le foto di una scheda sono un
 *  mazzetto: nel cassetto sotto il brief, accanto a quello delle note, e sulla
 *  bacheca accanto alla scheda. Il tocco sfila la foto davanti e porta la
 *  prossima (`home-board-deck.js`), «Tutte ›» apre la galleria nel foglio della
 *  bacheca: tre per riga, dalla piu' recente, con «Nuova foto» in testa.
 *
 *  **Ogni foto nasce con una nota**, facoltativa: scelta l'immagine, il foglio
 *  si apre sulla polaroid nuova con il campo per scriverla, e si carica solo al
 *  Salva (Annulla non lascia niente). La nota segue le regole di una nota: una
 *  riga `[said]` del diario, che il giardiniere porta nella wiki; senza nota
 *  niente diario. Aperta, una foto e' una **pagina d'album**: la polaroid
 *  quadrata con la sua nota e, accanto, la data, lo stato e i comandi —
 *  correggi la nota (nella polaroid stessa), a tutto schermo, togli (la
 *  sezione, non il file: `raw/` e' il grezzo); in alto «Tutte le foto» e le
 *  frecce fra le foto. Un tocco sulla polaroid la apre **intera**, nelle sue
 *  proporzioni, a tutto schermo (`home-photo-viewer.js`). Fino al 14/10/2026
 *  la foto aperta era una striscia alta 170 px, ritagliata: di una verticale se
 *  ne vedeva un terzo, e nessun gesto la mostrava tutta.
 *
 *  Fino al 14/10/2026 una foto era una sezione «Foto» a tutta larghezza in
 *  fondo al cassetto, senza un campo per la nota: cinque foto, cinque sezioni.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { confirmDialog } from './shared/dialog.js';
import { PinnedDeck } from './home-board-deck.js';
import { openPhotoViewer } from './home-photo-viewer.js';

/* Il lato lungo di una foto mandata al server: il telefono ne fa di 4000 px, e
   in un cassetto largo 590 ne servono meno di un terzo. */
const PHOTO_MAX_SIDE = 1600;
/* Le polaroid del mazzetto con l'immagine caricata: le tre che si vedono. */
const DECK_LOADED = 3;

/** Riduce una foto prima di mandarla: JPEG, lato lungo `PHOTO_MAX_SIDE`. */
export async function shrinkPhoto(file) {
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

/** «14 ottobre»: la data per esteso, nella pagina d'album e a tutto schermo. */
export function photoDay(iso, locale = i18n.locale) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return '';
  return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'long' }).format(at);
}

/** «14 ott»: il giorno in cui la foto e' stata appuntata. */
export function photoDate(iso, locale = i18n.locale) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return '';
  return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(at);
}

/** Le immagini di un quaderno come `blob:` (la rotta vuole il segreto, che un
 *  `<img src>` non porterebbe), una volta per percorso: a parita' di percorso
 *  una foto non cambia mai. `drop()` le rilascia tutte. */
export class PhotoUrls {
  constructor() {
    this.notebook = '';
    this._urls = new Map();
  }

  get(notebook, path) {
    if (notebook !== this.notebook) {
      this.drop();
      this.notebook = notebook;
    }
    if (!this._urls.has(path)) {
      this._urls.set(path, api.getBoardPhotoUrl(notebook, path).catch((err) => {
        console.warn('home.photos: photo failed', err);
        this._urls.delete(path);
        return '';
      }));
    }
    return this._urls.get(path);
  }

  /** Mette l'immagine in *img* quando arriva (se nel frattempo e' ancora li'). */
  fill(img, notebook, path) {
    if (!img || img.dataset.path === path) return;
    img.dataset.path = path;
    this.get(notebook, path).then((url) => {
      if (url && img.dataset.path === path) img.src = url;
    });
  }

  drop() {
    for (const p of this._urls.values()) {
      p.then((url) => { if (url) URL.revokeObjectURL?.(url); });
    }
    this._urls.clear();
  }
}

export class DrawerPhotos {
  /**
   * @param sheet     il foglio condiviso della bacheca (`HomeBoardSheet`).
   * @param write     `(method, params)` — una scrittura, che dopo rilegge la
   *                  scheda e quindi richiama `render` (`true` se e' andata).
   * @param notebook  `() => string`: il quaderno aperto.
   * @param urls      le immagini gia' prese (`PhotoUrls`), condivise con chi
   *                  disegna le stesse foto altrove.
   */
  constructor({ sheet, write, notebook, urls = new PhotoUrls() }) {
    this.sheet = sheet;
    this.write = write;
    this.notebook = notebook;
    this.urls = urls;
    /** Come si riduce un file scelto: un gancio per i test (jsdom non ha canvas). */
    this.shrink = shrinkPhoto;
    this.card = null;
    this.title = '';
    this.photos = [];
    /** La foto davanti al mazzetto, per indice. */
    this.front = 0;
    /** Nel foglio: la foto aperta, quella in correzione ('new' per una nuova),
     *  la nota che si sta scrivendo, e la foto nuova scelta e non ancora mandata. */
    this.openId = null;
    this.editId = null;
    this.draft = '';
    this.pending = null;
    this._saving = false;
    this._list = null;
    this._seen = null;
  }

  /** Il blocco del cassetto: titoletto, «Tutte ›» e il mazzetto. `null` senza
   *  foto (si aggiungono dal «+»). Se il foglio mostra la galleria, la
   *  ridisegna dai dati nuovi. */
  render(data) {
    const photos = (data.photos || []).filter((p) => p.exists !== false);
    const changed = this.card !== data.id
      || this.photos.map((p) => p.id).join() !== photos.map((p) => p.id).join();
    this.card = data.id;
    this.title = data.title;
    this.photos = photos;
    if (changed) this.front = 0;
    if (this._listShown()) {
      if (!this.photos.some((p) => p.id === this.openId)) this.openId = null;
      if (this.editId !== 'new' && !this.photos.some((p) => p.id === this.editId)) this.editId = null;
      this._drawList();
    }
    if (!this.photos.length) return null;
    const block = el('section', 'home-photos');
    block.setAttribute('aria-label', i18n.t('board.photos.title'));
    const head = el('div', 'home-notes-head');
    head.append(el('h3', 'home-notes-title home-photos-title',
      `${i18n.t('board.photos.title')} · ${this.photos.length}`),
    button('home-notes-all', i18n.t('board.notes.all'), () => this.openList()));
    block.append(head, this._deck());
    return block;
  }

  /* ── Il mazzetto ── */

  _deck() {
    const n = this.photos.length;
    this._pinned = new PinnedDeck({
      className: 'home-photos-deck',
      cardClass: 'home-photos-card',
      count: n,
      front: this.front,
      onTurn: (front) => {
        this.front = front;
        this._fillDeck();
      },
      drawCard: (i) => {
        const photo = this.photos[i];
        const img = el('img', 'home-photos-img');
        img.alt = '';
        const cap = el('span', 'home-photos-cap');
        cap.append(el('span', 'home-photos-cap-text', photo.caption || ' '),
          el('span', 'home-photos-cap-when', photoDate(photo.created)));
        return [el('span', 'home-photos-tape'), img, cap];
      },
      label: (i) => {
        const photo = this.photos[i];
        return i18n.t('board.photos.deck', {
          n: i + 1, count: n,
          text: photo.caption || i18n.t('board.photos.noNote'),
          when: photoDate(photo.created),
        });
      },
    });
    this.deck = this._pinned.el;
    this.cards = this._pinned.cards;
    this._fillDeck();
    return this.deck;
  }

  /** Le immagini delle polaroid che si vedono: le altre arrivano sfogliando. */
  _fillDeck() {
    const n = this.photos.length;
    for (let k = 0; k < Math.min(DECK_LOADED, n); k++) {
      const i = (this.front + k) % n;
      this.urls.fill(this.cards[i].querySelector('img'), this.notebook(), this.photos[i].path);
    }
  }

  /* ── La galleria, nel foglio ── */

  /** Apre la galleria; con `openId` gia' su quella foto. */
  openList({ openId = null } = {}) {
    this.openId = openId;
    this.editId = null;
    this.draft = '';
    this._list = el('div', 'home-notes-list home-photos-list');
    this._drawList();
    this.sheet.open(this._sheetTitle(), this._list);
  }

  /** Sceglie un'immagine e apre il foglio sulla polaroid nuova, con la nota da
   *  scrivere. Si chiama dentro un tocco: il selettore di file lo vuole. */
  compose() {
    const input = el('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      if (!file) return;
      let photo;
      try {
        photo = await this.shrink(file);
      } catch (err) {
        console.warn('home.photos: photo unreadable', err);
        this.onFailed?.();
        return;
      }
      this._dropPending();
      this.pending = { ...photo, preview: URL.createObjectURL(file) };
      if (!this._listShown()) this.openList();
      this._show({ editId: 'new', draft: '' });
    });
    input.click();
  }

  _listShown() {
    return Boolean(this._list && this.sheet?.isOpen && this.sheet.body?.contains(this._list));
  }

  _sheetTitle() {
    if (this.editId === 'new') return i18n.t('board.photos.new');
    if (this.openId || this.editId) return this.title;
    return `${i18n.t('board.photos.title')} · ${this.photos.length}`;
  }

  _drawList() {
    const list = this._list;
    this._seen?.disconnect();
    list.innerHTML = '';
    if (this.sheet?.isOpen && this.sheet.titleEl) this.sheet.titleEl.textContent = this._sheetTitle();
    /* Una foto aperta, in correzione o nuova: la sua pagina d'album. */
    const shown = this.photos.find((p) => p.id === (this.editId || this.openId));
    if (this.editId === 'new' || shown) {
      list.appendChild(this._page(this.editId === 'new' ? null : shown));
      this._lazy(list);
      const area = list.querySelector('.home-photos-pol-area');
      if (area) {
        area.focus();
        area.setSelectionRange(area.value.length, area.value.length);
      }
      return;
    }
    list.appendChild(el('p', 'home-notes-sub', `${this.title} · ${i18n.t('board.photos.hint')}`));
    const grid = el('div', 'home-photos-grid');
    const add = button('home-photos-new', '', () => this.compose());
    const icon = el('i', 'ti ti-camera-plus');
    icon.setAttribute('aria-hidden', 'true');
    add.append(icon, el('span', '', i18n.t('board.photos.new')));
    grid.appendChild(add);
    for (const photo of this.photos) grid.appendChild(this._tile(photo));
    list.appendChild(grid);
    this._lazy(grid);
  }

  /** Le immagini della griglia si prendono quando entrano nel foglio: una
   *  scheda con trenta foto non scarica trenta immagini all'apertura. */
  _lazy(grid) {
    const imgs = [...grid.querySelectorAll('img[data-want]')];
    const load = (img) => this.urls.fill(img, this.notebook(), img.dataset.want);
    if (typeof IntersectionObserver !== 'function') {
      imgs.forEach(load);
      return;
    }
    this._seen = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        load(entry.target);
        this._seen.unobserve(entry.target);
      }
    }, { root: this.sheet?.body || null, rootMargin: '200px' });
    imgs.forEach((img) => this._seen.observe(img));
  }

  _show(state) {
    Object.assign(this, { openId: null, editId: null }, state);
    if (this.editId !== 'new') this._dropPending();
    this._drawList();
  }

  _dropPending() {
    if (this.pending?.preview) URL.revokeObjectURL?.(this.pending.preview);
    this.pending = null;
  }

  _img(photo, className) {
    const img = el('img', className);
    img.alt = photo.caption || '';
    img.dataset.want = photo.path;
    return img;
  }

  /** Una foto a riposo: l'immagine, la nota su due righe, la data. */
  _tile(photo) {
    const tile = button('home-photos-tile', '', () => this._show({ openId: photo.id }));
    tile.setAttribute('aria-label', `${photo.caption || i18n.t('board.photos.noNote')}, ${photoDate(photo.created)}`);
    const cap = el('span', `home-photos-tile-text${photo.caption ? '' : ' is-empty'}`,
      photo.caption || i18n.t('board.photos.noNote'));
    tile.append(this._img(photo, 'home-photos-img'), cap,
      el('span', 'home-photos-tile-when', photoDate(photo.created)));
    return tile;
  }

  _status(photo) {
    if (photo.status === 'integrated') return i18n.t('board.photos.integrated');
    if (photo.status === 'pending') return i18n.t('board.photos.pending');
    return '';
  }

  _icon(name) {
    const icon = el('i', `ti ti-${name}`);
    icon.setAttribute('aria-hidden', 'true');
    return icon;
  }

  /** Un comando della colonna accanto alla polaroid: icona e parole. */
  _act(className, iconName, text, onClick) {
    const b = button(`home-photos-act${className ? ` ${className}` : ''}`, '', onClick);
    b.append(this._icon(iconName), el('span', '', text));
    return b;
  }

  /** La pagina d'album di una foto: in alto «Tutte le foto» e le frecce; la
   *  polaroid quadrata con la nota (un campo, quando la si scrive); accanto la
   *  data, lo stato e i comandi. `photo` null e' la foto nuova appena scelta. */
  _page(photo) {
    const editing = this.editId !== null;
    const page = el('div', `home-photos-page${editing ? ' is-editing' : ''}`);

    const nav = el('div', 'home-photos-nav');
    const back = this._act('home-photos-back', 'chevron-left', i18n.t('board.photos.back'),
      () => this._show({}));
    nav.appendChild(back);
    if (photo && !editing && this.photos.length > 1) {
      const i = this.photos.indexOf(photo);
      const go = (k) => () => this._show({ openId: this.photos[k].id });
      const prev = button('home-photos-step', '', go(i - 1));
      prev.appendChild(this._icon('chevron-left'));
      prev.setAttribute('aria-label', i18n.t('board.photos.prev'));
      prev.disabled = i === 0;
      const next = button('home-photos-step', '', go(i + 1));
      next.appendChild(this._icon('chevron-right'));
      next.setAttribute('aria-label', i18n.t('board.photos.next'));
      next.disabled = i === this.photos.length - 1;
      const pager = el('span', 'home-photos-pager');
      pager.append(prev, el('span', 'home-photos-pager-at',
        i18n.t('board.photos.pager', { n: i + 1, count: this.photos.length })), next);
      nav.appendChild(pager);
    }

    const album = el('div', 'home-photos-album');
    const pol = el('div', 'home-photos-pol');
    const shot = photo && !editing ? button('home-photos-pol-img', '', () => this._full(photo))
      : el('div', 'home-photos-pol-img');
    let img;
    if (photo) {
      img = this._img(photo, '');
    } else {
      img = el('img');
      img.alt = '';
      if (this.pending?.preview) img.src = this.pending.preview;
    }
    shot.appendChild(img);
    if (photo && !editing) {
      shot.setAttribute('aria-label', i18n.t('board.photos.full'));
      shot.appendChild(el('span', 'home-photos-pol-full')).appendChild(this._icon('arrows-maximize'));
    }
    const cap = el('div', 'home-photos-pol-cap');
    if (editing) {
      const area = el('textarea', 'home-photos-pol-area');
      area.value = this.draft;
      area.rows = 2;
      area.placeholder = i18n.t('board.photos.placeholder');
      area.setAttribute('aria-label', i18n.t('board.photos.noteLabel'));
      area.addEventListener('input', () => { this.draft = area.value; });
      cap.appendChild(area);
    } else {
      cap.appendChild(el('span', `home-photos-pol-note${photo.caption ? '' : ' is-empty'}`,
        photo.caption || i18n.t('board.photos.noNote')));
    }
    pol.append(el('span', 'home-photos-tape'), shot, cap);

    const side = el('div', 'home-photos-side');
    side.appendChild(el('span', 'home-photos-day',
      photo ? photoDay(photo.created) : i18n.t('board.photos.now')));
    const status = photo ? this._status(photo) : '';
    if (status) {
      const state = el('span', `home-photos-state is-${photo.status}`);
      state.append(el('i'), el('span', '', status));
      side.appendChild(state);
    }
    side.appendChild(el('span', 'home-photos-rule'));
    const acts = el('div', 'home-photos-acts');
    if (editing) {
      const save = button('home-photos-save', i18n.t('board.section.save'), () => this._save(photo));
      save.disabled = this._saving;
      acts.append(save, this._act('is-quiet', 'x', i18n.t('common.cancel'),
        () => this._show(photo ? { openId: photo.id } : {})));
    } else {
      acts.append(
        this._act('', 'pencil', i18n.t('board.photos.edit'),
          () => this._show({ editId: photo.id, openId: photo.id, draft: photo.caption || '' })),
        this._act('', 'arrows-maximize', i18n.t('board.photos.fullShort'), () => this._full(photo)),
        this._act('is-quiet', 'trash', i18n.t('board.photos.remove'), () => this._remove(photo)),
      );
    }
    side.appendChild(acts);
    album.append(pol, side);
    page.append(nav, album);
    return page;
  }

  /** La foto a tutto schermo, intera, sopra il foglio; sfogliando, la pagina
   *  d'album sotto segue. I `blob:` restano di `PhotoUrls`. */
  _full(photo) {
    const notebook = this.notebook();
    openPhotoViewer({
      photos: this.photos,
      index: Math.max(0, this.photos.indexOf(photo)),
      title: this.title,
      urlOf: (path) => this.urls.get(notebook, path),
      words: {
        count: (n, count, card) => i18n.t('board.photos.viewer', { card, n, count }),
        day: (iso) => photoDay(iso),
        state: (p) => this._status(p),
        close: i18n.t('board.notes.close'),
      },
      onIndex: (i) => {
        if (!this.openId || this.editId || this.photos[i]?.id === this.openId) return;
        this.openId = this.photos[i].id;
        if (this._listShown()) this._drawList();
      },
    });
  }

  async _save(photo) {
    if (this._saving) return;
    const text = this.draft.trim();
    if (photo && text === (photo.caption || '')) {
      this._show({ openId: photo.id });
      return;
    }
    if (!photo && !this.pending) return;
    /* La scrittura rilegge la scheda e `render` ridisegna la galleria a riposo;
       se non riesce (l'avviso lo da' chi scrive) si resta a scrivere, col testo. */
    this._saving = true;
    this._drawList();
    const pending = this.pending;
    const ok = photo
      ? await this.write('section.update', { card: this.card, id: photo.id, text })
      : await this.write('section.add', {
        card: this.card, kind: 'photo', mime: pending.mime, data: pending.data, caption: text,
      });
    this._saving = false;
    if (ok) {
      if (!photo) this._dropPending();
      this._show(photo && this.photos.some((p) => p.id === photo.id) ? { openId: photo.id } : {});
    } else {
      this._show({ editId: photo ? photo.id : 'new', openId: photo ? photo.id : null, draft: text });
    }
  }

  async _remove(photo) {
    if (!(await confirmDialog(i18n.t('board.photos.removeConfirm')))) return;
    await this.write('section.remove', { card: this.card, id: photo.id });
  }
}
