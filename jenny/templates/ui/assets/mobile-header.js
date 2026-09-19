/** View Title Controller — in-content view headings and actions.
 *
 * Replaces the old fixed 40px header: each view owns a `.view-title-mount`
 * (index.html) where the big scrolling-style title and its action buttons
 * are rendered. Chat and onboarding have no mount (chat renders its own
 * identity line inside the scroll area).
 */

import { i18n } from './shared/i18n.js';
import { scopeChip } from './shared/scope-chip.js';
import { isOpenableProjectName } from './shared/conversation-list.js';

/** Il tasto che dalla wiki porta nella chat del progetto che la possiede.
 *
 *  Il collegamento esisteva in una direzione sola: scegliere un progetto nello
 *  scope chip aggancia le viste wiki e grafo a quella wiki
 *  (`AppState.pinnedWiki`), ma dalla wiki non si tornava alla sua conversazione
 *  se non passando dalla chat e riaprendo la tendina. La destinazione è già a
 *  schermo — un progetto **è** una wiki, e il nome della cartella è il nome
 *  della sessione (`project:<nome>`) — quindi non c'è niente da chiedere a
 *  nessuno.
 *
 *  Nasce spento: lo accende chi sa quale wiki è a schermo
 *  (`_syncProjectAction`, nei due controller). La Home non ne ha uno da aprire
 *  — il grafo di tutte le wiki e l'indice delle wiki *sono* l'elenco dei
 *  progetti, e nessuno di quelli è più aperto degli altri — e una cartella con
 *  un nome che il server non accetta non ne ha uno raggiungibile.
 *
 *  Una funzione e non una costante perché i due elenchi ne prendono uno per
 *  uno: `renderActions` oggi non scrive nelle voci che legge, ma un flag posato
 *  su un literal condiviso comparirebbe anche nell'altra vista.
 */
function projectChatAction() {
  return {
    icon: 'ti-message-2',
    title: i18n.t('header.openProjectChat'),
    action: 'open-project-chat',
    hidden: true,
  };
}

export class ViewTitleController {
  constructor() {
    this.currentMode = null;
    this.titleEl = null;
    this.actionsEl = null;
    this.modeConfigs = {
      apps: {
        title: i18n.t('nav.apps'),
        actions: [
          { icon: 'ti-eye-off', title: i18n.t('header.showHiddenApps'), action: 'toggle-hidden' }
        ]
      },
      workspace: {
        title: i18n.t('nav.workspace'),
        actions: [
          { icon: 'ti-arrow-left', title: i18n.t('header.back'), action: 'ws-back', hidden: true },
          { icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' },
          { icon: 'ti-plus', title: i18n.t('header.new'), action: 'ws-new' }
        ]
      },
      wiki: {
        title: i18n.t('nav.wiki'),
        actions: [
          { icon: 'ti-clipboard-list', title: i18n.t('header.audits'), drawer: 'audit' },
          { icon: 'ti-folder', title: i18n.t('header.files'), drawer: 'files' },
          { type: 'sep' },
          { icon: 'ti-topology-star', title: i18n.t('header.graph'), action: 'graph' },
          projectChatAction()
        ]
      },
      graph: {
        title: i18n.t('nav.wiki'),
        actions: [
          { icon: 'ti-file-text', title: i18n.t('header.pages'), action: 'open-pages' },
          { icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' },
          projectChatAction()
        ]
      },
      settings: {
        title: i18n.t('nav.settings'),
        actions: [
          { icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' }
        ]
      }
    };

    i18n.onLocaleChange(() => this._refreshTitles());
  }

  _refreshTitles() {
    this.modeConfigs.apps.title = i18n.t('nav.apps');
    this.modeConfigs.workspace.title = i18n.t('nav.workspace');
    this.modeConfigs.wiki.title = i18n.t('nav.wiki');
    this.modeConfigs.graph.title = i18n.t('nav.wiki');
    this.modeConfigs.settings.title = i18n.t('nav.settings');
    if (this.currentMode) this.setMode(this.currentMode);
  }

  _mount(mode) {
    return document.getElementById(`title-${mode}`);
  }

  setMode(mode, customTitle = null) {
    this.currentMode = mode;
    const config = this.modeConfigs[mode];
    const mount = this._mount(mode);
    if (!config || !mount) {
      this.titleEl = null;
      this.actionsEl = null;
      return;
    }

    mount.innerHTML = '<div class="view-title">' +
      '<h1 class="view-title-text"></h1>' +
      '<div class="view-title-actions"></div>' +
      '</div>';
    this.titleEl = mount.querySelector('.view-title-text');
    this.actionsEl = mount.querySelector('.view-title-actions');

    this.titleEl.textContent = customTitle || config.title;
    this.renderActions(config.actions);
  }

  /** Scrive il titolo della vista, ma solo se chi lo scrive è ancora il
   *  proprietario della modalità corrente.
   *
   *  `titleEl` viene ripuntato soltanto da `setMode`, che `switchMode` chiama
   *  *prima* di `deactivate`/`activate`: un caricamento lento della sezione che
   *  si sta lasciando riprendeva dopo il cambio e scriveva il proprio titolo nel
   *  mount della sezione di **destinazione**. Il difetto è intermittente — chat
   *  e onboarding non hanno mount, quindi lì `titleEl` è null e non si vede
   *  niente — e per questo era rimasto invisibile.
   *
   *  `ownerMode` è opzionale solo per non rompere chiamanti futuri distratti:
   *  chi scrive un titolo asincrono deve passarlo.
   */
  setTitle(title, ownerMode = null) {
    if (ownerMode && ownerMode !== this.currentMode) return;
    if (this.titleEl) this.titleEl.textContent = title;
  }

  /** Accende un'azione, ma solo se chi la accende possiede ancora la modalità
   *  corrente — la stessa guardia di :meth:`setTitle`, e per lo stesso motivo.
   *
   *  `actionsEl` punta al mount della modalità **a schermo**: un caricamento
   *  lento della sezione che si sta lasciando riprende dopo il cambio e cerca il
   *  proprio bottone nell'header di destinazione. Oggi non lo trova (i nomi
   *  delle azioni non si ripetono fra le viste) e la riga è un no-op silenzioso,
   *  che è il tipo di innocuo che smette di esserlo appena due viste chiamano
   *  un'azione allo stesso modo.
   *
   *  `ownerMode` è opzionale per i chiamanti sincroni, che non possono sbagliare
   *  vista: chi accende un'azione dopo un `await` lo passa.
   */
  showAction(actionName, ownerMode = null) {
    if (ownerMode && ownerMode !== this.currentMode) return;
    const btn = this.actionsEl?.querySelector(`[data-action="${actionName}"]`);
    if (btn) btn.style.display = '';
  }

  hideAction(actionName, ownerMode = null) {
    if (ownerMode && ownerMode !== this.currentMode) return;
    const btn = this.actionsEl?.querySelector(`[data-action="${actionName}"]`);
    if (btn) btn.style.display = 'none';
  }

  renderActions(actions) {
    if (!this.actionsEl) return;
    const html = actions.map(action => {
      if (action.type === 'sep') {
        return '<div class="sep"></div>';
      }
      if (action.drawer) {
        const hiddenStyle = action.hidden ? ' style="display:none"' : '';
        return `<button class="ibtn ibtn-drawer" data-drawer="${action.drawer}" title="${action.title}"${hiddenStyle}>
          <i class="ti ${action.icon}"></i>
        </button>`;
      }
      const dangerClass = action.danger ? ' ibtn-danger' : '';
      const hiddenStyle = action.hidden ? ' style="display:none"' : '';
      return `<button class="ibtn ibtn-action${dangerClass}" data-action="${action.action}" title="${action.title}"${hiddenStyle}>
        <i class="ti ${action.icon}"></i>
      </button>`;
    }).join('');

    this.actionsEl.innerHTML = html;
    this.wireActions();
  }

  wireActions() {
    if (!this.actionsEl) return;
    this.actionsEl.querySelectorAll('[data-drawer]').forEach(btn => {
      btn.addEventListener('click', () => {
        const drawerId = btn.dataset.drawer;
        window.mobileApp.drawer.toggle(drawerId);
        this.syncDrawerTabs();
      });
    });

    this.actionsEl.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.action;
        this.handleAction(action);
      });
    });
  }

  syncDrawerTabs() {
    if (!this.actionsEl) return;
    const activeDrawer = window.mobileApp.drawer.activeDrawer;
    this.actionsEl.querySelectorAll('[data-drawer]').forEach(btn => {
      const isActive = btn.dataset.drawer === activeDrawer;
      btn.classList.toggle('active-tab', isActive);
      const icon = btn.querySelector('i');
      if (icon && btn.dataset.drawer === 'files') {
        icon.classList.toggle('ti-folder', !isActive);
        icon.classList.toggle('ti-folder-open', isActive);
      }
    });
  }

  handleAction(action) {
    const app = window.mobileApp;
    if (action === 'graph') {
      if (app.currentMode === 'graph') {
        const wiki = app.controllers.graph?.currentWiki;
        if (wiki) {
          const lastPage = app.controllers.wiki?.lastWikiPage?.[wiki];
          app.switchMode('wiki', false);
          app.controllers.wiki.loadWikiPage(wiki, lastPage || 'index.md', true);
        } else {
          app.switchMode('wiki', false);
          app.controllers.wiki.loadHome(true);
        }
      } else {
        // Sorgente unica: la vista voluta si deposita e la carica
        // `GraphController.activate()`. Prima qui c'era
        // `switchMode('graph'); loadGraph(...)`, e activate() aveva già
        // caricato per conto suo — due fetch e due settleSimulation sincroni.
        const wiki = app.controllers.wiki?.currentWiki;
        app.requestGraph(wiki || null, true);
      }
      return;
    }
    if (action === 'open-pages') {
      // Apre la vista file/pagina dalla vista grafo (landing di default).
      const wiki = app.controllers.graph?.currentWiki;
      if (wiki && wiki !== '_home') {
        const lastPage = app.controllers.wiki?.lastWikiPage?.[wiki];
        app.switchMode('wiki', false);
        app.controllers.wiki.loadWikiPage(wiki, lastPage || 'index.md', true);
      } else {
        app.switchMode('wiki', false);
        app.controllers.wiki.loadHome(true);
      }
      return;
    }
    if (action === 'open-project-chat') {
      /* La wiki a schermo la sa la vista che è a schermo, e le due la tengono
         in un campo con lo stesso nome. Si legge da lì e non da `pinnedWiki`:
         dentro un progetto le due risposte coincidono, ma il tasto serve
         soprattutto **fuori**, dalla personale, dove il pin è null ed è
         l'unica strada per entrare.

         Il nome viene ricontrollato qui e non solo quando il tasto si accende:
         fra l'accensione e la pressione la vista può essere cambiata sotto
         (cambio progetto, link a un'altra wiki), e questo è l'ultimo punto
         prima di cambiare conversazione — che è il solo guasto irrecuperabile
         del disegno delle sessioni-progetto. */
      const wiki = app.currentMode === 'graph'
        ? app.controllers.graph?.currentWiki
        : app.controllers.wiki?.currentWiki;
      if (!wiki || !isOpenableProjectName(wiki)) return;
      /* Prima la vista, poi lo scope, e l'ordine conta. `select` pubblica
         l'aggancio, e le due viste si riagganciano solo se sono **quella a
         schermo**: farlo da dentro la sezione wiki significa un grafo
         ricaricato per essere buttato un istante dopo, o — peggio — la pagina
         che si stava leggendo sostituita dall'indice del progetto un attimo
         prima di lasciarla. Da 'chat' i due ascoltatori si limitano a segnarsi
         il cambio e ricalcolano al rientro. */
      app.switchMode('chat');
      // Il primo avvio dirotta ogni navigazione sull'onboarding: se non ci
      // siamo arrivati lo scope non si tocca. Stessa verifica di `openChat`.
      if (app.currentMode !== 'chat') return;
      /* E si aspetta che la chat sia pronta. `scopeChip.onSwitch` lo installa
         lei dopo `sessionManager.init()`, che è asincrono, e alla **prima**
         apertura della chat il controller nasce proprio in questo `switchMode`:
         un `select()` sincrono qui cambierebbe l'etichetta del chip e nient'
         altro — nessuno starebbe ascoltando — e il caricamento della
         conversazione personale, arrivando dopo, rimetterebbe il chip com'era
         con la sua `syncFromSession`. Cioè il tasto portava in chat, ma nella
         chat sbagliata. Dalla seconda volta in poi `ready` è già risolta e
         questo è un microtask.

         `select` fa tutto il seguito — chip, placeholder, aggancio delle viste
         e cambio di conversazione — e non fa niente se quello è già lo scope
         aperto: da dentro il progetto questo tasto è solo la via più corta
         verso la chat. Il thread non si legge due volte: `loadInitialHistory`
         chiude il proprio latch da sé, e la generazione di `switchTo` scavalca
         il caricamento della conversazione di prima. */
      Promise.resolve(app.controllers.chat?.ready).then(() => {
        scopeChip.select({ kind: 'project', name: wiki });
      });
      return;
    }
    const controller = app.controllers[app.currentMode];
    if (controller && controller.handleAction) {
      controller.handleAction(action);
    }
  }
}
