/** Il volo pegman — la fisica del trascinamento della mascotte.
 *
 *  Estratto da `mobile-jenny.js` il 18/09/2026 **senza riscriverlo**: la
 *  fisica e' identica riga per riga, cambia solo da dove prende i suoi
 *  appigli. Il motivo dell'estrazione e' che le interfacce sono due — la casa e
 *  l'officina — e una seconda copia di 427 righe di pendoli e rimbalzi non si
 *  tiene allineata: diverge, e diverge in silenzio, perche' su questa roba non
 *  c'e' un test che possa accorgersene. Si vede solo lanciandola.
 *
 *  Chi la usa passa un *host*: lo sprite, il livello di volo, e le poche cose
 *  che i due gusci fanno in modo diverso. Tutto il resto — pendolo appeso alla
 *  mano, molla della presa, rimbalzo su pareti e pavimento, tonfo, rialzata e
 *  camminata di rientro — non sa dove vive.
 *
 *  L'unico concetto davvero di un guscio solo e' lo stato `out`: in officina
 *  la mascotte sta al bordo e "esce" per parlare, in casa sta appoggiata sul
 *  pavimento e basta. Un host che dichiara `hasOut: false` percorre gli stessi
 *  rami con lo scarto d'ancoraggio a zero, invece di avere un codice suo.
 */

import { mascotSide } from './mascot.js';

/* Tutta l'arte (riposo + pose di volo) vive sullo stesso canvas QUADRATO
   3000x3000, esportato cosi' com'e' da gen_pose_webp.py: scala e posizioni
   sono responsabilita' dell'artista, non del codice. Il layer del volo
   coincide col box dello sprite e le img sono tutte width:100%: nessuna scala
   o offset a runtime. L'unica costante e' il pivot della posa appesa. */
export const FLY_POSES = {
  hang: '/html-mobile/assets/jenny-hang.webp',
  fall: '/html-mobile/assets/jenny-fall.webp',
  ground: '/html-mobile/assets/jenny-ground.webp',
  walk1: '/html-mobile/assets/jenny-walk1.webp',
  walk2: '/html-mobile/assets/jenny-walk2.webp',
};

export const PIVOT_X = 0.5083; // punta della manica alzata (la mano) di jenny-hang,
export const PIVOT_Y = 0.4333; // in frazioni del canvas (v. gen_pose_webp.py)
const WALK_FRAME_MS = 500; // alternanza walk1/walk2 nel rientro
const DIR_MIN = 40; // px/s: sotto questa |vx| il facing non cambia (anti-jitter)
const G_L = 26; // rigidità del pendolo (g/L)
const SWING_DAMP = 2.1;
const ACCEL_COUPLING = 0.0048; // accelerazione orizzontale -> swing (3x)
const GRAB_K = 170;
const GRAB_DAMP = 2 * Math.sqrt(GRAB_K) * 0.72; // sottosmorzata: elastica
const MAX_TILT = (78 * Math.PI) / 180;
const MAX_SPEED = 5000; // px/s
const WALL_REST = 0.42; // rimbalzo sui bordi dello schermo
const FALL_G = 1300; // gravità della caduta al rilascio (px/s²) — rientro calmo
const FLOOR_REST = 0.12; // rimbalzo sul pavimento molto smorzato
const WALK_SPEED = 150; // rientro a passo costante (px/s), tipo camminata
const GETUP_MS = 700; // pausa a terra dopo il tonfo (tempo per "rialzarsi")
const RETURN_TIMEOUT_MS = 6000; // failsafe: oltre, snap allo stato finale
export const DRAG_THRESHOLD = 24;
export const TAP_SLOP = 6;
export const HOLD_DELAY_MS = 250; // soglia per distinguere tap da hold

/** Costruisce il livello del volo: le 5 pose impilate, una visibile alla volta. */
export function buildFlyLayer(parent) {
  const fly = document.createElement('div');
  fly.className = 'jenny-fly';
  const flyPose = {};
  for (const [key, src] of Object.entries(FLY_POSES)) {
    const im = document.createElement('img');
    im.src = src;
    im.alt = '';
    im.draggable = false;
    fly.appendChild(im);
    flyPose[key] = im;
  }
  parent.appendChild(fly);
  return { fly, flyPose };
}

/**
 * Lega il trascinamento allo sprite di *host*. Ritorna `abort()`, da chiamare
 * quando il volo va interrotto dall'esterno (cambio vista, smontaggio).
 *
 * host:
 *   el                 lo sprite (prende le classi `dragging` e `flying`)
 *   fly, flyPose       il livello del volo e le sue pose
 *   hasOut             esiste lo stato "uscita"? (default: no)
 *   outShiftRatio      scarto d'ancoraggio fra riposo e uscita, in frazioni di lato
 *   isOut()            lo stato attuale
 *   setOut(v)          applicalo (a fine volo)
 *   onDragCommit()     il trascinamento e' cominciato davvero
 *   onTap()            tocco secco, senza trascinamento
 *   onSideChange(side) e' atterrata sull'altro bordo
 *   onFlightEnd()      il volo e' finito: rimetti l'arte a posto
 */
export function bindMascotDrag(host) {
  const hasOut = !!host.hasOut;
  const outShiftRatio = host.outShiftRatio || 0;
  const isOut = host.isOut || (() => false);
  const setOut = host.setOut || (() => {});
  const onDragCommit = host.onDragCommit || (() => {});
  const onTap = host.onTap || (() => {});
  const onSideChange = host.onSideChange || (() => {});
  const onFlightEnd = host.onFlightEnd || (() => {});

  let startX = 0;
  let startY = 0;
  let dragging = false;
  let moved = false;
  let holdTimer = null;
  let dragStarted = false;
  let startEvent = null;

  /* Stato del volo. (x,y) = pivot (la mano) in coordinate viewport;
     (bx,by) = pivot naturale del layer a transform zero. */
  const fs = {
    active: false,
    phase: 'held', // held -> fall -> down (a terra) -> slide
    downUntil: 0,
    grounded: false, // true dal primo contatto col pavimento (per la posa ground)
    settled: true, // false dal rilascio finché settle() non sceglie bordo e arrivo
    targetOut: false, // stato out voluto dal gesto, applicato a fine volo
    dir: 1, // facing: 1 verso destra (arte originale), -1 verso sinistra (flip)
    x: 0, y: 0, vx: 0, vy: 0,
    th: 0, om: 0, axS: 0,
    bx: 0, by: 0, y0: 0, xT: 0,
    px: 0, py: 0,
    w: 0, h: 0,
    onEl: null, // posa del layer di volo attualmente visibile (.on)
    raf: 0, last: 0, deadline: 0, after: null, snapT: 0,
  };

  const clearHoldTimer = () => {
    if (holdTimer) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
  };

  const commitDrag = () => {
    if (dragStarted) return;
    dragStarted = true;
    host.el.classList.add('dragging');
    onDragCommit();
    startFlight(startEvent);
  };

  const vw = () =>
    (window.visualViewport && window.visualViewport.width) || window.innerWidth || 360;
  const vh = () =>
    (window.visualViewport && window.visualViewport.height) || window.innerHeight || 640;

  /* Mostra una sola posa del layer di volo, spegnendo la precedente. */
  const showEl = (el) => {
    if (fs.onEl === el) return;
    if (fs.onEl) fs.onEl.classList.remove('on');
    fs.onEl = el;
    el.classList.add('on');
  };

  const startFlight = (e) => {
    // Il layer di volo coincide col box dello sprite (canvas quadrato condiviso
    // da tutte le pose): nessun dimensionamento, solo il pivot sulla mano.
    const r = host.el.getBoundingClientRect();
    fs.w = r.width;
    fs.h = r.width;
    fs.bx = r.left + fs.w * PIVOT_X;
    fs.by = r.top + fs.h * PIVOT_Y;
    fs.x = fs.bx;
    fs.y = fs.by;
    fs.y0 = fs.by;
    fs.vx = 0;
    fs.vy = 0;
    fs.th = 0;
    fs.om = (fs.x - e.clientX) * 0.004; // piccolo strappo alla presa
    fs.axS = 0;
    fs.px = e.clientX;
    fs.py = e.clientY;
    fs.phase = 'held';
    fs.after = null;
    fs.settled = true; // niente da assestare finché non la si lascia andare
    fs.grounded = false;
    fs.dir = 1;
    fs.onEl = null;
    Object.values(host.flyPose).forEach((im) => im.classList.remove('on', 'flip'));
    showEl(host.flyPose.hang);
    host.fly.style.transformOrigin = `${fs.w * PIVOT_X}px ${fs.h * PIVOT_Y}px`;
    host.fly.style.transform = '';
    host.el.classList.add('flying');
    fs.active = true;
    fs.last = performance.now();
    fs.raf = requestAnimationFrame(loop);
  };

  /* Atterraggio: sceglie il bordo più vicino al punto in cui è caduta e ce
     la manda a piedi. Il cambio di lato si applica subito, non a fine volo:
     con .flying attivo l'ancoraggio non transiziona, e il layer vive in
     coordinate viewport (fs.x/fs.y), quindi ri-misurare la base del
     transform lo lascia esattamente dov'è — nessun salto. Solo dopo si sa
     dov'è il dock, e quindi dove deve arrivare la camminata. */
  const settle = () => {
    if (fs.settled) return;
    fs.settled = true;
    const side = fs.x < vw() / 2 ? 'left' : 'right';
    if (side !== mascotSide()) {
      onSideChange(side);
      // Attraversare lo schermo è già il gesto: la si ritrova a riposo sul
      // bordo nuovo, non aperta.
      fs.targetOut = false;
    }
    const r = host.el.getBoundingClientRect();
    fs.bx = r.left + fs.w * PIVOT_X;
    fs.by = r.top + fs.h * PIVOT_Y;
    const wasOut = isOut();
    if (fs.targetOut === wasOut) {
      fs.xT = fs.bx;
      fs.after = null;
      return;
    }
    // Il cambio di stato out avviene solo a fine volo (fs.after): la x di
    // arrivo la anticipa di uno scarto d'ancoraggio, verso l'interno se si
    // apre e verso il bordo se si chiude.
    const shift = fs.w * outShiftRatio * (side === 'left' ? -1 : 1);
    fs.xT = fs.bx + (fs.targetOut ? -shift : shift);
    fs.after = () => setOut(fs.targetOut);
  };

  /* Chiude il volo: applica l'eventuale cambio di stato e ripulisce.
     Il cambio classe avviene con .flying ancora attivo (transition: none),
     così il right nuovo non viene animato: lei è già lì col transform. */
  const endFlight = () => {
    if (!fs.active) return;
    fs.active = false;
    cancelAnimationFrame(fs.raf);
    clearTimeout(fs.snapT);
    // Uscite di sicurezza (deadline, snapT, app in background, tastiera):
    // il volo finisce senza che lei abbia mai toccato terra, ma il gesto
    // dell'utente va onorato lo stesso.
    settle();
    if (fs.after) fs.after();
    // Il nuovo ancoraggio va *committato* mentre .flying vale ancora, non
    // solo scritto: senza questo flush il browser confronta lo stile di
    // prima con quello di dopo la rimozione di .flying, vede la transizione
    // riattivata e anima lo scarto che lei ha già percorso a piedi — cioè un
    // salto all'indietro seguito da uno scivolamento di 0.3s.
    void host.el.offsetWidth;
    host.fly.style.transform = '';
    host.el.classList.remove('flying');
    onFlightEnd();
  };

  const step = (dt) => {
    let ax = 0;
    if (fs.phase === 'held') {
      ax = GRAB_K * (fs.px - fs.x) - GRAB_DAMP * fs.vx;
      const ay = GRAB_K * (fs.py - fs.y) - GRAB_DAMP * fs.vy;
      fs.vx += ax * dt;
      fs.vy += ay * dt;
    } else {
      ax = -1.7 * fs.vx;
      if (fs.phase === 'fall') {
        fs.vx -= 1.7 * fs.vx * dt;
        if (fs.y <= fs.y0) {
          fs.vy += FALL_G * dt; // sopra il dock: cade
        } else {
          fs.vy += (70 * (fs.y0 - fs.y) - 17 * fs.vy) * dt; // sotto: risale
        }
      } else if (fs.phase === 'down') {
        // a terra dopo il tonfo (posa ground): ferma, sta per rialzarsi
        fs.vy = 0;
        fs.y = fs.y0;
        fs.vx -= 8 * fs.vx * dt;
      } else {
        // slide: torna a passo costante (camminata)
        fs.vy = 0;
        fs.y = fs.y0;
        const d = fs.xT - fs.x;
        if (Math.abs(fs.vx) > WALK_SPEED * 1.5) {
          fs.vx -= 6 * fs.vx * dt; // attrito residuo
        } else {
          fs.vx = Math.sign(d) * Math.min(WALK_SPEED, Math.abs(d) / Math.max(dt, 0.001));
        }
      }
    }
    const sp = Math.hypot(fs.vx, fs.vy);
    if (sp > MAX_SPEED) {
      fs.vx *= MAX_SPEED / sp;
      fs.vy *= MAX_SPEED / sp;
    }
    fs.x += fs.vx * dt;
    fs.y += fs.vy * dt;

    // Pareti morbide: in mano e in caduta (lanciarla = rimbalza), ma NON in
    // slide — il dock sta oltre il bordo dello schermo e le pareti le
    // impedirebbero di arrivare (la condizione d'arrivo non scatterebbe mai).
    // Sono anche quelle che tengono fs.x dentro il viewport, quindi rendono
    // significativo il confronto con la metà schermo in settle().
    if (fs.phase !== 'slide') {
      const mL = fs.w * PIVOT_X * 0.5;
      const mR = vw() - mL;
      const mT = fs.h * PIVOT_Y * 0.6;
      const mB = vh() - fs.h * (1 - PIVOT_Y) * 0.5;
      if (fs.x < mL) { fs.x = mL; fs.vx = Math.abs(fs.vx) * WALL_REST; fs.om += fs.vx * 0.002; }
      if (fs.x > mR) { fs.x = mR; fs.vx = -Math.abs(fs.vx) * WALL_REST; fs.om -= fs.vx * 0.002; }
      if (fs.y < mT) { fs.y = mT; fs.vy = Math.abs(fs.vy) * WALL_REST; }
      if (fs.y > mB) { fs.y = mB; fs.vy = -Math.abs(fs.vy) * WALL_REST; }
    }

    // il facing segue il verso del moto (in mano, in caduta e nel rientro
    // a passo), con isteresi
    if (
      (fs.phase === 'held' || fs.phase === 'fall' || fs.phase === 'slide') &&
      Math.abs(fs.vx) > DIR_MIN
    ) {
      fs.dir = fs.vx > 0 ? 1 : -1;
    }

    // rimbalzo sulla quota del dock durante la caduta
    if (fs.phase === 'fall') {
      if (fs.y >= fs.y0) fs.grounded = true; // toccato terra: passa alla posa ground
      if (fs.vy > 0 && fs.y >= fs.y0) {
        fs.y = fs.y0;
        // tonfo quasi secco: al massimo un rimbalzino molto smorzato,
        // poi resta un attimo a terra prima di rialzarsi
        if (Math.abs(fs.vy) < 500) {
          fs.vy = 0;
          fs.phase = 'down';
          fs.downUntil = fs.last + GETUP_MS;
        } else {
          fs.vy = -Math.abs(fs.vy) * FLOOR_REST;
          fs.om += fs.vx * 0.0015;
        }
      } else if (Math.abs(fs.y - fs.y0) < 3 && Math.abs(fs.vy) < 60) {
        fs.y = fs.y0;
        fs.vy = 0;
        fs.phase = 'down';
        fs.downUntil = fs.last + GETUP_MS;
      }
    }

    // pendolo: la gravità raddrizza, l'accelerazione orizzontale fa swingare
    fs.axS += (ax - fs.axS) * Math.min(1, dt * 14);
    // in slide il pendolo si spegne in fretta: lo swap all'arrivo è immediato
    const damp =
      fs.phase === 'held' ? SWING_DAMP : fs.phase === 'fall' ? SWING_DAMP * 2.2 : SWING_DAMP * 5;
    const alpha =
      -G_L * Math.sin(fs.th) - ACCEL_COUPLING * fs.axS * Math.cos(fs.th) - damp * fs.om;
    fs.om += alpha * dt;
    fs.th += fs.om * dt;
    if (fs.th > MAX_TILT) { fs.th = MAX_TILT; fs.om *= -0.35; }
    if (fs.th < -MAX_TILT) { fs.th = -MAX_TILT; fs.om *= -0.35; }
  };

  const loop = (now) => {
    if (!fs.active) return;
    const dt = Math.min(0.033, Math.max(0.001, (now - fs.last) / 1000));
    fs.last = now;
    if (fs.phase === 'down' && now >= fs.downUntil) fs.phase = 'slide';
    step(dt);
    // Toccato terra (fall -> down): da qui in poi si sa dov'è caduta, quindi
    // quale bordo le tocca. Prima del disegno, perché settle() sposta la
    // base del transform.
    if (!fs.settled && fs.phase !== 'fall') settle();

    const tx = (fs.x - fs.bx).toFixed(1);
    const ty = (fs.y - fs.by).toFixed(1);
    if (fs.phase === 'held') {
      // pendolo appeso alla mano: ruota di -θ attorno al pivot (niente
      // flip in mano: l'arte resta nel suo verso, solo la caduta si specchia)
      const deg = (fs.th * 180) / Math.PI;
      showEl(host.flyPose.hang);
      host.fly.style.transform =
        `translate(${tx}px, ${ty}px) rotate(${(-deg).toFixed(2)}deg)`;
    } else {
      // posa di fase, dritta (niente swing): fall in caduta (flip in place
      // col verso del moto), ground a terra + rimbalzi, walk1/2 in cammino.
      let key = 'fall';
      if (fs.phase === 'slide') {
        key = Math.floor(now / WALK_FRAME_MS) % 2 ? 'walk2' : 'walk1';
      } else if (fs.phase === 'down' || fs.grounded) {
        key = 'ground';
      }
      host.flyPose.fall.classList.toggle('flip', key === 'fall' && fs.dir < 0);
      host.flyPose.ground.classList.toggle('flip', key === 'ground' && fs.dir < 0);
      host.flyPose.walk1.classList.toggle('flip', fs.phase === 'slide' && fs.dir < 0);
      host.flyPose.walk2.classList.toggle('flip', fs.phase === 'slide' && fs.dir < 0);
      showEl(host.flyPose[key]);
      host.fly.style.transform = `translate(${tx}px, ${ty}px)`;
    }

    if (fs.phase === 'slide') {
      // appena è in posizione si switcha: rotazione e passo non contano
      const settledNow = Math.abs(fs.x - fs.xT) < 5;
      if (settledNow || now >= fs.deadline) {
        endFlight();
        return;
      }
    } else if (fs.phase !== 'held' && now >= fs.deadline) {
      endFlight();
      return;
    }
    fs.raf = requestAnimationFrame(loop);
  };

  /* Il volo non inizia subito al pointerdown: il tap secco fa solo toggle
     (gestito dall'evento click). L'hold (timer) o il movimento vero
     (oltre TAP_SLOP) commettono il drag e fanno apparire lo sprite di volo. */
  let lastX = 0;
  let lastY = 0;

  host.el.addEventListener('pointerdown', (e) => {
    dragging = true;
    moved = false;
    dragStarted = false;
    startEvent = e;
    startX = e.clientX;
    startY = e.clientY;
    lastX = e.clientX;
    lastY = e.clientY;
    try {
      host.el.setPointerCapture(e.pointerId);
    } catch (_) {
      /* puntatore sintetico */
    }
    if (fs.active) endFlight();
    clearHoldTimer();
    holdTimer = setTimeout(() => {
      holdTimer = null;
      commitDrag();
    }, HOLD_DELAY_MS);
  });

  host.el.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    lastX = e.clientX;
    lastY = e.clientY;
    const overSlop =
      Math.abs(e.clientX - startX) > TAP_SLOP ||
      Math.abs(e.clientY - startY) > TAP_SLOP;
    if (overSlop) {
      if (!moved) {
        moved = true;
        if (!dragStarted) commitDrag();
      }
    }
    fs.px = e.clientX;
    fs.py = e.clientY;
  });

  /* Rilascio: decide con le soglie di sempre se il gesto era un apri/chiudi,
     poi la lascia cadere sulla y di partenza. Dove atterrerà — e quindi su
     che bordo finirà — non si sa ancora: lo fissa settle() al tonfo. */
  const finish = (clientX) => {
    if (!dragging) return;
    clearHoldTimer();
    dragging = false;
    host.el.classList.remove('dragging');
    // Niente focus persistente: essendo un <button>, dopo il tap resterebbe
    // "selezionata" (invisibile perché :focus ha outline:none) e la barra
    // spazio della tastiera fisica (Titan 2) la (ri)toggle-erebbe. blur() al
    // rilascio del puntatore copre tap, drag e pointercancel.
    host.el.blur();
    if (!dragStarted) {
      endFlight();
      return;
    }

    const dx = clientX - startX;
    const out = isOut();
    // Apri/chiudi è relativo al bordo su cui si trova adesso: da sinistra i
    // versi si specchiano (v. .jenny-duo.side-left in mobile-style.css).
    const sideSign = mascotSide() === 'left' ? -1 : 1;
    fs.targetOut = out;
    if (hasOut) {
      if (!out && dx * sideSign < -DRAG_THRESHOLD) fs.targetOut = true;
      else if (out && dx * sideSign > DRAG_THRESHOLD) fs.targetOut = false;
    }
    fs.xT = fs.bx; // provvisorio: la x di arrivo vera la fissa settle()
    fs.after = null;
    fs.settled = false;
    fs.phase = 'fall';
    fs.deadline = performance.now() + RETURN_TIMEOUT_MS;
    // Failsafe anche senza rAF (es. pagina nascosta): chiusura garantita.
    clearTimeout(fs.snapT);
    fs.snapT = setTimeout(endFlight, RETURN_TIMEOUT_MS + 300);
  };
  host.el.addEventListener('pointerup', (e) => finish(e.clientX));
  host.el.addEventListener('pointercancel', () => finish(lastX));
  host.el.addEventListener('lostpointercapture', () => finish(lastX));

  host.el.addEventListener('click', (e) => {
    if (dragStarted || moved) {
      e.preventDefault();
      return;
    }
    onTap();
  });

  host.el.addEventListener('contextmenu', (e) => e.preventDefault());

  // Tastiera che si apre o viewport che cambia: snap immediato, niente volo.
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => {
      clearHoldTimer();
      if (fs.active) {
        finish(lastX);
        endFlight();
      }
    });
  }

  // App in background a metà volo: rAF si ferma, quindi snap subito.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && fs.active) {
      clearHoldTimer();
      finish(lastX);
      endFlight();
    }
  });

  // Nessun toggle da tastiera: Jenny si mostra/nasconde solo con tap o
  // swipe. Su tastiera fisica lo spazio non deve avere effetto (v. blur in
  // finish()). Handler keydown rimosso di proposito.

  return () => {
    dragging = false;
    host.el.classList.remove('dragging');
    endFlight();
  };
}
