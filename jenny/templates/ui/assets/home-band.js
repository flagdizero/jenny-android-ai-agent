/** La casa — la fascia in fondo al filo, e la sagoma di Jenny sopra di lei.
 *
 *  Prende il posto di `shared/jenny-gap.js` (06/10/2026). Quello misurava quali
 *  messaggi le finissero dietro e dava loro un margine **a scorrimento fermo**:
 *  ogni volta che ti fermavi, le righe vicino a lei tornavano a capo sotto il
 *  dito, e la riga che stavi leggendo cambiava posto. Qui niente dipende da
 *  dove sta il filo: solo da cosa c'e' dentro.
 *
 *  Lo spazio per lei e' fatto di due pezzi, e uno solo vive in questo file.
 *
 *  - **La fascia** (`.home-band`, l'ultimo figlio del filo, v. `HomeChat`):
 *    alta meta' di lei, ed e' tutta CSS. Ci scende la coda dell'ultima risposta
 *    (Copia, ora, secondi) con una regola `:has()` di home-style.css, quindi
 *    la riga che la coda occupava comunque si recupera. Ed e' il posto per una
 *    striscia animata di chi lavora.
 *  - **La sagoma**, qui: sopra la fascia resta la sua testa, e le ultime righe
 *    dell'ultima risposta le girano attorno. Sono due float in testa all'ultimo
 *    blocco: il primo, largo zero, spinge il secondo in fondo, e il secondo ha
 *    la forma della spalla (`shape-outside`). La sagoma sta nel testo e scorre
 *    con lui, quindi si ricalcola solo quando il testo cambia: un pezzo di
 *    risposta, un messaggio, la taglia di lei, la larghezza del filo. **Mai**
 *    per uno scorrimento.
 *
 *  La larghezza e' quella di lei **fuori**, anche quando sta al bordo: al
 *  bordo ne copre meno e resta un po' d'aria, ma il testo non torna a capo
 *  quando la tocchi.
 */

import { ART_HEIGHT_RATIO, OUT_RATIO } from './shared/mascot.js';

/** Quanto del quadrato e' margine trasparente per lato, in larghezza: il
 *  personaggio occupa il 45% centrale (bbox alpha dei webp, v. `mascot.js`). */
export const SIDE_MARGIN = (1 - 0.45) / 2;

/** Da dove comincia la figura, contando dal bordo destro dello schermo, in
 *  frazioni del suo quadrato, quando e' fuori: il quadrato sborda di
 *  `OUT_RATIO`, e dentro ha `SIDE_MARGIN` di vuoto a destra. 0,475 → 57 px a
 *  taglia 120. */
export const FIGURE_FROM_EDGE = 1 - OUT_RATIO - SIDE_MARGIN;

/** L'aria fra le righe e lei, in px. */
export const BREATH = 6;

/** La sagoma da lasciarle nell'ultimo blocco, o `null` se non serve.
 *
 *  Pura di proposito: e' la regola, e si prova sotto node senza un browser.
 *
 *  @param {{size:number, padRight:number, distance:number, blockHeight:number}} m
 *    `size` il lato del suo quadrato; `padRight` il padding destro del filo
 *    (spazio in cui il testo non va comunque); `distance` quanto c'e' fra il
 *    fondo del blocco e il fondo del filo, cioe' il pavimento su cui
 *    appoggia; `blockHeight` l'altezza del blocco senza sagoma.
 *  @returns {{width:number, height:number} | null}
 */
export function shapeOf({ size, padRight, distance, blockHeight }) {
  const width = Math.round(size * FIGURE_FROM_EDGE - padRight + BREATH);
  /* La sua testa sta `ART_HEIGHT_RATIO` lati sopra il pavimento: del blocco
     tocca solo quel che c'e' sopra la fascia. Una risposta piu' corta di quella
     fetta la prende tutta, senza allungarsi. */
  const zone = Math.round(size * ART_HEIGHT_RATIO - distance + BREATH);
  if (width <= 0 || zone <= 0 || blockHeight <= 0) return null;
  return { width, height: Math.min(zone, blockHeight) };
}

/** I due float. Il nome della classe sul blocco e' anche il segno per toglierli. */
export const SHAPED = 'has-jenny-shape';
const PUSH = 'home-shape-push';
const SHAPE = 'home-shape';

export class HomeBand {
  /** @param band   la fascia (`HomeChat.bandEl`), ultimo figlio del filo
   *  @param mascot il nodo della mascotte: il suo lato e' la taglia vera */
  constructor(band, mascot) {
    this.band = band;
    this.thread = band?.parentElement || null;
    this.mascot = mascot;
    this._block = null;
    /* Le due cose che cambiano la sagoma senza cambiare il testo: la taglia di
       lei e la larghezza del filo (una rotazione, la chat che si sposta in una
       pagina). Osservate come scatole, non come contenuto: scorrere non le
       tocca, e la sagoma non cambia la loro misura. */
    if (typeof ResizeObserver === 'function') {
      this._observer = new ResizeObserver(() => this.refresh());
      if (this.thread) this._observer.observe(this.thread);
      if (mascot) this._observer.observe(mascot);
    }
  }

  /** Rifa la sagoma. Da chiamare quando il testo in fondo al filo cambia. */
  refresh() {
    this._clear();
    const block = this._lastBlock();
    if (!block) return;
    const size = this.mascot?.offsetWidth || 0;
    if (!size) return;
    const padRight = parseFloat(getComputedStyle(this.thread).paddingRight || '0') || 0;
    /* Una differenza fra due rettangoli dello stesso filo: scorrono insieme,
       quindi non dipende da dove sta lo scorrimento. */
    const distance = this.band.getBoundingClientRect().bottom - block.getBoundingClientRect().bottom;
    const shape = shapeOf({ size, padRight, distance, blockHeight: block.offsetHeight });
    if (!shape) return;

    const push = document.createElement('span');
    push.className = PUSH;
    push.style.height = '0px';
    const figure = document.createElement('span');
    figure.className = SHAPE;
    figure.style.width = `${shape.width}px`;
    figure.style.height = `${shape.height}px`;
    block.classList.add(SHAPED);
    block.prepend(push, figure);
    this._block = block;

    /* Il primo float porta il secondo in fondo al blocco. Avvolgere le righe
       accanto a lei puo' farne andare a capo una in piu', e allora il blocco
       cresce: qualche giro, e l'altezza si ferma. */
    for (let i = 0; i < 6; i++) {
      const want = Math.max(0, block.offsetHeight - shape.height);
      if (Math.abs((parseFloat(push.style.height) || 0) - want) < 1) break;
      push.style.height = `${want}px`;
    }
  }

  /** L'ultimo blocco di testo dell'ultima risposta, se e' subito sopra la
   *  fascia: un posto vivo acceso (qualcuno lavora) o una bolla tua in mezzo
   *  vogliono dire che la sua testa non e' accanto a una risposta. Un'immagine
   *  in fondo non si avvolge: la scansa il CSS. */
  _lastBlock() {
    let prev = this.band?.previousElementSibling || null;
    if (prev?.classList.contains('home-live')) {
      if (!prev.hidden) return null;
      prev = prev.previousElementSibling;
    }
    if (!prev?.classList.contains('home-msg-jenny')) return null;
    const content = [...prev.children].filter((c) => !c.classList.contains('home-tail'));
    const last = content[content.length - 1];
    return last?.classList.contains('home-block') ? last : null;
  }

  _clear() {
    const block = this._block;
    this._block = null;
    if (!block) return;
    for (const n of block.querySelectorAll(`:scope > .${PUSH}, :scope > .${SHAPE}`)) n.remove();
    block.classList.remove(SHAPED);
  }
}
