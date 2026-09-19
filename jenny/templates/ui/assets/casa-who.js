/** «Con chi parli» — la tendina del titolo.
 *
 *  Dice con quale conversazione stai parlando, e quali quaderni ci sono. Un
 *  quaderno e' un progetto, che e' una wiki: l'elenco, l'ordine e le sue regole
 *  stanno in `shared/conversation-list.js`, che e' lo stesso modulo su cui si
 *  appoggia la tendina dell'officina. Qui c'e' solo il disegno.
 *
 *  **In questo giro un tocco non cambia conversazione.** E' una decisione, non
 *  un pezzo mancante (v. `.agent/casa-who-plan.md`, D1), e il pannello e'
 *  costruito perche' si veda: le righe dei quaderni non sono bottoni, non hanno
 *  chevron e non hanno la spunta. La spunta ce l'ha la riga personale, perche'
 *  li' e' vera. Il giorno che lo scambio arriva, quelle righe diventano
 *  bottoni e il resto di questo file non cambia.
 *
 *  E' un `<dialog>` aperto con `showModal()`: cosi' sta nel *top layer*, cioe'
 *  sopra la mascotte, senza una guerra di `z-index` con un elemento che in
 *  casa e' disegnato sopra tutto. Il velo e' il suo `::backdrop`. Il tasto
 *  Indietro di Android **non** arriva qui — il guscio nativo lo intercetta
 *  prima della WebView — quindi lo chiude `CasaApp.handleHardwareBack`.
 */

import { i18n } from './shared/i18n.js';
import { api } from './shared/api-client.js';
import { ConversationList, UNOPENABLE_HINT_KEYS, ago } from './shared/conversation-list.js';

/** Quanto il pannello sta sotto l'intestazione.
 *
 *  Si misura invece di fissarlo: l'altezza dell'intestazione dipende dal font
 *  del titolo, e i temi ne cambiano tre (`--font-display`). Un `top` scritto a
 *  mano andrebbe bene su un tema e taglierebbe il titolo su un altro.
 */
const GAP_UNDER_HEAD = 8;

export class WhoPanel {
  /** @param trigger il bottone dentro l'h1 — e' anche cio' che porta lo stato
   *         `aria-expanded`.
   *  @param head l'intestazione: da li' si misura dove comincia il pannello.
   *  @param personalName funzione che da' il nome della conversazione
   *         personale. E' il testo del titolo: cosi' il pannello e
   *         l'intestazione non possono dire due nomi diversi.
   */
  constructor(trigger, { head, personalName }) {
    this._trigger = trigger;
    this._head = head;
    this._personalName = personalName;
    this._list = new ConversationList(() => api.listProjects());
    this._dialog = null;
    this._body = null;
  }

  get isOpen() {
    return Boolean(this._dialog && this._dialog.open);
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }

  async open() {
    if (this.isOpen) return;
    const dialog = this._ensure();
    const bottom = this._head ? this._head.getBoundingClientRect().bottom : 64;
    dialog.style.top = `${Math.round(bottom + GAP_UNDER_HEAD)}px`;
    dialog.showModal();
    this._trigger?.setAttribute('aria-expanded', 'true');
    // Subito, con quel che c'e' in cache: una tendina che compare vuota e si
    // riempie dopo sembra rotta anche quando l'elenco e' gia' noto.
    this.render();
    await this._list.load();
    if (this.isOpen) this.render();
  }

  close() {
    if (!this.isOpen) return;
    this._dialog.close();
    this._trigger?.setAttribute('aria-expanded', 'false');
  }

  /* Il pannello nasce alla prima apertura, come il dialogo dei provider in
     officina: il guscio resta senza nodi inerti, e chi non lo apre mai non se
     lo porta nel DOM. */
  _ensure() {
    if (this._dialog) return this._dialog;
    const dialog = document.createElement('dialog');
    dialog.className = 'casa-who';
    const body = document.createElement('div');
    body.className = 'casa-who-body';
    dialog.appendChild(body);
    /* Un click il cui bersaglio e' il `<dialog>` stesso e non il suo contenuto
       e' per definizione un click sul velo: il corpo lo riempie tutto. */
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog) this.close();
    });
    /* Esc. Il `<dialog>` lo chiuderebbe da se' — e quando lo fa arriva di qui
       il `cancel`, che serve a rimettere `aria-expanded` — ma il Titan ha una
       tastiera fisica e quella via non si riesce a provare da un'automazione:
       un tasto sintetico non arriva alla chiusura del browser. Un tasto che
       *forse* funziona non e' una via d'uscita, quindi la si chiude anche da
       qui. Il secondo `close` e' un no-op. */
    dialog.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.close();
    });
    dialog.addEventListener('cancel', () => {
      this._trigger?.setAttribute('aria-expanded', 'false');
    });
    document.body.appendChild(dialog);
    this._dialog = dialog;
    this._body = body;
    return dialog;
  }

  /** Ridisegna il contenuto. Pubblico: lo richiama un cambio di lingua. */
  render() {
    if (!this._body) return;
    const body = this._body;
    body.innerHTML = '';

    body.appendChild(this._label(i18n.t('casa.who.title')));
    body.appendChild(this._personalRow());

    body.appendChild(this._label(i18n.t('casa.who.notebooks'), true));
    /* Solo i quaderni scorrono: il resto del pannello e' alto quanto e' alto e
       non deve sparire quando l'elenco cresce. Stessa divisione della tendina
       dell'officina. */
    const list = document.createElement('div');
    list.className = 'casa-who-list';
    body.appendChild(list);

    /* I tre stati sono tre, e non due: *non lo so ancora*, *non ce n'e'*, e
       *non sono riuscita a leggerlo*. Confondere l'ultimo col secondo e' il
       difetto da cui nasce `conversation-list.js`, e la nota del guasto va
       **sopra** le righe perche' quelle possono essere vecchie e questa e'
       l'unica cosa che lo dice. */
    if (this._list.loadFailed) {
      list.appendChild(this._note(i18n.t('casa.who.loadFailed'), true));
    }
    const projects = this._list.projects;
    if (projects === null) {
      if (!this._list.loadFailed) list.appendChild(this._note(i18n.t('scope.loading')));
    } else if (!projects.length) {
      if (!this._list.loadFailed) list.appendChild(this._note(i18n.t('casa.who.none')));
    } else {
      for (const project of projects) list.appendChild(this._row(project));
    }

    /* Le cartelle che ci sono e non si aprono, in fondo. **Si mostrano**: su un
       telefono non c'e' un file manager con cui rinominarle, la sola strada e'
       chiederlo a Jenny — e questa e' proprio la chat da cui si chiede.
       Sparire dall'elenco sarebbe indistinguibile dall'essere state cancellate.
       Una spiegazione per motivo, non per riga. */
    const blocked = this._list.unopenable || [];
    if (blocked.length) {
      list.appendChild(this._label(i18n.t('scope.unopenableSection')));
      for (const folder of blocked) list.appendChild(this._row(folder, true));
      for (const reason of [...new Set(blocked.map((it) => it.reason))]) {
        const key = UNOPENABLE_HINT_KEYS[reason] || 'scope.unopenableOther';
        list.appendChild(this._note(i18n.t(key, { rule: i18n.t('scope.invalidName') })));
      }
    }
  }

  _label(text, divided = false) {
    const el = document.createElement('div');
    el.className = 'casa-who-label' + (divided ? ' is-divided' : '');
    el.textContent = text;
    return el;
  }

  _note(text, error = false) {
    const el = document.createElement('p');
    el.className = 'casa-who-note' + (error ? ' is-error' : '');
    el.textContent = text;
    return el;
  }

  /* La conversazione di casa e' una sola, e la spunta e' sua. */
  _personalRow() {
    const row = document.createElement('div');
    row.className = 'casa-who-row is-personal';

    const name = document.createElement('span');
    name.className = 'casa-who-row-name';
    name.textContent = this._personalName();
    row.appendChild(name);

    const kind = document.createElement('span');
    kind.className = 'casa-who-row-meta';
    kind.textContent = i18n.t('casa.who.personal');
    row.appendChild(kind);

    const check = document.createElement('i');
    check.className = 'ti ti-check casa-who-check';
    check.setAttribute('aria-hidden', 'true');
    row.appendChild(check);
    return row;
  }

  _row(item, blocked = false) {
    const row = document.createElement('div');
    row.className = 'casa-who-row' + (blocked ? ' is-blocked' : '');

    const dot = document.createElement('span');
    dot.className = 'casa-who-dot';
    if (!blocked) dot.style.background = dotColor(item.name);
    row.appendChild(dot);

    const name = document.createElement('span');
    name.className = 'casa-who-row-name';
    name.textContent = item.name;
    row.appendChild(name);

    const when = document.createElement('span');
    when.className = 'casa-who-row-meta';
    when.textContent = ago(item.modified, (key, vars) => i18n.t(key, vars));
    row.appendChild(when);
    return row;
  }
}

/** Un colore per il pallino, stabile nel nome.
 *
 *  **Non significa niente oltre l'identita'**: serve a riconoscere una riga in
 *  un elenco di nomi che si somigliano, non a dire uno stato. Per questo non
 *  esce dai token del tema — che vorrebbero dire qualcosa: un pallino
 *  `--error` su un quaderno sano si legge come un allarme — ma da una rampa di
 *  tinte tutte alla stessa saturazione e alla stessa chiarezza, che stanno
 *  sopra qualunque sfondo dei sette temi.
 */
export function dotColor(name) {
  let hash = 0;
  for (const ch of String(name || '')) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${hash}, 38%, 58%)`;
}
