/** «Da altri dispositivi»: il pannello che apre Jenny al tailnet.
 *
 * Vive solo in officina, nel cassetto Mani, dietro la riga «Tailscale» (stesso
 * schema di Telegram: in cassetto quel che si legge, l'amministrazione dietro
 * un tocco). Lo stato arriva dal canale (`rpc.remote`), che ha l'ascoltatore:
 * `jenny/channels/ws_remote.py`.
 *
 * Tre cose che il pannello deve sapere di se':
 * - **Il link porta la chiave.** Si compone qui, da `status.key`, e si mostra
 *   senza: a schermo c'e' solo l'indirizzo, la chiave va negli appunti o nel QR.
 * - **Puo' essere aperto da fuori** (`status.here`): allora spegnere o cambiare
 *   la chiave taglia fuori proprio questa finestra, e lo si chiede prima.
 * - **Il telefono non e' il browser.** «Apri Tailscale» ha senso solo nel guscio
 *   Android (`window.JennyNative`), e il QR solo se la libreria c'e'.
 */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { copyToClipboard, escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { detailDialog } from './shared/dialog.js';

// Mentre il pannello e' aperto si rilegge lo stato: chi si collega, Tailscale
// che cade o torna. Cinque secondi: il giro lato server e' di dieci.
const POLL_MS = 5000;

const TAILSCALE_PACKAGE = 'com.tailscale.ipn';

const t = (key, params) => i18n.t(`settings.remote.${key}`, params);

/** L'host da mettere nell'indirizzo: il nome MagicDNS se c'e', altrimenti l'IP. */
function hostOf(status) {
  const host = status?.name || status?.address;
  if (!host) return null;
  return host.includes(':') ? `[${host}]` : host;
}

/** L'indirizzo da aprire, senza chiave: quello che si legge a schermo. */
export function remoteAddress(status) {
  const host = hostOf(status);
  return host ? `http://${host}:${status.port}` : null;
}

/** Il link intero, chiave compresa: quello che va negli appunti e nel QR.
 *  La chiave sta nel frammento, come per la WebView: non arriva mai al server
 *  in una riga di richiesta, e la SPA la legge e la toglie dall'indirizzo. */
export function remoteLink(status) {
  const base = remoteAddress(status);
  if (!base || !status.key) return null;
  return `${base}/#bs=${encodeURIComponent(status.key)}`;
}

/** La riga in cassetto. */
export function remoteSummary(status) {
  if (!status) return i18n.t('settings.loading');
  if (!status.enabled) return t('summaryOff');
  if (!status.listening) return t('summaryWaiting');
  const peers = status.peers || [];
  if (peers.length === 1 && peers[0].name) return t('summaryOnNamed', { name: peers[0].name });
  if (peers.length) return t('summaryOnMany', { count: peers.length });
  return t('summaryOn');
}

function sinceText(epochS) {
  const time = new Date(epochS * 1000).toLocaleTimeString(i18n.locale, {
    hour: '2-digit',
    minute: '2-digit',
  });
  return t('since', { time });
}

export class RemoteAccessPanel {
  /**
   * @param {HTMLElement} container il corpo del pannello
   * @param {{onStatus?: Function}} opts `onStatus(status)` a ogni stato nuovo:
   *   la riga in cassetto lo segue.
   */
  constructor(container, opts = {}) {
    this.el = container;
    this.onStatus = opts.onStatus || null;
    this.status = null;
    this._qr = false;
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
      status = await rpc.remote('status');
    } catch (e) {
      if (this._destroyed) return;
      this.el.innerHTML = `<div class="settings-empty-state">${escapeHtml(e.message || 'error')}</div>`;
      return;
    }
    if (this._destroyed) return;
    this._show(status);
    this._schedule();
  }

  /* Si ferma da solo quando il pannello si chiude: nessuno lo congeda, e un
     giro di polling a pannello chiuso interrogherebbe il gateway per niente. */
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
    this.status = status;
    this.onStatus?.(status);
    this.render();
  }

  render() {
    const s = this.status;
    if (!s || this._destroyed) return;
    const native = !!window.JennyNative;
    const parts = [];
    if (s.here) {
      parts.push(`<div class="settings-notice remote-here">
        <i class="ti ti-device-laptop" aria-hidden="true"></i><div>${escapeHtml(t('remoteHere'))}</div></div>`);
    } else if (!(s.enabled && this._qr)) {
      parts.push(`<p class="onboarding-desc">${escapeHtml(t('intro'))}</p>`);
    }
    parts.push(`<div class="settings-field settings-toggle-row">
      <label class="settings-label" for="remote-enabled">${escapeHtml(t('toggle'))}</label>
      <label class="toggle-switch">
        <input type="checkbox" id="remote-enabled" ${s.enabled ? 'checked' : ''}>
        <span class="toggle-slider"></span>
      </label></div>`);

    if (!s.enabled) {
      parts.push(s.address
        ? this._status('ok', t('tsUp', { address: s.address }))
        : this._status('off', t('tsUnknown')));
      parts.push(`<p class="remote-hint">${escapeHtml(t('offHint'))}</p>`);
    } else if (!s.listening) {
      /* Tailscale giù con l'opzione accesa: Jenny lo sta riaccendendo da sé
         (`ws_remote.RemoteListener._follow_tailscale`), e lo si dice. Con
         `needs_you` ha smesso: aprire l'app non è bastato, tocca all'utente. */
      const needsYou = s.tailscale === 'needs_you';
      const waiting = needsYou ? t('needsYou')
        : s.tailscale === 'reviving' ? t('reviving', { count: s.revive_attempts })
        : t('waiting');
      parts.push(this._status('warn', s.error ? t('error', { error: s.error }) : waiting));
      if (!s.error) {
        parts.push(`<div class="settings-notice settings-notice-strong remote-ts-down">
          <i class="ti ti-plug-off" aria-hidden="true"></i><div>${escapeHtml(t(needsYou ? 'tsNeedsYou' : 'tsDown'))}</div></div>`);
        if (native) {
          parts.push(`<div class="remote-actions"><button class="settings-btn-add" type="button" data-remote="open-ts">
            <i class="ti ti-external-link" aria-hidden="true"></i> ${escapeHtml(t('openTailscale'))}</button></div>`);
        }
        parts.push(`<p class="remote-hint">${escapeHtml(t('autoResume'))}</p>`);
      }
    } else if (this._qr && remoteLink(s) && window.qrcode) {
      parts.push(`<div class="remote-sub">${escapeHtml(t('scanFrom'))}</div>
        <div class="remote-qr"><div class="remote-qr-box">${this._qrSvg(remoteLink(s))}</div></div>
        <p class="remote-hint remote-hint-center">${escapeHtml(t('scanHint'))}</p>
        <div class="remote-actions"><button class="settings-btn-add" type="button" data-remote="qr">
          <i class="ti ti-eye-off" aria-hidden="true"></i> ${escapeHtml(t('hideQr'))}</button></div>`);
      parts.push(this._peers(s));
    } else {
      parts.push(this._status('ok', t('listening', { address: s.address })));
      parts.push(`<div class="remote-sub">${escapeHtml(t('openFrom'))}</div>
        <code class="remote-addr">${escapeHtml(remoteAddress(s) || '')}</code>
        <div class="remote-actions">
          <button class="settings-btn-add" type="button" data-remote="copy">
            <i class="ti ti-copy" aria-hidden="true"></i> ${escapeHtml(t('copyLink'))}</button>
          ${window.qrcode ? `<button class="settings-btn-add" type="button" data-remote="qr">
            <i class="ti ti-qrcode" aria-hidden="true"></i> ${escapeHtml(t('showQr'))}</button>` : ''}
        </div>
        <p class="remote-hint"><i class="ti ti-key" aria-hidden="true"></i> ${escapeHtml(t('keyHint'))}</p>`);
      parts.push(this._peers(s));
      parts.push(`<div class="remote-actions"><button class="settings-btn-add remote-danger" type="button" data-remote="rotate">
        <i class="ti ti-plug-connected-x" aria-hidden="true"></i> ${escapeHtml(t('disconnectAll'))}</button></div>`);
    }
    this.el.innerHTML = parts.join('');
    this._wire();
  }

  _status(kind, text) {
    return `<div class="remote-status"><span class="remote-dot remote-dot-${kind}"></span>${escapeHtml(text)}</div>`;
  }

  _peers(s) {
    const peers = s.peers || [];
    /* Una riga per dispositivo (il server raggruppa per indirizzo); sotto il
       nome, l'IP e — se piu' d'una — quante finestre ha aperte. */
    const detail = (p) => [p.name ? p.ip : null, p.windows > 1 ? t('windows', { count: p.windows }) : null]
      .filter(Boolean).join(' · ');
    const rows = peers.length
      ? peers.map((p) => `<div class="remote-peer">
          <i class="ti ti-device-laptop" aria-hidden="true"></i>
          <span class="remote-peer-text"><span>${escapeHtml(p.name || p.ip)}</span>
            ${detail(p) ? `<span class="remote-peer-ip">${escapeHtml(detail(p))}</span>` : ''}</span>
          <span class="remote-peer-when">${escapeHtml(sinceText(p.since))}</span>
        </div>`).join('')
      : `<div class="remote-hint">${escapeHtml(t('noneConnected'))}</div>`;
    return `<div class="remote-sub">${escapeHtml(t('connected'))}</div>${rows}`;
  }

  _qrSvg(text) {
    const qr = window.qrcode(0, 'M');
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
  }

  _wire() {
    this.el.querySelector('#remote-enabled')?.addEventListener('change', (e) => this._toggle(e.target));
    for (const btn of this.el.querySelectorAll('[data-remote]')) {
      btn.addEventListener('click', () => this._action(btn.dataset.remote));
    }
  }

  async _toggle(input) {
    const enabled = input.checked;
    if (!enabled && this.status?.here) {
      const choice = await detailDialog({
        title: t('turnOffHereTitle'),
        bodyHtml: `<p style="font-size:13px">${escapeHtml(t('turnOffHereBody'))}</p>`,
        actions: [
          { id: 'cancel', label: i18n.t('common.cancel') },
          { id: 'ok', label: t('turnOffHereConfirm'), variant: 'primary' },
        ],
      });
      if (choice !== 'ok') {
        input.checked = true;
        return;
      }
    }
    await this._run(() => rpc.remote('set', { enabled }), () => { input.checked = !enabled; });
  }

  async _action(what) {
    const s = this.status;
    if (what === 'copy') {
      const link = remoteLink(s);
      if (link && await copyToClipboard(link)) showToast(t('copied'));
    } else if (what === 'qr') {
      this._qr = !this._qr;
      this.render();
    } else if (what === 'open-ts') {
      try {
        await api.launchAndroidApp(TAILSCALE_PACKAGE);
      } catch {
        showToast(t('openTailscaleFailed'), 'error');
      }
    } else if (what === 'rotate') {
      const choice = await detailDialog({
        title: t('disconnectTitle'),
        bodyHtml: `<p style="font-size:13px">${escapeHtml(t(s?.here ? 'disconnectBodyHere' : 'disconnectBody'))}</p>`,
        actions: [
          { id: 'cancel', label: i18n.t('common.cancel') },
          { id: 'ok', label: t('disconnectConfirm'), variant: 'primary' },
        ],
      });
      if (choice !== 'ok') return;
      this._qr = false;
      const done = await this._run(() => rpc.remote('rotate'));
      if (done) showToast(t('disconnectDone'));
    }
  }

  /* Uno alla volta: due tocchi sull'interruttore mentre il primo e' in volo
     manderebbero due `set` in ordine incerto. */
  async _run(call, onError) {
    if (this._busy) return false;
    this._busy = true;
    try {
      const status = await call();
      if (!this._destroyed) this._show(status);
      return true;
    } catch (e) {
      onError?.();
      showToast(t('failed', { error: e.message || 'error' }), 'error');
      return false;
    } finally {
      this._busy = false;
    }
  }
}
