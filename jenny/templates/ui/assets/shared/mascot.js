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
  document.documentElement.style.setProperty(
    '--jenny-size', `${MASCOT_SIZES[mascotSize()]}px`
  );
}
