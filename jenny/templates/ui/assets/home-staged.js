/** La casa — le bozze che aspettano un tuo tocco.
 *
 *  Un subagent che ha letto qualcosa da fuori (una pagina, un download) non
 *  scrive da solo i file che Jenny carica senza nessuno davanti: una skill,
 *  `HEARTBEAT.md`, un promemoria, `USER.md`, la memoria. La modifica resta in
 *  bozza (`jenny/agent/staged_writes.py`) e qui compare una scheda nel posto
 *  live della chat: quale file, da dove viene il lavoro, le modifiche, e
 *  **Installa** / **Scarta**. Il tocco e' la conferma: nessun tool del modello
 *  raggiunge `staged.install`, ed e' per questo che non c'e' un secondo
 *  dialogo «sei sicuro?».
 *
 *  Si aggiorna con il frame `staged_changed` e si rilegge all'avvio: le bozze
 *  stanno su disco e sopravvivono a un riavvio.
 */

import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { showToast } from './shared/utils.js';

export class HomeStaged {
  /** @param live    il posto live della chat (`HomeChat.live`).
   *  @param onChange chiamata dopo ogni ridisegno (`chat.liveChanged`). */
  constructor(live, { onChange } = {}) {
    this.live = live;
    this.onChange = onChange;
    this.items = [];
    this.open = new Set();
    this.card = null;
  }

  /** Il frame `staged_changed`: le bozze sono cambiate, si rilegge. */
  handleFrame(msg) {
    if (msg?.event !== 'staged_changed') return;
    this.refresh();
  }

  async refresh() {
    try {
      const out = await rpc.staged('list');
      this.items = Array.isArray(out?.staged) ? out.staged : [];
    } catch (err) {
      console.warn('home.staged: list failed', err);
      this.items = [];
    }
    this._draw();
    return this.items;
  }

  applyTranslations() {
    this._draw();
  }

  _draw() {
    if (!this.live) return;
    if (!this.items.length) {
      this.card?.remove();
      this.card = null;
      this.onChange?.();
      return;
    }
    if (!this.card) {
      this.card = document.createElement('div');
      this.card.className = 'home-work home-staged';
      const typing = this.live.querySelector(':scope > .home-typing');
      this.live.insertBefore(this.card, typing);
    }
    this.card.replaceChildren(this._head(), ...this.items.map((item) => this._row(item)));
    this.onChange?.();
  }

  _head() {
    const head = document.createElement('div');
    head.className = 'home-work-head';
    const icon = document.createElement('i');
    icon.className = 'ti ti-shield-check home-work-icon';
    icon.setAttribute('aria-hidden', 'true');
    const title = document.createElement('span');
    title.className = 'home-work-title';
    title.textContent = i18n.t('home.staged.title', { count: this.items.length });
    head.append(icon, title);
    return head;
  }

  _row(item) {
    const row = document.createElement('div');
    row.className = 'home-staged-row';
    row.dataset.stagedId = item.id;

    const file = document.createElement('div');
    file.className = 'home-staged-file';
    file.textContent = i18n.t(item.new_file ? 'home.staged.newFile' : 'home.staged.file',
      { path: item.target });

    const why = document.createElement('div');
    why.className = 'home-staged-why';
    why.textContent = i18n.t('home.staged.why', {
      sources: (item.sources || []).join(', ') || i18n.t('home.staged.unknownSource'),
    });

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'home-staged-toggle';
    const shown = this.open.has(item.id);
    toggle.textContent = i18n.t(shown ? 'home.staged.hideDiff' : 'home.staged.showDiff');
    toggle.setAttribute('aria-expanded', String(shown));
    toggle.addEventListener('click', () => {
      if (this.open.has(item.id)) this.open.delete(item.id);
      else this.open.add(item.id);
      this._draw();
    });

    row.append(file, why, toggle);
    if (shown) {
      const diff = document.createElement('pre');
      diff.className = 'home-staged-diff';
      diff.textContent = item.diff || '';
      row.appendChild(diff);
      if (item.truncated) {
        const more = document.createElement('div');
        more.className = 'home-staged-why';
        more.textContent = i18n.t('home.staged.truncated');
        row.appendChild(more);
      }
    }

    const actions = document.createElement('div');
    actions.className = 'home-staged-actions';
    const install = document.createElement('button');
    install.type = 'button';
    install.className = 'apps-trash-btn is-primary';
    install.textContent = i18n.t('home.staged.install');
    install.addEventListener('click', () => this._install(item, row));
    const discard = document.createElement('button');
    discard.type = 'button';
    discard.className = 'apps-trash-btn';
    discard.textContent = i18n.t('home.staged.discard');
    discard.addEventListener('click', () => this._discard(item, row));
    actions.append(install, discard);
    row.appendChild(actions);
    return row;
  }

  _busy(row, busy) {
    row.querySelectorAll('button').forEach((b) => { b.disabled = busy; });
  }

  async _install(item, row) {
    this._busy(row, true);
    try {
      await rpc.staged('install', { id: item.id, target_sha256: item.target_sha256 ?? null });
    } catch (err) {
      console.warn('home.staged: install failed', err);
      showToast(i18n.t(err?.code === 'conflict' ? 'home.staged.conflict' : 'home.staged.failed'),
        'error');
      await this.refresh();
      return;
    }
    showToast(i18n.t('home.staged.installed', { path: item.target }), 'success');
    await this.refresh();
  }

  async _discard(item, row) {
    this._busy(row, true);
    try {
      await rpc.staged('discard', { id: item.id });
    } catch (err) {
      console.warn('home.staged: discard failed', err);
      showToast(i18n.t('home.staged.failed'), 'error');
      this._busy(row, false);
      return;
    }
    showToast(i18n.t('home.staged.discarded'), 'info');
    await this.refresh();
  }
}
