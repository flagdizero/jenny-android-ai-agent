/** Preferenze della mascotte (JennyCompanion) — visibilità e aspetto.
 *
 * Stato puramente client-side (localStorage), come il tema: non passa mai
 * dal backend. Visibilità e taglia sono scelte dell'utente, nella stanza
 * «Jenny» della casa (`home-jenny.js`). Nasconderla vale solo per la casa: le
 * regole stanno in `home-style.css`, e l'officina non le carica.
 *
 * Il lato non c'è più (24/09/2026): Jenny sta **sempre a destra**, in casa e in
 * officina, e dopo un lancio ci torna a piedi da dovunque l'hai lasciata. A
 * sinistra il resto dell'interfaccia — testo, fumetti, riga di lavoro — le si
 * allineava male; e il lato non era una scelta, solo il ricordo dell'ultimo
 * lancio.
 *
 * Il bianco/nero non c'è più (08/09/2026): l'arte esiste in una sola
 * variante, a colori, col nome piano.
 */

/* Letture e scritture che non sollevano: con lo storage negato le preferenze
   tornano ai default e non si salvano, ma la mascotte c'e'.
   La regola di `readStorage` in `utils.js`, qui a mano
   perche' questo modulo non importa niente. */
function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) {
    /* storage non disponibile */
  }
}

const SIZE_KEY = 'jenny-mascot-scale';
const HIDDEN_KEY = 'jenny-mascot-hidden';
/* La taglia ha cambiato scala il 25/11/2026. Prima era `jenny-mascot-size`
   (e fino al 25/09/2026 `jenny-mascotte-size`) con 120/160/210 px; ora va da
   piccola a quella che era la media, con una nuova in mezzo: 120/140/160. Il
   valore cambia nome con lei, perche' `md` non vuol piu' dire la stessa cosa:
   chi l'aveva media la ritrova uguale, che adesso si chiama grande, e chi
   l'aveva grande scende alla grande nuova. Se la chiave nuova c'e' gia', vince
   lei: e' la scelta piu' recente. Le vecchie finiscono fra le chiavi morte. */
const OLD_SIZE_KEYS = ['jenny-mascot-size', 'jenny-mascotte-size'];
export const RESCALED_SIZES = { sm: 'sm', md: 'lg', lg: 'lg' };
try {
  if (localStorage.getItem(SIZE_KEY) === null) {
    for (const key of OLD_SIZE_KEYS) {
      const now = RESCALED_SIZES[localStorage.getItem(key)];
      if (now) {
        localStorage.setItem(SIZE_KEY, now);
        break;
      }
    }
  }
} catch (_) {
  /* storage non disponibile */
}
/* Chiavi di preferenze ritirate. Si ripuliscono una volta per caricamento e
   non una per lettura: non hanno più un getter in cui nascondersi. Stanno qui
   anche quelle che non erano della mascotte, perché questo modulo lo caricano
   tutti e due i gusci:
   - `jenny-mascotte-color`: il bianco/nero (08/09/2026);
   - `jenny-mascotte-dock-side`, `jenny-mascotte-side`: il lato (5961d22);
   - `jenny-mascot-size` e `jenny-mascotte-size`: la taglia nella scala di
     prima, portata in `SIZE_KEY` qui sopra (25/11/2026);
   - `jenny-mascot-visible` e il suo nome di prima `jenny-mascotte-visible`:
     «Mostra la mascotte», tolta il 05/10/2026 quando Jenny era l'unica porta
     della chat. E' tornata il 25/11/2026 come `HIDDEN_KEY`, con una chiave
     nuova: un «0» rimasto da allora non deve nasconderla di sorpresa;
   - `jenny-advanced-mode`: la modalità sviluppatore (78ff330);
   - `jenny-home-view`: la vista di Home scelta dall'utente (3d57980);
   - `locale`: il selettore di lingua dell'officina (v. shared/i18n.js). */
const DEAD_KEYS = [
  'jenny-mascotte-color',
  'jenny-mascotte-dock-side',
  'jenny-mascotte-side',
  ...OLD_SIZE_KEYS,
  'jenny-mascot-visible',
  'jenny-mascotte-visible',
  'jenny-advanced-mode',
  'jenny-home-view',
  'locale',
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
 *  scrivere il pixel. La grande e' la media di prima del 25/11/2026: a 210
 *  copriva troppo (v. `RESCALED_SIZES`). */
export const MASCOT_SIZES = { sm: 120, md: 140, lg: 160 };

/** Nascosta in casa? Il default e' no. */
export function mascotHidden() {
  return readStorage(HIDDEN_KEY) === '1';
}

/** Da nascosta la chat ha un'altra porta, la chiave in testa alla casa
 *  (`HomeApp._applyChatKey`): Jenny non e' piu' l'unica. */
export function setMascotHidden(hidden) {
  const on = Boolean(hidden);
  writeStorage(HIDDEN_KEY, on ? '1' : '0');
  applyMascotHidden();
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { hidden: on, size: mascotSize() },
  }));
  return on;
}

/** Scrive la preferenza su <html> come `data-jenny-hidden`, che legge solo
 *  `home-style.css`. Lo stesso attributo lo mette `bootstrap.js` al primo
 *  fotogramma, perche' non compaia per un istante. */
export function applyMascotHidden() {
  const root = document.documentElement;
  if (mascotHidden()) root.setAttribute('data-jenny-hidden', '');
  else root.removeAttribute?.('data-jenny-hidden');
}

export function mascotSize() {
  const s = readStorage(SIZE_KEY);
  return s in MASCOT_SIZES ? s : 'sm'; // default: piccola
}

export function setMascotSize(size) {
  const normalized = size in MASCOT_SIZES ? size : 'sm';
  writeStorage(SIZE_KEY, normalized);
  applyMascotSize();
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { hidden: mascotHidden(), size: normalized },
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
function applyDockAnchors() {
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
