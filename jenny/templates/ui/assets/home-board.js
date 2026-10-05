/** La casa — la bacheca di un quaderno.
 *
 *  **Un quaderno si apre sulla bacheca, non sulle pagine.** La wiki resta dov'e'
 *  e resta la fonte di verita', ma diventa l'archivio di Jenny: qui ci sono
 *  poche schede curate (una per soggetto, ognuna col suo cassetto), i post-it
 *  delle attese, i fili fra le schede e la busta con le proposte di Jenny.
 *
 *  La grammatica, e ogni oggetto ne dice una sola cosa:
 *  - **polaroid** = un soggetto. Tocca: apre il cassetto. Trascina: la sposti, e
 *    la puntina diventa rossa — da li' Jenny non la tocca piu'. Tieni premuto:
 *    si accendono i suoi fili, con l'etichetta, e il resto si attenua.
 *  - **post-it** = un'attesa. Tocca: il cassetto del suo soggetto.
 *  - **busta** = le proposte di Jenny. E' l'unico modo in cui la bacheca cambia
 *    forma senza che sia tu a farlo.
 *  - **un segnale per scheda, al massimo**: angolo rosso (un problema), angolo
 *    giallo (un'attesa vicina), puntino blu (novita' dall'ultima apertura).
 *
 *  **Il muro**: le posizioni sono in percento dello schermo, anche negative, e
 *  le decide il server; il client le disegna, e quando sposti qualcosa lo dice
 *  al server (`board.move`) — mai a `localStorage`, che la WebView perde al
 *  kill. Spostare e ingrandire il muro e' di d3-zoom; un dito su una scheda e'
 *  della scheda, finche' non ne arriva un secondo.
 *
 *  La bacheca e' anche **un elenco**: ogni oggetto e' un `role="button"`
 *  focalizzabile, e la vista elenco mostra le stesse schede in fila, per chi
 *  non ama le bacheche e per lo screen reader.
 */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { ensureVendor, escapeHtml, showToast } from './shared/utils.js';
import { WikiSearchIndex, foldText } from './shared/wiki-search.js';

/* Le misure del gesto, prese dal mockup e provate sul quadrato del Titan 2. */
export const LONG_PRESS_MS = 450;
export const DRAG_THRESHOLD_PX = 6;
/* L'etichetta di un filo acceso sta al 62% verso l'altro capo: a meta' finiva
   sotto la scheda sollevata. */
export const LABEL_AT = 0.62;
/* L'anteprima di una pagina fuori bacheca: le prime righe, non la pagina. */
export const PREVIEW_CHARS = 170;

/** Il colore della «foto» di una polaroid, da quello che la scheda e'.
 *
 *  Una tinta per id, stabile fra due aperture e fra due telefoni: la memoria di
 *  *dove* sta una cosa passa anche dal suo colore. Saturazione e luce restano
 *  basse, cosi' il colore e' un indizio e non una decorazione che grida. */
export function tintFor(id) {
  let h = 0;
  for (const ch of String(id || '')) h = (h * 31 + ch.codePointAt(0)) % 360;
  return [`hsl(${h} 32% 46%)`, `hsl(${(h + 18) % 360} 38% 28%)`];
}

/** La data di un'attesa, come la scrive un post-it: «10 ott», «mar 2027».
 *  Una condizione si scrive per quello che misura: «umidita' < 30». */
export function whenLabel(when, locale = i18n.locale) {
  if (!when) return '';
  if (when.kind === 'condition') return `${when.field} ${when.op} ${when.value}`;
  const [y, m, d] = String(when.date || '').split('-').map(Number);
  if (!y || !m) return '';
  const day = new Date(y, m - 1, d || 1);
  const opts = when.precision === 'month'
    ? { month: 'short', year: 'numeric' }
    : { day: 'numeric', month: 'short' };
  try {
    return new Intl.DateTimeFormat(locale, opts).format(day);
  } catch (_) {
    return when.date;
  }
}

/** Una riga di «dall'ultima volta», nella lingua di chi guarda.
 *
 *  Le righe le scrive il codice del giardiniere (e quello dei comandi), mai il
 *  modello: raccontano quel che e' stato fatto davvero. Arrivano come tipo e
 *  parametri, e la frase si compone qui. */
export function sinceText(row) {
  const key = `board.since.kind.${row?.kind}`;
  const params = { ...(row || {}) };
  /* Lo stato di un'app arriva come parola chiave (`alarm`, `stale`…): la
     frase la dice nella lingua di chi guarda. */
  if (row?.kind === 'app_event') params.state = i18n.t(`board.app.event.${row.state}`);
  const text = i18n.t(key, params);
  return text === key ? i18n.t('board.since.kind.other') : text;
}

/* Il muro, in percento dello schermo (come le posizioni): dall'origine fino a
   dieci schermi per lato, anche in negativo (`model.WALL_MAX`). Lo schermo
   d'aria oltre l'oggetto piu' lontano e l'ingrandimento possibile. L'ingombro
   degli oggetti e' `model.CARD_BOX`/`NOTE_BOX`. */
const WALL_MAX = 1000;
const AIR = 100;
const ZOOM_EXTENT = [0.3, 2.2];
const ITEM_BOX = { card: [20, 40], note: [15, 23] };
/* d3, che sposta e ingrandisce il muro: gia' spedito per le Jenny App, e
   caricato alla prima bacheca aperta, non all'avvio della casa. */
const D3_JS = '/html-mobile/assets/vendor/d3@7/d3.min.js';
/* A quanti pixel dal bordo il muro comincia a scorrere sotto una scheda
   trascinata, e di quanto a ogni movimento. */
const EDGE_SCROLL_PX = 36;
const EDGE_SCROLL_STEP = 14;

/** Il puntatore resta all'elemento anche fuori dai suoi bordi. Puo' rifiutare
 *  (un puntatore gia' finito, un evento sintetico): il gesto va avanti lo stesso. */
function capture(el, pointerId) {
  try {
    el.setPointerCapture?.(pointerId);
  } catch {
    /* niente: senza cattura il gesto funziona finche' il dito resta sopra */
  }
}

/** Dove sta il centro di una puntina, nelle coordinate del muro (senza zoom). */
function pinCenter(el, box, zoom = 1) {
  const pin = el.querySelector('.home-board-pin');
  const r = (pin || el).getBoundingClientRect();
  return {
    x: (r.left - box.left + r.width / 2) / zoom,
    y: (r.top - box.top + r.height / 2) / zoom,
  };
}

export class HomeBoard {
  /**
   * @param onOpenCard  `(cardId, title)` — apre il cassetto.
   * @param onOpenPage  `(path, label, cardId)` — apre una pagina nel lettore.
   * @param onAskJenny  `(text)` — porta una frase gia' scritta nella chat.
   * @param sheet       il foglio condiviso della bacheca (`HomeBoardSheet`).
   */
  constructor({ onOpenCard, onOpenPage, onAskJenny, sheet } = {}) {
    this.el = document.getElementById('home-board');
    this.canvas = document.getElementById('home-board-canvas');
    this.wall = document.getElementById('home-board-wall');
    this.threads = document.getElementById('home-board-threads');
    this.sinceBtn = document.getElementById('home-board-since');
    this.listEl = document.getElementById('home-board-list');
    this.emptyEl = document.getElementById('home-board-empty');
    this.onOpenCard = onOpenCard;
    this.onOpenPage = onOpenPage;
    this.onAskJenny = onAskJenny;
    this.sheet = sheet;
    this.notebook = null;
    this.data = null;
    /** La scheda coi fili accesi, o `null`. */
    this.focus = null;
    this.listView = false;
    this._token = 0;
    this._graph = null;

    /** Dove si guarda il muro: lo spostamento in pixel e l'ingrandimento, una
     *  `ZoomTransform` di d3 (`{x, y, k}`) che solo d3-zoom cambia. Una per
     *  quaderno, finche' la casa e' aperta: riaprire la bacheca torna li'. */
    this.view = { x: 0, y: 0, k: 1 };
    this._views = new Map();
    /** Il quaderno di cui `view` e' la vista. */
    this._viewOf = null;
    /** d3-zoom e la tela a cui e' legato, dalla prima bacheca aperta. */
    this._zoom = null;
    this._sel = null;
    /** Il trascinamento di una scheda in corso, con il modo di annullarlo. */
    this._itemDrag = null;

    this.sinceBtn?.addEventListener('click', () => this.openSince());
    /* Il menu del browser sulla pressione lunga non deve mai comparire sopra
       una scheda. */
    this.canvas?.addEventListener('contextmenu', (e) => e.preventDefault());
    /* I fili si ridisegnano quando la tela cambia misura: la tastiera che sale,
       la rotazione. Senza `ResizeObserver` (un banco senza layout) si
       ridisegnano solo quando la bacheca si ridisegna. */
    if (this.canvas && typeof ResizeObserver !== 'undefined') {
      this._resize = new ResizeObserver(() => {
        this._fitExtent();
        this.drawThreads();
      });
      this._resize.observe(this.canvas);
    }
    document.fonts?.ready?.then(() => this.drawThreads());
  }

  /** Carica e disegna la bacheca di *notebook*. */
  async load(notebook) {
    const token = ++this._token;
    /* Un altro quaderno: il muro di quello di prima si toglie **subito**. Restava
       a schermo finche' la bacheca nuova non arrivava, e per quel momento
       sembrava la sua. Lo stesso quaderno invece resta com'e' fino alla
       risposta: ridisegnare da vuoto sarebbe un lampo per niente. */
    if (notebook !== this.notebook) {
      this._graph = null;
      this.focus = null;
      this.data = null;
      this._clear();
      if (this.emptyEl) this.emptyEl.hidden = true;
    }
    this.notebook = notebook;
    let data;
    try {
      if (!this._zoom) {
        await ensureVendor(D3_JS);
        this._bindZoom();
      }
      data = await api.getBoard(notebook);
    } catch (err) {
      if (token !== this._token) return;
      console.warn('home.board: board failed', err);
      this.data = null;
      this._clear();
      this._empty(err?.status === 503 ? 'scope.createWikiOff' : 'board.failed');
      return;
    }
    if (token !== this._token) return;
    this.data = data;
    this.render();
    /* Il muro di un altro quaderno: si mette via la vista di quello, e si torna
       dove si guardava questo (o all'origine). */
    if (this._viewOf !== notebook) {
      if (this._viewOf) this._views.set(this._viewOf, this.view);
      this._viewOf = notebook;
      this._lookAt(this._views.get(notebook) || window.d3.zoomIdentity);
    }
    /* «L'ultima volta» e' adesso: la riga di novita' che hai appena visto non
       deve riaccendersi alla prossima apertura. */
    if (data.exists) {
      rpc.board('seen', notebook).catch((err) => console.warn('home.board: seen failed', err));
    }
  }

  /** Ridisegna da quel che si sa, senza chiedere al server. */
  render() {
    const data = this.data;
    this._clear();
    if (!data) return;
    this._fitExtent();
    this._drawSince();
    if (!data.cards.length) {
      this._empty(data.suggestions.length ? 'board.empty.proposed' : 'board.empty.title');
      this._drawEnvelope();
      return;
    }
    this.emptyEl.hidden = true;
    if (this.listView) {
      this._drawList();
      return;
    }
    for (const note of data.notes) this.wall.appendChild(this._postit(note));
    for (const card of data.cards) this.wall.appendChild(this._polaroid(card));
    this._drawEnvelope();
    /* I fili sopra le schede, e niente `z-index` (la casa ne ha uno solo, quello
       di Jenny): li mette sopra l'ordine nel DOM. */
    this.wall.appendChild(this.threads);
    this.drawThreads();
    requestAnimationFrame(() => this.drawThreads());
  }

  /** Bacheca o elenco: la stessa cosa, due forme. */
  toggleList() {
    this.listView = !this.listView;
    this.render();
    return this.listView;
  }

  applyTranslations() {
    if (this.data) this.render();
  }

  /* ── Il muro: spostare, ingrandire ── */

  _applyView() {
    const v = this.view;
    this.wall.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.k})`;
  }

  /** Lega d3-zoom alla tela, una volta. Un dito sul fondo sposta il muro, due lo
   *  ingrandiscono e lo spostano come una mappa; Ctrl+rotella (e la pinza del
   *  trackpad) ingrandisce, la rotella sola sposta. Un dito da solo su una
   *  scheda e' della scheda (`_bind`); il secondo dito ne fa una pinza, e la
   *  scheda torna dov'era. */
  _bindZoom() {
    if (this._zoom) return;
    const { d3 } = window;
    const onItem = (e) => e.target?.closest?.('.home-board-item, .home-board-envelope');
    this._sel = d3.select(this.canvas);
    this._zoom = d3.zoom()
      .scaleExtent(ZOOM_EXTENT)
      .filter((e) => (e.type === 'wheel'
        ? e.ctrlKey
        : !e.button && (e.touches?.length > 1 || !onItem(e))))
      .on('start', (e) => {
        /* Solo un gesto vero: lo scorrimento al bordo sotto una scheda
           trascinata e' un gesto del codice, e non deve lasciarla. */
        if (e.sourceEvent) this._itemDrag?.cancel();
      })
      .on('zoom', (e) => {
        this.view = e.transform;
        this._applyView();
      });
    this._sel.call(this._zoom)
      /* Il doppio tocco (e il doppio clic) sul fondo torna all'origine. */
      .on('dblclick.zoom', (e) => {
        if (!onItem(e)) this.resetView();
      });
    /* Un tocco sul fondo spegne i fili accesi. */
    this.canvas.addEventListener('click', (e) => {
      if (!onItem(e) && this.focus) this.setFocus(null);
    });
    this.canvas.addEventListener('wheel', (e) => {
      if (e.ctrlKey) return;
      e.preventDefault();
      const k = this.view.k;
      this._zoom.translateBy(this._sel, -e.deltaX / k, -e.deltaY / k);
    }, { passive: false });
  }

  /** Il muro non si perde: si puo' andare fin dove c'e' qualcosa, piu' uno
   *  schermo d'aria per lato (e lo schermo dell'origine c'e' sempre). Piu'
   *  piccolo della finestra, d3 lo centra. In pixel del muro, quindi da rifare
   *  quando la tela cambia misura o un oggetto si sposta. */
  _fitExtent() {
    const W = this.canvas?.clientWidth;
    const H = this.canvas?.clientHeight;
    if (!this._zoom || !W || !this.data) return;
    let x0 = 0, y0 = 0, x1 = 100, y1 = 100;
    const take = (pos, w, h) => {
      x0 = Math.min(x0, pos.x); y0 = Math.min(y0, pos.y);
      x1 = Math.max(x1, pos.x + w); y1 = Math.max(y1, pos.y + h);
    };
    for (const c of this.data.cards) take(c.pos, ITEM_BOX.card[0], ITEM_BOX.card[1]);
    for (const n of this.data.notes) take(n.pos, ITEM_BOX.note[0], ITEM_BOX.note[1]);
    this._zoom.translateExtent([
      [((x0 - AIR) / 100) * W, ((y0 - AIR) / 100) * H],
      [((x1 + AIR) / 100) * W, ((y1 + AIR) / 100) * H],
    ]);
    /* Un `translateExtent` nuovo non sposta niente da solo: uno spostamento
       nullo rimette la vista dentro. */
    this._zoom.translateBy(this._sel, 0, 0);
  }

  /** Porta il muro alla vista *t*, dentro i suoi bordi. */
  _lookAt(t) {
    this._zoom.transform(this._sel, t);
    this._zoom.translateBy(this._sel, 0, 0);
  }

  /** Il punto del muro al centro della finestra, in coordinate del muro: e' li'
   *  che nasce una scheda accettata dalla busta. */
  viewCenter() {
    const box = this.canvas?.getBoundingClientRect();
    if (!box?.width) return { x: 50, y: 50 };
    const v = this.view;
    return {
      x: Math.round((((box.width / 2 - v.x) / v.k) / box.width) * 1000) / 10,
      y: Math.round((((box.height / 2 - v.y) / v.k) / box.height) * 1000) / 10,
    };
  }

  /** Torna all'origine, a grandezza naturale. */
  resetView() {
    this._lookAt(window.d3.zoomIdentity);
  }

  _clear() {
    if (!this.canvas) return;
    this._itemDrag?.cancel();
    for (const el of [...this.wall.children]) {
      if (el !== this.threads) el.remove();
    }
    this.canvas.querySelector('.home-board-envelope')?.remove();
    if (this.threads) this.threads.innerHTML = '';
    if (this.listEl) {
      this.listEl.innerHTML = '';
      this.listEl.hidden = true;
    }
    this.canvas.hidden = false;
    if (this.sinceBtn) this.sinceBtn.hidden = true;
  }

  /** Il vuoto e' il tutorial: una frase, e un esempio che si tocca. */
  _empty(key) {
    if (!this.emptyEl) return;
    this.emptyEl.hidden = false;
    this.emptyEl.innerHTML = '';
    const title = document.createElement('p');
    title.className = 'home-board-empty-title';
    title.textContent = i18n.t(key);
    this.emptyEl.appendChild(title);
    if (key !== 'board.empty.title') return;
    const hint = document.createElement('p');
    hint.className = 'home-board-empty-hint';
    hint.textContent = i18n.t('board.empty.hint');
    const example = document.createElement('button');
    example.type = 'button';
    example.className = 'home-board-empty-example';
    example.textContent = `«${i18n.t('board.empty.example').trim()}…»`;
    example.addEventListener('click', () => this.onAskJenny?.(i18n.t('board.empty.example')));
    this.emptyEl.append(hint, example);
  }

  /* ── «Dall'ultima volta» ── */

  _sinceCount() {
    const data = this.data;
    if (!data) return 0;
    return data.since.length + (data.suggestions.length ? 1 : 0);
  }

  _drawSince() {
    const n = this._sinceCount();
    if (!this.sinceBtn) return;
    this.sinceBtn.hidden = !n;
    if (!n) return;
    this.sinceBtn.innerHTML = '';
    const dot = document.createElement('span');
    dot.className = 'home-board-since-dot';
    const text = document.createElement('span');
    text.innerHTML = escapeHtml(i18n.t('board.since.row', { count: n }))
      .replace(String(n), `<b>${n}</b>`);
    const chev = document.createElement('i');
    chev.className = 'ti ti-chevron-right home-board-since-chev';
    chev.setAttribute('aria-hidden', 'true');
    this.sinceBtn.append(dot, text, chev);
  }

  openSince() {
    const data = this.data;
    if (!data) return;
    const rows = data.since.map(sinceText);
    if (data.suggestions.length) {
      rows.push(i18n.t(data.suggestions.length === 1 ? 'board.since.envelopeOne'
        : 'board.since.envelopeMany', { count: data.suggestions.length }));
    }
    const body = document.createElement('div');
    const list = document.createElement('ul');
    list.className = 'home-board-since-list';
    for (const row of rows) {
      const li = document.createElement('li');
      li.textContent = row;
      list.appendChild(li);
    }
    const note = document.createElement('p');
    note.className = 'home-board-muted';
    note.textContent = i18n.t('board.since.note');
    body.append(list, note);
    this.sheet.open(i18n.t('board.since.title'), body);
  }

  /* ── Gli oggetti ── */

  _place(el, item) {
    /* Percento dello schermo, non del muro: un oggetto non si sposta quando il
       muro cresce. `cqw`/`cqh` sono quelli della tela, che e' la finestra. */
    el.style.left = `${item.pos.x}cqw`;
    el.style.top = `${item.pos.y}cqh`;
    el.dataset.id = item.id;
    const pin = document.createElement('span');
    pin.className = `home-board-pin is-${item.pos.by === 'user' ? 'user' : 'jenny'}`;
    pin.setAttribute('aria-hidden', 'true');
    el.appendChild(pin);
  }

  _polaroid(card) {
    const el = document.createElement('div');
    el.className = 'home-board-item home-board-polaroid';
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    el.style.setProperty('--tilt', `${this._tilt(card.id)}deg`);
    const [a, b] = tintFor(card.id);
    const photo = document.createElement('div');
    photo.className = 'home-board-photo';
    photo.style.background = `linear-gradient(160deg, ${a}, ${b})`;
    if (card.glyph) {
      const icon = document.createElement('i');
      icon.className = `ti ti-${card.glyph}`;
      icon.setAttribute('aria-hidden', 'true');
      photo.appendChild(icon);
    } else {
      const initial = document.createElement('span');
      initial.className = 'home-board-initial';
      initial.textContent = (card.title || '?').trim().charAt(0).toUpperCase();
      photo.appendChild(initial);
    }
    if (card.signal) {
      const sig = document.createElement('span');
      sig.className = card.signal === 'new' ? 'home-board-sig-dot' : `home-board-sig-corner is-${card.signal}`;
      photo.appendChild(sig);
    }
    /* Il segno ⟳: qualcosa in questo cassetto gira da solo (una micro app, un
       cron). Un segno e non un segnale: in basso, e mai rosso. */
    if (card.auto) {
      const auto = document.createElement('i');
      auto.className = 'ti ti-refresh home-board-auto';
      auto.setAttribute('aria-hidden', 'true');
      photo.appendChild(auto);
    }
    const label = document.createElement('div');
    label.className = 'home-board-label';
    label.textContent = card.title;
    el.append(photo, label);
    const sigWord = card.signal ? `, ${i18n.t(`board.signal.${card.signal}`)}` : '';
    const autoWord = card.auto ? `, ${i18n.t('board.signal.auto')}` : '';
    el.setAttribute('aria-label', `${card.title}${sigWord}${autoWord}`);
    this._place(el, card);
    this._bind(el, card, 'card');
    return el;
  }

  _postit(note) {
    const el = document.createElement('div');
    el.className = 'home-board-item home-board-postit';
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    el.style.setProperty('--tilt', `${this._tilt(note.id) + 3}deg`);
    const t = document.createElement('span');
    t.className = 'home-board-postit-text';
    t.textContent = note.text;
    const w = document.createElement('span');
    w.className = 'home-board-postit-when';
    w.textContent = whenLabel(note.when);
    el.append(t, w);
    el.setAttribute('aria-label', `${note.text}, ${w.textContent}`);
    this._place(el, note);
    this._bind(el, note, 'note');
    return el;
  }

  /* Un'inclinazione piccola e stabile per oggetto: la bacheca deve sembrare
     appuntata a mano, non stampata — ma sempre allo stesso modo. */
  _tilt(id) {
    let h = 7;
    for (const ch of String(id)) h = (h * 17 + ch.codePointAt(0)) % 997;
    return (h % 7) - 3;
  }

  _drawEnvelope() {
    const count = this.data?.suggestions?.length || 0;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `home-board-envelope${count ? '' : ' is-empty'}`;
    btn.setAttribute('aria-label', i18n.t('board.envelope.label', { count }));
    const badge = document.createElement('span');
    badge.className = 'home-board-envelope-count';
    badge.textContent = String(count);
    btn.appendChild(badge);
    btn.addEventListener('click', () => this.openEnvelope());
    this.canvas.appendChild(btn);
  }

  _drawList() {
    this.canvas.hidden = true;
    this.listEl.hidden = false;
    for (const card of this.data.cards) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'home-board-list-row';
      row.setAttribute('role', 'listitem');
      const name = document.createElement('span');
      name.className = 'home-board-list-name';
      name.textContent = card.title;
      row.appendChild(name);
      const next = this.data.notes.find((n) => n.card === card.id);
      if (next) {
        const when = document.createElement('span');
        when.className = 'home-board-list-next';
        when.textContent = `${next.text} · ${whenLabel(next.when)}`;
        row.appendChild(when);
      }
      if (card.signal) {
        const sig = document.createElement('span');
        sig.className = `home-board-list-sig is-${card.signal}`;
        sig.textContent = i18n.t(`board.signal.${card.signal}`);
        row.appendChild(sig);
      }
      row.addEventListener('click', () => this.onOpenCard?.(card.id, card.title));
      this.listEl.appendChild(row);
    }
    const envelope = document.createElement('button');
    envelope.type = 'button';
    envelope.className = 'home-board-list-row home-board-list-envelope';
    envelope.textContent = i18n.t('board.envelope.label', { count: this.data.suggestions.length });
    envelope.addEventListener('click', () => this.openEnvelope());
    this.listEl.appendChild(envelope);
  }

  /* ── Il gesto: tocca, trascina, tieni premuto ── */

  _bind(el, item, kind) {
    let start = null;
    let moved = false;
    let longFired = false;
    let timer = null;
    const open = () => {
      const cardId = kind === 'card' ? item.id : item.card;
      const card = this.data?.cards.find((c) => c.id === cardId);
      this.onOpenCard?.(cardId, card?.title || '');
    };
    /* La scheda torna dov'era e smette di seguire il dito: arriva una pinza,
       il gesto si interrompe, o la bacheca si ridisegna sotto il dito. */
    const drop = () => {
      clearTimeout(timer);
      if (moved) {
        el.classList.remove('is-dragging');
        el.style.left = `${item.pos.x}cqw`;
        el.style.top = `${item.pos.y}cqh`;
      }
      el._suppress = true;
      start = null;
      moved = false;
      this._itemDrag = null;
    };
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      /* Un secondo dito mentre un altro e' gia' giu': e' una pinza, non un
         trascinamento (la prende d3-zoom, v. `_bindZoom`). */
      if (e.isPrimary === false || this._itemDrag) return;
      const box = this.canvas.getBoundingClientRect();
      start = {
        px: e.clientX, py: e.clientY, x: item.pos.x, y: item.pos.y, w: box.width, h: box.height,
        vx: this.view.x, vy: this.view.y, box,
      };
      moved = false;
      longFired = false;
      this._itemDrag = { cancel: drop };
      capture(el, e.pointerId);
      if (kind === 'card') {
        timer = setTimeout(() => {
          longFired = true;
          this.setFocus(this.focus === item.id ? null : item.id);
        }, LONG_PRESS_MS);
      }
    });
    el.addEventListener('pointermove', (e) => {
      if (!start || longFired) return;
      const dx = e.clientX - start.px;
      const dy = e.clientY - start.py;
      if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
      clearTimeout(timer);
      if (!moved) {
        moved = true;
        el.classList.add('is-dragging');
        /* In cima a tutto mentre la porti, fili compresi: l'ultimo nel DOM.
           Spostarla nel DOM le toglie il puntatore, e senza il dito andrebbe
           al muro: lo si riprende subito. */
        this.wall.appendChild(el);
        capture(el, e.pointerId);
        if (this.focus) this.setFocus(null);
      }
      /* Vicino al bordo della finestra il muro scorre da se', e la scheda va
         con lui: cosi' la porti su un altro schermo senza lasciarla. */
      const b = start.box;
      const edge = EDGE_SCROLL_PX;
      const step = (lo, hi, at) => (at > hi - edge ? -EDGE_SCROLL_STEP
        : at < lo + edge ? EDGE_SCROLL_STEP : 0);
      const ex = step(b.left, b.right, e.clientX);
      const ey = step(b.top, b.bottom, e.clientY);
      if (ex || ey) this._zoom.translateBy(this._sel, ex / this.view.k, ey / this.view.k);
      /* Il dito si muove in pixel dello schermo; la scheda in percento dello
         schermo *del muro*, che puo' essere ingrandito. */
      const k = this.view.k;
      const sdx = (dx - (this.view.x - start.vx)) / k;
      const sdy = (dy - (this.view.y - start.vy)) / k;
      const w = (el.offsetWidth / start.w) * 100;
      const h = (el.offsetHeight / start.h) * 100;
      const x = Math.max(-WALL_MAX, Math.min(WALL_MAX - w, start.x + (sdx / start.w) * 100));
      const y = Math.max(-WALL_MAX, Math.min(WALL_MAX - h, start.y + (sdy / start.h) * 100));
      el.style.left = `${x}cqw`;
      el.style.top = `${y}cqh`;
      el._next = { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 };
      this.drawThreads();
    });
    const end = () => {
      if (!start) return;
      clearTimeout(timer);
      if (moved && el._next) {
        el.classList.remove('is-dragging');
        item.pos = { ...el._next, by: 'user' };
        el.querySelector('.home-board-pin')?.classList.replace('is-jenny', 'is-user');
        this._fitExtent();
        this.drawThreads();
        rpc.board('move', this.notebook, { kind, id: item.id, x: item.pos.x, y: item.pos.y })
          .catch((err) => {
            console.warn('home.board: move failed', err);
            showToast(i18n.t('board.moveFailed'), 'error');
            this.load(this.notebook);
          });
      }
      el._suppress = moved || longFired;
      start = null;
      this._itemDrag = null;
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', () => {
      if (start) drop();
    });
    el.addEventListener('click', () => {
      if (el._suppress) {
        el._suppress = false;
        return;
      }
      open();
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    });
  }

  /** Accende i fili di una scheda (o li spegne tutti, con `null`). */
  setFocus(id) {
    this.focus = id;
    const lit = this.drawThreads();
    if (id && !lit) showToast(i18n.t('board.noThreads'), 'info');
  }

  /** I fili: sempre visibili, sottili e senza scritte; quelli della scheda
   *  tenuta premuta si accendono, con l'etichetta, e il resto si attenua.
   *  Vero se ne ha acceso almeno uno. */
  drawThreads() {
    const svg = this.threads;
    if (!svg || !this.canvas || !this.data || this.listView) return false;
    svg.innerHTML = '';
    this.wall.querySelectorAll('.home-board-thread-label').forEach((l) => l.remove());
    this.wall.querySelectorAll('.home-board-item').forEach((i) => i.classList.remove('is-lifted', 'is-linked'));
    /* Le coordinate dei fili sono quelle del muro (origine e ingrandimento
       tolti), cosi' si spostano e si ingrandiscono con le schede. */
    const box = this.wall.getBoundingClientRect();
    if (!this.canvas.getBoundingClientRect().width) return false;
    const zoom = this.view.k;
    const elOf = (id) => this.wall.querySelector(`.home-board-polaroid[data-id="${CSS.escape(id)}"]`);
    const self = this.focus ? elOf(this.focus) : null;
    if (this.focus && !self) this.focus = null;
    this.canvas.classList.toggle('is-focus', Boolean(this.focus));
    self?.classList.add('is-lifted');
    /* La scheda sollevata sopra le altre e sotto i fili. */
    if (self && self.nextElementSibling !== svg) this.wall.insertBefore(self, svg);
    let lit = false;
    for (const conn of this.data.connections) {
      const ea = elOf(conn.a);
      const eb = elOf(conn.b);
      if (!ea || !eb) continue;
      const on = Boolean(this.focus) && (conn.a === this.focus || conn.b === this.focus);
      const a = pinCenter(ea, box, zoom);
      const b = pinCenter(eb, box, zoom);
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', a.x);
      line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x);
      line.setAttribute('y2', b.y);
      line.setAttribute('class', !this.focus ? 'is-rest' : on ? 'is-on' : 'is-off');
      svg.appendChild(line);
      if (!on) continue;
      lit = true;
      (conn.a === this.focus ? eb : ea).classList.add('is-linked');
      if (!conn.label) continue;
      const from = conn.a === this.focus ? a : b;
      const to = conn.a === this.focus ? b : a;
      const label = document.createElement('span');
      label.className = 'home-board-thread-label';
      label.textContent = conn.label;
      label.style.left = `${from.x + (to.x - from.x) * LABEL_AT}px`;
      label.style.top = `${from.y + (to.y - from.y) * LABEL_AT}px`;
      this.wall.appendChild(label);
    }
    return lit;
  }

  /* ── La busta ── */

  openEnvelope() {
    const suggestions = this.data?.suggestions || [];
    const body = document.createElement('div');
    body.className = 'home-board-proposals';
    if (!suggestions.length) {
      const p = document.createElement('p');
      p.className = 'home-board-muted';
      p.textContent = i18n.t('board.envelope.empty');
      body.appendChild(p);
      this.sheet.open(i18n.t('board.envelope.emptyTitle'), body);
      return;
    }
    for (const s of suggestions) body.appendChild(this._proposal(s));
    this.sheet.open(i18n.t('board.envelope.title'), body);
  }

  _proposal(s) {
    const box = document.createElement('div');
    box.className = 'home-board-proposal';
    const who = document.createElement('span');
    who.className = 'home-board-proposal-who';
    who.textContent = i18n.t('board.envelope.from');
    const text = document.createElement('p');
    text.textContent = s.text || i18n.t(`board.envelope.kind.${s.kind}`, { subject: s.subject });
    const actions = document.createElement('div');
    actions.className = 'home-board-actions';
    /* «Togliere» ha le sue parole: il no a una scheda da mettere via e' un
       «lasciala», non un rifiuto. L'ultimo bottone e' quello che fa. */
    const answers = s.kind === 'remove_card'
      ? [['no', 'board.envelope.keep'], ['yes', 'board.envelope.putAway']]
      : [['later', 'board.envelope.later'], ['no', 'board.envelope.no'], ['yes', 'board.envelope.yes']];
    answers.forEach(([answer, key], i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `home-board-btn${i === answers.length - 1 ? ' is-primary' : ''}`;
      b.textContent = i18n.t(key);
      b.addEventListener('click', () => this._respond(s, answer, box));
      actions.appendChild(b);
    });
    box.append(who, text, actions);
    return box;
  }

  async _respond(s, answer, box) {
    box.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      await rpc.board('suggestion.respond', this.notebook, { id: s.id, answer, near: this.viewCenter() });
    } catch (err) {
      console.warn('home.board: respond failed', err);
      showToast(i18n.t(err?.code === 'conflict' ? 'board.envelope.full' : 'board.envelope.failed'), 'error');
      box.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      return;
    }
    box.remove();
    if (!this.sheet.body?.querySelector('.home-board-proposal')) this.sheet.close();
    await this.load(this.notebook);
    /* Una scheda appena accettata compare con un piccolo salto: e' nuova, e
       deve farsi trovare. */
    if (answer === 'yes') {
      this.canvas.querySelector('.home-board-polaroid:last-of-type')?.classList.add('is-appearing');
    }
  }

  /* ── La ricerca: l'unica porta verso tutto ── */

  async openSearch() {
    const body = document.createElement('div');
    body.className = 'home-board-search';
    const input = document.createElement('input');
    input.type = 'search';
    input.className = 'home-board-search-input';
    input.placeholder = i18n.t('board.search.placeholder');
    input.setAttribute('aria-label', i18n.t('board.search.placeholder'));
    input.autocomplete = 'off';
    input.spellcheck = false;
    const results = document.createElement('div');
    results.className = 'home-board-results';
    body.append(input, results);
    this.sheet.open(i18n.t('board.search.title'), body);
    input.focus();
    try {
      this._graph ||= await api.getGraph(this.notebook);
    } catch (err) {
      console.warn('home.board: search index failed', err);
    }
    const index = this._graph ? WikiSearchIndex.from(this._graph.search) : null;
    const draw = () => this._drawResults(results, input.value, index);
    input.addEventListener('input', draw);
    draw();
  }

  /** I risultati: le schede per nome, poi le pagine per titolo e testo. Ogni
   *  risultato dice dove sta — il cassetto, o «fuori bacheca». */
  searchResults(query, index) {
    const q = foldText(query || '').trim();
    if (!q) return [];
    const data = this.data || { cards: [], pageOwners: {} };
    const owners = data.pageOwners || {};
    const titleOf = Object.fromEntries((data.cards || []).map((c) => [c.id, c.title]));
    const out = (data.cards || [])
      .filter((c) => foldText(c.title).includes(q))
      .map((c) => ({ kind: 'card', card: c.id, title: c.title }));
    const nodes = this._graph?.nodes || [];
    const hit = index ? index.query(query) : null;
    nodes.forEach((node, i) => {
      if (node.path === 'index.md') return;
      if (hit ? !hit.mask[i] : !foldText(node.title || node.label || '').includes(q)) return;
      const card = owners[node.path] || null;
      out.push({
        kind: card ? 'page' : 'offboard',
        path: node.path,
        title: node.title || node.label,
        card,
        cardTitle: card ? titleOf[card] : '',
      });
    });
    return out.slice(0, 40);
  }

  _drawResults(el, query, index) {
    el.innerHTML = '';
    const results = this.searchResults(query, index);
    if (!results.length) {
      if (query.trim()) {
        const none = document.createElement('p');
        none.className = 'home-board-muted';
        none.textContent = i18n.t('board.search.none');
        el.appendChild(none);
      }
      return;
    }
    for (const r of results) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'home-board-result';
      const name = document.createElement('span');
      name.textContent = r.title;
      const badge = document.createElement('span');
      badge.className = `home-board-result-badge${r.kind === 'offboard' ? '' : ' is-in'}`;
      badge.textContent = r.kind === 'card' ? i18n.t('board.search.card')
        : r.kind === 'page' ? r.cardTitle : i18n.t('board.offboard');
      row.append(name, badge);
      row.addEventListener('click', () => {
        this.sheet.close();
        if (r.kind === 'card') this.onOpenCard?.(r.card, r.title);
        else if (r.kind === 'page') this.onOpenPage?.(r.path, r.title, r.card);
        else this.preview(r.path, null);
      });
      el.appendChild(row);
    }
  }

  /* ── L'anteprima di una pagina fuori bacheca ── */

  /** Le prime righe di una pagina che non sta in nessun cassetto, con la
   *  strada per chiederla in bacheca. *from* e' la scheda da cui la si e'
   *  aperta: il giardiniere, di solito, la aggancia li'. */
  async preview(path, from) {
    let page;
    try {
      page = await api.getPage({ wiki: this.notebook, page: path });
    } catch (err) {
      console.warn('home.board: preview failed', err);
      showToast(i18n.t('home.reader.failed'), 'error');
      return;
    }
    /* Contata: le pagine aperte spesso in anteprima sono quelle che la bacheca
       trascura, e la passata dopo lo legge. Un conto perso non e' un guasto. */
    rpc.board('preview', this.notebook, { page: path }).catch(() => {});
    const tmp = document.createElement('div');
    tmp.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(page.html || '') : '';
    tmp.querySelector('h1')?.remove();
    const text = (tmp.textContent || page.raw || '').replace(/\s+/g, ' ').trim();
    const short = text.length > PREVIEW_CHARS
      ? `${text.slice(0, PREVIEW_CHARS).replace(/\s\S*$/, '')}…`
      : text;
    const body = document.createElement('div');
    const p = document.createElement('p');
    p.textContent = short;
    const note = document.createElement('p');
    note.className = 'home-board-muted';
    note.textContent = i18n.t('board.preview.notOnBoard');
    const actions = document.createElement('div');
    actions.className = 'home-board-actions';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'home-board-btn';
    close.textContent = i18n.t('board.preview.close');
    close.addEventListener('click', () => this.sheet.close());
    const want = document.createElement('button');
    want.type = 'button';
    want.className = 'home-board-btn is-primary';
    want.textContent = i18n.t('board.preview.want');
    want.addEventListener('click', async () => {
      want.disabled = true;
      try {
        await rpc.board('offboard.want', this.notebook, { page: path, from: from || undefined });
        this.sheet.close();
        showToast(i18n.t('board.preview.wanted'), 'success');
      } catch (err) {
        console.warn('home.board: want failed', err);
        want.disabled = false;
        showToast(i18n.t('board.envelope.failed'), 'error');
      }
    });
    actions.append(close, want);
    body.append(p, note, actions);
    this.sheet.open(page.title || path, body);
  }
}

/** Il foglio dal basso della bacheca: uno solo, per tutto quel che sale —
 *  «dall'ultima volta», la busta, la ricerca, l'anteprima, «aggiungi sezione».
 *
 *  Un `<dialog>` con `showModal()`, come le schede della casa: sta nel top
 *  layer, sopra la mascotte, e Indietro lo chiude per primo. */
export class HomeBoardSheet {
  constructor() {
    this.dialog = document.getElementById('home-board-sheet');
    this.titleEl = document.getElementById('home-board-sheet-title');
    this.body = document.getElementById('home-board-sheet-body');
    this.dialog?.addEventListener('click', (e) => {
      if (e.target === this.dialog) this.close();
    });
  }

  get isOpen() {
    return Boolean(this.dialog?.open);
  }

  open(title, content) {
    if (!this.dialog) return;
    this.titleEl.textContent = title || '';
    this.body.innerHTML = '';
    this.body.appendChild(content);
    if (!this.dialog.open) this.dialog.showModal();
  }

  close() {
    if (this.dialog?.open) this.dialog.close();
  }
}
