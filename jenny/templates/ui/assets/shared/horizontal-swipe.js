/** Il gesto orizzontale, senza sapere cosa muove.
 *
 *  Nasce estraendo `MobileApp::setupSwipeNav`, che funzionava e le cui costanti
 *  erano gia' state pagate con delle misure. La casa deve fare **lo stesso
 *  gesto** per cambiare pagina, e una seconda copia sarebbe la solita macchina
 *  che impara le cose una volta sola.
 *
 *  **Cosa e' condiviso e cosa no.** Qui dentro sta il *riconoscimento*: quando
 *  un trascinamento e' orizzontale, quando appartiene a qualcun altro, e quando
 *  e' abbastanza per contare. Qui **non** sta la risposta visiva, perche' i due
 *  gusci ne hanno due diverse e non per capriccio:
 *
 *  - l'officina trascina **la vista corrente** con una sbirciata smorzata e un
 *    velo grigio; la vicina non viene mai disegnata;
 *  - la casa fa scorrere **una pista** con le pagine affiancate, e la vicina si
 *    vede entrare davvero (v. la tavola `Pagine`).
 *
 *  Mettere anche quella qui dentro avrebbe voluto dire un modulo con due
 *  modalita', cioe' due moduli scritti nello stesso file.
 */

/** **Il dito si misura contro lo schermo, non contro la finestra.**
 *
 *  `clientX` e' relativo alla finestra di chi ascolta. Per l'officina e per il
 *  guscio della casa e' lo stesso: la loro finestra non si muove mai, si muove
 *  solo la pista dentro. Ma dentro una Jenny App la finestra **e'** la cornice
 *  che la pista sta trascinando: il righello si sposta insieme al dito, e
 *  quello che ne esce e' un anello di reazione.
 *
 *  Misurato su Chrome del telefono il 22/09/2026, uno scorrimento solo, i due
 *  righelli fianco a fianco dentro una cornice trascinata:
 *
 *      dx client=237  dx screen=284     dx client=257  dx screen=320
 *      dx client=268  dx screen=296     dx client=302  dx screen=328
 *      dx client=243  dx screen=300     dx client=271  dx screen=336
 *      dx client=288  dx screen=311     dx client=309  dx screen=340
 *
 *  Il primo va avanti e indietro — ed e' letteralmente la schermata che
 *  vibra, come l'ha vista l'utente; il secondo sale dritto. Lo scorrimento
 *  era di 850 punti fisici su un dispositivo a 2,5: 340. Cioe' `screenX` e'
 *  in pixel CSS **come `clientX`**, e le soglie qui sotto — che si
 *  confrontano con `clientWidth` — conservano il significato che avevano.
 *
 *  Una regola sola per tutti e tre i posti, e non due con un'eccezione: il
 *  giorno che qualcun altro trascina la cornice che lo contiene, non deve
 *  riscoprirlo da capo. Resta vero finche' non si muove la **finestra** a meta'
 *  gesto, cosa che nessuno dei tre fa.
 */

/** 24px, non 10.
 *
 *  Il touch slop di Android e' ~8dp (≈20-24px reali), e sotto quella soglia
 *  `preventDefault()` cade dentro la finestra in cui Chromium sta ancora
 *  decidendo se la pressione e' un long-press — che a quel punto viene
 *  scartato, e la selezione di testo non si apre piu'.
 */
export const AXIS_THRESHOLD = 24;

/** Oltre quanto, in px, il gesto conta come cambio. */
export function confirmThreshold(width) {
  return Math.max(60, width * 0.22);
}

/** ...oppure quanto veloce, in px/ms: un colpetto corto ma deciso vale. */
export const CONFIRM_SPEED = 0.5;

/** Elastico esponenziale: reattivo vicino a 0, frena verso `max`. */
export function elastic(delta, max) {
  if (!max) return 0;
  const mark = delta < 0 ? -1 : 1;
  return mark * max * (1 - Math.exp(-Math.abs(delta) / (max * 1.8)));
}

/** C'e' uno scorrevole orizzontale sotto il dito? Allora il gesto e' suo, non
 *  del carosello — **anche se e' gia' al bordo**.
 *
 *  Fino al 23/09/2026 cedeva solo se lo scorrevole poteva ancora scorrere in
 *  quel verso, come fa Android fra scorrevoli annidati. L'utente l'ha visto
 *  rompersi sulla striscia dei temi in Impostazioni, e l'ha registrato: la
 *  striscia sta all'inizio, il dito va prima a destra — «di la' non c'e'
 *  niente», quindi il gesto passa alla pagina — e poi torna a sinistra, e la
 *  pagina lo segue invece della striscia. Il verso del primo movimento non
 *  dice cosa vuole il dito; il posto in cui si appoggia si'. La regola
 *  dell'utente e' che sopra un componente che scorre di lato vince lui.
 *
 *  E c'era un secondo modo di rompersi, piu' nascosto: una striscia che sfora
 *  di poco arriva al bordo **dentro** i primi 24px, prima che l'asse sia
 *  deciso. A quel punto «non puo' piu' scorrere», la pagina si arma, ma il
 *  browser ha gia' cominciato a scorrere la striscia e il nostro
 *  `preventDefault` non vale piu': si muovevano tutte e due.
 *
 *  Conta solo chi sfora davvero: un contenitore `overflow-x: auto` in cui
 *  tutto ci sta — una tabella stretta in chat — non si tiene niente.
 */
export function insideHorizontalScrollable(target, boundary) {
  let el = target;
  while (el && el !== boundary && el !== document.body) {
    if (el.scrollWidth > el.clientWidth + 2) {
      const overflowX = getComputedStyle(el).overflowX;
      if (overflowX === 'auto' || overflowX === 'scroll') return true;
    }
    el = el.parentElement;
  }
  return false;
}

/** **Di lato e' scorrimento, un tocco e' un tocco — tranne dove il componente
 *  sotto il dito si trascina di lato: li' vince lui.** E' la regola dell'utente
 *  (23/09/2026), e prima di lei ce n'era una sola, lo scorrevole nativo qui
 *  sopra. Tutto quello che una app fa col suo codice non si vedeva, e il dito
 *  muoveva tutte e due le cose insieme.
 *
 *  Chi si trascina di lato si riconosce da quel che **dichiara** — uno
 *  scorrevole, un cursore a slitta, un `touch-action` che l'orizzontale non lo
 *  lascia al browser — e da quel che **fa** mentre il dito si muove (v. il
 *  `defaultPrevented` in `watchHorizontalSwipe`). Mai da quel che fa quando
 *  il dito si appoggia: un bottone reagisce subito anche lui, e se bastasse
 *  quello una app piena di bottoni sarebbe una trappola da cui non si esce.
 *
 *  Per lo stesso motivo un `touch-action: none` **su un comando** non conta.
 *  Life Counter lo mette sui suoi − e + perche' tenerli premuti non faccia
 *  scorrere la pagina, non perche' ci si trascini sopra: sono bottoni.
 */
export function componentSwipe(target, boundary) {
  if (insideHorizontalScrollable(target, boundary)) return true;
  /* Fino in cima, `body` compreso: dentro una app il confine e' la finestra,
     e un gioco che si prende tutto lo schermo lo dice proprio li'. */
  let el = target;
  while (el && el !== boundary) {
    if (el.tagName === 'INPUT' && el.type === 'range') return true;
    if (claimsHorizontal(el) && !isCommand(el)) return true;
    el = el.parentElement;
  }
  return false;
}

/** Il `touch-action` di `el` si tiene lo scorrimento orizzontale? `none`,
 *  `pan-y`, `pinch-zoom` si'; `auto` e `manipulation` lo lasciano al browser,
 *  e cosi' ogni valore che nomina un pan orizzontale. Non si eredita: per
 *  questo `componentSwipe` risale. */
export function claimsHorizontal(el) {
  const value = getComputedStyle(el).touchAction;
  if (!value || value === 'auto' || value === 'manipulation') return false;
  return !/pan-(x|left|right)/.test(value);
}

/** **Mentre la pagina scorre di lato, niente scorre su e giu'.** Lo chiede
 *  l'utente (23/09/2026), e il `preventDefault` sul `touchmove` da solo non
 *  basta: l'asse si decide a 24px (v. `AXIS_THRESHOLD`), ma il browser comincia a
 *  scorrere in verticale gia' a ~8. Da li' i suoi `touchmove` non sono piu'
 *  annullabili, e il filo sotto seguiva il dito in su e in giu' insieme alla
 *  pagina che andava di lato.
 *
 *  Uno scorrevole con `overflow-y: hidden` invece non si fa scorrere dal dito,
 *  nemmeno a gesto iniziato. Si bloccano quelli sotto il dito — piu' la pagina
 *  intera, che dentro una app e' lo scorrevole di tutto — e si liberano al
 *  rilascio, ciascuno col valore che aveva. La posizione non cambia: `hidden`
 *  toglie lo scorrimento al dito, non lo `scrollTop`.
 *
 *  @returns {() => void} per liberarli.
 */
export function lockVertical(target) {
  const blocked = [];
  const lock = (el) => {
    if (!el?.style || blocked.some((b) => b.el === el)) return;
    if (!(el.scrollHeight > el.clientHeight + 1)) return;
    blocked.push({ el, before: el.style.overflowY });
    el.style.overflowY = 'hidden';
  };
  for (let el = target; el && el !== document.body; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') lock(el);
  }
  lock(document.scrollingElement);
  return () => {
    for (const { el, before } of blocked) el.style.overflowY = before;
  };
}

/** Un elemento che si tocca per premere, non per trascinare. */
export function isCommand(el) {
  if (/^(BUTTON|A|LABEL|SELECT|SUMMARY|INPUT)$/.test(el.tagName || '')) return true;
  const role = el.getAttribute?.('role') || '';
  return /^(button|link|switch|tab|checkbox|radio|menuitem|option)$/.test(role);
}

/** C'e' del testo selezionato? Trascinare per aggiustarne i manici non deve
 *  far scivolare niente sotto le dita. */
export function selectedText() {
  const sel = globalThis.getSelection?.();
  return Boolean(sel && !sel.isCollapsed && String(sel));
}

/** Aggancia il riconoscimento a `element` e richiama il guscio.
 *
 *  I richiami, nell'ordine in cui possono arrivare:
 *
 *  - `canStart()` — le guardie del guscio piu' la sua preparazione. `false`
 *    e il dito viene ignorato del tutto.
 *  - `onHorizontal()` — l'asse e' deciso: da qui in poi il gesto e' nostro, e
 *    il guscio prepara l'elemento che muovera'.
 *  - `onDrag(dx, width)` — a ogni movimento.
 *  - `onEnd({direction, confirm, dx, width})` — `direction` e' `'prev'` (dito a
 *    destra) o `'next'`; `confirm` dice se ha superato spazio **o** velocita'.
 *  - `onCancel()` — il sistema si e' ripreso il gesto a meta'.
 *
 *  Una pressione lunga **non** sta qui. C'e' stata, dal 22 al 23/09/2026, per
 *  un chiamante solo — i pallini della casa, che aprivano il foglio delle
 *  pagine — ed e' uscita con lui: le pagine ora si appendono dal cassetto e
 *  dalla tendina, dove la pressione lunga la fa `shared/longpress.js`.
 *
 *  `onEnd` e `onCancel` arrivano **solo** se l'asse era stato deciso: un
 *  tocco che non diventa mai orizzontale non deve far ridisegnare niente.
 *
 *  `exclusive` e' per chi ascolta **sopra il contenuto di qualcun altro** — il
 *  kit, dentro una Jenny App. Quando il gesto diventa nostro, l'app riceve un
 *  annullo (`pointercancel` e `touchcancel`) e da li' al rilascio non sente piu'
 *  il dito: e' quel che fa Android col suo ACTION_CANCEL quando un genitore si
 *  prende lo scorrimento. Senza, l'app continuava il suo gesto sotto la pagina
 *  che scorreva — Life Counter contava la vita a ripetizione, perche' il suo
 *  «tieni premuto» non sapeva che il dito era gia' altrove.
 *
 *  @returns {() => void} per staccarlo.
 */
export function watchHorizontalSwipe(element, {
  canStart,
  onHorizontal,
  onDrag,
  onEnd,
  onCancel,
  exclusive = false,
} = {}) {
  let startX = 0;
  let startY = 0;
  let startT = 0;
  let tracking = false;     // un gesto candidato e' in corso
  let horizontal = false;   // l'asse e' stato deciso
  let target = null;
  let pointer = null;      // l'id del puntatore del dito, per annullarlo (esclusivo)
  let synthetic = false;     // stiamo mandando noi l'annullo: non e' un evento vero

  let free = null;         // libera gli scorrevoli verticali bloccati

  const reset = () => {
    tracking = false;
    horizontal = false;
    target = null;
    free?.();
    free = null;
  };

  const width = () => element.clientWidth || window.innerWidth;

  const down = (e) => {
    /* Un dito che scende mentre il gesto era gia' nostro — il secondo di un
       pizzico, di solito — lo chiude. Qui c'era un `reset()` nudo: il gesto
       spariva senza `onCancel`, e il guscio restava con la vista (o la pista)
       ferma a meta', dove l'aveva lasciata l'ultimo `onDrag`. */
    const wasHorizontal = horizontal;
    reset();
    if (wasHorizontal) onCancel?.();
    if (e.touches.length !== 1) return;
    if (selectedText()) return;
    if (canStart && canStart() === false) return;
    const t = e.touches[0];
    startX = t.screenX;
    startY = t.screenY;
    startT = Date.now();
    target = e.target;
    tracking = true;
  };

  const move = (e) => {
    if (!tracking) return;
    /* Un secondo dito e' un pizzico, non uno scorrimento. */
    if (e.touches.length > 1) { cancel(); return; }
    const t = e.touches[0];
    const dx = t.screenX - startX;
    const dy = t.screenY - startY;

    if (!horizontal) {
      /* **Quel che il componente fa mentre il dito si muove.** Chi si trascina
         col suo codice blocca lo scorrimento del browser col `preventDefault`,
         e lo fa prima di noi: si ascolta in risalita, dopo di lui. Un bottone
         non lo fa mai — e' questa la differenza, non chi reagisce per primo. */
      if (e.defaultPrevented) { reset(); return; }
      if (Math.abs(dx) < AXIS_THRESHOLD && Math.abs(dy) < AXIS_THRESHOLD) return;
      /* Dominanza orizzontale vera: un trascinamento diagonale (tipico di chi
         aggiusta una selezione) non arma il gesto. */
      if (Math.abs(dx) <= Math.abs(dy) * 1.5) { reset(); return; }
      if (componentSwipe(target, element)) { reset(); return; }
      horizontal = true;
      free = lockVertical(target);
      if (exclusive) cancelForTheOther(e);
      onHorizontal?.();
    }

    e.preventDefault(); // il gesto e' nostro (l'ascolto e' passive:false)
    onDrag?.(dx, width());
  };

  const su = (e) => {
    if (!tracking) return;
    const wasHorizontal = horizontal;
    const changed = (e.changedTouches && e.changedTouches[0]) || null;
    const dx = (changed ? changed.screenX : startX) - startX;
    const dt = Math.max(1, Date.now() - startT);
    const vx = dx / dt;
    const w = width();

    reset();
    if (!wasHorizontal) return;

    onEnd?.({
      direction: dx > 0 ? 'prev' : 'next',
      confirm: Math.abs(dx) > confirmThreshold(w) || Math.abs(vx) > CONFIRM_SPEED,
      dx,
      width: w,
    });
  };

  const cancel = () => {
    if (synthetic) return;
    const wasHorizontal = horizontal;
    reset();
    if (wasHorizontal) onCancel?.();
  };

  /** Il gesto e' nostro: chi c'e' sotto riceve l'annullo e si ferma. Un
   *  costruttore che manca, o che rifiuta i suoi argomenti, non deve fermare
   *  lo scorrimento: l'annullo e' una cortesia, il gesto no. */
  const cancelForTheOther = (e) => {
    if (!target?.dispatchEvent) return;
    synthetic = true;
    try {
      if (pointer !== null && typeof PointerEvent === 'function') {
        target.dispatchEvent(new PointerEvent('pointercancel', {
          bubbles: true, pointerId: pointer, pointerType: 'touch', isPrimary: true,
        }));
      }
      if (typeof TouchEvent === 'function') {
        target.dispatchEvent(new TouchEvent('touchcancel', {
          bubbles: true, touches: [], targetTouches: [],
          changedTouches: Array.from(e.touches || []),
        }));
      }
    } catch {
      /* v. sopra */
    } finally {
      synthetic = false;
    }
  };

  /** Da quando il gesto e' nostro al rilascio, il dito non arriva piu' a chi
   *  sta sotto. Si ferma **in discesa, sulla finestra** — prima di chiunque —
   *  e il riconoscimento lo si fa girare da qui: fermato, non arriverebbe
   *  nemmeno a noi, che ascoltiamo in risalita. */
  const hold = (e) => {
    if (synthetic || !horizontal) return;
    e.stopImmediatePropagation();
    if (e.type === 'touchmove') move(e);
    else if (e.type === 'touchend') su(e);
    else if (e.type === 'touchcancel') cancel();
  };
  const markPointer = (e) => {
    if (e.isPrimary) pointer = e.pointerId;
  };
  const TO_HOLD = [
    'touchmove', 'touchend', 'touchcancel', 'pointermove', 'pointerup', 'pointercancel',
  ];

  element.addEventListener('touchstart', down, { passive: true });
  element.addEventListener('touchmove', move, { passive: false });
  element.addEventListener('touchend', su, { passive: true });
  element.addEventListener('touchcancel', cancel, { passive: true });
  if (exclusive) {
    window.addEventListener('pointerdown', markPointer, { capture: true, passive: true });
    for (const type of TO_HOLD) {
      window.addEventListener(type, hold, { capture: true, passive: false });
    }
  }

  return () => {
    element.removeEventListener('touchstart', down);
    element.removeEventListener('touchmove', move);
    element.removeEventListener('touchend', su);
    element.removeEventListener('touchcancel', cancel);
    if (exclusive) {
      window.removeEventListener('pointerdown', markPointer, { capture: true });
      for (const type of TO_HOLD) {
        window.removeEventListener(type, hold, { capture: true });
      }
    }
  };
}
