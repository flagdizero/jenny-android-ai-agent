/** La casa — un mazzetto di carte appuntato nel cassetto (e sulla bacheca).
 *
 *  Le note (post-it lilla, `home-board-notes.js`) e le foto (polaroid,
 *  `home-board-photos.js`) sono lo stesso gesto: le carte una sopra l'altra, il
 *  tocco sfila quella davanti di lato e porta la prossima, e la sfilata rientra
 *  in fondo al mazzo. Qui c'e' solo la meccanica; cosa c'e' scritto su una
 *  carta, e il suo aspetto, sono di chi la usa.
 *
 *  La posizione di una carta e' una **classe** (`is-slot-0/1/2`, `is-hidden`,
 *  `is-out`): trasformazioni, colori e ombre stanno nel CSS di chi la usa. Il JS
 *  scrive solo le classi e l'ordine di sovrapposizione.
 */

/* Le carte del mazzetto che si vedono; le altre aspettano sotto la terza. */
const DECK_SLOTS = 3;
/* Quanto resta fuori, di lato, la carta sfilata prima di passare sotto: a meta'
   della sua corsa, cosi' uscire e rientrare sono un movimento solo. */
const OUT_MS = 170;

function reducedMotion() {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

export class PinnedDeck {
  /**
   * @param className  la classe del mazzetto (un `button`).
   * @param cardClass  la classe di ogni carta, a cui si aggiunge quella del posto.
   * @param count      quante carte.
   * @param drawCard   `(i) => Node[]`: quel che c'e' sulla carta `i`.
   * @param label      `(i) => string`: l'`aria-label` del mazzetto con `i` davanti.
   * @param front      la carta davanti all'inizio.
   * @param onTurn     `(front) => void`: dopo ogni tocco, la carta davanti nuova.
   */
  constructor({ className, cardClass, count, drawCard, label, front = 0, onTurn = null }) {
    this.cardClass = cardClass;
    this.count = count;
    this.label = label;
    this.front = count ? Math.min(front, count - 1) : 0;
    this.onTurn = onTurn;
    this._busy = false;
    const deck = document.createElement('button');
    deck.type = 'button';
    deck.className = className;
    deck.addEventListener('click', () => this.next());
    this.cards = Array.from({ length: count }, (_, i) => {
      const card = document.createElement('span');
      card.setAttribute('aria-hidden', 'true');
      card.append(...drawCard(i));
      deck.appendChild(card);
      return card;
    });
    this.el = deck;
    this._layout(null);
  }

  /** Ogni carta al suo posto. L'ordine di sovrapposizione si scrive qui, in
   *  linea: il mazzetto ha `isolation: isolate`, quindi resta dentro di lui e
   *  non puo' salire sopra Jenny o sopra il foglio — e il foglio di stile della
   *  casa resta senza `z-index`, come vuole la regola (l'unico e' di Jenny). */
  _layout(flyer) {
    const n = this.count;
    this.cards.forEach((card, i) => {
      const k = (i - this.front + n) % n;
      const slot = i === flyer ? 'is-out' : k < DECK_SLOTS ? `is-slot-${k}` : 'is-hidden';
      card.className = `${this.cardClass} ${slot}`;
      card.style.zIndex = String(i === flyer ? DECK_SLOTS + 2 : DECK_SLOTS - Math.min(k, DECK_SLOTS));
    });
    if (n) this.el.setAttribute('aria-label', this.label(this.front));
  }

  /** La carta davanti si sfila di lato mentre le altre avanzano di un posto;
   *  a meta' passa sotto e rientra in fondo. Un tocco durante il movimento
   *  non conta. */
  next() {
    const n = this.count;
    if (n < 2 || this._busy) return;
    const flyer = this.front;
    this.front = (this.front + 1) % n;
    this.onTurn?.(this.front);
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
}
