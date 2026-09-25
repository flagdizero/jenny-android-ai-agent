/** La casa — guscio.
 *
 *  La seconda interfaccia di Jenny, quella il cui unico mestiere e' la
 *  conversazione. L'officina (`mobile-app.js`) resta intera e resta dov'e': i
 *  due gusci non si vedono fra loro, non condividono un nodo del DOM e non si
 *  possono rompere a vicenda. Condividono le fondamenta — `assets/shared/*` —
 *  e nient'altro.
 *
 *  Piano: `.agent/casa-plan.md`.
 *
 *  **Il contratto col guscio nativo e' di sei metodi.** Android chiama
 *  `window.mobileApp.onNativeReady()`, `.goHome()`, `.onPackageChanged()`,
 *  `.handleHardwareBack()`, `.openChat()` e `.isChatOnScreen()`. Erano tre in
 *  questo commento, poi cinque, e il conto era sbagliato tutte e due le volte:
 *  quelle che mancavano non si trovano cercando `window.mobileApp.<name>`
 *  perche' il Kotlin le invoca su una variabile locale (`var app =
 *  window.mobileApp; … app.handleHardwareBack()`). Il ponte verso il JS si
 *  conta sulle **chiamate**, non sul nome dell'oggetto. La sesta era scritta
 *  per l'officina (`app.currentMode === 'chat'`), qui non c'era, e in casa gli
 *  avvisi in coda non si cancellavano mai (trovato il 24/09/2026).
 *
 *  Nell'altro verso la casa chiama `JennyNative.chatOpened()` quando la chat
 *  personale arriva a schermo (v. `_reportChatOnScreen`).
 *
 *  Il nome globale resta `mobileApp` apposta: cosi' il guscio nativo non sa, e
 *  non deve sapere, quale delle due interfacce ha caricato.
 */

import { ActivityLine } from './home-activity.js';
import { HomeChat } from './home-chat.js';
import { NotebookPages } from './home-notebook-pages.js';
import { HomeReader } from './home-reader.js';
import { HomeAudit, reportMessage } from './home-audit.js';
import { HomeJenny } from './home-jenny.js';
import { HomeModel } from './home-model.js';
import { HomeUpdates } from './home-updates.js';
import { HomeBackup } from './home-backup.js';
import { HomeYou } from './home-you.js';
import { WhoPanel, dotColor } from './home-who.js';
/* Il cassetto delle app, **preso dall'officina e non ricopiato**: e' lo stesso
   modulo per i due gusci. I dati e le azioni stanno in `shared/`, fuori da
   qualunque schermata — ci sono usciti il 21/09/2026, quando la scheda «App»
   che li ospitava e' stata cancellata. */
import { JennyGap } from './shared/jenny-gap.js';
import { JennyMascot } from './shared/jenny-mascot.js';
import { LauncherController } from './mobile-launcher.js';
import { HomePages } from './home-pages.js';
import { HomeStrip } from './home-strip.js';
import { ComposerFocus } from './home-focus.js';
import { NotebookCard } from './home-notebook.js';
import { ChatMove } from './home-move.js';
import { AppsSource } from './shared/apps-source.js';
import { AppsActions } from './shared/apps-actions.js';
import { isOpenableProjectName, projectKey, projectNameOf } from './shared/conversation-list.js';
import { PROJECT_WORDS, createProjectFlow } from './shared/project-create.js';
import { deleteProjectFlow } from './shared/project-delete.js';
import { showToast } from './shared/utils.js';
import { api } from './shared/api-client.js';
import { ImageHandler } from './shared/image-handler.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';
import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { sessionManager } from './shared/session-manager.js';
import { wsManager } from './shared/ws-manager.js';
import './shared/theme.js';

/* Quanto aspettare prima di dire che il filo e' interrotto. Il socket si riapre
   da se' e la maggior parte delle cadute dura meno di un battito: annunciarle
   tutte vorrebbe dire far lampeggiare una riga d'allarme mentre non e' successo
   niente. Si parla solo se il silenzio dura. */
const WIRE_GRACE_MS = 2_500;

/* Dove appoggia i piedi Jenny fuori dalla chat, in px dal fondo. Nella chat il
   suo pavimento e' il composer e si misura (v. `_bindComposer`); nelle pagine
   il composer non c'e', e la tavola `Quaderno.dc.html` la disegna a venti pixel
   dal fondo — sopra la maniglia del cassetto, che e' la tavola dopo. */
const FLOOR_NO_COMPOSER = 20;

/* Il nome di lei finche' le impostazioni non hanno detto il suo: lo stesso
   ripiego del server (`settings_api`, `bot_name or "Jenny"`). E' un nome
   proprio, non una frase da tradurre. */
const DEFAULT_BOT_NAME = 'Jenny';

/* Le stanze oltre la conversazione, e dove si atterra premendo Indietro una
   volta. La catena e' lineare e sta **in un posto solo**: `_setView` la usa per
   sapere quali nomi esistono, `goBackOneRoom` per percorrerla e l'occhiello
   per scriverci sopra dove porta. Aggiungere una stanza e' una riga qui.

   `settings` non e' una stanza: e' la **pagina** Impostazioni, dal
   23/09/2026, e le stanze che si aprono da li' ci tornano sopra — la vista
   torna `chat`, e la pista va su quella pagina (v. `goBackOneRoom`). */
const BACK_TO = {
  pages: 'chat',
  reader: 'pages',
  jenny: 'settings',
  model: 'settings',
  updates: 'settings',
  backup: 'settings',
};

/* I fogli che stanno sopra le pagine, nel top layer (`showModal()`): quelli
   di una pressione lunga — un quaderno dai Quaderni, un'app dal cassetto — e
   quello di «Segnala». Scritti una volta: li leggono sia chi chiede se c'e'
   qualcosa sopra (`hasOverlayAbove`) sia chi li chiude (`_closeOverlays`), e
   due elenchi separati divergevano al primo foglio nuovo. */
const LONG_PRESS_SHEETS = ['home-notebook-sheet', 'jenny-app-sheet', 'android-app-sheet'];
const REPORT_SHEET = 'home-audit-dialog';

/* Le stesse domande dell'officina, dette come si dicono in casa.
 *
 *  Il giro di creazione e' uno solo (`shared/project-create.js`) e non sa come
 *  si chiami quel che sta creando: prende le chiavi da qui. Si sovrascrivono
 *  **solo** le frasi che dicono «progetto» — invalidName no, perche' la regola
 *  dei nomi e' una e citarla due volte vorrebbe dire tenerne due allineate.
 */
const NOTEBOOK_WORDS = {
  ...PROJECT_WORDS,
  namePrompt: 'home.who.create.name',
  nameTaken: 'home.who.create.taken',
  seedRequired: 'home.who.create.seedRequired',
  created: 'home.who.create.created',
  wikiOff: 'home.who.create.wikiOff',
  rejected: 'home.who.create.rejected',
  leftoverBody: 'home.who.create.leftover',
  leftoverBodyNoCount: 'home.who.create.leftoverNoCount',
};

/* ...e quelle della cancellazione, per la stessa ragione: il giro e' uno
   (`shared/project-delete.js`), e in casa quel che si cancella e' un quaderno. */
const NOTEBOOK_DELETE_WORDS = {
  confirm: 'home.notebook.deleteConfirm',
  confirmWithChat: 'home.notebook.deleteConfirmWithChat',
  failed: 'home.notebook.deleteFailed',
  busy: 'home.notebook.deleteBusy',
};

class HomeApp {
  constructor() {
    this.thread = document.getElementById('home-thread');
    this.chat = new HomeChat(this.thread);
    this.jenny = new JennyMascot(document.querySelector('.home-shell'));
    /* Il margine che i messaggi lasciano a Jenny, **solo dove lei c'e'**. Si
       consegna alla chat dopo la mascotte perche' le serve il suo nodo vero:
       la banda da scansare si misura su di lei, non su dei numeri copiati —
       cosi' vale anche quando cambia taglia, quando la metti via sul bordo e
       quando la trascini dall'altra parte. */
    this.chat.gap = new JennyGap(this.thread, this.jenny.el);
    this.activity = new ActivityLine(document.getElementById('home-activity'), {
      onOpenInWorkshop: (turnId) => this._openInWorkshop(turnId),
    });
    this.empty = document.getElementById('home-empty');
    this.emptyText = document.getElementById('home-empty-text');
    this.wire = document.getElementById('home-wire');
    this.input = document.getElementById('home-input');
    this.send = document.getElementById('home-send');
    this.attach = document.getElementById('home-attach');
    this.pending = document.getElementById('home-pending');
    /* Il composer vero, preso adesso per riferimento: piu' tardi il trasloco
       mette nelle pagine delle foto della chat, e un `querySelector` per
       classe puo' rispondere con il composer di una foto (v. `_bindComposer`). */
    this.composer = this.input?.closest?.('.home-composer') || null;
    this.activityEl = document.getElementById('home-activity');
    this.shell = document.querySelector('.home-shell');

    /* I comandi dell'intestazione che cambiano con la stanza — e la pastiglia
       delle pagine del quaderno, che dal 23/09/2026 sta nella barra dove
       scrivi, al posto che era del bottone del cassetto. */
    this.pagesBtn = document.getElementById('home-notebook-pages-open');
    this.pagesCount = document.getElementById('home-notebook-pages-count');
    this.backBtn = document.getElementById('home-back');
    this.backLabel = document.getElementById('home-back-label');
    this.talkBtn = document.getElementById('home-talk');
    this.editBtn = document.getElementById('home-edit');
    this.talkLabel = document.getElementById('home-talk-label');

    /* «Tu e Jenny»: le impostazioni di chi la usa, cioe' la pagina
       Impostazioni. La porta dell'officina vive li' dentro, in fondo: la apre
       questo guscio, perche' e' lui a sapere come si apre. */
    this.you = new HomeYou({
      onWorkshop: () => this._openInWorkshop(null),
      onJenny: () => this.openJenny(),
      onModel: () => this.openModel(),
      onUpdates: () => this.openUpdates(),
      onBackup: () => this.openBackup(),
    });
    /* `jennyRoom` e non `jenny`: quella e' lei, lo sprite che cammina sul
       bordo. Questa e' la stanza che dice com'e' fatta. */
    this.jennyRoom = new HomeJenny({
      onChange: () => this.you.sayJenny(this.jennyRoom.value()),
      onFloating: (floating) => this._keepFloating(floating),
      onName: (name) => this._keepName(name),
    });
    /* Chi risponde. Un salvataggio li' dentro torna col payload intero di
       `/api/settings`: lo si rimette nella cache invece di richiederlo, o la
       riga di «Tu e Jenny» resterebbe sulla marca di prima. */
    this.modelRoom = new HomeModel({ onSettings: (data) => this._keepSettings(data) });
    /* Gli aggiornamenti: la seconda vista di `shared/update-flow.js`, di cui
       l'officina e' la prima. Un controllo riuscito porta una versione fresca,
       e quella deve riscrivere la riga **e** la cache del guscio. */
    this.updatesRoom = new HomeUpdates({
      onVersion: (version) => this._keepVersion(version),
    });
    /* Il backup. La terza vista di `shared/backup-flow.js`; l'unica cosa nuova
       e' la data, che prima non esisteva da nessuna parte. */
    this.backupRoom = new HomeBackup({
      onExported: () => this.you.sayBackup(this.backupRoom.value()),
    });

    /* Le altre due stanze. La mappa non si importa: si carica al primo tocco
       sulla sua linguetta insieme ai 280 kB di D3 (v. `home-map.js`), e un
       `import` statico la pagherebbe a ogni avvio della casa. */
    this.pages = new NotebookPages({
      onOpenPage: (path, label) => this.openPage(path, label),
      onNeedMap: (data, _rows, notebook) => this._drawMap(data, notebook),
    });
    this.reader = new HomeReader();
    this.reader.onTitle = (title) => this._readerTitle(title);
    this.audit = new HomeAudit(this.reader);
    this.audit.onFiled = (report) => this._bringToChat(report);
    /* Aperto o chiuso l'editor, cambiano i comandi dell'intestazione — e la
       barra della selezione, che con l'editor aperto non ha piu' senso: li' il
       gesto e' un altro. */
    this.reader.onEditing = () => { this._applyHead(); this.audit.refresh(); };
    this.map = null;

    /** Quale stanza e' a schermo: `chat` o una delle chiavi di `BACK_TO`. */
    this.view = 'chat';
    /* Com'era Jenny quando hai lasciato la chat. Fuori dalla chat sta al bordo
       — lo dice la tavola, che la disegna a `right:-56px` mentre nelle due
       della chat sta a `-30px` — ma se l'avevi messa via tu, tornando non deve
       ricomparire: quella era una tua decisione, non lo stato della stanza. */
    this._jennyWasOut = true;

    /* Il nome della conversazione personale e' il nome di lei: `bot_name`
       delle impostazioni, che arriva dopo (v. `_readName`). Non si legge
       dalla testa — porta il nome del quaderno o della pagina, e la riga
       personale dei Quaderni direbbe «piante» — ne' si scrive fisso: con un
       altro nome la fila e i Quaderni dicevano comunque «Jenny». */
    this.nameEl = document.getElementById('home-head-name');
    this._personalName = DEFAULT_BOT_NAME;

    /* Le bozze, una per conversazione. Senza, mezza frase scritta in casa
       partirebbe dentro il quaderno che apri subito dopo: e' la stessa famiglia
       di guasto di cui parla `switchGeneration` — quel che dici finisce nel
       diario di un altro progetto — solo un attimo prima. In memoria e basta:
       una bozza non e' una cosa da conservare fra due avvii. */
    this._drafts = new Map();

    /* «Con chi parli», cioe' la pagina Quaderni. Il pannello non sa cosa sia
       una chiave di sessione — dice quale nome hai toccato, e la conversazione
       la apre questo guscio (e porta alla chat: `openConversation`). */
    this.who = new WhoPanel(document.getElementById('home-notebooks'), {
      personalName: () => this._personalName,
      currentProject: () => projectNameOf(sessionManager.currentKey),
      onPick: (name) => this.switchConversation(name ? projectKey(name) : null),
      onHold: (name) => this.notebookCard().show(name),
    });

    /* Il selettore di allegati e' lo stesso dell'officina, con gli stessi tetti
       del server (4 immagini, 8 MB l'una): superarli fa rifiutare il messaggio
       intero, quindi i limiti devono stare da una parte sola. */
    this.files = new ImageHandler();
    this.files.onChange = () => this._renderPending();
    /* Un allegato che non entra lo diceva nessuno: spariva e basta. Ora lo
       dice il telefono, con le stesse parole che userebbe il gateway. */
    this.files.onReject = (reason) => this.chat.noteRefusal(reason);

    this._wireTimer = null;
    this._threadFailed = false;
    /* Il payload delle impostazioni, chiesto una volta e diviso fra le due
       stanze che ne leggono un campo per uno. */
    this._settings = null;
    this._running = false;
    /* Il guscio nativo copre la pagina con un caricamento finche' non chiama
       onNativeReady: fino ad allora qualunque animazione d'ingresso scorre
       dietro una tendina e se ne vede solo la coda. */
    this._shellReady = false;
    this._shellReadyCbs = [];

    /* Il cassetto, cioe' la pagina App: costruito subito perche' il suo
       markup e' statico. **Incorporato**: e' una pagina della pista, non un
       foglio che sale. I **dati** invece arrivano dopo — sono due fetch, e
       quella delle app Android ricodifica ogni icona in base64: si agganciano
       la prima volta che la pagina si accende (v. `appsSource()`). */
    this.launcher = new LauncherController(this, { builtin: true });
    /* La pista delle pagine. Si costruisce subito — il gesto va agganciato
       prima che un dito possa arrivarci — e si riempie dopo, quando il filo e'
       a schermo: l'elenco e' una lettura di rete, e farla aspettare dalla
       chat vorrebbe dire una casa vuota per il tempo di un giro. */
    /* La chat e' una, e si sposta nella pagina di un quaderno quando ci
       arrivi: il trasloco sa spostarla e fotografarla, questo guscio sa
       cambiarle conversazione. Prima della pista, che lo usa dal primo
       `goTo`. */
    this.chatMove = new ChatMove({
      chat: document.getElementById('home-chat'),
      change: (key) => this.showConversation(key),
      currentKey: () => sessionManager.currentKey,
      /* In fondo **senza condizioni**, non `keepBottom`: spostata nel
         documento la chat riparte da scroll 0, e `keepBottom` segue il fondo
         solo «se ci si era» — lo scroll azzerato puo' fargli credere che
         l'utente sia risalito. E la foto che e' appena entrata mostrava il
         fondo: arrivare altrove sarebbe il trucco che si vede. */
      atBottom: () => this.chat?.scrollToBottom(),
    });
    this.homePages = new HomePages(this);
    this._apps = null;
    /* La fila dei nomi in alto: legge le voci dalla pista, e tenendo premuto
       un nome le fa spostare. */
    this.strip = new HomeStrip(document.getElementById('home-strip'), {
      homePages: this.homePages,
      chatName: () => this._chatName(),
      onChange: (open) => this._onSort(open),
    });
    /* Le tre pagine fisse che non sono la chat: cosa fanno quando le guardi. */
    this.homePages.register('app', {
      activate: () => this.launcher.open(),
      deactivate: () => this.launcher.close(),
    });
    this.homePages.register('notebooks', { activate: () => this.who.show() });
    this.homePages.register('settings', { activate: () => this._openSettings() });
    /* La fila **non** si disegna qui: le traduzioni non ci sono ancora
       (arrivano in `init`, dopo il bootstrap) e i nomi delle pagine fisse
       uscivano come chiavi grezze — «casa.fila.app» — per il tempo di un giro.
       La disegna `_applyTranslations`, appena le parole sono arrivate. */

    window.mobileApp = this;
    this.init();
  }

  async init() {
    /* Il segreto di bootstrap viaggia in un fragment dell'URL e viene
       consumato al primo caricamento: senza questa prima chiamata ogni
       richiesta successiva e' anonima. */
    try {
      await api.bootstrap();
    } catch (err) {
      console.error('Bootstrap failed:', err);
      api.clientLog('error', 'home.bootstrap', String(err && err.stack || err));
    }

    /* Le traduzioni prima della storia: la conversazione porta etichette
       tradotte (la provenienza di un messaggio entrato da fuori), e disegnarla
       prima vorrebbe dire scriverci dentro le chiavi grezze. */
    await i18n.load(i18n.locale);
    this._applyTranslations();

    /* Quel che apparteneva alla conversazione lasciata scade qui. Il `turn_end`
       del turno in volo arrivera' a una chat che non guardiamo piu' e verra'
       scartato da `_belongsHere`: chi tiene stato *per turno* — la faccia di
       Jenny, la riga di lavoro, il bottone Ferma — resterebbe ad aspettarlo per
       sempre. In officina lo stesso evento serve alla stessa cosa
       (`mobile-jenny.js`, `_releaseTrackedTurn`). */
    sessionManager.addEventListener('chat:switch', () => this._releaseTurn());
    /* E riporta nella conversazione. Le pagine parlano di *un* quaderno: dai
       Quaderni si puo' saltare in un altro, e restare li' vorrebbe dire leggere
       l'elenco di una stanza in cui non sei piu'. */
    sessionManager.addEventListener('chat:switch', () => this._setView('chat'));

    wsManager.addEventListener('chat:open', () => this._setWire(true));
    wsManager.addEventListener('chat:close', () => this._setWire(false));
    wsManager.addEventListener('chat:message', (e) => {
      this._readRunStatus(e.detail);
      this._readActivity(e.detail);
      this.chat.handleFrame(e.detail);
    });

    this._bindComposer();
    /* Il fuoco resta sul campo: sul Titan la tastiera e' fisica, e un tocco sul
       filo che glielo toglie manda i tasti dopo nel vuoto (v. `home-focus.js`).
       La fila ne fa parte: toccare «Jenny» mentre scrivi non deve fermarti. */
    this.focus = new ComposerFocus({
      input: this.input,
      surfaces: [document.getElementById('home-chat'), document.getElementById('home-strip')],
      active: () => this._composerActive(),
    });
    this.focus.restore();
    /* Un messaggio rifiutato dal gateway torna nel campo, così puoi correggere
       invece di riscrivere — a meno che tu non abbia già ricominciato a
       scrivere: quello vince sempre, non si sovrascrive del testo vivo con del
       testo vecchio. */
    this.chat.onSendRejected = (text) => {
      if (!text || this.input.value.trim()) return;
      this.input.value = text;
      this._autosize();
    };
    this.pagesBtn?.addEventListener('click', () => this.openPages());
    /* Il + tondo della pagina Quaderni: un quaderno nuovo, e ci si entra. */
    this.newNotebook = document.getElementById('home-notebooks-new');
    this.newNotebook?.addEventListener('click', () => this.createNotebook());
    /* Le due vie d'uscita della stessa stanza, e fanno la stessa cosa: si esce
       da dove stai guardando — in alto a sinistra se leggi l'intestazione, in
       basso a destra col pollice. */
    this.backBtn?.addEventListener('click', () => this.goBackOneRoom());
    this.talkBtn?.addEventListener('click', () => this._setView('chat'));
    this.editBtn?.addEventListener('click', () => this.reader.startEdit());

    sessionManager.init();
    this._applyConversation();
    wsManager.connectChat();

    /* Non `await`: le pagine aggiunte e l'ordine arrivano quando arrivano, e
       finche' non ci sono la casa e' quella di chi non ha spostato niente, su
       Jenny — che e' esattamente quel che deve essere. */
    this.homePages.load();
    /* Neanche il nome si aspetta: fino ad allora la fila dice quello di
       sempre. */
    this._readName();

    try {
      await this.chat.load();
    } catch (err) {
      console.error('Thread load failed:', err);
      api.clientLog('error', 'home.thread', String(err && err.stack || err));
      this._showThreadError();
      return;
    }
    this.chat.syncEmpty();
  }

  /* La storia non e' arrivata. Non si finge una conversazione vuota: una chat
     vuota e una chat irraggiungibile sono due cose diverse, e confonderle
     significa far credere di aver perso tutto. */
  _showThreadError() {
    /* Il flag e' quel che impedisce a un cambio di lingua di riscriverci sopra
       "non c'e' ancora niente qui" — cioe' esattamente la bugia che questo
       messaggio esiste per non dire. */
    this._threadFailed = true;
    if (this.emptyText) this.emptyText.textContent = i18n.t('home.threadError');
    this._showEmpty(true);
  }

  /* ── Le conversazioni ── */

  /** Apre un'altra conversazione: un quaderno, o la casa (`null`).
   *
   *  Non e' una vista che cambia. Sotto una chiave `project:` Jenny lavora
   *  nella cartella di quel quaderno, si costruisce un altro contesto e
   *  **non** alimenta la memoria di lungo periodo (v. `session/keys.py`): e'
   *  un'altra conversazione, e l'intestazione e' la sola cosa a schermo che lo
   *  dice.
   *
   *  Due tocchi ravvicinati si sovrappongono e devono: chi perde e' il primo,
   *  sempre, e non chi risponde per ultimo. Lo garantisce la generazione che
   *  `switchTo` fa salire, che `loadThread` rilegge dopo la sua attesa — il
   *  filo butta da se' la storia di una conversazione gia' lasciata.
   */
  async switchConversation(key) {
    const target = key || sessionManager.personalKey;
    /* **Dove** la si apre lo decidono le pagine: una pagina di un quaderno
       mostra solo il suo, quindi da li' un'altra conversazione si apre nella
       pagina chat (v. `HomePages.openConversation`). */
    if (this.homePages) return this.homePages.openConversation(target);
    return this.showConversation(target);
  }

  /** La conversazione che la chat mostra adesso. */
  currentKey() {
    return sessionManager.currentKey;
  }

  /** Cambia la conversazione della chat, qui, senza chiedersi in che pagina.
   *
   *  E' il corpo che `switchConversation` aveva da solo prima delle pagine
   *  conversazione. Lo chiamano la regola delle pagine e il trasloco — che
   *  hanno gia' deciso dove — e nessun altro: da fuori si passa da
   *  `switchConversation`, o una pagina fissa finirebbe a mostrare un
   *  quaderno che non e' il suo.
   *
   *  **Cambia subito, prima della sua prima attesa**: il trasloco chiede la
   *  conversazione attuale appena dopo, e l'intestazione la legge.
   */
  async showConversation(key) {
    const from = sessionManager.currentKey;
    const target = key || sessionManager.personalKey;
    if (target === from) return;
    /* La bozza resta con la conversazione in cui l'hai scritta. */
    this._drafts.set(from, this.input?.value || '');
    if (!sessionManager.switchTo(target)) return;
    if (this.input) {
      this.input.value = this._drafts.get(target) || '';
      this._autosize();
    }
    this._applyConversation();
    try {
      await this.chat.reload();
      /* Una lettura riuscita toglie il messaggio d'errore precedente: se
         restasse, il vuoto di questa conversazione direbbe «non riesco a
         leggerla» di una storia che abbiamo appena letto. */
      if (this._threadFailed) {
        this._threadFailed = false;
        this._applyTranslations();
      }
    } catch (err) {
      console.error('Conversation switch failed:', err);
      api.clientLog('error', 'home.switch', String(err && err.stack || err));
      this._showThreadError();
    }
  }

  /** Un quaderno nuovo, e ci si entra.
   *
   *  Le due domande — come si chiama, di cosa si occupa — e tutto quel che puo'
   *  andare storto stanno in `shared/project-create.js`, che e' lo stesso giro
   *  che fa l'officina. Qui c'e' quel che e' di casa: le parole, i nomi gia'
   *  noti per l'avviso, e dove si va dopo.
   *
   *  Ci si entra, e non e' un di piu': aver dato un nome e scritto la riga di
   *  scope senza finire nella conversazione vorrebbe dire lasciare a meta' il
   *  gesto che l'utente ha cominciato.
   */
  async createNotebook() {
    const name = await createProjectFlow({
      words: NOTEBOOK_WORDS,
      t: (key, vars) => i18n.t(key, vars),
      known: this.who.known,
    });
    if (!name) return;
    this.who.invalidate();              // l'elenco su disco e' cambiato
    await this.switchConversation(projectKey(name));
  }

  /** La scheda di un quaderno, nata al primo uso. */
  notebookCard() {
    return (this._notebookCard ||= new NotebookCard({
      homePages: () => this.pagesPort(),
      open: (name) => this.switchConversation(projectKey(name)),
      delete: (name) => this.deleteNotebook(name),
      rename: (name) => this.renameNotebook(name),
    }));
  }

  /** Rinomina un quaderno dalla sua scheda.
   *
   *  La regola del nome si controlla **qui prima** che sul gateway — e' la
   *  stessa (`isOpenableProjectName` e' la copia fedele di quella del server) —
   *  solo per dirlo subito, senza un giro; il gateway la riapplica comunque.
   *  Il seguito e' quello della cancellazione, all'incontrario: se eri li'
   *  dentro ci resti, sotto il nome nuovo; i Quaderni, la fila e le pagine
   *  (che il gateway ha gia' rinominato) si rileggono.
   */
  async renameNotebook(name) {
    const written = await promptDialog(i18n.t('home.notebook.renamePrompt', { name: name }), {
      initial: name,
    });
    const newName = (written || '').trim();
    if (!newName || newName === name) return false;
    if (!isOpenableProjectName(newName)) {
      showToast(i18n.t('scope.invalidName'), 'error');
      return false;
    }
    try {
      await rpc.renameProject(name, newName);
    } catch (err) {
      /* I rifiuti attesi hanno un codice, e vanno detti nella lingua di chi
         legge: `conflict` (qualcuno ci sta scrivendo: un turno, un subagent,
         una passata del giardiniere, l'autocompact), `name_taken` (il nome
         nuovo e' gia' di una cartella o di una conversazione), `not_found` (il
         quaderno non c'e' piu'). Il testo inglese del server resta per i log;
         il motivo lo porta solo l'errore imprevisto. */
      const expected = {
        conflict: ['home.notebook.renameBusy', { name: name }],
        name_taken: ['home.notebook.renameTaken', { name: newName }],
        not_found: ['home.notebook.renameMissing', { name: name }],
      };
      const [key, parameters] = expected[err?.code]
        || ['home.notebook.renameFailed', { name: name, error: err?.message || '' }];
      showToast(i18n.t(key, parameters), 'error');
      return false;
    }
    const oldKey = projectKey(name);
    const newKey = projectKey(newName);
    this.homePages.renameConversation(oldKey, newKey);
    this._renameDraft(oldKey, newKey);
    /* I Quaderni rileggono **prima** del cambio: la pastiglia delle pagine,
       ridisegnandosi, chiede alla loro cache quante pagine ha il quaderno — e
       con la cache ancora sul nome vecchio perdeva il numero. Visto sul
       telefono il 23/09/2026 rinominando un quaderno di prova. */
    await this.who.refresh();
    /* La chat segue il nome nuovo **dove sta**, senza passare dalla regola
       delle pagine: `switchConversation` da qui — la pagina Quaderni —
       portava alla pagina chat, e rinominare non e' un modo di andarci. */
    if (sessionManager.currentKey === oldKey) {
      await this.showConversation(newKey);
      this._drafts.delete(oldKey);
    }
    await this.pagesPort().reload();
    showToast(i18n.t('home.notebook.renamed', { name: newName }), 'success');
    return true;
  }

  /* La bozza di un quaderno rinominato passa al nome nuovo. Restava sotto la
     chiave vecchia, cioe' persa: nessuna conversazione la chiede piu'. Se il
     quaderno e' quello a schermo la bozza viva e' nel campo, non in
     `_drafts`: si prende da li'. */
  _renameDraft(oldKey, newKey) {
    const draft = sessionManager.currentKey === oldKey
      ? this.input?.value || ''
      : this._drafts.get(oldKey);
    this._drafts.delete(oldKey);
    if (draft !== undefined) this._drafts.set(newKey, draft);
  }

  /** Cancella un quaderno dalla sua scheda, e fa il seguito che e' di casa.
   *
   *  La domanda la fa `deleteProjectFlow`, con le parole della casa. Il seguito:
   *  se la chat era li' dentro torna a un'altra conversazione (la personale,
   *  se la pagina chat mostrava quel quaderno) — restare in una chat che non
   *  esiste piu' vorrebbe dire scrivere a vuoto; la pagina
   *  Quaderni, sotto la scheda, si ridisegna senza quella riga; e le
   *  pagine si rileggono, perche' il gateway ha tolto anche la sua, se ne aveva
   *  una (v. `project_delete.py`).
   */
  async deleteNotebook(name) {
    if (!(await deleteProjectFlow(name, NOTEBOOK_DELETE_WORDS))) return false;
    const key = projectKey(name);
    /* Come nel rinomino, **senza portarti alla chat**: si cancella dalla
       pagina Quaderni, e li' si resta. La pagina chat che mostrava il
       quaderno torna alla personale; la chat, se lo mostrava, torna a quel
       che mostra la pagina chat. La bozza se ne va col quaderno: era scritta
       per una conversazione che non c'e' piu'. */
    if (this.homePages?.homeConversation === key) {
      this.homePages.homeConversation = sessionManager.personalKey;
    }
    if (sessionManager.currentKey === key) {
      await this.showConversation(this.homePages?.homeConversation || null);
    }
    this._drafts.delete(key);
    await this.who.refresh();
    await this.pagesPort().reload();
    showToast(i18n.t('home.notebook.eliminato', { name: name }), 'success');
    return true;
  }

  /* ── Le stanze ──
   *
   *  Le pagine del quaderno, il lettore, e le stanze delle impostazioni: si
   *  entra da una pagina e si torna indietro una alla volta — mai due per un
   *  gesto, che e' la stessa regola con cui Indietro chiude una scheda senza
   *  uscire anche dal quaderno.
   */

  /** Le pagine del quaderno in cui sei. Dalla chat personale non c'e' nulla da
   *  aprire, e infatti la pastiglia li' non compare. */
  async openPages() {
    const notebook = projectNameOf(sessionManager.currentKey);
    if (!notebook) return;
    this._setView('pages');
    await this.pages.load(notebook);
  }

  /** Una pagina del quaderno, letta. */
  async openPage(path, label) {
    const notebook = projectNameOf(sessionManager.currentKey);
    if (!notebook || !path) return;
    this._setView('reader');
    this._setHeadTitle(label || '');
    this._readerTitle(await this.reader.load(notebook, path, label));
  }

  /* Il titolo di una pagina arriva dopo una lettura — l'apertura, un
     salvataggio, un conflitto — e si scrive solo se si e' ancora nel lettore:
     tornati indietro nel frattempo, la testa dice gia' dove sei, e il titolo
     della pagina lasciata ci scriverebbe sopra. */
  _readerTitle(title) {
    if (this.view === 'reader') this._setHeadTitle(title);
  }


  /** La pagina Impostazioni e' diventata quella che guardi: si ridisegna e si
   *  rilegge quel che sa il server. */
  async _openSettings() {
    this.you.open();
    this.you.sayJenny(this.jennyRoom.value());
    const data = await this._askSettings();
    this.jennyRoom.setFloating(data?.floating || null);
    /* Lettura fallita: `null`, cioe' «non lo so», non un nome vuoto. */
    this.jennyRoom.setName(data ? data.agent?.bot_name || '' : null);
    if (data) this._applyBotName(data.agent?.bot_name);
    this.you.sayJenny(this.jennyRoom.value());
    this.modelRoom.setSettings(data);
    this.you.sayModel(this.modelRoom.value());
    this.updatesRoom.setVersion(data?.version || null);
    this.you.sayUpdates(this.updatesRoom.value());
    this.backupRoom.setBackup(data?.backup || null);
    this.you.sayBackup(this.backupRoom.value());
  }

  /** La stanza di lei: com'e' fatta. Ci si arriva solo da «Tu e Jenny», che
   *  e' anche il posto dove lo stato della finestra flottante e' gia' stato
   *  chiesto. */
  openJenny() {
    this._setView('jenny');
    this.jennyRoom.open();
  }

  /** «Chi risponde»: le marche configurate e i loro modelli. Ci si arriva da
   *  «Tu e Jenny», che ha gia' chiesto `/api/settings`. */
  openModel() {
    this._setView('model');
    this.modelRoom.open();
  }

  /** «Aggiornamenti». */
  openUpdates() {
    this._setView('updates');
    this.updatesRoom.open();
  }

  /** «Backup». */
  openBackup() {
    this._setView('backup');
    this.backupRoom.open();
  }

  /* Una versione fresca arrivata da un controllo manuale: va nella riga e
     nella cache, o alla prossima apertura di «Tu e Jenny» si rileggerebbe
     quella vecchia da un payload messo da parte prima del controllo. */
  _keepVersion(version) {
    if (!version) return;
    this._settings?.then?.((data) => {
      if (data) data.version = version;
    });
    this.you.sayUpdates(this.updatesRoom.value());
  }

  /* La finestra flottante appena accesa o spenta: va nella cache, o alla
     prossima apertura delle Impostazioni `setFloating` rimetterebbe lo stato
     letto la prima volta — l'interruttore tornava spento con la finestra
     accesa (visto sul telefono il 25/09). */
  _keepFloating(floating) {
    this._settings?.then?.((data) => {
      if (data && floating) data.floating = floating;
    });
  }

  /* Il nome appena salvato nella stanza di lei: va nella cache, o alla
     prossima apertura delle Impostazioni `setName` rimetterebbe il nome letto
     la prima volta; e va nella fila e nei Quaderni, che lo scrivono. */
  _keepName(name) {
    this._settings?.then?.((data) => {
      if (data && name) (data.agent ||= {}).bot_name = name;
    });
    this._applyBotName(name);
  }

  /* Il nome di lei, dalle impostazioni: la stessa lettura che la pagina
     Impostazioni fa comunque, e che resta in cache per lei. */
  async _readName() {
    const data = await this._askSettings();
    if (data) this._applyBotName(data.agent?.bot_name);
  }

  /* Il nome della conversazione personale, dove lo si scrive: la fila e la
     riga personale dei Quaderni. Vuoto vuol dire il ripiego, come sul server. */
  _applyBotName(name) {
    const newName = (typeof name === 'string' && name.trim()) || DEFAULT_BOT_NAME;
    if (newName === this._personalName) return;
    this._personalName = newName;
    this.strip?.draw();
    this.who?.render();
  }

  /* Il payload fresco che torna da un salvataggio: ha la stessa forma di
     `/api/settings`, quindi prende il posto di quello in cache e le righe che
     lo leggono si riscrivono. Senza, la riga «Chi risponde» direbbe la marca
     di prima fino al riavvio della casa. */
  _keepSettings(data) {
    if (!data) return;
    this._settings = Promise.resolve(data);
    this.you.sayModel(this.modelRoom.value());
  }

  /* `/api/settings` **una volta**, per due stanze: la versione la scrive «Tu e
     Jenny», lo stato della finestra flottante la stanza di lei. Quel payload
     porta provider, contatori e lavoratori periodici — chiederlo due volte per
     due campi sarebbe due volte quel peso.

     Il fallimento non si ricorda (stesso patto di `ensureVendor`): una rete
     andata male una volta lascerebbe la riga della finestra flottante
     nascosta fino al riavvio della casa, e quella non e' una versione che
     manca — e' un'impostazione sparita. */
  _askSettings() {
    if (!this._settings) {
      this._settings = api.getSettings().catch((err) => {
        console.warn('casa: settings not read', err);
        this._settings = null;
        return null;
      });
    }
    return this._settings;
  }

  /** Segnalata una cosa: si atterra nella chat del quaderno, col messaggio
   *  gia' partito.
   *
   *  **Non e' una funzione nuova, e' una giunzione.** «Parlane» porta gia' in
   *  questa stanza; `_send()` legge gia' dalla casella e disegna la bolla; e la
   *  sessione corrente, dal lettore, **e' gia' quella del quaderno** — ci sei
   *  dentro. Qui si mettono in fila tre cose che esistevano separate.
   *
   *  **Parte da solo e non resta nella casella.** Il file della segnalazione e'
   *  gia' nato in quel momento: lasciarlo li' senza inviare riporterebbe nel
   *  vuoto proprio quella segnalazione — che e' il difetto per cui questo
   *  atterraggio esiste. Un gesto, un atto completo.
   *
   *  Quel che l'utente stava scrivendo non si perde: se la casella non e'
   *  vuota, la bozza torna dov'era appena il messaggio e' partito.
   *
   *  **Se non parte** (il filo e' giu') nella casella resta il messaggio della
   *  segnalazione, che e' quello da non perdere: la bozza rimessa al suo posto
   *  lo cancellava, e la segnalazione tornava nel vuoto. La bozza gli va sotto,
   *  cosi' non si perde niente dei due e si rimanda con un tocco. */
  _bringToChat(report) {
    if (!this.input) return;
    const draft = this.input.value;
    this._setView('chat');
    const message = reportMessage(report);
    this.input.value = message;
    const started = this._send();
    if (!draft.trim()) return;
    this.input.value = started ? draft : `${message}\n\n${draft}`;
    this._autosize();
  }

  /** Conferma di uscire dal lettore buttando via le modifiche.
   *
   *  Il cambio stanza e' **differito**, non annullato: alla risposta
   *  affermativa si ripassa dallo stesso `_setView`, stavolta col buffer
   *  pulito. La pressione che ha aperto la modale l'ha consumata la modale,
   *  quindi nessuno naviga piu' al posto nostro.
   *
   *  `then`, se c'e', e' il resto del gesto che ha chiesto di uscire — Home, un
   *  avviso — e parte al posto del solo cambio di stanza: anche lui aspetta la
   *  risposta, e con un no non succede niente. */
  async _confirmLeaveReader(target, then = null) {
    this.reader.blurEditor();
    const ok = await confirmDialog(i18n.t('home.reader.discardConfirm'));
    if (!ok) return;
    if (this.view !== 'reader') return;  // uscito da un'altra strada nel frattempo
    this.reader.cancelEdit();
    if (then) then();
    else this._setView(target);
  }


  /** Indietro di **una** stanza. Vero se c'era dove tornare.
   *
   *  Fra le pagine c'e' un gradino in piu' che le stanze non hanno: da
   *  qualunque pagina che non sia la chat, «indietro» riporta alla chat —
   *  **ovunque sia finita nella fila**. Sta qui e non su un bottone suo perche'
   *  cosi' ci passa anche l'Indietro di Android, che da una pagina deve
   *  tornare a casa, non uscire dall'app. */
  goBackOneRoom() {
    if (this.view === 'chat') {
      if (!this.homePages || this.homePages.index === this.homePages.chatIndex) return false;
      this.homePages.goTo(this.homePages.chatIndex);
      return true;
    }
    const target = BACK_TO[this.view];
    if (!target) return false;
    if (target === 'settings') {
      this._setView('chat');
      this.homePages.goToId('settings', { animated: false });
      return true;
    }
    this._setView(target);
    return true;
  }

  /** La pista ha cambiato casella: la fila dice dove sei. */
  onPageChanged(index, entry) {
    this._entry = entry;
    /* Su quale pagina si e' lo dice un attributo, come per le stanze: cosi' la
       geometria resta nel CSS e qui c'e' solo il nome. */
    this.shell?.setAttribute('data-page', entry?.id || '');
    /* La tastiera non resta aperta su un campo che e' uscito di scena: i tasti
       dopo finirebbero nella chat che non guardi. Ne' su quello coperto
       dall'avviso di un quaderno cancellato (v. `onGoneChanged`). */
    if (!this._haComposer(entry) || this.homePages?.goneHere?.()) this.input?.blur();
    else this.focus?.restore();
    this._placeJenny();
    this.strip?.draw();
    this._applyHead();
    this._reportChatOnScreen();
  }

  /** Una pagina ha scoperto che la sua cosa non c'e' piu', o che c'e' di
   *  nuovo. Arriva **dopo** `onPageChanged` — il controllo e' una lettura
   *  di rete — e se la pagina e' quella a schermo il campo va tolto di mezzo:
   *  con la tastiera fisica il fuoco era gia' stato rimesso, e i tasti
   *  sarebbero andati a un quaderno cancellato. */
  onGoneChanged(panel) {
    if (!this.homePages || this.homePages.panelOf(this.homePages.index) !== panel) return;
    if (this.homePages.goneHere()) this.input?.blur();
    else this.focus?.restore();
  }

  /** La pista ha ridisegnato le sue pagine: un nome in piu', uno in meno, un
   *  ordine nuovo. */
  onPagesChanged() {
    this.strip?.draw();
    this._askAppNames();
  }

  /* Il nome vero di un'app appesa lo sa l'elenco delle Jenny App, che all'avvio
     nessuno ha ancora letto: la fila scriveva lo slug, «todo» invece di «Todo»
     (visto sul telefono il 23/09/2026, nella modalita' ordina). Si chiede
     **solo quell'elenco**, che e' una lettura leggera, e non `ensureLoaded`:
     quella porta anche le app Android con le icone in base64, che il cassetto
     legge quando lo apri. Una volta sola, e solo se c'e' un'app appesa. */
  _askAppNames() {
    if (this._appNamesRequested) return;
    if (!this.homePages?.pages?.some((s) => s.kind === 'app')) return;
    this._appNamesRequested = true;
    const source = this.appsSource();
    if (source.jennyApps?.length) return;
    source.loadJennyApps?.().then(() => this.strip?.draw()).catch(() => {});
  }

  /* Le pagine su cui si scrive: la chat, e una pagina quaderno che la ospita. */
  _haComposer(entry) {
    return entry?.kind === 'chat' || entry?.kind === 'conversation';
  }

  /* Dove appoggia i piedi Jenny: sul composer dove c'e', al pavimento delle
     stanze dove non c'e'. Una pagina App o Impostazioni non ha un composer, e
     misurarlo lo stesso — sta nella pagina accanto, ancora alto quanto era —
     la terrebbe sospesa a mezz'aria sopra le righe. */
  _placeJenny() {
    if (this.view !== 'chat') return;
    if (this._haComposer(this._entry)) {
      this._measureFloor?.();
      return;
    }
    document.documentElement.style.setProperty('--home-composer-h', `${FLOOR_NO_COMPOSER}px`);
  }

  /** Il nome della pagina chat nella fila: «Jenny», o il quaderno che mostra,
   *  col suo pallino. E' la conversazione **della pagina chat**, non quella a
   *  schermo: su una pagina quaderno la chat e' in prestito, e il nome che la
   *  fila scrive sulla pagina chat resta quello a cui tornerai. */
  _chatName() {
    const notebook = projectNameOf(this.homePages?.homeConversation || sessionManager.currentKey);
    return notebook
      ? { name: notebook, color: dotColor(notebook) }
      : { name: this._personalName, color: null };
  }

  /** La modalita' ordina si apre o si chiude: la pagina sotto si spegne, e la
   *  tastiera non resta aperta su un campo che non si puo' piu' toccare. */
  _onSort(open) {
    this.shell?.toggleAttribute('data-sort', open);
    if (open) this.input?.blur();
    else this.focus?.restore();
  }

  /* Il campo dove scrivi e' a schermo e niente gli sta sopra: e' la domanda
     che `home-focus.js` fa prima di prendersi un tasto o un tocco. Oltre agli
     strati della casa, qualunque `<dialog>` aperto — anche quelli condivisi di
     conferma — e l'immagine ingrandita, che non sono strati del cassetto. */
  _composerActive() {
    if (this.view !== 'chat' || !this._haComposer(this._entry)) return false;
    if (this.homePages?.goneHere?.()) return false;
    if (this.hasOverlayAbove()) return false;
    return !document.querySelector('dialog[open], .image-lightbox');
  }

  /** C'e' qualcosa sopra il cassetto? Lo chiede lui prima di prendersi un
   *  tasto: da pagina, e' vivo mentre la guardi — anche sotto una scheda aperta
   *  sopra, o sotto una app che ha lanciato. */
  hasOverlayAbove() {
    for (const id of [...LONG_PRESS_SHEETS, REPORT_SHEET]) {
      if (document.getElementById(id)?.open) return true;
    }
    return Boolean(this._appActions?.isAppOpen()) || Boolean(this.strip?.sorting);
  }

  /* La stanza a schermo la dice un attributo su `.home-shell`, e il resto lo
     fa il CSS: cosi' la geometria — cosa occupa lo spazio, cosa sparisce —
     resta in un posto solo, e qui c'e' solo quel che il CSS non sa fare.

     Falso se il cambio e' stato **differito** dalla conferma del lettore:
     `then`, se c'e', e' il resto del gesto, e riparte solo alla risposta
     affermativa (v. `_confirmLeaveReader`). */
  _setView(name, then = null) {
    const view = Object.hasOwn(BACK_TO, name) ? name : 'chat';
    if (view === this.view) return true;
    /* Uscire dal lettore con modifiche non salvate chiede conferma, e la
       guardia sta **qui** e non sui bottoni. Le strade per uscire sono gia'
       quattro — l'occhiello, l'Indietro del telefono, «Parlane», un cambio di
       conversazione — e una guardia per strada e' una guardia che la quinta
       strada non avra'. E' la lezione di `_closeEditor` nel gestore file, dove
       il controllo sul buffer sporco valeva «solo se non esiste una seconda
       strada» e le strade erano tre. */
    if (this.view === 'reader' && this.reader?.isDirty()) {
      this._confirmLeaveReader(view, then);
      return false;
    }
    /* Editor aperto ma intonso: si chiude senza chiedere. Lasciarlo aperto
       vorrebbe dire ritrovarlo all'ingresso successivo, sopra una pagina che
       nel frattempo puo' essere un'altra. */
    if (this.view === 'reader') this.reader?.cancelEdit();
    if (this.view === 'chat') {
      /* Com'era quando hai lasciato la chat: al ritorno si rimette com'era, e
         non «fuori» d'ufficio. Metterla via era una tua decisione. */
      this._jennyWasOut = this.jenny.el.classList.contains('out');
    }
    this.view = view;
    this.shell?.setAttribute('data-view', view);
    /* Il polling dell'installazione non tiene sveglia una stanza che non c'e'
       piu'. Rientrando si riaggancia da se' (`open()`), perche' l'installazione
       intanto e' andata avanti per conto suo. */
    if (view !== 'updates') this.updatesRoom?.close();

    if (view === 'chat') {
      this.map?.stop();
      this._measureFloor?.();
      this.jenny.setOut(this._jennyWasOut);
      this._applyConversation();
      this.chat.keepBottom();
      this.focus?.restore();
    } else {
      /* Niente composer, quindi il pavimento va dichiarato: senza, l'osservatore
         misurerebbe un elemento nascosto e lo troverebbe alto zero. */
      document.documentElement.style.setProperty(
        '--home-composer-h', `${FLOOR_NO_COMPOSER}px`,
      );
      /* Fuori dalla chat Jenny sta al bordo: lo dice la tavola, che qui la
         disegna a `right:-56px` contro i `-30px` delle due della chat. */
      this.jenny.setOut(false);
      this.input?.blur();
    }
    this._applyHead();
    return true;
  }

  /* Il nome in testa: la conversazione nella chat, il quaderno nelle pagine,
     la pagina nel lettore. E' lo stesso `<h1>`, perche' e' sempre la risposta
     alla stessa domanda — dove sono. */
  _setHeadTitle(title) {
    if (this.nameEl && title) this.nameEl.textContent = title;
  }

  /* Quali comandi dell'intestazione valgono in questa stanza. Nella
     conversazione l'intestazione non c'e' — c'e' la fila — quindi qui si
     decide solo per le stanze; la pastiglia delle pagine del quaderno, che sta
     nella barra dove scrivi, vale invece ovunque la barra si veda. */
  _applyHead() {
    /* «Parlane» riporta a parlare **di questo quaderno**: vale dalle sue
       pagine e dal lettore, e in nessun altro posto — non nelle stanze delle
       impostazioni, dove non c'e' niente di cui parlare. */
    const inNotebook = this.view === 'pages' || this.view === 'reader';
    const notebook = projectNameOf(sessionManager.currentKey);
    if (this.talkBtn) this.talkBtn.hidden = !inNotebook;
    /* «Modifica» e' solo del lettore, e sparisce appena l'editor e' aperto: da
       li' i comandi sono Salva e Annulla, e stanno in basso. */
    if (this.editBtn) this.editBtn.hidden = this.view !== 'reader' || this.reader.editing;
    /* Uscendo dal lettore la selezione se ne va con la stanza, ma il
       `selectionchange` non e' garantito quando i nodi selezionati spariscono:
       la barra va chiusa qui, o resterebbe accesa sopra un'altra stanza. */
    this.audit?.refresh();
    if (this.pagesBtn) this.pagesBtn.hidden = !notebook;
    if (this.view === 'pages') this._setHeadTitle(notebook);
    if (this.view === 'jenny') this._setHeadTitle(i18n.t('home.jenny.title'));
    if (this.view === 'model') this._setHeadTitle(i18n.t('home.model.title'));
    if (this.view === 'updates') this._setHeadTitle(i18n.t('home.updates.title'));
    if (this.view === 'backup') this._setHeadTitle(i18n.t('home.backup.title'));
    this._applyBackLabel();
  }

  /* L'occhiello dice **dove si atterra**, non «indietro». Con tre stanze la
     differenza non si vedeva; con quattro, «torna alla chat» sopra il lettore
     era falso — di li' si torna alle pagine. La frase la sceglie la stessa
     tabella che decide il salto, quindi le due non possono divergere. */
  _applyBackLabel() {
    if (!this.backLabel) return;
    this.backLabel.textContent = i18n.t(`home.back.${BACK_TO[this.view] || 'chat'}`);
  }

  /* La mappa costa 280 kB di D3, quindi il suo modulo arriva col primo tocco
     sulla linguetta e non con l'avvio della casa. `import()` dinamico e non
     statico: e' la differenza fra pagarla chi la apre e pagarla tutti.

     **La promessa, non la mappa, e' quella che si ricorda**: due tocchi sulla
     linguetta prima che il modulo arrivasse trovavano tutti e due `this.map`
     vuota, e nascevano due `HomeMap` sullo stesso SVG — due simulazioni che
     si contendevano i nodi. Un import fallito si dimentica, cosi' il tocco
     dopo riprova. */
  async _drawMap(data, notebook) {
    this._mapReady ||= import('./home-map.js').then(({ HomeMap }) => {
      this.map = new HomeMap({
        onOpenPage: (path, label) => this.openPage(path, label),
      });
      return this.map;
    });
    let map;
    try {
      map = await this._mapReady;
    } catch (err) {
      this._mapReady = null;
      throw err;
    }
    await map.draw(data, notebook);
  }

  /* La conversazione e' cambiata: la fila la dice col nome e il pallino — lo
     stesso colore che ha la riga nei Quaderni, ed e' l'unica cosa che lega il
     tocco alla stanza in cui sei finito — e i Quaderni spostano la spunta. */
  _applyConversation() {
    const project = projectNameOf(sessionManager.currentKey);
    this._applyTranslations();
    this._applyHead();
    this._updatePagesCount(project);
    this.strip?.draw();
    this.who?.render();
    this._reportChatOnScreen();
  }

  /* Il numero sulla pastiglia. Arriva quando arriva — il conteggio sta nello
     stesso elenco dei Quaderni — e fino ad allora la pastiglia c'e' con la sua
     icona: un quaderno le pagine ce le ha comunque, e aspettare la cifra per
     mostrare la porta vorrebbe dire nascondere la porta.

     Il nome si ricontrolla al ritorno: fra la domanda e la risposta si puo'
     essere passati in un altro quaderno, e scrivere li' il conteggio di quello
     di prima sarebbe un numero sbagliato su una stanza giusta. */
  _updatePagesCount(notebook) {
    if (!this.pagesCount) return;
    this.pagesCount.textContent = '';
    if (!notebook) return;
    this.who.pagesOf(notebook).then((count) => {
      if (projectNameOf(sessionManager.currentKey) !== notebook) return;
      if (count === null) return;
      this.pagesCount.textContent = i18n.t(
        count === 1 ? 'home.notebookPages.countOne' : 'home.notebookPages.countMany', { count },
      );
    }).catch((err) => console.warn('casa.pages: page count not read', err));
  }

  /* Il turno che stava girando nella conversazione lasciata non si chiudera'
     mai qui dentro: il suo `turn_end` arrivera' e verra' scartato. Si chiude a
     mano tutto cio' che lo stava aspettando. */
  _releaseTurn() {
    this.activity.stop();
    this._setRunning(false);
  }

  /* ── Il contratto col guscio nativo ── */

  /** Chiamato da MainActivity.hideLoading a dissolvenza finita. */
  onNativeReady() {
    if (this._shellReady) return;
    this._shellReady = true;
    this._shellReadyCbs.splice(0).forEach((cb) => cb());
  }

  /** Esegue `cb` quando la pagina e' davvero visibile.
   *
   *  Fuori dal guscio nativo — in un browser normale, dove `JennyNative` non
   *  esiste — non arrivera' mai nessun onNativeReady: si parte al frame dopo.
   */
  whenShellReady(cb) {
    if (this._shellReady) return cb();
    this._shellReadyCbs.push(cb);
    if (!window.JennyNative) requestAnimationFrame(() => this.onNativeReady());
  }

  /** La sorgente dei dati del cassetto, costruita alla prima richiesta.
   *
   *  Il cassetto la chiede da se' (`_attachSource`) e non piu' di una volta.
   *  Pigra e non nel costruttore: sono due fetch, e quella delle app Android
   *  ricodifica ogni icona in base64 — farle al boot per un cassetto che
   *  potrebbe non aprirsi mai e' un costo che si paga sempre e serve a volte.
   */
  appsSource() {
    return (this._apps ||= new AppsSource());
  }

  /** Le azioni sulle voci. Il guscio le da' l'unica cosa che sa fare lui:
   *  mettere una richiesta nel composer e mandarla. */
  appsActions() {
    return (this._appActions ||= new AppsActions(this.appsSource(), {
      sendChatPrompt: (text) => this._sendInChat(text),
      homePages: () => this.pagesPort(),
    }));
  }

  /** Quel che le schede — dell'app, del quaderno — possono chiedere alle
   *  pagine, e nient'altro.
   *
   *  La scheda dell'app e' **condivisa con l'officina**, che le pagine non le
   *  ha: la riga «Metti come pagina» compare solo se il guscio le passa questa
   *  porta, e l'officina non gliela passa. Cosi' la scheda non deve sapere in
   *  che guscio vive.
   *
   *  Appendere **chiude tutto quel che c'e' sopra** prima di atterrare sulla
   *  pagina nuova: si appende da una scheda aperta sopra la pagina App o i
   *  Quaderni, e atterrare sotto la scheda vorrebbe dire non vedere di aver
   *  fatto niente.
   */
  pagesPort() {
    return (this._pagesPort ||= {
      state: (kind, ref) => {
        if (this.homePages.pending(kind, ref)) return 'pending';
        return this.homePages.full ? 'piena' : 'free';
      },
      append: async (kind, ref) => {
        this._closeAllOverlays();
        return this.homePages.append(kind, ref);
      },
      detach: (kind, ref) => this.homePages.detach(kind, ref),
      reload: () => this.homePages.reload(),
    });
  }

  /** Porta una richiesta gia' scritta dentro la conversazione e la manda.
   *  Serve a «modifica questa app», che non apre un editor: chiede a Jenny. */
  _sendInChat(text) {
    this.goHome();
    if (!this.input) return;
    this.input.value = text;
    this.input.dispatchEvent(new Event('input', { bubbles: true }));
    this.input.focus();
  }

  /** Il cassetto delle app: dal 23/09/2026 e' la pagina App, e ci si va.
   *  Stesso nome del metodo dell'officina, perche' chi chiama e' lo stesso
   *  codice. */
  openLauncher() {
    this._setView('chat');
    this.homePages?.goToId('app');
  }

  /** Il nome che l'elenco delle app da' a uno slug, se l'elenco e' gia' stato
   *  letto: la fila lo scrive sopra la pagina di un'app appesa. */
  appName(slug) {
    return this._apps?.jennyApps?.find?.((a) => a.slug === slug)?.name || null;
  }

  /** Il tasto Home di Android, quando Jenny e' il launcher.
   *
   *  In officina Home smonta cinque livelli di overlay e collassa il
   *  sotto-stato di ogni sezione. Qui Home vuol dire una cosa sola: **sei a
   *  casa**. Si chiude quel che sta sopra, si torna nella conversazione
   *  personale — se eri dentro un quaderno, quella e' la casa da cui il tasto
   *  prende il nome — si chiude la tastiera a schermo (con quella fisica il
   *  campo tiene il fuoco) e si torna in fondo al filo, che e' il presente
   *  della conversazione.
   */
  goHome() {
    this._closeAllOverlays();
    /* Con l'editor modificato `_setView` chiede, e il resto di Home aspetta la
       risposta: cambiare conversazione sotto la conferma lasciava, con un no,
       il lettore di un quaderno sopra la chat personale. */
    if (!this._setView('chat', () => this.goHome())) return;
    this.switchConversation(null);
    /* Con la tastiera fisica non c'e' niente da chiudere, e a casa si torna
       per scrivere: il fuoco resta sul campo. */
    if (!this.focus?.restore()) this.input?.blur();
    this.chat.scrollToBottom();
  }

  /** Il tasto Indietro di Android.
   *
   *  In officina e' una catena di cinque livelli di overlay piu' lo stack di
   *  navigazione. In casa gli strati sono pochi — una scheda, un'app aperta,
   *  la modalita' ordina, l'immagine ingrandita — poi le stanze, poi le
   *  pagine, che tornano alla chat; e sotto la chat c'e' una cosa sola da cui
   *  si puo' tornare: un quaderno. Indietro allora e' la porta di casa, cioe'
   *  la conversazione personale.
   *
   *  Nella conversazione personale, senza niente sopra, **non si fa niente**, e
   *  non e' una dimenticanza: questa app e' il launcher del telefono, e
   *  Indietro non deve mai chiudere il task.
   *
   *  Una pressione, una cosa sola: chiudere una scheda *e* uscire dal quaderno
   *  con lo stesso tasto farebbe sparire due cose per un gesto.
   */
  handleHardwareBack() {
    if (this._closeOverlays()) return;
    /* Poi le stanze, una per pressione: lettore, pagine, chat. Solo quando la
       casa e' tornata alla conversazione Indietro vale come «esci dal
       quaderno» — altrimenti dalle pagine un tocco solo farebbe sparire due
       cose, la stanza e la stanza che la conteneva. */
    if (this.goBackOneRoom()) return;
    if (projectNameOf(sessionManager.currentKey)) this.switchConversation(null);
  }

  /** Chiude cio' che sta sopra le pagine. Vero se c'era qualcosa. Uno strato
   *  per pressione, dall'alto: l'ordine qui sotto e' quello in cui stanno a
   *  schermo, non una scelta fra candidati.
   */
  _closeOverlays() {
    /* Prima di tutto, i fogli che si aprono con una pressione lunga: quello di
       un quaderno **dai** Quaderni, e i due delle app **dal** cassetto (Open,
       Edit, Delete). Sono `<dialog>` con `showModal()`: stanno nel top layer,
       sopra tutto, e il loro commento in `apps-actions.js` lo dice — Indietro
       chiude prima loro. Visto sul telefono il 23/09/2026, quando mancavano:
       Indietro chiudeva il cassetto sotto e lasciava il foglio aperto sopra
       la chat, Delete compreso. */
    for (const id of LONG_PRESS_SHEETS) {
      const sheet = document.getElementById(id);
      if (sheet?.open) {
        sheet.close();
        return true;
      }
    }
    /* Poi un'app aperta a tutto schermo dal cassetto. Fino al 23/09/2026 la
       casa non la chiudeva mai: Indietro agiva su quel che c'era **sotto**, e
       l'app restava li'. Con la pagina App diventata la strada principale per
       aprirle, e' il livello che si incontra piu' spesso. `handleBack` e' dell'
       app: una sua schermata interna torna indietro dentro di lei, prima. */
    if (this._appActions?.handleBack()) return true;
    /* La modalita' ordina: Indietro esce **senza salvare**. Salvare e' «Fatto». */
    if (this.strip?.sorting) {
      this.strip.closeSort();
      return true;
    }
    /* Il cassetto non e' piu' uno strato, e' una pagina. Ma la ricerca scritta
       li' dentro lo e': Indietro prima la svuota, poi — alla pressione dopo —
       riporta alla chat. Una pressione, un cambiamento visibile. */
    if (this._entry?.kind === 'drawer' && this.launcher?.search?.value) {
      this.launcher.dismiss();
      return true;
    }
    /* Il foglio di «Segnala»: `showModal()`, quindi top layer come le schede.
       Un `<dialog>` modale si chiude da se' con Escape, ma qui Indietro arriva
       dal guscio nativo come un evento suo e nessuno lo traduce in Escape:
       senza questa riga la pressione uscirebbe dalla *stanza* lasciando il
       foglio aperto sopra un'altra. */
    const sheet = document.getElementById(REPORT_SHEET);
    if (sheet?.open) {
      sheet.close();
      return true;
    }
    const lightbox = document.querySelector('.image-lightbox');
    if (lightbox) {
      if (typeof lightbox.__jennyClose === 'function') lightbox.__jennyClose();
      else lightbox.remove();
      return true;
    }
    return false;
  }

  /** Chiude **tutto** quel che sta sopra le pagine, non uno strato.
   *
   *  Home e un avviso toccato sono un indirizzo, non un passo indietro — e lo
   *  stesso vale per chi appende una pagina da una scheda aperta. Passare da
   *  `_closeOverlays` una volta sola chiudeva uno strato con la logica di
   *  Indietro: un'app con schermate interne (`depth > 1`) risponde tornando
   *  indietro **dentro di se'**, e restava aperta sotto la chat — che intanto
   *  `isChatOnScreen` dava per vista, cancellando avvisi mai letti.
   *
   *  L'app si chiude per prima e davvero (`closeApp`); poi gli altri strati,
   *  uno per giro e con un tetto: `_closeOverlays` torna vero anche quando
   *  delega la chiusura (la lightbox), e un ciclo senza fine qui sarebbe la
   *  casa bloccata su un tocco. */
  _closeAllOverlays() {
    this._appActions?.closeApp();
    for (let i = 0; i < 8 && this._closeOverlays(); i += 1) { /* avanti */ }
  }

  /** Il tocco su un avviso proattivo: porta *in chat*.
   *
   *  **Nella conversazione personale**, e non in quella che stavi guardando:
   *  la copia websocket di un avviso proattivo va sempre li' (il fan-out di
   *  `runtime/delivery.py` ce la mette d'ufficio), quindi dentro un quaderno
   *  quell'avviso non c'e' — e portarti "in chat" lasciandoti dove sei
   *  vorrebbe dire aprire la stanza sbagliata per una notifica che hai appena
   *  toccato.
   *
   *  Qui gli strati si chiudono **e** si torna a casa: non e' un tasto
   *  Indietro, e' un indirizzo.
   */
  openChat() {
    this._closeAllOverlays();
    if (!this._setView('chat', () => this.openChat())) return true;
    this.switchConversation(null);
    this.chat.scrollToBottom();
    return true;
  }

  /** La chat dove arrivano gli avvisi e' a schermo? Lo chiede il guscio nativo
   *  al rientro in primo piano (`CHAT_ON_SCREEN_JS`), per cancellare gli avvisi
   *  gia' letti — un launcher torna in primo piano a ogni pressione di Home, e
   *  quel ritorno da solo non dice cosa stai guardando.
   *
   *  La pagina chat **con la conversazione personale**: e' li' che la copia
   *  websocket di un avviso proattivo arriva sempre (v. `openChat`). Una pagina
   *  quaderno, o la pagina chat su un quaderno scelto dai Quaderni, l'avviso
   *  non lo mostra. Al boot `_entry` e' ancora nullo e la risposta e' no: nel
   *  dubbio un avviso resta, che e' la direzione d'errore giusta.
   */
  isChatOnScreen() {
    return this.view === 'chat'
      && this._entry?.kind === 'chat'
      && sessionManager.currentKey === sessionManager.personalKey
      /* Un'app aperta, una scheda o la modalita' ordina la coprono: sotto
         c'e', ma non la stai guardando. */
      && !this.hasOverlayAbove();
  }

  /* Il secondo dei tre modi in cui la chat arriva a schermo (v.
     `NotifierBridge.clearAlerts`): un cambio di pagina o di conversazione dentro
     la WebView, che il guscio nativo non puo' vedere da se'. Si chiama su ogni
     occasione e decide qui; l'officina lo fa da `ChatController.activate`. */
  _reportChatOnScreen() {
    if (!this.isChatOnScreen()) return;
    try { window.JennyNative?.chatOpened?.(); } catch { /* nessun guscio nativo */ }
  }

  /** Una app Android e' stata installata o rimossa.
   *
   *  La pagina App lo deve sapere: Jenny e' il launcher, e un'app appena presa
   *  dal Play Store che non compare li' fino al riavvio della casa e' un'app
   *  che non si trova. Il lavoro lo fa la sorgente condivisa con l'officina
   *  (`AppsSource.onPackageChanged`: toglie subito una rimossa, rilegge
   *  l'elenco). Solo se esiste gia': se la pagina App non si e' mai accesa
   *  l'elenco non e' mai stato letto, e quando lo sara' sara' gia' fresco —
   *  costruirla qui vorrebbe dire pagare le icone per un cassetto chiuso.
   *
   *  Il metodo deve esistere comunque: il guscio nativo lo chiama senza
   *  guardare, e un `undefined` sarebbe un TypeError dentro la sua
   *  `evaluateJavascript`.
   */
  onPackageChanged(kind, packageName) {
    this._apps?.onPackageChanged?.(kind, packageName);
  }

  /* ── Il composer ── */

  _bindComposer() {
    this.attach.addEventListener('click', () => this.files.trigger());

    this.pending.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove]');
      if (btn) this.files.remove(Number(btn.dataset.remove));
    });

    this.send.addEventListener('click', () => {
      if (this._running) this._stop();
      else this._send();
    });

    this.input.addEventListener('keydown', (e) => {
      /* Il Titan ha una tastiera fisica: invio manda, shift-invio va a capo.
         `isComposing` e' il metodo di input in mezzo a una composizione (accenti,
         IME): quell'invio chiude la parola, non spedisce il messaggio. */
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        this._send();
      }
    });

    this.input.addEventListener('input', () => this._autosize());
    this._autosize();

    /* Jenny sta sopra il composer, e il composer cresce col testo. Un `bottom`
       fisso la farebbe finire dentro il campo al terzo capoverso: l'altezza
       vera si misura e si scrive in un token, cosi' la geometria resta nel CSS
       e qui c'e' solo il numero. */
    const measure = () => {
      /* Solo nella chat. Fuori di li' il composer e' `display:none`, quindi il
         suo `offsetHeight` e' zero: misurarlo scriverebbe un pavimento a filo
         del bordo, e Jenny finirebbe mezza fuori schermo mentre leggi le
         pagine. Chi non ha un composer il suo pavimento se lo dichiara
         (v. `_setView`). */
      if (this.view !== 'chat') return;
      /* ...e solo sulle pagine che un composer ce l'hanno. Le altre il loro
         pavimento lo dichiarano (v. `_placeJenny`). */
      if (this._entry && !this._haComposer(this._entry)) return;
      /* Il riferimento, non una ricerca per classe: le foto del trasloco sono
         copie della chat col loro composer, e una che sta prima nel documento
         veniva misurata al posto di quello vero. */
      const h = this.composer?.offsetHeight || 64;
      document.documentElement.style.setProperty('--home-composer-h', `${h}px`);
    };
    /* Serve anche a chi rientra nella chat da un'altra stanza: li' il composer
       torna visibile e la sua altezza va rimisurata, o Jenny resta appoggiata
       al pavimento delle pagine. */
    this._measureFloor = measure;
    measure();
    /* **Tutto quel che sta sotto il filo, non solo il composer.** Il filo è
       `flex: 1`: ogni riga che compare là sotto — gli allegati in attesa, la
       riga di lavoro, lo stato del collegamento — gliela toglie, e il suo fondo
       scivola sotto il bordo. Visto sul telefono allegando due video: la riga
       che spiegava il rifiuto finiva fuori schermo *proprio* nel momento in cui
       serviva leggerla. Vale anche per l'ultimo messaggio quando alleghi una
       foto, e c'era da sempre.

       `keepBottom` riaggancia solo se ci si era: chi sta rileggendo più su non
       viene strappato via. */
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => {
        measure();
        this.chat.keepBottom();
      });
      for (const el of [this.composer, this.pending, this.activityEl, this.wire]) {
        if (el) observer.observe(el);
      }
    }
  }

  /* Il campo cresce col testo fino al tetto del CSS, poi scorre. Si azzera
     l'altezza prima di leggere `scrollHeight`, o il campo non torna mai piu'
     piccolo dopo essere cresciuto. */
  _autosize() {
    this.input.style.height = 'auto';
    this.input.style.height = `${this.input.scrollHeight}px`;
  }

  /* Gli allegati in attesa: miniature per le immagini, una pastiglia col nome
     per tutto il resto. Ognuno si toglie da solo con la sua X — senza, l'unico
     modo di disfare uno sbaglio sarebbe mandare il messaggio. */
  _renderPending() {
    const entries = this.files.getAttachmentEntries();
    this.pending.innerHTML = '';
    this.pending.hidden = !entries.length;
    entries.forEach((entry, i) => {
      const item = document.createElement('div');
      item.className = 'home-pending-item';
      if (entry.kind === 'image') {
        const img = document.createElement('img');
        img.src = entry.url;
        img.alt = entry.name || '';
        item.appendChild(img);
      } else {
        const name = document.createElement('span');
        name.className = 'home-pending-name';
        name.textContent = entry.name || '';
        item.appendChild(name);
      }
      const x = document.createElement('button');
      x.type = 'button';
      x.dataset.remove = String(i);
      x.className = 'home-pending-x';
      x.setAttribute('aria-label', i18n.t('home.removeAttachment'));
      x.innerHTML = '<i class="ti ti-x"></i>';
      item.appendChild(x);
      this.pending.appendChild(item);
    });
  }

  /* Vero se il messaggio e' partito: `_bringToChat` lo chiede per sapere
     cosa lasciare nella casella. */
  _send() {
    const text = this.input.value.trim();
    /* Una foto senza didascalia e' un messaggio: la bolla e' l'immagine. E' la
       stessa regola che il gateway applica all'eco di Telegram — col solo
       controllo sul testo, una foto muta non partirebbe. */
    if (!text && !this.files.count) return false;
    /* La pagina di un quaderno cancellato: l'ultima guardia, se un Invio
       arriva comunque al campo sotto l'avviso. */
    if (this.homePages?.goneHere?.()) return false;
    /* Scritto a mano, `/stop` resta il comando che e' — in casa i comandi non
       ci sono, ma niente impedisce di digitarne uno, e disegnarne la bolla
       vorrebbe dire mostrare in chat una cosa che il transcript esclude
       apposta dall'eco. */
    if (text === '/stop') {
      this._stop();
      this.input.value = '';
      this._autosize();
      return true;
    }
    const chatId = sessionManager.currentChatId;
    const media = this.files.getImages();
    const entries = this.files.getAttachmentEntries();
    if (!wsManager.sendToChat(chatId, text, media)) {
      /* Socket chiuso: il messaggio non e' partito e non va disegnato. Una
         bolla che compare e un messaggio che non arriva sono la stessa cosa
         vista da due parti, e la prima fa credere alla seconda. */
      this._setWire(false);
      return false;
    }
    /* La bolla la disegna il client: il gateway rimanda l'eco solo dei messaggi
       entrati da *altri* canali (v. webui_turns._handle_session_turn_started,
       che per il canale websocket esce subito). */
    this.chat.appendOwn(text, entries);
    this.files.clear();
    this.input.value = '';
    this._autosize();
    return true;
  }

  /* Fermare un turno e' `/stop`, come in officina: non esiste un frame apposta,
     e il gateway lo riconosce come comando. Non diventa una bolla — ne' qui ne'
     nel transcript, che lo esclude esplicitamente dall'eco. */
  _stop() {
    wsManager.sendToChat(sessionManager.currentChatId, '/stop');
  }

  /* ── La riga di lavoro ── */

  /* Gli stessi frame che la chat butta via, qui diventano una parola sola.
     Nessuno di questi arriva per la riga: arrivano perche' il canale websocket
     li manda comunque, e in officina disegnano pannelli.

     Jenny no: legge i frame da se' (`shared/jenny-mascot.js`), con le stesse
     regole dell'officina, e qui non la si pilota piu' a mano. */
  _readActivity(msg) {
    if (!msg || !this._frameIsHere(msg)) return;
    if (msg.turn_id) this.activity.turnId = msg.turn_id;
    switch (msg.event) {
      case 'goal_status':
        if (msg.status === 'running') this.activity.start(msg.turn_id);
        else this.activity.stop();
        break;
      case 'reasoning_delta':
        this.activity.reasoning();
        break;
      case 'message':
        // Un `tool_hint` porta i nomi degli strumenti che stanno partendo.
        if (msg.tool_events) this.activity.tools(msg.tool_events);
        break;
      case 'delta':
        // La risposta sta arrivando: la riga si toglie di mezzo.
        this.activity.answering();
        break;
      case 'turn_end':
        this.activity.stop();
        break;
      default:
        break;
    }
  }

  /* Il tocco lungo sulla riga: lo stesso turno, in officina, con tutto quello
     che la casa non mostra. `api.navigate` e non `location.href` perche' il
     segreto di bootstrap vive solo nella memoria di questa pagina: una
     navigazione secca lo perderebbe e l'officina prenderebbe 401.

     Il frammento `#turn=` resta nell'URL dopo che il segreto e' stato consumato
     e tolto: l'officina oggi non lo legge ancora, e non fa danno — quando lo
     leggera', da questa parte non c'e' niente da cambiare.

     **Da dentro un quaderno questa porta apre l'officina sulla conversazione
     personale**, ed e' un buco noto, non una svista: nessuno dei due gusci
     ricorda la chiave aperta (nessun `localStorage`), quindi l'officina riparte
     sempre da `websocket:default`. Chiuderlo vuol dire passarle la chiave nel
     frammento e insegnarle a leggerla — lavoro nell'altro guscio, che non legge
     ancora nemmeno il `#turn=` che gli mandiamo da mesi. Il chip dell'officina
     dice comunque a voce alta dove sei finito, che e' il motivo per cui questo
     buco costa poco. */
  _openInWorkshop(turnId) {
    const target = turnId ? `/html-mobile/workshop.html#turn=${encodeURIComponent(turnId)}`
                          : '/html-mobile/workshop.html';
    api.navigate(target);
  }

  /* `goal_status` dice se un turno sta girando: e' quel che trasforma il
     bottone da "manda" a "ferma". */
  _readRunStatus(msg) {
    if (msg?.event !== 'goal_status' || !this._frameIsHere(msg)) return;
    this._setRunning(msg.status === 'running');
  }

  /* Il frame e' della conversazione a schermo? La stessa regola della chat
     (`HomeChat._belongsHere`): un frame senza `chat_id` e' di tutti, uno con
     un `chat_id` diverso e' di un'altra conversazione. Senza, un turno che
     gira in un quaderno accendeva il bottone Ferma sulla chat personale ferma
     — e Ferma avrebbe mandato `/stop` alla conversazione sbagliata — o lo
     spegneva sul turno vivo di questa. */
  _frameIsHere(msg) {
    const chatId = msg?.chat_id;
    return !chatId || chatId === sessionManager.currentChatId;
  }

  _setRunning(running) {
    if (this._running === running) return;
    this._running = running;
    this.send.classList.toggle('is-stop', running);
    this.send.innerHTML = running
      ? '<i class="ti ti-player-stop-filled"></i>'
      : '<i class="ti ti-arrow-up"></i>';
    this.send.setAttribute('aria-label', i18n.t(running ? 'home.stop' : 'home.send'));
  }

  /* ── Lo stato del filo ── */

  /** Vero quando il socket e' aperto. Una caduta breve non si annuncia. */
  _setWire(connected) {
    clearTimeout(this._wireTimer);
    if (connected) {
      this.wire.hidden = true;
      return;
    }
    this._wireTimer = setTimeout(() => {
      this.wire.textContent = i18n.t('home.wire.offline');
      this.wire.hidden = false;
    }, WIRE_GRACE_MS);
  }

  _showEmpty(visible) {
    if (this.empty) this.empty.hidden = !visible;
  }

  _applyTranslations() {
    /* Tre frasi cambiano con la conversazione, e cambiano insieme: dentro un
       quaderno il vuoto non dice «comincia tu» ma che quella conversazione non
       c'e' ancora *e resta li'*, che e' l'unico punto in cui si puo' dire senza
       spiegarlo che questa e' un'altra stanza. */
    const inNotebook = !!projectNameOf(sessionManager.currentKey);
    if (this.emptyText) {
      const empty = inNotebook ? 'home.emptyNotebook' : 'home.empty';
      this.emptyText.textContent = i18n.t(this._threadFailed ? 'home.threadError' : empty);
    }
    if (this.input) {
      this.input.placeholder = i18n.t(inNotebook ? 'home.placeholderNotebook' : 'home.placeholder');
    }
    if (this.send) {
      this.send.setAttribute('aria-label', i18n.t(this._running ? 'home.stop' : 'home.send'));
    }
    if (this.attach) this.attach.setAttribute('aria-label', i18n.t('home.attach'));
    this._applyBackLabel();
    if (this.talkLabel) this.talkLabel.textContent = i18n.t('home.notebookPages.talk');
    if (this.editBtn) this.editBtn.setAttribute('aria-label', i18n.t('home.reader.edit'));
    this.reader?.applyTranslations();
    this.audit?.applyTranslations();
    this.you?.applyTranslations();
    this.modelRoom?.applyTranslations();
    this.updatesRoom?.applyTranslations();
    this.backupRoom?.applyTranslations();
    this.jennyRoom?.applyTranslations();
    this.you?.sayJenny(this.jennyRoom?.value());
    if (this.pagesBtn) this.pagesBtn.setAttribute('aria-label', i18n.t('home.notebookPages.open'));
    this.newNotebook?.setAttribute('aria-label', i18n.t('home.who.newNotebook'));
    this.pages?.applyTranslations();
    // La pagina Quaderni ha le sue righe gia' disegnate: vanno riscritte.
    this.who?.render();
    this.strip?.draw();
    if (this.files?.count) this._renderPending();
    if (this.wire && !this.wire.hidden) this.wire.textContent = i18n.t('home.wire.offline');
  }

}

new HomeApp();
