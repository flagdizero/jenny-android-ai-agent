/** Il trasloco della chat — una chat sola, che si sposta.
 *
 *  La chat e' un elemento solo, preso per id dai suoi controller: non si
 *  copia, si sposta. Dal 05/10/2026 i suoi posti sono due: la pagina chat, e
 *  il velo sopra qualunque pagina, dove la porta Jenny (`lift`/`lower`). Fino
 *  ad allora si spostava fra le pagine — la pagina chat, i Quaderni, le pagine
 *  di un quaderno appeso — e ogni pagina era una conversazione.
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
   *  @param homeKey    `() => key`: la conversazione della pagina chat (la
   *                       personale). Serve a `lower` e `retune`, per sapere
   *                       quale foto mostrare li' quando la chat e' altrove.
   */
  constructor({ chat, change, currentKey, atBottom, homeKey } = {}) {
    this.chat = chat || null;
    this._change = change;
    this._currentKey = currentKey;
    this._atBottom = atBottom;
    this._homeKey = homeKey;
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
      this._putSnapshot(fromIndex, left);
    }

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

    return this._changeUnder(panel, key, mine);
  }

  /** Il cambio di conversazione, con la foto che copre la lettura: passi 3 e
   *  4 di `arrives`, in comune con `lift` e `retune`. *panel* e' dove sta la
   *  foto da togliere a lettura finita (`null`: nessuna). Cambia **subito**:
   *  quando questa funzione arriva alla sua prima attesa, la conversazione
   *  attuale e' gia' quella nuova. */
  async _changeUnder(panel, key, mine) {
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
          se arriva un arrivo nuovo (qui sopra, e in `lift`/`lower`): restava
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
    if (panel && this.chat.parentElement === panel) removeSnapshot(panel);
    this._atBottom?.();
  }

  /* ── Il velo: la chat sopra la pagina ─────────────────────────────────
     Dal 05/10/2026 Jenny e' la porta della chat: toccata dove la chat non
     c'e', la chat vera sale sopra la pagina, dentro un velo (`#home-over`),
     e ridiscende a casa quando si chiude. Sempre lei, mai una copia. */

  /** La chat sale in *host* con la conversazione *key*.
   *
   *  La pagina che lascia tiene la sua foto, come in `arrives`. Se la
   *  conversazione e' gia' quella — il caso comune: sopra la bacheca la chat
   *  ha gia' la chiave del quaderno, sopra le App quella personale — non si
   *  rilegge niente e nel velo non c'e' foto.
   */
  lift(host, key) {
    if (!this.chat || !host || !key) return Promise.resolve();
    const mine = ++this._arrivals;
    this._expireCap?.();
    const from = this.chat.parentElement;
    const left = this._currentKey?.();
    if (from && from !== host) {
      if (this._reliable) this._negatives.set(left, this._negative());
      this._putSnapshot(from, left);
    }
    if (left === key) {
      removeSnapshot(host);
      host.appendChild(this.chat);
      this._atBottom?.();
      return Promise.resolve();
    }
    this._putSnapshot(host, key);
    host.insertBefore(this.chat, snapshotIn(host));
    return this._changeUnder(host, key, mine);
  }

  /** La chat torna nella pagina chat (*home*) senza cambiare conversazione.
   *
   *  Se e' la conversazione di casa, li' non serve una foto: c'e' lei. Se e'
   *  un quaderno (era aperta sopra la bacheca) la pagina chat mostra la foto
   *  della personale, finche' un arrivo non la rimette. Il negativo della
   *  conversazione lasciata si tiene.
   */
  lower(home) {
    if (!this.chat || !home) return;
    this._arrivals += 1; // una lettura in volo nel velo non tocca piu' niente
    this._expireCap?.();
    const host = this.chat.parentElement;
    const key = this._currentKey?.();
    if (this._reliable) this._negatives.set(key, this._negative());
    if (host && host !== home) removeSnapshot(host);
    home.insertBefore(this.chat, snapshotIn(home));
    const homeKey = this._homeKey?.();
    if (!homeKey || key === homeKey) removeSnapshot(home);
    else if (!snapshotIn(home)) this._putSnapshot(home, homeKey);
    this._atBottom?.();
  }

  /** Cambia conversazione **senza che si veda**: un quaderno si apre sulla
   *  sua bacheca, e la chat — a casa, dietro la bacheca — deve gia' avere la
   *  sua chiave quando Jenny la porta su. La pagina chat intanto mostra la
   *  foto della personale. Cambia in modo sincrono: chi legge la conversazione
   *  attuale subito dopo (`openBoard`) deve gia' sentirsi dire quella nuova.
   */
  retune(home, key) {
    if (!this.chat || !key || this._currentKey?.() === key) return Promise.resolve();
    const mine = ++this._arrivals;
    this._expireCap?.();
    const left = this._currentKey?.();
    if (this._reliable) this._negatives.set(left, this._negative());
    const homeKey = this._homeKey?.();
    const atHome = Boolean(home) && this.chat.parentElement === home;
    if (atHome && !snapshotIn(home)) this._putSnapshot(home, homeKey || left);
    /* Tornando alla personale la foto di casa e' di troppo: va via a lettura
       finita, come in un arrivo. */
    return this._changeUnder(atHome && key === homeKey ? home : null, key, mine);
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
    /* Quel che e' *di un momento* non va in foto: le bolle di chi sta
       lavorando (il posto vivo in fondo al filo), lo stato della rete, gli
       allegati in partenza. Congelati, direbbero che Jenny sta ancora pensando
       in una conversazione che hai lasciato. */
    for (const sel of ['.home-live', '.home-wire', '.home-pending']) {
      for (const el of copy.querySelectorAll(sel)) el.setAttribute('hidden', '');
    }
    /* Anche il piano a lista e' di un momento: il tasto Piano acceso, la riga
       sopra il composer, la striscia. In foto direbbero «sto pianificando» in
       una conversazione lasciata; e il tasto, che ha un ascoltatore solo
       nell'originale, restava doppio nel documento — un `querySelector` per
       classe prendeva quello morto della foto. Tolti, non nascosti. */
    for (const el of copy.querySelectorAll('.home-plan-toggle, .home-plan-hint, .home-plan-strip')) {
      el.remove();
    }
    for (const el of copy.querySelectorAll('.is-plan, .is-plan-off')) {
      el.classList.remove('is-plan', 'is-plan-off');
    }
    /* La bozza e' della conversazione di chi scatta la foto, e resta con lei
       (`_drafts`): in una copia mostrerebbe testo che non e' di quella pagina.
       Un clone di `<textarea>` si porta dietro il valore, per specifica — e
       l'altezza che `_autosize` gli aveva scritto: senza toglierla, la foto
       mostrerebbe un campo vuoto alto quanto la bozza. */
    for (const field of copy.querySelectorAll('textarea')) {
      field.value = '';
      field.style.height = '';
    }
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
