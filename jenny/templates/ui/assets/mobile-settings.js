/** Mobile Settings Controller — i gruppi aperti dei tre cassetti dell'officina. */

import { api } from './shared/api-client.js';
import { copyToClipboard, escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { confirmDialog, detailDialog } from './shared/dialog.js';
import { TelegramPairingWidget, telegramSummary } from './shared/telegram-pairing.js';
import { getProviderBrand } from './shared/provider-brand.js';
import {
  BatteryExemptionCard,
  batteryExemptionSupported,
  batteryExemptionNeeded,
} from './shared/battery-exemption.js';
import { buildCronView } from './shared/cron-view.js';
import { whenText } from './shared/when.js';
import {
  controllable, splitSkill, blockReason, skillBlurb, skillsSummary,
} from './shared/skills-view.js';
/* Solo `runSnapshotRestore`: esportare e ripristinare da file sono in casa,
   e un import qui li rimetterebbe a portata di un bottone dimenticato. */
import { runSnapshotRestore } from './shared/backup-flow.js';

// Ripiego per `power.modes` quando il payload arriva da un gateway più vecchio
// del client: stesso ordine di `KEEP_AWAKE_MODES` in config/schema.py, dal più
// parsimonioso al più affamato.
const KEEP_AWAKE_CHOICES = ['off', 'turns', 'always'];

/* I quattro cassetti dell'officina, e cosa contiene ognuno.
 *
 * **Una tabella e non undici `if`.** Prima le sezioni erano un elenco dentro
 * `render()`; adesso sono un elenco per cassetto, e il controller ne disegna
 * uno per volta. Il giro delle tavole (`.agent/officina-tavole-plan.md`) sta
 * tutto qui dentro: spostare una sezione da un cassetto all'altro e' spostare
 * una stringa, e non c'e' nessun posto in cui possa restare scritta due volte.
 *
 * **Le porte non ci sono piu'.** Erano tre viste uscite dal dock che il
 * cassetto doveva pur far raggiungere — il cassetto delle app, i file, la
 * wiki — e una mappa `group -> porte` che le disegnava in fondo al gruppo
 * giusto. Il 21/09/2026 sono finite tutte e tre: il cassetto delle app ha la
 * sua maniglia accanto alla graffetta del composer, la wiki e' uscita
 * dall'officina (elenco, mappa e lettore vivono in casa), e i file non hanno
 * piu' una porta perche' **sono** la scheda — il gestore ci sta dentro, non
 * dietro. Un cassetto che manda altrove e' un cassetto che non contiene, ed
 * era la sola cosa che il meccanismo delle porte sapesse fare.
 *
 * **Dei due parcheggi ne resta uno.** `personalization` e' uscito il
 * 21/09/2026: temi, mascotte e finestra flottante vivevano gia' in casa, il
 * nome di Jenny e' andato nella stanza «Jenny» con loro. Resta `system` — la
 * versione e il consumo di token — che nessuna tavola disegna e che non ha
 * ancora un altro posto dove stare.
 */
/* Quali lavori periodici appartengono a Mani.
 *
 * «Programmazione» non era una famiglia: i quattro lavori di sistema finiscono
 * in tre posti diversi. `dream` e `gardener` riempiono la memoria e stanno
 * accanto a quel che riempiono (Memoria, passo 2); `update_check` e'
 * dell'app, e il suo giro e' in casa. Qui resta cio' che Jenny fa **per te**
 * quando non glielo stai chiedendo: i tuoi promemoria, e l'heartbeat, che
 * legge le cose che le hai lasciato in `HEARTBEAT.md`.
 *
 * Il payload porta gia' `kind: system|user` per riga, quindi e' un filtro e
 * non un giro di codice nuovo. */
export const HANDS_JOBS = (job) => job.kind !== 'system' || job.id === 'heartbeat';

/* I gruppi di ogni cassetto, nell'ordine in cui si scorrono.
 *
 * Erano undici **sezioni**, una per pezzo di codice. La tavola ne ha quindici,
 * piu' piccole e nominate per la domanda a cui rispondono: «Modello» da sola
 * conteneva chi risponde adesso, l'elenco delle marche e le manopole del
 * motore — tre cose che si leggono, si amministrano e non si toccano quasi
 * mai. Il taglio nuovo e' questo, non un rinominare.
 *
 * Una voce della tavola qui non c'e', e il motivo e' che **non esiste nel
 * prodotto**: un gruppo vuoto e' peggio di un gruppo assente.
 *   - «permessi di scrittura» (Mani): tre interruttori per ambito e `/ro`.
 *     Nel config esiste solo `security.restrict_to_workspace`, che oggi
 *     nessuna schermata espone.
 * Ne restava una sola dal 21/09/2026, «permessi di scrittura»: «i file veri»
 * adesso c'e' — e' il gruppo `file`, che legge davvero la radice del workspace
 * invece di rimandare altrove.
 *
 * `system` (Cervello) invece **resta**, pur non stando in nessuna tavola: e' la
 * versione e il consumo di token. Toglierlo senza dargli una casa lo farebbe
 * sparire e basta.
 */
export const DRAWERS = {
  brain: {
    sections: ['whoThinks', 'brands', 'parameters', 'battery', 'system'],
  },
  hands: {
    sections: ['webSearch', 'position', 'ssh', 'telegram', 'skill', 'scheduling'],
  },
  /* In Memoria «file» e' l'**ultima**, e non e' un dettaglio d'ordine: da
     quando quella scheda contiene l'esploratore vero la sua altezza dipende da
     quanti file ci sono, e una cartella piena sotterrerebbe qualunque cosa le
     stia sotto. In fondo non c'e' niente da sotterrare. */
  memory: {
    sections: ['howMuchItRemembers', 'dream', 'workers', 'backup', 'file'],
  },
};

/* Quale `<div id="view-...">` — e quale `<div id="title-...">` — serve un modo,
   quando non e' quello omonimo.
 *
 * Cervello, Mani e Memoria sono tre voci del dock e **una vista sola**: stesso
 * controller, cambia solo il cassetto disegnato. La tabella sta qui accanto a
 * `DRAWERS` perche' risponde alla stessa domanda, e perche' averla in due
 * copie e' costato un'intestazione: `mobile-app.js` ne aveva una per scegliere
 * la vista, `mobile-header.js` non ne aveva nessuna e cercava `title-cervello`,
 * che non esiste — quindi `setMode` usciva subito e i tre cassetti restavano
 * **senza titolo**, su uno schermo che comincia con una riga vuota (visto sul
 * telefono il 20/09/2026). */
export const VIEW_OF = { brain: 'settings', hands: 'settings', memory: 'settings' };

/* E siccome averla in due copie e' costato un'intestazione, averla **senza una
 * funzione** e' costato lo scorrimento.
 *
 * Il 22/09/2026 il carosello era morto su tre linguette su quattro, e il motivo
 * era di nuovo questo: `setupSwipeNav` faceva `getElementById(\`view-${mode}\`)`,
 * per `brain` trovava `null` e usciva in silenzio alla prima riga. Stessa
 * forma della volta prima, terzo sito — `mobile-app.js` due volte,
 * `mobile-header.js` una.
 *
 * Tre volte e' il punto in cui la tabella non basta piu': finche' resta una
 * cosa da **ricordarsi** di consultare, qualcuno scrivera' di nuovo l'id a
 * mano, e il difetto che ne esce non si vede — non in un file rotto, non in un
 * banco rosso, solo col dito sul telefono. Da qui in poi l'elemento di un modo
 * si chiede a queste due, e il banco
 * `tests/webui/test_no_raw_view_lookup_contract.py` rifiuta chi se le salta. */

/** Il `<div id="view-…">` di un modo. `null` se quel modo non ha una vista. */
export function viewElement(mode) {
  return document.getElementById(`view-${VIEW_OF[mode] || mode}`);
}

/** Il `<div id="title-…">` di un modo, dove si monta l'intestazione. */
export function titleElement(mode) {
  return document.getElementById(`title-${VIEW_OF[mode] || mode}`);
}

export class SettingsController {
  constructor() {
    this.contentEl = document.getElementById('settings-content');
    this.loadingEl = document.getElementById('settings-loading');
    this.data = null;
    this._debounceTimers = {};
    // Contatore di generazione: incrementato in deactivate(). Ogni
    // continuazione lo cattura prima del primo await ed esce se è cambiato —
    // altrimenti scrive nel DOM (o apre modali) di una sezione già lasciata.
    this._gen = 0;
    /* Posizione di lettura. Vive nel controller e non in localStorage: il
       contenitore che scorre è lo stesso che `render()` riscrive per intero,
       quindi qualunque salvataggio riportava in cima una pagina lunga. */
    this._scrollTop = 0;
    /* Vero mentre *noi* stiamo scrivendo `scrollTop`, e vero finché il
       contenuto asincrono di un `render()` non è ancora atterrato. Vedi
       `_restoreScrollTop()`. */
    this._restoringScroll = false;
    this._restorePending = false;
    /* Quale cassetto e' a schermo. Lo scrive il guscio prima di `activate()`;
       `null` vuol dire «tutti», che e' cio' che serve a chi istanzia questo
       controller da solo — i banchi, e la schermata finche' il dock non e'
       passato a quattro voci. */
    this._drawer = null;
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
      this._realignPanel(
        'brand', this._brandOpen,
        (this.data.providers || []).some(p => p.name === this._brandOpen),
        name => this._openBrand(name),
      );
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

  /** Quale dei cassetti disegnare. Lo dice il guscio, che sa quale voce del
   *  dock e' stata toccata. Ridisegna subito se i dati ci sono gia': i tre
   *  cassetti condividono un controller solo, quindi passare dall'uno
   *  all'altro non deve ricaricare `/api/settings`. */
  setDrawer(name) {
    if (this._drawer === name) return;
    this._drawer = name;
    if (this.data) this.render();
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
  }

  handleBack() {
    /* Una cosa sola, e sta in Memoria: il gestore file dentro la scheda «I
       file veri». Dentro una sottocartella Indietro risale di un livello,
       perche' uscire dal cassetto buttando via tre livelli di cammino in una
       pressione e' il difetto che la catena di `handleHardwareBack` esiste
       per evitare. Alla radice non consuma niente e si esce, come prima.

       Solo se la scheda sta nel cassetto a schermo: in Cervello e in Mani il
       gestore file non c'e', e girargli la pressione la faceva spendere a
       risalire cartelle invisibili lasciate aperte in Memoria. */
    const sections = DRAWERS[this._drawer]?.sections;
    if (sections && !sections.includes('file')) return false;
    return window.mobileApp?.controllers?.workspace?.handleCardBack?.() ?? false;
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
    /* Le undici sezioni, per id. Disegnarle tutte e poi nasconderne otto
       vorrebbe dire costruire ogni volta anche l'anagrafica delle marche e la
       storia degli snapshot: qui si costruisce **solo** quel che si vede. */
    const sections = {
      // Cervello
      whoThinks: () => this._group('whoThinks', i18n.t('workshop.groups.whoThinks'), this._renderWhoThinks(d)),
      brands: () => this._group('brands', i18n.t('settings.brands'), this._renderBrands(d)),
      parameters: () => this._group('parameters', i18n.t('workshop.groups.parameters'), this._renderParameters(d)),
      battery: () => this._renderBatterySection(d),
      system: () => this._group('system', i18n.t('settings.system'), this._renderSystem(d)),
      // Mani
      webSearch: () => this._group('webSearch', i18n.t('settings.webSearch'), this._renderWebSearch(d)),
      position: () => this._group('position', i18n.t('settings.location.section'), this._renderLocation(d)),
      ssh: () => this._group('ssh', i18n.t('settings.ssh.title'), this._renderSsh()),
      telegram: () => this._group('telegram', i18n.t('settings.telegram.title'), this._renderTelegram()),
      skill: () => this._group('skill', i18n.t('workshop.groups.skill'), this._renderSkill()),
      scheduling: () => this._group('scheduling', i18n.t('cron.byHerself'), this._renderScheduling()),
      // Memoria
      howMuchItRemembers: () => this._group('howMuchItRemembers', i18n.t('workshop.groups.howMuchItRemembers'), this._renderHowMuchItRemembers(d)),
      dream: () => this._group('dream', i18n.t('workshop.groups.dream'), this._renderDream(d)),
      workers: () => this._group('workers', i18n.t('workshop.groups.gardener'), this._renderWorkers(d)),
      file: () => this._group('file', i18n.t('workshop.groups.file'), this._renderFile()),
      backup: () => this._group('backup', i18n.t('backup.snapshotHistory'), this._renderBackup()),
    };
    const drawer = DRAWERS[this._drawer];
    const which = drawer ? drawer.sections : Object.keys(sections);

    this.contentEl.innerHTML = [
      this._renderConfigRecovery(d),
      this._renderCronRecovery(d),
      ...which.map((id) => sections[id]()),
    ].join('');

    this._wireSections();
    // L'innerHTML qui sopra ha appena riportato lo scroll in cima: va rimesso
    // dove l'aveva lasciato l'utente.
    /* In questo istante SSH, snapshot, widget Telegram e card
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

  /** Un gruppo: una soprascritta fuori, e sotto una scheda **aperta**.
   *
   *  Era una fisarmonica — testa cliccabile, chevron, corpo chiuso di
   *  default — e la tavola non ne ha nessuna. Il motivo non e' estetico: un
   *  cassetto di fisarmoniche chiuse si apre su quattro righe che non dicono
   *  niente, e per sapere cosa c'e' dentro bisogna toccarle una per una. La
   *  pagina della tavola si legge scorrendo.
   *
   *  **E porta via due pezze.** L'esenzione batteria mancante e il banner del
   *  cron aprivano d'ufficio la loro sezione, ognuno con la stessa nota: «un
   *  accordion chiuso e' esattamente il posto in cui il problema e' rimasto
   *  invisibile». Senza accordion non c'e' piu' niente da forzare.
   *
   *  L'`id` resta come `data-group`: non serve piu' a ricordare chi e'
   *  aperto, serve a chi cerca un gruppo nel DOM (il banco, e il cron che
   *  scrive nel proprio segnaposto).
   */
  _group(id, label, body) {
    return `<div class="settings-group" data-group="${id}">
      <div class="settings-group-label">${label}</div>
      <section class="settings-card">${body}</section>
    </div>`;
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
    const card = batteryExemptionSupported()
      ? `<div id="settings-battery-card"></div><div class="settings-divider"></div>`
      : '';
    return this._group('battery', i18n.t('settings.battery.title'),
      `${card}${this._renderKeepAwake(d)}<div id="settings-power-diagnostics"></div>`
      /* La riga che chiude il cassetto, come nella tavola: chi riempie la
         memoria non sta qui, sta accanto a quel che riempie. Senza, un
         cassetto che si chiama «Cervello» sembra il posto dove cercare
         Dream — ed e' esattamente l'errore che il giro dei cassetti ha
         fatto una volta. */
      + `<p class="settings-link">${i18n.t('workshop.link.dreamInMemory')}</p>`,
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
    /* A segmenti e non a tendina. Il criterio: a segmenti quando le voci
       stanno in riga, a tendina quando non ci stanno. Tre voci ci stanno — ma
       solo accorciate: «Solo
       mentre lavora (consigliato)» in un terzo di 590 px non entra. Il testo
       lungo, «(consigliato)» compreso, resta nel `title` di ogni bottone,
       quindi la raccomandazione non si perde: cambia dove si legge. */
    const options = modes.map(id => {
      const short = i18n.t(`settings.battery.keepAwakeShort.${id}`);
      const long = i18n.t(`settings.battery.keepAwake.${id}`);
      return `<button type="button" class="settings-seg-btn${id === current ? ' active' : ''}"
        data-keep-awake="${escapeHtml(id)}" title="${escapeHtml(long)}"
        role="radio" aria-checked="${id === current ? 'true' : 'false'}">${escapeHtml(short)}</button>`;
    }).join('');
    return `
      <div class="settings-subheading">${i18n.t('settings.battery.keepAwakeTitle')}</div>
      <div class="settings-field">
        <div class="settings-seg" id="keep-awake-seg" role="radiogroup"
          aria-label="${escapeHtml(i18n.t('settings.battery.keepAwakeTitle'))}">${options}</div>
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
      exactBtn.addEventListener('click', async () => {
        const native = window.JennyNative;
        if (!native || typeof native.requestExactAlarmPermission !== 'function') return;
        // Asincrono: la richiesta sta sulla porta del nativo che solo la SPA
        // raggiunge (v. `shared/native-bridge.js`).
        let opened = false;
        try {
          opened = !!(await native.requestExactAlarmPermission());
        } catch (_) { opened = false; }
        // Sotto Android 12 il permesso non esiste e la schermata nemmeno:
        // dirlo, invece di lasciare il tap senza conseguenze visibili.
        if (!opened) showToast(i18n.t('settings.battery.exactAlarmsFailed'), 'error');
      });
    }
    const btn = root.querySelector('#btn-oem-battery');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const native = window.JennyNative;
      if (!native || typeof native.openBatterySettings !== 'function') return;
      let opened = false;
      try {
        opened = !!(await native.openBatterySettings());
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

  /** Telegram, in cassetto, e' una riga: «collegato a @nome», o spento.
   *
   *  Il widget di accoppiamento — interruttore, token, codice, disaccoppia —
   *  e' un pannello di amministrazione, e sta dietro il tocco. E' lo stesso
   *  condiviso con l'onboarding: cambia dove lo si monta, non cosa fa.
   */
  _renderTelegram() {
    return this._summary('telegram', i18n.t('settings.telegram.title'), i18n.t('settings.loading'));
  }

  /** Il widget, montato dentro il pannello all'apertura. */
  _openTelegram() {
    const body = document.getElementById('drawer-telegram-body');
    if (!body) return;
    if (this._tgWidget) this._tgWidget.destroy();
    /* La riga in cassetto, sotto il pannello, segue il widget: si leggeva una
       volta al disegno e dopo un accoppiamento restava su «non collegato». */
    this._tgWidget = new TelegramPairingWidget(body, {
      mode: 'settings',
      onStatus: (state) => this._writeTelegramSummary(state),
    });
    this._tgWidget.refresh();
  }

  /** La riga in cassetto. Legge lo stato per conto suo: il widget non esiste
   *  finche' il pannello non si apre, e la riga deve dire qualcosa prima. */
  async _loadTelegramSummary() {
    const gen = this._gen;
    let state = null;
    try {
      state = await api.getTelegramStatus();
    } catch {
      state = null;
    }
    if (this._stale(gen)) return;
    this._writeTelegramSummary(state);
  }

  _writeTelegramSummary(state) {
    const el = this.contentEl?.querySelector('#summary-telegram');
    if (el) el.textContent = telegramSummary(state);
  }

  // ── Models & Providers ─────────────────────────────────────────────

  _formatLabel(fmt) {
    return {
      'openai_compat': i18n.t('provider.openai'),
      'anthropic': i18n.t('provider.anthropic'),
    }[fmt] || fmt || i18n.t('provider.unknown');
  }

  /* L'anagrafica delle marche e i parametri. **Non** la scelta del modello.
   *
   * Il catalogo — «Cambia modello», l'elenco per provider, il filtro — e' in
   * casa, da «Chi risponde»: li' un tocco su un modello salva `model` e
   * `default_provider` insieme, che e' il punto dell'intero redesign. Qui
   * resta cio' che ha bisogno di un paragrafo per spiegarsi: formato,
   * endpoint, CA bundle, e i tre parametri di generazione.
   *
   * In una riga: **in casa scegli fra quel che c'e', qui decidi cosa c'e'.**
   * Sono due verbi diversi sullo stesso oggetto, e nessuno dei due e' la
   * copia dell'altro.
   */
  /* «Modello» era una sezione sola con dentro tre cose che non si somigliano:
     chi risponde adesso, l'elenco delle marche, e le manopole del motore. La
     tavola le tiene separate, con tre soprascritte — ed e' giusto: la prima si
     legge, la seconda si amministra, la terza quasi mai. */

  /** Chi risponde adesso. Si legge, non si tocca: la scelta e' in casa. */
  _renderWhoThinks(d) {
    const a = d.agent || {};
    const providers = d.providers || [];
    const active = providers.find(p => p.name === d.default_provider);
    const via = active
      ? `${i18n.t('settings.via')} ${escapeHtml(active.name)} · ${escapeHtml(this._formatLabel(active.format))}`
      : i18n.t('settings.noProviderConfigured');

    return `
      <div class="model-inuse">
        <span class="model-inuse-name">${escapeHtml(a.model || '—')}</span>
        <span class="model-inuse-via">${via}</span>
      </div>
      <p class="settings-hint" style="margin:8px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.modelLivesInHome')}</p>`;
  }

  /** Quali marche esistono. Qui si amministra. */
  _renderBrands(d) {
    return `
      <div id="provider-list">
        ${this._renderProviderListHtml(d.providers || [], d.default_provider)}
      </div>
      <button class="settings-btn-add settings-btn-full" id="btn-add-provider"><i class="ti ti-plus"></i> ${i18n.t('settings.addProvider')}</button>
      <p class="settings-hint" style="margin:8px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.addProviderHint')}</p>`;
  }

  /** Le manopole del motore.
   *
   *  Erano dentro un `<details>` chiuso — una fisarmonica dentro una
   *  fisarmonica. Adesso che il gruppo e' suo e porta il proprio nome, il
   *  secondo strato non serve: chi scorre fin qui sa gia' cosa sta guardando.
   */
  _renderParameters(d) {
    const a = d.agent || {};
    /* La finestra di contesto esisteva nello schema, nel payload e nella rotta
       — con due soli valori accettati — e **nessuna schermata la mostrava**
       (verificato il 21/09/2026, ne' officina ne' casa). Non era un dato
       mancante: era un comando mancante.

       Le voci arrivano dal server (`context_window_options`) e non da un
       elenco ricopiato qui: la rotta ne rifiuta qualunque altro, e due copie
       divergerebbero in silenzio alla prima aggiunta. */
    const windows = a.context_window_options || [];
    const win = windows.length
      ? this._select(i18n.t('settings.contextWindow'), 'context_window_tokens',
          String(a.context_window_tokens || ''),
          windows.map((n) => ({ v: String(n), t: Number(n).toLocaleString(i18n.locale) })))
      : '';
    return `
      ${win}
      ${this._field(i18n.t('settings.maxTokens'), 'number', 'max_tokens', a.max_tokens || '', i18n.t('settings.maxTokensPlaceholder'))}
      ${this._field(i18n.t('settings.temperature'), 'number', 'temperature', a.temperature ?? '', i18n.t('settings.temperaturePlaceholder'))}
      ${this._select(i18n.t('settings.reasoningEffort'), 'reasoning_effort', a.reasoning_effort || '',
        ['', 'low', 'medium', 'high'])}`;
  }

  /** Una marca, in una riga da 52 px.
   *
   *  Era una scheda: nome, targhetta del formato, indirizzo, chiave e due
   *  bottoni-icona, per un'altezza tripla. La tavola ne fa una riga —
   *  pallino, nome, indirizzo, chiave mascherata, freccina — e ci aggiunge
   *  **la pastiglia «risponde»** su quella che risponde adesso, che la scheda
   *  non aveva: da qui si amministrano le marche, e sapere quale sta
   *  rispondendo e' il contesto di ogni decisione che si prende.
   *
   *  Modifica ed elimina passano nel pannello: sono cose che si fanno **a**
   *  una marca.
   */
  _renderProviderListHtml(providers, active) {
    if (!providers.length) return `<div class="settings-empty-state">${i18n.t('settings.noProviders')}</div>`;
    return providers.map(p => {
      const name = escapeHtml(p.name);
      const answers = p.name === active
        ? `<span class="brand-answers">${i18n.t('settings.answersNow')}</span>`
        : '';
      return `<button class="brand-row" type="button" data-brand-open="${name}">
        <span class="brand-dot" style="background:${this._brandColor(p.name)}" aria-hidden="true"></span>
        <span class="brand-text">
          <span class="brand-name">${name}${answers}</span>
          <span class="brand-where">${escapeHtml(this._formatLabel(p.format))} · ${escapeHtml(p.api_base || i18n.t('settings.defaultUrl'))}</span>
        </span>
        <span class="brand-key">${escapeHtml(p.api_key_hint || i18n.t('settings.noKey'))}</span>
        <i class="ti ti-chevron-right" aria-hidden="true"></i>
      </button>`;
    }).join('');
  }

  /** Il colore del pallino di una marca: quello della casa, da
   *  `getProviderBrand`. Qui c'era una seconda regola (una tinta dal nome per
   *  tutte), e la stessa marca aveva un colore in officina e un altro in casa;
   *  la tinta dal nome ora e' il ripiego di `getProviderBrand` per le marche
   *  che la tabella non conosce. */
  _brandColor(name) {
    return getProviderBrand(name).color;
  }

  /** Il pannello di una marca: modifica ed elimina. */
  _openBrand(name) {
    const body = document.getElementById('drawer-brand-body');
    const title = document.getElementById('drawer-brand-title');
    const p = (this.data?.providers || []).find(x => x.name === name);
    if (!body || !p) return;
    this._brandOpen = name;
    if (title) title.textContent = p.name;
    body.innerHTML = `
      <div class="settings-row">
        <span class="settings-label">${i18n.t('settings.brandAddress')}</span>
        <span class="settings-summary-value">${escapeHtml(p.api_base || i18n.t('settings.defaultUrl'))}</span>
      </div>
      <div class="settings-row">
        <span class="settings-label">${i18n.t('settings.brandKey')}</span>
        <span class="settings-summary-value">${escapeHtml(p.api_key_hint || i18n.t('settings.noKey'))}</span>
      </div>
      <div class="provider-card-actions" style="margin-top:12px">
        <button class="settings-btn-add provider-edit" data-provider="${escapeHtml(p.name)}">
          <i class="ti ti-edit"></i> ${i18n.t('settings.edit')}
        </button>
        <button class="settings-btn-add btn-danger provider-delete" data-provider="${escapeHtml(p.name)}">
          <i class="ti ti-trash"></i> ${i18n.t('settings.delete')}
        </button>
      </div>`;
    document.querySelectorAll('#drawer-brand-body .provider-edit').forEach(b =>
      b.addEventListener('click', () => this._editProvider(b.dataset.provider)));
    document.querySelectorAll('#drawer-brand-body .provider-delete').forEach(b =>
      b.addEventListener('click', () => this._deleteProvider(b.dataset.provider)));
  }

  // ── Strumenti ──────────────────────────────────────────────────────

  /* Le capacità dell'agente (ricerca web, posizione; i prossimi tool
     finiranno qui). I campi salvano da soli al cambio, come il resto. */
  /* «Strumenti» teneva insieme la ricerca web e il GPS, separati da una riga.
     Sono due cose che non si somigliano — una e' un motore di ricerca con i
     suoi limiti, l'altra e' un permesso su un sensore del telefono — e la
     tavola le tiene in due gruppi. `_renderLocation` era gia' un metodo suo:
     qui resta solo da togliere la ricerca dal suo. */
  _renderWebSearch(d) {
    const ws = d.web_search || {};
    const engines = ws.engines || ['bing'];
    return `
      ${this._select(i18n.t('settings.searchEngine'), 'ws_engine', ws.search_engine || 'bing', engines)}
      ${this._field(i18n.t('settings.maxResults'), 'number', 'ws_max', ws.max_results ?? 5, i18n.t('settings.maxResultsPlaceholder'))}
      ${this._field(i18n.t('settings.timeoutSec'), 'number', 'ws_timeout', ws.timeout ?? 30, i18n.t('settings.timeoutPlaceholder'))}
      ${this._field(i18n.t('settings.fetchMaxChars'), 'number', 'ws_fetch_max', ws.fetch_max_chars ?? 50000, i18n.t('settings.fetchMaxCharsPlaceholder'))}`;
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
    return `<div class="settings-row">
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

  /* «Memoria» teneva insieme **cosa** ricorda e **chi** gliela riempie. La
     tavola le separa, e l'ordine si rovescia: prima i tre file con le loro
     barre — la risposta a «quanto ricorda», che e' la domanda per cui si apre
     questo cassetto — e solo dopo le manopole di Dream, che e' il macchinario.

     Le due funzioni tornano la stessa frase quando il payload non c'e': il
     gruppo che non si puo' misurare lo dice al posto suo, invece di lasciare
     una scheda vuota che sembra un guasto della pagina. */

  /** I tre file e i loro tetti: la risposta a «quanto ricorda». */
  _renderHowMuchItRemembers(d) {
    const m = d.memory;
    if (!m) return `<div class="settings-empty">${i18n.t('settings.memory.unavailable')}</div>`;
    return `
      ${this._hint(i18n.t('settings.memory.budgetsHint'))}
      ${this._measureCap(m, 'MEMORY.md', 'memory_budget_chars')}
      ${this._measureCap(m, 'USER.md', 'user_budget_chars')}
      ${this._measureCap(m, 'SOUL.md', 'soul_budget_chars')}
      <button class="settings-btn-add" data-summary="caps" type="button">
        <i class="ti ti-adjustments"></i> ${i18n.t('settings.memory.changeBudgets')}
      </button>`;
  }

  /** Un file: quanto misura, la barra, e **quanto ne resta**.
   *
   *  In sola lettura. Il campo per cambiare il tetto stava qui accanto alla
   *  misura, e la tavola lo toglie: «quanto ricorda» e' una domanda con una
   *  risposta da leggere, e cambiarla e' un'altra cosa — dietro «Cambia i
   *  tetti».
   *
   *  La riga che resta e' quella che il cassetto non aveva: **i caratteri che
   *  mancano al tetto**. «2.090 su 3.000» va letto e sottratto; «restano 910»
   *  e' la risposta. Sopra il tetto la frase cambia del tutto, perche' cambia
   *  la conseguenza: Dream smette di scrivere.
   */
  _measureCap(m, label, key) {
    const { measured, rest, pct, beyond } = this._capState(m, label, key);
    return `<div class="settings-budget">
      <div class="settings-row">
        <span class="settings-label">${escapeHtml(label)}</span>
        <span class="settings-summary-value" data-measure-value="${label}">${escapeHtml(measured)}</span>
      </div>
      <div class="settings-meter${beyond ? ' is-over' : ''}" data-meter="${label}">
        <span style="width:${pct}%"></span>
      </div>
      <div class="settings-budget-measure" data-measure="${label}">${escapeHtml(rest)}</div>
    </div>`;
  }

  /** Quel che si dice di un tetto — la misura, quanto ne resta, la barra —
   *  calcolato in un posto solo.
   *
   *  Lo leggono il disegno (`_measureCap`) e il ridisegno dopo un
   *  salvataggio (`_repaintWorkerDerived`). Quando i conti erano due, il
   *  secondo scriveva la misura nella riga di «quanto resta»: dopo un
   *  salvataggio la misura compariva due volte e «restano N» spariva. */
  _capState(m, label, key) {
    const cap = m[key]?.value || 0;
    const file = (m.files || []).find(f => f.label === label);
    const chars = file && file.readable !== false && file.exists ? file.chars : null;
    const pct = cap > 0 && chars != null ? Math.min(100, Math.round((chars / cap) * 100)) : 0;
    const beyond = cap > 0 && chars != null && chars > cap;
    const rest = beyond
      ? i18n.t('settings.memory.headroomOver', { over: chars - cap })
      : (cap > 0 && chars != null ? i18n.t('settings.memory.headroom', { left: cap - chars }) : '');
    return { measured: this._budgetMeasure(m, label, key), rest, pct, beyond };
  }

  /** Il pannello dei tetti: i tre campi, con il loro range dal server. */
  _openCaps() {
    const m = this.data?.memory;
    const body = document.getElementById('drawer-caps-body');
    if (!m || !body) return;
    body.innerHTML = `
      ${this._hint(i18n.t('settings.memory.budgetsHint'))}
      ${this._numberField('MEMORY.md', 'memory_budget_chars', m.memory_budget_chars)}
      ${this._numberField('USER.md', 'user_budget_chars', m.user_budget_chars)}
      ${this._numberField('SOUL.md', 'soul_budget_chars', m.soul_budget_chars)}`;
    this._wireCaps();
  }

  /** I tre campi dentro il pannello. Stesso `change` del resto — su una
   *  tastiera mobile `input` salverebbe a ogni cifra. */
  _wireCaps() {
    document.querySelectorAll('#drawer-caps-body [data-worker-key]').forEach(el => {
      el.addEventListener('change', async () => {
        try {
          await this._saveWorkerParams('memory', { [el.dataset.workerKey]: el.value });
          showToast(i18n.t('settings.saved'));
          this.loadSettings();
        } catch (e) { showToast(e.message, 'error'); }
      });
    });
  }

  /** Dream: chi riempie quei tre file, e ogni quanto. */
  _renderDream(d) {
    const m = d.memory;
    if (!m) return `<div class="settings-empty">${i18n.t('settings.memory.unavailable')}</div>`;
    return `
      ${this._toggleRow(i18n.t('settings.memory.dreamEnabled'), 'dream-enabled-toggle', m.enabled)}
      ${this._hint(`<span id="dream-schedule">${escapeHtml(this._scheduleText(m.enabled, m.schedule))}</span>`)}
      ${this._numberField(i18n.t('settings.memory.dreamInterval'), 'dream_interval_h', m.interval_h)}
      ${this._numberField(i18n.t('settings.memory.reviewCadence'), 'review_every_runs', m.review_every_runs)}
      ${this._hint(i18n.t('settings.memory.reviewHint', { floor: m.review_floor }))}
      ${this._renderReviewState(m.review_state)}`;
  }

  /* La misura accanto al nome del file. In un metodo suo perché passa da
     `_capState`, che serve sia il disegno sia il ridisegno. */
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
      const { measured, rest, pct, beyond } = this._capState(memory, label, key);
      const meter = this.contentEl.querySelector(`[data-meter="${label}"]`);
      if (meter) {
        meter.classList.toggle('is-over', beyond);
        const fill = meter.querySelector('span');
        if (fill) fill.style.width = `${pct}%`;
      }
      const value = this.contentEl.querySelector(`[data-measure-value="${label}"]`);
      if (value) value.textContent = measured;
      const measure = this.contentEl.querySelector(`[data-measure="${label}"]`);
      if (measure) measure.textContent = rest;
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
    this._realignPanel(
      'ssh-host', this._sshOpen,
      (ssh.hosts || []).some(h => h.alias === this._sshOpen),
      alias => this._openSshHost(alias),
    );
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
  /** Un host, in una riga.
   *
   *  Era una scheda alta: alias, indirizzo, stato della credenziale, la chiave
   *  pubblica per intero, e quattro bottoni. La tavola ne fa una riga da 52 px.
   *
   *  **Ma la riga della tavola perde i due stati, e quelli restano.** Il
   *  commento che stava qui lo diceva gia', ed e' una misura e non un'opinione:
   *  credenziale pronta e impronta accettata sono i due passi che l'utente deve
   *  fare, e nasconderli dietro un tocco lascia host mezzi configurati che
   *  falliscono solo al primo comando. Qui diventano due segni brevi — un
   *  pallino e una parola — invece di due targhette larghe: stessa
   *  informazione, un decimo dello spazio, e il testo lungo resta nel `title`.
   *
   *  Tutto il resto — genera, copia, verifica, modifica, elimina — e' quel che
   *  si fa **a** un host, e si apre col tocco.
   */
  _renderSshHost(h) {
    const alias = escapeHtml(h.alias);
    const byPassword = h.auth === 'password';
    const credOk = byPassword ? !!h.has_password : !!h.has_key;
    const credLong = byPassword
      ? i18n.t(h.has_password ? 'settings.ssh.statusPasswordSet' : 'settings.ssh.statusPasswordMissing')
      : i18n.t(h.has_key ? 'settings.ssh.statusKeyReady' : 'settings.ssh.statusKeyMissing');
    const credShort = i18n.t(byPassword ? 'settings.ssh.markPassword' : 'settings.ssh.markKey');
    const impLong = i18n.t(h.pinned ? 'settings.ssh.statusPinned' : 'settings.ssh.statusUnpinned');
    return `<button class="ssh-row" type="button" data-ssh-open="${alias}">
      <span class="ssh-row-text">
        <span class="ssh-row-name">${alias}</span>
        <span class="ssh-row-where">${escapeHtml(`${h.username}@${h.host}:${h.port}`)}</span>
      </span>
      <span class="ssh-row-states">
        ${this._sshMark(credShort, credOk, credLong)}
        ${this._sshMark(i18n.t('settings.ssh.markFingerprint'), !!h.pinned, impLong)}
      </span>
      <i class="ti ti-chevron-right" aria-hidden="true"></i>
    </button>`;
  }

  /** Un pallino e una parola. Il colore da solo non basta — su un tema in cui
   *  l'accento e' il testo due pallini si somiglierebbero — quindi la
   *  differenza vera e' pieno contro vuoto, e il colore la rinforza. */
  _sshMark(word, ok, title) {
    return `<span class="ssh-mark${ok ? ' is-ok' : ' is-missing'}" title="${escapeHtml(title)}">
      <i class="ti ${ok ? 'ti-circle-check-filled' : 'ti-circle'}" aria-hidden="true"></i>${escapeHtml(word)}
    </span>`;
  }

  /** Il pannello di un host: tutto quel che gli si fa. */
  _openSshHost(alias) {
    const h = (this._ssh?.hosts || []).find(x => x.alias === alias);
    const body = document.getElementById('drawer-ssh-host-body');
    const title = document.getElementById('drawer-ssh-host-title');
    if (!h || !body) return;
    this._sshOpen = alias;
    if (title) title.textContent = h.alias;
    const byPassword = h.auth === 'password';
    const desc = h.description
      ? `<p class="settings-hint" style="margin:0 0 8px">${escapeHtml(h.description)}</p>`
      : '';
    body.innerHTML = `
      ${desc}
      <div class="settings-row">
        <span class="settings-label">${i18n.t('settings.ssh.where')}</span>
        <span class="settings-summary-value">${escapeHtml(`${h.username}@${h.host}:${h.port}`)}</span>
      </div>
      ${this._renderSshPublicKey(h)}
      <div class="provider-card-actions" style="margin-top:10px">
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
      </div>`;
    this._wireHostSsh();
  }

  /** Un pannello aperto su un oggetto appena cambiato si ridisegna — o si
   *  chiude, se l'oggetto non c'e' piu'.
   *
   *  I pannelli (host SSH, marca) si disegnano all'apertura con i dati di
   *  quel momento. Dopo «Genera chiave» il pannello restava su «nessuna
   *  chiave ancora»; dopo «Elimina» restava aperto su un host o una marca che
   *  non esistevano piu', coi bottoni ancora attivi. Lo chiamano i due
   *  caricamenti, cosi' qualunque azione che ricarica lo ottiene gratis. */
  _realignPanel(id, name, exists, reopen) {
    const drawer = window.mobileApp?.drawer;
    if (!name || drawer?.activeDrawer !== id) return;
    if (exists) reopen(name);
    else drawer.close(id);
  }

  /** I comandi dentro il pannello. All'apertura, non al caricamento. */
  _wireHostSsh() {
    const each = (sel, fn) =>
      document.querySelectorAll(`#drawer-ssh-host-body ${sel}`).forEach(btn =>
        btn.addEventListener('click', () => fn(btn.dataset.sshAlias, btn)));
    each('.ssh-generate', (alias, btn) => this._sshGenerateKey(alias, !!btn.dataset.hasKey));
    each('.ssh-verify', alias => this._sshVerify(alias));
    each('.ssh-edit', alias => this._showSshHostDialog(
      (this._ssh?.hosts || []).find(h => h.alias === alias)));
    each('.ssh-delete', alias => this._sshDelete(alias));
    each('.ssh-copy', alias => this._sshCopyPublicKey(alias));
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
    /* In cassetto ogni host e' una riga: il tocco apre il suo pannello. I
       comandi (genera, verifica, modifica, elimina, copia) li aggancia
       `_wireHostSsh` la' dentro — qui non esistono ancora nel DOM. */
    this.contentEl.querySelectorAll('[data-ssh-open]').forEach(row =>
      row.addEventListener('click', () => {
        window.mobileApp?.drawer?.open('ssh-host');
        this._openSshHost(row.dataset.sshOpen);
      }));
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
    /* Il bottone vive nel pannello dell'host, non nella schermata: cercato in
       `contentEl` non si trovava mai, e il segno di attesa non compariva. */
    const btn = document.querySelector(
      `#drawer-ssh-host-body .ssh-verify[data-ssh-alias="${CSS.escape(alias)}"]`);
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
      this._realignPanel('ssh-host', alias, false, null);
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

  // ── I file veri ────────────────────────────────────────────────────

  /** Il gestore file, dentro la scheda.
   *
   *  **La tavola la chiamava «i file veri», e fino al 21/09/2026 non
   *  esisteva.** Al suo posto c'era una riga in fondo al giardiniere che
   *  portava al gestore file: un collegamento, non un contenuto.
   *
   *  Il primo tentativo fu un riassunto — le prime otto voci della radice, poi
   *  una porta verso il gestore vero. Durato un giorno: un elenco troncato che
   *  non si tocca non risponde a nessuna domanda, e il bottone sotto rendeva
   *  due gesti quel che ne vale uno. **Adesso la scheda contiene il gestore.**
   *  Le cartelle si aprono qui dentro, senza mai lasciare Memoria; Indietro
   *  risale di una cartella (`handleCardBack`) prima di uscire dal cassetto.
   *
   *  L'unica uscita e' **aprire** un file, che e' una schermata sua come in
   *  qualunque gestore file, e da cui Indietro riporta esattamente in questa
   *  cartella (`_enterEditorView`).
   *
   *  Qui c'e' solo il contenitore: i nodi li riempie `WorkspaceController`,
   *  che e' anche l'unico posto dove sta il come — icone, miniature, tieni
   *  premuto, «nuovo». Riscriverne una seconda copia per la scheda avrebbe
   *  voluto dire due elenchi degli stessi file che col tempo si raccontano
   *  diversi, ed e' esattamente il difetto che la scheda riassunto aveva.
   *
   *  **E i file di servizio non ci sono**: il flag `internal` lo mette il
   *  server file per file, e a filtrarli e' `renderGrid`.
   */
  _renderFile() {
    return `
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('workshop.file.desc')}</p>
      <div class="ws-explorer" id="settings-file-explorer">
        <div class="ws-bar">
          <div class="ws-breadcrumb" data-ws-crumb></div>
          <button class="ws-new" data-ws-new type="button"
                  title="${escapeHtml(i18n.t('workspace.new'))}"
                  aria-label="${escapeHtml(i18n.t('workspace.new'))}">
            <i class="ti ti-plus"></i>
          </button>
        </div>
        <div class="ws-grid" data-ws-grid></div>
        <div class="ws-empty" data-ws-empty style="display:none">
          <div class="ws-empty-icon"><i class="ti ti-folder-off"></i></div>
          <div class="ws-empty-title">${escapeHtml(i18n.t('workspace.noFiles'))}</div>
          <div class="ws-empty-sub">${escapeHtml(i18n.t('workspace.folderEmpty'))}</div>
        </div>
      </div>`;
  }

  /** Consegna il contenitore appena disegnato al gestore file.
   *
   *  Il controller puo' non esistere ancora: fino a ieri nasceva alla prima
   *  apertura della sua vista, e quella vista adesso e' solo il file aperto —
   *  cioe' arriva **dopo**. `ensureController` lo costruisce senza portarcisi.
   */
  _mountFile() {
    const host = this.contentEl?.querySelector('#settings-file-explorer');
    if (!host) return;
    window.mobileApp?.ensureController('workspace')?.mount(host);
  }

  // ── Backup e ripristino ──────────────────────────────────────────────

  /* La storia locale, e **non** il backup cifrato.
   *
   * Esportare e ripristinare vivono in casa, da «Backup», dove sono arrivati
   * col giro di «Tu e Jenny» — e la frase che la casa gia' scrive di suo dice
   * anche perche' questa meta' sta qui: la storia locale «serve a rimettere a
   * posto una cosa cancellata per sbaglio, e si sfoglia in officina. Vive pero'
   * su questo telefono — di un telefono perso non salva niente».
   *
   * Due schermate che sanno esportare vorrebbero dire due posti da tenere
   * allineati per un gesto che si fa una volta al mese; e sarebbero anche due
   * posti in cui puo' comparire una passphrase.
   */
  /** La storia locale, in cassetto, e' **una riga**.
   *
   *  L'elenco disteso costava due terzi dei 6.467 px di Memoria (misurato sul
   *  telefono il 21/09/2026, foto intera): una riga per istantanea, e ce ne
   *  sono venti. Nessuna di quelle righe risponde alla domanda per cui si apre
   *  il gruppo — «ce l'ho una storia, e quanto va indietro?» — a cui invece
   *  rispondono due numeri.
   *
   *  Il resto (l'elenco, per quanto si conserva, «crea adesso») non sparisce:
   *  si apre nel pannello `drawer-history`. E' il criterio della tavola, ed e'
   *  lo stesso ovunque: **in cassetto quel che si legge, l'amministrazione
   *  dietro un tocco.**
   */
  /* Chi disegna il corpo di ogni pannello, per id. Tabella e non `if`: le
     righe di riepilogo sono destinate a diventare tre (storia, Telegram, SSH)
     e un elenco di condizioni le farebbe divergere una per volta. */
  get _OPEN_PANEL() {
    return {
      history: this._openHistory,
      telegram: this._openTelegram,
      skill: this._openSkill,
      caps: this._openCaps,
    };
  }

  _renderBackup() {
    return `
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.snapshotDesc')}</p>
      ${this._summary('history', i18n.t('backup.snapshotHistory'), i18n.t('settings.loading'))}`;
  }

  /** Una riga di riepilogo: cosa c'e', in due numeri, e una freccina.
   *
   *  `value` e' la risposta breve; il tocco apre `drawer-<id>`. Il bottone e'
   *  un `<button>` vero e non una riga cliccabile: da tastiera ci si arriva, e
   *  chi legge lo schermo sente che e' un comando.
   */
  _summary(id, label, value) {
    return `<button class="settings-summary" data-summary="${id}" type="button">
      <span class="settings-summary-name">${label}</span>
      <span class="settings-summary-value" id="summary-${id}">${value}</span>
      <i class="ti ti-chevron-right" aria-hidden="true"></i>
    </button>`;
  }

  /** Il corpo del pannello della storia: quel che stava disteso nel cassetto.
   *
   *  Si disegna **all'apertura** e non al caricamento: un pannello chiuso non
   *  ha i suoi nodi nel DOM, e `_loadSnapshotList` scriverebbe nel vuoto senza
   *  dire niente.
   */
  _openHistory() {
    const body = document.getElementById('drawer-history-body');
    if (!body) return;
    body.innerHTML = `
      <div class="settings-row">
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
    this._wireHistory();
    this._loadSnapshotList();
  }

  // ── Sistema ────────────────────────────────────────────────────────

  /* Diagnostica e opzioni da power user.
   *
   * **Il giro degli aggiornamenti non c'e' piu'**: controllo, riquadro,
   * installazione e diagnostica del meccanismo sono in casa, da
   * «Aggiornamenti», dove sono arrivati col giro di «Tu e Jenny». Qui resta il
   * numero di versione, che e' un dato e non un giro.
   *
   * **E nemmeno «riesegui la configurazione»**: `save_onboarding` fa
   * `config.providers.providers = [one]` — **sostituisce** l'elenco invece di
   * aggiungere. In una schermata da operatore quel bottone puo' solo toglierti
   * marche che hai configurato, e tutto cio' che il wizard imposta si fa
   * meglio di qua (la marca col suo `+`, il modello dalla casa). Resta vivo
   * dove serve: al primo avvio, quando non c'e' ancora niente da cancellare.
   */
  _renderSystem(d) {
    const v = d.version || {};
    return `
      <div class="settings-field-row">
        <span class="settings-field-label">${i18n.t('settings.version')}</span>
        <span class="settings-field-value">${escapeHtml(v.current || '—')}</span>
      </div>
      <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.updatesLiveInHome')}</p>
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.tokenUsage')}</div>
      ${this._renderUsage(d)}`;
  }


  /** In cassetto c'e' solo la riga: qui si va a prendere di che riempirla.
   *
   *  I comandi del pannello (crea, conservazione) li aggancia `_wireHistory`
   *  quando il pannello si apre: adesso non esistono nel DOM.
   */
  _wireBackup() {
    this._loadHistorySummary();
  }

  /** I comandi dentro il pannello. Girano all'apertura, non al caricamento. */
  _wireHistory() {
    const createBtn = document.getElementById('btn-snapshot-create');
    if (createBtn) {
      createBtn.addEventListener('click', async () => {
        try {
          const res = await api.createSnapshot();
          showToast(res.snapshot
            ? i18n.t('backup.snapshotCreated')
            : i18n.t('backup.snapshotNoChanges'));
          this._loadSnapshotList();
          this._loadHistorySummary();
        } catch (e) { showToast(e.message, 'error'); }
      });
    }
    const retentionEl = document.getElementById('snapshot-retention');
    if (retentionEl) {
      retentionEl.addEventListener('change', async () => {
        try {
          await api.updateSnapshotRetention(parseInt(retentionEl.value, 10));
          showToast(i18n.t('settings.saved'));
          this._loadSnapshotList();
          this._loadHistorySummary();
        } catch (e) { showToast(e.message, 'error'); }
      });
    }
  }

  /** Quante istantanee ci sono e quanto va indietro la piu' vecchia.
   *
   *  Due numeri al posto di venti righe. Un fallimento non lascia la riga a
   *  «Caricamento...» per sempre: dice che non si e' potuto leggere, che e'
   *  un'informazione, mentre un caricamento eterno e' un guasto travestito.
   */
  async _loadHistorySummary() {
    const gen = this._gen;
    const write = (text) => {
      const el = this.contentEl?.querySelector('#summary-history');
      if (el) el.textContent = text;
    };
    try {
      const history = await api.getSnapshotHistory();
      if (this._stale(gen)) return;
      const snapshots = history.snapshots || [];
      if (!snapshots.length) {
        write(i18n.t('backup.snapshotSummaryEmpty'));
        return;
      }
      /* La piu' vecchia, non la piu' recente: dice **quanto indietro si puo'
         tornare**, che e' la cosa per cui una storia esiste. */
      const oldest = Math.min(...snapshots.map(s => s.created_at_ms));
      write(i18n.t('backup.snapshotSummary', {
        count: snapshots.length,
        when: whenText(oldest),
      }));
    } catch {
      if (this._stale(gen)) return;
      write(i18n.t('backup.snapshotHistoryUnavailable'));
    }
  }

  /** Allinea la select al valore corrente; un valore fuori preset (config
   *  editata a mano) diventa un'opzione dedicata invece di mostrarne una falsa. */
  _syncRetentionSelect(days) {
    const el = document.getElementById('snapshot-retention');
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
    if (!document.getElementById('snapshot-list')) return;
    let snapshots = [];
    try {
      const history = await api.getSnapshotHistory();
      if (this._stale(gen)) return;
      snapshots = history.snapshots || [];
      this._syncRetentionSelect(history.retention_max_age_days);
    } catch {
      if (this._stale(gen)) return;
      const failEl = document.getElementById('snapshot-list');
      if (failEl) failEl.innerHTML = `<div class="settings-empty-state">${i18n.t('backup.snapshotHistoryUnavailable')}</div>`;
      return;
    }
    const listEl = document.getElementById('snapshot-list');
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

  /* Le tre righe dell'officina — un numero, un menu', un numero col suo range —
     hanno tutte la stessa forma: nome a sinistra, comando a destra. Era
     impilata: etichetta sopra, campo a tutta larghezza sotto. Vedi
     `.settings-row` nel foglio di stile per il motivo della classe nuova. */
  _field(label, type, key, value, placeholder = '') {
    return `<div class="settings-row">
      <label class="settings-label">${label}</label>
      <input type="${type}" class="settings-input" data-key="${key}" value="${escapeHtml(String(value))}"
        placeholder="${escapeHtml(placeholder)}">
    </div>`;
  }

  /** Un menu'. Le voci sono stringhe, oppure `{v, t}` quando quel che si
   *  salva e quel che si legge non sono la stessa cosa — un numero di token si
   *  salva «65536» e si legge «65 536», e senza i separatori nessuno conta le
   *  cifre. */
  _select(label, key, value, options) {
    const opts = options.map(o => {
      const v = typeof o === 'object' ? o.v : o;
      const t = typeof o === 'object' ? o.t : o;
      return `<option value="${escapeHtml(v)}" ${v === value ? 'selected' : ''}>${escapeHtml(t) || '—'}</option>`;
    }).join('');
    return `<div class="settings-row">
      <label class="settings-label">${label}</label>
      <select class="settings-select" data-key="${key}">${opts}</select>
    </div>`;
  }

  // ── Wire Events ────────────────────────────────────────────────────

  // ── Skill ──────────────────────────────────────────────────────────

  /** Le skill, in cassetto, sono una riga: quante vengono con l'app e quante
   *  sono tue. L'elenco e l'interruttore stanno nel pannello.
   *
   *  Stavano nella schermata Apps, cancellata il 21/09/2026, e da allora non si
   *  vedevano da nessuna parte. Qui e non altrove perche' Mani risponde a
   *  *cosa sa fare* Jenny, e una skill e' una procedura che sa eseguire (v.
   *  `.agent/officina-skill-plan.md`). Crearle, cambiarle e cancellarle
   *  restano fuori, per scelta: si chiede a lei, in chat.
   */
  _renderSkill() {
    return this._summary('skill', i18n.t('skills.summaryName'), i18n.t('settings.loading'))
      + `<p class="settings-link">${i18n.t('skills.howToTeach')}</p>`;
  }

  /** La riga in cassetto. Un errore lo dice invece di restare a caricare. */
  async _loadSkillsSummary() {
    const gen = this._gen;
    const write = (text) => {
      const el = this.contentEl?.querySelector('#summary-skill');
      if (el) el.textContent = text;
    };
    try {
      const { skills } = await api.listSkills();
      if (this._stale(gen)) return;
      write(skillsSummary(splitSkill(skills), (k, v) => i18n.t(k, v)));
    } catch {
      if (this._stale(gen)) return;
      write(i18n.t('skills.summaryError'));
    }
  }

  /** Il pannello. Rilegge **sempre** all'apertura, e non riusa la fetch della
   *  riga: una skill che Jenny ha scritto un minuto fa deve esserci.
   *
   *  Il corpo si cerca in `document` e non in `contentEl`: il pannello vive
   *  fuori dalla vista, ed e' la trappola in cui `_loadSnapshotList` e' gia'
   *  caduta una volta. */
  async _openSkill() {
    const body = document.getElementById('drawer-skill-body');
    if (!body) return;
    const gen = this._gen;
    body.innerHTML = `<div class="settings-empty-state">${i18n.t('settings.loading')}</div>`;
    let skills;
    try {
      ({ skills } = await api.listSkills());
    } catch {
      if (this._stale(gen)) return;
      body.innerHTML = `
        <div class="settings-empty-state">${i18n.t('skills.readError')}</div>
        <button class="settings-btn-add" type="button" data-skill-retry>${i18n.t('skills.retry')}</button>`;
      body.querySelector('[data-skill-retry]')
        ?.addEventListener('click', () => this._openSkill());
      return;
    }
    if (this._stale(gen)) return;
    const { yours, integrate, service } = splitSkill(skills);
    body.innerHTML = `
      <div class="settings-group">
        <div class="settings-group-label">${i18n.t('skills.yours')}</div>
        <section class="settings-card">${yours.length
          ? yours.map((sk) => this._skillRow(sk)).join('')
          : this._skillEmpty()}</section>
      </div>
      ${integrate.length ? `
      <div class="settings-group">
        <div class="settings-group-label">${i18n.t('skills.integrate')}</div>
        <section class="settings-card">${integrate.map((sk) => this._skillRow(sk)).join('')}</section>
      </div>` : ''}
      ${service ? `<p class="settings-link">${i18n.t('skills.service', { n: service })}</p>` : ''}`;
    this._wireSkill(body);
  }

  /** Una skill: nome, una riga sotto, e a destra o l'interruttore o il
   *  lucchetto. L'interruttore c'e' solo dove la scelta sopravvive al riavvio
   *  (`controllable`): su una integrata l'avvio la cancellerebbe.
   *
   *  «Non disponibile» e «spenta» sono due cose: la prima e' un impedimento, e
   *  prende la riga sotto col suo motivo — in testo, non solo in colore. */
  _skillRow(sk) {
    const name = escapeHtml(sk.name);
    const under = sk.available === false
      ? `<span class="skill-row-broken"><span class="skill-row-dot" aria-hidden="true"></span>${escapeHtml(sk.unavailable_reason || '')}</span>`
      : escapeHtml(skillBlurb(sk, i18n.locale));
    const command = controllable(sk)
      ? `<label class="toggle-switch">
          <input type="checkbox" data-skill-toggle="${name}" ${sk.disabled ? '' : 'checked'}
                 aria-label="${escapeHtml(i18n.t('skills.active', { name: sk.name }))}">
          <span class="toggle-slider"></span>
        </label>`
      : `<i class="ti ti-lock skill-row-lock" role="img"
            aria-label="${escapeHtml(i18n.t(blockReason(sk)))}"
            title="${escapeHtml(i18n.t(blockReason(sk)))}"></i>`;
    return `<div class="skill-row">
      <button class="skill-row-text" type="button" aria-expanded="false">
        <span class="skill-row-name">${name}</span>
        ${under ? `<span class="skill-row-under">${under}</span>` : ''}
      </button>
      ${command}
    </div>`;
  }

  /** «Le tue», vuota: un invito, non una scusa. Il bottone scrive nel composer
   *  e **non manda**: la frase la finisce l'utente. */
  _skillEmpty() {
    return `<div class="skill-empty">
      <i class="ti ti-sparkles" aria-hidden="true"></i>
      <div class="skill-empty-title">${i18n.t('skills.emptyTitle')}</div>
      <p class="settings-hint">${i18n.t('skills.emptyText')}</p>
      <button class="settings-btn-add" type="button" data-skill-ask>${i18n.t('skills.ask')}</button>
    </div>`;
  }

  _wireSkill(body) {
    // Il tocco sul testo scioglie il troncamento a due righe: e' l'unica cosa
    // in piu' che la riga ha da dire, e non vale un foglio.
    body.querySelectorAll('.skill-row-text').forEach((btn) => {
      btn.addEventListener('click', () => {
        btn.setAttribute('aria-expanded', String(btn.getAttribute('aria-expanded') !== 'true'));
      });
    });
    body.querySelectorAll('[data-skill-toggle]').forEach((input) => {
      input.addEventListener('change', async () => {
        const active = input.checked;
        input.disabled = true;  // una richiesta alla volta
        try {
          await api.setSkillDisabled(input.dataset.skillToggle, !active);
        } catch {
          input.checked = !active;  // rollback sull'errore
          showToast(i18n.t('settings.saveError'), 'error');
        } finally {
          input.disabled = false;
        }
      });
    });
    body.querySelector('[data-skill-ask]')?.addEventListener('click', () => {
      const app = window.mobileApp;
      app?.drawer?.close('skill');
      app?.sendInChat?.(i18n.t('skills.askPrompt'));
    });
  }

  // ── Programmazione ─────────────────────────────────────────────────────

  /* Segnaposto: il blocco si popola da `_loadCron`, come SSH e gli snapshot.
     Renderlo sincrono vorrebbe dire una fetch dentro `render()`, e `render()`
     gira anche dopo ogni salvataggio. */
  _renderScheduling() {
    return `<div id="cron-block"><div class="settings-empty-state">${i18n.t('settings.loading')}</div></div>`;
  }

  /* Una sola lettura, all'apertura della schermata e sul pulsante Aggiorna.

     **Nessun polling**, ed è una decisione e non una semplificazione: su questo
     telefono il lavoro anti-doze si misura in job puntuali a ±2 s attraverso ore
     di doze profondo, e un pannello che sveglia gateway e WebView ogni cinque
     secondi finché è aperto è esattamente il contrario. Il timbro «aggiornato
     alle …» in cima dichiara che è un'istantanea, così l'utente non deve
     chiedersi se sta guardando il presente. */
  async _loadCron() {
    const gen = this._gen;
    if (!this.contentEl.querySelector('#cron-block')) return;
    let payload;
    try {
      payload = await api.getCron();
    } catch {
      if (this._stale(gen)) return;
      const failEl = this.contentEl.querySelector('#cron-block');
      if (failEl) {
        failEl.innerHTML = `<div class="settings-empty-state">${i18n.t('cron.failed')}</div>`;
      }
      return;
    }
    if (this._stale(gen)) return;
    // Il nodo si cerca **dopo** l'await: `render()` rifà tutto l'innerHTML, e un
    // `#cron-block` catturato prima della fetch è già staccato dal documento.
    const blockEl = this.contentEl.querySelector('#cron-block');
    if (!blockEl) return;
    this._cron = buildCronView(payload, {
      nowMs: payload.now_ms,
      tr: (key, params) => i18n.t(key, params),
      locale: i18n.locale,
      keep: HANDS_JOBS,
    });
    blockEl.innerHTML = this._renderCronBlock(this._cron);
    this._wireCronBlock();
    this._restoreScrollTop();
  }

  _renderCronBlock(view) {
    if (!view.available) {
      return `<div class="settings-empty-state">${i18n.t('cron.notStarted')}</div>`;
    }
    const stamp = new Date(view.asOf).toLocaleTimeString(i18n.locale, {
      hour: '2-digit', minute: '2-digit',
    });
    const rows = view.rows.length
      ? view.rows.map(r => this._renderCronJob(r)).join('')
      : `<div class="settings-empty-state">${i18n.t('cron.empty')}</div>`;
    return `
      ${this._renderCronBanner(view)}
      ${rows}
      <div class="cron-asof">
        <span>${escapeHtml(i18n.t('cron.asOf', { time: stamp }))}</span>
        <button class="cron-refresh" id="btn-cron-refresh">
          <i class="ti ti-refresh"></i> ${i18n.t('cron.refresh')}
        </button>
      </div>`;
  }

  /* Un banner solo, il più utile: la scelta sta in `pickBanner` (modulo puro),
     qui c'è soltanto il testo. Il caso `inert` cerca prima di tutto l'heartbeat,
     perché per lui la frase può dire *cosa* manca — un file senza task — mentre
     per gli altri tre può solo dire che l'interruttore è giù. */
  _renderCronBanner(view) {
    const b = view.banner;
    if (!b) return '';
    let text = '';
    let strong = false;
    if (b.kind === 'stopped') {
      text = i18n.t('cron.banner.stopped');
      strong = true;
    } else if (b.kind === 'recovered') {
      const empty = b.restoredFrom === 'empty';
      text = i18n.t(empty ? 'cron.banner.recoveredEmpty' : 'cron.banner.recoveredBackup');
      strong = empty;
    } else if (b.kind === 'inert') {
      const hb = view.rows.find(r => r.id === 'heartbeat' && r.heartbeat?.checkingNothing);
      if (hb) {
        text = i18n.t('cron.banner.heartbeatEmpty', { every: hb.schedule });
      } else if (b.jobs.length === 1) {
        text = i18n.t('cron.banner.inertOne', { name: b.jobs[0] });
      } else {
        text = i18n.t('cron.banner.inertMany', { names: b.jobs.join(', ') });
      }
    } else {
      return '';
    }
    return `<div class="settings-notice${strong ? ' settings-notice-strong' : ''}">
      <i class="ti ti-alert-triangle"></i>
      <div>${escapeHtml(text)}</div>
    </div>`;
  }

  /* Una card per job. In chiaro ci sta quel che si legge di sfuggita — nome,
     schedulazione, prossima e ultima esecuzione — e il resto (storico, testo del
     promemoria, controlli dell'heartbeat) sta nel dettaglio: una riga d'elenco
     che porta tutto smette di essere un elenco. */
  /** Un lavoro, in una riga.
   *
   *  Era una scheda alta: nome e schedule in testa, una fila di targhette, e
   *  «Next: …» / «Last: … · esito» su due righe intere. Con cinque lavori
   *  faceva oltre un terzo dell'altezza di Mani (misurato sul telefono il
   *  21/09/2026, foto intera).
   *
   *  La tavola ne fa una riga: **nome** a sinistra con la sua natura, l'esito
   *  dell'ultimo giro sotto, e **quando tocca di nuovo** a destra. Le due
   *  parole «Next» e «Last» spariscono: la colonna di destra *e'* il prossimo
   *  giro, e la riga sotto *e'* l'ultimo. Restano il pallino dell'esito e le
   *  targhette che cambiano il significato della riga (spento, inerte), che non
   *  sono decorazione: un lavoro spento con su scritto «fra 4 minuti» sarebbe
   *  una bugia.
   */
  _renderCronJob(row) {
    const badges = [];
    if (row.kind === 'system') badges.push(i18n.t('cron.job.protected'));
    if (row.monitor) badges.push(i18n.t('cron.job.monitor'));
    if (row.oneShot) badges.push(i18n.t('cron.job.oneShot'));
    if (row.health === 'off') badges.push(i18n.t('cron.job.disabled'));
    if (row.health === 'inert') badges.push(i18n.t('cron.job.inert'));
    const badgeHtml = badges
      .map(b => `<span class="cron-badge">${escapeHtml(b)}</span>`).join('');
    const when = row.next
      ? `<span class="cron-row-when${row.next.overdue ? ' is-late' : ''}">${escapeHtml(row.next.relative)}${this._cronTz(row.next)}</span>`
      : `<span class="cron-row-when is-mute">${escapeHtml(i18n.t('cron.job.noNext'))}</span>`;
    const last = row.last
      ? `<span class="cron-dot cron-dot-${row.lastTone}"></span>${escapeHtml(row.last.relative)} · ${escapeHtml(this._cronStatusText(row.lastStatus))}`
      : escapeHtml(i18n.t('cron.job.neverRun'));
    /* Il conteggio dei «non ho potuto controllare» resta su una riga sua: e' lo
       stato che dice che un monitor sta girando a vuoto, e incastrarlo nella
       riga dell'esito lo farebbe leggere come parte di quello. */
    const cnc = row.couldNotCheck?.consecutive_could_not_check
      ? `<div class="cron-health">${escapeHtml(this._cronHealthText(row.couldNotCheck))}</div>`
      : '';
    return `
      <button class="cron-row cron-card-${row.health}" type="button" data-cron-job="${escapeHtml(row.id)}">
        <span class="cron-row-text">
          <span class="cron-row-name">${escapeHtml(row.name)}${badgeHtml}</span>
          <span class="cron-row-under">${last}</span>
          ${cnc}
        </span>
        ${when}
      </button>`;
  }

  /* Il fuso si nomina solo quando diverge da quello del dispositivo: `09:00
     (Asia/Tokyo)` è utile, `(Europe/Rome)` su un telefono a Roma è rumore su
     ogni riga. La decisione sta in `timeZoneNote`. */
  _cronTz(when) {
    return when.timeZoneNote ? ` <span class="cron-tz">(${escapeHtml(when.timeZoneNote)})</span>` : '';
  }

  _cronStatusText(status) {
    const text = i18n.t(`cron.status.${status}`);
    // `i18n.t` ritorna la chiave grezza quando non la conosce: uno store scritto
    // da una versione più nuova non deve stampare "cron.status.qualcosa".
    return text.startsWith('cron.status.') ? i18n.t('cron.status.unknown') : text;
  }

  _cronHealthText(health) {
    const parts = [i18n.t('cron.health.couldNotCheck', { n: health.consecutive_could_not_check })];
    if (health.since_ms) {
      parts.push(i18n.t('cron.health.since', {
        when: new Date(health.since_ms).toLocaleString(i18n.locale),
      }));
    }
    parts.push(i18n.t(health.escalated ? 'cron.health.warned' : 'cron.health.notWarned'));
    return parts.join(' · ');
  }

  _wireCronBlock() {
    this._wireBtn('btn-cron-refresh', () => this._loadCron());
    this.contentEl.querySelectorAll('[data-cron-job]').forEach(card => {
      card.addEventListener('click', () => {
        const row = this._cron?.rows.find(r => r.id === card.dataset.cronJob);
        if (row) this._showCronJobDialog(row);
      });
    });
  }

  /* Il dettaglio è **piatto**: storico e controlli dell'heartbeat stanno dentro
     la stessa modale. `detailDialog` ha una sola istanza (`#oc-detail-dialog`) e
     si rifiuta di aprirsi se è già aperta, quindi un secondo livello job→run non
     esisterebbe comunque — meglio progettarlo piatto che scoprirlo dopo. */
  _showCronJobDialog(row) {
    const parts = [];
    if (row.purpose) parts.push(`<p class="oc-detail-lead">${escapeHtml(row.purpose)}</p>`);
    if (row.message) {
      parts.push(`<div class="settings-subheading">${i18n.t('cron.job.text')}</div>
        <p class="cron-detail-text">${escapeHtml(row.message)}</p>`);
    }
    if (row.heartbeat) parts.push(this._renderCronHeartbeat(row.heartbeat));
    parts.push(`<div class="settings-subheading">${i18n.t('cron.job.runs')}</div>`);
    if (row.runs.length) {
      parts.push(`<div class="cron-runs">${row.runs.map(run => `
        <div class="cron-run">
          <span class="cron-dot cron-dot-${run.tone}"></span>
          <span class="cron-run-when">${escapeHtml(new Date(run.atMs).toLocaleString(i18n.locale))}</span>
          <span class="cron-run-status">${escapeHtml(this._cronStatusText(run.status))}</span>
          ${run.error ? `<span class="cron-run-error">${escapeHtml(run.error)}</span>` : ''}
        </div>`).join('')}</div>`);
    } else {
      parts.push(`<div class="settings-empty-state">${i18n.t('cron.job.noRuns')}</div>`);
    }
    detailDialog({ title: row.name, bodyHtml: parts.join('') });
  }

  /* I controlli di HEARTBEAT.md. Il blocco esiste solo per il job `heartbeat`, e
     dice due cose che lo store da solo non sa dire: quali controlli **esistono**
     (il file), e quali di quelli sono rotti (lo stato). Senza la prima metà, un
     heartbeat che gira a vuoto è indistinguibile da uno sano — registra `ok` a
     ogni giro. */
  _renderCronHeartbeat(hb) {
    const head = `<div class="settings-subheading">${i18n.t('cron.heartbeat.title')}</div>`;
    if (!hb.fileReadable) {
      return `${head}<div class="settings-notice settings-notice-strong">
        <i class="ti ti-alert-triangle"></i>
        <div>${escapeHtml(i18n.t('cron.heartbeat.fileUnreadable'))}</div></div>`;
    }
    if (!hb.filePresent) {
      return `${head}<div class="settings-empty-state">${i18n.t('cron.heartbeat.fileMissing')}</div>`;
    }
    if (hb.checkingNothing) {
      return `${head}<div class="settings-notice settings-notice-strong">
        <i class="ti ti-alert-triangle"></i>
        <div>${escapeHtml(i18n.t('cron.heartbeat.checkingNothing'))}</div></div>`;
    }
    const tasks = hb.tasks.map(t => {
      let note;
      if (t.state === 'broken') note = i18n.t('cron.heartbeat.taskBroken', { n: t.consecutive });
      else if (t.state === 'pending') note = i18n.t('cron.heartbeat.taskPending');
      else note = i18n.t('cron.heartbeat.taskOk');
      const warned = t.state === 'broken'
        ? ` · ${i18n.t(t.escalated ? 'cron.health.warned' : 'cron.health.notWarned')}`
        : '';
      return `<div class="cron-task cron-task-${t.state}">
        <span class="cron-task-label">${escapeHtml(t.label || String(t.index))}</span>
        <span class="cron-task-note">${escapeHtml(note + warned)}</span>
      </div>`;
    }).join('');
    const orphans = hb.orphans.length
      ? `<div class="settings-subheading">${i18n.t('cron.heartbeat.orphans')}</div>
         <p class="settings-field-hint">${escapeHtml(i18n.t('cron.heartbeat.orphansHint'))}</p>
         ${hb.orphans.map(o => `<div class="cron-task cron-task-orphan">
            <span class="cron-task-label">${escapeHtml(o.label || o.id)}</span>
            <span class="cron-task-note">${escapeHtml(i18n.t('cron.heartbeat.taskBroken', { n: o.consecutive }))}</span>
          </div>`).join('')}`
      : '';
    return `${head}<div class="cron-tasks">${tasks}</div>${orphans}`;
  }

  _wireSections() {
    // Active config fields → auto-save on change
    for (const key of ['max_tokens', 'temperature', 'reasoning_effort', 'context_window_tokens']) {
      const el = this.contentEl.querySelector(`[data-key="${key}"]`);
      if (!el) continue;
      el.addEventListener('change', () => this._debouncedSave(key, el.value));
    }

    this._wireWorkerSettings();

    // I file veri: la scheda nasce vuota e il gestore file ci si aggancia
    // sopra. Fuori da Memoria il contenitore non esiste e il metodo esce
    // subito.
    this._mountFile();

    // Telegram: in cassetto solo la riga. Il widget lo monta `_openTelegram`
    // quando il pannello si apre — prima, il suo contenitore non esiste.
    this._loadTelegramSummary();

    // Skill: stessa forma: la riga si riempie da sé, il pannello all'apertura.
    this._loadSkillsSummary();

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

    /* Ogni marca e' una riga: il tocco apre il suo pannello, dove vivono
       modifica ed elimina. Nel cassetto quei due bottoni non esistono piu'. */
    this.contentEl.querySelectorAll('[data-brand-open]').forEach(row => {
      row.addEventListener('click', () => {
        window.mobileApp?.drawer?.open('brand');
        this._openBrand(row.dataset.brandOpen);
      });
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
    const keepAwakeSeg = this.contentEl.querySelector('#keep-awake-seg');
    if (keepAwakeSeg) {
      const buttons = [...keepAwakeSeg.querySelectorAll('[data-keep-awake]')];
      // `previous` segue l'ultimo valore accettato dal server, non quello del
      // primo render: due cambi di fila con il secondo fallito riporterebbero
      // altrimenti il comando su un modo che non è più quello salvato.
      let previous = buttons.find(b => b.classList.contains('active'))?.dataset.keepAwake;
      // Il costo della scelta sta sotto il comando e segue la selezione
      // subito, prima ancora del salvataggio: è quello che l'utente sta
      // valutando, non la conferma di quello che ha già scelto.
      const costEl = this.contentEl.querySelector('#keep-awake-cost');
      const show = (mode) => {
        if (costEl) costEl.textContent = this._keepAwakeCost(mode);
        buttons.forEach(b => {
          const its = b.dataset.keepAwake === mode;
          b.classList.toggle('active', its);
          b.setAttribute('aria-checked', its ? 'true' : 'false');
        });
      };
      buttons.forEach(btn => btn.addEventListener('click', () => {
        const mode = btn.dataset.keepAwake;
        if (mode === previous) return;
        show(mode);
        api.updatePower({ keep_awake: mode })
          .then(() => {
            previous = mode;
            if (this.data) this.data.power = { ...(this.data.power || {}), keep_awake: mode };
            showToast(i18n.t('settings.battery.keepAwakeSaved'));
          })
          .catch(() => {
            show(previous);  // rollback sull'errore
            showToast(i18n.t('settings.saveError'));
          });
      }));
    }

    // Add provider
    this._wireBtn('btn-add-provider', () => this._showAddProviderDialog());

    // SSH: il blocco si popola da solo (chiamata a parte, v. _renderSsh)
    this._loadSsh();

    // Programmazione: stessa forma, un segnaposto e un caricatore
    this._loadCron();

    // Backup e ripristino
    this._wireBackup();
    /* Ogni riga di riepilogo apre `drawer-<id>`, e chiede al suo gruppo di
       disegnarne il corpo. Una regola sola per tutte le righe: la prossima non
       ha bisogno di cablaggio nuovo. */
    this.contentEl.querySelectorAll('[data-summary]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.summary;
        window.mobileApp?.drawer?.open(id);
        this._OPEN_PANEL[id]?.call(this);
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
  /* Salva una marca. In aggiunta, la finisce.
   *
   * **Una marca senza un modello che funziona non e' una marca che c'e'.**
   * Senza il primo modello, aggiungerne una vorrebbe dire uscire di qui,
   * andare in casa e sceglierne uno: una cosa sola in due posti, che e' il
   * difetto che questo giro esiste per togliere. Il giro iniziale la pensa
   * gia' cosi' — `save_onboarding` pretende provider **e** modello insieme,
   * perche' e' quella coppia a fare una configurazione valida.
   *
   * «Usala adesso» e' un interruttore e non un automatismo: acceso di suo,
   * perche' nove volte su dieci la aggiungi per usarla; spegnibile, perche'
   * la decima aggiungi una marca di scorta — e attivarla d'ufficio
   * cambierebbe chi risponde senza dirlo, con la sorpresa alla risposta
   * successiva.
   */
  async _saveProvider(
    name, format, apiKey, apiBase,
    { keepStoredKey = false, caBundle = '', clearCaBundle = false,
      firstModel = '', useItNow = false } = {},
  ) {
    if (!name || (!apiKey && !keepStoredKey)) {
      showToast(i18n.t('settings.nameAndKeyRequired'), 'error');
      return;
    }
    if (useItNow && !firstModel) {
      showToast(i18n.t('settings.firstModelRequired'), 'error');
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

    /* La seconda scrittura, e solo se l'hai chiesto. Separata dalla prima
       perche' sono due rotte diverse — la marca sta nel config dei provider,
       chi risponde negli `agents.defaults` — e perche' se questa fallisce la
       marca resta comunque salvata: quel che si perde e' l'attivazione, non
       il lavoro di compilare cinque campi. */
    if (useItNow && firstModel) {
      try {
        await api.updateSettings({ model: firstModel, default_provider: name });
      } catch (e) {
        this._closeProviderDialog();
        showToast(i18n.t('settings.providerSavedNotActive', { error: e.message }), 'error');
        this.loadSettings();
        return;
      }
    }

    this._closeProviderDialog();
    showToast(useItNow ? i18n.t('settings.providerSavedAndActive') : i18n.t('settings.providerSaved'));
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
        this._realignPanel('brand', name, false, null);
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
        ${isEdit ? '' : `
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.firstModel')}</label>
          <input type="text" class="settings-input" id="dlg-first-model"
            placeholder="${i18n.t('settings.firstModelPlaceholder')}" autocomplete="off" value="" />
          <span class="settings-field-hint">${i18n.t('settings.firstModelHint')}</span>
        </div>
        <div class="settings-field settings-toggle-row">
          <label class="settings-label" for="dlg-use-now">${i18n.t('settings.useNow')}</label>
          <label class="toggle-switch">
            <input type="checkbox" id="dlg-use-now" checked>
            <span class="toggle-slider"></span>
          </label>
        </div>`}
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
        /* Solo in aggiunta, mai in modifica: cambiare l'endpoint di una marca
           gia' in uso non deve poter cambiare anche chi risponde. */
        firstModel: isEdit ? '' : (dialog.querySelector('#dlg-first-model')?.value.trim() || ''),
        useItNow: !isEdit && !!dialog.querySelector('#dlg-use-now')?.checked,
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
