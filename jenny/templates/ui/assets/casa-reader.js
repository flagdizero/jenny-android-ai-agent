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
 */

import { api } from './shared/api-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';

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

  /* I wikilink il renderer li scrive come `?wiki=<nome>&page=<path>`. Un
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

export class CasaReader {
  constructor() {
    this.el = document.getElementById('casa-reader');
    this.bodyEl = document.getElementById('casa-reader-body');
    this.notebook = null;
    this.path = null;
    this.title = '';
    /* Come per l'elenco: due pagine di fila, e solo l'ultima disegna. */
    this._token = 0;
    this.bodyEl?.addEventListener('click', (e) => this._onClick(e));
  }

  /** Carica *path* dentro *notebook*. Torna il titolo da mettere in testa. */
  async load(notebook, path, fallbackTitle = '') {
    const token = ++this._token;
    this.notebook = notebook;
    this.path = path;
    this.title = fallbackTitle;
    this.bodyEl.innerHTML = '';
    this._say('casa.pages.loading');

    let page;
    try {
      page = await api.getPage({ wiki: notebook, page: path });
    } catch (err) {
      if (token !== this._token) return this.title;
      console.warn('casa.reader: page failed', err);
      this._say('casa.reader.failed');
      return this.title;
    }
    if (token !== this._token) return this.title;

    this.title = page.title || fallbackTitle || path;
    this.bodyEl.innerHTML = this._safeHtml(page.html, page.raw);
    this.bodyEl.scrollTop = 0;
    return this.title;
  }

  /* Lo stesso ripiego dell'officina, e per la stessa ragione: senza DOMPurify
     non si mostra l'HTML del server "tanto viene da noi" — viene da un file che
     l'agente ha scritto. Si mostra il markdown, scappato. */
  _safeHtml(html, raw) {
    if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html || '');
    console.warn('casa.reader: DOMPurify assente, ripiego sul markdown');
    return `<pre class="casa-reader-raw">${escapeHtml(raw || '')}</pre>`;
  }

  _say(key) {
    this.bodyEl.innerHTML = '';
    const note = document.createElement('p');
    note.className = 'casa-reader-note';
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
        console.warn('casa.reader: link esterno non aperto', err);
        showToast(i18n.t('common.linkNotOpenable'), 'error');
      }
      return;
    }
    this.load(this.notebook, target.path).then((title) => this.onTitle?.(title));
  }
}
