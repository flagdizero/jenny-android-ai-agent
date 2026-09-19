/** La casa — Jenny.
 *
 *  La stessa arte dell'officina, molto meno macchina intorno. In officina la
 *  companion e' anche un *comando*: la si trascina, la si lancia, la si tocca
 *  per aprire una minichat. Qui no.
 *
 *  **Si prende, si lancia, e si tocca per toglierla di mezzo.** Il tocco la
 *  manda al bordo e ritoccarla la fa uscire: e' lo stesso gesto dell'officina,
 *  con gli stessi due ancoraggi (`DOCK_RATIO`/`OUT_RATIO`) e la stessa fisica
 *  (`shared/mascot-drag.js`) — non una copia. Qui serve anche piu' che la':
 *  in casa Jenny sta *sopra* il filo della conversazione, e ogni tanto uno
 *  vuole leggere quello che le sta sotto.
 *
 *  Quel che resta dell'officina e' la minichat, e resta la' apposta: esiste
 *  perche' li' la chat puo' non essere a schermo, mentre in casa la chat *e'*
 *  lo schermo — un secondo posto dove scriverle sarebbe una porta che da'
 *  sulla stanza in cui sei gia'. Percio' il tocco, in casa, fa solo meta' di
 *  quel che fa in officina: nasconde e mostra, non apre niente.
 *
 *  **Non si ricorda.** Al prossimo avvio e' di nuovo fuori, come in officina.
 *  Nasconderla e' un gesto per adesso — sto leggendo questo pezzo qui — non
 *  una preferenza; e una preferenza senza un posto dove disfarla si dimentica
 *  di essere stata presa, mentre lei, dal bordo, si vede ancora e si ritocca.
 *
 *  Le tabelle degli sprite sono duplicate da `mobile-jenny.js` invece che
 *  importate: quelle sono costanti private di un modulo da 1.353 righe, e
 *  tirarsi dentro la companion intera per due dizionari sarebbe il contrario di
 *  quel che la casa e'. La copia e' tenuta onesta da un test
 *  (`tests/webui/test_casa_mascot_contract.py`): stessi file, stesso manifest,
 *  stessi umori del backend.
 */

import { mascotSide, mascotSize, mascotVisible, applyMascotSize, setMascotSide } from './shared/mascot.js';
import { bindMascotDrag, buildFlyLayer } from './shared/mascot-drag.js';

/* La posa del bordo. E' **una sola immagine** — di profilo, con la faccia gia'
   dentro — e non due livelli: da li' sporge meno di meta' Jenny, e sovrapporle
   un volto frontale vorrebbe dire incollarle una faccia sulla nuca. La seconda
   e' la stessa con la bocca aperta: e' cosi' che si vede che sta parlando
   quando la si e' messa via. */
const ART = {
  side: '/html-mobile/assets/jenny-side.webp',
  sideTalk: '/html-mobile/assets/jenny-side-talk.webp',
};

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

    /* Un bottone, non un div: da quando toccarla fa qualcosa, `aria-hidden`
       sarebbe una bugia detta a chi non la vede. `tabindex="-1"` come in
       officina — si puo' toccare, ma non e' una tappa fra il titolo e il campo
       di scrittura, e sulla tastiera fisica del Titan la barra spazio non deve
       nasconderla (v. il `blur()` nel modulo della fisica). */
    this.el = document.createElement('button');
    this.el.type = 'button';
    this.el.className = 'casa-jenny out';
    this.el.setAttribute('aria-label', 'Jenny');
    this.el.setAttribute('tabindex', '-1');
    this.body = document.createElement('img');
    this.face = document.createElement('img');
    this.body.alt = '';
    this.face.alt = '';
    this.body.draggable = false;
    this.face.draggable = false;
    /* I due livelli dentro un contenitore loro, come in officina
       (`.jenny-art-stack`). Non e' cerimonia: lo specchio del lato sinistro va
       su di lui e **non** sullo sprite, o ribalterebbe anche il livello del
       volo — e la fisica scrive li' le sue traslazioni in coordinate schermo,
       quindi lanciandola a destra andrebbe a sinistra. Il respiro del pensa
       resta sulle img, cosi' i due transform si compongono invece di
       sovrascriversi. */
    this.art = document.createElement('div');
    this.art.className = 'casa-jenny-art';
    this.art.append(this.body, this.face);
    this.el.appendChild(this.art);

    /* Il livello del volo e la fisica: gli stessi dell'officina, dal modulo
       condiviso — e dal 19/09/2026 anche gli stessi rami, perche' lo stato
       `out` non e' piu' di un guscio solo. */
    const { fly, flyPose } = buildFlyLayer(this.el);
    this.fly = fly;
    this.flyPose = flyPose;

    this._applySide();
    host.appendChild(this.el);

    this.abortFlight = bindMascotDrag({
      el: this.el,
      fly: this.fly,
      flyPose: this.flyPose,
      isOut: () => this.el.classList.contains('out'),
      setOut: (v) => this.setOut(v),
      onTap: () => this.setOut(!this.el.classList.contains('out')),
      onSideChange: (side) => {
        setMascotSide(side);
        this._applySide();
      },
      onFlightEnd: () => {
        this._paint();
        this._updateGestureExclusion();
      },
    });
    /* Lo scarto fra i due ancoraggi muove il rettangolo, e l'area esclusa
       dalle gesture di sistema deve seguirlo: va riportata a transizione
       *finita*, non appena parte, o si dichiarerebbe ad Android il posto da cui
       se ne sta andando. A destra transiziona `right`, a sinistra `left`. */
    this.el.addEventListener('transitionend', (e) => {
      if (e.propertyName === 'right' || e.propertyName === 'left') {
        this._updateGestureExclusion();
      }
    });
    this._updateGestureExclusion();

    /* Le due pose del bordo si caricano adesso e non al primo tocco: sono
       l'unico disegno che entra in scena *sostituendo* tutto quel che c'era —
       le altre subentrano dentro una Jenny gia' a schermo, e al massimo
       arrivano un frame tardi. Qui, senza, il primo tocco la fa sparire. */
    for (const src of Object.values(ART)) {
      const im = new Image();
      im.src = src;
    }

    window.addEventListener('mascotchange', () => {
      this.visible = mascotVisible();
      applyMascotSize();
      this._applySide();
      this._paint();
      this._updateGestureExclusion();
    });

    this._paint();
  }

  /* Il lato su cui e' stata lasciata: lo ricorda `shared/mascot.js`, ed e' lo
     stesso ricordo dell'officina — attraversare lo schermo di la' la sposta
     anche di qua, che e' giusto: e' la stessa persona nello stesso telefono. */
  _applySide() {
    this.el.classList.toggle('side-left', mascotSide() === 'left');
  }

  /** Al bordo (`false`) o venuta fuori (`true`).
   *
   *  Non la gira solo il tocco: la si puo' anche spingere contro il bordo o
   *  tirare verso l'interno, e allora e' la fisica a chiamare qui — a volo
   *  finito, con lo stato che il gesto aveva chiesto. Per questo non e' un
   *  metodo privato.
   */
  setOut(out) {
    this.el.classList.toggle('out', out);
    this._paint();
    this._updateGestureExclusion();
  }

  /* L'area di Jenny va dichiarata ad Android, o il trascinamento sul bordo fa
     partire la gesture di sistema invece del volo. Identico all'officina: e'
     una proprieta' della finestra, non dell'interfaccia. */
  _updateGestureExclusion() {
    const api = window.JennyNative;
    if (!api || typeof api.setGestureExclusion !== 'function') return;
    if (!this.visible) {
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
    if (!this.el.classList.contains('out')) {
      /* Dal bordo il disegno e' cotto: una posa sola, con la faccia dentro. La
         bocca resta l'unica cosa che si muove — se sta parlando si vede anche
         da li'. Il dondolio del pensa no: mezza Jenny che oscilla contro il
         bordo somiglia a un difetto, non a uno stato. */
      this.body.src = this.state === 'talking' && this._mouthOpen ? ART.sideTalk : ART.side;
      this.face.classList.add('off');
      this.el.classList.remove('thinking');
      return;
    }
    this.face.classList.remove('off');
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
