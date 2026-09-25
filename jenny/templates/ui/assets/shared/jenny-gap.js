/** Lasciare spazio a Jenny **solo dove Jenny c'e' davvero**.
 *
 *  Il problema, misurato sul Titan 2 il 20/09/2026. I messaggi avevano
 *  `max-width: 88%`, e quel tetto non era decorazione: serviva a non finirle
 *  dietro. Solo che lei sta **in fondo a destra** e il tetto era applicato a
 *  *tutti* i messaggi, anche a quelli in cima dove non c'e' nessuno — 82,6 px
 *  CSS buttati su ogni riga, il **14% dello schermo**, per un ostacolo alto
 *  87,6 px in un angolo.
 *
 *  **La geometria non ha numeri magici.** Il quadrato dell'arte ha i margini
 *  trasparenti, e i due rapporti sono gia' misurati e gia' esportati da
 *  `shared/mascot.js`: il personaggio occupa il **45% centrale in larghezza** e
 *  il **73% in altezza** del canvas. Da li' escono sia la banda da scansare sia
 *  il margine da lasciare, a qualunque taglia della mascotte e in qualunque
 *  ancoraggio — e il conto non va rifatto a mano se un giorno lei cresce.
 *
 *  Con i valori di serie (`--jenny-size: 120`, fuori): la figura comincia a
 *  517,4 px CSS e intrude negli **87,6 px in fondo** al filo; il margine che
 *  serve e' **39 px**, non 82.
 *
 *  **Vale per tutti e due i lati della conversazione** (21/09/2026). All'inizio
 *  si scansavano solo le risposte, perche' il tetto che si stava togliendo era
 *  loro. Ma le bolle di chi scrive stanno a **destra**, e la piu' recente sta
 *  in fondo: l'angolo di Jenny e' esattamente il loro. Il margine si calcola
 *  una volta sola e va su chiunque la tocchi.
 *
 *  **Quel che questo modulo non fa, ed e' dichiarato.** Il filo scorre e lei
 *  no: quale messaggio le finisca dietro cambia a ogni scorrimento, e il solo
 *  CSS non lo sa esprimere. Qui si ricalcola all'arrivo di un messaggio e
 *  **quando lo scorrimento si ferma**, non a ogni fotogramma — perche'
 *  aggiungere un margine manda il testo a capo, e farlo *durante* lo
 *  scorrimento sposterebbe sotto le dita quel che si sta leggendo. Il prezzo e'
 *  che per un istante, mentre scorri, un messaggio puo' passarle dietro.
 */

import { ART_HEIGHT_RATIO } from './mascot.js';

/** Quanto del quadrato e' margine trasparente **per lato**, in larghezza.
 *
 *  Il personaggio occupa il 45% centrale (bbox alpha dei webp, v. `mascot.js`),
 *  quindi di qua e di la' ne avanza (1 - 0,45) / 2. E' il numero che distingue
 *  «dove comincia il suo riquadro» da «dove comincia lei», e sono 33 px a 120:
 *  scansare il riquadro vorrebbe dire lasciare un buco dove non c'e' nessuno.
 */
export const MARGINE_LATERALE = (1 - 0.45) / 2;

/** Il rettangolo della **figura**, ricavato da quello del suo riquadro.
 *
 *  @param {{left:number,right:number,top:number,bottom:number}} quadrato
 *  @param {number} lato  il lato del quadrato (`--jenny-size`)
 *  @returns {{left:number, top:number}} gli unici due bordi che contano: da
 *           dove comincia lei andando verso destra, e da dove verso l'alto.
 */
export function figuraDi(quadrato, lato) {
  return {
    left: quadrato.left + lato * MARGINE_LATERALE,
    /* I piedi appoggiano sul composer, quindi la figura sta **in basso** nel
       quadrato: il suo bordo alto si conta dal fondo, non dall'alto. */
    top: quadrato.bottom - lato * ART_HEIGHT_RATIO,
  };
}

/** Questo messaggio le finisce addosso?
 *
 *  Pura di proposito: e' la regola, e si prova sotto node senza un browser.
 *  Basta che i due rettangoli si sovrappongano in **tutti e due** gli assi —
 *  un messaggio alto che finisce sopra di lei non va scansato, e nemmeno uno
 *  corto che sta alla sua altezza ma tutto a sinistra.
 */
export function serveScansare(messaggio, figura) {
  return messaggio.right > figura.left && messaggio.bottom > figura.top;
}

/** Quanto margine destro serve perche' il testo le si fermi accanto. */
export function margineDa(figura, destraDelFilo) {
  return Math.max(0, Math.round(destraDelFilo - figura.left));
}

/* ── L'aggancio al DOM ───────────────────────────────────────────────────── */

export const CLASSE = 'is-under-jenny';
/** Quanto si aspetta, dopo l'ultimo evento di scorrimento, prima di rifare i
 *  conti. Abbastanza da non cadere dentro uno scorrimento con l'inerzia
 *  ancora viva, abbastanza poco da non farsi notare. */
export const QUIETE_MS = 120;

export class JennyGap {
  /** @param thread   il contenitore che scorre
   *  @param mascotte il nodo della mascotte, o `null` se e' spenta */
  constructor(thread, mascotte) {
    this.thread = thread;
    this.mascotte = mascotte;
    this._timer = null;
    this._segnati = new Set();
  }

  /** Ricalcola adesso. Da chiamare all'arrivo di un messaggio. */
  aggiorna() {
    if (!this.thread || !this.mascotte || this.mascotte.classList?.contains('hidden-mode')) {
      this._pulisci();
      return;
    }
    const quadrato = this.mascotte.getBoundingClientRect();
    if (!quadrato.width) {
      this._pulisci();
      return;
    }
    const figura = figuraDi(quadrato, quadrato.width);
    const filo = this.thread.getBoundingClientRect();
    /* Il bordo destro del **contenuto**, non della scatola: il padding del filo
       non e' spazio in cui il testo possa finire. */
    const stile = getComputedStyle(this.thread);
    const destra = filo.right - parseFloat(stile.paddingRight || '0');
    const margine = margineDa(figura, destra);

    this.thread.style.setProperty('--jenny-gap', `${margine}px`);

    const vivi = new Set();
    /* **Tutti** i messaggi, non solo le risposte. Qui c'era `.home-msg-jenny`,
       e le bolle di chi scrive restavano fuori: peccato che quelle siano
       `align-self: flex-end`, cioe' incollate al bordo destro — proprio la
       colonna dove sta lei. L'ultima cosa scritta e' anche quella piu' in
       basso, quindi era la piu' coperta di tutte. Il *come* scansare cambia
       fra le due (la risposta stringe il testo, la bolla si sposta), e quello
       lo dice il CSS; qui la regola e' una sola, ed e' geometrica. */
    for (const msg of this.thread.querySelectorAll('.home-msg')) {
      /* Il rettangolo va letto **senza** il margine che gli abbiamo messo noi,
         altrimenti un messaggio scansato si misura piu' stretto, esce dalla
         banda, e al giro dopo rientra: un'altalena a ogni ricalcolo. */
      const segnato = msg.classList.contains(CLASSE);
      if (segnato) msg.classList.remove(CLASSE);
      const r = msg.getBoundingClientRect();
      if (serveScansare(r, figura)) {
        msg.classList.add(CLASSE);
        vivi.add(msg);
      }
    }
    this._segnati = vivi;
  }

  /** Lo scorrimento e' in corso: si aspetta che si fermi. */
  scorrendo() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this._timer = null;
      this.aggiorna();
    }, QUIETE_MS);
  }

  _pulisci() {
    for (const msg of this._segnati) msg.classList.remove(CLASSE);
    this._segnati = new Set();
  }
}
