/** «Altre Jenny»: il pannello che abbina questa Jenny a un'altra dell'utente.
 *
 * Vive in officina, nel cassetto Mani, accanto a «Da altri dispositivi»: stessa
 * forma (in cassetto la riga, l'amministrazione dietro un tocco). Lo stato
 * arriva dal canale (`rpc.peer`), che tiene il collegamento:
 * `jenny/peers/rpc.py`.
 *
 * Due strade per abbinare, sullo stesso schermo:
 * - **il QR**, la principale: questa Jenny mostra un QR con indirizzo e codice;
 *   l'altra lo inquadra con la fotocamera di sistema e si apre già compilata
 *   (`shared/peer-link.js`);
 * - **a mano**, la riserva: nome su Tailscale o `100.x`, più il codice.
 * In tutti e due i casi chi ha il codice conferma con «Collega»: un link
 * arrivato da fuori riempie il modulo, non abbina.
 */

import { rpc } from './shared/rpc-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { detailDialog, formDialog } from './shared/dialog.js';

const POLL_MS = 5000;

const t = (key, params) => i18n.t(`settings.peers.${key}`, params);

/** La riga in cassetto. */
export function peersSummary(status) {
  if (!status) return i18n.t('settings.loading');
  if (!status.enabled) return t('summaryOff');
  const peers = status.peers || [];
  if (peers.length === 1) return t('summaryOne', { name: peers[0].name });
  if (peers.length) return t('summaryMany', { count: peers.length });
  return t('summaryNone');
}

function timeText(ms) {
  return new Date(ms).toLocaleString(i18n.locale, {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

/** Vero per un collegato che è opencode sul Mac, non un'altra Jenny (`kind`). */
export function isOpencodePeer(peer) {
  return peer?.kind === 'opencode';
}

/** Lo stato di un'altra Jenny in una riga: raggiungibile, in coda, da ricollegare. */
export function peerStateText(peer) {
  if (!peer.has_secret) return t('stateRepair');
  const parts = [];
  if (peer.last_error && peer.last_try_ms > peer.last_seen_ms) {
    parts.push(t('stateUnreachable'));
  } else if (peer.last_seen_ms) {
    parts.push(t('stateSeen', { time: timeText(peer.last_seen_ms) }));
  } else {
    parts.push(t('stateNever'));
  }
  if (peer.queued) parts.push(t('stateQueued', { count: peer.queued }));
  return parts.join(' · ');
}

/** L'altra Jenny appena collegata dal QR di questa, o `null`.
 *
 *  Chi mostra il QR non sceglieva un nome: il collegamento le dava quello di
 *  Tailscale (`sdk-gphone16k-arm64-2` sui due emulatori, 20/11/2026), da
 *  rinominare a mano. Una Jenny nuova comparsa mentre il codice era aperto è
 *  quella che l'ha usato: il pannello chiede subito come chiamarla. */
export function peerLinkedFromQr(before, after) {
  if (!before?.pairing || !after) return null;
  const known = new Set((before.peers || []).map((p) => p.id));
  return (after.peers || []).find((p) => !known.has(p.id)) || null;
}

function errorText(e) {
  const reason = e?.message || '';
  const known = ['bad_code', 'bad_name', 'no_code_open', 'unreachable', 'disabled', 'protocol',
    'still_working', 'nothing_to_undo', 'bad_level'];
  return known.includes(reason) ? t(`errors.${reason}`) : t('errors.generic', { error: reason || 'error' });
}

export class PeersPanel {
  /**
   * @param {HTMLElement} container il corpo del pannello
   * @param {{onStatus?: Function, joinWith?: object|null}} opts `joinWith`: un
   *   link d'abbinamento già letto (dal QR): il modulo «Ho il codice» si apre
   *   compilato appena lo stato è arrivato.
   */
  constructor(container, opts = {}) {
    this.el = container;
    this.onStatus = opts.onStatus || null;
    this._joinWith = opts.joinWith || null;
    this.status = null;
    this._pair = null;
    /* La cronologia aperta, per Jenny: si chiede al tocco e resta finche' il
       pannello e' aperto. */
    this._history = {};
    /* Le richieste di fare arrivate da quella Jenny, lette con la cronologia. */
    this._acts = {};
    this._busy = false;
    this._timer = null;
    this._destroyed = false;
  }

  destroy() {
    this._destroyed = true;
    clearTimeout(this._timer);
  }

  async refresh() {
    let status;
    try {
      status = await rpc.peer('status');
    } catch (e) {
      if (this._destroyed) return;
      this.el.innerHTML = `<div class="settings-empty-state">${escapeHtml(e.message || 'error')}</div>`;
      return;
    }
    if (this._destroyed) return;
    this._show(status);
    this._schedule();
    if (this._joinWith) {
      const link = this._joinWith;
      this._joinWith = null;
      this.join(link);
    }
  }

  _schedule() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      if (this._destroyed) return;
      if (!this.el.isConnected || !this.el.closest('.drawer')?.classList.contains('open')) {
        this.destroy();
        return;
      }
      if (!this._busy) this.refresh();
    }, POLL_MS);
  }

  _show(status) {
    const before = this.status;
    this.status = status;
    /* Il codice mostrato vive finché il server lo tiene aperto: usato o
       scaduto, il QR sparisce da solo. */
    if (status.pair) this._pair = status.pair;
    if (!status.pairing) this._pair = null;
    this.onStatus?.(status);
    this.render();
    const fresh = peerLinkedFromQr(before, status);
    if (fresh) this._rename(fresh, { fresh: true });
  }

  render() {
    const s = this.status;
    if (!s || this._destroyed) return;
    const parts = [];
    parts.push(`<p class="onboarding-desc">${escapeHtml(t('intro'))}</p>`);
    parts.push(`<div class="settings-field settings-toggle-row">
      <label class="settings-label" for="peers-enabled">${escapeHtml(t('toggle'))}</label>
      <label class="toggle-switch">
        <input type="checkbox" id="peers-enabled" ${s.enabled ? 'checked' : ''}>
        <span class="toggle-slider"></span>
      </label>
    </div>`);
    if (!s.enabled) {
      parts.push(`<p class="remote-hint">${escapeHtml(t('offHint'))}</p>`);
      this._paint(parts);
      return;
    }
    if (s.tailscale?.state !== 'up') {
      parts.push(`<div class="settings-notice settings-notice-strong">
        <i class="ti ti-plug-off" aria-hidden="true"></i><div>${escapeHtml(t('needsTailscale'))}</div></div>`);
    }
    if (this._pair) {
      parts.push(this._pairHtml(this._pair));
    } else {
      parts.push(`<div class="remote-actions">
        <button class="settings-btn-add" type="button" data-peers="show-qr">
          <i class="ti ti-qrcode" aria-hidden="true"></i> ${escapeHtml(t('showQr'))}</button>
        <button class="settings-btn-add" type="button" data-peers="join">
          <i class="ti ti-keyboard" aria-hidden="true"></i> ${escapeHtml(t('haveCode'))}</button>
      </div>`);
    }
    const peers = s.peers || [];
    parts.push(`<div class="remote-sub">${escapeHtml(t('linked'))}</div>`);
    if (!peers.length) {
      parts.push(`<div class="remote-hint">${escapeHtml(t('noneLinked'))}</div>`);
    }
    for (const p of peers) {
      // Un Mac con opencode (il plugin `opencode-jenny`) risponde e basta: non
      // fa domande a questa Jenny, non riceve file né chiede di fare. I permessi
      // che vanno da qui a lui non hanno niente da proteggere, e non si mostrano.
      const mac = isOpencodePeer(p);
      parts.push(`<div class="peer-card${mac ? ' peer-card-opencode' : ''}" data-peer="${escapeHtml(p.id)}">
        <div class="peer-card-head">
          <i class="ti ${mac ? 'ti-device-laptop' : 'ti-arrows-exchange'}" aria-hidden="true"></i>
          <span class="peer-card-name">${escapeHtml(p.name)}</span>
          ${mac ? `<span class="peer-card-kind">${escapeHtml(t('kindOpencode'))}</span>` : ''}
        </div>
        <div class="peer-card-state">${escapeHtml(peerStateText(p))}</div>
        ${mac ? `<div class="remote-hint">${escapeHtml(t('opencodeHint'))}</div>` : ''}
        ${mac ? '' : this._permHtml(p, 'can_ask', 'canAsk', 'canAskHint')}
        ${mac ? '' : this._permHtml(p, 'send_files', 'sendFiles', 'sendFilesHint')}
        ${mac ? '' : this._actHtml(p)}
        ${this._pendingHtml((s.pending || []).filter((c) => c.peer === p.id))}
        <div class="remote-actions">
          <button class="settings-btn-add" type="button" data-peers="ping" ${p.has_secret ? '' : 'disabled'}>
            <i class="ti ti-antenna-bars-5" aria-hidden="true"></i> ${escapeHtml(t('ping'))}</button>
          <button class="settings-btn-add" type="button" data-peers="rename">
            <i class="ti ti-pencil" aria-hidden="true"></i> ${escapeHtml(t('rename'))}</button>
          <button class="settings-btn-add remote-danger" type="button" data-peers="remove">
            <i class="ti ti-unlink" aria-hidden="true"></i> ${escapeHtml(t('remove'))}</button>
          <button class="settings-btn-add" type="button" data-peers="history">
            <i class="ti ti-history" aria-hidden="true"></i> ${escapeHtml(t('history'))}</button>
        </div>
        ${this._acts[p.id] ? this._actsHtml(this._acts[p.id]) : ''}
        ${this._history[p.id] ? this._historyHtml(this._history[p.id]) : ''}
      </div>`);
    }
    this._paint(parts);
  }

  /** Un permesso di quella Jenny su questa: lo decide chi riceve. */
  _permHtml(peer, key, label, hint) {
    const id = `peer-${key}-${peer.id.slice(0, 8)}`;
    return `<div class="settings-field settings-toggle-row peer-perm">
      <label class="settings-label" for="${id}">${escapeHtml(t(label))}
        <span class="settings-field-hint">${escapeHtml(t(hint))}</span></label>
      <label class="toggle-switch">
        <input type="checkbox" id="${id}" data-perm="${key}" ${peer[key] ? 'checked' : ''}>
        <span class="toggle-slider"></span>
      </label>
    </div>`;
  }

  /** Le richieste di fare di quella Jenny: no, sì chiedendo prima di ciò che
   *  esce dal telefono, o sì per tutto. Lo decide chi riceve. */
  _actHtml(peer) {
    const id = `peer-act-${peer.id.slice(0, 8)}`;
    const level = ['off', 'confirm', 'free'].includes(peer.act) ? peer.act : 'off';
    const opts = ['off', 'confirm', 'free'].map((v) => `<option value="${v}" ${v === level ? 'selected' : ''}>${
      escapeHtml(t(`act.${v}`))}</option>`).join('');
    return `<div class="settings-field peer-perm peer-act">
      <label class="settings-label" for="${id}">${escapeHtml(t('act.label'))}
        <span class="settings-field-hint">${escapeHtml(t(`act.hint.${level}`))}</span></label>
      <select class="settings-select" id="${id}" data-act>${opts}</select>
    </div>`;
  }

  /** Le conferme che aspettano l'utente di qui: «Personale vuole aprire …». */
  _pendingHtml(rows) {
    if (!rows.length) return '';
    return rows.map((c) => `<div class="peer-confirm" data-cid="${escapeHtml(c.cid)}">
      <div class="peer-confirm-what"><i class="ti ti-hand-stop" aria-hidden="true"></i>
        <span>${escapeHtml(c.what)}</span></div>
      <div class="remote-actions">
        <button class="settings-btn-add" type="button" data-confirm="no">${escapeHtml(t('confirmNo'))}</button>
        <button class="settings-btn-add" type="button" data-confirm="yes">
          <i class="ti ti-check" aria-hidden="true"></i> ${escapeHtml(t('confirmYes'))}</button>
      </div>
    </div>`).join('');
  }

  /** Le richieste di fare arrivate: cosa, com'è finita, cosa ha toccato, l'Annulla. */
  _actsHtml(rows) {
    if (!rows.length) return '';
    const row = (r) => {
      const when = new Date(r.at_ms).toLocaleString(i18n.locale, {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      });
      const state = r.working ? 'working' : r.state;
      const touched = (r.done || []).filter((d) => d && d.undoable).length;
      const canUndo = !r.working && r.state === 'answered' && touched > 0;
      return `<div class="peer-act-row" data-qid="${escapeHtml(r.qid)}">
        <div class="peer-log-row"><span>«${escapeHtml(r.text)}»</span>
          <span class="peer-log-when">${escapeHtml(when)}</span></div>
        <div class="peer-act-state">${escapeHtml(t(`actState.${state}`, { count: touched }))}
          ${canUndo ? `<button class="settings-btn-add" type="button" data-undo>
            <i class="ti ti-arrow-back-up" aria-hidden="true"></i> ${escapeHtml(t('undo'))}</button>` : ''}
        </div>
      </div>`;
    };
    return `<div class="peer-log peer-acts"><div class="remote-sub">${escapeHtml(t('actsTitle'))}</div>${
      rows.map(row).join('')}</div>`;
  }

  _historyHtml(rows) {
    if (!rows.length) return `<div class="remote-hint">${escapeHtml(t('historyEmpty'))}</div>`;
    const line = (row) => {
      const d = row.data || {};
      const key = {
        'out:ask': ['log.askOut', { text: d.text || '' }],
        'in:ask': ['log.askIn', { text: d.text || '' }],
        'out:answer': ['log.answerOut', {}],
        'in:answer': ['log.answerIn', {}],
        'in:file': ['log.file', { name: d.name || '' }],
        'out:ping': ['log.ping', {}],
        'in:ping': ['log.ping', {}],
        'sys:paired': ['log.paired', {}],
        'sys:refused': ['log.refused', { kind: d.kind || '' }],
        'sys:removed': ['log.removed', {}],
        'sys:removed_by_other': ['log.removedByOther', {}],
        'sys:confirm': [d.allowed ? 'log.confirmYes' : 'log.confirmNo', { target: d.target || '' }],
        'sys:undo': ['log.undo', { files: d.restored || 0, jobs: d.jobs || 0 }],
        'out:undo': ['log.undoAsked', {}],
        'in:undone': ['log.undone', {}],
      }[`${row.direction}:${row.kind}`];
      if (!key) return '';
      const when = new Date(row.at_ms).toLocaleString(i18n.locale, {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      });
      return `<div class="peer-log-row"><span>${escapeHtml(t(key[0], key[1]))}</span>
        <span class="peer-log-when">${escapeHtml(when)}</span></div>`;
    };
    return `<div class="peer-log">${rows.map(line).join('')}</div>`;
  }

  _pairHtml(pair) {
    const qr = window.qrcode ? this._qrSvg(pair.link) : '';
    const minutes = Math.max(0, Math.round((pair.expires_ms - Date.now()) / 60000));
    const address = pair.host || pair.ip || '';
    return `<div class="peer-pair">
      <div class="remote-sub">${escapeHtml(t('scanFrom'))}</div>
      ${qr ? `<div class="remote-qr"><div class="remote-qr-box">${qr}</div></div>` : ''}
      <p class="remote-hint remote-hint-center">${escapeHtml(t('expires', { minutes }))}</p>
      <div class="remote-sub">${escapeHtml(t('orByHand'))}</div>
      <div class="peer-pair-facts">
        <div><span>${escapeHtml(t('address'))}</span><code>${escapeHtml(address)}</code></div>
        ${pair.ip && pair.host ? `<div><span>${escapeHtml(t('orIp'))}</span><code>${escapeHtml(pair.ip)}</code></div>` : ''}
        <div><span>${escapeHtml(t('code'))}</span><code class="peer-pair-code">${escapeHtml(pair.code)}</code></div>
      </div>
      <div class="remote-actions"><button class="settings-btn-add" type="button" data-peers="cancel">
        <i class="ti ti-x" aria-hidden="true"></i> ${escapeHtml(t('cancel'))}</button></div>
    </div>`;
  }

  _paint(parts) {
    this.el.innerHTML = parts.join('');
    this.el.querySelector('#peers-enabled')?.addEventListener('change', (e) => this._toggle(e.target));
    for (const input of this.el.querySelectorAll('[data-perm]')) {
      input.addEventListener('change', () => {
        const peerId = input.closest('[data-peer]')?.dataset.peer;
        const key = input.dataset.perm;
        const value = input.checked;
        this._run(() => rpc.peer('permissions', { id: peerId, [key]: value }),
          () => { input.checked = !value; });
      });
    }
    for (const select of this.el.querySelectorAll('[data-act]')) {
      const before = select.value;
      select.addEventListener('change', () => {
        const peerId = select.closest('[data-peer]')?.dataset.peer;
        this._run(() => rpc.peer('permissions', { id: peerId, act: select.value }),
          () => { select.value = before; });
      });
    }
    for (const btn of this.el.querySelectorAll('[data-confirm]')) {
      btn.addEventListener('click', () => {
        const cid = btn.closest('[data-cid]')?.dataset.cid;
        this._run(() => rpc.peer('confirm', { cid, allow: btn.dataset.confirm === 'yes' }));
      });
    }
    for (const btn of this.el.querySelectorAll('[data-undo]')) {
      btn.addEventListener('click', () => {
        const peerId = btn.closest('[data-peer]')?.dataset.peer;
        const qid = btn.closest('[data-qid]')?.dataset.qid;
        this._undo(peerId, qid);
      });
    }
    for (const btn of this.el.querySelectorAll('[data-peers]')) {
      btn.addEventListener('click', () => {
        const card = btn.closest('[data-peer]');
        this._action(btn.dataset.peers, card?.dataset.peer || null);
      });
    }
  }

  _qrSvg(text) {
    const qr = window.qrcode(0, 'M');
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
  }

  async _toggle(input) {
    const enabled = input.checked;
    await this._run(() => rpc.peer('set', { enabled }), () => { input.checked = !enabled; });
  }

  _peer(id) {
    return (this.status?.peers || []).find((p) => p.id === id) || null;
  }

  async _action(what, peerId) {
    if (what === 'show-qr') {
      await this._run(() => rpc.peer('pair.start'));
    } else if (what === 'cancel') {
      this._pair = null;
      await this._run(() => rpc.peer('pair.cancel'));
    } else if (what === 'join') {
      this.join(null);
    } else if (what === 'ping') {
      const done = await this._run(() => rpc.peer('ping', { id: peerId }));
      if (done) showToast(t('pingSent'));
    } else if (what === 'history') {
      if (this._history[peerId]) {
        delete this._history[peerId];
        delete this._acts[peerId];
        this.render();
        return;
      }
      try {
        const status = await rpc.peer('history', { id: peerId });
        this._history[peerId] = status.history || [];
        const acts = await rpc.peer('acts', { id: peerId });
        this._acts[peerId] = acts.acts || [];
      } catch (e) {
        showToast(errorText(e), 'error');
        return;
      }
      this.render();
    } else if (what === 'rename') {
      this._rename(this._peer(peerId));
    } else if (what === 'remove') {
      const peer = this._peer(peerId);
      const choice = await detailDialog({
        title: t('removeTitle', { name: peer?.name || '' }),
        bodyHtml: `<p style="font-size:13px">${escapeHtml(t('removeBody'))}</p>`,
        actions: [
          { id: 'cancel', label: i18n.t('common.cancel') },
          { id: 'ok', label: t('removeConfirm'), variant: 'primary' },
        ],
      });
      if (choice !== 'ok') return;
      const done = await this._run(() => rpc.peer('remove', { id: peerId }));
      if (done) showToast(t('removed'));
    }
  }

  async _undo(peerId, qid) {
    let result;
    try {
      result = await rpc.peer('undo', { id: peerId, qid });
    } catch (e) {
      showToast(errorText(e), 'error');
      return;
    }
    const u = result.undo || {};
    showToast(t('undone', { files: u.restored || 0, jobs: u.jobs || 0 }));
    try {
      const acts = await rpc.peer('acts', { id: peerId });
      this._acts[peerId] = acts.acts || [];
    } catch (e) { /* la lista si riprende al prossimo giro */ }
    this.render();
  }

  /** Il modulo «Ho il codice dell'altra Jenny», vuoto o compilato da un QR. */
  join(link) {
    const value = (v) => escapeHtml(String(v ?? ''));
    formDialog({
      id: 'peer-join-dialog',
      title: t('joinTitle'),
      okText: t('joinOk'),
      cancelText: i18n.t('common.cancel'),
      busyText: t('joining'),
      bodyHtml: `
        ${link ? `<p class="oc-dialog-note">${escapeHtml(t('fromQr'))}</p>` : ''}
        ${this.status && !this.status.enabled
    ? `<p class="oc-dialog-note">${escapeHtml(t('joinTurnsOn'))}</p>` : ''}
        <div class="settings-field">
          <label class="settings-label" for="dlg-peer-host">${escapeHtml(t('address'))}</label>
          <input type="text" class="settings-input" id="dlg-peer-host" autocomplete="off"
            placeholder="${escapeHtml(t('addressPlaceholder'))}" value="${value(link?.host || link?.ip)}" />
          <span class="settings-field-hint">${escapeHtml(t('addressHint'))}</span>
        </div>
        <div class="settings-field">
          <label class="settings-label" for="dlg-peer-code">${escapeHtml(t('code'))}</label>
          <input type="text" class="settings-input" id="dlg-peer-code" autocomplete="off"
            autocapitalize="characters" placeholder="K7Q4-MZP2" value="${value(link?.code)}" />
        </div>
        <div class="settings-field">
          <label class="settings-label" for="dlg-peer-name">${escapeHtml(t('nameLabel'))}</label>
          <input type="text" class="settings-input" id="dlg-peer-name" autocomplete="off"
            placeholder="${escapeHtml(t('namePlaceholder'))}" maxlength="40" />
        </div>`,
      setup: (dialog) => {
        const target = link ? '#dlg-peer-name' : '#dlg-peer-host';
        setTimeout(() => dialog.querySelector(target)?.focus(), 50);
      },
      onOk: async (dialog) => {
        const field = (id) => dialog.querySelector(`#dlg-peer-${id}`).value.trim();
        const address = field('host');
        const code = field('code');
        const name = field('name');
        if (!address || !code || !name) {
          showToast(t('fieldsRequired'), 'error');
          return undefined;
        }
        /* Un 100.x scritto a mano è un indirizzo, non un nome: va in `ip`,
           così il collegamento lo salva come tale. Dal QR arrivano tutti e due. */
        const isIp = /^[0-9.]+$/.test(address) || address.includes(':');
        const params = {
          host: isIp ? (link?.host || '') : address,
          ip: isIp ? address : (link?.ip || ''),
          code,
          name,
        };
        if (link?.port) params.port = link.port;
        try {
          /* Chi tocca «Collega» vuole il collegamento: con l'interruttore spento
             il link del QR apriva il modulo e poi falliva con «spento», e
             bisognava annullare, accendere e inquadrare di nuovo (prova sui due
             emulatori, 20/11/2026). Il modulo lo dice, e lo accende qui. */
          if (this.status && !this.status.enabled) {
            const on = await rpc.peer('set', { enabled: true });
            if (!this._destroyed) this._show(on);
          }
          const status = await rpc.peer('pair.join', params);
          if (!this._destroyed) this._show(status);
        } catch (e) {
          showToast(errorText(e), 'error');
          return undefined;
        }
        showToast(t('joined', { name }));
        return true;
      },
    });
  }

  /** «Come la chiami?». Con `fresh`, appena collegata dal QR di qui: il
   *  titolo dice che cosa è successo. */
  _rename(peer, { fresh = false } = {}) {
    if (!peer) return;
    formDialog({
      id: 'peer-rename-dialog',
      title: t(fresh ? 'linkedTitle' : 'renameTitle'),
      okText: i18n.t('common.save'),
      cancelText: i18n.t('common.cancel'),
      bodyHtml: `
        ${fresh ? `<p class="oc-dialog-note">${escapeHtml(t('linkedNote'))}</p>` : ''}
        <div class="settings-field">
          <label class="settings-label" for="dlg-peer-rename">${escapeHtml(t('nameLabel'))}</label>
          <input type="text" class="settings-input" id="dlg-peer-rename" maxlength="40"
            autocomplete="off" value="${escapeHtml(peer.name)}" />
        </div>`,
      /* Il nome che c'è, selezionato: si scrive sopra senza cancellarlo a mano
         (quello di Tailscale, appena collegata, non lo vuole tenere nessuno). */
      setup: (dialog) => {
        setTimeout(() => {
          const input = dialog.querySelector('#dlg-peer-rename');
          input?.focus();
          input?.select();
        }, 50);
      },
      onOk: async (dialog) => {
        const name = dialog.querySelector('#dlg-peer-rename').value.trim();
        if (!name) {
          showToast(t('fieldsRequired'), 'error');
          return undefined;
        }
        const done = await this._run(() => rpc.peer('rename', { id: peer.id, name }));
        return done ? true : undefined;
      },
    });
  }

  async _run(call, onError) {
    if (this._busy) return false;
    this._busy = true;
    try {
      const status = await call();
      if (!this._destroyed) this._show(status);
      return true;
    } catch (e) {
      onError?.();
      showToast(errorText(e), 'error');
      return false;
    } finally {
      this._busy = false;
    }
  }
}
