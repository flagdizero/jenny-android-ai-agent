/** Preferenze della mascotte (JennyCompanion) — visibilità e aspetto.
 *
 * Stato puramente client-side (localStorage), come tema/lingua/modalità
 * avanzata: non passa mai dal backend. Visibilità e taglia sono scelte
 * dell'utente (Impostazioni → Personalizzazione).
 *
 * Il lato non c'è più (24/09/2026): Jenny sta **sempre a destra**, in casa e in
 * officina, e dopo un lancio ci torna a piedi da dovunque l'hai lasciata. A
 * sinistra il resto dell'interfaccia — testo, fumetti, riga di lavoro — le si
 * allineava male; e il lato non era una scelta, solo il ricordo dell'ultimo
 * lancio.
 * V. .agent/tre-ritocchi-plan.md, voce 1.
 *
 * Il bianco/nero non c'è più (08/09/2026): l'arte esiste in una sola
 * variante, a colori, col nome piano — v. .agent/mascot-faces-plan.md, F9.
 */

const VISIBLE_KEY = 'jenny-mascotte-visible';
const SIZE_KEY = 'jenny-mascotte-size';
/* Chiavi di preferenze ritirate. Si ripuliscono una volta per caricamento e
   non una per lettura: non hanno più un getter in cui nascondersi. Le due del
   lato: la prima era la scelta esplicita di un tempo, la seconda il ricordo di
   dove l'avevi lasciata. */
const DEAD_KEYS = [
  'jenny-mascotte-color',
  'jenny-mascotte-side',
  'jenny-mascotte-dock-side',
];
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
    detail: { visible: on },
  }));
  return on;
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
    detail: { visible: mascotVisible(), size: normalized },
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
   lo legge il foglio di stile per ancorarla (uno solo per i due gusci, da quando
   la Jenny e' una) e `mascot-drag.js` per sapere
   dove farla arrivare a piedi dopo un lancio. Due dichiarazioni CSS e una
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
/** Quanto del quadrato occupa il personaggio **in altezza**.
 *
 *  L'altro numero (45%) e' la larghezza, ed e' quello da cui vengono i due
 *  ancoraggi qui sopra: confonderli e' un errore gia' fatto una volta. Questo
 *  serve a chi deve lasciarle spazio — una pagina di impostazioni non puo'
 *  finire sotto di lei — e vale la pena che stia qui, accanto agli altri due,
 *  invece che scritto a mano dentro un `calc()`. */
export const ART_HEIGHT_RATIO = 0.73;

/** Porta i rapporti al CSS, che di suo non sa moltiplicare costanti JS. */
export function applyDockAnchors() {
  const style = document.documentElement.style;
  style.setProperty('--jenny-dock', String(DOCK_RATIO));
  style.setProperty('--jenny-out', String(OUT_RATIO));
  style.setProperty('--jenny-art-h', String(ART_HEIGHT_RATIO));
}

/* All'import e non nel costruttore delle due companion: `mobile-jenny.js`
   attacca lo sprite al documento *prima* di chiamare `applyMascotSize()`, e un
   `calc()` con una variabile che non esiste ancora non è "il valore di prima",
   è una dichiarazione invalida — Jenny comparirebbe per un frame dove la mette
   il flusso invece che sul bordo. Un modulo, invece, viene valutato prima che
   qualunque elemento esista. */
applyDockAnchors();
