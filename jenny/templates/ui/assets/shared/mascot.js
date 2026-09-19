/** Preferenze della mascotte (JennyCompanion) — visibilità, aspetto e lato.
 *
 * Stato puramente client-side (localStorage), come tema/lingua/modalità
 * avanzata: non passa mai dal backend. Visibilità e taglia sono scelte
 * dell'utente (Impostazioni → Personalizzazione); il lato invece non è più
 * un'impostazione ma il ricordo di dove l'hai lasciata: lo scrive la
 * companion quando lei atterra dopo un lancio (v. mobile-jenny.js#settle).
 *
 * Il bianco/nero non c'è più (08/09/2026): l'arte esiste in una sola
 * variante, a colori, col nome piano — v. .agent/mascot-faces-plan.md, F9.
 */

const VISIBLE_KEY = 'jenny-mascotte-visible';
/* Chiave nuova rispetto a 'jenny-mascotte-side': il vecchio valore era una
   preferenza esplicita, e chi aveva scelto "destra" se la ritroverebbe come
   posizione di partenza di una feature che quella scelta non ce l'ha più.
   Ripartono tutti da sinistra; la chiave morta si ripulisce sotto. */
const SIDE_KEY = 'jenny-mascotte-dock-side';
const LEGACY_SIDE_KEY = 'jenny-mascotte-side';
const SIZE_KEY = 'jenny-mascotte-size';
/* Chiavi di preferenze ritirate. Si ripuliscono una volta per caricamento e
   non una per lettura: non hanno più un getter in cui nascondersi. */
const DEAD_KEYS = ['jenny-mascotte-color'];
for (const key of DEAD_KEYS) {
  try {
    localStorage.removeItem(key);
  } catch (_) {
    /* storage non disponibile */
  }
}

/** Lato del canvas quadrato per ogni taglia. Il default è 'sm'; la geometria
 *  in mobile-style.css deriva tutta da --jenny-size, quindi qui basta
 *  scrivere il pixel. */
export const MASCOT_SIZES = { sm: 120, md: 160, lg: 210 };

export function mascotVisible() {
  const v = localStorage.getItem(VISIBLE_KEY);
  if (v === null) return true; // default: visibile
  return v === '1';
}

export function setMascotVisible(on) {
  localStorage.setItem(VISIBLE_KEY, on ? '1' : '0');
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { visible: on, side: mascotSide() },
  }));
  return on;
}

export function mascotSide() {
  try {
    localStorage.removeItem(LEGACY_SIDE_KEY);
  } catch (_) {
    /* storage non disponibile */
  }
  const s = localStorage.getItem(SIDE_KEY);
  return s === 'right' ? 'right' : 'left'; // default: sinistra
}

/* Diversamente dalle altre preferenze NON emette 'mascotchange': lo scrive la
   companion mentre lei sta atterrando, e l'evento la farebbe passare da
   _applyMascotPrefs -> setMode -> _abortFlight, cioè ucciderebbe il volo
   nell'istante esatto in cui sceglie il bordo. La classe .side-left la
   applica direttamente chi chiama (v. mobile-jenny.js#_setSide). */
export function setMascotSide(side) {
  const normalized = side === 'right' ? 'right' : 'left';
  localStorage.setItem(SIDE_KEY, normalized);
  return normalized;
}

export function mascotSize() {
  const s = localStorage.getItem(SIZE_KEY);
  return s in MASCOT_SIZES ? s : 'sm'; // default: piccola
}

export function setMascotSize(size) {
  const normalized = size in MASCOT_SIZES ? size : 'sm';
  localStorage.setItem(SIZE_KEY, normalized);
  applyMascotSize();
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { visible: mascotVisible(), side: mascotSide(), size: normalized },
  }));
  return normalized;
}

/** Scrive la taglia attiva su <html> come --jenny-size. Da chiamare anche
 *  all'avvio: il default CSS copre solo la taglia di default. */
export function applyMascotSize() {
  const px = MASCOT_SIZES[mascotSize()];
  document.documentElement.style.setProperty('--jenny-size', `${px}px`);
  // La mascotte flottante è la stessa persona: prende di qui la sua taglia,
  // in px fisici, invece di averne una propria da tenere allineata a mano.
  // Il guscio nativo la ricorda, quindi vale anche se in questo momento è
  // spenta. Fuori dall'APK il ponte non c'è e non succede niente.
  try {
    window.JennyNative?.setMascotSize?.(px, window.devicePixelRatio || 1);
  } catch (_) {
    /* ponte assente */
  }
}

/* ── Al bordo, o venuta fuori ────────────────────────────────────────────────
   I due posti in cui Jenny sta ferma, e quanto del suo quadrato resta fuori
   dallo schermo in ciascuno. Non sono una preferenza — si toccano e cambiano,
   non si scelgono dalle impostazioni — ma stanno qui perché qui vive tutto il
   resto della sua geometria, e perché il numero deve esistere una volta sola:
   lo leggono i due fogli di stile per ancorarla e `mascot-drag.js` per sapere
   dove farla arrivare a piedi dopo un lancio. Cinque dichiarazioni CSS e una
   moltiplicazione, un numero solo.

   0.469 e 0.25 sono misurati sull'arte, non scelti: **in larghezza** il
   personaggio occupa il 45% centrale del canvas quadrato (bbox alpha dei webp
   impacchettati), quindi "al bordo" e "fuori" vogliono dire due scarti precisi
   e non due impressioni. In altezza il rapporto e' un altro — 73% — e
   confonderli e' un errore gia' fatto una volta, v. il commento sopra
   `.jenny-duo` in mobile-style.css. */
export const DOCK_RATIO = 0.469;
export const OUT_RATIO = 0.25;
/** Quanto si sposta l'ancoraggio passando da uno stato all'altro. */
export const OUT_SHIFT_RATIO = DOCK_RATIO - OUT_RATIO;

/** Porta i due ancoraggi al CSS, che di suo non sa moltiplicare costanti JS. */
export function applyDockAnchors() {
  const style = document.documentElement.style;
  style.setProperty('--jenny-dock', String(DOCK_RATIO));
  style.setProperty('--jenny-out', String(OUT_RATIO));
}

/* All'import e non nel costruttore delle due companion: `mobile-jenny.js`
   attacca lo sprite al documento *prima* di chiamare `applyMascotSize()`, e un
   `calc()` con una variabile che non esiste ancora non è "il valore di prima",
   è una dichiarazione invalida — Jenny comparirebbe per un frame dove la mette
   il flusso invece che sul bordo. Un modulo, invece, viene valutato prima che
   qualunque elemento esista. */
applyDockAnchors();
