/** La scheda di un quaderno — la pressione lunga nella pagina Quaderni.
 *
 *  **Una cosa si appende dal posto dove vive.** Le app vivono nella pagina App
 *  e la loro scheda si apre di li'; i quaderni vivono nella pagina Quaderni, e
 *  questa e' la loro. Le due hanno le stesse righe nello stesso ordine e lo
 *  stesso aspetto — Apri · Metti come pagina · Rinomina · Elimina — perche'
 *  chi ha imparato una ha imparato l'altra (`.agent/pagine-dal-posto-plan.md`).
 *
 *  «Rinomina» e non «Modifica»: la coerenza sta nella struttura, non nel
 *  costringere la stessa parola su due atti diversi. Modificare un'app vuol
 *  dire chiedere a Jenny di cambiarla; su un quaderno l'atto e' cambiargli
 *  nome, e chiamarlo col suo nome e' piu' onesto. La riga c'e' solo se il
 *  guscio sa rinominare.
 *
 *  La scheda non fa niente da se': chiede al guscio. Qui c'e' solo il disegno
 *  e la scelta di quale riga, come in `shared/apps-actions.js`.
 *
 *  E' un `<dialog>` con `showModal()`: sta nel top layer **sopra** la pagina
 *  Quaderni. Indietro chiude prima lei (`_closeOverlays`).
 */

import { i18n } from './shared/i18n.js';
import { escapeHtml } from './shared/utils.js';
import { drawRow } from './shared/apps-actions.js';
import { projectKey } from './shared/conversation-list.js';

export class NotebookCard {
  /** @param guscio `{ homePages(), open(name), delete(name), rename?(name) }`. */
  constructor(shell) {
    this.shell = shell;
    this.sheet = document.getElementById('home-notebook-sheet');
  }

  get isOpen() {
    return Boolean(this.sheet?.open);
  }

  close() {
    if (this.isOpen) this.sheet.close();
  }

  /** Le righe per il quaderno *nome*, nell'ordine della scheda di un'app. */
  rows(name) {
    const key = projectKey(name);
    const state = this.shell.homePages?.()?.state('conversation', key) || null;
    const rows = [{ icon: 'ti-message', label: i18n.t('apps.open'), action: 'open' }];
    if (state === 'pending') {
      rows.push({ icon: 'ti-pinned-off', label: i18n.t('apps.unpinPage'), action: 'unpin' });
    } else if (state) {
      rows.push({
        icon: 'ti-pin',
        label: i18n.t('apps.pinAsPage'),
        action: 'pin',
        ...(state === 'piena' ? { disabled: true, reason: i18n.t('apps.pageFull') } : {}),
      });
    }
    if (this.shell.rename) {
      rows.push({ icon: 'ti-cursor-text', label: i18n.t('home.notebook.rename'), action: 'rename' });
    }
    rows.push({ icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true });
    return rows;
  }

  show(name) {
    if (!this.sheet || !name) return;
    document.getElementById('home-notebook-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ti-notebook"></i></div>
        <div class="app-sheet-name">${escapeHtml(name)}</div>
      </div>`;

    const actions = document.getElementById('home-notebook-sheet-actions');
    actions.innerHTML = this.rows(name).map(drawRow).join('');
    actions.querySelectorAll('.oc-sheet-action').forEach((b) => {
      b.addEventListener('click', async (e) => {
        e.stopPropagation();
        this.close();
        await this.perform(b.dataset.action, name);
      });
    });

    const cancel = document.getElementById('home-notebook-sheet-cancel');
    if (cancel) {
      cancel.textContent = i18n.t('common.cancel');
      cancel.onclick = () => this.close();
    }
    /* Il tocco sintetico che segue una pressione lunga arriva sul velo e
       chiuderebbe subito la scheda appena aperta: per un attimo lo si ignora,
       come fa la scheda di un'app. */
    const open = Date.now();
    this.sheet.onclick = (e) => {
      if (e.target === this.sheet && Date.now() - open > 400) this.close();
    };
    this.sheet.showModal();
  }

  /** Cosa fa ogni riga: chiede al guscio. */
  async perform(action, name) {
    const key = projectKey(name);
    const homePages = this.shell.homePages?.();
    if (action === 'open') return this.shell.open(name);
    if (action === 'pin') return homePages?.append('conversation', key);
    if (action === 'unpin') return homePages?.detach('conversation', key);
    if (action === 'rename') return this.shell.rename?.(name);
    if (action === 'delete') return this.shell.delete(name);
    return undefined;
  }
}
