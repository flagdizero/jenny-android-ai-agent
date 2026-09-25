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
export const SNAPSHOT_CAP_MS = 600;

/** Quanti messaggi tiene una foto. Il resto non si vede comunque: la chat sta
 *  in fondo, e una copia del filo intero costerebbe per niente. */
export const SNAPSHOT_MESSAGES = 20;

/** I figli di un elemento, come lista. `children` e' una HTMLCollection: si
 *  scorre, ma `find` non ce l'ha. */
const children = (el) => Array.from(el?.children || []);

const isSnapshot = (el) => Boolean(el?.classList?.contains('home-snapshot'));

/** La foto che sta in questo pannello, o `null`. */
function snapshotIn(panel) {
  return children(panel).find(isSnapshot) || null;
}

function removeSnapshot(panel) {
  for (const f of children(panel).filter(isSnapshot)) f.remove();
}

export class ChatMove {
  /** @param chat          l'involucro della chat vera (`#home-chat`)
   *  @param cambia        `(key) => Promise`: cambia conversazione e rilegge
   *                       il filo. Deve cambiarla **subito**, prima della sua
   *                       prima attesa: chi chiede la conversazione attuale
   *                       appena dopo deve gia' sentirsi dire quella nuova.
   *  @param currentKey `() => key`: la conversazione che la chat mostra
   *  @param atBottom       riporta il filo in fondo. Serve anche senza cambio:
   *                       un contenitore staccato e riattaccato perde lo scroll.
   */
  constructor({ chat, change, currentKey, atBottom } = {}) {
    this.chat = chat || null;
    this._change = change;
    this._currentKey = currentKey;
    this._atBottom = atBottom;
    /** Una foto per conversazione: il negativo, da cui si stampano le copie. */
    this._negatives = new Map();
    /** Un segno per arrivo: vince l'ultimo, sempre. */
    this._arrivals = 0;
    /** La lettura in corso, per chi deve aspettarla (v. `openConversation`). */
    this.read = Promise.resolve();
    /* Si fotografa solo una chat che ha finito di leggere. A meta' lettura il
       filo e' vuoto, o e' gia' quello nuovo: la foto della conversazione
       lasciata direbbe il falso, e resterebbe li' fino alla visita dopo. */
    this._reliable = true;
    /** Il pannello in cui la chat e' stata riportata **senza** cambiarle
     *  conversazione (v. `bringBackHome`), o `null`. */
    this._outOfPlace = null;
    /** Fa scadere subito il tetto dell'arrivo in corso, o `null`. */
    this._expireCap = null;
  }

  /** La chat arriva in `panel`, che mostra la conversazione `key`.
   *
   *  Si chiama **al rilascio** e non a fine animazione: la lettura di rete
   *  parte mentre la pista scorre ancora, e i suoi 220 ms sono guadagnati.
   */
  async arrives(panel, key) {
    if (!this.chat || !panel || !key) return;
    const fromIndex = this.chat.parentElement;
    const change = this._currentKey?.() !== key;
    if (fromIndex === panel && !change) return;
    const mine = ++this._arrivals;
    this._expireCap?.();

    /* 1. La pagina che la chat lascia tiene la sua foto — scattata **prima**
          del cambio: dopo, sarebbe la foto della conversazione d'arrivo. */
    if (fromIndex && fromIndex !== panel) {
      const left = this._currentKey?.();
      if (this._reliable) this._negatives.set(left, this._negative());
      /* ...tranne quando la chat li' era **fuori posto**: riportata a casa da
         un ridisegno, mostrava ancora il quaderno da cui veniva. La pagina che
         lascia ha gia' la foto giusta — la sua — e appenderci questa vorrebbe
         dire far entrare il quaderno nella pagina chat, a meta' scorrimento. */
      if (fromIndex !== this._outOfPlace) this._putSnapshot(fromIndex, left);
    }
    this._outOfPlace = null;

    /* 2. La chat entra **sotto** la foto della pagina d'arrivo. Se la pagina
          una foto non ce l'ha — appena disegnata — gliene si da' una adesso,
          o si vedrebbe la lettura. */
    if (change && !snapshotIn(panel)) this._putSnapshot(panel, key);
    panel.insertBefore(this.chat, snapshotIn(panel));

    /* Stessa conversazione in due pagine (la pagina chat e quella del quaderno):
       la chat e' gia' giusta, basta spostarla. */
    if (!change) {
      removeSnapshot(panel);
      this._atBottom?.();
      return;
    }

    /* 3. Il cambio. */
    let read;
    try {
      read = Promise.resolve(this._change?.(key));
    } catch (err) {
      read = Promise.reject(err);
    }
    this.read = read;
    this._reliable = false;
    const finished = () => {
      if (this.read === read) this._reliable = true;
    };
    read.then(finished, finished);

    /* 4. Via la foto quando la lettura e' finita, o al tetto. Solo se questo
          e' ancora l'ultimo arrivo: un dito veloce fa A → B → A, e il «finito»
          di B non deve scoprire la chat sotto la foto di A.
          Il timer del tetto si spegne appena la lettura vince, e scade subito
          se arriva un arrivo nuovo (qui sopra, e in `bringBackHome`): restava
          vivo 600 ms per niente a ogni arrivo, e teneva sveglio chi lo
          aspettava. */
    let cap;
    let expire;
    const expired = new Promise((r) => {
      cap = setTimeout(r, SNAPSHOT_CAP_MS);
      expire = () => { clearTimeout(cap); r(); };
    });
    this._expireCap = expire;
    await Promise.race([read.catch(() => {}), expired]);
    clearTimeout(cap);
    if (this._expireCap === expire) this._expireCap = null;
    if (mine !== this._arrivals) return;
    if (this.chat.parentElement === panel) removeSnapshot(panel);
    this._atBottom?.();
  }

  /** Una pagina di chat che la chat non abita deve mostrare la sua foto.
   *
   *  Se la chat e' li' — parcheggiata mentre guardi un'app — resta lei: e' la
   *  conversazione di quella pagina, e viva e' meglio che in foto. Se una foto
   *  c'e' gia' non si ristampa: questo passa a ogni cambio di pagina.
   */
  snapshotIfNeeded(panel, key) {
    if (!panel || !key) return;
    if (this.chat && this.chat.parentElement === panel) return;
    if (snapshotIn(panel)) return;
    this._putSnapshot(panel, key);
  }

  /** Il pannello `old` sta per essere buttato: se la chat e' li', torna
   *  nel pannello di casa **prima**. Senza, ridisegnare le pagine porterebbe
   *  via la chat intera — filo, composer, bozza — insieme al pannello. */
  bringBackHome(old, home) {
    if (!this.chat || !home || this.chat.parentElement !== old) return;
    this._arrivals += 1;  // un arrivo in volo verso `old` non tocca piu' niente
    this._expireCap?.();
    home.insertBefore(this.chat, snapshotIn(home));
    /* La chat e' a casa ma con la conversazione di prima, sotto la foto di
       casa: il prossimo arrivo lo deve sapere (v. il passo 1 di `arrives`). */
    this._outOfPlace = home;
  }

  /* ── La foto ─────────────────────────────────────────────────────────── */

  /** Il negativo: la chat com'e' adesso, resa inerte.
   *
   *  **Senza nessun id.** Un `cloneNode` porta con se' `#home-thread` e gli
   *  altri: se la copia sta nel DOM *prima* della chat vera, `getElementById`
   *  restituisce la copia, e un controller comincia a scrivere dentro una foto.
   */
  _negative() {
    const copy = this.chat.cloneNode(true);
    copy.removeAttribute('id');
    for (const el of copy.querySelectorAll('[id]')) el.removeAttribute('id');
    for (const el of copy.querySelectorAll('[for]')) el.removeAttribute('for');
    /* Quel che e' *di un momento* non va in foto: la riga di lavoro di Jenny,
       lo stato della rete, gli allegati in partenza. Congelati, direbbero che
       Jenny sta ancora pensando in una conversazione che hai lasciato. */
    for (const sel of ['.home-activity', '.home-wire', '.home-pending']) {
      for (const el of copy.querySelectorAll(sel)) el.setAttribute('hidden', '');
    }
    /* La bozza e' della conversazione di chi scatta la foto, e resta con lei
       (`_drafts`): in una copia mostrerebbe testo che non e' di quella pagina.
       Un clone di `<textarea>` si porta dietro il valore, per specifica. */
    for (const field of copy.querySelectorAll('textarea')) field.value = '';
    const messages = copy.querySelectorAll('.home-msg');
    for (let i = 0; i < messages.length - SNAPSHOT_MESSAGES; i += 1) messages[i].remove();
    copy.classList.add('home-snapshot');
    copy.setAttribute('inert', '');
    copy.setAttribute('aria-hidden', 'true');
    return copy;
  }

  /** La foto di una conversazione che non si e' mai vista: la chat senza i
   *  messaggi. Senza nemmeno lo stato vuoto — «non c'e' niente qui» sarebbe
   *  falso: c'e', solo non l'abbiamo ancora letto. */
  _skeleton() {
    const copy = this._negative();
    for (const el of copy.querySelectorAll('.home-msg, .home-boundary')) el.remove();
    for (const el of copy.querySelectorAll('.home-empty')) el.setAttribute('hidden', '');
    return copy;
  }

  _putSnapshot(panel, key) {
    removeSnapshot(panel);
    const negative = this._negatives.get(key);
    const snapshot = negative ? negative.cloneNode(true) : this._skeleton();
    panel.appendChild(snapshot);
    /* Un clone parte dall'alto, e la chat sta in fondo: senza, la pagina
       entrerebbe coi messaggi di una settimana fa. */
    const thread = snapshot.querySelector('.home-thread');
    if (thread) thread.scrollTop = thread.scrollHeight;
  }
}
