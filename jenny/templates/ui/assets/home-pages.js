/** La casa — la pista delle pagine.
 *
 *  La casa e' un launcher: una fila di pagine che si scorrono, e i loro nomi in
 *  alto (v. `home-strip.js`). **Quattro ci sono sempre** — il cassetto delle app,
 *  la chat, i quaderni, le impostazioni — e accanto ci sono quelle che l'utente
 *  ha aggiunto. **Tutte si spostano**, la chat compresa: dal 23/09/2026 la chat
 *  non e' piu' la pagina 0, e nessun indice qui dentro vuol dire «la chat» da
 *  solo (v. `.agent/pagine-in-alto-plan.md`). Dove sta ogni pagina lo dice
 *  `order`, che il gateway salva accanto alle `pages`.
 *
 *  **Che cosa muove.** Una pista in fila orizzontale, un pannello per pagina,
 *  e un `translateX`. Non e' la stessa risposta visiva del carosello
 *  dell'officina — li' si trascina la vista corrente con una sbirciata
 *  smorzata e la vicina non si disegna mai — e non per capriccio: la tavola
 *  mostra la pagina di fianco che **entra davvero**, affiancata, non che
 *  sbircia. Quel che i due gusci condividono e' il *riconoscimento* del gesto
 *  (`shared/horizontal-swipe.js`), non la risposta.
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
import { showToast } from './shared/utils.js';
import { watchHorizontalSwipe } from './shared/horizontal-swipe.js';
import { frameForApp } from './shared/apps-actions.js';
import { projectNameOf } from './shared/conversation-list.js';

/** Quanto la pista si lascia tirare oltre il capo, in frazione di schermo.
 *  Serve a dire «di la' non c'e' niente» col dito invece che con un blocco
 *  secco, che sembra un difetto. */
const BEYOND_NEWLINE = 0.06;

/** Le pagine che ci sono sempre, nell'ordine di chi non ha mai spostato
 *  niente. E' la copia di `FIXED_PAGES` dello schema, e serve solo finche' il
 *  server non ha risposto: la risposta porta le sue, e vincono quelle. */
export const FIXED_PAGES = ['app', 'chat', 'notebooks', 'settings'];

/** La specie di una pagina fissa. Il cassetto non si chiama `app` qui dentro:
 *  `app` e' gia' la specie di una **Jenny App** appesa, e due cose diverse con
 *  lo stesso nome si confondono al primo `if`. */
const FIXED_KINDS = { app: 'drawer', chat: 'chat', notebooks: 'notebooks', settings: 'settings' };

/** L'ordine reso coerente con le pagine che ci sono. La stessa regola di
 *  `normalize_order` nello schema, per quando il server non l'ha detta (una
 *  lettura fallita, un gateway vecchio): mai un ordine che perde una pagina. */
export function normalizeOrder(order, pages, fixed = FIXED_PAGES) {
  const ids = pages.map((s) => s.id);
  const valid = new Set([...fixed, ...ids]);
  const seen = [];
  for (const v of Array.isArray(order) ? order : []) {
    if (typeof v === 'string' && valid.has(v) && !seen.includes(v)) seen.push(v);
  }
  if (!seen.length) {
    const i = fixed.indexOf('chat') + 1;
    return [...fixed.slice(0, i), ...ids, ...fixed.slice(i)];
  }
  for (const f of fixed) if (!seen.includes(f)) seen.push(f);
  const missing = ids.filter((id) => !seen.includes(id));
  const after = seen.indexOf('chat') + 1;
  return [...seen.slice(0, after), ...missing, ...seen.slice(after)];
}


export class HomePages {
  /** @param app  il guscio, per le guardie che solo lui conosce. */
  constructor(app) {
    this.app = app;
    this.track = document.getElementById('home-track');
    /** Le pagine aggiunte, come le ha salvate il server. */
    this.pages = [];
    this.fixed = [...FIXED_PAGES];
    /** Dove sta ogni pagina: gli id delle fisse e delle schermate. */
    this.order = normalizeOrder([], [], this.fixed);
    this.index = this.chatIndex;
    this._cap = 8;
    /** Chi accende e spegne una pagina fissa: `{activate, deactivate}` per id. */
    this._hooks = {};
    /** La pagina fissa accesa adesso, per spegnerla quando la lasci. */
    this._active = null;
    /** La conversazione della pagina chat — la chat «libera», quella che
     *  scegli dai Quaderni. Le pagine conversazione hanno la loro nel `ref`;
     *  questa non sta nell'elenco salvato. Parte da quella che la chat mostra
     *  all'avvio, cioe' la personale. */
    this.homeConversation = app?.currentKey?.() || null;

    if (this.track) {
      /* Si parte sulla chat anche prima che il server abbia detto l'ordine:
         nell'HTML la chat e' la seconda, e senza questo il primo fotogramma
         sarebbe il cassetto. Solo il `transform` — `goTo` vorrebbe un guscio
         gia' costruito, e qui lo si sta ancora costruendo. */
      this.track.style.transform = `translateX(${-this.index * 100}%)`;
      this._armSwipe();
    }
  }

  /** Quante caselle ha la pista. */
  get howMany() {
    return this.order.length;
  }

  /** Dove sta la chat adesso. */
  get chatIndex() {
    return Math.max(0, this.order.indexOf('chat'));
  }

  /** Dove sta la pagina `id`, o -1. */
  indexOf(id) {
    return this.order.indexOf(id);
  }

  /** Se il tetto e' pieno non si puo' aggiungere: lo chiedono le schede. */
  get full() {
    return this.pages.length >= this._cap;
  }

  /** Cosa c'e' nella casella `i`: `{id, kind, fixed}`, piu' il `ref` di una
   *  schermata. `null` fuori dalla pista. */
  entry(i) {
    const id = this.order[i];
    if (id === undefined) return null;
    if (this.fixed.includes(id)) return { id, kind: FIXED_KINDS[id] || id, fixed: true };
    const s = this.pages.find((x) => x.id === id);
    return s ? { ...s, fixed: false } : null;
  }

  /** Tutte le voci, in ordine: la fila le disegna, la modalita' ordina le sposta. */
  get entries() {
    return this.order.map((_, i) => this.entry(i)).filter(Boolean);
  }

  /** Una pagina fissa dice come accendersi quando la guardi e spegnersi
   *  quando la lasci. Il cassetto legge le app, le impostazioni il server. */
  register(id, hooks) {
    this._hooks[id] = hooks;
    /* Se la si sta gia' guardando, si accende adesso: il `goTo` che ci ha
       portato qui e' passato quando il gancio non c'era ancora. */
    if (this.order[this.index] === id) this._activateFixed(id);
  }

  /** Legge l'elenco dal server e disegna. Non alza: una casa che non apre
   *  perche' non ha saputo leggere le sue pagine e' peggio di una casa con le
   *  sole quattro. */
  async load() {
    try {
      this._take(await api.getPages());
    } catch {
      this._take(null);
    }
    this._draw();
    this.goTo(this.chatIndex, { animated: false });
  }

  _take(data) {
    this.pages = Array.isArray(data?.pages) ? data.pages : [];
    if (Array.isArray(data?.fixed) && data.fixed.includes('chat')) this.fixed = data.fixed;
    if (Number.isFinite(data?.max)) this._cap = data.max;
    this.order = normalizeOrder(data?.order, this.pages, this.fixed);
  }

  /** Salva tutto — aggiungere, togliere e spostare sono la stessa scrittura,
   *  e mandarla intera toglie di mezzo il caso in cui due scritture si
   *  incrociano lasciando un ordine che nessuno ha chiesto.
   *
   *  Dopo, si resta **sulla pagina in cui si era**, ovunque sia finita; se non
   *  c'e' piu', sulla chat.
   *
   *  **Non alza**: una scrittura rifiutata lo dice con un avviso e torna
   *  `false`, e la pista resta com'era. Prima l'errore saliva a chi chiamava,
   *  e nessuno lo prendeva: «Fatto» in modalita' ordina perdeva l'ordine in
   *  silenzio, «Metti/Togli pagina» non diceva niente. Chi ha qualcosa da
   *  tenere da parte (la bozza dell'ordine) la tiene finche' non torna vero. */
  async save(pages, order) {
    const where = this.order[this.index];
    let saved;
    try {
      saved = await api.savePages(pages, normalizeOrder(order, pages, this.fixed));
    } catch (err) {
      console.warn('home.homePages: pages not saved', err);
      showToast(i18n.t('home.pages.saveFailed'), 'error');
      return false;
    }
    this.pages = saved.pages || [];
    this.order = normalizeOrder(saved.order, this.pages, this.fixed);
    this._draw();
    const again = this.indexOf(where);
    this.goTo(again >= 0 ? again : this.chatIndex, { animated: false });
    return saved;
  }


  /** Va alla casella `i`, se esiste. */
  goTo(i, { animated = true } = {}) {
    if (!this.track) return;
    const target = Math.max(0, Math.min(i, this.howMany - 1));
    this.index = target;
    this.track.style.transition = animated
      ? 'transform .22s cubic-bezier(.22,.61,.36,1)'
      : 'none';
    this.track.style.transform = `translateX(${-target * 100}%)`;
    this._activateOnly(target);
    const entry = this.entry(target);
    /* La chat arriva **prima** che l'intestazione si ridisegni: il trasloco
       cambia conversazione subito, e la fila deve gia' leggere quella nuova. */
    const key = this.conversationOf(target);
    if (key) this.app?.chatMove?.arrives(this.panelOf(target), key);
    if (entry?.kind === 'conversation') this._checkNotebook(this.panelOf(target), entry);
    this.app?.onPageChanged?.(target, entry);
  }

  /** Va alla pagina `id`. */
  goToId(id, opts) {
    const i = this.indexOf(id);
    if (i >= 0) this.goTo(i, opts);
  }

  /** Quale conversazione mostra la casella `i`, o `null` se non e' di chat.
   *
   *  La chat ha la sua (`homeConversation`), una pagina conversazione il suo
   *  quaderno; il resto nessuna — attraversarle non cambia la chat.
   */
  conversationOf(i) {
    const entry = this.entry(i);
    if (entry?.kind === 'chat') return this.homeConversation;
    return entry?.kind === 'conversation' ? entry.ref : null;
  }

  /** Il pannello della casella `i`. */
  panelOf(i) {
    const id = this.order[i];
    return id === undefined ? null : this._panelFor(id);
  }

  _panelFor(id) {
    if (!this.track) return null;
    const children = Array.from(this.track.children);
    if (this.fixed.includes(id)) return children.find((c) => c.dataset?.page === id) || null;
    return children.find((c) => c.dataset?.id === id) || null;
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
  openConversation(key) {
    const here = this.entry(this.index);
    if (here?.kind === 'conversation' && key === here.ref) {
      return this.app?.showConversation?.(key);
    }
    this.homeConversation = key;
    if (here?.kind === 'chat') return this.app?.showConversation?.(key);
    this.goTo(this.chatIndex);
    return this.app?.chatMove?.read;
  }

  /* ── Appendere e staccare ───────────────────────────────────────────── */
  /* Si appende **dal posto dove la cosa vive** — l'app dal cassetto, il
     quaderno dai Quaderni — con una pressione lunga, come ogni launcher
     Android fa «aggiungi alla schermata principale». Queste sono l'unica
     porta: le schede chiedono, e qui si decide. */

  /** E' gia' una pagina? */
  pending(kind, ref) {
    return this.pages.some((s) => s.kind === kind && s.ref === ref);
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
  async append(kind, ref) {
    if (this.pending(kind, ref) || this.full) return false;
    const id = `p${Date.now().toString(36)}`;
    const added = this.order
      .map((x, i) => (this.fixed.includes(x) ? -1 : i))
      .filter((i) => i >= 0);
    const after = added.length ? Math.max(...added) : this.chatIndex;
    const order = [...this.order];
    order.splice(after + 1, 0, id);
    if (!(await this.save([...this.pages, { id, kind, ref }], order))) return false;
    this.goToId(id);
    return true;
  }

  /** La stacca. Niente conferma: una pagina si rimette con una pressione, e
   *  una domanda per un gesto annullabile e' solo un tocco in piu' ogni volta. */
  async detach(kind, ref) {
    const via = this.pages.find((s) => s.kind === kind && s.ref === ref);
    if (!via) return false;
    const saved = await this.save(
      this.pages.filter((s) => s !== via),
      this.order.filter((id) => id !== via.id),
    );
    return Boolean(saved);
  }

  /** Un quaderno ha cambiato nome: la pagina chat lo segue, se era il suo.
   *  Le pagine appese le ha gia' rinominate il gateway; la conversazione della
   *  pagina chat non sta nell'elenco salvato, e senza questa resterebbe
   *  puntata a un nome che non c'e' piu'. */
  renameConversation(oldKey, newKey) {
    if (this.homeConversation === oldKey) this.homeConversation = newKey;
  }

  /** Rilegge dal server **senza** spostarti.
   *
   *  Serve dopo una cancellazione: il gateway ha tolto la pagina insieme alla
   *  cosa, e qui bisogna saperlo. `load()` qui sarebbe sbagliato — riporta
   *  sempre alla chat, cioe' ti sposta anche quando la tua pagina c'e' ancora.
   */
  async reload() {
    let data;
    try {
      data = await api.getPages();
    } catch {
      return;
    }
    const where = this.order[this.index];
    this._take(data);
    this._draw();
    const again = this.indexOf(where);
    this.goTo(again >= 0 ? again : this.chatIndex, { animated: false });
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
  _draw() {
    if (!this.track) return;
    /* Prima di buttare un pannello, si riprende la chat se era parcheggiata li':
       un `remove()` secco la porterebbe via insieme al pannello — cioe' filo,
       composer e bozza (trovato il 23/09/2026, quando le pagine tenevano anche
       le stanze del guscio e perdevano pure quelle). */
    const home = this._panelFor('chat');
    for (const oldValue of Array.from(this.track.children).filter((c) => c.dataset?.id)) {
      this.app?.chatMove?.bringBackHome(oldValue, home);
      oldValue.remove();
    }
    this.order.forEach((id, i) => {
      let panel = this._panelFor(id);
      if (!panel) {
        const s = this.pages.find((x) => x.id === id);
        if (!s) return;
        panel = document.createElement('div');
        panel.className = 'home-page';
        panel.dataset.id = s.id;
        panel.dataset.kind = s.kind;
      }
      const here = this.track.children[i];
      if (here !== panel) this.track.insertBefore(panel, here || null);
    });
    this.app?.onPagesChanged?.();
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
  _activateOnly(index) {
    if (!this.track) return;
    const corrente = this.panelOf(index);
    /* Fuori schermo non vuol dire fuori portata: senza `inert` il Tab della
       tastiera fisica, e chi legge lo schermo, finivano nelle pagine accanto —
       un campo di ricerca, un interruttore delle impostazioni — che si
       attivavano senza vederle. Vale per tutti i pannelli, fissi compresi. */
    for (const panel of Array.from(this.track.children)) {
      panel.inert = panel !== corrente;
    }
    for (const panel of this._panels()) {
      if (panel === corrente) this._fill(panel);
      else this._empty(panel);
    }
    const id = this.order[index];
    if (this._active && this._active !== id) {
      const before = this._active;
      this._active = null;
      this._hooks[before]?.deactivate?.();
    }
    if (this.fixed.includes(id) && this._active !== id) this._activateFixed(id);
  }

  _activateFixed(id) {
    this._active = id;
    this._hooks[id]?.activate?.();
  }

  /** Il contenuto di una pagina, costruito adesso perche' adesso si guarda. */
  async _fill(panel) {
    /* `full` porta il **numero del tentativo**, non un `1`: qui basta che sia
       valorizzato. Confrontarlo con `'1'` — com'era finche' il numero non
       c'era — avrebbe lasciato passare ogni rientro dal secondo in poi. */
    /* Una pagina conversazione non si riempie: ci arriva la chat, e la porta
       il trasloco da `goTo`. */
    if (panel.dataset.kind === 'conversation') return;
    if (panel.dataset.full) return;
    const page = this.pages.find((x) => x.id === panel.dataset.id);
    if (!page) return;
    /* Un segno **per tentativo**, non un flag condiviso.
       Un dito veloce fra due pagine fa: riempi → svuota → riempi. Il primo
       tentativo e' fermo sull'attesa del segreto; quando riparte trova la
       pagina di nuovo «piena» — ma piena per colpa del *secondo* — e monta
       lui pure. Due cornici, cioe' la stessa app viva due volte.
       Col numero di tentativo ognuno riconosce se e' ancora il suo giro.
       Misurato dal banco, non ipotizzato (22/09/2026). */
    const mine = String((this._attempt = (this._attempt || 0) + 1));
    panel.dataset.full = mine;
    if (page.kind === 'app') {
      /* Il segreto **prima** della cornice: l'indirizzo se lo porta dentro, e
         costruirla senza vorrebbe dire un `token=undefined`, cioe' un 401 e
         una pagina bianca. `openApp` questa guardia ce l'ha da sempre; qui si
         era persa estraendo la cornice. */
      if (!api.getSecret()) {
        try { await api.bootstrap(); } catch {
          if (panel.dataset.full === mine) panel.dataset.full = '';
          return;
        }
      }
      /* E il token **dell'app**, non il segreto: la cornice se lo porta
         nell'indirizzo, e il segreto aprirebbe all'app l'intera API del
         gateway (v. `frameForApp`). */
      let token;
      try { token = await api.appToken(page.ref); } catch {
        if (panel.dataset.full === mine) panel.dataset.full = '';
        return;
      }
      /* Nel frattempo si puo' essere usciti dalla pagina, o rientrati: in tutti
         e due i casi il giro buono non e' piu' il nostro. */
      if (panel.dataset.full !== mine) return;
      const frame = frameForApp(page.ref, { token });
      frame.className = 'home-page-app';
      panel.appendChild(frame);
      /* La cornice si monta **subito**, e intanto si chiede se l'app c'e'
         ancora: aspettare l'elenco prima di montare vorrebbe dire una pagina
         vuota a ogni ingresso, per un caso raro. Sul telefono l'elenco e' gia'
         in cache e la risposta arriva prima che l'app abbia dipinto. */
      this._checkApp(panel, page, mine);
      this._listenAppData();
    }
  }

  /** La pagina app che si guarda si rilegge quando i suoi dati cambiano da
   *  fuori (Jenny ha girato una sua azione): `jenny:data-changed` e' cio' che
   *  `jenny-sdk.js` ascolta. Come la mini-app sopra tutto in `apps-actions.js`,
   *  ma la cornice e' di questo file — v. `_pageWindow`.
   *
   *  Ci si iscrive una volta sola, alla prima pagina app riempita: prima non
   *  c'e' nessuna cornice da avvisare, e chiedere la sorgente al boot la
   *  costruirebbe per niente. Solo la pagina corrente e' viva, quindi si avvisa
   *  lei e solo se e' l'app di cui si parla.
   */
  _listenAppData() {
    if (this._detachAppData) return;
    const source = this.app?.appsSource?.();
    if (!source?.onAppDataChanged) return;
    this._detachAppData = source.onAppDataChanged((slug) => {
      const entry = this.entry(this.index);
      if (entry?.kind !== 'app' || entry.ref !== slug) return;
      this._pageWindow()?.postMessage({ type: 'jenny:data-changed', slug }, '*');
    });
  }

  /** Spegne una pagina: la cornice dell'app se ne va, e con lei l'app viva. */
  _empty(panel) {
    /* ...e non si svuota: tiene la sua foto, o la chat se e' parcheggiata li'
       mentre guardi un'app. Spenta resta comunque — una foto non gira. */
    if (panel.dataset.kind === 'conversation') {
      const s = this.pages.find((x) => x.id === panel.dataset.id);
      this.app?.chatMove?.snapshotIfNeeded(panel, s?.ref);
      return;
    }
    if (!panel.dataset.full) return;
    panel.textContent = '';
    panel.dataset.full = '';
    /* L'avviso se n'e' andato col resto: al prossimo ingresso lo rimette, se
       serve, il controllo dell'app. */
    delete panel.dataset.gone;
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

  async _checkApp(panel, page, mine) {
    const source = this.app?.appsSource?.();
    if (!source?.awaitJennyApps) return;
    let list;
    try {
      list = await source.awaitJennyApps();
    } catch {
      return;
    }
    if (source.jennyListFailed?.()) return;
    if (panel.dataset.full !== mine) return;       // nel frattempo sei uscito
    if (list.some((a) => a.slug === page.ref)) return;
    panel.textContent = '';                        // via la cornice verso il nulla
    this._gone(panel, page);
  }

  async _checkNotebook(panel, page) {
    if (!panel || !page) return;
    let names;
    try {
      const data = await api.listProjects();
      names = new Set((data?.projects || []).map((q) => q?.name));
    } catch {
      return;
    }
    if (names.has(projectNameOf(page.ref))) this._removeGone(panel);
    else this._gone(panel, page);
  }

  /** L'avviso, **sopra** quel che c'e' nel pannello e non al suo posto: in una
   *  pagina quaderno sotto c'e' la chat, portata dal trasloco, e toccarla da
   *  qui vorrebbe dire rompere un meccanismo che ha le sue regole.
   *
   *  Coprirla ferma il dito, **non la tastiera**: sul Titan il campo sotto
   *  l'avviso teneva il fuoco (glielo rimette ogni arrivo su una pagina con la
   *  chat) e i tasti scrivevano a `project:<cancellato>`. Il pannello porta
   *  quindi un segno che il guscio legge (`goneHere`) prima di dare o
   *  lasciare il fuoco al campo, e gli si dice che la pagina e' cambiata. */
  _gone(panel, page) {
    this._removeGone(panel);
    const card = document.createElement('div');
    card.className = 'home-page-gone';
    const text = document.createElement('p');
    text.textContent = i18n.t(
      page.kind === 'app' ? 'home.pages.appGone' : 'home.pages.notebookGone',
      { name: this.nameOf(page) },
    );
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'home-page-gone-remove';
    remove.textContent = i18n.t('home.pages.removePage');
    remove.addEventListener('click', () => this.detach(page.kind, page.ref));
    card.append(text, remove);
    panel.appendChild(card);
    panel.dataset.gone = '1';
    this.app?.onGoneChanged?.(panel);
  }

  _removeGone(panel) {
    for (const c of Array.from(panel.children)) {
      if (c.className === 'home-page-gone') c.remove();
    }
    if (!panel.dataset.gone) return;
    delete panel.dataset.gone;
    this.app?.onGoneChanged?.(panel);
  }

  /** La pagina a schermo dice che la sua cosa non c'e' piu'? Il guscio lo
   *  chiede prima di dare il fuoco al campo o di mandare un messaggio. */
  goneHere() {
    return Boolean(this.panelOf(this.index)?.dataset?.gone);
  }

  /** I pannelli delle pagine aggiunte, in ordine. Quelli fissi non ci sono:
   *  hanno `data-page` e non `data-id`. */
  _panels() {
    if (!this.track) return [];
    return Array.from(this.track.children).filter((c) => c.dataset?.id);
  }

  /** Le guardie del guscio: si puo' cambiare pagina adesso?
   *
   *  Stanno in un posto solo perche' le strade che ci arrivano sono due — il
   *  dito sulla pista e il dito dentro una app — e una seconda copia
   *  divergerebbe al primo caso particolare.
   */
  _canScroll() {
    // Fuori dalla chat comandano le stanze, non le pagine.
    if (this.app?.view && this.app.view !== 'chat') return false;
    // Mentre si spostano le pagine il dito e' della modalita' ordina.
    if (this.app?.strip?.sorting) return false;
    if (this.howMany < 2) return false;
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
  _reply() {
    let trackWidth = 0;
    return {
      start: () => {
        trackWidth = this.track.clientWidth || window.innerWidth;
        this.track.style.transition = 'none';
        this.track.style.willChange = 'transform';
      },

      drag: (dx) => {
        /* Ai due capi il dito tira, ma di meno: la pista non si richiude in
           cerchio come le linguette dell'officina. Li' le voci sono quattro e
           note; qui quante siano lo decide l'utente, e girando in tondo fra
           dodici pagine non si sa piu' dove si e'. */
        let offset = dx;
        const toFirst = this.index === 0 && dx > 0;
        const toLast = this.index === this.howMany - 1 && dx < 0;
        if (toFirst || toLast) {
          offset = dx * BEYOND_NEWLINE * 2;
        }
        const base = -this.index * trackWidth;
        this.track.style.transform = `translateX(${(base + offset).toFixed(2)}px)`;
      },

      end: ({ direction, confirm }) => {
        this.track.style.willChange = '';
        const step = confirm ? (direction === 'prev' ? -1 : +1) : 0;
        this.goTo(this.index + step);
      },

      cancel: () => {
        this.track.style.willChange = '';
        this.goTo(this.index);
      },
    };
  }

  _armSwipe() {
    const reply = this._reply();

    /* Il gesto vive quanto la pista, cioe' quanto la pagina: non c'e' niente
       da staccare, e il valore che `watchHorizontalSwipe` torna non serve. */
    watchHorizontalSwipe(this.track, {
      canStart: () => this._canScroll(),
      onHorizontal: reply.start,
      onDrag: reply.drag,
      onEnd: reply.end,
      onCancel: reply.cancel,
    });

    this._listenAppSwipe(reply);
  }

  /** La finestra della pagina che si sta guardando, se e' una app appesa.
   *
   *  Serve a una cosa sola: **riconoscere chi parla**. Solo la pagina corrente
   *  e' viva (v. `_activateOnly`), quindi questa e' l'unica finestra da cui un
   *  gesto possa arrivare davvero.
   *
   *  Le pagine fisse sono fuori per costruzione: non sono in `_panels()`. E
   *  una pagina quaderno ospita la chat, che una `contentWindow` non ce l'ha:
   *  controllare anche la specie vorrebbe dire due regole da tenere d'accordo.
   */
  _pageWindow() {
    const panel = this.panelOf(this.index);
    if (!panel?.dataset?.id) return null;
    const frame = panel.children[0] || panel.firstElementChild;
    return frame?.contentWindow || null;
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
   *  lui sa se una pagina di fianco c'e' — e lo decide a ogni `start`, non
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
  _listenAppSwipe(reply) {
    let ours = false;
    window.addEventListener?.('message', (e) => {
      const msg = e?.data;
      if (!msg || typeof msg !== 'object' || msg.type !== 'jenny:swipe') return;
      /* `!win` **prima** del confronto, e non e' ridondante: su una
         pagina che non e' una app qui c'e' `null`, e `MessageEvent.source` e'
         nullabile per specifica. Senza, un messaggio con sorgente nulla si
         confronterebbe `null !== null`, cioe' falso, cioe' passerebbe — e
         muoverebbe la pista chiunque. L'ha detto la mutazione, non la
         rilettura (22/09/2026). */
      const win = this._pageWindow();
      if (!win || e.source !== win) return;

      if (msg.phase === 'start') {
        ours = this._canScroll();
        if (ours) reply.start();
        return;
      }
      /* Senza questa, un `move` che arrivasse senza il suo `start` —
         perche' rifiutato, o perche' la pagina e' cambiata in mezzo —
         muoverebbe la pista su una larghezza mai misurata. */
      if (!ours) return;

      if (msg.phase === 'move') {
        const dx = Number(msg.dx);
        if (Number.isFinite(dx)) reply.drag(dx);
      } else if (msg.phase === 'end') {
        ours = false;
        reply.end({
          direction: msg.direction === 'prev' ? 'prev' : 'next',
          confirm: msg.confirm === true,
        });
      } else if (msg.phase === 'cancel') {
        ours = false;
        reply.cancel();
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
  nameOf(page) {
    if (!page) return '';
    if (page.kind === 'conversation') return projectNameOf(page.ref) || page.ref;
    return this.app?.appName?.(page.ref) || page.ref;
  }
}
