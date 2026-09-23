/** La casa — la pista delle pagine.
 *
 *  La casa e' un launcher: di lato alla chat ci sono le pagine che l'utente ha
 *  aggiunto (tavola `Pagine`). **La chat e' la pagina 0**: c'e' sempre, non si
 *  sposta e non si toglie — per questo non sta nell'elenco salvato, e per
 *  questo qui dentro l'indice 0 non e' una `schermata`.
 *
 *  **Che cosa muove.** Una pista in fila orizzontale, un pannello per pagina,
 *  e un `translateX`. Non e' la stessa risposta visiva del carosello
 *  dell'officina — li' si trascina la vista corrente con una sbirciata
 *  smorzata e la vicina non si disegna mai — e non per capriccio: la tavola
 *  mostra la pagina di fianco che **entra davvero**, affiancata, non che
 *  sbircia. Quel che i due gusci condividono e' il *riconoscimento* del gesto
 *  (`shared/gesto-orizzontale.js`), non la risposta.
 *
 *  **Niente `z-index`.** Quello di Jenny e' l'unico del foglio della casa e
 *  deve restarlo: lei sta sopra la chat, sopra un'app, sopra tutto.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { osservaGestoOrizzontale } from './shared/gesto-orizzontale.js';
import { cornicePerApp } from './shared/apps-actions.js';
import {
  isOpenableProjectName,
  projectKey,
  projectNameOf,
} from './shared/conversation-list.js';

/** Quanto la pista si lascia tirare oltre il capo, in frazione di schermo.
 *  Serve a dire «di la' non c'e' niente» col dito invece che con un blocco
 *  secco, che sembra un difetto. */
const OLTRE_IL_CAPO = 0.06;


/** Un'icona per specie. Non quella dell'app: per averla servirebbe l'elenco
 *  caricato, e un foglio che aspetta la rete per disegnare una riga e' un
 *  foglio che a volte non si apre. */
const ICONE = { app: 'ti-apps', conversazione: 'ti-notebook' };

export class CasaPagine {
  /** @param app  il guscio, per le guardie che solo lui conosce. */
  constructor(app) {
    this.app = app;
    this.pista = document.getElementById('casa-pista');
    /** Le pagine aggiunte, come le ha salvate il server. La chat non c'e'. */
    this.schermate = [];
    /** 0 = la chat. 1..n = `this.schermate[i - 1]`. */
    this.indice = 0;
    this._tetto = 8;
    this._staccaGesto = null;
    /** La conversazione della pagina 0 — la chat «libera», quella che scegli
     *  dal titolo. Le pagine conversazione hanno la loro nel `ref`; questa
     *  non sta nell'elenco perche' la pagina 0 non ci sta. Parte da quella che
     *  la chat mostra all'avvio, cioe' la personale. */
    this.conversazioneCasa = app?.chiaveAttuale?.() || null;
    this.striscia = document.getElementById('casa-pallini');
    this.foglio = document.getElementById('casa-pagine-dialog');
    this.elenco = document.getElementById('casa-foglio-elenco');

    if (this.pista) this._armaGesto();
    if (this.striscia && this.foglio) this._armaFoglio();
    this._pallini();
    i18n.onLocaleChange(() => this._pallini());
  }

  /** Quante caselle ha la pista, chat compresa. */
  get quante() {
    return this.schermate.length + 1;
  }

  /** Se il tetto e' pieno non si puo' aggiungere: lo chiede il foglio. */
  get pienoZeppo() {
    return this.schermate.length >= this._tetto;
  }

  /** Legge l'elenco dal server e disegna. Non alza: una casa che non apre
   *  perche' non ha saputo leggere le sue pagine e' peggio di una casa con la
   *  sola chat. */
  async carica() {
    try {
      const dati = await api.getSchermate();
      this.schermate = Array.isArray(dati?.schermate) ? dati.schermate : [];
      if (Number.isFinite(dati?.max)) this._tetto = dati.max;
    } catch {
      this.schermate = [];
    }
    this._disegna();
    this.vaiA(0, { animato: false });
  }

  /** Salva l'elenco intero — aggiungere, togliere e spostare sono la stessa
   *  scrittura, e mandarlo tutto toglie di mezzo il caso in cui due
   *  scritture si incrociano lasciando un ordine che nessuno ha chiesto. */
  async salva(schermate) {
    const salvate = await api.setSchermate(schermate);
    this.schermate = salvate;
    this._disegna();
    if (this.indice > this.schermate.length) this.vaiA(this.schermate.length);
    else this.vaiA(this.indice, { animato: false });
    return salvate;
  }

  /** Va alla casella `i`, se esiste. */
  vaiA(i, { animato = true } = {}) {
    if (!this.pista) return;
    const bersaglio = Math.max(0, Math.min(i, this.quante - 1));
    this.indice = bersaglio;
    this.pista.style.transition = animato
      ? 'transform .22s cubic-bezier(.22,.61,.36,1)'
      : 'none';
    this.pista.style.transform = `translateX(${-bersaglio * 100}%)`;
    this._pallini();
    this._accendiSolo(bersaglio);
    /* La chat arriva **prima** che l'intestazione si ridisegni: il trasloco
       cambia conversazione subito, e il titolo deve gia' leggere quella nuova. */
    const chiave = this.conversazioneDi(bersaglio);
    if (chiave) this.app?.trasloco?.arriva(this.pannelloDi(bersaglio), chiave);
    this.app?.onPaginaCambiata?.(bersaglio, this.schermate[bersaglio - 1] || null);
  }

  /** Quale conversazione mostra la casella `i`, o `null` se non e' di chat.
   *
   *  La pagina 0 ha la sua (`conversazioneCasa`), una pagina conversazione il
   *  suo quaderno; un'app nessuna — attraversarla non cambia la chat.
   */
  conversazioneDi(i) {
    if (i === 0) return this.conversazioneCasa;
    const s = this.schermate[i - 1];
    return s?.kind === 'conversazione' ? s.ref : null;
  }

  /** Il pannello della casella `i`. La chat e' la 0, e non ha `data-id`. */
  pannelloDi(i) {
    if (!this.pista) return null;
    if (i === 0) return Array.from(this.pista.children).find((c) => c.dataset?.pagina === 'chat') || null;
    return this._pannelli()[i - 1] || null;
  }

  /** Da fuori — il titolo, Home, un avviso — si chiede una conversazione.
   *
   *  **Una pagina conversazione mostra solo il suo quaderno**: e' l'invariante
   *  di tutto il disegno. Quindi se la conversazione chiesta non e' quella
   *  della pagina in cui sei, la si apre nella pagina 0, e ci si va. Oggi da
   *  una pagina fissa il titolo non apre la tendina, ma le strade che cambiano
   *  conversazione sono gia' cinque (titolo, nuovo quaderno, Home, Indietro,
   *  un avviso) e la sesta arrivera'.
   *
   *  Dalla pagina 0 si apre li', anche un quaderno che ha una pagina sua: deciso
   *  dall'utente il 23/09/2026 — «fai come ora, non scorrere».
   *
   *  Torna la promessa del cambio, e non per scrupolo: chi chiama ci manda
   *  subito dopo un messaggio, e deve finire nella conversazione giusta.
   */
  apriConversazione(chiave) {
    if (this.indice !== 0 && chiave === this.conversazioneDi(this.indice)) {
      return this.app?.mostraConversazione?.(chiave);
    }
    this.conversazioneCasa = chiave;
    if (this.indice === 0) return this.app?.mostraConversazione?.(chiave);
    this.vaiA(0);
    return this.app?.trasloco?.lettura;
  }

  /* ── Appendere e staccare ───────────────────────────────────────────── */
  /* Si appende **dal posto dove la cosa vive** — l'app dal cassetto, il
     quaderno dalla tendina — con una pressione lunga, come ogni launcher
     Android fa «aggiungi alla schermata principale». Queste tre sono l'unica
     porta: le schede chiedono, e qui si decide. */

  /** E' gia' una pagina? */
  appesa(kind, ref) {
    return this.schermate.some((s) => s.kind === kind && s.ref === ref);
  }

  /** La appende e ci porta sopra. `false` se c'era gia' o se il tetto e' pieno.
   *
   *  **Ci si atterra**: chi l'ha appena aggiunta vuole vederla, e lasciarlo
   *  dov'era gli farebbe credere che non sia successo niente.
   */
  async appendi(kind, ref) {
    if (this.appesa(kind, ref) || this.pienoZeppo) return false;
    const id = `p${Date.now().toString(36)}`;
    await this.salva([...this.schermate, { id, kind, ref }]);
    this.vaiA(this.schermate.length);
    return true;
  }

  /** La stacca. Niente conferma: una pagina si rimette con una pressione, e
   *  una domanda per un gesto annullabile e' solo un tocco in piu' ogni volta. */
  async stacca(kind, ref) {
    if (!this.appesa(kind, ref)) return false;
    await this.salva(this.schermate.filter((s) => !(s.kind === kind && s.ref === ref)));
    return true;
  }

  /** Rilegge dal server **senza** riportarti alla chat.
   *
   *  Serve dopo una cancellazione: il gateway ha tolto la pagina insieme alla
   *  cosa, e qui bisogna saperlo. `carica()` qui sarebbe sbagliato — riporta
   *  sempre alla pagina 0, cioe' ti sposta anche quando la tua c'e' ancora.
   */
  async ricarica() {
    let dati;
    try {
      dati = await api.getSchermate();
    } catch {
      return;
    }
    const nuove = Array.isArray(dati?.schermate) ? dati.schermate : [];
    const dove = this.schermate[this.indice - 1]?.id;
    this.schermate = nuove;
    this._disegna();
    const ancora = dove ? nuove.findIndex((s) => s.id === dove) : -1;
    this.vaiA(ancora >= 0 ? ancora + 1 : Math.min(this.indice, nuove.length), { animato: false });
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  /** Un pannello per schermata, accanto a quello della chat.
   *
   *  Il pannello della chat non si tocca mai: e' nell'HTML, ci vivono il filo
   *  e il composer, e ridisegnarlo vorrebbe dire buttare via la conversazione
   *  a ogni salvataggio.
   */
  _disegna() {
    if (!this.pista) return;
    /* Prima di buttare un pannello, si riprende la chat se era parcheggiata li':
       un `remove()` secco la porterebbe via insieme al pannello — cioe' filo,
       composer e bozza (trovato il 23/09/2026 mettendo qui la chat, quando le
       pagine tenevano anche le stanze del guscio e perdevano pure quelle). */
    const casa = this.pannelloDi(0);
    for (const vecchio of this.pista.querySelectorAll('.casa-pagina[data-id]')) {
      this.app?.trasloco?.riportaACasa(vecchio, casa);
      vecchio.remove();
    }
    for (const s of this.schermate) {
      const pannello = document.createElement('div');
      pannello.className = 'casa-pagina';
      pannello.dataset.id = s.id;
      pannello.dataset.kind = s.kind;
      this.pista.appendChild(pannello);
    }
    this._pallini();
  }

  /** Un pallino per casella, quello corrente allungato.
   *
   *  Bottoni veri e non `<span>`: chi i gesti non li fa — o non puo' farli —
   *  cambia pagina toccando, e chi legge lo schermo sente «pagina 2 di 3»
   *  invece di silenzio. `role="tab"` perche' la striscia e' una `tablist`:
   *  e' esattamente quel che e', un elenco di destinazioni di cui una e'
   *  accesa.
   */
  _pallini() {
    if (!this.striscia) return;
    this.striscia.textContent = '';
    for (let i = 0; i < this.quante; i += 1) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'casa-pallino';
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(i === this.indice));
      b.setAttribute(
        'aria-label',
        i === 0
          ? i18n.t('casa.pagine.chat')
          : i18n.t('casa.pagine.numero', { n: i, tot: this.quante - 1 }),
      );
      b.addEventListener('click', () => this.vaiA(i));
      this.striscia.appendChild(b);
    }
  }

  /** «Resta viva solo la pagina che guardi: le altre si spengono, o te le
   *  paghi in batteria» (tavola `PagineGestione`).
   *
   *  Non «la corrente piu' le due vicine»: tre `<iframe>` che girano insieme
   *  su un telefono sono tre app vive, e la regola della tavola e' piu'
   *  stretta **e** piu' semplice. Il prezzo e' che entrando in una pagina
   *  l'app riparte; il prezzo dell'altra scelta lo paga la batteria sempre.
   */
  _accendiSolo(indice) {
    if (!this.pista) return;
    this._pannelli().forEach((pannello, i) => {
      const suo = i + 1 === indice;
      if (suo) this._riempi(pannello);
      else this._svuota(pannello);
    });
  }

  /** Il contenuto di una pagina, costruito adesso perche' adesso si guarda. */
  async _riempi(pannello) {
    /* `pieno` porta il **numero del tentativo**, non un `1`: qui basta che sia
       valorizzato. Confrontarlo con `'1'` — com'era finche' il numero non
       c'era — avrebbe lasciato passare ogni rientro dal secondo in poi. */
    /* Una pagina conversazione non si riempie: ci arriva la chat, e la porta
       il trasloco da `vaiA`. */
    if (pannello.dataset.kind === 'conversazione') return;
    if (pannello.dataset.pieno) return;
    const schermata = this.schermate.find((x) => x.id === pannello.dataset.id);
    if (!schermata) return;
    /* Un segno **per tentativo**, non un flag condiviso.
       Un dito veloce fra due pagine fa: riempi → svuota → riempi. Il primo
       tentativo e' fermo sull'attesa del segreto; quando riparte trova la
       pagina di nuovo «piena» — ma piena per colpa del *secondo* — e monta
       lui pure. Due cornici, cioe' la stessa app viva due volte.
       Col numero di tentativo ognuno riconosce se e' ancora il suo giro.
       Misurato dal banco, non ipotizzato (22/09/2026). */
    const mio = String((this._tentativo = (this._tentativo || 0) + 1));
    pannello.dataset.pieno = mio;
    if (schermata.kind === 'app') {
      /* Il segreto **prima** della cornice: l'indirizzo se lo porta dentro, e
         costruirla senza vorrebbe dire un `token=undefined`, cioe' un 401 e
         una pagina bianca. `openApp` questa guardia ce l'ha da sempre; qui si
         era persa estraendo la cornice. */
      if (!api.getSecret()) {
        try { await api.bootstrap(); } catch {
          if (pannello.dataset.pieno === mio) pannello.dataset.pieno = '';
          return;
        }
      }
      /* Nel frattempo si puo' essere usciti dalla pagina, o rientrati: in tutti
         e due i casi il giro buono non e' piu' il nostro. */
      if (pannello.dataset.pieno !== mio) return;
      const cornice = cornicePerApp(schermata.ref);
      cornice.className = 'casa-pagina-app';
      pannello.appendChild(cornice);
    }
  }

  /** Spegne una pagina: la cornice dell'app se ne va, e con lei l'app viva. */
  _svuota(pannello) {
    /* ...e non si svuota: tiene la sua foto, o la chat se e' parcheggiata li'
       mentre guardi un'app. Spenta resta comunque — una foto non gira. */
    if (pannello.dataset.kind === 'conversazione') {
      const s = this.schermate.find((x) => x.id === pannello.dataset.id);
      this.app?.trasloco?.fotoSeServe(pannello, s?.ref);
      return;
    }
    if (!pannello.dataset.pieno) return;
    pannello.textContent = '';
    pannello.dataset.pieno = '';
  }

  /** I pannelli aggiunti, in ordine. Il pannello della chat non c'e': non ha
   *  `data-id` e non e' una `schermata`. */
  _pannelli() {
    if (!this.pista) return [];
    return this.pista.children.filter
      ? this.pista.children.filter((c) => c.dataset?.id)
      : [...this.pista.querySelectorAll('.casa-pagina[data-id]')];
  }

  /** Le guardie del guscio: si puo' cambiare pagina adesso?
   *
   *  Stanno in un posto solo perche' adesso le strade che ci arrivano sono
   *  due — il dito sulla pista e il dito dentro una app — e una seconda copia
   *  divergerebbe al primo caso particolare.
   */
  _puoScorrere() {
    // Fuori dalla chat comandano le stanze, non le pagine.
    if (this.app?.view && this.app.view !== 'chat') return false;
    // Un cassetto aperto possiede il proprio gesto.
    if (this.app?.launcher?.isOpen?.()) return false;
    // Con la sola chat non c'e' nessun posto dove andare, e un elastico che
    // risponde a vuoto sembra un difetto invece che un confine.
    if (this.quante < 2) return false;
    return true;
  }

  /** La risposta visiva al gesto, staccata da chi lo riconosce.
   *
   *  Due strade la percorrono: il dito **sulla pista**, e il dito **dentro
   *  una app** — che la pista non la tocca mai e parla da dietro la feritoia.
   *  La larghezza si misura all'inizio di ogni gesto e non a ogni movimento:
   *  leggerla in mezzo a un trascinamento costa un ricalcolo di layout per
   *  frame.
   */
  _risposta() {
    let larghezzaPista = 0;
    return {
      inizio: () => {
        larghezzaPista = this.pista.clientWidth || window.innerWidth;
        this.pista.style.transition = 'none';
        this.pista.style.willChange = 'transform';
      },

      trascina: (dx) => {
        /* Ai due capi il dito tira, ma di meno: la pista non si richiude in
           cerchio come le linguette dell'officina. Li' le voci sono quattro e
           note; qui quante siano lo decide l'utente, e girando in tondo fra
           otto pagine non si sa piu' dove si e'. */
        let scostamento = dx;
        const alPrimo = this.indice === 0 && dx > 0;
        const allUltimo = this.indice === this.quante - 1 && dx < 0;
        if (alPrimo || allUltimo) {
          scostamento = dx * OLTRE_IL_CAPO * 2;
        }
        const base = -this.indice * larghezzaPista;
        this.pista.style.transform = `translateX(${(base + scostamento).toFixed(2)}px)`;
      },

      fine: ({ verso, conferma }) => {
        this.pista.style.willChange = '';
        const passo = conferma ? (verso === 'prev' ? -1 : +1) : 0;
        this.vaiA(this.indice + passo);
      },

      annulla: () => {
        this.pista.style.willChange = '';
        this.vaiA(this.indice);
      },
    };
  }

  _armaGesto() {
    const risposta = this._risposta();

    this._staccaGesto = osservaGestoOrizzontale(this.pista, {
      puoIniziare: () => this._puoScorrere(),
      onOrizzontale: risposta.inizio,
      onTrascina: risposta.trascina,
      onFine: risposta.fine,
      onAnnulla: risposta.annulla,
    });

    this._ascoltaGestoDaApp(risposta);
  }

  /** La finestra della pagina che si sta guardando, se e' una app.
   *
   *  Serve a una cosa sola: **riconoscere chi parla**. Solo la pagina corrente
   *  e' viva (v. `_accendiSolo`), quindi questa e' l'unica finestra da cui un
   *  gesto possa arrivare davvero.
   *
   *  Non c'e' nessun controllo sulla specie della pagina, e non e' una
   *  dimenticanza: una pagina quaderno ospita la chat, che una `contentWindow`
   *  non ce l'ha. Dirlo due volte vorrebbe dire due regole da tenere d'accordo.
   */
  _finestraPagina() {
    if (this.indice === 0) return null;
    const pannello = this._pannelli()[this.indice - 1];
    const cornice = pannello && (pannello.children[0] || pannello.firstElementChild);
    return cornice?.contentWindow || null;
  }

  /** Il gesto che arriva da **dentro** una app.
   *
   *  La pagina di una app e' tutta l'app, intestazione compresa: il dito che
   *  la tocca non arriva mai al guscio, e lo scorrimento fra pagine — che
   *  ovunque altro funziona — li' dentro non esisteva. Misurato sul telefono
   *  il 22/09/2026: **in nessuna delle due direzioni**, non solo in una.
   *
   *  Il riconoscimento lo fa la app, perche' solo li' dentro si vede il DOM
   *  della app e quindi si puo' dire che il gesto appartiene a un suo
   *  scorrevole orizzontale. **Cosa farne lo decide il guscio**, perche' solo
   *  lui sa se una pagina di fianco c'e' — e lo decide a ogni `inizio`, non
   *  una volta per sempre: fra un gesto e l'altro il cassetto puo' aprirsi.
   *
   *  Vive qui e non in `apps-actions.js` perche' le cornici delle pagine sono
   *  di questo file: solo qui si puo' dire se chi parla e' la pagina che si
   *  sta guardando. `_onAppMessage` la scarta gia' — guarda solo la app
   *  aperta sopra tutto — e allargare quella guardia vorrebbe dire due
   *  proprietari per la stessa cornice.
   *
   *  **Tutto quel che arriva e' dell'app, cioe' non e' fidato**: la sorgente
   *  si confronta con la cornice viva, e i numeri si ripassano. Un `dx` che
   *  non e' un numero scriverebbe `translateX(NaN)` e la pista sparirebbe.
   */
  _ascoltaGestoDaApp(risposta) {
    let nostro = false;
    window.addEventListener?.('message', (e) => {
      const msg = e?.data;
      if (!msg || typeof msg !== 'object' || msg.type !== 'jenny:gesto') return;
      /* `!finestra` **prima** del confronto, e non e' ridondante: su una
         pagina che non e' una app qui c'e' `null`, e `MessageEvent.source` e'
         nullabile per specifica. Senza, un messaggio con sorgente nulla si
         confronterebbe `null !== null`, cioe' falso, cioe' passerebbe — e
         muoverebbe la pista chiunque. L'ha detto la mutazione, non la
         rilettura (22/09/2026). */
      const finestra = this._finestraPagina();
      if (!finestra || e.source !== finestra) return;

      if (msg.fase === 'inizio') {
        nostro = this._puoScorrere();
        if (nostro) risposta.inizio();
        return;
      }
      /* Senza questa, un `muove` che arrivasse senza il suo `inizio` —
         perche' rifiutato, o perche' la pagina e' cambiata in mezzo —
         muoverebbe la pista su una larghezza mai misurata. */
      if (!nostro) return;

      if (msg.fase === 'muove') {
        const dx = Number(msg.dx);
        if (Number.isFinite(dx)) risposta.trascina(dx);
      } else if (msg.fase === 'fine') {
        nostro = false;
        risposta.fine({
          verso: msg.verso === 'prev' ? 'prev' : 'next',
          conferma: msg.conferma === true,
        });
      } else if (msg.fase === 'annulla') {
        nostro = false;
        risposta.annulla();
      }
    });
  }
}

/* ── Il foglio «Le pagine di casa» ──────────────────────────────────────── */

Object.assign(CasaPagine.prototype, {
  /** La terza via della striscia: **tieni premuto**.
   *
   *  La pressione la riconosce il modulo condiviso e non questo file, per un
   *  motivo solo: annullarla quando il dito si muove vuol dire guardare i
   *  movimenti, e i movimenti si guardano in un posto solo. Cosi' chi tira su
   *  il cassetto, o cambia pagina, non si ritrova il foglio in faccia.
   */
  _armaFoglio() {
    osservaGestoOrizzontale(this.striscia, {
      puoIniziare: () => true,
      onPressioneLunga: () => this.apriFoglio(),
    });
  },

  apriFoglio() {
    if (!this.foglio || this.foglio.open) return;
    this._disegnaFoglio();
    this.foglio.showModal();
    /* Toccare fuori chiude, come ogni foglio che sale dal basso. Un `<dialog>`
       non lo fa da se': il click sul velo arriva **sul dialogo**, e si
       riconosce perche' il punto e' fuori dal suo riquadro. */
    if (!this._chiudeFuori) {
      this._chiudeFuori = (e) => {
        if (e.target !== this.foglio) return;
        const r = this.foglio.getBoundingClientRect();
        const dentro = e.clientX >= r.left && e.clientX <= r.right
          && e.clientY >= r.top && e.clientY <= r.bottom;
        if (!dentro) this.chiudiFoglio();
      };
      this.foglio.addEventListener('click', this._chiudeFuori);
    }
  },

  chiudiFoglio() {
    if (this.foglio?.open) this.foglio.close();
  },

  /** Una riga per pagina piena, poi la prima libera con la scelta aperta. */
  _disegnaFoglio() {
    const t = (k, v) => i18n.t(k, v);
    document.getElementById('casa-foglio-titolo').textContent = t('casa.foglio.titolo');
    document.getElementById('casa-foglio-occhiello').textContent = t('casa.foglio.occhiello');
    document.getElementById('casa-foglio-nota-cassetto').textContent = t('casa.foglio.notaCassetto');
    document.getElementById('casa-foglio-nota-viva').textContent = t('casa.foglio.notaViva');

    this.elenco.textContent = '';
    this.schermate.forEach((s, i) => this.elenco.appendChild(this._riga(s, i + 1)));
    if (!this.pienoZeppo) this.elenco.appendChild(this._rigaLibera());
    this._segnaSparite();
  },

  /** Un quaderno fissato che non c'e' piu' resta nel foglio, e lo dice.
   *
   *  Non si toglie da solo: cancellare una pagina dell'utente per conto proprio
   *  sarebbe una decisione presa dal codice al posto suo (la stessa regola
   *  delle app disinstallate, v. `test_a_page_pointing_at_nothing_is_kept`).
   *  E la pagina resta visitabile senza danni: il gateway rifiuta gia' i turni
   *  di un quaderno la cui cartella manca, e non la ricrea (`loop.py`).
   *
   *  Una lettura che fallisce non segna niente: «non lo so» non e' «sparito».
   */
  async _segnaSparite() {
    const fissate = this.schermate.filter((s) => s.kind === 'conversazione');
    if (!fissate.length) return;
    let nomi;
    try {
      const dati = await api.listProjects();
      nomi = new Set((dati?.projects || []).map((q) => q?.name));
    } catch {
      return;
    }
    for (const s of fissate) {
      if (nomi.has(projectNameOf(s.ref))) continue;
      const riga = Array.from(this.elenco.children).find((r) => r.dataset?.id === s.id);
      const specie = riga?.querySelector?.('.casa-foglio-specie');
      if (specie) specie.textContent = i18n.t('casa.foglio.sparito');
      riga?.classList?.add('casa-foglio-sparita');
    }
  },

  _riga(schermata, numero) {
    const riga = document.createElement('div');
    riga.className = 'casa-foglio-riga';
    riga.dataset.id = schermata.id;

    const icona = document.createElement('span');
    icona.className = 'casa-foglio-icona';
    const i = document.createElement('i');
    i.className = `ti ${ICONE[schermata.kind] || 'ti-square'}`;
    icona.appendChild(i);

    const testo = document.createElement('span');
    testo.className = 'casa-foglio-testo';
    const nome = document.createElement('span');
    nome.className = 'casa-foglio-nome';
    nome.textContent = this.nomeDi(schermata);
    const specie = document.createElement('span');
    specie.className = 'casa-foglio-specie';
    specie.textContent = i18n.t('casa.foglio.riga', {
      n: numero,
      specie: i18n.t(`casa.foglio.specie.${schermata.kind}`),
    });
    testo.append(nome, specie);

    const togli = document.createElement('button');
    togli.type = 'button';
    togli.className = 'casa-foglio-togli';
    togli.setAttribute('aria-label', i18n.t('casa.foglio.togli', { nome: this.nomeDi(schermata) }));
    const x = document.createElement('i');
    x.className = 'ti ti-x';
    togli.appendChild(x);
    /* Niente conferma: una pagina si rimette con tre tocchi, e una conferma
       per un gesto annullabile e' solo un tocco in piu' ogni volta. */
    togli.addEventListener('click', () => this._togli(schermata.id));

    riga.append(icona, testo, togli);
    return riga;
  },

  _rigaLibera() {
    const box = document.createElement('div');
    box.className = 'casa-foglio-libera';
    const domanda = document.createElement('div');
    domanda.className = 'casa-foglio-domanda';
    domanda.textContent = i18n.t('casa.foglio.vuota', { n: this.schermate.length + 1 });
    const scelte = document.createElement('div');
    scelte.className = 'casa-foglio-scelte';
    for (const kind of ['app', 'conversazione']) {
      scelte.appendChild(this._scelta(kind));
    }
    box.append(domanda, scelte);
    return box;
  },

  _scelta(kind) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'casa-foglio-scelta';
    b.dataset.kind = kind;
    const i = document.createElement('i');
    i.className = `ti ${ICONE[kind]}`;
    const testo = document.createElement('span');
    testo.textContent = i18n.t(`casa.foglio.scegli.${kind}`);
    b.append(i, testo);
    b.addEventListener('click', () => this._apriScelta(kind));
    return b;
  },

  /** Secondo passo: **quale**. Stesso foglio, non un secondo: due fogli
   *  impilati su un telefono lasciano metà schermo di velo e nessuno sa piu'
   *  quale «indietro» chiude cosa. */
  async _apriScelta(kind) {
    this.elenco.textContent = '';
    const indietro = document.createElement('button');
    indietro.type = 'button';
    indietro.className = 'casa-foglio-scelta';
    const freccia = document.createElement('i');
    freccia.className = 'ti ti-arrow-left';
    const et = document.createElement('span');
    et.textContent = i18n.t('casa.foglio.indietro');
    indietro.append(freccia, et);
    indietro.addEventListener('click', () => this._disegnaFoglio());
    this.elenco.appendChild(indietro);

    let voci = [];
    try {
      voci = await this._voci(kind);
    } catch {
      voci = [];
    }
    if (!voci.length) {
      const vuoto = document.createElement('p');
      vuoto.className = 'casa-foglio-nota';
      vuoto.textContent = i18n.t(`casa.foglio.niente.${kind}`);
      this.elenco.appendChild(vuoto);
      return;
    }
    for (const v of voci) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'casa-foglio-scelta';
      const i = document.createElement('i');
      i.className = `ti ${ICONE[kind]}`;
      const testo = document.createElement('span');
      testo.textContent = v.nome;
      b.append(i, testo);
      b.addEventListener('click', () => this._aggiungi(kind, v.ref));
      this.elenco.appendChild(b);
    }
  },

  /** Cosa si puo' scegliere, per specie. */
  async _voci(kind) {
    if (kind === 'conversazione') return this._quaderni();
    const fonte = this.app?.appsSource?.();
    /* **Attese**, non solo avviate: `ensureLoaded()` non e' asincrona e chi ci
       mette un `await` davanti aspetta `undefined`. Sul telefono diceva «non
       hai Jenny App» a chi ne aveva quattro. */
    const app = (await fonte?.attendiJennyApps?.()) || [];
    /* Un'app rotta non si puo' appendere: `openApp` per quelle chiede
       conferma e propone la riparazione in chat, e una pagina fissa rotta e'
       un'altra cosa — resterebbe li' a non funzionare tutti i giorni.
       Un'app **esterna** apre un indirizzo che il guscio non controlla:
       incastonarla in una pagina fissa e' una decisione a se'. */
    return app
      .filter((a) => !a.broken && a.view_kind !== 'external')
      .map((a) => ({ ref: a.slug, nome: a.name || a.slug }));
  },

  /** I quaderni che si possono fissare.
   *
   *  Solo `projects`, **mai** `unopenable`: una cartella di quel gruppo, aperta
   *  come conversazione, aprirebbe *un'altra* conversazione — il guasto per cui
   *  `wiki_routes.py::_collect_projects` divide i due elenchi. E fuori quelli
   *  gia' fissati: due pagine sullo stesso quaderno sono una di troppo.
   *
   *  Dal piu' recente, come la tendina del titolo: il quaderno che si cerca e'
   *  quasi sempre quello su cui si e' lavorato ieri, non il primo in ordine
   *  alfabetico.
   */
  async _quaderni() {
    const dati = await api.listProjects();
    const fissati = new Set(
      this.schermate.filter((s) => s.kind === 'conversazione').map((s) => s.ref),
    );
    return (dati?.projects || [])
      .filter((q) => isOpenableProjectName(q?.name) && !fissati.has(projectKey(q.name)))
      .sort((a, b) => (b.modified || 0) - (a.modified || 0) || a.name.localeCompare(b.name))
      .map((q) => ({ ref: projectKey(q.name), nome: q.name }));
  },

  /** Come si chiama una pagina, per l'intestazione e per il foglio.
   *
   *  Un'app e un quaderno portano il loro riferimento, che e' gia' il nome
   *  che l'utente ha visto quando l'ha scelto.
   */
  nomeDi(schermata) {
    if (!schermata) return '';
    if (schermata.kind === 'conversazione') return projectNameOf(schermata.ref) || schermata.ref;
    return schermata.ref;
  },

  async _aggiungi(kind, ref) {
    const id = `p${Date.now().toString(36)}`;
    await this.salva([...this.schermate, { id, kind, ref }]);
    this.chiudiFoglio();
    /* Si atterra sulla pagina appena fatta: chi l'ha aggiunta vuole vederla,
       e lasciarlo sulla chat gli farebbe credere che non sia successo niente. */
    this.vaiA(this.schermate.length);
  },

  async _togli(id) {
    await this.salva(this.schermate.filter((s) => s.id !== id));
    this._disegnaFoglio();
  },
});
