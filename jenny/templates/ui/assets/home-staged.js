/** La casa — le bozze che aspettano un tuo tocco.
 *
 *  Un subagent che ha letto qualcosa da fuori (una pagina, un download) non
 *  scrive da solo i file che Jenny carica senza nessuno davanti: una skill,
 *  un promemoria, `USER.md`, la memoria. La modifica resta in
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

/* Le fonti che il registro della contaminazione scrive ma che all'utente non
   dicono niente: i risultati di un tool che Jenny ha salvato in
   `.jenny/tool-results/` dentro un quaderno (o nel workspace). Sono copie di
   cose gia' elencate, con nomi come `call_x.txt`, e in fila erano proprio il
   percorso lungo che usciva dal bordo della scheda. */
const INTERNAL_SOURCE = /^file:(?:.*\/)?\.jenny\/tool-results\//;
/* Quante fonti si nominano: oltre, «e altre N». */
const SOURCES_SHOWN = 3;

/** Le fonti di una bozza come si leggono: le prime tre, senza quelle interne,
 *  e «e altre N». La usa anche la scheda del piano (`home-plan.js`), che
 *  mostra le bozze di un passo con le stesse parole. */
export function stagedSources(sources) {
  const all = (Array.isArray(sources) ? sources : []).map(String);
  const shown = all.filter((s) => !INTERNAL_SOURCE.test(s));
  if (!shown.length) {
    /* Solo fonti interne: c'erano, quindi «sconosciuta» direbbe il falso. */
    return i18n.t(all.length ? 'home.staged.toolResults' : 'home.staged.unknownSource');
  }
  const head = shown.slice(0, SOURCES_SHOWN).join(', ');
  if (shown.length <= SOURCES_SHOWN) return head;
  return i18n.t('home.staged.moreSources', { list: head, count: shown.length - SOURCES_SHOWN });
}

export class HomeStaged {
  /** @param live    il posto live della chat (`HomeChat.live`).
   *  @param onChange chiamata dopo ogni ridisegno (`chat.liveChanged`).
   *  @param onItems  chiamata quando l'elenco riletto e' arrivato: la scheda
   *                  del piano mostra le bozze di un passo da qui.
   *  @param hidden   `() => Set` degli id che un'altra scheda a schermo sta
   *                  gia' mostrando (le bozze di un piano fermo). */
  constructor(live, { onChange, onItems, hidden } = {}) {
    this.live = live;
    this.onChange = onChange;
    this.onItems = onItems;
    this.hidden = hidden || (() => null);
    /** Tutte le bozze in attesa, anche quelle che questa scheda non mostra. */
    this.items = [];
    /** L'ultima lettura e' fallita: «nessuna bozza» qui non vuol dire che non
     *  ce ne sono. La scheda del piano lo distingue (v. `HomePlan._draftsBox`). */
    this.failed = false;
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
      this.failed = false;
    } catch (err) {
      console.warn('home.staged: list failed', err);
      this.items = [];
      this.failed = true;
    }
    this._draw();
    this.onItems?.(this.items);
    return this.items;
  }

  applyTranslations() {
    this._draw();
  }

  /** Ridisegna senza rileggere: e' cambiato chi mostra cosa, non le bozze. */
  redraw() {
    this._draw();
  }

  /* Le bozze che questa scheda mostra. Quelle di un piano fermo stanno nella
     scheda del piano, quando e' a schermo: lo stesso Installa in due schede
     con parole diverse confondeva, e deciso in una l'altra sobbalzava. Nelle
     altre chat restano qui, dove si possono decidere lo stesso (il piano
     riparte da solo comunque). */
  _shownItems() {
    const hidden = this.hidden();
    if (!hidden?.size) return this.items;
    return this.items.filter((item) => !hidden.has(item.id));
  }

  _draw() {
    if (!this.live) return;
    const items = this._shownItems();
    if (!items.length) {
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
    this.card.replaceChildren(this._head(items.length), ...items.map((item) => this._row(item)));
    this.onChange?.();
  }

  _head(count) {
    const head = document.createElement('div');
    head.className = 'home-work-head';
    const icon = document.createElement('i');
    icon.className = 'ti ti-shield-check home-work-icon';
    icon.setAttribute('aria-hidden', 'true');
    const title = document.createElement('span');
    title.className = 'home-work-title';
    title.textContent = i18n.t('home.staged.title', { count });
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
    why.textContent = i18n.t('home.staged.why', { sources: stagedSources(item.sources) });

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
