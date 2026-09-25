/** Il trasloco della chat — una chat sola, che si sposta fra le pagine.
 *
 *  Una pagina «conversazione» non e' una seconda chat: e' una **scorciatoia**
 *  che cambia la conversazione dell'unica che c'e', travestita da pagina
 *  (v. `.agent/pagine-conversazione-plan.md`). La chat e' un elemento solo,
 *  preso per id dai suoi controller: non si copia, si sposta — come le stanze
 *  prestate — nella pagina che guardi.
 *
 *  **Il travestimento e' una foto.** Nelle pagine di chat che la chat non abita
 *  c'e' una copia statica di com'era l'ultima volta che l'hai vista. Scorrendo,
 *  la pagina che entra e' quella foto: due chat affiancate, e lo scorrimento
 *  sembra vero perche' lo e'. All'arrivo la chat vera ci scivola **sotto**,
 *  cambia conversazione, e la foto se ne va quando la lettura e' finita — cosi'
 *  non si vede il vuoto che `reload()` lascia fra lo svuotare e il rileggere.
 *
 *  Un quaderno mai aperto da quando l'app e' partita una foto non ce l'ha: entra
 *  come chat vuota e si riempie all'arrivo. E' l'unico punto in cui il trucco
 *  si vede, e l'utente l'ha accettato (23/09/2026).
 *
 *  Il modulo non sa cosa sia una conversazione: riceve una chiave, e chi sa
 *  cambiarla. Per questo si prova da solo, su un DOM finto.
 */

/** Quanto si aspetta la lettura prima di togliere la foto comunque.
 *
 *  Oltre, meglio la chat vera nel suo stato di caricamento che una foto
 *  immobile su cui il dito non fa niente: la foto e' `inert`, e un composer che
 *  non risponde sembra un difetto.
 */
export const TETTO_FOTO_MS = 600;

/** Quanti messaggi tiene una foto. Il resto non si vede comunque: la chat sta
 *  in fondo, e una copia del filo intero costerebbe per niente. */
export const FOTO_MESSAGGI = 20;

const attendi = (ms) => new Promise((r) => setTimeout(r, ms));

/** I figli di un elemento, come lista. `children` e' una HTMLCollection: si
 *  scorre, ma `find` non ce l'ha. */
const figli = (el) => Array.from(el?.children || []);

const eFoto = (el) => Boolean(el?.classList?.contains('casa-foto'));

/** La foto che sta in questo pannello, o `null`. */
function fotoIn(pannello) {
  return figli(pannello).find(eFoto) || null;
}

function togliFoto(pannello) {
  for (const f of figli(pannello).filter(eFoto)) f.remove();
}

export class Trasloco {
  /** @param chat          l'involucro della chat vera (`#casa-chat`)
   *  @param cambia        `(chiave) => Promise`: cambia conversazione e rilegge
   *                       il filo. Deve cambiarla **subito**, prima della sua
   *                       prima attesa: chi chiede la conversazione attuale
   *                       appena dopo deve gia' sentirsi dire quella nuova.
   *  @param chiaveAttuale `() => chiave`: la conversazione che la chat mostra
   *  @param inFondo       riporta il filo in fondo. Serve anche senza cambio:
   *                       un contenitore staccato e riattaccato perde lo scroll.
   */
  constructor({ chat, cambia, chiaveAttuale, inFondo } = {}) {
    this.chat = chat || null;
    this._cambia = cambia;
    this._chiaveAttuale = chiaveAttuale;
    this._inFondo = inFondo;
    /** Una foto per conversazione: il negativo, da cui si stampano le copie. */
    this._negativi = new Map();
    /** Un segno per arrivo: vince l'ultimo, sempre. */
    this._arrivi = 0;
    /** La lettura in corso, per chi deve aspettarla (v. `apriConversazione`). */
    this.lettura = Promise.resolve();
    /* Si fotografa solo una chat che ha finito di leggere. A meta' lettura il
       filo e' vuoto, o e' gia' quello nuovo: la foto della conversazione
       lasciata direbbe il falso, e resterebbe li' fino alla visita dopo. */
    this._affidabile = true;
    /** Il pannello in cui la chat e' stata riportata **senza** cambiarle
     *  conversazione (v. `riportaACasa`), o `null`. */
    this._fuoriPosto = null;
  }

  /** La chat arriva in `pannello`, che mostra la conversazione `chiave`.
   *
   *  Si chiama **al rilascio** e non a fine animazione: la lettura di rete
   *  parte mentre la pista scorre ancora, e i suoi 220 ms sono guadagnati.
   */
  async arriva(pannello, chiave) {
    if (!this.chat || !pannello || !chiave) return;
    const da = this.chat.parentElement;
    const cambia = this._chiaveAttuale?.() !== chiave;
    if (da === pannello && !cambia) return;
    const mio = ++this._arrivi;

    /* 1. La pagina che la chat lascia tiene la sua foto — scattata **prima**
          del cambio: dopo, sarebbe la foto della conversazione d'arrivo. */
    if (da && da !== pannello) {
      const lasciata = this._chiaveAttuale?.();
      if (this._affidabile) this._negativi.set(lasciata, this._negativo());
      /* ...tranne quando la chat li' era **fuori posto**: riportata a casa da
         un ridisegno, mostrava ancora il quaderno da cui veniva. La pagina che
         lascia ha gia' la foto giusta — la sua — e appenderci questa vorrebbe
         dire far entrare il quaderno nella pagina chat, a meta' scorrimento. */
      if (da !== this._fuoriPosto) this._mettiFoto(da, lasciata);
    }
    this._fuoriPosto = null;

    /* 2. La chat entra **sotto** la foto della pagina d'arrivo. Se la pagina
          una foto non ce l'ha — appena disegnata — gliene si da' una adesso,
          o si vedrebbe la lettura. */
    if (cambia && !fotoIn(pannello)) this._mettiFoto(pannello, chiave);
    pannello.insertBefore(this.chat, fotoIn(pannello));

    /* Stessa conversazione in due pagine (la pagina chat e quella del quaderno):
       la chat e' gia' giusta, basta spostarla. */
    if (!cambia) {
      togliFoto(pannello);
      this._inFondo?.();
      return;
    }

    /* 3. Il cambio. */
    let lettura;
    try {
      lettura = Promise.resolve(this._cambia?.(chiave));
    } catch (err) {
      lettura = Promise.reject(err);
    }
    this.lettura = lettura;
    this._affidabile = false;
    const finita = () => {
      if (this.lettura === lettura) this._affidabile = true;
    };
    lettura.then(finita, finita);

    /* 4. Via la foto quando la lettura e' finita, o al tetto. Solo se questo
          e' ancora l'ultimo arrivo: un dito veloce fa A → B → A, e il «finito»
          di B non deve scoprire la chat sotto la foto di A. */
    await Promise.race([lettura.catch(() => {}), attendi(TETTO_FOTO_MS)]);
    if (mio !== this._arrivi) return;
    if (this.chat.parentElement === pannello) togliFoto(pannello);
    this._inFondo?.();
  }

  /** Una pagina di chat che la chat non abita deve mostrare la sua foto.
   *
   *  Se la chat e' li' — parcheggiata mentre guardi un'app — resta lei: e' la
   *  conversazione di quella pagina, e viva e' meglio che in foto. Se una foto
   *  c'e' gia' non si ristampa: questo passa a ogni cambio di pagina.
   */
  fotoSeServe(pannello, chiave) {
    if (!pannello || !chiave) return;
    if (this.chat && this.chat.parentElement === pannello) return;
    if (fotoIn(pannello)) return;
    this._mettiFoto(pannello, chiave);
  }

  /** Il pannello `vecchio` sta per essere buttato: se la chat e' li', torna
   *  nel pannello di casa **prima**. Senza, ridisegnare le pagine porterebbe
   *  via la chat intera — filo, composer, bozza — insieme al pannello. */
  riportaACasa(vecchio, casa) {
    if (!this.chat || !casa || this.chat.parentElement !== vecchio) return;
    this._arrivi += 1;  // un arrivo in volo verso `vecchio` non tocca piu' niente
    casa.insertBefore(this.chat, fotoIn(casa));
    /* La chat e' a casa ma con la conversazione di prima, sotto la foto di
       casa: il prossimo arrivo lo deve sapere (v. il passo 1 di `arriva`). */
    this._fuoriPosto = casa;
  }

  /* ── La foto ─────────────────────────────────────────────────────────── */

  /** Il negativo: la chat com'e' adesso, resa inerte.
   *
   *  **Senza nessun id.** Un `cloneNode` porta con se' `#casa-thread` e gli
   *  altri: se la copia sta nel DOM *prima* della chat vera, `getElementById`
   *  restituisce la copia, e un controller comincia a scrivere dentro una foto.
   */
  _negativo() {
    const copia = this.chat.cloneNode(true);
    copia.removeAttribute('id');
    for (const el of copia.querySelectorAll('[id]')) el.removeAttribute('id');
    for (const el of copia.querySelectorAll('[for]')) el.removeAttribute('for');
    /* Quel che e' *di un momento* non va in foto: la riga di lavoro di Jenny,
       lo stato della rete, gli allegati in partenza. Congelati, direbbero che
       Jenny sta ancora pensando in una conversazione che hai lasciato. */
    for (const sel of ['.casa-activity', '.casa-wire', '.casa-pending']) {
      for (const el of copia.querySelectorAll(sel)) el.setAttribute('hidden', '');
    }
    /* La bozza e' della conversazione di chi scatta la foto, e resta con lei
       (`_drafts`): in una copia mostrerebbe testo che non e' di quella pagina.
       Un clone di `<textarea>` si porta dietro il valore, per specifica. */
    for (const campo of copia.querySelectorAll('textarea')) campo.value = '';
    const messaggi = copia.querySelectorAll('.casa-msg');
    for (let i = 0; i < messaggi.length - FOTO_MESSAGGI; i += 1) messaggi[i].remove();
    copia.classList.add('casa-foto');
    copia.setAttribute('inert', '');
    copia.setAttribute('aria-hidden', 'true');
    return copia;
  }

  /** La foto di una conversazione che non si e' mai vista: la chat senza i
   *  messaggi. Senza nemmeno lo stato vuoto — «non c'e' niente qui» sarebbe
   *  falso: c'e', solo non l'abbiamo ancora letto. */
  _scheletro() {
    const copia = this._negativo();
    for (const el of copia.querySelectorAll('.casa-msg, .casa-boundary')) el.remove();
    for (const el of copia.querySelectorAll('.casa-empty')) el.setAttribute('hidden', '');
    return copia;
  }

  _mettiFoto(pannello, chiave) {
    togliFoto(pannello);
    const negativo = this._negativi.get(chiave);
    const foto = negativo ? negativo.cloneNode(true) : this._scheletro();
    pannello.appendChild(foto);
    /* Un clone parte dall'alto, e la chat sta in fondo: senza, la pagina
       entrerebbe coi messaggi di una settimana fa. */
    const filo = foto.querySelector('.casa-thread');
    if (filo) filo.scrollTop = filo.scrollHeight;
  }
}
