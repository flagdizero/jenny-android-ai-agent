/** La casa — la bolla di lavoro.
 *
 *  Quando qualcuno lavora e tu stai aspettando in questa chat, in fondo al filo
 *  c'e' una bolla, come un messaggio di Jenny che sta per arrivare. Una sola
 *  forma per chiunque lavori: una passata del giardiniere (`/gardener`, o quella
 *  automatica se sei dentro il suo quaderno), un ciclo di Dream chiesto a mano,
 *  le mini-me (i subagent) che Jenny ha messo al lavoro. Il turno di Jenny non
 *  ha una bolla: e' un divisore in fondo al posto vivo (`home-activity.js`),
 *  perche' con la stessa pelle si leggeva come un messaggio.
 *
 *  Quattro stati, e nessuno ne racconta piu' del necessario:
 *  - **in corso**: chi lavora, cosa fa (una riga), da quanto, una barra;
 *  - **lenta**: una riga in piu', solo dopo un minuto senza progresso o mentre
 *    il modello riprova;
 *  - **fatta**: la stessa bolla diventa il resoconto, con un'azione sola;
 *  - **non riuscita**: il motivo in parole normali, e «Riprova».
 *
 *  Nasce dal 05/10/2026: prima la chat diceva «Gardening etf-finance...» e poi
 *  niente per sei minuti, e sembrava ferma. Sostituisce la riga piccola sotto
 *  la chat e il chip degli agenti, che dicevano *che* qualcuno lavorava ma non
 *  si vedevano.
 *
 *  **Da dove vengono i dati.** Il lavoro lungo dal frame `work_status`
 *  (`jenny/runtime/work_status.py`) e, a ogni riapertura, da `/api/work`; gli
 *  aiutanti dallo snapshot `subagent_status` e da `/api/subagents`. Niente
 *  polling: l'unico timer e' quello dei minuti, e gira solo mentre c'e' una
 *  bolla in corso.
 *
 *  **Dove sta.** Le bolle in corso stanno nel «posto vivo» in fondo al filo
 *  (`HomeChat.live`), sempre sotto l'ultimo messaggio. Una bolla finita scende
 *  nel filo come un messaggio qualunque, e i messaggi dopo le vengono sotto.
 */

import { i18n } from './shared/i18n.js';
import { setupLongPress } from './shared/longpress.js';
import { saIsTerminal } from './shared/subagent-policy.js';
import { FAMILY_BY_TOOL } from './home-activity.js';

/* Ogni quanto si riscrivono i minuti, e si guarda se una bolla e' ferma. */
const TICK_MS = 30_000;

/* Senza progresso da tanto, la bolla lo dice. Prima no: una passata ha
   momenti di silenzio normali, e un avviso che scatta ogni volta e' rumore. */
export const SLOW_AFTER_MS = 60_000;

/* Un aiutante fallito si racconta solo se e' finito da poco: lo snapshot porta
   anche i finiti di ore fa, e riaprire la chat non deve tirarli fuori. */
const FAILED_FRESH_MS = 10 * 60_000;

/* Quanto resta la bolla «la mini-me ha finito»: il tempo di leggerla. Il
   risultato vero lo racconta Jenny subito dopo, nel filo; questa bolla dice
   solo che la mini-me e' tornata, e non deve restare a fare da doppione. */
const DONE_LINGER_MS = 6_000;

const ICON = {
  gardener: 'ti-plant',
  dream: 'ti-moon-stars',
  helpers: 'ti-users',
  done: 'ti-check',
  failed: 'ti-alert-circle',
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* I tre puntini che saltano: le stesse classi della bolla di Jenny. */
function dots() {
  const wrap = el('span', 'home-ellipsis');
  wrap.setAttribute('aria-hidden', 'true');
  for (let k = 0; k < 3; k++) {
    const d = el('span', 'home-ellipsis-dot', '.');
    d.style.setProperty('--k', String(k));
    wrap.appendChild(d);
  }
  return wrap;
}

export class WorkBubbles {
  /**
   * @param {HTMLElement} liveEl il posto vivo del filo (`HomeChat.live`).
   * @param {{
   *   chat: { settle: (node: HTMLElement) => void, liveChanged: () => void },
   *   currentKey: () => string,
   *   fetchWork: (sessionKey: string) => Promise<any>,
   *   fetchSubagents: (sessionKey: string) => Promise<any>,
   *   restartSubagent?: (taskId: string, sessionKey: string) => Promise<any>,
   *   sendCommand?: (text: string) => void,
   *   openBoard?: () => void,
   *   onOpenInWorkshop?: () => void,
   *   now?: () => number,
   * }} deps
   */
  constructor(liveEl, deps) {
    this.live = liveEl;
    this.deps = deps;
    this._now = deps.now || (() => Date.now());
    /* id del lavoro -> { payload, node, receivedAt }. */
    this.works = new Map();
    /* Gli id dei lavori di cui questa home ha mostrato la bolla: i loro testi
       (l'annuncio e il resoconto) non si disegnano una seconda volta. */
    this._claimed = new Set();
    this.helpers = [];
    this._helpersNode = null;
    this._helpersAt = 0;
    this._failedShown = new Set();
    /* task_id -> label delle mini-me che questa home ha visto lavorare: solo
       loro, finendo, dicono «ho finito». Una conclusa da ore che arriva con lo
       snapshot di una riapertura non e' una notizia. */
    this._seenRunning = new Map();
    this._doneNode = null;
    this._doneTimer = null;
    this._doneLabels = [];
    this.doneLingerMs = DONE_LINGER_MS;
    /* lineage -> { node, attempt, open, stops } delle bolle «si e' fermato».
       Una linea ha una bolla sola: quando riparte (dal tuo «Riprova», da Jenny,
       dall'officina) la bolla esce dal filo, perche' il tentativo nuovo ha gia'
       la sua bolla «in corso» (`open` falso); quando si ferma di nuovo, torna la
       stessa bolla in fondo al filo. Fino al 06/10/2026 ogni tentativo ne apriva
       una sua; fino al 07/10 quella fermata restava come «Ripartito», e dopo un
       «Riprova» le bolle erano due, una vecchia e una che girava. */
    this._lineageCards = new Map();
    /* Ogni frame alza il contatore: una lettura HTTP partita prima di un frame
       porta uno stato piu' vecchio, e non deve sovrascriverlo (la regola del
       chip degli agenti, che questa bolla sostituisce). */
    this._workFrames = 0;
    this._helperFrames = 0;
    this._tick = null;
  }

  /* ── Il lavoro lungo: giardiniere, Dream ── */

  /** Un frame `work_status` della conversazione a schermo. */
  ingestWork(payload) {
    this._workFrames += 1;
    this._upsertWork(payload);
  }

  /** Rilegge i lavori della conversazione a schermo (riapertura, cambio chat). */
  async loadWork() {
    const key = this.deps.currentKey();
    const before = this._workFrames;
    let body;
    try {
      body = await this.deps.fetchWork(key);
    } catch {
      return;
    }
    if (key !== this.deps.currentKey() || before !== this._workFrames) return;
    for (const payload of body?.work || []) this._upsertWork(payload, { fromSnapshot: true });
  }

  /** Vero se il testo con questo `work_id` racconta un lavoro che e' gia' una bolla. */
  claims(workId) {
    return typeof workId === 'string' && this._claimed.has(workId);
  }

  _upsertWork(payload, { fromSnapshot = false } = {}) {
    if (!payload || typeof payload.id !== 'string') return;
    const known = this.works.get(payload.id);
    const finished = payload.state === 'done' || payload.state === 'failed';
    /* Da uno snapshot, un lavoro finito che questa home non ha visto lavorare
       non diventa una bolla: il suo resoconto di testo e' gia' nel filo. */
    if (!known && finished && fromSnapshot) return;
    if (known?.settled) return;
    this._claimed.add(payload.id);
    const entry = known || { node: null };
    entry.payload = payload;
    entry.receivedAt = this._now();
    this.works.set(payload.id, entry);
    if (!entry.node) {
      entry.node = this._bubble(payload.kind);
      this._mountLive(entry.node);
    }
    this._paintWork(entry);
    if (finished) this._settle(entry);
    this._changed();
  }

  _paintWork(entry) {
    const p = entry.payload;
    const node = entry.node;
    const finished = p.state === 'done' || p.state === 'failed';
    node.classList.toggle('is-done', p.state === 'done');
    node.classList.toggle('is-failed', p.state === 'failed');
    const kind = p.kind === 'dream' ? 'dream' : 'gardener';
    const title = finished
      ? i18n.t(p.state === 'done' ? 'home.work.done' : 'home.work.failed')
      : i18n.t(kind === 'dream' ? 'home.work.title.dream'
        : (p.manual === false ? 'home.work.title.gardenerAuto' : 'home.work.title.gardener'));
    const icon = finished ? ICON[p.state] : ICON[kind];
    const end = finished && typeof p.ended_at === 'number' ? p.ended_at * 1000 : this._now();
    this._head(node, icon, title, this._elapsed(p.started_at * 1000, end));

    if (finished) {
      this._bar(node, null);
      this._line(node, this._summary(p), false);
      this._slow(node, '');
      this._note(node, '');
      this._action(node, this._finishedAction(p));
      return;
    }
    this._bar(node, p.phases, p.phase);
    this._line(node, this._doing(p), true);
    this._slow(node, this._slowText(p, entry.receivedAt));
    this._note(node, p.manual === false ? i18n.t('home.work.autoNote') : '');
    this._action(node, null);
  }

  /* Cosa sta facendo, in una riga: la fase, e quante pagine ha scritto. */
  _doing(p) {
    const phase = p.phase ? i18n.t(`home.work.phase.${p.phase}`) : i18n.t('home.work.phase.start');
    if (p.phase === 'pages' && p.writes > 0) {
      const count = i18n.t(p.writes === 1 ? 'home.work.writesOne' : 'home.work.writesMany',
                           { count: p.writes });
      return `${phase} · ${count}`;
    }
    return phase;
  }

  _slowText(p, receivedAt) {
    if (p.retry && p.retry.attempt) {
      return i18n.t('home.work.retry', { attempt: p.retry.attempt, of: p.retry.of || 3 });
    }
    const updated = typeof p.updated_at === 'number' ? p.updated_at * 1000 : receivedAt;
    return this._now() - Math.max(updated, receivedAt) > SLOW_AFTER_MS ? i18n.t('home.work.slow') : '';
  }

  /* Il resoconto, dai campi dell'esito: niente prosa dal server. */
  _summary(p) {
    const o = p.outcome || {};
    if (p.state === 'failed') {
      const reason = { model: 'model', not_a_project: 'notAProject' }[o.reason] || 'error';
      return i18n.t(`home.work.why.${reason}`);
    }
    if (p.kind === 'dream') {
      const key = { advanced: 'dreamAdvanced', no_input: 'dreamNothing' }[o.status] || 'dreamHeld';
      return i18n.t(`home.work.result.${key}`);
    }
    const parts = [];
    if (o.status === 'already_running') return i18n.t('home.work.result.alreadyRunning');
    if (o.writes > 0) {
      parts.push(i18n.t(o.writes === 1 ? 'home.work.result.pagesOne' : 'home.work.result.pagesMany',
                        { count: o.writes }));
    }
    if (o.proposals > 0) parts.push(i18n.t('home.work.result.boardProposed'));
    if (!parts.length && o.absence === 'research') parts.push(i18n.t('home.work.result.research'));
    if (!parts.length) parts.push(i18n.t('home.work.result.nothing'));
    return parts.join(' ');
  }

  _finishedAction(p) {
    const o = p.outcome || {};
    if (p.state === 'failed') {
      const command = p.kind === 'dream' ? '/dream' : '/gardener';
      if (!this.deps.sendCommand || o.reason === 'not_a_project') return null;
      return { label: i18n.t('home.work.retryAction'), run: () => this.deps.sendCommand(command) };
    }
    if (p.kind === 'gardener' && o.proposals > 0 && this.deps.openBoard) {
      return { label: i18n.t('home.work.openBoard'), run: () => this.deps.openBoard() };
    }
    return null;
  }

  /* ── Gli aiutanti ── */

  /** Uno snapshot `subagent_status` della conversazione a schermo. */
  ingestSubagents(snapshot) {
    this._helperFrames += 1;
    this._renderHelpers(snapshot);
  }

  async loadSubagents() {
    const key = this.deps.currentKey();
    const before = this._helperFrames;
    let snapshot;
    try {
      snapshot = await this.deps.fetchSubagents(key);
    } catch {
      return;
    }
    if (key !== this.deps.currentKey() || before !== this._helperFrames) return;
    this._renderHelpers(snapshot);
  }

  _renderHelpers(snapshot) {
    const running = Array.isArray(snapshot?.running)
      ? snapshot.running.filter((entry) => entry && !saIsTerminal(entry.state))
      : [];
    this.helpers = running;
    this._helpersAt = this._now();
    if (!running.length) {
      this._helpersNode?.remove();
      this._helpersNode = null;
    } else {
      if (!this._helpersNode) {
        this._helpersNode = this._bubble('helpers');
        this._mountLive(this._helpersNode);
      }
      this._paintHelpers();
    }
    const recent = Array.isArray(snapshot?.recent) ? snapshot.recent : [];
    const all = [...running, ...recent];
    for (const entry of recent) this._maybeFailedHelper(entry, all);
    this._maybeDoneHelpers(running, recent);
    this._resolveRestarted(all);
    this._changed();
  }

  _paintHelpers() {
    const node = this._helpersNode;
    const running = this.helpers;
    const label = running.length === 1 ? String(running[0].label || '').trim() : '';
    const title = running.length > 1
      ? i18n.t('home.work.title.helpersMany', { count: running.length })
      : (label ? i18n.t('home.work.title.helperOne', { label }) : i18n.t('home.work.title.helperUnnamed'));
    const oldest = Math.max(...running.map((e) => Number(e.elapsed_s) || 0));
    const since = this._now() - this._helpersAt;
    this._head(node, ICON.helpers, title, this._elapsed(0, oldest * 1000 + since));
    this._bar(node, null);
    if (running.length === 1) {
      this._rows(node, []);
      this._line(node, this._helperDoing(running[0]), true);
    } else {
      this._line(node, '', false);
      this._rows(node, running.map((e) => ({
        label: String(e.label || '').trim() || i18n.t('home.work.title.helperUnnamed'),
        doing: this._helperDoing(e),
      })));
    }
    const stalled = running.some((e) => e.state === 'stalled'
      || (Number(e.idle_s) || 0) * 1000 + since > SLOW_AFTER_MS);
    this._slow(node, stalled ? i18n.t('home.work.helperSlow') : '');
    this._note(node, '');
    this._action(node, null);
  }

  /* Cosa sta facendo un aiutante: la famiglia del suo ultimo strumento, in
     parole semplici («Legge», «Cerca»). La stessa tabella della bolla di
     Jenny, cosi' le due non si contraddicono. */
  _helperDoing(entry) {
    const family = FAMILY_BY_TOOL[entry.last_tool] || (entry.last_tool ? 'busy' : 'think');
    return i18n.t(`home.work.doing.${family}`);
  }

  /* Un tentativo piu' nuovo della stessa linea, vivo o finito: il lavoro e'
     gia' ripartito. Senza `lineage_id` (snapshot vecchi) non si sa, e vale no. */
  static _newerAttempt(entry, all) {
    const lineage = entry.lineage_id;
    if (!lineage) return false;
    const attempt = Number(entry.attempt) || 0;
    return all.some((e) => e && e.lineage_id === lineage && (Number(e.attempt) || 0) > attempt);
  }

  _maybeFailedHelper(entry, all = []) {
    if (!entry || entry.state !== 'failed' || !entry.task_id) return;
    if (this._failedShown.has(entry.task_id)) return;
    const ended = Number(entry.ended_at) * 1000;
    if (!ended || this._now() - ended > FAILED_FRESH_MS) return;
    /* Gia' rilanciato prima che questa home lo vedesse (riapertura, o Jenny
       piu' svelta): un «Riprova» qui rilancerebbe un lavoro che e' gia' in
       corso, e cancellerebbe quello. */
    if (WorkBubbles._newerAttempt(entry, all)) return;
    this._failedShown.add(entry.task_id);
    const attempt = Number(entry.attempt) || 0;
    const card = entry.lineage_id ? this._lineageCards.get(entry.lineage_id) : null;
    if (card && card.attempt < attempt) {
      card.attempt = attempt;
      card.open = true;
      card.stops += 1;
      this._paintFailedHelper(card.node, entry);
      this._note(card.node, i18n.t('home.work.helperStoppedAgain', { count: card.stops }));
      /* La notizia e' nuova: la bolla scende in fondo, dove guardi. */
      this.deps.chat.settle(card.node);
      return;
    }
    const node = this._bubble('helpers');
    this._paintFailedHelper(node, entry);
    if (entry.lineage_id) {
      this._lineageCards.set(entry.lineage_id, { node, attempt, open: true, stops: 1 });
    }
    this._mountLive(node);
    this.deps.chat.settle(node);
  }

  _paintFailedHelper(node, entry) {
    delete node.dataset.restarted;
    node.classList.add('is-failed');
    const label = String(entry.label || '').trim();
    this._head(node, ICON.failed, label
      ? i18n.t('home.work.helperFailed', { label })
      : i18n.t('home.work.helperFailedUnnamed'), '');
    this._bar(node, null);
    const why = entry.stop_reason === 'max_iterations' || entry.stop_reason === 'budget'
      ? 'home.work.why.helperBudget' : 'home.work.why.helperError';
    this._line(node, i18n.t(why), false);
    this._note(node, '');
    const button = node.querySelector('.home-work-action');
    button.disabled = false;
    this._action(node, entry.can_restart && this.deps.restartSubagent
      ? { label: i18n.t('home.work.helperRetry'), run: () => this._restartFailed(node, entry.task_id) }
      : null);
  }

  /* Le mini-me appena tornate. Si guardano solo quelle viste lavorare qui, e
     quelle finite insieme fanno una bolla sola. */
  _maybeDoneHelpers(running, recent) {
    for (const entry of running) {
      if (entry.task_id) this._seenRunning.set(entry.task_id, String(entry.label || '').trim());
    }
    const done = [];
    for (const entry of recent) {
      if (!entry || entry.state !== 'done' || !this._seenRunning.has(entry.task_id)) continue;
      done.push(String(entry.label || '').trim() || this._seenRunning.get(entry.task_id));
      this._seenRunning.delete(entry.task_id);
    }
    if (done.length) this._showDone(done);
  }

  _showDone(labels) {
    /* Se ne torna un'altra mentre la bolla e' ancora li', la bolla le conta
       tutte e due, e il tempo riparte. */
    this._doneLabels = [...this._doneLabels, ...labels];
    if (!this._doneNode) {
      this._doneNode = this._bubble('helpers');
      this._doneNode.classList.add('is-done');
      this._mountLive(this._doneNode);
    }
    this._paintDone();
    clearTimeout(this._doneTimer);
    this._doneTimer = setTimeout(() => this._hideDone(), this.doneLingerMs);
    this._doneTimer?.unref?.();
  }

  _paintDone() {
    const node = this._doneNode;
    const labels = this._doneLabels;
    const label = labels.length === 1 ? labels[0] : '';
    const title = labels.length > 1
      ? i18n.t('home.work.helpersDoneMany', { count: labels.length })
      : (label ? i18n.t('home.work.helperDone', { label }) : i18n.t('home.work.helperDoneUnnamed'));
    this._head(node, ICON.done, title, '');
    this._bar(node, null);
    this._line(node, '', false);
    this._rows(node, []);
    this._slow(node, '');
    this._note(node, '');
    this._action(node, null);
  }

  _hideDone() {
    clearTimeout(this._doneTimer);
    this._doneTimer = null;
    this._doneLabels = [];
    if (!this._doneNode) return;
    this._doneNode.remove();
    this._doneNode = null;
    this.deps.chat.liveChanged();
  }

  /* Il tap su «Riprova»: il bottone si spegne subito (un secondo tap
     rilancerebbe ancora, cancellando il primo); se il rilancio parte la bolla
     esce, se no dice perche'. Prima restava identica, e il rilancio partito
     sembrava un tap perso. */
  async _restartFailed(node, taskId) {
    const button = node.querySelector('.home-work-action');
    button.disabled = true;
    this._note(node, '');
    try {
      await this.deps.restartSubagent(taskId, this.deps.currentKey());
    } catch (err) {
      button.disabled = false;
      this._note(node, err?.message || i18n.t('home.work.helperRetryFailed'));
      return;
    }
    this._markRestarted(node);
  }

  /* Il lavoro e' ripartito: la bolla del fallimento esce dal filo, e resta
     quella del tentativo in corso. */
  _markRestarted(node) {
    if (node.dataset.restarted) return;
    node.dataset.restarted = '1';
    const card = [...this._lineageCards.values()].find((c) => c.node === node);
    if (card) card.open = false;
    node.remove();
    this._changed();
  }

  /* Chiunque abbia rilanciato la linea, la bolla del fallimento esce. */
  _resolveRestarted(all) {
    for (const [lineage, card] of this._lineageCards) {
      if (!card.open) continue;
      if (!WorkBubbles._newerAttempt({ lineage_id: lineage, attempt: card.attempt }, all)) continue;
      this._markRestarted(card.node);
      card.open = false;
    }
  }

  /* ── La conversazione cambia, la lingua cambia ── */

  /** Si cambia conversazione: quel che era in corso qui era dell'altra. */
  clear() {
    this._workFrames += 1;
    this._helperFrames += 1;
    for (const entry of this.works.values()) if (!entry.settled) entry.node?.remove();
    this.works.clear();
    this._helpersNode?.remove();
    this._helpersNode = null;
    this.helpers = [];
    this._lineageCards.clear();
    this._seenRunning.clear();
    this._hideDone();
    this._changed();
  }

  applyTranslations() {
    for (const entry of this.works.values()) this._paintWork(entry);
    if (this._helpersNode) this._paintHelpers();
    if (this._doneNode) this._paintDone();
  }

  /* ── La bolla ── */

  _bubble(kind) {
    const node = el('article', `home-work home-work-${kind}`);
    node.setAttribute('aria-live', 'polite');
    const head = el('div', 'home-work-head');
    const icon = el('i', 'ti home-work-icon');
    icon.setAttribute('aria-hidden', 'true');
    head.append(icon, el('span', 'home-work-title'), el('span', 'home-work-time'));
    const bar = el('div', 'home-work-bar');
    const line = el('div', 'home-work-line');
    const rows = el('ul', 'home-work-rows');
    const slow = el('div', 'home-work-slow');
    const note = el('div', 'home-work-note');
    const action = el('button', 'home-work-action');
    action.type = 'button';
    action.addEventListener('click', () => node._run?.());
    node.append(head, bar, line, rows, slow, note, action);
    if (this.deps.onOpenInWorkshop) {
      setupLongPress(node, () => this.deps.onOpenInWorkshop());
      /* Il tap sintetico che segue la pressione lunga si ferma qui, in
         cattura, prima di arrivare al bottone: una pressione lunga su
         «Riprova» apre l'officina, non rilancia anche il lavoro. */
      node.addEventListener('click', (e) => {
        if (node.dataset.longpress) {
          delete node.dataset.longpress;
          e.stopPropagation();
        }
      }, true);
    }
    return node;
  }

  _head(node, icon, title, time) {
    const i = node.querySelector('.home-work-icon');
    i.className = `ti ${icon} home-work-icon`;
    node.querySelector('.home-work-title').textContent = title;
    node.querySelector('.home-work-time').textContent = time;
  }

  /* La barra: un tratto per fase se le fasi si sanno, altrimenti una barra che
     scorre. `null` toglie la barra (una bolla finita non ne ha). */
  _bar(node, phases, phase) {
    const bar = node.querySelector('.home-work-bar');
    bar.replaceChildren();
    if (phases === null) {
      bar.className = 'home-work-bar';
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    const list = Array.isArray(phases) ? phases : [];
    const at = list.indexOf(phase);
    if (!list.length || at < 0) {
      bar.className = 'home-work-bar is-sweep';
      bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-label', i18n.t('home.work.inProgress'));
      bar.removeAttribute('aria-valuenow');
      bar.appendChild(el('span', 'home-work-sweep'));
      return;
    }
    bar.className = 'home-work-bar';
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', String(list.length));
    bar.setAttribute('aria-valuenow', String(at + 1));
    bar.setAttribute('aria-label', i18n.t('home.work.step', { at: at + 1, of: list.length }));
    list.forEach((_, i) => {
      bar.appendChild(el('span', `home-work-seg${i < at ? ' is-past' : ''}${i === at ? ' is-now' : ''}`));
    });
  }

  _line(node, text, busy) {
    const line = node.querySelector('.home-work-line');
    line.replaceChildren();
    line.hidden = !text;
    if (!text) return;
    line.append(document.createTextNode(text));
    if (busy) line.appendChild(dots());
  }

  _rows(node, rows) {
    const list = node.querySelector('.home-work-rows');
    list.replaceChildren();
    list.hidden = !rows.length;
    for (const row of rows) {
      const li = el('li', 'home-work-row');
      li.append(el('span', 'home-work-row-label', row.label), el('span', 'home-work-row-doing', row.doing));
      list.appendChild(li);
    }
  }

  _slow(node, text) {
    const slow = node.querySelector('.home-work-slow');
    slow.replaceChildren();
    slow.hidden = !text;
    if (!text) return;
    const icon = el('i', 'ti ti-clock-hour-4');
    icon.setAttribute('aria-hidden', 'true');
    slow.append(icon, document.createTextNode(text));
  }

  _note(node, text) {
    const note = node.querySelector('.home-work-note');
    note.textContent = text;
    note.hidden = !text;
  }

  _action(node, action) {
    const button = node.querySelector('.home-work-action');
    node._run = action?.run || null;
    button.hidden = !action;
    button.textContent = action?.label || '';
  }

  /* Minuti, per una persona: «ora», «4 min». */
  _elapsed(from, to) {
    const minutes = Math.floor(Math.max(0, to - from) / 60_000);
    return minutes < 1 ? i18n.t('home.work.time.now') : i18n.t('home.work.time.min', { min: minutes });
  }

  /* ── Il posto vivo ── */

  /* Le bolle in corso stanno sopra quella di Jenny che scrive, che e' sempre
     l'ultima: e' lei la cosa che arriva subito. */
  _mountLive(node) {
    const typing = this.live.querySelector(':scope > .home-typing');
    this.live.insertBefore(node, typing);
  }

  /* Il lavoro e' finito: la bolla scende nel filo, e da li' in poi e' un
     messaggio come gli altri — quel che arriva dopo le va sotto. */
  _settle(entry) {
    entry.settled = true;
    this.deps.chat.settle(entry.node);
  }

  _changed() {
    const running = [...this.works.values()].some((e) => !e.settled) || this.helpers.length > 0;
    if (running && !this._tick) {
      this._tick = setInterval(() => this._onTick(), TICK_MS);
      /* Fuori dal browser (i banchi in node) il timer non deve tenere vivo il
         processo; nel browser `unref` non c'e' e la riga non fa niente. */
      this._tick?.unref?.();
    } else if (!running && this._tick) {
      clearInterval(this._tick);
      this._tick = null;
    }
    this.deps.chat.liveChanged();
  }

  _onTick() {
    for (const entry of this.works.values()) if (!entry.settled) this._paintWork(entry);
    if (this._helpersNode) this._paintHelpers();
  }
}
