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
import { WhoPanel, dotColor } from './casa-who.js';
import { projectKey, projectNameOf } from './shared/conversation-list.js';
import { PROJECT_WORDS, createProjectFlow } from './shared/project-create.js';
import { api } from './shared/api-client.js';
import { ImageHandler } from './shared/image-handler.js';
import { i18n } from './shared/i18n.js';
import { sessionManager } from './shared/session-manager.js';
import { wsManager } from './shared/ws-manager.js';
import './shared/theme.js';

/* Quanto aspettare prima di dire che il filo e' interrotto. Il socket si riapre
   da se' e la maggior parte delle cadute dura meno di un battito: annunciarle
   tutte vorrebbe dire far lampeggiare una riga d'allarme mentre non e' successo
   niente. Si parla solo se il silenzio dura. */
const WIRE_GRACE_MS = 2_500;

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

class CasaApp {
  constructor() {
    this.thread = document.getElementById('casa-thread');
    this.chat = new CasaChat(this.thread);
    this.jenny = new CasaMascot(document.querySelector('.casa-shell'));
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
    this._running = false;
    /* Il guscio nativo copre la pagina con un caricamento finche' non chiama
       onNativeReady: fino ad allora qualunque animazione d'ingresso scorre
       dietro una tendina e se ne vede solo la coda. */
    this._shellReady = false;
    this._shellReadyCbs = [];

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
    this.door.addEventListener('click', () => this._openInWorkshop(null));
    document.getElementById('casa-who')?.addEventListener('click', () => this.who.toggle());

    sessionManager.init();
    this._applyConversation();
    wsManager.connectChat();

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
    // Aperto mentre si cambia (non capita col tocco, capita con Indietro).
    if (this.who?.isOpen) this.who.render();
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
      const h = document.querySelector('.casa-composer')?.offsetHeight || 64;
      document.documentElement.style.setProperty('--casa-composer-h', `${h}px`);
    };
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
    if (this.door) this.door.setAttribute('aria-label', i18n.t('casa.workshop'));
    const whoBtn = document.getElementById('casa-who');
    if (whoBtn) whoBtn.setAttribute('aria-label', i18n.t('casa.who.open'));
    // Aperta mentre la lingua cambia: le sue righe sono gia' a schermo.
    if (this.who?.isOpen) this.who.render();
    if (this.files?.count) this._renderPending();
    if (this.wire && !this.wire.hidden) this.wire.textContent = i18n.t('casa.wire.offline');
  }

}

new CasaApp();
