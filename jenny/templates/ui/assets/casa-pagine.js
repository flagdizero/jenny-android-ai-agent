/** La casa — la pista delle pagine.
 *
 *  La casa e' un launcher: di lato alla chat ci sono le pagine che l'utente ha
 *  aggiunto (tavola `Pagine`). **La chat e' la pagina 0**: c'e' sempre, non si
 *  sposta e non si toglie — per questo non sta nell'elenco salvato, e per
 *  questo qui dentro l'indice 0 non e' una `schermata`.
 *
 *  **Che cosa muove.** Una pista in fila orizzontale, un pannello per pagina,
 *  e un `translateX`. Non e' la stessa risposta visiva del carosello
 *  dell'officina — li' si trascina la vista corrente con una sbirciata
 *  smorzata e la vicina non si disegna mai — e non per capriccio: la tavola
 *  mostra la pagina di fianco che **entra davvero**, affiancata, non che
 *  sbircia. Quel che i due gusci condividono e' il *riconoscimento* del gesto
 *  (`shared/gesto-orizzontale.js`), non la risposta.
 *
 *  **Niente `z-index`.** Quello di Jenny e' l'unico del foglio della casa e
 *  deve restarlo: lei sta sopra la chat, sopra un'app, sopra tutto.
 */

import { api } from './shared/api-client.js';
import { osservaGestoOrizzontale } from './shared/gesto-orizzontale.js';

/** Quanto la pista si lascia tirare oltre il capo, in frazione di schermo.
 *  Serve a dire «di la' non c'e' niente» col dito invece che con un blocco
 *  secco, che sembra un difetto. */
const OLTRE_IL_CAPO = 0.06;

export class CasaPagine {
  /** @param app  il guscio, per le guardie che solo lui conosce. */
  constructor(app) {
    this.app = app;
    this.pista = document.getElementById('casa-pista');
    /** Le pagine aggiunte, come le ha salvate il server. La chat non c'e'. */
    this.schermate = [];
    /** 0 = la chat. 1..n = `this.schermate[i - 1]`. */
    this.indice = 0;
    this._tetto = 8;
    this._staccaGesto = null;

    if (this.pista) this._armaGesto();
  }

  /** Quante caselle ha la pista, chat compresa. */
  get quante() {
    return this.schermate.length + 1;
  }

  /** Se il tetto e' pieno non si puo' aggiungere: lo chiede il foglio. */
  get pienoZeppo() {
    return this.schermate.length >= this._tetto;
  }

  /** Legge l'elenco dal server e disegna. Non alza: una casa che non apre
   *  perche' non ha saputo leggere le sue pagine e' peggio di una casa con la
   *  sola chat. */
  async carica() {
    try {
      const dati = await api.getSchermate();
      this.schermate = Array.isArray(dati?.schermate) ? dati.schermate : [];
      if (Number.isFinite(dati?.max)) this._tetto = dati.max;
    } catch {
      this.schermate = [];
    }
    this._disegna();
    this.vaiA(0, { animato: false });
  }

  /** Salva l'elenco intero — aggiungere, togliere e spostare sono la stessa
   *  scrittura, e mandarlo tutto toglie di mezzo il caso in cui due
   *  scritture si incrociano lasciando un ordine che nessuno ha chiesto. */
  async salva(schermate) {
    const salvate = await api.setSchermate(schermate);
    this.schermate = salvate;
    this._disegna();
    if (this.indice > this.schermate.length) this.vaiA(this.schermate.length);
    else this.vaiA(this.indice, { animato: false });
    return salvate;
  }

  /** Va alla casella `i`, se esiste. */
  vaiA(i, { animato = true } = {}) {
    if (!this.pista) return;
    const bersaglio = Math.max(0, Math.min(i, this.quante - 1));
    this.indice = bersaglio;
    this.pista.style.transition = animato
      ? 'transform .22s cubic-bezier(.22,.61,.36,1)'
      : 'none';
    this.pista.style.transform = `translateX(${-bersaglio * 100}%)`;
    this.app?.onPaginaCambiata?.(bersaglio, this.schermate[bersaglio - 1] || null);
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  /** Un pannello per schermata, accanto a quello della chat.
   *
   *  Il pannello della chat non si tocca mai: e' nell'HTML, ci vivono il filo
   *  e il composer, e ridisegnarlo vorrebbe dire buttare via la conversazione
   *  a ogni salvataggio.
   */
  _disegna() {
    if (!this.pista) return;
    for (const vecchio of this.pista.querySelectorAll('.casa-pagina[data-id]')) {
      vecchio.remove();
    }
    for (const s of this.schermate) {
      const pannello = document.createElement('div');
      pannello.className = 'casa-pagina';
      pannello.dataset.id = s.id;
      pannello.dataset.kind = s.kind;
      this.pista.appendChild(pannello);
    }
  }

  _armaGesto() {
    let larghezzaPista = 0;

    this._staccaGesto = osservaGestoOrizzontale(this.pista, {
      puoIniziare: () => {
        // Guardia: fuori dalla chat comandano le stanze, non le pagine.
        if (this.app?.view && this.app.view !== 'chat') return false;
        // Guardia: un cassetto aperto possiede il proprio gesto.
        if (this.app?.launcher?.isOpen?.()) return false;
        // Con la sola chat non c'e' nessun posto dove andare, e un elastico
        // che risponde a vuoto sembra un difetto invece che un confine.
        if (this.quante < 2) return false;
        larghezzaPista = this.pista.clientWidth || window.innerWidth;
        return true;
      },

      onOrizzontale: () => {
        this.pista.style.transition = 'none';
        this.pista.style.willChange = 'transform';
      },

      onTrascina: (dx) => {
        /* Ai due capi il dito tira, ma di meno: la pista non si richiude in
           cerchio come le linguette dell'officina. Li' le voci sono quattro e
           note; qui quante siano lo decide l'utente, e girando in tondo fra
           otto pagine non si sa piu' dove si e'. */
        let scostamento = dx;
        const alPrimo = this.indice === 0 && dx > 0;
        const allUltimo = this.indice === this.quante - 1 && dx < 0;
        if (alPrimo || allUltimo) {
          scostamento = dx * OLTRE_IL_CAPO * 2;
        }
        const base = -this.indice * larghezzaPista;
        this.pista.style.transform = `translateX(${(base + scostamento).toFixed(2)}px)`;
      },

      onFine: ({ verso, conferma }) => {
        this.pista.style.willChange = '';
        const passo = conferma ? (verso === 'prev' ? -1 : +1) : 0;
        this.vaiA(this.indice + passo);
      },

      onAnnulla: () => {
        this.pista.style.willChange = '';
        this.vaiA(this.indice);
      },
    });
  }
}
