/** La casa — la stanza «Altre Jenny».
 *
 *  Fino al 22/11/2026 le altre Jenny stavano solo in officina (`mobile-peers.js`,
 *  cassetto Mani). Da quando la linea si passa dalla chat (`home-line.js`) sono
 *  una cosa di chi usa Jenny, non di chi la regola: l'elenco, il collegamento e
 *  i due permessi che contano entrano in casa. File, cronologia e
 *  scollegamento restano in officina, e la stanza lo dice.
 *
 *  Quattro viste, una alla volta:
 *  - **l'elenco**: ogni collegata col suo stato, «Parla», se puo' passare la
 *    linea qui, e cosa puo' chiedere. Un computer con opencode non ha permessi:
 *    risponde e basta;
 *  - **collega, passo 1**: mostrare il QR o scrivere il codice dell'altra;
 *  - **passo 2**: il QR (o il modulo). Una Jenny nuova comparsa mentre il
 *    codice e' aperto e' quella che l'ha usato;
 *  - **passo 3**: come la chiami — e' il nome che dirai in chat — e i due
 *    permessi.
 *
 *  Lo stato arriva dagli stessi comandi dell'officina (`rpc.peer`,
 *  `jenny/peers/rpc.py`): qui non c'e' una seconda verita'.
 */

import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { button, el, icon } from './shared/dom.js';
import { closeActionSheet, openActionSheet, sheetHead, sheetRow } from './shared/sheet.js';
import { showToast } from './shared/utils.js';
import { isComputer, peerGlyph } from './home-line.js';

const t = (key, params) => i18n.t(`home.peers.${key}`, params);

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

/** Lo stato di una collegata in una riga. */
export function stateText(peer) {
  if (!peer.has_secret) return t('state.repair');
  if (isUnreachable(peer)) return t('state.unreachable');
  if (peer.last_seen_ms) {
    const time = new Date(peer.last_seen_ms).toLocaleString(i18n.locale, {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
    return t('state.seen', { time });
  }
  return t('state.never');
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

export class HomePeers {
  /**
   * @param body     il contenitore che scorre della stanza.
   * @param sheet    un `<dialog>` della forma dei fogli del piano, per i livelli.
   * @param onTalk   `(peer)`: apri la linea con lei, nella chat.
   * @param onStatus `(status)`: lo stato e' cambiato (la riga delle Impostazioni).
   * @param onWorkshop porta all'officina, per quel che qui non c'e'.
   */
  constructor({ body, sheet, onTalk, onStatus, onWorkshop } = {}) {
    this.body = body;
    this.sheet = sheet;
    this.onTalk = onTalk || (() => {});
    this.onStatus = onStatus || (() => {});
    this.onWorkshop = onWorkshop || (() => {});
    this.status = null;
    this.view = 'list';
    this._pair = null;
    this._named = null;
    this._timer = null;
    this._open = false;
    this._busy = false;
  }

  /* ── Ciclo di vita ───────────────────────────────────────────────────── */

  async open({ add = false } = {}) {
    this._open = true;
    this.view = add ? 'add' : 'list';
    this.render();
    await this.refresh();
    this._schedule();
  }

  close() {
    this._open = false;
    clearTimeout(this._timer);
    /* Un codice lasciato aperto si chiude con la stanza. */
    if (this._pair) {
      this._pair = null;
      rpc.peer('pair.cancel').catch(() => {});
    }
  }

  /** Vero se Indietro ha fatto un passo dentro la stanza (dal collegamento all'elenco). */
  back() {
    if (this.view === 'list') return false;
    if (this.view === 'qr') this._cancelPair();
    this.view = this.view === 'add' || this.view === 'name' ? 'list' : 'add';
    this.render();
    return true;
  }

  async refresh() {
    try {
      this._show(await rpc.peer('status'));
    } catch (e) {
      if (!this.status) {
        this.body.replaceChildren(el('div', 'home-card-note', t('unavailable')));
      }
    }
  }

  /** Lo stato, senza disegnare: per la riga delle Impostazioni all'avvio. */
  async peek() {
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
      /* Mentre scrivi un nome non si ridisegna niente sotto le dita. */
      if (!this._busy && this.view !== 'name' && this.view !== 'join') await this.refresh();
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
      add: () => this._add(),
      qr: () => this._qr(),
      join: () => this._join(),
      name: () => this._name(),
    }[this.view] || (() => this._list());
    this.body.replaceChildren(...view().filter(Boolean));
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

  _list() {
    const s = this.status;
    if (!s) return [el('div', 'home-card-note', i18n.t('settings.loading'))];
    const peers = s.enabled ? (s.peers || []) : [];
    const nodes = [];
    if (!peers.length) {
      const empty = el('div', 'home-peers-empty');
      empty.append(icon('ti-arrows-exchange'), el('b', null, t('emptyTitle')),
        el('span', null, t('emptyBody')));
      nodes.push(empty);
    }
    for (const p of peers) nodes.push(this._card(p));
    const add = button('home-peers-add', null, () => { this.view = 'add'; this.render(); });
    add.append(icon('ti-plus'), document.createTextNode(t('add')));
    nodes.push(add);
    if (peers.length) {
      const more = button('home-key-btn home-peers-more', t('more'), () => this.onWorkshop());
      nodes.push(more);
    }
    return nodes;
  }

  _card(p) {
    const card = el('div', 'home-card home-peers-card');
    card.dataset.peer = p.id;
    const top = el('div', 'home-peers-top');
    const badge = el('span', `home-peers-badge${isComputer(p) ? ' is-computer' : ''}`);
    badge.appendChild(icon(peerGlyph(p)));
    const who = el('span', 'home-peers-who');
    who.appendChild(el('span', 'home-peers-name', p.name));
    const state = el('span', 'home-peers-state');
    state.append(el('i', `home-line-dot${isUnreachable(p) || !p.has_secret ? ' is-off' : ''}`),
      document.createTextNode(isComputer(p) ? `${t('computer')} · ${stateText(p)}` : stateText(p)));
    who.appendChild(state);
    const talk = button('home-peers-talk', null, () => this.onTalk(p));
    talk.append(icon('ti-message-2'), document.createTextNode(t('talk')));
    talk.disabled = !p.has_secret;
    top.append(badge, who, talk);
    card.appendChild(top);
    if (isComputer(p)) {
      card.appendChild(el('div', 'home-card-note home-peers-note', t('computerNote')));
      return card;
    }
    card.appendChild(this._permRow(
      t('line'), t('lineSub', { name: p.name }),
      this._switch(Boolean(p.line), t('line'), (on) => this._run(
        () => rpc.peer('permissions', { id: p.id, line: on }),
      )),
    ));
    const level = button('home-row home-peers-row', null, () => this._levels(p));
    level.append(el('span', 'home-row-label', t('ask')),
      el('span', 'home-row-value', t(`level.${askLevel(p)}`)),
      icon('ti-chevron-right', 'home-row-go'));
    card.appendChild(level);
    return card;
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
      children: [...sheetHead(t('askTitle', { name: p.name }), t('askNote')), ...rows],
      closeLabel: i18n.t('common.close'),
      cls: 'home-line-sheet',
    });
    this.sheet.showModal?.();
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
    card.append(
      choice('ti-qrcode', t('showQr'), t('showQrSub'), () => this._startQr()),
      choice('ti-keyboard', t('haveCode'), t('haveCodeSub'), () => {
        this.view = 'join';
        this.render();
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
    const card = el('div', 'home-card home-peers-form');
    card.append(
      this._field('home-peers-host', t('address'), {
        placeholder: t('addressPlaceholder'), hint: t('addressHint'),
      }),
      this._field('home-peers-code', t('code'), { placeholder: 'K7Q4-MZP2' }),
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
        return rpc.peer('pair.join', {
          host: isIp ? '' : address, ip: isIp ? address : '', code, name: t('defaultName'),
        });
      });
      const fresh = status && freshPeer(before, status);
      if (fresh) {
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
      this.view = 'list';
      this.render();
    });
    const tail = el('div', 'home-card-note home-peers-center', t('doneNote', { name: peer.name }));
    return [this._steps(3), card, computer ? null : rows, done, tail];
  }
}
