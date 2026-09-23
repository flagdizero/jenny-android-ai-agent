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
 *  **Il contratto col guscio nativo e' di cinque metodi.** Android chiama
 *  `window.mobileApp.onNativeReady()`, `.goHome()`, `.onPackageChanged()`,
 *  `.handleHardwareBack()` e `.openChat()`. Erano tre in questo commento, e il
 *  conto era sbagliato: le due che mancavano non si trovano cercando
 *  `window.mobileApp.<nome>` perche' il Kotlin le invoca su una variabile
 *  locale (`var app = window.mobileApp; … app.handleHardwareBack()`). Il ponte
 *  verso il JS si conta sulle **chiamate**, non sul nome dell'oggetto.
 *
 *  Il nome globale resta `mobileApp` apposta: cosi' il guscio nativo non sa, e
 *  non deve sapere, quale delle due interfacce ha caricato.
 */

import { ActivityLine } from './casa-activity.js';
import { CasaChat } from './casa-chat.js';
import { CasaMascot } from './casa-mascot.js';
import { CasaPages } from './casa-pages.js';
import { CasaReader } from './casa-reader.js';
import { CasaAudit, messaggioSegnalazione } from './casa-audit.js';
import { CasaJenny } from './casa-jenny.js';
import { CasaModel } from './casa-model.js';
import { CasaUpdates } from './casa-updates.js';
import { CasaBackup } from './casa-backup.js';
import { CasaTu } from './casa-tu.js';
import { WhoPanel, dotColor } from './casa-who.js';
/* Il cassetto delle app, **preso dall'officina e non ricopiato**: e' lo stesso
   modulo per i due gusci. I dati e le azioni stanno in `shared/`, fuori da
   qualunque schermata — ci sono usciti il 21/09/2026, quando la scheda «App»
   che li ospitava e' stata cancellata. */
import { JennyGap } from './shared/jenny-gap.js';
import { LauncherController } from './mobile-launcher.js';
import { CasaPagine } from './casa-pagine.js';
import { SchedaQuaderno } from './casa-quaderno.js';
import { Trasloco } from './casa-trasloco.js';
import { AppsSource } from './shared/apps-source.js';
import { AppsActions } from './shared/apps-actions.js';
import { isOpenableProjectName, projectKey, projectNameOf } from './shared/conversation-list.js';
import { PROJECT_WORDS, createProjectFlow } from './shared/project-create.js';
import { deleteProjectFlow } from './shared/project-delete.js';
import { showToast } from './shared/utils.js';
import { api } from './shared/api-client.js';
import { ImageHandler } from './shared/image-handler.js';
import { setupLongPress } from './shared/longpress.js';
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

/* Le stanze oltre la chat, e dove si atterra premendo Indietro una volta.
   La catena e' lineare e sta **in un posto solo**: `_setView` la usa per
   sapere quali nomi esistono, `goBackOneRoom` per percorrerla e l'occhiello
   per scriverci sopra dove porta. Erano tre `if` in fila finche' le stanze
   erano tre; con cinque, l'occhiello diceva «torna alla chat» anche dal
   lettore, che torna alle pagine. Aggiungere una stanza e' una riga qui. */
const BACK_TO = {
  pages: 'chat',
  reader: 'pages',
  tu: 'chat',
  jenny: 'tu',
  model: 'tu',
  updates: 'tu',
  backup: 'tu',
};

/* Le stesse domande dell'officina, dette come si dicono in casa.
 *
 *  Il giro di creazione e' uno solo (`shared/project-create.js`) e non sa come
 *  si chiami quel che sta creando: prende le chiavi da qui. Si sovrascrivono
 *  **solo** le frasi che dicono «progetto» — invalidName no, perche' la regola
 *  dei nomi e' una e citarla due volte vorrebbe dire tenerne due allineate.
 */
const NOTEBOOK_WORDS = {
  ...PROJECT_WORDS,
  namePrompt: 'casa.who.create.name',
  nameTaken: 'casa.who.create.taken',
  seedRequired: 'casa.who.create.seedRequired',
  created: 'casa.who.create.created',
  wikiOff: 'casa.who.create.wikiOff',
  rejected: 'casa.who.create.rejected',
  leftoverBody: 'casa.who.create.leftover',
  leftoverBodyNoCount: 'casa.who.create.leftoverNoCount',
};

/* ...e quelle della cancellazione, per la stessa ragione: il giro e' uno
   (`shared/project-delete.js`), e in casa quel che si cancella e' un quaderno. */
const NOTEBOOK_DELETE_WORDS = {
  confirm: 'casa.quaderno.deleteConfirm',
  confirmWithChat: 'casa.quaderno.deleteConfirmWithChat',
  failed: 'casa.quaderno.deleteFailed',
};

class CasaApp {
  constructor() {
    this.thread = document.getElementById('casa-thread');
    this.chat = new CasaChat(this.thread);
    this.jenny = new CasaMascot(document.querySelector('.casa-shell'));
    /* Il margine che i messaggi lasciano a Jenny, **solo dove lei c'e'**. Si
       consegna alla chat dopo la mascotte perche' le serve il suo nodo vero:
       la banda da scansare si misura su di lei, non su dei numeri copiati —
       cosi' vale anche quando cambia taglia, quando la metti via sul bordo e
       quando la trascini dall'altra parte. */
    this.chat.gap = new JennyGap(this.thread, this.jenny.el);
    this.activity = new ActivityLine(document.getElementById('casa-activity'), {
      onOpenInWorkshop: (turnId) => this._openInWorkshop(turnId),
    });
    this.empty = document.getElementById('casa-empty');
    this.emptyText = document.getElementById('casa-empty-text');
    this.wire = document.getElementById('casa-wire');
    this.input = document.getElementById('casa-input');
    this.send = document.getElementById('casa-send');
    this.attach = document.getElementById('casa-attach');
    this.door = document.getElementById('casa-door');
    this.kicker = document.getElementById('casa-kicker');
    this.pending = document.getElementById('casa-pending');
    this.shell = document.querySelector('.casa-shell');

    /* I comandi dell'intestazione che cambiano con la stanza. */
    this.pagesBtn = document.getElementById('casa-pages-open');
    this.pagesCount = document.getElementById('casa-pages-count');
    this.backBtn = document.getElementById('casa-back');
    this.backLabel = document.getElementById('casa-back-label');
    this.talkBtn = document.getElementById('casa-talk');
    this.editBtn = document.getElementById('casa-edit');
    this.talkLabel = document.getElementById('casa-talk-label');

    /* «Tu e Jenny»: le impostazioni di chi la usa. La porta dell'officina vive
       qui dentro, in fondo, e resta sotto il tocco lungo sull'avatar — che e'
       l'unica cosa di quella stanza che questo guscio si tiene, perche' e' lui
       a sapere come si apre l'officina. */
    this.tu = new CasaTu({
      onWorkshop: () => this._openInWorkshop(null),
      onJenny: () => this.openJenny(),
      onModel: () => this.openModel(),
      onUpdates: () => this.openUpdates(),
      onBackup: () => this.openBackup(),
    });
    /* `jennyRoom` e non `jenny`: quella e' lei, lo sprite che cammina sul
       bordo. Questa e' la stanza che dice com'e' fatta. */
    this.jennyRoom = new CasaJenny({ onChange: () => this.tu.sayJenny(this.jennyRoom.value()) });
    /* Chi risponde. Un salvataggio li' dentro torna col payload intero di
       `/api/settings`: lo si rimette nella cache invece di richiederlo, o la
       riga di «Tu e Jenny» resterebbe sulla marca di prima. */
    this.modelRoom = new CasaModel({ onSettings: (data) => this._keepSettings(data) });
    /* Gli aggiornamenti: la seconda vista di `shared/update-flow.js`, di cui
       l'officina e' la prima. Un controllo riuscito porta una versione fresca,
       e quella deve riscrivere la riga **e** la cache del guscio. */
    this.updatesRoom = new CasaUpdates({
      onVersion: (version) => this._keepVersion(version),
    });
    /* Il backup. La terza vista di `shared/backup-flow.js`; l'unica cosa nuova
       e' la data, che prima non esisteva da nessuna parte. */
    this.backupRoom = new CasaBackup({
      onExported: () => this.tu.sayBackup(this.backupRoom.value()),
    });

    /* Le altre due stanze. La mappa non si importa: si carica al primo tocco
       sulla sua linguetta insieme ai 280 kB di D3 (v. `casa-map.js`), e un
       `import` statico la pagherebbe a ogni avvio della casa. */
    this.pages = new CasaPages({
      onOpenPage: (path, label) => this.openPage(path, label),
      onNeedMap: (data, _rows, quaderno) => this._drawMap(data, quaderno),
    });
    this.reader = new CasaReader();
    this.reader.onTitle = (title) => this._setHeadTitle(title);
    this.audit = new CasaAudit(this.reader);
    this.audit.onFiled = (segnalazione) => this._portaInChat(segnalazione);
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

    /* Il nome della conversazione personale, messo da parte **prima** che il
       titolo cominci a cambiare. Lo prendeva dal titolo, ed era giusto finche'
       li' c'era sempre lo stesso nome: da quando il titolo porta il quaderno
       aperto, la riga personale del pannello direbbe «piante». */
    this.nameEl = document.querySelector('.casa-who-name');
    this.dotEl = document.getElementById('casa-who-dot');
    this._personalName = this.nameEl?.textContent?.trim() || 'Jenny';

    /* Le bozze, una per conversazione. Senza, mezza frase scritta in casa
       partirebbe dentro il quaderno che apri subito dopo: e' la stessa famiglia
       di guasto di cui parla `switchGeneration` — quel che dici finisce nel
       diario di un altro progetto — solo un attimo prima. In memoria e basta:
       una bozza non e' una cosa da conservare fra due avvii. */
    this._drafts = new Map();

    /* «Con chi parli»: il titolo apre la tendina delle conversazioni. Il
       pannello non sa cosa sia una chiave di sessione — dice quale nome hai
       toccato, e la conversazione la apre questo guscio. */
    this.who = new WhoPanel(document.getElementById('casa-who'), {
      head: document.querySelector('.casa-head'),
      personalName: () => this._personalName,
      currentProject: () => projectNameOf(sessionManager.currentKey),
      onPick: (name) => this.switchConversation(name ? projectKey(name) : null),
      onCreate: () => this.createNotebook(),
      onHold: (name) => this.schedaQuaderno().mostra(name),
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

    /* Il cassetto: costruito subito perche' il suo markup e' statico, come in
       officina. I **dati** invece no — sono due fetch, e quella delle app
       Android ricodifica ogni icona in base64: si agganciano alla prima
       apertura (v. `appsSource()`), che e' la stessa scelta che il cassetto fa
       gia' di suo in `_attachSource()`. */
    this.launcher = new LauncherController(this);
    /* La pista delle pagine. Si costruisce subito — il gesto va agganciato
       prima che un dito possa arrivarci — e si riempie dopo, quando il filo e'
       a schermo: l'elenco e' una lettura di rete, e farla aspettare dalla
       chat vorrebbe dire una casa vuota per il tempo di un giro. */
    /* La chat e' una, e si sposta nella pagina di un quaderno quando ci
       arrivi: il trasloco sa spostarla e fotografarla, questo guscio sa
       cambiarle conversazione. Prima della pista, che lo usa dal primo
       `vaiA`. */
    this.trasloco = new Trasloco({
      chat: document.getElementById('casa-chat'),
      cambia: (chiave) => this.mostraConversazione(chiave),
      chiaveAttuale: () => sessionManager.currentKey,
      /* In fondo **senza condizioni**, non `keepBottom`: spostata nel
         documento la chat riparte da scroll 0, e `keepBottom` segue il fondo
         solo «se ci si era» — lo scroll azzerato puo' fargli credere che
         l'utente sia risalito. E la foto che e' appena entrata mostrava il
         fondo: arrivare altrove sarebbe il trucco che si vede. */
      inFondo: () => this.chat?.scrollToBottom(),
    });
    this.pagine = new CasaPagine(this);
    this._apps = null;

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
      api.clientLog('error', 'casa.bootstrap', String(err && err.stack || err));
    }

    /* Le traduzioni prima della storia: la conversazione porta etichette
       tradotte (la provenienza di un messaggio entrato da fuori), e disegnarla
       prima vorrebbe dire scriverci dentro le chiavi grezze. */
    await i18n.load(i18n.locale);
    this._applyTranslations();
    i18n.onLocaleChange(() => this._applyTranslations());

    /* Quel che apparteneva alla conversazione lasciata scade qui. Il `turn_end`
       del turno in volo arrivera' a una chat che non guardiamo piu' e verra'
       scartato da `_belongsHere`: chi tiene stato *per turno* — la faccia di
       Jenny, la riga di lavoro, il bottone Ferma — resterebbe ad aspettarlo per
       sempre. In officina lo stesso evento serve alla stessa cosa
       (`mobile-jenny.js`, `_releaseTrackedTurn`). */
    sessionManager.addEventListener('chat:switch', () => this._releaseTurn());
    /* E riporta nella conversazione. Le pagine parlano di *un* quaderno: dalla
       tendina si puo' saltare in un altro, e restare li' vorrebbe dire leggere
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
    /* Un messaggio rifiutato dal gateway torna nel campo, così puoi correggere
       invece di riscrivere — a meno che tu non abbia già ricominciato a
       scrivere: quello vince sempre, non si sovrascrive del testo vivo con del
       testo vecchio. */
    this.chat.onSendRejected = (text) => {
      if (!text || this.input.value.trim()) return;
      this.input.value = text;
      this._autosize();
    };
    /* L'avatar apre «Tu e Jenny»; **tenuto premuto** va dritto in officina,
       che e' la scorciatoia scritta sulla scheda in fondo a quella pagina. Il
       flag lo posa `setupLongPress` e lo consuma il click che segue la
       pressione: senza, un tocco lungo aprirebbe l'officina *e* la pagina. */
    this.door.addEventListener('click', () => {
      if (this.door.dataset.longpress) { delete this.door.dataset.longpress; return; }
      this.openTu();
    });
    setupLongPress(this.door, () => this._openInWorkshop(null));
    document.getElementById('casa-who')?.addEventListener('click', () => this.who.toggle());
    this.pagesBtn?.addEventListener('click', () => this.openPages());
    /* Le due vie d'uscita della stessa stanza, e fanno la stessa cosa: si esce
       da dove stai guardando — in alto a sinistra se leggi l'intestazione, in
       basso a destra col pollice. */
    this.backBtn?.addEventListener('click', () => this.goBackOneRoom());
    this.talkBtn?.addEventListener('click', () => this._setView('chat'));
    this.editBtn?.addEventListener('click', () => this.reader.startEdit());

    sessionManager.init();
    this._applyConversation();
    wsManager.connectChat();

    /* Non `await`: le pagine aggiunte arrivano quando arrivano, e finche' non
       ci sono la casa e' la chat — che e' esattamente quel che deve essere. */
    this.pagine.carica();

    try {
      await this.chat.load();
    } catch (err) {
      console.error('Thread load failed:', err);
      api.clientLog('error', 'casa.thread', String(err && err.stack || err));
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
    if (this.emptyText) this.emptyText.textContent = i18n.t('casa.threadError');
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
       pagina 0 (v. `CasaPagine.apriConversazione`). */
    if (this.pagine) return this.pagine.apriConversazione(target);
    return this.mostraConversazione(target);
  }

  /** La conversazione che la chat mostra adesso. */
  chiaveAttuale() {
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
  async mostraConversazione(key) {
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
      api.clientLog('error', 'casa.switch', String(err && err.stack || err));
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
  schedaQuaderno() {
    return (this._schedaQuaderno ||= new SchedaQuaderno({
      pagine: () => this.portaPagine(),
      apri: (nome) => {
        this.who.close();
        return this.switchConversation(projectKey(nome));
      },
      elimina: (nome) => this.deleteNotebook(nome),
      rinomina: (nome) => this.renameNotebook(nome),
    }));
  }

  /** Rinomina un quaderno dalla sua scheda.
   *
   *  La regola del nome si controlla **qui prima** che sul gateway — e' la
   *  stessa (`isOpenableProjectName` e' la copia fedele di quella del server) —
   *  solo per dirlo subito, senza un giro; il gateway la riapplica comunque.
   *  Il seguito e' quello della cancellazione, all'incontrario: se eri li'
   *  dentro ci resti, sotto il nome nuovo; la tendina, il titolo e le pagine
   *  (che il gateway ha gia' rinominato) si rileggono.
   */
  async renameNotebook(nome) {
    const scritto = await promptDialog(i18n.t('casa.quaderno.renamePrompt', { name: nome }), {
      initial: nome,
    });
    const nuovo = (scritto || '').trim();
    if (!nuovo || nuovo === nome) return false;
    if (!isOpenableProjectName(nuovo)) {
      showToast(i18n.t('scope.invalidName'), 'error');
      return false;
    }
    try {
      await rpc.renameProject(nome, nuovo);
    } catch (err) {
      showToast(i18n.t('casa.quaderno.renameFailed', { name: nome, error: err?.message || '' }), 'error');
      return false;
    }
    const vecchia = projectKey(nome);
    const nuova = projectKey(nuovo);
    this.pagine.rinominaConversazione(vecchia, nuova);
    /* La tendina rilegge **prima** del cambio: il titolo, ridisegnandosi, chiede
       alla sua cache quante pagine ha il quaderno — e con la cache ancora sul
       nome vecchio la pastiglia perdeva il numero. Visto sul telefono il
       23/09/2026 rinominando un quaderno di prova. */
    await this.who.refresh();
    if (sessionManager.currentKey === vecchia) await this.switchConversation(nuova);
    await this.portaPagine().ricarica();
    showToast(i18n.t('casa.quaderno.renamed', { name: nuovo }), 'success');
    return true;
  }

  /** Cancella un quaderno dalla sua scheda, e fa il seguito che e' di casa.
   *
   *  La domanda la fa `deleteProjectFlow`, con le parole della casa. Il seguito:
   *  se eri li' dentro torni alla conversazione personale — restare in una
   *  chat che non esiste piu' vorrebbe dire scrivere a vuoto; la tendina, che
   *  e' ancora aperta sotto la scheda, si ridisegna senza quella riga; e le
   *  pagine si rileggono, perche' il gateway ha tolto anche la sua, se ne aveva
   *  una (v. `project_delete.py`).
   */
  async deleteNotebook(nome) {
    if (!(await deleteProjectFlow(nome, NOTEBOOK_DELETE_WORDS))) return false;
    if (projectNameOf(sessionManager.currentKey) === nome) await this.switchConversation(null);
    await this.who.refresh();
    await this.portaPagine().ricarica();
    showToast(i18n.t('casa.quaderno.eliminato', { name: nome }), 'success');
    return true;
  }

  /* ── Le stanze ──
   *
   *  La casa ne ha tre: la conversazione, le pagine del quaderno, una pagina.
   *  Si entra dall'intestazione e si torna indietro una alla volta — mai due
   *  per un gesto, che e' la stessa regola con cui Indietro chiude la tendina
   *  senza uscire anche dal quaderno.
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
    this._setHeadTitle(await this.reader.load(notebook, path, label));
  }

  /** «Tu e Jenny». */
  async openTu() {
    this._setView('tu');
    this.tu.open();
    this.tu.sayJenny(this.jennyRoom.value());
    const data = await this._askSettings();
    this.jennyRoom.setFloating(data?.floating || null);
    this.jennyRoom.setName(data?.agent?.bot_name || '');
    this.tu.sayJenny(this.jennyRoom.value());
    this.modelRoom.setSettings(data);
    this.tu.sayModel(this.modelRoom.value());
    this.updatesRoom.setVersion(data?.version || null);
    this.tu.sayUpdates(this.updatesRoom.value());
    this.backupRoom.setBackup(data?.backup || null);
    this.tu.sayBackup(this.backupRoom.value());
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
    this.tu.sayUpdates(this.updatesRoom.value());
  }

  /* Il payload fresco che torna da un salvataggio: ha la stessa forma di
     `/api/settings`, quindi prende il posto di quello in cache e le righe che
     lo leggono si riscrivono. Senza, la riga «Chi risponde» direbbe la marca
     di prima fino al riavvio della casa. */
  _keepSettings(data) {
    if (!data) return;
    this._settings = Promise.resolve(data);
    this.tu.sayModel(this.modelRoom.value());
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
        console.warn('casa: impostazioni non lette', err);
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
   *  vuota, la bozza torna dov'era appena il messaggio e' partito. */
  _portaInChat(segnalazione) {
    if (!this.input) return;
    const bozza = this.input.value;
    this._setView('chat');
    this.input.value = messaggioSegnalazione(segnalazione);
    this._send();
    if (bozza.trim()) {
      this.input.value = bozza;
      this._autosize();
    }
  }

  /** Conferma di uscire dal lettore buttando via le modifiche.
   *
   *  Il cambio stanza e' **differito**, non annullato: alla risposta
   *  affermativa si ripassa dallo stesso `_setView`, stavolta col buffer
   *  pulito. La pressione che ha aperto la modale l'ha consumata la modale,
   *  quindi nessuno naviga piu' al posto nostro. */
  async _confirmLeaveReader(target) {
    this.reader.blurEditor();
    const ok = await confirmDialog(i18n.t('casa.reader.discardConfirm'));
    if (!ok) return;
    if (this.view !== 'reader') return;  // uscito da un'altra strada nel frattempo
    this.reader.cancelEdit();
    this._setView(target);
  }

  /** Indietro di **una** stanza. Vero se c'era dove tornare.
   *
   *  Dentro la chat c'e' un gradino in piu' che le stanze non hanno: se sei su
   *  una pagina di lato, «indietro» riporta alla conversazione. Sta qui e non
   *  su un bottone suo perche' cosi' ci passa anche l'Indietro di Android —
   *  che da una pagina deve tornare a casa, non uscire dall'app. */
  goBackOneRoom() {
    if (this.view === 'chat') {
      if (!this.pagine || this.pagine.indice === 0) return false;
      this.pagine.vaiA(0);
      return true;
    }
    const target = BACK_TO[this.view];
    if (!target) return false;
    this._setView(target);
    return true;
  }

  /** La pista ha cambiato casella: l'intestazione dice dove sei. */
  onPaginaCambiata(indice, schermata) {
    this._pagina = indice;
    this._schermata = schermata;
    /* Su quale pagina si e' lo dice un attributo, come per le stanze: cosi' la
       geometria — cosa sparisce quando non sei nella conversazione — resta nel
       CSS e qui c'e' solo il numero. Serve perche' `data-view` da solo non
       basta piu': su una pagina di lato la vista **e' ancora** `chat`, e le
       regole scritte su quella lasciavano acceso il chevron della tendina
       sopra il nome di un'app. */
    this.shell?.setAttribute('data-pagina', String(indice));
    this._applyHead();
  }

  /* La stanza a schermo la dice un attributo su `.casa-shell`, e il resto lo
     fa il CSS: cosi' la geometria — cosa occupa lo spazio, cosa sparisce —
     resta in un posto solo, e qui c'e' solo quel che il CSS non sa fare. */
  _setView(name) {
    const view = Object.hasOwn(BACK_TO, name) ? name : 'chat';
    if (view === this.view) return;
    /* Uscire dal lettore con modifiche non salvate chiede conferma, e la
       guardia sta **qui** e non sui bottoni. Le strade per uscire sono gia'
       quattro — l'occhiello, l'Indietro del telefono, «Parlane», un cambio di
       conversazione — e una guardia per strada e' una guardia che la quinta
       strada non avra'. E' la lezione di `_closeEditor` nel gestore file, dove
       il controllo sul buffer sporco valeva «solo se non esiste una seconda
       strada» e le strade erano tre. */
    if (this.view === 'reader' && this.reader?.isDirty()) {
      this._confirmLeaveReader(view);
      return;
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
    } else {
      /* Niente composer, quindi il pavimento va dichiarato: senza, l'osservatore
         misurerebbe un elemento nascosto e lo troverebbe alto zero. */
      document.documentElement.style.setProperty(
        '--casa-composer-h', `${FLOOR_NO_COMPOSER}px`,
      );
      /* Fuori dalla chat Jenny sta al bordo: lo dice la tavola, che qui la
         disegna a `right:-56px` contro i `-30px` delle due della chat. */
      this.jenny.setOut(false);
      this.input?.blur();
    }
    this._applyHead();
  }

  /* Il nome in testa: la conversazione nella chat, il quaderno nelle pagine,
     la pagina nel lettore. E' lo stesso `<h1>`, perche' e' sempre la risposta
     alla stessa domanda — dove sono. */
  _setHeadTitle(title) {
    if (this.nameEl && title) this.nameEl.textContent = title;
  }

  /* Quali comandi dell'intestazione valgono in questa stanza. */
  _applyHead() {
    /* Una pagina di lato e' «dentro la chat» per le stanze, ma per
       l'intestazione no: ci si sta come in una stanza, col nome di dove sei e
       la via per tornare. Per questo `inChat` qui vuol dire **la
       conversazione**, non la vista. */
    /* La pagina di un quaderno **e'** una chat: occhiello, nome, pastiglia
       delle pagine del quaderno, e niente «Torna alla chat» — ci sei gia'.
       Solo il chevron no: da una pagina fissa non si cambia conversazione. */
    const suQuaderno = (this._pagina || 0) > 0 && this._schermata?.kind === 'conversazione';
    const suPagina = this.view === 'chat' && (this._pagina || 0) > 0 && !suQuaderno;
    const inChat = this.view === 'chat' && !suPagina;
    /* «Parlane» riporta a parlare **di questo quaderno**: vale dalle sue
       pagine e dal lettore, e in nessun altro posto. Era `!inChat`, che con
       tre stanze diceva la stessa cosa e con cinque no — il bottone sarebbe
       comparso dentro «Tu e Jenny», dove non c'e' niente di cui parlare. */
    const inNotebook = this.view === 'pages' || this.view === 'reader';
    const notebook = projectNameOf(sessionManager.currentKey);
    if (this.kicker) this.kicker.hidden = !inChat;
    if (this.backBtn) this.backBtn.hidden = inChat;
    if (this.door) this.door.hidden = !inChat;
    if (this.talkBtn) this.talkBtn.hidden = !inNotebook;
    /* «Modifica» e' solo del lettore, e sparisce appena l'editor e' aperto: da
       li' i comandi sono Salva e Annulla, e stanno in basso. */
    if (this.editBtn) this.editBtn.hidden = this.view !== 'reader' || this.reader.editing;
    /* Uscendo dal lettore la selezione se ne va con la stanza, ma il
       `selectionchange` non e' garantito quando i nodi selezionati spariscono:
       la barra va chiusa qui, o resterebbe accesa sopra un'altra stanza. */
    this.audit?.refresh();
    if (this.pagesBtn) this.pagesBtn.hidden = !inChat || !notebook;
    if (this.dotEl) this.dotEl.hidden = !notebook || !inChat;
    /* Fuori dalla chat il titolo non apre piu' niente: nelle pagine dice quale
       quaderno stai guardando, e un chevron che promette una scelta la' sopra
       porterebbe a cambiare stanza da dentro un'altra. */
    const whoBtn = document.getElementById('casa-who');
    if (whoBtn) whoBtn.disabled = !inChat || suQuaderno;
    if (!inChat && this.view === 'pages') this._setHeadTitle(notebook);
    if (this.view === 'tu') this._setHeadTitle(i18n.t('casa.tu.title'));
    if (this.view === 'jenny') this._setHeadTitle(i18n.t('casa.jenny.title'));
    if (this.view === 'model') this._setHeadTitle(i18n.t('casa.model.title'));
    if (this.view === 'updates') this._setHeadTitle(i18n.t('casa.updates.title'));
    if (this.view === 'backup') this._setHeadTitle(i18n.t('casa.backup.title'));
    if (suPagina) this._setHeadTitle(this.pagine.nomeDi(this._schermata));
    /* Tornando alla pagina 0 il nome della conversazione va rimesso: il
       titolo e' uno solo, e chi ci ha scritto sopra il nome di una pagina
       l'ha coperto. */
    else if (this.view === 'chat') {
      this._setHeadTitle(projectNameOf(sessionManager.currentKey) || this._personalName);
    }
    this._applyBackLabel();
  }

  /* L'occhiello dice **dove si atterra**, non «indietro». Con tre stanze la
     differenza non si vedeva; con quattro, «torna alla chat» sopra il lettore
     era falso — di li' si torna alle pagine. La frase la sceglie la stessa
     tabella che decide il salto, quindi le due non possono divergere. */
  _applyBackLabel() {
    if (!this.backLabel) return;
    this.backLabel.textContent = i18n.t(`casa.back.${BACK_TO[this.view] || 'chat'}`);
  }

  /* La mappa costa 280 kB di D3, quindi il suo modulo arriva col primo tocco
     sulla linguetta e non con l'avvio della casa. `import()` dinamico e non
     statico: e' la differenza fra pagarla chi la apre e pagarla tutti. */
  async _drawMap(data, quaderno) {
    if (!this.map) {
      const { CasaMap } = await import('./casa-map.js');
      this.map = new CasaMap({
        onOpenPage: (path, label) => this.openPage(path, label),
      });
    }
    await this.map.draw(data, quaderno);
  }

  /* L'intestazione dice dove sei: l'occhiello, il nome, il pallino — lo stesso
     colore che ha la riga nel pannello, ed e' l'unica cosa che lega il tocco
     alla stanza in cui sei finito. */
  _applyConversation() {
    const project = projectNameOf(sessionManager.currentKey);
    if (this.nameEl) this.nameEl.textContent = project || this._personalName;
    if (this.dotEl) {
      this.dotEl.hidden = !project;
      this.dotEl.style.background = project ? dotColor(project) : '';
    }
    this._applyTranslations();
    this._applyHead();
    this._updatePagesCount(project);
    // Aperto mentre si cambia (non capita col tocco, capita con Indietro).
    if (this.who?.isOpen) this.who.render();
  }

  /* Il numero sulla pastiglia. Arriva quando arriva — il conteggio sta nello
     stesso elenco della tendina — e fino ad allora la pastiglia c'e' con la sua
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
        count === 1 ? 'casa.pages.countOne' : 'casa.pages.countMany', { count },
      );
    }).catch((err) => console.warn('casa.pages: conteggio non letto', err));
  }

  /* Il turno che stava girando nella conversazione lasciata non si chiudera'
     mai qui dentro: il suo `turn_end` arrivera' e verra' scartato. Si chiude a
     mano tutto cio' che lo stava aspettando. */
  _releaseTurn() {
    this.activity.stop();
    this.jenny.noteTurnRunning(false);
    this.jenny.idle();
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
    return (this._azioniApp ||= new AppsActions(this.appsSource(), {
      sendChatPrompt: (testo) => this._mandaInChat(testo),
      pagine: () => this.portaPagine(),
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
   *  pagina nuova: si appende dal cassetto o dalla tendina, e atterrare sotto
   *  un cassetto aperto vorrebbe dire non vedere di aver fatto niente.
   */
  portaPagine() {
    return (this._portaPagine ||= {
      stato: (kind, ref) => {
        if (this.pagine.appesa(kind, ref)) return 'appesa';
        return this.pagine.pienoZeppo ? 'piena' : 'libera';
      },
      appendi: async (kind, ref) => {
        /* Uno strato per giro, e con un tetto: `_closeOverlays` torna vero
           anche quando delega la chiusura (la lightbox), e un ciclo senza
           fine qui sarebbe la casa bloccata su un tocco. */
        for (let i = 0; i < 8 && this._closeOverlays(); i += 1) { /* avanti */ }
        return this.pagine.appendi(kind, ref);
      },
      stacca: (kind, ref) => this.pagine.stacca(kind, ref),
      ricarica: () => this.pagine.ricarica(),
    });
  }

  /** Porta una richiesta gia' scritta dentro la conversazione e la manda.
   *  Serve a «modifica questa app», che non apre un editor: chiede a Jenny. */
  _mandaInChat(testo) {
    this.goHome();
    if (!this.input) return;
    this.input.value = testo;
    this.input.dispatchEvent(new Event('input', { bubbles: true }));
    this.input.focus();
  }

  /** Apre il cassetto delle app. Stesso nome del metodo dell'officina, perche'
   *  chi chiama e' lo stesso codice. */
  openLauncher() {
    this.launcher?.open();
  }

  /** Il tasto Home di Android, quando Jenny e' il launcher.
   *
   *  In officina Home smonta cinque livelli di overlay e collassa il
   *  sotto-stato di ogni sezione. Qui Home vuol dire una cosa sola: **sei a
   *  casa**. Si chiude quel che sta sopra, si torna nella conversazione
   *  personale — se eri dentro un quaderno, quella e' la casa da cui il tasto
   *  prende il nome — si chiude la tastiera e si torna in fondo al filo, che e'
   *  il presente della conversazione.
   */
  goHome() {
    this._closeOverlays();
    this._setView('chat');
    this.switchConversation(null);
    this.input?.blur();
    this.chat.scrollToBottom();
  }

  /** Il tasto Indietro di Android.
   *
   *  In officina e' una catena di cinque livelli di overlay piu' lo stack di
   *  navigazione. In casa gli strati sono due — la tendina e l'immagine
   *  ingrandita — e sotto c'e' una cosa sola da cui si puo' tornare: un
   *  quaderno. Indietro allora e' la porta di casa, cioe' la conversazione
   *  personale.
   *
   *  Nella conversazione personale, senza niente sopra, **non si fa niente**, e
   *  non e' una dimenticanza: questa app e' il launcher del telefono, e
   *  Indietro non deve mai chiudere il task.
   *
   *  Una pressione, una cosa sola: chiudere la tendina *e* uscire dal quaderno
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

  /** Chiude cio' che sta sopra la conversazione. Vero se c'era qualcosa.
   *
   *  La tendina per prima: `showModal()` la mette nel top layer, quindi e' lo
   *  strato piu' in alto che ci sia. Aperta, copre il filo con il suo velo — da
   *  li' non si apre nessuna immagine — quindi le due cose non convivono e
   *  l'ordine e' una garanzia, non una scelta fra due candidati.
   */
  _closeOverlays() {
    /* Prima ancora del cassetto, i fogli che si aprono con una pressione lunga:
       quello di un quaderno **dalla** tendina, e i due delle app **dal**
       cassetto (Open, Edit, Delete). Sono `<dialog>`
       con `showModal()`: stanno nel top layer, sopra il cassetto, e il loro
       commento in `apps-actions.js` lo dice — Indietro chiude prima loro. Qui
       non c'erano: visto sul telefono il 23/09/2026, Indietro chiudeva il
       cassetto sotto e lasciava il foglio aperto sopra la chat, Delete
       compreso. */
    for (const id of ['casa-quaderno-sheet', 'jenny-app-sheet', 'android-app-sheet']) {
      const foglio = document.getElementById(id);
      if (foglio?.open) {
        foglio.close();
        return true;
      }
    }
    /* Poi il cassetto: sotto i suoi fogli, sopra tutto il resto, e in officina
       sta nello stesso posto della catena (fra la mini-app e la tendina). Il
       suo `close()` e' idempotente, quindi `isOpen()` e' l'unica domanda da
       fare — e si chiama cosi', non `present()`: quello e' il nome della
       *proprieta'* che l'officina mette nei suoi livelli di overlay, e
       scriverlo qui sarebbe passato in silenzio (optional chaining su un metodo
       che non c'e' torna `undefined`, cioe' «Indietro non chiude il cassetto»
       senza un errore). */
    if (this.launcher?.isOpen()) {
      this.launcher.close();
      return true;
    }
    /* Il foglio di «Segnala»: `showModal()`, quindi top layer come la tendina.
       Un `<dialog>` modale si chiude da se' con Escape, ma qui Indietro arriva
       dal guscio nativo come un evento suo e nessuno lo traduce in Escape:
       senza questa riga la pressione uscirebbe dalla *stanza* lasciando il
       foglio aperto sopra un'altra. */
    const sheet = document.getElementById('casa-audit-dialog');
    if (sheet?.open) {
      sheet.close();
      return true;
    }
    if (this.who.isOpen) {
      this.who.close();
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
    this._closeOverlays();
    this._setView('chat');
    this.switchConversation(null);
    this.chat.scrollToBottom();
    return true;
  }

  /** Una app Android e' stata installata o rimossa.
   *
   *  Qui non c'e' ancora nessun elenco di app da rinfrescare — il cassetto e'
   *  una tavola del giro dopo. Il metodo esiste comunque, e non e' cerimonia:
   *  il guscio nativo lo chiama senza guardare, e un `undefined` sarebbe un
   *  TypeError dentro la sua `evaluateJavascript`.
   */
  onPackageChanged() {}

  /* ── Il composer ── */

  _bindComposer() {
    this.attach.addEventListener('click', () => this.files.trigger());

    /* Il cassetto delle app, a sinistra del composer. Rimesso il 22/09/2026
       dopo la prova sul telefono: il «tira su» dalla striscia che la tavola
       chiedeva non arriva mai all'app, perche' con la navigazione a gesti il
       bordo basso e' di Android. */
    const cassetto = document.getElementById('casa-drawer');
    cassetto?.addEventListener('click', () => this.openLauncher());
    /* L'etichetta per chi legge lo schermo. La casa non ha una passata
       generica sui `data-i18n-*`, quindi il valore va scritto. */
    const dilloBene = () =>
      cassetto?.setAttribute('aria-label', i18n.t('nav.launcher'));
    dilloBene();
    i18n.onLocaleChange(dilloBene);

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
      const h = document.querySelector('.casa-composer')?.offsetHeight || 64;
      /* **Anche la striscia dei pallini.** Sta sotto il composer, e da quando
         ha preso il posto del bottone del cassetto e' alta 26px: senza
         contarla Jenny ci finirebbe sopra, cioe' sopra la presa con cui si
         tira su il cassetto. */
      const striscia = document.getElementById('casa-pallini')?.offsetHeight || 0;
      document.documentElement.style.setProperty('--casa-composer-h', `${h + striscia}px`);
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
      for (const sel of ['.casa-composer', '.casa-pending', '.casa-activity', '.casa-wire']) {
        const el = document.querySelector(sel);
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
      item.className = 'casa-pending-item';
      if (entry.kind === 'image') {
        const img = document.createElement('img');
        img.src = entry.url;
        img.alt = entry.name || '';
        item.appendChild(img);
      } else {
        const name = document.createElement('span');
        name.className = 'casa-pending-name';
        name.textContent = entry.name || '';
        item.appendChild(name);
      }
      const x = document.createElement('button');
      x.type = 'button';
      x.dataset.remove = String(i);
      x.className = 'casa-pending-x';
      x.setAttribute('aria-label', i18n.t('casa.removeAttachment'));
      x.innerHTML = '<i class="ti ti-x"></i>';
      item.appendChild(x);
      this.pending.appendChild(item);
    });
  }

  _send() {
    const text = this.input.value.trim();
    /* Una foto senza didascalia e' un messaggio: la bolla e' l'immagine. E' la
       stessa regola che il gateway applica all'eco di Telegram — col solo
       controllo sul testo, una foto muta non partirebbe. */
    if (!text && !this.files.count) return;
    /* Scritto a mano, `/stop` resta il comando che e' — in casa i comandi non
       ci sono, ma niente impedisce di digitarne uno, e disegnarne la bolla
       vorrebbe dire mostrare in chat una cosa che il transcript esclude
       apposta dall'eco. */
    if (text === '/stop') {
      this._stop();
      this.input.value = '';
      this._autosize();
      return;
    }
    const chatId = sessionManager.currentChatId;
    const media = this.files.getImages();
    const entries = this.files.getAttachmentEntries();
    if (!wsManager.sendToChat(chatId, text, media)) {
      /* Socket chiuso: il messaggio non e' partito e non va disegnato. Una
         bolla che compare e un messaggio che non arriva sono la stessa cosa
         vista da due parti, e la prima fa credere alla seconda. */
      this._setWire(false);
      return;
    }
    /* La bolla la disegna il client: il gateway rimanda l'eco solo dei messaggi
       entrati da *altri* canali (v. webui_turns._handle_session_turn_started,
       che per il canale websocket esce subito). */
    this.chat.appendOwn(text, entries);
    this.files.clear();
    this.input.value = '';
    this._autosize();
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
     li manda comunque, e in officina disegnano pannelli. */
  _readActivity(msg) {
    if (!msg) return;
    if (msg.turn_id) this.activity.turnId = msg.turn_id;
    switch (msg.event) {
      case 'goal_status':
        this.jenny.noteTurnRunning(msg.status === 'running');
        if (msg.status === 'running') {
          this.activity.start(msg.turn_id);
          this.jenny.thinking();
        } else {
          this.activity.stop();
          this.jenny.idle();
        }
        break;
      case 'reasoning_delta':
        this.activity.reasoning();
        this.jenny.thinking();
        break;
      case 'stream_end':
        // Il testo e' finito: torna in quiete senza aspettare il silenzio.
        this.jenny.idle();
        break;
      case 'mascot_mood':
        this.jenny.setMood(msg.mood, msg.turn_id || null);
        break;
      case 'message':
        // Un `tool_hint` porta i nomi degli strumenti che stanno partendo.
        if (msg.tool_events) this.activity.tools(msg.tool_events);
        break;
      case 'delta':
        // La risposta sta arrivando: la riga si toglie di mezzo e la bocca si
        // muove.
        this.activity.answering();
        this.jenny.talking();
        break;
      case 'turn_end':
        this.activity.stop();
        this.jenny.noteTurnClosed(msg.turn_id || null);
        this.jenny.idle();
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
    const target = turnId ? `/html-mobile/officina.html#turn=${encodeURIComponent(turnId)}`
                          : '/html-mobile/officina.html';
    api.navigate(target);
  }

  /* `goal_status` dice se un turno sta girando: e' quel che trasforma il
     bottone da "manda" a "ferma". */
  _readRunStatus(msg) {
    if (msg?.event !== 'goal_status') return;
    this._setRunning(msg.status === 'running');
  }

  _setRunning(running) {
    if (this._running === running) return;
    this._running = running;
    this.send.classList.toggle('is-stop', running);
    this.send.innerHTML = running
      ? '<i class="ti ti-player-stop-filled"></i>'
      : '<i class="ti ti-arrow-up"></i>';
    this.send.setAttribute('aria-label', i18n.t(running ? 'casa.stop' : 'casa.send'));
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
      this.wire.textContent = i18n.t('casa.wire.offline');
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
      const empty = inNotebook ? 'casa.emptyNotebook' : 'casa.empty';
      this.emptyText.textContent = i18n.t(this._threadFailed ? 'casa.threadError' : empty);
    }
    if (this.input) {
      this.input.placeholder = i18n.t(inNotebook ? 'casa.placeholderNotebook' : 'casa.placeholder');
    }
    if (this.kicker) {
      this.kicker.textContent = i18n.t(inNotebook ? 'casa.kickerNotebook' : 'casa.kicker');
    }
    if (this.send) {
      this.send.setAttribute('aria-label', i18n.t(this._running ? 'casa.stop' : 'casa.send'));
    }
    if (this.attach) this.attach.setAttribute('aria-label', i18n.t('casa.attach'));
    if (this.door) this.door.setAttribute('aria-label', i18n.t('casa.tu.open'));
    this._applyBackLabel();
    if (this.talkLabel) this.talkLabel.textContent = i18n.t('casa.pages.talk');
    if (this.editBtn) this.editBtn.setAttribute('aria-label', i18n.t('casa.reader.edit'));
    this.reader?.applyTranslations();
    this.audit?.applyTranslations();
    this.tu?.applyTranslations();
    this.modelRoom?.applyTranslations();
    this.updatesRoom?.applyTranslations();
    this.backupRoom?.applyTranslations();
    this.jennyRoom?.applyTranslations();
    this.tu?.sayJenny(this.jennyRoom?.value());
    if (this.pagesBtn) this.pagesBtn.setAttribute('aria-label', i18n.t('casa.pages.open'));
    this.pages?.applyTranslations();
    const whoBtn = document.getElementById('casa-who');
    if (whoBtn) whoBtn.setAttribute('aria-label', i18n.t('casa.who.open'));
    // Aperta mentre la lingua cambia: le sue righe sono gia' a schermo.
    if (this.who?.isOpen) this.who.render();
    if (this.files?.count) this._renderPending();
    if (this.wire && !this.wire.hidden) this.wire.textContent = i18n.t('casa.wire.offline');
  }

}

new CasaApp();
