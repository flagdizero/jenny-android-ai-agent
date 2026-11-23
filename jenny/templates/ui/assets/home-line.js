/** La casa — la linea passata a un'altra Jenny (`jenny/peers/line.py`).
 *
 *  «Passami Lavoro»: da quel momento quel che scrivi nella chat di sempre va
 *  all'altra tua Jenny, e questa non lo legge. Qui c'e' tutto quel che si vede:
 *
 *  - **il tasto ⇄ nel composer**, accanto al tasto Piano. C'e' solo quando
 *    un'altra Jenny e' collegata; a linea aperta porta il suo nome;
 *  - **il foglio «Con chi parli»**, a due livelli: in alto le Jenny (questa, le
 *    altre, il + per collegarne una), sotto le chat di quella accesa — la chat
 *    di sempre e i quaderni. Per un collegato che non e' una Jenny (opencode
 *    sul Mac) la riga e' una sola: non ha quaderni;
 *  - **la testa** al posto della fila delle pagine: «‹ LAVORO», o
 *    «‹ LAVORO › ● FORNITORI». La freccia torna a casa;
 *  - **il filo della linea**, in un contenitore suo che prende il posto di
 *    quello della chat finche' la linea e' aperta. Il testo non sta nel
 *    transcript di questo telefono: arriva dal gateway, che lo tiene in
 *    memoria, e alla chiusura qui resta il segnaposto (`shared/peer-card.js`).
 *
 *  Quel che scrivi parte con `peer.line.say` e non con un messaggio della chat:
 *  cosi' la bolla la disegna il frame di ritorno (lo stesso per ogni finestra),
 *  e Jenny non si mette a pensare a un turno che non c'e'.
 *
 *  La linea e' della chat di sempre: nella chat di un quaderno il tasto c'e',
 *  ma la testa, il colore e il filo no.
 */

import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { button, el, icon } from './shared/dom.js';
import { closeActionSheet, openActionSheet, sheetHead } from './shared/sheet.js';
import { chatTimeNode } from './shared/chat-time.js';
import { dotColor } from './shared/spines.js';
import { renderMarkdown } from './shared/markdown.js';
import { showToast } from './shared/utils.js';

const t = (key, params) => i18n.t(`home.line.${key}`, params);

/** Ogni quanto si rilegge lo stato a linea aperta (in coda, irraggiungibile). */
const POLL_MS = 5000;
/** Quanto resta «sta scrivendo» senza una risposta. */
const WAIT_MS = 120_000;
/** Il tuo telefono nella fila del foglio. */
const HERE = '';

/** Vero per un collegato che e' opencode sul Mac, non un'altra Jenny. */
export function isComputer(peer) {
  return peer?.kind === 'opencode';
}

/** L'icona di un collegato: il portatile per un computer, lo scambio per una Jenny. */
export function peerGlyph(peer) {
  return isComputer(peer) ? 'ti-device-laptop' : 'ti-arrows-exchange';
}

/** Le voci del percorso in testa: il nome, e il quaderno se la linea e' li'. */
export function headParts(line) {
  if (!line) return [];
  return line.place ? [line.name, line.place] : [line.name];
}

/** Se l'altra non risponde e c'e' qualcosa che aspetta di partire. */
export function isStuck(line) {
  return Boolean(line && line.unreachable && Number(line.queued) > 0);
}

/** Le Jenny a cui si puo' passare la linea: collegate e con il segreto. */
export function linePeers(status) {
  if (!status?.enabled) return [];
  return (status.peers || []).filter((p) => p.has_secret);
}

export class HomeLine {
  /**
   * @param shell     `.home-shell`: la classe `is-line` decide testa e fila.
   * @param chatEl    `#home-chat`: il filo della linea ci sta dentro.
   * @param composer  `{field, input, send}` del composer della casa.
   * @param sheet     il `<dialog>` del foglio «Con chi parli».
   * @param isPersonal `() => boolean`: a schermo c'e' la chat di sempre.
   * @param notebooks `async () => [nomi]`: i quaderni di questo telefono.
   * @param openHome  `(nome|null)`: apri una chat di questo telefono.
   * @param openPeers `({add})`: la stanza «Altre Jenny».
   * @param onChange  chiamata quando cambia la linea (segnaposto del campo).
   */
  constructor({
    shell, chatEl, composer = {}, sheet, isPersonal, notebooks, openHome, openPeers, onChange,
    onPeers,
  } = {}) {
    this.shell = shell;
    this.chatEl = chatEl;
    this.composer = composer;
    this.sheet = sheet;
    this.isPersonal = isPersonal || (() => true);
    this.notebooks = notebooks || (async () => []);
    /** I quaderni di qui, letti all'apertura del foglio. */
    this._homeNames = [];
    this.openHome = openHome || (() => {});
    this.openPeers = openPeers || (() => {});
    this.onChange = onChange || (() => {});
    /** Le Jenny collegate sono cambiate (frame `peers`). */
    this.onPeers = onPeers || (() => {});
    /** Lo stato dei collegamenti (`peer.status`), o `null` prima di saperlo. */
    this.status = null;
    /** La linea aperta sulla chat di sempre, o `null`. */
    this.line = null;
    this._timer = null;
    this._waitTimer = null;
    this._sheetPeer = HERE;
    this._chats = new Map();
    this._build();
  }

  /* ── Costruzione ─────────────────────────────────────────────────────── */

  _build() {
    const field = this.composer.field;
    if (field) {
      this.toggle = button('home-line-toggle', null, () => this.openSheet());
      this.toggle.hidden = true;
      this.toggle.appendChild(icon('ti-arrows-exchange'));
      this.toggle.appendChild(el('span', 'home-line-toggle-label'));
      const plan = field.querySelector('.home-plan-toggle');
      if (plan) plan.after(this.toggle);
      else field.prepend(this.toggle);
    }
    /* La testa: stessa forma e stesse classi dell'intestazione delle stanze. */
    this.head = el('header', 'home-head home-line-head');
    this.head.hidden = true;
    const title = el('div', 'home-title');
    this.backBtn = button('home-back', null, () => this.close());
    this.backBtn.appendChild(icon('ti-chevron-left'));
    this.path = el('nav', 'home-path');
    this.path.addEventListener('click', (e) => {
      if (e.target.closest('button')) this.openSheet();
    });
    title.append(this.backBtn, this.path);
    this.live = el('span', 'home-line-live');
    this.head.append(title, this.live);
    this.shell?.querySelector('.home-showcase')?.before(this.head);

    /* Il filo della linea e la striscia di quando l'altra non risponde. */
    this.thread = el('div', 'home-thread home-line-thread');
    this.thread.hidden = true;
    const own = this.chatEl?.querySelector('#home-thread');
    if (own) own.after(this.thread);
    this.wire = el('div', 'home-wire home-line-wire');
    this.wire.hidden = true;
    this.chatEl?.querySelector('.home-composer')?.before(this.wire);
  }

  /* ── Stato ───────────────────────────────────────────────────────────── */

  /** Rilegge collegamenti e linea. Mai un'eccezione: senza, il tasto non c'e'. */
  async refresh() {
    try {
      const status = await rpc.peer('line.status');
      this.status = status;
      this._setLine(status.line || null, { redraw: true });
    } catch {
      /* Collegamenti spenti o gateway senza le linee: la casa resta com'e'. */
      this.status = this.status || null;
      this.apply();
    }
  }

  isOpenHere() {
    return Boolean(this.line) && this.isPersonal();
  }

  /** Il segnaposto del campo a linea aperta, o `null`. */
  placeholder() {
    return this.isOpenHere() ? t('placeholder', { name: this.line.name }) : null;
  }

  _setLine(line, { redraw = false } = {}) {
    const before = this.line;
    this.line = line;
    if (line && (redraw || !before || before.lid !== line.lid)) this._drawThread(line.items || []);
    if (!line) this._clearThread();
    this.apply();
    this._schedule();
    this.onChange();
  }

  _schedule() {
    clearTimeout(this._timer);
    if (!this.line) return;
    this._timer = setTimeout(async () => {
      if (!this.line) return;
      try {
        const status = await rpc.peer('line.status');
        this.status = status;
        if (status.line && this.line && status.line.lid === this.line.lid) {
          this.line = { ...status.line, items: this.line.items };
          this.apply();
        } else {
          this._setLine(status.line || null, { redraw: true });
          return;
        }
      } catch { /* il prossimo giro riprova */ }
      this._schedule();
    }, POLL_MS);
  }

  /** Mette a schermo quel che lo stato dice: tasto, testa, colore, striscia. */
  apply() {
    const peers = linePeers(this.status);
    const here = this.isOpenHere();
    if (this.toggle) {
      this.toggle.hidden = !peers.length && !this.line;
      const label = this.toggle.querySelector('.home-line-toggle-label');
      label.textContent = here ? this.line.name : '';
      this.toggle.setAttribute('aria-label', here
        ? t('toggleOn', { name: this.line.name }) : t('toggle'));
      const glyph = this.toggle.querySelector('.ti');
      glyph.className = `ti ${here ? peerGlyph(this.line) : 'ti-arrows-exchange'}`;
    }
    this.shell?.classList.toggle('is-line', here);
    this.chatEl?.classList.toggle('is-line', here);
    this.composer.field?.classList.toggle('is-line', here);
    this.composer.send?.classList.toggle('is-line', here);
    this.head.hidden = !here;
    this.thread.hidden = !here;
    if (here) this._drawHead();
    this._drawWire();
  }

  _drawHead() {
    const line = this.line;
    const parts = headParts(line);
    this.backBtn.setAttribute('aria-label', t('back'));
    this.path.replaceChildren();
    parts.forEach((part, i) => {
      const last = i === parts.length - 1;
      if (i) this.path.appendChild(el('span', 'home-path-sep', '›'));
      const name = el('span', 'home-path-name', part);
      if (last) {
        const here = button('home-path-here home-line-here');
        if (line.place) {
          const dot = el('span', 'home-path-dot');
          dot.style.background = dotColor(line.place);
          here.appendChild(dot);
        }
        here.appendChild(name);
        this.path.appendChild(here);
      } else {
        const root = button('home-path-root');
        root.appendChild(name);
        this.path.appendChild(root);
      }
    });
    const stuck = isStuck(line);
    this.live.className = `home-line-live${stuck ? ' is-off' : ''}`;
    this.live.replaceChildren(el('i'), document.createTextNode(
      stuck ? t('offline') : (line.state === 'opening' ? t('opening') : t('online')),
    ));
  }

  _drawWire() {
    const stuck = this.isOpenHere() && isStuck(this.line);
    this.wire.hidden = !stuck;
    if (!stuck) return;
    this.wire.replaceChildren(
      el('span', 'home-line-wire-text', t('stuck', { name: this.line.name })),
      button('home-line-key', t('retry'), () => this.retry()),
      button('home-line-key is-main', t('backHome'), () => this.close()),
    );
  }

  /* ── Il filo ─────────────────────────────────────────────────────────── */

  _clearThread() {
    clearTimeout(this._waitTimer);
    this.thread.replaceChildren();
  }

  _drawThread(items) {
    this._clearThread();
    this.thread.appendChild(this._mark(t('now', { name: this.line.name }), peerGlyph(this.line)));
    for (const item of items) this._appendItem(item, { live: false });
    this._toBottom();
  }

  _mark(text, glyph) {
    const node = el('div', 'home-line-mark');
    node.append(icon(glyph), document.createTextNode(text));
    return node;
  }

  _appendItem(item, { live = true } = {}) {
    if (!item || typeof item.text !== 'string') return;
    if (item.id && this.thread.querySelector(`[data-line-item="${CSS.escape(item.id)}"]`)) return;
    let node = null;
    if (item.role === 'user') {
      node = el('div', 'home-msg home-msg-user');
      node.appendChild(el('div', 'home-block', item.text));
      const time = chatTimeNode(item.at, 'home-time');
      if (time) node.appendChild(time);
    } else if (item.role === 'peer') {
      this._stopWaiting();
      node = el('div', 'home-msg home-msg-jenny');
      const block = el('div', 'home-block');
      block.innerHTML = renderMarkdown(item.text);
      node.appendChild(block);
      const time = chatTimeNode(item.at, 'home-time');
      if (time) {
        const tail = el('div', 'home-tail');
        tail.appendChild(time);
        node.appendChild(tail);
      }
    } else if (item.role === 'place') {
      const where = item.text ? `${this.line?.name || ''} › ${item.text}` : t('mainChat');
      node = this._mark(where, item.text ? 'ti-notebook' : 'ti-message-2');
    }
    if (!node) return;
    if (item.id) node.dataset.lineItem = item.id;
    this.thread.appendChild(node);
    if (live && item.role === 'user') this._startWaiting();
    this._toBottom();
  }

  _startWaiting() {
    this._stopWaiting();
    this._waiting = el('div', 'home-msg home-msg-jenny home-line-wait');
    this._waiting.appendChild(el('div', 'home-block', '…'));
    this.thread.appendChild(this._waiting);
    this._waitTimer = setTimeout(() => this._stopWaiting(), WAIT_MS);
  }

  _stopWaiting() {
    clearTimeout(this._waitTimer);
    this._waiting?.remove();
    this._waiting = null;
  }

  _toBottom() {
    this.thread.scrollTop = this.thread.scrollHeight;
  }

  /* ── Frame dal gateway ───────────────────────────────────────────────── */

  handleFrame(msg) {
    if (msg?.event !== 'line' || msg.chat_id !== 'default') return;
    if (msg.kind === 'state') {
      const line = msg.line || null;
      if (line && this.line && this.line.lid === line.lid) {
        this.line = { ...line, items: this.line.items || [] };
        this.apply();
        this.onChange();
      } else {
        this._setLine(line ? { ...line, items: line.items || [] } : null, { redraw: true });
      }
      if (msg.note === 'no_place') this._appendNote(t('noPlace'));
    } else if (msg.kind === 'item' && this.line) {
      (this.line.items = this.line.items || []).push(msg.item);
      this._appendItem(msg.item);
    } else if (msg.kind === 'ask' && this.line) {
      this._appendAsk(msg.ask);
    } else if (msg.kind === 'ask_closed') {
      this._dropAsk(msg.cid);
    } else if (msg.kind === 'peers') {
      /* Collegata o scollegata una Jenny, anche dall'altro telefono: si
         rilegge chi c'e' (il tasto, il foglio, la riga delle Impostazioni). */
      this.onPeers();
      this.refresh();
    } else if (msg.kind === 'closed') {
      const reason = msg.reason;
      this._setLine(null);
      if (reason && reason !== 'user') {
        showToast(i18n.tOr(`home.line.closed.${reason}`, t('closed.closed'), { name: msg.name || '' }), 'error');
      }
    }
  }

  /** Una domanda dell'altra Jenny prima di usare un'app sul suo telefono
   *  (`jenny/peers/phone_consent.py`): la frase la scrive questo codice, e i
   *  tasti rispondono con `peer.line.answer`. */
  _appendAsk(ask) {
    if (!ask || typeof ask.cid !== 'string') return;
    this._dropAsk(ask.cid);
    this._stopWaiting();
    const name = this.line?.name || '';
    const node = el('div', 'home-msg home-msg-jenny home-line-ask');
    node.dataset.lineAsk = ask.cid;
    const costly = ask.kind === 'costly';
    node.appendChild(el('div', 'home-block', costly
      ? t('ask.costly', { name, what: ask.what || '', app: ask.app || '' })
      : t('ask.app', { name, app: ask.app || '' })));
    if (ask.goal) node.appendChild(el('div', 'home-line-ask-goal', `«${ask.goal}»`));
    const keys = el('div', 'home-line-keys');
    const options = Array.isArray(ask.options) ? ask.options : ['ok', 'no'];
    for (const choice of ['ok', 'always', 'no']) {
      if (!options.includes(choice) && choice !== 'no') continue;
      keys.appendChild(button(`home-line-key${choice === 'ok' ? ' is-main' : ''}`,
        t(`ask.${choice}`), () => {
          this._dropAsk(ask.cid);
          rpc.peer('line.answer', { cid: ask.cid, choice })
            .catch(() => showToast(t('sayFailed'), 'error'));
          if (choice !== 'no') this._startWaiting();
        }));
    }
    node.appendChild(keys);
    this.thread.appendChild(node);
    this._toBottom();
  }

  _dropAsk(cid) {
    if (typeof cid !== 'string') return;
    this.thread.querySelector(`[data-line-ask="${CSS.escape(cid)}"]`)?.remove();
  }

  _appendNote(text) {
    this.thread.appendChild(el('div', 'home-note', text));
    this._toBottom();
  }

  /* ── Gesti ───────────────────────────────────────────────────────────── */

  /** Manda *text* all'altra Jenny. Vero se e' partito verso il gateway. */
  say(text) {
    if (!this.line) return false;
    rpc.peer('line.say', { text }).catch(() => showToast(t('sayFailed'), 'error'));
    return true;
  }

  async open(peerId, place = '') {
    closeActionSheet(this.sheet);
    try {
      const status = await rpc.peer('line.open', { id: peerId, place });
      this.status = status;
      this._setLine(status.line || null, { redraw: !this.line });
    } catch (e) {
      showToast(i18n.tOr(`home.line.errors.${e?.message}`, t('errors.generic')), 'error');
    }
  }

  async close() {
    if (!this.line) return false;
    try {
      const status = await rpc.peer('line.close');
      this.status = status;
      this._setLine(null);
    } catch {
      showToast(t('errors.generic'), 'error');
    }
    return true;
  }

  retry() {
    rpc.peer('line.retry').catch(() => {});
    showToast(t('retrying'));
  }

  /* ── Il foglio «Con chi parli» ───────────────────────────────────────── */

  async openSheet() {
    if (!this.sheet) return;
    this._sheetPeer = this.isOpenHere() ? this.line.peer : HERE;
    this._renderSheet();
    this.sheet.showModal?.();
    this._loadChats(this._sheetPeer);
    /* I collegamenti e i quaderni possono essere cambiati da quando la casa
       e' partita: si rileggono, e il foglio si ridisegna con quel che arriva. */
    const [status, names] = await Promise.allSettled([
      rpc.peer('line.status'), this.notebooks(),
    ]);
    if (status.status === 'fulfilled') this.status = status.value;
    if (names.status === 'fulfilled') this._homeNames = names.value || [];
    if (this.sheet.open) this._renderSheet();
  }

  async _loadChats(peerId) {
    if (!peerId || this._chats.has(peerId)) return;
    const peer = linePeers(this.status).find((p) => p.id === peerId);
    if (!peer || isComputer(peer)) return;
    this._chats.set(peerId, { state: 'loading', names: [] });
    if (this.sheet.open) this._renderSheet();
    try {
      const got = await rpc.peer('line.chats', { id: peerId });
      this._chats.set(peerId, { state: 'ok', names: got.chats || [] });
    } catch {
      this._chats.set(peerId, { state: 'failed', names: [] });
      /* La prossima apertura riprova. */
      setTimeout(() => this._chats.delete(peerId), 0);
    }
    if (this.sheet.open && this._sheetPeer === peerId) this._renderSheet();
  }

  _renderSheet() {
    const peers = linePeers(this.status);
    const tabs = el('div', 'home-line-tabs');
    tabs.setAttribute('role', 'tablist');
    const tab = (id, label, glyph, peer) => {
      const on = this._sheetPeer === id;
      const b = button(`home-line-tab${on ? (id ? ' is-peer' : ' is-here') : ''}`, null, () => {
        this._sheetPeer = id;
        this._renderSheet();
        this._loadChats(id);
      });
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      if (glyph) b.appendChild(icon(glyph));
      else {
        const dot = el('i', `home-line-dot${peer?.last_error && peer.last_try_ms > peer.last_seen_ms ? ' is-off' : ''}`);
        b.appendChild(dot);
      }
      b.appendChild(document.createTextNode(label));
      return b;
    };
    tabs.appendChild(tab(HERE, t('here'), 'ti-home'));
    for (const p of peers) {
      tabs.appendChild(tab(p.id, p.name, isComputer(p) ? 'ti-device-laptop' : null, p));
    }
    const add = button('home-line-tab is-add', null, () => {
      closeActionSheet(this.sheet);
      this.openPeers({ add: true });
    });
    add.setAttribute('aria-label', t('add'));
    add.appendChild(icon('ti-plus'));
    tabs.appendChild(add);

    const rows = [];
    const row = (glyphNode, label, sub, onPick, { current = false, peer = false } = {}) => {
      const b = button(`home-plan-sheet-row${current ? ' is-primary' : ''}`, null, onPick);
      b.appendChild(glyphNode);
      const text = el('span', 'home-plan-row-text home-line-row-text');
      text.appendChild(el('span', null, label));
      if (sub) text.appendChild(el('span', 'home-plan-row-sub', sub));
      b.appendChild(text);
      if (current) b.appendChild(icon('ti-check', peer ? 'home-line-check is-peer' : 'home-line-check'));
      return b;
    };
    const chatGlyph = (peer) => icon('ti-message-2', peer ? 'is-peer' : '');
    const nbGlyph = (name) => {
      const wrap = el('span', 'home-line-nbdot');
      const dot = el('i');
      dot.style.background = dotColor(name);
      wrap.appendChild(dot);
      return wrap;
    };
    const line = this.line;
    if (this._sheetPeer === HERE) {
      const homeNow = !line;
      rows.push(row(chatGlyph(false), t('mainChat'), homeNow && this.isPersonal() ? t('youAreHere') : '',
        () => this._pickHome(null), { current: homeNow && this.isPersonal() }));
      for (const name of this._homeNames) {
        rows.push(row(nbGlyph(name), name, '', () => this._pickHome(name)));
      }
    } else {
      const peer = peers.find((p) => p.id === this._sheetPeer);
      const onThis = line && line.peer === this._sheetPeer;
      if (peer && isComputer(peer)) {
        rows.push(row(chatGlyph(true), t('computerChat'), t('computerSub'),
          () => this.open(peer.id, ''), { current: Boolean(onThis), peer: true }));
        rows.push(el('p', 'home-plan-sheet-note home-line-note', t('computerNote')));
      } else if (peer) {
        rows.push(row(chatGlyph(true), t('mainChat'), t('mainChatOf', { name: peer.name }),
          () => this.open(peer.id, ''), { current: Boolean(onThis && !line.place), peer: true }));
        const chats = this._chats.get(peer.id);
        if (!chats || chats.state === 'loading') {
          rows.push(el('p', 'home-plan-sheet-note home-line-note', t('loadingChats')));
        } else if (chats.state === 'failed') {
          rows.push(el('p', 'home-plan-sheet-note home-line-note', t('noChats', { name: peer.name })));
        } else {
          for (const name of chats.names) {
            const here = Boolean(onThis && line.place === name);
            rows.push(row(nbGlyph(name), name, here ? t('youAreHere') : '',
              () => this.open(peer.id, name), { current: here, peer: true }));
          }
        }
      }
    }
    openActionSheet(this.sheet, {
      children: [...sheetHead(t('sheetTitle')), tabs, ...rows],
      closeLabel: i18n.t('common.close'),
      cls: 'home-line-sheet',
    });
  }

  async _pickHome(name) {
    closeActionSheet(this.sheet);
    /* Tornare a una chat di questo telefono chiude la linea. */
    if (this.line) await this.close();
    this.openHome(name);
  }
}
