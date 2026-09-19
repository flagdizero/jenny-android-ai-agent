/** Mobile Wiki Controller — drawer-based wiki with audits and file tree. */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { escapeHtml, showToast, ensureVendor } from './shared/utils.js';
import { AppState } from './shared/state.js';
import { renderTree, wireTreeFolder, wireTreeFiles } from './shared/tree-renderer.js';
import { i18n } from './shared/i18n.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';
import { isOpenableProjectName } from './shared/conversation-list.js';

export class WikiController {
  constructor() {
    this.contentEl = document.getElementById('wiki-content');
    this.auditDrawerBody = document.getElementById('drawer-audit-body');
    this.filesDrawerBody = document.getElementById('drawer-files-body');
    this.currentWiki = null;
    this.currentPath = null;
    this.currentTitle = null;
    this.isHome = false;
    this.rawMarkdown = '';
    this._auditMode = 'open';  // 'open' | 'resolved'
    this.loadingEl = document.getElementById('wiki-loading');
    this._pending = null;
    this._popover = document.getElementById('wiki-feedback-popover');
    this._popoverBtn = document.getElementById('wiki-feedback-trigger');
    this.lastWikiPage = {};
    // Monotonic load token: guards against stale-response races when the user
    // navigates rapidly (only the latest load* call writes the content area).
    this._loadToken = 0;
    // Contatore di generazione: incrementato in deactivate(). Il token è
    // monotono solo rispetto ad altre navigazioni *della wiki*, ed è cieco
    // all'abbandono della sezione: una continuazione che riprende dopo il
    // cambio vista scriverebbe titolo, drawer e history di un'altra schermata.
    this._gen = 0;
    // Prima vista della sezione: init() carica solo la configurazione, e la
    // navigazione iniziale la decide il chiamante — o activate(), se nessuno
    // lo ha fatto. `_settled` = c'è una vista disegnata; `_inFlightGen` = la
    // generazione del caricamento in volo, che dopo un deactivate è ormai
    // condannata (v. _loadInitialView).
    this._settled = false;
    this._inFlightGen = -1;
    AppState.wiki = AppState.wiki || {};
    // Re-render al cambio lingua: se la wiki è la vista attiva ri-traduce
    // subito (breadcrumb "Home", toggle/etichette audit, titolo header, stati
    // vuoti); altrimenti marca "sporco" e rinvia il re-render al rientro in
    // vista (activate), così non si sovrascrive l'header di un'altra vista.
    this._localeDirty = false;
    i18n.onLocaleChange(() => this._onLocaleChange());
    // Cambiare progetto mentre la wiki è aperta la riaggancia; se è aperta
    // un'altra vista basta marcare sporco, e ci pensa `activate()`.
    AppState.on('pinnedWiki', () => this._onPinChange());
    this._ready = this.init();
  }

  get ready() { return this._ready; }

  /** La wiki a cui questa vista è agganciata, o `null` nella chat personale.
   *
   *  Dentro un progetto le viste mostrano *quel* progetto e basta: la Home
   *  elenca tutte le wiki, e quell'elenco è esattamente quello che il prompt
   *  di un progetto ha smesso di portarsi dietro nel 2.2 — Claude Code non ti
   *  parla degli altri tuoi repository. Il valore lo pubblica lo scope chip,
   *  che è l'unico a sapere in che conversazione siamo.
   */
  get pinnedWiki() { return AppState.pinnedWiki || null; }

  /** Riaggancia la vista dopo un cambio di progetto (o il ritorno alla
   *  personale, che scioglie l'aggancio e rimette la Home). */
  _onPinChange() {
    // Niente disegnato: `activate()` partirà comunque dalla vista giusta.
    if (!this._settled) return;
    if (window.mobileApp?.currentMode !== 'wiki') {
      this._settled = false;   // ricalcolo al rientro
      return;
    }
    const pin = this.pinnedWiki;
    if (pin) this.loadWikiPage(pin, 'index.md', false);
    else this.loadHome(false);
  }

  showLoading() {
    if (this.loadingEl) this.loadingEl.classList.add('active');
  }
  hideLoading() {
    if (this.loadingEl) this.loadingEl.classList.remove('active');
  }

  // Sanifica l'HTML del server; se DOMPurify manca, mostra il markdown grezzo
  // escapato invece di una pagina bianca silenziosa.
  _safeHtml(html, raw) {
    if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html);
    console.warn('DOMPurify non disponibile: fallback a markdown escapato');
    return `<pre class="wiki-raw-fallback">${escapeHtml(raw || '')}</pre>`;
  }

  /** Carica **solo** la configurazione. La prima navigazione non sta qui.
   *
   *  Prima init() ri-derivava la vista dalla query string dopo l'await e
   *  chiamava load*, bruciando `_loadToken`: chi ci aveva appena portati in
   *  wiki (`switchMode('wiki', false)` seguito da `loadHome(true)`) veniva
   *  invalidato **prima** della sua `pushNav`. Risultato: la schermata giusta a
   *  video e nessuna entry di history dietro, cioè un Indietro che salta via
   *  dalla sezione. */
  async init() {
    this.showLoading();
    try {
      const cfg = await api.getConfig();
      AppState.wiki = AppState.wiki || {};
      AppState.wiki.author = cfg.author || 'me';
      AppState.wiki.wikis = cfg.wikis || [];
    } catch {}
    this.hideLoading();
    this.initWikiFeedback();
  }

  /** Prima vista della sezione, quando nessun chiamante l'ha già scelta.
   *
   *  `switchMode` invoca questo activate in un microtask (`ready.then(...)`),
   *  quindi un `loadHome(true)`/`loadWikiPage(...)` lanciato sincronamente
   *  subito dopo lo switch ha già marcato il proprio caricamento come in volo:
   *  la sua navigazione — con la sua pushNav — resta l'unica.
   *
   *  Il caricamento in volo trattiene questo activate solo finché è **ancora
   *  valido**. Dopo un `deactivate()` la generazione è cambiata e quel
   *  caricamento non disegnerà mai: senza il confronto, uscire dalla wiki
   *  mentre la prima pagina è in arrivo la lasciava sul suo "Caricamento…" per
   *  il resto della sessione. */
  _loadInitialView() {
    if (this._settled || this._inFlightGen === this._gen) return;
    const params = new URLSearchParams(window.location.search);
    // L'aggancio vince sull'URL: una `?wiki=` di un altro progetto è una vista
    // che questa conversazione non deve avere, da qualunque parte arrivi.
    const pin = this.pinnedWiki;
    const wiki = pin || params.get('wiki');
    const page = (!pin || pin === params.get('wiki')) ? (params.get('page') || 'index.md') : 'index.md';
    if (wiki) this.loadWikiPage(wiki, page, false);
    else this.loadHome(false);
  }

  activate() {
    this._loadInitialView();
    // `setMode` ha appena ridisegnato le azioni, e il tasto rinasce spento a
    // ogni rientro. Riaccenderlo non è compito del caricamento: quando la vista
    // era già disegnata `_loadInitialView` non ne fa nessuno, e il tasto
    // resterebbe spento sopra una pagina che il suo progetto ce l'ha.
    this._syncProjectAction();
    if (this._localeDirty) {
      this._localeDirty = false;
      this._rerenderForLocale();
    }
    this._selectionHandler = () => {
      // Gli audit si creano solo dentro una wiki: niente popover in Home.
      if (this.isHome || !this.currentWiki) {
        this._popover?.classList.add('hidden');
        return;
      }
      const sel = document.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
        this._popover?.classList.add('hidden');
        return;
      }
      const range = sel.getRangeAt(0);
      if (!this.contentEl.contains(range.commonAncestorContainer)) {
        this._popover?.classList.add('hidden');
        return;
      }
      const rect = range.getBoundingClientRect();
      this._popover.style.left = `${window.scrollX + rect.left}px`;
      this._popover.style.top = `${window.scrollY + rect.top - 42}px`;
      this._popover.classList.remove('hidden');
    };
    document.addEventListener('selectionchange', this._selectionHandler);
  }

  deactivate() {
    // Ogni continuazione in volo (pagina, albero, audit) deve poter scoprire
    // che la sezione è stata lasciata: da qui in poi non scrive più niente.
    this._gen++;
    this._popover?.classList.add('hidden');
    this._pending = null;
    if (this._selectionHandler) {
      document.removeEventListener('selectionchange', this._selectionHandler);
      this._selectionHandler = null;
    }
  }

  _onLocaleChange() {
    if (window.mobileApp?.currentMode === 'wiki') {
      this._rerenderForLocale();
    } else {
      this._localeDirty = true;
    }
  }

  _rerenderForLocale() {
    // Ricarica la vista corrente senza toccare la history: rigenera tutta la
    // chrome localizzata. Il markdown della pagina è indipendente dalla lingua,
    // ma il re-fetch è l'unico modo per ricostruire breadcrumb/audit/titolo; il
    // guard _loadToken in load* copre eventuali race con altri caricamenti.
    if (this.isHome) {
      this.loadHome(false);
    } else if (this.currentWiki) {
      this.loadWikiPage(this.currentWiki, this.currentPath, false);
    }
  }

  /** true se questo caricamento è stato superato da uno più recente (`token`)
   *  oppure se nel frattempo si è usciti dalla sezione (`gen`). Le due domande
   *  sono diverse e servono entrambe: il token non sa niente dell'abbandono. */
  _stale(token, gen) {
    return token !== this._loadToken || gen !== this._gen;
  }

  async loadHome(pushHistory = true) {
    // Unico imbuto verso la Home: breadcrumb, `_index.md` dell'albero e
    // wikilink passano tutti di qui, quindi chiuderla qui le chiude tutte.
    const pin = this.pinnedWiki;
    if (pin) return this.loadWikiPage(pin, 'index.md', pushHistory);
    const token = ++this._loadToken;
    const gen = this._gen;
    this._inFlightGen = gen;
    this.isHome = true;
    this.currentWiki = null;
    this.currentPath = '_index.md';
    this._syncProjectAction();
    AppState.wiki.currentWiki = null;
    AppState.wiki.currentPath = '_index.md';
    this.contentEl.innerHTML = `<p class="wiki-blockq">${i18n.t('wiki.loading')}</p>`;
    try {
      const data = await api.getPage({});
      if (this._stale(token, gen)) return;  // superseded, or section left
      this._settled = true;
      this.rawMarkdown = data.raw;
      this.currentTitle = null;
      this.contentEl.innerHTML = this._safeHtml(data.html, data.raw);
      window.mobileApp.header.setTitle(i18n.t('wiki.home'), 'wiki');
      this._renderBreadcrumbs();
      this._wireWikiLinks();
      // Albero e audit hanno una fetch propria: senza passare loro il token
      // riempivano i drawer con l'albero e gli audit della pagina *vecchia*,
      // sopra quelli della pagina nuova.
      await this.loadTree(undefined, token, gen);
      if (this._stale(token, gen)) return;
      await this.loadAudits(null, token, gen);
      if (this._stale(token, gen)) return;
      this._renderLatex();
      if (pushHistory) window.mobileApp.pushNav({ mode: 'wiki' });
    } catch (err) {
      if (this._stale(token, gen)) return;  // don't surface errors of stale loads
      this._settled = true;  // anche una pagina d'errore è una vista disegnata
      const msg = err.message || '';
      if (msg.includes('404') || msg.includes('not found') || msg.includes('Page not found') || msg.includes('file not found')) {
        let isEmpty = true;
        try {
          const tree = await api.getTree();
          isEmpty = this._countMdFiles(tree) === 0;
        } catch {
          isEmpty = true;
        }
        if (this._stale(token, gen)) return;  // a newer load started during getTree
        if (isEmpty) {
          this.contentEl.innerHTML = `<p class="wiki-blockq">${i18n.t('wiki.empty')}</p>`;
        } else {
          this.contentEl.innerHTML = `<p class="wiki-blockq" style="color: var(--error)">${i18n.t('wiki.homeMissing')}</p>`;
        }
        window.mobileApp.header.setTitle(i18n.t('wiki.home'), 'wiki');
        this._renderBreadcrumbs();
        this.loadTree(undefined, token, gen);
        this.loadAudits(null, token, gen);
        if (pushHistory) window.mobileApp.pushNav({ mode: 'wiki' });
      } else {
        this.contentEl.innerHTML = `<p class="wiki-blockq" style="color: var(--error)">${i18n.t('common.error')}: ${escapeHtml(msg)}</p>`;
      }
    }
  }

  _countMdFiles(node) {
    if (!node) return 0;
    if (node.kind === 'file') return 1;
    if (!Array.isArray(node.children)) return 0;
    return node.children.reduce((sum, child) => sum + this._countMdFiles(child), 0);
  }

  async loadWikiPage(wiki, page, pushHistory = true) {
    const pin = this.pinnedWiki;
    if (pin && wiki && wiki !== pin) {
      // Un link che porta fuori dal progetto non ci porta: si resta dove si è
      // e lo si dice, invece di cambiare progetto sotto ai piedi.
      showToast(i18n.t('wiki.onlyThisProject', { name: pin }), 'error');
      return;
    }
    if (pin && !wiki) wiki = pin;
    const token = ++this._loadToken;
    const gen = this._gen;
    this._inFlightGen = gen;
    this.isHome = false;
    this.currentWiki = wiki;
    this.currentPath = page;
    // Accanto all'assegnazione, e non dopo la fetch: il tasto nomina la wiki
    // che questa vista sta mostrando, e una pagina che non si carica la mostra
    // comunque (resta il progetto, cambia la pagina).
    this._syncProjectAction();
    AppState.wiki.currentWiki = wiki;
    AppState.wiki.currentPath = page;
    this.contentEl.innerHTML = `<p class="wiki-blockq">${i18n.t('wiki.loading')}</p>`;
    try {
      const data = await api.getPage({ wiki, page });
      if (this._stale(token, gen)) return;  // superseded, or section left
      this._settled = true;
      this.currentPath = data.page || page;
      this.currentWiki = data.wiki || wiki;
      this.currentTitle = data.title || null;
      this.rawMarkdown = data.raw;
      this.contentEl.innerHTML = this._safeHtml(data.html, data.raw);
      window.mobileApp.header.setTitle(data.title || page, 'wiki');
      this._renderBreadcrumbs();
      this._wireWikiLinks();
      // v. loadHome: senza token, albero e audit della pagina vecchia
      // arrivavano sopra quelli della pagina nuova.
      await this.loadTree(this.currentWiki, token, gen);
      if (this._stale(token, gen)) return;
      await this.loadAudits(this.currentPath, token, gen);
      if (this._stale(token, gen)) return;
      this._renderLatex();
      this._renderMermaid(token, gen);
      if (pushHistory) {
        window.mobileApp.pushNav({ mode: 'wiki', wikiPage: true, wiki: this.currentWiki, page: this.currentPath });
      }
      this.lastWikiPage[this.currentWiki] = this.currentPath;
    } catch (err) {
      if (this._stale(token, gen)) return;  // don't surface errors of stale loads
      this._settled = true;  // anche una pagina d'errore è una vista disegnata
      this.contentEl.innerHTML = `<p class="wiki-blockq" style="color: var(--error)">${i18n.t('common.error')}: ${escapeHtml(err.message)}</p>`;
    }
  }

  /** Accende il tasto «chat del progetto» se questa vista ne ha uno da aprire.
   *
   *  Sulla Home (`isHome`) non c'è: quell'indice *è* l'elenco delle wiki, e non
   *  ce n'è una da aprire più delle altre. Su una cartella con un nome che il
   *  server non accetta come progetto non c'è nemmeno: aprirla porterebbe in
   *  un'**altra** conversazione (v. `isOpenableProjectName`).
   */
  _syncProjectAction() {
    const header = window.mobileApp?.header;
    if (!header) return;
    const wiki = this.isHome ? null : this.currentWiki;
    if (wiki && isOpenableProjectName(wiki)) header.showAction('open-project-chat', 'wiki');
    else header.hideAction('open-project-chat', 'wiki');
  }

  _renderBreadcrumbs() {
    let existing = this.contentEl.querySelector('.wiki-breadcrumbs');
    if (existing) existing.remove();
    const wrap = document.createElement('div');
    wrap.className = 'wiki-breadcrumbs';
    const sep = ` <span class="bc-sep">/</span> `;

    if (this.isHome) {
      wrap.innerHTML = `<span class="bc-current">${i18n.t('wiki.home')}</span>`;
    } else {
      const crumbs = [];
      // Dentro un progetto la Home non è raggiungibile: un crumb che non
      // naviga è peggio che assente, perché promette una via d'uscita.
      if (!this.pinnedWiki) {
        crumbs.push(`<a class="bc-link" data-home href="/?mode=wiki">${i18n.t('wiki.home')}</a>`);
      }

      // Segmenti del path: cartelle intermedie + foglia (senza estensione).
      let segs = (this.currentPath || '')
        .replace(/\\/g, '/').replace(/\.md$/i, '').split('/').filter(Boolean);
      // Un trailing "index" (root wiki o folder-split) è ridondante col crumb padre.
      if (segs.length > 1 && segs[segs.length - 1] === 'index') segs.pop();
      const onWikiRoot = segs.length === 0 || (segs.length === 1 && segs[0] === 'index');

      const wikiCrumb = onWikiRoot
        ? `<span class="bc-current">${escapeHtml(this.currentWiki)}</span>`
        : `<a class="bc-link" data-wiki="${escapeHtml(this.currentWiki)}" href="/?mode=wiki&wiki=${encodeURIComponent(this.currentWiki)}">${escapeHtml(this.currentWiki)}</a>`;
      crumbs.push(wikiCrumb);

      if (!onWikiRoot) {
        const leafIdx = segs.length - 1;
        segs.forEach((seg, i) => {
          if (i === leafIdx) {
            const label = this.currentTitle || seg;
            crumbs.push(`<span class="bc-current">${escapeHtml(label)}</span>`);
          } else {
            // Cartella intermedia: nessuna pagina garantita → testo non-link.
            crumbs.push(`<span class="bc-folder">${escapeHtml(seg)}</span>`);
          }
        });
      }
      wrap.innerHTML = crumbs.join(sep);
    }

    this.contentEl.insertBefore(wrap, this.contentEl.firstChild);
    wrap.querySelectorAll('a[data-home]').forEach(a => {
      a.addEventListener('click', (e) => { e.preventDefault(); this.loadHome(); });
    });
    wrap.querySelectorAll('a[data-wiki]').forEach(a => {
      a.addEventListener('click', (e) => { e.preventDefault(); this.loadWikiPage(a.dataset.wiki, 'index.md'); });
    });
  }

  /** Cabla TUTTI gli `a[href]` della pagina, non solo `a.wikilink`.
      `wiki.py` emette la classe `wikilink` soltanto per `[[Target]]`: un
      `[testo](altra.md)`, un `[TOC]` o un `[x](www.google.com)` scritti a mano
      restavano link veri, cioè navigazioni di main frame verso l'origine del
      gateway. Esito: la SPA ricaricata senza il fragment `#bs=` (de-autenticata)
      o sostituita da un 404 sotto `/api/`, con `window.mobileApp` — e quindi il
      tasto Indietro — spariti. Le ancore interne, che prima impilavano una entry
      di history con `state: null`, diventano uno scroll. Da qui non si naviga
      mai: si carica una pagina wiki, si scrolla, si esce dalla WebView, o niente. */
  _wireWikiLinks() {
    this.contentEl.querySelectorAll('a[href]').forEach(a => {
      // I breadcrumb hanno già il loro listener da _renderBreadcrumbs (che gira
      // prima di qui): un secondo handler li farebbe caricare E avvisare.
      if (a.hasAttribute('data-home') || a.hasAttribute('data-wiki')) return;
      a.addEventListener('click', (e) => {
        e.preventDefault();
        const href = a.getAttribute('href') || '';
        if (href.startsWith('#')) { this._scrollToHash(href); return; }
        let url = null;
        try { url = new URL(href, window.location.href); } catch (_) { url = null; }
        const scheme = url?.protocol || '';
        const isWeb = scheme === 'http:' || scheme === 'https:';
        if ((isWeb && url.origin !== window.location.origin) || scheme === 'mailto:' || scheme === 'tel:') {
          this._openOutsideWebView(url.href);
          return;
        }
        // Link markdown relativo a un'altra pagina della stessa wiki:
        // `[nota](note.md)`, `[api](ref/api.md)`. Non ha la classe wikilink —
        // il renderer la mette solo per `[[Target]]` — quindi finiva nel ramo
        // "non apribile" e restava inerte, pur essendo il modo più naturale di
        // scrivere un link a mano. Si risolve contro la cartella della pagina
        // corrente e diventa una navigazione wiki vera.
        const relPage = this._resolveRelativePage(href);
        if (relPage) {
          this.loadWikiPage(this.currentWiki, relPage);
          if (url?.hash) this._scrollToHash(url.hash);
          return;
        }
        if (!url || !isWeb || !a.classList.contains('wikilink') || href.includes('..')) {
          if (href.includes('..')) console.warn('Dead or invalid wikilink:', href);
          showToast(i18n.t('common.linkNotOpenable'), 'info');
          return;
        }
        const wiki = url.searchParams.get('wiki') || this.currentWiki;
        const page = url.searchParams.get('page');
        const hash = url.hash;
        if (wiki && page) { this.loadWikiPage(wiki, page); }
        else if (wiki) { this.loadWikiPage(wiki, 'index.md'); }
        else { this.loadHome(); }
        if (hash) this._scrollToHash(hash);
      });
    });
  }

  /** Apre un URL fuori dalla WebView: gemello di `_openOutsideWebView` in
      mobile-chat.js. `window.open` non apre una finestra (la WebView non
      supporta le finestre multiple): la richiesta ricade su
      `shouldOverrideUrlLoading`, che per un'origine non-gateway apre una Chrome
      Custom Tab e lascia la SPA dov'è. */
  _openOutsideWebView(href) {
    try {
      window.open(href, '_blank', 'noopener');
    } catch (err) {
      console.warn('Could not open external link:', err);
      showToast(i18n.t('common.linkNotOpenable'), 'error');
    }
  }

  /* Path della pagina wiki per un href markdown relativo, o null se l'href non
     è di quella forma. Volutamente conservativo: niente schemi, niente path
     assoluti, niente `..` (risalire vorrebbe dire inventare una semantica di
     traversata che il resto della wiki non ha), solo `.md` e solo dentro la
     wiki corrente. Tutto il resto resta al ramo che avvisa e non naviga. */
  _resolveRelativePage(href) {
    if (!this.currentWiki || this.isHome) return null;
    const clean = href.split('#')[0].split('?')[0];
    if (!clean || !/\.md$/i.test(clean)) return null;
    if (clean.startsWith('/') || clean.includes('..') || /^[a-z][a-z0-9+.-]*:/i.test(clean)) return null;
    const dir = (this.currentPath || '').replace(/\\/g, '/').split('/').slice(0, -1);
    const rel = clean.replace(/^\.\//, '');
    return [...dir, ...rel.split('/')].filter(Boolean).join('/');
  }

  /* L'ancora si cerca *dentro il contenuto della pagina*, non nel documento:
     con getElementById un `[x](#dock)` scritto in una wiki portava lo scroll su
     un elemento di chrome della SPA. Stessa disciplina del gemello in chat. */
  _scrollToHash(hash) {
    const id = hash.slice(1);
    if (!id) return;
    setTimeout(() => {
      let el = null;
      try {
        el = this.contentEl?.querySelector(`#${CSS.escape(id)}, a[name="${CSS.escape(id)}"]`);
      } catch (_) { el = null; }
      if (el) el.scrollIntoView({ behavior: 'smooth' });
      else showToast(i18n.t('common.linkNotOpenable'), 'info');
    }, 200);
  }

  /** I default riprendono il caricamento corrente: chi chiama dall'esterno
   *  (toggle, resolve) non deve conoscere il protocollo del token. */
  async loadTree(wiki, token = this._loadToken, gen = this._gen) {
    try {
      const data = await api.getTree(wiki);
      if (this._stale(token, gen)) return;
      this.filesDrawerBody.innerHTML = '';
      const nav = document.createElement('nav');
      nav.className = 'wiki-tree';
      nav.appendChild(renderTree(data.children || []));
      this.filesDrawerBody.appendChild(nav);
      this.wireTreeLinks(wiki);
    } catch (err) {
      if (this._stale(token, gen)) return;
      this.filesDrawerBody.innerHTML = `<div class="ftree-path">${i18n.t('wiki.failedToLoadFiles')}</div>`;
    }
  }

  wireTreeLinks(wiki) {
    const self = this;
    this.filesDrawerBody.querySelectorAll('.tree-folder').forEach(folder => wireTreeFolder(folder, {}));
    wireTreeFiles(this.filesDrawerBody.querySelectorAll('.tree-file'), (path) => {
      // Remove active from previously active item
      this.filesDrawerBody.querySelectorAll('.tree-file.active').forEach(el => el.classList.remove('active'));
      // Find and activate the clicked item
      const target = document.activeElement?.closest('.tree-file') ||
        this.filesDrawerBody.querySelector(`.tree-file[data-path="${CSS.escape(path)}"]`);
      target?.classList.add('active');
      if (path === '_index.md') { self.loadHome(); }
      else {
        const parts = path.replace(/\\/g, '/').split('/');
        if (parts.length >= 2 && parts[1] === 'wiki') {
          self.loadWikiPage(parts[0], parts.slice(2).join('/') || 'index.md');
        } else if (wiki) {
          self.loadWikiPage(wiki, path);
        }
      }
      window.mobileApp.drawer.closeAll();
    });
  }

  async _renderMermaid(token = this._loadToken, gen = this._gen) {
    // Carica mermaid solo se in pagina c'è davvero un diagramma: aprire la
    // wiki su una nota di testo non deve costare 3,2 MB di JS.
    if (!this.contentEl.querySelector('pre.mermaid-block')) return;
    if (typeof mermaid === 'undefined') {
      try {
        // 3,2 MB su rete mobile: qui dentro ci sta comodamente una
        // navigazione, e al ritorno la pagina a schermo può essere un'altra.
        await ensureVendor('/html-mobile/assets/vendor/mermaid@10/dist/mermaid.min.js');
      } catch (err) {
        console.error('Mermaid load failed:', err);
        return;
      }
      if (this._stale(token, gen)) return;
    }
    // Senza initialize, mermaid usa la palette chiara di default: riquadri
    // lavanda su fondo scuro. Dei sette temi quattro sono scuri (chanel,
    // synthwave, kyoto, sticker) e tre chiari (fumetto, y2k, pietra), quindi la
    // scelta segue `color-scheme` invece di essere fissata. Rieseguito a ogni
    // render, così un cambio di tema a pagina aperta viene raccolto.
    const light = getComputedStyle(document.documentElement).colorScheme === 'light';
    mermaid.initialize({
      startOnLoad: false,
      theme: light ? 'default' : 'dark',
      // I diagrammi arrivano dal modello: `strict` tiene l'HTML fuori dalle
      // etichette. È già il default di mermaid, lo rendiamo esplicito.
      securityLevel: 'strict',
    });
    this.contentEl.querySelectorAll('pre.mermaid-block').forEach((block, i) => {
      const code = block.querySelector('code');
      if (!code) return;
      try {
        mermaid.render(`mermaid-${Date.now()}-${i}`, code.textContent).then(({ svg }) => {
          if (this._stale(token, gen)) return;  // `block` è già staccato: non riscriverlo
          block.innerHTML = svg;
        });
      } catch (err) { console.error('Mermaid render failed:', err); }
    });
  }

  _renderLatex() {
    if (typeof renderMathInElement === 'function') {
      try { renderMathInElement(this.contentEl, { delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}] }); }
      catch (e) { /* silent */ }
    }
  }

  _renderAuditToggle() {
    const mk = (mode, label) =>
      `<button data-audit-mode="${mode}" class="${this._auditMode === mode ? 'active' : ''}">${label}</button>`;
    return `<div class="audit-mode-toggle">${mk('open', i18n.t('wiki.auditsOpen'))}${mk('resolved', i18n.t('wiki.auditsResolved'))}</div>`;
  }

  _wireAuditToggle() {
    this.auditDrawerBody.querySelectorAll('button[data-audit-mode]').forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.auditMode;
        if (mode === this._auditMode) return;
        this._auditMode = mode;
        this.loadAudits(this.currentPath);
      });
    });
  }

  /** v. loadTree per i default. */
  async loadAudits(path, token = this._loadToken, gen = this._gen) {
    const toggle = this._renderAuditToggle();
    try {
      const data = await api.getAudits({ wiki: this.currentWiki, targetPath: path, mode: this._auditMode });
      if (this._stale(token, gen)) return;
      if (!data.entries?.length) {
        const emptyMsg = this._auditMode === 'resolved'
          ? i18n.t('wiki.noResolvedAudits') : i18n.t('wiki.noOpenAudits');
        this.auditDrawerBody.innerHTML = `${toggle}<div class="audit-item"><div class="ai-title">${emptyMsg}</div></div>`;
        this._wireAuditToggle();
        return;
      }
      const isOpen = this._auditMode === 'open';
      this.auditDrawerBody.innerHTML = toggle + data.entries.map(e => {
        const body = (e.body || '')
          .replace(/^#\s*Comment\s*/i, '')
          .split(/^#\s*Resolution/im)[0]
          .replace(/<!--[\s\S]*?-->/g, '').trim();
        const when = new Date(e.created).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        const sev = escapeHtml(e.severity || '');
        const tagClass = isOpen ? `tag-${sev}` : 'tag-resolved';
        const tagLabel = isOpen ? sev : i18n.t('wiki.auditsResolved');
        const resolveBtn = isOpen
          ? `<div style="margin-top:4px"><button data-resolve="${escapeHtml(e.id)}" style="font-size:10px;padding:2px 6px;background:var(--surface-2);border:0.5px solid var(--border);color:var(--text-faint);border-radius:var(--radius-sm);cursor:pointer;">${i18n.t('wiki.markResolved')}</button></div>`
          : '';
        return `<div class="audit-item sev-${sev}">
          <div class="ai-title"><i class="ti ti-alert-circle" style="color:var(--accent);font-size:14px"></i> ${escapeHtml(body.substring(0, 50))}${body.length > 50 ? '...' : ''} <span class="audit-tag ${tagClass}">${tagLabel}</span></div>
          <div class="ai-meta">${escapeHtml(e.author)} \u00b7 ${when}</div>
          ${resolveBtn}
        </div>`;
      }).join('');
      this._wireAuditToggle();
      this.auditDrawerBody.querySelectorAll('button[data-resolve]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.dataset.resolve;
          const note = await promptDialog(i18n.t('wiki.resolutionNote'), { placeholder: i18n.t('wiki.resolutionNote') });
          if (note === null) return;  // annullato
          try {
            await rpc.resolveAudit(id, this.currentWiki, note);
            await this.loadAudits(this.currentPath);
          } catch (err) { showToast(i18n.t('wiki.failedToResolve') + err.message, 'error'); }
        });
      });
    } catch (err) {
      if (this._stale(token, gen)) return;
      this.auditDrawerBody.innerHTML = `${toggle}<div class="audit-item"><div class="ai-title">${i18n.t('wiki.failedToLoadAudits')}</div></div>`;
      this._wireAuditToggle();
    }
  }

  initWikiFeedback() {
    const popoverBtn = this._popoverBtn;
    const dialog = document.getElementById('wiki-feedback-dialog');
    const preview = document.getElementById('wiki-feedback-preview');
    const textarea = document.getElementById('wiki-feedback-comment');
    const form = document.getElementById('wiki-feedback-form');
    const cancelBtn = document.getElementById('wiki-feedback-cancel');

    popoverBtn?.addEventListener('click', async () => {
      const sel = document.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
      const text = sel.toString();
      if (!text.trim()) return;
      const raw = this.rawMarkdown;
      const offsets = this._resolveSelectionToOffsets(raw, text);
      if (!offsets) {
        // Ancoraggio non univoco: l'audit potrebbe puntare al punto sbagliato.
        // Avvisa esplicitamente invece di ancorare in sordina.
        if (!(await confirmDialog(i18n.t('wiki.createAuditAnyway')))) return;
      }
      this._pending = offsets || { selStart: raw.indexOf(text), selEnd: raw.indexOf(text) + text.length };
      if (this._pending.selStart < 0) { showToast(i18n.t('wiki.couldNotCreateAudit'), 'error'); return; }
      preview.textContent = text.length > 400 ? text.slice(0, 400) + '\u2026' : text;
      textarea.value = '';
      dialog?.showModal();
      setTimeout(() => textarea?.focus(), 30);
      if (this._popover) this._popover.classList.add('hidden');
    });

    cancelBtn?.addEventListener('click', (e) => { e.preventDefault(); dialog?.close(); this._pending = null; });

    form?.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!this._pending || !this.currentWiki) { showToast(i18n.t('wiki.auditsOnlyInWiki'), 'error'); return; }
      const severity = form.querySelector('input[name="severity"]:checked')?.value || 'warn';
      const comment = textarea?.value.trim();
      if (!comment) { showToast(i18n.t('wiki.commentEmpty'), 'error'); return; }
      try {
        await api.createAudit({
          wiki: this.currentWiki, target: this.currentPath,
          rawMarkdown: this.rawMarkdown,
          selStart: this._pending.selStart, selEnd: this._pending.selEnd,
          comment, severity, author: AppState.wiki?.author || 'anonymous',
        });
        dialog?.close();
        this._pending = null;
        await this.loadAudits(this.currentPath);
      } catch (err) { showToast(`${i18n.t('common.error')}: ${err.message}`, 'error'); }
    });
  }

  _resolveSelectionToOffsets(raw, selText) {
    if (!selText) return null;
    // Match univoco tollerante agli spazi: i run di whitespace della selezione
    // (resa dal DOM) matchano \s+ nel markdown grezzo, così una selezione che
    // attraversa a-capo/indentazioni della sorgente risolve comunque. Ritorna
    // gli offset reali nel raw (null se assente o non univoco).
    const uniqueMatch = (str) => {
      const trimmed = (str || '').trim();
      if (!trimmed) return null;
      const pattern = trimmed
        .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        .replace(/\s+/g, '\\s+');
      let re;
      try { re = new RegExp(pattern, 'g'); } catch { return null; }
      let m, first = null, count = 0;
      while ((m = re.exec(raw)) !== null) {
        if (count === 0) first = m;
        count++;
        if (count > 1) break;
        if (m.index === re.lastIndex) re.lastIndex++;  // guardia match vuoto
      }
      return count === 1 ? { selStart: first.index, selEnd: first.index + first[0].length } : null;
    };
    const direct = uniqueMatch(selText);
    if (direct) return direct;
    const words = selText.split(/\s+/).filter(Boolean);
    for (let len = Math.min(words.length, 10); len >= 3; len--) {
      for (let start = 0; start + len <= words.length; start++) {
        const hit = uniqueMatch(words.slice(start, start + len).join(' '));
        if (hit) return hit;
      }
    }
    return null;
  }
}
