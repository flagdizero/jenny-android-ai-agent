/** La casa — Jenny.
 *
 *  La stessa arte dell'officina, molto meno macchina intorno. In officina la
 *  companion e' anche un *comando*: la si trascina, la si lancia, la si tocca
 *  per aprire una minichat. Qui no.
 *
 *  **In casa Jenny e' presenza, non controllo** (`pointer-events: none`). Le
 *  ragioni sono due. La prima: la minichat esiste perche' in officina la chat
 *  puo' non essere a schermo — in casa la chat *e'* lo schermo, e un secondo
 *  posto dove scriverle sarebbe una porta che da' sulla stanza in cui sei gia'.
 *  La seconda: sta sopra il filo che scorre, e ogni suo gesto sarebbe un gesto
 *  rubato allo scorrimento.
 *
 *  Le tabelle degli sprite sono duplicate da `mobile-jenny.js` invece che
 *  importate: quelle sono costanti private di un modulo da 1.353 righe, e
 *  tirarsi dentro la companion intera per due dizionari sarebbe il contrario di
 *  quel che la casa e'. La copia e' tenuta onesta da un test
 *  (`tests/webui/test_casa_mascot_contract.py`): stessi file, stesso manifest,
 *  stessi umori del backend.
 */

import { mascotSize, mascotVisible, applyMascotSize } from './shared/mascot.js';

const BODY = {
  idle: '/html-mobile/assets/jenny-body-front-idle.webp',
  hand: '/html-mobile/assets/jenny-body-front-hand.webp',
  think: '/html-mobile/assets/jenny-body-front-think.webp',
};

const FACE = {
  normal: '/html-mobile/assets/jenny-face-front-normal.webp',
  talk: '/html-mobile/assets/jenny-face-front-normal-talk.webp',
  thinking: '/html-mobile/assets/jenny-face-front-thinking.webp',
  happy: '/html-mobile/assets/jenny-face-front-happy.webp',
  sad: '/html-mobile/assets/jenny-face-front-sad.webp',
  angry: '/html-mobile/assets/jenny-face-front-angry.webp',
};

/* Gli umori che il backend manda dopo un turno (`MOODS` meno `neutral`, che non
   manda frame). Un frame che nominasse un'altra faccia si scarta: le altre
   chiavi di FACE sono facce di *stato*, non umori. */
const MOOD_FACES = ['happy', 'sad', 'angry'];
const MOOD_HOLD_MS = 12_000;

/* La bocca si apre e chiude mentre il testo arriva. */
const MOUTH_FRAME_MS = 260;
/* Silenzio dopo l'ultimo pezzo di testo: sotto questa soglia sta ancora
   parlando, sopra ha finito. Senza, ogni pausa fra due delta la farebbe
   tornare ferma e ripartire, cioe' tremare. */
const TALK_QUIET_MS = 1_000;

export class CasaMascot {
  constructor(host) {
    this.visible = mascotVisible();
    this.state = 'idle';
    this.mood = null;
    this._moodUntil = 0;
    this._moodTimer = null;
    this._turnActive = false;
    this._lastClosedTurn = null;
    this._mouthTimer = null;
    this._quietTimer = null;
    this._mouthOpen = false;

    applyMascotSize();

    this.el = document.createElement('div');
    this.el.className = 'casa-jenny';
    this.el.setAttribute('aria-hidden', 'true');
    this.body = document.createElement('img');
    this.face = document.createElement('img');
    this.body.alt = '';
    this.face.alt = '';
    this.el.append(this.body, this.face);
    host.appendChild(this.el);

    window.addEventListener('mascotchange', () => {
      this.visible = mascotVisible();
      applyMascotSize();
      this._paint();
    });

    this._paint();
  }

  /** La taglia scelta, in px. Serve a chi deve lasciarle spazio. */
  get size() {
    return mascotSize();
  }

  /* ── Gli stati ── */

  /** Sta lavorando: faccia dell'attesa, e il corpo che oscilla. */
  thinking() {
    this._stopMouth();
    this._set('thinking');
  }

  /** Sta rispondendo: la bocca si muove finche' il testo arriva. */
  talking() {
    this._set('talking');
    if (!this._mouthTimer) {
      this._mouthTimer = setInterval(() => {
        this._mouthOpen = !this._mouthOpen;
        this._paint();
      }, MOUTH_FRAME_MS);
    }
    // Ogni pezzo di testo rimanda il ritorno alla quiete.
    clearTimeout(this._quietTimer);
    this._quietTimer = setTimeout(() => this.idle(), TALK_QUIET_MS);
  }

  /** Non sta facendo niente. */
  idle() {
    this._stopMouth();
    this._set('idle');
  }

  /** Un turno sta girando, o ha smesso. */
  noteTurnRunning(running) {
    this._turnActive = !!running;
  }

  /** Quale turno si e' appena chiuso: e' il metro con cui si giudica l'umore
   *  che arriva dopo. Un frame di chiusura senza id vale per quello che stava
   *  seguendo. */
  noteTurnClosed(turnId) {
    this._lastClosedTurn = turnId || this._lastClosedTurn;
  }

  /** La reazione del backend alla risposta appena data.
   *
   *  Arriva **dopo** il `turn_end`, da un giro in background. Si accetta solo
   *  se e' la reazione all'ultimo turno chiuso e non ce n'e' un altro in
   *  corso: una faccia per una risposta che non e' piu' l'ultima confonde piu'
   *  di nessuna faccia.
   */
  setMood(mood, turnId = null) {
    if (!MOOD_FACES.includes(mood)) return false;
    if (this._turnActive) return false;
    if (turnId && this._lastClosedTurn && turnId !== this._lastClosedTurn) return false;
    this.mood = mood;
    this._moodUntil = Date.now() + MOOD_HOLD_MS;
    /* Il ritorno alla faccia normale vuole un timer: `_activeMood` legge
       l'orologio, ma senza qualcuno che ridipinga la scadenza arriverebbe
       senza che si veda, e la faccia resterebbe fino al frame dopo. */
    clearTimeout(this._moodTimer);
    this._moodTimer = setTimeout(() => {
      this.mood = null;
      this._paint();
    }, MOOD_HOLD_MS);
    this._paint();
    return true;
  }

  /* ── Disegno ── */

  _set(state) {
    if (this.state === state) return;
    this.state = state;
    this._paint();
  }

  _stopMouth() {
    clearInterval(this._mouthTimer);
    clearTimeout(this._quietTimer);
    this._mouthTimer = null;
    this._quietTimer = null;
    this._mouthOpen = false;
  }

  /* L'umore e' una faccia, non un quarto stato: **perde contro il pensa**, che
     e' cio' che sta succedendo adesso, e vale su quel che e' appena successo. */
  _activeMood() {
    if (!this.mood || Date.now() >= this._moodUntil) return null;
    return this.mood;
  }

  _paint() {
    this.el.hidden = !this.visible;
    if (!this.visible) return;
    const mood = this.state === 'thinking' ? null : this._activeMood();
    if (this.state === 'thinking') {
      this.body.src = BODY.think;
      this.face.src = FACE.thinking;
    } else if (this.state === 'talking') {
      this.body.src = BODY.idle;
      this.face.src = mood ? FACE[mood] : (this._mouthOpen ? FACE.talk : FACE.normal);
    } else {
      this.body.src = BODY.idle;
      this.face.src = mood ? FACE[mood] : FACE.normal;
    }
    this.el.classList.toggle('thinking', this.state === 'thinking');
  }
}
