/** La casa — la riga di lavoro.
 *
 *  Mentre Jenny lavora, sotto la conversazione compare una riga sola che dice
 *  cosa sta facendo, con una parola scelta male apposta: *elucubro*, *rovisto*,
 *  *scartabello*. Quando ha finito, sparisce.
 *
 *  **La regola che la tiene onesta: non si mostra mai un verbo di una famiglia
 *  che non sta girando.** E' l'unica cosa che distingue questa riga da
 *  un'animazione di caricamento, e il giorno che la violiamo tanto vale mettere
 *  tre puntini. I nomi degli strumenti arrivano gia' nei frame `tool_events`
 *  (v. `jenny/agent/progress_events.py`): la riga non inventa niente, traduce.
 *
 *  Mentre la risposta si sta formando la riga **sparisce**: il testo e' li' che
 *  arriva, e dirti che sto scrivendo mentre leggi quel che scrivo e' rumore.
 *  Torna se dopo il testo ricominciano gli strumenti — un turno puo' alternare
 *  le due cose piu' volte.
 */

import { i18n } from './shared/i18n.js';
import { setupLongPress } from './shared/longpress.js';

/* Da quale famiglia e' ogni strumento. Tabella e non una catena di if perche'
   e' un dizionario, e perche' e' l'unico posto da aggiornare quando nasce uno
   strumento nuovo. Chi non e' in elenco non sparisce: cade sul vocabolario
   generico, che dice "mi do da fare" invece di mentire su cosa sta facendo. */
const FAMILY_BY_TOOL = {
  // Legge: guarda qualcosa che gia' esiste, senza toccarlo.
  read_file: 'read', list_dir: 'read', get_source: 'read', recall: 'read',
  recall_history: 'read', memory: 'read', ui_view: 'read', my: 'read',
  subagent_status: 'read', list_exec_sessions: 'read', update_status: 'read',
  ssh_hosts: 'read', get_recent_logs: 'read',
  // Cerca: non sa ancora dove sia.
  grep: 'search', find_files: 'search', web_search: 'search',
  // Scrive: lascia un segno.
  write_file: 'write', edit_file: 'write', apply_patch: 'write',
  journal_append: 'write',
  // Esce: qualcosa fuori da questo telefono.
  web_fetch: 'out', browser_open: 'out', browser_read: 'out', browser_do: 'out',
  browser_close: 'out', browser_snapshot: 'out', download_file: 'out',
  ssh_exec: 'out', ssh_job: 'out', ssh_transfer: 'out', get_location: 'out',
  install_update: 'out',
  // Esegue: fa girare qualcosa.
  python_exec: 'run', write_stdin: 'run',
  // Delega: mette al lavoro qualcun altro, o il se' stesso di dopo.
  spawn: 'delegate', subagent_send: 'delegate', subagent_cancel: 'delegate',
  subagent_restart: 'delegate', long_task: 'delegate', cron: 'delegate',
  message: 'delegate',
};

const FALLBACK_FAMILY = 'busy';

/* Mezzo secondo prima di comparire: una risposta che arriva subito non deve
   far lampeggiare niente. */
const SHOW_AFTER_MS = 500;

/* Dentro una famiglia lunga la parola cambia ogni tanto, perche' una riga ferma
   sembra bloccata. Non cambia la *famiglia*: cambia solo il modo di dirla. */
const ROTATE_MS = 4_000;

export class ActivityLine {
  constructor(el, { onOpenInWorkshop } = {}) {
    this.el = el;
    this.family = null;
    this.lastWord = null;
    this.turnId = null;
    this._showTimer = null;
    this._rotateTimer = null;
    this._onOpen = onOpenInWorkshop;

    if (this._onOpen) {
      const line = this.el;
      setupLongPress(line, () => {
        if (!line.hidden) this._onOpen(this.turnId);
      });
      /* Il flag lo posa `setupLongPress` e va consumato da chi lo chiama: qui
         non c'e' niente da fare a un tocco breve, ma senza questo il flag
         resterebbe attaccato alla riga per sempre dopo la prima pressione
         lunga — e il giorno che a un tocco breve si vorra' far fare qualcosa,
         quel qualcosa partirebbe anche dopo ogni pressione lunga. */
      line.addEventListener('click', () => {
        if (line.dataset.longpress) { delete line.dataset.longpress; return; }
      });
    }
  }

  /* ── Quel che succede nel turno ── */

  /** Un turno e' partito. */
  start(turnId) {
    this.turnId = turnId || this.turnId;
    if (this.family) return;
    this._setFamily('think');
  }

  /** Jenny sta ragionando. */
  reasoning() {
    this._setFamily('think');
  }

  /** Sono partiti degli strumenti: la famiglia e' quella dell'ultimo che parte. */
  tools(events) {
    if (!Array.isArray(events)) return;
    // Solo gli `start`: un `end` dice che una cosa e' finita, non cosa sta
    // succedendo adesso, e prenderlo per buono farebbe raccontare il passato.
    const started = events.filter((e) => e && e.phase === 'start' && e.name);
    if (!started.length) return;
    const name = started[started.length - 1].name;
    this._setFamily(FAMILY_BY_TOOL[name] || FALLBACK_FAMILY);
  }

  /** La risposta sta arrivando: la riga si toglie di mezzo. */
  answering() {
    this._clear();
  }

  /** Il turno e' finito. */
  stop() {
    this._clear();
    this.turnId = null;
  }

  /* ── La riga ── */

  _setFamily(family) {
    if (this.family === family) return;
    this.family = family;
    clearTimeout(this._showTimer);
    clearInterval(this._rotateTimer);
    if (this.el.hidden) {
      // Non ancora a schermo: si aspetta il mezzo secondo prima di comparire.
      this._showTimer = setTimeout(() => this._paint(), SHOW_AFTER_MS);
    } else {
      // Gia' a schermo: la famiglia e' cambiata e si dice subito, senza
      // aspettare — il mezzo secondo serve a non lampeggiare all'inizio, non a
      // raccontare in ritardo.
      this._paint();
    }
  }

  _paint() {
    this.el.textContent = this._word();
    this.el.hidden = false;
    clearInterval(this._rotateTimer);
    this._rotateTimer = setInterval(() => {
      this.el.textContent = this._word();
    }, ROTATE_MS);
  }

  /* Una parola a caso della famiglia in corso, mai due volte di fila la stessa.
     I vocabolari si leggono da `i18n.translations` e non da `i18n.t()`: `t()`
     ritorna la chiave quando il valore non e' una stringa, e questi sono
     elenchi. Lettura sola, nessun ritocco allo strato condiviso. */
  _word() {
    const dict = i18n.translations[i18n.locale];
    const words = dict?.casa?.verbs?.[this.family] || dict?.casa?.verbs?.[FALLBACK_FAMILY];
    if (!words || !words.length) return '';
    if (words.length === 1) return words[0];
    let pick = this.lastWord;
    while (pick === this.lastWord) pick = words[Math.floor(Math.random() * words.length)];
    this.lastWord = pick;
    return pick;
  }

  _clear() {
    clearTimeout(this._showTimer);
    clearInterval(this._rotateTimer);
    this._showTimer = null;
    this._rotateTimer = null;
    this.family = null;
    this.lastWord = null;
    this.el.hidden = true;
    this.el.textContent = '';
  }
}
