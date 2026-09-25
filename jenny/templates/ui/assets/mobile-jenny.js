/** Jenny companion — la mascotte che vive sul bordo di ogni vista.
 *
 * A riposo sporge dal bordo dove l'hai lasciata (sinistro finché non la
 * lanci da qualche parte, v. settle()). Richiamata (swipe verso l'interno o tap)
 * esce in overlay con una minichat a un turno: campo "Chiedi qui" in basso,
 * pensa, risponde con un fumetto sopra la testa. In chat niente minichat:
 * è solo presente all'angolo — la conversazione vera è già aperta.
 *
 * I messaggi viaggiano sulla stessa sessione WebSocket della chat, senza
 * contesto aggiuntivo sulla vista corrente.
 *
 * **Jenny in sé non è qui**: arte, stati, parlato, umore e lettura dei frame
 * stanno in `shared/jenny-mascot.js`, e sono gli stessi della casa. Qui resta
 * quel che l'officina ha in più — le viste che non sono la chat, e la minichat
 * che in quelle viste le si apre sotto.
 */

import { AppState } from './shared/state.js';
import { wsManager } from './shared/ws-manager.js';
import { sessionManager } from './shared/session-manager.js';
import { i18n } from './shared/i18n.js';
import { mascotVisible } from './shared/mascot.js';
import { JennyMascot } from './shared/jenny-mascot.js';

const CONNECT_TIMEOUT_MS = 6000;
const REPLY_TIMEOUT_MS = 90000;
const REPLY_MAX_CHARS = 280;

/* Riduce il markdown della risposta a testo piano da fumetto. */
function plainText(md) {
  let t = String(md || '');
  t = t.replace(/```[\s\S]*?```/g, ' ' + i18n.t('jenny.codeTag') + ' ');
  t = t.replace(/`([^`]*)`/g, '$1');
  t = t.replace(/!\[[^\]]*\]\([^)]*\)/g, '');
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
  t = t.replace(/^#{1,6}\s+/gm, '');
  t = t.replace(/[*_~]/g, '');
  t = t.replace(/^>\s?/gm, '');
  t = t.replace(/\s+/g, ' ').trim();
  if (t.length > REPLY_MAX_CHARS) {
    t = t.slice(0, REPLY_MAX_CHARS - 1).trimEnd() + '…';
  }
  return t || '✿';
}

export class JennyCompanion extends JennyMascot {
  constructor() {
    super(document.getElementById('app'), { mode: AppState.currentMode || 'chat' });
    this.awaiting = false;
    this._replyShown = false;
    this._replyTimer = null;
    this._deltaBuffer = '';

    this._bindMinichat();
    AppState.on('currentMode', (mode) => this.setMode(mode));
  }

  _buildDom() {
    this.scrim = document.createElement('button');
    this.scrim.className = 'jenny-scrim';
    this.scrim.addEventListener('click', () => this._setOut(false));

    this.mc = document.createElement('div');
    this.mc.className = 'jenny-mc';
    this.mc.dataset.state = 'ask';
    this.mc.innerHTML = `
      <div class="jenny-mc-bubble"></div>
      <div class="jenny-mc-think">…</div>
      <form class="jenny-mc-ask compose-row">
        <div class="compose-pill">
          <input class="jenny-mc-input" type="text" autocomplete="off">
        </div>
        <button class="jenny-mc-send compose-send" type="submit" disabled>
          <i class="ti ti-arrow-up"></i>
        </button>
      </form>`;

    this.host.appendChild(this.scrim);
    this.host.appendChild(this.mc);
    // Lo sprite dopo la minichat, come e' sempre stato: e' l'ordine del DOM.
    super._buildDom();

    this.bubble = this.mc.querySelector('.jenny-mc-bubble');
    this.askForm = this.mc.querySelector('.jenny-mc-ask');
    this.input = this.mc.querySelector('.jenny-mc-input');
    this.sendBtn = this.mc.querySelector('.jenny-mc-send');

    // Come in chat: il send si accende solo quando c'è testo.
    this.input.addEventListener('input', () => {
      this.sendBtn.disabled = !this.input.value.trim();
    });

    /* Le parole si scrivono adesso e di nuovo quando arrivano le traduzioni:
       questo costruttore gira prima di `i18n.load()` (v. il costruttore di
       MobileApp), e fino ad allora `i18n.t` torna le chiavi grezze. Prima si
       rileggeva solo il placeholder, e scrim, campo e invio restavano con
       «jenny.send» come etichetta per il lettore di schermo. */
    const traduci = () => {
      this.scrim.setAttribute('aria-label', i18n.t('jenny.closeMinichat'));
      this.input.setAttribute('aria-label', i18n.t('jenny.askJenny'));
      this.sendBtn.setAttribute('aria-label', i18n.t('jenny.send'));
      // Stesso placeholder (e stessa lingua) della chat vera.
      const t = i18n.t('chat.placeholder');
      this.input.placeholder = t && t !== 'chat.placeholder' ? t : i18n.t('jenny.askHere');
    };
    traduci();
    i18n.load(i18n.locale).then(traduci).catch(() => {});
  }

  /* ── Modalità vista ── */

  _enterInitialState() {
    this.setMode(this.mode);
  }

  setMode(mode) {
    this.mode = mode;
    this._agentState = 'idle';
    this._turnActive = false;
    this._streamTurnId = null;
    if (this._abortFlight) this._abortFlight();
    this._closeMini();
    // Nascosta durante l'onboarding, oppure per preferenza utente
    // (la stanza «Jenny» della casa → visibile, v. shared/mascot.js).
    const hidden = mode === 'onboarding' || !mascotVisible();
    this.el.classList.toggle('hidden-mode', hidden);
    // Coerente col media-query landscape: nascondi anche gli overlay
    // (minichat e scrim), non solo il duo, per evitare residui interattivi.
    this.mc.classList.toggle('hidden-mode', hidden);
    this.scrim.classList.toggle('hidden-mode', hidden);
    if (hidden) {
      this._updateGestureExclusion();
      return;
    }

    if (mode === 'chat') {
      // Presenza pura: all'angolo sopra la barra di input, senza minichat.
      this.el.classList.add('in-chat', 'out');
    } else {
      this.el.classList.remove('in-chat', 'out', 'mini');
    }
    this._syncArt();
    this._updateGestureExclusion();
  }

  /* Le preferenze passano da `setMode`, che sa anche dell'onboarding e degli
     overlay della minichat. */
  _applyVisibility() {
    this.setMode(this.mode);
  }

  /* ── Drag / tap ── */

  /* La minichat si chiude quando il trascinamento comincia. */
  _onDragCommit() {
    if (this.mc.classList.contains('open')) this._closeMini();
  }

  _onOutChange(out) {
    if (this.mode === 'chat') return;
    this.el.classList.toggle('mini', out);
    if (out) this._openMini();
    else this._closeMini();
  }

  /* Tasto Indietro hardware: con la minichat aperta lo si consuma per
     richiuderla (scrim e tastiera comprese), come il tap sullo scrim.
     Ritorna false se non c'era niente di aperto. */
  handleBack() {
    if (!this.mc?.classList.contains('open')) return false;
    this._setOut(false);
    return true;
  }

  /* ── Minichat ── */

  _openMini() {
    this.scrim.classList.add('open');
    this.mc.classList.add('open');
    this.mc.dataset.state = 'ask';
    // Il campo prende il fuoco da solo: la minichat si apre per scrivere, e
    // chiederle di aprirla e poi toccare il campo è un tap di troppo. Va fatto
    // qui e in modo sincrono — siamo ancora dentro il gesto dell'utente
    // (tap o rilascio del drag), l'unico momento in cui la WebView Android
    // accetta di alzare la tastiera senza che l'utente tocchi l'input.
    this.input.focus();
  }

  _closeMini() {
    // Simmetrico al focus di _openMini: chiudendola la tastiera se ne deve
    // andare con lei, non restare aperta su un campo che non si vede più.
    this.input.blur();
    this.scrim.classList.remove('open');
    this.mc.classList.remove('open');
    this.mc.dataset.state = 'ask';
    this.bubble.textContent = '';
    this._deltaBuffer = '';
    this.input.value = '';
    this.sendBtn.disabled = true;
    this.el.classList.remove('thinking', 'mini');
    this._setAgentState('idle');
    this.awaiting = false;
    this._replyShown = false;
    if (this._replyTimer) {
      clearTimeout(this._replyTimer);
      this._replyTimer = null;
    }
  }

  _bindMinichat() {
    this.askForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const text = this.input.value.trim();
      if (!text || this.awaiting) return;
      this.input.value = '';
      this.sendBtn.disabled = true;
      this.input.blur();
      this._send(text);
    });
  }

  async _send(text) {
    this.mc.dataset.state = 'think';
    this._turnActive = true;
    this._pendingTurn = true;
    this._setAgentState('thinking'); // ferma un eventuale parlato precedente; _syncArt -> think
    this.awaiting = true;
    this._replyShown = false;
    this._deltaBuffer = '';

    try {
      await this._ensureConnected();
      sessionManager.ensureAttached();
      if (!wsManager.sendToChat(sessionManager.currentKey, text)) {
        throw new Error('ws send failed');
      }
      this._replyTimer = setTimeout(() => {
        if (this.awaiting) {
          this._showReply(i18n.t('jenny.workingReply'));
          this.awaiting = false;
        }
      }, REPLY_TIMEOUT_MS);
    } catch (err) {
      console.error('Jenny send failed:', err);
      this._showReply(i18n.t('jenny.connectionError'));
      this.awaiting = false;
      // Niente è partito: nessun turn_end arriverà a chiudere il flag.
      this._pendingTurn = false;
      this._turnActive = false;
    }
  }

  _ensureConnected() {
    wsManager.connectChat();
    if (wsManager.chatConnected) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        wsManager.removeEventListener('chat:open', onOpen);
        reject(new Error('ws connect timeout'));
      }, CONNECT_TIMEOUT_MS);
      const onOpen = () => {
        clearTimeout(timer);
        wsManager.removeEventListener('chat:open', onOpen);
        resolve();
      };
      wsManager.addEventListener('chat:open', onOpen);
    });
  }

  /* Il filtro sulla conversazione e l'umore li fa `_handleWsMessage`, che e'
     di tutte e due; qui si decide solo dove va il frame. */
  _handleFrame(msg) {
    if (this.mode === 'chat') {
      this._handleChatStream(msg);
      return;
    }
    /* La guardia scarta gli eventi di un turno che la mascotte non sta
       seguendo. Ma le sue due condizioni — `awaiting` e la classe `thinking` —
       sono esattamente ciò che `_closeMini()` azzera: chiudendo la minichat
       prima della fine del turno, `turn_end` finiva qui e veniva scartato. Ed è
       l'unico punto che invalida lo storico della chat principale, quindi la
       domanda fatta alla minichat non compariva più: la chat restava con una
       risposta senza domanda per tutta la sessione (un reload "riparava", cioè
       lo scambio nel file c'era sempre stato).

       Gli eventi che *chiudono* il turno passano quindi su un flag che la UI
       non tocca. Il resto — delta, stati intermedi — resta legato a ciò che è
       a schermo: dipingere una bolla che non si vede non serve a nessuno. */
    const closing = msg.event === 'turn_end' || msg.event === 'error';
    const onScreen = this.awaiting || this.el.classList.contains('thinking');
    if (!onScreen && !(closing && this._pendingTurn)) return;
    // Il tracciamento si nutre dei frame che la mascotte anima davvero (quelli
    // scartati qui sopra non li ha mai visti), e serve a una cosa sola: un
    // turno estraneo — l'avviso proattivo atterrato durante l'attesa — non
    // chiude la domanda in volo, la cui risposta sta ancora arrivando.
    const mine = this._trackedTurnMatches(msg);
    if (closing && !mine) return;
    this._handleChatStream(msg, mine);
  }

  /* La minichat mette il fumetto sopra la macchina a stati della madre, che
     resta l'unica. Si arriva qui solo da `_handleFrame` fuori dalla chat e
     oltre la sua guardia: una chiusura, qui, è sempre del turno seguito. */
  _beforeChatState(msg) {
    if (this.mode === 'chat') return;
    switch (msg.event) {
      case 'delta':
        this._deltaBuffer += (msg.text || '');
        this._showReply(plainText(this._deltaBuffer));
        break;
      case 'stream_end':
        if (msg.text) this._showReply(plainText(msg.text));
        break;
      case 'message':
        if (msg.text && msg.kind !== 'tool_hint' && msg.kind !== 'progress') {
          this._showReply(plainText(msg.text));
        }
        break;
      case 'turn_end':
        this.awaiting = false;
        if (this._replyTimer) {
          clearTimeout(this._replyTimer);
          this._replyTimer = null;
        }
        if (!this._replyShown) this._showReply('✿');
        this._invalidateChatHistory();
        break;
      case 'error':
        this.awaiting = false;
        this._showReply(plainText(msg.detail || msg.reason || i18n.t('jenny.genericError')));
        break;
    }
  }

  /* Al cambio di conversazione si dimentica anche la minichat in volo: la
     sua attesa, il suo timer e il testo accumulato (v. il cappello di
     `_releaseTrackedTurn` in shared/jenny-mascot.js). */
  _releaseTrackedTurn() {
    this.awaiting = false;
    if (this._replyTimer) {
      clearTimeout(this._replyTimer);
      this._replyTimer = null;
    }
    this._deltaBuffer = '';
    super._releaseTrackedTurn();
  }

  _showReply(text) {
    if (!this.mc.classList.contains('open')) return;
    this._setAgentState('talking');
    this.bubble.textContent = text;
    this.mc.dataset.state = 'reply';
    this._replyShown = true;
  }

  /* Lo scambio è nello storico della sessione: al prossimo ingresso in chat
     la vista si ricarica per mostrarlo. */
  _invalidateChatHistory() {
    const chat = window.mobileApp?.controllers?.chat;
    if (chat && this.mode !== 'chat') chat.invalidateHistory();
  }
}
