/** Lo schiaccia e allunga del volo — le formule, senza DOM.
 *
 *  Il volo (`mascot-drag.js`) e' fisica a ogni fotogramma, quindi la sua
 *  deformazione non puo' essere un keyframe CSS: la calcola da velocita' e
 *  tempi, e qui stanno le formule. Funzioni pure, che ritornano
 *  `{ sx, sy, dy }` — le due scale e uno spostamento verticale in px — da
 *  applicare **sugli assi del disegno**: mai lungo il verso del moto, che in
 *  diagonale storce lo sprite (il primo mockup del 05/10/2026 lo faceva, e
 *  la testa scivolava di lato rispetto ai piedi).
 *
 *  Le stesse formule, con le stesse costanti, le porta la mascotte flottante
 *  nativa (`FloatingSquash.kt`): un contratto in tests/webui le tiene uguali.
 *
 *  Regola di tutte: il volume resta (se si allunga si stringe, `sx·sy` entro
 *  il 4% da 1), e ogni deformazione torna a 1 da se'.
 */

/** Appesa per la mano: si allunga quanto piu' la strattoni. */
export const HELD_MAX = 0.05;
export const HELD_SPEED = 4000; // px/s per un allungamento del 100%: il tetto arriva prima
/** In aria: si allunga in verticale quanto va veloce su o giu'. */
export const AIR_MAX = 0.06;
export const AIR_SPEED = 2500;
/** Il tonfo: schiacciata quanto e' caduta forte, poi una molla smorzata. */
export const IMPACT_MIN = 0.05;
export const IMPACT_MAX = 0.18;
export const IMPACT_SPEED = 1800;
export const IMPACT_PERIOD_MS = 300;
export const IMPACT_DECAY_MS = 120;
export const IMPACT_MS = 600; // oltre, e' ferma
/** La ripartenza: carica le gambe **da ferma**, negli ultimi LOAD_MS a terra,
 *  e si distende nel primo passo in BLEND_MS, mentre il passo entra. Niente
 *  spostamento in su: alzarla mentre comincia a camminare si leggeva come un
 *  saltellino (dall'utente, 06/10/2026). */
export const LOAD_MS = 110;
export const BLEND_MS = 150;
/** Il passo: affonda quando cambia piede, si alza a meta', in una curva
 *  continua. Il periodo e' quello dell'alternanza walk1/walk2 di
 *  `mascot-drag.js`. */
export const STEP_MS = 500;
export const STEP_LIFT_PX = 2;

/** Dove poggiano i piedi, in frazioni del quadrato: e' il centro delle scale
 *  a terra, cosi' i piedi restano dove sono. */
export const FEET = { ground: 0.92, walk: 0.88 };

const IDENTITY = Object.freeze({ sx: 1, sy: 1, dy: 0 });
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/* Un allungamento verticale *s*, col volume conservato. */
function stretch(s, squeeze) {
  return { sx: 1 - s * squeeze, sy: 1 + s, dy: 0 };
}

/** Appesa: *speed* e' il modulo della velocita' (px/s). La scala va applicata
 *  dopo la rotazione del pendolo: lungo il braccio, cioe' l'asse del disegno. */
export function heldStretch(speed) {
  return stretch(clamp(Math.abs(speed) / HELD_SPEED, 0, HELD_MAX), 0.45);
}

/** In aria: *vy* e' la velocita' verticale (px/s), salita o caduta e' uguale. */
export function airStretch(vy) {
  return stretch(clamp(Math.abs(vy) / AIR_SPEED, 0, AIR_MAX), 0.5);
}

/** Il tonfo, *ms* dopo l'impatto a velocita' *vImpact* (px/s). */
export function impactSquash(vImpact, ms) {
  if (!(ms >= 0) || ms >= IMPACT_MS) return IDENTITY;
  const amp = clamp(Math.abs(vImpact) / IMPACT_SPEED, IMPACT_MIN, IMPACT_MAX);
  const f = amp * Math.cos((2 * Math.PI * ms) / IMPACT_PERIOD_MS) * Math.exp(-ms / IMPACT_DECAY_MS);
  return { sx: 1 + f, sy: 1 - f, dy: 0 };
}

/** Carica le gambe, *ms* dopo l'inizio della carica (a terra, ferma). */
export function loadLegs(ms) {
  const k = clamp(ms / LOAD_MS, 0, 1);
  return { sx: 1 + 0.06 * k, sy: 1 - 0.07 * k, dy: 0 };
}

/** Il passo al tempo *now* (lo stesso orologio che sceglie walk1/walk2, cosi'
 *  l'affondo cade sul cambio di piede). *d* vale 1 al cambio di piede e 0 a
 *  meta' passo: una curva continua, che torna su se stessa a ogni passo. */
export function walkStep(now) {
  const p = (((now % STEP_MS) + STEP_MS) % STEP_MS) / STEP_MS;
  const d = (1 + Math.cos(2 * Math.PI * p)) / 2;
  const sy = 1 + 0.02 * (1 - d) - 0.025 * d;
  return { sx: 1 - (sy - 1) * 0.6, sy, dy: -STEP_LIFT_PX * (1 - d) };
}

/** I primi passi, *ms* dopo la ripartenza: dalla carica (`loadLegs` alla fine)
 *  si distende, e il passo entra con lo stesso peso. Dopo BLEND_MS e' il passo
 *  e basta. */
export function walkStart(ms, now) {
  const k = clamp(ms / BLEND_MS, 0, 1);
  const e = 1 - (1 - k) * (1 - k);
  const w = walkStep(now);
  return {
    sx: (1.06 - 0.06 * e) * (1 + (w.sx - 1) * e),
    sy: (0.93 + 0.07 * e) * (1 + (w.sy - 1) * e),
    dy: w.dy * e,
  };
}

/** `{ sx, sy, dy }` come pezzo di `transform`, scalato attorno al punto che sta
 *  *feetPx* sotto l'origine del livello (il pivot del volo). */
export function aroundFeet({ sx, sy, dy }, feetPx) {
  if (sx === 1 && sy === 1 && dy === 0) return '';
  const fo = feetPx.toFixed(1);
  return ` translate(0px, ${(feetPx + dy).toFixed(1)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)}) translate(0px, -${fo}px)`;
}
