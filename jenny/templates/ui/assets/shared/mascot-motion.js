/** I gesti di Jenny: il catalogo e l'imbuto, per tutte le sue Jenny.
 *
 *  Un gesto e' un'animazione CSS a tempo, su uno di due livelli annidati:
 *  `lean` e' l'inclinazione del moto (`.jenny-lean`), `squash` lo schiaccia e
 *  allunga del corpo (`.jenny-squash`). Ogni canale ne porta uno alla volta, e
 *  i due si sommano perche' sono due elementi, non due animazioni sulla stessa
 *  proprieta'. Il gesto sta in `data-lean`/`data-squash` sull'elemento della
 *  Jenny; la durata e' **solo qui**: `MotionChannels.play` la scrive in
 *  `--jenny-<canale>-ms`, che il CSS legge, cosi' il timer che toglie
 *  l'attributo e l'animazione non possono divergere.
 *
 *  Le Jenny sono due: la mascotte (`jenny-mascot.js`) e quella che cade
 *  nell'onboarding (`onboarding-wizard.js`), che e' un documento a parte con
 *  la sua arte cotta. Hanno gesti diversi, ma il catalogo e' uno: un nome
 *  vale la stessa cosa dappertutto, e il CSS di un nome e' uno solo.
 *
 *  Dall'utente del 05/10/2026, sui mockup «Il saluto di Jenny» e «Jenny in
 *  movimento»: squash and stretch su tutto, mai in diagonale.
 */

export const MOTIONS = {
  lean: {
    // Esce dal bordo (verso l'interno) o ci torna: si sporge nel verso in cui
    // va, frena, si assesta. Una rotazione attorno ai piedi, non un taglio.
    'go-in': 480,
    'go-edge': 480,
    // Il cambio di gesto nel parlato (braccia giu' / mano su): uno scatto
    // invece del cambio di colpo.
    gest: 300,
  },
  squash: {
    // Si carica, salta alzando il braccio, saluta tre volte ondeggiando a tempo
    // col braccio, si assesta.
    greet: 1500,
    // Il saltello: il saluto quando un braccio alzato direbbe una cosa falsa
    // (pensa, parla) o non si vedrebbe (al bordo).
    hop: 1000,
    // «Sono qui, chiudi da me»: lo chiede il guscio quando tocchi il velo
    // dietro la chat, che apposta non la chiude. Si carica e si sporge verso
    // di te.
    nudge: 520,
    // L'umore arriva con una reazione del corpo (i nomi sono quelli di
    // MOOD_FACES): un saltello, un afflosciarsi, una pestata.
    happy: 700,
    sad: 1800,
    angry: 650,
    // Il pallino si accende e lei se ne accorge.
    perk: 340,
    // L'atterraggio a fine volo, quando le pose lasciano il posto al riposo.
    land: 300,
    // Una sillaba: ogni volta che apre la bocca si allunga di un niente.
    syl: 260,
    // L'onboarding: cade dall'alto allungandosi, si schiaccia all'impatto e
    // rimbalza, si rialza caricando le gambe (e poi saluta col `greet`).
    fall: 650,
    impact: 360,
    getup: 300,
  },
};

/* Le variabili da cui il CSS legge la durata del gesto di ogni canale. */
export const MOTION_VARS = { lean: '--jenny-lean-ms', squash: '--jenny-squash-ms' };

/** I due canali dei gesti di un elemento. */
export class MotionChannels {
  constructor(el) {
    this.el = el;
    this._now = { lean: null, squash: null };
    this._timers = { lean: null, squash: null };
  }

  /** Il gesto in corso su *channel*, o null. */
  now(channel) {
    return this._now[channel];
  }

  /* Un gesto su un canale, riacceso da capo se c'era gia', al posto di quello
     che c'era; *onEnd* quando finisce da se' (non quando lo si ferma). Tolto a
     tempo e non su `animationend`: con `prefers-reduced-motion` l'animazione
     non parte, e l'attributo resterebbe addosso. */
  play(channel, name, onEnd = null) {
    const ms = MOTIONS[channel][name];
    this.stop(channel);
    void this.el.offsetWidth; // riparte l'animazione anche se era la stessa
    this.el.style.setProperty(MOTION_VARS[channel], `${ms}ms`);
    this.el.setAttribute(`data-${channel}`, name);
    this._now[channel] = name;
    this._timers[channel] = setTimeout(() => {
      this.stop(channel);
      onEnd?.();
    }, ms);
  }

  stop(channel) {
    if (this._timers[channel]) clearTimeout(this._timers[channel]);
    this._timers[channel] = null;
    if (!this._now[channel]) return;
    this._now[channel] = null;
    this.el.removeAttribute(`data-${channel}`);
  }

  stopAll() {
    this.stop('lean');
    this.stop('squash');
  }
}
