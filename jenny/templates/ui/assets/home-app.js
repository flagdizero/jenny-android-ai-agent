/** La casa — guscio.
 *
 *  La seconda interfaccia di Jenny, quella il cui unico mestiere e' la
 *  conversazione. L'officina (`mobile-app.js`) resta intera e resta dov'e': i
 *  due gusci non si vedono fra loro, non condividono un nodo del DOM e non si
 *  possono rompere a vicenda. Condividono le fondamenta — `assets/shared/*` —
 *  e nient'altro.
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
 *  personale arriva a schermo e `JennyNative.chatClosed()` quando ne esce
 *  (v. `_reportChatOnScreen`): e' quel che decide se un avviso squilla.
 *
 *  Il nome globale resta `mobileApp` apposta: cosi' il guscio nativo non sa, e
 *  non deve sapere, quale delle due interfacce ha caricato.
 */

import { ActivityLine } from './home-activity.js';
import { WorkBubbles } from './home-work.js';
import { HomeChat } from './home-chat.js';
import { HomeBoard, HomeBoardSheet } from './home-board.js';
import { BoardDrawer } from './home-board-drawer.js';
import { HomeReader } from './home-reader.js';
import { HomeAudit, reportMessage } from './home-audit.js';
import { HomeJenny } from './home-jenny.js';
import { HomeModel } from './home-model.js';
import { HomeUpdates } from './home-updates.js';
import { HomeBackup } from './home-backup.js';
import { HomeYou } from './home-you.js';
import { WhoPanel } from './home-who.js';
import { dotColor } from './shared/spines.js';
/* Il cassetto delle app, **preso dall'officina e non ricopiato**: e' lo stesso
   modulo per i due gusci. I dati e le azioni stanno in `shared/`, fuori da
   qualunque schermata — ci sono usciti il 21/09/2026, quando la scheda «App»
   che li ospitava e' stata cancellata. */
import { HomeBand } from './home-band.js';
import { JennyMascot } from './shared/jenny-mascot.js';
import { LauncherController } from './mobile-launcher.js';
import { HomePages } from './home-pages.js';
import { HomeStrip } from './home-strip.js';
import { HomeUiQuery } from './home-ui-query.js';
import { ComposerFocus } from './home-focus.js';
import { NotebookCard } from './home-notebook.js';
import { ChatMove } from './home-move.js';
import { ChatOver } from './home-over.js';
import { UnseenReplies } from './shared/unseen-replies.js';
import { AppsSource } from './shared/apps-source.js';
import { AppsActions } from './shared/apps-actions.js';
import { isOpenableProjectName, projectKey, projectNameOf } from './shared/conversation-list.js';
import { createProjectFlow } from './shared/project-create.js';
import { deleteProjectFlow } from './shared/project-delete.js';
import { readStorage, showToast, writeStorage } from './shared/utils.js';
import { api } from './shared/api-client.js';
import { isFirstRun } from './shared/first-run.js';
import { ImageHandler } from './shared/image-handler.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';
import { botName } from './shared/bot-name.js';
import { rpc } from './shared/rpc-client.js';
import { clearSelection, releaseSelectionOnBlur } from './shared/selection.js';
import { i18n } from './shared/i18n.js';
import { HomeAppsTrash } from './home-apps-trash.js';
import { HomeStaged } from './home-staged.js';
import { sessionManager } from './shared/session-manager.js';
import { chatIdOf, wsManager } from './shared/ws-manager.js';
import './shared/theme.js';

/* Quanto aspettare prima di dire che il filo e' interrotto. Il socket si riapre
   da se' e la maggior parte delle cadute dura meno di un battito: annunciarle
   tutte vorrebbe dire far lampeggiare una riga d'allarme mentre non e' successo
   niente. Si parla solo se il silenzio dura. */
/* Lo stesso ripiego del server e di `shared/bot-name.js`. */
const DEFAULT_BOT_NAME = 'Jenny';
const WIRE_GRACE_MS = 2_500;

/* Dove appoggia i piedi Jenny fuori dalla chat, in px dal fondo. Nella chat il
   suo pavimento e' il composer e si misura (v. `_bindComposer`); nelle pagine
   il composer non c'e'. L'altezza e' quella che aveva quando sopra i suoi piedi
   c'era il campo della minichat (dall'utente, 28/09/2026; la minichat non c'e'
   piu' dal 05/10/2026, il posto le e' rimasto). E' lo stesso numero di
   `--jenny-away-floor` in mobile-style.css, che i fondi delle stanze leggono
   per lasciarle posto. */
const FLOOR_NO_COMPOSER = 66;

/* Dove eri nella casa, per ritrovarlo dopo un riavvio della WebView (v.
   `_savePlace`). Per-telefono e di comodo: `localStorage` basta, e se manca
   si riparte dalla chat come sempre. */
const PLACE_KEY = 'home-place';
/* Oltre, il posto e' di un altro momento: si riparte dalla chat. */
const PLACE_MAX_AGE_MS = 6 * 60 * 60 * 1000;

/* Il nome di lei finche' le impostazioni non hanno detto il suo: lo stesso
   ripiego del server (`settings_api`, `bot_name or "Jenny"`). E' un nome
   proprio, non una frase da tradurre. */

/* Le stanze oltre la conversazione, e dove si atterra premendo Indietro una
   volta. La catena e' lineare e sta **in un posto solo**: `_setView` la usa per
   sapere quali nomi esistono, `goBackOneRoom` per percorrerla e la freccia
   per dire, a chi non la vede, dove porta. Aggiungere una stanza e' una riga qui.

   `settings` non e' una stanza: e' la **pagina** Impostazioni, dal
   23/09/2026, e le stanze che si aprono da li' ci tornano sopra — la vista
   torna `chat`, e la pista va su quella pagina (v. `goBackOneRoom`). */
const BACK_TO = {
  /* La bacheca del quaderno, il cassetto di una scheda, e una pagina. Il
     lettore torna al cassetto da cui l'hai aperta (profondita' massima uno: da
     un cassetto apri una pagina, e «indietro» porta sempre al cassetto); aperta
     dalla ricerca senza un cassetto, torna alla bacheca. Quale dei due lo dice
     `_backTarget`, questa tabella dice il caso comune. */
  board: 'chat',
  drawer: 'board',
  reader: 'drawer',
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
const LONG_PRESS_SHEETS = ['home-notebook-sheet', 'jenny-app-sheet', 'android-app-sheet', 'apps-trash-sheet'];
/* Il foglio della bacheca: «dall'ultima volta», la busta, la ricerca,
   l'anteprima di una pagina, «aggiungi sezione». */
const BOARD_SHEET = 'home-board-sheet';

/* Le stanze di un quaderno: da qui Jenny apre la chat **del quaderno**, da
   ogni altro posto quella personale (v. `_summonKey`). */
const NOTEBOOK_ROOMS = ['board', 'drawer', 'reader'];
const REPORT_SHEET = 'home-audit-dialog';

/* I dialoghi condivisi con l'officina (`shared/dialog.js`): «Elimina
   quaderno?», «Nuovo nome per …», il dettaglio. Si aprono *da* una scheda o da
   una pagina, quindi stanno sopra a tutto il resto, e Indietro li chiude per
   primi. */
const SHARED_DIALOGS = [
  'oc-confirm-dialog', 'oc-prompt-dialog', 'oc-detail-dialog',
  /* Le due finestre di `shared/backup-flow.js`, che si costruiscono al volo
     invece di stare nel DOM: la passphrase e il riavvio dopo un ripristino.
     Mancavano, e nella stanza Backup Indietro passava sotto la passphrase —
     via la stanza, la domanda restava aperta sopra Impostazioni (visto
     sull'emulatore il 29/09/2026). Il riavvio rifiuta `cancel`, quindi la
     pressione e' consumata senza chiuderlo: e' quel che deve fare. */
  'oc-backup-passphrase-dialog', 'oc-backup-restart-dialog',
];

class HomeApp {
  constructor() {
    this.thread = document.getElementById('home-thread');
    this.chat = new HomeChat(this.thread);
    /* Jenny e' la porta della chat (dal 05/10/2026): dove la chat non c'e' la
       apre, sopra la pagina; dove e' aperta sopra la pagina la chiude; nella
       pagina chat saluta. Prima apriva la minichat, una domanda a un turno. */
    this.jenny = new JennyMascot(document.querySelector('.home-shell'), {
      chat: {
        isOpen: () => this.chatOver,
        onScreen: () => !this.chatOver && this._jennyInChat,
        open: () => this.openChatOver(),
        close: () => this.closeChatOver(),
      },
    });
    /* Il velo in cui sale la chat. Un tocco sul suo fondo non lo chiude: Jenny
       sobbalza, perche' e' lei che lo chiude. */
    this.over = new ChatOver({
      shell: document.querySelector('.home-shell'),
      host: document.getElementById('home-over'),
      onVeilTap: () => this.jenny.nudge(),
    });
    /* Il pallino su Jenny: una risposta arrivata mentre la sua chat non era a
       schermo, nella conversazione che lei aprirebbe da qui. */
    this.unseen = new UnseenReplies({
      onScreen: (id) => id === sessionManager.currentChatId
        && (this.chatOver || (this.view === 'chat' && (this._entry?.kind ?? 'chat') === 'chat')),
      wanted: () => chatIdOf(this._summonKey()),
      paint: (on) => this.jenny.setBadge(on, i18n.t('jenny.replyWaiting')),
    });
    /* La sagoma di Jenny sopra la fascia in fondo al filo (`home-band.js`). Si
       consegna alla chat dopo la mascotte perche' le serve il suo nodo vero:
       la sua taglia si legge da lei, non da un numero copiato. */
    this.chat.band = new HomeBand(this.chat.bandEl, this.jenny.el);
    /* **Chi lavora, in fondo al filo** (05/10/2026, `home-work.js`). Jenny al
       lavoro e' l'ultima riga del posto vivo; sopra di lei le bolle del lavoro
       lungo (giardiniere, Dream) e degli aiutanti, che durano oltre la fine del
       turno. Dal 13/10/2026 la sua non e' piu' una bolla ma un divisore
       (`home-activity.js`): come bolla si leggeva come un messaggio. */
    const typing = document.createElement('div');
    typing.className = 'home-typing';
    typing.hidden = true;
    this.chat.live.appendChild(typing);
    this.activity = new ActivityLine(typing, {
      onOpenInWorkshop: (turnId) => this._openInWorkshop(turnId, sessionManager.currentKey),
      onChange: () => this.chat.liveChanged(),
    });
    this.work = new WorkBubbles(this.chat.live, {
      chat: this.chat,
      currentKey: () => sessionManager.currentKey,
      fetchWork: (sessionKey) => api.getWork({ sessionKey }),
      fetchSubagents: (sessionKey) => api.getSubagents({ sessionKey }),
      restartSubagent: (taskId, sessionKey) => api.restartSubagent(taskId, { sessionKey }),
      sendCommand: (text) => this._sendText(text),
      openBoard: () => {
        this.closeChatOver();
        this.openBoard();
      },
      onOpenInWorkshop: () => this._openInWorkshop(null, sessionManager.currentKey),
    });
    /* Le bozze di un subagent che ha letto qualcosa da fuori: una scheda nel
       posto live, con Installa / Scarta (v. `home-staged.js`). */
    this.staged = new HomeStaged(this.chat.live, { onChange: () => this.chat.liveChanged() });
    this.chat.claimsWork = (workId) => this.work.claims(workId);
    this.empty = document.getElementById('home-empty');
    this.emptyText = document.getElementById('home-empty-text');
    this.wire = document.getElementById('home-wire');
    this.input = document.getElementById('home-input');
    this.overTitle = document.getElementById('home-over-title');
    this.overName = document.getElementById('home-over-name');
    this.overDot = document.getElementById('home-over-dot');
    this.send = document.getElementById('home-send');
    this.attach = document.getElementById('home-attach');
    this.pending = document.getElementById('home-pending');
    /* Il composer vero, preso adesso per riferimento: piu' tardi il trasloco
       mette nelle pagine delle foto della chat, e un `querySelector` per
       classe puo' rispondere con il composer di una foto (v. `_bindComposer`). */
    this.composer = this.input?.closest?.('.home-composer') || null;
    this.shell = document.querySelector('.home-shell');

    this.backBtn = document.getElementById('home-back');
    /* Il percorso della riga: la radice (di che posto e' la stanza) e i
       pallini del quaderno, quando la stanza e' sua. */
    this.headEl = document.querySelector('.home-head');
    this.pathEl = document.getElementById('home-path');
    this.pathRoot = document.getElementById('home-path-root');
    this.pathRootName = document.getElementById('home-path-root-name');
    this.pathRootDot = document.getElementById('home-path-root-dot');
    this.headDot = document.getElementById('home-head-dot');
    this.editBtn = document.getElementById('home-edit');
    this.editLabel = document.getElementById('home-edit-label');

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
      onScreenBack: () => this._refreshFloating(),
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

    /* Le stanze di un quaderno: la bacheca, il cassetto di una scheda, e una
       pagina. Un solo foglio dal basso, che le prime due si dividono. */
    this.boardSheet = new HomeBoardSheet();
    /* L'elenco delle app arriva la prima volta che si apre la pagina App:
       senza aspettarlo, «Apri» da una polaroid o da un cassetto non faceva
       niente. */
    const openApp = async (slug) => {
      const source = this.appsSource();
      const apps = await source.awaitJennyApps();
      /* Un'app appena scritta in chat puo' non esserci ancora: si rilegge. */
      if (!apps.some((a) => a.slug === slug)) await source.loadJennyApps();
      this.appsActions().openApp(slug);
    };
    this.board = new HomeBoard({
      sheet: this.boardSheet,
      onOpenCard: (card, title) => this.openCard(card, title),
      onOpenApp: openApp,
      onOpenPage: (path, label, card, opts) => this.openPage(path, label, card, opts),
      onAskJenny: (text) => this._askInNotebook(text),
    });
    /* I dati di un'app cambiano — scritti dal suo schermo o in chat, o
       ricalcolati dal suo lettore: la sua polaroid si aggiorna da sola. */
    this.appsSource().onAppDataChanged((slug) => this.board.refreshApp(slug));
    this.drawer = new BoardDrawer({
      sheet: this.boardSheet,
      onOpenCard: (card, title) => this.openCard(card, title),
      onOpenPage: (path, label, card) => this.openPage(path, label, card),
      onAskJenny: (text) => this._askInNotebook(text),
      onOpenApp: openApp,
      onRenameCard: (card, title) => this.board.openRename(card, title, () => this.drawer.reload()),
      /* «Togli dalla bacheca» dal fondo del cassetto: si torna alla bacheca, e
         la scheda se ne va sotto gli occhi, con l'Annulla. */
      onRemoveCard: async (card, title) => {
        await this.openBoard();
        await this.board.removeCard(card, title);
      },
      /* «Mettici accanto una tua app» dal cassetto: la polaroid nuova nasce
         sulla bacheca, legata a questa scheda; si torna a vederla. */
      onAppCard: async (card) => {
        await this.openBoard();
        if (card) this.board.reveal(card);
      },
    });
    /* Da quale cassetto e' stata aperta la pagina nel lettore, o `null` se
       dalla ricerca: «indietro» torna li'. */
    this._readerCard = null;
    this.reader = new HomeReader();
    this.reader.onTitle = (title) => this._readerTitle(title);
    this.reader.onOpenDrawer = (card, title) => this.openCard(card, title);
    this.reader.onPreview = (path, from) => this.board.preview(path, from);
    this.reader.onFollow = (path) => this._followPage(path);
    /* Le pagine lette passando da un link, per Indietro: la piu' recente in
       fondo. Si azzera a ogni pagina aperta da fuori (`openPage`). */
    this._pageTrail = [];
    this.audit = new HomeAudit(this.reader);
    this.audit.onFiled = (report) => this._bringToChat(report);
    /* Aperto o chiuso l'editor, cambiano i comandi dell'intestazione — e la
       barra della selezione, che con l'editor aperto non ha piu' senso: li' il
       gesto e' un altro. */
    this.reader.onEditing = () => { this._applyHead(); this.audit.refresh(); };

    /** Quale stanza e' a schermo: `chat` o una delle chiavi di `BACK_TO`. */
    this.view = 'chat';
    /* Com'era Jenny quando hai lasciato la chat. Fuori dalla chat sta al bordo
       — lo dice la tavola, che la disegna a `right:-56px` mentre nelle due
       della chat sta a `-30px` — ma se l'avevi messa via tu, tornando non deve
       ricomparire: quella era una tua decisione, non lo stato della stanza. */
    this._jennyWasOut = true;
    // La chat e' a schermo per lei? v. `_syncJennyPlace`.
    this._jennyInChat = true;

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
    /* Le bozze messe da parte da una frase pronta, per conversazione (v.
       `_prefill`): tornano nel campo quando la frase parte. */
    this._draftsAside = new Map();

    /* «Con chi parli», cioe' la pagina Quaderni. Il pannello non sa cosa sia
       una chiave di sessione — dice quale nome hai toccato, e la conversazione
       la apre questo guscio (e porta alla chat: `openConversation`). */
    this.who = new WhoPanel(document.getElementById('home-notebooks'), {
      currentProject: () => projectNameOf(sessionManager.currentKey),
      onPick: (name) => this.openNotebook(name),
      onHold: (name) => this.notebookCard().show(name),
      /* Dal posto vuoto senza nome, da una ricerca a vuoto col nome cercato
         gia' scritto: e' il giro di sempre, solo con la prima risposta data. */
      onCreate: (name) => this.createNotebook(name),
      search: document.getElementById('home-notebooks-search'),
      searchClear: document.getElementById('home-notebooks-search-clear'),
      plate: document.getElementById('home-notebooks-plate'),
      /* Una lettera e' per la ricerca solo con l'elenco a schermo: non sotto
         una stanza, la chat aperta sopra o un dialogo. */
      active: () => this.view === 'chat' && this._entry?.kind === 'notebooks'
        && !this.chatOver && !document.querySelector('dialog[open]'),
    });

    /* Il selettore di allegati e' lo stesso dell'officina, con gli stessi tetti
       del server (4 immagini, 8 MB l'una): superarli fa rifiutare il messaggio
       intero, quindi i limiti devono stare da una parte sola. */
    /* **Uno per conversazione**, come le bozze: una foto scelta dentro un
       quaderno partiva col primo messaggio della conversazione personale.
       `this.files` e' quello della conversazione a
       schermo, e cambia con lei (v. `showConversation`). */
    this._attachments = new Map();
    this.files = this._filesFor(sessionManager.currentKey);

    this._wireTimer = null;
    this._threadFailed = false;
    /* Le letture del filo in volo (v. `_readThread`), e la riga che dice che
       non e' arrivato quando il filo non e' vuoto (v. `_showThreadError`). */
    this._threadReads = 0;
    this._threadNote = null;
    /* Il payload delle impostazioni, chiesto una volta e diviso fra le due
       stanze che ne leggono un campo per uno. */
    this._settings = null;
    /* Quante volte il guscio ha saputo qualcosa di nuovo sulle impostazioni
       senza chiederlo: un salvataggio, un nome, la finestra flottante, una
       versione. Una lettura partita prima di uno di questi non ridipinge piu'
       niente quando torna — prima riportava a schermo il modello e il nome di
       prima del salvataggio. */
    this._settingsGen = 0;
    this._running = false;

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
      /* Non `keepBottom`: spostata nel documento la chat riparte da scroll 0,
         e `keepBottom` segue il fondo solo «se ci si era» — lo scroll azzerato
         puo' fargli credere che l'utente sia risalito. `restorePlace` non
         guarda lo scroll ma il ricordo della conversazione: in fondo se eri
         in fondo, se no dove eri rimasto (dal 10/10/2026; prima sempre in
         fondo, e chi rileggeva la storia la perdeva aprendo Jenny). */
      atBottom: () => this.chat?.restorePlace(),
      homeKey: () => sessionManager.personalKey,
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
      activate: () => {
        this.launcher.open();
        this.appsTrash().refresh();
      },
      deactivate: () => this.launcher.close(),
    });
    this.homePages.register('notebooks', { activate: () => this.who.show() });
    this.homePages.register('settings', { activate: () => this._openSettings() });
    /* La fila **non** si disegna qui: le traduzioni non ci sono ancora
       (arrivano in `init`, dopo il bootstrap) e i nomi delle pagine fisse
       uscivano come chiavi grezze — «casa.fila.app» — per il tempo di un giro.
       La disegna `_applyTranslations`, appena le parole sono arrivate. */

    /* «Cosa vedi?»: il tool `ui_view` interroga la connessione da cui e'
       partito il messaggio, e scrivendo da qui e' questa. Senza chi risponde
       Jenny aspettava sei secondi e diceva che l'app era in background. */
    this.uiQuery = new HomeUiQuery(this);

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

    /* Senza un provider la casa non ha nessuno che risponda: il primo avvio si
       fa in `onboarding.html`, e ci si va prima di aprire il filo o leggere la
       storia. */
    if (await this._goToOnboardingIfFirstRun()) return;

    /* Le traduzioni prima della storia: la conversazione porta etichette
       tradotte (la provenienza di un messaggio entrato da fuori), e disegnarla
       prima vorrebbe dire scriverci dentro le chiavi grezze. */
    await i18n.load(i18n.locale);
    this._applyTranslations();

    // La barra di selezione che riappariva al rientro: v. `releaseSelectionOnBlur`.
    releaseSelectionOnBlur();

    /* Quel che apparteneva alla conversazione lasciata scade qui. Il `turn_end`
       del turno in volo arrivera' a una chat che non guardiamo piu' e verra'
       scartato da `_belongsHere`: chi tiene stato *per turno* — la faccia di
       Jenny, la riga di lavoro, il bottone Ferma — resterebbe ad aspettarlo per
       sempre. In officina lo stesso evento serve alla stessa cosa
       (`mobile-jenny.js`, `_releaseTrackedTurn`). */
    sessionManager.addEventListener('chat:switch', () => this._releaseTurn());
    /* E porta fuori dalle stanze del quaderno lasciato: la bacheca, un
       cassetto e il lettore parlano di *un* quaderno, e restarci dopo essere
       passati a un altro vorrebbe dire leggere la bacheca di un posto in cui
       non sei piu'. **Solo** da li', e mai con la chat aperta sopra: dal
       05/10/2026 la chat cambia conversazione anche salendo sopra una pagina
       (sopra le App diventa la personale), e quel cambio non deve chiudere
       quel che le sta sotto — una stanza delle Impostazioni, la bacheca. */
    sessionManager.addEventListener('chat:switch', () => {
      if (!this.chatOver && NOTEBOOK_ROOMS.includes(this.view)) this._setView('chat');
    });
    /* Chi lavora e' della conversazione: le bolle della vecchia spariscono, e
       quelle della nuova si leggono, perche' l'attach non rimanda lo snapshot. */
    sessionManager.addEventListener('chat:switch', () => {
      this.work.clear();
      this.work.loadSubagents();
      this.work.loadWork();
    });

    wsManager.addEventListener('chat:open', () => this._onWireOpen());
    wsManager.addEventListener('chat:close', () => this._setWire(false));
    wsManager.addEventListener('chat:message', (e) => {
      this._readRunStatus(e.detail);
      this._readActivity(e.detail);
      this._readSubagents(e.detail);
      this._readWork(e.detail);
      this._readBoardChanged(e.detail);
      this.staged.handleFrame(e.detail);
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
      if (!text) return;
      /* Nel campo c'e' solo la bozza rimessa dopo una frase pronta: la frase
         rifiutata torna, e la bozza si rimette da parte. */
      const restored = this._restoredDraft;
      this._restoredDraft = null;
      if (restored && restored.key === sessionManager.currentKey
          && this.input.value === restored.text) {
        this._draftsAside.set(restored.key, restored.text);
        this.input.value = '';
      }
      if (this.input.value.trim()) return;
      this.input.value = text;
      this._autosize();
    };
    /* «Riprova» sotto una risposta finita in errore: rimanda lo stesso testo,
       e quel che hai nel campo resta li'. */
    this.chat.onRetry = (text) => { if (text) this._sendText(text); };
    /* I due comandi della bacheca in testa: la lente, l'unica porta verso
       tutto, e l'elenco al posto della bacheca. */
    this.boardSearchBtn = document.getElementById('home-board-search-open');
    this.boardListBtn = document.getElementById('home-board-list-toggle');
    this.boardAddBtn = document.getElementById('home-board-add');
    this.boardAddBtn?.addEventListener('click', () => this.board.openNewCard());
    /* I nomi subito, non solo al cambio di lingua: questo cablaggio arriva dopo
       il primo `_applyTranslations`, e fino al 06/10/2026 la lente e l'elenco
       restavano senza nome finche' non si cambiava lingua. */
    this._labelBoardTools();
    this.boardSearchBtn?.addEventListener('click', () => this.board.openSearch());
    this.boardListBtn?.addEventListener('click', () => {
      const on = this.board.toggleList();
      this.boardListBtn.setAttribute('aria-pressed', String(on));
    });
    /* Il + tondo della pagina Quaderni: un quaderno nuovo, e ci si entra. */
    this.newNotebook = document.getElementById('home-notebooks-new');
    this.newNotebook?.addEventListener('click', () => this.createNotebook());
    /* Le due vie d'uscita della stessa stanza, e fanno la stessa cosa: si esce
       da dove stai guardando — in alto a sinistra se leggi l'intestazione, in
       basso a destra col pollice. */
    this.backBtn?.addEventListener('click', () => this.goBackOneRoom());
    this.pathRoot?.addEventListener('click', () => this.goToPathRoot());
    this.editBtn?.addEventListener('click', () => this.reader.startEdit());

    sessionManager.init();
    this._applyConversation();
    wsManager.connectChat();

    /* Non `await`: le pagine aggiunte e l'ordine arrivano quando arrivano, e
       finche' non ci sono la casa e' quella di chi non ha spostato niente, su
       Jenny — che e' esattamente quel che deve essere. */
    /* Finche' il posto di prima non e' stato ripreso (o lasciato), non se ne
       scrive uno nuovo: il primo arrivo sulla chat lo cancellerebbe. */
    this._placeHeld = true;
    this.homePages.load().then(() => this._restorePlace()).finally(() => {
      this._placeHeld = false;
      this._savePlace();
      /* Ripreso il posto, il guscio sente dov'e' la casa (v. `_reportChatOnScreen`). */
      this._reportChatOnScreen();
    });
    /* Neanche il nome si aspetta: fino ad allora la fila dice quello di
       sempre. */
    this._readName();

    /* Un `session_boundary` rilegge il filo da qui, cosi' una lettura che
       non arriva si dice come tutte le altre. */
    this.chat.onSessionBoundary = () => this._readThread();
    /* «Ci spostiamo?»: il tasto sotto una risposta apre la chat del quaderno
       proposto, dove Jenny trova nel diario da dove venite. */
    this.chat.onOpenNotebook = (name) => {
      if (isOpenableProjectName(name)) this.switchConversation(projectKey(name));
    };
    /* Un filo che non e' arrivato si riprova quando torni a guardare: la casa
       e' il launcher, e fra un'occhiata e l'altra il gateway e' tornato. */
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') return;
      this._retryThread();
      this._reportChatOnScreen();
    });
    /* Un foglio o una domanda che si chiude sopra la chat la rimette a
       schermo: `close` non risale, ma passa dalla cattura del documento. */
    document.addEventListener('close', () => this._reportChatOnScreen(), true);
    await this._readThread({ fresh: false });
  }

  /** Legge il filo — all'avvio, a un cambio di conversazione, dopo una
   *  riconnessione o un `session_boundary` — e dice com'e' andata.
   *
   *  **Un posto solo**, e non quattro `try` diversi: prima l'avvio mostrava
   *  l'errore e non riprovava piu', il resync lo scriveva nel log e basta, e
   *  il `session_boundary` lasciava un rifiuto di promessa senza padrone.
   *  Le letture si contano: finche' ce n'e' una
   *  in volo una riconnessione non ne fa partire un'altra, e se partono lo
   *  stesso disegna solo l'ultima (v. `HomeChat._read`). */
  async _readThread({ fresh = true } = {}) {
    this._threadReads += 1;
    try {
      const shown = await (fresh ? this.chat.reload() : this.chat.load());
      if (shown === null) return;  // scavalcata: decide la lettura dopo
      /* Una lettura riuscita toglie il messaggio d'errore precedente: se
         restasse, il vuoto di questa conversazione direbbe «non riesco a
         leggerla» di una storia che abbiamo appena letto. */
      if (this._threadFailed) {
        this._threadFailed = false;
        this._applyConversationTexts();
      }
    } catch (err) {
      console.error('Thread load failed:', err);
      api.clientLog('error', 'home.thread', String(err && err.stack || err));
      this._showThreadError();
    } finally {
      this._threadReads -= 1;
    }
  }

  /* Un filo che non e' arrivato, riprovato: a visibilita', a Home, quando la
     chat torna a schermo. Niente se e' arrivato, o se una lettura e' gia' in
     volo. */
  _retryThread() {
    if (!this._threadFailed || this._threadReads > 0) return;
    this._readThread();
  }

  /* La storia non e' arrivata. Non si finge una conversazione vuota: una chat
     vuota e una chat irraggiungibile sono due cose diverse, e confonderle
     significa far credere di aver perso tutto.

     Se nel filo c'e' gia' qualcosa — un resync fallito tiene quel che c'era —
     il vuoto non si vede, e l'avviso va **nel filo**, una riga come quelle di
     un rifiuto: la rilettura riuscita la toglie. Una sola, anche se i
     tentativi falliti sono tanti. */
  _showThreadError() {
    /* Il flag e' quel che impedisce a un cambio di lingua di riscriverci sopra
       "non c'e' ancora niente qui" — cioe' esattamente la bugia che questo
       messaggio esiste per non dire. */
    this._threadFailed = true;
    const text = i18n.t('home.threadError');
    if (this.chat && !this.chat.isBlank) {
      if (!this._threadNote?.isConnected) this._threadNote = this.chat.showNote(text);
      return;
    }
    if (this.emptyText) this.emptyText.textContent = text;
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
    /* ...e cosi' gli allegati in attesa. */
    this.files = this._filesFor(target);
    this._renderPending();
    this._applyConversation();
    await this._readThread();
  }

  /** Un quaderno nuovo, e ci si entra: **nella sua chat**, dove Jenny saluta.
   *
   *  Le due domande — come si chiama, di cosa si occupa — e tutto quel che puo'
   *  andare storto stanno in `shared/project-create.js`, che e' lo stesso giro
   *  che fa l'officina, con le stesse parole. Qui c'e' quel che e' di casa: i nomi
   *  gia' noti per l'avviso, e dove si va dopo.
   *
   *  Dove si va: il quaderno si apre come sempre, e sopra la sua bacheca sale
   *  subito la chat. La bacheca di un quaderno appena nato e' vuota per forza, e
   *  fermarsi li' lasciava l'utente davanti a un posto che non dice niente; la
   *  chat invece si apre con Jenny che parla per prima (`project.greet`, v.
   *  `jenny/webui/project_greet.py`). Fino al 10/10/2026 qui c'erano altre due
   *  domande — le sezioni dei cassetti e cosa ricordare — tolte perche' a
   *  quaderno vuoto non si sa ancora rispondere: le sezioni le propone Jenny.
   */
  async createNotebook(initial = '') {
    const name = await createProjectFlow({
      t: (key, vars) => i18n.t(key, vars),
      known: this.who.known,
      initial: typeof initial === 'string' ? initial : '',
    });
    if (!name) return;
    this.who.invalidate();              // l'elenco su disco e' cambiato
    await this.openNotebook(name);
    if (!this.openChatOver({ key: projectKey(name) })) return;
    await this._greetNotebook(name);
  }

  /** Chiede il saluto di Jenny nella chat appena aperta. Il messaggio arriva
   *  dal filo come ogni messaggio suo; qui c'e' solo la bolla «sta scrivendo»
   *  per l'attesa. Si spegne a risposta arrivata, comunque sia andata: il
   *  `turn_end` del saluto la spegnerebbe da se', ma un frame perso la
   *  lascerebbe accesa per sempre. Un saluto che non arriva non e' un errore da
   *  mostrare: la chat resta quella di sempre. */
  async _greetNotebook(name) {
    this.activity.start();
    try {
      await rpc.greetProject(name, i18n.locale);
    } catch (err) {
      console.warn('home: notebook greeting failed', err);
    } finally {
      this.activity.stop();
    }
  }

  /** Apre un quaderno: **sempre sulla sua bacheca** (dal 05/10/2026).
   *
   *  La sua chat non ha piu' una pagina: e' dietro Jenny, che la porta sopra
   *  la bacheca quando la tocchi. Qui la chat — a casa, dietro la bacheca —
   *  passa alla conversazione del quaderno senza farsi vedere (`retune`), e la
   *  pagina chat continua a mostrare la personale. Lo apre chi tocca un
   *  quaderno nei Quaderni, la sua scheda, la porta di un quaderno appeso, un
   *  quaderno appena nato, e chiunque chieda una conversazione `project:`.
   */
  async openNotebook(name) {
    if (!name) return;
    /* Si passa dalla pagina chat, ancora sulla personale, per arrivare alla
       bacheca: detto al guscio, quel passaggio cancellava gli avvisi personali
       non letti (misurato sull'emulatore il 10/10/2026, toccando l'avviso di un
       quaderno). Al guscio si dice solo dove si arriva. */
    this._holdChatReport = true;
    try {
      this._closeAllOverlays();
      if (!this._setView('chat', () => this.openNotebook(name))) return;
      const read = this.chatMove.retune(this.homePages?._panelFor?.('chat'), projectKey(name));
      /* Si entra nel quaderno da fuori: la bacheca si apre inquadrata tutta.
         Le altre strade per la bacheca ci tornano da dentro, e la ritrovano dove
         l'avevano lasciata (v. `HomeBoard.load`). */
      await this.openBoard({ fit: true });
      await read;
    } finally {
      this._holdChatReport = false;
      this._reportChatOnScreen();
    }
  }

  /** La scheda di un quaderno, nata al primo uso. */
  notebookCard() {
    return (this._notebookCard ||= new NotebookCard({
      homePages: () => this.pagesPort(),
      open: (name) => this.openNotebook(name),
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
    /* Come alla creazione: la regola sotto il campo, e un nome sbagliato
       tiene aperto il dialog col testo scritto invece di chiuderlo. */
    const written = await promptDialog(i18n.t('home.notebook.renamePrompt', { name: name }), {
      initial: name,
      hint: i18n.t('scope.newProjectHint'),
      validate: (value) => {
        const clean = value.trim();
        return !clean || clean === name || isOpenableProjectName(clean)
          ? null : i18n.t('scope.invalidName');
      },
    });
    const newName = (written || '').trim();
    if (!newName || newName === name) return false;
    try {
      await rpc.renameProject(name, newName);
    } catch (err) {
      /* I rifiuti attesi hanno un codice, e vanno detti nella lingua di chi
         legge: `conflict` (qualcuno ci sta scrivendo: un turno, un subagent,
         una passata del giardiniere, l'autocompact), `name_taken` (il nome
         nuovo e' gia' di una cartella o di una conversazione), `not_found` (il
         quaderno non c'e' piu'). Il testo inglese del server resta per i log,
         anche per l'errore imprevisto: nel toast italiano era un corpo
         estraneo (10/10/2026). */
      console.warn('home.notebook: rename failed', err?.code || '(no code)', err?.message);
      const expected = {
        conflict: ['home.notebook.renameBusy', { name: name }],
        name_taken: ['home.notebook.renameTaken', { name: newName }],
        not_found: ['home.notebook.renameMissing', { name: name }],
      };
      const [key, parameters] = expected[err?.code]
        || ['home.notebook.renameFailed', { name: name }];
      showToast(i18n.t(key, parameters), 'error');
      return false;
    }
    /* La bacheca sta dentro la cartella del quaderno: il rinomino la porta con
       se', e non c'e' niente da spostare a mano. */
    const oldKey = projectKey(name);
    const newKey = projectKey(newName);
    this.homePages.renameConversation(oldKey, newKey);
    this._renameDraft(oldKey, newKey);
    /* I Quaderni rileggono l'elenco **prima** del cambio: chi ridisegna dopo
       trova la loro cache gia' sul nome nuovo. */
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

  /** Gli allegati in attesa della conversazione `key`, nati al primo uso.
   *
   *  Ognuno parla col guscio solo mentre e' quello a schermo: un file letto
   *  dopo che hai cambiato conversazione finisce nella sua, e non ridisegna
   *  la striscia ne' scrive un rifiuto nel filo di un'altra. */
  _filesFor(key) {
    let files = this._attachments.get(key);
    if (files) return files;
    files = new ImageHandler();
    files.onChange = () => { if (this.files === files) this._renderPending(); };
    /* Un allegato che non entra lo diceva nessuno: spariva e basta. Ora lo
       dice il telefono, con le stesse parole che userebbe il gateway. */
    files.onReject = (reason) => { if (this.files === files) this.chat.noteRefusal(reason); };
    this._attachments.set(key, files);
    return files;
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
    /* Gli allegati in attesa seguono la bozza. */
    const files = this._attachments?.get(oldKey);
    if (files) {
      this._attachments.delete(oldKey);
      this._attachments.set(newKey, files);
    }
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
    if (!(await deleteProjectFlow(name))) return false;
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
      /* Dalla bacheca del quaderno cancellato si esce; la chat torna alla
         personale dietro la sua foto, come quando la si riabbassa. */
      if (NOTEBOOK_ROOMS.includes(this.view)) this._setView('chat');
      await this.chatMove.retune(
        this.homePages?._panelFor?.('chat'),
        this.homePages?.homeConversation || sessionManager.personalKey,
      );
    }
    this._drafts.delete(key);
    this._attachments?.delete(key);
    await this.who.refresh();
    await this.pagesPort().reload();
    showToast(i18n.t('home.notebook.deleted', { name: name }), 'success');
    return true;
  }

  /* ── Le stanze ──
   *
   *  Le pagine del quaderno, il lettore, e le stanze delle impostazioni: si
   *  entra da una pagina e si torna indietro una alla volta — mai due per un
   *  gesto, che e' la stessa regola con cui Indietro chiude una scheda senza
   *  uscire anche dal quaderno.
   */

  /** La bacheca del quaderno in cui sei. Dalla chat personale non c'e' nulla da
   *  aprire, e infatti la pastiglia li' non compare.
   *
   *  **Un quaderno si apre sulla bacheca, non sulle pagine**: l'elenco delle
   *  pagine e la mappa a grafo sono usciti dalla casa, e la wiki si raggiunge
   *  da un cassetto o dalla ricerca. */
  async openBoard({ fit = false } = {}) {
    const notebook = projectNameOf(sessionManager.currentKey);
    if (!notebook) return;
    if (!this._setView('board', () => this.openBoard({ fit }))) return;
    await this.board.load(notebook, { fit });
  }

  /** Il cassetto di una scheda. */
  async openCard(card, title, { keepPlace = false } = {}) {
    const notebook = projectNameOf(sessionManager.currentKey);
    if (!notebook || !card) return;
    this.boardSheet.close();
    if (!this._setView('drawer', () => this.openCard(card, title, { keepPlace }))) return;
    this._cardTitle = title || '';
    this._setHeadTitle(title || '');
    const loaded = await this.drawer.load(notebook, card, { keepPlace });
    if (loaded && this.view === 'drawer') {
      this._cardTitle = loaded;
      this._setHeadTitle(loaded);
      this._applyPath(notebook);
    }
    this._savePlace();
  }

  /** Una pagina del quaderno, letta. *card* e' il cassetto da cui la si apre:
   *  «indietro» torna li'. */
  async openPage(path, label, card = null, { fromSearch = null } = {}) {
    const notebook = projectNameOf(sessionManager.currentKey);
    if (!notebook || !path) return;
    this._readerCard = card || null;
    this._pageTrail = [];
    /* Aperta fra i risultati della ricerca della bacheca: Indietro torna li',
       con la frase scritta, non nel cassetto della pagina — che non avevi
       aperto (giro UX del 10/10/2026; il commento di `BACK_TO` lo prometteva
       gia'). */
    this._searchReturn = fromSearch;
    /* Il nome del cassetto da cui viene la pagina, per il percorso: quello del
       cassetto aperto se e' lui, se no quello che la bacheca conosce (una
       pagina aperta dalla ricerca puo' stare in un cassetto mai aperto). */
    if (card) {
      this._cardTitle = (card === this.drawer.card && this.drawer.title)
        || this.board.data?.cards?.find((c) => c.id === card)?.title
        || '';
    }
    if (!this._setView('reader')) return;
    this._setHeadTitle(label || '');
    this._applyHead();
    this._readerTitle(await this.reader.load(notebook, path, label));
    this._savePlace();
  }

  /* Dove torna la freccia da questa stanza. Il lettore torna al cassetto da cui
     l'hai aperto; senza un cassetto (dalla ricerca), alla bacheca. */
  _backTarget(view = this.view) {
    if (view === 'reader' && this._pageTrail?.length) return 'page';
    if (view === 'reader' && (this._searchReturn != null || !this._readerCard)) return 'board';
    return BACK_TO[view];
  }

  /** Un link a un'altra pagina dello stesso cassetto, dal lettore.
   *
   *  La pagina che lasci entra nella pila (dov'eri compreso), e Indietro ci
   *  torna prima di tornare al cassetto. Fino al 10/10/2026 il link caricava
   *  la pagina nuova al posto della vecchia senza traccia: Indietro saltava A
   *  e portava al cassetto. Dieci pagine bastano: e' un sentiero, non una
   *  cronologia. */
  async _followPage(path) {
    const notebook = projectNameOf(sessionManager.currentKey);
    if (!notebook || !path || this.view !== 'reader') return;
    if (this.reader.path) {
      this._pageTrail.push({
        path: this.reader.path,
        label: this.nameEl?.textContent || '',
        top: this.reader.bodyEl?.scrollTop || 0,
      });
      if (this._pageTrail.length > 10) this._pageTrail.shift();
    }
    this._applyHead();
    this._readerTitle(await this.reader.load(notebook, path));
    this._savePlace();
  }

  /* Indietro di una pagina lungo la pila dei link, dov'eri. */
  async _backOnePage() {
    const notebook = projectNameOf(sessionManager.currentKey);
    const step = this._pageTrail.pop();
    if (!notebook || !step) return;
    this._applyHead();
    this._setHeadTitle(step.label);
    this._readerTitle(await this.reader.load(notebook, step.path, step.label));
    if (this.view === 'reader' && this.reader.path === step.path && this.reader.bodyEl) {
      this.reader.bodyEl.scrollTop = step.top;
    }
    this._savePlace();
  }

  /* «Altro…» e l'esempio della bacheca vuota: una frase gia' scritta, nella
     chat del quaderno, da mandare tu. La chat sale sopra la bacheca, come
     quando tocchi Jenny. */
  _askInNotebook(text) {
    this.boardSheet.close();
    if (this.chatOver) this._prefill(text);
    else this.openChatOver({ prefill: text });
  }

  /* ── Il velo: la chat sopra la pagina ──
   *
   *  Dal 05/10/2026 Jenny e' la porta della conversazione: toccata dove la chat
   *  non c'e', la chat vera sale a tutto schermo sopra la pagina, e la pagina
   *  resta visibile in trasparenza. Sopra la bacheca, un cassetto o una pagina
   *  di un quaderno e' la chat di quel quaderno; da ogni altro posto e' la
   *  personale. Si chiude ritoccando Jenny o con Indietro.
   */

  /** La chat e' aperta sopra la pagina? */
  get chatOver() {
    return Boolean(this.over?.isOpen);
  }

  /* Quale conversazione porta su Jenny, da qui. */
  _summonKey() {
    return NOTEBOOK_ROOMS.includes(this.view) ? sessionManager.currentKey : sessionManager.personalKey;
  }

  /** Apre la chat sopra la pagina. *prefill* e' una frase gia' scritta nel
   *  campo, da mandare tu. *key* impone la conversazione al posto di quella
   *  che Jenny porterebbe da qui (v. `openChat`). Vero se si e' aperta. */
  openChatOver({ prefill = null, key: wanted = null } = {}) {
    if (this.chatOver) return true;
    /* Mentre si ordinano le pagine, o con una domanda aperta, il velo non
       sale: lei sobbalza, e la cosa aperta resta quella. */
    if (this.strip?.sorting || document.querySelector('dialog[open]')) {
      this.jenny?.nudge?.();
      return false;
    }
    if (!this.over?.host) return false;
    const key = wanted || this._summonKey();
    /* Una conversazione imposta sopra una stanza di quaderno — l'avviso porta
       la personale sopra la bacheca — si ricorda quella che c'era: la bacheca
       sotto e' del quaderno, e la chat dietro di lei torna sua quando il velo
       scende (v. `closeChatOver`). */
    this._overReturn = key !== sessionManager.currentKey && NOTEBOOK_ROOMS.includes(this.view)
      ? sessionManager.currentKey : null;
    this.over.show();
    this.jenny?.setExpanded?.(true);
    this.chatMove.lift(this.over.host, key);
    this.jenny?.setChatOnScreen?.(true);
    this.jenny?.setOut?.(true);
    this._measureFloor?.();
    if (prefill !== null && this.input) {
      this._prefill(prefill);
    } else {
      this.focus?.restore();
    }
    this._retryThread();
    this._reportChatOnScreen();
    this.unseen?.refresh();
    return true;
  }

  /** Chiude la chat sopra la pagina: torna a casa, nella pagina chat, con la
   *  conversazione che aveva. Vero se era aperta. */
  closeChatOver() {
    if (!this.chatOver) return false;
    const back = this._overReturn;
    this._overReturn = null;
    this.input?.blur();
    const home = this.homePages?._panelFor?.('chat');
    this.chatMove.lower(home);
    /* Il velo era salito con un'altra conversazione sopra il quaderno (v.
       `openChatOver`): la chat, a casa dietro la bacheca, torna sua senza
       farsi vedere — come quando il quaderno si apre. */
    if (back && back !== sessionManager.currentKey && NOTEBOOK_ROOMS.includes(this.view)) {
      this.chatMove.retune(home, back);
    }
    this.over.hide();
    this.jenny?.setExpanded?.(false);
    this.jenny?.setChatOnScreen?.(false);
    this.jenny?.setOut?.(false);
    /* Il pavimento torna quello della pagina sotto: il composer la' non c'e'. */
    if (this.view === 'chat' && this._hasComposer(this._entry)) this._measureFloor?.();
    else document.documentElement.style.setProperty('--home-composer-h', `${FLOOR_NO_COMPOSER}px`);
    this._reportChatOnScreen();
    this.unseen?.refresh();
    return true;
  }

  /* Il titolo di una pagina arriva dopo una lettura — l'apertura, un
     salvataggio, un conflitto — e si scrive solo se si e' ancora nel lettore:
     tornati indietro nel frattempo, la testa dice gia' dove sei, e il titolo
     della pagina lasciata ci scriverebbe sopra. */
  _readerTitle(title) {
    if (this.view === 'reader') this._setHeadTitle(title);
  }


  /** La pagina Impostazioni e' diventata quella che guardi: si ridisegna e si
   *  rilegge quel che sa il server.
   *
   *  **Si rilegge davvero, a ogni apertura.** Il commento lo diceva gia', ma
   *  la lettura era quella dell'avvio, tenuta per tutta la vita della pagina:
   *  e la casa e' il launcher, vive per giorni. Un aggiornamento trovato dal
   *  controllo periodico non compariva mai, e «ultimo controllo» invecchiava.
   *  Intanto si mostra quel che si sa gia' — la pagina non resta vuota per il
   *  tempo di un giro — e quando arriva la risposta nuova si ridisegna. Una
   *  rilettura fallita lascia a schermo quel che c'era. */
  async _openSettings() {
    this.you.open();
    this.you.sayJenny(this.jennyRoom.value());
    const gen = this._settingsGen;
    const cached = this._settings ? await this._settings : null;
    if (cached && gen === this._settingsGen) this._paintSettings(cached);
    const data = await this._askSettings({ fresh: true });
    /* Nel frattempo e' arrivato qualcosa di piu' nuovo, ed e' gia' a schermo. */
    if (gen !== this._settingsGen) return;
    if (data || !cached) this._paintSettings(data);
  }

  /* Le righe di «Tu e Jenny» e le stanze che si aprono da li', da un payload
     di `/api/settings` (`null` = lettura fallita, cioe' «non lo so»). */
  _paintSettings(data) {
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
    this._settingsGen += 1;
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
    this._settingsGen += 1;
    this._settings?.then?.((data) => {
      if (data && floating) data.floating = floating;
    });
  }

  /* Di ritorno da Accessibilita': l'interruttore della lettura dello schermo
     e' di Android, quindi lo stato vero si rilegge dal server. */
  async _refreshFloating() {
    const data = await this._askSettings({ fresh: true });
    if (!data?.floating) return;
    this._keepFloating(data.floating);
    this.jennyRoom.setFloating(data.floating);
  }

  /* Il nome appena salvato nella stanza di lei: va nella cache, o alla
     prossima apertura delle Impostazioni `setName` rimetterebbe il nome letto
     la prima volta; e va nella fila e nei Quaderni, che lo scrivono. */
  _keepName(name) {
    this._settingsGen += 1;
    this._settings?.then?.((data) => {
      if (data && name) (data.agent ||= {}).bot_name = name;
    });
    this._applyBotName(name);
  }

  /* Il nome di lei, dalle impostazioni: la stessa lettura che la pagina
     Impostazioni fa comunque, e che resta in cache per lei. */
  async _readName() {
    const gen = this._settingsGen;
    const data = await this._askSettings();
    if (data && gen === this._settingsGen) this._applyBotName(data.agent?.bot_name);
  }

  /* Il nome della conversazione personale, dove lo si scrive: la fila. Vuoto
     vuol dire il ripiego, come sul server. */
  _applyBotName(name) {
    const newName = (typeof name === 'string' && name.trim()) || DEFAULT_BOT_NAME;
    if (newName === this._personalName) return;
    this._personalName = newName;
    botName.set(newName);
    this.strip?.draw();
    // La riga delle Impostazioni, la sua stanza e il testo della finestra
    // flottante portano il nome: rinominata li' dentro, cambiano subito.
    this.you?.applyTranslations();
    this.jennyRoom?.applyTranslations();
    if (this.view === 'jenny') this._applyHead();
    this._applyConversationTexts();
  }

  /* Il payload fresco che torna da un salvataggio: ha la stessa forma di
     `/api/settings`, quindi prende il posto di quello in cache e le righe che
     lo leggono si riscrivono. Senza, la riga «Chi risponde» direbbe la marca
     di prima fino al riavvio della casa. */
  _keepSettings(data) {
    if (!data) return;
    this._settingsGen += 1;
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
     manca — e' un'impostazione sparita.

     `fresh` la rilegge comunque: lo chiede l'apertura delle Impostazioni, che
     e' il momento in cui quel che si mostra deve essere di adesso. */
  /** Se e' il primo avvio, va a `onboarding.html` e restituisce `true`.
   *
   *  Un «non lo so» (lettura fallita) non manda da nessuna parte, e con
   *  `retry` lascia `_firstRunUnknown` acceso perche' `_onWireOpen` richieda.
   *  `replace`: Indietro dal wizard non deve tornare a una casa che non aveva
   *  nessuno a rispondere. */
  async _goToOnboardingIfFirstRun({ fresh = false, retry = true } = {}) {
    const first = await isFirstRun(() => this._askSettings({ fresh }));
    if (first === null) {
      this._firstRunUnknown = retry;
      return false;
    }
    if (!first) return false;
    api.navigate('/html-mobile/onboarding.html', { replace: true });
    return true;
  }

  _askSettings({ fresh = false } = {}) {
    if (!this._settings || fresh) {
      /* Una rilettura che fallisce non butta quel che si sapeva: torna `null`
         a chi l'ha chiesta, e la cache torna quella di prima. */
      const previous = this._settings;
      const request = api.getSettings().catch((err) => {
        console.warn('home: settings not read', err);
        if (this._settings === request) this._settings = previous;
        return null;
      });
      this._settings = request;
    }
    return this._settings;
  }

  /** Segnalata una cosa: si atterra nella chat del quaderno, col messaggio
   *  gia' partito.
   *
   *  **Non e' una funzione nuova, e' una giunzione.** «Chat» dell'interruttore
   *  porta gia' in questa stanza; `_send()` legge gia' dalla casella e disegna la bolla; e la
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
    /* Dal lettore la chat e' gia' quella del quaderno: sale sopra la pagina. */
    this.openChatOver();
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


  /** Ricorda dove sei: la pagina in alto, e dentro un quaderno la stanza —
   *  bacheca, cassetto, pagina letta.
   *
   *  Serve quando la WebView riparte da capo (Android chiude il processo o il
   *  renderer mentre usi un'altra app pesante): prima la casa ripartiva
   *  sempre dalla chat (giro UX del 10/10/2026). Si scrive a ogni cambio di
   *  pagina o di stanza; si rilegge una volta, all'avvio (`_restorePlace`). */
  _savePlace() {
    if (this._placeHeld) return;
    const reading = this.view === 'reader';
    writeStorage(PLACE_KEY, JSON.stringify({
      at: Date.now(),
      page: this._entry?.id || null,
      view: this.view,
      notebook: NOTEBOOK_ROOMS.includes(this.view) ? projectNameOf(sessionManager.currentKey) : null,
      card: this.view === 'drawer' ? this.drawer?.card || null : reading ? this._readerCard : null,
      cardTitle: this._cardTitle || '',
      path: reading ? this.reader?.path || null : null,
      label: reading ? this.nameEl?.textContent || '' : '',
    }));
  }

  /** All'avvio, il posto di prima — se e' recente, e se l'avvio non chiede
   *  la chat (`?mode=chat`: un avviso toccato a app chiusa, il riavvio dopo
   *  un ripristino). Una cosa che nel frattempo non c'e' piu' (un quaderno
   *  cancellato) lascia dove si e' arrivati. */
  async _restorePlace() {
    const params = new URLSearchParams(globalThis.location?.search || '');
    if (params.get('mode') === 'chat') {
      /* L'avviso toccato a app chiusa era di un quaderno: si va li' (v.
         `openChat`), e si aspetta di esserci — il posto resta tenuto, e il
         guscio non sente la pagina chat da cui si passa. */
      const notebook = projectNameOf(params.get('chat'));
      if (notebook && isOpenableProjectName(notebook)) await this._enterNotebookChat(notebook);
      return;
    }
    let place = null;
    try { place = JSON.parse(readStorage(PLACE_KEY) || 'null'); } catch { place = null; }
    if (!place || !(Date.now() - place.at < PLACE_MAX_AGE_MS)) return;
    // Nel frattempo ti sei gia' mosso: vince quel che hai fatto tu.
    if (this.view !== 'chat' || this.chatOver) return;
    if (place.page && place.page !== 'chat' && this.homePages.indexOf(place.page) >= 0) {
      this.homePages.goToId(place.page, { animated: false });
    }
    if (!place.notebook) return;
    await this.openNotebook(place.notebook);
    if (this.view !== 'board') return;
    if (place.card && (place.view === 'drawer' || place.view === 'reader')) {
      await this.openCard(place.card, place.cardTitle);
    }
    if (place.view === 'reader' && place.path) {
      await this.openPage(place.path, place.label, place.card);
    }
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
    const target = this._backTarget();
    if (!target) return false;
    if (target === 'page') {
      this._backOnePage();
      return true;
    }
    /* La bacheca aperta dalla porta di un quaderno appeso torna **alla
       porta**, cioe' alla pagina in alto da cui l'hai aperta: dal 10/10/2026
       la porta non si riapre da sola (v. `onPageChanged`), e prima di allora qui
       si saltava alla chat per non ritrovarsela riaperta. La pista e' gia'
       li': basta cambiare stanza. */
    if (target === 'chat' && this._entry?.kind === 'conversation') {
      this._setView('chat', () => this.goBackOneRoom());
      return true;
    }
    if (target === 'drawer' && this._readerCard) {
      /* Il cassetto da cui eri entrato, ritrovato dove l'avevi lasciato. */
      this.openCard(this._readerCard, this._cardTitle, { keepPlace: true });
      return true;
    }
    if (target === 'board') {
      const query = this.view === 'reader' ? this._searchReturn : null;
      this.openBoard().then(() => {
        if (query != null && this.view === 'board') this.board.openSearch(query);
      });
      return true;
    }
    if (target === 'settings') {
      this._setView('chat');
      /* Le stanze si aprono dalla pagina Impostazioni, e la pista di solito
         e' ancora li': ripassarci sopra ridisegnava la fila per niente. */
      if (this.homePages.order[this.homePages.index] !== 'settings') {
        this.homePages.goToId('settings', { animated: false });
      }
      return true;
    }
    this._setView(target);
    return true;
  }

  /** La pista ha cambiato casella: la fila dice dove sei. */
  onPageChanged(index, entry, { animated = false } = {}) {
    this._entry = entry;
    /* La pagina di un quaderno appeso e' una porta che si apre **al tocco**
       («Apri la bacheca», `HomePages._fillDoor`), non al passaggio: fino al
       10/10/2026 si apriva da sola appena la pista ci si fermava, e
       scorrendo verso le pagine dopo restavi chiuso nella sua bacheca (li' la
       pista non scorre). */
    /* Su quale pagina si e' lo dice un attributo, come per le stanze: cosi' la
       geometria resta nel CSS e qui c'e' solo il nome. */
    this.shell?.setAttribute('data-page', entry?.id || '');
    /* La tastiera non resta aperta su un campo che e' uscito di scena: i tasti
       dopo finirebbero nella chat che non guardi. Ne' su quello coperto
       dall'avviso di un quaderno cancellato (v. `onGoneChanged`). */
    if (!this._hasComposer(entry) || this.homePages?.goneHere?.()) this.input?.blur();
    else this.focus?.restore();
    // La chat torna a schermo: un filo che non era arrivato si riprova.
    if (this._hasComposer(entry)) this._retryThread();
    this._placeJenny();
    this._syncJennyPlace();
    this.strip?.draw();
    this._applyHead();
    this._reportChatOnScreen();
    this.unseen?.refresh();
    this._savePlace();
  }

  /** Una pagina ha scoperto che la sua cosa non c'e' piu', o che c'e' di
   *  nuovo. Arriva **dopo** `onPageChanged` — il controllo e' una lettura
   *  di rete — e se la pagina e' quella a schermo il campo va tolto di mezzo:
   *  con la tastiera fisica il fuoco era gia' stato rimesso, e i tasti
   *  sarebbero andati a un quaderno cancellato. */
  onGoneChanged(panel) {
    if (!this.homePages || this.homePages.panelOf(this.homePages.index) !== panel) return;
    if (this.homePages.goneHere()) {
      this.input?.blur();
      /* La porta di un quaderno sparito ha fatto in tempo ad aprire la sua
         bacheca: si torna alla pagina, che dice che non c'e' piu'. */
      if (this._entry?.kind === 'conversation' && NOTEBOOK_ROOMS.includes(this.view)) this._setView('chat');
    } else {
      this.focus?.restore();
    }
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

  /* La pagina su cui si scrive: la chat. Dal 05/10/2026 l'unica — la chat di
     un quaderno non abita piu' una pagina, la porta su Jenny. */
  _hasComposer(entry) {
    return entry?.kind === 'chat';
  }

  /* Dove appoggia i piedi Jenny: sul composer dove c'e', al pavimento delle
     stanze dove non c'e'. Una pagina App o Impostazioni non ha un composer, e
     misurarlo lo stesso — sta nella pagina accanto, ancora alto quanto era —
     la terrebbe sospesa a mezz'aria sopra le righe. */
  _placeJenny() {
    // Con la chat sopra la pagina il pavimento e' il suo composer.
    if (this.chatOver) {
      this._measureFloor?.();
      return;
    }
    if (this.view !== 'chat') return;
    if (this._hasComposer(this._entry)) {
      this._measureFloor?.();
      return;
    }
    document.documentElement.style.setProperty('--home-composer-h', `${FLOOR_NO_COMPOSER}px`);
  }

  /* Dove sta Jenny: la chat e' a schermo solo nella stanza chat **e** sulla
     pagina chat. Fuori sta al bordo — lo dice la tavola, che la disegna a
     `right:-56px` contro i `-30px` della chat — e un tocco porta su la chat.
     Tornando si rimette com'era quando hai lasciato la chat, e non «fuori»
     d'ufficio: metterla via era una tua decisione. Con la chat aperta sopra la
     pagina la mette il velo, e qui non si tocca niente.
     Al boot la pagina non c'e' ancora, ed e' la chat. */
  _syncJennyPlace() {
    if (this.chatOver) return;
    const inChat = this.view === 'chat' && (!this._entry || this._hasComposer(this._entry));
    if (inChat === this._jennyInChat) return;
    this._jennyInChat = inChat;
    if (inChat) {
      this.jenny.setChatOnScreen(true);
      this.jenny.setOut(this._jennyWasOut);
      return;
    }
    this._jennyWasOut = this.jenny.el.classList.contains('out');
    this.jenny.setChatOnScreen(false);
    this.jenny.setOut(false);
  }

  /** Il nome della pagina chat nella fila: il nome di lei, e basta.
   *
   *  Fino al 26/09/2026 era anche il quaderno che la chat mostrava, col suo
   *  pallino: una pagina che cambiava nome a seconda di cosa ci guardavi
   *  dentro, e la conversazione personale spariva dal menu. Oggi un quaderno si
   *  apre nei Quaderni (v. `HomePages.openConversation`), e la pagina chat e'
   *  sempre la conversazione personale. */
  _chatName() {
    return { name: this._personalName, color: null };
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
    /* Con la chat sopra la pagina il campo e' li', davanti a tutto il resto
       della casa: lo coprono solo un foglio, un dialogo o l'immagine. */
    if (this.chatOver) return !this._sheetOpen() && !document.querySelector('dialog[open], .image-lightbox');
    if (this.view !== 'chat' || !this._hasComposer(this._entry)) return false;
    if (this.homePages?.goneHere?.()) return false;
    if (this.hasOverlayAbove()) return false;
    return !document.querySelector('dialog[open], .image-lightbox');
  }

  /** C'e' qualcosa sopra il cassetto? Lo chiede lui prima di prendersi un
   *  tasto: da pagina, e' vivo mentre la guardi — anche sotto una scheda aperta
   *  sopra, o sotto una app che ha lanciato. */
  hasOverlayAbove() {
    for (const id of [...SHARED_DIALOGS, ...LONG_PRESS_SHEETS, REPORT_SHEET, BOARD_SHEET]) {
      if (document.getElementById(id)?.open) return true;
    }
    /* La chat sopra la pagina anche: il cassetto non deve prendersi i tasti
       scritti nel suo campo. */
    return Boolean(this._appActions?.isAppOpen()) || Boolean(this.strip?.sorting)
      || this.chatOver;
  }

  /* Un foglio della casa e' aperto: un dialogo condiviso, una scheda di una
     pressione lunga, «Segnala», il foglio della bacheca. */
  _sheetOpen() {
    for (const id of [...SHARED_DIALOGS, ...LONG_PRESS_SHEETS, REPORT_SHEET, BOARD_SHEET]) {
      if (document.getElementById(id)?.open) return true;
    }
    return false;
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
    /* Un cambio di stanza con la chat aperta sopra la pagina — un link nel
       filo, «Apri la bacheca» di una bolla — e' un posto dove andare: la chat
       ridiscende, o la stanza nuova si aprirebbe sotto di lei. */
    if (this.chatOver) this.closeChatOver();
    /* Uscire dal lettore con modifiche non salvate chiede conferma, e la
       guardia sta **qui** e non sui bottoni. Le strade per uscire sono gia'
       cinque — la freccia, la radice del percorso, l'Indietro del telefono,
       Jenny che porta su la chat, un cambio di conversazione — e una
       guardia per strada e' una guardia che la sesta strada non avra'. E' la lezione di `_closeEditor` nel gestore file, dove
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
    if (view !== 'reader') {
      this._pageTrail = [];
      this._searchReturn = null;
    }
    this.view = view;
    this.shell?.setAttribute('data-view', view);
    /* Il polling dell'installazione non tiene sveglia una stanza che non c'e'
       piu'. Rientrando si riaggancia da se' (`open()`), perche' l'installazione
       intanto e' andata avanti per conto suo. */
    if (view !== 'updates') this.updatesRoom?.close();

    if (view !== 'board' && view !== 'drawer') this.boardSheet?.close();
    if (view === 'chat') {
      this._measureFloor?.();
      /* **Non** `_applyConversation`: la conversazione non e' cambiata, e
         quello rifaceva le traduzioni di tutta la casa e ridisegnava fila e
         Quaderni due volte a ogni ritorno. Chi cambia
         conversazione lo chiama da se' (`showConversation`); qui basta
         l'intestazione, in fondo. Il filo torna dove l'avevi lasciato, o
         in fondo (`restorePlace`): nascosto dietro una stanza puo' aver perso
         lo scroll. */
      this.chat.restorePlace?.();
      this.focus?.restore();
    } else {
      /* Niente composer, quindi il pavimento va dichiarato: senza, l'osservatore
         misurerebbe un elemento nascosto e lo troverebbe alto zero. */
      document.documentElement.style.setProperty(
        '--home-composer-h', `${FLOOR_NO_COMPOSER}px`,
      );
      this.input?.blur();
    }
    this._syncJennyPlace();
    this._applyHead();
    this._reportChatOnScreen();
    this.unseen?.refresh();
    this._savePlace();
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
     decide solo per le stanze. */
  _applyHead() {
    const notebook = projectNameOf(sessionManager.currentKey);
    /* La lente e l'elenco, solo sulla bacheca. L'interruttore Chat | Bacheca
       che stava accanto non c'e' piu' dal 05/10/2026: la chat del quaderno la
       porta su Jenny, sopra la bacheca. */
    if (this.boardAddBtn) this.boardAddBtn.hidden = this.view !== 'board';
    if (this.boardSearchBtn) this.boardSearchBtn.hidden = this.view !== 'board';
    if (this.boardListBtn) this.boardListBtn.hidden = this.view !== 'board';
    /* «Modifica» e' solo del lettore, e sparisce appena l'editor e' aperto: da
       li' i comandi sono Salva e Annulla, e stanno in basso. */
    if (this.editBtn) this.editBtn.hidden = this.view !== 'reader' || this.reader.editing;
    /* Uscendo dal lettore la selezione se ne va con la stanza, ma il
       `selectionchange` non e' garantito quando i nodi selezionati spariscono:
       la barra va chiusa qui, o resterebbe accesa sopra un'altra stanza. */
    this.audit?.refresh();
    if (this.view === 'board') this._setHeadTitle(notebook);
    if (this.view === 'drawer' && this._cardTitle) this._setHeadTitle(this._cardTitle);
    if (this.view === 'jenny') {
      this._setHeadTitle(i18n.t('home.jenny.title', { name: this._personalName || DEFAULT_BOT_NAME }));
    }
    if (this.view === 'model') this._setHeadTitle(i18n.t('home.model.title'));
    if (this.view === 'updates') this._setHeadTitle(i18n.t('home.updates.title'));
    if (this.view === 'backup') this._setHeadTitle(i18n.t('home.backup.title'));
    this._applyPath(notebook);
    this._applyBackLabel();
  }

  /* La radice del percorso, e il colore del quaderno quando la stanza e' sua.
     La radice dice **di che posto** e' la stanza, non da dove si e' venuti
     (quello lo sa la freccia): le pagine sono dei Quaderni, il lettore e' del
     quaderno, le altre stanze delle Impostazioni. */
  _applyPath(notebook) {
    const color = notebook ? dotColor(notebook) : null;
    const own = ['board', 'drawer', 'reader'].includes(this.view);
    let root = i18n.t('home.strip.settings');
    if (this.view === 'board') root = i18n.t('home.strip.notebooks');
    if (this.view === 'drawer') root = notebook || '';
    /* Il percorso della pagina dice da quale cassetto viene: «Acero › travaso
       acero». Sei sempre a un tocco dal riassunto e a due dalla bacheca. */
    if (this.view === 'reader') root = this._readerCard ? this._cardTitle || notebook || '' : notebook || '';
    if (this.pathRootName) this.pathRootName.textContent = root;
    this._paintDot(this.pathRootDot, this.view === 'drawer' || this.view === 'reader' ? color : null);
    this._paintDot(this.headDot, this.view === 'board' ? color : null);
    if (own && color) this.headEl?.style.setProperty('--path-line', color);
    else this.headEl?.style.removeProperty('--path-line');
  }

  _paintDot(el, color) {
    if (!el) return;
    el.hidden = !color;
    el.style.background = color || '';
  }

  /** Il tocco sulla radice del percorso: porta al posto di cui la stanza e'.
   *  Dal lettore e dalle stanze delle impostazioni e' anche dove porta la
   *  freccia; dalla bacheca porta **all'elenco** dei Quaderni (deciso con
   *  l'utente il 26/09/2026). */
  goToPathRoot() {
    if (this.view === 'reader' && this._readerCard) return this.goBackOneRoom();
    if (this.view === 'reader' || this.view === 'drawer') {
      if (this.view === 'reader' && !this._setView('board', () => this.goToPathRoot())) return true;
      this.openBoard();
      return true;
    }
    if (this.view !== 'board') return this.goBackOneRoom();
    if (!this._setView('chat', () => this.goToPathRoot())) return true;
    this.homePages?.goToId('notebooks', { animated: false });
    return true;
  }

  /* L'etichetta della freccia dice **dove si atterra**, non «indietro». Con
     tre stanze la differenza non si vedeva; con quattro, «torna alla chat»
     sopra il lettore era falso — di li' si torna alle pagine. La frase la
     sceglie la stessa tabella che decide il salto, quindi le due non possono
     divergere. Fino al 26/09/2026 era scritta a schermo, come occhiello. */
  _applyBackLabel() {
    this.backBtn?.setAttribute('aria-label', i18n.t(`home.back.${this._backLabelKey()}`));
  }

  /* Dove porta davvero la freccia. «Torna alla chat» da una stanza voleva
     dire «torna alla pista», che sta sulla pagina da cui sei entrato — i
     Quaderni, le App, una pagina in alto — e la frase mentiva a chi la
     sente col lettore di schermo (giro UX del 10/10/2026). */
  _backLabelKey() {
    const target = this._backTarget() || 'chat';
    if (target !== 'chat') return target;
    const entry = this._entry;
    if (!entry || entry.kind === 'chat') return 'chat';
    if (entry.id === 'notebooks') return 'notebooks';
    if (entry.id === 'app') return 'apps';
    if (entry.id === 'settings') return 'settings';
    return 'pinned';
  }

  /* La conversazione e' cambiata: le frasi del campo e del vuoto, la testa, e
     i Quaderni spostano la spunta. */
  _applyConversation() {
    /* Solo le frasi che cambiano con la conversazione, non le traduzioni di
       tutta la casa. */
    this._applyConversationTexts();
    this._applyHead();
    this.who?.render();
    this._reportChatOnScreen();
  }

  /* Il turno che stava girando nella conversazione lasciata non si chiudera'
     mai qui dentro: il suo `turn_end` arrivera' e verra' scartato. Si chiude a
     mano tutto cio' che lo stava aspettando. */
  _releaseTurn() {
    this.activity.stop();
    this._setRunning(false);
  }

  /* ── Il contratto col guscio nativo ── */

  /** Chiamato da MainActivity.hideLoading a dissolvenza finita.
   *
   *  In casa non aspetta niente: la coda di chi aspettava la pagina visibile
   *  era copiata dall'officina, dove la usa l'onboarding, e qui nessuno ci
   *  metteva niente. Il metodo resta perche' il
   *  guscio nativo lo chiama comunque, e un `undefined` sarebbe un TypeError
   *  dentro la sua `evaluateJavascript`. */
  onNativeReady() {
    // Niente da fare: v. sopra.
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

  /** Il cestino delle app, in fondo alla pagina App. */
  appsTrash() {
    return (this._appsTrash ||= new HomeAppsTrash(this.appsSource()));
  }

  /** Le azioni sulle voci. Il guscio le da' l'unica cosa che sa fare lui:
   *  mettere una richiesta nel composer e mandarla. */
  appsActions() {
    return (this._appActions ||= new AppsActions(this.appsSource(), {
      sendChatPrompt: (text) => this._sendInChat(text),
      keepsAppUnderChat: true,
      homePages: () => this.pagesPort(),
      onAppClosed: (slug) => {
        this.board?.refreshApp(slug);
        this._reportChatOnScreen();
      },
      /* Un'app aperta copre la chat: gli avvisi tornano a squillare. */
      onAppOpened: () => this._reportChatOnScreen(),
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
        return this.homePages.full ? 'full' : 'free';
      },
      append: async (kind, ref) => {
        this._closeAllOverlays();
        return this.homePages.append(kind, ref);
      },
      detach: (kind, ref) => this.homePages.detach(kind, ref),
      reload: () => this.homePages.reload(),
    });
  }

  /** Porta una richiesta gia' scritta dentro la conversazione personale, nel
   *  campo e **senza mandarla**: chi la legge ci aggiunge cosa vuole, poi la
   *  manda lui. Serve a «modifica questa app», a «parlane in chat» di un'app
   *  e all'app rotta.
   *
   *  **Sopra dove sei, nel velo** (dal 10/10/2026), come un avviso toccato:
   *  prima passava da Home, che chiudeva l'app, usciva dal quaderno e
   *  scriveva sopra la mezza frase che avevi nel campo. Ora il foglio da cui
   *  arriva si chiude, la chat sale con la personale e la bozza si mette da
   *  parte finche' non mandi (v. `_prefill`). */
  _sendInChat(text) {
    this._closeSheets();
    if (this.chatOver && sessionManager.currentKey !== sessionManager.personalKey) this.closeChatOver();
    if (this.chatOver) {
      this._prefill(text);
      return;
    }
    this.openChatOver({ key: sessionManager.personalKey, prefill: text });
  }

  /** Mette *text* nel campo **senza buttare la bozza**.
   *
   *  La bozza che c'era — se diversa — si mette da parte per la conversazione
   *  in cui e' scritta, e torna nel campo quando il messaggio pronto parte
   *  (`_send`), o resta nel campo se il gateway lo rifiuta. E' la regola di
   *  `prefillComposer` nell'officina (`test_prefill_keeps_the_draft_client`):
   *  una frase pronta non vale piu' di quel che stavi scrivendo. */
  _prefill(text) {
    if (!this.input) return;
    const draft = this.input.value;
    const key = sessionManager.currentKey;
    if (draft.trim() && draft.trim() !== String(text).trim() && !this._draftsAside.has(key)) {
      this._draftsAside.set(key, draft);
    }
    this.input.value = text;
    this.input.dispatchEvent(new Event('input', { bubbles: true }));
    /* Sincrono, dentro il tocco: e' l'unico modo perche' Android alzi la
       tastiera. Il cursore in fondo, dove si aggiunge. */
    this.input.focus();
    const end = this.input.value.length;
    try { this.input.setSelectionRange(end, end); } catch { /* campo senza selezione */ }
    this._autosize();
  }

  /** Il nome che l'elenco delle app da' a uno slug, se l'elenco e' gia' stato
   *  letto: la fila lo scrive sopra la pagina di un'app appesa. */
  appName(slug) {
    return this._apps?.jennyApps?.find?.((a) => a.slug === slug)?.name || null;
  }

  /** Il tasto Home di Android, quando Jenny e' il launcher — premuto **mentre
   *  la casa e' gia' davanti**. Quello che ti riporta qui da un'altra app non
   *  arriva: il guscio nativo lo filtra, e la casa ricompare dove l'avevi
   *  lasciata (v. `MainActivity.onNewIntent`). E' la regola dei launcher:
   *  il primo Home riporta a casa, il secondo alla pagina di partenza.
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
    // Prima di cambiare vista: v. `releaseSelectionOnBlur`.
    clearSelection();
    this._closeAllOverlays();
    /* Con l'editor modificato `_setView` chiede, e il resto di Home aspetta la
       risposta: cambiare conversazione sotto la conferma lasciava, con un no,
       il lettore di un quaderno sopra la chat personale. */
    if (!this._setView('chat', () => this.goHome())) return;
    this.switchConversation(null);
    // Gia' a casa, con un filo che non era arrivato: si riprova.
    this._retryThread();
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
    /* Poi le stanze, una per pressione: lettore, pagine, il quaderno aperto
       nei Quaderni, la pagina in cui sei. Una per pressione: dalle pagine un
       tocco solo non deve far sparire due cose, la stanza e il quaderno che la
       conteneva. Fino al 26/09/2026 c'era un ultimo gradino — dalla pagina
       chat, «esci dal quaderno» — che non c'e' piu': la pagina chat un
       quaderno non lo mostra mai. */
    this.goBackOneRoom();
  }

  /** Chiude cio' che sta sopra le pagine. Vero se c'era qualcosa. Uno strato
   *  per pressione, dall'alto: l'ordine qui sotto e' quello in cui stanno a
   *  schermo, non una scelta fra candidati.
   */
  _closeOverlays({ editor = true } = {}) {
    /* Prima di tutto i dialoghi condivisi, che si aprono sopra le schede e
       sopra le pagine. Si congedano con la semantica di Esc, come fa
       l'officina (`_dismissTopDialog`): l'evento `cancel`, annullabile, e
       `close()` solo se nessuno l'ha rifiutato — cosi' chi aspettava la
       risposta la riceve, ed e' un no. Mancavano: Indietro portava via la
       pagina sotto «Elimina quaderno?», e la domanda restava aperta sopra
       un'altra. */
    for (const id of SHARED_DIALOGS) {
      const dialog = document.getElementById(id);
      if (dialog?.open) {
        if (dialog.dispatchEvent(new Event('cancel', { cancelable: true }))) dialog.close();
        return true;
      }
    }
    /* L'immagine ingrandita: si apre anche *dalla* chat sopra la pagina, e le
       sta davanti (115 contro 112), quindi si chiude prima di lei. E dal
       14/10/2026 si apre anche *dal* foglio della bacheca (una foto della
       galleria), come dialog sopra di lui: per questo viene prima dei fogli.
       Prima Indietro chiudeva il foglio e lasciava aperta la foto. */
    const lightbox = document.querySelector('.image-lightbox');
    if (lightbox) {
      if (typeof lightbox.__jennyClose === 'function') lightbox.__jennyClose();
      else lightbox.remove();
      return true;
    }
    /* Poi i fogli che si aprono con una pressione lunga: quello di
       un quaderno **dai** Quaderni, e i due delle app **dal** cassetto (Open,
       Edit, Delete). Sono `<dialog>` con `showModal()`: stanno nel top layer,
       sopra tutto, e il loro commento in `apps-actions.js` lo dice — Indietro
       chiude prima loro. Visto sul telefono il 23/09/2026, quando mancavano:
       Indietro chiudeva il cassetto sotto e lasciava il foglio aperto sopra
       la chat, Delete compreso. */
    for (const id of [...LONG_PRESS_SHEETS, BOARD_SHEET]) {
      const sheet = document.getElementById(id);
      if (sheet?.open) {
        sheet.close();
        return true;
      }
    }
    /* La chat sopra la pagina (dal 05/10/2026, al posto della minichat): sta
       sopra le mini-app, perche' Jenny si apre anche li', quindi si chiude
       prima dell'app che le sta sotto. */
    if (this.chatOver) return this.closeChatOver();
    /* L'editor di una pagina: Indietro chiude **lui**, e resta sulla pagina
       (dal 10/10/2026). Prima Indietro usciva dal lettore — in silenzio con
       l'editor intonso, e con la conferma che, detto si', buttava le
       modifiche *e* usciva: due cose per una pressione. Se dentro c'e' del
       lavoro chiede, come il suo Annulla. */
    if (editor && this.view === 'reader' && this.reader?.editing) {
      this.reader.askCancel();
      return true;
    }
    /* Poi un'app aperta a tutto schermo dal cassetto. Fino al 23/09/2026 la
       casa non la chiudeva mai: Indietro agiva su quel che c'era **sotto**, e
       l'app restava li'. Con la pagina App diventata la strada principale per
       aprirle, e' il livello che si incontra piu' spesso. `handleBack` e' dell'
       app: una sua schermata interna torna indietro dentro di lei, prima. */
    if (this._appActions?.handleBack()) return true;
    /* La barra di una scheda tenuta premuta: Indietro la chiude (e spegne i
       fili) prima di lasciare la bacheca. */
    if (this.view === 'board' && this.board?.closeCardBar?.()) return true;
    /* Il ventaglio del «+» nel cassetto: Indietro lo chiude, e si resta li'. */
    if (this.view === 'drawer' && this.drawer?.closeDial?.()) return true;
    /* La modalita' ordina: ogni uscita **salva**, come nei launcher (scelta
       dell'utente, 10/10/2026). Fino ad allora Indietro e Home uscivano
       senza salvare, e il nuovo ordine spariva senza dirlo. Se la scrittura
       non riesce si resta dentro con la bozza intatta (`closeSort`), e la
       pista lo dice. */
    if (this.strip?.sorting) {
      this.strip.closeSort({ save: true });
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
    /* Senza l'editor: chi chiude tutto cambia anche stanza, e la domanda sul
       lavoro non salvato la fa `_setView` — una volta, non otto. */
    for (let i = 0; i < 8 && this._closeOverlays({ editor: false }); i += 1) { /* avanti */ }
  }

  /** Il tocco su un avviso proattivo, o sulla mascotte flottante: porta il
   *  messaggio davanti **senza toglierti il posto**.
   *
   *  **La conversazione personale**, e non quella che stavi guardando: la
   *  copia websocket di un avviso proattivo va sempre li' (il fan-out di
   *  `runtime/delivery.py` ce la mette d'ufficio), quindi dentro un quaderno
   *  quell'avviso non c'e'.
   *
   *  **Sopra la pagina, nel velo**, e non al suo posto (dal 10/10/2026). Fino
   *  ad allora l'avviso era un indirizzo come Home: chiudeva l'app aperta,
   *  usciva dal quaderno e tornava alla pagina chat — e chi leggeva una
   *  pagina della wiki, toccata la notifica, perdeva la pagina. L'avviso
   *  dice «Jenny ti ha scritto», non «smetti quel che fai»: si legge, si
   *  risponde, e Indietro (o Jenny) riporta esattamente dov'eri — la stessa
   *  porta che apre Jenny toccata, con la chat di sempre al posto di quella
   *  del quaderno. Sulla pagina chat, dove il velo non serve, si cambia
   *  conversazione li'.
   *
   *  I fogli di un momento (una pressione lunga, la bacheca, «Segnala»,
   *  l'immagine ingrandita) si chiudono: starebbero sopra il velo. Una domanda
   *  aperta no — e' una risposta che qualcuno aspetta — e allora il velo non
   *  sale, il risultato e' falso e l'avviso resta in tendina.
   *
   *  **L'avviso di un quaderno porta nel quaderno** (*key* =
   *  `project:<nome>`, dal tag della notifica: v. `AlertConversation.kt`).
   *  Fino al 10/10/2026 anche quello apriva la personale, dove la risposta
   *  del quaderno non c'e'. V. `_openNotebookChat`.
   */
  openChat(key = null) {
    const notebook = projectNameOf(key);
    if (notebook && isOpenableProjectName(notebook)) return this._openNotebookChat(notebook);
    this._closeSheets();
    if (this.isChatOnScreen()) {
      this._retryThread();
      this.chat.scrollToBottom();
      return true;
    }
    if (this.view === 'chat' && this._entry?.kind === 'chat' && !this.hasOverlayAbove()) {
      this.switchConversation(null);
      this._retryThread();
      this.chat.scrollToBottom();
      return true;
    }
    /* Il velo e' gia' su con la chat di un quaderno: scende e risale con la
       personale, che e' dove sta l'avviso. */
    if (this.chatOver) this.closeChatOver();
    if (!this.openChatOver({ key: sessionManager.personalKey })) return false;
    this.chat.scrollToBottom();
    return true;
  }

  /** Il tocco sull'avviso di un quaderno: la sua bacheca sotto e la sua chat
   *  sopra, come quando il quaderno nasce (`createNotebook`). Indietro
   *  riporta alla bacheca.
   *
   *  **Si entra nel quaderno**, e non si apre la sua chat sopra la pagina in
   *  cui eri: il velo, richiudendosi, riporterebbe la chat a casa con la
   *  chiave del quaderno, e la pagina chat finirebbe a mostrarlo. Se sei
   *  gia' in una stanza di quel quaderno, sale solo il velo.
   *
   *  Torna vero **subito**, prima di aver finito: il guscio nativo chiede da
   *  `evaluateJavascript`, che non aspetta una Promise, e senza un vero
   *  sincrono gli avvisi non si cancellerebbero mai. Una domanda aperta, come
   *  per la personale, ferma tutto e lascia l'avviso. Un quaderno che non
   *  c'e' piu' (cancellato con la notifica in tendina) ripiega sulla
   *  personale. */
  _openNotebookChat(name) {
    const key = projectKey(name);
    this._closeSheets();
    if (document.querySelector('dialog[open]')) {
      this.jenny?.nudge?.();
      return false;
    }
    if (this.chatOver && sessionManager.currentKey === key) {
      this._retryThread();
      this.chat.scrollToBottom();
      return true;
    }
    if (this.chatOver) this.closeChatOver();
    this._enterNotebookChat(name).catch((err) => {
      console.warn('home: notebook alert not opened', err);
    });
    return true;
  }

  async _enterNotebookChat(name) {
    const key = projectKey(name);
    if (!(await this._notebookExists(name))) {
      this.openChat(null);
      return;
    }
    if (NOTEBOOK_ROOMS.includes(this.view) && sessionManager.currentKey === key) {
      this._closeAllOverlays();
    } else {
      await this.openNotebook(name);
    }
    if (sessionManager.currentKey !== key) return;   // nel frattempo ti sei mosso
    if (this.openChatOver({ key })) this.chat.scrollToBottom();
  }

  /* C'e' ancora? Nel dubbio — la lettura non e' riuscita — si', e si prova:
     l'errore da evitare e' mandare nella personale chi ha un quaderno vero. */
  async _notebookExists(name) {
    try {
      const projects = (await api.listProjects())?.projects;
      return !Array.isArray(projects) || projects.some((p) => p?.name === name);
    } catch {
      return true;
    }
  }

  /* Chiude i fogli di un momento, tutti: quelli di una pressione lunga, il
     foglio della bacheca, «Segnala» e l'immagine ingrandita. Non i dialoghi
     condivisi, che sono domande; non l'app aperta, che il velo copre e
     ritrova. */
  _closeSheets() {
    for (const id of [...LONG_PRESS_SHEETS, BOARD_SHEET, REPORT_SHEET]) {
      const sheet = document.getElementById(id);
      if (sheet?.open) sheet.close();
    }
    const lightbox = document.querySelector('.image-lightbox');
    if (!lightbox) return;
    if (typeof lightbox.__jennyClose === 'function') lightbox.__jennyClose();
    else lightbox.remove();
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
    return this.chatOnScreenKey() === 'personal';
  }

  /** **Quale** chat e' a schermo: `'personal'`, `'project:<nome>'`, o `''`.
   *
   *  La domanda di `isChatOnScreen` estesa ai quaderni: gli avvisi di un
   *  quaderno (le sue risposte, i suoi cron) si cancellano quando e' la sua
   *  chat ad arrivare a schermo, e un suo cron tace con quella davanti. Lo
   *  chiede `CHAT_ON_SCREEN_JS` al rientro, e lo riporta
   *  `_reportChatOnScreen` a ogni cambio. */
  chatOnScreenKey() {
    const key = sessionManager.currentKey;
    /* La chat sopra la pagina e' a schermo davanti a tutto, a parte un foglio
       che le si apre sopra. */
    const visible = this.chatOver
      ? !this._sheetOpen()
      : this.view === 'chat'
        && this._entry?.kind === 'chat'
        /* Un'app aperta, una scheda, una domanda condivisa o la modalita'
           ordina la coprono: sotto c'e', ma non la stai guardando. */
        && !this.hasOverlayAbove();
    if (!visible) return '';
    if (key === sessionManager.personalKey) return 'personal';
    return projectNameOf(key) ? key : '';
  }

  /* Il secondo dei tre modi in cui la chat arriva a schermo (v.
     `NotifierBridge.clearAlerts`): un cambio di pagina o di conversazione dentro
     la WebView, che il guscio nativo non puo' vedere da se'. Si chiama su ogni
     occasione e decide qui; l'officina lo fa da `ChatController.activate`.

     E anche il contrario, dal 10/10/2026: quando la chat esce di scena il
     guscio lo deve sapere, perche' un avviso con Jenny davanti si sopprime
     solo se la chat e' davvero a schermo (`MainActivity.isChatOnScreen`).
     Prima si sopprimeva su «app in primo piano», e Jenny — il launcher — lo e'
     quasi sempre: i promemoria arrivavano muti sulla pagina App o su una
     bacheca. `chatOpened` va a ogni occasione (cancella gli avvisi letti);
     `chatClosed` solo quando cambia, o al primo giro della pagina. */
  _reportChatOnScreen() {
    /* A meta' di `openNotebook`, o mentre la casa riprende il posto di prima
       all'avvio: in entrambi i casi si passa dalla pagina chat personale senza
       restarci, e detto al guscio quel passaggio cancellava gli avvisi
       personali non letti (misurato sull'emulatore il 10/10/2026). */
    if (this._holdChatReport || this._placeHeld) return;
    const on = this.chatOnScreenKey();
    if (!on && this._chatReported === '') return;
    this._chatReported = on;
    try {
      /* La personale senza argomento, come l'officina; un quaderno col suo
         nome, perche' il guscio cancelli gli avvisi di quello. */
      if (on === 'personal') window.JennyNative?.chatOpened?.();
      else if (on) window.JennyNative?.chatOpened?.(on);
      else window.JennyNative?.chatClosed?.();
    } catch { /* nessun guscio nativo */ }
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

    /* Ferma solo a campo vuoto. Con qualcosa da mandare il tasto manda, come
       l'Invio della tastiera: il gateway lo inietta nel turno in corso. Prima
       il tocco fermava Jenny e lasciava la precisazione nel campo — lo stesso
       testo faceva due cose diverse secondo il gesto (giro UX del 10/10/2026). */
    this.send.addEventListener('click', () => {
      if (this._running && !this._hasDraft()) this._stop();
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
      if (!this.chatOver) {
        if (this.view !== 'chat') return;
        /* ...e solo sulle pagine che un composer ce l'hanno. Le altre il loro
           pavimento lo dichiarano (v. `_placeJenny`). Con la chat sopra la
           pagina il composer c'e' sempre. */
        if (this._entry && !this._hasComposer(this._entry)) return;
      }
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
      for (const el of [this.composer, this.pending, this.wire]) {
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
    /* Ogni cambio del campo passa di qui: il tasto dice se manda o ferma. */
    this._paintSend();
  }

  /* C'e' qualcosa da mandare: testo o un allegato. */
  _hasDraft() {
    return Boolean(this.input?.value.trim()) || Boolean(this.files?.count);
  }

  /* Gli allegati in attesa: miniature per le immagini, una pastiglia col nome
     per tutto il resto. Ognuno si toglie da solo con la sua X — senza, l'unico
     modo di disfare uno sbaglio sarebbe mandare il messaggio. */
  _renderPending() {
    this._paintSend();
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
      x.innerHTML = '<i class="ti ti-x" aria-hidden="true"></i>';
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
    if (!this._sendText(text, this.files.getImages(), this.files.getAttachmentEntries())) {
      return false;
    }
    this.files.clear();
    /* Era una frase pronta: la bozza messa da parte torna nel campo. */
    const key = sessionManager.currentKey;
    const aside = this._draftsAside.get(key);
    this._draftsAside.delete(key);
    this.input.value = aside || '';
    /* Se il gateway la rifiuta, la frase deve poter tornare al posto della
       bozza (v. `onSendRejected`). */
    this._restoredDraft = aside ? { key, text: aside } : null;
    this._autosize();
    return true;
  }

  /* Manda e disegna la bolla. Ci passano il composer, con i suoi allegati, e i
     comandi delle bolle di lavoro (`sendCommand`). */
  _sendText(text, media = [], entries = []) {
    if (!wsManager.sendToChat(sessionManager.currentChatId, text, media)) {
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
        /* I nomi degli strumenti che partono. Con `sendToolHints` spento, cioe'
           di default, arrivano senza il testo del suggerimento, come un
           `progress` (v. `WebSocketDispatcher._tool_start_without_hint`). */
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

  /* Il tocco lungo sulla riga o sul chip degli agenti: la stessa conversazione,
     in officina, con tutto quello che la casa non mostra. `api.navigate` e non
     `location.href` perche' il segreto di bootstrap vive solo nella memoria di
     questa pagina: una navigazione secca lo perderebbe e l'officina prenderebbe
     401.

     `key` e' la conversazione da aprire. Un quaderno viaggia nel frammento come
     `chat=project:<nome>`, e l'officina lo legge al boot (v.
     `conversationFromFragment` in `mobile-app.js`). La personale non viaggia:
     e' gia' quella da cui l'officina parte. La porta delle Impostazioni non
     passa nessuna chiave, e porta sempre alla personale — da li' non stai
     guardando un quaderno.

     Il frammento `#turn=` invece l'officina non lo legge ancora: non fa danno,
     e quando lo leggera' da questa parte non c'e' niente da cambiare. */
  _openInWorkshop(turnId, key = null) {
    const fragment = new URLSearchParams();
    if (turnId) fragment.set('turn', turnId);
    const name = projectNameOf(key);
    if (name !== null && isOpenableProjectName(name)) fragment.set('chat', key);
    const rest = fragment.toString();
    api.navigate(`/html-mobile/workshop.html${rest ? `#${rest}` : ''}`);
  }

  /* Lo snapshot dei subagent, se e' della conversazione a schermo. */
  _readSubagents(msg) {
    if (msg?.event !== 'subagent_status' || !this._frameIsHere(msg)) return;
    this.work.ingestSubagents(msg);
  }

  /* Lo stato di un lavoro lungo (giardiniere, Dream), se e' di questa chat. */
  _readWork(msg) {
    if (msg?.event !== 'work_status' || !this._frameIsHere(msg)) return;
    this.work.ingestWork(msg);
  }

  /* `board_changed`: una bacheca e' cambiata fuori dalla bacheca (l'editore
     lanciato dalla chat, la passata del giardiniere). Non e' di una
     conversazione: vale per chiunque abbia aperta quella bacheca. */
  _readBoardChanged(msg) {
    if (msg?.event !== 'board_changed' || !msg.notebook) return;
    this.board?.refreshNotebook(msg.notebook);
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
    this._paintSend();
  }

  /* Il tasto tondo: Ferma mentre Jenny lavora **e** il campo e' vuoto, manda
     in ogni altro caso (v. il suo click). */
  _paintSend() {
    if (!this.send) return;
    const stop = Boolean(this._running) && !this._hasDraft();
    if (this._sendShowsStop === stop) return;
    this._sendShowsStop = stop;
    this.send.classList.toggle('is-stop', stop);
    this.send.innerHTML = stop
      ? '<i class="ti ti-player-stop-filled" aria-hidden="true"></i>'
      : '<i class="ti ti-arrow-up" aria-hidden="true"></i>';
    this.send.setAttribute('aria-label', i18n.t(stop ? 'home.stop' : 'home.send'));
  }

  /* ── Lo stato del filo ── */

  /** Il socket si e' aperto. La prima volta e' l'avvio, e il filo lo legge
   *  `init()`; da li' in poi e' una **riconnessione**, e quel che c'e' a
   *  schermo puo' essere rimasto indietro. */
  _onWireOpen() {
    this._setWire(true);
    /* Chi lavora si rilegge a ogni apertura, la prima compresa: le
       transizioni avvenute a socket chiuso non le ridice nessuno. */
    this.work.loadSubagents();
    this.work.loadWork();
    this.staged?.refresh();
    /* Al boot le impostazioni non si leggevano, quindi non si sa se questo e'
       il primo avvio: il socket aperto dice che il gateway adesso c'e', e si
       richiede. Una volta sola — un secondo «non lo so» resta nella casa. */
    if (this._firstRunUnknown) {
      this._firstRunUnknown = false;
      this._goToOnboardingIfFirstRun({ fresh: true, retry: false });
    }
    const first = !this._wireOpenedOnce;
    this._wireOpenedOnce = true;
    if (first && !this._threadFailed) return;
    this._resyncAfterReconnect();
  }

  /** Dopo una riconnessione: si lascia il turno e si rilegge il filo.
   *
   *  **Il turno.** Se il gateway e' ripartito a meta' risposta, quel turno e'
   *  morto con lui e nessuno mandera' il suo `turn_end`: il bottone Ferma
   *  restava acceso, la riga di lavoro e il fiore giravano, e Jenny restava a
   *  pensare. Lo si lascia qui, come a un cambio di conversazione. Se invece
   *  il turno e' vivo, il gateway lo ridice lui: `ws-manager` emette
   *  `chat:open` **prima** di rifare gli `attach`, e la risposta a un `attach`
   *  rimanda `goal_status: running` quando un turno gira — che riaccende tutto.
   *
   *  **Il filo.** Una caduta lascia una risposta tronca, o un messaggio
   *  arrivato mentre il socket era giu' che non arrivera' piu'. Si butta e si
   *  rilegge, come fa l'officina (`_resyncThreadAfterReconnect`) e per le
   *  stesse ragioni: gli id del thread non sono un'ancora, quindi non c'e'
   *  niente con cui riconciliare. E, come la', **solo se si era in fondo**:
   *  chi sta rileggendo piu' su perderebbe le pagine che guarda per un
   *  messaggio in coda che non sta guardando. Un filo che non e' mai arrivato
   *  si riprova sempre: la riconnessione e' il segno che il gateway c'e'. */
  async _resyncAfterReconnect() {
    this._releaseTurn();
    this.jenny?._releaseTrackedTurn?.();
    if (this._threadReads > 0) return;
    if (!this._threadFailed && !this.chat.following) return;
    await this._readThread();
  }

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

  /* Tre frasi cambiano con la conversazione, e cambiano insieme: dentro un
     quaderno il vuoto non dice «comincia tu» ma che quella conversazione non
     c'e' ancora *e resta li'*, che e' l'unico punto in cui si puo' dire senza
     spiegarlo che questa e' un'altra stanza. */
  _applyConversationTexts() {
    const inNotebook = !!projectNameOf(sessionManager.currentKey);
    if (this.emptyText) {
      const empty = inNotebook ? 'home.emptyNotebook' : 'home.empty';
      this.emptyText.textContent = i18n.t(this._threadFailed ? 'home.threadError' : empty);
    }
    if (this.input) {
      /* Il nome di lei, non «Jenny»: rinominata, il campo diceva ancora di
         scrivere a qualcun altro (collaudo del 27/09/2026). */
      this.input.placeholder = i18n.t(inNotebook ? 'home.placeholderNotebook' : 'home.placeholder', {
        name: this._personalName || DEFAULT_BOT_NAME,
      });
    }
    this._applyOverTitle();
  }

  /* Il titolo del velo: dove scrivi, nella lingua della fila. In un quaderno
     il suo nome col pallino, e la riga del suo colore; nella chat di sempre il
     nome di lei con la riga d'accento, com'e' nella fila quando ci sei. Segue
     la conversazione e non il velo: se ne cambia una mentre e' aperto, la
     riga dice gia' quella nuova. */
  _applyOverTitle() {
    if (!this.overName) return;
    const notebook = projectNameOf(sessionManager.currentKey);
    const color = notebook ? dotColor(notebook) : null;
    this.overName.textContent = notebook || this._personalName || DEFAULT_BOT_NAME;
    this._paintDot(this.overDot, color);
    if (color) this.overTitle?.style.setProperty('--path-line', color);
    else this.overTitle?.style.removeProperty('--path-line');
  }

  /** I nomi dei tre comandi della bacheca in testa, che sono solo icone. */
  _labelBoardTools() {
    this.boardAddBtn?.setAttribute('aria-label', i18n.t('board.card.add'));
    this.boardSearchBtn?.setAttribute('aria-label', i18n.t('board.search.title'));
    this.boardListBtn?.setAttribute('aria-label', i18n.t('board.listView'));
  }

  _applyTranslations() {
    this._applyConversationTexts();
    if (this.send) {
      this.send.setAttribute('aria-label', i18n.t(this._running ? 'home.stop' : 'home.send'));
    }
    if (this.attach) this.attach.setAttribute('aria-label', i18n.t('home.attach'));
    this._applyBackLabel();
    this._labelBoardTools();
    this.over?.host?.setAttribute('aria-label', i18n.t('home.over.label'));
    this.pathEl?.setAttribute('aria-label', i18n.t('home.path.label'));
    if (this.editLabel) this.editLabel.textContent = i18n.t('home.reader.edit');
    this.reader?.applyTranslations();
    this.audit?.applyTranslations();
    this.you?.applyTranslations();
    this.modelRoom?.applyTranslations();
    this.updatesRoom?.applyTranslations();
    this.backupRoom?.applyTranslations();
    this._appsTrash?.applyTranslations();
    this.staged?.applyTranslations();
    this.jennyRoom?.applyTranslations();
    this.you?.sayJenny(this.jennyRoom?.value());
    this.newNotebook?.setAttribute('aria-label', i18n.t('home.who.newNotebook'));
    this.board?.applyTranslations();
    this.drawer?.applyTranslations();
    // La pagina Quaderni ha le sue righe gia' disegnate: vanno riscritte.
    this.who?.render();
    this.strip?.draw();
    if (this.files?.count) this._renderPending();
    if (this.wire && !this.wire.hidden) this.wire.textContent = i18n.t('home.wire.offline');
    this.work?.applyTranslations();
  }

}

new HomeApp();
