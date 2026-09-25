/** View Title Controller — in-content view headings and actions.
 *
 * Replaces the old fixed 40px header: each view owns a `.view-title-mount`
 * (officina.html) where the big scrolling-style title and its action buttons
 * are rendered. Only onboarding has no mount: la chat ne ha uno dal
 * 21/09/2026 — v. `chat` in `modeConfigs`.
 */

import { i18n } from './shared/i18n.js';
import { api } from './shared/api-client.js';
import { escapeHtml } from './shared/utils.js';
import { VISTA_DI, elementoTitolo } from './mobile-settings.js';

/** Il pill «Jenny»: l'unica porta dell'officina verso la casa.
 *
 *  Una funzione e non una costante, e la ragione e' la stessa del 21/09/2026:
 *  **le stringhe qui dentro vanno lette quando si disegna, non quando il file
 *  si carica.** Il pill e' l'unica azione dell'officina che porta una parola
 *  visibile invece di una sola icona, quindi e' anche l'unica in cui una
 *  traduzione letta troppo presto si vede a schermo — e infatti si e' vista:
 *  «officina.casaPill» scritto per esteso dentro il bottone.
 */
function pillCasa() {
  return {
    icon: 'ti-home',
    title: i18n.t('casa.backHome'),
    action: 'go-casa',
    pill: i18n.t('officina.casaPill'),
  };
}

/** L'intestazione di un cassetto dell'officina.
 *
 *  `eyebrow` e `sub` sono le due righe che la tavola mette attorno al nome; il
 *  bottone `home` e' il pill «Jenny» che riporta in casa — la stessa
 *  destinazione del tasto in fondo a «Sistema», che da qui in poi e' un
 *  doppione e se ne va (`_renderSystem`).
 */
function cassetto(nome) {
  return {
    eyebrow: i18n.t('officina.eyebrow'),
    title: i18n.t(`nav.${nome}`),
    sub: i18n.t(`officina.sub.${nome}`),
    /* Niente «aggiorna»: la tavola non ce l'ha, e non serve — `activate()`
       ricarica a ogni apertura del cassetto, e ogni salvataggio ridisegna. Un
       bottone che rifa' quel che e' appena successo insegna a premerlo per
       scaramanzia, e occupa il posto accanto all'unico che porta da qualche
       parte. */
    actions: [pillCasa()],
  };
}

/** L'intestazione della Console — la chat dell'officina.
 *
 *  Quella di un cassetto senza soprascritta ne' sottotitolo (la vista sta gia'
 *  dentro l'officina, e dirglielo di nuovo non aggiunge niente), e il nome e'
 *  «Console», la stessa stringa della voce del dock (`nav.console` in
 *  officina.html) — una parola sola per due posti, cosi' non possono divergere.
 *
 *  Come `cassetto`, e' una funzione perche' si ricostruisce intera a ogni
 *  cambio di lingua: riassegnare il solo titolo lasciava il pill con la
 *  stringa letta al caricamento del file, cioe' la chiave grezza.
 */
function consolle() {
  return {
    title: i18n.t('nav.console'),
    actions: [pillCasa()],
  };
}

/* Le due viste rimaste fuori dai cassetti. Funzioni per la stessa ragione di
   `consolle`: nel costruttore `i18n.load()` non e' ancora tornato, e i titoli
   delle azioni (che sono tooltip ed etichetta per il lettore di schermo)
   restavano le chiavi grezze — `_refreshTitles` riscriveva solo `title`. */

/* Questa vista e' **un file aperto**, da quando l'esploratore e' una scheda
   di Memoria. «Aggiorna» qui non aggiornava gia' niente (il ramo usciva subito
   in modalita' editor) e «nuovo» crea file nella cartella che si sta
   guardando, che ora si guarda altrove: il bottone e' andato accanto alle
   briciole, dentro la scheda. Resta la freccia indietro. */
function fileAperto() {
  return {
    title: i18n.t('nav.workspace'),
    actions: [{ icon: 'ti-arrow-left', title: i18n.t('header.back'), action: 'ws-back' }],
  };
}

function impostazioni() {
  return {
    title: i18n.t('nav.settings'),
    actions: [{ icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' }],
  };
}

export class ViewTitleController {
  constructor() {
    this.currentMode = null;
    this.titleEl = null;
    this.actionsEl = null;
    this.modeConfigs = {
      /* La chat. Fino al 21/09/2026 era l'unica vista dell'officina a partire
         dal bordo dello schermo: nessun titolo, e nessuna via verso casa che
         non passasse da un altro cassetto. V. `consolle()`. */
      chat: consolle(),
      /* Qui c'era anche `apps`, la scheda uscita il 21/09/2026 col suo
         «mostra app nascoste»: nessun modo la raggiunge piu'. */
      workspace: fileAperto(),
      settings: impostazioni(),
      /* I tre cassetti. Stessa vista e stesso mount (`title-settings`, via
         `VISTA_DI`), titolo e sottotitolo diversi.

         Il sottotitolo e' la differenza che si vede di piu' rispetto a prima:
         un cassetto che si apre su quattro righe chiuse non dice a cosa serve,
         e «Cervello» da solo nemmeno. La tavola mette una riga sotto il nome —
         `officina` sopra, il nome in serif, la riga che spiega — ed e' quella
         riga a trasformare quattro etichette in una pagina. */
      cervello: { ...cassetto('cervello') },
      mani: { ...cassetto('mani') },
      memoria: { ...cassetto('memoria') },
    };
  }

  _refreshTitles() {
    /* Intera, non il solo titolo: il pill porta una parola visibile, e
       riassegnare `title` lasciava quella com'era al caricamento del file. */
    this.modeConfigs.chat = consolle();
    this.modeConfigs.workspace = fileAperto();
    this.modeConfigs.settings = impostazioni();
    /* I tre cassetti hanno tre stringhe a testa (soprascritta, nome,
       sottotitolo) piu' il pill: si ricostruiscono interi invece di
       riassegnarne una per volta, che e' il modo in cui se ne dimentica una. */
    for (const nome of Object.keys(VISTA_DI)) this.modeConfigs[nome] = cassetto(nome);
    if (this.currentMode) this.setMode(this.currentMode);
  }

  /* Il mount di un modo e' `title-<modo>`, **tranne** per i tre cassetti, che
     condividono la vista delle impostazioni e quindi il suo mount. Senza
     questa riga `setMode('cervello')` cercava `title-cervello`, non lo
     trovava, e usciva lasciando i cassetti senza intestazione. */
  _mount(mode) {
    return elementoTitolo(mode);
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

    /* `eyebrow` e `sub` sono facoltativi: le viste che non li dichiarano
       disegnano esattamente l'intestazione di prima. `textContent` e non
       interpolazione perche' sono stringhe tradotte, non markup. */
    mount.innerHTML = '<div class="view-title">' +
      '<div class="view-title-stack">' +
        '<div class="view-title-eyebrow"></div>' +
        '<h1 class="view-title-text"></h1>' +
        '<div class="view-title-sub"></div>' +
      '</div>' +
      '<div class="view-title-actions"></div>' +
      '</div>';
    this.titleEl = mount.querySelector('.view-title-text');
    this.actionsEl = mount.querySelector('.view-title-actions');

    const eyebrowEl = mount.querySelector('.view-title-eyebrow');
    const subEl = mount.querySelector('.view-title-sub');
    eyebrowEl.textContent = config.eyebrow || '';
    eyebrowEl.hidden = !config.eyebrow;
    subEl.textContent = config.sub || '';
    subEl.hidden = !config.sub;

    this.titleEl.textContent = customTitle || config.title;
    this.renderActions(config.actions);
  }


  /** Accende un'azione, ma solo se chi la accende possiede ancora la modalità
   *  corrente.
   *
   *  La stessa guardia stava anche su `setTitle`, che scriveva il titolo della
   *  vista dopo un `await`. Quel metodo se n'è andato il 21/09/2026 con i suoi
   *  unici chiamanti — wiki e grafo, usciti dall'officina — e il titolo oggi lo
   *  scrive solo `setMode`, che è sincrono e non può sbagliare vista. La
   *  ragione della guardia però è la stessa, ed è questa:
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
      const dangerClass = action.danger ? ' ibtn-danger' : '';
      const hiddenStyle = action.hidden ? ' style="display:none"' : '';
      /* Un'azione con `pill` non e' un'icona nuda ma icona + parola, come il
         bottone «Jenny» della tavola. Serve quando la destinazione non si
         indovina dall'icona: una casetta puo' voler dire tante cose, «Jenny»
         una sola. */
      if (action.pill) {
        return `<button class="ibtn ibtn-action ibtn-pill${dangerClass}" data-action="${action.action}" title="${action.title}"${hiddenStyle}>
          <i class="ti ${action.icon}"></i><span>${escapeHtml(action.pill)}</span>
        </button>`;
      }
      return `<button class="ibtn ibtn-action${dangerClass}" data-action="${action.action}" title="${action.title}"${hiddenStyle}>
        <i class="ti ${action.icon}"></i>
      </button>`;
    }).join('');

    this.actionsEl.innerHTML = html;
    this.wireActions();
  }

  wireActions() {
    if (!this.actionsEl) return;
    this.actionsEl.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.action;
        this.handleAction(action);
      });
    });
  }

  handleAction(action) {
    const app = window.mobileApp;
    /* La stessa destinazione del vecchio tasto in fondo a «Sistema»: la casa e'
       un documento a parte, quindi si naviga, non si cambia vista. */
    if (action === 'go-casa') {
      api.navigate('/html-mobile/index.html');
      return;
    }
    const controller = app.controllers[app.currentMode];
    if (controller && controller.handleAction) {
      controller.handleAction(action);
    }
  }
}
