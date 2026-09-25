/** La casa — la fila dei nomi in alto.
 *
 *  Prende il posto di quattro cose che c'erano (v. `.agent/pagine-in-alto-plan.md`):
 *  il titolo «Jenny ⌄» con la sua tendina, l'ingranaggio verso «Tu e Jenny», il
 *  bottone del cassetto e la striscia dei pallini. I pallini dicevano quante
 *  pagine c'erano, non che cosa: qui ci sono i nomi, e **quello dove sei e'
 *  acceso** — stesso carattere e stessa taglia degli altri, cambia il colore e
 *  c'e' la riga d'accento (v. `home-style.css`). Toccare un nome ci va; il
 *  gesto di lato resta.
 *
 *  **Tenere premuto un nome apre la modalita' ordina**: le pagine diventano
 *  pastiglie che si trascinano, quelle aggiunte hanno la × per toglierle, e
 *  «Fatto» salva tutto in una scrittura. Le quattro fisse si spostano ma non si
 *  tolgono. Indietro esce senza salvare.
 *
 *  Il modulo non sa dove si salvi niente: legge le voci dalla pista
 *  (`HomePages.entries`) e le rimanda indietro intere.
 */

import { i18n } from './shared/i18n.js';
import { setupLongPress } from './shared/longpress.js';
import { dotColor } from './home-who.js';

/** Quanto spazio lasciare accanto al nome acceso quando la fila lo riporta in
 *  vista: a filo del bordo sembrerebbe tagliato anche quando non lo e'. */
const MARGIN_IN_VIEW = 24;

export class HomeStrip {
  /** @param el        il contenitore (`#home-strip`)
   *  @param pagine    la pista: `entries`, `index`, `goTo`, `save`, `pages`
   *  @param chatName  `() => ({name, color})`: la pagina chat si chiama come
   *                   la conversazione che mostra — «Jenny», o il quaderno
   *  @param onChange  chiamata quando la modalita' ordina si apre o si chiude */
  constructor(el, { homePages, chatName, onChange } = {}) {
    this.el = el;
    this.homePages = homePages;
    this._chatName = chatName || (() => ({ name: 'Jenny', color: null }));
    this._onChange = onChange || null;
    /** In modalita' ordina: la bozza dell'ordine, finche' non si preme Fatto. */
    this._draft = null;
    this._drag = null;
  }

  get sorting() {
    return this._draft !== null;
  }

  /** Il nome di una voce, come si legge nella fila. */
  name(entry) {
    if (!entry) return '';
    if (entry.kind === 'chat') return this._chatName().name;
    if (entry.fixed) return i18n.t(`home.strip.${entry.id}`);
    return this.homePages.nameOf(entry);
  }

  /** Il pallino di una voce, se ne ha uno: la chat dentro un quaderno, e le
   *  pagine quaderno. E' lo stesso colore della riga nei Quaderni, ed e' la
   *  sola cosa che lega il nome alla stanza in cui sei. */
  color(entry) {
    if (entry?.kind === 'chat') return this._chatName().color || null;
    if (entry?.kind === 'conversation') return dotColor(this.name(entry));
    return null;
  }

  draw() {
    if (!this.el) return;
    if (this.sorting) this._drawSort();
    else this._drawNames();
  }

  /* ── La fila ─────────────────────────────────────────────────────────── */

  _drawNames() {
    this.el.classList.remove('is-sort');
    const nav = document.createElement('nav');
    nav.className = 'home-strip-names';
    nav.setAttribute('role', 'tablist');
    nav.setAttribute('aria-label', i18n.t('home.strip.label'));
    let active = null;
    this.homePages.entries.forEach((entry, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'home-strip-entry';
      b.dataset.id = entry.id;
      b.setAttribute('role', 'tab');
      const on = i === this.homePages.index;
      b.setAttribute('aria-selected', String(on));
      if (on) {
        b.classList.add('is-on');
        active = b;
      }
      const color = this.color(entry);
      if (color) {
        const dot = document.createElement('span');
        dot.className = 'home-strip-dot';
        dot.style.background = color;
        dot.setAttribute('aria-hidden', 'true');
        b.appendChild(dot);
      }
      const name = document.createElement('span');
      name.className = 'home-strip-name';
      name.textContent = this.name(entry);
      b.appendChild(name);
      /* Il tocco che segue una pressione lunga non e' un tocco: senza questa
         riga tenere premuto un nome aprirebbe la modalita' ordina **e** ci
         porterebbe sopra. Stessa guardia di ogni pressione lunga della casa. */
      b.addEventListener('click', () => {
        if (b.dataset.longpress) { delete b.dataset.longpress; return; }
        this.homePages.goTo(i);
      });
      setupLongPress(b, () => this.openSort());
      nav.appendChild(b);
    });
    this.el.replaceChildren(nav);
    this._inView(nav, active);
  }

  /** Il nome acceso resta sempre in vista, e la fila sfuma dal lato in cui
   *  qualcosa non ci sta. `scrollLeft` a mano e non `scrollIntoView`: quello
   *  scorrerebbe anche gli antenati, e la vetrina della pista e' uno di loro. */
  _inView(nav, active) {
    if (!active || !nav.scrollWidth) return;
    const start = active.offsetLeft;
    const fine = start + active.offsetWidth;
    if (fine > nav.scrollLeft + nav.clientWidth) {
      nav.scrollLeft = fine - nav.clientWidth + MARGIN_IN_VIEW;
    } else if (start < nav.scrollLeft) {
      nav.scrollLeft = Math.max(0, start - MARGIN_IN_VIEW);
    }
    const fade = () => {
      nav.classList.toggle('fade-after', nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 1);
      nav.classList.toggle('fade-before', nav.scrollLeft > 1);
    };
    fade();
    nav.addEventListener('scroll', fade, { passive: true });
  }

  /* ── La modalita' ordina ─────────────────────────────────────────────── */

  openSort() {
    if (this.sorting) return;
    this._draft = this.homePages.entries.map((v) => v.id);
    this.draw();
    this._onChange?.(true);
  }

  /** Esce. Con `save` scrive l'ordine della bozza, e toglie le pagine che non
   *  ci sono piu'; senza, lascia tutto com'era — e' Indietro.
   *
   *  **Si esce solo a salvataggio riuscito.** La bozza si azzerava prima di
   *  scrivere, e una scrittura rifiutata perdeva l'ordine in silenzio: ora si
   *  resta in modalita' ordina con la bozza intatta — l'avviso lo da' la pista
   *  — e si torna `false`, cosi' «Fatto» si puo' ripremere. Un secondo «Fatto»
   *  mentre il primo sta scrivendo non fa niente. */
  async closeSort({ save = false } = {}) {
    if (!this.sorting) return true;
    const draft = this._draft;
    if (save) {
      if (this._saving) return false;
      const remain = this.homePages.pages.filter((s) => draft.includes(s.id));
      const before = this.homePages.entries.map((v) => v.id);
      const equal = remain.length === this.homePages.pages.length
        && before.join() === draft.join();
      if (!equal) {
        this._saving = true;
        let saved;
        try {
          saved = await this.homePages.save(remain, [...draft]);
        } finally {
          this._saving = false;
        }
        if (!saved) return false;
        /* Nel frattempo Indietro e' gia' uscito: non c'e' piu' niente da chiudere. */
        if (this._draft !== draft) return true;
      }
    }
    this._draft = null;
    this._endDrag();
    this.draw();
    this._onChange?.(false);
    return true;
  }

  /** Sposta la voce `id` alla posizione `where` della bozza. */
  move(id, where) {
    if (!this.sorting) return;
    const fromIndex = this._draft.indexOf(id);
    if (fromIndex < 0) return;
    const a = Math.max(0, Math.min(where, this._draft.length - 1));
    if (fromIndex === a) return;
    this._draft.splice(fromIndex, 1);
    this._draft.splice(a, 0, id);
  }

  /** Toglie dalla bozza una pagina aggiunta. Le fisse non si tolgono. */
  remove(id) {
    if (!this.sorting || this.homePages.fixed.includes(id)) return;
    this._draft = this._draft.filter((x) => x !== id);
    this.draw();
  }

  _drawSort() {
    this.el.classList.add('is-sort');
    const head = document.createElement('div');
    head.className = 'home-sort-head';
    const help = document.createElement('span');
    help.className = 'home-sort-help';
    help.textContent = i18n.t('home.strip.sortHelp');
    const done = document.createElement('button');
    done.type = 'button';
    done.className = 'home-sort-done';
    done.textContent = i18n.t('home.strip.done');
    done.addEventListener('click', () => this.closeSort({ save: true }));
    head.append(help, done);

    const pills = document.createElement('div');
    pills.className = 'home-sort-pills';
    const entries = new Map(this.homePages.entries.map((v) => [v.id, v]));
    for (const id of this._draft) {
      const entry = entries.get(id);
      if (entry) pills.appendChild(this._pill(entry, pills));
    }
    this.el.replaceChildren(head, pills);
  }

  _pill(entry, container) {
    const p = document.createElement('div');
    p.className = 'home-sort-pill';
    p.dataset.id = entry.id;
    /* Una pastiglia si prende con Tab e si sposta con le frecce: chi non
       trascina — o non puo' — deve poter fare lo stesso. */
    p.tabIndex = 0;
    p.setAttribute('role', 'button');
    p.setAttribute('aria-label', i18n.t('home.strip.move', { name: this.name(entry) }));
    const grip = document.createElement('i');
    grip.className = 'ti ti-grip-vertical';
    grip.setAttribute('aria-hidden', 'true');
    const name = document.createElement('span');
    name.textContent = this.name(entry);
    p.append(grip, name);
    if (!entry.fixed) {
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'home-sort-remove';
      x.setAttribute('aria-label', i18n.t('home.strip.remove', { name: this.name(entry) }));
      x.innerHTML = '<i class="ti ti-x" aria-hidden="true"></i>';
      /* Il dito che preme la × non deve cominciare un trascinamento. */
      x.addEventListener('pointerdown', (e) => e.stopPropagation());
      x.addEventListener('click', () => this.remove(entry.id));
      p.appendChild(x);
    }
    p.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const i = this._draft.indexOf(entry.id);
      this.move(entry.id, i + (e.key === 'ArrowRight' ? 1 : -1));
      this.draw();
      this.el.querySelector(`.home-sort-pill[data-id="${CSS.escape(entry.id)}"]`)?.focus();
    });
    p.addEventListener('pointerdown', (e) => this._take(e, p, container));
    return p;
  }

  /* Il trascinamento. La pastiglia presa **si sposta nel DOM** al posto in
     cui cadrebbe, e un `transform` la tiene sotto il dito: cosi' il buco che
     lascia e' il suo posto vero, e al rilascio non c'e' niente da ricalcolare.

     Due cose misurate sul telefono il 23/09/2026, alla prima prova:
     - **il dito si ascolta sul documento**, non sulla pastiglia. Spostata nel
       DOM, Chromium le toglie la cattura del puntatore: il `pointerup` andava
       a chi stava sotto il dito, e la pastiglia restava sollevata a mezz'aria;
     - **la base si legge senza `transform`**, dal rettangolo vero. Con
       `offsetTop` era misurata dal guscio e non dal contenitore — contata due
       volte — e la pastiglia finiva un'intestazione piu' in alto del dito. */

  _take(e, p, container) {
    if (e.button !== undefined && e.button !== 0) return;
    /* Un secondo dito mentre il primo trascina non ne comincia un altro: gli
       ascoltatori del primo restavano attaccati al documento per sempre,
       sovrascritti da quelli del secondo. */
    if (this._drag) return;
    const r = p.getBoundingClientRect();
    this._drag = {
      id: p.dataset.id,
      el: p,
      container,
      gripX: e.clientX - r.left,
      gripY: e.clientY - r.top,
      pointerId: e.pointerId,
    };
    p.classList.add('is-lifted');
    this._onPointerMove = (ev) => this._move(ev);
    this._onPointerUp = (ev) => this._leave(ev);
    document.addEventListener('pointermove', this._onPointerMove);
    document.addEventListener('pointerup', this._onPointerUp);
    document.addEventListener('pointercancel', this._onPointerUp);
  }

  _move(e) {
    const t = this._drag;
    if (!t || e.pointerId !== t.pointerId) return;
    const other = Array.from(t.container.children).filter((c) => c !== t.el);
    let nearby = null;
    let better = Infinity;
    for (const c of other) {
      const r = c.getBoundingClientRect();
      const d = Math.hypot(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2));
      if (d < better) { better = d; nearby = c; }
    }
    if (nearby) {
      const fromIndex = this._draft.indexOf(t.id);
      const a = this._draft.indexOf(nearby.dataset.id);
      const r = nearby.getBoundingClientRect();
      const beyondMeta = e.clientX > r.left + r.width / 2;
      /* Dove cadrebbe: prima della vicina, o dopo se il dito ne ha passato la
         meta'. Contato **senza** la pastiglia presa, che sta ancora nella bozza. */
      let where = beyondMeta ? a + 1 : a;
      if (fromIndex < where) where -= 1;
      if (where !== fromIndex) {
        this.move(t.id, where);
        const next = this._draft[where + 1];
        const node = next ? t.container.querySelector(`[data-id="${CSS.escape(next)}"]`) : null;
        t.container.insertBefore(t.el, node);
      }
    }
    t.el.style.transform = '';
    const base = t.el.getBoundingClientRect();
    const x = e.clientX - t.gripX - base.left;
    const y = e.clientY - t.gripY - base.top;
    t.el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
  }

  _leave(e) {
    const t = this._drag;
    if (!t || e.pointerId !== t.pointerId) return;
    this._endDrag();
  }

  /* La fine di un trascinamento, da qualunque strada ci si arrivi — il dito
     che si alza, o la modalita' ordina che si chiude col dito ancora giu'
     (Indietro, «Fatto»): gli ascoltatori sul documento se ne vanno sempre.
     Prima la chiusura azzerava solo `_drag`, e li lasciava li'.
     Idempotente: chiamarla senza un trascinamento in corso non fa niente. */
  _endDrag() {
    const t = this._drag;
    this._drag = null;
    if (this._onPointerMove) {
      document.removeEventListener?.('pointermove', this._onPointerMove);
      document.removeEventListener?.('pointerup', this._onPointerUp);
      document.removeEventListener?.('pointercancel', this._onPointerUp);
    }
    this._onPointerMove = null;
    this._onPointerUp = null;
    if (!t) return;
    t.el.style.transform = '';
    t.el.classList.remove('is-lifted');
  }
}
