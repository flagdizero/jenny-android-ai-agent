/** Quel che si **fa** con una voce del cassetto: aprirla, e la sua scheda.
 *
 *  Stava dentro `AppsController`, la scheda «App» dell'officina, cancellata il
 *  21/09/2026. I dati sono usciti di li' per primi (`shared/apps-source.js`);
 *  queste sono le azioni, ed erano l'altra meta' che valeva la pena salvare —
 *  il resto era la griglia, che il cassetto rimpiazza.
 *
 *  **Il codice e' quello di prima, spostato e non riscritto.** Le uniche
 *  differenze sono dichiarate e sono tre: i dati si chiedono alla sorgente
 *  invece di tenerli qui; non c'e' piu' nessuna schermata da ridisegnare, e
 *  chi guarda lo scopre dall'avviso della sorgente; e le due cose che sono
 *  state tolte per intero — le **skill**, che nel cassetto non erano mai
 *  entrate perche' non si lanciano, e **«nascondi»**, che se n'e' andato con
 *  la schermata che lo ospitava.
 *
 *  `shell` e' l'unico appiglio al guscio, e ne serve uno solo: mandare un
 *  messaggio in chat. «Modifica una Jenny App» non apre nessun editor — scrive
 *  una richiesta a Jenny — e la chat e' l'unica cosa che i due gusci fanno in
 *  due modi diversi.
 */

import { api } from './api-client.js';
import { escapeHtml, showToast } from './utils.js';
import { confirmDialog } from './dialog.js';
import { i18n } from './i18n.js';
import { currentTheme, themeTokens } from './theme.js';

/** L'`<iframe>` di una Jenny App, senza decidere dove va a finire.
 *
 *  Estratta da `openApp` il 22/09/2026: la casa monta la stessa cornice
 *  **dentro una pagina** invece che in un velo a tutto schermo (tavola
 *  `PaginaApp`), e le due non devono divergere — l'indirizzo porta il token e
 *  i colori del tema, e sbagliarne uno vuol dire un'app che si apre bianca o
 *  che non parla col gateway.
 *
 *  `sandbox="allow-scripts"` e basta: origine opaca apposta, cosi' l'app non
 *  raggiunge il DOM ne' il `localStorage` della SPA. Il tema poi arriva a caldo
 *  via `jenny:theme` (v. sopra), quindi cambiare tema **non** obbliga a
 *  ricostruirla.
 *
 *  **Il token e' quello dell'app, non il segreto del gateway.** Fino a Sett
 *  2026 qui viaggiava `api.getSecret()`, che apre ogni route `/api/` e la
 *  WebSocket: il sandbox teneva l'app fuori dal DOM della SPA, non fuori dal
 *  gateway. `token` e' `await api.appToken(slug)`, che il gateway accetta solo
 *  sui file e sulle azioni di quell'app (`jenny/apps/token.py`).
 *
 *  Chi chiama deve gia' averlo: qui e' passato e non atteso, perche' un
 *  `await` in mezzo alla costruzione di un nodo e' il modo in cui una cornice
 *  finisce attaccata a una pagina che non c'e' piu'.
 *
 *  `overlay` dice all'app che sta nel velo a tutto schermo e non in una
 *  pagina della casa: li' nessuno ascolta lo scorrimento laterale, e il kit
 *  non deve prenderselo (con `exclusive` l'app perderebbe il dito a ogni
 *  gesto di lato, per niente). Il default e' la pagina, cosi' la casa non
 *  deve dire niente.
 */
export function frameForApp(slug, { overlay = false, token } = {}) {
  const t = currentTheme();
  const lang = document.documentElement.lang || 'it';
  const src = `/apps/${encodeURIComponent(slug)}/index.html`
    + `?token=${encodeURIComponent(token || '')}`
    + `&theme=${encodeURIComponent(t.scheme)}&lang=${encodeURIComponent(lang)}`
    + `&accent=${encodeURIComponent(t.accent)}&onAccent=${encodeURIComponent(t.onAccent)}`
    + `&tokens=${encodeURIComponent(themeTokens())}`
    + (overlay ? '&overlay=1' : '');
  const iframe = document.createElement('iframe');
  iframe.setAttribute('sandbox', 'allow-scripts');
  iframe.src = src;
  return iframe;
}

export class AppsActions {
  /** @param source {import('./apps-source.js').AppsSource}
   *  @param shell  `{ sendChatPrompt(text) }` */
  constructor(source, shell) {
    this.source = source;
    this.shell = shell;
    /** La mini-app aperta sopra tutto, o `null`. */
    this._openApp = null;
    this._appHtmlWaiters = new Map();
    this._appHtmlSeq = 0;
    window.addEventListener('message', (e) => this._onAppMessage(e));
    /* Un iframe che non carica non dice niente al JS che lo contiene
       (cross-origin: `onerror` non scatta, `contentDocument` e' inaccessibile).
       L'unico che lo vede e' il WebViewClient del guscio, che ce lo rigira qui
       — v. `MainActivity.reportSubframeError`. Senza, una app che non carica e'
       un riquadro bianco e nient'altro, per qualunque causa.

       **Ed e' esattamente com'e' stato per un mese.** Questo ascolto stava nel
       costruttore della scheda «App» e se n'e' andato con lei: il guscio ha
       continuato a mandare l'evento, e di qua non c'era piu' nessuno. Il
       messaggio sul cleartext — la causa piu' frequente, e la meno indovinabile
       — e' tornato visibile solo in logcat. Stesso incidente del tema qui
       sotto, che invece qualcuno aveva notato. */
    window.addEventListener('jenny-subframe-error', (e) => this._onSubframeError(e));
    /* Il tema cambia **mentre** una mini-app e' aperta: dentro l'iframe non
       c'e' il nostro CSS, quindi la palette gli va spinta. Stava nel
       costruttore della scheda «App» e per un momento, cancellandola, e' andata
       persa con lei — la mini-app sarebbe restata coi colori di prima finche'
       non la riaprivi. */
    new MutationObserver(() => {
      const t = currentTheme();
      this._openApp?.iframe.contentWindow?.postMessage(
        { type: 'jenny:theme', theme: t.scheme, accent: t.accent, onAccent: t.onAccent,
          tokens: themeTokens() }, '*');
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    /* I dati della mini-app aperta sono cambiati da fuori — Jenny ha girato
       una sua azione in chat — e la mini-app se ne accorge solo se glielo si
       dice: `jenny:data-changed` e' cio' che `jenny-sdk.js` ascolta per
       rileggersi. Stesso incidente del tema qui sopra, e dell'errore di
       caricamento: stava nella scheda «App» ed e' andato via con lei. */
    source.onAppDataChanged((slug) => {
      const open = this._openApp;
      if (open?.slug !== slug) return;
      open.iframe.contentWindow?.postMessage({ type: 'jenny:data-changed', slug }, '*');
    });
  }

  /** «Annulla» sui due fogli.
   *
   *  Vive qui per la stessa ragione della passata del cassetto: la casa non ha
   *  nessun giro generico sui `data-i18n-*` — non ne aveva mai avuto bisogno —
   *  e il markup di questi fogli e' arrivato di la' dall'officina portandosi
   *  dietro le sue parole. Si scrive **all'apertura** e mai al caricamento:
   *  `i18n.load()` e' asincrona, e chiamarla presto stampa la chiave grezza
   *  (gia' successo il 20/09/2026 sul campo di ricerca).
   */
  _translateSheet(sheet) {
    const b = sheet?.querySelector('[id$="cancel"]');
    if (b) b.textContent = i18n.t('common.cancel');
  }

  /** Apre la *scheda* di una voce del cassetto: il foglio informativo, non la
   *  cosa. È ⇧⏎ dal cassetto, ed è la pressione lunga dalla cella della
   *  griglia — cioè la stessa strada già battuta, per la stessa ragione di
   *  `activateEntry`: due copie della scelta divergerebbero al primo caso
   *  particolare.
   *
   *  Le tre schede sono `<dialog>` aperte con `showModal()`, quindi vivono nel
   *  livello `dialog`, che sta **sopra** `launcher`: si sovrappongono al foglio
   *  e Indietro chiude prima loro, esattamente come la scheda di una skill
   *  locked aperta col tocco (3.7).
   */
  detailEntry(entry) {
    if (!entry) return;
    if (entry.kind === 'android') this.showAndroidAppSheet(entry.id);
    else if (entry.kind === 'jenny') this.showJennyAppSheet(entry.id);
  }
  /** Avvia una voce del cassetto.
   *
   *  Sta qui e non nel foglio perché "aprire" significa tre cose diverse nei tre
   *  spazi di nomi, e sono già decise: sono le stesse azioni del tap sulla cella
   *  spazi di nomi, e la scelta era gia' presa dalla scheda che non c'e' piu'.
   *  Due copie di questa scelta divergerebbero
   *  al primo caso particolare — una skill locked, una Jenny App rotta — ed è
   *  esattamente dove la divergenza si nota di meno e costa di più. */
  activateEntry(entry) {
    if (!entry) return;
    if (entry.kind === 'android') {
      /* **Ritornata**, non lasciata cadere: è l'unica delle tre attivazioni che
         può fallire in modo osservabile, e il cassetto ci decide sopra se
         chiudersi (6.3). Le altre due aprono qualcosa *sopra* il foglio e non
         hanno un esito da aspettare. */
      return this.launchAndroidApp(entry.id);
    }
    if (entry.kind === 'jenny') {
      // Rotta compresa: `openApp` chiede conferma e propone la riparazione in
      // chat, che dalla riga del cassetto è la strada giusta come dalla cella.
      this.openApp(entry.id);
    }
  }
  /** Avvia una app Android. Ritorna **se ci è riuscita** (6.3).
   *
   *  Prima qui c'era un `catch` vuoto commentato "best effort", e un avvio
   *  fallito non diceva niente: nessun toast, nessun messaggio — il difetto che
   *  `docs/using/app-launcher.md` elencava. L'informazione c'era già e la si
   *  buttava: l'endpoint risponde 404 quando il pacchetto non c'è più o Android
   *  rifiuta di avviarlo, e `api.launchAndroidApp` lo alza.
   *
   *  Il caso vero non è esotico: una app disinstallata (o disabilitata) fra il
   *  caricamento della lista e il tocco lascia una riga stantia, e toccarla non
   *  faceva assolutamente niente — indistinguibile da un tocco non registrato.
   *
   *  L'etichetta si cerca nella lista in memoria: se il pacchetto è già sparito
   *  di lì, il nome del pacchetto è comunque meglio di una frase senza soggetto.
   */
  async launchAndroidApp(packageName) {
    try {
      await api.launchAndroidApp(packageName);
      return true;
    } catch {
      const name = this.source.androidApps.find(a => a.packageName === packageName)?.label
        || packageName;
      showToast(i18n.t('apps.launchFailed', { name }), 'error');
      return false;
    }
  }
  async openApp(slug) {
    const app = this.source.jennyApps.find(a => a.slug === slug);
    if (!app) return;
    if (app.broken) {
      const ok = await confirmDialog(
        i18n.t('apps.brokenConfirm', { name: app.name || slug, error: app.error || i18n.t('apps.invalidManifest') })
      );
      if (ok) {
        this.shell.sendChatPrompt(i18n.t('apps.brokenPrompt', { slug, error: app.error || i18n.t('apps.invalidManifest') }));
      }
      return;
    }

    if (!api.getSecret()) {
      try { await api.bootstrap(); } catch { return; }
    }

    if (app.view_kind === 'external') {
      await this._openExternalView(slug, app);
      return;
    }

    let token;
    try {
      token = await api.appToken(slug);
    } catch (e) {
      showToast(String(e.message || e), 'error');
      return;
    }
    this._mountVeil(slug, app, frameForApp(slug, { overlay: true, token }));
  }

  /* Il velo sopra tutto con la testata e la *iframe* dentro: è lo stesso per
     l'app del gateway e per la vista esterna, che differiscono solo nella
     cornice (sandbox e origine, v. `_openExternalView`) e nel proxy da
     chiudere, che `closeApp` riconosce da *external*. */
  _mountVeil(slug, app, iframe, { external = false } = {}) {
    this.closeApp();
    const overlay = document.createElement('div');
    overlay.className = 'app-frame-overlay';
    overlay.innerHTML = `
      <div class="app-frame-header">
        <span class="app-frame-title">${escapeHtml(app.name || slug)}</span>
        <button class="app-frame-close" title="${i18n.t('apps.close')}"><i class="ti ti-x"></i></button>
      </div>
    `;
    overlay.appendChild(iframe);
    overlay.querySelector('.app-frame-close').addEventListener('click', () => this.closeApp());

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    this._openApp = { slug, overlay, iframe, depth: 1 };
    if (external) this._openApp.external = true;
  }
  async _openExternalView(slug, app) {
    let url;
    try {
      const res = await fetch(`/api/webui/apps/${encodeURIComponent(slug)}/view`, {
        headers: { 'Authorization': `Bearer ${api.getSecret()}` },
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.url) throw new Error(body.error || `HTTP ${res.status}`);
      url = body.url;
    } catch (e) {
      showToast(i18n.t('apps.viewProxyFailed', { error: String(e.message || e) }), 'error');
      return;
    }

    const iframe = document.createElement('iframe');
    /* Sandbox più largo che per una app normale, e la differenza è l'ORIGINE,
       non la fiducia.

       Una app normale è servita DALL'ORIGINE DEL GATEWAY: lasciarle la sua
       origine naturale (`allow-same-origin`) le darebbe il DOM e il
       localStorage della SPA e l'API del gateway col token. Per quello lì il
       sandbox è `allow-scripts` e basta.

       Una vista esterna sta su `http://127.0.0.1:<porta effimera>`: porta
       diversa → **origine diversa** dal gateway. `allow-same-origin` le
       restituisce la sua origine, che è quella del proxy e di nessun altro, e
       la same-origin policy del browser la tiene comunque fuori dalla SPA.

       Senza `allow-same-origin` invece l'origine è opaca e la pagina remota è
       di fatto morta: cookie bloccati, `localStorage` che solleva, e i suoi
       stessi `fetch` con `Origin: null`. È anche il motivo per cui questa vista
       NON può essere un iframe annidato dentro l'app-frame sandboxata: i flag
       di sandbox si ereditano nei frame figli. */
    iframe.setAttribute(
      'sandbox',
      'allow-scripts allow-same-origin allow-forms allow-popups allow-modals'
    );
    iframe.src = url;
    this._mountVeil(slug, app, iframe, { external: true });
  }
  /** C'e' una mini-app aperta sopra tutto? Lo chiede il guscio della casa,
   *  che prima leggeva `_openApp` da fuori. */
  isAppOpen() {
    return Boolean(this._openApp);
  }
  closeApp() {
    const open = this._openApp;
    if (!open) return;
    this._openApp = null;
    open.overlay.classList.remove('visible');
    setTimeout(() => open.overlay.remove(), 200);
    /* Il listener del proxy vive quanto la vista: chiuderlo qui è la via
       normale. Se questo fetch non arriva (processo ucciso, rete interna giù)
       ci pensa l'idle timeout lato Python — non resta aperto per sempre. */
    if (open.external) {
      fetch(`/api/webui/apps/${encodeURIComponent(open.slug)}/view/close`, {
        headers: { 'Authorization': `Bearer ${api.getSecret()}` },
      }).catch(() => {});
    }
  }
  handleBack() {
    const open = this._openApp;
    if (!open) return false;
    /* `depth` è ciò che l'app dichiara via `jenny:nav-state`: schermate interne
       più <dialog> aperti. I dialog contano perché l'iframe ha origine opaca e
       il livello `dialog` della catena, che interroga solo il documento del
       parent, non li vede: senza questo ramo Indietro chiudeva tutta l'app
       portandosi via il form a metà. */
    if (open.depth > 1) {
      open.iframe.contentWindow?.postMessage({ type: 'jenny:go-back' }, '*');
      return true;
    }
    /* All'ultimo livello l'app si chiude, e sotto c'e' gia' da dove e' stata
       aperta: il cassetto, una pagina della casa, la vista dell'officina.
       Qui c'era un `switchMode('apps')` verso la scheda «App», che non esiste
       piu' (21/09/2026): nell'officina era un «Unknown mode» a ogni Indietro. */
    this.closeApp();
    return true;
  }
  _onSubframeError(event) {
    const open = this._openApp;
    if (!open) return;
    const d = event?.detail;
    if (!d || typeof d !== 'object') return;

    /* `ERR_CLEARTEXT_NOT_PERMITTED` merita il suo messaggio: la causa non è
       nell'app né nella rete, è la network security config dell'APK, che
       permette il cleartext solo verso il gateway. Detto come errore generico
       manda a cercare il guasto dove non è — che è esattamente quello che è
       successo. */
    const cleartext = String(d.description || '').includes('ERR_CLEARTEXT_NOT_PERMITTED');
    const host = String(d.host || d.url || '');
    const message = cleartext
      ? i18n.t('apps.frameCleartextBlocked', { host })
      : i18n.t('apps.frameLoadFailed', {
          host, reason: String(d.description || d.errorCode || ''),
        });

    let banner = open.overlay.querySelector('.app-frame-error');
    if (!banner) {
      banner = document.createElement('div');
      banner.className = 'app-frame-error';
      open.overlay.querySelector('.app-frame-header')?.insertAdjacentElement('afterend', banner);
    }
    banner.innerHTML = `<i class="ti ti-alert-triangle"></i><span>${escapeHtml(message)}</span>`;
  }
  _onAppMessage(event) {
    const open = this._openApp;
    if (!open || event.source !== open.iframe.contentWindow) return;
    const msg = event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'jenny:nav-state') {
      // Il valore arriva da codice dell'app: si accetta solo un intero in un
      // intervallo sensato, altrimenti un NaN (o un numero enorme) renderebbe
      // Indietro inutile fino alla ✕.
      const depth = Math.floor(Number(msg.depth));
      open.depth = Number.isFinite(depth) ? Math.min(99, Math.max(1, depth)) : 1;
      return;
    }
    if (msg.type === 'jenny:discuss') {
      const app = this.source.jennyApps.find(a => a.slug === open.slug);
      const name = app?.name || open.slug;
      const text = String(msg.text || '').slice(0, 4000);
      this.closeApp();
      this.shell.sendChatPrompt(i18n.t('apps.chatAboutApp', { name, text }));
      return;
    }
    if (msg.type === 'jenny:ui-result') {
      // Risposta al round-trip di requestAppHtml: risolve il waiter del nonce.
      const waiter = this._appHtmlWaiters.get(msg.nonce);
      if (waiter) {
        this._appHtmlWaiters.delete(msg.nonce);
        clearTimeout(waiter.timer);
        waiter.resolve(String(msg.html || ''));
      }
    }
  }
  requestAppHtml(timeoutMs = 2000) {
    const open = this._openApp;
    if (!open || !open.iframe.contentWindow) return Promise.resolve(null);
    const nonce = 'app-html-' + (++this._appHtmlSeq);
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this._appHtmlWaiters.delete(nonce);
        resolve(null);
      }, timeoutMs);
      this._appHtmlWaiters.set(nonce, { resolve, timer });
      open.iframe.contentWindow.postMessage({ type: 'jenny:ui-query', nonce }, '*');
    });
  }
  showAndroidAppSheet(packageName) {
    const app = this.source.androidApps.find(a => a.packageName === packageName);
    if (!app) return;

    const sheet = document.getElementById('android-app-sheet');
    if (!sheet) return;

    const icon = app.icon
      ? `<img src="${escapeHtml(app.icon)}" alt="">`
      : '<i class="ti ti-apps"></i>';
    document.getElementById('android-app-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon">${icon}</div>
        <div class="app-sheet-name">${escapeHtml(app.label)}</div>
      </div>`;

    const actions = [
      { icon: 'ti-player-play', label: i18n.t('apps.open'), action: 'launch' },
      { icon: 'ti-info-circle', label: i18n.t('apps.appInfo'), action: 'info' },
    ];
    if (!app.system) {
      actions.push({ icon: 'ti-trash', label: i18n.t('apps.uninstall'), action: 'uninstall', danger: true });
    }
    const actionsEl = document.getElementById('android-app-actions');
    actionsEl.innerHTML = actions.map(a =>
      `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
        <i class="ti ${a.icon}"></i>${a.label}
      </button>`
    ).join('');

    const close = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        sheet.close();
        await this._handleAndroidSheetAction(btn.dataset.action, app);
      });
    });

    const cancelBtn = document.getElementById('android-app-cancel');
    cancelBtn.onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    this._translateSheet(sheet);
    sheet.showModal();
  }
  async _handleAndroidSheetAction(action, app) {
    const pkg = app.packageName;
    if (action === 'launch') {
      this.launchAndroidApp(pkg);
    } else if (action === 'info') {
      try { await api.openAndroidAppInfo(pkg); } catch {}
      // Da "Info app" si può disinstallare: al rientro la lista va riallineata.
      this.source.reloadOnReturn();
    } else if (action === 'uninstall') {
      // Nessuna conferma nostra: quella di Android arriva comunque e non è
      // aggirabile, quindi la nostra era solo un tap in più prima della
      // domanda vera. Stesso comportamento dei launcher di sistema.
      try { await api.uninstallAndroidApp(pkg); } catch {}
      this.source.reloadOnReturn();
    }
  }
  showJennyAppSheet(slug) {
    const app = this.source.jennyApps.find(a => a.slug === slug);
    if (!app) return;

    const sheet = document.getElementById('jenny-app-sheet');
    if (!sheet) return;

    const icon = app.broken ? 'ti-alert-triangle' : (app.icon || 'ti-apps');
    document.getElementById('jenny-app-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ${escapeHtml(icon)}"></i></div>
        <div class="app-sheet-name">${escapeHtml(app.name || app.slug)}</div>
      </div>`;

    /* Le quattro righe della casa, nello stesso ordine della scheda di un
       quaderno: Apri · Metti come pagina · Modifica · Elimina. La seconda c'e'
       solo se il guscio ha le pagine — l'officina no, e la scheda non deve
       sapere in che guscio vive: lo dice la porta che le passano. */
    const homePages = this.shell?.homePages?.() || null;
    const actions = [
      { icon: 'ti-player-play', label: i18n.t('apps.open'), action: 'open' },
      ...(homePages ? [pageRow(homePages.state('app', slug), app)] : []),
      { icon: 'ti-edit', label: i18n.t('apps.edit'), action: 'edit' },
      { icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true },
    ];

    const actionsEl = document.getElementById('jenny-app-sheet-actions');
    actionsEl.innerHTML = actions.map(drawRow).join('');

    const close = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        sheet.close();
        await this._handleJennySheetAction(btn.dataset.action, app);
      });
    });

    document.getElementById('jenny-app-sheet-cancel').onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    this._translateSheet(sheet);
    sheet.showModal();
  }
  async _handleJennySheetAction(action, app) {
    const slug = app.slug;
    if (action === 'open') {
      this.openApp(slug);
    } else if (action === 'pin') {
      await this.shell?.homePages?.()?.append('app', slug);
    } else if (action === 'unpin') {
      if (await this.shell?.homePages?.()?.detach('app', slug)) {
        showToast(i18n.t('apps.unpinned'), 'success');
      }
    } else if (action === 'edit') {
      this._startAppModification(app);
    } else if (action === 'delete') {
      const ok = await confirmDialog(
        i18n.t('apps.deleteAppConfirm', { name: app.name || slug })
      );
      if (!ok) return;
      try {
        await api.deleteJennyApp(slug);
        await this.source.loadJennyApps();
        /* Il gateway ha tolto anche la sua pagina, se ne aveva una (v.
           `apps_api.delete_app`): qui la casa lo deve sapere, o resterebbe un
           pagina verso un'app che non c'e' piu'. */
        await this.shell?.homePages?.()?.reload();
        showToast(i18n.t('apps.appDeleted'), 'success');
      } catch {
        showToast(i18n.t('apps.deleteFailed'), 'error');
      }
    }
  }
  _startAppModification(app) {
    this.shell.sendChatPrompt(i18n.t('apps.editAppPrompt', { name: app.name || app.slug, slug: app.slug }));
  }
}

/** La riga «Metti come pagina», o perche' non si puo'.
 *
 *  **Spenta, non assente**, quando l'app non puo' stare in una pagina: una
 *  riga che manca fa chiedere «perche' Todo si' e WaterBot no?», una spenta
 *  lo dice. Un'app *esterna* apre un indirizzo che il guscio non controlla;
 *  una *rotta* resterebbe li' a non funzionare tutti i giorni.
 */
function pageRow(state, app) {
  const why = app.broken
    ? 'apps.pageBroken'
    : app.view_kind === 'external'
      ? 'apps.pageExternal'
      : state === 'piena' ? 'apps.pageFull' : null;
  if (state === 'pending') {
    return { icon: 'ti-pinned-off', label: i18n.t('apps.unpinPage'), action: 'unpin' };
  }
  return {
    icon: 'ti-pin',
    label: i18n.t('apps.pinAsPage'),
    action: 'pin',
    ...(why ? { disabled: true, reason: i18n.t(why) } : {}),
  };
}

/** Una riga di scheda, come testo. Esportata perche' la scheda di un quaderno
 *  in casa e' **la stessa cosa a vedersi**: stesso markup, stesse classi, e una
 *  seconda copia divergerebbe al primo ritocco. */
export function drawRow(a) {
  const classes = `oc-sheet-action${a.danger ? ' danger' : ''}`;
  const off = a.disabled ? ' disabled aria-disabled="true"' : '';
  /* Il testo nudo come prima, se non c'e' un perche': la stessa scheda la
     disegna anche l'officina, e li' non deve cambiare niente. */
  const text = a.reason
    ? `<span class="oc-sheet-label">${a.label}<span class="oc-sheet-reason">${escapeHtml(a.reason)}</span></span>`
    : a.label;
  return `<button class="${classes}" data-action="${a.action}"${off}>
        <i class="ti ${a.icon}"></i>${text}
      </button>`;
}
