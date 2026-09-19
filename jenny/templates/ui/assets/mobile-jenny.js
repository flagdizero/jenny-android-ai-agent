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
 */

import { AppState } from './shared/state.js';
import { wsManager } from './shared/ws-manager.js';
import { sessionManager } from './shared/session-manager.js';
import { i18n } from './shared/i18n.js';
import { bindMascotDrag, buildFlyLayer } from './shared/mascot-drag.js';
import {
  mascotVisible, mascotSide, setMascotSide, applyMascotSize,
} from './shared/mascot.js';

/* Arte "cotta" (faccia disegnata dentro, una sola img): serve al bordo, dove
   la mascotte sporge a metà e una faccia non si leggerebbe. */
const ART = {
  side: '/html-mobile/assets/jenny-side.webp',
  sideTalk: '/html-mobile/assets/jenny-side-talk.webp',
};
const SIDE_TALK_ANIM = [ART.side, ART.sideTalk];

/* ── Arte a due livelli (v. .agent/mascot-faces-plan.md) ──
   A mascotte intera il disegno è due img impilate sullo stesso quadrato: il
   CORPO porta il gesto, la FACCIA l'espressione. Sono ortogonali, quindi
   "triste mentre pensa" non è un disegno in più ma una composizione, e nel
   parlato sbatte solo la faccia (6 kB) invece di un corpo intero (22 kB). */
const BODY = {
  idle: '/html-mobile/assets/jenny-body-front-idle.webp',
  hand: '/html-mobile/assets/jenny-body-front-hand.webp',
  think: '/html-mobile/assets/jenny-body-front-think.webp',
};
/* `normal`/`talk` sono le due bocche del parlato; `thinking` è la faccia
   dell'attesa; le altre tre sono gli umori del backend. Nei sorgenti il nome
   senza suffisso è la faccia di RIPOSO di quell'espressione — per happy è un
   sorriso a bocca aperta, ed è giusto così. */
const FACE = {
  normal: '/html-mobile/assets/jenny-face-front-normal.webp',
  talk: '/html-mobile/assets/jenny-face-front-normal-talk.webp',
  thinking: '/html-mobile/assets/jenny-face-front-thinking.webp',
  happy: '/html-mobile/assets/jenny-face-front-happy.webp',
  sad: '/html-mobile/assets/jenny-face-front-sad.webp',
  angry: '/html-mobile/assets/jenny-face-front-angry.webp',
};
/* Il gesto del parlato alterna questi due ogni TALK_ANIM_SWITCH_MS. */
const TALK_BODIES = [BODY.idle, BODY.hand];
const MOUTH_FRAME_MS = 260; // apri/chiudi bocca
const TALK_ANIM_SWITCH_MS = 2600; // permanenza su una posa di parlato
const TALK_QUIET_TO_THINK_MS = 1000; // silenzio testo -> torna a pensa
const CONNECT_TIMEOUT_MS = 6000;
const REPLY_TIMEOUT_MS = 90000;
const REPLY_MAX_CHARS = 280;

/* ── Umore ──
   Dopo il turn_end il backend può mandare un frame `mascot_mood` con la
   reazione di Jenny alla risposta appena data (jenny/session/mascot_mood.py:
   una lettera chiesta al modello fuori dal turno). Qui è la FACCIA, non un
   quarto stato: si mostra solo a mascotte intera (dove c'è il livello), perde
   contro il pensa, e decade da sé dopo MOOD_HOLD_MS.

   Sono le etichette del backend (`MOODS` meno `neutral`, che non manda frame) e
   un sottoinsieme di FACE: le altre chiavi di FACE sono facce di stato, non
   umori, e un frame che ne nominasse una si scarta. Un contratto in
   tests/webui tiene allineate le due liste. */
const MOOD_FACES = ['happy', 'sad', 'angry'];
const MOOD_HOLD_MS = 12000; // quanto dura una faccia prima di tornare a normale

/* La fisica del volo pegman vive in `shared/mascot-drag.js`: la usano in due,
   la casa e l'officina, e una seconda copia di 427 righe di pendoli e rimbalzi
   sarebbe una seconda verita' da tenere allineata a mano — su codice che
   nessun test copre. Qui restano solo gli appigli che l'officina le passa. */

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

export class JennyCompanion {
  constructor() {
    this.mode = AppState.currentMode || 'chat';
    this.awaiting = false;
    this._replyShown = false;
    this._replyTimer = null;
    this._deltaBuffer = '';
    this._agentState = 'idle';
    this._turnActive = false;
    // Turno chiesto dalla minichat e non ancora concluso. È deliberatamente
    // *indipendente dalla UI*: `awaiting` e la classe `thinking` descrivono
    // cosa c'è a schermo e li azzera `_closeMini()`, questo descrive cosa c'è
    // in volo sul WebSocket e lo chiude solo `turn_end`/`error`.
    this._pendingTurn = false;
    // Id del turno che sta animando (v. `_trackedTurnMatches`): la mascotte ne
    // segue uno alla volta, e un turno estraneo non glielo deve togliere.
    this._streamTurnId = null;
    this._talk = {
      timer: null, animIdx: 0, open: false,
      lastTextAt: 0, switchAt: 0,
    };
    this._reducedMotion =
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    // Umore (v. MOOD_FACES): etichetta viva, scadenza, e l'id dell'ultimo turno
    // chiuso — un frame `mascot_mood` di un altro turno è una reazione a una
    // risposta che non è più l'ultima, e si scarta.
    this._mood = null;
    this._moodUntil = 0;
    this._moodTimer = null;
    this._lastClosedTurnId = null;

    this._buildDom();
    this._bindDrag();
    this._bindMinichat();

    // Preload di tutto ciò che può comparire: un corpo senza la sua faccia è
    // una Jenny senza volto, peggio di una bocca in ritardo.
    for (const src of [ART.sideTalk, ...Object.values(BODY), ...Object.values(FACE)]) {
      const im = new Image();
      im.src = src;
    }

    this._onWsMessage = (e) => this._handleWsMessage(e.detail);
    wsManager.addEventListener('chat:message', this._onWsMessage);
    this._onChatSent = (e) => this._handleChatSent(e.detail);
    wsManager.addEventListener('chat:sent', this._onChatSent);
    // Cambio di conversazione: v. `_releaseTrackedTurn`.
    this._onChatSwitch = () => this._releaseTrackedTurn();
    sessionManager.addEventListener('chat:switch', this._onChatSwitch);

    // Preferenze mascotte (Impostazioni → Personalizzazione): visibilità e
    // lato dello schermo, v. shared/mascot.js.
    this._onMascotChange = () => this._applyMascotPrefs();
    window.addEventListener('mascotchange', this._onMascotChange);

    AppState.on('currentMode', (mode) => this.setMode(mode));
    applyMascotSize();
    this._applySide();
    this.setMode(this.mode);

    // Da qui in poi la visibilità è governata a runtime dalla classe
    // hidden-mode su nodi creati da JS: rimuovi il ponte anti-flash
    // impostato al boot da bootstrap.js, altrimenti :root[data-mascotte-hidden]
    // continuerebbe a forzare display:none anche dopo che l'utente riattiva
    // la mascotte dalle Impostazioni.
    document.documentElement.removeAttribute('data-mascotte-hidden');

    // Android: escludi l'area di Jenny dalle gesture di sistema (v. sotto).
    this._onResize = () => this._updateGestureExclusion();
    window.addEventListener('resize', this._onResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', this._onResize);
    }
    // La transizione di right (docked <-> out) sposta il rettangolo: riallinea
    // a fine slide, così l'esclusione combacia con la posizione finale.
    // Docked <-> out transiziona su 'right' a destra, su 'left' a sinistra
    // (v. mobile-style.css .jenny-duo.side-left).
    this.el.addEventListener('transitionend', (e) => {
      if (e.propertyName === 'right' || e.propertyName === 'left') this._updateGestureExclusion();
    });
    this._updateGestureExclusion();
  }

  /* ── Gesture di sistema (Android) ──
     Jenny vive su un bordo verticale, e da entrambi uno swipe che parte da lì
     viene letto come back edge-swipe di sistema. Riportiamo la sua area (in px
     fisici) al bridge nativo JennyNative, che la esclude via
     setSystemGestureExclusionRects.
     No-op su WebView senza il bridge (browser desktop, ecc.). */
  _updateGestureExclusion() {
    const api = window.JennyNative;
    if (!api || typeof api.setGestureExclusion !== 'function') return;
    if (this.el.classList.contains('hidden-mode')) {
      try {
        api.clearGestureExclusion?.();
      } catch (_) {
        /* bridge assente */
      }
      return;
    }
    const r = this.el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const m = 8; // margine di sicurezza (px CSS) attorno all'hitbox
    try {
      api.setGestureExclusion(
        Math.round((r.left - m) * dpr),
        Math.round((r.top - m) * dpr),
        Math.round((r.right + m) * dpr),
        Math.round((r.bottom + m) * dpr),
      );
    } catch (_) {
      /* bridge assente */
    }
  }

  _buildDom() {
    const app = document.getElementById('app');

    this.scrim = document.createElement('button');
    this.scrim.className = 'jenny-scrim';
    this.scrim.setAttribute('aria-label', i18n.t('jenny.closeMinichat'));

    this.mc = document.createElement('div');
    this.mc.className = 'jenny-mc';
    this.mc.dataset.state = 'ask';
    this.mc.innerHTML = `
      <div class="jenny-mc-bubble"></div>
      <div class="jenny-mc-think">…</div>
      <form class="jenny-mc-ask compose-row">
        <div class="compose-pill">
          <input class="jenny-mc-input" type="text" placeholder="${i18n.t('jenny.askHere')}"
                 autocomplete="off" aria-label="${i18n.t('jenny.askJenny')}">
        </div>
        <button class="jenny-mc-send compose-send" type="submit" aria-label="${i18n.t('jenny.send')}" disabled>
          <i class="ti ti-arrow-up"></i>
        </button>
      </form>`;

    this.el = document.createElement('button');
    this.el.className = 'jenny-duo';
    this.el.setAttribute('aria-label', 'Jenny');
    this.el.setAttribute('tabindex', '-1');
    // I due livelli in un contenitore solo: lo specchio del lato sinistro va
    // su di lui (v. mobile-style.css), il respiro resta sulle img.
    const stack = document.createElement('div');
    stack.className = 'jenny-art-stack';
    const img = document.createElement('img');
    img.className = 'jenny-art';
    img.src = ART.side;
    img.alt = '';
    img.draggable = false;
    stack.appendChild(img);
    this.img = img;
    // La faccia nasce spenta: al primo render è al bordo, dove l'arte è cotta.
    const face = document.createElement('img');
    face.className = 'jenny-face off';
    face.src = FACE.normal;
    face.alt = '';
    face.draggable = false;
    stack.appendChild(face);
    this.face = face;
    this.el.appendChild(stack);

    // Layer del volo: le 5 pose impilate (stesso canvas condiviso, tutte
    // width:100%), visibili solo con .flying e una alla volta (.on, v. showEl).
    const { fly, flyPose } = buildFlyLayer(this.el);
    this.fly = fly;
    this.flyPose = flyPose;

    app.appendChild(this.scrim);
    app.appendChild(this.mc);
    app.appendChild(this.el);

    this.bubble = this.mc.querySelector('.jenny-mc-bubble');
    this.askForm = this.mc.querySelector('.jenny-mc-ask');
    this.input = this.mc.querySelector('.jenny-mc-input');
    this.sendBtn = this.mc.querySelector('.jenny-mc-send');

    // Come in chat: il send si accende solo quando c'è testo.
    this.input.addEventListener('input', () => {
      this.sendBtn.disabled = !this.input.value.trim();
    });

    // Stesso placeholder (e stessa lingua) della chat vera.
    const syncPlaceholder = () => {
      const t = i18n.t('chat.placeholder');
      if (t && t !== 'chat.placeholder') this.input.placeholder = t;
    };
    i18n.onLocaleChange(syncPlaceholder);
    i18n.load(i18n.locale).then(syncPlaceholder).catch(() => {});
  }

  /* ── Modalità vista ── */

  /* A mascotte intera (`out`) il disegno è a due livelli; al bordo è la posa
     cotta. Non dipende da altro: è la stessa condizione che il CSS usa per
     ancorarla, e in volo il layer .jenny-fly copre tutto comunque. */
  _layered() {
    return this.el.classList.contains('out');
  }

  _setBody(src) {
    if (this.img.getAttribute('src') !== src) this.img.src = src;
  }

  /* `null` spegne la faccia (arte cotta). Si nasconde con una classe e non con
     display: v. il commento in mobile-style.css sulle due animazioni. */
  _setFace(src) {
    if (!src) {
      this.face.classList.add('off');
      return;
    }
    if (this.face.getAttribute('src') !== src) this.face.src = src;
    this.face.classList.remove('off');
  }

  /* L'espressione, in precedenza stretta: il pensa vince sull'umore.
     Aspettare una risposta è uno stato, non un sentimento, e una faccia felice
     mentre lei sta ancora pensando racconterebbe una cosa falsa. */
  _faceKey() {
    if (this._agentState === 'thinking') return 'thinking';
    return this._moodFace() || 'normal';
  }

  /* Riallinea le immagini allo stato corrente (dopo un drag, un tap o un
     cambio di stato dell'agente). */
  _syncArt() {
    if (this._talk.timer) return; // il frame lo gestisce l'animatore del parlato
    if (!this._layered()) {
      this._setBody(ART.side);
      this._setFace(null);
      return;
    }
    this._setBody(this._agentState === 'thinking' ? BODY.think : BODY.idle);
    this._setFace(FACE[this._faceKey()]);
  }

  /* Stato logico dell'agente. In docked (chat senza out) lo stato 'thinking'
     non ha effetto visivo: Jenny resta sul bordo, side statico. */
  _setAgentState(state) {
    // Un segnale di parlato tiene viva la bocca **anche a stato invariato**: i
    // delta di un flusso lungo arrivano tutti come 'talking' e la guardia qui
    // sotto li scarterebbe tutti tranne il primo. Allora dopo
    // TALK_QUIET_TO_THINK_MS l'animatore tornerebbe al pensa in mezzo alla
    // frase e, ripartendo, rimetterebbe animIdx a zero: il gesto del parlato
    // non cambierebbe mai. Misurato sul telefono l'08/09/2026, 23 scatti su 9
    // secondi di parlato sempre a braccia giù.
    if (state === 'talking') this._noteTalkActivity();
    if (this._agentState === state) return;
    this._agentState = state;
    const docked = this.mode === 'chat' && !this.el.classList.contains('out');

    // Un turno che riparte rende stantia la faccia del turno prima: una
    // risposta neutra non manderebbe niente e la vecchia faccia riapparirebbe a
    // parlato finito. Da qui in poi l'espressione la porta lo stato: `thinking`
    // ha la sua faccia, e non serve un timer per dargliela.
    if (state !== 'idle') this._clearMood();
    if (state === 'talking') {
      this.el.classList.remove('thinking');
    } else if (state === 'thinking') {
      if (!docked) this.el.classList.add('thinking');
      this._stopTalk();
    } else {
      this.el.classList.remove('thinking');
      this._stopTalk();
    }
    this._syncArt();
  }

  /* ── Umore ── */

  /* Il frame `mascot_mood` del backend. Accettato solo se è la reazione
     all'ultimo turno chiuso e nessun altro turno è in corso: una faccia per
     una risposta che non è più l'ultima confonderebbe più di nessuna faccia. */
  _onMoodFrame(msg) {
    if (!this._acceptMood(msg)) return;
    this._applyMood(msg.mood);
  }

  _acceptMood(msg) {
    if (!msg || !MOOD_FACES.includes(msg.mood)) return false;
    if (this._turnActive || this._pendingTurn) return false;
    const turnId = msg.turn_id || msg.turnId || null;
    if (turnId && this._lastClosedTurnId && turnId !== this._lastClosedTurnId) return false;
    return true;
  }

  /* Ricorda quale turno si è chiuso: è il metro con cui _acceptMood giudica il
     frame che arriva dopo. Un frame di chiusura senza id (retry) vale per il
     turno che stava seguendo. */
  _noteTurnClosed(msg) {
    this._lastClosedTurnId = msg.turn_id || msg.turnId || this._streamTurnId || null;
  }

  _applyMood(mood, now = performance.now()) {
    if (!MOOD_FACES.includes(mood)) return;
    this._mood = mood;
    this._moodUntil = now + MOOD_HOLD_MS;
    if (this._moodTimer) clearTimeout(this._moodTimer);
    this._moodTimer = setTimeout(() => this._clearMood(), MOOD_HOLD_MS);
    this._syncArt();
  }

  _clearMood() {
    if (this._moodTimer) {
      clearTimeout(this._moodTimer);
      this._moodTimer = null;
    }
    if (!this._mood) return;
    this._mood = null;
    this._moodUntil = 0;
    this._syncArt();
  }

  /* L'umore vivo, o null. Che dal bordo non si veda non è una regola scritta
     qui: là l'arte è cotta e la faccia è spenta, e se la si richiama entro il
     tempo la trova ancora. */
  _moodFace(now = performance.now()) {
    if (!this._mood || now >= this._moodUntil) return null;
    return this._mood;
  }

  /* ── Parlato animato ── */

  /* Ogni testo nuovo tiene viva la bocca; il primo avvia l'animatore. */
  _noteTalkActivity() {
    if (this._reducedMotion) return;
    const now = performance.now();
    this._talk.lastTextAt = now;
    if (this._talk.timer) return;
    this._talk.animIdx = 0;
    this._talk.open = false;
    this._talk.switchAt = now + TALK_ANIM_SWITCH_MS;
    this._talk.timer = setInterval(() => this._talkTick(), MOUTH_FRAME_MS);
    this._talkTick();
  }

  _talkTick() {
    const now = performance.now();
    // Silenzio nel flusso: torna allo stato 'pensa' invece di tenere la bocca
    // congelata in posa di parlato.
    if (now - this._talk.lastTextAt > TALK_QUIET_TO_THINK_MS) {
      this._setAgentState('thinking');
      return;
    }
    this._talk.open = !this._talk.open;
    if (!this._layered()) {
      // Dal bordo la posa è unica: la coppia cotta side/side-talk. La faccia si
      // spegne qui e non solo in _syncArt: se la trascinano al bordo *mentre*
      // parla, _syncArt esce subito (il frame è dell'animatore) e la faccia
      // resterebbe accesa sopra un'arte che ce l'ha già dentro.
      this._setBody(SIDE_TALK_ANIM[this._talk.open ? 1 : 0]);
      this._setFace(null);
      return;
    }
    if (now >= this._talk.switchAt) {
      this._talk.animIdx = (this._talk.animIdx + 1) % TALK_BODIES.length;
      this._talk.switchAt = now + TALK_ANIM_SWITCH_MS;
    }
    this._setBody(TALK_BODIES[this._talk.animIdx]);
    this._setFace(FACE[this._talk.open ? 'talk' : 'normal']);
  }

  /* Chiude il parlato e torna all'arte statica (idle/side/think). */
  _stopTalk() {
    if (this._talk.timer) {
      clearInterval(this._talk.timer);
      this._talk.timer = null;
    }
    this._syncArt();
  }

  setMode(mode) {
    this.mode = mode;
    this._agentState = 'idle';
    this._turnActive = false;
    this._streamTurnId = null;
    if (this._abortFlight) this._abortFlight();
    this._closeMini();
    // Nascosta durante l'onboarding, oppure per preferenza utente
    // (Impostazioni → Personalizzazione → mascotte visibile, v. shared/mascot.js).
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

  /* Riallinea visibilità e lato quando l'utente cambia le preferenze da
     Impostazioni → Personalizzazione (evento 'mascotchange'). Le img del volo
     hanno src fisso a creazione e non si ricablano più: da quando l'arte ha
     una sola variante, il loro path non dipende da nessuna preferenza. */
  _applyMascotPrefs() {
    this._applySide();
    this.setMode(this.mode);
    if (!this._talk.timer) this._syncArt();
  }

  /* Lato dello schermo (mirroring completo, v. mobile-style.css .side-left):
     arte specchiata + ancoraggi minichat riflessi via CSS su questa classe.
     Non è una preferenza: è dove l'hai lasciata l'ultima volta, quindi lo
     stato si scrive qui e non passa dall'evento 'mascotchange'. */
  _setSide(side) {
    const left = side === 'left';
    this.el.classList.toggle('side-left', left);
    this.mc.classList.toggle('side-left', left);
    setMascotSide(side);
    this._updateGestureExclusion();
  }

  _applySide() {
    this._setSide(mascotSide());
  }

  /* ── Drag / tap ── */

  _bindDrag() {
    /* La fisica e gli ancoraggi stanno in `shared/mascot-drag.js`. Qui resta
       solo cio' che l'officina fa in modo suo: la minichat da chiudere quando
       il trascinamento comincia. Lo stato `out` e il tocco che lo gira sono di
       tutti e due i gusci (v. casa-mascot.js). */
    this._abortFlight = bindMascotDrag({
      el: this.el,
      fly: this.fly,
      flyPose: this.flyPose,
      isOut: () => this.el.classList.contains('out'),
      setOut: (v) => this._setOut(v),
      onDragCommit: () => {
        if (this.mc.classList.contains('open')) this._closeMini();
      },
      onTap: () => this._setOut(!this.el.classList.contains('out')),
      onSideChange: (side) => this._setSide(side),
      onFlightEnd: () => {
        this._syncArt();
        this._updateGestureExclusion();
      },
    });

    this.scrim.addEventListener('click', () => this._setOut(false));
  }

  _setOut(out) {
    this.el.classList.toggle('out', out);
    if (this.mode !== 'chat') {
      this.el.classList.toggle('mini', out);
      if (out) this._openMini();
      else this._closeMini();
    }
    this._syncArt();
    this._updateGestureExclusion();
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

  _handleWsMessage(msg) {
    // La conversazione aperta, nella forma che portano i frame: la regola della
    // conversione sta in un posto solo (`ws-manager.chatIdOf`), e deve essere la
    // stessa con cui la chat decide cosa rendere.
    const current = sessionManager.currentChatId;
    if (msg.chat_id && current && msg.chat_id !== current) return;
    // L'umore arriva dopo il turn_end, quando non c'è più niente "a schermo"
    // che la guardia sotto riconosca: si tratta prima, e in entrambe le viste.
    if (msg.event === 'mascot_mood') {
      this._onMoodFrame(msg);
      return;
    }
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

    switch (msg.event) {
      case 'delta':
        this._setAgentState('talking');
        this._deltaBuffer += (msg.text || '');
        this._showReply(plainText(this._deltaBuffer));
        break;
      case 'stream_end':
        if (msg.text) this._showReply(plainText(msg.text));
        this._setAgentState(this._turnActive ? 'thinking' : 'idle');
        break;
      case 'message':
        if (msg.text && msg.kind !== 'tool_hint' && msg.kind !== 'progress') {
          this._setAgentState('talking');
          this._showReply(plainText(msg.text));
        } else if (msg.tool_events || msg.kind === 'tool_hint' || msg.kind === 'progress') {
          this._setAgentState('thinking');
        }
        break;
      case 'reasoning_delta':
        this._setAgentState('thinking');
        break;
      case 'reasoning_end':
        break;
      case 'file_edit':
        this._setAgentState('thinking');
        break;
      case 'goal_status':
        if (msg.status === 'running') {
          this._turnActive = true;
          this._setAgentState('thinking');
        } else if (msg.status === 'idle') {
          this._turnActive = false;
          this._setAgentState('idle');
        }
        break;
      case 'turn_end':
        this._turnActive = false;
        this._pendingTurn = false;
        this._noteTurnClosed(msg);
        this._streamTurnId = null;
        this.awaiting = false;
        if (this._replyTimer) {
          clearTimeout(this._replyTimer);
          this._replyTimer = null;
        }
        if (!this._replyShown) this._showReply('✿');
        this._setAgentState('idle');
        this._invalidateChatHistory();
        break;
      case 'error':
        this._turnActive = false;
        this._pendingTurn = false;
        this._noteTurnClosed(msg);
        this._streamTurnId = null;
        this.awaiting = false;
        this._showReply(plainText(msg.detail || msg.reason || i18n.t('jenny.genericError')));
        this._setAgentState('idle');
        this._applyMood('sad'); // livello 0: l'errore ha la sua faccia, gratis
        break;
    }
  }

  /* ── Chat principale: la mascotte segue la conversazione vera ──
     Out = pensa e parla come in minichat; docked = salta il "pensa" e parla
     nella versione semplificata side/side-talk (la coppia la sceglie il tick). */

  _handleChatSent(detail) {
    if (this.mode !== 'chat') return;
    const current = sessionManager.currentChatId;
    if (detail?.chat_id && current && detail.chat_id !== current) return;
    this._turnActive = true;
    this._clearMood(); // sta ascoltando, non sta ancora reagendo
    if (!this.el.classList.contains('out')) return; // docked: niente pensa visibile
    this._setAgentState('thinking'); // _syncArt -> think
  }

  /* Cambio di conversazione: la mascotte lascia il turno che stava seguendo.

     Ne anima uno alla volta e resta sul proprio finché non si chiude
     (`_trackedTurnMatches`): giusto contro un avviso proattivo che atterra in
     mezzo, fatale al cambio di chat, perché il `turn_end` di quel turno lo
     scarta il filtro sul `chat_id` qui sopra — non arriverà mai, e lei
     resterebbe a pensare per sempre. È lo stesso incantesimo già visto quando
     una consegna proattiva non emetteva `turn_end`, per un'altra porta.

     Non "chiude" il turno: lo dimentica. Nessuna risposta da mostrare, nessuna
     cronologia da invalidare — quella la ricarica la chat, che il thread lo
     ridisegna da sé. */
  _releaseTrackedTurn() {
    this._turnActive = false;
    this._pendingTurn = false;
    this._streamTurnId = null;
    this.awaiting = false;
    if (this._replyTimer) {
      clearTimeout(this._replyTimer);
      this._replyTimer = null;
    }
    this._deltaBuffer = '';
    this._clearMood();
    this._setAgentState('idle');
  }

  /* Il turno che la mascotte sta seguendo.

     Regola opposta a quella della chat, e per una ragione: la chat *rende*
     tutti i turni, quindi a ogni cambio d'id apre una bolla nuova; la mascotte
     ne **anima uno solo**, e deve restare su quello finché non si chiude. Se
     adottasse l'id di un avviso proattivo atterrato in mezzo a una risposta,
     il `turn_end` della risposta non combacerebbe più con nulla e lei
     resterebbe animata per sempre — cioè di nuovo il difetto da cui siamo
     partiti, da un'altra porta.

     Quindi: a turno fermo si adotta l'id del primo frame che lo apre; a turno
     in corso lo si tiene. Un frame senza id vale sempre per il turno corrente
     (il retry di una consegna parziale arriva senza annotazione). */
  _trackedTurnMatches(msg) {
    const turnId = msg.turn_id || msg.turnId || null;
    if (!turnId) return true;
    if (this._streamTurnId === null) {
      // Una chiusura non apre mai un tracciamento — chiuderebbe un turno che
      // non abbiamo mai visto aprirsi — ma resta permissiva: è il caso della
      // minichat chiusa a metà turno, dove i frame intermedi sono stati
      // scartati perché non c'era niente a schermo, e ignorare anche il
      // `turn_end` lascerebbe `_pendingTurn` alzato per sempre.
      if (msg.event !== 'turn_end' && msg.event !== 'error') this._streamTurnId = turnId;
      return true;
    }
    return this._streamTurnId === turnId;
  }

  _handleChatStream(msg) {
    const mine = this._trackedTurnMatches(msg);
    switch (msg.event) {
      case 'delta':
        this._setAgentState('talking');
        break;
      case 'stream_end':
        this._setAgentState(this._turnActive ? 'thinking' : 'idle');
        break;
      case 'message':
        if (msg.text && msg.kind !== 'tool_hint' && msg.kind !== 'progress') {
          this._setAgentState('talking');
        } else {
          this._setAgentState('thinking');
        }
        break;
      case 'reasoning_delta':
        this._setAgentState('thinking');
        break;
      case 'reasoning_end':
        break;
      case 'file_edit':
        this._setAgentState('thinking');
        break;
      case 'goal_status':
        if (msg.status === 'running') {
          this._turnActive = true;
          this._setAgentState('thinking');
        } else if (msg.status === 'idle') {
          this._turnActive = false;
          this._setAgentState('idle');
        }
        break;
      case 'turn_end':
      case 'error':
        // La chiusura di un turno che non stiamo seguendo (un avviso proattivo
        // atterrato in mezzo a una risposta) non ci riguarda: la risposta vera
        // sta ancora arrivando.
        if (!mine) break;
        this._turnActive = false;
        this._noteTurnClosed(msg);
        this._streamTurnId = null;
        // Un turno partito dalla minichat e concluso dopo essere passati nella
        // sezione chat: il flag va chiuso anche qui, altrimenti resterebbe
        // alzato per sempre (qui lo storico non serve invalidarlo, la chat è
        // la vista attiva e riceve lo stream da sé).
        this._pendingTurn = false;
        this._setAgentState('idle');
        if (msg.event === 'error') this._applyMood('sad'); // livello 0, gratis
        break;
    }
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
