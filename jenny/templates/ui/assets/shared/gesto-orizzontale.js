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

/** Quanto deve stare fermo il dito perche' sia una pressione lunga. */
export const PRESSIONE_LUNGA_MS = 500;

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

/** C'e' uno scorrevole orizzontale, sotto il dito, che puo' ancora scorrere in
 *  quel verso? Allora il gesto e' suo, non del carosello.
 *
 *  Si sale da `bersaglio` fino a `confine` cercandone uno. E' la regola che sul
 *  telefono fa «fallire» lo scorrimento sopra un blocco di codice largo: non e'
 *  un difetto, e' questa funzione che fa il suo mestiere.
 */
export function dentroScorrevoleOrizzontale(bersaglio, dx, confine) {
  let el = bersaglio;
  while (el && el !== confine && el !== document.body) {
    if (el.scrollWidth > el.clientWidth + 2) {
      const overflowX = getComputedStyle(el).overflowX;
      if (overflowX === 'auto' || overflowX === 'scroll') {
        const alBordo = el.scrollLeft <= 0;
        const allaFine = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1;
        // dx > 0 (dito a destra) scorre il contenuto verso il suo inizio;
        // dx < 0 (dito a sinistra) verso la sua fine.
        if (dx > 0 && !alBordo) return true;
        if (dx < 0 && !allaFine) return true;
      }
    }
    el = el.parentElement;
  }
  return false;
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
 *  - `onPressioneLunga()` — il dito e' rimasto fermo mezzo secondo. Sta qui e
 *    non nel guscio per un motivo preciso: **annullarla quando il dito si
 *    muove** vuol dire guardare i movimenti, e i movimenti si guardano in un
 *    posto solo. Chi la usa senza volere il trascinamento passa solo questa.
 *
 *  `onFine` e `onAnnulla` arrivano **solo** se l'asse era stato deciso: un
 *  tocco che non diventa mai orizzontale non deve far ridisegnare niente.
 *
 *  @returns {() => void} per staccarlo.
 */
export function osservaGestoOrizzontale(elemento, {
  puoIniziare,
  onOrizzontale,
  onTrascina,
  onFine,
  onAnnulla,
  onPressioneLunga,
} = {}) {
  let partenzaX = 0;
  let partenzaY = 0;
  let partenzaT = 0;
  let inAscolto = false;     // un gesto candidato e' in corso
  let orizzontale = false;   // l'asse e' stato deciso
  let bersaglio = null;
  let attesaPressione = null;

  const disarmaPressione = () => {
    if (attesaPressione === null) return;
    clearTimeout(attesaPressione);
    attesaPressione = null;
  };

  const azzera = () => {
    inAscolto = false;
    orizzontale = false;
    bersaglio = null;
    disarmaPressione();
  };

  const larghezza = () => elemento.clientWidth || window.innerWidth;

  const giu = (e) => {
    azzera();
    if (e.touches.length !== 1) return;
    if (puoIniziare && puoIniziare() === false) return;
    const t = e.touches[0];
    partenzaX = t.screenX;
    partenzaY = t.screenY;
    partenzaT = Date.now();
    bersaglio = e.target;
    inAscolto = true;
    if (onPressioneLunga) {
      attesaPressione = setTimeout(() => {
        attesaPressione = null;
        /* Le due guardie bastano da sole, ed e' misurato: un dito che si
           muove o diventa **orizzontale** (`orizzontale`), o non supera la
           dominanza e allora passa da `azzera()` — che disarma. Una terza
           `disarmaPressione()` dentro `muove` non uccideva nessuna mutazione,
           quindi e' uscita (22/09/2026). */
        if (inAscolto && !orizzontale) onPressioneLunga();
      }, PRESSIONE_LUNGA_MS);
    }
  };

  const muove = (e) => {
    if (!inAscolto) return;
    const t = e.touches[0];
    const dx = t.screenX - partenzaX;
    const dy = t.screenY - partenzaY;

    if (!orizzontale) {
      if (Math.abs(dx) < SOGLIA_ASSE && Math.abs(dy) < SOGLIA_ASSE) return;
      /* Dominanza orizzontale vera: un trascinamento diagonale (tipico di chi
         aggiusta una selezione) non arma il gesto. */
      if (Math.abs(dx) <= Math.abs(dy) * 1.5) { azzera(); return; }
      if (dentroScorrevoleOrizzontale(bersaglio, dx, elemento)) { azzera(); return; }
      orizzontale = true;
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
    const eraOrizzontale = orizzontale;
    azzera();
    if (eraOrizzontale) onAnnulla?.();
  };

  elemento.addEventListener('touchstart', giu, { passive: true });
  elemento.addEventListener('touchmove', muove, { passive: false });
  elemento.addEventListener('touchend', su, { passive: true });
  elemento.addEventListener('touchcancel', annulla, { passive: true });

  return () => {
    elemento.removeEventListener('touchstart', giu);
    elemento.removeEventListener('touchmove', muove);
    elemento.removeEventListener('touchend', su);
    elemento.removeEventListener('touchcancel', annulla);
  };
}
