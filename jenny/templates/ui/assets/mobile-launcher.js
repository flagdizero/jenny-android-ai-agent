/** Mobile Launcher — il foglio che sale dal composer.
 *
 *  Piano `.agent/apps-drawer-plan.md`, passi 1-6: l'impianto di navigazione, le
 *  tre liste vere, il campo di ricerca con sotto la lista ordinata e attivabile
 *  col tocco, **il modo di usarlo senza toccare lo schermo** (type-ahead,
 *  frecce, rotella, ⏎ e ⇧⏎), la geometria che schiva la gesture di home e
 *  sopravvive alla tastiera, e i bordi: la riga «Gestisci», gli stati vuoti
 *  distinti e un avvio fallito che lo dice.
 *
 *  Il passo 4 è la ragione per cui la direzione *Digita* è stata scelta su un
 *  telefono con tastiera fisica: due tasti invece di sei schermate. Da qui in
 *  poi il foglio si usa senza alzare un dito — si scrive, si sceglie con le
 *  frecce o con la rotella, si apre con ⏎.
 *
 *  Il foglio non è una vista: non sta in `controllerFactories`, non ha un
 *  `view-*` e non tocca la history. È un **livello** di
 *  `MobileApp._overlayLayers()`, fra `miniapp` e `drawer` (D3), ed è da lì che
 *  eredita gratis il tasto Indietro, Home e la guardia del type-ahead della
 *  chat.
 *
 *  **Non possiede i dati** (D5). Skill, Jenny App e app Android stanno in
 *  `AppsController`, che ha già il ricaricamento con annuncio delle rimozioni,
 *  l'elenco delle nascoste, `onPackageChanged` e l'ascolto dei frame
 *  `apps_list_changed` / `app_data_changed`. Qui si legge da lì e ci si iscrive
 *  ai suoi cambi: una seconda macchina di ricarica sarebbe una seconda verità
 *  da tenere allineata, e si scoprirebbe disallineata proprio nei casi che il
 *  cassetto deve servire bene (una app disinstallata mentre il foglio è aperto).
 */

import { i18n } from './shared/i18n.js';
import { UsageRanking, rankEntries } from './shared/launcher-rank.js';
import { usageStore } from './shared/launcher-usage-store.js';
import { isTypeAheadKey } from './shared/type-ahead.js';

/* Etichetta del tipo, a destra della riga. Chiavi proprie del cassetto e non
   quelle della scheda: lì i titoli sono intestazioni di sezione (plurali,
   "Jenny Apps"), qui qualificano una voce sola. */
const KIND_LABEL_KEYS = {
  skill: 'launcher.kindSkill',
  jenny: 'launcher.kindJennyApp',
  android: 'launcher.kindAndroidApp',
};

/* Quanti pixel di rotella valgono un passo di selezione, quando l'evento li
   conta in pixel (`deltaMode === 0`). Circa l'altezza di una riga: così la
   lista sotto la selezione si muove alla stessa velocità con cui si muoverebbe
   scorrendo, e non c'è un secondo ritmo da imparare. Con `deltaMode` a righe o
   a pagine il valore non si usa: lì l'unità è già un passo. */
const WHEEL_PIXELS_PER_STEP = 24;

/* ── Geometria (passo 5) ───────────────────────────────────────────────────
   Le tre costanti qui sotto sono **forma**, non misure del dispositivo: quelle
   si leggono a runtime dal ponte nativo e dal `visualViewport`, e non compaiono
   in questo file. Il numero misurato il 30/08 sull'emulatore quadrato (96 px
   fisici di zona di gesture, di cui 8 px CSS dentro la WebView) è servito a
   dimensionare il problema; cablarlo qui sarebbe sbagliato, perché la soglia la
   decide la shell del dispositivo e il Titan 2 ha la propria. */

/* Quanto del viewport **senza tastiera** occupa il foglio a riposo. */
const SHEET_HEIGHT_RATIO = 0.66;
/* Il minimo di sfondo che resta a vedersi sopra il foglio: senza, in uno spazio
   stretto il foglio diventa a tutto schermo e smette di sembrare un foglio. */
const SHEET_TOP_GAP = 8;
/* Sotto questa altezza il foglio stringe la propria cornice (v. `.compact` nel
   CSS). Il conto: cornice piena ≈ 105 px, una riga 52 — sotto le due righe la
   cornice costa più di quanto renda, ed è la lista la ragione per cui il foglio
   esiste. */
const COMPACT_HEIGHT = 220;
/* Quanto va trascinato in giù, in frazione della propria altezza, perché il
   foglio si chiuda invece di tornare su. Distanza e basta: **nessuna soglia di
   velocità** (D7). Un lancio veloce e corto e un trascinamento lento e lungo
   fanno la stessa cosa, e non c'è una costante di velocità da tarare su un
   dispositivo per poi scoprirla sbagliata su un altro. */
const DRAG_CLOSE_RATIO = 0.3;

export class LauncherController {
  /** @param {object} app istanza di MobileApp (per il ritorno del fuoco). */
  constructor(app) {
    this.app = app;
    this.sheet = document.getElementById('launcher-sheet');
    this.scrim = document.getElementById('launcher-scrim');
    this.list = document.getElementById('launcher-list');
    this.search = document.getElementById('launcher-search');
    this.titleEl = document.getElementById('launcher-title');
    this.clearBtn = document.getElementById('launcher-search-clear');
    this.statusEl = document.getElementById('launcher-status');
    this.retryBtn = document.getElementById('launcher-status-retry');
    this.manageBtn = document.getElementById('launcher-manage');

    /* Verità unica sullo stato del foglio, e la ragione per cui esiste invece
       di interrogare il DOM: `present()` deve diventare falso *nell'istante*
       in cui si chiude, non a transizione finita. `_dismissAllOverlays()`
       richiama il livello fino a otto volte finché `present()` è vero — e un
       foglio che scivola giù per 320 ms resterebbe nel DOM, e quindi
       "presente", per tutte e otto. Un flag scritto sincronamente da close()
       chiude quel ciclo alla prima iterazione. */
    this._open = false;
    // Chi aveva il fuoco all'apertura: ci torna alla chiusura (idioma di
    // DrawerManager._releaseContent).
    this._lastFocus = null;
    // Il proprietario dei dati, agganciato alla prima apertura e mai più
    // lasciato: l'iscrizione ai suoi cambi deve sopravvivere alla chiusura del
    // foglio, altrimenti riaprirlo mostrerebbe l'elenco di quando si è chiuso.
    this._apps = null;
    // Le voci come le consegna `launcherEntries()`, non ordinate per la query:
    // riordinarle e filtrarle è lavoro di `_renderList()`, che gira a ogni
    // tasto, mentre questa si rinfresca solo quando i dati cambiano davvero.
    this._entries = [];
    /* Le righe già costruite, per chiave. È **il** motivo per cui digitare non
       costa: il difetto 07 del rilievo è che la scheda ricostruisce 68 celle e
       ridecodifica 47 icone base64 a ogni carattere, e ricostruire qui la riga
       a ogni tasto lo riprodurrebbe identico. Le righe si costruiscono una
       volta, e la ricerca si limita a rimetterle in fila: un nodo `<img>` che
       resta vivo con la stessa `src` non si ridecodifica.
       Valore: `{ sig, el }` — `sig` è la firma del contenuto, così una voce che
       cambia (una app che si rompe, un nome che cambia lingua) si riconosce e
       si ricostruisce, e solo lei. */
    this._rows = new Map();
    /* Le chiavi effettivamente in lista, nell'ordine in cui si vedono. È su
       questo che si muovono ↑↓ e la rotella: leggere il DOM a ogni passo
       darebbe la stessa risposta al prezzo di un layout, e la rotella di passi
       ne produce a raffica. Scritto solo da `_renderList()`. */
    this._rankedKeys = [];
    // La riga selezionata, per chiave e non per indice: fra un tasto e l'altro
    // la lista può essersi riordinata (un `apps_list_changed`, una app
    // disinstallata), e un indice punterebbe a un'altra voce senza dirlo.
    this._selectedKey = null;
    /* La selezione è stata spostata da chi guarda, o sta solo seguendo la cima?
       Le due cose vanno distinte perché un ricaricamento dei dati deve
       rispettare una scelta e sovrascrivere un default — v. `_renderList`. */
    this._selectionPinned = false;
    /* Gli `id` delle righe, che servono ad `aria-activedescendant` e devono
       quindi esistere nel DOM ed essere unici. Un id derivato dalla chiave
       sarebbe più leggibile ma la chiave viene da fuori (nomi di pacchetto,
       slug, nomi di skill): un contatore non ha caratteri da ripulire e non
       collide. L'id vive quanto la riga in cache, cioè resta stabile. */
    this._rowSeq = 0;
    // Pixel di rotella non ancora spesi — v. `_onWheel`.
    this._wheelAcc = 0;
    // Frequenza e recenza per chiave (D9). Costruito qui e non alla prima
    // apertura: leggere una riga di storage costa meno di decidere se
    // leggerla, e il ranking serve già al primo disegno.
    /* Lo storage non è più `window.localStorage` diretto: dentro l'APK il
       valore vive nelle SharedPreferences, che sopravvivono al kill del
       processo — v. `shared/launcher-usage-store.js`, che sceglie il posto e
       porta di là il valore vecchio una volta sola. */
    this._usage = new UsageRanking(usageStore());
    /* L'altezza del viewport **senza tastiera**, da cui si calcola quella del
       foglio. Serve ricordarla perché su questo guscio la finestra si
       ridimensiona davvero quando la tastiera software sale (misurato: 432 →
       124 px CSS), e un foglio alto il 66% di *quel* che resta si accartoccia
       proprio quando serve di più. Si riparte da capo quando cambia la
       larghezza — cioè a una rotazione: la tastiera l'altezza la cambia, la
       larghezza no. */
    this._fullViewportH = window.innerHeight;
    this._viewportWidth = window.innerWidth;
    // Trascinamento in corso, o null. V. `_onDragStart`.
    this._drag = null;

    if (!this.sheet) return;

    this.closeBtn = document.getElementById('launcher-close');
    this.closeBtn?.addEventListener('click', () => this.close());
    this.scrim?.addEventListener('click', () => this.close());

    this.manageBtn?.addEventListener('click', () => this._openManager());
    this.retryBtn?.addEventListener('click', () => this._retryFailedLists());

    this.search?.addEventListener('input', () => this._onQueryChanged());
    this.clearBtn?.addEventListener('click', () => {
      this.search.value = '';
      this._onQueryChanged();
      this.search.focus();
    });

    /* Attivazione: un solo ascoltatore sulla lista, non uno per riga.
       Le righe sono centinaia e si rimettono in fila a ogni tasto; appenderci
       un listener ciascuna li moltiplicherebbe per il numero di ricostruzioni.
       ⏎ e Spazio non passano di qui: un `<div role="option">` non riceve il
       click sintetizzato che un `<button>` vero riceverebbe, e darlo per
       scontato era il difetto silenzioso del passo 3 — la riga sembrava
       attivabile da tastiera e non lo era. Li gestisce `_onKeyDown`, che
       finisce comunque in `_activate`: un percorso solo, dichiarato. */
    this.list?.addEventListener('click', (e) => {
      const row = e.target.closest?.('.launcher-row');
      if (row?.dataset.key) this._activate(row.dataset.key);
    });

    /* Il fuoco che entra in una riga *è* una selezione: chi arriva con Tab o
       col dito di TalkBack e poi preme ⏎ deve aprire quella riga, non quella
       evidenziata prima. Un solo ascoltatore, per la stessa ragione del click:
       `focusin` sale, `focus` no. */
    this.list?.addEventListener('focusin', (e) => {
      const row = e.target.closest?.('.launcher-row');
      if (!row?.dataset.key) return;
      this._selectionPinned = true;
      this._select(row.dataset.key);
    });

    /* I tasti si ascoltano sul documento, non sul foglio: all'apertura il fuoco
       è sul foglio, ma può finire sul `body` (una riga che sparisce sotto il
       fuoco, un ridisegno) e da lì un ascoltatore locale non sentirebbe più
       niente — cioè proprio quando il type-ahead serve. Lo sfondo è inerte, e
       la guardia di `_onKeyDown` fa il resto. */
    document.addEventListener('keydown', (e) => this._onKeyDown(e));

    /* La rotella. `passive: false` perché il gesto qui **sostituisce** lo
       scorrimento invece di accompagnarlo: si muove la selezione, e la
       selezione si porta dietro la lista. Sul foglio intero e non sulla sola
       lista, così funziona anche partendo dalla riga di ricerca. */
    this.sheet.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });

    /* I nomi dei tipi cambiano con la lingua, e questa lista la costruisce JS:
       `_applyStaticTranslations()` passa sui `data-i18n` che *sono già in
       pagina*, quindi copre le righe esistenti ma non quelle che nasceranno
       dopo. Ridisegnare al cambio di lingua le copre entrambe. Il primo disegno
       non è qui ma in `open()`: al boot le traduzioni non sono ancora arrivate
       (`i18n.load` è asincrona) e disegnare adesso vorrebbe dire scrivere le
       chiavi grezze in un foglio che nessuno sta guardando. */
    i18n.onLocaleChange(() => {
      // Le righe portano dentro testo tradotto (il tipo, l'errore di un
      // manifest rotto): la cache va buttata, non riordinata.
      this._rows.clear();
      this._render();
    });

    this._setupDrag();
    this._setupGeometry();
  }

  /* ── Geometria: la zona di gesture e la tastiera (5.2, 5.3, 5.5) ────────── */

  /** Aggancia le due misure che il foglio non può decidere da sé.
   *
   *  Entrambe vanno **rilette**, non prese una volta all'avvio: l'inset di
   *  gesture cambia se si passa da gesture a tre pulsanti (o viceversa) mentre
   *  l'app è viva, e la geometria del viewport cambia a ogni tastiera che sale
   *  e a ogni rotazione.
   */
  _setupGeometry() {
    const sync = () => { this._syncGestureInset(); this._syncViewport(); };
    /* L'annuncio del lato nativo: `MainActivity.refreshGestureInsets()` lo
       manda quando il valore **cambia davvero**, cioè su un cambio di modalità
       di navigazione o di geometria della finestra. */
    window.addEventListener('jenny-gesture-insets', sync);
    window.addEventListener('resize', sync);
    window.addEventListener('orientationchange', sync);
    /* Il `visualViewport` è l'unico che vede la tastiera software su un guscio
       che *non* ridimensiona la finestra (`adjustPan`/`adjustNothing`): lì
       `innerHeight` non si muove e `resize` non parte. Dove invece la finestra
       si ridimensiona — come su questo guscio — arrivano entrambi, e i due
       percorsi convergono sullo stesso calcolo. */
    window.visualViewport?.addEventListener('resize', sync);
    window.visualViewport?.addEventListener('scroll', sync);
    /* Subito, e non a shell pronta: `addJavascriptInterface` corre prima di
       `loadUrl`, quindi `JennyNative` c'è già. E qui *non si può* aspettare —
       questo costruttore gira dentro quello di `MobileApp`, prima che
       `whenShellReady` abbia la sua coda: chiamarlo di qui lo faceva morire
       sul nascere, e con lui tutta la SPA (visto girare, non dedotto).
       Se il primo valore arrivasse comunque a zero perché la WebView non è
       ancora stata misurata, il lato nativo manda `jenny-gesture-insets`
       appena lo sa, e `open()` rilegge comunque. */
    sync();
  }

  /** Porta al CSS l'inset di gesture in fondo, che il CSS non sa leggere.
   *
   *  `env(safe-area-inset-bottom)` **non** serve: misurato a `0px` su tutti e
   *  quattro i lati, perché il decor di AppCompat consuma gli inset delle barre
   *  prima della WebView. Il numero vero vive solo sul lato nativo
   *  (`WindowInsets.getMandatorySystemGestureInsets()`), e arriva da
   *  `JennyNative.getBottomGestureInset()` in px **fisici**.
   *
   *  Fuori da Android (browser, test) il ponte non c'è e la proprietà resta a
   *  zero: nessuna zona di gesture da schivare, che è la verità.
   */
  _syncGestureInset() {
    const native = window.JennyNative;
    let px = 0;
    if (typeof native?.getBottomGestureInset === 'function') {
      try {
        px = Number(native.getBottomGestureInset()) || 0;
      } catch (_) {
        /* bridge assente o troppo vecchio */
      }
    }
    const dpr = window.devicePixelRatio || 1;
    document.documentElement.style.setProperty(
      /* `ceil`, non `round`: mezzo pixel di troppo non si vede, mezzo di
         meno rimette la lista dentro la fascia in cui il tocco va alla
         shell. A dpr 3 la differenza è reale — 25 px nativi fanno 8,33
         CSS, e `round` li darebbe come 8. È l'unica riga in cui questo
         margine si calcola: qui l'errore ha una direzione sicura sola. */
      '--gesture-inset-bottom', `${Math.max(0, Math.ceil(px / dpr))}px`,
    );
  }

  /** Ricalcola quanto spazio ha il foglio, e quanto ne prende (5.5).
   *
   *  Due grandezze, e non una sola:
   *  - `--launcher-kb-inset`: quanto della finestra è coperto in basso da
   *    qualcosa che non l'ha ridimensionata (la tastiera in `adjustPan`). Il
   *    foglio ci si appoggia sopra con `bottom`, invece di finirci sotto.
   *  - `--launcher-height`: l'altezza del foglio. È il 66% del viewport
   *    **senza tastiera**, ma non più dello spazio che c'è davvero — il
   *    secondo termine è quello che conta quando la tastiera è su.
   *
   *  Senza questo, col fuoco nel campo il foglio resta alto il 66% di ciò che
   *  la tastiera gli ha lasciato: misurati 82 px CSS su 124, cioè maniglia,
   *  titolo, campo — e **zero righe**. Si cerca alla cieca, che è esattamente
   *  ciò che un cassetto che si digita non deve fare.
   */
  _syncViewport() {
    if (!this.sheet) return;
    const layoutH = window.innerHeight;
    if (window.innerWidth !== this._viewportWidth) {
      // Rotazione: l'altezza "piena" di prima non vale più.
      this._viewportWidth = window.innerWidth;
      this._fullViewportH = layoutH;
    } else {
      this._fullViewportH = Math.max(this._fullViewportH, layoutH);
    }
    const vv = window.visualViewport;
    const kbInset = vv
      ? Math.max(0, Math.round(layoutH - (vv.offsetTop + vv.height)))
      : 0;
    const available = layoutH - kbInset;
    const height = Math.max(
      0,
      Math.min(Math.round(this._fullViewportH * SHEET_HEIGHT_RATIO), available - SHEET_TOP_GAP),
    );
    const root = document.documentElement.style;
    root.setProperty('--launcher-kb-inset', `${kbInset}px`);
    root.setProperty('--launcher-height', `${height}px`);
    /* Qualcosa sta mangiando il viewport: la tastiera software, in uno dei due
       modi. Non è la stessa domanda di `.compact` — uno schermo corto e basta
       (un pieghevole chiuso, un landscape) dà un foglio basso **che tocca
       ancora il fondo dello schermo**, e lì la zona di gesture c'è. */
    this.sheet.classList.toggle('kb-open', kbInset > 0 || layoutH < this._fullViewportH);
    this.sheet.classList.toggle('compact', height < COMPACT_HEIGHT);
    // La riga scelta deve restare sotto gli occhi anche dopo che il foglio si è
    // ristretto: è metà della casella 5.5, e senza è un foglio che si
    // ridimensiona bene attorno al vuoto.
    if (this._open) this._revealSelected();
  }

  /** Riporta in vista la riga selezionata, se ce n'è una. */
  _revealSelected() {
    const el = this._selectedKey ? this._rows.get(this._selectedKey)?.el : null;
    if (el?.isConnected) el.scrollIntoView({ block: 'nearest' });
  }

  /* ── Trascinamento (5.1, D7) ────────────────────────────────────────────── */

  /** Il foglio si trascina **dalla maniglia e dalla riga del titolo. Dalla
   *  lista mai.**
   *
   *  È l'unica regola, e decide l'origine del tocco: niente axis-lock, niente
   *  soglia di velocità, nessun arbitrato fra "scorrere" e "chiudere" da fare a
   *  metà gesto. Il motivo è che qui sotto c'è una chat, e in chat i
   *  trascinamenti verticali sono già promessi allo scroller da `setupSwipeNav`
   *  (`mobile-app.js`), che fa axis-lock a 10 px e restituisce ogni gesto più
   *  verticale che orizzontale. Un foglio che contendesse i verticali della
   *  propria lista è precisamente la ragione per cui i bottom sheet dentro
   *  contenuto scrollabile sembrano rotti: la lista a volte scorre e a volte no,
   *  e chi guarda non ha modo di sapere quale delle due sta per succedere.
   */
  _setupDrag() {
    const zones = [
      document.getElementById('launcher-handle-row'),
      this.sheet.querySelector('.launcher-head'),
    ];
    for (const zone of zones) {
      zone?.addEventListener('pointerdown', (e) => this._onDragStart(e));
    }
    /* Move e up si ascoltano sul **foglio**, non sulla zona: col puntatore
       catturato gli eventi arrivano lì, e il dito esce quasi subito dai 20 px
       della maniglia. */
    this.sheet.addEventListener('pointermove', (e) => this._onDragMove(e));
    this.sheet.addEventListener('pointerup', (e) => this._onDragEnd(e, false));
    this.sheet.addEventListener('pointercancel', (e) => this._onDragEnd(e, true));
  }

  _onDragStart(e) {
    if (!this._open || this._drag) return;
    // Solo il pulsante primario / il dito: un tasto destro non trascina.
    if (e.button > 0) return;
    /* La ✕ resta un pulsante. Sta dentro la zona di trascinamento perché la
       riga del titolo *è* la zona, e senza questa riga un tocco su di lei
       comincerebbe un trascinamento da zero pixel — innocuo a vedersi, ma il
       `setPointerCapture` porterebbe via il `click`. */
    if (e.target.closest?.('button')) return;
    const rect = this.sheet.getBoundingClientRect();
    this._drag = { id: e.pointerId, startY: e.clientY, dy: 0, height: rect.height };
    try {
      this.sheet.setPointerCapture(e.pointerId);
    } catch (_) {
      /* puntatore già rilasciato */
    }
    // Durante il trascinamento il foglio segue il dito e basta: la transizione
    // di `.launcher-sheet` lo farebbe arrivare in ritardo di 320 ms.
    this.sheet.classList.add('dragging');
  }

  _onDragMove(e) {
    if (!this._drag || e.pointerId !== this._drag.id) return;
    /* Solo verso il basso. Tirare in su un foglio già a fine corsa lo
       staccherebbe dal fondo dello schermo, e sotto non c'è niente da mostrare:
       il rimbalzo elastico dei drawer qui è una fessura di sfondo. */
    const dy = Math.max(0, e.clientY - this._drag.startY);
    this._drag.dy = dy;
    this.sheet.style.transform = `translateY(${dy}px)`;
    // Lo scrim segue: la chiusura si vede arrivare mentre la si decide, invece
    // di essere un sì/no che si scopre al rilascio.
    if (this.scrim) {
      this.scrim.style.opacity = String(Math.max(0, 1 - dy / this._drag.height));
    }
  }

  _onDragEnd(e, cancelled) {
    if (!this._drag || e.pointerId !== this._drag.id) return;
    const { dy, height } = this._drag;
    this._drag = null;
    try {
      this.sheet.releasePointerCapture(e.pointerId);
    } catch (_) {
      /* già rilasciato */
    }
    /* Si torna al foglio "di CSS": si tolgono gli stili in linea **e** la
       classe che spegneva la transizione, così il tratto che resta — il ritorno
       su, o la discesa fino in fondo — lo anima il CSS. Che è anche il modo in
       cui `prefers-reduced-motion` lo spegne: la transizione è una proprietà di
       `.launcher-sheet`, e la regola per il movimento ridotto la azzera lì. */
    this.sheet.style.transform = '';
    if (this.scrim) this.scrim.style.opacity = '';
    this.sheet.classList.remove('dragging');
    // Un gesto annullato dal sistema (una chiamata, una gesture di sistema che
    // se lo prende) non è una decisione di chi guarda: il foglio torna su.
    if (!cancelled && dy > height * DRAG_CLOSE_RATIO) this.close();
  }

  /** Il foglio è a schermo e reattivo? Letto da `_overlayLayers().present`. */
  isOpen() {
    return this._open;
  }

  open() {
    if (!this.sheet || this._open) return;
    this._open = true;
    this._lastFocus = document.activeElement;
    /* Ogni apertura riparte dal campo vuoto. Ritrovare la query di ieri
       vorrebbe dire aprire il cassetto su tre voci su settanta senza aver
       chiesto niente — e il costo di ricominciare è una parola, mentre il costo
       di non capire perché manca tutto è un cassetto che sembra rotto. */
    if (this.search) this.search.value = '';
    /* E dalla prima riga. La selezione **non** è uno stato che sopravvive alla
       chiusura, e per una ragione più forte di quella del campo: ⏎ appena
       aperto aprirebbe quel che era evidenziato l'altra volta — cioè
       lancerebbe qualcosa che nessuno ha scelto adesso. Azzerata qui e non in
       `close()`, così la riga resta evidenziata mentre il foglio scende.

       Via `_select(null)` e non azzerando il campo a mano: la riga di ieri è
       ancora in cache **col suo `aria-selected` e la sua classe addosso**, e
       dimenticarne solo la chiave la lascerebbe lì marcata per sempre. Visto
       girare: due righe selezionate insieme nell'albero di accessibilità, e
       nessuna delle due sbagliata a guardare il DOM. */
    this._select(null);
    this._selectionPinned = false;
    /* La geometria può essere cambiata mentre il foglio era chiuso — si è
       passati a tre pulsanti, si è ruotato lo schermo, la tastiera è su per il
       composer della chat. Si rilegge prima di mostrarlo, non dopo: il foglio
       arriva già dell'altezza giusta invece di assestarsi a fine corsa. */
    this._syncGestureInset();
    this._syncViewport();
    // Prima di mostrarlo: la lista è già quella giusta quando il foglio arriva
    // a fine corsa, e non c'è un fotogramma con dentro l'elenco di ieri.
    this._attachSource();
    this._render();
    this.sheet.classList.add('open');
    this.sheet.setAttribute('aria-hidden', 'false');
    this.scrim?.classList.add('open');
    // Il campo è un `combobox` e la sua lista è a schermo per tutto il tempo in
    // cui il foglio lo è: non c'è un popup che si apre e si chiude a parte.
    this.search?.setAttribute('aria-expanded', 'true');
    // Lo sfondo diventa inerte in un colpo solo: niente fuoco, niente tap,
    // niente lettura TalkBack. Il foglio vive *fuori* da `.app` (in fondo al
    // <body>, come .app-frame-overlay) proprio perché questa riga possa essere
    // una riga sola — e perché una mini-app aperta sopra di esso resti viva.
    this._setBackgroundInert(true);
    /* Il fuoco entra nel foglio, altrimenti Tab ripartirebbe da dentro il
       contenuto appena reso inerte, cioè da nessuna parte. Sul *campo* di
       ricerca non ci va: alzerebbe la tastiera software e si mangerebbe il
       foglio (D6, type-ahead invece di autofocus).

       Va sul **contenitore** (`tabindex="-1"`, `role="dialog"`) e non più sulla
       ✕ del passo 1, per due ragioni che il passo 4 ha reso vere insieme: ⏎
       appena aperto deve aprire il primo risultato, e con il fuoco su un
       pulsante quel ⏎ chiudeva il foglio invece; e TalkBack, entrando dal
       dialog, ne annuncia il titolo prima del contenuto anziché leggere
       "Chiudi" come prima cosa di un cassetto appena aperto. */
    this.sheet.focus?.();
  }

  /** Chiusura completa. Idempotente: Home la chiama comunque. */
  close() {
    if (!this.sheet || !this._open) return;
    this._open = false;
    /* Home può arrivare a metà trascinamento (1.8: `goHome()` smonta ogni
       livello). Gli stili in linea del gesto vanno via qui, altrimenti alla
       riapertura il foglio comparirebbe già spostato in giù di quanto era il
       dito l'ultima volta. Il gesto in corso non si annulla: il suo
       `pointerup` arriverà comunque e troverà `close()` già fatto, che è
       idempotente. */
    this.sheet.style.transform = '';
    if (this.scrim) this.scrim.style.opacity = '';
    this.sheet.classList.remove('dragging');
    this.sheet.classList.remove('open');
    this.sheet.setAttribute('aria-hidden', 'true');
    this.scrim?.classList.remove('open');
    this.search?.setAttribute('aria-expanded', 'false');
    this._setBackgroundInert(false);
    const previous = this._lastFocus;
    this._lastFocus = null;
    // La vista sotto può essere stata ridisegnata mentre il foglio era aperto:
    // in quel caso non si sposta niente.
    if (previous && previous.isConnected) previous.focus?.();
  }

  /** Semantica del tasto Indietro — **e di Esc**, che non ha un handler
   *  proprio: `keyboard.register('escape')` lo manda in `handleHardwareBack()`,
   *  cioè nella stessa catena di livelli. Una decisione sola per due tasti, che
   *  sul Titan 2 stanno entrambi sotto le dita.
   *
   *  Un passo alla volta (4.4): prima si svuota la ricerca, poi si chiude. È
   *  l'invariante di `handleHardwareBack` — *una pressione, un cambiamento
   *  visibile* — e svuotare il campo lo è: la lista torna quella intera. Senza
   *  questo, una query digitata male costerebbe chiudere e riaprire il foglio,
   *  che è la cosa che il cassetto esiste per non far fare.
   *
   *  A campo vuoto il comportamento è quello del passo 1 (1.6): chiude.
   */
  dismiss() {
    if (this.search?.value) {
      this.search.value = '';
      this._onQueryChanged();
      return;
    }
    this.close();
  }


  /** La riga «Gestisci» (6.1, D4): il foglio lancia, la scheda gestisce.
   *
   *  `switchMode('apps')` chiude già il foglio da sé (1.5), e da lì si passa
   *  sempre: verificato con un tocco vero. La chiusura esplicita qui **non è**
   *  quindi la correzione di un difetto osservato — è la guardia sull'unico
   *  modo in cui quella catena si spezza: `switchMode` esce subito se il modo
   *  richiesto è già quello corrente, e allora il foglio resterebbe aperto
   *  sopra la scheda che avrebbe dovuto mostrare — un overlay orfano, cioè
   *  precisamente ciò che 6.1 chiede di escludere.
   *
   *  Oggi quel caso non si raggiunge: il pulsante che apre il foglio sta in
   *  `#input-bar`, che vive dentro `#view-chat`, quindi il modo corrente
   *  all'apertura è sempre `chat`. È una coincidenza di *dove sta un pulsante*,
   *  però, non una proprietà del cassetto — e il piano stesso lascia aperta la
   *  possibilità di aprirlo da altrove (v. la decisione sul dock). Chiudere
   *  prima costa una riga ed è idempotente: quando la catena normale funziona,
   *  la chiusura dentro `switchMode` diventa un giro a vuoto.
   */
  _openManager() {
    this.close();
    this.app.switchMode('apps');
  }

  /** «Riprova» dell'avviso di 6.2.
   *
   *  Il pulsante si spegne finché non arriva una risposta: senza, un tocco su
   *  una rete ancora assente non produce nessun segno e sembra non aver fatto
   *  niente — che è il difetto di 6.3, spostato di un elemento. Lo riaccende
   *  `_syncStatus()`, che gira a ogni ridisegno della lista: alla fine di ogni
   *  fetch, riuscita o no, e anche a ogni tasto. Riacceso troppo presto non fa
   *  danni — un secondo tocco rifà semplicemente la stessa ritentata.
   */
  _retryFailedLists() {
    if (!this._apps || !this.retryBtn) return;
    this.retryBtn.disabled = true;
    this._apps.retryFailedLists();
  }

  /** Mostra o nasconde l'avviso di elenco incompleto (6.2).
   *
   *  Il caso che lo motiva è **parziale**: il ponte nativo non risponde, le
   *  skill e le Jenny App arrivano tutte, e mancano solo le app del telefono.
   *  Nessuno stato vuoto comparirebbe — la lista è piena — e l'unico segno
   *  sarebbe un cassetto che non trova Telefono. Gli stati vuoti da soli non
   *  bastavano: coprono la lista vuota, non la lista monca.
   */
  _syncStatus() {
    if (!this.statusEl) return;
    const failed = !!this._apps && this._apps.listsFailed();
    this.statusEl.hidden = !failed;
    if (this.retryBtn) this.retryBtn.disabled = false;
  }

  _setBackgroundInert(on) {
    const shell = document.getElementById('app');
    if (shell) shell.inert = on;
    /* `inert` toglie fuoco e tocchi, **non** l'impilamento: la mascotte vive
       dentro `#app` (v. `JennyCompanion._buildDom`) ma a z-index 120, sopra
       foglio (100) e scrim (99), e resterebbe *dipinta* sulle righe. Visto sul
       telefono, non sull'emulatore, dove non capitava di sovrapporsi.
       Il segno sta su `<html>` perché la mascotte è dentro lo sfondo che si sta
       oscurando: sotto lo scrim è il posto giusto, non nascosta — sparire di
       colpo sarebbe più brusco che essere velata come il resto della chat. */
    document.documentElement.classList.toggle('launcher-open', on);
  }

  /** Aggancia la sorgente dei dati (D5) alla prima apertura, e ci resta.
   *
   *  Non nel costruttore: `AppsController` fa quattro fetch, e quella delle app
   *  Android ricodifica ogni icona in base64. Farle al boot per un foglio che
   *  potrebbe non aprirsi mai è un costo che si paga sempre e serve a volte.
   *  Da qui in poi però l'iscrizione non si scioglie più — v. `this._apps`.
   */
  _attachSource() {
    if (this._apps) {
      // Aperture successive: la scheda App può essere stata visitata e lasciata
      // nel frattempo, e `deactivate()` invalida le Jenny App di proposito.
      this._apps.ensureLoaded();
      return;
    }
    const apps = this.app.appsController?.();
    if (!apps) return;
    this._apps = apps;
    apps.addChangeListener(() => this._onDataChanged());
    apps.ensureLoaded();
  }

  /** Una delle tre liste è cambiata: un `apps_list_changed` dal gateway, un
   *  pacchetto annunciato dal PackageManager, o una fetch appena tornata.
   *
   *  La lista si riscrive **sul posto**: il foglio non si chiude e non si
   *  rimonta. A foglio chiuso non si disegna niente — riaprirlo ridisegna
   *  comunque, e ridipingere un elenco che nessuno vede è lavoro sul thread
   *  principale sottratto a chi invece si sta guardando una risposta arrivare.
   */
  _onDataChanged() {
    if (this._open) this._render();
  }

  /** I dati sono cambiati: si rilegge la lista, si aggiorna la cache delle
   *  righe e si ridisegna. È il percorso *costoso*, e per questo non è quello
   *  che gira quando si digita — v. `_onQueryChanged`. */
  _render() {
    this._syncEntries();
    this._renderList();
  }

  /** Rilegge `launcherEntries()` e riallinea la cache delle righe.
   *
   *  Una riga si ricostruisce solo se il suo contenuto è cambiato davvero: la
   *  firma tiene tutto ciò che finisce nel DOM. Senza il confronto, ogni
   *  `apps_list_changed` — che arriva a ogni turno dell'agente in cui
   *  `workspace/apps/` si è mossa — butterebbe via le 47 icone base64 già
   *  decodificate per riscriverle identiche.
   */
  _syncEntries() {
    this._entries = this._apps ? this._apps.launcherEntries() : [];
    const next = new Map();
    for (const entry of this._entries) {
      /* Separatore NUL *scritto come escape*, non incollato: un byte zero nel
         sorgente rende il file binario per grep, e la ricerca fallisce in
         silenzio (gotcha noto del progetto su `mobile-chat.js`). Serve perché
         uno spazio darebbe la stessa firma a un nome che finisce dove la
         descrizione comincia, e quella riga resterebbe indietro. */
      const sig = [entry.name, entry.description, entry.problem, entry.kind,
        entry.glyph, entry.hasServer].join('\u0000');
      const cached = this._rows.get(entry.key);
      /* L'icona sta fuori dalla firma: è una data URL da decine di kB, e
         concatenarla vorrebbe dire costruire un megabyte di stringhe a ogni
         cambio per confrontare byte che si confrontano benissimo da soli. */
      next.set(entry.key, (cached && cached.sig === sig && cached.icon === entry.icon)
        ? cached
        : { sig, icon: entry.icon, el: this._buildRow(entry) });
    }
    this._rows = next;
  }

  /** Il campo è cambiato: si riordina e si rimette in fila, **senza ricostruire
   *  niente** (difetto 07 del rilievo). Le righe sono già nel DOM: qui si
   *  spostano, e le escluse si staccano restando in cache. */
  _onQueryChanged() {
    // Una query nuova è una domanda nuova: la risposta migliore torna in cima e
    // l'evidenziazione con lei (v. la regola in `_renderList`).
    this._selectionPinned = false;
    this._renderList();
  }

  /** Filtra, ordina e impagina. L'unica funzione che tocca `this.list`.
   *
   *  L'ordine è quello di D9/3.3 — pertinenza, poi frequenza, poi recenza — e
   *  vive in `shared/launcher-rank.js`, che non conosce il DOM ed è provabile
   *  sotto node.
   */
  _renderList() {
    if (!this.list) return;
    const apps = this._apps;
    const query = this.search?.value || '';
    this._syncHeading(query);
    this._syncStatus();
    if (!this._entries.length) {
      this._rankedKeys = [];
      this._select(null);
      this._setListRole(false);
      /* **Tre** stati vuoti, non uno solo (6.2). "Non è ancora arrivato
         niente", "non si è potuto leggere" e "non c'è niente" sono ancora la
         stessa schermata nella griglia della scheda — era il limite denunciato
         in `docs/using/app-launcher.md` — e sono tre risposte diverse: la prima
         chiede di aspettare, la seconda di riprovare, la terza di installare
         qualcosa. Un controller che non c'è affatto è un guasto, non
         un'attesa: senza il primo ramo resterebbe "Caricamento…" per sempre. */
      let key = 'launcher.empty';
      if (!apps) key = 'launcher.error';
      else if (apps.isLoadingLists()) key = 'launcher.loading';
      else if (apps.listsFailed()) key = 'launcher.error';
      this.list.replaceChildren(this._note(key));
      return;
    }
    const ranked = rankEntries(this._entries, query, this._usage, i18n.locale);
    if (!ranked.length) {
      this._rankedKeys = [];
      this._select(null);
      this._setListRole(false);
      // Terzo stato, distinto dai due di sopra: le voci ci sono, è la query a
      // non trovarle. Dirlo *con dentro la query* è la differenza fra "non c'è"
      // e "non c'è **questo**".
      this.list.replaceChildren(this._note('launcher.noResults', { query: query.trim() }));
      return;
    }
    /* `replaceChildren` con i nodi già esistenti: quelli che restano vengono
       spostati, non ricreati, e quelli fuori dai risultati si staccano ma
       sopravvivono in `this._rows`. Una sola scrittura sul DOM per tasto. */
    this._setListRole(true);
    this.list.replaceChildren(...ranked.map(entry => this._rows.get(entry.key).el));
    // La lista è stata riordinata sotto il dito: si riparte dall'alto, dove sta
    // il risultato migliore. Senza questo, dopo aver scorso e poi digitato si
    // resterebbe a metà di una lista che nel frattempo si è accorciata.
    this.list.scrollTop = 0;
    this._rankedKeys = ranked.map(entry => entry.key);
    /* Chi comanda la selezione: finché non l'ha spostata nessuno, **segue la
       cima**; appena qualcuno la sposta (frecce, rotella, Tab) resta dov'è
       finché la sua voce è in lista.

       La distinzione non è teorica, ed è costata due difetti visti girare:
       senza il pin, un `apps_list_changed` mentre si sceglie riporterebbe
       l'evidenziazione in cima sotto le dita; **con** il pin e basta, invece,
       la prima apertura resta incollata alla riga che era in cima quando c'erano
       solo le skill — le app Android arrivano dopo, la lista si riordina, e ci
       si ritrova evidenziata la dodicesima voce, fuori schermo, che è quella
       che ⏎ aprirebbe.

       Digitare **non** conserva il pin: si sta rifacendo la domanda, e la
       risposta migliore è di nuovo in cima. È come si comporta ogni cassetto
       che si digita, e l'alternativa è ⏎ che apre qualcosa che non è più il
       primo risultato mentre scorre via dallo schermo.

       Non c'è mai "niente selezionato" con delle righe a schermo: ⏎ deve avere
       sempre una risposta, e "apre il primo se non hai scelto" sarebbe una
       seconda regola invisibile. Meglio una evidenziata, che si vede. */
    const keep = this._selectionPinned && this._rankedKeys.includes(this._selectedKey);
    // `reveal` solo se si conserva: la lista è appena tornata in cima, e una
    // selezione conservata va riportata sotto gli occhi di chi l'aveva scelta.
    this._select(keep ? this._selectedKey : this._rankedKeys[0], keep);
  }

  /** Un `listbox` promette che i suoi figli siano `option`, e nei tre stati
   *  vuoti il figlio è una frase. Tenere il ruolo anche allora farebbe
   *  annunciare "elenco, 1 voce" davanti a un messaggio che voce non è; con
   *  `presentation` il contenitore sparisce e resta il testo, che è tutto
   *  quello che c'è da leggere. */
  _setListRole(hasOptions) {
    this.list.setAttribute('role', hasOptions ? 'listbox' : 'presentation');
  }

  /* ── Selezione ─────────────────────────────────────────────────────────── */

  /** Sposta l'evidenziazione sulla riga `key`.
   *
   *  **Non muove il fuoco**, ed è la scelta centrale del passo 4: il fuoco
   *  resta nel campo mentre la selezione scorre, così si continua a scrivere e
   *  la tastiera software non si abbassa a ogni freccia. Chi legge lo schermo
   *  lo sa lo stesso, perché il campo è un `combobox` e la riga attiva gliela
   *  dice `aria-activedescendant`.
   *
   *  @param {string|null} key chiave della riga, o null per nessuna selezione.
   *  @param {boolean} reveal portarla in vista (4.3) — falso quando la
   *         selezione si sta solo riallineando a una lista appena riscritta,
   *         che è già scorrere in cima.
   */
  _select(key, reveal = false) {
    const previous = this._selectedKey && this._rows.get(this._selectedKey)?.el;
    if (previous) {
      previous.classList.remove('selected');
      previous.setAttribute('aria-selected', 'false');
    }
    this._selectedKey = key || null;
    const el = key ? this._rows.get(key)?.el : null;
    if (!el) {
      this._selectedKey = null;
      this.search?.removeAttribute('aria-activedescendant');
      return;
    }
    el.classList.add('selected');
    el.setAttribute('aria-selected', 'true');
    this.search?.setAttribute('aria-activedescendant', el.id);
    /* `block: 'nearest'` scorre di quel tanto che basta a farla rientrare: con
       'center' ogni passo rimescolerebbe la lista sotto gli occhi, e scendere
       di una riga sposterebbe tutte le altre di mezza schermata. È la
       differenza fra seguire la selezione e saltare. */
    if (reveal) el.scrollIntoView({ block: 'nearest' });
  }

  /** ↑↓ e rotella: `step` righe più in basso (positivo) o più in alto. */
  _moveSelection(step) {
    const keys = this._rankedKeys;
    if (!keys.length || !step) return;
    const current = keys.indexOf(this._selectedKey);
    /* Niente giro completo dalla coda alla testa: in una lista di settanta
       voci il salto disorienta più di quanto aiuti, e la rotella — che di passi
       ne produce a raffica — lo produrrebbe di continuo senza che nessuno
       l'abbia chiesto. Ai capi ci si ferma. */
    const next = Math.min(keys.length - 1, Math.max(0, (current < 0 ? 0 : current + step)));
    this._selectionPinned = true;
    this._select(keys[next], true);
  }

  /* ── Tastiera e rotella ────────────────────────────────────────────────── */

  /** Il foglio ha diritto ai tasti adesso?
   *
   *  A foglio aperto, e solo se non c'è un livello **sopra** di lui: una
   *  mini-app lanciata da qui, la scheda di una skill, la minichat. Il foglio
   *  resta aperto sotto di loro (è quello che 1.7 e 3.7 hanno verificato), e
   *  senza questa guardia continuerebbe a rispondere a frecce e ⏎ da dietro un
   *  overlay — la stessa classe di difetto per cui la chat ha smesso di rubare
   *  i caratteri (1.9), rovesciata.
   */
  _ownsKeys() {
    return this._open && !this.app?.hasOverlayAbove?.('launcher');
  }

  _onKeyDown(e) {
    if (!this._ownsKeys()) return;
    const active = document.activeElement;

    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      // Le frecce nel campo muoverebbero il cursore fra un capo e l'altro del
      // testo: qui il testo è una riga sola e la lista è l'unica cosa lunga.
      e.preventDefault();
      this._moveSelection(e.key === 'ArrowDown' ? 1 : -1);
      return;
    }

    if (e.key === 'Enter') {
      /* Un pulsante che ha il fuoco (la ✕, la crocetta della ricerca) si
         attiva da sé con ⏎, ed è quello che chi ci è arrivato con Tab si
         aspetta. Rubargli la pressione per aprire una riga che non sta
         guardando sarebbe l'unico punto del foglio in cui il fuoco non conta. */
      if (active?.tagName === 'BUTTON') return;
      e.preventDefault();
      // ⇧⏎ apre la **scheda** del risultato, non il risultato: il foglio
      // informativo da cui si disinstalla, si nasconde, si modifica. È la
      // pressione lunga sulla cella, sotto forma di tasto.
      this._activateSelected(e.shiftKey);
      return;
    }

    /* Lo spazio su una riga che ha il fuoco la attiva: è quello che un `option`
       promette a chi naviga con TalkBack o con Tab. Non entra in conflitto col
       type-ahead, che lo scarta apposta (v. `shared/type-ahead.js`), né col
       campo, dove lo spazio è testo e arriva qui con `active` di tipo INPUT. */
    if (e.key === ' ' && active?.classList?.contains('launcher-row')) {
      e.preventDefault();
      this._activateSelected(e.shiftKey);
      return;
    }

    /* Type-ahead (4.1, D6). Le guardie sono quelle della chat, alla lettera,
       perché è lo stesso hardware: `shared/type-ahead.js`. `focus()` sincrono
       dentro il keydown — Chromium recapita l'inserimento del carattere
       all'elemento appena messo a fuoco, quindi il primo tasto non va perso. */
    if (!isTypeAheadKey(e, active)) return;
    this.search?.focus();
  }

  /** La rotella di scorrimento muove la selezione (4.3).
   *
   *  **Quali eventi produca la rotella del Titan 2 non è accertato**: potrebbe
   *  essere `wheel`, potrebbero essere i codici delle frecce, potrebbe essere
   *  altro, e sull'emulatore la rotella non c'è — quindi da qui non è
   *  verificabile in nessun modo. Le due letture più probabili sono coperte
   *  entrambe e portano allo stesso posto: `wheel` qui, ↑↓ in `_onKeyDown`.
   *  Resta da leggere sul telefono vero (v. il piano, «Cosa NON è stabilito»).
   *
   *  Il gesto **sostituisce** lo scorrimento invece di accompagnarlo: si muove
   *  la selezione, e la selezione si porta dietro la lista con `scrollIntoView`.
   *  Due cose che si muovono con lo stesso gesto — la lista sotto e la
   *  selezione dentro — sarebbero due velocità da inseguire con l'occhio.
   */
  _onWheel(e) {
    if (!this._ownsKeys() || !e.deltaY) return;
    e.preventDefault();
    // `deltaMode`: 0 = pixel, 1 = righe, 2 = pagine. Fuori dai pixel l'unità è
    // già un passo e non c'è niente da accumulare.
    if (e.deltaMode !== 0) {
      this._wheelAcc = 0;
      this._moveSelection(Math.trunc(e.deltaY) || Math.sign(e.deltaY));
      return;
    }
    // Un cambio di verso azzera il residuo: altrimenti la prima passata
    // all'indietro spenderebbe l'avanzo di quella in avanti e sembrerebbe
    // ignorata.
    if (Math.sign(e.deltaY) !== Math.sign(this._wheelAcc)) this._wheelAcc = 0;
    this._wheelAcc += e.deltaY;
    const steps = Math.trunc(this._wheelAcc / WHEEL_PIXELS_PER_STEP);
    if (!steps) return;
    this._wheelAcc -= steps * WHEEL_PIXELS_PER_STEP;
    this._moveSelection(steps);
  }

  /** ⏎ / ⇧⏎ / Spazio: apre la voce selezionata, o la sua scheda. */
  _activateSelected(wantsDetail) {
    const key = this._selectedKey;
    if (!key) return;
    if (!wantsDetail) {
      this._activate(key);
      return;
    }
    const entry = this._entries.find(item => item.key === key);
    if (!entry) return;
    /* La scheda **non** conta come uso: è il posto dove si va per disinstallare
       o per capire cosa sia una voce, e contarla farebbe salire in classifica
       proprio le app di cui si dubita. Il ranking misura gli avvii. */
    this._apps?.detailEntry(entry);
  }

  /** Il titolo del foglio dice in che ordine si sta guardando: a campo vuoto è
   *  la lista di ciò che si usa ("Recenti", 3.3), appena si digita sono i
   *  risultati di una ricerca. */
  _syncHeading(query) {
    if (!this.titleEl) return;
    const key = query.trim() ? 'launcher.results' : 'launcher.recent';
    this.titleEl.dataset.i18n = key;
    this.titleEl.textContent = i18n.t(key);
    this.clearBtn?.classList.toggle('visible', !!query);
  }

  _note(key, params) {
    const note = document.createElement('p');
    note.className = 'launcher-note';
    // Niente `data-i18n` quando c'è un parametro: `_applyStaticTranslations()`
    // riscriverebbe il nodo con la stringa grezza, `{query}` compreso.
    if (!params) note.dataset.i18n = key;
    note.textContent = i18n.t(key, params);
    return note;
  }

  /** Apre la voce toccata e ne registra l'uso (3.4).
   *
   *  La chiave si rilegge dalla lista corrente e non dal `dataset` soltanto: fra
   *  il tocco e qui la lista può essere cambiata (una app disinstallata mentre
   *  il foglio è aperto è proprio il caso che il passo 2 ha verificato), e
   *  lanciare qualcosa che non c'è più è peggio che non fare niente.
   */
  _activate(key) {
    const entry = this._entries.find(e => e.key === key);
    if (!entry) return;
    /* L'uso si registra **prima** di avviare: `launchAndroidApp` porta via il
       task, e da lì in poi non è detto che questo JS giri ancora. Il costo di
       registrare un avvio poi fallito è una posizione in classifica; il costo
       opposto è un cassetto che non impara mai le app che si usano di più. */
    this._usage.record(entry.key);
    const started = this._apps?.activateEntry(entry);
    /* Una app Android se ne va con tutto il task: il foglio deve chiudersi, o
       al ritorno lo si ritroverebbe aperto sopra la conversazione senza averlo
       chiesto. Le altre due no — una Jenny App si apre *sopra* il foglio e
       Indietro ci riporta (1.7), e una skill o apre un dialog sopra il foglio o
       cambia vista, e allora è `switchMode` a chiuderlo (1.5).

       **Ma solo se è partita davvero** (6.3). Una riga stantia — il pacchetto
       disinstallato o disabilitato fra il caricamento della lista e il tocco —
       fallisce, e chiudere il foglio su un avvio fallito lascerebbe chi guarda
       davanti alla chat con un toast e senza più il cassetto da cui riprovare.
       Il ritardo non costa niente nel caso normale: quando l'avvio riesce siamo
       già in secondo piano, e il foglio si chiude dietro l'app che è salita. */
    if (entry.kind !== 'android') return;
    Promise.resolve(started).then((ok) => { if (ok !== false) this.close(); });
  }

  /** Una riga. Costruita nel DOM, non concatenando HTML: i nomi delle app
   *  arrivano dal PackageManager e dai manifest, cioè da testo che non
   *  scriviamo noi. */
  _buildRow(entry) {
    const row = document.createElement('div');
    row.className = 'launcher-row';
    row.dataset.key = entry.key;
    /* Semantica giusta dalla nascita, non aggiunta dopo — la stessa regola che
       la scheda Apps ha poi adottato per le sue tre stanze (`mobile-apps.js`),
       dove le righe nascono `<button>` invece di essere `<div>` a cui si
       riappiccicano `tabindex` e `role` a ogni ridisegno.

       `option` e non `button` (4.5): una lista di risultati con una selezione
       attiva è un `listbox` — l'elenco lo dichiara, la riga ne è una voce, e
       `aria-selected` dice **quale**. Con `role="button"` su ogni riga quella
       selezione non avrebbe modo di esistere per chi legge lo schermo: si
       sentirebbero settanta pulsanti tutti uguali, e l'evidenziazione sarebbe
       un colore e basta.

       `tabindex="0"` su *tutte*, non solo sulla selezionata: il pattern con
       fuoco mobile (`roving tabindex`) darebbe una sola fermata a Tab, e su
       Android il gesto di scorrimento di TalkBack passa per gli elementi
       focalizzabili. Le righe devono restare raggiungibili una per una. Chi
       arriva col fuoco su una riga la seleziona (v. l'ascoltatore `focusin`),
       così le due strade non si contraddicono mai.

       L'`id` serve ad `aria-activedescendant`: senza, il campo non avrebbe come
       nominare la riga attiva. */
    row.id = `launcher-opt-${++this._rowSeq}`;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');
    row.setAttribute('tabindex', '0');

    const iconWrap = document.createElement('div');
    iconWrap.className = 'launcher-row-icon';
    /* Le app Android hanno l'icona vera, che il gateway consegna come data URL
       base64; skill e Jenny App un glifo Tabler. Il prefisso si controlla
       perché `src` accetta anche schemi che eseguono, e questo valore ha fatto
       un giro fuori dal nostro codice. */
    if (entry.icon && entry.icon.startsWith('data:image/')) {
      const img = document.createElement('img');
      img.src = entry.icon;
      img.alt = '';
      iconWrap.appendChild(img);
    } else {
      const glyph = document.createElement('i');
      glyph.className = `ti ${entry.glyph || 'ti-apps'}`;
      /* Un glifo Tabler è un carattere della zona a uso privato dentro un
         font: senza questo, il nome accessibile della riga comincia con
         `` e TalkBack lo legge prima del nome. Misurato nell'albero di
         accessibilità della WebView, dove la riga di una skill si annunciava
         con un carattere di spazzatura davanti. L'icona di una app Android non
         ha il problema perché è una `<img alt="">`, che non contribuisce. */
      glyph.setAttribute('aria-hidden', 'true');
      iconWrap.appendChild(glyph);
    }
    row.appendChild(iconWrap);

    const text = document.createElement('div');
    text.className = 'launcher-row-text';
    const name = document.createElement('span');
    name.className = 'launcher-row-name';
    name.textContent = entry.name;
    text.appendChild(name);

    /* La seconda riga (difetto 02 e difetto 05 insieme). È **una sola**, e su
       una riga sola: un guasto ha la precedenza sulla descrizione perché è
       quello che spiega cosa fare adesso, e sta *dentro* la riga con
       `text-overflow: ellipsis` invece che in un blocco che si allarga. Nella
       griglia di oggi l'errore dentro la cella alza tutta la riga da 100 a 147
       px — un manifest rotto deforma la pagina di tutti gli altri. */
    const secondary = entry.problem || entry.description;
    if (secondary) {
      const desc = document.createElement('span');
      desc.className = entry.problem
        ? 'launcher-row-desc launcher-row-desc--problem' : 'launcher-row-desc';
      desc.textContent = secondary;
      // Il testo è tagliato dall'ellissi: il titolo lo dà per intero a chi ci
      // resta sopra, e TalkBack lo legge comunque dal contenuto.
      desc.title = secondary;
      text.appendChild(desc);
    }
    row.appendChild(text);

    /* `has_server`: una Jenny App con un backend proprio non è la stessa cosa
       di una che vive nel workspace, e il gateway lo dice già. Un glifo, non
       una parola: la colonna di destra è stretta e il tipo ci sta già. */
    if (entry.hasServer) {
      const server = document.createElement('i');
      server.className = 'ti ti-cloud launcher-row-server';
      server.title = i18n.t('launcher.hasServer');
      /* Questo glifo, a differenza di quello dell'icona, **porta
         informazione**: non si nasconde, si nomina. Con `role="img"` e
         un'etichetta il carattere della zona a uso privato non arriva a
         TalkBack, che legge la frase al suo posto. */
      server.setAttribute('role', 'img');
      server.setAttribute('aria-label', i18n.t('launcher.hasServer'));
      row.appendChild(server);
    }

    const kind = document.createElement('span');
    kind.className = 'launcher-row-kind';
    const kindKey = KIND_LABEL_KEYS[entry.kind];
    if (kindKey) {
      kind.dataset.i18n = kindKey;
      kind.textContent = i18n.t(kindKey);
    }
    row.appendChild(kind);
    return row;
  }
}
