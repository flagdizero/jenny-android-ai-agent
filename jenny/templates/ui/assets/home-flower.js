/** La casa — il fiore della riga di lavoro.
 *
 *  Il ✿ di Jenny, disegnato in SVG davanti alla parola, che si muove in modo
 *  diverso per ogni famiglia di strumenti (v. `home-activity.js`). Quando la
 *  famiglia cambia, il fiore si chiude e si riapre: un cambio vero si vede anche
 *  con la coda dell'occhio.
 *
 *  **Tutto nel piano.** Il fiore gira, respira, spinge i petali verso fuori, li
 *  fa oscillare di lato — e nient'altro. Due cose sono escluse apposta, perche'
 *  l'occhio le legge come profondita':
 *    - un petalo che si stringe su un asse (e' un ribaltamento, cioe' 3D);
 *    - grandezza o spinta **sfasate** da un petalo all'altro: un'onda di scala
 *      che fa il giro fa sembrare il fiore un disco inclinato che ondeggia.
 *  Da petalo a petalo puo' cambiare solo l'oscillazione di lato. Le due
 *  eccezioni: `read` solleva un petalo alla volta e `write` li fa spuntare in
 *  fila — un gesto che si compie, non un'onda che gira. Il banco
 *  (`test_casa_fiore_client.py`) lo misura.
 *
 *  Il ciclo di animazione gira solo mentre la riga e' a schermo: `start()` e
 *  `stop()` li chiama la riga, e a riga nascosta il fiore non costa niente.
 */

const NS = 'http://www.w3.org/2000/svg';
const TAU = Math.PI * 2;

// Un lobo del ✿: largo in punta, con la tacca, stretto alla base.
const PETAL = 'M0,-5 C-13,-12 -22,-30 -12,-41 C-7,-46 -2,-45 0,-39 C2,-45 7,-46 12,-41 C22,-30 13,-12 0,-5 Z';
const VEIN = 'M0,-12 C-6,-17 -10,-28 -5.5,-34 C-3,-36.5 -1,-35.5 0,-32 C1,-35.5 3,-36.5 5.5,-34 C10,-28 6,-17 0,-12 Z';

const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const easeOutBack = (x) => { const c1 = 1.9, c3 = c1 + 1; return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2; };
const easeInOut = (x) => (x < 0.5 ? 2 * x * x : 1 - (-2 * x + 2) ** 2 / 2);

/* Ogni famiglia e' una posa: rotazione in gradi al secondo, grandezza del
   fiore, l'aiutante che gli gira intorno, e per ogni petalo scala `s`, spinta
   verso fuori `push` e oscillazione di lato `wob` (gradi). Le pose non si
   sostituiscono di colpo: lo stato corrente le insegue, e il salto lo copre la
   sbocciata. */
export const POSE = {
  think: {
    spin: () => 26, main: 1, sat: 0,
    petal: (t, i) => ({ s: 0.9 + 0.1 * Math.sin(t * 2.4), push: 0, wob: 9 * Math.sin(t * 2.4 - i * TAU / 5) }),
    sway: (t) => 8 * Math.sin(t * 0.9), bob: () => 0, center: (t) => 1 + 0.14 * Math.sin(t * 2.4),
  },
  // Sfoglia: un petalo alla volta si solleva e oscilla di lato, come una pagina.
  read: {
    spin: () => 9, main: 1, sat: 0,
    petal: (t, i) => {
      const P = 0.8;
      if (i !== Math.floor(t / P) % 5) return { s: 0.93, push: 0, wob: 0 };
      const q = (t % P) / P;
      return { s: 0.93, push: 5 * Math.sin(q * Math.PI), wob: 26 * Math.sin(q * TAU) };
    },
    sway: (t) => 3 * Math.sin(t * 0.6), bob: () => 0, center: () => 1,
  },
  // Si guarda intorno: scatti di rotazione e pause.
  search: {
    spin: (t) => 40 + 560 * Math.max(0, Math.sin(t * 2.6)) ** 6, main: 0.95, sat: 0, snappy: true,
    petal: (t) => ({ s: 0.9 + 0.05 * Math.sin(t * 5), push: 0, wob: 0 }),
    sway: () => 0, bob: () => 0, center: (t) => 0.9 + 0.1 * Math.sin(t * 5),
  },
  // Scrive un petalo alla volta; finita la riga si richiude e ricomincia.
  write: {
    spin: () => 14, main: 1, sat: 0,
    petal: (t, i) => {
      const C = 2.6, q = (t % C) / C;
      const g = q < 0.72
        ? easeOutBack(clamp((q - i * 0.11) / 0.16, 0, 1))
        : 1 - 0.75 * easeInOut(clamp((q - 0.72) / 0.28, 0, 1));
      return { s: 0.22 + 0.8 * g, push: 0, wob: 0 };
    },
    sway: (t) => 4 * Math.sin(t * 1.1), bob: () => 0, center: () => 1,
  },
  // I petali si allungano verso fuori e tornano, e il fiore galleggia.
  out: {
    spin: () => 38, main: 0.96, sat: 0,
    petal: (t, i) => ({ s: 0.92, push: 3 + 9 * Math.max(0, Math.sin(t * 2.6)), wob: 3 * Math.sin(t * 2 + i) }),
    sway: () => 0, bob: (t) => 3.5 * Math.sin(t * 1.7), center: () => 1,
  },
  // Gira forte, i petali vibrano di lato, il centro batte.
  run: {
    spin: () => 250, main: 1, sat: 0,
    petal: (t, i) => ({ s: 0.92 + 0.08 * Math.sin(t * 16), push: 0, wob: 4 * Math.sin(t * 21 + i * 2.1) }),
    sway: () => 0, bob: (t) => 0.8 * Math.sin(t * 30), center: (t) => 1 + 0.22 * Math.sin(t * 12),
  },
  // Rallenta e si fa piu' piccolo, e gli gira intorno un fiorellino aiutante.
  delegate: {
    spin: () => 20, main: 0.72, sat: 1,
    petal: (t) => ({ s: 0.9 + 0.06 * Math.sin(t * 1.8), push: 0, wob: 0 }),
    sway: () => 0, bob: () => 0, center: () => 1,
  },
  // Il ripiego: rotazione e respiro regolari, senza fingere di sapere cosa.
  busy: {
    spin: () => 75, main: 1, sat: 0,
    petal: (t, i) => ({ s: 0.92 + 0.08 * Math.sin(t * 4), push: 0, wob: 6 * Math.sin(t * 4 - i * TAU / 5) }),
    sway: () => 0, bob: () => 0, center: () => 1,
  },
  // A riposo: un bocciolo. E' la posa da cui sboccia alla prima comparsa.
  idle: {
    spin: () => 5, main: 1, sat: 0,
    petal: (t) => ({ s: 0.5 + 0.04 * Math.sin(t * 1.5), push: 0, wob: 0 }),
    sway: () => 0, bob: () => 0, center: () => 0.8,
  },
};

const BLOOM_S = 0.65;

function node(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  parent.appendChild(n);
  return n;
}

export class Flower {
  constructor(svg, { reducedMotion = false } = {}) {
    this.svg = svg;
    this.reduced = reducedMotion;
    this._raf = null;
    this._last = 0;
    this._build();
    this.reset();
  }

  _build() {
    const svg = this.svg;
    svg.setAttribute('viewBox', '-60 -60 120 120');
    svg.setAttribute('aria-hidden', 'true');
    this.main = node('g', {}, svg);
    this.petals = [];
    for (let i = 0; i < 5; i++) {
      const g = node('g', {}, this.main);
      node('path', { d: PETAL, class: 'home-flower-petal' }, g);
      node('path', { d: VEIN, class: 'home-flower-vein' }, g);
      this.petals.push(g);
    }
    this.center = node('circle', { r: 7, class: 'home-flower-heart' }, this.main);
    this.dot = node('circle', { r: 2.2, class: 'home-flower-pollen' }, this.main);
    this.sat = node('g', { opacity: 0 }, svg);
    for (let i = 0; i < 5; i++) {
      node('path', { d: PETAL, class: 'home-flower-petal', transform: `rotate(${i * 72})` }, this.sat);
    }
    node('circle', { r: 7, class: 'home-flower-heart' }, this.sat);
  }

  /** Torna bocciolo, fermo: la prossima comparsa sboccia da qui. */
  reset() {
    this.stop();
    this.mode = 'idle';
    this.t = 0; this.rot = 0; this.speed = 5;
    this.mainScale = 1; this.satAmt = 0; this.bob = 0; this.sway = 0; this.centerScale = 0.8;
    this.state = Array.from({ length: 5 }, () => ({ s: 0.5, push: 0, wob: 0 }));
    this.bloomAt = -9;
    this._render();
  }

  /** La famiglia in corso. Cambiarla fa sbocciare il fiore. */
  setMode(mode) {
    const next = POSE[mode] ? mode : 'busy';
    if (next === this.mode) return;
    this.mode = next;
    if (this.reduced) {
      // Fermo nella posa della famiglia: si fotografa uno stato gia' assestato.
      for (let i = 0; i < 60; i++) this._step(1 / 30);
      this.bloomAt = -9;
      this._render();
      return;
    }
    this.bloomAt = this.t;
  }

  start() {
    if (this.reduced || this._raf !== null) return;
    this._last = performance.now();
    const loop = (now) => {
      const dt = Math.min(0.05, (now - this._last) / 1000);
      this._last = now;
      this._step(dt);
      this._render();
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    if (this._raf !== null) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  _step(dt) {
    const P = POSE[this.mode];
    this.t += dt;
    const t = this.t;
    const kf = 1 - Math.exp(-dt * 14);
    const ks = 1 - Math.exp(-dt * 4);
    this.speed += (P.spin(t) - this.speed) * (P.snappy ? 1 - Math.exp(-dt * 12) : ks);
    this.rot = (this.rot + this.speed * dt) % 360;
    this.mainScale += (P.main - this.mainScale) * ks;
    this.satAmt += (P.sat - this.satAmt) * ks;
    this.bob += (P.bob(t) - this.bob) * kf;
    this.sway += (P.sway(t) - this.sway) * kf;
    this.centerScale += (P.center(t) - this.centerScale) * kf;
    this.state.forEach((p, i) => {
      const g = P.petal(t, i);
      p.s += (g.s - p.s) * kf;
      p.push += (g.push - p.push) * kf;
      p.wob += (g.wob - p.wob) * kf;
    });
  }

  /* La sbocciata: si chiude in un quarto abbondante del tempo e si riapre con
     un poco di rimbalzo. */
  _bloom() {
    const b = (this.t - this.bloomAt) / BLOOM_S;
    if (b >= 1 || b < 0) return 1;
    if (b < 0.28) return 1 - 0.68 * easeInOut(b / 0.28);
    return 0.32 + 0.68 * easeOutBack((b - 0.28) / 0.72);
  }

  _render() {
    const bm = this._bloom();
    const spin = this.rot + this.sway;
    this.main.setAttribute('transform',
      `translate(0 ${this.bob.toFixed(2)}) rotate(${spin.toFixed(2)}) scale(${(this.mainScale * bm).toFixed(3)})`);
    this.state.forEach((p, i) => {
      this.petals[i].setAttribute('transform',
        `rotate(${(i * 72 + p.wob).toFixed(2)}) translate(0 ${(-p.push).toFixed(2)}) scale(${p.s.toFixed(3)})`);
    });
    // Mentre i petali si chiudono il cuore si allarga: il bocciolo resta pieno.
    this.center.setAttribute('r', (7 * this.centerScale * (1.9 - 0.9 * bm)).toFixed(2));
    this.dot.setAttribute('r', (2.2 * this.centerScale).toFixed(2));
    const a = this.t * 2.2;
    const sx = 50 * Math.cos(a), sy = 50 * Math.sin(a) + this.bob;
    this.sat.setAttribute('opacity', this.satAmt.toFixed(3));
    this.sat.setAttribute('transform',
      `translate(${sx.toFixed(2)} ${sy.toFixed(2)}) rotate(${(-this.t * 160).toFixed(1)}) scale(${(0.3 * this.satAmt).toFixed(3)})`);
  }
}
