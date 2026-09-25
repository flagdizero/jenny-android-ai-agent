/** La casa — la fila dei nomi in alto.
 *
 *  Prende il posto di quattro cose che c'erano (v. `.agent/pagine-in-alto-plan.md`):
 *  il titolo «Jenny ⌄» con la sua tendina, l'ingranaggio verso «Tu e Jenny», il
 *  bottone del cassetto e la striscia dei pallini. I pallini dicevano quante
 *  pagine c'erano, non che cosa: qui ci sono i nomi, e **quello dove sei e'
 *  grande**. Toccare un nome ci va; il gesto di lato resta.
 *
 *  **Tenere premuto un nome apre la modalita' ordina**: le pagine diventano
 *  pastiglie che si trascinano, quelle aggiunte hanno la × per toglierle, e
 *  «Fatto» salva tutto in una scrittura. Le quattro fisse si spostano ma non si
 *  tolgono. Indietro esce senza salvare.
 *
 *  Il modulo non sa dove si salvi niente: legge le voci dalla pista
 *  (`HomePages.voci`) e le rimanda indietro intere.
 */

import { i18n } from './shared/i18n.js';
import { setupLongPress } from './shared/longpress.js';
import { dotColor } from './home-who.js';

/** Quanto spazio lasciare accanto al nome acceso quando la fila lo riporta in
 *  vista: a filo del bordo sembrerebbe tagliato anche quando non lo e'. */
const MARGINE_IN_VISTA = 24;

export class HomeStrip {
  /** @param el        il contenitore (`#home-strip`)
   *  @param pagine    la pista: `voci`, `indice`, `vaiA`, `salva`, `pages`
   *  @param nomeChat  `() => ({name, colore})`: la pagina chat si chiama come
   *                   la conversazione che mostra — «Jenny», o il quaderno
   *  @param onCambia  chiamata quando la modalita' ordina si apre o si chiude */
  constructor(el, { homePages, nomeChat, onCambia } = {}) {
    this.el = el;
    this.homePages = homePages;
    this._nomeChat = nomeChat || (() => ({ name: 'Jenny', colore: null }));
    this._onCambia = onCambia || null;
    /** In modalita' ordina: la bozza dell'ordine, finche' non si preme Fatto. */
    this._bozza = null;
    this._trascina = null;
  }

  get ordinando() {
    return this._bozza !== null;
  }

  /** Il nome di una voce, come si legge nella fila. */
  name(voce) {
    if (!voce) return '';
    if (voce.kind === 'chat') return this._nomeChat().name;
    if (voce.fissa) return i18n.t(`home.strip.${voce.id}`);
    return this.homePages.nomeDi(voce);
  }

  /** Il pallino di una voce, se ne ha uno: la chat dentro un quaderno, e le
   *  pagine quaderno. E' lo stesso colore della riga nei Quaderni, ed e' la
   *  sola cosa che lega il nome alla stanza in cui sei. */
  colore(voce) {
    if (voce?.kind === 'chat') return this._nomeChat().colore || null;
    if (voce?.kind === 'conversation') return dotColor(this.name(voce));
    return null;
  }

  disegna() {
    if (!this.el) return;
    if (this.ordinando) this._disegnaOrdina();
    else this._disegnaNomi();
  }

  /* ── La fila ─────────────────────────────────────────────────────────── */

  _disegnaNomi() {
    this.el.classList.remove('is-sort');
    const nav = document.createElement('nav');
    nav.className = 'home-strip-names';
    nav.setAttribute('role', 'tablist');
    nav.setAttribute('aria-label', i18n.t('home.strip.label'));
    let acceso = null;
    this.homePages.voci.forEach((voce, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'home-strip-entry';
      b.dataset.id = voce.id;
      b.setAttribute('role', 'tab');
      const on = i === this.homePages.indice;
      b.setAttribute('aria-selected', String(on));
      if (on) {
        b.classList.add('is-on');
        acceso = b;
      }
      const colore = this.colore(voce);
      if (colore) {
        const dot = document.createElement('span');
        dot.className = 'home-strip-dot';
        dot.style.background = colore;
        dot.setAttribute('aria-hidden', 'true');
        b.appendChild(dot);
      }
      const name = document.createElement('span');
      name.className = 'home-strip-name';
      name.textContent = this.name(voce);
      b.appendChild(name);
      /* Il tocco che segue una pressione lunga non e' un tocco: senza questa
         riga tenere premuto un nome aprirebbe la modalita' ordina **e** ci
         porterebbe sopra. Stessa guardia di ogni pressione lunga della casa. */
      b.addEventListener('click', () => {
        if (b.dataset.longpress) { delete b.dataset.longpress; return; }
        this.homePages.vaiA(i);
      });
      setupLongPress(b, () => this.apriOrdina());
      nav.appendChild(b);
    });
    this.el.replaceChildren(nav);
    this._inVista(nav, acceso);
  }

  /** Il nome acceso resta sempre in vista, e la fila sfuma dal lato in cui
   *  qualcosa non ci sta. `scrollLeft` a mano e non `scrollIntoView`: quello
   *  scorrerebbe anche gli antenati, e la vetrina della pista e' uno di loro. */
  _inVista(nav, acceso) {
    if (!acceso || !nav.scrollWidth) return;
    const inizio = acceso.offsetLeft;
    const fine = inizio + acceso.offsetWidth;
    if (fine > nav.scrollLeft + nav.clientWidth) {
      nav.scrollLeft = fine - nav.clientWidth + MARGINE_IN_VISTA;
    } else if (inizio < nav.scrollLeft) {
      nav.scrollLeft = Math.max(0, inizio - MARGINE_IN_VISTA);
    }
    const sfuma = () => {
      nav.classList.toggle('fade-after', nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 1);
      nav.classList.toggle('fade-before', nav.scrollLeft > 1);
    };
    sfuma();
    nav.addEventListener('scroll', sfuma, { passive: true });
  }

  /* ── La modalita' ordina ─────────────────────────────────────────────── */

  apriOrdina() {
    if (this.ordinando) return;
    this._bozza = this.homePages.voci.map((v) => v.id);
    this.disegna();
    this._onCambia?.(true);
  }

  /** Esce. Con `salva` scrive l'ordine della bozza, e toglie le pagine che non
   *  ci sono piu'; senza, lascia tutto com'era — e' Indietro.
   *
   *  **Si esce solo a salvataggio riuscito.** La bozza si azzerava prima di
   *  scrivere, e una scrittura rifiutata perdeva l'ordine in silenzio: ora si
   *  resta in modalita' ordina con la bozza intatta — l'avviso lo da' la pista
   *  — e si torna `false`, cosi' «Fatto» si puo' ripremere. Un secondo «Fatto»
   *  mentre il primo sta scrivendo non fa niente. */
  async chiudiOrdina({ salva = false } = {}) {
    if (!this.ordinando) return true;
    const bozza = this._bozza;
    if (salva) {
      if (this._salvando) return false;
      const restano = this.homePages.pages.filter((s) => bozza.includes(s.id));
      const prima = this.homePages.voci.map((v) => v.id);
      const uguale = restano.length === this.homePages.pages.length
        && prima.join() === bozza.join();
      if (!uguale) {
        this._salvando = true;
        let salvate;
        try {
          salvate = await this.homePages.salva(restano, [...bozza]);
        } finally {
          this._salvando = false;
        }
        if (!salvate) return false;
        /* Nel frattempo Indietro e' gia' uscito: non c'e' piu' niente da chiudere. */
        if (this._bozza !== bozza) return true;
      }
    }
    this._bozza = null;
    this._fineTrascina();
    this.disegna();
    this._onCambia?.(false);
    return true;
  }

  /** Sposta la voce `id` alla posizione `dove` della bozza. */
  move(id, dove) {
    if (!this.ordinando) return;
    const da = this._bozza.indexOf(id);
    if (da < 0) return;
    const a = Math.max(0, Math.min(dove, this._bozza.length - 1));
    if (da === a) return;
    this._bozza.splice(da, 1);
    this._bozza.splice(a, 0, id);
  }

  /** Toglie dalla bozza una pagina aggiunta. Le fisse non si tolgono. */
  remove(id) {
    if (!this.ordinando || this.homePages.fixed.includes(id)) return;
    this._bozza = this._bozza.filter((x) => x !== id);
    this.disegna();
  }

  _disegnaOrdina() {
    this.el.classList.add('is-sort');
    const testa = document.createElement('div');
    testa.className = 'home-sort-head';
    const aiuto = document.createElement('span');
    aiuto.className = 'home-sort-help';
    aiuto.textContent = i18n.t('home.strip.sortHelp');
    const done = document.createElement('button');
    done.type = 'button';
    done.className = 'home-sort-done';
    done.textContent = i18n.t('home.strip.done');
    done.addEventListener('click', () => this.chiudiOrdina({ salva: true }));
    testa.append(aiuto, done);

    const pastiglie = document.createElement('div');
    pastiglie.className = 'home-sort-pills';
    const voci = new Map(this.homePages.voci.map((v) => [v.id, v]));
    for (const id of this._bozza) {
      const voce = voci.get(id);
      if (voce) pastiglie.appendChild(this._pastiglia(voce, pastiglie));
    }
    this.el.replaceChildren(testa, pastiglie);
  }

  _pastiglia(voce, contenitore) {
    const p = document.createElement('div');
    p.className = 'home-sort-pill';
    p.dataset.id = voce.id;
    /* Una pastiglia si prende con Tab e si sposta con le frecce: chi non
       trascina — o non puo' — deve poter fare lo stesso. */
    p.tabIndex = 0;
    p.setAttribute('role', 'button');
    p.setAttribute('aria-label', i18n.t('home.strip.move', { name: this.name(voce) }));
    const presa = document.createElement('i');
    presa.className = 'ti ti-grip-vertical';
    presa.setAttribute('aria-hidden', 'true');
    const name = document.createElement('span');
    name.textContent = this.name(voce);
    p.append(presa, name);
    if (!voce.fissa) {
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'home-sort-remove';
      x.setAttribute('aria-label', i18n.t('home.strip.remove', { name: this.name(voce) }));
      x.innerHTML = '<i class="ti ti-x" aria-hidden="true"></i>';
      /* Il dito che preme la × non deve cominciare un trascinamento. */
      x.addEventListener('pointerdown', (e) => e.stopPropagation());
      x.addEventListener('click', () => this.remove(voce.id));
      p.appendChild(x);
    }
    p.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const i = this._bozza.indexOf(voce.id);
      this.move(voce.id, i + (e.key === 'ArrowRight' ? 1 : -1));
      this.disegna();
      this.el.querySelector(`.home-sort-pill[data-id="${CSS.escape(voce.id)}"]`)?.focus();
    });
    p.addEventListener('pointerdown', (e) => this._prendi(e, p, contenitore));
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

  _prendi(e, p, contenitore) {
    if (e.button !== undefined && e.button !== 0) return;
    /* Un secondo dito mentre il primo trascina non ne comincia un altro: gli
       ascoltatori del primo restavano attaccati al documento per sempre,
       sovrascritti da quelli del secondo. */
    if (this._trascina) return;
    const r = p.getBoundingClientRect();
    this._trascina = {
      id: p.dataset.id,
      el: p,
      contenitore,
      presaX: e.clientX - r.left,
      presaY: e.clientY - r.top,
      pointerId: e.pointerId,
    };
    p.classList.add('is-lifted');
    this._suMuovi = (ev) => this._muovi(ev);
    this._suLascia = (ev) => this._lascia(ev);
    document.addEventListener('pointermove', this._suMuovi);
    document.addEventListener('pointerup', this._suLascia);
    document.addEventListener('pointercancel', this._suLascia);
  }

  _muovi(e) {
    const t = this._trascina;
    if (!t || e.pointerId !== t.pointerId) return;
    const altre = Array.from(t.contenitore.children).filter((c) => c !== t.el);
    let vicina = null;
    let meglio = Infinity;
    for (const c of altre) {
      const r = c.getBoundingClientRect();
      const d = Math.hypot(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2));
      if (d < meglio) { meglio = d; vicina = c; }
    }
    if (vicina) {
      const da = this._bozza.indexOf(t.id);
      const a = this._bozza.indexOf(vicina.dataset.id);
      const r = vicina.getBoundingClientRect();
      const oltreMeta = e.clientX > r.left + r.width / 2;
      /* Dove cadrebbe: prima della vicina, o dopo se il dito ne ha passato la
         meta'. Contato **senza** la pastiglia presa, che sta ancora nella bozza. */
      let dove = oltreMeta ? a + 1 : a;
      if (da < dove) dove -= 1;
      if (dove !== da) {
        this.move(t.id, dove);
        const prossima = this._bozza[dove + 1];
        const nodo = prossima ? t.contenitore.querySelector(`[data-id="${CSS.escape(prossima)}"]`) : null;
        t.contenitore.insertBefore(t.el, nodo);
      }
    }
    t.el.style.transform = '';
    const base = t.el.getBoundingClientRect();
    const x = e.clientX - t.presaX - base.left;
    const y = e.clientY - t.presaY - base.top;
    t.el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
  }

  _lascia(e) {
    const t = this._trascina;
    if (!t || e.pointerId !== t.pointerId) return;
    this._fineTrascina();
  }

  /* La fine di un trascinamento, da qualunque strada ci si arrivi — il dito
     che si alza, o la modalita' ordina che si chiude col dito ancora giu'
     (Indietro, «Fatto»): gli ascoltatori sul documento se ne vanno sempre.
     Prima la chiusura azzerava solo `_trascina`, e li lasciava li'.
     Idempotente: chiamarla senza un trascinamento in corso non fa niente. */
  _fineTrascina() {
    const t = this._trascina;
    this._trascina = null;
    if (this._suMuovi) {
      document.removeEventListener?.('pointermove', this._suMuovi);
      document.removeEventListener?.('pointerup', this._suLascia);
      document.removeEventListener?.('pointercancel', this._suLascia);
    }
    this._suMuovi = null;
    this._suLascia = null;
    if (!t) return;
    t.el.style.transform = '';
    t.el.classList.remove('is-lifted');
  }
}
