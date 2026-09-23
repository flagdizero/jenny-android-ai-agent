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
export const SOGLIA_ASSE = 24;

/** Oltre quanto, in px, il gesto conta come cambio. */
export function sogliaConferma(larghezza) {
  return Math.max(60, larghezza * 0.22);
}

/** ...oppure quanto veloce, in px/ms: un colpetto corto ma deciso vale. */
export const VELOCITA_CONFERMA = 0.5;

/** Elastico esponenziale: reattivo vicino a 0, frena verso `max`. */
export function elastico(delta, max) {
  if (!max) return 0;
  const segno = delta < 0 ? -1 : 1;
  return segno * max * (1 - Math.exp(-Math.abs(delta) / (max * 1.8)));
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
export function dentroScorrevoleOrizzontale(bersaglio, confine) {
  let el = bersaglio;
  while (el && el !== confine && el !== document.body) {
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
 *  `defaultPrevented` in `osservaGestoOrizzontale`). Mai da quel che fa quando
 *  il dito si appoggia: un bottone reagisce subito anche lui, e se bastasse
 *  quello una app piena di bottoni sarebbe una trappola da cui non si esce.
 *
 *  Per lo stesso motivo un `touch-action: none` **su un comando** non conta.
 *  Life Counter lo mette sui suoi − e + perche' tenerli premuti non faccia
 *  scorrere la pagina, non perche' ci si trascini sopra: sono bottoni.
 */
export function gestoDiUnComponente(bersaglio, confine) {
  if (dentroScorrevoleOrizzontale(bersaglio, confine)) return true;
  /* Fino in cima, `body` compreso: dentro una app il confine e' la finestra,
     e un gioco che si prende tutto lo schermo lo dice proprio li'. */
  let el = bersaglio;
  while (el && el !== confine) {
    if (el.tagName === 'INPUT' && el.type === 'range') return true;
    if (tieneOrizzontale(el) && !eUnComando(el)) return true;
    el = el.parentElement;
  }
  return false;
}

/** Il `touch-action` di `el` si tiene lo scorrimento orizzontale? `none`,
 *  `pan-y`, `pinch-zoom` si'; `auto` e `manipulation` lo lasciano al browser,
 *  e cosi' ogni valore che nomina un pan orizzontale. Non si eredita: per
 *  questo `gestoDiUnComponente` risale. */
export function tieneOrizzontale(el) {
  const valore = getComputedStyle(el).touchAction;
  if (!valore || valore === 'auto' || valore === 'manipulation') return false;
  return !/pan-(x|left|right)/.test(valore);
}

/** **Mentre la pagina scorre di lato, niente scorre su e giu'.** Lo chiede
 *  l'utente (23/09/2026), e il `preventDefault` sul `touchmove` da solo non
 *  basta: l'asse si decide a 24px (v. `SOGLIA_ASSE`), ma il browser comincia a
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
export function bloccaVerticali(bersaglio) {
  const bloccati = [];
  const blocca = (el) => {
    if (!el?.style || bloccati.some((b) => b.el === el)) return;
    if (!(el.scrollHeight > el.clientHeight + 1)) return;
    bloccati.push({ el, prima: el.style.overflowY });
    el.style.overflowY = 'hidden';
  };
  for (let el = bersaglio; el && el !== document.body; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') blocca(el);
  }
  blocca(document.scrollingElement);
  return () => {
    for (const { el, prima } of bloccati) el.style.overflowY = prima;
  };
}

/** Un elemento che si tocca per premere, non per trascinare. */
export function eUnComando(el) {
  if (/^(BUTTON|A|LABEL|SELECT|SUMMARY|INPUT)$/.test(el.tagName || '')) return true;
  const ruolo = el.getAttribute?.('role') || '';
  return /^(button|link|switch|tab|checkbox|radio|menuitem|option)$/.test(ruolo);
}

/** C'e' del testo selezionato? Trascinare per aggiustarne i manici non deve
 *  far scivolare niente sotto le dita. */
export function testoSelezionato() {
  const sel = globalThis.getSelection?.();
  return Boolean(sel && !sel.isCollapsed && String(sel));
}

/** Aggancia il riconoscimento a `elemento` e richiama il guscio.
 *
 *  I richiami, nell'ordine in cui possono arrivare:
 *
 *  - `puoIniziare()` — le guardie del guscio piu' la sua preparazione. `false`
 *    e il dito viene ignorato del tutto.
 *  - `onOrizzontale()` — l'asse e' deciso: da qui in poi il gesto e' nostro, e
 *    il guscio prepara l'elemento che muovera'.
 *  - `onTrascina(dx, larghezza)` — a ogni movimento.
 *  - `onFine({verso, conferma, dx, larghezza})` — `verso` e' `'prev'` (dito a
 *    destra) o `'next'`; `conferma` dice se ha superato spazio **o** velocita'.
 *  - `onAnnulla()` — il sistema si e' ripreso il gesto a meta'.
 *
 *  Una pressione lunga **non** sta qui. C'e' stata, dal 22 al 23/09/2026, per
 *  un chiamante solo — i pallini della casa, che aprivano il foglio delle
 *  pagine — ed e' uscita con lui: le pagine ora si appendono dal cassetto e
 *  dalla tendina, dove la pressione lunga la fa `shared/longpress.js`.
 *
 *  `onFine` e `onAnnulla` arrivano **solo** se l'asse era stato deciso: un
 *  tocco che non diventa mai orizzontale non deve far ridisegnare niente.
 *
 *  `esclusivo` e' per chi ascolta **sopra il contenuto di qualcun altro** — il
 *  kit, dentro una Jenny App. Quando il gesto diventa nostro, l'app riceve un
 *  annullo (`pointercancel` e `touchcancel`) e da li' al rilascio non sente piu'
 *  il dito: e' quel che fa Android col suo ACTION_CANCEL quando un genitore si
 *  prende lo scorrimento. Senza, l'app continuava il suo gesto sotto la pagina
 *  che scorreva — Life Counter contava la vita a ripetizione, perche' il suo
 *  «tieni premuto» non sapeva che il dito era gia' altrove.
 *
 *  @returns {() => void} per staccarlo.
 */
export function osservaGestoOrizzontale(elemento, {
  puoIniziare,
  onOrizzontale,
  onTrascina,
  onFine,
  onAnnulla,
  esclusivo = false,
} = {}) {
  let partenzaX = 0;
  let partenzaY = 0;
  let partenzaT = 0;
  let inAscolto = false;     // un gesto candidato e' in corso
  let orizzontale = false;   // l'asse e' stato deciso
  let bersaglio = null;
  let puntatore = null;      // l'id del puntatore del dito, per annullarlo (esclusivo)
  let sintetico = false;     // stiamo mandando noi l'annullo: non e' un evento vero

  let libera = null;         // libera gli scorrevoli verticali bloccati

  const azzera = () => {
    inAscolto = false;
    orizzontale = false;
    bersaglio = null;
    libera?.();
    libera = null;
  };

  const larghezza = () => elemento.clientWidth || window.innerWidth;

  const giu = (e) => {
    azzera();
    if (e.touches.length !== 1) return;
    if (testoSelezionato()) return;
    if (puoIniziare && puoIniziare() === false) return;
    const t = e.touches[0];
    partenzaX = t.screenX;
    partenzaY = t.screenY;
    partenzaT = Date.now();
    bersaglio = e.target;
    inAscolto = true;
  };

  const muove = (e) => {
    if (!inAscolto) return;
    /* Un secondo dito e' un pizzico, non uno scorrimento. */
    if (e.touches.length > 1) { annulla(); return; }
    const t = e.touches[0];
    const dx = t.screenX - partenzaX;
    const dy = t.screenY - partenzaY;

    if (!orizzontale) {
      /* **Quel che il componente fa mentre il dito si muove.** Chi si trascina
         col suo codice blocca lo scorrimento del browser col `preventDefault`,
         e lo fa prima di noi: si ascolta in risalita, dopo di lui. Un bottone
         non lo fa mai — e' questa la differenza, non chi reagisce per primo. */
      if (e.defaultPrevented) { azzera(); return; }
      if (Math.abs(dx) < SOGLIA_ASSE && Math.abs(dy) < SOGLIA_ASSE) return;
      /* Dominanza orizzontale vera: un trascinamento diagonale (tipico di chi
         aggiusta una selezione) non arma il gesto. */
      if (Math.abs(dx) <= Math.abs(dy) * 1.5) { azzera(); return; }
      if (gestoDiUnComponente(bersaglio, elemento)) { azzera(); return; }
      orizzontale = true;
      libera = bloccaVerticali(bersaglio);
      if (esclusivo) annullaPerLAltro(e);
      onOrizzontale?.();
    }

    e.preventDefault(); // il gesto e' nostro (l'ascolto e' passive:false)
    onTrascina?.(dx, larghezza());
  };

  const su = (e) => {
    if (!inAscolto) return;
    const eraOrizzontale = orizzontale;
    const cambiato = (e.changedTouches && e.changedTouches[0]) || null;
    const dx = (cambiato ? cambiato.screenX : partenzaX) - partenzaX;
    const dt = Math.max(1, Date.now() - partenzaT);
    const vx = dx / dt;
    const w = larghezza();

    azzera();
    if (!eraOrizzontale) return;

    onFine?.({
      verso: dx > 0 ? 'prev' : 'next',
      conferma: Math.abs(dx) > sogliaConferma(w) || Math.abs(vx) > VELOCITA_CONFERMA,
      dx,
      larghezza: w,
    });
  };

  const annulla = () => {
    if (sintetico) return;
    const eraOrizzontale = orizzontale;
    azzera();
    if (eraOrizzontale) onAnnulla?.();
  };

  /** Il gesto e' nostro: chi c'e' sotto riceve l'annullo e si ferma. Un
   *  costruttore che manca, o che rifiuta i suoi argomenti, non deve fermare
   *  lo scorrimento: l'annullo e' una cortesia, il gesto no. */
  const annullaPerLAltro = (e) => {
    if (!bersaglio?.dispatchEvent) return;
    sintetico = true;
    try {
      if (puntatore !== null && typeof PointerEvent === 'function') {
        bersaglio.dispatchEvent(new PointerEvent('pointercancel', {
          bubbles: true, pointerId: puntatore, pointerType: 'touch', isPrimary: true,
        }));
      }
      if (typeof TouchEvent === 'function') {
        bersaglio.dispatchEvent(new TouchEvent('touchcancel', {
          bubbles: true, touches: [], targetTouches: [],
          changedTouches: Array.from(e.touches || []),
        }));
      }
    } catch {
      /* v. sopra */
    } finally {
      sintetico = false;
    }
  };

  /** Da quando il gesto e' nostro al rilascio, il dito non arriva piu' a chi
   *  sta sotto. Si ferma **in discesa, sulla finestra** — prima di chiunque —
   *  e il riconoscimento lo si fa girare da qui: fermato, non arriverebbe
   *  nemmeno a noi, che ascoltiamo in risalita. */
  const trattieni = (e) => {
    if (sintetico || !orizzontale) return;
    e.stopImmediatePropagation();
    if (e.type === 'touchmove') muove(e);
    else if (e.type === 'touchend') su(e);
    else if (e.type === 'touchcancel') annulla();
  };
  const segnaPuntatore = (e) => {
    if (e.isPrimary) puntatore = e.pointerId;
  };
  const DA_TRATTENERE = [
    'touchmove', 'touchend', 'touchcancel', 'pointermove', 'pointerup', 'pointercancel',
  ];

  elemento.addEventListener('touchstart', giu, { passive: true });
  elemento.addEventListener('touchmove', muove, { passive: false });
  elemento.addEventListener('touchend', su, { passive: true });
  elemento.addEventListener('touchcancel', annulla, { passive: true });
  if (esclusivo) {
    window.addEventListener('pointerdown', segnaPuntatore, { capture: true, passive: true });
    for (const tipo of DA_TRATTENERE) {
      window.addEventListener(tipo, trattieni, { capture: true, passive: false });
    }
  }

  return () => {
    elemento.removeEventListener('touchstart', giu);
    elemento.removeEventListener('touchmove', muove);
    elemento.removeEventListener('touchend', su);
    elemento.removeEventListener('touchcancel', annulla);
    if (esclusivo) {
      window.removeEventListener('pointerdown', segnaPuntatore, { capture: true });
      for (const tipo of DA_TRATTENERE) {
        window.removeEventListener(tipo, trattieni, { capture: true });
      }
    }
  };
}
