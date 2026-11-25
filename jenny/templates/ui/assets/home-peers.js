/** La casa — la stanza «Altre Jenny».
 *
 *  Fino al 22/11/2026 le altre Jenny stavano solo in officina (`mobile-peers.js`,
 *  cassetto Mani). Da quando la linea si passa dalla chat (`home-line.js`) sono
 *  una cosa di chi usa Jenny, non di chi la regola.
 *
 *  Dal 25/11/2026 la stanza è un menu. Prima ogni collegata era una scheda con
 *  dentro tutto — stato, «Parla», interruttore, livello — e con tre collegate la
 *  pagina era piena; file, cronologia e scollegamento erano un rimando
 *  all'officina. Ora l'elenco è solo un elenco, e ogni collegata ha la sua
 *  pagina, dove sta tutto quel che la riguarda.
 *
 *  Le viste, una alla volta:
 *  - **l'elenco** (`list`): una riga per collegata, nome e stato; sotto,
 *    «Collega» e lo stato di Tailscale su questo telefono. Senza collegate, a
 *    cosa servono e il tasto per collegarne una;
 *  - **la pagina di una collegata** (`peer`): «Parla», cosa può fare qui (la
 *    linea, domande e richieste, i file), la cronologia, la prova, il nome,
 *    «Scollega». Un computer con opencode non ha permessi: risponde e basta;
 *  - **la cronologia** (`history`) e **il nome** (`rename`), dalla sua pagina;
 *  - **collega, passo 1** (`add`): mostrare il QR o scrivere il codice dell'altra;
 *  - **passo 2** (`qr`, `join`): il QR, o il modulo. Una Jenny nuova comparsa
 *    mentre il codice è aperto è quella che l'ha usato;
 *  - **passo 3** (`name`): come la chiami — è il nome che dirai in chat — e i
 *    due permessi.
 *
 *  Due permessi fini restano solo in officina: la conferma dell'uso del
 *  telefono dal suo, e le due righe di riassunto dopo una linea.
 *
 *  Lo stato arriva dagli stessi comandi dell'officina (`rpc.peer`,
 *  `jenny/peers/rpc.py`): qui non c'è una seconda verità.
 */

import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { button, el, icon } from './shared/dom.js';
import { closeActionSheet, openActionSheet, sheetHead, sheetRow } from './shared/sheet.js';
import { showToast } from './shared/utils.js';
import { isComputer, peerGlyph } from './home-line.js';

const t = (key, params) => i18n.t(`home.peers.${key}`, params);
/* Le frasi della cronologia e delle richieste di fare sono quelle dell'officina:
   una riga del registro si legge uguale nei due posti. */
const tw = (key, params) => i18n.t(`settings.peers.${key}`, params);

const POLL_MS = 4000;
const QR_SRC = '/html-mobile/assets/vendor/qrcode-generator@1.4.4/qrcode.min.js';

/** I quattro livelli di «Domande e richieste», dal piu' chiuso: cosa accendono. */
export const ASK_LEVELS = [
  { id: 'none', can_ask: false, act: 'off' },
  { id: 'ask', can_ask: true, act: 'off' },
  { id: 'confirm', can_ask: true, act: 'confirm' },
  { id: 'free', can_ask: true, act: 'free' },
];

/** Il livello di una collegata, dai suoi due permessi. */
export function askLevel(peer) {
  if (peer?.act === 'free') return 'free';
  if (peer?.act === 'confirm') return 'confirm';
  return peer?.can_ask ? 'ask' : 'none';
}

/** Vero se l'ultimo tentativo di raggiungerla e' fallito dopo l'ultima volta vista. */
export function isUnreachable(peer) {
  return Boolean(peer?.last_error && peer.last_try_ms > peer.last_seen_ms);
}

/** Quanto tempo fa, a parole: «2 minuti fa», «ieri», e oltre la settimana il
 *  giorno. Una data secca («25/11, 17:32») obbligava a fare il conto. */
export function agoText(ms, now = Date.now()) {
  const seconds = Math.max(0, Math.round((now - ms) / 1000));
  if (seconds < 90) return t('ago.now');
  const rel = new Intl.RelativeTimeFormat(i18n.locale, { numeric: 'auto' });
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return rel.format(-minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (hours < 24) return rel.format(-hours, 'hour');
  const days = Math.round(hours / 24);
  if (days < 7) return rel.format(-days, 'day');
  const date = new Date(ms).toLocaleDateString(i18n.locale, { day: '2-digit', month: '2-digit' });
  return t('ago.date', { date });
}

/** Lo stato di una collegata in una riga. Una che non risponde dice anche
 *  quando si è vista l'ultima volta: è quel che serve per capire da quanto. */
export function stateText(peer, now = Date.now()) {
  if (!peer.has_secret) return t('state.repair');
  const seen = peer.last_seen_ms
    ? t(isComputer(peer) ? 'state.seenComputer' : 'state.seen',
      { time: agoText(peer.last_seen_ms, now) })
    : '';
  if (isUnreachable(peer)) {
    return seen ? `${t('state.unreachable')} · ${seen}` : t('state.unreachable');
  }
  return seen || t('state.never');
}

/** Vero se Tailscale è su in questo telefono: senza, nessuna collegata risponde. */
export function netUp(status) {
  return status?.tailscale?.state === 'up';
}

/* Le righe della cronologia: `direzione:tipo` → la frase (le stesse
   dell'officina, `settings.peers.log.*`) e i suoi parametri. Un tipo che qui
   non c'è non si mostra: sono i pezzi di un file, gli `ack`. */
const LOG_LINES = {
  'out:ask': (d) => ['askOut', { text: d.text || '' }],
  'in:ask': (d) => ['askIn', { text: d.text || '' }],
  'out:answer': () => ['answerOut', {}],
  'in:answer': () => ['answerIn', {}],
  'in:file': (d) => ['file', { name: d.name || '' }],
  'out:ping': () => ['ping', {}],
  'in:ping': () => ['ping', {}],
  'sys:paired': () => ['paired', {}],
  'sys:refused': (d) => ['refused', { kind: d.kind || '' }],
  'sys:removed': () => ['removed', {}],
  'sys:removed_by_other': () => ['removedByOther', {}],
  'sys:confirm': (d) => [d.allowed ? 'confirmYes' : 'confirmNo', { target: d.target || '' }],
  'sys:undo': (d) => ['undo', { files: d.restored || 0, jobs: d.jobs || 0 }],
  'out:undo': () => ['undoAsked', {}],
  'out:line_open': () => ['lineOut', {}],
  'in:line_open': () => ['lineIn', {}],
  'out:line_close': () => ['lineOutClosed', {}],
  'in:line_close': () => ['lineInClosed', {}],
  'in:line_trace': () => ['lineTrace', {}],
  'in:undone': () => ['undone', {}],
};

/** La frase di una riga della cronologia, come `[chiave, parametri]`, o `null`. */
export function historyLine(row) {
  const make = LOG_LINES[`${row?.direction}:${row?.kind}`];
  return make ? make(row.data || {}) : null;
}

/** Vero se una richiesta di fare si può ancora annullare da qui. */
export function canUndoAct(act) {
  const touched = (act?.done || []).filter((d) => d && d.undoable).length;
  return !act?.working && act?.state === 'answered' && touched > 0;
}

/** La riga delle Impostazioni: i nomi, o che non ce n'e' nessuna. */
export function peersSummary(status) {
  const peers = status?.enabled ? (status.peers || []) : [];
  if (!peers.length) return t('none');
  return peers.map((p) => p.name).join(', ');
}

/** La Jenny comparsa fra *before* e *after*, o `null`. */
export function freshPeer(before, after) {
  const known = new Set((before?.peers || []).map((p) => p.id));
  return (after?.peers || []).find((p) => !known.has(p.id)) || null;
}

let qrLoading = null;
function loadQr() {
  if (window.qrcode) return Promise.resolve(true);
  if (!qrLoading) {
    qrLoading = new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = QR_SRC;
      s.onload = () => resolve(true);
      s.onerror = () => { qrLoading = null; resolve(false); };
      document.head.appendChild(s);
    });
  }
  return qrLoading;
}

/* Le viste della pagina di una collegata: in testa c'è il suo nome. */
const PEER_VIEWS = ['peer', 'history', 'rename'];
/* Dove porta Indietro da ogni vista; dall'elenco si esce dalla stanza. */
const BACK = {
  peer: 'list', history: 'peer', rename: 'peer', add: 'list', name: 'list', qr: 'add', join: 'add',
};
/* Mentre scrivi un nome o un codice non si ridisegna niente sotto le dita. */
const TYPING_VIEWS = ['name', 'join', 'rename'];

export class HomePeers {
  /**
   * @param body     il contenitore che scorre della stanza.
   * @param sheet    un `<dialog>` della forma dei fogli del piano, per i livelli.
   * @param onTalk   `(peer)`: apri la linea con lei, nella chat.
   * @param onStatus `(status)`: lo stato e' cambiato (la riga delle Impostazioni).
   * @param onHead   `(name | null)`: in testa va il nome di questa collegata
   *                 (la sua pagina), o il titolo della stanza.
   */
  constructor({ body, sheet, onTalk, onStatus, onHead } = {}) {
    this.body = body;
    this.sheet = sheet;
    this.onTalk = onTalk || (() => {});
    this.onStatus = onStatus || (() => {});
    this.onHead = onHead || (() => {});
    this.status = null;
    this.view = 'list';
    this._peerId = null;
    this._log = null;
    this._pair = null;
    this._named = null;
    this._timer = null;
    this._open = false;
    this._busy = false;
    this._drawn = '';
    this._html = '';
  }

  /* ── Ciclo di vita ───────────────────────────────────────────────────── */

  /** *link*: il link d'abbinamento di un QR (`shared/peer-link.js`), che apre
   *  il modulo già compilato. Collegare resta il tocco su «Collega». */
  async open({ add = false, link = null } = {}) {
    this._open = true;
    this._link = link;
    this._peerId = null;
    this._log = null;
    this.view = link ? 'join' : (add ? 'add' : 'list');
    this.render();
    await this.refresh();
    this._schedule();
  }

  close() {
    this._open = false;
    this._link = null;
    clearTimeout(this._timer);
    /* Un codice lasciato aperto si chiude con la stanza. */
    if (this._pair) {
      this._pair = null;
      rpc.peer('pair.cancel').catch(() => {});
    }
  }

  /** Vero se Indietro ha fatto un passo dentro la stanza: dalla pagina di una
   *  collegata all'elenco, dal collegamento al passo prima. */
  back() {
    const to = BACK[this.view];
    if (!to) return false;
    if (this.view === 'qr') this._cancelPair();
    this._go(to);
    return true;
  }

  /** Torna all'elenco, da dovunque nella stanza: la radice del percorso in
   *  testa («Altre Jenny › Lavoro») porta qui. */
  toList() {
    if (this.view === 'qr') this._cancelPair();
    this._go('list');
  }

  /** Dove porta Indietro, a parole, per chi non vede la freccia; `null`
   *  dall'elenco, da cui si esce dalla stanza. */
  backLabel() {
    const to = BACK[this.view];
    if (!to) return null;
    if (to === 'peer') return t('back.peer', { name: this._current()?.name || '' });
    return t(to === 'list' ? 'back.list' : 'back.add');
  }

  /** Il nome da mettere in testa: la collegata di cui si guarda la pagina. */
  headName() {
    return PEER_VIEWS.includes(this.view) ? (this._current()?.name || null) : null;
  }

  _go(view) {
    this.view = view;
    if (!PEER_VIEWS.includes(view)) {
      this._peerId = null;
      this._log = null;
    }
    if (view !== 'rename') this._draftName = undefined;
    this.render();
  }

  _current() {
    return (this.status?.peers || []).find((p) => p.id === this._peerId) || null;
  }

  async refresh() {
    try {
      this._show(await rpc.peer('status'));
    } catch (e) {
      if (!this.status) {
        this._html = '';
        this.body.replaceChildren(el('div', 'home-card-note', t('unavailable')));
      }
    }
  }

  /** Lo stato, senza disegnare: per la riga delle Impostazioni all'avvio.
   *
   *  A stanza aperta lo stato è suo: col QR a schermo è dal confronto fra
   *  prima e dopo che si riconosce la Jenny appena collegata. Rileggerlo qui
   *  (l'avviso `peers` del gateway arriva proprio a collegamento fatto) faceva
   *  credere il QR scaduto, e il passo del nome non arrivava. */
  async peek() {
    if (this._open) {
      if (!this._busy && !TYPING_VIEWS.includes(this.view)) await this.refresh();
      return this.status;
    }
    try {
      this.status = await rpc.peer('status');
      this.onStatus(this.status);
    } catch {
      /* Collegamenti non disponibili: la riga dice che non ce n'e' nessuna. */
      if (!this.status) this.onStatus(null);
    }
    return this.status;
  }

  _schedule() {
    clearTimeout(this._timer);
    if (!this._open) return;
    this._timer = setTimeout(async () => {
      if (!this._open) return;
      if (!this._busy && !TYPING_VIEWS.includes(this.view)) await this.refresh();
      this._schedule();
    }, POLL_MS);
  }

  _show(status) {
    const before = this.status;
    this.status = status;
    if (status.pair) this._pair = status.pair;
    if (!status.pairing && this.view !== 'qr') this._pair = null;
    this.onStatus(status);
    /* Il QR appena usato: la Jenny nuova e' quella da chiamare. */
    if (this.view === 'qr') {
      const fresh = freshPeer(before, status);
      if (fresh) {
        this._pair = null;
        this._named = fresh;
        this.view = 'name';
      } else if (!status.pairing) {
        this._pair = null;
        this.view = 'add';
        showToast(t('qrExpired'));
      }
    }
    /* La collegata di cui si guardava la pagina non c'è più (l'ha scollegata
       l'altra, o il collegamento è stato spento): si torna all'elenco. */
    if (PEER_VIEWS.includes(this.view) && !(status.enabled && this._current())) {
      this.view = 'list';
      this._peerId = null;
      this._log = null;
    }
    this.render();
  }

  async _run(call, onError) {
    if (this._busy) return null;
    this._busy = true;
    try {
      const status = await call();
      this._show(status);
      return status;
    } catch (e) {
      onError?.();
      showToast(i18n.tOr(`home.peers.errors.${e?.message}`, t('errors.generic')), 'error');
      return null;
    } finally {
      this._busy = false;
    }
  }

  /* ── Disegno ─────────────────────────────────────────────────────────── */

  render() {
    if (!this.body) return;
    const view = {
      list: () => this._list(),
      peer: () => this._peer(),
      history: () => this._history(),
      rename: () => this._rename(),
      add: () => this._add(),
      qr: () => this._qr(),
      join: () => this._join(),
      name: () => this._name(),
    }[this.view] || (() => this._list());
    const nodes = view().filter(Boolean);
    /* Il giro di controllo ridisegna ogni quattro secondi, e quasi sempre
       uguale. Cambiare i nodi sotto un dito che sta toccando una riga perde il
       tocco (il `click` vuole lo stesso nodo all'andata e al ritorno): se il
       disegno è lo stesso, resta quello che c'è. */
    const html = nodes.map((node) => node.outerHTML).join('');
    const drawn = `${this.view}:${this._peerId || ''}`;
    if (html !== this._html || drawn !== this._drawn) {
      this.body.replaceChildren(...nodes);
      /* Una vista nuova parte dall'alto. */
      if (drawn !== this._drawn) this.body.scrollTop = 0;
      this._html = html;
      this._drawn = drawn;
    }
    this.onHead(this.headName());
  }

  _switch(on, label, onToggle) {
    const sw = button(`home-switch is-peer${on ? ' is-on' : ''}`, null, () => onToggle(!on));
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', on ? 'true' : 'false');
    sw.setAttribute('aria-label', label);
    sw.appendChild(el('span', 'home-switch-knob'));
    return sw;
  }

  _permRow(label, sub, control) {
    const row = el('div', 'home-row home-peers-row');
    const text = el('span', 'home-row-label');
    text.appendChild(document.createTextNode(label));
    if (sub) text.appendChild(el('span', 'home-peers-sub', sub));
    row.append(text, control);
    return row;
  }

  /* ── I pezzi del menu ────────────────────────────────────────────────── */

  _sec(text) {
    return el('div', 'home-peers-sec', text);
  }

  _menu(rows, cls = '') {
    const menu = el('div', `home-rows home-peers-menu${cls ? ` ${cls}` : ''}`);
    menu.append(...rows.filter(Boolean));
    return menu;
  }

  /** La tessera con l'icona di una riga: del colore delle altre Jenny, grigia
   *  (`plain`) per quel che non è un permesso, rossa (`danger`), piena (`add`). */
  _tile(glyph, kind = '') {
    const tile = el('span', `home-peers-tile${kind ? ` is-${kind}` : ''}`);
    tile.appendChild(icon(glyph));
    return tile;
  }

  /** Una riga di menu: la tessera, l'etichetta con la riga piccola sotto, e a
   *  destra un valore, la freccia o un interruttore.
   *
   *  Con `run` è un bottone. Con `toggle` è la riga di un interruttore: lo
   *  muove anche il tocco sul testo, perché su un telefono si mira alla riga.
   *  Senza nessuno dei due è una riga ferma, che dice e basta. */
  _row({
    glyph, kind = '', label, sub = '', value = null, go = true, run = null,
    on = false, toggle = null, cls = '',
  }) {
    const classes = `home-row${sub ? '' : ' is-slim'}${cls ? ` ${cls}` : ''}`;
    const row = run ? button(classes, null, run) : el('div', classes);
    const text = el('span', 'home-row-label');
    text.appendChild(document.createTextNode(label));
    if (sub) text.appendChild(el('span', 'home-peers-sub', sub));
    row.append(this._tile(glyph, kind), text);
    if (value) {
      const right = el('span', 'home-row-value');
      right.append(value);
      row.appendChild(right);
    }
    if (toggle) {
      row.appendChild(this._switch(on, label, toggle));
      row.addEventListener('click', (e) => {
        if (!e.target.closest('.home-switch')) toggle(!on);
      });
    } else if (run && go) {
      row.appendChild(icon('ti-chevron-right', 'home-row-go'));
    }
    return row;
  }

  _badge(p) {
    const badge = el('span', `home-peers-badge${isComputer(p) ? ' is-computer' : ''}`);
    badge.appendChild(icon(peerGlyph(p)));
    return badge;
  }

  /** Il pallino e lo stato di una collegata; di un computer dice che lo è. */
  _state(p) {
    const state = el('span', 'home-peers-state');
    const text = stateText(p);
    state.append(el('i', `home-line-dot${isUnreachable(p) || !p.has_secret ? ' is-off' : ''}`),
      document.createTextNode(isComputer(p) ? `${t('computer')} · ${text}` : text));
    return state;
  }

  /** Lo stato di Tailscale su questo telefono, come valore di una riga. */
  _net() {
    const up = netUp(this.status);
    const value = el('span', 'home-peers-state');
    value.append(el('i', `home-line-dot${up ? '' : ' is-off'}`),
      document.createTextNode(t(up ? 'net.on' : 'net.off')));
    return this._row({
      glyph: 'ti-network', kind: up ? '' : 'plain', label: t('net.label'),
      sub: up ? '' : t('net.offSub'), value,
    });
  }

  /** A cosa serve un'altra Jenny: le tre frasi che si dicono in chat. Il nome
   *  nell'esempio è quello di una collegata, se ce n'è una. */
  _uses() {
    const name = (this.status?.peers || []).find((p) => !isComputer(p))?.name || t('defaultExample');
    return [
      ['ti-message-question', t('uses.ask'), t('uses.askSay', { name })],
      ['ti-hand-click', t('uses.act'), t('uses.actSay', { name })],
      ['ti-arrows-exchange', t('uses.line'), t('uses.lineSay', { name })],
    ];
  }

  _useRow([glyph, label, say]) {
    const row = this._row({ glyph, label, sub: say, cls: 'home-peers-use' });
    return row;
  }

  /* ── L'elenco ────────────────────────────────────────────────────────── */

  _list() {
    const s = this.status;
    if (!s) return [el('div', 'home-card-note', i18n.t('settings.loading'))];
    const peers = s.enabled ? (s.peers || []) : [];
    if (!peers.length) return this._empty();
    return [
      el('div', 'home-peers-intro', t('intro')),
      this._sec(t('sec.linked')),
      this._menu(peers.map((p) => this._peerRow(p))),
      this._sec(t('sec.add')),
      this._menu([this._row({
        glyph: 'ti-plus', kind: 'add', label: t('add'), sub: t('addSub'),
        run: () => this._go('add'), cls: 'home-peers-addrow',
      })]),
      this._sec(t('sec.phone')),
      this._menu([
        this._net(),
        this._row({
          glyph: 'ti-help', kind: 'plain', label: t('uses.title'), run: () => this._help(),
        }),
      ]),
    ];
  }

  _peerRow(p) {
    const row = button('home-row home-peers-peer', null, () => {
      this._peerId = p.id;
      this._go('peer');
    });
    row.dataset.peer = p.id;
    const who = el('span', 'home-row-label home-peers-who');
    who.append(el('span', 'home-peers-name', p.name), this._state(p));
    row.append(this._badge(p), who, icon('ti-chevron-right', 'home-row-go'));
    return row;
  }

  _empty() {
    const empty = el('div', 'home-peers-empty');
    const art = el('span', 'home-peers-empty-art');
    art.appendChild(icon('ti-arrows-exchange'));
    empty.append(art, el('b', null, t('emptyTitle')), el('span', null, t('emptyBody')));
    const add = button('home-peers-big home-peers-talkbig', null, () => this._go('add'));
    add.append(icon('ti-plus'), document.createTextNode(t('add')));
    return [
      empty,
      add,
      this._sec(t('uses.short')),
      this._menu(this._uses().map((use) => this._useRow(use))),
      this._sec(t('sec.phone')),
      this._menu([this._net()]),
      el('div', 'home-peers-foot', t('net.note')),
    ];
  }

  _help() {
    if (!this.sheet) return;
    const rows = this._uses().map(([glyph, label, say]) => {
      const row = el('div', 'home-plan-sheet-row home-peers-use');
      const text = el('span', 'home-plan-row-text');
      text.append(el('span', null, label), el('span', 'home-plan-row-sub', say));
      row.append(icon(glyph, 'is-peer'), text);
      return row;
    });
    openActionSheet(this.sheet, {
      children: [...sheetHead(t('uses.title'), t('uses.note')), ...rows],
      closeLabel: i18n.t('common.close'),
      cls: 'home-line-sheet',
    });
    this.sheet.showModal?.();
  }

  /* ── La pagina di una collegata ──────────────────────────────────────── */

  _peer() {
    const p = this._current();
    if (!p) return this._list();
    const computer = isComputer(p);
    const down = isUnreachable(p);
    const hero = el('div', 'home-peers-hero');
    const who = el('div', 'home-peers-who');
    who.append(el('div', 'home-peers-hero-name', p.name), this._state(p));
    hero.append(this._badge(p), who);
    /* «Parla» resta anche se non risponde: quel che scrivi parte appena torna,
       e la chat lo dice. Senza la chiave del collegamento non c'è niente da
       aprire. */
    const talk = button(`home-peers-big home-peers-talkbig${down || !p.has_secret ? ' is-ghost' : ''}`,
      null, () => this.onTalk(p));
    talk.append(icon('ti-message-2'), document.createTextNode(t('talkTo', { name: p.name })));
    talk.disabled = !p.has_secret;
    const link = [
      this._row({
        glyph: 'ti-history', kind: 'plain', label: t('history'),
        value: p.queued ? t('queued', { count: p.queued }) : null,
        run: () => this._openHistory(p),
      }),
      p.has_secret && this._row({
        glyph: 'ti-antenna-bars-5', kind: 'plain', label: t('ping'), go: false,
        run: () => this._ping(p),
      }),
      this._row({
        glyph: 'ti-pencil', kind: 'plain', label: t('rename'), run: () => this._go('rename'),
      }),
    ];
    return [
      hero,
      this._trouble(p),
      talk,
      computer ? el('div', 'home-peers-foot', t('computerNote')) : null,
      computer ? null : this._sec(t('perm.title')),
      computer ? null : this._menu([
        this._row({
          glyph: 'ti-arrows-exchange', label: t('perm.line'), sub: t('lineSub', { name: p.name }),
          on: Boolean(p.line),
          toggle: (on) => this._run(() => rpc.peer('permissions', { id: p.id, line: on })),
        }),
        this._row({
          glyph: 'ti-message-question', label: t('ask'), value: t(`level.${askLevel(p)}`),
          run: () => this._levels(p),
        }),
        this._row({
          glyph: 'ti-paperclip', label: t('perm.files'), sub: t('perm.filesSub'),
          on: Boolean(p.send_files),
          toggle: (on) => this._run(() => rpc.peer('permissions', { id: p.id, send_files: on })),
        }),
      ]),
      this._sec(t('sec.link')),
      this._menu(link),
      this._menu([this._row({
        glyph: 'ti-unlink', kind: 'danger', label: t('unlink', { name: p.name }), go: false,
        run: () => this._unlink(p),
      })], 'is-danger'),
    ];
  }

  /** Quel che non va con questa collegata, in cima alla sua pagina, con la
   *  cosa da fare: senza chiave si ricollega, se non risponde si riprova. */
  _trouble(p) {
    if (!p.has_secret) {
      return this._warn(t('repair.title'), t('repair.body'), t('repair.go'), () => this._go('add'));
    }
    if (!isUnreachable(p)) return null;
    let body = t(isComputer(p) ? 'down.bodyComputer' : 'down.body');
    if (!netUp(this.status)) body = t('down.bodyHere');
    if (p.queued) body += ` ${t('down.queued', { count: p.queued })}`;
    return this._warn(t('down.title'), body, t('down.retry'), () => this._ping(p));
  }

  _warn(title, body, action, run) {
    const card = el('div', 'home-peers-warn');
    const text = el('div', 'home-peers-warn-text');
    text.append(el('b', null, title), el('span', null, body), button('home-key-btn', action, run));
    card.append(icon('ti-plug-connected-x'), text);
    return card;
  }

  async _ping(p) {
    const done = await this._run(() => rpc.peer('ping', { id: p.id }));
    if (done) showToast(t('pingSent', { name: p.name }));
  }

  _levels(p) {
    if (!this.sheet) return;
    const now = askLevel(p);
    const rows = ASK_LEVELS.map((lv) => {
      const row = sheetRow(lv.id === now ? 'ti-check' : 'ti-point', t(`level.${lv.id}`),
        t(`levelSub.${lv.id}`), lv.id === now ? 'is-primary' : '', async () => {
          closeActionSheet(this.sheet);
          await this._run(() => rpc.peer('permissions', {
            id: p.id, can_ask: lv.can_ask, act: lv.act,
          }));
        });
      return row;
    });
    openActionSheet(this.sheet, {
      children: [...sheetHead(t('askTitle', { name: p.name }), t('askNote', { name: p.name })), ...rows],
      closeLabel: i18n.t('common.close'),
      cls: 'home-line-sheet',
    });
    this.sheet.showModal?.();
  }

  /** Scollegare non si annulla da qui (serve un codice nuovo): si chiede. */
  _unlink(p) {
    if (!this.sheet) return;
    const go = sheetRow('ti-unlink', t('unlink', { name: p.name }), '', 'is-danger', async () => {
      closeActionSheet(this.sheet);
      const done = await this._run(() => rpc.peer('remove', { id: p.id }));
      if (done) showToast(t('unlinked', { name: p.name }));
    });
    openActionSheet(this.sheet, {
      children: [...sheetHead(t('unlinkTitle', { name: p.name }), t('unlinkBody')), go],
      closeLabel: i18n.t('common.cancel'),
      cls: 'home-line-sheet',
    });
    this.sheet.showModal?.();
  }

  /* ── La cronologia ───────────────────────────────────────────────────── */

  async _openHistory(p) {
    this._log = { id: p.id, rows: null, acts: [] };
    this._go('history');
    await this._loadLog(p.id);
  }

  async _loadLog(id) {
    let rows = [];
    let acts = [];
    try {
      rows = (await rpc.peer('history', { id })).history || [];
      /* Le richieste di fare hanno il loro registro, che può non esserci. */
      acts = (await rpc.peer('acts', { id }).catch(() => null))?.acts || [];
    } catch (e) {
      showToast(i18n.tOr(`home.peers.errors.${e?.message}`, t('errors.generic')), 'error');
    }
    if (this._log?.id !== id) return;
    this._log = { id, rows, acts };
    if (this.view === 'history') this.render();
  }

  _when(ms) {
    return new Date(ms).toLocaleString(i18n.locale, {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  _history() {
    const p = this._current();
    const log = this._log;
    if (!p || !log) return this._list();
    if (log.rows === null) return [el('div', 'home-card-note', i18n.t('settings.loading'))];
    const lines = log.rows.map((row) => {
      const line = historyLine(row);
      if (!line) return null;
      const node = el('div', 'home-row home-peers-log');
      node.append(el('span', 'home-row-label', tw(`log.${line[0]}`, line[1])),
        el('span', 'home-row-value', this._when(row.at_ms)));
      return node;
    }).filter(Boolean);
    return [
      log.acts.length ? this._sec(tw('actsTitle')) : null,
      log.acts.length ? this._menu(log.acts.map((act) => this._actRow(p, act))) : null,
      this._sec(t('history')),
      lines.length ? this._menu(lines) : el('div', 'home-peers-foot', tw('historyEmpty')),
    ];
  }

  /** Una richiesta di fare arrivata da lei: cosa ha chiesto, com'è finita, e
   *  «Annulla» finché quel che ha toccato si può rimettere com'era. */
  _actRow(p, act) {
    const touched = (act.done || []).filter((d) => d && d.undoable).length;
    const state = act.working ? 'working' : act.state;
    const node = el('div', 'home-row home-peers-log');
    const text = el('span', 'home-row-label');
    text.append(document.createTextNode(`«${act.text || ''}»`),
      el('span', 'home-peers-sub',
        `${tw(`actState.${state}`, { count: touched })} · ${this._when(act.at_ms)}`));
    node.appendChild(text);
    if (canUndoAct(act)) {
      node.appendChild(button('home-key-btn', tw('undo'), () => this._undoAct(p, act)));
    }
    return node;
  }

  async _undoAct(p, act) {
    try {
      const u = (await rpc.peer('undo', { id: p.id, qid: act.qid })).undo || {};
      showToast(tw('undone', { files: u.restored || 0, jobs: u.jobs || 0 }));
    } catch (e) {
      showToast(i18n.tOr(`home.peers.errors.${e?.message}`, t('errors.generic')), 'error');
    }
    await this._loadLog(p.id);
  }

  /* ── Il nome ─────────────────────────────────────────────────────────── */

  _rename() {
    const p = this._current();
    if (!p) return this._list();
    const card = el('div', 'home-card home-peers-form');
    const field = this._field('home-peers-rename', t('nameAsk'), {
      value: this._draftName ?? p.name, hint: t('nameNote'),
    });
    const input = field.querySelector('input');
    input.maxLength = 40;
    input.addEventListener('input', () => { this._draftName = input.value; });
    card.appendChild(field);
    const save = button('home-peers-big', i18n.t('common.save'), async () => {
      const name = input.value.trim();
      if (!name) {
        showToast(t('fieldsRequired'), 'error');
        return;
      }
      if (name !== p.name && !(await this._run(() => rpc.peer('rename', { id: p.id, name })))) {
        return;
      }
      this._go('peer');
    });
    return [card, save];
  }

  _steps(n) {
    const steps = el('div', 'home-peers-steps');
    for (let i = 1; i <= 3; i += 1) steps.appendChild(el('i', i <= n ? 'is-on' : ''));
    return steps;
  }

  _add() {
    const s = this.status;
    const up = s?.tailscale?.state === 'up';
    const lead = el('div', 'home-peers-lead', t('addLead'));
    const card = el('div', 'home-card home-peers-choices');
    const choice = (glyph, title, sub, run) => {
      const b = button('home-peers-choice', null, run);
      b.appendChild(icon(glyph));
      const text = el('span', 'home-peers-choice-text');
      text.append(el('b', null, title), el('span', 'home-peers-sub', sub));
      b.append(text, icon('ti-chevron-right', 'home-row-go'));
      return b;
    };
    /* Il lettore interno c'è solo nell'app Android: nel browser la scelta
       non compare. */
    const scan = globalThis.window?.JennyNative?.scanPairQr;
    card.append(
      choice('ti-qrcode', t('showQr'), t('showQrSub'), () => this._startQr()),
      ...(typeof scan === 'function'
        ? [choice('ti-scan', t('scanQr'), t('scanQrSub'), () => scan())]
        : []),
      choice('ti-keyboard', t('haveCode'), t('haveCodeSub'), () => {
        this._link = null;
        this._go('join');
      }),
    );
    const net = el('div', 'home-peers-state home-peers-net');
    net.append(el('i', `home-line-dot${up ? '' : ' is-off'}`),
      document.createTextNode(up ? t('tailscaleUp') : t('tailscaleDown')));
    return [this._steps(1), lead, card, net];
  }

  async _startQr() {
    await loadQr();
    const status = await this._run(async () => {
      const cfg = this.status;
      /* Chi tocca «Mostra il QR» vuole il collegamento: se era spento, si accende. */
      if (cfg && !cfg.enabled) await rpc.peer('set', { enabled: true });
      return rpc.peer('pair.start');
    });
    if (status?.pair) {
      this._pair = status.pair;
      this.view = 'qr';
      this.render();
    }
  }

  _cancelPair() {
    this._pair = null;
    rpc.peer('pair.cancel').catch(() => {});
  }

  _qr() {
    const pair = this._pair;
    if (!pair) return this._add();
    const card = el('div', 'home-card home-peers-qr');
    card.appendChild(el('div', 'home-peers-qr-title', t('scan')));
    if (window.qrcode && pair.link) {
      const qr = window.qrcode(0, 'M');
      qr.addData(pair.link);
      qr.make();
      const box = el('div', 'home-peers-qr-box');
      /* L'SVG lo genera la libreria dal link che il gateway ha dato: niente
         testo di altri dentro. */
      box.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
      card.appendChild(box);
    }
    card.appendChild(el('div', 'home-peers-code', pair.code || ''));
    const minutes = Math.max(0, Math.round(((pair.expires_ms || 0) - Date.now()) / 60000));
    const note = el('div', 'home-card-note home-peers-center');
    note.appendChild(document.createTextNode(t('expires', { minutes })));
    const address = pair.host || pair.ip || '';
    if (address) {
      note.appendChild(el('br'));
      note.appendChild(document.createTextNode(t('byHand', { address })));
    }
    card.appendChild(note);
    const cancel = button('home-key-btn home-peers-cancel', i18n.t('common.cancel'), () => {
      this._cancelPair();
      this.view = 'add';
      this.render();
    });
    return [this._steps(2), card, cancel];
  }

  _field(id, label, { value = '', placeholder = '', hint = '' } = {}) {
    const wrap = el('label', 'home-peers-field');
    wrap.appendChild(el('span', 'home-peers-field-label', label));
    const input = el('input', 'home-rules home-peers-input');
    input.type = 'text';
    input.id = id;
    input.value = value;
    input.placeholder = placeholder;
    input.autocomplete = 'off';
    input.autocapitalize = 'none';
    input.spellcheck = false;
    wrap.appendChild(input);
    if (hint) wrap.appendChild(el('span', 'home-peers-sub', hint));
    return wrap;
  }

  _join() {
    /* Dal QR (`open({ link })`): indirizzo e codice già scritti. Fino al 23/11
       il QR portava sempre in officina, che non ha il terzo passo: la linea
       restava spenta anche per chi l'avrebbe accesa. */
    const link = this._link || null;
    const shown = link ? (link.host || link.ip) : '';
    const card = el('div', 'home-card home-peers-form');
    card.append(
      this._field('home-peers-host', t('address'), {
        value: shown, placeholder: t('addressPlaceholder'), hint: t('addressHint'),
      }),
      this._field('home-peers-code', t('code'), {
        value: link ? link.code : '', placeholder: 'K7Q4-MZP2',
      }),
    );
    const go = button('home-peers-big', t('link'), async () => {
      const address = card.querySelector('#home-peers-host').value.trim();
      const code = card.querySelector('#home-peers-code').value.trim();
      if (!address || !code) {
        showToast(t('fieldsRequired'), 'error');
        return;
      }
      const isIp = /^[0-9.]+$/.test(address) || address.includes(':');
      const before = this.status;
      const status = await this._run(async () => {
        if (before && !before.enabled) await rpc.peer('set', { enabled: true });
        /* L'indirizzo del QR com'era: col nome tiene anche l'IP e la porta. */
        const same = link && address === shown;
        return rpc.peer('pair.join', same ? {
          host: link.host, ip: link.ip, ...(link.port ? { port: link.port } : {}),
          code, name: t('defaultName'),
        } : {
          host: isIp ? '' : address, ip: isIp ? address : '', code, name: t('defaultName'),
        });
      });
      const fresh = status && freshPeer(before, status);
      if (fresh) {
        this._link = null;
        this._named = fresh;
        this.view = 'name';
        this.render();
      }
    });
    return [this._steps(2), card, go];
  }

  _name() {
    const peer = (this.status?.peers || []).find((p) => p.id === this._named?.id) || this._named;
    if (!peer) return this._list();
    const card = el('div', 'home-card home-peers-form');
    const top = el('div', 'home-peers-top is-flat');
    const badge = el('span', 'home-peers-badge');
    badge.appendChild(icon('ti-check'));
    const who = el('span', 'home-peers-who');
    who.append(el('span', 'home-peers-name', t('linked')),
      el('span', 'home-peers-state', t('nameAsk')));
    top.append(badge, who);
    const input = el('input', 'home-rules home-peers-input');
    input.type = 'text';
    input.maxLength = 40;
    input.value = this._draftName ?? peer.name;
    input.setAttribute('aria-label', t('nameAsk'));
    input.addEventListener('input', () => { this._draftName = input.value; });
    card.append(top, input, el('div', 'home-card-note home-peers-flatnote', t('nameNote')));
    const perms = { line: this._draftLine ?? true, can_ask: this._draftAsk ?? true };
    const rows = el('div', 'home-rows');
    const computer = isComputer(peer);
    if (!computer) {
      rows.append(
        this._permRow(t('line'), t('lineSub', { name: input.value || peer.name }),
          this._switch(perms.line, t('line'), (on) => { this._draftLine = on; this.render(); })),
        this._permRow(t('canAsk'), t('canAskSub'),
          this._switch(perms.can_ask, t('canAsk'), (on) => { this._draftAsk = on; this.render(); })),
      );
    }
    const done = button('home-peers-big', t('done'), async () => {
      const name = input.value.trim();
      if (!name) {
        showToast(t('fieldsRequired'), 'error');
        return;
      }
      const ok = await this._run(async () => {
        if (name !== peer.name) await rpc.peer('rename', { id: peer.id, name });
        if (computer) return rpc.peer('status');
        return rpc.peer('permissions', { id: peer.id, line: perms.line, can_ask: perms.can_ask });
      });
      if (!ok) return;
      this._named = null;
      this._draftName = this._draftLine = this._draftAsk = undefined;
      this._go('list');
    });
    const tail = el('div', 'home-card-note home-peers-center', t('doneNote', { name: peer.name }));
    return [this._steps(3), card, computer ? null : rows, done, tail];
  }
}
