/** Mobile Settings Controller — accordion-based settings panel. */

import { api } from './shared/api-client.js';
import { copyToClipboard, escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { AppState } from './shared/state.js';
import { confirmDialog, detailDialog } from './shared/dialog.js';
import { THEMES, DEFAULT_THEME, setTheme } from './shared/theme.js';
import { advancedMode, setAdvancedMode } from './shared/advanced-mode.js';
import { mascotVisible, setMascotVisible, mascotSize, setMascotSize,
  MASCOT_SIZES } from './shared/mascot.js';
import { homeView, setHomeView, HOME_VIEW_CHOICES } from './shared/home-view.js';
import { TelegramPairingWidget } from './shared/telegram-pairing.js';
import {
  BatteryExemptionCard,
  batteryExemptionSupported,
  batteryExemptionNeeded,
} from './shared/battery-exemption.js';
import {
  runExportFlow,
  runImportFlow,
  runSnapshotRestore,
} from './shared/backup-flow.js';

// Ripiego per `power.modes` quando il payload arriva da un gateway più vecchio
// del client: stesso ordine di `KEEP_AWAKE_MODES` in config/schema.py, dal più
// parsimonioso al più affamato.
const KEEP_AWAKE_CHOICES = ['off', 'turns', 'always'];

/* Distanza fra "ultimo tentativo" e "ultimo tentativo riuscito" oltre la quale
   il controllo aggiornamenti va dichiarato rotto in pagina.

   Sette giorni perché il job gira ogni ventiquattr'ore (`updates.checkInterval_h`,
   default 24): una settimana di scarto vuol dire almeno sette tentativi andati
   a vuoto di fila, che nessun disguido passeggero spiega — un'antenna che va e
   viene, un riavvio, una notte senza rete rientrano tutti abbondantemente sotto
   soglia e restano silenziosi, come devono. Più corta griderebbe al lupo; più
   lunga lascerebbe un manifest pubblicato col nome sbagliato invisibile per
   metà mese. */
const UPDATE_STALE_MS = 7 * 86400000;

export class SettingsController {
  constructor() {
    this.contentEl = document.getElementById('settings-content');
    this.loadingEl = document.getElementById('settings-loading');
    this.data = null;
    this._debounceTimers = {};
    // Sezioni aperte, per id: sopravvive ai re-render (che ricostruiscono
    // tutto l'HTML), non alla navigazione — niente localStorage di proposito.
    this._openSections = new Set();
    // Contatore di generazione: incrementato in deactivate(). Ogni
    // continuazione lo cattura prima del primo await ed esce se è cambiato —
    // altrimenti scrive nel DOM (o apre modali) di una sezione già lasciata.
    this._gen = 0;
    /* Posizione di lettura e stato del catalogo modelli. Come `_openSections`
       vivono nel controller: il contenitore che scorre è lo stesso che
       `render()` riscrive per intero, quindi qualunque salvataggio — e
       scegliere un modello *è* un salvataggio — riportava in cima una pagina
       lunga, col catalogo richiuso e il filtro perso. */
    this._scrollTop = 0;
    this._catalogOpen = false;
    this._catalogFilter = '';
    /* Vero mentre *noi* stiamo scrivendo `scrollTop`, e vero finché il
       contenuto asincrono di un `render()` non è ancora atterrato. Vedi
       `_restoreScrollTop()`. */
    this._restoringScroll = false;
    this._restorePending = false;
    /* Installazione dell'aggiornamento: `null` finché non la si avvia, poi
       {busy, noteKey, phase, progress, detail}. Vive nel controller e non nel
       DOM perché ogni salvataggio riscrive tutta la pagina, e un'installazione
       in corso non è una cosa che possa sparire da sotto gli occhi. */
    this._update = null;
    this._updateTimer = null;
    this._updatePolls = 0;
    /* Vero mentre un controllo manuale è in volo. Vive nel controller e non
       nel DOM per lo stesso motivo di `_update`: un salvataggio qualsiasi
       riscrive tutta la pagina, e il bottone deve restare disabilitato. */
    this._checking = false;
    /* La posizione va letta *mentre* la vista è visibile: `switchMode` mette il
       display:none sulla view prima di chiamare `deactivate()`, e un
       contenitore senza box legge scrollTop 0 — salvare lì avrebbe riportato in
       cima a ogni rientro invece di evitarlo. */
    this.contentEl?.addEventListener('scroll', () => {
      // Un ripristino non è una lettura: la sua assegnazione torna clampata
      // dalla pagina ancora corta e qui riscriverebbe `_scrollTop` col valore
      // sbagliato, distruggendo proprio ciò che stava ripristinando.
      if (this._restoringScroll) return;
      // Ha scorso l'utente: da qui in poi nessun contenuto in ritardo ha più il
      // diritto di riportarlo dov'era prima del re-render.
      this._restorePending = false;
      if (this.contentEl.clientHeight) this._scrollTop = this.contentEl.scrollTop;
    }, { passive: true });
    /* Niente `loadSettings()` qui. Il costruttore gira dentro `switchMode`,
       che subito dopo chiama `activate()` — e `activate()` carica. Risultato:
       due GET /api/settings e due render completi alla prima apertura, con il
       secondo che butta via tutto il DOM del primo (widget Telegram e card
       batteria compresi, ricreati da capo). `this.ready = this.loadSettings()`
       non risolverebbe: `switchMode` chiama `activate()` comunque. */
  }

  showLoading() { this.loadingEl?.classList.add('active'); }
  hideLoading() { this.loadingEl?.classList.remove('active'); }

  /** true se nel frattempo si è usciti dalla sezione. */
  _stale(gen) { return gen !== this._gen; }

  async loadSettings() {
    const gen = this._gen;
    this.showLoading();
    try {
      const settings = await api.getSettings();
      if (this._stale(gen)) return;
      this.data = settings;
      this.render();
    } catch (err) {
      if (this._stale(gen)) return;
      this.contentEl.innerHTML = `
        <div class="settings-error">
          <i class="ti ti-cloud-off" style="font-size:32px;color:var(--text-faint)"></i>
           <p>${i18n.t('settings.failedToLoad')}</p>
          <p style="font-size:11px;color:var(--text-faint)">${escapeHtml(err.message)}</p>
        </div>`;
    } finally {
      if (!this._stale(gen)) this.hideLoading();
    }
  }

  activate() { this.loadSettings(); }
  deactivate() {
    // Da qui in poi nessuna continuazione in volo tocca più niente: né il DOM
    // di questa sezione, né — soprattutto — una modale sopra un'altra.
    this._gen++;
    this.hideLoading();
    if (this._tgWidget) {
      this._tgWidget.destroy();
      this._tgWidget = null;
    }
    // La card batteria resta in ascolto di visibilitychange finché non la si
    // chiude: senza questo, ogni ritorno nelle impostazioni ne lascia una viva.
    if (this._batteryCard) {
      this._batteryCard.destroy();
      this._batteryCard = null;
    }
    // Stesso motivo per il listener della diagnostica: senza, ogni ritorno
    // nelle impostazioni ne lascia uno vivo che ricarica l'endpoint.
    if (this._onPowerVisible) {
      document.removeEventListener('visibilitychange', this._onPowerVisible);
      this._onPowerVisible = null;
    }
    // Il polling dell'installazione: il guard di generazione già ferma la
    // continuazione, ma il timer va spento comunque per non tenere sveglia una
    // sezione che non è più a schermo.
    clearTimeout(this._updateTimer);
    this._updateTimer = null;
  }

  /* Sotto-stato della sezione: il catalogo modelli aperto occupa la vista e per
     l'utente *è* una schermata (ci si arriva da un pulsante, si scorre, si
     sceglie). Senza questo il tasto Indietro saltava quel livello e usciva
     direttamente dalle impostazioni: due schermate in una pressione sola. */
  handleBack() {
    const el = this.contentEl?.querySelector('#model-catalog');
    if (!el || el.style.display === 'none') return false;
    this._toggleModelCatalog();
    return true;
  }

  handleAction(action) {
    if (action === 'refresh') this.loadSettings();
  }

  // ── Rendering ──────────────────────────────────────────────────────

  render() {
    const d = this.data;
    if (!d) return;

    // Sezioni tematiche, una per asse mentale: preferenze d'interfaccia, motore
    // LLM, capacità dell'agente, la sua memoria, i lavoratori periodici che la
    // curano, canali, dati, diagnostica.
    this.contentEl.innerHTML = [
      this._renderConfigRecovery(d),
      this._renderCronRecovery(d),
      this._section('personalization', 'ti-palette', i18n.t('settings.personalization'), this._renderPersonalization(d)),
      this._section('models', 'ti-cpu', i18n.t('settings.model'), this._renderModelSettings(d)),
      this._section('tools', 'ti-tool', i18n.t('settings.tools'), this._renderTools(d)),
      this._section('memory', 'ti-sparkles', i18n.t('settings.memory.title'), this._renderMemory(d)),
      this._section('workers', 'ti-map', i18n.t('settings.workers.title'), this._renderWorkers(d)),
      this._renderBatterySection(d),
      this._section('ssh', 'ti-terminal-2', i18n.t('settings.ssh.title'), this._renderSsh()),
      this._section('telegram', 'ti-brand-telegram', i18n.t('settings.telegram.title'), this._renderTelegram()),
      this._section('backup', 'ti-database-export', i18n.t('backup.sectionTitle'), this._renderBackup()),
      this._section('system', 'ti-info-circle', i18n.t('settings.system'), this._renderSystem(d)),
    ].join('');

    this._wireSections();
    // L'innerHTML qui sopra ha appena riportato il catalogo chiuso e vuoto e lo
    // scroll in cima: entrambi vanno rimessi come li aveva lasciati l'utente.
    this._restoreCatalogState();
    /* In questo istante catalogo, SSH, snapshot, widget Telegram e card
       batteria sono ancora segnaposto: la pagina è molto più corta di quando la
       posizione fu misurata. Si rimette ora *e* la si riapplica quando i pezzi
       atterrano — v. `_restoreScrollTop()`. */
    this._restorePending = true;
    this._restoreScrollTop();
  }

  /* Rimette la posizione di lettura senza distruggerla. Due insidie, entrambe
     osservate su Blink:
     1. scrivere `scrollTop` emette un evento `scroll`, e il listener del
        costruttore riscriveva `_scrollTop` con quello che l'assegnazione era
        *diventata* — cioè col clamp a `scrollHeight - clientHeight` di una
        pagina ancora fatta di segnaposto. La posizione buona non risultava
        approssimata: risultava persa. Il flag rende cieco quel listener finché
        l'evento non è smaltito (le scroll steps girano prima dei callback di
        `requestAnimationFrame`, quindi azzerarlo lì è sicuro).
     2. una sola assegnazione non basta: i caricatori asincroni allungano la
        pagina dopo, e ognuno richiama questo metodo quando ha finito; in più
        subito dopo un `innerHTML` le altezze definitive arrivano un frame dopo,
        e per quello c'è il secondo colpo nel rAF. Il flag si azzera in un rAF
        *annidato* perché l'evento della seconda scrittura viene smaltito nelle
        scroll steps del frame ancora successivo.
     `_restorePending` è la clausola di rispetto: se nel frattempo l'utente ha
     scorso di suo, un fetch in ritardo non lo strattona più. */
  _restoreScrollTop() {
    if (!this.contentEl || !this._restorePending || !this._scrollTop) return;
    this._restoringScroll = true;
    this.contentEl.scrollTop = this._scrollTop;
    requestAnimationFrame(() => {
      if (this._restorePending) this.contentEl.scrollTop = this._scrollTop;
      requestAnimationFrame(() => { this._restoringScroll = false; });
    });
  }

  /* Riapre il catalogo modelli e rimette il testo del filtro dopo un
     `render()`. Non è una comodità: il catalogo si richiude a ogni salvataggio,
     e provare due modelli di fila significava riaprirlo e rifiltrarlo ogni
     volta. */
  _restoreCatalogState() {
    if (!this._catalogOpen) return;
    const el = this.contentEl.querySelector('#model-catalog');
    if (!el) return;
    el.style.display = '';
    const search = this.contentEl.querySelector('#model-search');
    if (search) search.value = this._catalogFilter;
    this._loadModelCatalog();
  }

  /* Avviso di config recuperata all'avvio. Silenzioso nel caso normale: se
     compare, l'utente sta usando impostazioni che non sono quelle che aveva
     scelto — e con restored_from = "defaults" deve rimettere anche la chiave
     API. Farglielo scoprire da solo sarebbe la sorpresa peggiore. */
  _renderConfigRecovery(d) {
    const info = d.config_recovery;
    if (!info) return '';
    const fromDefaults = info.restored_from === 'defaults';
    return this._recoveryNotice(
      i18n.t(fromDefaults ? 'settings.configRecoveredDefaults' : 'settings.configRecoveredBackup'),
      info.broken_file,
      fromDefaults,
    );
  }

  /* Stesso avviso per lo store dei job cron. Merita una riga sua e non una
     variante di quella sopra: qui, con restored_from = "empty", a mancare sono
     i promemoria che l'utente aveva creato — e quelli non si notano assenti,
     si notano solo quando non suonano. */
  _renderCronRecovery(d) {
    const info = d.cron_recovery;
    if (!info) return '';
    const empty = info.restored_from === 'empty';
    return this._recoveryNotice(
      i18n.t(empty ? 'settings.cronRecoveredEmpty' : 'settings.cronRecoveredBackup'),
      info.broken_file,
      empty,
    );
  }

  _recoveryNotice(text, brokenFile, strong) {
    const where = brokenFile
      ? `<div class="settings-notice-path">${escapeHtml(brokenFile)}</div>`
      : '';
    return `<div class="settings-notice${strong ? ' settings-notice-strong' : ''}">
      <i class="ti ti-alert-triangle"></i>
      <div>
        <div>${text}</div>
        ${where}
      </div>
    </div>`;
  }

  _section(id, icon, title, body) {
    const collapsed = this._openSections.has(id) ? '' : ' collapsed';
    return `<div class="settings-section${collapsed}" data-section="${id}">
      <div class="settings-section-header">
        <i class="ti ${icon}"></i>
        <span>${title}</span>
        <i class="ti ti-chevron-down settings-chevron"></i>
      </div>
      <div class="settings-section-body">${body}</div>
    </div>`;
  }

  // ── Personalizzazione ──────────────────────────────────────────────

  /* Tutte le preferenze "come appare e come parla l'interfaccia": temi,
     mascotte, nome del bot e lingua vivono qui, sullo stesso asse. */
  _renderPersonalization(d) {
    const a = d.agent || {};
    return `
      ${this._renderTheme()}
      ${this._renderHomeView()}
      <div class="theme-strip-eyebrow">${i18n.t('settings.botName')}</div>
      <div class="settings-field">
        <input type="text" class="settings-input" data-key="bot_name" value="${escapeHtml(a.bot_name || '')}" />
      </div>
      <div class="theme-strip-eyebrow">${i18n.t('settings.language')}</div>
      ${this._renderLanguage()}`;
  }

  // ── Attività in background (doze) ──────────────────────────────────

  /* Sezione a sé, non annidata sotto Telegram: il doze differisce cron, Dream,
     promemoria e heartbeat esattamente come rallenta il long-poll, ma
     finché la richiesta viveva solo nella card di pairing chi Telegram non lo
     usa non se la vedeva chiedere mai.

     Due impostazioni, una storia sola: l'esenzione dice ad Android di non
     strozzare Jenny, keepAwake decide se Jenny tiene sveglia la CPU da sé.
     Fuori dalla WebView Android il bridge nativo non c'è e la card sparisce,
     ma keepAwake vive nel config del gateway ed è modificabile da qualunque
     browser: la sezione resta, con il solo controllo che ha ancora senso. */
  _renderBatterySection(d) {
    // Aperta d'ufficio quando l'esenzione manca: un accordion chiuso è
    // esattamente il posto in cui il problema è rimasto invisibile finora.
    if (batteryExemptionSupported() && batteryExemptionNeeded()) {
      this._openSections.add('battery');
    }
    const card = batteryExemptionSupported()
      ? `<div id="settings-battery-card"></div><div class="settings-divider"></div>`
      : '';
    return this._section(
      'battery', 'ti-battery-charging', i18n.t('settings.battery.title'),
      `${card}${this._renderKeepAwake(d)}<div id="settings-power-diagnostics"></div>`,
    );
  }

  /* Wakelock anti-doze. Il costo della scelta ("i lavori slittano", "consuma
     batteria") è l'unica cosa che qui conta — il nome da solo ("Sempre") non
     dice cosa l'utente sta accettando — ma dentro una <option> non ci stava:
     il testo di un'opzione nativa non va a capo, e su un telefono da 1440px la
     frase veniva tagliata esattamente sulla clausola del costo. Quindi nella
     select resta il nome breve e il costo vive sotto, su una riga che segue la
     selezione (v. `_wireSections`) e che può occupare le righe che le servono.

     La riga sul riavvio non è un dettaglio da nota a piè di pagina: il lock di
     servizio si prende una volta all'avvio del gateway, quindi chi passa a
     "Sempre" e resta a guardare non vedrebbe cambiare niente e penserebbe che
     l'impostazione sia rotta. */
  _renderKeepAwake(d) {
    const power = (d && d.power) || {};
    const current = power.keep_awake || 'turns';
    const modes = power.modes || KEEP_AWAKE_CHOICES;
    const options = modes.map(id =>
      `<option value="${escapeHtml(id)}"${id === current ? ' selected' : ''}>${escapeHtml(i18n.t(`settings.battery.keepAwake.${id}`))}</option>`
    ).join('');
    return `
      <div class="settings-subheading">${i18n.t('settings.battery.keepAwakeTitle')}</div>
      <div class="settings-field">
        <select class="settings-select" id="keep-awake-select">${options}</select>
        <p class="settings-choice-cost" id="keep-awake-cost">${escapeHtml(this._keepAwakeCost(current))}</p>
        <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.battery.keepAwakeHint')}</p>
        <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)"><i class="ti ti-refresh"></i> ${i18n.t('settings.battery.keepAwakeRestart')}</p>
      </div>`;
  }

  /** Il costo della modalità, o stringa vuota se non lo conosciamo.
   *
   *  I modi arrivano dal gateway (`power.modes`), la copy dai file i18n: un
   *  gateway più nuovo del client può mandarne uno che qui non ha frase, e
   *  `i18n.t` in quel caso ritorna la chiave — stampata sotto la select
   *  sembrerebbe un guasto. Meglio nessuna riga che "settings.battery...". */
  _keepAwakeCost(mode) {
    const key = `settings.battery.keepAwakeCost.${mode}`;
    const text = i18n.t(key);
    return text === key ? '' : text;
  }

  // ── Diagnostica energetica ─────────────────────────────────────────

  /* Chiamata a parte e non dentro il payload delle impostazioni: interroga il
     bridge Android (tre chiamate JNI) e va riletta al ritorno da un dialogo di
     sistema, quando il resto delle impostazioni non è cambiato.

     Il pannello risponde alla domanda che finora non aveva risposta: "sta
     girando o no?". Un gateway ucciso dal gestore energetico dell'OEM non
     lascia niente dietro di sé — nessun errore, nessuna notifica, solo
     promemoria che smettono di arrivare — e l'utente se ne accorge giorni
     dopo, se se ne accorge. */
  async _loadPowerDiagnostics() {
    const el = this.contentEl.querySelector('#settings-power-diagnostics');
    if (!el) return;
    let diag = null;
    try {
      diag = await api.getPowerDiagnostics();
    } catch (_) {
      // Endpoint muto (gateway vecchio, richiesta fallita): una riga sobria,
      // non un errore rosso — qui non si è rotto niente di quello che l'utente
      // stava facendo.
      if (el.isConnected) {
        el.innerHTML = `<div class="settings-divider"></div>
          <div class="settings-empty-state">${i18n.t('settings.battery.diagUnavailable')}</div>`;
      }
      return;
    }
    // Un re-render nel frattempo ha staccato questo nodo: la risposta appartiene
    // a un pannello che non è più nel documento, e il nuovo si ricarica da sé.
    if (!el.isConnected) return;
    // Fuori da Android i tre booleani non significano niente e i buchi non si
    // misurano: meglio niente pannello che un pannello di "no".
    if (!diag || !diag.android) { el.innerHTML = ''; return; }
    el.innerHTML = this._renderPowerDiagnostics(diag);
    this._wirePowerDiagnostics(el);
  }

  _renderPowerDiagnostics(diag) {
    const rows = [
      ['diagExempt', diag.battery_exempt],
      ['diagExactAlarms', diag.exact_alarms],
      ['diagWakelock', diag.wakelock_held],
    ].map(([key, ok]) => `
      <div class="settings-field-row">
        <span class="settings-field-label">${i18n.t(`settings.battery.${key}`)}</span>
        <span class="settings-field-value"><i class="ti ti-${ok ? 'check' : 'x'}"></i> ${i18n.t(ok ? 'settings.battery.diagYes' : 'settings.battery.diagNo')}</span>
      </div>`).join('');
    const gaps = Array.isArray(diag.gaps) ? diag.gaps : [];
    const gapRows = gaps.length
      ? gaps.map(g => `
          <div class="settings-field-row">
            <span class="settings-field-label">${escapeHtml(this._formatGapDuration(g.duration_ms))}</span>
            <span class="settings-field-value">${escapeHtml(this._formatGapWhen(g.start_ms))}</span>
          </div>`).join('')
      : `<div class="settings-empty-state">${i18n.t('settings.battery.gapsEmpty')}</div>`;
    return `
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.battery.diagTitle')}</div>
      ${rows}
      ${this._renderExactAlarmRequest(diag)}
      <div class="settings-subheading">${i18n.t('settings.battery.gapsTitle')}</div>
      ${gapRows}
      <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.battery.gapsHint', { minutes: diag.gap_warning_min })}</p>
      ${gaps.length ? this._renderOemGuidance() : ''}`;
  }

  /* Il permesso da cui dipende tutto il resto, e l'unico modo di concederlo.
     Un'app che punta ad API 33 o più si ritrova SCHEDULE_EXACT_ALARM negato
     alla prima installazione: dichiararlo nel manifest non basta, e finché
     manca ogni sveglia degrada a inesatta — misurato su un telefono nuovo,
     cron e watchdog partivano con dieci minuti di ritardo e il controllo di
     rete con un'ora. La riga sopra lo diceva già, ma dirlo e basta lasciava
     l'utente senza niente da fare.

     Attaccata alla riga "Sveglie precise" e solo quando è "no": a permesso
     concesso sarebbe un avviso che si impara a ignorare, come per la card
     dell'esenzione. `!== false` e non `!diag.exact_alarms`: da un gateway che
     il campo non lo manda non si deduce che il permesso manchi. */
  _renderExactAlarmRequest(diag) {
    if (diag.exact_alarms !== false) return '';
    // Bridge più vecchio della UI: nessun bottone da offrire, il permesso si
    // concede solo dalla schermata di sistema che sa aprire lui.
    const native = window.JennyNative;
    if (!native || typeof native.requestExactAlarmPermission !== 'function') return '';
    return `
      <div class="settings-notice settings-notice-strong">
        <i class="ti ti-alarm"></i>
        <div>
          <div>${i18n.t('settings.battery.exactAlarmsHint')}</div>
          <div style="margin-top:6px"><i class="ti ti-refresh"></i> ${i18n.t('settings.battery.exactAlarmsRestart')}</div>
        </div>
      </div>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="btn-exact-alarms">
          ${i18n.t('settings.battery.exactAlarmsButton')}
        </button>
      </div>`;
  }

  /* La carta che dice l'unica cosa che solo l'utente può fare. Compare solo
     quando un buco è stato davvero registrato: senza prove sarebbe l'ennesimo
     avviso preventivo che si impara a ignorare. */
  _renderOemGuidance() {
    const native = window.JennyNative;
    let brandRaw = '';
    try {
      if (native && typeof native.deviceManufacturer === 'function') {
        brandRaw = String(native.deviceManufacturer() || '');
      }
    } catch (_) { /* bridge che solleva: si resta sul link generico */ }
    // Slug di dontkillmyapp.com: minuscolo e ridotto ad ASCII sicuro, perché
    // Build.MANUFACTURER è testo libero deciso dall'OEM ("TCL Communication").
    const slug = brandRaw.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    const url = slug
      ? `https://dontkillmyapp.com/${encodeURIComponent(slug)}`
      : 'https://dontkillmyapp.com/';
    const brand = brandRaw || i18n.t('settings.battery.oemUnknownBrand');
    // Il link resta un <a> normale: la WebView devia le navigazioni fuori dal
    // gateway locale su una Chrome Custom Tab (MainActivity#openExternalUrl),
    // mentre aprirlo dentro la SPA la sostituirebbe senza via di ritorno.
    const link = `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(i18n.t('settings.battery.oemLink', { brand }))}</a>`;
    const canOpen = !!(native && typeof native.openBatterySettings === 'function');
    const button = canOpen
      ? `<div class="onboarding-nav">
           <button class="onboarding-btn onboarding-btn-secondary" id="btn-oem-battery">
             ${i18n.t('settings.battery.oemButton')}
           </button>
         </div>`
      : '';
    return `
      <div class="settings-notice settings-notice-strong">
        <i class="ti ti-alert-triangle"></i>
        <div>
          <div>${i18n.t('settings.battery.oemHint')}</div>
          <div style="margin-top:6px">${link}</div>
        </div>
      </div>
      ${button}`;
  }

  _wirePowerDiagnostics(root) {
    // Sveglie precise: al ritorno dalla schermata di sistema il pannello si
    // ricarica da sé (il visibilitychange di `_wireSections`), quindi la riga
    // passa a "Sì" e la richiesta sparisce senza fare niente qui.
    const exactBtn = root.querySelector('#btn-exact-alarms');
    if (exactBtn) {
      exactBtn.addEventListener('click', () => {
        const native = window.JennyNative;
        if (!native || typeof native.requestExactAlarmPermission !== 'function') return;
        let opened = false;
        try {
          opened = !!native.requestExactAlarmPermission();
        } catch (_) { opened = false; }
        // Sotto Android 12 il permesso non esiste e la schermata nemmeno:
        // dirlo, invece di lasciare il tap senza conseguenze visibili.
        if (!opened) showToast(i18n.t('settings.battery.exactAlarmsFailed'), 'error');
      });
    }
    const btn = root.querySelector('#btn-oem-battery');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const native = window.JennyNative;
      if (!native || typeof native.openBatterySettings !== 'function') return;
      let opened = false;
      try {
        opened = !!native.openBatterySettings();
      } catch (_) { opened = false; }
      // Nessuna schermata raggiungibile: dirlo, invece di lasciare il tap
      // senza conseguenze visibili. Restano le istruzioni del link.
      if (!opened) showToast(i18n.t('settings.battery.oemOpenFailed'), 'error');
    });
  }

  /** Durata di un buco, nella lingua dell'utente ("4h 12m"). */
  _formatGapDuration(ms) {
    // Arrotondato al minuto e mai a zero: un buco registrato è sopra soglia,
    // e "0m" lo farebbe sembrare un errore di misura.
    const totalMin = Math.max(1, Math.round((Number(ms) || 0) / 60000));
    const days = Math.floor(totalMin / 1440);
    const hours = Math.floor((totalMin % 1440) / 60);
    const minutes = totalMin % 60;
    if (days) return i18n.t('settings.battery.gapDays', { days, hours });
    if (hours) return i18n.t('settings.battery.gapHours', { hours, minutes });
    return i18n.t('settings.battery.gapMinutes', { minutes });
  }

  /** Quando il buco è cominciato ("ieri alle 23:40").
   *
   *  L'inizio e non la fine: l'ora in cui Jenny è stata uccisa è quella che si
   *  riconosce ("ah, quando metto il telefono in carica la notte"). */
  _formatGapWhen(startMs) {
    const at = new Date(Number(startMs) || 0);
    const time = at.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const dayMs = 86400000;
    if (at.getTime() >= midnight.getTime()) {
      return i18n.t('settings.battery.gapToday', { time });
    }
    if (at.getTime() >= midnight.getTime() - dayMs) {
      return i18n.t('settings.battery.gapYesterday', { time });
    }
    const date = at.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short' });
    return i18n.t('settings.battery.gapOn', { date, time });
  }

  // ── Telegram ───────────────────────────────────────────────────────

  _renderTelegram() {
    // Il contenuto vero lo disegna il TelegramPairingWidget (condiviso con
    // l'onboarding) dentro questo placeholder, in _wireSections.
    return `<div id="settings-telegram-widget"></div>`;
  }

  // ── Models & Providers ─────────────────────────────────────────────

  _formatLabel(fmt) {
    return {
      'openai_compat': i18n.t('provider.openai'),
      'anthropic': i18n.t('provider.anthropic'),
    }[fmt] || fmt || i18n.t('provider.unknown');
  }

  /* Gerarchia a decisione unica: la card "In uso" mostra modello e provider
     correnti; il catalogo unificato (raggruppato per provider) salva
     modello + default_provider insieme, in una chiamata sola. Le chiavi API
     sono pura gestione credenziali (nessuno stato "attivo" da leggere lì);
     i parametri di generazione stanno in una disclosure chiusa. */
  _renderModelSettings(d) {
    const a = d.agent || {};
    const providers = d.providers || [];
    const active = providers.find(p => p.name === d.default_provider);
    const via = active
      ? `${i18n.t('settings.via')} ${escapeHtml(active.name)} · ${escapeHtml(this._formatLabel(active.format))}`
      : i18n.t('settings.noProviderConfigured');

    return `
      <div class="settings-subheading">${i18n.t('settings.inUse')}</div>
      <div class="model-inuse">
        <span class="model-inuse-name">${escapeHtml(a.model || '—')}</span>
        <span class="model-inuse-via">${via}</span>
        <button class="settings-btn-save model-change-btn" id="btn-change-model">${i18n.t('settings.changeModel')}</button>
      </div>
      <div class="model-catalog" id="model-catalog" style="display:none">
        <input type="text" class="settings-input" id="model-search" placeholder="${i18n.t('settings.filterModels')}" autocomplete="off" />
        <div id="model-catalog-groups"></div>
      </div>
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.apiKeys')}</div>
      <div id="provider-list">
        ${this._renderProviderListHtml(providers)}
      </div>
      <button class="settings-btn-add" id="btn-add-provider"><i class="ti ti-plus"></i> ${i18n.t('settings.addProvider')}</button>
      <details class="settings-disclosure">
        <summary>${i18n.t('settings.advancedParams')}</summary>
        ${this._field(i18n.t('settings.maxTokens'), 'number', 'max_tokens', a.max_tokens || '', i18n.t('settings.maxTokensPlaceholder'))}
        ${this._field(i18n.t('settings.temperature'), 'number', 'temperature', a.temperature ?? '', i18n.t('settings.temperaturePlaceholder'))}
        ${this._select(i18n.t('settings.reasoningEffort'), 'reasoning_effort', a.reasoning_effort || '',
          ['', 'low', 'medium', 'high'])}
      </details>`;
  }

  _renderProviderListHtml(providers) {
    if (!providers.length) return `<div class="settings-empty-state">${i18n.t('settings.noProviders')}</div>`;
    return providers.map(p => {
      return `<div class="provider-card" data-provider="${escapeHtml(p.name)}">
        <div class="provider-card-header">
          <span class="provider-name">${escapeHtml(p.name)}</span>
          <span class="provider-badge format-badge">${escapeHtml(this._formatLabel(p.format))}</span>
        </div>
        <div class="provider-card-body">
          <span class="provider-url">${escapeHtml(p.api_base || i18n.t('settings.defaultUrl'))}</span>
          <span class="provider-key">${escapeHtml(p.api_key_hint || i18n.t('settings.noKey'))}</span>
        </div>
        <div class="provider-card-actions">
          <button class="btn-icon provider-edit" data-provider="${escapeHtml(p.name)}" title="${i18n.t('settings.edit')}">
            <i class="ti ti-edit"></i>
          </button>
          <button class="btn-icon btn-danger provider-delete" data-provider="${escapeHtml(p.name)}" title="${i18n.t('settings.delete')}">
            <i class="ti ti-trash"></i>
          </button>
        </div>
      </div>`;
    }).join('');
  }

  // ── Strumenti ──────────────────────────────────────────────────────

  /* Le capacità dell'agente (ricerca web, posizione; i prossimi tool
     finiranno qui). I campi salvano da soli al cambio, come il resto. */
  _renderTools(d) {
    const ws = d.web_search || {};
    const engines = ws.engines || ['bing'];
    return `
      <div class="settings-subheading">${i18n.t('settings.webSearch')}</div>
      ${this._select(i18n.t('settings.searchEngine'), 'ws_engine', ws.search_engine || 'bing', engines)}
      ${this._field(i18n.t('settings.maxResults'), 'number', 'ws_max', ws.max_results ?? 5, i18n.t('settings.maxResultsPlaceholder'))}
      ${this._field(i18n.t('settings.timeoutSec'), 'number', 'ws_timeout', ws.timeout ?? 30, i18n.t('settings.timeoutPlaceholder'))}
      ${this._field(i18n.t('settings.fetchMaxChars'), 'number', 'ws_fetch_max', ws.fetch_max_chars ?? 50000, i18n.t('settings.fetchMaxCharsPlaceholder'))}
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.location.section')}</div>
      ${this._renderLocation(d)}`;
  }

  _renderLocation(d) {
    const loc = d.location || {};
    return `
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.location.enable')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="location-enabled-toggle" ${loc.enabled ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.location.hint')}</p>`;
  }

  // ── Memoria e lavoratori periodici ─────────────────────────────────
  //
  // Dream e il giardiniere sono i due job di sistema. Le loro manopole
  // stavano dentro due slash command (`/dream budget`, `/gardener settings`);
  // prima ancora si editava `config.json` a mano, che è l'incidente da cui
  // quei comandi erano nati. Un comando è un verbo; una
  // preferenza che sopravvive al turno sta qui, con le altre diciotto.
  //
  // Due sezioni e non una né tre, sullo stesso confine che il resto del codice
  // traccia già: la memoria personale da una parte, wiki e progetti dall'altra.

  /* Una riga con un interruttore. Non è in `_field` perché quel helper fa un
     input di testo; qui la label e il toggle stanno sulla stessa riga, come in
     `_renderLocation`. */
  _toggleRow(label, id, checked) {
    return `<div class="settings-field settings-toggle-row">
      <label class="settings-label">${label}</label>
      <label class="toggle-switch">
        <input type="checkbox" id="${id}" ${checked ? 'checked' : ''}>
        <span class="toggle-slider"></span>
      </label>
    </div>`;
  }

  /* Un numero col suo range **dal server**. `min`/`max` non sono scritti qui:
     arrivano dallo schema Python nel payload, così la tastiera numerica e il
     rifiuto del server non possono raccontare due limiti diversi. */
  _numberField(label, key, spec) {
    const min = spec?.min ?? '';
    const max = spec?.max ?? '';
    return `<div class="settings-field">
      <label class="settings-label">${label}</label>
      <input type="number" class="settings-input" data-worker-key="${key}"
        value="${escapeHtml(String(spec?.value ?? ''))}"
        ${min === '' ? '' : `min="${min}"`} ${max === '' ? '' : `max="${max}"`}>
    </div>`;
  }

  _hint(text) {
    return `<p class="settings-hint">${text}</p>`;
  }

  /* La riga della pianificazione, **solo se quel lavoratore è accesso**.
     `describe_schedule()` descrive lo schedule anche a lavoratore spento, ed è
     giusto lato server (è la pianificazione che verrebbe armata). In interfaccia
     però, sotto un interruttore su OFF, «ogni 30min» si legge come se stesse
     ancora girando — visto sul telefono il 31/08/2026. Spento la riga tace: a
     dire cosa resta possibile c'è già l'aiuto sotto. */
  _scheduleText(enabled, schedule) {
    return enabled ? (schedule || '') : '';
  }

  _renderMemory(d) {
    const m = d.memory;
    if (!m) return `<div class="settings-empty">${i18n.t('settings.memory.unavailable')}</div>`;
    return `
      <div class="settings-subheading">${i18n.t('settings.memory.dream')}</div>
      ${this._toggleRow(i18n.t('settings.memory.dreamEnabled'), 'dream-enabled-toggle', m.enabled)}
      ${this._hint(`<span id="dream-schedule">${escapeHtml(this._scheduleText(m.enabled, m.schedule))}</span>`)}
      ${this._numberField(i18n.t('settings.memory.dreamInterval'), 'dream_interval_h', m.interval_h)}
      ${this._numberField(i18n.t('settings.memory.reviewCadence'), 'review_every_runs', m.review_every_runs)}
      ${this._hint(i18n.t('settings.memory.reviewHint', { floor: m.review_floor }))}
      ${this._renderReviewState(m.review_state)}
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.memory.budgets')}</div>
      ${this._hint(i18n.t('settings.memory.budgetsHint'))}
      ${this._renderBudget(m, 'MEMORY.md', 'memory_budget_chars')}
      ${this._renderBudget(m, 'USER.md', 'user_budget_chars')}
      ${this._renderBudget(m, 'SOUL.md', 'soul_budget_chars')}`;
  }

  /* Una riga di tetto: il nome del file, quanto misura adesso, la barra, e il
     campo. La misura sta accanto al campo e non in un pannello a parte perché è
     il numero su cui si decide il tetto — separarli era quel che rendeva
     `/dream budget` due comandi invece di uno. */
  _renderBudget(m, label, key) {
    const spec = m[key] || {};
    const file = (m.files || []).find(f => f.label === label);
    const budget = spec.value || 0;
    const chars = file ? file.chars : null;
    const measure = this._budgetMeasure(m, label, key);
    const pct = budget > 0 && chars != null ? Math.min(100, Math.round((chars / budget) * 100)) : 0;
    const over = budget > 0 && chars != null && chars > budget;
    return `<div class="settings-budget">
      ${this._numberField(label, key, spec)}
      <div class="settings-meter${over ? ' is-over' : ''}" data-meter="${label}">
        <span style="width:${pct}%"></span>
      </div>
      <div class="settings-budget-measure" data-measure="${label}">${measure}</div>
    </div>`;
  }

  /* La frase sotto la barra. In un metodo suo perché la scrive anche
     `_repaintWorkerDerived`, e due copie divergerebbero al primo cambio. */
  _budgetMeasure(m, label, key) {
    const budget = m[key]?.value || 0;
    const file = (m.files || []).find(f => f.label === label);
    if (!file) return i18n.t('settings.memory.unmeasured');
    if (!file.exists) return i18n.t('settings.memory.notYetWritten');
    // Esiste ma non si apre: `chars` è 0 e non è vero. Misurato sul telefono
    // con un `chmod 000` — «0 caratteri su 3.000» per un file da 2.407 byte.
    if (file.readable === false) return i18n.t('settings.memory.unmeasured');
    if (budget > 0) return i18n.t('settings.memory.ofBudget', { chars: file.chars, budget });
    return i18n.t('settings.memory.measuredOnly', { chars: file.chars });
  }

  /* Quante passate dall'ultimo review, e i due contatori di stallo. Gli ultimi
     due compaiono solo se non sono zero: nel caso normale sarebbero due righe
     che dicono "tutto a posto", e nel caso guasto sono l'unica traccia visibile
     che Dream sta girando senza consolidare niente. */
  _renderReviewState(state) {
    if (!state) return '';
    const lines = [i18n.t('settings.memory.sinceReview', { runs: state.runs_since_review })];
    if (state.stuck_runs) lines.push(i18n.t('settings.memory.stuckRuns', { runs: state.stuck_runs }));
    if (state.nothing_new_runs) {
      lines.push(i18n.t('settings.memory.nothingNewRuns', { runs: state.nothing_new_runs }));
    }
    return this._hint(lines.map(escapeHtml).join('<br>'));
  }

  _renderWorkers(d) {
    const w = d.workers;
    if (!w) return `<div class="settings-empty">${i18n.t('settings.workers.unavailable')}</div>`;
    const gardener = w.gardener || {};
    return `
      <div class="settings-subheading">${i18n.t('settings.workers.gardener')}</div>
      ${this._toggleRow(i18n.t('settings.workers.gardenerEnabled'), 'gardener-enabled-toggle', gardener.enabled)}
      ${this._hint(`<span id="gardener-schedule">${escapeHtml(this._scheduleText(gardener.enabled, gardener.schedule))}</span>`)}
      ${this._hint(i18n.t('settings.workers.gardenerOffHint'))}
      ${this._numberField(i18n.t('settings.workers.gardenerInterval'), 'gardener_interval_min', gardener.interval_min)}
      ${this._numberField(i18n.t('settings.workers.gardenerIdle'), 'gardener_idle_min', gardener.idle_min)}
      ${this._numberField(i18n.t('settings.workers.gardenerDistance'), 'gardener_min_hours_between_passes', gardener.min_hours_between_passes)}
      ${this._hint(i18n.t('settings.workers.gardenerNumbersHint'))}
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.workers.compact')}</div>
      ${this._toggleRow(i18n.t('settings.workers.compactEnabled'), 'compact-projects-toggle', w.compact_projects_when_idle)}
      ${this._hint(i18n.t('settings.workers.compactHint'))}
      ${this._hint(i18n.t('settings.workers.compactRestart'))}`;
  }

  /* Il cablaggio delle due sezioni. Un solo salvatore per famiglia, e il
     rollback su errore: un toggle che resta acceso dopo un 400 è la bugia più
     facile da raccontare in una schermata di impostazioni. */
  _wireWorkerSettings() {
    const toggles = [
      ['dream-enabled-toggle', 'memory', 'dream_enabled'],
      ['gardener-enabled-toggle', 'workers', 'gardener_enabled'],
      ['compact-projects-toggle', 'workers', 'compact_projects_when_idle'],
    ];
    for (const [id, family, key] of toggles) {
      const el = this.contentEl.querySelector(`#${id}`);
      if (!el) continue;
      el.addEventListener('change', async () => {
        const on = el.checked;
        try {
          await this._saveWorkerParams(family, { [key]: on ? '1' : '0' });
        } catch (e) {
          el.checked = !on;                       // rollback
          showToast(e.message, 'error');
        }
      });
    }

    // I numeri: `change` (non `input`), come il resto della schermata — su una
    // tastiera mobile `input` salverebbe a ogni cifra, e "4" è un valore valido
    // sulla strada verso "45".
    this.contentEl.querySelectorAll('[data-worker-key]').forEach(el => {
      const key = el.dataset.workerKey;
      const family = key.startsWith('gardener_') || key.startsWith('compact_')
        ? 'workers' : 'memory';
      el.addEventListener('change', () => this._saveWorkerNumber(family, key, el));
    });
  }

  /* Un numero, col caso speciale della cadenza di review davanti.
     Il pavimento è del server (rifiuta senza il flag di conferma); qui c'è il
     dialogo che quel flag lo giustifica, con dentro le misure vere. */
  async _saveWorkerNumber(family, key, el) {
    const previous = this._workerValue(family, key);
    const raw = el.value.trim();
    if (raw === '') { el.value = previous ?? ''; return; }
    const value = Number(raw);
    if (!Number.isInteger(value)) {
      el.value = previous ?? '';
      showToast(i18n.t('settings.workers.notAWholeNumber'), 'error');
      return;
    }
    const params = { [key]: String(value) };
    if (key === 'review_every_runs' && value < (this.data?.memory?.review_floor ?? 12)) {
      const ok = await confirmDialog(i18n.t('settings.memory.reviewConfirm', {
        runs: value,
        floor: this.data?.memory?.review_floor ?? 12,
      }));
      if (!ok) { el.value = previous ?? ''; return; }
      params.confirm_back_to_back = '1';
    }
    try {
      await this._saveWorkerParams(family, params);
    } catch (e) {
      el.value = previous ?? '';                  // rollback
      showToast(e.message, 'error');
    }
  }

  _workerValue(family, key) {
    const data = this.data || {};
    if (family === 'memory') return data.memory?.[key]?.value;
    if (key.startsWith('gardener_')) return data.workers?.gardener?.[key.slice(9)]?.value;
    return undefined;
  }

  /* La chiamata, più il riallineamento di quel che dipende dal valore appena
     scritto: le due pianificazioni e le barre dei tetti. Nessun `render()`
     intero — riscriverebbe la sezione sotto le dita di chi sta ancora
     scrivendo, e richiuderebbe l'accordion. */
  async _saveWorkerParams(family, params) {
    const payload = family === 'memory'
      ? await api.updateMemorySettings(params)
      : await api.updateWorkerSettings(params);
    if (payload && this.data) {
      this.data.memory = payload.memory;
      this.data.workers = payload.workers;
      this._repaintWorkerDerived();
    }
    showToast(i18n.t(payload?.requires_restart
      ? 'settings.workers.savedRestart'
      : 'settings.saved'));
    return payload;
  }

  _repaintWorkerDerived() {
    const set = (id, text) => {
      const el = this.contentEl.querySelector(`#${id}`);
      if (el) el.textContent = text || '';
    };
    const memoryData = this.data?.memory;
    const gardener = this.data?.workers?.gardener;
    set('dream-schedule', this._scheduleText(memoryData?.enabled, memoryData?.schedule));
    set('gardener-schedule', this._scheduleText(gardener?.enabled, gardener?.schedule));
    const memory = this.data?.memory;
    if (!memory) return;
    /* Solo barra e misura. Riscrivere la riga intera porterebbe via l'input —
       e con lui il suo listener di `change`, che questo metodo non rimonta: il
       secondo salvataggio di quel campo non partirebbe mai. */
    for (const [label, key] of [
      ['MEMORY.md', 'memory_budget_chars'],
      ['USER.md', 'user_budget_chars'],
      ['SOUL.md', 'soul_budget_chars'],
    ]) {
      const budget = memory[key]?.value || 0;
      const file = (memory.files || []).find(f => f.label === label);
      const chars = file ? file.chars : null;
      const meter = this.contentEl.querySelector(`[data-meter="${label}"]`);
      if (meter) {
        const pct = budget > 0 && chars != null
          ? Math.min(100, Math.round((chars / budget) * 100)) : 0;
        meter.classList.toggle('is-over', budget > 0 && chars != null && chars > budget);
        const fill = meter.querySelector('span');
        if (fill) fill.style.width = `${pct}%`;
      }
      const measure = this.contentEl.querySelector(`[data-measure="${label}"]`);
      if (measure) measure.textContent = this._budgetMeasure(memory, label, key);
    }
  }

  // ── SSH ────────────────────────────────────────────────────────────

  /* Il blocco SSH arriva da /api/settings/ssh, non dal payload di /api/settings:
     è l'unica sezione con stato che vive fuori dal config (chiave sul disco,
     riga in known_hosts), e tenerla su una chiamata sua evita di far pagare
     quelle letture a ogni apertura delle impostazioni. */
  _renderSsh() {
    return `<div id="ssh-block"><div class="settings-empty-state">${i18n.t('settings.loading')}</div></div>`;
  }

  /* Il nodo si cerca **dopo** l'await, non prima: `render()` ricostruisce tutto
     `contentEl.innerHTML`, quindi un `#ssh-block` catturato prima della fetch è
     con ogni probabilità già staccato dal documento — ci si scriveva dentro
     senza che a schermo cambiasse niente, e la sezione SSH restava sul suo
     "Caricamento…" per sempre. */
  async _loadSsh() {
    const gen = this._gen;
    if (!this.contentEl.querySelector('#ssh-block')) return;
    let ssh;
    try {
      ssh = await api.getSsh();
    } catch {
      if (this._stale(gen)) return;
      const failEl = this.contentEl.querySelector('#ssh-block');
      if (failEl) failEl.innerHTML = `<div class="settings-empty-state">${i18n.t('settings.ssh.loadFailed')}</div>`;
      return;
    }
    if (this._stale(gen)) return;
    const blockEl = this.contentEl.querySelector('#ssh-block');
    if (!blockEl) return;
    this._ssh = ssh;
    blockEl.innerHTML = this._renderSshBlock(this._ssh);
    this._wireSshBlock();
    // Anche questo blocco era un segnaposto quando la posizione è stata rimessa.
    this._restoreScrollTop();
  }

  _renderSshBlock(d) {
    const hosts = d.hosts || [];
    const list = hosts.length
      ? hosts.map(h => this._renderSshHost(h)).join('')
      : `<div class="settings-empty-state">${i18n.t('settings.ssh.empty')}</div>`;
    return `
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.ssh.enable')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="ssh-enabled-toggle" ${d.enabled ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <p class="settings-hint" style="margin:6px 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('settings.ssh.hint')}</p>
      ${this._renderSshCredentialsLost(d)}
      ${list}
      <button class="settings-btn-add" id="btn-ssh-add"><i class="ti ti-plus"></i> ${i18n.t('settings.ssh.addHost')}</button>`;
  }

  /* Credenziali sparite da host che erano già stati verificati: quasi sempre
     un workspace ripristinato da backup. Chiave privata e known_hosts vivono
     fuori dal workspace apposta, quindi nel backup non ci sono e il ripristino
     non può riportarli. Senza questa riga l'utente vede solo dei badge "non
     verificato" e un tool SSH che fallisce, e sembra un guasto. */
  _renderSshCredentialsLost(d) {
    const aliases = d.credentials_lost || [];
    if (!aliases.length) return '';
    const text = i18n.t('settings.ssh.credentialsLost', { aliases: aliases.join(', ') });
    return `<div class="settings-notice settings-notice-strong">
      <i class="ti ti-alert-triangle"></i>
      <div>${escapeHtml(text)}</div>
    </div>`;
  }

  /* Una card per host. I due stati che decidono se l'host è usabile —
     credenziale pronta (chiave generata o password impostata) e impronta
     accettata — stanno in chiaro sulla card: sono i due passi che l'utente deve
     fare, e nasconderli dietro un tap lascerebbe host mezzi configurati che
     falliscono solo al primo comando. */
  _renderSshHost(h) {
    const alias = escapeHtml(h.alias);
    const byPassword = h.auth === 'password';
    const pinned = h.pinned
      ? `<span class="provider-badge format-badge">${i18n.t('settings.ssh.statusPinned')}</span>`
      : `<span class="provider-badge format-badge">${i18n.t('settings.ssh.statusUnpinned')}</span>`;
    /* Lo stato della credenziale segue il modo scelto: su un host a password
       "Nessuna chiave" sarebbe un allarme per qualcosa che non serve, e
       nasconderebbe l'unica cosa che conta lì, cioè se la password c'è. */
    const credentialState = byPassword
      ? (h.has_password
        ? i18n.t('settings.ssh.statusPasswordSet')
        : i18n.t('settings.ssh.statusPasswordMissing'))
      : (h.has_key
        ? i18n.t('settings.ssh.statusKeyReady')
        : i18n.t('settings.ssh.statusKeyMissing'));
    const desc = h.description
      ? `<div style="font-size:12px;color:var(--text-faint)">${escapeHtml(h.description)}</div>`
      : '';
    return `<div class="provider-card" data-ssh-alias="${alias}">
      <div class="provider-card-header">
        <span class="provider-name">${alias}</span>
        ${pinned}
      </div>
      <div class="provider-card-body">
        <span class="provider-url">${escapeHtml(`${h.username}@${h.host}:${h.port}`)}</span>
        <span class="provider-key">${escapeHtml(credentialState)}</span>
      </div>
      ${desc}
      ${this._renderSshPublicKey(h)}
      <div class="provider-card-actions">
        ${byPassword ? '' : `<button class="settings-btn-add ssh-generate" data-ssh-alias="${alias}" data-has-key="${h.has_key ? '1' : ''}">
          ${h.has_key ? i18n.t('settings.ssh.regenerateKey') : i18n.t('settings.ssh.generateKey')}
        </button>`}
        <button class="settings-btn-add ssh-verify" data-ssh-alias="${alias}">${i18n.t('settings.ssh.verify')}</button>
        <button class="btn-icon ssh-edit" data-ssh-alias="${alias}" title="${i18n.t('settings.edit')}">
          <i class="ti ti-edit"></i>
        </button>
        <button class="btn-icon btn-danger ssh-delete" data-ssh-alias="${alias}" title="${i18n.t('settings.delete')}">
          <i class="ti ti-trash"></i>
        </button>
      </div>
    </div>`;
  }

  /* La pubblica resta a schermo finché l'host esiste: il passo "incollala in
     authorized_keys" avviene su un'altra macchina, e mostrarla una volta sola
     costringerebbe a rigenerare la coppia — cioè a invalidare la chiave che si
     stava installando.

     Su un host a password tutto questo blocco sparisce: "copia questa chiave
     pubblica sul server" è un passo che lì non esiste, e lasciarlo a schermo
     farebbe credere che manchi qualcosa da fare. Una chiave eventualmente
     generata prima resta sul disco e ricompare tornando a `auth = key`. */
  _renderSshPublicKey(h) {
    if (h.auth === 'password') return '';
    if (!h.public_key) {
      return `<div class="settings-field-hint">${i18n.t('settings.ssh.noKeyYet')}</div>`;
    }
    return `<div style="margin-top:8px">
      <div class="settings-field-hint">${i18n.t('settings.ssh.publicKeyHint')}</div>
      <code style="display:block;margin:4px 0;padding:6px 8px;font-size:11px;word-break:break-all;
        background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(h.public_key)}</code>
      <button class="settings-btn-add ssh-copy" data-ssh-alias="${escapeHtml(h.alias)}">
        <i class="ti ti-copy"></i> ${i18n.t('settings.ssh.copy')}
      </button>
    </div>`;
  }

  _wireSshBlock() {
    const toggle = this.contentEl.querySelector('#ssh-enabled-toggle');
    if (toggle) {
      toggle.addEventListener('change', () => {
        const enabled = toggle.checked;
        api.updateSsh({ enabled: enabled ? '1' : '0' })
          .then(() => showToast(i18n.t(enabled ? 'settings.ssh.on' : 'settings.ssh.off')))
          .catch(e => {
            toggle.checked = !enabled;  // rollback sull'errore
            showToast(e.message || i18n.t('settings.saveError'), 'error');
          });
      });
    }
    this._wireBtn('btn-ssh-add', () => this._showSshHostDialog());
    const each = (selector, fn) =>
      this.contentEl.querySelectorAll(selector).forEach(btn =>
        btn.addEventListener('click', () => fn(btn.dataset.sshAlias, btn)));
    each('.ssh-generate', (alias, btn) => this._sshGenerateKey(alias, !!btn.dataset.hasKey));
    each('.ssh-verify', alias => this._sshVerify(alias));
    each('.ssh-edit', alias => this._showSshHostDialog(
      (this._ssh?.hosts || []).find(h => h.alias === alias)));
    each('.ssh-delete', alias => this._sshDelete(alias));
    each('.ssh-copy', alias => this._sshCopyPublicKey(alias));
  }

  /* Le route SSH rispondono con un corpo di errore in testo semplice, e quel
     testo è già scritto per l'utente ("host refused by the network policy: …"):
     va mostrato com'è, non sostituito da un codice di stato. */
  async _sshGenerateKey(alias, hasKey) {
    if (hasKey && !await confirmDialog(i18n.t('settings.ssh.regenerateConfirm', { alias }))) return;
    try {
      await api.generateSshKey(alias, { replace: hasKey });
      showToast(i18n.t('settings.ssh.keyGenerated'));
      this._loadSsh();
    } catch (e) { showToast(e.message, 'error'); }
  }

  async _sshCopyPublicKey(alias) {
    const host = (this._ssh?.hosts || []).find(h => h.alias === alias);
    if (!host?.public_key) return;
    // Il fallback su `execCommand` viveva qui, ed è la ragione per cui esiste
    // `copyToClipboard`: era l'unico punto della SPA che lo faceva bene.
    if (await copyToClipboard(host.public_key)) {
      showToast(i18n.t('settings.ssh.copied'));
    } else {
      showToast(i18n.t('settings.ssh.copyFailed'), 'error');
    }
  }

  /* Segno di attesa sul bottone che ha lanciato il probe.

     Il probe apre una connessione di rete e può durare secondi. Prima non
     lasciava traccia: il bottone restava identico e cliccabile, e passati i 3 s
     del toast a schermo non c'era più **niente** che dicesse che stava
     succedendo qualcosa. È da lì che nasce N8 — l'utente conclude che il tap è
     andato perso, preme Indietro, e la modale dell'impronta gli si apre sopra
     un'altra sezione. */
  _setSshVerifyBusy(alias, busy) {
    const btn = this.contentEl?.querySelector(`.ssh-verify[data-ssh-alias="${CSS.escape(alias)}"]`);
    if (!btn) return;
    btn.disabled = busy;
    btn.textContent = i18n.t(busy ? 'settings.ssh.verifying' : 'settings.ssh.verify');
  }

  /* Legge l'impronta e la mette davanti all'utente. Il probe non accetta
     niente: fin qui known_hosts non è stato toccato.

     La guardia di generazione non è cosmetica: "Accetta" scrive davvero in
     known_hosts, e senza di essa la modale poteva comparire sopra la chat o il
     workspace — cioè chiedere di fidarsi di un host in un contesto che non
     spiega più di cosa si stia parlando. */
  async _sshVerify(alias) {
    const gen = this._gen;
    this._setSshVerifyBusy(alias, true);
    showToast(i18n.t('settings.ssh.verifying'));
    let probe;
    try {
      probe = (await api.probeSshHostKey(alias)).probe;
    } catch (e) {
      if (this._stale(gen)) return;
      showToast(e.message, 'error');
      return;
    } finally {
      this._setSshVerifyBusy(alias, false);
    }
    if (this._stale(gen)) return;

    if (probe.already_accepted) {
      showToast(i18n.t('settings.ssh.alreadyAccepted'));
      this._loadSsh();
      return;
    }
    const accepted = probe.changed
      ? await this._confirmChangedHostKey(alias, probe)
      : await this._confirmNewHostKey(alias, probe);
    if (this._stale(gen)) return;
    if (!accepted) {
      // Era l'unica uscita muta della funzione: annullare non dava alcun
      // riscontro, e il badge "Impronta da verificare" restava lì identico —
      // indistinguibile da un annullamento non registrato.
      showToast(i18n.t('settings.ssh.verifyCancelled'), 'info');
      return;
    }

    try {
      await api.acceptSshHostKey(alias, probe.fingerprint, { replace: probe.changed });
      if (this._stale(gen)) return;
      showToast(i18n.t('settings.ssh.accepted'));
      this._loadSsh();
    } catch (e) {
      if (this._stale(gen)) return;
      showToast(e.message, 'error');
    }
  }

  /* Con la password il pinning conta di più, non di meno: una chiave la si
     presenta a un impostore senza dargli niente di riutilizzabile, una password
     invece gliela si consegna intera al primo comando. Per questo l'avviso in
     più sta proprio qui, nel momento in cui l'utente decide di fidarsi. */
  _sshPasswordPinningWarning(alias) {
    const host = (this._ssh?.hosts || []).find(h => h.alias === alias);
    if (host?.auth !== 'password') return '';
    return `<p style="font-size:12px;margin-top:8px">${escapeHtml(i18n.t('settings.ssh.fingerprintPasswordWarning'))}</p>`;
  }

  _confirmNewHostKey(alias, probe) {
    const passwordWarning = this._sshPasswordPinningWarning(alias);
    return detailDialog({
      title: i18n.t('settings.ssh.fingerprintTitle'),
      bodyHtml: `
        <p style="font-size:13px">${escapeHtml(i18n.t('settings.ssh.fingerprintIntro', { alias }))}</p>
        <code style="display:block;margin:8px 0;padding:8px;font-size:12px;word-break:break-all;
          background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(probe.fingerprint)}</code>
        <p style="font-size:12px;color:var(--text-faint)">${escapeHtml(i18n.t('settings.ssh.fingerprintServerHint'))}</p>
        ${passwordWarning}`,
      actions: [
        { id: 'cancel', label: i18n.t('common.cancel') },
        { id: 'accept', label: i18n.t('settings.ssh.accept'), variant: 'primary' },
      ],
    }).then(choice => choice === 'accept');
  }

  /* Host key diversa da quella accettata: potenziale MITM, non un
     aggiornamento. Le due impronte vanno affiancate — senza la vecchia accanto
     alla nuova, "accetta" e "annulla" sono una scelta alla cieca — e la
     sostituzione chiede una seconda conferma, perché è quella che butta via la
     verifica fatta la prima volta. */
  async _confirmChangedHostKey(alias, probe) {
    const choice = await detailDialog({
      title: i18n.t('settings.ssh.changedTitle'),
      bodyHtml: `
        <p style="font-size:13px">${escapeHtml(i18n.t('settings.ssh.changedWarning', { alias }))}</p>
        <div style="font-size:12px;color:var(--text-faint);margin-top:8px">${escapeHtml(i18n.t('settings.ssh.changedOld'))}</div>
        <code style="display:block;padding:6px 8px;font-size:12px;word-break:break-all;
          background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(probe.pinned_fingerprint || '—')}</code>
        <div style="font-size:12px;color:var(--text-faint);margin-top:8px">${escapeHtml(i18n.t('settings.ssh.changedNew'))}</div>
        <code style="display:block;padding:6px 8px;font-size:12px;word-break:break-all;
          background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(probe.fingerprint)}</code>
        ${this._sshPasswordPinningWarning(alias)}`,
      actions: [
        { id: 'cancel', label: i18n.t('common.cancel') },
        { id: 'accept', label: i18n.t('settings.ssh.replace'), variant: 'primary' },
      ],
    });
    if (choice !== 'accept') return false;
    return confirmDialog(i18n.t('settings.ssh.replaceConfirm', { alias }));
  }

  async _sshDelete(alias) {
    if (!await confirmDialog(i18n.t('settings.ssh.deleteConfirm', { alias }))) return;
    try {
      await api.deleteSshHost(alias);
      showToast(i18n.t('settings.ssh.deleted'));
      this._loadSsh();
    } catch (e) { showToast(e.message, 'error'); }
  }

  /* L'alias non è modificabile: è l'identità dell'host, il nome del file di
     chiave e l'unica cosa che il modello passa ai tool SSH. Rinominarlo
     scollegherebbe chiave e impronta dall'host senza dirlo a nessuno.

     La password non viene mai pre-compilata perché non arriva mai: il payload
     di lettura porta solo `has_password`. Il campo vuoto in modifica significa
     quindi "tieni quella salvata", ed è il server a rifiutare il caso in cui
     non ce ne sia una da tenere. */
  _showSshHostDialog(existing) {
    const isEdit = !!existing;
    const auth = existing?.auth === 'password' ? 'password' : 'key';
    /* Il campo password si nasconde con `display` inline, non con l'attributo
       `hidden`: `.settings-field` porta un `display:flex` d'autore, che batte
       il `[hidden] { display:none }` dello user-agent. Con `hidden` il campo
       resterebbe a schermo su un host a chiave. */
    const passwordFieldStyle = auth === 'password' ? '' : 'display:none';
    const dialog = document.createElement('dialog');
    dialog.className = 'oc-dialog';
    dialog.id = 'ssh-host-dialog';
    const value = (field, fallback = '') => escapeHtml(String(existing?.[field] ?? fallback));
    dialog.innerHTML = `
      <div class="oc-dialog-inner">
        <h3 style="margin:0 0 16px;font-size:15px;font-weight:600">
          ${isEdit ? i18n.t('settings.ssh.editHost') : i18n.t('settings.ssh.addHost')}
        </h3>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.alias')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-alias" placeholder="${i18n.t('settings.ssh.aliasPlaceholder')}"
            value="${isEdit ? value('alias') : ''}" ${isEdit ? 'readonly' : ''} autocomplete="off" />
          <span class="settings-field-hint">${i18n.t('settings.ssh.aliasHint')}</span>
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.host')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-host" placeholder="${i18n.t('settings.ssh.hostPlaceholder')}"
            value="${value('host')}" autocomplete="off" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.port')}</label>
          <input type="number" class="settings-input" id="dlg-ssh-port" value="${value('port', '22')}" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.username')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-username" placeholder="${i18n.t('settings.ssh.usernamePlaceholder')}"
            value="${value('username')}" autocomplete="off" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.description')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-description" placeholder="${i18n.t('settings.ssh.descriptionPlaceholder')}"
            value="${value('description')}" autocomplete="off" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.auth')}</label>
          <select class="settings-select" id="dlg-ssh-auth">
            <option value="key" ${auth === 'key' ? 'selected' : ''}>${i18n.t('settings.ssh.authKey')}</option>
            <option value="password" ${auth === 'password' ? 'selected' : ''}>${i18n.t('settings.ssh.authPassword')}</option>
          </select>
          <span class="settings-field-hint">${i18n.t('settings.ssh.authHint')}</span>
        </div>
        <div class="settings-field" id="dlg-ssh-password-field" style="${passwordFieldStyle}">
          <label class="settings-label">${i18n.t('settings.ssh.password')}</label>
          <input type="password" class="settings-input" id="dlg-ssh-password"
            placeholder="${i18n.t('settings.ssh.passwordPlaceholder')}"
            autocomplete="off" data-lpignore="true" value="" />
          <span class="settings-field-hint">${existing?.has_password
            ? i18n.t('settings.ssh.passwordKeepBlank')
            : i18n.t('settings.ssh.passwordHint')}</span>
        </div>
        <div class="oc-dialog-buttons" style="margin-top:16px">
          <button class="oc-btn oc-btn-cancel" id="dlg-ssh-cancel">${i18n.t('common.cancel')}</button>
          <button class="oc-btn oc-btn-confirm" id="dlg-ssh-save">${i18n.t('settings.save')}</button>
        </div>
      </div>`;
    document.body.appendChild(dialog);
    dialog.showModal();

    const close = () => { dialog.close(); dialog.remove(); };
    const authEl = dialog.querySelector('#dlg-ssh-auth');
    const passwordField = dialog.querySelector('#dlg-ssh-password-field');
    const passwordEl = dialog.querySelector('#dlg-ssh-password');
    /* Passando a "chiave" il campo password viene anche svuotato, non solo
       nascosto: un campo nascosto ma pieno resterebbe nel DOM, e il valore
       digitato per ripensarci un attimo dopo non ha motivo di sopravvivere al
       cambio di modo. */
    authEl.addEventListener('change', () => {
      const byPassword = authEl.value === 'password';
      // Stringa vuota, non 'flex': così torna a valere la regola della classe.
      passwordField.style.display = byPassword ? '' : 'none';
      if (!byPassword) passwordEl.value = '';
    });

    dialog.querySelector('#dlg-ssh-cancel').addEventListener('click', close);
    dialog.querySelector('#dlg-ssh-save').addEventListener('click', async () => {
      const params = {
        alias: dialog.querySelector('#dlg-ssh-alias').value.trim(),
        host: dialog.querySelector('#dlg-ssh-host').value.trim(),
        port: dialog.querySelector('#dlg-ssh-port').value.trim() || '22',
        username: dialog.querySelector('#dlg-ssh-username').value.trim(),
        description: dialog.querySelector('#dlg-ssh-description').value.trim(),
        auth: authEl.value,
      };
      if (!params.alias || !params.host || !params.username) {
        showToast(i18n.t('settings.ssh.fieldsRequired'), 'error');
        return;
      }
      if (params.auth === 'password') {
        // Niente `.trim()`: gli spazi in una password sono contenuto. Il campo
        // vuoto vale "tieni quella salvata", e senza niente di salvato il
        // server rifiuta comunque — questo controllo evita solo il giro.
        const typed = passwordEl.value;
        if (!typed && !existing?.has_password) {
          showToast(i18n.t('settings.ssh.passwordRequired'), 'error');
          return;
        }
        if (typed) params.password = typed;
      }
      try {
        await api.saveSshHost(params);
        close();
        showToast(i18n.t('settings.saved'));
        this._loadSsh();
      } catch (e) { showToast(e.message, 'error'); }
    });
    dialog.addEventListener('close', () => dialog.remove());
  }

  // ── Theme ──────────────────────────────────────────────────────────

  _renderTheme() {
    const current = AppState.theme || localStorage.getItem('tc-theme') || DEFAULT_THEME;
    // Each card is dressed in its own theme (self-contained `.tk-<id>` styles)
    // and *is* the preview — a mini-conversation + input, not just a swatch.
    const cards = THEMES.map(t => {
      const sel = t.id === current;
      return `<button class="tcard tk-${t.id}${sel ? ' sel' : ''}" data-theme-choice="${t.id}" title="${escapeHtml(t.label)}">
        ${sel ? '<span class="tsel">✓</span>' : ''}
        <div class="thead"><span class="tnm">${escapeHtml(t.label)}</span><span class="tfl">✿</span></div>
        <div class="tconv">
          <div class="tblo">${escapeHtml(i18n.t('themes.' + t.id + '.desc'))}</div>
          <div class="trep">${escapeHtml(i18n.t('themes.' + t.id + '.reply'))}</div>
          <div class="tmeta">0.8s</div>
        </div>
        <div class="tfoot"><span class="tin">${i18n.t('themes.placeholder')}</span><span class="tsend">↑</span></div>
      </button>`;
    }).join('');
    return `<div class="theme-strip-eyebrow">${i18n.t('settings.themeLabel')}</div>
      <div class="tstrip">${cards}</div>
      ${this._renderMascot()}`;
  }

  // ── Mascotte ───────────────────────────────────────────────────────

  /* Blocco della sezione "Personalizzazione", sotto la passerella dei temi:
     mini-label, toggle di visibilità e taglia.
     Le opzioni restano SEMPRE a schermo: nasconderle a mascotte spenta faceva
     sembrare che l'unica scelta fosse tenerla o buttarla via — chi la spegneva
     subito non scopriva mai che era personalizzabile. Da spenta si vedono
     inerti (attributo `disabled`), come promessa di cosa si ottiene
     riaccendendola.
     Il lato NON si sceglie qui: lo decide il lancio (v. mobile-jenny.js), e
     un'impostazione che cambia da sola al primo lancio sarebbe una bugia. */
  _renderMascot() {
    const visible = mascotVisible();
    const size = mascotSize();
    const off = visible ? '' : ' disabled';
    const sizeLabels = {
      sm: i18n.t('settings.mascotSizeSmall'),
      md: i18n.t('settings.mascotSizeMedium'),
      lg: i18n.t('settings.mascotSizeLarge'),
    };
    const sizeButtons = Object.keys(MASCOT_SIZES).map(id =>
      `<button class="settings-seg-btn${id === size ? ' active' : ''}" data-mascot-size="${id}"${off}>
        ${escapeHtml(sizeLabels[id])}
        ${id === size ? '<i class="ti ti-check"></i>' : ''}
      </button>`
    ).join('');
    return `
      <div class="theme-strip-eyebrow">${i18n.t('settings.mascotSection')}</div>
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.mascotVisible')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="mascot-visible-toggle" ${visible ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <div class="settings-field"${visible ? '' : ' data-settings-off'}>
        <label class="settings-label">${i18n.t('settings.mascotSize')}</label>
        <div class="settings-seg">${sizeButtons}</div>
      </div>`;
  }

  // ── Tasto Home ─────────────────────────────────────────────────────

  /* Da launcher, Home significa "torna alla schermata iniziale": qui si sceglie
     quale sia. Select e non segmented: quattro voci non stanno in riga su un
     telefono. Le etichette delle viste sono quelle del dock (nav.*), così
     restano allineate a quello che si vede nella barra. */
  _renderHomeView() {
    const current = homeView();
    const labels = {
      chat: i18n.t('nav.chat'),
      apps: i18n.t('nav.apps'),
      workspace: i18n.t('nav.workspace'),
      last: i18n.t('settings.homeLast'),
    };
    const options = HOME_VIEW_CHOICES.map(id =>
      `<option value="${id}"${id === current ? ' selected' : ''}>${escapeHtml(labels[id])}</option>`
    ).join('');
    return `
      <div class="theme-strip-eyebrow">${i18n.t('settings.homeSection')}</div>
      <div class="settings-field">
        <select class="settings-select" id="home-view-select">${options}</select>
        <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.homeHint')}</p>
      </div>`;
  }

  // ── Language ───────────────────────────────────────────────────────

  _renderLanguage() {
    const current = i18n.locale;
    let html = '<div class="settings-language-list">';
    for (const locale of i18n.availableLocales) {
      const isActive = locale === current;
      html += `<button class="settings-seg-btn${isActive ? ' active' : ''}" data-locale="${locale}">
        ${i18n.getLocaleName(locale)}
        ${isActive ? '<i class="ti ti-check"></i>' : ''}
      </button>`;
    }
    html += '</div>';
    return html;
  }

  // ── Backup e ripristino ──────────────────────────────────────────────

  _renderBackup() {
    return `
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.exportDesc')}</p>
      <button class="settings-btn-save settings-btn-block" id="btn-backup-export"><i class="ti ti-file-export"></i> ${i18n.t('backup.exportButton')}</button>
      <div class="settings-divider"></div>
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.importDesc')}</p>
      <button class="settings-btn-add" id="btn-backup-import"><i class="ti ti-file-import"></i> ${i18n.t('backup.importButton')}</button>
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('backup.snapshotHistory')}</div>
      <div class="settings-field">
        <label class="settings-label">${i18n.t('backup.retentionLabel')}</label>
        <select class="settings-select" id="snapshot-retention">
          <option value="7">${i18n.t('backup.retentionWeek')}</option>
          <option value="30">${i18n.t('backup.retentionMonth')}</option>
          <option value="365">${i18n.t('backup.retentionYear')}</option>
          <option value="0">${i18n.t('backup.retentionForever')}</option>
        </select>
      </div>
      <button class="settings-btn-add" id="btn-snapshot-create"><i class="ti ti-camera"></i> ${i18n.t('backup.snapshotCreate')}</button>
      <div id="snapshot-list" style="margin-top:8px">
        <div class="settings-empty-state">${i18n.t('settings.loading')}</div>
      </div>`;
  }

  // ── Sistema ────────────────────────────────────────────────────────

  /* Diagnostica e opzioni da power user: versione, modalità avanzata,
     statistiche di utilizzo token. */
  _renderSystem(d) {
    const v = d.version || {};
    return `
      <div class="settings-field-row">
        <span class="settings-field-label">${i18n.t('settings.version')}</span>
        <span class="settings-field-value">${escapeHtml(v.current || '—')}${this._renderUpdateBadge(v)}</span>
      </div>
      ${this._renderUpdateCard(v)}
      ${this._renderUpdateCheck(v)}
      <div class="settings-divider"></div>
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.advancedMode')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="advanced-mode-toggle" ${advancedMode() ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <p class="settings-hint" style="margin-top:6px;font-size:12px;color:var(--text-faint)">${i18n.t('settings.advancedModeHint')}</p>
      <div class="settings-divider"></div>
      ${this._renderRerunOnboarding()}
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.tokenUsage')}</div>
      ${this._renderUsage(d)}`;
  }

  /* ── Aggiornamento dell'app ──────────────────────────────────────────

     Il backend calcola già tutto (`version.update_available` viene dallo stato
     dell'updater, non da un giro di rete fatto qui): questa parte si limita a
     dirlo e a offrire il bottone. Senza, una release nuova esisteva solo nei
     log del job periodico. */

  /* Pastiglia accanto al numero di versione. Un aggiornamento critico non è
     "una versione nuova con più cose": è una fix che conviene installare
     subito, e deve leggersi diversamente già da qui. */
  _renderUpdateBadge(v) {
    if (!v.update_available) return '';
    const critical = !!v.critical;
    const label = i18n.t(critical ? 'settings.update.badgeCritical' : 'settings.update.badge');
    const icon = critical ? 'shield-exclamation' : 'arrow-up';
    return ` <span class="update-badge${critical ? ' critical' : ''}"><i class="ti ti-${icon}"></i>${escapeHtml(label)}</span>`;
  }

  _renderUpdateCard(v) {
    if (!v.update_available) return '';
    const critical = !!v.critical;
    const headline = i18n.t(
      critical ? 'settings.update.availableCritical' : 'settings.update.available',
      { version: v.latest || '' },
    );
    const summary = v.summary
      ? `<div style="margin-top:4px;color:var(--text-muted)">${escapeHtml(v.summary)}</div>`
      : '';
    // Link normale: la WebView devia le navigazioni fuori dal gateway locale su
    // una Custom Tab (v. _renderOemGuidance), aprirlo dentro la SPA la
    // sostituirebbe senza ritorno.
    const notes = v.notes_url
      ? `<div style="margin-top:6px"><a href="${escapeHtml(v.notes_url)}" target="_blank" rel="noopener">${escapeHtml(i18n.t('settings.update.notes'))}</a></div>`
      : '';
    const busy = !!this._update?.busy;
    return `
      <div class="settings-notice${critical ? ' settings-notice-strong' : ''}">
        <i class="ti ti-${critical ? 'shield-exclamation' : 'download'}"></i>
        <div style="flex:1;min-width:0">
          <div>${escapeHtml(headline)}</div>
          ${summary}
          ${notes}
        </div>
      </div>
      <div id="update-progress">${this._updateProgressHtml()}</div>
      <div class="onboarding-nav">
        <button class="onboarding-btn ${critical ? 'onboarding-btn-primary' : 'onboarding-btn-secondary'}" id="btn-update-install" ${busy ? 'disabled' : ''}>
          ${escapeHtml(i18n.t('settings.update.install'))}
        </button>
      </div>`;
  }

  /* ── Il controllo degli aggiornamenti, visto dall'utente ─────────────

     Il riquadro sopra racconta un aggiornamento *trovato*. Questo racconta il
     meccanismo che dovrebbe trovarlo, e c'è sempre — anche, soprattutto, quando
     non c'è niente da installare: senza, un manifest irraggiungibile da mesi
     mostra esattamente la stessa schermata di "sei aggiornato", e su un
     telefono headless nessuno va a leggere i log per accorgersene. */

  _renderUpdateCheck(v) {
    const busy = !!this._checking;
    const label = i18n.t(busy ? 'settings.update.checking' : 'settings.update.checkNow');
    return `
      <div class="update-check" id="update-check">
        ${this._updateCheckLinesHtml(v)}
        <button class="settings-btn-add" id="btn-update-check" ${busy ? 'disabled' : ''}>
          <i class="ti ti-refresh"></i> ${escapeHtml(label)}
        </button>
      </div>`;
  }

  /* Quando il controllo è riuscito l'ultima volta, e se il caso l'avviso che
     non riesce più. Il confronto è fra i due timestamp dell'updater, non con
     l'ora corrente: `last_check` è scritto a ogni tentativo, `last_success`
     solo quando il manifest è stato letto davvero, e un telefono spento per una
     settimana li ha vecchi entrambi — nessun meccanismo rotto da segnalare. */
  _updateCheckLinesHtml(v) {
    const success = Number(v.last_success) || 0;
    const check = Number(v.last_check) || 0;
    if (!success) {
      /* Prima del primo tentativo in assoluto non c'è nessun guasto: c'è
         un'installazione appena fatta, e darle l'aria dell'allarme sarebbe la
         prima cosa falsa che Jenny dice. */
      if (!check) {
        return `<div class="update-check-line">${escapeHtml(i18n.t('settings.update.neverChecked'))}</div>`;
      }
      /* Tentativi sì, esiti positivi no. Vale anche per uno stato scritto prima
         che `last_success` esistesse, ed è per questo che la stringa manda a
         premere "Controlla ora" invece di sentenziare: un tap distingue i due
         casi meglio di qualunque euristica. */
      return `<div class="update-check-line warn">${escapeHtml(i18n.t('settings.update.staleNever'))}</div>`;
    }
    const when = this._formatUpdateWhen(success);
    const rows = [
      `<div class="update-check-line">${escapeHtml(i18n.t('settings.update.lastSuccess', { when }))}</div>`,
    ];
    if (check - success > UPDATE_STALE_MS) {
      rows.push(`<div class="update-check-line warn">${escapeHtml(i18n.t('settings.update.stale', { when }))}</div>`);
    }
    return rows.join('');
  }

  /* "oggi alle 14:22", "3 giorni fa", "27 giu 2025". Relativo finché resta
     leggibile, datato dopo: a novanta giorni "90 giorni fa" non dice più
     niente, una data sì. "Ieri" ha un ramo suo perché "1 giorni fa" si legge
     male in entrambe le lingue. Le date passano da `toLocaleDateString`, che le
     localizza da sé: non sono stringhe scritte a mano. */
  _formatUpdateWhen(ms) {
    const at = new Date(Number(ms) || 0);
    const dayMs = 86400000;
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const time = at.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
    if (at.getTime() >= midnight.getTime()) return i18n.t('settings.update.whenToday', { time });
    if (at.getTime() >= midnight.getTime() - dayMs) return i18n.t('settings.update.whenYesterday', { time });
    // Arrotondato per eccesso: il ramo "ieri" qui sopra garantisce già >= 2, e
    // troncando, un controllo di tre giorni fa alle 23:00 diventerebbe "2".
    const days = Math.ceil((midnight.getTime() - at.getTime()) / dayMs);
    if (days <= 30) return i18n.t('settings.update.whenDays', { days });
    return at.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  /* Ridipinge solo questo blocco: come `_paintUpdate`, un render() completo
     ricostruirebbe tutta la pagina per accendere l'etichetta di un bottone. Il
     rewire è obbligatorio, `outerHTML` butta via il nodo con il suo listener. */
  _paintUpdateCheck() {
    const host = this.contentEl?.querySelector('#update-check');
    if (!host) return;
    host.outerHTML = this._renderUpdateCheck(this.data?.version || {});
    this._wireBtn('btn-update-check', () => this._runUpdateCheck());
  }

  /* Controllo forzato. Esiste perché senza di esso non c'è alcun modo di
     chiedere "sei ancora viva?": si aspetta il cron, ventiquattr'ore, e se
     fallisce non lo dice nessuno. È l'affordance che rende diagnosticabili le
     righe qui sopra, e `check_for_update` ignora `updates.enabled` proprio per
     poter essere chiamata così. Il doppio tap è fermato due volte: qui dal
     flag, e lato server da un lock, perché la rotta fa rete. */
  async _runUpdateCheck() {
    if (this._checking) return;
    const gen = this._gen;
    this._checking = true;
    this._paintUpdateCheck();

    let payload = null;
    try {
      /* GET e non POST: v. `_startUpdate` per il motivo (il server HTTP di
         `websockets` rifiuta ogni altro metodo prima del router). */
      const res = await api._fetch('/api/updates/check');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      payload = await res.json();
    } catch (_) {
      payload = null;
    }
    /* Il flag si azzera anche se nel frattempo si è usciti dalla sezione: è
       roba del controller, non del DOM, e lasciarlo acceso bloccherebbe il
       bottone al rientro senza che nulla lo rimetta a posto. */
    this._checking = false;
    if (this._stale(gen)) return;

    if (!payload) {
      this._paintUpdateCheck();
      showToast(i18n.t('settings.update.checkFailed'), 'error');
      return;
    }
    if (payload.status === 'busy') {
      this._paintUpdateCheck();
      showToast(i18n.t('settings.update.checkBusy'));
      return;
    }
    /* Il payload versione fresco entra in `this.data` e la pagina si ridisegna:
       senza, una versione appena trovata comparirebbe solo alla prossima
       apertura delle impostazioni — cioè proprio dopo il gesto con cui
       l'utente l'ha chiesta. */
    if (this.data && payload.version) this.data.version = payload.version;
    this.render();
    if (payload.status !== 'ok') {
      showToast(i18n.t('settings.update.checkFailed'), 'error');
      return;
    }
    const v = payload.version || {};
    showToast(v.update_available
      ? i18n.t('settings.update.available', { version: v.latest || '' })
      : i18n.t('settings.update.checkUpToDate'));
  }

  /* Chiave della riga di fase. `idle` non ne ha una: prima di premere il
     bottone non c'è niente da raccontare, e dopo un errore la fase torna a
     essere l'ultima cosa detta, non "inattivo". */
  _updatePhaseKey(phase) {
    return {
      downloading: 'settings.update.phaseDownloading',
      installing: 'settings.update.phaseInstalling',
      prompt: 'settings.update.phasePrompt',
      error: 'settings.update.phaseError',
      done: 'settings.update.phaseDone',
    }[phase] || '';
  }

  _updateProgressHtml() {
    const u = this._update;
    if (!u) return '';
    const rows = [];
    // La nota sopravvive ai cambi di fase: dice che cosa ci si deve aspettare
    // (riavvio in arrivo, conferma di sistema da dare), la fase dice solo a che
    // punto è.
    if (u.noteKey) rows.push(`<div class="update-note">${escapeHtml(i18n.t(u.noteKey))}</div>`);
    const phaseKey = this._updatePhaseKey(u.phase);
    if (phaseKey) rows.push(`<div class="update-phase">${escapeHtml(i18n.t(phaseKey))}</div>`);
    if (u.detail) rows.push(`<div class="update-detail">${escapeHtml(u.detail)}</div>`);
    if ((u.phase === 'downloading' || u.phase === 'installing') && u.progress > 0) {
      rows.push(`
        <div class="update-progress-track">
          <span class="update-progress-bar" style="width:${Math.min(Math.max(u.progress, 0), 100)}%"></span>
        </div>`);
    }
    return rows.length ? `<div class="update-status">${rows.join('')}</div>` : '';
  }

  /* Ridipinge solo il riquadro di stato: un render() completo qui
     ricostruirebbe tutta la pagina a ogni giro di polling. */
  _paintUpdate() {
    const host = this.contentEl?.querySelector('#update-progress');
    if (host) host.innerHTML = this._updateProgressHtml();
    const btn = this.contentEl?.querySelector('#btn-update-install');
    if (btn) btn.disabled = !!this._update?.busy;
  }

  async _startUpdate() {
    const gen = this._gen;
    this._update = { busy: true, noteKey: 'settings.update.starting', phase: 'idle', progress: 0, detail: '' };
    this._updatePolls = 0;
    this._paintUpdate();

    /* Il polling parte *prima* di aspettare la risposta, non dopo: la richiesta
       di installazione risponde solo a download+commit conclusi, e nel percorso
       "silent" non risponde affatto — il sistema uccide il processo mentre la
       risposta è ancora in volo. Lo stato vero arriva da qui. */
    this._scheduleUpdatePoll(gen);

    let result;
    try {
      /* GET e non POST, come ogni altra scrittura di questa WebUI: il server
         HTTP è quello di `websockets`, e `Request.parse` rifiuta qualunque
         metodo diverso da GET *prima* che la richiesta arrivi al router — un
         POST qui non fallirebbe con un 405, fallirebbe con la connessione
         chiusa. `api._fetch` e non un metodo di api-client: aggiungerlo lì è
         fuori dal perimetro di questa modifica. */
      const res = await api._fetch('/api/updates/install');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      result = await res.json();
    } catch (err) {
      if (this._stale(gen) || !this._update) return;
      /* Connessione caduta a installazione già avviata: è il riavvio, non un
         guasto — chiamarlo errore sarebbe una bugia proprio nel caso normale.
         Solo se il polling non ha ancora visto muoversi niente si tratta di un
         vero fallimento di partenza. */
      if (this._update.phase !== 'idle') {
        this._update.noteKey = 'settings.update.restarting';
        this._paintUpdate();
        return;
      }
      this._failUpdate(err.message);
      return;
    }
    if (this._stale(gen) || !this._update) return;

    if (!result.ok || result.state === 'error') {
      // Un rifiuto (niente da installare, versione già applicata) non sporca la
      // fase lato server: il motivo sta tutto nel `detail` della risposta, e il
      // polling lo cancellerebbe rileggendo una fase ancora "idle".
      this._failUpdate(result.detail || '');
      return;
    }

    if (result.state === 'prompt') {
      this._settleUpdateAtPrompt(result.detail || '');
      return;
    }

    /* "silent" non vuol dire "finito": la sessione è committata, il sistema
       ucciderà questo processo e Jenny ripartirà da sola. La WebSocket cadrà —
       ws-manager riconnette da sé con backoff — e dirlo prima è l'unico modo
       perché quella caduta non sembri un guasto. */
    this._update.noteKey = 'settings.update.restarting';
    this._paintUpdate();
  }

  /* `prompt` è terminale: la palla è passata ad Android e non torna indietro da
     sola. La fase resta su questo valore finché l'utente non risponde, quindi
     continuare a interrogarla non porta niente di nuovo — porta solo dieci
     minuti di polling con il bottone disabilitato, e un'uscita-e-rientro dalle
     impostazioni ne fa ripartire altri dieci.

     Non è un caso di nicchia da cui difendersi per scrupolo: su Android 14+,
     con l'update ownership, il ramo con conferma è *la* strada normale
     (v. `UpdateBridge.kt`). Qui si ferma il polling, si sblocca il bottone e si
     dice che cosa manca — la conferma può essere una schermata aperta davanti
     agli occhi o, se l'app era in background, una notifica ancora in attesa. Se
     l'utente la perde, "Installa ora" è di nuovo premibile. */
  _settleUpdateAtPrompt(detail) {
    this._stopUpdatePoll();
    this._update = {
      ...(this._update || {}),
      busy: false,
      noteKey: 'settings.update.promptNote',
      phase: 'prompt',
      progress: 0,
      detail: detail || this._update?.detail || '',
    };
    this._paintUpdate();
  }

  /* Esito negativo definitivo: ferma il polling *prima* di scrivere lo stato,
     altrimenti il giro successivo sovrascrive il motivo con la fase del
     server, che dopo un rifiuto è ancora "inattivo". */
  _failUpdate(detail) {
    this._stopUpdatePoll();
    this._update = { busy: false, noteKey: null, phase: 'error', progress: 0, detail };
    this._paintUpdate();
    showToast(i18n.t('settings.update.startFailed'), 'error');
  }

  _stopUpdatePoll() {
    clearTimeout(this._updateTimer);
    this._updateTimer = null;
  }

  _scheduleUpdatePoll(gen) {
    clearTimeout(this._updateTimer);
    this._updateTimer = setTimeout(() => this._pollUpdateStatus(gen), 1500);
  }

  async _pollUpdateStatus(gen) {
    if (this._stale(gen)) return;
    let status;
    try {
      const res = await api._fetch('/api/updates/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      status = await res.json();
    } catch (_) {
      /* Un polling che fallisce non è un'installazione fallita: durante
         l'installazione il gateway *sparisce* (è il caso normale, non
         l'eccezione). Si riprova senza cambiare quello che l'utente legge. */
      if (!this._stale(gen) && this._updatePolls++ < 400) this._scheduleUpdatePoll(gen);
      return;
    }
    if (this._stale(gen) || !this._update) return;

    this._update = {
      ...this._update,
      phase: status.phase || 'idle',
      progress: Number(status.progress) || 0,
      detail: status.detail || '',
    };
    /* Terminale quanto `error` e `done`, ma per l'altro motivo: non è finita,
       è ferma e aspetta una persona. Vedi `_settleUpdateAtPrompt`. */
    if (status.phase === 'prompt') {
      this._settleUpdateAtPrompt(status.detail || '');
      return;
    }
    if (status.phase === 'error' || status.phase === 'done') {
      this._stopUpdatePoll();
      this._update.busy = false;
      /* "done" lato server vuol dire "sessione committata", non "installato":
         subito dopo Android sostituisce l'app e il processo muore. La nota sul
         riavvio è quindi più vera adesso che mai — e la si rimette anche se la
         risposta della richiesta non è mai arrivata. */
      if (status.phase === 'done') this._update.noteKey = 'settings.update.restarting';
      this._paintUpdate();
      return;
    }
    this._paintUpdate();
    // Tetto di sicurezza (~10 minuti): senza, una fase che non si muove più
    // lascerebbe un timer vivo per tutta la vita della pagina.
    if (this._updatePolls++ < 400) this._scheduleUpdatePoll(gen);
  }

  /* Strada permanente verso il wizard di configurazione. Finora l'onboarding
     era raggiungibile solo al primo avvio, e solo perché il gateway rispondeva
     `first_run: true`: chi voleva rifare la configurazione da capo — o chi al
     boot ha incontrato un errore che ha lasciato indeterminato lo stato del
     primo avvio — non aveva alcun modo di riaprirlo. La voce sta qui e non
     accanto ai provider perché non è "cambia il modello": rifà tutto il giro. */
  _renderRerunOnboarding() {
    return `
      <div class="settings-subheading">${i18n.t('settings.rerunOnboarding')}</div>
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('settings.rerunOnboardingHint')}</p>
      <button class="settings-btn-add" id="btn-rerun-onboarding"><i class="ti ti-rocket"></i> ${i18n.t('settings.rerunOnboardingAction')}</button>`;
  }

  async _rerunOnboarding() {
    if (!await confirmDialog(i18n.t('settings.rerunOnboardingConfirm'))) return;
    const app = window.mobileApp;
    if (!app) return;
    app.openOnboarding();
  }

  _wireBackup() {
    this._wireBtn('btn-backup-export', () => runExportFlow());
    this._wireBtn('btn-backup-import', async () => {
      if (await confirmDialog(i18n.t('backup.importConfirm'))) runImportFlow();
    });
    this._wireBtn('btn-snapshot-create', async () => {
      try {
        const res = await api.createSnapshot();
        showToast(res.snapshot
          ? i18n.t('backup.snapshotCreated')
          : i18n.t('backup.snapshotNoChanges'));
        this._loadSnapshotList();
      } catch (e) { showToast(e.message, 'error'); }
    });
    const retentionEl = this.contentEl.querySelector('#snapshot-retention');
    if (retentionEl) {
      retentionEl.addEventListener('change', async () => {
        try {
          await api.updateSnapshotRetention(parseInt(retentionEl.value, 10));
          showToast(i18n.t('settings.saved'));
          this._loadSnapshotList();
        } catch (e) { showToast(e.message, 'error'); }
      });
    }
    this._loadSnapshotList();
  }

  /** Allinea la select al valore corrente; un valore fuori preset (config
   *  editata a mano) diventa un'opzione dedicata invece di mostrarne una falsa. */
  _syncRetentionSelect(days) {
    const el = this.contentEl.querySelector('#snapshot-retention');
    if (el == null || days == null) return;
    const value = String(days);
    if (![...el.options].some(o => o.value === value)) {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = i18n.t('backup.retentionDays', { days: value });
      el.appendChild(opt);
    }
    el.value = value;
  }

  /* Stesso motivo di `_loadSsh`: il nodo si cerca dopo l'await. */
  async _loadSnapshotList() {
    const gen = this._gen;
    if (!this.contentEl.querySelector('#snapshot-list')) return;
    let snapshots = [];
    try {
      const history = await api.getSnapshotHistory();
      if (this._stale(gen)) return;
      snapshots = history.snapshots || [];
      this._syncRetentionSelect(history.retention_max_age_days);
    } catch {
      if (this._stale(gen)) return;
      const failEl = this.contentEl.querySelector('#snapshot-list');
      if (failEl) failEl.innerHTML = `<div class="settings-empty-state">${i18n.t('backup.snapshotHistoryUnavailable')}</div>`;
      return;
    }
    const listEl = this.contentEl.querySelector('#snapshot-list');
    if (!listEl) return;
    if (!snapshots.length) {
      listEl.innerHTML = `<div class="settings-empty-state">${i18n.t('backup.snapshotHistoryEmpty')}</div>`;
      this._restoreScrollTop();
      return;
    }
    listEl.innerHTML = snapshots.map(s => {
      const date = new Date(s.created_at_ms).toLocaleString(i18n.locale);
      const triggerKey = `backup.trigger.${s.trigger}`;
      let trigger = i18n.t(triggerKey);
      if (trigger === triggerKey) trigger = s.trigger;
      const label = s.label ? ` · ${escapeHtml(s.label)}` : '';
      return `<button class="snapshot-row" data-snapshot="${escapeHtml(s.id)}" data-date="${escapeHtml(date)}"
        style="display:flex;flex-direction:column;align-items:flex-start;width:100%;gap:2px;padding:8px 10px;margin-bottom:6px;border:1px solid var(--border,rgba(128,128,128,.25));border-radius:8px;background:transparent;color:inherit;text-align:left">
        <span style="font-size:13px">${escapeHtml(date)}${label}</span>
        <span style="font-size:11px;color:var(--text-faint)">${escapeHtml(trigger)} · ${s.file_count} ${i18n.t('backup.files')} · ${this._fmtBytes(s.total_bytes)}</span>
      </button>`;
    }).join('');
    listEl.querySelectorAll('.snapshot-row').forEach(btn => {
      btn.addEventListener('click', async () => {
        const ok = await confirmDialog(
          i18n.t('backup.snapshotRestoreConfirm', { date: btn.dataset.date }));
        if (ok) runSnapshotRestore(btn.dataset.snapshot);
      });
    });
    // Anche la lista degli snapshot era un segnaposto: v. `_restoreScrollTop()`.
    this._restoreScrollTop();
  }

  _fmtBytes(n) {
    if (n == null) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + ' KB';
    return n + ' B';
  }

  // ── Token Usage ──────────────────────────────────────────────────────

  _renderUsage(d) {
    const u = d.usage || {};
    if (u.total_tokens == null) return `<div class="settings-empty">${i18n.t('settings.usage.noData')}</div>`;
    return `
      <div class="settings-usage-grid">
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.total_tokens)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.totalTokens')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.total_tokens_30d)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.last30d')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.total_tokens_365d)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.last365d')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.peak_day_tokens)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.peakDay')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${u.current_streak_days || 0}d</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.streak')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${u.active_days_30d || 0}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.activeDays')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${u.requests_30d || 0}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.requests')}</div>
        </div>
      </div>`;
  }

  // ── Form Helpers ───────────────────────────────────────────────────

  _field(label, type, key, value, placeholder = '') {
    return `<div class="settings-field">
      <label class="settings-label">${label}</label>
      <input type="${type}" class="settings-input" data-key="${key}" value="${escapeHtml(String(value))}"
        placeholder="${escapeHtml(placeholder)}">
    </div>`;
  }

  _select(label, key, value, options) {
    const opts = options.map(o =>
      `<option value="${escapeHtml(o)}" ${o === value ? 'selected' : ''}>${o || '—'}</option>`
    ).join('');
    return `<div class="settings-field">
      <label class="settings-label">${label}</label>
      <select class="settings-select" data-key="${key}">${opts}</select>
    </div>`;
  }

  // ── Wire Events ────────────────────────────────────────────────────

  _wireSections() {
    // Accordion toggle (lo stato aperto va in _openSections così i
    // re-render non richiudono la sezione in cui l'utente sta lavorando)
    this.contentEl.querySelectorAll('.settings-section-header').forEach(h => {
      h.addEventListener('click', () => {
        const sec = h.closest('.settings-section');
        const collapsed = sec.classList.toggle('collapsed');
        if (collapsed) this._openSections.delete(sec.dataset.section);
        else this._openSections.add(sec.dataset.section);
      });
    });

    // Active config fields → auto-save on change
    for (const key of ['bot_name', 'max_tokens', 'temperature', 'reasoning_effort']) {
      const el = this.contentEl.querySelector(`[data-key="${key}"]`);
      if (!el) continue;
      el.addEventListener('change', () => this._debouncedSave(key, el.value));
    }

    this._wireWorkerSettings();

    // Telegram: widget condiviso con lo step di onboarding
    const tgContainer = this.contentEl.querySelector('#settings-telegram-widget');
    if (tgContainer) {
      if (this._tgWidget) this._tgWidget.destroy();
      this._tgWidget = new TelegramPairingWidget(tgContainer, { mode: 'settings' });
      this._tgWidget.refresh();
    }

    // Attività in background: stessa card condivisa con onboarding e Telegram.
    // Qui `grantedKey` è d'obbligo — è l'unica superficie che l'utente apre
    // apposta per controllare, e "nessun messaggio" non è una risposta.
    const batteryContainer = this.contentEl.querySelector('#settings-battery-card');
    if (batteryContainer) {
      if (this._batteryCard) this._batteryCard.destroy();
      this._batteryCard = new BatteryExemptionCard(batteryContainer, {
        tone: 'notice',
        grantedKey: 'settings.battery.granted',
      });
      this._batteryCard.render();
    }

    // Diagnostica energetica: si popola da sola (chiamata a parte) e si
    // rilegge al rientro nell'app — il permesso si concede in un dialogo di
    // sistema, e al ritorno la pagina non ha ricevuto nessun evento.
    if (this._onPowerVisible) {
      document.removeEventListener('visibilitychange', this._onPowerVisible);
    }
    this._onPowerVisible = () => {
      if (document.visibilityState === 'visible') this._loadPowerDiagnostics();
    };
    document.addEventListener('visibilitychange', this._onPowerVisible);
    this._loadPowerDiagnostics();

    // Catalogo modelli unificato
    this._wireBtn('btn-change-model', () => this._toggleModelCatalog());
    const modelSearch = this.contentEl.querySelector('#model-search');
    if (modelSearch) {
      modelSearch.addEventListener('input', () => this._applyCatalogFilter());
    }

    // Provider edit/delete buttons
    this.contentEl.querySelectorAll('.provider-edit').forEach(btn => {
      btn.addEventListener('click', () => this._editProvider(btn.dataset.provider));
    });
    this.contentEl.querySelectorAll('.provider-delete').forEach(btn => {
      btn.addEventListener('click', () => this._deleteProvider(btn.dataset.provider));
    });

    // Ricerca web → auto-save con debounce (payload completo, come il
    // bottone Salva che sostituisce)
    for (const key of ['ws_engine', 'ws_max', 'ws_timeout', 'ws_fetch_max']) {
      const el = this.contentEl.querySelector(`[data-key="${key}"]`);
      if (!el) continue;
      el.addEventListener('change', () => {
        clearTimeout(this._debounceTimers.web_search);
        this._debounceTimers.web_search = setTimeout(() => this._saveWebSearch(), 600);
      });
    }

    // Posizione: toggle auto-applicato al cambio (nessun bottone salva).
    const locToggle = this.contentEl.querySelector('#location-enabled-toggle');
    if (locToggle) {
      locToggle.addEventListener('change', () => {
        const enabled = locToggle.checked;
        api.updateLocation({ enabled: enabled ? '1' : '0' })
          .then(() => {
            if (this.data && this.data.location) this.data.location.enabled = enabled;
            showToast(i18n.t(enabled ? 'settings.location.on' : 'settings.location.off'));
          })
          .catch(() => {
            locToggle.checked = !enabled;  // rollback sull'errore
            showToast(i18n.t('settings.saveError'));
          });
      });
    }

    // Wakelock anti-doze: si salva al cambio, e il toast ripete che vale dal
    // prossimo riavvio — chi lo cambia dalla select non rilegge la riga sotto.
    const keepAwakeSelect = this.contentEl.querySelector('#keep-awake-select');
    if (keepAwakeSelect) {
      // `previous` segue l'ultimo valore accettato dal server, non quello del
      // primo render: due cambi di fila con il secondo fallito riporterebbero
      // altrimenti la select su un modo che non è più quello salvato.
      let previous = keepAwakeSelect.value;
      // Il costo della scelta sta fuori dalla select (una <option> non va a
      // capo) e segue la selezione subito, prima ancora del salvataggio: è
      // quello che l'utente sta valutando, non la conferma di quello che ha
      // già scelto. `textContent`: la frase viene da i18n, non da HTML.
      const costEl = this.contentEl.querySelector('#keep-awake-cost');
      const showCost = (mode) => {
        if (costEl) costEl.textContent = this._keepAwakeCost(mode);
      };
      keepAwakeSelect.addEventListener('change', () => {
        const mode = keepAwakeSelect.value;
        showCost(mode);
        api.updatePower({ keep_awake: mode })
          .then(() => {
            previous = mode;
            if (this.data) this.data.power = { ...(this.data.power || {}), keep_awake: mode };
            showToast(i18n.t('settings.battery.keepAwakeSaved'));
          })
          .catch(() => {
            keepAwakeSelect.value = previous;  // rollback sull'errore
            showCost(previous);
            showToast(i18n.t('settings.saveError'));
          });
      });
    }

    // Add provider
    this._wireBtn('btn-add-provider', () => this._showAddProviderDialog());

    // SSH: il blocco si popola da solo (chiamata a parte, v. _renderSsh)
    this._loadSsh();

    // Backup e ripristino
    this._wireBackup();

    // Modalità avanzata
    const advToggle = this.contentEl.querySelector('#advanced-mode-toggle');
    if (advToggle) advToggle.addEventListener('change', () => setAdvancedMode(advToggle.checked));

    // Riesegui configurazione: strada permanente verso il wizard.
    this._wireBtn('btn-rerun-onboarding', () => this._rerunOnboarding());

    // Aggiornamento dell'app (il bottone c'è solo se il backend ne annuncia uno)
    this._wireBtn('btn-update-install', () => this._startUpdate());
    // Il controllo manuale invece c'è sempre: è la diagnostica del meccanismo.
    this._wireBtn('btn-update-check', () => this._runUpdateCheck());
    /* Rientro nella sezione con un'installazione già avviata: va avanti per
       conto suo, ma `deactivate()` aveva spento il polling. Senza riagganciarlo
       il bottone resterebbe disabilitato e lo stato congelato all'ultima cosa
       vista. Il timer nullo è la prova che il polling non è già in corso. */
    if (this._update?.busy && !this._updateTimer) this._scheduleUpdatePoll(this._gen);

    // Mascotte: toggle visibilità (re-render per accendere/spegnere le
    // opzioni sotto) + scelta della taglia
    const mascotToggle = this.contentEl.querySelector('#mascot-visible-toggle');
    if (mascotToggle) {
      mascotToggle.addEventListener('change', () => {
        setMascotVisible(mascotToggle.checked);
        this.render();
      });
    }
    this.contentEl.querySelectorAll('[data-mascot-size]').forEach(btn => {
      btn.addEventListener('click', () => {
        setMascotSize(btn.dataset.mascotSize);
        this.render();
      });
    });
    // Tasto Home: nessun re-render, il valore serve solo a goHome()
    const homeSelect = this.contentEl.querySelector('#home-view-select');
    if (homeSelect) {
      homeSelect.addEventListener('change', () => setHomeView(homeSelect.value));
    }

    // Theme selector — tap a card to switch theme
    this.contentEl.querySelectorAll('.tcard[data-theme-choice]').forEach(btn => {
      btn.addEventListener('click', () => {
        const theme = setTheme(btn.dataset.themeChoice);
        this.render();
        showToast(theme.label);
      });
    });

    // Language selector — solo i bottoni seg con data-locale (quelli della
    // taglia mascotte condividono la classe ma hanno data-mascot-size).
    this.contentEl.querySelectorAll('.settings-seg-btn[data-locale]').forEach(btn => {
      btn.addEventListener('click', () => {
        const locale = btn.dataset.locale;
        i18n.setLocale(locale).then(() => {
          this.render();
          showToast(i18n.t('settings.saved'));
        });
      });
    });
  }

  _wireBtn(id, fn) {
    const btn = this.contentEl.querySelector(`#${id}`);
    if (btn) btn.addEventListener('click', fn);
  }

  // ── Save Handlers ──────────────────────────────────────────────────

  _debouncedSave(key, value) {
    clearTimeout(this._debounceTimers[key]);
    this._debounceTimers[key] = setTimeout(async () => {
      try {
        await api.updateSettings({ [key]: value });
        showToast(i18n.t('settings.saved'));
      } catch (e) { showToast(e.message, 'error'); }
    }, 600);
  }

  /* keepStoredKey: il provider ha già una chiave salvata, quindi un campo
     vuoto significa "lasciala com'è" e non va segnalato come errore. */
  async _saveProvider(
    name, format, apiKey, apiBase,
    { keepStoredKey = false, caBundle = '', clearCaBundle = false } = {},
  ) {
    if (!name || (!apiKey && !keepStoredKey)) {
      showToast(i18n.t('settings.nameAndKeyRequired'), 'error');
      return;
    }

    // Finestra di salvataggio in volo: finché la richiesta è in corso il
    // dialog non deve poter sparire. Un Indietro dispatcha un `cancel` che
    // nessuno preveniva, il listener `close` faceva remove(), e la chiave API
    // appena digitata se ne andava col DOM lasciando solo un toast. Il flag
    // vive sul dataset perché è il `cancel` a doverlo leggere.
    const dialog = document.getElementById('provider-dialog');
    const buttons = dialog ? [...dialog.querySelectorAll('.oc-btn')] : [];
    if (dialog) dialog.dataset.busy = '1';
    buttons.forEach(b => { b.disabled = true; });

    try {
      await api.updateProvider({
        name, format, api_key: apiKey, api_base: apiBase,
        ca_bundle: caBundle,
        ca_bundle_clear: clearCaBundle ? '1' : '',
      });
    } catch (e) {
      showToast(e.message, 'error');
      return;
    } finally {
      if (dialog) delete dialog.dataset.busy;
      buttons.forEach(b => { b.disabled = false; });
    }

    this._closeProviderDialog();
    showToast(i18n.t('settings.providerSaved'));
    this.loadSettings();
  }

  _editProvider(name) {
    const p = this.data?.providers?.find(pr => pr.name === name);
    if (!p) return;
    this._showAddProviderDialog(p);
  }

  async _deleteProvider(name) {
    const providers = this.data?.providers || [];
    if (providers.length <= 1) {
      showToast(i18n.t('settings.cannotDeleteLast'), 'error');
      return;
    }
    // `confirmDialog`, non la confirm() nativa: nella WebView dell'app quella
    // non mostra niente e ritorna false, quindi il tasto elimina non faceva
    // assolutamente nulla — nessun dialogo, nessuna richiesta, nessun errore.
    if (!await confirmDialog(i18n.t('settings.deleteProviderConfirm', { name }))) return;
    api.deleteProvider({ name })
      .then(() => {
        showToast(i18n.t('settings.providerDeleted'));
        this.loadSettings();
      })
      .catch(e => showToast(e.message, 'error'));
  }

  _closeProviderDialog() {
    const dialog = document.getElementById('provider-dialog');
    if (dialog) { dialog.close(); dialog.remove(); }
  }

  _saveWebSearch() {
    const v = k => this._val(k);
    const payload = {
      search_engine: v('ws_engine'),
      max_results: Number(v('ws_max')) || null,
      timeout: Number(v('ws_timeout')) || null,
      fetch_max_chars: Number(v('ws_fetch_max')) || null,
    };
    api.updateWebSearch(payload)
      .then(() => showToast(i18n.t('settings.saved')))
      .catch(e => showToast(e.message, 'error'));
  }

  // ── Catalogo modelli ───────────────────────────────────────────────

  _toggleModelCatalog() {
    const el = this.contentEl.querySelector('#model-catalog');
    if (!el) return;
    const wasOpen = el.style.display !== 'none';
    el.style.display = wasOpen ? 'none' : '';
    this._catalogOpen = !wasOpen;
    if (!wasOpen) this._loadModelCatalog();
  }

  /* Un gruppo per provider; i cataloghi arrivano in parallelo e ogni gruppo
     si riempie appena il suo fetch risponde. In coda a ogni gruppo c'è
     l'input per un ID manuale (il provider è implicito nel gruppo). */
  _loadModelCatalog() {
    const groupsEl = this.contentEl.querySelector('#model-catalog-groups');
    if (!groupsEl) return;
    const providers = this.data?.providers || [];
    if (!providers.length) {
      groupsEl.innerHTML = `<div class="settings-empty-state">${i18n.t('settings.noProviders')}</div>`;
      return;
    }
    groupsEl.innerHTML = providers.map(p => `
      <div class="model-group" data-group="${escapeHtml(p.name)}">
        <div class="model-group-label">${escapeHtml(p.name)} <span>· ${escapeHtml(this._formatLabel(p.format))}</span></div>
        <div class="model-group-items"><p class="model-group-msg">${i18n.t('settings.loading')}</p></div>
      </div>`).join('');
    for (const p of providers) {
      api.getProviderModels(p.name)
        .then(res => this._fillCatalogGroup(p, (res.models || []).map(m => m.id || m), res.message))
        .catch(() => this._fillCatalogGroup(p, [], i18n.t('settings.couldNotFetch')));
    }
  }

  _fillCatalogGroup(p, models, message) {
    const group = this.contentEl.querySelector(
      `.model-group[data-group="${CSS.escape(p.name)}"] .model-group-items`);
    if (!group) return; // catalogo richiuso o re-render nel frattempo
    const current = this.data?.agent?.model;
    const isActive = this.data?.default_provider === p.name;
    const rows = models.map(m =>
      `<div class="onboarding-model-item${isActive && m === current ? ' selected' : ''}"
        data-model="${escapeHtml(m)}" data-provider="${escapeHtml(p.name)}">${escapeHtml(m)}</div>`
    ).join('');
    const msg = !models.length && message
      ? `<p class="model-group-msg">${escapeHtml(message)}</p>` : '';
    group.innerHTML = `${rows}${msg}
      <input type="text" class="settings-input model-custom-input"
        placeholder="${i18n.t('settings.customModelId')}" autocomplete="off" />`;
    group.querySelectorAll('[data-model]').forEach(el => {
      el.addEventListener('click', () => this._selectModel(el.dataset.provider, el.dataset.model));
    });
    const custom = group.querySelector('.model-custom-input');
    custom.addEventListener('keydown', e => {
      if (e.key === 'Enter' && custom.value.trim()) this._selectModel(p.name, custom.value.trim());
    });
    this._applyCatalogFilter();
    // Il gruppo ha appena sostituito un «Caricamento…» con decine di righe: la
    // pagina è più alta di quando `render()` ha rimesso la posizione, e quella
    // andava clampata. Si riapplica ora che c'è spazio per contenerla.
    this._restoreScrollTop();
  }

  /* Il punto dell'intero redesign: modello e provider si salvano insieme. */
  _selectModel(providerName, model) {
    api.updateSettings({ model, default_provider: providerName })
      .then(() => {
        showToast(i18n.t('settings.saved'));
        this.loadSettings();
      })
      .catch(e => showToast(e.message, 'error'));
  }

  _applyCatalogFilter() {
    // Il testo si memorizza grezzo: è quello che va rimesso nell'input dopo un
    // re-render, e rimetterlo minuscolo sarebbe una riscrittura di ciò che
    // l'utente ha battuto.
    this._catalogFilter = this.contentEl.querySelector('#model-search')?.value || '';
    const q = this._catalogFilter.toLowerCase();
    this.contentEl.querySelectorAll('#model-catalog-groups [data-model]').forEach(el => {
      el.style.display = el.dataset.model.toLowerCase().includes(q) ? '' : 'none';
    });
  }

  _showAddProviderDialog(existingProvider) {
    const isEdit = !!existingProvider;
    // La chiave salvata non torna mai al client: il backend manda solo un
    // suggerimento offuscato. Va nel placeholder, MAI nel value, altrimenti
    // un salvataggio senza riscrivere la chiave persisterebbe la maschera.
    const hasStoredKey = isEdit && !!existingProvider.api_key_hint;
    const keyPlaceholder = hasStoredKey
      ? existingProvider.api_key_hint
      : i18n.t('settings.apiKeyPlaceholder');
    const dialog = document.createElement('dialog');
    dialog.className = 'oc-dialog';
    dialog.id = 'provider-dialog';
    dialog.innerHTML = `
      <div class="oc-dialog-inner">
        <h3 style="margin:0 0 16px;font-size:15px;font-weight:600">
          ${isEdit ? i18n.t('settings.editProvider') : i18n.t('settings.addProviderTitle')}
        </h3>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.name')}</label>
          <input type="text" class="settings-input" id="dlg-provider-name" placeholder="${i18n.t('settings.namePlaceholder')}"
            value="${isEdit ? escapeHtml(existingProvider.name) : ''}"
            ${isEdit ? 'readonly' : ''} />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.format')}</label>
          <select class="settings-select" id="dlg-provider-format">
            <option value="openai_compat" ${isEdit && existingProvider.format === 'openai_compat' ? 'selected' : ''}>${i18n.t('settings.openaiCompat')}</option>
            <option value="anthropic" ${isEdit && existingProvider.format === 'anthropic' ? 'selected' : ''}>${i18n.t('settings.anthropicCompat')}</option>
          </select>
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.apiKey')}</label>
          <input type="password" class="settings-input" id="dlg-api-key"
            placeholder="${escapeHtml(keyPlaceholder)}"
            autocomplete="off" data-lpignore="true" value="" />
          ${hasStoredKey ? `<span class="settings-field-hint">${i18n.t('settings.apiKeyKeepBlank')}</span>` : ''}
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.baseUrl')}</label>
          <input type="text" class="settings-input" id="dlg-api-base" placeholder="https://api.openai.com/v1"
            value="${isEdit ? escapeHtml(existingProvider.api_base || '') : ''}" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.caBundle')}</label>
          <input type="text" class="settings-input" id="dlg-ca-bundle" placeholder="${i18n.t('settings.caBundlePlaceholder')}"
            autocomplete="off" value="${isEdit ? escapeHtml(existingProvider.ca_bundle || '') : ''}" />
          <span class="settings-field-hint">${i18n.t('settings.caBundleHint')}</span>
        </div>
        <div class="oc-dialog-buttons" style="margin-top:16px">
          <button class="oc-btn oc-btn-cancel" id="dlg-provider-cancel">${i18n.t('common.cancel')}</button>
          <button class="oc-btn oc-btn-confirm" id="dlg-provider-save">${i18n.t('settings.save')}</button>
        </div>
      </div>`;
    document.body.appendChild(dialog);
    dialog.showModal();

    const formatSelect = dialog.querySelector('#dlg-provider-format');
    const baseInput = dialog.querySelector('#dlg-api-base');
    formatSelect.addEventListener('change', () => {
      const defaults = {
        'openai_compat': 'https://api.openai.com/v1',
        'anthropic': 'https://api.anthropic.com',
      };
      baseInput.placeholder = defaults[formatSelect.value] || '';
    });

    // Anche Annulla passa dal `cancel` annullabile, come Esc e il tasto
    // Indietro: chiamando close() dritto scavalcava il guard del salvataggio in
    // volo, e restava l'unica via per perdere la chiave API a metà richiesta.
    dialog.querySelector('#dlg-provider-cancel').addEventListener('click', () => {
      if (dialog.dispatchEvent(new Event('cancel', { cancelable: true }))) this._closeProviderDialog();
    });
    dialog.querySelector('#dlg-provider-save').addEventListener('click', () => {
      const name = dialog.querySelector('#dlg-provider-name').value.trim();
      const format = dialog.querySelector('#dlg-provider-format').value;
      const apiKey = dialog.querySelector('#dlg-api-key').value.trim();
      const apiBase = dialog.querySelector('#dlg-api-base').value.trim();
      const caBundle = dialog.querySelector('#dlg-ca-bundle').value.trim();
      // In modifica il campo vuoto vale sempre "tieni la chiave salvata":
      // il provider esiste già, non serve ridigitarla per cambiare l'URL.
      // Per la CA vale l'opposto — campo vuoto significa "nessuna CA" — ma la
      // stringa vuota non sopravvive alla query, quindi svuotarla si dichiara.
      this._saveProvider(name, format, apiKey, apiBase, {
        keepStoredKey: isEdit,
        caBundle,
        clearCaBundle: !caBundle && !!(isEdit && existingProvider.ca_bundle),
      });
    });
    // Il congedo (Indietro, Esc, catena della shell) passa da un `cancel`
    // annullabile: durante un salvataggio in volo lo si rifiuta, altrimenti il
    // dialog si smonta portandosi via i campi mentre la richiesta è ancora in
    // corso. Fuori da quella finestra lo scarto dei campi resta la semantica
    // normale di una modale annullabile e non si tocca.
    dialog.addEventListener('cancel', (e) => {
      if (dialog.dataset.busy) e.preventDefault();
    });
    dialog.addEventListener('close', () => dialog.remove());
  }

  _val(key) {
    const el = this.contentEl.querySelector(`[data-key="${key}"]`);
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked ? 'on' : '';
    return el.value;
  }

  // ── Utils ──────────────────────────────────────────────────────────

  _fmtNum(n) {
    if (n == null) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
  }
}
