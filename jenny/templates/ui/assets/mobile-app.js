/** Mobile App — Entry point and orchestration. */

import { AppState } from './shared/state.js';
import { sessionManager } from './shared/session-manager.js';
import { scopeChip } from './shared/scope-chip.js';
import { writeSwitch } from './shared/write-switch.js';
import { showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { api } from './shared/api-client.js';
import { ViewTitleController } from './mobile-header.js';
import { DrawerManager } from './mobile-drawer.js';
import { LauncherController } from './mobile-launcher.js';
import { ChatController } from './mobile-chat.js';
import { WorkspaceController } from './mobile-workspace.js';
import { AppsSource } from './shared/apps-source.js';
import { AppsActions } from './shared/apps-actions.js';
import { SettingsController, VISTA_DI, elementoVista } from './mobile-settings.js';
import { OnboardingController } from './mobile-onboarding.js';
import { JennyCompanion } from './mobile-jenny.js';
import { UiQueryResponder } from './mobile-ui-query.js';
import { keyboard } from './shared/keyboard.js';
import { hasSelection, exposeSelectionState, forwardTapsThroughChrome } from './shared/selection.js';
import { watchHorizontalSwipe, elastic } from './shared/horizontal-swipe.js';
import './shared/theme.js';

/* ── Global Error Handling ── */
// Oltre al toast, l'errore viene inoltrato al log del gateway (/api/client-log):
// la console del WebView è visibile solo via adb, quindi senza inoltro un
// errore JS on-device è di fatto invisibile.
window.addEventListener('error', (e) => {
  console.error('Global error:', e.error);
  const detail = e.error && e.error.stack ? e.error.stack : `${e.message} @ ${e.filename}:${e.lineno}`;
  api.clientLog('error', 'window.onerror', detail);
  showToast(i18n.t('common.genericError'), 'error');
});

window.addEventListener('unhandledrejection', (e) => {
  console.error('Unhandled rejection:', e.reason);
  const detail = e.reason && e.reason.stack ? e.reason.stack : String(e.reason);
  api.clientLog('error', 'unhandledrejection', detail);
  showToast(i18n.t('common.networkError'), 'error');
});

/* ── Keyboard Helper ── */
function ensureVisible(el) {
  setTimeout(() => {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, 300);
}

class MobileApp {
  constructor() {
    this.header = new ViewTitleController();
    this.drawer = new DrawerManager();
    this.jenny = new JennyCompanion();
    this.uiQuery = new UiQueryResponder();
    // Il cassetto delle app non è una vista: niente `view-*`, niente entry di
    // history, quindi non sta fra i controller lazy. È un livello sopra la
    // vista corrente, e il suo markup è statico — si costruisce qui, con gli
    // altri pezzi permanenti del guscio.
    this.launcher = new LauncherController(this);

    // Lazy controller factories
    /* Cervello, Mani e Memoria sono **un controller solo**: sono tre cassetti
       della stessa schermata, e istanziarne tre vorrebbe dire tre
       `/api/settings` e tre copie dello stesso stato — con due che invecchiano
       mentre guardi la terza. La fabbrica li serve tutti dalla stessa
       istanza, creata alla prima apertura. */
    const settings = () => (this._settings ||= new SettingsController());
    this.controllerFactories = {
      chat:      () => new ChatController(),
      workspace: () => new WorkspaceController(),
      settings:  settings,
      cervello:  settings,
      mani:      settings,
      memoria:   settings,
      onboarding: () => new OnboardingController(),
    };
    this.controllers = {};

    this.currentMode = null;
    // Posizione nello stack di navigazione *nostro* (0 = radice). Ogni entry
    // spinta da pushNav se la porta dietro, così il back sa quando è arrivato
    // in fondo. `history.length` non risponde alla domanda: conta l'intera
    // sessione del WebView (iframe delle mini-app, reload) e non cala mai.
    this._navPos = 0;
    // Gate di readiness dello shell nativo: alcune animazioni d'ingresso (es. la
    // caduta della mini Jenny nell'onboarding) devono partire solo quando il
    // loading nativo è sparito e il WebView è visibile, altrimenti scorrono
    // dietro l'overlay e se ne vede solo la coda.
    this._shellReady = false;
    this._shellReadyCbs = [];
    window.mobileApp = this;
    this.init();
  }

  /* Invocato dal guscio Android (MainActivity.hideLoading) a fade completato.
     Fuori dallo shell nativo lo emula whenShellReady via requestAnimationFrame. */
  onNativeReady() {
    if (this._shellReady) return;
    this._shellReady = true;
    this._shellReadyCbs.splice(0).forEach((cb) => cb());
  }

  /* Esegue `cb` quando lo shell è pronto (loading nascosto). In un browser
     normale — senza il bridge JennyNative — parte al frame successivo. */
  whenShellReady(cb) {
    if (this._shellReady) return cb();
    this._shellReadyCbs.push(cb);
    if (!window.JennyNative) requestAnimationFrame(() => this.onNativeReady());
  }

  async init() {
    // Bootstrap auth token before anything else
    try {
      await api.bootstrap();
    } catch (err) {
      console.error('Bootstrap failed:', err);
    }

    // Load i18n and update sidebar
    i18n.load(i18n.locale).then(() => {
      this._applyStaticTranslations();
      this.header._refreshTitles();
      // Il chip dello scope scrive il proprio testo da JS, quindi
      // _applyStaticTranslations non lo raggiunge: senza questo resterebbe con
      // le chiavi grezze ("scope.personal").
      scopeChip.render();
      writeSwitch.render();
    });

    // Sidebar navigation
    document.querySelectorAll('.dock-item[data-mode]').forEach(item => {
      item.addEventListener('click', () => this.switchMode(item.dataset.mode));
    });

    /* Il cassetto delle app, dal composer.

       Qui c'era un ramo `data-opens === 'launcher'` sullo slot Apps del dock, e
       dal passo 0 era **codice morto**: quel dock non esiste più, e con lui se
       n'è andato l'unico gesto da un tocco per aprire il foglio — restava Mani
       → «Cassetto delle app», tre tocchi per la cosa che un launcher fa più di
       ogni altra. Il pulsante è tornato dove la mano ce l'ha già, accanto alla
       graffetta, e la casa ne ha uno identico. */
    document.getElementById('btn-launcher')
      ?.addEventListener('click', () => this.openLauncher());

    /* Qui c'erano due ascolti che ridipingevano le linguette dei pannelli
       nell'intestazione: le portava solo la wiki, uscita dall'officina il
       21/09/2026. I pannelli dei cassetti si aprono da una riga di riepilogo,
       non da un'azione dell'intestazione, quindi non c'e' piu' niente da
       sincronizzare. */

    // Browser back/forward
    window.addEventListener('popstate', (e) => {
      const state = e.state;
      // Entry non nostra (state null): la lasciava passare in silenzio, senza
      // aggiornare _navPos e senza cambiare niente a schermo — una pressione
      // persa, e da lì in poi _navPos sballato in permanenza. Si prosegue
      // invece all'indietro: la radice ha sempre uno stato (init la riscrive
      // con replaceNav), quindi il salto termina sempre su una entry nostra.
      if (!state) {
        if (this._navPos > 0) window.history.back();
        return;
      }
      // La entry ripristinata porta con sé la propria posizione nello stack:
      // è così che handleHardwareBack sa se sotto c'è ancora roba nostra.
      this._navPos = typeof state.pos === 'number' ? state.pos : 0;
      /* Le entry `wikiPage`/`wiki`/`graph` non si producono piu' dal
         21/09/2026 — wiki e grafo sono usciti dall'officina e vivono in casa —
         ma una entry vecchia puo' ancora stare nella history di un WebView che
         non e' stato chiuso: cade nel ramo `state.mode`, e `switchMode` di un
         modo che non esiste non fa niente invece di esplodere. */
      if (state.mode) {
        this.switchMode(state.mode, false);
      } else {
        return;
      }
      // Sync URL with restored state
      this.replaceNav(state);
    });

    // Persist mode changes
    AppState.on('currentMode', (mode) => {
      localStorage.setItem('mobile-last-mode', mode);
    });

    // Viewport height sync (Android keyboard fix)
    this.setupViewportHeight();

    // Keyboard scroll helpers
    this.setupKeyboardHelpers();

    // Horizontal swipe to navigate between dock tabs
    this.setupSwipeNav();
    /* Vale per tutta la pagina, non solo per la chat: il salto dell'ancora
       colpisce qualunque testo lungo, fogli compresi. */
    /* Finché c'è una selezione, composer, dock e mascotte escono dal hit-test:
       è la condizione perché il tocco di un manico non ributti l'estremo
       fermo sulla chrome (v. .agent/chat-selection-root-plan.md, pagina C).
       Il tap che così finirebbe sotto viene riconsegnato al bersaglio vero. */
    exposeSelectionState();
    forwardTapsThroughChrome(['.chat-bottom', '.dock']);

    // Determine initial mode
    const urlParams = new URLSearchParams(window.location.search);
    const urlMode = urlParams.get('mode');
    const savedMode = localStorage.getItem('mobile-last-mode');
    let initialMode = urlMode || savedMode || 'chat';
    /* `workspace` **e' un file aperto**, non una sezione: l'esploratore vive
       in Memoria, e questa vista senza il suo file e' una schermata bianca.
       Da `mobile-last-mode` arriva esattamente cosi' — chiudendo l'app con un
       file aperto — quindi si riparte da dove i file stanno. */
    if (initialMode === 'workspace') initialMode = 'memoria';

    // Radice dello stack, marcata *prima* dei due await qui sotto. I listener
    // del dock sono già registrati da un pezzo: un tap durante il boot impilava
    // la propria entry sopra una radice non ancora marcata, e la marcatura
    // tardiva la riscriveva con pos 0 riportando indietro la vista da sé — tap
    // annullato in silenzio, e sotto una entry che _navPos non contava più.
    this._navPos = 0;
    this.replaceNav(this._navStateFor(initialMode));

    // Check first-run: redirect to onboarding (always when first_run is true)
    let firstRunKnown = false;
    try {
      const settings = await api.getSettings();
      firstRunKnown = true;
      if (settings?.first_run) {
        initialMode = 'onboarding';
        localStorage.removeItem('onboarding-complete');
        this._setFirstRunLock(true);
      }
    } catch (err) {
      // "Non lo so" non è "onboarding già fatto". Se le impostazioni non si
      // leggono (gateway a metà avvio, token non ancora valido, rete), trattare
      // l'ignoto come configurazione completa cancellava il marcatore locale e
      // portava in chat una Jenny senza provider, senza più alcuna strada verso
      // il wizard. Qui si sospende il giudizio: nessuno stato viene riscritto,
      // La contabilità vera è `firstRunKnown`, che resta false: qui basta
      // lasciarne traccia nel log.
      //
      // Questo commento prometteva anche che «il wizard resta raggiungibile da
      // Impostazioni → Riesegui configurazione». **Non è più vero** dal
      // 20/09/2026: quel bottone è uscito con il giro degli aggiornamenti, per
      // una ragione misurata (`save_onboarding` sostituisce l'elenco dei
      // provider invece di aggiungere). Chi si ritrova senza provider ci rimedia
      // dalla casa, dove la marca ha il suo «Aggiungi».
      api.clientLog('warning', 'boot-first-run',
        `settings unavailable at boot: ${err?.message || err}`);
    }

    // After onboarding completed: force chat mode, clear stale state.
    // Solo se sappiamo davvero che il primo avvio è alle spalle: consumare il
    // marcatore su un "non lo so" lo perde per sempre.
    if (firstRunKnown && !this._firstRun && localStorage.getItem('onboarding-complete')) {
      localStorage.removeItem('onboarding-complete');
      localStorage.setItem('mobile-last-mode', 'chat');
      initialMode = 'chat';
    }

    // Initialize sessions and load module
    await this._initSessions();

    // Un tap sul dock durante i due await qui sopra ha già scelto la vista e
    // impilato la propria entry sopra la radice (marcata prima di partire):
    // quella pressione va onorata, non annullata. Il primo avvio è l'unica
    // eccezione — finché l'onboarding non è finito la navigazione è bloccata,
    // quindi la vista scelta dal tap non è una destinazione lecita.
    if (!this.currentMode || this._firstRun) {
      // La entry iniziale *è* già la vista iniziale: va riscritta, non
      // impilata. Prima si faceva replaceState + switchMode(push) e restavano
      // due entry identiche, così il primo Indietro veniva ingoiato da
      // switchMode (`mode === currentMode`) senza cambiare niente a schermo —
      // da lì la sensazione che il tasto "salti" una pagina. Si riscrive di
      // nuovo perché `initialMode` può essere cambiato durante gli await
      // (primo avvio → onboarding, onboarding appena concluso → chat).
      this._navPos = 0;
      this.replaceNav(this._navStateFor(initialMode));
      this.switchMode(initialMode, false);
    }

    // Register keyboard shortcuts
    this._initKeyboardShortcuts();

    console.log('Mobile app initialized');
  }

  _applyStaticTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
      el.textContent = i18n.t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = i18n.t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = i18n.t(el.dataset.i18nTitle);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(el => {
      el.setAttribute('aria-label', i18n.t(el.dataset.i18nAria));
    });
  }

  setupViewportHeight() {
    const root = document.documentElement;
    const setH = () => {
      if (!window.visualViewport) return;
      // Una pagina caricata a vista nascosta (il pannello browser del Mac)
      // misura 0: scriverlo azzererebbe il guscio, e il primo resize vero
      // arriva comunque.
      const h = window.visualViewport.height;
      if (!h) return;
      // Il CSS legge `--vv-height` (`.app` fuori dalla chat, `min-height` in
      // chat): la tastiera restringe il viewport e il guscio la segue.
      root.style.setProperty('--vv-height', h + 'px');
      // In chat lo scroller è il documento e la posizione di scroll è una
      // posizione di lettura: non si azzera per un resize (chi era in fondo
      // ci torna da sé, v. ChatController). Fuori dalla chat il guscio è
      // fisso e uno scroll residuo della pagina va rimesso a zero, come prima.
      if (!root.classList.contains('mode-chat')) window.scrollTo(0, 0);
    };
    window.visualViewport?.addEventListener('resize', setH);
    setH();
  }

  setupKeyboardHelpers() {
    // Ensure inputs stay visible when keyboard opens
    const modes = ['chat', 'workspace'];
    modes.forEach(mode => {
      const view = elementoVista(mode);
      if (!view) return;
      const input = view.querySelector('input, textarea');
      if (input) {
        input.addEventListener('focus', () => ensureVisible(input));
      }
    });
  }

  async _initSessions() {
    try {
      await sessionManager.init();
    } catch (err) {
      console.error('Failed to init sessions:', err);
    }
    // Il chip dello scope si accende comunque: senza sessione mostra la
    // personale, che e' lo stato giusto quando non c'e' niente da leggere.
    scopeChip.init();
    // Accanto al chip, e per la stessa ragione: senza sessione mostra lo stato
    // di default (scrive), che è quello giusto quando non c'è niente da leggere.
    writeSwitch.init();
  }

  _initKeyboardShortcuts() {
    // Cmd/Ctrl+,: open settings
    keyboard.register('mod+,', () => {
      this.switchMode('settings');
    });

    // Escape: stessa catena del tasto Indietro hardware. Sul Titan 2 la
    // tastiera fisica è sempre sotto le dita, quindi Esc *è* la scorciatoia
    // primaria: quando copriva solo drawer e dialog sembrava funzionare ed era
    // inerte su mini-app, minichat, lightbox, sotto-stato di sezione e history.
    keyboard.register('escape', () => {
      this.handleHardwareBack();
    });
  }

  /* ── Navigazione: stack unico ──────────────────────────────────────────
     Unico punto di scrittura della history. L'URL è *derivato* dallo stato,
     mai il contrario, così una entry ripristinata da popstate riproduce
     esattamente la schermata che l'aveva spinta. */

  _navUrl(state) {
    const url = new URL(window.location);
    url.searchParams.set('mode', state.mode);
    /* `wiki` e `page` erano i due parametri della sezione wiki, uscita
       dall'officina il 21/09/2026. Si cancellano e non si scrivono piu': un
       WebView riaperto su un vecchio indirizzo non deve portarseli dietro in
       ogni entry successiva. */
    url.searchParams.delete('wiki');
    url.searchParams.delete('page');
    return url;
  }

  /** Stato di navigazione per una vista (usato al boot e dai controller). */
  _navStateFor(mode) {
    return { mode };
  }

  /** Impila una nuova schermata. Impilare due volte la stessa (ritap sullo
      stesso link, doppio tap sulla stessa voce) è la ricetta per una pressione
      di Indietro che non cambia niente: in quel caso si riscrive e basta. */
  pushNav(state) {
    const cur = history.state;
    if (cur && cur.mode === state.mode) {
      this.replaceNav(state);
      return;
    }
    this._navPos += 1;
    history.pushState({ ...state, pos: this._navPos }, '', this._navUrl(state));
  }

  /** Riscrive la schermata corrente senza impilarne una nuova. */
  replaceNav(state) {
    history.replaceState({ ...state, pos: this._navPos }, '', this._navUrl(state));
  }

  /* ── I livelli sopra la vista: una definizione sola ─────────────────────
     "Cosa sta sopra" era scritto in quattro posti che divergevano — il back
     hardware, goHome, la shortcut Escape e la guardia del type-ahead della
     chat — e ogni divergenza era un difetto: Home lasciava aperta la lightbox
     e scavalcava i dialog non annullabili, Esc copriva due livelli su cinque,
     la chat rimetteva il fuoco su un composer coperto. Da qui in poi l'elenco
     è uno, e i quattro consumatori lo percorrono.

     L'ordine è quello di sovrapposizione reale (z-index in mobile-style.css:
     .app-frame-overlay 110 · .image-lightbox 115 · .jenny-scrim 119 ·
     .jenny-duo 120 · .jenny-mc 121; `.app` non crea stacking context). La
     minichat copre la mini-app, quindi va consumata *prima*: l'ordine opposto
     chiudeva l'app che stava sotto lasciando a schermo la minichat, cioè una
     pressione senza alcun cambiamento visibile. Lightbox e minichat non stanno
     mai aperte insieme (D3: Jenny sta sopra la lightbox ma non prende tocchi,
     e lo scrim della minichat copre le immagini), quindi fra loro due l'ordine
     non conta.

     Ogni livello espone:
       present()  test di presenza, senza effetti collaterali;
       dismiss()  chiusura di un passo (semantica del tasto Indietro); ritorna
                  false se non ha consumato niente, e allora la catena prosegue
                  invece di ingoiare la pressione;
       close()    smontaggio completo (semantica di Home), default = dismiss. */
  _overlayLayers() {
    return [
      {
        // <dialog> modali e sheet: con showModal() vivono nel top layer, sopra
        // qualunque z-index.
        name: 'dialog',
        present: () => !!document.querySelector('dialog[open]'),
        dismiss: () => this._dismissTopDialog(),
      },
      {
        // Lightbox immagini: overlay normale, ma si chiude solo col proprio
        // handler — remove() salterebbe il cleanup (onClose, che revoca gli
        // object URL del workspace).
        name: 'lightbox',
        present: () => !!document.querySelector('.image-lightbox'),
        dismiss: () => {
          const lightbox = document.querySelector('.image-lightbox');
          if (!lightbox) return;
          if (typeof lightbox.__jennyClose === 'function') lightbox.__jennyClose();
          else lightbox.remove();
        },
      },
      {
        // Minichat della mascotte.
        name: 'minichat',
        present: () => !!document.querySelector('.jenny-mc.open'),
        dismiss: () => this.jenny?.handleBack() ?? false,
      },
      {
        // Mini-app aperta: gestisce la propria navigazione interna e, all'ultimo
        // livello, si chiude. Home invece la smonta e basta. L'overlay resta nel
        // DOM per i 200 ms della dissolvenza di chiusura: lì handleBack ritorna
        // false e la catena prosegue, che è meglio di una pressione ingoiata.
        name: 'miniapp',
        present: () => !!document.querySelector('.app-frame-overlay'),
        /* Le azioni delle app, non un controller: la scheda «App» non esiste
           piu' e `this.controllers.apps` era sempre undefined, quindi
           Indietro scavalcava la mini-app aperta. Senza `_appsActions` non si
           e' mai aperta un'app, e il livello non puo' essere presente. */
        dismiss: () => this._appsActions?.handleBack() ?? false,
        close: () => { this._appsActions?.closeApp(); },
      },
      {
        // Il cassetto delle app. Sta *sotto* la mini-app — un'app aperta dal
        // foglio lo copre, e Indietro deve chiudere prima l'app — e *sopra* il
        // drawer, che è laterale e non copre il lanciatore. `present()` legge
        // un flag, non il DOM: il foglio resta nel DOM per i 320 ms della
        // discesa, e su una lettura dal DOM il ciclo di _dismissAllOverlays lo
        // richiamerebbe otto volte a vuoto.
        name: 'launcher',
        present: () => this.launcher.isOpen(),
        dismiss: () => { this.launcher.dismiss(); },
        /* Dal passo 4 `dismiss` e `close` **non** coincidono più, e il default
           `layer.close || layer.dismiss` non basta: Indietro (ed Esc, stessa
           catena) svuota prima la ricerca e chiude solo a campo vuoto — due
           passi, che sono giusti per una pressione dell'utente e sbagliati per
           Home, che smonta e basta. Senza questa riga il ciclo di
           `_dismissAllOverlays` arriverebbe comunque in fondo, ma in due giri
           invece di uno: il conto di 1.8 lo direbbe. */
        close: () => { this.launcher.close(); },
      },
      {
        name: 'drawer',
        present: () => !!this.drawer.activeDrawer,
        dismiss: () => { this.drawer.closeAll(); },
      },
    ];
  }

  /** True se sopra la vista corrente c'è un overlay.
   *
   *  Con `belowLayer` la domanda diventa "c'è un overlay sopra *questo*
   *  livello?", e si guardano solo i livelli che lo precedono nella catena.
   *  Serve a chi un livello ce l'ha: il cassetto ascolta i tasti a foglio
   *  aperto, e senza il parametro si escluderebbe da solo — `present()` del
   *  proprio livello è vero per definizione mentre è a schermo. Un nome
   *  sconosciuto degrada sulla domanda originale, che è la risposta prudente:
   *  meglio cedere i tasti che rubarli.
   *
   *  @param {string|null} belowLayer nome del livello di chi chiede.
   */
  hasOverlayAbove(belowLayer = null) {
    const layers = this._overlayLayers();
    const stop = belowLayer ? layers.findIndex((layer) => layer.name === belowLayer) : -1;
    const above = stop >= 0 ? layers.slice(0, stop) : layers;
    return above.some((layer) => layer.present());
  }

  /* Congeda il <dialog> più in alto con la semantica di Esc: evento `cancel`
     annullabile, e close() solo se nessuno l'ha rifiutato. C'è chi lo rifiuta
     apposta — il dialog di riavvio dopo un restore non deve essere chiudibile —
     e chiamare close() diretto, come facevano goHome e la shortcut Escape,
     scavalca quel rifiuto. */
  _dismissTopDialog() {
    const dialogs = document.querySelectorAll('dialog[open]');
    if (!dialogs.length) return;
    const top = dialogs[dialogs.length - 1];
    if (top.dispatchEvent(new Event('cancel', { cancelable: true }))) top.close();
  }

  /* Tasto Indietro hardware (MainActivity lo inoltra sempre qui: il guscio
     nativo non ne gestisce nessun caso da solo).

     Catena di consumatori, dal livello più in alto verso il basso: il primo che
     consuma si ferma. L'invariante è che *una pressione = un cambiamento
     visibile*, altrimenti il tasto sembra saltare le schermate. */
  handleHardwareBack() {
    // 1..5 — overlay sopra la vista. Il primo presente consuma la pressione:
    // sotto un overlay non si naviga, altrimenti il cambiamento resta nascosto.
    // Un livello che si dichiara presente ma non consuma (ritorna false) lascia
    // proseguire la catena: meglio del livello successivo saltato in silenzio.
    for (const layer of this._overlayLayers()) {
      if (layer.present() && layer.dismiss() !== false) return;
    }

    // 6. Sotto-stato della sezione corrente (cartella del workspace, editor,
    //    step dell'onboarding, popover Info sessione, focus sul grafo, catalogo
    //    modelli): risalire di un livello dentro la sezione viene prima di
    //    uscirne.
    if (this.controllers[this.currentMode]?.handleBack?.()) return;

    // 7. Schermata precedente.
    if (this._navPos > 0) {
      window.history.back();
    }
    // Alla radice non c'è niente sopra di noi: non si fa niente. Questa app è
    // il launcher, quindi "indietro" non deve mai chiudere il task.
  }

  // Android Home button / home gesture. This app is the device launcher, so
  // Home means "collapse to the home screen": dismiss every overlay layer,
  // collapse each section's sub-state, and go to the chat — collapsing to the
  // root of the navigation stack rather than pushing onto it. Not a no-op when
  // already home: the sub-state still has to come down. Called from
  // MainActivity.onNewIntent.
  //
  // La schermata iniziale e' **sempre** la chat. Era una preferenza con
  // quattro voci (`shared/home-view.js`, con un 'last' che voleva dire "non
  // muoverti"): tolta il 21/09/2026 insieme al gruppo che la conteneva, perche'
  // una casa che a ogni Home puo' aprirsi su una schermata diversa non e' una
  // casa. Chi vuole i file o le app li raggiunge da dentro, in un tocco.
  /* Smonta *tutti* i livelli, non un sottoinsieme scritto a mano: è così che
     si perdevano lightbox e minichat, e i dialog venivano chiusi con close()
     diretto. Il ciclo interno serve ai livelli impilabili (più <dialog>); il
     tetto evita che un livello che rifiuta di chiudersi — il dialog di riavvio
     dopo un restore fa preventDefault apposta — mandi il chiamante in loop. */
  _dismissAllOverlays() {
    for (const layer of this._overlayLayers()) {
      const dismiss = layer.close || layer.dismiss;
      for (let i = 0; i < 8 && layer.present(); i++) dismiss();
    }
  }

  goHome() {
    this._dismissAllOverlays();
    // Gli overlay non sono tutto: le sezioni hanno un sotto-stato che
    // sopravvive al cambio vista (l'editor del workspace resta montato e
    // `activate()` lo ripropone al rientro, la griglia riapre l'ultima
    // sottocartella). Home collassa anche quello — su *tutti* i controller già
    // istanziati, perché la sezione stantia può essere sia quella che si lascia
    // sia quella di destinazione.
    Object.values(this.controllers).forEach((c) => c.collapseToRoot?.());
    // Home *collassa* alla radice, non ci impila sopra un'altra schermata.
    // Con lo switchMode di prima (push di default) la schermata iniziale
    // diventava annullabile con Indietro — nessun launcher si comporta così —
    // e lo stack non calava mai: dieci Home = dieci entry, tutte da smaltire
    // una pressione alla volta prima di arrivare al fondo.
    this.switchMode('chat', false);
    // Il blocco del primo avvio può aver dirottato lo switch sull'onboarding:
    // in quel caso la radice non descrive la schermata iniziale, e marcarla
    // comunque scriverebbe nella entry corrente una vista che non è a schermo.
    if (this.currentMode !== 'chat') return;
    this._navPos = 0;
    this.replaceNav(this._navStateFor('chat'));
  }

  /* La chat è la vista a schermo? Lo chiede il guscio nativo al rientro in
     primo piano (`CHAT_ON_SCREEN_JS`) per cancellare gli avvisi già letti. È
     un metodo e non il campo `currentMode` letto da fuori perché la casa, che
     `currentMode` non ce l'ha, risponde alla stessa domanda a modo suo. */
  isChatOnScreen() {
    return this.currentMode === 'chat';
  }

  /* Tap sulla notifica di un messaggio proattivo (MainActivity). Non è "vai a
     casa e poi in chat": quella composizione lasciava la entry di radice a
     descrivere la *vista home* mentre a schermo c'era la chat, e con una vista
     home diversa da chat il primo Indietro atterrava dove l'utente non era mai
     stato — più un activate/deactivate di troppo sul controller di mezzo.
     Qui la chat *diventa* la radice. Ritorna false se non ci si è arrivati (il
     blocco del primo avvio dirotta sull'onboarding), così il guscio nativo sa
     che non deve ancora cancellare la notifica. */
  openChat() {
    this._dismissAllOverlays();
    Object.values(this.controllers).forEach((c) => c.collapseToRoot?.());
    this.switchMode('chat', false);
    if (this.currentMode !== 'chat') return false;
    this._navPos = 0;
    this.replaceNav(this._navStateFor('chat'));
    return true;
  }

  /** Torna alla schermata precedente se ce n'è una nostra sotto, altrimenti
      atterra su `fallbackMode` riscrivendo la entry corrente. Serve a chi deve
      *tornare* dove si trovava (la freccia ← dell'header del workspace, quando
      l'editor è stato aperto da un'altra sezione): impilare una entry in avanti
      mentre si va indietro è l'opposto di ciò che fa il tasto Indietro con lo
      stesso stato, e lascia dietro una pressione che non cambia niente. */
  navigateBack(fallbackMode) {
    if (this._navPos > 0) {
      window.history.back();
      return;
    }
    if (!fallbackMode || fallbackMode === this.currentMode) return;
    this.switchMode(fallbackMode, false);
    this.replaceNav(this._navStateFor(fallbackMode));
  }

  // Un'app di sistema è stata installata o disinstallata (kind: 'added' |
  // 'removed'). Chiamato da MainActivity, che ascolta i broadcast del
  // PackageManager. Se il cassetto non e' mai stato aperto la sorgente non
  // esiste e non c'e' niente da aggiornare: la lista verra' caricata fresca
  // alla prima volta.
  onPackageChanged(kind, packageName) {
    this._appsSource?.onPackageChanged(kind, packageName);
  }

  /** La sorgente dei dati del cassetto. Pigra: sono due fetch, e quella delle
   *  app Android ricodifica ogni icona in base64 — farle al boot per un
   *  cassetto che potrebbe non aprirsi mai e' un costo che si paga sempre e
   *  serve a volte.
   *
   *  Qui c'era `appsController()`, che costruiva la scheda «App» anche quando
   *  non era a schermo, proprio perche' il cassetto ci leggeva dentro. Quella
   *  scheda non esiste piu' (21/09/2026): restano i dati, e stanno per conto
   *  loro.
   */
  appsSource() {
    return (this._appsSource ||= new AppsSource());
  }

  /** Le azioni sulle voci, col modo dell'officina di mandare una richiesta in
   *  chat: cambia vista e scrive nel composer. */
  appsActions() {
    return (this._appsActions ||= new AppsActions(this.appsSource(), {
      sendChatPrompt: (testo) => this.mandaInChat(testo),
    }));
  }

  /** Scrive una richiesta nel composer della Console, **senza mandarla**.
   *  Pubblico perche' lo usa anche Mani («Chiedi a Jenny» fra le skill): un
   *  secondo modo di scrivere in chat divergerebbe da questo alla prima
   *  correzione. */
  mandaInChat(testo) {
    this.switchMode('chat');
    const chat = this.controllers?.chat;
    if (!chat?.input) return;
    chat.input.value = testo;
    chat._autoResize?.();
    chat._updateSendState?.();
    chat.input.focus();
  }

  /** Blocco del dock durante il primo avvio, in un interruttore solo.
   *
   *  Prima esisteva solo il ramo che *accende*: `grep -rn nav-disabled` dava
   *  due righe, una che aggiungeva la classe e una che la leggeva, e nessuna
   *  che la togliesse — né toglieva `pointer-events: none`, l'opacità 0.4 o la
   *  voce onboarding dal dock. Finito il wizard, l'unica cosa che sbloccava
   *  l'interfaccia era il reload che segue: qualunque percorso che completasse
   *  l'onboarding senza ricaricare (il ripristino da backup) lasciava il dock
   *  spento a tempo indeterminato. Un blocco a senso unico è un blocco che non
   *  si sa togliere: qui accensione e spegnimento sono lo stesso codice. */
  _setFirstRunLock(on) {
    this._firstRun = !!on;
    const navOnb = document.getElementById('nav-onboarding');
    if (navOnb) navOnb.style.display = on ? '' : 'none';
    document.querySelectorAll('.dock-item[data-mode]').forEach((item) => {
      if (item.dataset.mode === 'onboarding') return;
      item.classList.toggle('nav-disabled', !!on);
      item.style.pointerEvents = on ? 'none' : '';
      item.style.opacity = on ? '0.4' : '';
    });
  }


  /** Apre il cassetto delle app (pulsante nella riga del composer, D1).
   *
   *  Il blocco del primo avvio vale anche qui, con la stessa guardia di
   *  `switchMode`: finché l'onboarding non è finito non si va da nessuna
   *  parte, e un foglio che si apre sopra il wizard è una strada per uscirne
   *  senza averlo finito. Si dirotta invece di ignorare, esattamente come fa
   *  `switchMode`: durante il primo avvio la vista è già `onboarding`, quindi
   *  a schermo non cambia niente. */
  openLauncher() {
    if (!localStorage.getItem('onboarding-complete') && this._firstRun) {
      console.warn('Onboarding not complete - redirecting to onboarding');
      this.switchMode('onboarding');
      return;
    }
    this.launcher.open();
  }

  /** Il controller di `mode`, costruendolo se e' la prima volta.
   *
   *  Nasceva dentro `switchMode`, cioe' solo andando nella sua vista. Non
   *  basta piu': il gestore file si monta dentro una scheda di Memoria, quindi
   *  serve **prima** che qualcuno vada nella sua vista — che oggi e' il file
   *  aperto, e arriva dopo. Ritorna null se la costruzione fallisce, che e' la
   *  ragione per cui `switchMode` non prosegue. */
  ensureController(mode) {
    if (this.controllers[mode]) return this.controllers[mode];
    if (!this.controllerFactories[mode]) return null;
    try {
      this.controllers[mode] = this.controllerFactories[mode]();
    } catch (err) {
      console.error(`Failed to init ${mode} controller:`, err);
      showToast(i18n.t('common.failedToLoadMode', { mode }), 'error');
      return null;
    }
    return this.controllers[mode];
  }

  switchMode(mode, pushState = true) {
    // Block ANY navigation if onboarding is not complete
    if (mode !== 'onboarding' && !localStorage.getItem('onboarding-complete') && this._firstRun) {
      console.warn('Onboarding not complete - redirecting to onboarding');
      this.switchMode('onboarding', pushState);
      return;
    }

    if (mode === this.currentMode) return;
    if (!this.controllerFactories[mode]) {
      console.warn(`Unknown mode: ${mode}`);
      return;
    }

    if (!this.ensureController(mode)) return;

    // Hide all views
    document.querySelectorAll('.view').forEach(v => {
      v.style.display = 'none';
    });

    // Show target view
    const view = elementoVista(mode);
    if (view) {
      view.style.display = 'flex';
    }

    /* La modalità corrente anche su <html>: serve al CSS, che altrimenti non
       ha modo di sapere quale vista è a schermo (le viste si mostrano con un
       `display` inline, non con una classe che risalga). Gancio generale, non
       un caso speciale: la prima cosa che ne ha bisogno è la mascotte, v.
       `:root.mode-chat .jenny-duo` in mobile-style.css.

       **Va scritta qui, accanto al `display`, e non in fondo al metodo.** Sta
       piu' in basso fino al 22/09/2026, cioe' *dopo* `activate()`, e per la
       chat quella distanza e' un difetto: il suo scroller **e' il documento**,
       e il documento scorre solo sotto `:root.mode-chat { overflow-y: auto }`
       (mobile-style.css). Entrando in chat, `activate()` misurava e
       correggeva lo scroll di una pagina che in quell'istante non era ancora
       scorrevole; poi la classe arrivava e la posizione era quella sbagliata.
       Si vedeva a intermittenza, e in due modi che sembravano scollegati: la
       chat che risaliva in mezzo alla cronologia invece di stare in fondo, e
       il dock — `position: sticky; bottom: 0`, che si incolla solo se il
       documento scorre — che sfarfallava. La classe e' *cosa c'e' a schermo*,
       come il `display`: le due righe stanno insieme. */
    document.documentElement.classList.forEach((c) => {
      if (c.startsWith('mode-')) document.documentElement.classList.remove(c);
    });
    document.documentElement.classList.add(`mode-${mode}`);

    // Update sidebar active state.
    document.querySelectorAll('.dock-item').forEach(item => {
      item.classList.toggle('active', item.dataset.mode === mode);
    });

    // Update header
    this.header.setMode(mode);

    // Notify controllers
    // `?.`: non ogni sezione ha qualcosa da smontare uscendo. Il gestore file
    // aveva un `deactivate` che azzerava la sezione d'origine dell'editor, e se
    // n'e' andato con lei — un metodo vuoto tenuto in vita da una chiamata
    // obbligatoria e' una risposta finta a «questa sezione ha pulizie?».
    this.controllers[this.currentMode]?.deactivate?.();
    this.currentMode = mode;
    const next = this.controllers[mode];
    /* Quale cassetto, per i tre modi che condividono la schermata. Va detto
       **prima** di `activate()`: quello ricarica e ridisegna, e saperlo dopo
       vorrebbe dire un frame col cassetto di prima. */
    next.setCassetto?.(VISTA_DI[mode] === 'settings' ? mode : null);
    if (next.ready) {
      next.ready.then(() => next.activate());
    } else {
      next.activate();
    }

    // Close any open drawer
    this.drawer.closeAll();
    // …e il cassetto delle app, per la stessa ragione: è ancorato alla vista
    // che si sta lasciando, e restare aperto sopra quella nuova sarebbe un
    // overlay orfano che nessuno ha chiesto.
    this.launcher.close();

    // Update state and URL
    AppState.set('currentMode', mode);
    if (pushState) this.pushNav({ mode });
  }

  // Ordered list of navigable modes, derived from the dock DOM order.
  // Skips hidden (onboarding when not first-run) and disabled items.
  _visibleModes() {
    return Array.from(document.querySelectorAll('.dock-item[data-mode]'))
      .filter(el => el.style.display !== 'none' && !el.classList.contains('nav-disabled'))
      .map(el => el.dataset.mode);
  }

  // Global horizontal swipe on the content area to move between dock tabs.
  // The current view follows the finger (damped) as an affordance; on release
  // past a threshold it commits with a crisp slide-in of the target view,
  // otherwise it springs back. See plan: "carosello a step".
  /* Il carosello dell'officina: di lato si cambia linguetta.
   *
   *  Il *riconoscimento* del gesto — quando e' orizzontale, quando appartiene a
   *  uno scorrevole sotto il dito, quando e' abbastanza — sta in
   *  `shared/horizontal-swipe.js`, perche' la casa fa lo stesso gesto per
   *  cambiare pagina. Qui resta la **risposta**, che invece e' solo di qui: si
   *  trascina la vista corrente con una sbirciata smorzata e un velo grigio, e
   *  la vicina non viene mai disegnata. */
  setupSwipeNav() {
    const main = document.querySelector('.main');
    if (!main) return;

    // Velo grigio sopra la vista che sbircia (v. .swipe-scrim nel CSS).
    const scrim = document.createElement('div');
    scrim.className = 'swipe-scrim';
    scrim.setAttribute('aria-hidden', 'true');
    main.appendChild(scrim);
    const setScrim = (opacity, animate) => {
      scrim.style.transition = animate ? 'opacity .22s ease' : 'none';
      scrim.style.opacity = String(opacity);
    };

    const PEEK = 0.13;       // sbirciata verso una vicina (frazione di larghezza)
    const EDGE_PEEK = 0.05;  // sbirciata quando di la' non c'e' niente

    let view = null;         // la vista corrente, quella che segue il dito
    let neighbors = null;    // { prev, next } come nomi di modo

    const clearView = (el) => {
      if (!el) return;
      el.style.transition = '';
      el.style.willChange = '';
      el.style.transform = '';
      el.style.filter = '';
    };

    watchHorizontalSwipe(main, {
      canStart: () => {
        view = null; neighbors = null;
        // Guardia: durante il primo avvio la navigazione e' bloccata.
        if (this._firstRun && !localStorage.getItem('onboarding-complete')) return false;
        // Guardia: un cassetto aperto possiede il proprio gesto (verticale).
        if (this.drawer.activeDrawer) return false;
        // Guardia: c'e' del testo selezionato. Trascinare per aggiustare i
        // manici della selezione non deve far scivolare la vista sotto le dita.
        if (hasSelection()) return false;

        view = elementoVista(this.currentMode);
        if (!view) return false;

        const modes = this._visibleModes();
        const idx = modes.indexOf(this.currentMode);
        if (idx === -1) return false; // onboarding non e' nel dock — niente carosello

        /* Il giro si chiude: da Memoria a destra si torna in Console, e da
           Console a sinistra si va in Memoria. Con quattro voci in fila i due
           capi erano l'unico posto in cui il gesto non faceva niente — e «di
           lato si cambia linguetta» e' una regola che non regge se su due
           linguette su quattro vale solo in un verso.
           Sotto le due voci non c'e' nessun giro da fare: prev e next restano
           nulli, e la sbirciata di fine corsa (`EDGE_PEEK`) resta per quel
           caso. */
        neighbors = modes.length > 1
          ? {
            prev: modes[(idx - 1 + modes.length) % modes.length],
            next: modes[(idx + 1) % modes.length],
          }
          : { prev: null, next: null };
        return true;
      },

      onHorizontal: () => {
        view.style.transition = 'none';
        view.style.willChange = 'transform';
      },

      onDrag: (dx, w) => {
        const goingPrev = dx > 0;
        const hasNeighbor = goingPrev ? neighbors.prev : neighbors.next;
        const max = w * (hasNeighbor ? PEEK : EDGE_PEEK);
        const tx = elastic(dx, max);
        // Quanto si e' vicini all'asintoto guida il grigio.
        const progress = hasNeighbor && max ? Math.min(1, Math.abs(tx) / max) : 0;
        view.style.transform = `translateX(${tx.toFixed(2)}px)`;
        setScrim(progress, false);
      },

      onEnd: ({ direction, confirm }) => {
        const el = view;
        const goingPrev = direction === 'prev';
        const target = goingPrev ? neighbors.prev : neighbors.next;
        view = null; neighbors = null;

        if (confirm && target) {
          setScrim(0, false);         // la vista nuova non deve ereditare il velo
          clearView(el);              // la vecchia sta per essere nascosta da switchMode
          this.switchMode(target);
          this._animateSlideIn(elementoVista(target), goingPrev);
        } else {
          // Torna a riposo (offset e grigio insieme).
          setScrim(0, true);
          el.style.transition = 'transform .22s cubic-bezier(.22,.61,.36,1)';
          el.style.transform = 'translateX(0)';
          const onEnd = () => { clearView(el); el.removeEventListener('transitionend', onEnd); };
          el.addEventListener('transitionend', onEnd);
        }
      },

      onCancel: () => {
        setScrim(0, false);
        clearView(view);
        view = null; neighbors = null;
      },
    });
  }

  // Slide the freshly-shown view in from the swipe direction.
  // goingPrev → came from the left; otherwise from the right.
  _animateSlideIn(view, goingPrev) {
    if (!view) return;
    const from = goingPrev ? '-100%' : '100%';
    /* L'ancoraggio automatico dello scroll, spento per la durata della scivolata.
     *
     *  In chat lo scroller **e' il documento** (v. `_scroller` in
     *  mobile-chat.js). Chromium, per conto suo, tiene ferma la lettura quando
     *  qualcosa sopra cambia: sceglie un nodo d'ancora e corregge `scrollTop`.
     *  Far entrare una vista alta quanto la pagina con un `translateX` gli
     *  sembra esattamente quel caso — e la correzione **combatte** il «vai in
     *  fondo» che la chat ha appena chiesto.
     *
     *  Misurato fuori dall'app il 22/09/2026, su una pagina di 4000px: si va in
     *  fondo (`scrollTop` 3115), parte la stessa animazione, e a fine corsa lo
     *  scroll e' 2175 — **940px piu' su**. Con `overflow-anchor: none`: zero.
     *  E' il difetto segnalato come «la chat torna in alto», e la sua
     *  intermittenza e' quella della scelta del nodo d'ancora.
     *
     *  Si spegne **solo durante l'animazione** e non per sempre: fuori di qui
     *  l'ancoraggio e' utile — e' quel che tiene il segno quando la cronologia
     *  cresce sopra la riga che si sta leggendo. */
    const radice = document.documentElement;
    const ancoraPrima = radice.style.overflowAnchor;
    radice.style.overflowAnchor = 'none';

    view.style.filter = '';
    view.style.transition = 'none';
    view.style.willChange = 'transform';
    view.style.transform = `translateX(${from})`;
    void view.offsetWidth; // force reflow so the start transform sticks
    view.style.transition = 'transform .2s cubic-bezier(.22,.61,.36,1)';
    view.style.transform = 'translateX(0)';
    let fatto = false;
    const onEnd = () => {
      if (fatto) return;
      fatto = true;
      view.style.transition = '';
      view.style.willChange = '';
      view.style.transform = '';
      radice.style.overflowAnchor = ancoraPrima;
      view.removeEventListener('transitionend', onEnd);
    };
    view.addEventListener('transitionend', onEnd);
    /* Rete di sicurezza: `transitionend` non arriva se la vista viene nascosta
       a meta' corsa (un secondo gesto, il tasto Indietro). Senza, l'ancoraggio
       resterebbe spento per il resto della sessione. */
    setTimeout(onEnd, 400);
  }
}

// Initialize when DOM ready
document.addEventListener('DOMContentLoaded', () => {
  new MobileApp();
});
