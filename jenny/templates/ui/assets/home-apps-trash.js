/** La casa — il cestino delle app, in fondo alla pagina App.
 *
 *  «Elimina» su un'app non cancella piu' niente: la sposta nel cestino, dati
 *  compresi, con un Annulla di qualche secondo (v. `shared/apps-actions.js`).
 *  Passato l'Annulla, l'app si ritrova qui: una riga in fondo alla griglia,
 *  che c'e' solo quando il cestino non e' vuoto, apre un foglio con le voci —
 *  **Rimetti** e **Elimina per sempre**. Solo la seconda chiede conferma, e la
 *  domanda dice quanti dati se ne vanno: e' l'unico punto delle app da cui non
 *  si torna indietro.
 */

import { rpc } from './shared/rpc-client.js';
import { i18n } from './shared/i18n.js';
import { confirmDialog } from './shared/dialog.js';
import { showToast } from './shared/utils.js';

export class HomeAppsTrash {
  /** @param source  `AppsSource`: dopo un ripristino la griglia si ricarica. */
  constructor(source) {
    this.source = source;
    this.button = document.getElementById('launcher-trash');
    this.dialog = document.getElementById('apps-trash-sheet');
    this.titleEl = document.getElementById('apps-trash-sheet-title');
    this.body = document.getElementById('apps-trash-sheet-body');
    this.entries = [];
    this.button?.addEventListener('click', () => this.open());
    document.getElementById('apps-trash-sheet-close')
      ?.addEventListener('click', () => this.close());
    this.dialog?.addEventListener('click', (e) => {
      if (e.target === this.dialog) this.close();
    });
    /* Un'app eliminata cambia l'elenco: il conto del cestino va riletto. */
    source?.addChangeListener?.(() => this.refresh());
  }

  get isOpen() {
    return Boolean(this.dialog?.open);
  }

  close() {
    if (this.dialog?.open) this.dialog.close();
  }

  /** Rilegge il cestino e aggiorna la riga in fondo alla griglia. */
  async refresh() {
    try {
      const out = await rpc.apps('trash');
      this.entries = Array.isArray(out?.trash) ? out.trash : [];
    } catch (err) {
      console.warn('home.apps-trash: list failed', err);
      this.entries = [];
    }
    this._drawButton();
    if (this.isOpen) this._drawBody();
    return this.entries;
  }

  applyTranslations() {
    this._drawButton();
    if (this.isOpen) this._drawBody();
  }

  _drawButton() {
    if (!this.button) return;
    const n = this.entries.length;
    this.button.hidden = !n;
    if (!n) return;
    this.button.innerHTML = '';
    const icon = document.createElement('i');
    icon.className = 'ti ti-trash';
    icon.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = i18n.t('apps.trashRow', { count: n });
    this.button.append(icon, text);
  }

  async open() {
    if (!this.dialog) return;
    await this.refresh();
    this._drawBody();
    if (!this.dialog.open) this.dialog.showModal();
  }

  _drawBody() {
    if (!this.body) return;
    if (this.titleEl) this.titleEl.textContent = i18n.t('apps.trashTitle');
    /* La casa non applica i `data-i18n` dell'HTML: senza, restava «Chiudi». */
    const close = document.getElementById('apps-trash-sheet-close');
    if (close) close.textContent = i18n.t('common.close');
    this.body.innerHTML = '';
    if (!this.entries.length) {
      const p = document.createElement('p');
      p.className = 'apps-trash-empty';
      p.textContent = i18n.t('apps.trashEmpty');
      this.body.appendChild(p);
      return;
    }
    for (const entry of this.entries) this.body.appendChild(this._row(entry));
  }

  _row(entry) {
    const row = document.createElement('div');
    row.className = 'apps-trash-row';
    const text = document.createElement('div');
    text.className = 'apps-trash-text';
    const name = document.createElement('span');
    name.className = 'apps-trash-name';
    name.textContent = entry.name || entry.slug;
    const meta = document.createElement('span');
    meta.className = 'apps-trash-meta';
    meta.textContent = i18n.t(entry.records ? 'apps.trashRecords' : 'apps.trashNoData',
      { count: entry.records || 0, when: this._when(entry.at) });
    text.append(name, meta);
    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'apps-trash-btn is-primary';
    restore.textContent = i18n.t('apps.restore');
    restore.addEventListener('click', () => this._restore(entry, row));
    const purge = document.createElement('button');
    purge.type = 'button';
    purge.className = 'apps-trash-btn is-danger';
    purge.textContent = i18n.t('apps.purge');
    purge.addEventListener('click', () => this._purge(entry, row));
    row.append(text, restore, purge);
    return row;
  }

  _when(at) {
    const d = at ? new Date(at) : null;
    if (!d || Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short' });
  }

  async _restore(entry, row) {
    row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      await rpc.apps('restore', { id: entry.id });
    } catch (err) {
      console.warn('home.apps-trash: restore failed', err);
      showToast(i18n.t(err?.code === 'conflict' ? 'apps.restoreTaken' : 'apps.restoreFailed'),
        'error');
      row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      return;
    }
    showToast(i18n.t('apps.restored', { name: entry.name || entry.slug }), 'success');
    await this.source?.loadJennyApps?.();
    await this.refresh();
    if (!this.entries.length) this.close();
  }

  async _purge(entry, row) {
    const ok = await confirmDialog(i18n.t(entry.records ? 'apps.purgeConfirmData'
      : 'apps.purgeConfirm', { name: entry.name || entry.slug, count: entry.records || 0 }),
    i18n.t('apps.purge'), null, { danger: true });
    if (!ok) return;
    row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      await rpc.apps('purge', { id: entry.id });
    } catch (err) {
      console.warn('home.apps-trash: purge failed', err);
      showToast(i18n.t('apps.purgeFailed'), 'error');
      row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      return;
    }
    await this.refresh();
    if (!this.entries.length) this.close();
  }
}
