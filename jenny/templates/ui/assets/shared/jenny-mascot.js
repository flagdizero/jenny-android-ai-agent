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
 *  pavimento** — dove appoggia i piedi, cioe' `--jenny-floor`, che ciascun
 *  foglio di stile dichiara per se' (`:root` in mobile-style.css,
 *  `.home-shell` in home-style.css).
 *
 *  Dal 05/10/2026 e' anche **la porta della chat** (v. `_onTap`): dove la chat
 *  non e' a schermo il tocco la porta su, sopra la pagina; dove e' aperta sopra
 *  la pagina la chiude; dove e' a schermo da se' lei saluta. Fino ad allora,
 *  dove la chat non c'era, apriva la minichat (`shared/jenny-minichat.js`, una
 *  sottoclasse con una domanda a un turno in un fumetto), che non c'e' piu'.
 *
 *  Per il resto e' Jenny nella chat vera: presente all'angolo, segue la
 *  conversazione aperta, si prende e si lancia.
 */

import { wsManager } from './ws-manager.js';
import { sessionManager } from './session-manager.js';
import { bindMascotDrag, buildFlyLayer } from './mascot-drag.js';
import { botName } from './bot-name.js';
import { applyMascotSize } from './mascot.js';

/* Arte "cotta" (faccia disegnata dentro, una sola img): serve al bordo, dove
   la mascotte sporge a metà e una faccia non si leggerebbe. */
const ART = {
  side: '/html-mobile/assets/jenny-side.webp',
  sideTalk: '/html-mobile/assets/jenny-side-talk.webp',
};
const SIDE_TALK_ANIM = [ART.side, ART.sideTalk];

/* ── Arte a due livelli ──
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
/* Di lato, al bordo, con un umore vivo: il corpo della posa cotta senza la
   faccia, e la faccia dell'umore sopra — la stessa composizione a due livelli
   di quando e' fuori, sull'orientamento `side`. Senza umore resta la posa
   cotta (`ART.side`): la faccia neutra di lato non c'e', e non serve.
   Fino al 28/09/2026 al bordo l'umore non si vedeva: «da docked una faccia non
   si legge», si era detto, e i sorgenti di lato erano rimasti in riserva in
   android/image_source/. Dall'utente: le espressioni devono agganciarsi anche
   li'. */
const SIDE_BODY = '/html-mobile/assets/jenny-body-side-idle.webp';
const SIDE_FACE = {
  happy: '/html-mobile/assets/jenny-face-side-happy.webp',
  sad: '/html-mobile/assets/jenny-face-side-sad.webp',
  angry: '/html-mobile/assets/jenny-face-side-angry.webp',
};
/* Il gesto del parlato alterna questi due ogni TALK_ANIM_SWITCH_MS. */
const TALK_BODIES = [BODY.idle, BODY.hand];
const MOUTH_FRAME_MS = 260; // apri/chiudi bocca
const TALK_ANIM_SWITCH_MS = 2600; // permanenza su una posa di parlato
const TALK_QUIET_TO_THINK_MS = 1000; // silenzio testo -> torna a pensa

/* ── Umore ──
   Dopo il turn_end il backend può mandare un frame `mascot_mood` con la
   reazione di Jenny alla risposta appena data (jenny/session/mascot_mood.py:
   letta dagli emoji che lei stessa ha scritto). Qui è la FACCIA, non un
   quarto stato: fuori da FACE, al bordo da SIDE_FACE; perde contro il pensa,
   e decade da sé dopo MOOD_HOLD_MS.

   Sono le etichette del backend (`MOODS` meno `neutral`, che non manda frame) e
   un sottoinsieme di FACE: le altre chiavi di FACE sono facce di stato, non
   umori, e un frame che ne nominasse una si scarta. Un contratto in
   tests/webui tiene allineate le due liste. */
const MOOD_FACES = ['happy', 'sad', 'angry'];
const MOOD_HOLD_MS = 12000; // quanto dura una faccia prima di tornare a normale

/* Il saluto vero: i due fotogrammi col braccio alzato dell'onboarding, che
   si alternano nel livello `.jenny-wave` (v. `_buildDom`). */
const WAVE = {
  a: '/html-mobile/assets/jenny-hello1.webp',
  b: '/html-mobile/assets/jenny-hello2.webp',
};

/* ── I gesti ──
   Un gesto e' un'animazione CSS a tempo, su uno di due livelli annidati:
   `lean` e' l'inclinazione del moto (`.jenny-lean`), `squash` lo schiaccia e
   allunga del corpo (`.jenny-squash`). Ognuno ne porta uno alla volta, e i
   due si sommano perche' sono due elementi, non due animazioni sulla stessa
   proprieta'. Il gesto sta in `data-lean`/`data-squash`; la durata e' **solo
   qui**: `_motion` la scrive in `--jenny-<canale>-ms`, che il CSS legge, cosi'
   il timer che toglie l'attributo e l'animazione non possono divergere.

   Dall'utente del 05/10/2026, sui mockup «Il saluto di Jenny» e «Jenny in
   movimento»: squash and stretch su tutto, mai in diagonale. */
const MOTIONS = {
  lean: {
    // Esce dal bordo (verso l'interno) o ci torna: si sporge nel verso in cui
    // va, frena, si assesta. Una rotazione attorno ai piedi, non un taglio.
    'go-in': 480,
    'go-edge': 480,
    // Il cambio di gesto nel parlato (braccia giu' / mano su): uno scatto
    // invece del cambio di colpo.
    gest: 300,
  },
  squash: {
    // Si carica, salta alzando il braccio, saluta tre volte ondeggiando a tempo
    // col braccio, si assesta. Sostituisce mano alzata + occhi felici.
    greet: 1500,
    // Il saltello: il saluto quando un braccio alzato direbbe una cosa falsa
    // (pensa, parla) o non si vedrebbe (al bordo).
    hop: 1000,
    // «Sono qui, chiudi da me»: lo chiede il guscio quando tocchi il velo
    // dietro la chat, che apposta non la chiude. Si carica e si sporge verso
    // di te.
    nudge: 520,
    // L'umore arriva con una reazione del corpo (i nomi sono quelli di
    // MOOD_FACES): un saltello, un afflosciarsi, una pestata.
    happy: 700,
    sad: 1800,
    angry: 650,
    // Il pallino si accende e lei se ne accorge.
    perk: 340,
    // L'atterraggio a fine volo, quando le pose lasciano il posto al riposo.
    land: 300,
    // Una sillaba: ogni volta che apre la bocca si allunga di un niente.
    syl: 260,
  },
};
/* Le variabili da cui il CSS legge la durata del gesto di ogni canale. */
const MOTION_VARS = { lean: '--jenny-lean-ms', squash: '--jenny-squash-ms' };
/* Quanto dura lo scivolamento dal bordo (`transition: right` di
   `.jenny-duo`): il saluto aspetta che lei sia fuori del tutto, perche' un
   braccio alzato mentre e' ancora mezza dietro il bordo non si vedrebbe. */
const SLIDE_MS = 300;

export class JennyMascot {
  /**
   * @param {HTMLElement} host  dove vive lo sprite (`#app` in officina,
   *   `.home-shell` in casa): e' il suo riferimento per `position: absolute`.
   * @param {{mode?: string, chat?: object}} [opts]  `mode` e' la vista di
   *   partenza (conta solo per l'officina, che ne ha piu' d'una). `chat` e' la
   *   porta della conversazione, l'adattatore del guscio:
   *   `{ isOpen(), onScreen(), canOpen?(), open({gesture}), close() }` —
   *   `isOpen` vuol dire «la chat e' aperta SOPRA una pagina», `onScreen` «la
   *   chat e' a schermo da se'» (la pagina chat, la Console). Senza, il tocco
   *   la manda al bordo o la fa uscire, come prima del 05/10/2026.
   */
  constructor(host, { mode = 'chat', chat = null } = {}) {
    this.host = host;
    this.mode = mode;
    this._chat = chat;
    // I gesti in corso, uno per canale (v. MOTIONS), e i timer che li tolgono.
    this._motionNow = { lean: null, squash: null };
    this._motionTimers = { lean: null, squash: null };
    // Il saluto che aspetta la fine dello scivolamento (v. `hello`).
    this._greetTimer = null;
    this._slideUntil = 0;
    // Cosa aggiungere al nome nell'etichetta (v. `setBadge`).
    this._labelNote = '';
    this._agentState = 'idle';
    this._turnActive = false;
    // Id del turno che sta animando (v. `_trackedTurnMatches`): la mascotte ne
    // segue uno alla volta, e un turno estraneo non glielo deve togliere.
    this._streamTurnId = null;
    // Dall'ultimo invio a riposo e' arrivato `goal_status: running`, cioe' e'
    // partito un turno vero? Senza, un messaggio e' la risposta di un comando
    // (v. `_isCommandReply`).
    this._runSeen = false;
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
    for (const src of [
      ART.sideTalk, ...Object.values(BODY), ...Object.values(FACE),
      SIDE_BODY, ...Object.values(SIDE_FACE), ...Object.values(WAVE),
    ]) {
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
    // Filo caduto: v. `_onWireClose`.
    this._onChatClose = () => this._onWireClose();
    wsManager.addEventListener('chat:close', this._onChatClose);

    // Preferenze mascotte (la stanza «Jenny» della casa): la taglia, v.
    // shared/mascot.js.
    this._onMascotChange = () => this._applyMascotPrefs();
    window.addEventListener('mascotchange', this._onMascotChange);

    applyMascotSize();
    this._enterInitialState();

    // Android: escludi l'area di Jenny dalle gesture di sistema (v. sotto).
    this._onResize = () => this._updateGestureExclusion();
    window.addEventListener('resize', this._onResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', this._onResize);
    }
    // La transizione di right (docked <-> out) sposta il rettangolo: riallinea
    // a fine slide, così l'esclusione combacia con la posizione finale.
    this.el.addEventListener('transitionend', (e) => {
      if (e.propertyName === 'right') this._updateGestureExclusion();
    });
    this._updateGestureExclusion();
  }

  /* Nella chat vera Jenny nasce fuori, all'angolo sopra il campo di scrittura.
     L'officina, che ha altre viste, lo decide da `setMode`. */
  _enterInitialState() {
    this.el.classList.add('in-chat', 'out');
    this._syncArt();
  }

  /* ── Gesture di sistema (Android) ──
     Jenny vive sul bordo destro, e uno swipe che parte da lì viene letto
     come back edge-swipe di sistema. Riportiamo la sua area (in px
     fisici) al bridge nativo JennyNative, che la esclude via
     setSystemGestureExclusionRects.
     No-op su WebView senza il bridge (browser desktop, ecc.). */
  _updateGestureExclusion() {
    const api = window.JennyNative;
    if (!api || typeof api.setGestureExclusion !== 'function') return;
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
    // Il nome di lei, e resta giusto se la si rinomina a pagina aperta.
    this._paintLabel();
    this._offName = botName.onChange(() => this._paintLabel());
    this.el.setAttribute('tabindex', '-1');
    /* Un livello per trasformazione (v. MOTIONS), dal piu' esterno:
       l'inclinazione, lo schiaccia e allunga, l'arte. Nell'arte il riposo
       (corpo e faccia) e il saluto sono due livelli fratelli: il saluto si
       accende sopra senza toccare le img del riposo, che hanno il respiro e
       non lo devono perdere. */
    const lean = document.createElement('div');
    lean.className = 'jenny-lean';
    const squash = document.createElement('div');
    squash.className = 'jenny-squash';
    const stack = document.createElement('div');
    stack.className = 'jenny-art-stack';
    const rest = document.createElement('div');
    rest.className = 'jenny-rest';
    const img = document.createElement('img');
    img.className = 'jenny-art';
    img.src = ART.side;
    img.alt = '';
    img.draggable = false;
    rest.appendChild(img);
    this.img = img;
    // La faccia nasce spenta: al primo render è al bordo, dove l'arte è cotta.
    const face = document.createElement('img');
    face.className = 'jenny-face off';
    face.src = FACE.normal;
    face.alt = '';
    face.draggable = false;
    rest.appendChild(face);
    this.face = face;
    stack.appendChild(rest);
    // I due fotogrammi del saluto, ciascuno nel suo contenitore: e' il
    // contenitore che il CSS accende, l'img tiene il respiro come le altre.
    const wave = document.createElement('div');
    wave.className = 'jenny-wave';
    for (const [key, src] of Object.entries(WAVE)) {
      const frame = document.createElement('div');
      frame.className = `jenny-wave-${key}`;
      const im = document.createElement('img');
      im.src = src;
      im.alt = '';
      im.draggable = false;
      frame.appendChild(im);
      wave.appendChild(frame);
    }
    stack.appendChild(wave);
    squash.appendChild(stack);
    lean.appendChild(squash);
    this.el.appendChild(lean);

    // Layer del volo: le 5 pose impilate (stesso canvas condiviso, tutte
    // width:100%), visibili solo con .flying e una alla volta (.on, v. showEl).
    const { fly, flyPose } = buildFlyLayer(this.el);
    this.fly = fly;
    this.flyPose = flyPose;

    // Il pallino «c'e' una risposta» (v. `setBadge`): e' di lei, quindi sta
    // dentro il suo bottone, e si vede al bordo come fuori.
    const dot = document.createElement('span');
    dot.className = 'jenny-dot';
    dot.setAttribute('aria-hidden', 'true');
    this.el.appendChild(dot);

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
      // L'umore c'e' solo a riposo: pensa e parla lo cancellano (`_setAgentState`).
      const mood = this._moodFace();
      if (mood) {
        this._setBody(SIDE_BODY);
        this._setFace(SIDE_FACE[mood]);
        return;
      }
      this._setBody(ART.side);
      this._setFace(null);
      return;
    }
    this._setBody(this._agentState === 'thinking' ? BODY.think : BODY.idle);
    this._setFace(FACE[this._faceKey()]);
  }

  /* ── Gesti: saluto, sobbalzo ── */

  /** Il saluto (v. MOTIONS.squash.greet). Solo a riposo e fuori: mentre pensa
   *  o parla un braccio alzato direbbe una cosa che non sta facendo, e al
   *  bordo non si vedrebbe. Li' resta il saltello. Appena uscita dal bordo
   *  aspetta che lo scivolamento finisca. */
  hello() {
    if (this._greetTimer) {
      clearTimeout(this._greetTimer);
      this._greetTimer = null;
    }
    const busy = this._agentState !== 'idle' || this._talk.timer;
    if (busy || !this._layered()) {
      this._motion('squash', 'hop');
      return;
    }
    const wait = this._slideUntil - performance.now();
    if (wait > 0) {
      this._greetTimer = setTimeout(() => {
        this._greetTimer = null;
        this.hello();
      }, wait);
      return;
    }
    this._motion('squash', 'greet');
  }

  /** Il sobbalzo: «sono qui, chiudi da me». Lo chiede il guscio quando tocchi
   *  il velo dietro la chat, che apposta non la chiude. */
  nudge() {
    this._motion('squash', 'nudge');
  }

  /** Il gesto in corso su *channel*, o null. */
  motion(channel) {
    return this._motionNow[channel];
  }

  /* Un gesto su un canale, riacceso da capo se c'era gia', al posto di quello
     che c'era. Tolto a tempo e non su `animationend`: con
     `prefers-reduced-motion` l'animazione non parte, e l'attributo
     resterebbe addosso. */
  _motion(channel, name) {
    const ms = MOTIONS[channel][name];
    this._stopMotion(channel);
    void this.el.offsetWidth; // riparte l'animazione anche se era la stessa
    this.el.style.setProperty(MOTION_VARS[channel], `${ms}ms`);
    this.el.setAttribute(`data-${channel}`, name);
    this._motionNow[channel] = name;
    this._motionTimers[channel] = setTimeout(() => this._stopMotion(channel), ms);
  }

  _stopMotion(channel) {
    if (this._motionTimers[channel]) clearTimeout(this._motionTimers[channel]);
    this._motionTimers[channel] = null;
    if (!this._motionNow[channel]) return;
    this._motionNow[channel] = null;
    this.el.removeAttribute(`data-${channel}`);
  }

  /* Il saluto e' di Jenny a riposo e fuori: un turno che parte o il ritorno al
     bordo lo fermano a meta', invece di lasciare il braccio alzato sopra il
     pensa o contro il bordo. */
  _stopGreet() {
    if (this._greetTimer) {
      clearTimeout(this._greetTimer);
      this._greetTimer = null;
    }
    if (this.motion('squash') === 'greet') this._stopMotion('squash');
  }

  /** Il pallino «c'e' una risposta che non hai visto». *note* e' la frase che
   *  lo dice a chi non lo vede: la sceglie il guscio, che ha le traduzioni. */
  setBadge(on, note = '') {
    const was = this.el.classList.contains('has-dot');
    this.el.classList.toggle('has-dot', Boolean(on));
    if (on && !was) this._motion('squash', 'perk');
    this._labelNote = on ? note : '';
    this._paintLabel();
  }

  /** La chat e' aperta sopra la pagina: lei e' il bottone che la chiude. */
  setExpanded(open) {
    this.el.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  _paintLabel() {
    const name = botName.get();
    this.el?.setAttribute('aria-label', this._labelNote ? `${name} · ${this._labelNote}` : name);
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
    if (state !== 'idle') {
      this._clearMood();
      this._stopGreet();
    }
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
    if (this._turnActive) return false;
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
    // La faccia arriva con la reazione del corpo; al bordo il CSS la tace.
    this._motion('squash', mood);
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

  /* L'umore vivo, o null. Vale fuori e al bordo, ognuno con le sue facce, e
     passando dall'uno all'altro entro il tempo lei lo tiene. */
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
    // Silenzio nel flusso: torna a pensare invece di tenere la bocca congelata
    // in posa di parlato — **se un turno e' ancora aperto**. Altrimenti a
    // riposo. Prima tornava a pensare sempre: un messaggio arrivato a turno
    // chiuso (la risposta di `/stop` dopo il suo `turn_end`, quella di
    // `/status`, un risultato di `/dream`) la lasciava a pensare per sempre,
    // perche' nessun frame dopo l'avrebbe chiusa. Misurato sull'emulatore il
    // 28/09/2026.
    if (now - this._talk.lastTextAt > TALK_QUIET_TO_THINK_MS) {
      this._setAgentState(this._turnActive ? 'thinking' : 'idle');
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
      this._motion('lean', 'gest');
    }
    this._setBody(TALK_BODIES[this._talk.animIdx]);
    this._setFace(FACE[this._talk.open ? 'talk' : 'normal']);
    if (this._talk.open) this._motion('squash', 'syl');
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

  /* Riallinea l'arte quando l'utente cambia le preferenze dalla
     stanza «Jenny» della casa (evento 'mascotchange'). Le img del volo
     hanno src fisso a creazione e non si ricablano più: da quando l'arte ha
     una sola variante, il loro path non dipende da nessuna preferenza. */
  _applyMascotPrefs() {
    if (!this._talk.timer) this._syncArt();
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
      setOut: (v) => this._onGestureOut(v),
      // Presa in mano, l'arte di riposo sparisce sotto le pose del volo: un
      // gesto a meta' riapparirebbe all'atterraggio come un avanzo.
      onDragCommit: () => {
        this._stopGreet();
        this._stopMotion('lean');
        this._stopMotion('squash');
      },
      onTap: () => this._onTap(),
      // In volo la deformazione la calcola la fisica (v. mascot-squash.js);
      // con movimento ridotto il volo resta, la deformazione no.
      squash: () => !this._reducedMotion,
      onFlightEnd: () => {
        this._syncArt();
        this._updateGestureExclusion();
        // Le pose lasciano il posto al riposo con un piccolo assestamento.
        this._motion('squash', 'land');
      },
    });
  }

  /* ── La porta della chat ──
     Dal 05/10/2026 il tocco su Jenny e' la porta della conversazione: dove la
     chat non c'e' la apre, a tutto schermo sopra la pagina; dove e' aperta
     sopra una pagina la chiude; dove e' a schermo da se' (la pagina chat, la
     Console) lei saluta. Senza adattatore (`chat`) resta il gesto di prima:
     il tocco la manda al bordo o la fa uscire. */
  _onTap() {
    const chat = this._chat;
    const out = this.el.classList.contains('out');
    if (!chat) {
      this._setOut(!out);
      return;
    }
    if (chat.isOpen()) {
      chat.close();
      return;
    }
    if (chat.onScreen()) {
      if (!out) this._setOut(true);
      this.hello();
      return;
    }
    if (chat.canOpen?.() === false) {
      this.hello();
      return;
    }
    chat.open({ gesture: 'tap' });
  }

  /* Dove la fisica la vuole mettere a fine gesto: uno swipe verso l'interno
     (o un volo che finisce fuori) e' «vieni», verso il bordo e' «vai via».
     «Vieni» dove la chat non c'e' la apre, come il tocco; «vai via» con la
     chat aperta sopra una pagina la chiude. Il resto e' solo dove sta lei. */
  _onGestureOut(out) {
    const chat = this._chat;
    if (!chat) {
      this._setOut(out);
      return;
    }
    if (chat.isOpen()) {
      if (out) this._setOut(true);
      else chat.close();
      return;
    }
    if (out && !chat.onScreen()) {
      if (chat.canOpen?.() === false) {
        this._setOut(false);
        this.hello();
        return;
      }
      chat.open({ gesture: 'swipe' });
      return;
    }
    this._setOut(out);
  }

  /** La chat e' a schermo (`true`) o no. Fuori dalla chat lei non pensa per
   *  gli invii — non ce ne sono — ma continua a seguire la conversazione: una
   *  risposta che arriva mentre e' al bordo la fa parlare di lato. */
  setChatOnScreen(on) {
    this.mode = on ? 'chat' : 'away';
  }

  /** Al bordo (`false`) o venuta fuori (`true`).
   *
   *  Non la gira solo il tocco: la fisica chiama qui a volo finito, e la casa
   *  la manda al bordo quando lasci la chat per un'altra stanza. */
  setOut(out) {
    this._setOut(out);
  }

  _setOut(out) {
    const was = this.el.classList.contains('out');
    this.el.classList.toggle('out', out);
    if (out && !was) this._slideUntil = performance.now() + SLIDE_MS;
    if (!out) this._stopGreet();
    // Si sporge solo se scivola davvero: a fine volo (`flying`) e' gia' li'.
    if (out !== was && !this.el.classList.contains('flying')) {
      this._motion('lean', out ? 'go-in' : 'go-edge');
    }
    this._syncArt();
    this._updateGestureExclusion();
  }


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
    this._handleChatStream(msg);
  }

  /* ── Chat principale: la mascotte segue la conversazione vera ──
     Out = pensa e parla; docked = salta il "pensa" e parla nella versione
     semplificata side/side-talk (la coppia la sceglie il tick). */

  _handleChatSent(detail) {
    if (this.mode !== 'chat') return;
    const current = sessionManager.currentChatId;
    if (detail?.chat_id && current && detail.chat_id !== current) return;
    // Un invio a riposo apre un'attesa nuova: di un turno vecchio non si tiene
    // niente, nemmeno l'id — uno rimasto appeso le farebbe ignorare ogni
    // `turn_end` da qui in poi. Un invio a turno aperto invece entra in quel
    // turno (il gateway lo inietta), e l'attesa resta quella.
    if (!this._turnActive) {
      this._runSeen = false;
      this._streamTurnId = null;
    }
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
    this._forgetTurnState();
    this._clearMood();
  }

  /* Il filo e' caduto (un riavvio del gateway, una rete che se ne va). La
     chiusura del turno in volo, se arriva, arriva a nessuno: al rientro il
     gateway rimanda solo `goal_status: running`, e solo se il turno gira
     ancora. Quindi lei torna a riposo, e un turno ancora vivo la rimette a
     pensare da se' col suo `running`. */
  _onWireClose() {
    this._forgetTurnState();
  }

  _forgetTurnState() {
    this._turnActive = false;
    this._streamTurnId = null;
    this._runSeen = false;
    this._setAgentState('idle');
  }

  /* Un messaggio con del testo che risponde a un invio **senza** che sia
     partito un turno: la risposta di un comando (`/status`, `/stop` a riposo)
     o di un rifiuto. Un turno vero comincia sempre con `goal_status: running`;
     questo no, e dopo potrebbe non arrivare niente — il gateway, dal
     28/09/2026, lo chiude con un `turn_end`, ma un gateway di prima no, e una
     chiusura persa per strada nemmeno. Quindi l'attesa finisce qui. */
  _isCommandReply(msg) {
    return msg.event === 'message' && !!msg.text
      && msg.kind !== 'tool_hint' && msg.kind !== 'progress'
      && this._turnActive && !this._runSeen;
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
      // non abbiamo mai visto aprirsi — ma resta permissiva: un turno il cui
      // tracciamento e' stato lasciato a meta' si chiude lo stesso.
      //
      // E si adotta solo **dentro** un turno. Fuori — la risposta di `/stop`
      // arrivata dopo il suo `idle`, un avviso proattivo — un id adottato non
      // si chiuderebbe mai, e da li' in poi ogni `turn_end` sarebbe «di un
      // altro». La chiusura di quel frame passa lo stesso, per la regola sopra.
      const closing = msg.event === 'turn_end' || msg.event === 'error';
      if (!closing && this._turnActive) this._streamTurnId = turnId;
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
          if (this._isCommandReply(msg)) {
            // Parla, e poi il silenzio la porta a riposo (v. `_talkTick`).
            this._turnActive = false;
            this._streamTurnId = null;
          }
          this._setAgentState('talking');
        } else if (this._turnActive) {
          this._setAgentState('thinking');
        }
        break;
      /* Ragionamento e file toccati sono lavoro di un turno: fuori da un turno
         (un frame arrivato dopo la sua chiusura) non la rimettono a pensare,
         o ci resterebbe. */
      case 'reasoning_delta':
        if (this._turnActive) this._setAgentState('thinking');
        break;
      case 'reasoning_end':
        break;
      case 'file_edit':
        if (this._turnActive) this._setAgentState('thinking');
        break;
      case 'goal_status':
        if (msg.status === 'running') {
          this._turnActive = true;
          this._runSeen = true;
          this._setAgentState('thinking');
        } else if (msg.status === 'idle') {
          /* `idle` chiude il turno anche quando il suo `turn_end` non si e'
             riconosciuto — quello di `/stop` porta l'id di `/stop`, non del
             turno fermato — e con lui l'id che lei seguiva: tenerlo le avrebbe
             fatto ignorare ogni `turn_end` dopo, avvisi proattivi compresi,
             che un `idle` non lo mandano. */
          this._turnActive = false;
          this._streamTurnId = null;
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
        this._setAgentState('idle');
        if (msg.event === 'error') this._applyMood('sad'); // livello 0, gratis
        break;
    }
  }
}
