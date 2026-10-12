/** La casa — la bacheca di un quaderno.
 *
 *  **Un quaderno si apre sulla bacheca, non sulle pagine.** La wiki resta dov'e'
 *  e resta la fonte di verita', ma diventa l'archivio di Jenny: qui ci sono
 *  poche schede curate (una per soggetto, ognuna col suo cassetto), l'agenda
 *  delle attese, i fili fra le schede e la busta con le proposte di Jenny.
 *
 *  La grammatica, e ogni oggetto ne dice una sola cosa:
 *  - **polaroid** = un soggetto. Tocca: apre il cassetto. Trascina: la sposti, e
 *    la puntina diventa rossa — da li' Jenny non la tocca piu'. Tieni premuto:
 *    si accendono i suoi fili, con l'etichetta, il resto si attenua, e in basso
 *    sale la barra della scheda (Rinomina · Togli dalla bacheca).
 *  - **il muro vuoto**: tienilo premuto e nasce una scheda li', sotto il dito
 *    (lo stesso del + in alto, che la mette al centro di quel che guardi).
 *  - **agenda** = le attese, un foglio solo per bacheca: le prossime tre, e un
 *    filo dal suo chiodo a ogni scheda che vi compare. Tocca una riga: il
 *    cassetto del suo soggetto; «+N più avanti»: tutte, per mese. Trascina: la
 *    sposti, come una scheda. Fino al 12/10/2026 ogni attesa era un post-it a
 *    sé, senza filo: sette foglietti per tre piante, e di chi fossero lo diceva
 *    solo la vicinanza.
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
import { ensureVendor, escapeHtml, showToast, showUndo } from './shared/utils.js';
import { WikiSearchIndex, foldText } from './shared/wiki-search.js';
import { metaText, renderBlock } from './home-board-blocks.js';

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

/** La data di un'attesa, come la scrive l'agenda: «10 ott», «mar 2027».
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

/** Il giorno di *d* come `AAAA-MM-GG`, nell'ora di chi guarda (non in UTC). */
export function localDay(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Le attese in gruppi, nell'ordine del calendario di cura: quelle gia'
 *  passate e ancora aperte («in ritardo»), poi un gruppo per mese, poi quelle a
 *  condizione, che un giorno non ce l'hanno («quando succede»).
 *
 *  Arrivano gia' ordinate dal gateway (`model.sorted_reminders`), quindi ogni
 *  gruppo e' una corsa di righe vicine. Un'attesa «a marzo» non e' in ritardo
 *  finche' marzo non e' finito. Il nome del mese porta l'anno solo se non e'
 *  quello in corso. */
export function reminderGroups(rems, today = new Date(), locale = i18n.locale) {
  const now = localDay(today);
  const groups = [];
  let cur = null;
  for (const rem of rems || []) {
    const when = rem.when || {};
    const date = String(when.date || '');
    let key = 'whenever';
    if (when.kind !== 'condition' && date) {
      const late = when.precision === 'month' ? date.slice(0, 7) < now.slice(0, 7) : date < now;
      key = late ? 'overdue' : date.slice(0, 7);
    }
    if (!cur || cur.key !== key) {
      const kind = key === 'overdue' || key === 'whenever' ? key : 'month';
      cur = { key, kind, label: kind === 'month' ? monthLabel(key, today, locale) : '', items: [] };
      groups.push(cur);
    }
    cur.items.push(rem);
  }
  return groups;
}

function monthLabel(ym, today, locale) {
  const [y, m] = ym.split('-').map(Number);
  const opts = y === today.getFullYear() ? { month: 'long' } : { month: 'long', year: 'numeric' };
  try {
    return new Intl.DateTimeFormat(locale, opts).format(new Date(y, m - 1, 1));
  } catch (_) {
    return ym;
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
   degli oggetti e' `model.CARD_BOX`/`AGENDA_BOX`. */
const WALL_MAX = 1000;
const AIR = 100;
const ZOOM_EXTENT = [0.3, 2.2];
/* La striscia: una riga e' il 3,55% della larghezza (`SLIP_ROWS` in
   `jenny/board/model.py`, e `--slip-row` nel CSS). I suoi controlli si toccano da
   quando una riga supera NEAR_ROW_PX pixel sullo schermo. */
const SLIP_ROW = 0.0355;
const NEAR_ROW_PX = 30;
const ITEM_BOX = { card: [20, 40], agenda: [30, 48] };
/* Entrando, la bacheca si inquadra tutta (`zoomToFit`): l'aria in pixel fra le
   schede e il bordo, e l'ingrandimento massimo di quell'inquadratura — a
   grandezza naturale, non oltre: due schede sole non diventano due manifesti. */
const FIT_PAD = 16;
const FIT_MAX_K = 1;
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
   * @param onOpenApp   `(slug)` — apre lo schermo di un'app (o la sua prova).
   * @param onOpenPage  `(path, label, cardId)` — apre una pagina nel lettore.
   * @param onAskJenny  `(text)` — porta una frase gia' scritta nella chat.
   * @param sheet       il foglio condiviso della bacheca (`HomeBoardSheet`).
   */
  constructor({ onOpenCard, onOpenApp, onOpenPage, onAskJenny, sheet } = {}) {
    this.el = document.getElementById('home-board');
    this.canvas = document.getElementById('home-board-canvas');
    this.wall = document.getElementById('home-board-wall');
    this.threads = document.getElementById('home-board-threads');
    this.sinceBtn = document.getElementById('home-board-since');
    this.listEl = document.getElementById('home-board-list');
    this.emptyEl = document.getElementById('home-board-empty');
    this.onOpenCard = onOpenCard;
    this.onOpenApp = onOpenApp;
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
     *  `ZoomTransform` di d3 (`{x, y, k}`) che solo d3-zoom cambia. Non si
     *  ricorda: dal 06/10/2026 ogni ingresso nella bacheca la inquadra tutta
     *  (`zoomToFit`). */
    this.view = { x: 0, y: 0, k: 1 };
    /** Il muro e' stato mosso da un dito dopo l'ingresso: la risposta del
     *  server, se arriva dopo, non gli rifa' l'inquadratura sotto il dito. */
    this._touched = false;
    /** L'inquadratura e' in attesa che la tela abbia una misura (v. `_fit`). */
    this._fitOnSize = false;
    /** d3-zoom e la tela a cui e' legato, dalla prima bacheca aperta. */
    this._zoom = null;
    this._sel = null;
    /** Il trascinamento di una scheda in corso, con il modo di annullarlo. */
    this._itemDrag = null;
    /** La barra della scheda tenuta premuta, creata la prima volta che serve. */
    this._cardBar = null;

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
        if (this._fitOnSize && this.zoomToFit()) this._showWall();
        this.drawThreads();
      });
      this._resize.observe(this.canvas);
    }
    document.fonts?.ready?.then(() => this.drawThreads());
  }

  /** Carica e disegna la bacheca di *notebook*.
   *
   *  *fit* e' chi entra nel quaderno da fuori — i Quaderni, la sua scheda, la
   *  porta appesa, un quaderno appena nato (v. `HomeApp.openNotebook`): la
   *  bacheca si inquadra tutta. Chi ci torna da dentro — Indietro da un
   *  cassetto o da una pagina, il percorso in alto — la ritrova dove l'aveva
   *  lasciata. Si inquadra comunque quando una vista a cui tornare non c'e':
   *  questo muro non e' ancora stato disegnato, o l'ultimo era di un altro
   *  quaderno. */
  async load(notebook, { fit = false } = {}) {
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
    const same = notebook === this.notebook && Boolean(this.data);
    const frame = fit || !same;
    this.notebook = notebook;
    this._touched = false;
    /* Lo stesso quaderno ha gia' il suo muro a schermo: se va inquadrato, lo si
       fa subito, prima che la bacheca si veda, invece di mostrarlo dove lo si
       era lasciato e farlo saltare all'arrivo della risposta. */
    if (frame && same) this._fit();
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
    /* Nello stesso giro del disegno, quindi prima del primo fotogramma: la
       bacheca compare gia' inquadrata, mai all'origine e poi zoomata. */
    if (frame && !this._touched) this._fit();
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
    for (const url of this._mediaUrls || []) URL.revokeObjectURL(url);
    this._mediaUrls = [];
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
    /* L'agenda prima delle schede: se una le sta sopra, si legge la scheda. */
    if (data.agenda && data.notes?.length) this.wall.appendChild(this._agenda(data));
    /* Una scheda-app con dati e' una striscia; le altre, polaroid. */
    for (const card of data.cards) {
      this.wall.appendChild(card.slip ? this._slip(card) : this._polaroid(card));
    }
    this._drawEnvelope();
    /* I fili sopra le schede, e niente `z-index` (la casa ne ha uno solo, quello
       di Jenny): li mette sopra l'ordine nel DOM. */
    this.wall.appendChild(this.threads);
    this.drawThreads();
    this._drawCardBar();
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
    /* «Da vicino»: i controlli delle strisce si toccano solo quando una riga
       e' alta abbastanza per un dito; piu' lontano la striscia intera apre l'app. */
    const rowPx = (this.canvas?.clientWidth || 0) * SLIP_ROW * v.k;
    this.wall.classList.toggle('is-near', rowPx >= NEAR_ROW_PX);
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
        if (e.sourceEvent) this._touched = true;
        this.view = e.transform;
        this._applyView();
      });
    this._sel.call(this._zoom)
      /* Il doppio tocco (e il doppio clic) sul fondo inquadra tutto, come
         l'ingresso. */
      .on('dblclick.zoom', (e) => {
        if (!onItem(e)) this.zoomToFit();
      });
    /* Un tocco sul fondo spegne i fili accesi. */
    this.canvas.addEventListener('click', (e) => {
      if (this._wallLong) {
        this._wallLong = false;
        return;
      }
      if (!onItem(e) && !e.target?.closest?.('.home-board-cardbar') && this.focus) {
        this.setFocus(null);
      }
    });
    this._bindWallPress(onItem);
    this.canvas.addEventListener('wheel', (e) => {
      if (e.ctrlKey) return;
      e.preventDefault();
      const k = this.view.k;
      this._zoom.translateBy(this._sel, -e.deltaX / k, -e.deltaY / k);
    }, { passive: false });
  }

  /** Tenere premuto il muro vuoto fa nascere una scheda li', sotto il dito.
   *
   *  Il fondo e' di d3-zoom, che pero' ascolta mouse e tocco, non i pointer
   *  event: un timer proprio, che qualunque movimento oltre la soglia, un
   *  secondo dito o il dito alzato annullano. Cosi' il gesto non ruba niente
   *  allo spostamento del muro, che parte appena il dito si muove. */
  _bindWallPress(onItem) {
    let press = null;
    const cancel = () => {
      if (press) clearTimeout(press.timer);
      press = null;
    };
    this.canvas.addEventListener('pointerdown', (e) => {
      this._wallLong = false;
      if ((e.button !== undefined && e.button !== 0) || onItem(e)
          || e.target?.closest?.('.home-board-cardbar')) return;
      if (press || e.isPrimary === false) {
        cancel();
        return;
      }
      const box = this.canvas.getBoundingClientRect();
      press = {
        x: e.clientX, y: e.clientY,
        timer: setTimeout(() => {
          const at = this.wallPoint(press.x, press.y, box);
          press = null;
          this._wallLong = true;
          this.openNewCard(at);
        }, LONG_PRESS_MS),
      };
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (press && Math.hypot(e.clientX - press.x, e.clientY - press.y) >= DRAG_THRESHOLD_PX) cancel();
    });
    for (const type of ['pointerup', 'pointercancel', 'pointerleave']) {
      this.canvas.addEventListener(type, cancel);
    }
  }

  /** Il punto del muro sotto (*clientX*, *clientY*), in percento dello schermo:
   *  la stessa misura delle posizioni delle schede. */
  wallPoint(clientX, clientY, box = this.canvas?.getBoundingClientRect()) {
    if (!box?.width) return this.viewCenter();
    const v = this.view;
    return {
      x: Math.round((((clientX - box.left - v.x) / v.k) / box.width) * 1000) / 10,
      y: Math.round((((clientY - box.top - v.y) / v.k) / box.height) * 1000) / 10,
    };
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
    /* L'ingombro lo dice il gateway (`box`: una polaroid o una striscia della
       sua taglia); `ITEM_BOX` resta per una risposta di prima. */
    for (const c of this.data.cards) {
      const [w, h] = Array.isArray(c.box) ? c.box : ITEM_BOX.card;
      take(c.pos, w, h);
    }
    if (this.data.agenda && this.data.notes?.length) {
      take(this.data.agenda.pos, ITEM_BOX.agenda[0], ITEM_BOX.agenda[1]);
    }
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

  /** Porta in vista la scheda *id*, se e' fuori (anche solo in parte): una
   *  scheda appena nata o rimessa deve farsi vedere intera, non mezza sotto
   *  l'intestazione. Si sposta il muro quanto basta, con un margine. */
  reveal(id) {
    const el = this.wall?.querySelector(`.home-board-card[data-id="${CSS.escape(id)}"]`);
    const box = this.canvas?.getBoundingClientRect();
    if (!el || !box?.width || !this._zoom) return;
    const r = el.getBoundingClientRect();
    const pad = 12;
    let dx = 0;
    let dy = 0;
    if (r.top < box.top + pad) dy = box.top + pad - r.top;
    else if (r.bottom > box.bottom - pad) dy = box.bottom - pad - r.bottom;
    if (r.left < box.left + pad) dx = box.left + pad - r.left;
    else if (r.right > box.right - pad) dx = box.right - pad - r.right;
    if (dx || dy) this._zoom.translateBy(this._sel, dx / this.view.k, dy / this.view.k);
  }

  /** Inquadra tutto quel che sta sul muro: le schede e l'agenda, misurate
   *  dove sono davvero (inclinati, con la didascalia), centrati nella finestra
   *  con `FIT_PAD` d'aria e sopra la busta, che sta ferma in basso. Fra 0,3 e
   *  grandezza naturale; un muro vuoto torna all'origine. Falso se la tela non
   *  ha ancora una misura, e allora non si e' mosso niente. */
  zoomToFit() {
    if (!this._zoom || !this.canvas || this.listView) return false;
    const box = this.canvas.getBoundingClientRect();
    if (!box?.width || !box?.height) return false;
    const items = this.wall.querySelectorAll('.home-board-card, .home-board-agenda');
    if (!items.length) {
      this._lookAt(window.d3.zoomIdentity);
      return true;
    }
    const v = this.view;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const el of items) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return false;  // ancora senza layout
      x0 = Math.min(x0, (r.left - box.left - v.x) / v.k);
      y0 = Math.min(y0, (r.top - box.top - v.y) / v.k);
      x1 = Math.max(x1, (r.right - box.left - v.x) / v.k);
      y1 = Math.max(y1, (r.bottom - box.top - v.y) / v.k);
    }
    const env = this.canvas.querySelector('.home-board-envelope')?.getBoundingClientRect();
    const bottom = env?.height ? Math.max(FIT_PAD, box.bottom - env.top + FIT_PAD / 2) : FIT_PAD;
    const w = box.width - 2 * FIT_PAD;
    const h = Math.max(1, box.height - FIT_PAD - bottom);
    const k = Math.max(ZOOM_EXTENT[0], Math.min(FIT_MAX_K, w / (x1 - x0), h / (y1 - y0)));
    const t = window.d3.zoomIdentity
      .translate(FIT_PAD + w / 2 - ((x0 + x1) / 2) * k, FIT_PAD + h / 2 - ((y0 + y1) / 2) * k)
      .scale(k);
    this._lookAt(t);
    return true;
  }

  /* L'inquadratura dell'ingresso. Se la tela non ha ancora una misura, il muro
     resta nascosto finche' non ce l'ha (`ResizeObserver`): meglio un istante
     vuoto che le schede all'origine e poi un salto. */
  _fit() {
    if (this.zoomToFit()) {
      this._showWall();
      return;
    }
    if (!this._zoom || !this.data || this.listView || typeof ResizeObserver === 'undefined') return;
    this._fitOnSize = true;
    if (this.wall) this.wall.style.visibility = 'hidden';
  }

  _showWall() {
    this._fitOnSize = false;
    if (this.wall) this.wall.style.visibility = '';
  }

  _clear() {
    if (!this.canvas) return;
    this._itemDrag?.cancel();
    for (const el of [...this.wall.children]) {
      if (el !== this.threads) el.remove();
    }
    this.canvas.querySelector('.home-board-envelope')?.remove();
    if (this._cardBar) this._cardBar.hidden = true;
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
    el.className = `home-board-item home-board-card home-board-polaroid${card.app ? ' is-app' : ''}`;
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    el.style.setProperty('--tilt', `${this._tilt(card.id)}deg`);
    const [a, b] = tintFor(card.id);
    const photo = document.createElement('div');
    photo.className = 'home-board-photo';
    photo.style.background = `linear-gradient(160deg, ${a}, ${b})`;
    if (card.glyph || card.app?.icon) {
      /* Un'app senza dati (niente striscia) mostra la sua icona. */
      const icon = document.createElement('i');
      icon.className = `ti ${card.glyph ? `ti-${card.glyph}` : card.app.icon}`;
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
    /* Il segno ⟳: qualcosa in questo cassetto gira da solo (un cron). Un
       segno e non un segnale: in basso, e mai rosso. */
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

  /** Una scheda che **e'** un'app con dati: la sua striscia.
   *
   *  Carta larga due polaroid, alta la sua taglia (S/M/L/XL): l'intestazione con
   *  l'icona, il nome e un dato a destra (quello dell'app, o il suo stato), e
   *  sotto i blocchi, disegnati da `home-board-blocks.js`. Si tocca come una
   *  polaroid (apre l'app), si porta e si tiene premuta allo stesso modo; i
   *  controlli dentro si usano solo da vicino (`is-near` sul muro). */
  _slip(card) {
    const slip = card.slip;
    const el = document.createElement('div');
    el.className = `home-board-item home-board-card home-board-slip is-${slip.size} is-${slip.state || 'ok'}`;
    /* Un gruppo, non un bottone: dentro ci sono controlli veri. Invio lo apre. */
    el.setAttribute('role', 'group');
    el.tabIndex = 0;
    el.style.setProperty('--tilt', `${this._tilt(card.id) / 3}deg`);
    const head = document.createElement('div');
    head.className = 'home-board-slip-head';
    const badge = document.createElement('span');
    badge.className = 'home-board-slip-icon';
    const [a, b] = tintFor(card.id);
    badge.style.background = `linear-gradient(160deg, ${a}, ${b})`;
    const icon = document.createElement('i');
    icon.className = `ti ${slip.icon || card.app?.icon || 'ti-apps'}`;
    icon.setAttribute('aria-hidden', 'true');
    badge.appendChild(icon);
    const name = document.createElement('span');
    name.className = 'home-board-slip-name';
    name.textContent = card.title;
    const meta = document.createElement('span');
    meta.className = 'home-board-slip-meta';
    meta.textContent = metaText(slip);
    head.append(badge, name, meta);
    const body = document.createElement('div');
    body.className = 'home-board-slip-body';
    const ctx = { act: (req) => this._act(card, req), media: (name) => this._media(card, name) };
    for (const b of slip.blocks || []) body.appendChild(renderBlock(b, ctx));
    el.append(head, body);
    if (card.signal && card.signal !== 'new') {
      const sig = document.createElement('span');
      sig.className = `home-board-sig-corner is-${card.signal}`;
      el.appendChild(sig);
    } else if (card.signal === 'new') {
      const sig = document.createElement('span');
      sig.className = 'home-board-sig-dot';
      el.appendChild(sig);
    }
    const sigWord = card.signal ? `, ${i18n.t(`board.signal.${card.signal}`)}` : '';
    const metaWord = meta.textContent ? `, ${meta.textContent}` : '';
    el.setAttribute('aria-label', `${card.title}${metaWord}${sigWord}`);
    this._place(el, card);
    this._bind(el, card, 'card');
    return el;
  }

  /** Un'immagine del blocco Foto: un `blob:` che si rilascia al prossimo disegno. */
  async _media(card, name) {
    const url = await api.getAppMediaUrl(card.app?.slug, name);
    (this._mediaUrls ||= []).push(url);
    return url;
  }

  /** Un controllo di una striscia: l'azione dell'app, poi la bacheca si rilegge.
   *  Se non va, l'avviso e la striscia torna com'era (si rilegge lo stesso). */
  async _act(card, { action, params } = {}) {
    const slug = card.app?.slug;
    if (!slug || !action) return;
    try {
      await api.runAppAction(slug, action, params || {});
    } catch (err) {
      console.warn('home.board: strip action failed', err);
      showToast(i18n.t('board.slip.failed'), 'error');
    }
    this.refreshApp(slug);
  }

  /** L'agenda: le prossime attese del quaderno su un foglio solo.
   *
   *  Le prime `agenda.rows` (tre), dalla piu' vicina, ognuna col nome della sua
   *  scheda; le altre dietro «+N più avanti». E' un gruppo, non un bottone: le
   *  righe sono bottoni veri, e il foglio si porta tenendolo fuori da loro o
   *  sopra, come una scheda (v. `_bind`). I fili verso le schede li disegna
   *  `drawThreads`, uno per scheda: tre righe dello stesso soggetto sono un filo. */
  _agenda(data) {
    const agenda = data.agenda;
    const notes = data.notes;
    const shown = notes.slice(0, agenda.rows || 3);
    /* L'oggetto della vista stesso: trascinato, il posto nuovo e' subito quello
       che `_fitExtent` misura. */
    const item = agenda;
    item.id = 'agenda';
    const el = document.createElement('div');
    el.className = 'home-board-item home-board-agenda';
    el.setAttribute('role', 'group');
    el.setAttribute('aria-label', i18n.t('board.agenda.title'));
    el.tabIndex = 0;
    el.style.setProperty('--tilt', '1deg');
    const head = document.createElement('div');
    head.className = 'home-board-agenda-title';
    head.textContent = i18n.t('board.agenda.title');
    el.appendChild(head);
    const today = localDay();
    for (const note of shown) {
      const card = data.cards.find((c) => c.id === note.card);
      const row = document.createElement('button');
      row.type = 'button';
      const late = note.when?.kind !== 'condition' && note.when?.precision !== 'month'
        && String(note.when?.date || '') < today;
      row.className = `home-board-agenda-row${late ? ' is-late' : ''}`;
      row.dataset.card = note.card;
      const when = document.createElement('span');
      when.className = 'home-board-agenda-when';
      when.textContent = whenLabel(note.when);
      const what = document.createElement('span');
      what.className = 'home-board-agenda-what';
      const subject = document.createElement('span');
      subject.className = 'home-board-agenda-card';
      subject.textContent = card?.title || '';
      const text = document.createElement('span');
      text.className = 'home-board-agenda-text';
      text.textContent = note.text;
      what.append(subject, text);
      row.append(when, what);
      row.setAttribute('aria-label', `${when.textContent}, ${card?.title || ''}: ${note.text}`);
      el.appendChild(row);
    }
    const rest = notes.length - shown.length;
    if (rest > 0) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'home-board-agenda-more';
      more.textContent = i18n.t('board.agenda.more', { count: rest });
      el.appendChild(more);
    }
    this._place(el, item);
    this._bind(el, item, 'agenda');
    return el;
  }

  /** Il tocco sull'agenda: una riga apre il cassetto della sua scheda, «+N»
   *  l'elenco intero; il foglio fuori dalle righe non fa niente. */
  _agendaTap(target) {
    const row = target?.closest?.('.home-board-agenda-row');
    if (row) {
      const card = this.data?.cards.find((c) => c.id === row.dataset.card);
      this.onOpenCard?.(row.dataset.card, card?.title || '');
      return;
    }
    if (target?.closest?.('.home-board-agenda-more')) this.openAgenda();
  }

  /** Tutte le attese del quaderno, per mese, nel foglio: ogni riga porta al
   *  cassetto della sua scheda. */
  openAgenda() {
    const notes = this.data?.notes || [];
    const body = document.createElement('div');
    body.className = 'home-board-agenda-all';
    for (const group of reminderGroups(notes)) {
      const h = document.createElement('h3');
      h.className = `home-board-agenda-group is-${group.kind}`;
      h.textContent = group.kind === 'month' ? group.label
        : i18n.t(`board.drawer.calendar.${group.kind}`);
      body.appendChild(h);
      for (const note of group.items) {
        const card = this.data.cards.find((c) => c.id === note.card);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = `home-board-agenda-row${group.kind === 'overdue' ? ' is-late' : ''}`;
        const when = document.createElement('span');
        when.className = 'home-board-agenda-when';
        when.textContent = whenLabel(note.when);
        const what = document.createElement('span');
        what.className = 'home-board-agenda-what';
        const subject = document.createElement('span');
        subject.className = 'home-board-agenda-card';
        subject.textContent = card?.title || '';
        const text = document.createElement('span');
        text.className = 'home-board-agenda-text';
        text.textContent = note.text;
        what.append(subject, text);
        row.append(when, what);
        row.addEventListener('click', () => {
          this.sheet.close();
          this.onOpenCard?.(note.card, card?.title || '');
        });
        body.appendChild(row);
      }
    }
    this.sheet.open(i18n.t('board.agenda.all'), body);
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
      row.addEventListener('click', () => this.openCard(card));
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
    /* Il ⋯ della scheda: la sua barra (Rinomina, Togli), come la pressione
       lunga, per chi non sa che si puo' tenere premuto (10/10/2026). Il suo
       tocco non e' un tocco della scheda: niente trascinamento, niente
       cassetto. */
    if (kind === 'card') {
      const more = document.createElement('span');
      more.className = 'home-more home-board-more';
      more.setAttribute('aria-hidden', 'true');
      more.innerHTML = '<i class="ti ti-dots" aria-hidden="true"></i>';
      more.addEventListener('pointerdown', (e) => e.stopPropagation());
      more.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        this.setFocus(this.focus === item.id ? null : item.id, { quiet: true });
      });
      el.appendChild(more);
    }
    let start = null;
    let moved = false;
    let longFired = false;
    let timer = null;
    const open = (target) => {
      /* L'agenda non si apre: si apre la riga che hai toccato. */
      if (kind === 'agenda') {
        this._agendaTap(target);
        return;
      }
      const card = this.data?.cards.find((c) => c.id === item.id);
      /* La polaroid di un'app apre l'app. */
      if (card) this.openCard(card);
      else this.onOpenCard?.(item.id, '');
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
    /* Un controllo di una striscia, da vicino: e' suo il tocco, non della
       scheda (niente trascinamento, niente pressione lunga, niente apertura). */
    const onControl = (e) => !!e.target?.closest?.('.home-board-control')
      && this.wall.classList.contains('is-near');
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      if (onControl(e)) return;
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
      /* L'agenda cattura il dito solo quando comincia a portarla: catturato
         subito, il click finirebbe al foglio e non alla riga toccata. */
      if (kind !== 'agenda') capture(el, e.pointerId);
      if (kind === 'card') {
        timer = setTimeout(() => {
          longFired = true;
          this.setFocus(this.focus === item.id ? null : item.id, { quiet: true });
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
    el.addEventListener('click', (e) => {
      if (el._suppress) {
        el._suppress = false;
        return;
      }
      if (onControl(e)) return;
      open(e.target);
    });
    el.addEventListener('keydown', (e) => {
      if (e.target !== el) return;
      /* ⇧⏎: la barra della scheda, come ⇧⏎ su un quaderno o su un'app.
         Prima a tastiera Rinomina e Togli non si raggiungevano. */
      if (kind === 'card' && e.key === 'Enter' && e.shiftKey) {
        e.preventDefault();
        this.setFocus(this.focus === item.id ? null : item.id, { quiet: true });
        return;
      }
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    });
  }

  /** Accende i fili di una scheda (o li spegne tutti, con `null`).
   *
   *  Con la barra della scheda (`quiet`, dalla pressione lunga) l'avviso «non
   *  ha fili» non serve: la barra che sale e' gia' la risposta al gesto. */
  setFocus(id, { quiet = false } = {}) {
    this.focus = id;
    const lit = this.drawThreads();
    if (id && !lit && !quiet) showToast(i18n.t('board.noThreads'), 'info');
    this._drawCardBar();
  }

  /* ── La barra della scheda: quel che si fa a una scheda intera ── */

  /** La barra in basso sulla tela, per la scheda coi fili accesi. Sta sulla
   *  tela e non sul muro, come la busta: non si sposta col muro e non si
   *  ingrandisce, e non copre i fili, che stanno sopra. */
  _drawCardBar() {
    const card = this.focus && this.data?.cards.find((c) => c.id === this.focus);
    if (!card) {
      if (this._cardBar) this._cardBar.hidden = true;
      return;
    }
    if (!this._cardBar) {
      this._cardBar = document.createElement('div');
      this._cardBar.className = 'home-board-cardbar';
      this._cardBar.setAttribute('role', 'toolbar');
    }
    const bar = this._cardBar;
    bar.innerHTML = '';
    bar.setAttribute('aria-label', i18n.t('board.card.actions', { title: card.title }));
    const act = (icon, key, fn) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'home-board-cardbar-btn';
      const i = document.createElement('i');
      i.className = `ti ti-${icon}`;
      i.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.textContent = i18n.t(key);
      b.append(i, label);
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        fn();
      });
      return b;
    };
    bar.append(act('pencil', 'board.card.rename', () => this.openRename(card.id, card.title)));
    bar.append(act('square-x', 'board.card.remove', () => this.removeCard(card.id, card.title)));
    const close = act('x', 'board.card.close', () => this.setFocus(null));
    close.classList.add('is-close');
    close.setAttribute('aria-label', i18n.t('board.card.close'));
    close.querySelector('span').remove();
    bar.appendChild(close);
    if (bar.parentNode !== this.canvas) this.canvas.appendChild(bar);
    bar.hidden = false;
  }

  /** Il tocco su una polaroid. Una scheda che **e'** un'app apre il suo
   *  schermo. Un'app senza schermo, o che non c'e' piu' (nel cestino, rotta),
   *  apre il cassetto, che dice il resto. */
  openCard(card) {
    const app = card.app;
    if (app && this.onOpenApp && app.state === 'live' && app.hasScreen) {
      this.onOpenApp(app.slug);
      return;
    }
    this.onOpenCard?.(card.id, card.title || '');
  }

  /** I dati di un'app sono cambiati (una scrittura dallo schermo o in chat, un
   *  lettore che ricalcola), o la sua finestra si e' chiusa: se l'app e' una
   *  polaroid di questa bacheca, la bacheca si rilegge e il valore si
   *  aggiorna. Un colpo solo per una raffica; mai sotto il dito. */
  refreshApp(slug) {
    if (!this.notebook || !this.data?.cards.some((c) => c.app?.slug === slug)) return;
    this._reloadSoon();
  }

  /** La bacheca di *notebook* e' cambiata fuori da qui (l'editore lanciato
   *  dalla chat, un'app posata, la passata del giardiniere): se e' quella
   *  aperta, si rilegge. */
  refreshNotebook(notebook) {
    if (!this.notebook || notebook !== this.notebook || !this.data) return;
    this._reloadSoon({ frameShape: true });
  }

  /** Un colpo solo per una raffica; mai sotto il dito. Con *frameShape*, se la
   *  forma e' cambiata fuori da qui — schede nate o spostate dall'editore — la
   *  bacheca si inquadra: sul telefono il 09/10/2026 due schede su tre erano
   *  fuori dallo schermo. Un cassetto riscritto non sposta la vista. */
  _reloadSoon({ frameShape = false } = {}) {
    clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(async () => {
      if (this._itemDrag) return;
      const before = this._shape();
      await this.load(this.notebook);
      if (frameShape && this._shape() !== before) this.zoomToFit();
    }, 250);
  }

  /** Quali schede ci sono e dove: la firma di cio' che si vede del muro. */
  _shape() {
    return JSON.stringify((this.data?.cards || []).map((c) => [c.id, c.pos?.x, c.pos?.y]));
  }

  /** Indietro: chiude la barra (e spegne i fili). Vero se c'era da chiudere. */
  closeCardBar() {
    if (!this.focus) return false;
    this.setFocus(null);
    return true;
  }

  /** Il foglio «Nuova scheda»: un nome, e la scheda nasce in *at* (un punto del
   *  muro) o al centro di quel che guardi. */
  openNewCard(at = null) {
    if (!this.notebook || !this.data) return;
    const near = at || this.viewCenter();
    this._nameSheet({
      title: i18n.t('board.card.newTitle'),
      value: '',
      submit: i18n.t('board.card.create'),
      withApp: true,
      send: async (title, app) => {
        const out = await rpc.board('card.add', this.notebook,
          app ? { title, near, app } : { title, near });
        await this.load(this.notebook);
        const id = out?.card?.id;
        if (id) {
          this.wall.querySelector(`.home-board-card[data-id="${CSS.escape(id)}"]`)
            ?.classList.add('is-appearing');
          this.reveal(id);
        }
      },
    });
  }

  /** Il foglio «Rinomina». *after* si chiama a nome cambiato (il cassetto si
   *  ricarica). */
  openRename(cardId, current, after = null) {
    if (!this.notebook) return;
    this._nameSheet({
      title: i18n.t('board.card.renameTitle'),
      value: current || '',
      submit: i18n.t('board.card.save'),
      send: async (title) => {
        await rpc.board('card.rename', this.notebook, { id: cardId, title });
        await this.load(this.notebook);
        if (this.focus === cardId) this.setFocus(cardId, { quiet: true });
        await after?.();
      },
    });
  }

  _nameSheet({ title, value, submit, send, withApp = false }) {
    const body = document.createElement('div');
    body.className = 'home-board-form';
    /* «Cosa ci metti?»: niente per ora, o un'app che hai gia' — allora la
       scheda *e'* l'app, e il suo valore va al posto della foto. Le app si
       leggono quando il foglio si apre; senza, la domanda non c'e'. */
    let picked = null;
    let paramInputs = {};
    const appBox = document.createElement('div');
    appBox.className = 'home-board-form';
    if (withApp) {
      api.getJennyApps().then((out) => {
        const apps = (out?.apps || []).filter((a) => !a.broken && a.section);
        if (!apps.length) return;
        const label = document.createElement('label');
        label.className = 'home-board-form-label';
        label.textContent = i18n.t('board.card.what');
        const select = document.createElement('select');
        select.className = 'home-board-input';
        const none = document.createElement('option');
        none.value = '';
        none.textContent = i18n.t('board.card.whatNothing');
        select.appendChild(none);
        for (const a of apps) {
          const o = document.createElement('option');
          o.value = a.slug;
          o.textContent = a.name || a.slug;
          select.appendChild(o);
        }
        label.appendChild(select);
        const params = document.createElement('div');
        params.className = 'home-board-form';
        select.addEventListener('change', () => {
          picked = apps.find((a) => a.slug === select.value) || null;
          params.innerHTML = '';
          paramInputs = {};
          for (const name of Object.keys(picked?.section?.params || {})) {
            const pl = document.createElement('label');
            pl.className = 'home-board-form-label';
            pl.textContent = i18n.t('apps.placeParam', { name });
            const pi = document.createElement('input');
            pi.type = 'text';
            pi.className = 'home-board-input';
            pl.appendChild(pi);
            params.appendChild(pl);
            paramInputs[name] = pi;
          }
          input.placeholder = picked ? (picked.name || picked.slug)
            : i18n.t('board.card.namePlaceholder');
          sync();
        });
        appBox.append(label, params);
      }).catch((err) => console.warn('home.board: apps for a card failed', err));
    }
    const label = document.createElement('label');
    label.className = 'home-board-form-label';
    label.textContent = i18n.t('board.card.name');
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'home-board-input';
    input.maxLength = 60;
    input.value = value;
    input.placeholder = i18n.t('board.card.namePlaceholder');
    input.autocapitalize = 'sentences';
    label.appendChild(input);
    const actions = document.createElement('div');
    actions.className = 'home-board-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'home-board-btn';
    cancel.textContent = i18n.t('common.cancel');
    cancel.addEventListener('click', () => this.sheet.close());
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'home-board-btn is-primary';
    ok.textContent = submit;
    const sync = () => { ok.disabled = !input.value.trim() && !picked; };
    const go = async () => {
      const name = input.value.trim() || (picked ? picked.name || picked.slug : '');
      if (!name || ok.disabled) return;
      ok.disabled = true;
      const app = picked ? {
        slug: picked.slug,
        params: Object.fromEntries(Object.entries(paramInputs)
          .map(([k, el]) => [k, el.value.trim()]).filter(([, v]) => v)),
      } : null;
      try {
        await send(name, app);
        this.sheet.close();
      } catch (err) {
        console.warn('home.board: card failed', err);
        showToast(i18n.t(err?.code === 'conflict' ? 'board.card.full' : 'board.card.failed'), 'error');
        ok.disabled = false;
      }
    };
    ok.addEventListener('click', go);
    input.addEventListener('input', sync);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        go();
      }
    });
    sync();
    actions.append(cancel, ok);
    body.append(label, appBox, actions);
    this.sheet.open(title, body);
    input.focus();
  }

  /** «Togli dalla bacheca»: subito, con un Annulla di qualche secondo al posto
   *  di una domanda. L'Annulla rimette la scheda dov'era, coi suoi fili. */
  async removeCard(cardId, title) {
    const notebook = this.notebook;
    if (!notebook) return;
    try {
      await rpc.board('card.remove', notebook, { id: cardId });
    } catch (err) {
      console.warn('home.board: remove failed', err);
      showToast(i18n.t('board.card.failed'), 'error');
      return;
    }
    if (this.focus === cardId) this.focus = null;
    await this.load(notebook);
    showUndo(i18n.t('board.card.removed', { title }), async () => {
      try {
        await rpc.board('card.restore', notebook, { id: cardId });
      } catch (err) {
        console.warn('home.board: restore failed', err);
        showToast(i18n.t(err?.code === 'conflict' ? 'board.card.full' : 'board.card.restoreFailed'),
          'error');
        return;
      }
      if (this.notebook === notebook) {
        await this.load(notebook);
        this.wall.querySelector(`.home-board-card[data-id="${CSS.escape(cardId)}"]`)
          ?.classList.add('is-appearing');
        this.reveal(cardId);
      }
    }, { label: i18n.t('board.card.undo') });
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
    const elOf = (id) => this.wall.querySelector(`.home-board-card[data-id="${CSS.escape(id)}"]`);
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
    /* I fili dell'agenda: dal suo chiodo a quello di ogni scheda che compare
       nelle sue righe, uno per scheda anche se le righe sono tre. */
    const agenda = this.wall.querySelector('.home-board-agenda');
    if (agenda) {
      const from = pinCenter(agenda, box, zoom);
      const ids = [...new Set([...agenda.querySelectorAll('.home-board-agenda-row')]
        .map((r) => r.dataset.card))];
      for (const id of ids) {
        const card = elOf(id);
        if (!card) continue;
        const on = this.focus === id;
        const to = pinCenter(card, box, zoom);
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', from.x);
        line.setAttribute('y1', from.y);
        line.setAttribute('x2', to.x);
        line.setAttribute('y2', to.y);
        line.setAttribute('class', `is-agenda ${!this.focus ? 'is-rest' : on ? 'is-on' : 'is-off'}`);
        svg.appendChild(line);
        if (!on) continue;
        lit = true;
        agenda.classList.add('is-linked');
      }
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
      this.canvas.querySelector('.home-board-card:last-of-type')?.classList.add('is-appearing');
    }
  }

  /* ── La ricerca: l'unica porta verso tutto ── */

  /** *query*: si torna a una ricerca gia' fatta (da una pagina aperta fra i
   *  suoi risultati), con la frase ancora scritta. */
  async openSearch(query = '') {
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
    input.value = query;
    this.searchQuery = query;
    this.sheet.open(i18n.t('board.search.title'), body);
    input.focus();
    try {
      this._graph ||= await api.getGraph(this.notebook);
    } catch (err) {
      console.warn('home.board: search index failed', err);
    }
    const index = this._graph ? WikiSearchIndex.from(this._graph.search) : null;
    const draw = () => this._drawResults(results, input.value, index);
    input.addEventListener('input', () => {
      this.searchQuery = input.value;
      draw();
    });
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
        if (r.kind === 'card') {
          const card = this.data?.cards.find((c) => c.id === r.card);
          if (card) this.openCard(card);
          else this.onOpenCard?.(r.card, r.title);
        }
        /* Aperta da qui, Indietro torna alla ricerca con la frase scritta, per
           provare il risultato dopo (v. `HomeApp._searchReturn`). */
        else if (r.kind === 'page') this.onOpenPage?.(r.path, r.title, r.card, { fromSearch: query });
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
