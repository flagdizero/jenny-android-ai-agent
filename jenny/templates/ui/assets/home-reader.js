/** La casa — una pagina del quaderno, letta.
 *
 *  Il markdown lo rende gia' il server (`/api/page`), quindi qui non c'e' un
 *  secondo renderer: c'e' la sanificazione, e i collegamenti.
 *
 *  **Quel che la casa non porta dentro.** L'officina ha 746 righe attorno alla
 *  stessa risposta: audit, briciole di pane, LaTeX a richiesta, il pannello dei
 *  riscontri, la risoluzione dei link relativi fra wiki diverse. Sono arnesi da
 *  operatore, e l'unica parte che serve a chi legge una nota che ha scritto lui
 *  e' la prima: **il testo, e i wikilink che portano a un'altra pagina dello
 *  stesso quaderno.**
 *
 *  Un collegamento fuori dal quaderno non si finge apribile: lo dice. E' la
 *  stessa frase che usa l'officina (`common.linkNotOpenable`), perche' e' la
 *  stessa verita' detta da due parti.
 *
 *  **Modifica.** Le pagine sono `.md` nel workspace e gia' oggi si potevano
 *  cambiare — dal gestore file, in officina, sei tocchi e un altro guscio, dal
 *  lato opposto del telefono rispetto a dove ti accorgi dell'errore. Da qui e'
 *  una textarea col markdown che `/api/page` ha gia' mandato (`raw`), quindi
 *  senza una seconda richiesta.
 *
 *  Il salvataggio non e' `workspace.write`: passa da `page.write`, che risolve
 *  il percorso lato server. E porta con se' il testo da cui si e' partiti,
 *  perche' **queste pagine le scrive anche Jenny**: se il file e' cambiato
 *  sotto, il server risponde `conflict` e non scrive. Salvare a occhi chiusi
 *  qui vuol dire cancellarle il lavoro senza che nessuno se ne accorga.
 */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { confirmDialog } from './shared/dialog.js';
import { renderRich } from './shared/rich-content.js';

/** Un link markdown relativo risolto contro la pagina che lo contiene.
 *
 *  `[nota](note.md)` dentro `concepts/orto.md` e' `concepts/note.md`. Torna
 *  `null` per tutto cio' che non e' una pagina di questo quaderno: percorsi
 *  assoluti, risalite (`..`), schemi (`http:`, `mailto:`), e qualunque cosa non
 *  finisca in `.md`.
 *
 *  La risalita si rifiuta qui e non si normalizza: il server ha la sua guardia
 *  (`safe_wiki_page_path`), e due guardie che normalizzano in modo diverso sono
 *  il modo classico di aprircisi un buco in mezzo.
 */
export function resolveRelativePage(currentPath, href) {
  const clean = String(href || '').split('#')[0].split('?')[0];
  if (!clean || !/\.md$/i.test(clean)) return null;
  if (clean.startsWith('/') || clean.includes('..')) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(clean)) return null;
  const dir = String(currentPath || '').replace(/\\/g, '/').split('/').slice(0, -1);
  const rel = clean.replace(/^\.\//, '');
  return [...dir, ...rel.split('/')].filter(Boolean).join('/');
}

/** Dove porta un collegamento dentro la pagina.
 *
 *  Tre uscite, e la terza e' la piu' importante: `null` vuol dire «non da qui»,
 *  e chi chiama lo deve **dire**, non ignorare.
 *
 *  @returns `{kind:'page', path}` | `{kind:'external', href}` |
 *           `{kind:'hash', id}` | `null`
 */
export function linkTarget({ href, wikilink, notebook, currentPath }) {
  const raw = String(href || '');
  if (!raw) return null;
  if (raw.startsWith('#')) return { kind: 'hash', id: raw.slice(1) };

  if (/^(https?:)\/\//i.test(raw)) return { kind: 'external', href: raw };
  if (/^(mailto:|tel:)/i.test(raw)) return { kind: 'external', href: raw };

  /* I wikilink il renderer li scrive come `?wiki=<name>&page=<path>`. Un
     wikilink verso un **altro** quaderno non si apre da qui: in casa una
     pagina appartiene alla conversazione in cui sei, e saltare in un'altra
     stanza senza dirlo sarebbe il tipo di scorciatoia che poi non si sa piu'
     come disfare. */
  if (wikilink && raw.includes('page=')) {
    let url = null;
    try {
      url = new URL(raw, 'https://jenny.invalid/');
    } catch (_) {
      return null;
    }
    const wiki = url.searchParams.get('wiki');
    const page = url.searchParams.get('page');
    if (!page || (wiki && wiki !== notebook)) return null;
    return { kind: 'page', path: page };
  }

  const rel = resolveRelativePage(currentPath, raw);
  return rel ? { kind: 'page', path: rel } : null;
}

export class HomeReader {
  constructor() {
    this.el = document.getElementById('home-reader');
    this.bodyEl = document.getElementById('home-reader-body');
    this.editEl = document.getElementById('home-reader-edit');
    this.barEl = document.getElementById('home-reader-bar');
    this.saveBtn = document.getElementById('home-reader-save');
    this.cancelBtn = document.getElementById('home-reader-cancel');
    this.notebook = null;
    this.path = null;
    this.title = '';
    /** Il markdown sorgente della pagina a schermo: e' quel che l'editor apre,
     *  ed e' il `base` che il salvataggio manda al server. */
    this.raw = '';
    this.editing = false;
    /** Chi disegna l'intestazione: entrare e uscire dall'editor cambia quali
     *  comandi valgono lassu'. */
    this.onEditing = null;
    /* Come per l'elenco: due pagine di fila, e solo l'ultima disegna. */
    this._token = 0;
    this.bodyEl?.addEventListener('click', (e) => this._onClick(e));
    this.saveBtn?.addEventListener('click', () => this.save());
    this.cancelBtn?.addEventListener('click', () => this.askCancel());
  }

  /** Le parole dei due bottoni in basso. Chiamata all'avvio e a ogni cambio
   *  di lingua: sono scritte solo qui, mai nell'HTML. */
  applyTranslations() {
    if (this.saveBtn) this.saveBtn.textContent = i18n.t('home.reader.save');
    if (this.cancelBtn) this.cancelBtn.textContent = i18n.t('home.reader.cancel');
  }

  /* ── Modifica ── */

  /** Vero se c'e' un testo aperto e cambiato. Lo chiede chi sta per uscire. */
  isDirty() {
    return this.editing && this.editEl?.value !== this.raw;
  }

  /** Apre l'editor sul markdown gia' in mano. Falso se non c'e' niente da aprire. */
  startEdit() {
    if (this.editing || !this.path || !this.editEl) return false;
    this.editing = true;
    this.editEl.value = this.raw;
    this.editEl.hidden = false;
    this.barEl.hidden = false;
    this.bodyEl.hidden = true;
    this.editEl.focus();
    /* **Dall'inizio, non dalla fine.** `focus()` su una textarea porta il
       cursore in fondo al testo e ci trascina la vista: aperta la modifica su
       una pagina di trenta righe ti ritrovavi in coda, lontano dal punto che
       stavi leggendo. Visto sul Titan 2 il 22/09/2026, non da un banco.
       L'inizio e' lo stesso posto in cui atterra il reso (`bodyEl.scrollTop =
       0` in `load`), quindi entrare in modifica non sposta piu' niente. */
    this.editEl.setSelectionRange(0, 0);
    this.editEl.scrollTop = 0;
    this.onEditing?.();
    return true;
  }

  /** Chiude l'editor **buttando via** quel che c'e' dentro. Chi chiama ha gia'
   *  chiesto, o sa che non c'era niente da chiedere. */
  cancelEdit() {
    if (!this.editing) return;
    this.editing = false;
    this.editEl.hidden = true;
    this.barEl.hidden = true;
    this.bodyEl.hidden = false;
    this.onEditing?.();
  }

  /** La tastiera software scende **prima** di qualunque modale.
   *
   *  Un `<dialog>` chiuso ridà il fuoco a chi ce l'aveva, e con quello risale
   *  l'IME: la pressione di Indietro successiva se la mangia la tastiera per
   *  richiudersi, e a schermo non cambia niente. Lezione del gestore file,
   *  trovata sul Titan 2 e non in un test. */
  blurEditor() {
    this.editEl?.blur();
  }

  /** Annulla dal bottone: se c'e' del lavoro dentro, chiede. */
  async askCancel() {
    if (!this.isDirty()) {
      this.cancelEdit();
      return;
    }
    this.blurEditor();
    if (!(await confirmDialog(i18n.t('home.reader.discardConfirm')))) return;
    this.cancelEdit();
  }

  /** Salva, e **ricarica dal server**.
   *
   *  Non si fida di quel che ha appena scritto: il reso lo fa il server, ed e'
   *  lui che deve dire com'e' venuta — un titolo nuovo, un wikilink che adesso
   *  risolve, un frontmatter rotto.
   *
   *  Un secondo tocco su Salva mentre il primo sta scrivendo non fa niente:
   *  partiva una seconda scrittura con lo stesso `base`, e il server — che nel
   *  frattempo aveva gia' il testo nuovo — rispondeva `conflict` su una pagina
   *  che nessun altro aveva toccato. */
  async save() {
    if (!this.editing || this._saving) return;
    this._saving = true;
    try {
      await this._save();
    } finally {
      this._saving = false;
    }
  }

  async _save() {
    const content = this.editEl.value;
    const notebook = this.notebook;
    const path = this.path;
    try {
      await rpc.writePage(notebook, path, content, this.raw);
    } catch (err) {
      console.warn('casa.reader: save failed', err?.code || '(no code)', err);
      if (err?.code === 'conflict') {
        await this._onConflict();
        return;
      }
      showToast(i18n.t('home.reader.saveFailed'), 'error');
      return;
    }
    this.editing = false;
    this.editEl.hidden = true;
    this.barEl.hidden = true;
    this.bodyEl.hidden = false;
    this.onEditing?.();
    const title = await this.load(notebook, path, this.title);
    this.onTitle?.(title);
    showToast(i18n.t('home.reader.saved'), 'success');
  }

  /** Jenny ha riscritto la pagina mentre era aperta.
   *
   *  Non si sceglie al posto di chi ha scritto: rispondendo di no l'editor
   *  resta aperto col suo testo dentro, che e' l'unica copia rimasta. */
  async _onConflict() {
    this.blurEditor();
    const reload = await confirmDialog(i18n.t('home.reader.conflict'));
    if (!reload) return;
    this.cancelEdit();
    const title = await this.load(this.notebook, this.path, this.title);
    this.onTitle?.(title);
  }

  /** Carica *path* dentro *notebook*. Torna il titolo da mettere in testa. */
  async load(notebook, path, fallbackTitle = '') {
    const token = ++this._token;
    this.notebook = notebook;
    this.path = path;
    this.title = fallbackTitle;
    /* Azzerato **prima** della richiesta: se questa fallisce, il sorgente della
       pagina precedente non deve restare qui — «Modifica» aprirebbe un testo
       che non e' quello a schermo, e lo salverebbe sopra un'altra pagina. */
    this.raw = '';
    this.bodyEl.innerHTML = '';
    this._say('home.notebookPages.loading');

    let page;
    try {
      page = await api.getPage({ wiki: notebook, page: path });
    } catch (err) {
      if (token !== this._token) return this.title;
      console.warn('casa.reader: page failed', err);
      this._say('home.reader.failed');
      return this.title;
    }
    if (token !== this._token) return this.title;

    this.title = page.title || fallbackTitle || path;
    /* Il sorgente resta qui: e' quel che l'editor apre e il `base` che il
       salvataggio confronta. `/api/page` lo manda gia', quindi «Modifica» non
       costa una seconda richiesta. */
    this.raw = page.raw || '';
    this.bodyEl.innerHTML = this._safeHtml(page.html, page.raw);
    /* Diagrammi e formule, che il server lascia da rendere: marca i blocchi
       mermaid (`webui/wiki.py`) e il LaTeX lo lascia nel testo.
       **Non si aspetta**: la pagina e' gia' leggibile, e mermaid sono 3,3 MB —
       tenere ferma la lettura finche' non sono scesi vorrebbe dire una schermata
       bianca per un disegno in fondo.
       `inlineDollar`: qui **si accende**, e solo qui. La skill `llm-wiki` impone
       a Jenny il `$f(x)$` in riga, quindi in una pagina quel dollaro e' una
       formula per regola della casa. In chat no — li' e' un prezzo. */
    renderRich(this.bodyEl, { inlineDollar: true });
    this.bodyEl.scrollTop = 0;
    return this.title;
  }

  /* Lo stesso ripiego dell'officina, e per la stessa ragione: senza DOMPurify
     non si mostra l'HTML del server "tanto viene da noi" — viene da un file che
     l'agente ha scritto. Si mostra il markdown, scappato. */
  _safeHtml(html, raw) {
    if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html || '');
    console.warn('casa.reader: DOMPurify missing, falling back to markdown');
    return `<pre class="home-reader-raw">${escapeHtml(raw || '')}</pre>`;
  }

  _say(key) {
    this.bodyEl.innerHTML = '';
    const note = document.createElement('p');
    note.className = 'home-reader-note';
    note.textContent = i18n.t(key);
    this.bodyEl.appendChild(note);
  }

  _onClick(e) {
    const a = e.target.closest('a[href]');
    if (!a) return;
    e.preventDefault();
    const target = linkTarget({
      href: a.getAttribute('href'),
      wikilink: a.classList.contains('wikilink'),
      notebook: this.notebook,
      currentPath: this.path,
    });
    if (!target) {
      showToast(i18n.t('common.linkNotOpenable'), 'info');
      return;
    }
    if (target.kind === 'hash') {
      const anchor = this.bodyEl.querySelector(`#${CSS.escape(target.id)}`);
      anchor?.scrollIntoView({ block: 'start' });
      return;
    }
    if (target.kind === 'external') {
      /* Fuori dalla WebView: `window.open` non apre una finestra (la WebView
         non le supporta), la richiesta ricade su `shouldOverrideUrlLoading` e
         il guscio nativo apre una scheda di Chrome. */
      try {
        window.open(target.href, '_blank', 'noopener');
      } catch (err) {
        console.warn('casa.reader: external link not opened', err);
        showToast(i18n.t('common.linkNotOpenable'), 'error');
      }
      return;
    }
    this.load(this.notebook, target.path).then((title) => this.onTitle?.(title));
  }
}
