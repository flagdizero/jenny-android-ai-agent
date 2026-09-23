/** La scheda di un quaderno — la pressione lunga nella tendina «Con chi parli».
 *
 *  **Una cosa si appende dal posto dove vive.** Le app vivono nel cassetto e la
 *  loro scheda si apre di li'; i quaderni vivono nella tendina del titolo, e
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
 *  E' un `<dialog>` con `showModal()`: sta nel top layer **sopra** la tendina,
 *  che resta aperta sotto. Indietro chiude prima lei (`_closeOverlays`).
 */

import { i18n } from './shared/i18n.js';
import { escapeHtml } from './shared/utils.js';
import { disegnaRiga } from './shared/apps-actions.js';
import { projectKey } from './shared/conversation-list.js';

export class SchedaQuaderno {
  /** @param guscio `{ pagine(), apri(nome), elimina(nome), rinomina?(nome) }`. */
  constructor(guscio) {
    this.guscio = guscio;
    this.foglio = document.getElementById('casa-quaderno-sheet');
  }

  get isOpen() {
    return Boolean(this.foglio?.open);
  }

  close() {
    if (this.isOpen) this.foglio.close();
  }

  /** Le righe per il quaderno *nome*, nell'ordine della scheda di un'app. */
  righe(nome) {
    const chiave = projectKey(nome);
    const stato = this.guscio.pagine?.()?.stato('conversazione', chiave) || null;
    const righe = [{ icon: 'ti-message', label: i18n.t('apps.open'), action: 'open' }];
    if (stato === 'appesa') {
      righe.push({ icon: 'ti-pinned-off', label: i18n.t('apps.unpinPage'), action: 'unpin' });
    } else if (stato) {
      righe.push({
        icon: 'ti-pin',
        label: i18n.t('apps.pinAsPage'),
        action: 'pin',
        ...(stato === 'piena' ? { disabled: true, reason: i18n.t('apps.pageFull') } : {}),
      });
    }
    if (this.guscio.rinomina) {
      righe.push({ icon: 'ti-cursor-text', label: i18n.t('casa.quaderno.rinomina'), action: 'rename' });
    }
    righe.push({ icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true });
    return righe;
  }

  mostra(nome) {
    if (!this.foglio || !nome) return;
    document.getElementById('casa-quaderno-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ti-notebook"></i></div>
        <div class="app-sheet-name">${escapeHtml(nome)}</div>
      </div>`;

    const azioni = document.getElementById('casa-quaderno-sheet-actions');
    azioni.innerHTML = this.righe(nome).map(disegnaRiga).join('');
    azioni.querySelectorAll('.oc-sheet-action').forEach((b) => {
      b.addEventListener('click', async (e) => {
        e.stopPropagation();
        this.close();
        await this.fai(b.dataset.action, nome);
      });
    });

    const annulla = document.getElementById('casa-quaderno-sheet-cancel');
    if (annulla) {
      annulla.textContent = i18n.t('common.cancel');
      annulla.onclick = () => this.close();
    }
    /* Il tocco sintetico che segue una pressione lunga arriva sul velo e
       chiuderebbe subito la scheda appena aperta: per un attimo lo si ignora,
       come fa la scheda di un'app. */
    const aperta = Date.now();
    this.foglio.onclick = (e) => {
      if (e.target === this.foglio && Date.now() - aperta > 400) this.close();
    };
    this.foglio.showModal();
  }

  /** Cosa fa ogni riga: chiede al guscio. */
  async fai(azione, nome) {
    const chiave = projectKey(nome);
    const pagine = this.guscio.pagine?.();
    if (azione === 'open') return this.guscio.apri(nome);
    if (azione === 'pin') return pagine?.appendi('conversazione', chiave);
    if (azione === 'unpin') return pagine?.stacca('conversazione', chiave);
    if (azione === 'rename') return this.guscio.rinomina?.(nome);
    if (azione === 'delete') return this.guscio.elimina(nome);
    return undefined;
  }
}
