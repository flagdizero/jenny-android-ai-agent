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
 *  **Fermare e annullare una mini-me** (26/10/2026). Sulla bolla di chi lavora
 *  c'e' «Fermala»: un foglio dal basso, nella forma del Ferma del piano, chiede
 *  se tenere quel che ha fatto o annullarlo. Una mini-me ferma (fermata da te o
 *  da Jenny, inceppata, senza fiato, interrotta) che ha cambiato dei file ha
 *  «Annulla le sue modifiche (N file)». L'annulla lo fa il server
 *  (`agent/subagent_journal.py`): rimette solo i file ancora come li ha
 *  lasciati lei, e la bolla dice quanti e quali no.
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
   *   continueSubagent?: (taskId: string, sessionKey: string) => Promise<any>,
   *   cancelSubagent?: (taskId: string, sessionKey: string, opts?: { undo?: boolean }) => Promise<any>,
   *   undoSubagent?: (taskId: string, sessionKey: string) => Promise<any>,
   *   sheet?: HTMLDialogElement | null,
   *   sendCommand?: (text: string) => void,
   *   openBoard?: () => void,
   *   onOpenInWorkshop?: () => void,
   *   onHelpers?: (running: any[]) => void,  // le mini-me al lavoro sono cambiate
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
    /* lineage -> { node, attempt, taskId, endedAt, open, stops } delle bolle
       «si e' fermato».
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
    /* Il foglio di «Fermala» / «Annulla le sue modifiche» (`index.html`). */
    this.sheet = deps.sheet !== undefined ? deps.sheet
      : (typeof document !== 'undefined' ? document.getElementById('home-work-sheet') : null);
    this._sheetBusy = false;
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
    this._refreshCards(all);
    this._maybeDoneHelpers(running, recent);
    this._resolveRestarted(all);
    this._changed();
    /* La scheda del piano conta le mini-me del passo in corso da qui. */
    this.deps.onHelpers?.(running);
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
    const canStop = Boolean(this.deps.cancelSubagent);
    if (running.length === 1) {
      this._rows(node, []);
      this._line(node, this._helperDoing(running[0]), true);
    } else {
      this._line(node, '', false);
      this._rows(node, running.map((e) => ({
        label: String(e.label || '').trim() || i18n.t('home.work.title.helperUnnamed'),
        doing: this._helperDoing(e),
        stop: canStop ? () => this.openStop(e.task_id) : null,
      })));
    }
    const stalled = running.some((e) => e.state === 'stalled'
      || (Number(e.idle_s) || 0) * 1000 + since > SLOW_AFTER_MS);
    this._slow(node, stalled ? i18n.t('home.work.helperSlow') : '');
    this._note(node, '');
    this._action(node, null);
    /* Con piu' mini-me il tasto sta su ogni riga: uno solo qui non direbbe quale. */
    const single = running.length === 1 ? running[0] : null;
    this._action2(node, single && canStop
      ? { label: i18n.t('home.work.helperStop'), run: () => this.openStop(single.task_id) }
      : null);
  }

  /* ── Fermare e annullare ── */

  /** «Fermala»: un foglio con tre scelte, e la prima e' non fare niente (la
   *  forma del Ferma del piano). Annullare c'e' solo se ha cambiato dei file. */
  openStop(taskId) {
    const entry = this.helpers.find((e) => e.task_id === taskId);
    if (!entry) return null;
    const label = String(entry.label || '').trim();
    const changes = Number(entry.changes) || 0;
    const rows = [
      this._sheetRow('ti-player-play', i18n.t('home.work.stopSheet.continue'),
        i18n.t('home.work.stopSheet.continueSub'), 'is-primary', 'continue',
        () => this._closeSheet()),
      this._sheetRow('ti-player-stop', i18n.t('home.work.stopSheet.keep'),
        changes ? i18n.t('home.work.stopSheet.keepSub') : i18n.t('home.work.stopSheet.keepSubNothing'),
        '', 'keep', () => this._stop(entry, false)),
    ];
    if (changes > 0 && this.deps.cancelSubagent) {
      rows.push(this._sheetRow('ti-arrow-back-up', i18n.t('home.work.stopSheet.undo'),
        i18n.t(changes === 1 ? 'home.work.undoSubOne' : 'home.work.undoSubMany', { count: changes }),
        'is-danger', 'undo', () => this._stop(entry, true)));
    }
    const title = label ? i18n.t('home.work.stopSheet.title', { label })
      : i18n.t('home.work.stopSheet.titleUnnamed');
    return this._openSheet(title, changes > 0 ? i18n.t('home.work.undoLimit') : '', rows);
  }

  /** «Annulla le sue modifiche» su una mini-me gia' ferma: conferma, poi il
   *  server rimette i file. */
  openUndo(node) {
    const entry = node?._entry;
    if (!entry) return null;
    const changes = Number(entry.changes) || 0;
    const rows = [
      this._sheetRow('ti-check', i18n.t('home.work.undoSheet.keep'),
        i18n.t('home.work.undoSheet.keepSub'), 'is-primary', 'keep', () => this._closeSheet()),
      this._sheetRow('ti-arrow-back-up', i18n.t('home.work.undoSheet.undo'),
        i18n.t(changes === 1 ? 'home.work.undoSubOne' : 'home.work.undoSubMany', { count: changes }),
        'is-danger', 'undo', () => this._undoStopped(node, entry)),
    ];
    return this._openSheet(i18n.t('home.work.undoSheet.title'), i18n.t('home.work.undoLimit'), rows);
  }

  async _stop(entry, undo) {
    if (this._sheetBusy) return;
    this._sheetBusy = true;
    this._sheetDisabled(true);
    const token = this._sheetToken;
    try {
      await this.deps.cancelSubagent(entry.task_id, this.deps.currentKey(), { undo });
    } catch (err) {
      this._sheetBusy = false;
      this._sheetDisabled(false);
      if (token === this._sheetToken) {
        this._sheetError(err?.message || i18n.t(undo ? 'home.work.undoFailed' : 'home.work.stopFailed'));
      }
      return;
    }
    this._sheetBusy = false;
    this._sheetDisabled(false);
    if (token === this._sheetToken) this._closeSheet();
    /* Il resto lo porta lo snapshot: la mini-me esce dalle «in corso», e se ha
       cambiato dei file torna come bolla ferma, con l'annulla o il suo esito. */
    this.loadSubagents();
  }

  async _undoStopped(node, entry) {
    if (this._sheetBusy) return;
    this._sheetBusy = true;
    this._sheetDisabled(true);
    const token = this._sheetToken;
    let out;
    try {
      out = await this.deps.undoSubagent(entry.task_id, this.deps.currentKey());
    } catch (err) {
      this._sheetBusy = false;
      this._sheetDisabled(false);
      if (token === this._sheetToken) this._sheetError(err?.message || i18n.t('home.work.undoFailed'));
      return;
    }
    this._sheetBusy = false;
    this._sheetDisabled(false);
    if (token === this._sheetToken) this._closeSheet();
    const report = out?.report || {};
    const kept = Array.isArray(report.kept) ? report.kept : [];
    this._paintFailedHelper(node, {
      ...entry, changes: 0, resumable: false,
      undo: { restored: (report.restored || []).length, kept: kept.slice(0, 5),
              kept_count: kept.length, by: report.by || 'user', at: report.at || 0 },
    });
    this.loadSubagents();
  }

  /* Cosa ha fatto l'annulla, in una riga; i file lasciati nella nota. */
  _undoneText(undo) {
    const restored = Number(undo.restored) || 0;
    const keptCount = Number(undo.kept_count) || 0;
    if (!restored && !keptCount) return i18n.t('home.work.undone.nothing');
    const who = undo.by === 'jenny' ? 'jenny' : 'you';
    return i18n.t(`home.work.undone.${who}${restored === 1 ? 'One' : ''}`, { count: restored });
  }

  _undoneNote(undo) {
    const kept = Array.isArray(undo.kept) ? undo.kept : [];
    const keptCount = Number(undo.kept_count) || kept.length;
    if (!keptCount) return '';
    const names = kept.slice(0, 3).map((k) => String(k.path || '').split('/').pop()).filter(Boolean);
    const more = keptCount > names.length ? '…' : '';
    const why = kept.every((k) => k.reason === 'changed_after' || k.reason === 'changed_during')
      ? 'home.work.undone.keptOthers' : 'home.work.undone.keptOther';
    return i18n.t(why + (keptCount === 1 ? 'One' : ''), { count: keptCount, files: names.join(', ') + more });
  }

  _openSheet(title, note, rows) {
    const sheet = this.sheet;
    if (!sheet) return null;
    const body = sheet.querySelector('.home-plan-sheet-inner') || sheet;
    const grab = el('div', 'home-plan-sheet-grab');
    const close = el('button', 'home-plan-sheet-close');
    close.type = 'button';
    close.setAttribute('aria-label', i18n.t('home.work.sheetClose'));
    const x = el('i', 'ti ti-x');
    x.setAttribute('aria-hidden', 'true');
    close.appendChild(x);
    close.addEventListener('click', () => this._closeSheet());
    const head = [el('h2', 'home-plan-sheet-title', title)];
    if (note) head.push(el('p', 'home-plan-sheet-sub', note));
    const error = el('p', 'home-plan-sheet-note home-work-sheet-error');
    error.hidden = true;
    body.replaceChildren(grab, close, ...head, ...rows, error);
    /* Ogni apertura e' un foglio nuovo: una richiesta ancora in volo di quello
       di prima non lo chiude, e finche' non torna i suoi tasti restano spenti
       (due richieste insieme fermerebbero o annullerebbero due volte). */
    this._sheetToken = (this._sheetToken || 0) + 1;
    if (this._sheetBusy) this._sheetDisabled(true);
    if (!sheet.open) {
      if (typeof sheet.showModal === 'function') sheet.showModal();
      else sheet.setAttribute('open', '');
    }
    return sheet;
  }

  _closeSheet() {
    const sheet = this.sheet;
    if (!sheet?.open) return;
    if (typeof sheet.close === 'function') sheet.close();
    else sheet.removeAttribute('open');
  }

  _sheetRow(glyph, label, sub, cls, action, run) {
    const row = el('button', `home-plan-sheet-row ${cls || ''}`.trim());
    row.type = 'button';
    row.dataset.workAction = action;
    const i = el('i', `ti ${glyph}`);
    i.setAttribute('aria-hidden', 'true');
    row.appendChild(i);
    const text = el('span', 'home-plan-row-text');
    text.append(el('span', null, label));
    if (sub) text.appendChild(el('span', 'home-plan-row-sub', sub));
    row.appendChild(text);
    row.addEventListener('click', run);
    return row;
  }

  _sheetDisabled(disabled) {
    for (const b of this.sheet?.querySelectorAll('.home-plan-sheet-row') || []) b.disabled = disabled;
  }

  _sheetError(text) {
    const node = this.sheet?.querySelector('.home-work-sheet-error');
    if (!node) return;
    node.textContent = text;
    node.hidden = !text;
  }

  /* Cosa sta facendo un aiutante: la famiglia del suo ultimo strumento, in
     parole semplici («Legge», «Cerca»). La stessa tabella della bolla di
     Jenny, cosi' le due non si contraddicono. */
  _helperDoing(entry) {
    const family = FAMILY_BY_TOOL[entry.last_tool] || (entry.last_tool ? 'busy' : 'think');
    return i18n.t(`home.work.doing.${family}`);
  }

  /* La linea e' gia' andata avanti oltre questo task: un altro task della
     stessa linea con un tentativo piu' nuovo (un rilancio), oppure con lo
     stesso tentativo ma vivo o finito dopo (una ripresa con `subagent_send`,
     che tiene l'`attempt` e cambia `task_id`). Fino al 24/10/2026 contava solo
     l'`attempt`, e dopo una ripresa la bolla «le e' finito il fiato» restava
     accanto all'aiutante ripartito. Un tentativo piu' vecchio non supera mai
     uno piu' nuovo, anche se il suo `ended_at` e' di un soffio dopo. Senza
     `lineage_id` (snapshot vecchi) non si sa, e vale no. */
  static _superseded(entry, all) {
    const lineage = entry.lineage_id;
    if (!lineage) return false;
    const attempt = Number(entry.attempt) || 0;
    const ended = Number(entry.ended_at) || 0;
    return all.some((e) => {
      if (!e || e.lineage_id !== lineage || e.task_id === entry.task_id) return false;
      const other = Number(e.attempt) || 0;
      if (other !== attempt) return other > attempt;
      const otherEnded = Number(e.ended_at) || 0;
      /* Senza `ended_at` il task e' vivo: i record finiti lo portano sempre. */
      return !otherEnded || otherEnded > ended;
    });
  }

  /* Dentro un piano Jenny guida gli aiutanti da sola: letto il resoconto e
     chiuso il suo turno (`handled_at`), se ne e' occupata lei (l'ha fatta
     continuare, o ha deciso altro), e un tasto qui farebbe il doppione del suo
     lavoro. Fuori da un piano no: Jenny legge ogni resoconto, e nascondere la
     bolla toglierebbe a te il «Riprova»; li' vale la regola di prima (finche'
     la linea non riparte, o per `FAILED_FRESH_MS`). */
  static _handledInPlan(entry) {
    return Boolean(entry && entry.handled_at && entry.plan_id);
  }

  /* Fermato: per un errore (`failed`), perche' ha finito le chiamate
     (`budget`, da cui si riprende con la storia), o da un riavvio dell'app con
     la conversazione salvata (`_interrupted`). */
  static _stopped(entry) {
    return entry.state === 'failed' || entry.state === 'budget'
      || WorkBubbles._interrupted(entry) || WorkBubbles._stoppedByHand(entry);
  }

  /* Fermata da te o da Jenny dopo aver cambiato dei file, o gia' annullata:
     la bolla dice cosa resta, e offre di toglierlo. Una fermata che non ha
     scritto niente non ha niente da dire, e resta senza bolla. */
  static _stoppedByHand(entry) {
    return Boolean(entry && entry.state === 'cancelled' && entry.cancel_reason === 'user'
      && ((Number(entry.changes) || 0) > 0 || entry.undo));
  }

  /* Quel che, cambiando, fa ridisegnare una bolla ferma gia' mostrata: un
     annulla fatto altrove (da Jenny, da un'altra finestra), la ripresa che non
     c'e' piu'. */
  static _cardSig(entry) {
    return `${Number(entry.changes) || 0}|${entry.undo?.at || ''}|${entry.resumable ? 1 : 0}`;
  }

  /* Interrotto da un riavvio (Android ha chiuso l'app mentre lavorava) con una
     fotografia della conversazione da cui ripartire. Un `cancelled` fermato da
     te (`cancel_reason: 'user'`) no: un tasto che lo rimette al lavoro accanto
     a un tuo Stop e' una trappola. */
  static _interrupted(entry) {
    return Boolean(entry && entry.state === 'cancelled'
      && entry.cancel_reason === 'shutdown' && entry.resumable);
  }

  _maybeFailedHelper(entry, all = []) {
    if (!entry || !WorkBubbles._stopped(entry) || !entry.task_id) return;
    if (this._failedShown.has(entry.task_id)) return;
    if (WorkBubbles._handledInPlan(entry)) return;
    const ended = Number(entry.ended_at) * 1000;
    /* Un interrotto dal riavvio porta come `ended_at` l'ultima fotografia, cioe'
       prima del riavvio, e l'app si riapre anche ore dopo: la sua freschezza e'
       `resumable`, che il server spegne quando la storia scade o la linea e'
       ripartita. */
    if (!ended) return;
    if (!WorkBubbles._interrupted(entry) && this._now() - ended > FAILED_FRESH_MS) return;
    /* Gia' rilanciato o ripreso prima che questa home lo vedesse (riapertura,
       o Jenny piu' svelta): un «Riprova» qui rilancerebbe un lavoro che e'
       gia' in corso, e cancellerebbe quello. */
    if (WorkBubbles._superseded(entry, all)) return;
    this._failedShown.add(entry.task_id);
    const attempt = Number(entry.attempt) || 0;
    const card = entry.lineage_id ? this._lineageCards.get(entry.lineage_id) : null;
    /* La stessa linea si e' fermata di nuovo con un task nuovo (un rilancio o
       una ripresa): la stessa bolla, che torna in fondo. */
    if (card && card.taskId !== entry.task_id) {
      card.attempt = attempt;
      card.taskId = entry.task_id;
      card.endedAt = Number(entry.ended_at) || 0;
      card.open = true;
      card.stops += 1;
      card.sig = WorkBubbles._cardSig(entry);
      this._paintFailedHelper(card.node, entry);
      this._note(card.node, i18n.t('home.work.helperStoppedAgain', { count: card.stops }));
      /* La notizia e' nuova: la bolla scende in fondo, dove guardi. */
      this.deps.chat.settle(card.node);
      return;
    }
    const node = this._bubble('helpers');
    this._paintFailedHelper(node, entry);
    if (entry.lineage_id) {
      this._lineageCards.set(entry.lineage_id, {
        node, attempt, taskId: entry.task_id, endedAt: Number(entry.ended_at) || 0,
        open: true, stops: 1, sig: WorkBubbles._cardSig(entry),
      });
    }
    this._mountLive(node);
    this.deps.chat.settle(node);
  }

  _paintFailedHelper(node, entry) {
    delete node.dataset.restarted;
    node._entry = entry;
    node.classList.add('is-failed');
    const label = String(entry.label || '').trim();
    const interrupted = WorkBubbles._interrupted(entry);
    const byHand = WorkBubbles._stoppedByHand(entry);
    const title = byHand
      ? (entry.stopped_by === 'jenny' ? 'helperStoppedByJenny' : 'helperStoppedByYou')
      : (interrupted ? 'helperInterrupted' : 'helperFailed');
    this._head(node, ICON.failed, label
      ? i18n.t(`home.work.${title}`, { label })
      : i18n.t(`home.work.${title}Unnamed`), '');
    this._bar(node, null);
    const undone = entry.undo && typeof entry.undo === 'object' ? entry.undo : null;
    const changes = Number(entry.changes) || 0;
    const budget = entry.state === 'budget'
      || entry.stop_reason === 'max_iterations' || entry.stop_reason === 'budget';
    /* Un `failed` riprendibile e' solo quello fermato dal provider (429, server,
       timeout) con la storia salvata: non e' inciampata nel lavoro. */
    const why = byHand ? 'helperStoppedKept' : interrupted ? 'helperRestart'
      : (budget ? 'helperBudget' : (entry.resumable ? 'helperProvider' : 'helperError'));
    this._line(node, undone ? this._undoneText(undone)
      : i18n.t(`home.work.why.${why}`, { count: changes }), false);
    this._note(node, undone ? this._undoneNote(undone) : '');
    const button = node.querySelector('.home-work-action');
    button.disabled = false;
    /* Annullata: niente da far continuare (la sua conversazione parlava di
       file che non ci sono piu') e niente da annullare ancora. */
    this._action2(node, !undone && changes > 0 && this.deps.undoSubagent
      ? { label: i18n.t(changes === 1 ? 'home.work.undoActionOne' : 'home.work.undoActionMany',
                        { count: changes }),
          run: () => this.openUndo(node) }
      : null);
    if (undone || byHand) {
      this._action(node, null);
      return;
    }
    /* Fermata dal budget o dal provider, o interrotta da un riavvio, con la
       storia ancora intera: si fa continuare, e riparte da dove era con quel che
       ha letto e scritto. «Rimettila in piedi» invece rilancia da zero, e
       butterebbe quel lavoro. */
    const resumable = interrupted
      || ((entry.state === 'budget' || entry.state === 'failed') && entry.resumable);
    if (resumable && this.deps.continueSubagent) {
      this._action(node, {
        label: i18n.t('home.work.helperContinue'),
        run: () => this._continueStopped(node, entry.task_id),
      });
      return;
    }
    this._action(node, entry.can_restart && this.deps.restartSubagent
      ? { label: i18n.t('home.work.helperRetry'), run: () => this._restartFailed(node, entry.task_id) }
      : null);
  }

  /* Una bolla ferma gia' mostrata si ridisegna se il suo stato e' cambiato
     altrove: Jenny l'ha annullata, la ripresa non c'e' piu'. */
  _refreshCards(all) {
    for (const card of this._lineageCards.values()) {
      if (!card.open) continue;
      const fresh = all.find((e) => e && e.task_id === card.taskId);
      if (!fresh) continue;
      const sig = WorkBubbles._cardSig(fresh);
      if (sig === card.sig) continue;
      card.sig = sig;
      this._paintFailedHelper(card.node, fresh);
    }
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

  /* Il tap su «Falla continuare»: come «Riprova», ma la mini-me riprende con
     la sua storia invece di ripartire da zero. */
  async _continueStopped(node, taskId) {
    const button = node.querySelector('.home-work-action');
    button.disabled = true;
    this._note(node, '');
    try {
      await this.deps.continueSubagent(taskId, this.deps.currentKey());
    } catch (err) {
      button.disabled = false;
      this._note(node, err?.message || i18n.t('home.work.helperContinueFailed'));
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

  /* Chiunque abbia rilanciato o ripreso la linea, o se Jenny se ne e' gia'
     occupata dentro un piano (`_handledInPlan`), la bolla del fallimento esce. */
  _resolveRestarted(all) {
    for (const [lineage, card] of this._lineageCards) {
      if (!card.open) continue;
      const self = { lineage_id: lineage, attempt: card.attempt, task_id: card.taskId,
                     ended_at: card.endedAt };
      const handled = all.some((e) => e && e.task_id === card.taskId && WorkBubbles._handledInPlan(e));
      if (!handled && !WorkBubbles._superseded(self, all)) continue;
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
    this.deps.onHelpers?.([]);
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
    /* Spenti finche' qualcuno non li riempie: una bolla ferma non li tocca mai,
       e vuoti ma accesi aggiungevano due spazi fra la riga e i tasti. */
    rows.hidden = true;
    slow.hidden = true;
    note.hidden = true;
    const action = el('button', 'home-work-action');
    action.type = 'button';
    action.addEventListener('click', () => node._run?.());
    /* La seconda azione, piu' quieta: «Fermala», «Annulla le sue modifiche». */
    const action2 = el('button', 'home-work-action2');
    action2.type = 'button';
    action2.hidden = true;
    action2.addEventListener('click', () => node._run2?.());
    const actions = el('div', 'home-work-actions');
    actions.hidden = true;
    actions.append(action, action2);
    node.append(head, bar, line, rows, slow, note, actions);
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
      if (row.stop) {
        const stop = el('button', 'home-work-row-stop');
        stop.type = 'button';
        stop.setAttribute('aria-label', i18n.t('home.work.helperStopNamed', { label: row.label }));
        const i = el('i', 'ti ti-player-stop');
        i.setAttribute('aria-hidden', 'true');
        stop.appendChild(i);
        stop.addEventListener('click', row.stop);
        li.appendChild(stop);
      }
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
    this._actionsShown(node);
  }

  _action2(node, action) {
    const button = node.querySelector('.home-work-action2');
    node._run2 = action?.run || null;
    button.hidden = !action;
    button.textContent = action?.label || '';
    this._actionsShown(node);
  }

  /* Il contenitore dei tasti sparisce quando sono spenti tutti e due: vuoto,
     aggiungerebbe lo spazio di una riga in fondo alla bolla. */
  _actionsShown(node) {
    const box = node.querySelector('.home-work-actions');
    if (!box) return;
    box.hidden = [...box.children].every((b) => b.hidden);
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
