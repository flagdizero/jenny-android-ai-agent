/** La casa — guscio.
 *
 *  La seconda interfaccia di Jenny, quella il cui unico mestiere e' la
 *  conversazione. L'officina (`mobile-app.js`) resta intera e resta dov'e': i
 *  due gusci non si vedono fra loro, non condividono un nodo del DOM e non si
 *  possono rompere a vicenda. Condividono le fondamenta — `assets/shared/*` —
 *  e nient'altro.
 *
 *  Piano: `.agent/casa-plan.md`. Questo file e' il passo 1, e fa tre cose:
 *  accende il filo, onora il contratto col guscio nativo, e disegna il posto
 *  dove la conversazione andra'.
 *
 *  **Il contratto col guscio nativo e' di tre metodi.** Android chiama
 *  `window.mobileApp.onNativeReady()`, `.goHome()` e `.onPackageChanged()` —
 *  sono gli unici tre attraversamenti in quella direzione in tutto il sorgente
 *  Kotlin. Il nome globale resta `mobileApp` apposta: cosi' il guscio nativo
 *  non sa, e non deve sapere, quale delle due interfacce ha caricato.
 */

import { ActivityLine } from './casa-activity.js';
import { CasaChat } from './casa-chat.js';
import { CasaMascot } from './casa-mascot.js';
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

    /* Il selettore di allegati e' lo stesso dell'officina, con gli stessi tetti
       del server (4 immagini, 8 MB l'una): superarli fa rifiutare il messaggio
       intero, quindi i limiti devono stare da una parte sola. */
    this.files = new ImageHandler();
    this.files.onChange = () => this._renderPending();

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

    wsManager.addEventListener('chat:open', () => this._setWire(true));
    wsManager.addEventListener('chat:close', () => this._setWire(false));
    wsManager.addEventListener('chat:message', (e) => {
      this._readRunStatus(e.detail);
      this._readActivity(e.detail);
      this.chat.handleFrame(e.detail);
    });

    this._bindComposer();
    this.door.addEventListener('click', () => this._openInWorkshop(null));

    sessionManager.init();
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
   *  sotto-stato di ogni sezione. Qui sopra la conversazione non c'e' ancora
   *  niente da smontare, e Home vuol dire una cosa sola: sei a casa, sei gia'
   *  arrivato. Si chiude la tastiera, perche' quella si' e' uno strato, e si
   *  torna in fondo al filo, che e' il presente della conversazione.
   */
  goHome() {
    this.handleHardwareBack();
    this.input?.blur();
    this.chat.scrollToBottom();
  }

  /** Il tasto Indietro di Android.
   *
   *  In officina e' una catena di cinque livelli di overlay piu' lo stack di
   *  navigazione. In casa sopra la conversazione c'e' una cosa sola che si puo'
   *  chiudere, l'immagine ingrandita — e sotto non c'e' nessuna schermata
   *  precedente, perche' la casa e' una schermata sola.
   *
   *  Alla radice **non si fa niente**, e non e' una dimenticanza: questa app e'
   *  il launcher del telefono, e Indietro non deve mai chiudere il task.
   */
  handleHardwareBack() {
    const lightbox = document.querySelector('.image-lightbox');
    if (lightbox) {
      if (typeof lightbox.__jennyClose === 'function') lightbox.__jennyClose();
      else lightbox.remove();
    }
  }

  /** Il tocco su un avviso proattivo: porta *in chat*.
   *
   *  In officina significa cambiare vista; qui la chat e' l'unica cosa che
   *  c'e', quindi vuol dire togliere di mezzo cio' che la copre e riportarsi
   *  sul presente della conversazione.
   */
  openChat() {
    this.handleHardwareBack();
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
    if (window.ResizeObserver) {
      new ResizeObserver(measure).observe(document.querySelector('.casa-composer'));
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
     leggera', da questa parte non c'e' niente da cambiare. */
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
    if (this.emptyText) {
      this.emptyText.textContent = i18n.t(this._threadFailed ? 'casa.threadError' : 'casa.empty');
    }
    if (this.input) this.input.placeholder = i18n.t('casa.placeholder');
    if (this.send) {
      this.send.setAttribute('aria-label', i18n.t(this._running ? 'casa.stop' : 'casa.send'));
    }
    if (this.attach) this.attach.setAttribute('aria-label', i18n.t('casa.attach'));
    if (this.door) this.door.setAttribute('aria-label', i18n.t('casa.workshop'));
    if (this.kicker) this.kicker.textContent = i18n.t('casa.kicker');
    if (this.files?.count) this._renderPending();
    if (this.wire && !this.wire.hidden) this.wire.textContent = i18n.t('casa.wire.offline');
  }

}

new CasaApp();
