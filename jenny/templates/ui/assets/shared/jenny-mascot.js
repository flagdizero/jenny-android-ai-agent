/** Jenny — la mascotte, una sola per tutti e due i gusci.
 *
 *  Fino al 24/09/2026 le mascotte erano due: `mobile-jenny.js` in officina e
 *  `casa-mascot.js` in casa, con la fisica e gli ancoraggi in comune ma il
 *  *cervello* — stati, parlato, umore, lettura dei frame — scritto due volte.
 *  Le due copie si erano allontanate: in casa mancava il gesto della mano nel
 *  parlato, un umore vivo congelava la bocca per dodici secondi, l'errore non
 *  aveva la sua faccia, e un avviso proattivo atterrato in mezzo a una
 *  risposta la fermava a meta'. Nessuna di queste era una scelta.
 *
 *  Adesso il cervello e' qui, ed e' quello dell'officina: il ramo che aveva gia'
 *  preso le correzioni misurate sul telefono. **Fra i due gusci cambia solo il
 *  pavimento** — dove appoggia i piedi, che e' una regola del foglio di stile
 *  di ciascuno (`.jenny-duo` in mobile-style.css, `.casa-shell .jenny-duo` in
 *  casa-style.css). L'officina ci aggiunge sopra la minichat
 *  (`mobile-jenny.js`, sottoclasse), perche' la' la chat puo' non essere a
 *  schermo; in casa la chat *e'* lo schermo.
 *
 *  Questa classe e' Jenny nella chat vera: presente all'angolo, segue la
 *  conversazione aperta, si prende e si lancia, e un tocco la manda al bordo o
 *  la fa tornare fuori.
 */

import { wsManager } from './ws-manager.js';
import { sessionManager } from './session-manager.js';
import { bindMascotDrag, buildFlyLayer } from './mascot-drag.js';
import {
  mascotVisible, mascotSide, setMascotSide, applyMascotSize,
} from './mascot.js';

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

export class JennyMascot {
  /**
   * @param {HTMLElement} host  dove vive lo sprite (`#app` in officina,
   *   `.casa-shell` in casa): e' il suo riferimento per `position: absolute`.
   * @param {{mode?: string}} [opts]  la vista di partenza. Conta solo per
   *   l'officina, che ne ha piu' d'una; qui e' sempre `chat`.
   */
  constructor(host, { mode = 'chat' } = {}) {
    this.host = host;
    this.mode = mode;
    this._agentState = 'idle';
    this._turnActive = false;
    // Turno chiesto dalla minichat e non ancora concluso (v. mobile-jenny.js).
    // Sta qui e non nella sottoclasse perche' lo legge la guardia dell'umore:
    // in casa non si alza mai, e la guardia resta la stessa.
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

    // Preferenze mascotte (Impostazioni → Personalizzazione): visibilità,
    // taglia e lato dello schermo, v. shared/mascot.js.
    this._onMascotChange = () => this._applyMascotPrefs();
    window.addEventListener('mascotchange', this._onMascotChange);

    applyMascotSize();
    this._applySide();
    this._enterInitialState();

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

  /* Nella chat vera Jenny nasce fuori, all'angolo sopra il campo di scrittura.
     L'officina, che ha altre viste, lo decide da `setMode`. */
  _enterInitialState() {
    this.el.classList.add('in-chat', 'out');
    this._applyVisibility();
    this._syncArt();
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
    this.el = document.createElement('button');
    this.el.type = 'button';
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

    this.host.appendChild(this.el);
  }

  /* ── Arte ── */

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

  /* ── Preferenze ── */

  /* Visibile o spenta (Impostazioni → Personalizzazione, v. shared/mascot.js).
     L'officina la spegne anche durante l'onboarding, e lo fa da `setMode`. */
  _applyVisibility() {
    this.el.classList.toggle('hidden-mode', !mascotVisible());
    this._updateGestureExclusion();
  }

  /* Riallinea visibilità e lato quando l'utente cambia le preferenze da
     Impostazioni → Personalizzazione (evento 'mascotchange'). Le img del volo
     hanno src fisso a creazione e non si ricablano più: da quando l'arte ha
     una sola variante, il loro path non dipende da nessuna preferenza. */
  _applyMascotPrefs() {
    this._applySide();
    this._applyVisibility();
    if (!this._talk.timer) this._syncArt();
  }

  /* Lato dello schermo (mirroring completo, v. mobile-style.css .side-left).
     Non è una preferenza: è dove l'hai lasciata l'ultima volta, quindi lo
     stato si scrive qui e non passa dall'evento 'mascotchange'. */
  _setSide(side) {
    this.el.classList.toggle('side-left', side === 'left');
    setMascotSide(side);
    this._updateGestureExclusion();
  }

  _applySide() {
    this._setSide(mascotSide());
  }

  /* ── Drag / tap ── */

  _bindDrag() {
    /* La fisica e gli ancoraggi stanno in `shared/mascot-drag.js`; lo stato
       `out` e il tocco che lo gira sono qui. */
    this._abortFlight = bindMascotDrag({
      el: this.el,
      fly: this.fly,
      flyPose: this.flyPose,
      isOut: () => this.el.classList.contains('out'),
      setOut: (v) => this._setOut(v),
      onDragCommit: () => this._onDragCommit(),
      onTap: () => this._setOut(!this.el.classList.contains('out')),
      onSideChange: (side) => this._setSide(side),
      onFlightEnd: () => {
        this._syncArt();
        this._updateGestureExclusion();
      },
    });
  }

  /* Il trascinamento e' partito davvero. Qui non c'e' niente da chiudere; in
     officina la minichat. */
  _onDragCommit() {}

  /** Al bordo (`false`) o venuta fuori (`true`).
   *
   *  Non la gira solo il tocco: la fisica chiama qui a volo finito, e la casa
   *  la manda al bordo quando lasci la chat per un'altra stanza. */
  setOut(out) {
    this._setOut(out);
  }

  _setOut(out) {
    this.el.classList.toggle('out', out);
    this._onOutChange(out);
    this._syncArt();
    this._updateGestureExclusion();
  }

  /* Appiglio per chi ha qualcosa da aprire quando lei esce (la minichat). */
  _onOutChange(_out) {}

  /* ── I frame della conversazione ── */

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
    this._handleFrame(msg);
  }

  /* Dove va un frame della conversazione aperta. Qui sempre alla chat vera;
     l'officina, fuori dalla chat, lo manda alla minichat. */
  _handleFrame(msg) {
    this._handleChatStream(msg);
  }

  /* ── Chat principale: la mascotte segue la conversazione vera ──
     Out = pensa e parla; docked = salta il "pensa" e parla nella versione
     semplificata side/side-talk (la coppia la sceglie il tick). */

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
}
