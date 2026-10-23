/** La casa — il piano a lista.
 *
 *  Un lavoro grosso in un quaderno si fa a passi: Jenny propone una lista, tu
 *  togli quel che non vuoi e approvi, il codice la porta avanti un passo alla
 *  volta. Qui c'e' tutto quel che la casa ne mostra:
 *
 *    - la **scheda** nel posto vivo della chat del quaderno (sul modello di
 *      `home-staged.js`: si rilegge a ogni frame `plan_changed` e si
 *      ridisegna da capo), in tutti gli stati del piano;
 *    - la **striscia** in cima alla chat quando la scheda e' fuori schermo;
 *    - i **fogli dal basso**: cosa ha fatto un passo, cosa ha cambiato il
 *      piano, il conto del ritorno, Ferma, il piano completo;
 *    - il **tasto Piano** nel composer, e il composer spento mentre il piano
 *      corre;
 *    - chi e' **chiuso** (il quaderno in sola lettura per l'utente mentre un
 *      piano e' aperto) e il **bollino** sulla copertina.
 *
 *  Una fonte sola: lo stato vive in `plan.json`, e la casa non ne tiene una
 *  copia che valga piu' della prossima rilettura. Ogni tocco e' una RPC
 *  `plan.*`; la risposta non si usa per ridisegnare, si rilegge `plan.get`.
 *  Un `conflict` (Jenny ha fatto una versione nuova mentre guardavi) si dice
 *  e si rilegge, come la scheda delle bozze.
 *
 *  Le regole decise con l'utente, che il disegno non riapre: in corsa solo
 *  Pausa e Ferma (e la risposta quando e' bloccata); toccare un passo fatto
 *  **mostra** cosa ha fatto, non lo cambia; il ritorno indietro lo fa il
 *  codice; la foto la fa l'app all'Approva, e qui e' una riga, non un passo.
 */

import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { showToast } from './shared/utils.js';
import { AppState } from './shared/state.js';

/** Gli stati in cui il piano tiene il quaderno: l'utente non ci scrive, il
 *  composer della sua chat si spegne mentre corre, la copertina ha il bollino.
 *  `proposed` no: una proposta non ha ancora toccato niente. */
export const LIVE_STATUSES = new Set(['running', 'pausing', 'paused', 'blocked']);
/* Chiusi dopo aver girato: hanno le foto, e la scheda resta finche' non la togli. */
const RAN_STATUSES = new Set(['done', 'stopped', 'rolled_back']);
/* Il composer e' spento solo mentre un passo gira: in pausa e in blocco si parla. */
const COMPOSER_OFF = new Set(['running', 'pausing']);

const KIND_ICON = {
  research: 'ti-world-search',
  write: 'ti-pencil',
  delete: 'ti-trash',
  check: 'ti-checks',
  other: 'ti-list-details',
};

/** Il gruppo di un percorso del quaderno, per il conto del ritorno: la stessa
 *  regola di `jenny/plan/photos.py::group_of` (che decide davvero cosa si
 *  tiene). Qui serve solo a contare le caselle; `journal` non compare mai,
 *  perche' il diario non torna indietro. */
export function groupOf(path) {
  const p = String(path || '');
  if (p.startsWith('raw/journal/')) return 'journal';
  const head = p.split('/', 1)[0];
  if (head === 'wiki') return 'pages';
  if (head === 'raw') return 'sources';
  if (head === 'board') return 'board';
  return 'other';
}

export const KEEPABLE_GROUPS = ['pages', 'sources', 'board', 'other'];

/** «13:09» da un ISO. Vuoto se non si legge. */
export function clockOf(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Minuti interi fra due ISO (o fra un ISO e adesso). `null` se non si sa. */
export function minutesBetween(fromIso, toIso = null) {
  const a = Date.parse(fromIso || '');
  const b = toIso ? Date.parse(toIso) : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round((b - a) / 60000));
}

/** Quanti passi contano: i tagliati prima del via non sono del piano. */
export function countedSteps(plan) {
  return (plan?.steps || []).filter((s) => s.status !== 'cut');
}

/* Quando e' successo un evento della storia (l'approvazione, per la foto). */
function historyAt(plan, events) {
  const hit = (plan?.history || []).find((h) => events.includes(h?.event));
  return hit?.at || null;
}

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const icon = (name, cls = '') => {
  const i = el('i', `ti ${name}${cls ? ` ${cls}` : ''}`);
  i.setAttribute('aria-hidden', 'true');
  return i;
};
/* Un testo con un pezzo in grassetto al posto di `{bold}`: i pezzi a mano,
   mai `innerHTML`, perche' titoli e note li scrive il modello. */
function withBold(template, bold) {
  const span = el('span');
  const [before, after = ''] = String(template).split('{bold}');
  span.append(before);
  span.appendChild(el('b', null, bold));
  span.append(after);
  return span;
}

const t = (key, params) => i18n.t(`home.plan.${key}`, params);

/** Il testo di un rifiuto del gateway, per codice (v. il contratto in
 *  `jenny/webui/plan_commands.py`). `plan_running` e `rollback_failed` hanno
 *  parole loro: il generico «non ci sono riuscita» non dice che un altro piano
 *  tiene il quaderno, ne' che il quaderno e' rimasto a meta'. */
export function errorKey(code) {
  return {
    conflict: 'conflict',
    busy: 'busy',
    bad_state: 'conflict',
    not_found: 'gone',
    plan_running: 'planRunning',
    rollback_failed: 'rollbackFailed',
  }[code] || 'failed';
}

/* I contenitori della scheda che un ridisegno puo' aggiornare dentro invece di
   sostituire: non hanno ascoltatori loro, quindi tenerli non lascia in giro
   chiusure vecchie. Chi ha un ascoltatore (un bottone, una riga che si tocca)
   si tiene solo se e' identico, o si sostituisce intero. */
const SHELLS = new WeakSet();
const shell = (node) => {
  SHELLS.add(node);
  return node;
};

/** Porta i figli di *parent* a essere *fresh* toccando il meno possibile: un
 *  nodo uguale resta (col suo fuoco, il cursore, una composizione IME in
 *  corso), un contenitore si aggiorna dentro, il resto si sostituisce. */
function patchChildren(parent, fresh) {
  const old = [...parent.childNodes];
  fresh.forEach((node, i) => {
    const prev = old[i];
    if (!prev) {
      parent.appendChild(node);
      return;
    }
    if (prev.isEqualNode(node)) return;
    if (prev.nodeType === 1 && node.nodeType === 1 && SHELLS.has(node)
      && prev.tagName === node.tagName) {
      for (const a of [...prev.attributes]) {
        if (!node.hasAttribute(a.name)) prev.removeAttribute(a.name);
      }
      for (const a of [...node.attributes]) {
        if (prev.getAttribute(a.name) !== a.value) prev.setAttribute(a.name, a.value);
      }
      SHELLS.add(prev);
      patchChildren(prev, [...node.childNodes]);
      return;
    }
    parent.replaceChild(node, prev);
  });
  for (const n of old.slice(fresh.length)) n.remove();
}

export class HomePlan {
  /**
   * @param live       il posto vivo della chat (`HomeChat.live`).
   * @param thread     lo scroller del filo, per sapere se la scheda si vede.
   * @param chatEl     `#home-chat`: la striscia ci sta in cima e si sposta con lei.
   * @param composer   `{field, input, attach, send, root}` del composer della casa.
   * @param currentNotebook il quaderno della chat a schermo, o `null`.
   * @param onChange   chiamata dopo ogni ridisegno della scheda (`chat.liveChanged`).
   * @param onLockChange chiamata quando cambia l'elenco dei quaderni chiusi.
   * @param onComposerChange chiamata quando cambiano segnaposto o stato del composer.
   * @param sendText   manda un messaggio nella chat a schermo, con i campi in piu'.
   */
  constructor({
    live, thread, chatEl, composer = {}, currentNotebook, onChange, onLockChange,
    onComposerChange, sendText, appendOwn, turnRunning, sheet,
  } = {}) {
    this.live = live;
    this.thread = thread;
    this.chatEl = chatEl;
    this.composer = composer;
    this.currentNotebook = currentNotebook || (() => null);
    this.onChange = onChange;
    this.onLockChange = onLockChange;
    this.onComposerChange = onComposerChange;
    this.sendText = sendText;
    /** Disegna nel filo un messaggio tuo: la risposta a un piano fermo passa
     *  da `plan.answer`, non dal registro della chat, e senza l'eco la bolla
     *  comparirebbe solo rileggendo il filo. */
    this.appendOwn = appendOwn;
    /** Vero se Jenny ha un turno in volo in questa chat: Approva e Riprendi
     *  aspettano (il gateway risponderebbe `busy`). */
    this.turnRunning = turnRunning || (() => false);
    /** L'azione rimandata da un `busy`, rifatta una volta a fine turno. */
    this._retry = null;
    /** Ferma e torna indietro: il piano si e' fermato ma il ritorno no. */
    this._stopError = null;
    this.sheet = sheet || document.getElementById('home-plan-sheet');
    this.sheetBody = this.sheet?.querySelector('.home-plan-sheet-inner') || this.sheet;
    /** Il piano del quaderno a schermo, com'e' arrivato da `plan.get`. */
    this.plan = null;
    /** I piani aperti in tutti i quaderni (`plan.list`), per nome. */
    this.open = new Map();
    /** I passi tolti prima dell'Approva, per `id@version`: si azzerano con
     *  una versione nuova, che ha la sua lista. */
    this.cut = new Set();
    this._cutFor = '';
    /** I conteggi del piano finito (`plan.changes`), per `id@updated_at`. */
    this._changes = null;
    this._changesFor = '';
    this.card = null;
    this.strip = null;
    this._token = 0;
    this._busy = false;
    /** La risposta che stai scrivendo a un piano fermo, per id: un ridisegno
     *  (un frame qualunque) non deve buttarla. */
    this._answers = new Map();
    /** Il ridisegno di ogni minuto, che fa avanzare «da 4 min». */
    this._tick = null;
    /** Cosa c'e' disegnato nella scheda (`id@version@status@revisione`): se
     *  non cambia, il ridisegno aggiorna i nodi invece di rifarli. */
    this._drawnKey = '';
    /** Una lettura (`plan.get`, `plan.list`) e' fallita: si rifa' al prossimo
     *  frame, al ricollegamento, o (l'elenco) una volta da sola. Una lettura
     *  fallita non e' «nessun piano»: il piano che c'era resta a schermo. */
    this._stale = false;
    this._listStale = false;
    this._staleAt = 0;
    this._listToken = 0;
    this._listRetry = null;
    /** Quanto aspettare prima di rifare una lettura fallita (ms). */
    this.retryDelay = 2000;
    /** Quando si e' aperto l'ultimo foglio: il tocco sintetico che segue una
     *  pressione non deve chiuderlo subito dal velo (come gli altri fogli). */
    this._sheetOpenedAt = 0;

    this._buildToggle();
    this.thread?.addEventListener('scroll', () => this._syncStrip());
    /* Un tocco sul velo chiude il foglio, come gli altri fogli della casa
       (`apps-actions.js`, `home-notebook.js`): i fogli che si guardano e basta
       non hanno un Annulla, e senza questo restavano aperti. */
    this.sheet?.addEventListener('click', (e) => {
      if (e.target === this.sheet && Date.now() - this._sheetOpenedAt > 400) this.closeSheet();
    });
  }

  /* ── Letture ─────────────────────────────────────────────────────────── */

  /** Il frame `plan_changed`: un piano e' cambiato. Si rilegge l'elenco (il
   *  lucchetto e il bollino sono di tutti i quaderni) e, se e' il quaderno a
   *  schermo, la scheda. */
  handleFrame(msg) {
    if (msg?.event !== 'plan_changed') {
      /* Una lettura fallita si rifa' al primo frame che arriva (il gateway
         risponde di nuovo), al massimo una volta ogni `retryDelay`. */
      if ((this._stale || this._listStale) && msg?.event) this._retryStale();
      return;
    }
    this.refreshList();
    if (!msg.notebook || msg.notebook === this.currentNotebook()) this.refresh();
  }

  _retryStale() {
    const now = Date.now();
    if (now - this._staleAt < this.retryDelay) return;
    this._staleAt = now;
    if (this._stale) this.refresh();
    if (this._listStale) this.refreshList({ retry: false });
  }

  /** Rilegge il piano del quaderno a schermo e ridisegna. */
  async refresh() {
    const notebook = this.currentNotebook();
    const token = ++this._token;
    let plan = null;
    if (notebook) {
      try {
        const out = await rpc.plan('get', { notebook });
        plan = out?.plan || null;
      } catch (err) {
        console.warn('home.plan: get failed', err);
        if (token !== this._token) return this.plan;
        /* Non si sa com'e' il piano, che non vuol dire che non c'e': buttarlo
           toglieva la scheda di un piano che corre e riaccendeva la chat sotto
           di lui, col quaderno ancora chiuso. Si tiene quel che c'era e si
           riprova (v. `handleFrame`, e il ricollegamento in `home-app.js`). */
        this._stale = true;
        this._draw();
        return this.plan;
      }
    }
    if (token !== this._token) return this.plan;
    this._stale = false;
    this.plan = plan;
    this._draw();
    if (plan?.status === 'done' || plan?.status === 'stopped') this._loadChanges(plan);
    if (this._listStale) this.refreshList({ retry: false });
    return plan;
  }

  /** Rilegge i piani aperti di tutti i quaderni: chi e' chiuso, i bollini. */
  async refreshList({ retry = true } = {}) {
    const token = ++this._listToken;
    let plans = [];
    try {
      const out = await rpc.plan('list', {});
      plans = Array.isArray(out?.plans) ? out.plans : [];
    } catch (err) {
      console.warn('home.plan: list failed', err);
      if (token !== this._listToken) return;
      /* Il lucchetto resta com'era: se questa era la rilettura dopo l'ultimo
         `plan_changed` (il piano e' finito), senza un altro giro il quaderno
         restava chiuso finche' non arrivava un altro frame. Si riprova una
         volta da soli, e di nuovo alla prossima rilettura. */
      this._listStale = true;
      if (retry) {
        clearTimeout(this._listRetry);
        this._listRetry = setTimeout(() => {
          this._listRetry = null;
          this.refreshList({ retry: false });
        }, this.retryDelay);
        this._listRetry?.unref?.();
      }
      return;
    }
    if (token !== this._listToken) return;
    this._listStale = false;
    clearTimeout(this._listRetry);
    this._listRetry = null;
    const next = new Map();
    for (const p of plans) {
      if (p?.notebook && LIVE_STATUSES.has(p.status)) next.set(p.notebook, p);
    }
    const before = JSON.stringify([...this.open]);
    this.open = next;
    if (JSON.stringify([...next]) !== before) {
      this.onLockChange?.();
      /* L'elenco fa da riserva allo stato del composer (v. `_status`). */
      this._paintComposer();
    }
  }

  /** Il quaderno e' chiuso all'utente: c'e' un piano aperto che ci lavora. */
  isLocked(notebook) {
    return Boolean(notebook) && this.open.has(notebook);
  }

  /** Il bollino «Piano 2/8» per la copertina di un quaderno, o `null`. */
  badgeFor(notebook) {
    const p = this.open.get(notebook);
    if (!p) return null;
    const badge = el('span', `home-plan-badge is-${p.status}`);
    badge.dataset.planBadge = notebook;
    badge.appendChild(this._ring(Number(p.done) || 0, Number(p.total) || 0, { label: false }));
    badge.appendChild(el('span', null, t('badge', { done: p.done ?? 0, total: p.total ?? 0 })));
    return badge;
  }

  applyTranslations() {
    this._draw();
  }

  /** La conversazione e' cambiata: il tasto torna spento, la revisione si
   *  perde, e la scheda si rilegge per il quaderno nuovo. */
  onConversationChanged() {
    this._retry = null;
    this._setToggle(false);
    this.plan = null;
    this._draw();
    this.refresh();
  }

  /** Un turno di Jenny comincia o finisce in questa chat. A fine turno
   *  un'azione rifiutata con `busy` si rifa', una volta sola. */
  onTurnRunning(running) {
    this._draw();
    if (running || !this._retry) return;
    const { method, params } = this._retry;
    this._retry = null;
    this._call(method, params);
  }

  /** Il composer ha mandato il suo messaggio: il tasto Piano vuol dire «il
   *  **prossimo** messaggio chiede un piano», quindi si spegne, e la
   *  revisione con lui. Restando acceso faceva delle domande che seguono dei
   *  turni di pianificazione in sola lettura. La chiama solo il composer
   *  (`home-app.js::_send`): `/stop` e i comandi delle bolle non la toccano. */
  onSent() {
    if (!AppState.planTurn && !AppState.planRevise) return;
    this._setToggle(false);
    this._draw();
  }

  /** I campi del piano per il messaggio che il composer sta per mandare (il
   *  terzo argomento di `wsManager.sendToChat`): vuoti se il tasto e' spento
   *  o non vale in questa chat. */
  composerExtra() {
    if (!this._inNotebook() || LIVE_STATUSES.has(this._status())) return {};
    if (AppState.planTurn !== true) return {};
    return AppState.planRevise ? { plan: true, planRevise: AppState.planRevise } : { plan: true };
  }

  /* ── Il composer ─────────────────────────────────────────────────────── */

  _buildToggle() {
    const field = this.composer.field;
    if (!field) return;
    const btn = el('button', 'home-plan-toggle');
    btn.type = 'button';
    btn.hidden = true;
    btn.setAttribute('aria-pressed', 'false');
    btn.appendChild(icon('ti-list-check'));
    btn.appendChild(el('span', 'home-plan-toggle-label'));
    btn.addEventListener('click', () => {
      this._setToggle(!AppState.planTurn);
      this.composer.input?.focus?.();
    });
    field.insertBefore(btn, field.firstChild);
    this.toggle = btn;

    /* La riga sopra il composer: cosa succede al prossimo messaggio. */
    this.hint = el('div', 'home-plan-hint');
    this.hint.hidden = true;
    const root = this.composer.root;
    root?.parentNode?.insertBefore(this.hint, root);
  }

  _setToggle(on) {
    if (Boolean(AppState.planTurn) !== Boolean(on)) AppState.set('planTurn', Boolean(on));
    if (!on && AppState.planRevise) AppState.set('planRevise', null);
    this._paintComposer();
  }

  /** Il segnaposto che il composer deve avere adesso, o `null` per il suo. */
  placeholder() {
    if (!this._inNotebook()) return null;
    if (COMPOSER_OFF.has(this._status())) return t('composerOff');
    if (AppState.planTurn) return t('placeholder');
    return null;
  }

  _inNotebook() {
    return Boolean(this.currentNotebook());
  }

  /** Lo stato del piano del quaderno a schermo: dalla scheda se e' di questo
   *  quaderno, se no dall'elenco dei piani aperti. Cosi' una `plan.get`
   *  fallita, o un piano di un altro quaderno rimasto in mano, non
   *  riaccendono la chat sotto un piano che corre. */
  _status() {
    const notebook = this.currentNotebook();
    if (!notebook) return null;
    if (this.plan && this.plan.notebook === notebook) return this.plan.status;
    return this.open.get(notebook)?.status || null;
  }

  _paintComposer() {
    const { field, input, attach, send } = this.composer;
    const inNotebook = this._inNotebook();
    const status = this._status();
    const off = inNotebook && COMPOSER_OFF.has(status);
    /* Il tasto: solo nella chat di un quaderno, e non mentre un piano lo tiene
       (non se ne fa un secondo sopra il primo). */
    const toggleable = inNotebook && !LIVE_STATUSES.has(status);
    if (!toggleable && AppState.planTurn) {
      AppState.set('planTurn', false);
      if (AppState.planRevise) AppState.set('planRevise', null);
    }
    const on = toggleable && Boolean(AppState.planTurn);
    if (this.toggle) {
      this.toggle.hidden = !toggleable;
      this.toggle.setAttribute('aria-pressed', String(on));
      this.toggle.setAttribute('aria-label', t(on ? 'toggleOn' : 'toggleOff'));
      this.toggle.querySelector('.home-plan-toggle-label').textContent = on ? t('toggle') : '';
    }
    field?.classList.toggle('is-plan', on);
    send?.classList.toggle('is-plan', on);
    field?.classList.toggle('is-plan-off', off);
    for (const node of [input, attach, send]) {
      if (node) node.disabled = off;
    }
    if (this.hint) {
      let text = '';
      let name = '';
      if (off) {
        text = t('hintRunning');
        name = 'ti-player-pause';
      } else if (on && AppState.planRevise) {
        text = t('hintRevise', { version: this.plan?.version ?? 1 });
        name = 'ti-pencil';
      } else if (on) {
        text = t('hintPlan');
        name = 'ti-lock';
      }
      this.hint.replaceChildren();
      if (text) this.hint.append(icon(name), el('span', null, text));
      this.hint.hidden = !text;
      this.hint.classList.toggle('is-off', off);
    }
    this.onComposerChange?.();
  }

  /* ── La scheda ───────────────────────────────────────────────────────── */

  _shown() {
    const plan = this.plan;
    if (!plan || !this._inNotebook() || plan.notebook !== this.currentNotebook()) return null;
    if (plan.status === 'superseded' || plan.status === 'discarded') return null;
    /* Un ritorno rimasto a meta' non si toglie: il quaderno e' a meta', e la
       scheda e' l'unico posto che lo dice e offre di riprovare. */
    if (RAN_STATUSES.has(plan.status) && !plan.rollback_failed && this._dismissed(plan)) return null;
    return plan;
  }

  _draw() {
    this._paintComposer();
    if (!this.live) return;
    const plan = this._shown();
    if (!plan) {
      this._scheduleTick(null);
      this.card?.remove();
      this.card = null;
      this._drawnKey = '';
      this._syncStrip();
      this.onChange?.();
      return;
    }
    const key = `${plan.id}@${plan.version}`;
    if (this._cutFor !== key) {
      this._cutFor = key;
      this.cut = new Set();
    }
    if (!this.card) {
      this.card = el('div', 'home-work home-plan');
      this._drawnKey = '';
      const typing = this.live.querySelector(':scope > .home-typing');
      /* Sopra chi lavora: la scheda e' il lavoro, la riga di Jenny ne e' il dettaglio. */
      const first = this.live.querySelector(':scope > .home-work:not(.home-plan), :scope > .home-typing');
      this.live.insertBefore(this.card, first || typing || null);
    }
    this.card.className = `home-work home-plan is-${plan.status}`;
    if (plan.status === 'proposed' && AppState.planRevise === plan.id) {
      this.card.classList.add('is-review');
    }
    this.card.dataset.planId = plan.id;
    this.card.dataset.planStatus = plan.status;
    this._paintCard(plan);
    this._syncStrip();
    this._scheduleTick(plan);
    this.onChange?.();
  }

  /* Il ridisegno della scheda arriva a ogni frame (`goal_status`, ogni
     `plan_changed`, il minuto che passa): rifarla da capo spostava il cursore
     della risposta in fondo, rompeva una composizione IME e toglieva il fuoco
     ai bottoni. Con lo stesso piano nello stesso stato si aggiornano solo i
     nodi cambiati; con un piano, una versione o uno stato nuovi si rifa'
     tutto, perche' gli ascoltatori dei bottoni tengono il piano di allora. */
  _paintCard(plan) {
    const card = this.card;
    const key = `${plan.id}@${plan.version}@${plan.status}@${AppState.planRevise === plan.id}`;
    const active = document.activeElement;
    const had = active && card.contains(active) ? active : null;
    const caret = had?.classList?.contains('home-plan-answer')
      ? [had.selectionStart, had.selectionEnd] : null;
    const action = had?.dataset?.planAction || null;
    const stepBox = had?.classList?.contains('home-plan-box')
      ? had.closest('[data-step-id]')?.dataset.stepId : null;
    const fresh = this._cardParts(plan);
    if (this._drawnKey === key) patchChildren(card, fresh);
    else card.replaceChildren(...fresh);
    this._drawnKey = key;
    /* Se il nodo col fuoco e' stato sostituito, il fuoco va al suo erede. */
    if (!had || card.contains(had)) return;
    let heir = null;
    if (caret) heir = card.querySelector('.home-plan-answer');
    else if (action) heir = card.querySelector(`[data-plan-action="${action}"]`);
    else if (stepBox) heir = card.querySelector(`[data-step-id="${stepBox}"] .home-plan-box`);
    heir?.focus?.();
    if (caret && heir) {
      try {
        heir.setSelectionRange(Math.min(caret[0], heir.value.length), Math.min(caret[1], heir.value.length));
      } catch { /* un campo senza selezione: basta il fuoco */ }
    }
  }

  /* Mentre un passo gira, «da N min» va avanti da solo: un ridisegno al minuto,
     e nessuno quando non c'e' niente che corre. */
  _scheduleTick(plan) {
    clearTimeout(this._tick);
    this._tick = null;
    if (plan?.status !== 'running' && plan?.status !== 'pausing') return;
    this._tick = setTimeout(() => {
      this._tick = null;
      if (this.plan === plan) this._draw();
    }, 60000);
    /* Fuori dal browser (i banchi node) il timer non deve tenere vivo il processo. */
    this._tick?.unref?.();
  }

  _cardParts(plan) {
    const parts = [this._head(plan)];
    const meta = this._meta(plan);
    if (meta) parts.push(meta);
    if (LIVE_STATUSES.has(plan.status)) parts.push(this._bar(plan));
    if (plan.status === 'proposed') parts.push(this._photoLine());
    if (plan.status === 'done' || plan.status === 'stopped') {
      const sum = this._summary(plan);
      if (sum) parts.push(sum);
    }
    if (plan.status === 'rolled_back') parts.push(this._rolledBackNote(plan));
    if (plan.rollback_failed) parts.push(this._rollbackFailedNote(plan));
    parts.push(this._list(plan));
    const note = this._footNote(plan);
    if (note) parts.push(note);
    parts.push(this._fullLink(plan));
    const actions = this._actions(plan);
    if (actions) parts.push(actions);
    return parts;
  }

  _progress(plan) {
    const steps = countedSteps(plan);
    const done = Number.isFinite(plan.done)
      ? plan.done
      : steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
    const total = Number.isFinite(plan.total) ? plan.total : steps.length;
    return { done, total, steps };
  }

  /* Il numero del passo corrente, contato fra quelli del piano (1-based). */
  _currentIndex(plan) {
    const steps = countedSteps(plan);
    const id = plan.current_step_id
      || steps.find((s) => s.status === 'now' || s.status === 'blocked')?.id;
    const at = steps.findIndex((s) => s.id === id);
    return at < 0 ? null : at + 1;
  }

  _head(plan) {
    const head = shell(el('div', 'home-work-head'));
    const glyph = {
      blocked: 'ti-alert-triangle',
      done: 'ti-circle-check',
      stopped: 'ti-player-stop',
      rolled_back: 'ti-arrow-back-up',
    }[plan.status] || 'ti-list-check';
    head.appendChild(icon(glyph, 'home-work-icon'));
    head.appendChild(el('div', 'home-work-title', plan.title || t('untitled')));
    const { done, total } = this._progress(plan);
    let ver = '';
    let tone = '';
    switch (plan.status) {
      case 'proposed':
        if (AppState.planRevise === plan.id) {
          ver = t('reviewing');
          tone = 'is-hot';
        } else {
          ver = t('version', { version: plan.version ?? 1 });
          tone = (plan.version ?? 1) > 1 ? 'is-hot' : '';
        }
        break;
      case 'running':
        ver = t('progress', { done: Math.min(done + 1, total), total });
        tone = 'is-hot';
        break;
      case 'pausing':
        ver = t('pausingVer', { done: Math.min(done + 1, total), total });
        tone = 'is-hot';
        break;
      case 'paused':
        ver = t('pausedVer', { done, total });
        break;
      case 'blocked':
        ver = t('blockedVer', { done: this._currentIndex(plan) ?? done + 1, total });
        tone = 'is-warn';
        break;
      case 'done': {
        const minutes = minutesBetween(historyAt(plan, ['approved', 'started']) || plan.created_at,
          plan.updated_at);
        ver = minutes == null ? t('doneVerShort') : t('doneVer', { minutes });
        tone = 'is-ok';
        break;
      }
      case 'stopped':
        ver = t('stoppedVer', { done, total });
        break;
      case 'rolled_back':
        ver = t('rolledBackVer');
        break;
      default:
        break;
    }
    if (ver) head.appendChild(el('span', `home-plan-ver ${tone}`.trim(), ver));
    if (RAN_STATUSES.has(plan.status) && !plan.rollback_failed) {
      const close = el('button', 'home-plan-close');
      close.type = 'button';
      close.setAttribute('aria-label', t('close'));
      close.appendChild(icon('ti-x'));
      close.addEventListener('click', () => this._dismiss(plan));
      head.appendChild(close);
    }
    return head;
  }

  _meta(plan) {
    const meta = el('div', 'home-plan-meta');
    if (plan.status === 'proposed') {
      const total = countedSteps(plan).length;
      const kept = total - this._cutCount(plan);
      const span = el('span');
      span.appendChild(icon('ti-list-check'));
      span.appendChild(el('b', null, kept === total
        ? t('steps', { count: total })
        : t('stepsCut', { count: kept, total })));
      meta.appendChild(span);
      return meta;
    }
    if (LIVE_STATUSES.has(plan.status)) {
      const at = clockOf(historyAt(plan, ['approved', 'started']));
      const span = el('span', 'home-plan-photo');
      span.appendChild(icon('ti-camera'));
      span.append(at ? t('photoTaken', { time: at }) : t('photoTakenNoTime'));
      meta.appendChild(span);
      return meta;
    }
    return null;
  }

  _photoLine() {
    const line = el('div', 'home-plan-note home-plan-photo-line');
    line.appendChild(icon('ti-camera'));
    line.appendChild(withBold(t('photoLine'), t('photoWord')));
    return line;
  }

  _bar(plan) {
    const bar = el('div', 'home-work-bar');
    for (const step of countedSteps(plan)) {
      let state = '';
      if (step.status === 'done' || step.status === 'skipped') state = 'is-past';
      else if (step.status === 'now') state = 'is-now';
      else if (step.status === 'blocked') state = 'is-now is-blocked';
      const seg = el('span', `home-work-seg ${state}`.trim());
      bar.appendChild(seg);
    }
    return bar;
  }

  _cutCount(plan) {
    return countedSteps(plan).filter((s) => this.cut.has(s.id)).length;
  }

  _list(plan) {
    const list = shell(el('ol', 'home-plan-list'));
    const proposed = plan.status === 'proposed';
    countedSteps(plan).forEach((step, i) => {
      list.appendChild(proposed ? this._proposedItem(plan, step, i) : this._liveItem(plan, step, i));
    });
    return list;
  }

  _tags(step) {
    const tags = [];
    if (step.kind === 'delete') tags.push(['trash', 'is-danger']);
    if (step.tag === 'new') tags.push(['new', 'is-new']);
    if (step.tag === 'moved') tags.push(['moved', 'is-moved']);
    if (step.tag === 'changed') tags.push(['changed', 'is-changed']);
    return tags.map(([key, cls]) => el('span', `home-plan-tag ${cls}`, t(`tag.${key}`)));
  }

  _text(step, { time = '' } = {}) {
    const text = el('span', 'home-plan-text');
    text.append(step.title || '');
    for (const tag of this._tags(step)) text.appendChild(tag);
    if (time) text.appendChild(el('span', 'home-plan-time', time));
    return text;
  }

  _proposedItem(plan, step) {
    const off = this.cut.has(step.id);
    const item = el('li', `home-plan-item ${off ? 'is-off' : 'is-on'}${step.tag === 'new' ? ' is-new' : ''}`);
    item.dataset.stepId = step.id;
    const box = el('button', 'home-plan-box');
    box.type = 'button';
    box.setAttribute('role', 'checkbox');
    box.setAttribute('aria-checked', String(!off));
    box.setAttribute('aria-label', step.title || '');
    if (!off) box.appendChild(icon('ti-check'));
    box.addEventListener('click', () => {
      if (this.cut.has(step.id)) this.cut.delete(step.id);
      else this.cut.add(step.id);
      this._draw();
    });
    item.append(box, icon(KIND_ICON[step.kind] || KIND_ICON.other, 'home-plan-kind'), this._text(step));
    if (off) item.appendChild(el('span', 'home-plan-sub', t('cutSub')));
    /* Toccare la riga e' come toccare la casella: il bersaglio e' tutta la riga. */
    item.addEventListener('click', (e) => {
      if (e.target.closest('.home-plan-box')) return;
      box.click();
    });
    return item;
  }

  _liveItem(plan, step) {
    const reverted = plan.status === 'rolled_back';
    let state = step.status === 'pending' ? 'todo' : step.status;
    if (reverted && (step.status === 'done' || step.status === 'interrupted')) state = 'undone';
    const item = el('li', `home-plan-item is-${state}`);
    item.dataset.stepId = step.id;
    const box = el('span', 'home-plan-box');
    const mark = {
      done: 'ti-check', blocked: null, skipped: 'ti-minus', undone: 'ti-arrow-back-up',
      interrupted: 'ti-player-stop',
    }[state];
    if (state === 'blocked') box.textContent = '!';
    else if (mark) box.appendChild(icon(mark));
    const ended = step.status === 'done' || step.status === 'skipped' ? clockOf(step.ended_at) : '';
    item.append(box, icon(KIND_ICON[step.kind] || KIND_ICON.other, 'home-plan-kind'),
      this._text(step, { time: reverted ? '' : ended }));
    let sub = '';
    if (step.status === 'now') {
      const minutes = minutesBetween(step.started_at);
      sub = minutes ? t('nowSince', { minutes }) : t('nowJustStarted');
    } else if (step.status === 'blocked') {
      const at = clockOf(plan.blocked?.at || step.started_at);
      sub = at ? t('blockedSince', { time: at }) : '';
    } else if (step.status === 'skipped') {
      sub = step.note ? t('skippedBecause', { why: step.note }) : t('skipped');
    } else if (step.status === 'interrupted') {
      sub = t('interrupted');
    } else if (step.note) {
      sub = step.note;
    }
    if (sub) item.appendChild(el('span', 'home-plan-sub', sub));
    if (step.status === 'blocked' && plan.status === 'blocked') item.appendChild(this._askBox(plan));
    /* Un passo fatto si tocca per vedere cosa ha fatto: non si cambia. */
    if (step.status !== 'done' && step.status !== 'interrupted') shell(item);
    if (step.status === 'done' || step.status === 'interrupted') {
      item.classList.add('is-openable');
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      const open = () => this.openStepFiles(plan, step);
      item.addEventListener('click', open);
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
        }
      });
    }
    return item;
  }

  _askBox(plan) {
    const ask = shell(el('div', 'home-plan-ask'));
    ask.appendChild(el('div', 'home-plan-question', plan.blocked?.question || t('blockedNoQuestion')));
    const field = el('textarea', 'home-plan-answer');
    field.rows = 2;
    field.placeholder = t('answerPlaceholder');
    field.setAttribute('aria-label', t('answerPlaceholder'));
    const send = this._button(t('answer'), 'is-warn is-small', 'ti-send');
    send.classList.add('home-plan-answer-send');
    send.disabled = true;
    field.value = this._answers.get(plan.id) || '';
    send.disabled = !field.value.trim();
    field.addEventListener('input', () => {
      this._answers.set(plan.id, field.value);
      send.disabled = !field.value.trim();
    });
    field.addEventListener('click', (e) => e.stopPropagation());
    send.addEventListener('click', async (e) => {
      e.stopPropagation();
      const text = field.value.trim();
      if (!text) return;
      if (await this._call('answer', { id: plan.id, text })) {
        this._answers.delete(plan.id);
        this.appendOwn?.(text);
        showToast(t('answered'), 'success');
      }
    });
    const actions = shell(el('div', 'home-plan-actions'));
    actions.appendChild(send);
    ask.append(field, actions);
    return ask;
  }

  _footNote(plan) {
    if (plan.status === 'stopped' && this._stopError?.id === plan.id && !plan.rollback_failed) {
      const note = el('div', 'home-plan-note is-error');
      note.append(icon('ti-alert-triangle'), el('span', null,
        this._stopError.code === 'conflict' ? t('stopRollbackConflict') : t('stopRollbackFailed')));
      return note;
    }
    let key = '';
    if (plan.status === 'blocked') key = 'blockedNote';
    if (plan.status === 'pausing') key = 'pausingNote';
    if (plan.status === 'paused') {
      const next = countedSteps(plan).findIndex((s) => s.status === 'pending' || s.status === 'now');
      return el('div', 'home-plan-note', next < 0 ? t('pausedNoteEnd') : t('pausedNote', { step: next + 1 }));
    }
    if (!key) return null;
    const note = el('div', 'home-plan-note');
    if (key === 'blockedNote') note.appendChild(withBold(t(key), t('blockedNoteBold')));
    else note.textContent = t(key);
    return note;
  }

  _rolledBackNote(plan) {
    const at = clockOf(historyAt(plan, ['approved', 'started']));
    const note = el('div', 'home-plan-note');
    note.appendChild(at ? withBold(t('rolledBackNote'), at) : el('span', null, t('rolledBackNoteNoTime')));
    return note;
  }

  /* Un ritorno (o il suo annullamento) partito e fermato a meta': il
     quaderno e' in parte tornato. Lo dice il gateway in `plan.rollback_failed`
     (`{at, kind, error, applied?, total?}`), e riprovare e' sicuro: la regola
     a tre vie vede quel che e' gia' fatto come «gia' com'era». */
  _rollbackFailedNote(plan) {
    const f = plan.rollback_failed || {};
    const undo = f.kind === 'undo';
    const counted = Number.isFinite(f.applied) && Number.isFinite(f.total);
    const key = `${undo ? 'undoHalf' : 'rollbackHalf'}${counted ? '' : 'NoCount'}`;
    const note = el('div', 'home-plan-note is-error home-plan-half');
    note.append(icon('ti-alert-triangle'), el('span', null, t(key, { applied: f.applied, total: f.total })));
    return note;
  }

  _fullLink(plan) {
    const link = el('button', 'home-plan-open');
    link.type = 'button';
    link.appendChild(icon('ti-file-text'));
    link.appendChild(el('span', 'home-plan-open-label', t('full')));
    link.appendChild(el('span', 'home-plan-open-sub', t('fullSub', { version: plan.version ?? 1 })));
    link.appendChild(icon('ti-chevron-right'));
    link.addEventListener('click', () => this.openFull(plan));
    return link;
  }

  _button(label, cls = '', glyph = '') {
    const b = el('button', `home-plan-btn ${cls}`.trim());
    b.type = 'button';
    if (glyph) b.appendChild(icon(glyph));
    b.appendChild(el('span', null, label));
    return b;
  }

  _actions(plan) {
    const row = shell(el('div', 'home-plan-actions'));
    const add = (label, cls, glyph, action, run) => {
      const b = this._button(label, cls, glyph);
      b.dataset.planAction = action;
      b.addEventListener('click', run);
      row.appendChild(b);
      return b;
    };
    switch (plan.status) {
      case 'proposed': {
        /* In revisione la scheda aspetta il tuo messaggio: niente bottoni (il
           tasto Piano spento esce dalla revisione). */
        if (AppState.planRevise === plan.id) break;
        const kept = countedSteps(plan).length - this._cutCount(plan);
        const approve = add(this.cut.size ? t('approveSome', { count: kept }) : t('approve'),
          'is-primary', 'ti-check', 'approve', () => this.approve());
        approve.disabled = kept < 1 || this.turnRunning();
        add(t('revise'), '', 'ti-pencil', 'revise', () => this.startRevise());
        add(t('discard'), 'is-quiet', '', 'discard', () => this._call('discard', { id: plan.id }));
        break;
      }
      case 'running':
        add(t('pause'), '', 'ti-player-pause', 'pause', () => this._call('pause', { id: plan.id }));
        add(t('stop'), 'is-danger', 'ti-player-stop', 'stop', () => this.openStop());
        break;
      case 'pausing':
      case 'paused':
        add(t('resume'), plan.status === 'paused' ? 'is-primary' : '', 'ti-player-play', 'resume',
          () => this._call('resume', { id: plan.id }, { retryOnBusy: true }))
          .disabled = plan.status === 'paused' && this.turnRunning();
        add(t('stop'), 'is-danger', 'ti-player-stop', 'stop', () => this.openStop());
        break;
      case 'blocked':
        add(t('stop'), 'is-danger', 'ti-player-stop', 'stop', () => this.openStop());
        break;
      case 'done':
      case 'stopped':
        add(t('seeChanges'), '', 'ti-git-compare', 'changes', () => this.openChanges());
        if (plan.can_rollback !== false) {
          const again = plan.rollback_failed?.kind === 'rollback';
          add(again ? t('rollbackRetry') : t('rollback'), again ? 'is-danger' : 'is-quiet',
            'ti-arrow-back-up', 'rollback', () => this.openRollback());
        }
        break;
      case 'rolled_back':
        add(t('redo'), 'is-primary', 'ti-refresh', 'redo', () => this.redo());
        add(t('revise'), '', 'ti-pencil', 'revise', () => this.startRevise());
        if (plan.can_undo_rollback) {
          const again = plan.rollback_failed?.kind === 'undo';
          add(again ? t('undoRetry') : t('undoRollback'), again ? 'is-danger' : 'is-quiet',
            'ti-arrow-forward-up', 'undo', () => this.undoRollback());
        }
        break;
      default:
        break;
    }
    return row.children.length ? row : null;
  }

  /* I conti della scheda finita: dal confronto delle due foto (`plan.changes`). */
  _summary(plan) {
    const key = `${plan.id}@${plan.updated_at}`;
    if (this._changesFor !== key || !this._changes) return null;
    const c = this._changes;
    const inGroup = (list, group) => (list || []).filter((p) => groupOf(p) === group).length;
    const cells = [
      [inGroup(c.added, 'pages'), t('summary.newPages')],
      [(c.changed || []).length, t('summary.changed')],
      [(c.removed || []).length, t('summary.removed')],
      [inGroup(c.added, 'sources'), t('summary.sources')],
    ];
    const sum = el('div', 'home-plan-sum');
    for (const [n, label] of cells) {
      const cell = el('div');
      cell.append(el('b', null, String(n)), el('span', null, label));
      sum.appendChild(cell);
    }
    return sum;
  }

  async _loadChanges(plan) {
    const key = `${plan.id}@${plan.updated_at}`;
    if (this._changesFor === key && this._changes) return;
    try {
      const out = await rpc.plan('changes', { id: plan.id });
      this._changes = out;
      this._changesFor = key;
    } catch (err) {
      console.warn('home.plan: changes failed', err);
      return;
    }
    if (this.plan?.id === plan.id) this._draw();
  }

  /* La scheda di un piano chiuso si toglie a mano, ed e' una comodita' di chi
     guarda: il piano resta (e «Torna a prima» con lui), solo la scheda va via
     da questo telefono. */
  _dismissKey(plan) {
    return `home-plan-dismissed:${plan.id}:${plan.status}`;
  }

  _dismissed(plan) {
    try {
      return globalThis.localStorage?.getItem(this._dismissKey(plan)) === '1';
    } catch {
      return false;
    }
  }

  _dismiss(plan) {
    try {
      globalThis.localStorage?.setItem(this._dismissKey(plan), '1');
    } catch { /* senza memoria la scheda torna al prossimo avvio */ }
    this._draw();
  }

  /* ── La striscia ─────────────────────────────────────────────────────── */

  /** Vero se la scheda e' fuori dallo scroller del filo. Senza misure (un
   *  filo nascosto, un banco senza layout) la scheda si considera visibile. */
  cardOutOfView() {
    if (!this.card || !this.thread) return false;
    const view = this.thread.getBoundingClientRect();
    if (!view.height) return false;
    const box = this.card.getBoundingClientRect();
    return box.bottom <= view.top + 8 || box.top >= view.bottom - 8;
  }

  _syncStrip() {
    const plan = this._shown();
    const want = plan && LIVE_STATUSES.has(plan.status) && this.cardOutOfView();
    if (!want) {
      if (this.strip) this.strip.hidden = true;
      return;
    }
    if (!this.strip) {
      this.strip = el('div', 'home-plan-strip');
      this.strip.setAttribute('role', 'status');
      this.chatEl?.insertBefore(this.strip, this.chatEl.firstChild);
    }
    this._paintStrip(plan);
    this.strip.hidden = false;
  }

  _paintStrip(plan) {
    const strip = this.strip;
    const { done, total, steps } = this._progress(plan);
    strip.className = `home-plan-strip is-${plan.status}`;
    const current = steps.find((s) => s.status === 'now' || s.status === 'blocked');
    const index = this._currentIndex(plan);
    let title = t('strip.title', { title: plan.title || '' });
    let sub = current ? t('strip.step', { n: index, title: current.title }) : '';
    let btnIcon = 'ti-player-pause';
    let btnLabel = t('pause');
    let run = () => this._call('pause', { id: plan.id });
    let waits = false;
    if (plan.status === 'blocked') {
      title = t('strip.blocked');
      sub = t('strip.step', { n: index, title: plan.blocked?.question || current?.title || '' });
      btnIcon = 'ti-arrow-down';
      btnLabel = t('strip.show');
      run = () => this.revealCard();
    } else if (plan.status === 'paused') {
      title = t('strip.paused', { title: plan.title || '' });
      const next = steps.findIndex((s) => s.status === 'pending' || s.status === 'now');
      sub = next < 0 ? '' : t('strip.resumeFrom', { n: next + 1 });
      btnIcon = 'ti-player-play';
      btnLabel = t('resume');
      /* Lo stesso Riprendi della scheda: aspetta il turno, e un `busy` si
         rifa' da solo a fine turno invece di finire in un errore. */
      run = () => this._call('resume', { id: plan.id }, { retryOnBusy: true });
      waits = this.turnRunning();
    } else if (plan.status === 'pausing') {
      sub = t('pausingNote');
      btnIcon = 'ti-arrow-down';
      btnLabel = t('strip.show');
      run = () => this.revealCard();
    }
    const tone = { blocked: 'var(--warning)', paused: 'var(--text-muted)' }[plan.status];
    const text = el('button', 'home-plan-strip-text');
    text.type = 'button';
    text.append(el('b', null, title), el('span', null, sub));
    text.addEventListener('click', () => this.revealCard());
    const btn = el('button', 'home-plan-strip-btn');
    btn.type = 'button';
    btn.setAttribute('aria-label', btnLabel);
    if (plan.status === 'paused') btn.dataset.planAction = 'resume';
    btn.disabled = waits;
    btn.appendChild(icon(btnIcon));
    btn.addEventListener('click', run);
    strip.replaceChildren(this._ring(done, total, { tone }), text, btn);
  }

  /** L'anello «2/8» della striscia e del bollino. */
  _ring(done, total, { tone = null, label = true } = {}) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'home-plan-ring');
    svg.setAttribute('viewBox', '0 0 30 30');
    svg.setAttribute('aria-hidden', 'true');
    const r = 12;
    const c = 2 * Math.PI * r;
    const f = total > 0 ? (c * Math.min(done, total)) / total : 0;
    const track = document.createElementNS(ns, 'circle');
    for (const [k, v] of [['cx', 15], ['cy', 15], ['r', r], ['class', 'home-plan-ring-track']]) {
      track.setAttribute(k, String(v));
    }
    const arc = document.createElementNS(ns, 'circle');
    for (const [k, v] of [['cx', 15], ['cy', 15], ['r', r], ['class', 'home-plan-ring-arc'],
      ['stroke-dasharray', `${f} ${c}`], ['transform', 'rotate(-90 15 15)']]) {
      arc.setAttribute(k, String(v));
    }
    if (tone) arc.style.stroke = tone;
    svg.append(track, arc);
    if (label) {
      const txt = document.createElementNS(ns, 'text');
      for (const [k, v] of [['x', 15], ['y', 18.5], ['text-anchor', 'middle'], ['class', 'home-plan-ring-text']]) {
        txt.setAttribute(k, String(v));
      }
      txt.textContent = `${done}/${total}`;
      svg.appendChild(txt);
    }
    return svg;
  }

  /** Porta la scheda a schermo (dalla striscia). */
  revealCard() {
    if (!this.card || !this.thread) return;
    const view = this.thread.getBoundingClientRect();
    const box = this.card.getBoundingClientRect();
    this.thread.scrollTop += box.top - view.top - 8;
    this._syncStrip();
  }

  /* ── Le azioni ───────────────────────────────────────────────────────── */

  /** Una RPC del piano, e poi la rilettura. Vero se e' andata. Gli errori si
   *  dicono in parole: `conflict` vuol dire che c'e' una versione nuova. */
  async _call(method, params, { retryOnBusy = false } = {}) {
    if (this._busy) return null;
    this._busy = true;
    this.card?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    this.strip?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    let out = null;
    try {
      out = (await rpc.plan(method, params)) || { ok: true };
    } catch (err) {
      out = null;
      console.warn(`home.plan: ${method} failed`, err);
      if (err?.code === 'busy' && retryOnBusy) {
        /* Jenny sta finendo il turno (quello che ha scritto il piano, o una
           chiacchiera in pausa): si rifa' da solo quando finisce. */
        this._retry = { method, params };
        showToast(t('waitTurn'), 'info');
      } else {
        showToast(t(`error.${errorKey(err?.code)}`), 'error');
      }
    } finally {
      this._busy = false;
    }
    await this.refresh();
    this.refreshList();
    return out;
  }

  /** Approva: la versione che si vede, senza i passi tolti. */
  approve() {
    const plan = this.plan;
    if (!plan || plan.status !== 'proposed') return Promise.resolve(false);
    const cut = countedSteps(plan).filter((s) => this.cut.has(s.id)).map((s) => s.id);
    if (AppState.planRevise) AppState.set('planRevise', null);
    return this._call('approve', { id: plan.id, version: plan.version, cut }, { retryOnBusy: true });
  }

  /** Rivedi: il tasto Piano si accende in revisione, e il prossimo messaggio
   *  porta l'id del piano (`plan_revise`). Cosa cambiare lo scrivi tu. */
  startRevise() {
    const plan = this.plan;
    if (!plan) return;
    AppState.set('planTurn', true);
    AppState.set('planRevise', plan.id);
    this._draw();
    this.composer.input?.focus?.();
  }

  /** Rifai il piano (dopo un ritorno): un messaggio in modo Piano, che chiede
   *  a Jenny un piano nuovo per lo stesso lavoro. */
  redo() {
    const plan = this.plan;
    if (!plan) return false;
    return Boolean(this.sendText?.(t('redoMessage', { title: plan.title || '' }), { plan: true }));
  }

  async undoRollback() {
    const plan = this.plan;
    if (!plan) return false;
    const ok = await this._call('undo_rollback', { id: plan.id });
    if (ok) showToast(t('undone'), 'success');
    return ok;
  }

  /* ── I fogli ─────────────────────────────────────────────────────────── */

  _openSheet(children, cls = '') {
    if (!this.sheet) return null;
    this.sheet.className = `oc-sheet home-plan-sheet ${cls}`.trim();
    const grab = el('div', 'home-plan-sheet-grab');
    /* Una X visibile: i fogli che si guardano e basta (cosa ha fatto un passo,
       le modifiche, il piano completo) non hanno un Annulla, e il velo e
       Indietro non si vedono. */
    const close = el('button', 'home-plan-sheet-close');
    close.type = 'button';
    close.dataset.planAction = 'sheet-close';
    close.setAttribute('aria-label', t('sheetClose'));
    close.appendChild(icon('ti-x'));
    close.addEventListener('click', () => this.closeSheet());
    this.sheetBody.replaceChildren(grab, close, ...children.filter(Boolean));
    this._sheetOpenedAt = Date.now();
    if (!this.sheet.open) this.sheet.showModal();
    return this.sheet;
  }

  closeSheet() {
    if (this.sheet?.open) this.sheet.close();
  }

  _sheetHead(title, sub = '') {
    const nodes = [el('h2', 'home-plan-sheet-title', title)];
    if (sub) nodes.push(el('p', 'home-plan-sheet-sub', sub));
    return nodes;
  }

  _row(glyph, label, sub, cls, action, run) {
    const row = el('button', `home-plan-sheet-row ${cls || ''}`.trim());
    row.type = 'button';
    row.dataset.planAction = action;
    row.appendChild(icon(glyph));
    const text = el('span', 'home-plan-row-text');
    text.append(el('span', null, label));
    if (sub) text.appendChild(el('span', 'home-plan-row-sub', sub));
    row.appendChild(text);
    row.addEventListener('click', run);
    return row;
  }

  /* Un elenco di percorsi: «+» nuovi, matita cambiati, «−» tolti. */
  _pathList(out, { limit = 40 } = {}) {
    const box = el('div', 'home-plan-sheet-list');
    const rows = [
      ...(out.added || []).map((p) => ['is-added', 'ti-plus', p]),
      ...(out.changed || []).map((p) => ['is-changed', 'ti-pencil', p]),
      ...(out.removed || []).map((p) => ['is-removed', 'ti-minus', p]),
    ];
    for (const [cls, glyph, path] of rows.slice(0, limit)) {
      const line = el('div', cls);
      line.append(icon(glyph), el('span', null, path));
      box.appendChild(line);
    }
    if (rows.length > limit) box.appendChild(el('div', 'is-more', t('more', { count: rows.length - limit })));
    return box;
  }

  /** Cosa ha fatto un passo: il confronto fra la foto di prima e quella di
   *  dopo il passo. Si guarda e basta. */
  async openStepFiles(plan, step) {
    const n = countedSteps(plan).findIndex((s) => s.id === step.id) + 1;
    let out;
    try {
      out = await rpc.plan('step_files', { id: plan.id, step: step.id });
    } catch (err) {
      console.warn('home.plan: step_files failed', err);
      showToast(t('error.failed'), 'error');
      return null;
    }
    const added = (out?.added || []).length;
    const changed = (out?.changed || []).length;
    const removed = (out?.removed || []).length;
    const parts = [];
    const at = clockOf(step.ended_at);
    if (at) parts.push(t('stepSheet.when', { time: at }));
    parts.push(added + changed + removed
      ? t('stepSheet.counts', { added, changed, removed })
      : t('stepSheet.nothing'));
    const children = [...this._sheetHead(t('stepSheet.title', { n, title: step.title || '' }), parts.join(' '))];
    if (step.note) children.push(el('p', 'home-plan-sheet-note', step.note));
    if (added + changed + removed) children.push(this._pathList(out));
    return this._openSheet(children, 'is-step');
  }

  /** Cosa ha cambiato il piano: la foto di prima contro quella di dopo. */
  async openChanges() {
    const plan = this.plan;
    if (!plan) return null;
    let out;
    try {
      out = await rpc.plan('changes', { id: plan.id });
    } catch (err) {
      console.warn('home.plan: changes failed', err);
      showToast(t('error.failed'), 'error');
      return null;
    }
    const groups = out?.groups || {};
    const groupName = (p) => t(`group.${groups[p] || groupOf(p)}`);
    const box = el('div', 'home-plan-sheet-list');
    let any = false;
    for (const [kind, cls, glyph] of [['added', 'is-added', 'ti-plus'],
      ['changed', 'is-changed', 'ti-pencil'], ['removed', 'is-removed', 'ti-trash']]) {
      const byGroup = new Map();
      for (const p of out?.[kind] || []) {
        const g = groupName(p);
        if (!byGroup.has(g)) byGroup.set(g, []);
        byGroup.get(g).push(p);
      }
      for (const [g, paths] of byGroup) {
        any = true;
        const line = el('div', cls);
        line.dataset.kind = kind;
        line.appendChild(icon(glyph));
        line.appendChild(el('b', null, t(`changes.${kind}`, { count: paths.length, group: g })));
        const names = paths.slice(0, 6).map((p) => p.split('/').pop().replace(/\.md$/i, ''));
        line.append(` · ${names.join(', ')}${paths.length > 6 ? '…' : ''}`);
        box.appendChild(line);
      }
    }
    if (!any) box.appendChild(el('div', null, t('changes.none')));
    const start = clockOf(historyAt(plan, ['approved', 'started']));
    const end = clockOf(plan.updated_at);
    const sub = start && end ? t('changes.sub', { start, end }) : t('changes.subNoTime');
    return this._openSheet([...this._sheetHead(t('changes.title'), sub), box], 'is-changes');
  }

  /** Ferma: tre scelte, e la prima e' non fare niente. */
  openStop() {
    const plan = this.plan;
    if (!plan) return null;
    const { done, total } = this._progress(plan);
    const at = clockOf(historyAt(plan, ['approved', 'started']));
    const rows = [
      this._row('ti-player-play', t('stopSheet.continue'), t('stopSheet.continueSub'), 'is-primary',
        'continue', () => this.closeSheet()),
      this._row('ti-player-stop', t('stopSheet.keep'), t('stopSheet.keepSub'), '', 'keep',
        () => {
          this.closeSheet();
          this._call('stop', { id: plan.id, then: 'keep' });
        }),
      this._row('ti-arrow-back-up', t('stopSheet.back'),
        at ? t('stopSheet.backSub', { time: at }) : t('stopSheet.backSubNoTime'), 'is-danger', 'back',
        () => {
          this.closeSheet();
          this._stop(plan, 'rollback');
        }),
    ];
    return this._openSheet([...this._sheetHead(t('stopSheet.title'), t('stopSheet.sub', { done, total })),
      ...rows], 'is-stop');
  }

  /* Ferma e torna indietro: il piano si ferma anche se il ritorno viene
     rifiutato (`rollback_error`), e la scheda fermata lo dice. */
  async _stop(plan, then) {
    const out = await this._call('stop', { id: plan.id, then });
    if (out?.rollback_error) {
      console.warn('home.plan: rollback after stop refused', out.rollback_error);
      this._stopError = { id: plan.id, code: out.rollback_error.code || '' };
      this._draw();
    }
    return out;
  }

  /** Torna a prima: prima il conto (un ritorno a vuoto), poi il tocco. Le
   *  caselle tengono un gruppo intero (le ricerche, le pagine…). */
  async openRollback() {
    const plan = this.plan;
    if (!plan) return null;
    let preview;
    try {
      preview = await rpc.plan('rollback_preview', { id: plan.id });
    } catch (err) {
      console.warn('home.plan: rollback_preview failed', err);
      showToast(t(`error.${errorKey(err?.code)}`), 'error');
      this.refresh();
      return null;
    }
    const report = preview?.report || {};
    const restored = report.restored || [];
    const removed = report.removed || [];
    const left = report.left || [];
    const keep = new Set();
    const box = el('div', 'home-plan-sheet-list');
    const lines = el('div', 'home-plan-rollback-lines');
    const counted = () => {
      const r = restored.filter((p) => !keep.has(groupOf(p))).length;
      const g = removed.filter((p) => !keep.has(groupOf(p))).length;
      lines.replaceChildren();
      if (r) {
        const line = el('div', 'is-back');
        line.append(icon('ti-arrow-back-up'), el('span', null, t('rollbackSheet.restored', { count: r })));
        lines.appendChild(line);
      }
      if (g) {
        const line = el('div', 'is-gone');
        line.append(icon('ti-x'), el('span', null, t('rollbackSheet.removed', { count: g })));
        lines.appendChild(line);
      }
      if (!r && !g) lines.appendChild(el('div', 'is-keep', t('rollbackSheet.nothing')));
      confirm.disabled = !r && !g;
    };
    box.appendChild(lines);
    if (left.length) {
      const names = left.slice(0, 4).map((p) => p.split('/').pop().replace(/\.md$/i, ''));
      const line = el('div', 'is-keep');
      line.append(icon('ti-lock'), el('span', null, t('rollbackSheet.left', {
        names: names.join(', ') + (left.length > 4 ? '…' : ''),
      })));
      box.appendChild(line);
    }
    /* Una casella per gruppo che il ritorno toccherebbe. */
    const touched = new Map();
    for (const p of [...restored, ...removed]) {
      const g = groupOf(p);
      if (!KEEPABLE_GROUPS.includes(g)) continue;
      touched.set(g, (touched.get(g) || 0) + 1);
    }
    for (const g of KEEPABLE_GROUPS) {
      if (!touched.has(g)) continue;
      const label = el('label', 'home-plan-keep');
      const check = el('input');
      check.type = 'checkbox';
      check.dataset.group = g;
      check.addEventListener('change', () => {
        if (check.checked) keep.add(g);
        else keep.delete(g);
        counted();
      });
      label.append(check, el('span', null, t('rollbackSheet.keep', {
        group: t(`groupThe.${g}`), count: touched.get(g),
      })));
      box.appendChild(label);
    }
    const actions = el('div', 'home-plan-sheet-actions');
    const cancel = this._button(t('rollbackSheet.cancel'));
    cancel.dataset.planAction = 'cancel';
    cancel.addEventListener('click', () => this.closeSheet());
    const confirm = this._button(t('rollbackSheet.confirm'), 'is-danger-fill', 'ti-arrow-back-up');
    confirm.dataset.planAction = 'confirm';
    confirm.addEventListener('click', async () => {
      confirm.disabled = true;
      this.closeSheet();
      const ok = await this._rollback(plan, [...keep], preview?.expect_end);
      if (!ok) confirm.disabled = false;
    });
    actions.append(cancel, confirm);
    counted();
    const at = clockOf(historyAt(plan, ['approved', 'started']));
    const sub = at ? t('rollbackSheet.sub', { time: at }) : t('rollbackSheet.subNoTime');
    return this._openSheet([...this._sheetHead(t('rollbackSheet.title'), sub), box, actions], 'is-rollback');
  }

  async _rollback(plan, keep, expectEnd) {
    let out;
    try {
      out = await rpc.plan('rollback', { id: plan.id, keep, expect_end: expectEnd ?? null });
    } catch (err) {
      console.warn('home.plan: rollback failed', err);
      showToast(t(`error.${errorKey(err?.code)}`), 'error');
      await this.refresh();
      this.refreshList();
      /* `conflict`: il quaderno e' cambiato dopo il conto (`expect_end` e' la
         foto di quel conto). Il conto vecchio non vale piu': se ne rifa' uno
         e lo si rimostra, coi numeri nuovi, invece di lasciare l'utente a
         riaprire il foglio per sapere cosa tornerebbe adesso. */
      if (err?.code === 'conflict' && this.plan?.id === plan.id && this.plan.can_rollback !== false) {
        await this.openRollback();
      }
      return false;
    }
    const counts = out?.report?.counts || {};
    showToast(t('rolledBack', { count: (counts.restored || 0) + (counts.removed || 0) }), 'success');
    await this.refresh();
    this.refreshList();
    return true;
  }

  /** Il piano completo: i dettagli che Jenny rilegge a ogni passo. In sola
   *  lettura per costruzione — per cambiarlo c'e' Rivedi. */
  async openFull(plan = this.plan) {
    if (!plan) return null;
    let out;
    try {
      out = await rpc.plan('full', { id: plan.id });
    } catch (err) {
      console.warn('home.plan: full failed', err);
      showToast(t('error.failed'), 'error');
      return null;
    }
    const body = el('div', 'home-plan-full');
    (out?.steps || []).filter((s) => s.status !== 'cut').forEach((s, i) => {
      const h = el('h3', 'home-plan-full-step');
      h.append(icon(KIND_ICON[s.kind] || KIND_ICON.other), el('span', null, `${i + 1}. ${s.title || ''}`));
      for (const tag of this._tags(s)) h.appendChild(tag);
      body.appendChild(h);
      if (s.detail) body.appendChild(el('p', 'home-plan-full-detail', s.detail));
    });
    const dropped = plan.dropped || [];
    if (dropped.length) {
      body.appendChild(el('h3', 'home-plan-full-dropped', t('fullSheet.dropped')));
      const ul = el('ul', 'home-plan-full-dropped-list');
      for (const d of dropped) ul.appendChild(el('li', null, d?.title || d?.id || ''));
      body.appendChild(ul);
    }
    return this._openSheet([
      ...this._sheetHead(out?.title || plan.title || '', t('fullSheet.sub', { version: plan.version ?? 1 })),
      body,
    ], 'is-full');
  }
}
