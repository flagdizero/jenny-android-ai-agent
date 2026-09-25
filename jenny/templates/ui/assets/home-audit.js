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
 *  **Niente gravita'.** Il formato aveva quattro livelli — info/suggest/warn/
 *  error — che chi segnalava sceglieva prima di scrivere. Sono usciti dal
 *  formato il 22/09/2026 (v. la docstring di `webui/audit.py::AuditEntry`):
 *  erano un campo da coda di smistamento, e qui chi segnala e chi corregge sono
 *  la stessa persona. Un menu' che chiede di dare un voto alla propria
 *  lamentela e' la cosa piu' innaturale che ci fosse in questa schermata.
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

/** Tetto sul commento, e **non e' una misura di stile**: il commento viaggia
 *  nella query string, cioe' nella riga di richiesta, dove `websockets` ne
 *  ammette 8192 byte in tutto. Un'emoji percent-encodata ne costa 12, quindi
 *  500 caratteri restano al sicuro anche nel caso peggiore. Oltre non e' piu'
 *  un riscontro ancorato a una frase, e' una pagina — e quella si scrive. */
const MAX_COMMENT = 500;

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

/** Il messaggio che la casa manda in chat dopo aver segnalato.
 *
 *  Tre pezzi, e ognuno serve a una cosa sola:
 *
 *    - il **titolo**, perche' lei sappia quale pagina senza aprire il file;
 *    - la **citazione**, perche' la rivedi tu nella tua cronologia — ed e' il
 *      modo in cui un umano dice «questa frase qui»;
 *    - **l'id**, perche' e' l'unica cosa che le permette di *chiudere* la
 *      segnalazione quando ha finito. Senza, corregge e il file resta aperto
 *      per sempre. E' l'unico pezzo di vocabolario del file che passa, e passa
 *      fra parentesi.
 *
 *  I segnaposto li riempie `i18n.t`, che sostituisce con una funzione: un
 *  `.replace(stringa, testo)` interpreta `$$`, `$&` e `$'` dentro il testo, e
 *  una formula citata (`$$E = mc^2$$`) arrivava a Jenny storpiata. Una passata
 *  sola, anche: un titolo che contiene «{quote}» resta com'e'.
 */
export function messaggioSegnalazione({ title, quote, comment, id }) {
  const testa = i18n.t('home.audit.msgHead', {
    page: String(title || ''),
    quote: String(quote || '').trim(),
  });
  const coda = id ? `\n(${i18n.t('home.audit.msgRef')} ${id})` : '';
  return `${testa}\n${String(comment || '').trim()}${coda}`;
}

export class HomeAudit {
  constructor(reader) {
    this.reader = reader;
    /** Chi porta la segnalazione in chat. Sta fuori perche' questa classe non
     *  sa niente di stanze ne' di sessioni: sa solo che e' stata depositata. */
    this.onFiled = null;
    this.barEl = document.getElementById('home-sel-bar');
    this.openBtn = document.getElementById('home-sel-report');
    this.dialog = document.getElementById('home-audit-dialog');
    this.quoteEl = document.getElementById('home-audit-quote');
    this.commentEl = document.getElementById('home-audit-comment');
    this.sendBtn = document.getElementById('home-audit-send');
    this.cancelBtn = document.getElementById('home-audit-cancel');
    this.titleEl = document.getElementById('home-audit-title');
    /** Il testo su cui si e' aperto il foglio: la selezione sparisce appena il
     *  dialogo prende il fuoco, quindi va copiata adesso. */
    this._selected = '';

    this.openBtn?.addEventListener('click', () => this.open());
    this.cancelBtn?.addEventListener('click', () => this.dialog?.close());
    this.sendBtn?.addEventListener('click', () => this.send());
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
    if (this.openBtn) this.openBtn.textContent = i18n.t('home.audit.report');
    if (this.titleEl) this.titleEl.textContent = i18n.t('home.audit.title');
    if (this.sendBtn) this.sendBtn.textContent = i18n.t('home.audit.send');
    if (this.cancelBtn) this.cancelBtn.textContent = i18n.t('home.audit.cancel');
    if (this.commentEl) this.commentEl.placeholder = i18n.t('home.audit.placeholder');
  }

  /** Apre il foglio sul testo scelto.
   *
   *  L'ancora si controlla **qui**, non solo all'invio: un testo scelto a
   *  cavallo di un grassetto, o ripetuto nella pagina, non si puo' ancorare,
   *  e scoprirlo dopo aver scritto il commento vuol dire buttarlo via. Lo si
   *  dice subito, e il foglio non si apre. */
  open() {
    const selected = String(document.getSelection() || '');
    if (!selected.trim()) return;
    const spot = offsetsIn(this.reader?.raw, selected);
    if (!spot.ok) {
      showToast(i18n.t(`home.audit.${spot.reason}`), 'error');
      return;
    }
    this._selected = selected;
    if (this.quoteEl) this.quoteEl.textContent = selected;
    if (this.commentEl) this.commentEl.value = '';
    /* La selezione si chiude adesso: aperto il dialogo il fuoco va li' dentro e
       i manici resterebbero appesi sopra una pagina che non si tocca piu'. */
    document.getSelection()?.removeAllRanges();
    this.refresh();
    this.dialog?.showModal();
    this.commentEl?.focus();
  }

  /* Un secondo tocco su Invia mentre il primo sta andando non fa niente: ogni
     invio crea un file, e due tocchi facevano due segnalazioni uguali — e due
     messaggi in chat. */
  async send() {
    if (this._sending) return;
    this._sending = true;
    try {
      await this._send();
    } finally {
      this._sending = false;
    }
  }

  async _send() {
    const comment = (this.commentEl?.value || '').trim();
    if (!comment) {
      showToast(i18n.t('home.audit.needComment'), 'info');
      return;
    }
    const spot = offsetsIn(this.reader?.raw, this._selected);
    if (!spot.ok) {
      showToast(i18n.t(`home.audit.${spot.reason}`), 'error');
      return;
    }
    let creata;
    try {
      creata = await api.createAudit({
        wiki: this.reader.notebook,
        target: this.reader.path,
        selStart: spot.start,
        selEnd: spot.end,
        comment,
      });
    } catch (err) {
      console.warn('casa.audit: report not filed', err);
      showToast(i18n.t('home.audit.failed'), 'error');
      return;
    }
    this.dialog?.close();
    this.onFiled?.({
      title: this.reader.title || this.reader.path,
      quote: this._selected,
      comment,
      id: creata?.id || '',
    });
  }
}
