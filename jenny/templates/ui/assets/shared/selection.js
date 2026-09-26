/** Lo stato della selezione di testo, per chi deve smettere di intralciarla.
 *
 * Tre domande e nessuno stato tenuto: `getSelection()` costa O(1) e ricalcolare
 * ogni volta evita una classe intera di bug da stallo — una selezione
 * cancellata insieme ai suoi nodi non garantisce un `selectionchange`, e un
 * latch resterebbe alzato su una selezione che non esiste più.
 */

/* Il range vivo, o `null` se non c'è niente di selezionato. Mai
   `sel.toString()`: su una selezione lunga è O(n) e questo viene chiesto a ogni
   frame di streaming. `isCollapsed` basta e costa niente. */
function activeRange() {
  const sel = document.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  return sel.getRangeAt(0);
}

/* Il composer è una `<textarea>`: una selezione lasciata lì dentro non è una
   selezione di lettura, e bloccherebbe rendering e autoscroll a tempo
   indeterminato. */
function inEditableField() {
  const el = document.activeElement;
  if (!el) return false;
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable === true;
}

/** C'è una selezione di lettura in corso da qualche parte nella pagina? */
export function hasSelection() {
  if (inEditableField()) return false;
  return activeRange() !== null;
}

/** …e quella selezione sta dentro `el`? */
export function selectionInside(el) {
  if (!el || inEditableField()) return false;
  const range = activeRange();
  return !!range && el.contains(range.commonAncestorContainer);
}

/** Notifica a ogni cambio di selezione, con lo stato già risolto.
 *  Torna la funzione che stacca il listener. */
export function onSelectionChange(fn) {
  const handler = () => fn(hasSelection());
  document.addEventListener('selectionchange', handler);
  return () => document.removeEventListener('selectionchange', handler);
}

/** Chiude la selezione di testo, ovunque sia nella pagina. */
export function clearSelection() {
  document.getSelection()?.removeAllRanges();
}

/* ── La selezione non sopravvive all'uscita dalla finestra ───────────────────
   Quando la finestra perde il fuoco (un'altra app davanti, il selettore file,
   Impostazioni) Chromium nasconde la barra Copia/Condividi ma si tiene il fatto
   che *c'e'* una selezione, e al rientro la rimostra
   (`restoreSelectionPopupsIfNecessary`). Nel frattempo la pagina, nascosta e
   senza fotogrammi, ha spostato o tolto il DOM sotto quella selezione: `goHome`
   cambia vista e conversazione, il rientro rimette il fuoco nel composer. Il
   lato nativo non lo sa — i limiti della selezione gli arrivano solo coi
   fotogrammi — e la barra riappariva sopra il composer senza niente di
   evidenziato. Visto sul Titan 2 in casa e in officina, a ogni ritorno con Home
   o dal selettore file.

   `blur` arriva mentre la vista e' ancora visibile: un fotogramma con la
   selezione chiusa fa in tempo a partire, e al rientro non c'e' niente da
   ripristinare. Torna la funzione che stacca il listener. */
export function releaseSelectionOnBlur(win = window) {
  win.addEventListener('blur', clearSelection);
  return () => win.removeEventListener('blur', clearSelection);
}

/* ── La chrome esce dal hit-test finché c'è una selezione ───────────────────
   Al tocco di un manico Chromium ri-deriva l'estremo *fermo* della selezione
   con un hit-test dalle sue coordinate di schermo (`OnDragBegin` →
   `SelectBetweenCoordinates`). Se in quel punto c'è il composer o il dock,
   vincono loro: la selezione salta sulla chrome o collassa. Con
   `pointer-events: none` la chrome non partecipa al hit-test e il motore
   ritrova da solo il testo che ci sta sotto — misurato con tre pagine di
   prova in .agent/selection-rig (pagina C). La classe la mette questo modulo,
   la regola sta in mobile-style.css. */
export const SELECTING_CLASS = 'has-selection';

/** Tiene `has-selection` su `<html>` allineata allo stato della selezione.
    Torna la funzione che stacca il listener. */
export function exposeSelectionState(root = document.documentElement) {
  const apply = (active) => root.classList.toggle(SELECTING_CLASS, active);
  apply(hasSelection());
  return onSelectionChange(apply);
}

/* Un tocco è un tap se il dito non si è spostato più di così (px CSS). Sotto
   c'è lo slop di Android (8dp): un movimento maggiore è uno scroll, e lo
   scroll deve restare nativo. */
const TAP_SLOP = 12;

/* Il hit-test a classe spenta: chi c'è *davvero* sotto il dito, chrome
   compresa. La classe torna su subito dopo, qualunque cosa succeda. */
function chromeUnder(root, x, y, chromeSelector) {
  root.classList.remove(SELECTING_CLASS);
  let el = null;
  try {
    el = document.elementFromPoint(x, y);
  } finally {
    root.classList.add(SELECTING_CLASS);
  }
  return el && el.closest(chromeSelector) ? el : null;
}

/* Cosa voleva il dito: scrivere, se è caduto su un campo; premere, per tutto
   il resto (il bottone che contiene il punto, o l'elemento stesso). */
function deliverTap(el) {
  const field = el.closest('textarea, input, [contenteditable]');
  if (field) {
    field.focus();
    return;
  }
  const control = el.closest('button, [role="button"], .dock-item, a[href]');
  (control || el).click();
}

/** Il prezzo della trasparenza, ripagato: con una selezione attiva un tap sul
    composer o sul dock arriverebbe al testo sotto (e chiuderebbe solo la
    selezione). Qui il tap viene riconosciuto sul `touchend`, il click nativo
    che finirebbe sotto viene annullato, la selezione chiusa e il tap
    consegnato al bersaglio vero. Solo con la classe su, solo per bersagli
    dentro `selectors`, solo per un tap (un trascinamento resta uno scroll).
    Torna la funzione che smonta il tutto. */
export function forwardTapsThroughChrome(selectors, root = document.documentElement) {
  const chromeSelector = selectors.join(', ');
  let pending = null;

  const onStart = (e) => {
    pending = null;
    if (e.touches.length !== 1 || !root.classList.contains(SELECTING_CLASS)) return;
    const t = e.touches[0];
    const target = chromeUnder(root, t.clientX, t.clientY, chromeSelector);
    if (target) pending = { target, x: t.clientX, y: t.clientY };
  };
  const onEnd = (e) => {
    const p = pending;
    pending = null;
    if (!p) return;
    const t = e.changedTouches && e.changedTouches[0];
    if (t && (Math.abs(t.clientX - p.x) > TAP_SLOP || Math.abs(t.clientY - p.y) > TAP_SLOP)) return;
    // Annulla il click sintetico che il browser manderebbe al testo sotto la
    // chrome (un "Copia" di una bolla, ad esempio).
    e.preventDefault();
    document.getSelection()?.removeAllRanges();
    deliverTap(p.target);
  };

  document.addEventListener('touchstart', onStart, { passive: true, capture: true });
  document.addEventListener('touchend', onEnd, { passive: false, capture: true });
  document.addEventListener('touchcancel', () => { pending = null; }, { passive: true, capture: true });
  return () => {
    document.removeEventListener('touchstart', onStart, { capture: true });
    document.removeEventListener('touchend', onEnd, { capture: true });
  };
}
