/** La casa — le pagine di un quaderno.
 *
 *  «Wiki, grafo e progetti sono una cosa sola: i quaderni, che sono
 *  conversazioni» (la tavola `Concetto`). Questa e' quella cosa: due linguette
 *  — l'elenco e la mappa — sopra **una sola risposta del server**.
 *
 *  `/api/graph?wiki=<name>` porta insieme i nodi, gli archi e l'indice
 *  full-text, e non e' una comodita': il commento in `wiki_routes.py` dice
 *  perche' non sono due endpoint — fra due chiamate la wiki puo' cambiare, e il
 *  client accenderebbe i nodi sbagliati. Quindi elenco, ricerca e mappa non
 *  sono tre caricamenti, sono tre rese della stessa risposta.
 *
 *  **La trappola di questo modulo sta in una riga.** Le postings dell'indice
 *  sono *indici di posizione* nell'array `nodes` che il server ha spedito
 *  (v. `wiki_search.py`), non id: la maschera che torna da `query()` si legge
 *  con quel numero. Ma a schermo le righe vanno in un altro ordine — per
 *  gruppo, alfabetiche dentro — e la mappa in un terzo ancora. Percio' ogni
 *  riga si porta dietro il suo `index` originale invece di dedurlo da dove
 *  sta: ordinare non puo' rinumerare i nodi.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { WikiSearchIndex } from './shared/wiki-search.js';

/* I gruppi di una wiki, nell'ordine in cui si leggono. Le cose prima delle
   idee: la tavola elenca le voci e poi i concetti, ed e' l'ordine in cui uno
   cerca — si va a vedere *il rosmarino*, non *i concimi*.

   **Sono tre e non quattro, e `summaries` non e' una dimenticanza.** Una wiki
   di ricerca ha anche `wiki/summaries/`, ma quelle pagine da `/api/graph` non
   escono: `WIKI_PAGES_SKIP_DIRS` le esclude da tutte e quattro le camminate,
   perche' sono il livello di citazione del pattern di ricerca — un riassunto
   per documento grezzo, non una cosa di cui la wiki parla. Il grafo
   dell'officina ha un colore per loro (`.legend-dot.summaries`) e la sua
   legenda ha tre righe: quella non era una riga mancante, era il conto giusto.
   Misurato costruendo il grafo vero di una wiki con `summaries/` dentro: sei
   nodi su sette, e il settimo era quello.

   Un gruppo che non conosciamo cade comunque in `other` (v. `sanitizeGroup`),
   quindi il giorno in cui quella regola cambiasse non si romperebbe niente:
   comparirebbe una riga in piu' con l'etichetta generica. */
export const GROUPS = ['entities', 'concepts', 'other'];

/* Le etichette esistono gia': sono quelle della legenda del grafo
   dell'officina, e sono le stesse tre. */
export const GROUP_KEYS = {
  entities: 'graph.entities',
  concepts: 'graph.concepts',
  other: 'graph.other',
};

/** Un gruppo che non conosciamo e' `other`, come nell'officina. */
export function sanitizeGroup(group) {
  return GROUPS.includes(group) ? group : 'other';
}

/** Il nome da scrivere: il titolo del frontmatter se c'e', se no il file. */
export function labelOf(node) {
  return node.title || node.label || node.id || '';
}

/** Le righe, nell'ordine in cui si disegnano — **con l'indice di partenza**.
 *
 *  `index` e' la posizione nell'array che il server ha spedito, cioe' la
 *  chiave con cui si legge la maschera della ricerca. Va calcolata **prima**
 *  di ordinare e portata con la riga: dedurla dopo vorrebbe dire accendere la
 *  riga sbagliata a ogni carattere digitato.
 */
export function orderPages(nodes) {
  const rows = (nodes || []).map((node, index) => ({
    index,
    id: node.id,
    path: node.path,
    label: labelOf(node),
    group: sanitizeGroup(node.group),
    degree: node.degree || 0,
  }));
  const rank = (row) => GROUPS.indexOf(row.group);
  return rows.sort(
    (a, b) =>
      rank(a) - rank(b) ||
      a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }),
  );
}

/** Il gruppo si mostra solo se divide qualcosa.
 *
 *  Cinque quaderni su quattordici sono piatti: ogni pagina cade in `other`, e
 *  l'etichetta comparirebbe identica su ogni riga. Una parola che vale per
 *  tutte non informa, occupa: sparisce, e con lei il pallino.
 */
export function groupsAreMeaningful(rows) {
  const seen = new Set((rows || []).map((row) => row.group));
  return seen.size > 1;
}

export class NotebookPages {
  /**
   * @param onOpenPage  cosa fare quando si tocca una riga: `(path, label)`.
   * @param onNeedMap   la mappa la disegna un altro modulo, e si carica al
   *                    primo tocco sulla linguetta: `(data) => Promise`.
   */
  constructor({ onOpenPage, onNeedMap } = {}) {
    this.el = document.getElementById('home-notebook-pages');
    this.listEl = document.getElementById('home-notebook-page-list');
    this.noteEl = document.getElementById('home-notebook-pages-note');
    this.queryEl = document.getElementById('home-notebook-pages-q');
    this.tabListEl = document.getElementById('home-tab-list');
    this.tabMapEl = document.getElementById('home-tab-map');
    this.mapEl = document.getElementById('home-map');
    this.mapNoteEl = document.getElementById('home-map-note');

    this._onOpenPage = onOpenPage;
    this._onNeedMap = onNeedMap;
    this.notebook = null;
    this.data = null;
    this.rows = [];
    this._index = null;
    this._tab = 'list';
    /* Token di carico monotono: `/api/graph` su una wiki grossa non torna in
       un frame, e dai Quaderni si puo' saltare in un altro quaderno mentre
       arriva. Solo l'ultimo chiamante disegna. */
    this._token = 0;

    this.queryEl?.addEventListener('input', () => this._applySearch());
    this.tabListEl?.addEventListener('click', () => this.showTab('list'));
    this.tabMapEl?.addEventListener('click', () => this.showTab('map'));
    this.listEl?.addEventListener('click', (e) => {
      const row = e.target.closest('[data-page]');
      if (row) this._onOpenPage?.(row.dataset.page, row.dataset.label || '');
    });
  }

  /** Le parole della stanza. Il campo di ricerca riusa il segnaposto del grafo
   *  dell'officina — e' gia', parola per parola, quel che la tavola scrive. */
  applyTranslations() {
    if (this.tabListEl) this.tabListEl.textContent = i18n.t('home.notebookPages.tabList');
    if (this.tabMapEl) this.tabMapEl.textContent = i18n.t('home.notebookPages.tabMap');
    if (this.queryEl) this.queryEl.placeholder = i18n.t('graph.searchPlaceholder');
    /* Le righe gia' a schermo portano l'etichetta del gruppo: cambiata la
       lingua, si ridisegnano invece di restare nella precedente. */
    if (this.rows.length) this._render();
  }

  /** Carica e disegna le pagine di *notebook*. */
  async load(notebook) {
    const token = ++this._token;
    this.notebook = notebook;
    this.data = null;
    this.rows = [];
    this._index = null;
    if (this.queryEl) this.queryEl.value = '';
    this.showTab('list');
    this._say('home.notebookPages.loading');
    this.listEl.innerHTML = '';

    let data;
    try {
      data = await api.getGraph(notebook);
    } catch (err) {
      if (token !== this._token) return;
      console.warn('casa.pages: graph failed', err);
      /* Le wiki spente sono un 503 e non un guasto: la frase esiste gia', ed e'
         quella che il giro di creazione usa per dire la stessa cosa. */
      const off = /\b503\b/.test(String(err?.message || ''));
      this._say(off ? 'home.who.create.wikiOff' : 'home.notebookPages.failed');
      return;
    }
    if (token !== this._token) return;

    this.data = data;
    /* Il nome serve alla mappa come chiave degli spilli, e va tenuto qui: e'
       l'unico posto che lo sa gia'. */
    this.notebook = notebook;
    this.rows = orderPages(data.nodes);
    this._index = WikiSearchIndex.from(data.search);
    this._render();
  }

  /** Quale linguetta e' accesa. La mappa si carica al primo tocco, mai prima. */
  showTab(tab) {
    this._tab = tab === 'map' ? 'map' : 'list';
    const onList = this._tab === 'list';
    this.tabListEl?.classList.toggle('is-on', onList);
    this.tabMapEl?.classList.toggle('is-on', !onList);
    this.tabListEl?.setAttribute('aria-selected', String(onList));
    this.tabMapEl?.setAttribute('aria-selected', String(!onList));
    if (this.listEl) this.listEl.hidden = !onList;
    if (this.mapEl) this.mapEl.hidden = onList;
    /* Il campo di ricerca e' dell'elenco: nella mappa non c'e' niente da
       filtrare che si legga. */
    const search = this.queryEl?.closest('.home-search');
    if (search) search.hidden = !onList;
    if (!onList && this.data) this._onNeedMap?.(this.data, this.rows, this.notebook);
  }

  _say(key) {
    if (!this.noteEl) return;
    this.noteEl.textContent = key ? i18n.t(key) : '';
    this.noteEl.hidden = !key;
  }

  _render() {
    this.listEl.innerHTML = '';
    if (!this.rows.length) {
      this._say('home.notebookPages.none');
      return;
    }
    this._say(null);
    const withGroups = groupsAreMeaningful(this.rows);
    const frag = document.createDocumentFragment();
    for (const row of this.rows) frag.appendChild(this._row(row, withGroups));
    this.listEl.appendChild(frag);
  }

  _row(row, withGroups) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'home-notebook-page';
    el.dataset.page = row.path;
    el.dataset.label = row.label;
    /* L'indice di partenza viaggia col nodo del DOM: e' cosi' che la ricerca
       ritrova la riga senza contare le posizioni (v. il cappello). */
    el.dataset.node = String(row.index);

    if (withGroups) {
      const dot = document.createElement('span');
      dot.className = `home-notebook-page-dot home-group-${row.group}`;
      el.appendChild(dot);
    }

    const name = document.createElement('span');
    name.className = 'home-notebook-page-name';
    name.textContent = row.label;
    el.appendChild(name);

    if (withGroups) {
      const group = document.createElement('span');
      group.className = 'home-notebook-page-group';
      group.textContent = i18n.t(GROUP_KEYS[row.group]);
      el.appendChild(group);
    }
    return el;
  }

  /* Ogni carattere: nessuna rete, solo una maschera di byte. */
  _applySearch() {
    if (!this.listEl) return;
    const result = this._index ? this._index.query(this.queryEl.value) : null;
    let shown = 0;
    for (const el of this.listEl.children) {
      const on = !result || !!result.mask[Number(el.dataset.node)];
      el.hidden = !on;
      if (on) shown += 1;
    }
    if (!this.rows.length) return;
    this._say(shown ? null : 'home.notebookPages.noMatch');
  }
}
