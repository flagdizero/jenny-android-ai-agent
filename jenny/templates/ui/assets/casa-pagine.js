/** La casa — la pista delle pagine.
 *
 *  La casa e' un launcher: una fila di pagine che si scorrono, e i loro nomi in
 *  alto (v. `casa-fila.js`). **Quattro ci sono sempre** — il cassetto delle app,
 *  la chat, i quaderni, le impostazioni — e accanto ci sono quelle che l'utente
 *  ha aggiunto. **Tutte si spostano**, la chat compresa: dal 23/09/2026 la chat
 *  non e' piu' la pagina 0, e nessun indice qui dentro vuol dire «la chat» da
 *  solo (v. `.agent/pagine-in-alto-plan.md`). Dove sta ogni pagina lo dice
 *  `ordine`, che il gateway salva accanto alle `schermate`.
 *
 *  **Che cosa muove.** Una pista in fila orizzontale, un pannello per pagina,
 *  e un `translateX`. Non e' la stessa risposta visiva del carosello
 *  dell'officina — li' si trascina la vista corrente con una sbirciata
 *  smorzata e la vicina non si disegna mai — e non per capriccio: la tavola
 *  mostra la pagina di fianco che **entra davvero**, affiancata, non che
 *  sbircia. Quel che i due gusci condividono e' il *riconoscimento* del gesto
 *  (`shared/gesto-orizzontale.js`), non la risposta.
 *
 *  **I pannelli delle quattro fisse sono nell'HTML**, come la chat e' sempre
 *  stata: qui si spostano, non si creano e non si distruggono. Il 22-23/09 le
 *  stanze del guscio venivano *prestate* alle pagine a runtime, e quel prestito
 *  era il pezzo piu' fragile della casa (si perdevano a ogni ridisegno); una
 *  pagina fissa non ha niente da prestare.
 *
 *  **Niente `z-index`.** Quello di Jenny e' l'unico del foglio della casa e
 *  deve restarlo: lei sta sopra la chat, sopra un'app, sopra tutto.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { osservaGestoOrizzontale } from './shared/gesto-orizzontale.js';
import { cornicePerApp } from './shared/apps-actions.js';
import { projectNameOf } from './shared/conversation-list.js';

/** Quanto la pista si lascia tirare oltre il capo, in frazione di schermo.
 *  Serve a dire «di la' non c'e' niente» col dito invece che con un blocco
 *  secco, che sembra un difetto. */
const OLTRE_IL_CAPO = 0.06;

/** Le pagine che ci sono sempre, nell'ordine di chi non ha mai spostato
 *  niente. E' la copia di `PAGINE_FISSE` dello schema, e serve solo finche' il
 *  server non ha risposto: la risposta porta le sue, e vincono quelle. */
export const FISSE = ['app', 'chat', 'quaderni', 'impostazioni'];

/** La specie di una pagina fissa. Il cassetto non si chiama `app` qui dentro:
 *  `app` e' gia' la specie di una **Jenny App** appesa, e due cose diverse con
 *  lo stesso nome si confondono al primo `if`. */
const SPECIE_FISSA = { app: 'cassetto', chat: 'chat', quaderni: 'quaderni', impostazioni: 'impostazioni' };

/** L'ordine reso coerente con le pagine che ci sono. La stessa regola di
 *  `ordine_normale` nello schema, per quando il server non l'ha detta (una
 *  lettura fallita, un gateway vecchio): mai un ordine che perde una pagina. */
export function ordineNormale(ordine, schermate, fisse = FISSE) {
  const ids = schermate.map((s) => s.id);
  const validi = new Set([...fisse, ...ids]);
  const visti = [];
  for (const v of Array.isArray(ordine) ? ordine : []) {
    if (typeof v === 'string' && validi.has(v) && !visti.includes(v)) visti.push(v);
  }
  if (!visti.length) {
    const i = fisse.indexOf('chat') + 1;
    return [...fisse.slice(0, i), ...ids, ...fisse.slice(i)];
  }
  for (const f of fisse) if (!visti.includes(f)) visti.push(f);
  const mancanti = ids.filter((id) => !visti.includes(id));
  const dopo = visti.indexOf('chat') + 1;
  return [...visti.slice(0, dopo), ...mancanti, ...visti.slice(dopo)];
}


export class CasaPagine {
  /** @param app  il guscio, per le guardie che solo lui conosce. */
  constructor(app) {
    this.app = app;
    this.pista = document.getElementById('casa-pista');
    /** Le pagine aggiunte, come le ha salvate il server. */
    this.schermate = [];
    this.fisse = [...FISSE];
    /** Dove sta ogni pagina: gli id delle fisse e delle schermate. */
    this.ordine = ordineNormale([], [], this.fisse);
    this.indice = this.indiceChat;
    this._tetto = 8;
    this._staccaGesto = null;
    /** Chi accende e spegne una pagina fissa: `{accendi, spegni}` per id. */
    this._ganci = {};
    /** La pagina fissa accesa adesso, per spegnerla quando la lasci. */
    this._accesa = null;
    /** La conversazione della pagina chat — la chat «libera», quella che
     *  scegli dai Quaderni. Le pagine conversazione hanno la loro nel `ref`;
     *  questa non sta nell'elenco salvato. Parte da quella che la chat mostra
     *  all'avvio, cioe' la personale. */
    this.conversazioneCasa = app?.chiaveAttuale?.() || null;

    if (this.pista) {
      /* Si parte sulla chat anche prima che il server abbia detto l'ordine:
         nell'HTML la chat e' la seconda, e senza questo il primo fotogramma
         sarebbe il cassetto. Solo il `transform` — `vaiA` vorrebbe un guscio
         gia' costruito, e qui lo si sta ancora costruendo. */
      this.pista.style.transform = `translateX(${-this.indice * 100}%)`;
      this._armaGesto();
    }
  }

  /** Quante caselle ha la pista. */
  get quante() {
    return this.ordine.length;
  }

  /** Dove sta la chat adesso. */
  get indiceChat() {
    return Math.max(0, this.ordine.indexOf('chat'));
  }

  /** Dove sta la pagina `id`, o -1. */
  indiceDi(id) {
    return this.ordine.indexOf(id);
  }

  /** Se il tetto e' pieno non si puo' aggiungere: lo chiedono le schede. */
  get pienoZeppo() {
    return this.schermate.length >= this._tetto;
  }

  /** Cosa c'e' nella casella `i`: `{id, kind, fissa}`, piu' il `ref` di una
   *  schermata. `null` fuori dalla pista. */
  voce(i) {
    const id = this.ordine[i];
    if (id === undefined) return null;
    if (this.fisse.includes(id)) return { id, kind: SPECIE_FISSA[id] || id, fissa: true };
    const s = this.schermate.find((x) => x.id === id);
    return s ? { ...s, fissa: false } : null;
  }

  /** Tutte le voci, in ordine: la fila le disegna, la modalita' ordina le sposta. */
  get voci() {
    return this.ordine.map((_, i) => this.voce(i)).filter(Boolean);
  }

  /** Una pagina fissa dice come accendersi quando la guardi e spegnersi
   *  quando la lasci. Il cassetto legge le app, le impostazioni il server. */
  registra(id, ganci) {
    this._ganci[id] = ganci;
    /* Se la si sta gia' guardando, si accende adesso: il `vaiA` che ci ha
       portato qui e' passato quando il gancio non c'era ancora. */
    if (this.ordine[this.indice] === id) this._accendiFissa(id);
  }

  /** Legge l'elenco dal server e disegna. Non alza: una casa che non apre
   *  perche' non ha saputo leggere le sue pagine e' peggio di una casa con le
   *  sole quattro. */
  async carica() {
    try {
      this._prendi(await api.getSchermate());
    } catch {
      this._prendi(null);
    }
    this._disegna();
    this.vaiA(this.indiceChat, { animato: false });
  }

  _prendi(dati) {
    this.schermate = Array.isArray(dati?.schermate) ? dati.schermate : [];
    if (Array.isArray(dati?.fisse) && dati.fisse.includes('chat')) this.fisse = dati.fisse;
    if (Number.isFinite(dati?.max)) this._tetto = dati.max;
    this.ordine = ordineNormale(dati?.ordine, this.schermate, this.fisse);
  }

  /** Salva tutto — aggiungere, togliere e spostare sono la stessa scrittura,
   *  e mandarla intera toglie di mezzo il caso in cui due scritture si
   *  incrociano lasciando un ordine che nessuno ha chiesto.
   *
   *  Dopo, si resta **sulla pagina in cui si era**, ovunque sia finita; se non
   *  c'e' piu', sulla chat. */
  async salva(schermate, ordine) {
    const dove = this.ordine[this.indice];
    const salvate = await api.salvaPagine(schermate, ordineNormale(ordine, schermate, this.fisse));
    this.schermate = salvate.schermate || [];
    this.ordine = ordineNormale(salvate.ordine, this.schermate, this.fisse);
    this._disegna();
    const ancora = this.indiceDi(dove);
    this.vaiA(ancora >= 0 ? ancora : this.indiceChat, { animato: false });
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
    this._accendiSolo(bersaglio);
    const voce = this.voce(bersaglio);
    /* La chat arriva **prima** che l'intestazione si ridisegni: il trasloco
       cambia conversazione subito, e la fila deve gia' leggere quella nuova. */
    const chiave = this.conversazioneDi(bersaglio);
    if (chiave) this.app?.trasloco?.arriva(this.pannelloDi(bersaglio), chiave);
    if (voce?.kind === 'conversazione') this._controllaQuaderno(this.pannelloDi(bersaglio), voce);
    this.app?.onPaginaCambiata?.(bersaglio, voce);
  }

  /** Va alla pagina `id`. */
  vaiAId(id, opts) {
    const i = this.indiceDi(id);
    if (i >= 0) this.vaiA(i, opts);
  }

  /** Quale conversazione mostra la casella `i`, o `null` se non e' di chat.
   *
   *  La chat ha la sua (`conversazioneCasa`), una pagina conversazione il suo
   *  quaderno; il resto nessuna — attraversarle non cambia la chat.
   */
  conversazioneDi(i) {
    const voce = this.voce(i);
    if (voce?.kind === 'chat') return this.conversazioneCasa;
    return voce?.kind === 'conversazione' ? voce.ref : null;
  }

  /** Il pannello della casella `i`. */
  pannelloDi(i) {
    const id = this.ordine[i];
    return id === undefined ? null : this._pannelloPer(id);
  }

  _pannelloPer(id) {
    if (!this.pista) return null;
    const figli = Array.from(this.pista.children);
    if (this.fisse.includes(id)) return figli.find((c) => c.dataset?.pagina === id) || null;
    return figli.find((c) => c.dataset?.id === id) || null;
  }

  /** Da fuori — i Quaderni, Home, un avviso — si chiede una conversazione.
   *
   *  **Una pagina conversazione mostra solo il suo quaderno**: e' l'invariante
   *  di tutto il disegno. Quindi se la conversazione chiesta non e' quella
   *  della pagina in cui sei, la si apre nella pagina chat, e ci si va. Le
   *  strade che cambiano conversazione sono gia' cinque (i Quaderni, nuovo
   *  quaderno, Home, Indietro, un avviso) e la sesta arrivera'.
   *
   *  Dalla pagina chat si apre li', anche un quaderno che ha una pagina sua:
   *  deciso dall'utente il 23/09/2026 — «fai come ora, non scorrere».
   *
   *  Torna la promessa del cambio, e non per scrupolo: chi chiama ci manda
   *  subito dopo un messaggio, e deve finire nella conversazione giusta.
   */
  apriConversazione(chiave) {
    const qui = this.voce(this.indice);
    if (qui?.kind === 'conversazione' && chiave === qui.ref) {
      return this.app?.mostraConversazione?.(chiave);
    }
    this.conversazioneCasa = chiave;
    if (qui?.kind === 'chat') return this.app?.mostraConversazione?.(chiave);
    this.vaiA(this.indiceChat);
    return this.app?.trasloco?.lettura;
  }

  /* ── Appendere e staccare ───────────────────────────────────────────── */
  /* Si appende **dal posto dove la cosa vive** — l'app dal cassetto, il
     quaderno dai Quaderni — con una pressione lunga, come ogni launcher
     Android fa «aggiungi alla schermata principale». Queste sono l'unica
     porta: le schede chiedono, e qui si decide. */

  /** E' gia' una pagina? */
  appesa(kind, ref) {
    return this.schermate.some((s) => s.kind === kind && s.ref === ref);
  }

  /** La appende e ci porta sopra. `false` se c'era gia' o se il tetto e' pieno.
   *
   *  **Dove va:** dopo l'ultima pagina aggiunta, o subito dopo la chat se non
   *  ce n'e'. Le aggiunte restano vicine fra loro anche dopo che l'utente ha
   *  spostato le fisse, ed e' il posto in cui le cercava prima dell'ordine.
   *
   *  **Ci si atterra**: chi l'ha appena aggiunta vuole vederla, e lasciarlo
   *  dov'era gli farebbe credere che non sia successo niente.
   */
  async appendi(kind, ref) {
    if (this.appesa(kind, ref) || this.pienoZeppo) return false;
    const id = `p${Date.now().toString(36)}`;
    const aggiunte = this.ordine
      .map((x, i) => (this.fisse.includes(x) ? -1 : i))
      .filter((i) => i >= 0);
    const dopo = aggiunte.length ? Math.max(...aggiunte) : this.indiceChat;
    const ordine = [...this.ordine];
    ordine.splice(dopo + 1, 0, id);
    await this.salva([...this.schermate, { id, kind, ref }], ordine);
    this.vaiAId(id);
    return true;
  }

  /** La stacca. Niente conferma: una pagina si rimette con una pressione, e
   *  una domanda per un gesto annullabile e' solo un tocco in piu' ogni volta. */
  async stacca(kind, ref) {
    const via = this.schermate.find((s) => s.kind === kind && s.ref === ref);
    if (!via) return false;
    await this.salva(
      this.schermate.filter((s) => s !== via),
      this.ordine.filter((id) => id !== via.id),
    );
    return true;
  }

  /** Un quaderno ha cambiato nome: la pagina chat lo segue, se era il suo.
   *  Le pagine appese le ha gia' rinominate il gateway; la conversazione della
   *  pagina chat non sta nell'elenco salvato, e senza questa resterebbe
   *  puntata a un nome che non c'e' piu'. */
  rinominaConversazione(vecchia, nuova) {
    if (this.conversazioneCasa === vecchia) this.conversazioneCasa = nuova;
  }

  /** Rilegge dal server **senza** spostarti.
   *
   *  Serve dopo una cancellazione: il gateway ha tolto la pagina insieme alla
   *  cosa, e qui bisogna saperlo. `carica()` qui sarebbe sbagliato — riporta
   *  sempre alla chat, cioe' ti sposta anche quando la tua pagina c'e' ancora.
   */
  async ricarica() {
    let dati;
    try {
      dati = await api.getSchermate();
    } catch {
      return;
    }
    const dove = this.ordine[this.indice];
    this._prendi(dati);
    this._disegna();
    const ancora = this.indiceDi(dove);
    this.vaiA(ancora >= 0 ? ancora : this.indiceChat, { animato: false });
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  /** Un pannello per schermata, e tutti in fila nell'ordine.
   *
   *  I pannelli fissi non si toccano mai: sono nell'HTML, ci vivono la chat,
   *  il cassetto, i quaderni e le impostazioni, e ridisegnarli vorrebbe dire
   *  buttare via la conversazione a ogni salvataggio. **Si spostano soltanto**,
   *  e solo quelli fuori posto: spostare un nodo che e' gia' dove deve stare
   *  gli costerebbe il fuoco e lo scorrimento per niente.
   */
  _disegna() {
    if (!this.pista) return;
    /* Prima di buttare un pannello, si riprende la chat se era parcheggiata li':
       un `remove()` secco la porterebbe via insieme al pannello — cioe' filo,
       composer e bozza (trovato il 23/09/2026, quando le pagine tenevano anche
       le stanze del guscio e perdevano pure quelle). */
    const casa = this._pannelloPer('chat');
    for (const vecchio of Array.from(this.pista.children).filter((c) => c.dataset?.id)) {
      this.app?.trasloco?.riportaACasa(vecchio, casa);
      vecchio.remove();
    }
    this.ordine.forEach((id, i) => {
      let pannello = this._pannelloPer(id);
      if (!pannello) {
        const s = this.schermate.find((x) => x.id === id);
        if (!s) return;
        pannello = document.createElement('div');
        pannello.className = 'casa-pagina';
        pannello.dataset.id = s.id;
        pannello.dataset.kind = s.kind;
      }
      const qui = this.pista.children[i];
      if (qui !== pannello) this.pista.insertBefore(pannello, qui || null);
    });
    this.app?.onPagineCambiate?.();
  }

  /** «Resta viva solo la pagina che guardi: le altre si spengono, o te le
   *  paghi in batteria» (tavola `PagineGestione`).
   *
   *  Non «la corrente piu' le due vicine»: tre `<iframe>` che girano insieme
   *  su un telefono sono tre app vive, e la regola della tavola e' piu'
   *  stretta **e** piu' semplice. Il prezzo e' che entrando in una pagina
   *  l'app riparte; il prezzo dell'altra scelta lo paga la batteria sempre.
   *
   *  Vale anche per le fisse, a modo loro: il cassetto ascolta la tastiera solo
   *  mentre lo guardi, e le impostazioni si rileggono quando ci arrivi.
   */
  _accendiSolo(indice) {
    if (!this.pista) return;
    const corrente = this.pannelloDi(indice);
    for (const pannello of this._pannelli()) {
      if (pannello === corrente) this._riempi(pannello);
      else this._svuota(pannello);
    }
    const id = this.ordine[indice];
    if (this._accesa && this._accesa !== id) {
      const prima = this._accesa;
      this._accesa = null;
      this._ganci[prima]?.spegni?.();
    }
    if (this.fisse.includes(id) && this._accesa !== id) this._accendiFissa(id);
  }

  _accendiFissa(id) {
    this._accesa = id;
    this._ganci[id]?.accendi?.();
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
      /* La cornice si monta **subito**, e intanto si chiede se l'app c'e'
         ancora: aspettare l'elenco prima di montare vorrebbe dire una pagina
         vuota a ogni ingresso, per un caso raro. Sul telefono l'elenco e' gia'
         in cache e la risposta arriva prima che l'app abbia dipinto. */
      this._controllaApp(pannello, schermata, mio);
      this._ascoltaDatiApp();
    }
  }

  /** La pagina app che si guarda si rilegge quando i suoi dati cambiano da
   *  fuori (Jenny ha girato una sua azione): `jenny:data-changed` e' cio' che
   *  `jenny-sdk.js` ascolta. Come la mini-app sopra tutto in `apps-actions.js`,
   *  ma la cornice e' di questo file — v. `_finestraPagina`.
   *
   *  Ci si iscrive una volta sola, alla prima pagina app riempita: prima non
   *  c'e' nessuna cornice da avvisare, e chiedere la sorgente al boot la
   *  costruirebbe per niente. Solo la pagina corrente e' viva, quindi si avvisa
   *  lei e solo se e' l'app di cui si parla.
   */
  _ascoltaDatiApp() {
    if (this._staccaDatiApp) return;
    const fonte = this.app?.appsSource?.();
    if (!fonte?.onAppDataChanged) return;
    this._staccaDatiApp = fonte.onAppDataChanged((slug) => {
      const voce = this.voce(this.indice);
      if (voce?.kind !== 'app' || voce.ref !== slug) return;
      this._finestraPagina()?.postMessage({ type: 'jenny:data-changed', slug }, '*');
    });
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
    /* L'avviso se n'e' andato col resto: al prossimo ingresso lo rimette, se
       serve, il controllo dell'app. */
    delete pannello.dataset.sparita;
  }

  /* ── La pagina di una cosa che non c'e' piu' ─────────────────────────── */
  /* Cancellata dalla sua scheda, la cosa si porta via la pagina (lo fa il
     gateway). Ma un'app o un quaderno possono sparire anche per altre strade —
     Jenny, una mano sui file — e allora la pagina resta: **toglierla per conto
     proprio sarebbe una decisione presa dal codice al posto dell'utente**. La
     pagina lo dice, e offre di togliersi. Il foglio delle pagine che lo faceva
     non c'e' piu', e senza questo una pagina verso un quaderno sparito si
     toglierebbe solo dalla modalita' ordina, che da qui non si vede.

     Una lettura che fallisce non segna niente: «non lo so» non e' «sparito». */

  async _controllaApp(pannello, schermata, mio) {
    const fonte = this.app?.appsSource?.();
    if (!fonte?.attendiJennyApps) return;
    let elenco;
    try {
      elenco = await fonte.attendiJennyApps();
    } catch {
      return;
    }
    if (fonte.jennyListFailed?.()) return;
    if (pannello.dataset.pieno !== mio) return;       // nel frattempo sei uscito
    if (elenco.some((a) => a.slug === schermata.ref)) return;
    pannello.textContent = '';                        // via la cornice verso il nulla
    this._sparita(pannello, schermata);
  }

  async _controllaQuaderno(pannello, schermata) {
    if (!pannello || !schermata) return;
    let nomi;
    try {
      const dati = await api.listProjects();
      nomi = new Set((dati?.projects || []).map((q) => q?.name));
    } catch {
      return;
    }
    if (nomi.has(projectNameOf(schermata.ref))) this._togliSparita(pannello);
    else this._sparita(pannello, schermata);
  }

  /** L'avviso, **sopra** quel che c'e' nel pannello e non al suo posto: in una
   *  pagina quaderno sotto c'e' la chat, portata dal trasloco, e toccarla da
   *  qui vorrebbe dire rompere un meccanismo che ha le sue regole.
   *
   *  Coprirla ferma il dito, **non la tastiera**: sul Titan il campo sotto
   *  l'avviso teneva il fuoco (glielo rimette ogni arrivo su una pagina con la
   *  chat) e i tasti scrivevano a `project:<cancellato>`. Il pannello porta
   *  quindi un segno che il guscio legge (`sparitaQui`) prima di dare o
   *  lasciare il fuoco al campo, e gli si dice che la pagina e' cambiata. */
  _sparita(pannello, schermata) {
    this._togliSparita(pannello);
    const scheda = document.createElement('div');
    scheda.className = 'casa-pagina-sparita';
    const testo = document.createElement('p');
    testo.textContent = i18n.t(
      schermata.kind === 'app' ? 'casa.pagine.appSparita' : 'casa.pagine.quadernoSparito',
      { nome: this.nomeDi(schermata) },
    );
    const togli = document.createElement('button');
    togli.type = 'button';
    togli.className = 'casa-pagina-sparita-togli';
    togli.textContent = i18n.t('casa.pagine.togliPagina');
    togli.addEventListener('click', () => this.stacca(schermata.kind, schermata.ref));
    scheda.append(testo, togli);
    pannello.appendChild(scheda);
    pannello.dataset.sparita = '1';
    this.app?.onSparitaCambiata?.(pannello);
  }

  _togliSparita(pannello) {
    for (const c of Array.from(pannello.children)) {
      if (c.className === 'casa-pagina-sparita') c.remove();
    }
    if (!pannello.dataset.sparita) return;
    delete pannello.dataset.sparita;
    this.app?.onSparitaCambiata?.(pannello);
  }

  /** La pagina a schermo dice che la sua cosa non c'e' piu'? Il guscio lo
   *  chiede prima di dare il fuoco al campo o di mandare un messaggio. */
  sparitaQui() {
    return Boolean(this.pannelloDi(this.indice)?.dataset?.sparita);
  }

  /** I pannelli delle pagine aggiunte, in ordine. Quelli fissi non ci sono:
   *  hanno `data-pagina` e non `data-id`. */
  _pannelli() {
    if (!this.pista) return [];
    return Array.from(this.pista.children).filter((c) => c.dataset?.id);
  }

  /** Le guardie del guscio: si puo' cambiare pagina adesso?
   *
   *  Stanno in un posto solo perche' le strade che ci arrivano sono due — il
   *  dito sulla pista e il dito dentro una app — e una seconda copia
   *  divergerebbe al primo caso particolare.
   */
  _puoScorrere() {
    // Fuori dalla chat comandano le stanze, non le pagine.
    if (this.app?.view && this.app.view !== 'chat') return false;
    // Mentre si spostano le pagine il dito e' della modalita' ordina.
    if (this.app?.fila?.ordinando) return false;
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
           dodici pagine non si sa piu' dove si e'. */
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

  /** La finestra della pagina che si sta guardando, se e' una app appesa.
   *
   *  Serve a una cosa sola: **riconoscere chi parla**. Solo la pagina corrente
   *  e' viva (v. `_accendiSolo`), quindi questa e' l'unica finestra da cui un
   *  gesto possa arrivare davvero.
   *
   *  Le pagine fisse sono fuori per costruzione: non sono in `_pannelli()`. E
   *  una pagina quaderno ospita la chat, che una `contentWindow` non ce l'ha:
   *  controllare anche la specie vorrebbe dire due regole da tenere d'accordo.
   */
  _finestraPagina() {
    const pannello = this.pannelloDi(this.indice);
    if (!pannello?.dataset?.id) return null;
    const cornice = pannello.children[0] || pannello.firstElementChild;
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
   *  una volta per sempre: fra un gesto e l'altro puo' essere cambiato tutto.
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

  /** Come si chiama una pagina aggiunta, per la fila e per l'avviso «non c'e'
   *  piu'».
   *
   *  Un quaderno porta il suo nome nel riferimento. Un'app porta lo slug, e il
   *  nome vero lo sa l'elenco delle app se e' gia' stato letto: fino ad
   *  allora lo slug, che e' comunque quel che l'utente ha visto nascere.
   */
  nomeDi(schermata) {
    if (!schermata) return '';
    if (schermata.kind === 'conversazione') return projectNameOf(schermata.ref) || schermata.ref;
    return this.app?.nomeApp?.(schermata.ref) || schermata.ref;
  }
}
