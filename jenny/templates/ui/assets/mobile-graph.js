import { api } from './shared/api-client.js';
import { escapeHtml, hashString } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { WikiSearchIndex } from './shared/wiki-search.js';
import { AppState } from './shared/state.js';
import { isOpenableProjectName } from './shared/conversation-list.js';

export class GraphController {
  constructor() {
    this.svgEl = document.getElementById('graph-svg');
    this.loadingEl = document.getElementById('graph-loading');
    this.graphWrapEl = document.getElementById('graph-wrap');
    this.currentWiki = null;
    this.teardown = null;
    // Azzeratore del focus sul nodo, installato da renderWikiGraph (v. lì).
    this._clearFocus = null;
    // Indice full-text della wiki corrente, montato dalla stessa risposta che
    // porta il grafo (v. loadGraph). Null nella vista home: lì i nodi sono le
    // wiki, non le pagine, e non c'è testo su cui cercare.
    this._searchIndex = null;
    // Applicatore della maschera di ricerca, installato da renderWikiGraph
    // insieme a `_clearFocus`: vive nel grafo disegnato, non nel controller.
    this._applySearch = null;
    this._zoomToMatches = null;
    this._searchRaf = 0;
    // Token di caricamento (monotono): solo l'ultimo loadGraph disegna.
    this._loadToken = 0;
    // Contatore di generazione: incrementato in deactivate(), permette a una
    // continuazione di scoprire che la *sezione* è stata abbandonata. Il token
    // da solo non basta: è cieco all'uscita dal grafo.
    this._gen = 0;
    // Il grafo si ridisegna comunque a ogni activate() (rientro in vista), e le
    // etichette della legenda sono data-i18n statiche già ri-tradotte da
    // _applyStaticTranslations(). Questo listener copre solo il caso in cui la
    // lingua cambi mentre il grafo è già la vista attiva.
    i18n.onLocaleChange(() => {
      if (window.mobileApp?.currentMode === 'graph') {
        this.loadGraph(this.currentWiki, false);
      }
    });
    // Cambiare progetto riaggancia il grafo, come la wiki (v. lì). Fuori vista
    // basta spostare `currentWiki`: è da lì che riparte `activate()`, e senza
    // questa riga il rientro disegnava il grafo del progetto di prima — o, al
    // ritorno sulla personale, restava sull'ultimo progetto invece che sulla
    // Home. Vale anche per `null`: le due viste devono rispondere uguale.
    AppState.on('pinnedWiki', (pin) => {
      this.currentWiki = pin || null;
      if (window.mobileApp?.currentMode === 'graph') this.loadGraph(this.currentWiki, false);
    });
    this._initSearchUI();
  }

  /** La wiki a cui il grafo è agganciato, o `null` nella chat personale.
   *  Stessa ragione della wiki: senza aggancio il grafo home ha per nodi *le
   *  wiki*, cioè l'elenco degli altri progetti. */
  get pinnedWiki() { return AppState.pinnedWiki || null; }

  /* ── Ricerca ──────────────────────────────────────────────────────────────
     La barra vive nel DOM statico, non nel grafo: sopravvive a un reload della
     tela, così ricaricare la vista (cambio lingua, refresh) non svuota ciò che
     l'utente stava scrivendo. Il ponte fra le due vite è `_applySearch`, che
     renderWikiGraph reinstalla a ogni disegno e `_cleanup()` stacca. */
  _initSearchUI() {
    this.searchEl = document.getElementById('graph-search');
    this.searchInput = document.getElementById('graph-search-input');
    this.searchCountEl = document.getElementById('graph-search-count');
    this.searchClearBtn = document.getElementById('graph-search-clear');

    this.searchInput?.addEventListener('input', () => this._scheduleSearch());
    this.searchInput?.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      // Invio = "ho finito di scrivere": si chiude la tastiera e si inquadra
      // ciò che si è trovato. Inquadrare a ogni tasto darebbe la nausea.
      this.searchInput.blur();
      this._runSearch();
      this._zoomToMatches?.();
    });
    this.searchClearBtn?.addEventListener('click', () => this._setQuery(''));
  }

  /** Ricalcolo coalescato su rAF: al massimo una volta per fotogramma
   *  disegnato, e senza il ritardo artificiale di un debounce — con l'indice
   *  già in memoria una query costa meno di un fotogramma, quindi aspettare
   *  sarebbe latenza regalata. */
  _scheduleSearch() {
    if (this._searchRaf) return;
    this._searchRaf = requestAnimationFrame(() => {
      this._searchRaf = 0;
      this._runSearch();
    });
  }

  /** Imposta la query e applica *subito*: chi la chiama (pulsante Cancella,
   *  tasto Indietro) si aspetta un cambiamento visibile in quella pressione,
   *  non al fotogramma dopo. */
  _setQuery(text) {
    if (this.searchInput) this.searchInput.value = text;
    if (this._searchRaf) {
      cancelAnimationFrame(this._searchRaf);
      this._searchRaf = 0;
    }
    this._runSearch();
  }

  _runSearch() {
    const query = this.searchInput?.value || '';
    const hasText = query.trim().length > 0;
    if (this.searchClearBtn) this.searchClearBtn.hidden = !hasText;

    // `query()` ritorna null quando la ricerca non impone alcun vincolo (testo
    // vuoto, o solo parole presenti ovunque): è diverso da "zero risultati", e
    // deve lasciare il grafo intero acceso invece di spegnerlo tutto.
    const result = (hasText && this._searchIndex) ? this._searchIndex.query(query) : null;
    this._applySearch?.(result ? result.mask : null);

    if (this.searchCountEl) {
      this.searchCountEl.hidden = !result;
      if (result) this.searchCountEl.textContent = String(result.count);
      this.searchCountEl.classList.toggle('empty', !!result && result.count === 0);
    }
  }

  /** Mostra la barra solo dove ha senso, e riapplica la query al nuovo grafo. */
  _syncSearchUI() {
    if (this.searchEl) this.searchEl.hidden = !this._searchIndex;
    if (!this._searchIndex && this.searchInput) this.searchInput.value = '';
    this._runSearch();
  }

  showLoading() {
    if (this.loadingEl) this.loadingEl.classList.add('active');
  }

  hideLoading() {
    if (this.loadingEl) this.loadingEl.classList.remove('active');
  }

  activate() {
    // Sorgente unica del caricamento. Chi porta qui una vista precisa (il
    // popstate, il boot, il pulsante Grafo dell'header) la deposita in
    // `mobileApp` con requestGraph e la carica questo activate(): prima ogni
    // chiamante rifaceva `loadGraph` subito dopo `switchMode`, cioè due volte.
    // Senza richiesta si ricarica la vista corrente (landing di default sul
    // grafo overview, non su una wiki specifica).
    const pending = window.mobileApp?.takePendingGraph?.() || null;
    if (pending) this.loadGraph(pending.wiki, pending.push === true);
    else this.loadGraph(this.pinnedWiki || this.currentWiki, false);
  }

  deactivate() {
    // Le continuazioni di loadGraph nate prima dell'uscita dalla sezione devono
    // poter scoprire di essere state superate: il token non le copre, perché è
    // monotono solo rispetto ad altri caricamenti *del grafo*.
    this._gen++;
    this._cleanup();
    // Un ricalcolo già in coda troverebbe `_applySearch` staccato: innocuo, ma
    // è un fotogramma di lavoro su una vista che non è più a schermo.
    if (this._searchRaf) {
      cancelAnimationFrame(this._searchRaf);
      this._searchRaf = 0;
    }
    this.hideLoading();
  }

  /** true se questo caricamento è stato superato da uno più recente o se nel
   *  frattempo si è usciti dalla sezione. */
  _stale(token, gen) {
    return token !== this._loadToken || gen !== this._gen;
  }

  _cleanup() {
    // Il focus vive nel grafo che sta per essere smontato: tenerne
    // l'azzeratore significherebbe far consumare una pressione di Indietro a
    // una closure che opera su nodi non più a schermo. Stesso discorso per la
    // maschera di ricerca, che indicizza i nodi *di quel* disegno.
    this._clearFocus = null;
    this._applySearch = null;
    this._zoomToMatches = null;
    if (this.teardown) {
      this.teardown();
      this.teardown = null;
    }
  }

  async loadGraph(wiki = null, pushHistory = true) {
    // L'aggancio vince su qualunque chiamante — history, header, link. Senza
    // wiki si finirebbe sul grafo home, i cui nodi sono le altre wiki.
    const pin = this.pinnedWiki;
    if (pin) wiki = pin;
    // Catturati prima del primo await: la fetch del grafo può essere superata
    // da un altro caricamento o dall'uscita dalla sezione, e disegnare dopo
    // significherebbe sovrascrivere il grafo di qualcun altro (o quello di
    // nessuno) e impilare una entry di history per una schermata mai vista.
    const token = ++this._loadToken;
    const gen = this._gen;
    this.currentWiki = wiki;
    // Accanto all'assegnazione, e non dopo la fetch: il tasto nomina la wiki
    // che questa vista sta mostrando, e un caricamento fallito la mostra
    // comunque (il titolo resta, il grafo diventa una riga d'errore). Ultimo
    // chiamante vince, come per `currentWiki`.
    this._syncProjectAction(wiki);
    this._cleanup();
    this.showLoading();

    try {
      const data = await api.getGraph(wiki);
      if (this._stale(token, gen)) return;
      // Indice e grafo arrivano insieme apposta: le postings dell'indice sono
      // *indici* in `data.nodes`, quindi montarlo qui — e non con una fetch a
      // parte — è ciò che garantisce che accendano i nodi che stiamo per
      // disegnare, e non quelli di un'istantanea diversa della wiki.
      this._searchIndex = WikiSearchIndex.from(data.search);
      if (wiki) {
        this.renderWikiGraph(data, wiki);
      } else {
        this.renderHomeGraph(data);
      }
      this._syncSearchUI();

      if (pushHistory) {
        window.mobileApp.pushNav({ mode: 'graph', wiki });
      }

      this._updateTitle(wiki);
      this._renderBreadcrumbs(wiki);
    } catch (err) {
      if (this._stale(token, gen)) return;
      console.error('Failed to load graph:', err);
      this._searchIndex = null;
      this._syncSearchUI();
      this.svgEl.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#666" font-size="12">${i18n.t('graph.failedToLoad')}</text>`;
    } finally {
      // Chi è stato superato non spegne lo spinner di chi lo ha superato.
      if (!this._stale(token, gen)) this.hideLoading();
    }
  }

  /** Accende il tasto «chat del progetto» se questa vista ne ha uno da aprire.
   *
   *  Sul grafo home (`wiki` null) non c'è: i nodi sono *le* wiki, e non ce n'è
   *  una da aprire più delle altre. Su una cartella con un nome che il server
   *  non accetta come progetto non c'è nemmeno: aprirla porterebbe in
   *  un'**altra** conversazione (v. `isOpenableProjectName`).
   */
  _syncProjectAction(wiki) {
    const header = window.mobileApp?.header;
    if (!header) return;
    if (wiki && isOpenableProjectName(wiki)) header.showAction('open-project-chat', 'graph');
    else header.hideAction('open-project-chat', 'graph');
  }

  _updateTitle(wiki) {
    const title = wiki ? `Graph: ${wiki}` : i18n.t('graph.wikis');
    if (window.mobileApp?.header) {
      window.mobileApp.header.setTitle(title, 'graph');
    }
  }

  _renderBreadcrumbs(wiki) {
    if (!this.graphWrapEl) return;
    let existing = this.graphWrapEl.querySelector('.wiki-breadcrumbs');
    if (existing) existing.remove();

    const wrap = document.createElement('div');
    wrap.className = 'wiki-breadcrumbs graph-breadcrumbs';

    if (!wiki) {
      wrap.innerHTML = `<span class="bc-current">${i18n.t('graph.wikis')}</span>`;
    } else if (this.pinnedWiki) {
      // Dentro un progetto il grafo home non è raggiungibile: il crumb "Wikis"
      // resterebbe lì a promettere una via d'uscita che `loadGraph` riaggancia
      // subito. Stessa scelta della vista wiki.
      wrap.innerHTML = `<span class="bc-current">${escapeHtml(wiki)}</span>`;
    } else {
      wrap.innerHTML = `
        <a class="bc-link" data-home href="/?mode=graph">${i18n.t('graph.wikis')}</a>
        <span class="bc-sep">/</span>
        <span class="bc-current">${escapeHtml(wiki)}</span>
      `;
    }

    this.graphWrapEl.insertBefore(wrap, this.graphWrapEl.firstChild);

    wrap.querySelectorAll('a[data-home]').forEach(a => {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        this.loadGraph(null);
      });
    });
  }

  renderHomeGraph(data) {
    // `this.teardown` va **invocato**, non solo riassegnato: la riassegnazione
    // in fondo a questo metodo dimenticava la simulazione d3 precedente senza
    // fermarla, e quella continuava a ticchettare per sempre su nodi non più a
    // schermo. `_cleanup()` è idempotente, quindi ripeterlo dopo quello di
    // loadGraph non costa niente e copre ogni altro chiamante.
    this._cleanup();
    const svg = d3.select(this.svgEl);
    svg.selectAll('*').remove();

    // Nella vista home i colori dei nodi sono per-wiki (non semantici):
    // la legenda concepts/entities/... non si applica → nascosta.
    const legend = document.getElementById('graph-legend');
    if (legend) legend.style.display = 'none';

    const width = this.svgEl.clientWidth || 400;
    const height = this.svgEl.clientHeight || 600;
    svg.attr('viewBox', `0 0 ${width} ${height}`);

    const root = svg.append('g');
    const linkLayer = root.append('g');
    const nodeLayer = root.append('g');
    const labelLayer = root.append('g');  // sopra i nodi

    const cx = width / 2;
    const cy = height / 2;

    const allNodes = data.nodes.map(n => ({ ...n }));
    const allLinks = data.edges.map(e => ({ ...e }));

    const homeNode = allNodes.find(n => n.id === '_home');
    const wikiNodes = allNodes.filter(n => n.id !== '_home');

    if (homeNode) {
      homeNode.x = cx;
      homeNode.y = cy;
      homeNode.fx = cx;
      homeNode.fy = cy;
    }

    const starRadius = Math.min(width, height) * 0.32;
    wikiNodes.forEach((n, i) => {
      const angle = (i / Math.max(wikiNodes.length, 1)) * 2 * Math.PI - Math.PI / 2;
      n.x = cx + starRadius * Math.cos(angle);
      n.y = cy + starRadius * Math.sin(angle);
    });

    const WIKI_PALETTE = ['var(--accent)','var(--ok)','var(--error)','var(--orange, #f59e0b)','var(--cyan, #06b6d4)','var(--purple, #a855f7)','var(--pink, #ec4899)','var(--teal, #14b8a6)','var(--amber, #f97316)','var(--lime, #84cc16)'];
    wikiNodes.forEach((n, i) => { n._color = WIKI_PALETTE[i % WIKI_PALETTE.length]; });

    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const links = allLinks
      .map(e => ({
        source: nodeMap.get(e.source),
        target: nodeMap.get(e.target)
      }))
      .filter(l => l.source && l.target);

    const wikiRadiusFn = (n) => {
      if (n.id === '_home') return 14 + Math.sqrt(n.degree || 0) * 0.4;
      return 16;
    };

    const sim = d3.forceSimulation(allNodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(130).strength(0.5))
      .force('charge', d3.forceManyBody().strength(-260).distanceMax(500))
      .force('center', d3.forceCenter(cx, cy).strength(0.02))
      .force('collision', d3.forceCollide().radius(d => wikiRadiusFn(d) + 8).strength(0.9))
      .alphaDecay(0.04)
      .velocityDecay(0.35);

    // Layout statico: converge prima di disegnare (niente animazione a riposo).
    settleSimulation(sim);

    const linkSel = linkLayer.selectAll('line').data(links).enter().append('line')
      .attr('class', 'link')
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    const nodeSel = nodeLayer.selectAll('g.node').data(allNodes).enter().append('g')
      .attr('class', d => `node group-${sanitizeGroup(d.group)}${d.id === '_home' ? ' big' : ''}`)
      .attr('transform', d => `translate(${d.x},${d.y})`);

    nodeSel.append('circle').attr('class', 'node-main')
      .attr('r', d => wikiRadiusFn(d))
      .style('fill', d => d._color || null);
    nodeSel.append('title').text(d => nodeLabelText(d));

    // Poche etichette nella vista home: sempre visibili (classe .big).
    const labelSel = labelLayer.selectAll('text.node-label').data(allNodes).enter().append('text')
      .attr('class', 'node-label big')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.id === '_home' ? 4 : wikiRadiusFn(d) + 12)
      .attr('x', d => d.x)
      .attr('y', d => d.y)
      .text(d => truncateLabel(nodeLabelText(d)));

    sim.on('tick', () => {
      linkSel
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);
      nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
      labelSel.attr('x', d => d.x).attr('y', d => d.y);
    });

    const dragBehavior = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        if (d.id !== '_home') {
          d.fx = null;
          d.fy = null;
        }
      });

    nodeSel.call(dragBehavior);

    nodeSel
      .style('cursor', d => d.id === '_home' ? 'default' : 'pointer')
      .on('click', (event, d) => {
        event.stopPropagation();
        if (d.id !== '_home') {
          this.loadGraph(d.id, true);
        }
      });

    const zoomBehavior = d3.zoom()
      .scaleExtent([0.3, 4])
      .on('zoom', (event) => root.attr('transform', event.transform));
    svg.call(zoomBehavior);

    this.teardown = () => {
      sim.stop();
      svg.selectAll('*').remove();
    };
  }

  renderWikiGraph(data, wiki) {
    // Come in renderHomeGraph: la simulazione precedente si ferma, non si
    // dimentica (v. il commento lì).
    this._cleanup();
    const svg = d3.select(this.svgEl);
    svg.selectAll('*').remove();

    // Vista wiki: i nodi sono colorati per gruppo semantico → legenda visibile.
    const legend = document.getElementById('graph-legend');
    if (legend) legend.style.display = '';

    const width = this.svgEl.clientWidth || 400;
    const height = this.svgEl.clientHeight || 600;
    svg.attr('viewBox', `0 0 ${width} ${height}`);

    const root = svg.append('g');
    const linkLayer = root.append('g');
    const nodeLayer = root.append('g');
    const labelLayer = root.append('g');  // sopra i nodi: label sempre in primo piano

    const EXCLUDE_ID = 'wiki/index.md';

    // `_i` è la posizione del nodo **nell'array servito dal server**, non in
    // quello disegnato: è la chiave con cui l'indice full-text ha scritto le
    // sue postings. Va catturata prima del filtro che toglie la index.md,
    // altrimenti dopo lo scarto ogni maschera punterebbe al nodo sbagliato.
    const allNodes = data.nodes.map((n, i) => ({ ...n, _i: i }));
    const allLinks = data.edges.map(e => ({ ...e }));

    const adjacency = new Map();
    for (const n of allNodes) adjacency.set(n.id, new Set());
    for (const e of allLinks) {
      if (!adjacency.has(e.source) || !adjacency.has(e.target)) continue;
      adjacency.get(e.source).add(e.target);
      adjacency.get(e.target).add(e.source);
    }

    const radius = (n) => 6 + Math.sqrt(n.degree || 0) * 2.6;

    const cx = width / 2;
    const cy = height / 2;

    const bfsOrder = new Map();
    const queue = [EXCLUDE_ID];
    bfsOrder.set(EXCLUDE_ID, 0);
    let bfsIdx = 1;
    while (queue.length > 0) {
      const current = queue.shift();
      const currentNeighbors = adjacency.get(current);
      if (!(currentNeighbors instanceof Set)) continue;
      const neighbors = [...currentNeighbors].sort((a, b) => hashString(a) - hashString(b));
      for (const neighbor of neighbors) {
        if (!bfsOrder.has(neighbor)) {
          bfsOrder.set(neighbor, bfsIdx++);
          queue.push(neighbor);
        }
      }
    }

    const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
    const SPACING = 22;

    for (const n of allNodes) {
      if (n.id === EXCLUDE_ID) {
        n.x = cx;
        n.y = cy;
        continue;
      }
      const idx = bfsOrder.get(n.id) ?? (bfsIdx + hashString(n.id) % 1000);
      const theta = idx * GOLDEN_ANGLE + (hashString(n.id) % 1000) / 1000 * 0.3;
      const r = SPACING * Math.sqrt(idx);
      n.x = cx + r * Math.cos(theta);
      n.y = cy + r * Math.sin(theta);
    }

    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const nodes = allNodes.filter(n => n.id !== EXCLUDE_ID);
    const visibleIds = new Set(nodes.map(n => n.id));
    const links = allLinks
      .filter(e => e.source !== EXCLUDE_ID && e.target !== EXCLUDE_ID
        && visibleIds.has(e.source) && visibleIds.has(e.target))
      .map(e => ({
        source: nodeMap.get(e.source),
        target: nodeMap.get(e.target)
      }))
      .filter(l => l.source && l.target);

    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(90).strength(0.35))
      .force('charge', d3.forceManyBody().strength(-180).distanceMax(500))
      .force('center', d3.forceCenter(cx, cy).strength(0.008))
      .force('collision', d3.forceCollide().radius(d => radius(d) + 6).strength(0.85))
      .alphaDecay(0.04)
      .velocityDecay(0.35);

    // Layout statico: converge prima di disegnare (niente animazione a riposo).
    settleSimulation(sim);

    const linkSel = linkLayer.selectAll('line').data(links).enter().append('line')
      .attr('class', 'link')
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    const nodeSel = nodeLayer.selectAll('g.node').data(nodes).enter().append('g')
      .attr('class', d => `node group-${sanitizeGroup(d.group)}`)
      .attr('transform', d => `translate(${d.x},${d.y})`);

    nodeSel.append('circle').attr('class', 'node-main').attr('r', d => radius(d));
    // Anello del risultato di ricerca, disegnato *dentro* il disco (v. il CSS
    // per il perché del colore). Esiste sempre e resta invisibile finché il
    // nodo non è un match: crearlo al bisogno vorrebbe dire ricostruire nodi
    // durante la digitazione, che è esattamente ciò che questa ricerca evita.
    nodeSel.append('circle').attr('class', 'node-ring').attr('r', d => ringRadius(radius(d)));
    nodeSel.append('title').text(d => nodeLabelText(d));  // tooltip: titolo completo

    // Ogni nodo ha la sua etichetta: è declutterLabels() a spegnere solo quelle
    // che si sovrappongono, non una soglia sul numero di collegamenti.
    const labelSel = labelLayer.selectAll('text.node-label').data(nodes).enter().append('text')
      .attr('class', d => `node-label group-${sanitizeGroup(d.group)}`)
      .attr('text-anchor', 'middle')
      .attr('dy', d => -radius(d) - 8)
      .attr('x', d => d.x)
      .attr('y', d => d.y)
      .text(d => truncateLabel(nodeLabelText(d)));

    declutterLabels(labelSel);

    // Maschera di ricerca: 1 byte per nodo, indicizzata per `_i`. Null = nessuna
    // ricerca in corso. Vive qui e non nel controller perché ha senso solo
    // rispetto a *questo* disegno.
    let searchMask = null;
    let matchedIds = null;
    const isMatch = (d) => !!searchMask && searchMask[d._i] === 1;

    // Durante il drag i nodi si spostano: il declutter va rifatto, ma throttlato
    // (è O(n²) sul numero di etichette) e ripetuto a simulazione ferma. Con una
    // ricerca attiva non va rifatto affatto: le etichette dei match sono accese
    // d'ufficio (v. _refreshLabels) e il declutter le rispegnerebbe.
    let lastDeclutter = 0;
    sim.on('tick', () => {
      linkSel
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);
      nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
      labelSel.attr('x', d => d.x).attr('y', d => d.y);
      if (searchMask) return;
      const now = performance.now();
      if (now - lastDeclutter > 150) {
        lastDeclutter = now;
        declutterLabels(labelSel);
      }
    });
    sim.on('end', () => { if (!searchMask) declutterLabels(labelSel); });

    const dragBehavior = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });

    nodeSel.call(dragBehavior);

    const zoomBehavior = d3.zoom()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => root.attr('transform', event.transform));

    svg.call(zoomBehavior);

    let focusNodeId = null;
    let hoverNodeId = null;

    // Costruito una volta sola: era ricalcolato a ogni updateVisualState, cioè
    // — ora che anche la ricerca lo chiama — a ogni carattere digitato.
    const allNodeIds = new Set(nodes.map(n => n.id));

    function getNeighbors(id) {
      return adjacency.get(id) || new Set();
    }

    /* Insieme di partenza dei nodi "vivi", prima che hover e focus decidano
       chi accendere fra loro. La ricerca è una *terza lente*, non un terzo
       ramo: restringe la base, e da lì in poi tutta la logica esistente di
       dim/highlight vale identica. Il focus ha la precedenza perché esplorare
       i collegamenti di un nodo vuole vederli tutti, anche quelli che la query
       non tocca: i match restano comunque riconoscibili dalla classe `.match`,
       che è indipendente da dim/highlight. */
    function baseActiveIds() {
      if (focusNodeId) return new Set([focusNodeId, ...getNeighbors(focusNodeId)]);
      if (matchedIds) return matchedIds;
      return allNodeIds;
    }

    function computeExtent(targetNodes) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of targetNodes) {
        minX = Math.min(minX, n.x);
        minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, n.x);
        maxY = Math.max(maxY, n.y);
      }
      const pad = 60;
      return [[minX - pad, minY - pad], [maxX + pad, maxY + pad]];
    }

    function zoomToExtent(targetNodes, duration = 750) {
      const [[x0, y0], [x1, y1]] = computeExtent(targetNodes);
      const w = x1 - x0, h = y1 - y0;
      const k = Math.min(width / w, height / h, 4) * 0.9;
      const tx = width / 2 - k * (x0 + x1) / 2;
      const ty = height / 2 - k * (y0 + y1) / 2;
      svg.transition().duration(duration).call(
        zoomBehavior.transform,
        d3.zoomIdentity.translate(tx, ty).scale(k)
      );
    }

    /* Raggio dei due cerchi di ogni nodo in un punto solo. Erano due `attr('r')`
       separati a rischio di deriva: l'anello del match deve stare dentro al
       disco *anche* mentre hover e focus lo ingrandiscono, altrimenti a nodo
       cresciuto sborda e diventa un contorno esterno che sparisce sul fondo. */
    function applyRadii(scaleOf) {
      nodeSel.selectAll('.node-main').attr('r', n => radius(n) * scaleOf(n));
      nodeSel.selectAll('.node-ring').attr('r', n => ringRadius(radius(n) * scaleOf(n)));
    }

    function updateVisualState() {
      const activeNodeIds = baseActiveIds();
      const highlightedNode = hoverNodeId || focusNodeId;

      if (highlightedNode && activeNodeIds.has(highlightedNode)) {
        const neighbors = getNeighbors(highlightedNode);
        const litNodes = new Set([highlightedNode, ...neighbors]);

        nodeSel
          .classed('dim', n => !litNodes.has(n.id))
          .classed('highlight', n => litNodes.has(n.id));
        applyRadii(n => {
          if (n.id === highlightedNode) return 1.4;
          if (litNodes.has(n.id)) return 1.15;
          return 1;
        });

        labelSel
          .classed('dim', n => !litNodes.has(n.id))
          .classed('highlight', n => litNodes.has(n.id));

        linkSel
          .classed('dim', l => !litNodes.has(l.source.id) || !litNodes.has(l.target.id))
          .classed('highlight', l => {
            const sLit = litNodes.has(l.source.id);
            const tLit = litNodes.has(l.target.id);
            return sLit && tLit && (l.source.id === highlightedNode || l.target.id === highlightedNode);
          });
      } else {
        nodeSel
          .classed('dim', n => !activeNodeIds.has(n.id))
          .classed('highlight', false);
        applyRadii(() => 1);

        labelSel
          .classed('dim', n => !activeNodeIds.has(n.id))
          .classed('highlight', false);

        linkSel
          .classed('dim', l => {
            return !activeNodeIds.has(l.source.id) || !activeNodeIds.has(l.target.id);
          })
          .classed('highlight', false);
      }
    }

    /* Etichette. Con una ricerca attiva sono accese esattamente sui match: il
       declutter è O(n²) e rieseguirlo a ogni carattere sarebbe il costo
       dominante della digitazione — mentre i match sono pochi e mostrarli
       tutti è anche il risultato visivamente giusto. Uscendo dalla ricerca il
       declutter torna a decidere lui, una volta sola. */
    function refreshLabels() {
      if (searchMask) labelSel.classed('big', isMatch);
      else declutterLabels(labelSel);
    }

    nodeSel
      .on('mouseenter', (event, d) => {
        if (!baseActiveIds().has(d.id)) return;
        hoverNodeId = d.id;
        updateVisualState();
      })
      .on('mouseleave', () => {
        hoverNodeId = null;
        updateVisualState();
      })
      .on('click', (event, d) => {
        event.stopPropagation();
        // Un nodo spento — dal focus o dalla ricerca — non si tocca: a 0.12 di
        // opacità un tap è un incidente, non un'intenzione.
        if (!baseActiveIds().has(d.id)) return;
        if (focusNodeId === d.id) return;
        focusNodeId = d.id;
        hoverNodeId = null;
        updateVisualState();
        const focusNodes = nodes.filter(n => n.id === d.id || getNeighbors(d.id).has(n.id));
        zoomToExtent(focusNodes);
      })
      .on('dblclick', (event, d) => {
        event.stopPropagation();
        if (!baseActiveIds().has(d.id)) return;
        const pagePath = d.path;
        if (!pagePath) return;
        window.mobileApp.switchMode('wiki', false);
        window.mobileApp.controllers.wiki.loadWikiPage(this.currentWiki, pagePath, true);
      });

    /* Uscita dal focus su un nodo. Era una closure locale, e l'unico modo di
       uscirne era azzeccare il tap su una zona vuota dell'SVG — che a grafo
       zoomato può non esistere. Promossa a stato del controller, così anche il
       tasto Indietro (handleBack) la trova. Ritorna false se non c'era focus. */
    this._clearFocus = () => {
      if (!focusNodeId) return false;
      focusNodeId = null;
      hoverNodeId = null;
      updateVisualState();
      // Tornando dal focus si reinquadra ciò che la ricerca aveva selezionato,
      // non tutta la wiki: la query è ancora nella barra, e ritrovarsi il grafo
      // intero sarebbe come averla persa.
      zoomToExtent(matchedNodes() || nodes);
      return true;
    };

    /** I nodi disegnati che soddisfano la query, o null se non c'è ricerca. */
    function matchedNodes() {
      if (!searchMask) return null;
      const hits = nodes.filter(isMatch);
      return hits.length ? hits : null;
    }

    /* Ponte fra la barra di ricerca (che vive nel DOM statico) e questo
       disegno. Accetta la maschera grezza del motore — un byte per nodo — e la
       traduce una volta sola nelle due forme che servono: la classe `.match`,
       marcatore stilistico indipendente da dim/highlight, e l'insieme di id che
       fa da base a tutto il resto della logica visiva.

       La conversione in Set si paga qui, a ogni cambio di query, e non dentro
       updateVisualState — che gira anche a ogni hover. */
    this._applySearch = (mask) => {
      // Nessuna ricerca prima, nessuna dopo: uscire subito evita che il
      // riallineamento a fine caricamento rifaccia un declutter O(n²) appena
      // fatto dal disegno. Non si può confrontare l'identità delle maschere:
      // il motore riusa sempre lo stesso buffer, query dopo query.
      if (!mask && !searchMask) return;
      searchMask = mask || null;
      matchedIds = null;
      if (searchMask) {
        matchedIds = new Set();
        for (const n of nodes) if (isMatch(n)) matchedIds.add(n.id);
      }
      nodeSel.classed('match', isMatch);
      labelSel.classed('match', isMatch);
      updateVisualState();
      refreshLabels();
    };

    this._zoomToMatches = () => {
      const hits = matchedNodes();
      if (hits) zoomToExtent(hits);
    };

    /* Il listener è attaccato al nodo `#graph-svg`, che è statico: sopravvive sia
       a `svg.selectAll('*').remove()` sia al cambio di sezione, mentre
       `this._clearFocus` viene azzerato da `_cleanup()`. Il tap sullo sfondo
       dopo un teardown (o durante la fetch che lo segue) deve quindi essere un
       no-op, non un TypeError. */
    svg.on('click', () => { this._clearFocus?.(); });

    zoomToExtent(nodes, 0);

    this.teardown = () => {
      sim.stop();
      svg.selectAll('*').remove();
    };
  }

  /* Sotto-stati della sezione, dal più interno al più esterno: il focus su un
     nodo (grafo zoomato sul nodo e sui suoi vicini) sta *dentro* una ricerca,
     perché ci si arriva toccando un risultato. Il tasto Indietro li smonta in
     quest'ordine, uno per pressione — una pressione, un cambiamento visibile. */
  handleBack() {
    if (this._clearFocus?.()) return true;
    if (this.searchInput?.value) {
      this._setQuery('');
      return true;
    }
    return false;
  }

  handleAction(action) {
    if (action === 'refresh') {
      this.loadGraph(this.currentWiki, false);
    }
  }
}

function sanitizeGroup(g) {
  if (['concepts', 'entities', 'summaries', 'home', 'wiki'].includes(g)) return g;
  return 'other';
}

// Raggio dell'anello di risultato per un nodo di raggio *r*. Rientra di poco più
// del suo spessore (2px, centrato sul tracciato) così resta interamente dentro
// il disco; il minimo evita che sui nodi più piccoli — quelli senza
// collegamenti, r=6 — collassi in un punto o esca dal riempimento.
function ringRadius(r) {
  return Math.max(2.5, r - 3.5);
}

// Testo dell'etichetta: precedenza uniforme title → label → id.
function nodeLabelText(d) {
  return d.title || d.label || d.id || '';
}

// Tronca le etichette lunghe (il titolo completo resta nel tooltip <title>).
function truncateLabel(s, max = 22) {
  if (!s) return '';
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

// Misura una volta il rettangolo dell'etichetta e memorizza l'offset rispetto al
// nodo, così i ricalcoli successivi sono pura aritmetica (nessun reflow SVG).
// Il fallback stimato copre il caso in cui getBBox() non misuri (SVG non ancora
// renderizzato): meglio una larghezza approssimata che zero, che farebbe passare
// tutte le etichette come "non sovrapposte".
function measureLabel(el, d) {
  if (d._lw !== undefined) return;
  let box = null;
  try {
    box = el.getBBox();
  } catch {
    box = null;
  }
  if (box && box.width > 0) {
    d._lw = box.width;
    d._lh = box.height;
    d._lox = box.x - d.x;
    d._loy = box.y - d.y;
  } else {
    const chars = (el.textContent || '').length;
    d._lw = chars * 6;
    d._lh = 11;
    d._lox = -d._lw / 2;
    d._loy = Number(el.getAttribute('dy') || 0) - d._lh;
  }
}

// Mostra il nome di ogni nodo e ne nasconde uno solo quando il suo rettangolo si
// sovrappone a un'etichetta già piazzata. La priorità va ai nodi più collegati:
// in una contesa vince l'hub, che resta quindi sempre etichettato.
// Nota: le etichette stanno dentro il gruppo trasformato dallo zoom, quindi il
// testo scala insieme alle posizioni e le sovrapposizioni non dipendono dal
// livello di zoom — non serve ricalcolare a ogni pinch.
function declutterLabels(labelSel) {
  const items = [];
  labelSel.each(function (d) {
    measureLabel(this, d);
    items.push(d);
  });
  items.sort((a, b) => (b.degree || 0) - (a.degree || 0) || hashString(a.id) - hashString(b.id));

  const PAD = 3;  // margine attorno al testo (l'alone di stroke è 2.5px)
  const placed = [];
  const shown = new Set();
  for (const d of items) {
    const x0 = d.x + d._lox - PAD;
    const y0 = d.y + d._loy - PAD;
    const x1 = x0 + d._lw + PAD * 2;
    const y1 = y0 + d._lh + PAD * 2;
    if (placed.some(r => x0 < r[2] && x1 > r[0] && y0 < r[3] && y1 > r[1])) continue;
    placed.push([x0, y0, x1, y1]);
    shown.add(d.id);
  }
  labelSel.classed('big', d => shown.has(d.id));
}

// Fa convergere la simulazione off-screen e la congela: il grafo viene
// disegnato già assestato e inquadrato, senza fly-apart né drift a riposo.
function settleSimulation(sim, ticks = 300) {
  sim.stop();
  for (let i = 0; i < ticks; i++) sim.tick();
  sim.alpha(0);
}
