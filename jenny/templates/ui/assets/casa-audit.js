/** La casa — «Segnala»: un riscontro ancorato a un punto della pagina.
 *
 *  **Non e' un doppione di «Modifica»**, e la differenza e' il verbo:
 *
 *    - Modifica  = *lo aggiusto io adesso*. Un refuso, una riga storta.
 *    - Segnala   = *e' sbagliato nel merito, aggiustalo tu* — e resta scritto
 *                  dove il linter lo vede, ancorato al punto esatto.
 *
 *  Il giro degli audit e' vivo da entrambi i lati tranne uno: Jenny li legge a
 *  inizio passata (`scripts/audit_review.py`), il linter li controlla
 *  (`scripts/lint_wiki.py`), ogni quaderno ha le sue cartelle. Dal 21/09/2026,
 *  quando la selezione del testo e' uscita dal lettore, mancava solo il modo di
 *  metterci qualcosa: il canale c'era ed era muto.
 *
 *  **Il server fa quasi tutto da solo.** `/api/audit/create` si rilegge il
 *  markdown dal disco e calcola le tre ancore (`anchor_before`/`anchor_text`/
 *  `anchor_after`). Il vecchio client gli mandava anche `rawMarkdown` e la
 *  rotta lo ignorava: qui non si manda.
 */

import { api } from './shared/api-client.js';
import { showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { selectionInside, onSelectionChange } from './shared/selection.js';

/** Le quattro gravita' che il formato prevede (v. `references/audit-guide.md`). */
export const SEVERITIES = ['info', 'suggest', 'warn', 'error'];

/** Tetto sul commento, e **non e' una misura di stile**: il commento viaggia
 *  nella query string, cioe' nella riga di richiesta, dove `websockets` ne
 *  ammette 8192 byte in tutto. Un'emoji percent-encodata ne costa 12, quindi
 *  500 caratteri restano al sicuro anche nel caso peggiore. Oltre non e' piu'
 *  un riscontro ancorato a una frase, e' una pagina — e quella si scrive. */
export const MAX_COMMENT = 500;

/** Da un testo selezionato ai due offset nel **markdown sorgente**.
 *
 *  `selStart`/`selEnd` sono posizioni nel `.md`, ma la selezione avviene nel
 *  reso. Qui non serve ricostruire la mappa fra i due — il sorgente e' gia' in
 *  mano (`/api/page` manda `raw`) — basta ritrovarci dentro il testo scelto.
 *
 *  **Con un controllo di unicita'.** Se quel testo compare piu' di una volta
 *  l'ancora e' ambigua, e allora si dice invece di ancorare alla cieca: un
 *  commento attaccato al punto sbagliato e' peggio di un commento non scritto,
 *  perche' nessuno dei due lati se ne accorge. Era gia' la scelta del vecchio
 *  codice dell'officina.
 *
 *  @returns `{ok: true, start, end}` | `{ok: false, reason: 'empty'|'notFound'|'ambiguous'}`
 */
export function offsetsIn(raw, selected) {
  const needle = String(selected || '');
  if (!needle.trim()) return { ok: false, reason: 'empty' };
  const hay = String(raw || '');
  const first = hay.indexOf(needle);
  if (first < 0) return { ok: false, reason: 'notFound' };
  if (hay.indexOf(needle, first + 1) >= 0) return { ok: false, reason: 'ambiguous' };
  return { ok: true, start: first, end: first + needle.length };
}

export class CasaAudit {
  constructor(reader) {
    this.reader = reader;
    this.barEl = document.getElementById('casa-sel-bar');
    this.openBtn = document.getElementById('casa-sel-report');
    this.dialog = document.getElementById('casa-audit-dialog');
    this.quoteEl = document.getElementById('casa-audit-quote');
    this.commentEl = document.getElementById('casa-audit-comment');
    this.sevEl = document.getElementById('casa-audit-sev');
    this.sendBtn = document.getElementById('casa-audit-send');
    this.cancelBtn = document.getElementById('casa-audit-cancel');
    this.titleEl = document.getElementById('casa-audit-title');
    /** Il testo su cui si e' aperto il foglio: la selezione sparisce appena il
     *  dialogo prende il fuoco, quindi va copiata adesso. */
    this._selected = '';
    this._severity = 'warn';

    this.openBtn?.addEventListener('click', () => this.open());
    this.cancelBtn?.addEventListener('click', () => this.dialog?.close());
    this.sendBtn?.addEventListener('click', () => this.send());
    this.sevEl?.addEventListener('click', (e) => this._pickSeverity(e));
    if (this.commentEl) this.commentEl.maxLength = MAX_COMMENT;
    onSelectionChange(() => this.refresh());
  }

  /** La barra compare quando c'e' del testo scelto **dentro la pagina**, e
   *  solo se non si sta gia' modificando: li' il gesto e' un altro. */
  refresh() {
    if (!this.barEl) return;
    const attivo = !this.reader?.editing
      && selectionInside(this.reader?.bodyEl)
      && !!this.reader?.raw;
    this.barEl.hidden = !attivo;
  }

  applyTranslations() {
    if (this.openBtn) this.openBtn.textContent = i18n.t('casa.audit.report');
    if (this.titleEl) this.titleEl.textContent = i18n.t('casa.audit.title');
    if (this.sendBtn) this.sendBtn.textContent = i18n.t('casa.audit.send');
    if (this.cancelBtn) this.cancelBtn.textContent = i18n.t('casa.audit.cancel');
    if (this.commentEl) this.commentEl.placeholder = i18n.t('casa.audit.placeholder');
    this._renderSeverities();
  }

  _renderSeverities() {
    if (!this.sevEl) return;
    this.sevEl.innerHTML = SEVERITIES.map((s) => (
      `<button class="casa-audit-chip${s === this._severity ? ' is-on' : ''}" `
      + `type="button" data-sev="${s}">${i18n.t(`casa.audit.sev.${s}`)}</button>`
    )).join('');
  }

  _pickSeverity(e) {
    const chip = e.target.closest('[data-sev]');
    if (!chip) return;
    this._severity = chip.dataset.sev;
    this._renderSeverities();
  }

  /** Apre il foglio sul testo scelto. */
  open() {
    const selected = String(document.getSelection() || '');
    if (!selected.trim()) return;
    this._selected = selected;
    this._severity = 'warn';
    if (this.quoteEl) this.quoteEl.textContent = selected;
    if (this.commentEl) this.commentEl.value = '';
    this._renderSeverities();
    /* La selezione si chiude adesso: aperto il dialogo il fuoco va li' dentro e
       i manici resterebbero appesi sopra una pagina che non si tocca piu'. */
    document.getSelection()?.removeAllRanges();
    this.refresh();
    this.dialog?.showModal();
    this.commentEl?.focus();
  }

  async send() {
    const comment = (this.commentEl?.value || '').trim();
    if (!comment) {
      showToast(i18n.t('casa.audit.needComment'), 'info');
      return;
    }
    const spot = offsetsIn(this.reader?.raw, this._selected);
    if (!spot.ok) {
      showToast(i18n.t(`casa.audit.${spot.reason}`), 'error');
      return;
    }
    try {
      await api.createAudit({
        wiki: this.reader.notebook,
        target: this.reader.path,
        selStart: spot.start,
        selEnd: spot.end,
        comment,
        severity: this._severity,
      });
    } catch (err) {
      console.warn('casa.audit: invio fallito', err);
      showToast(i18n.t('casa.audit.failed'), 'error');
      return;
    }
    this.dialog?.close();
    showToast(i18n.t('casa.audit.sent'), 'success');
  }
}
