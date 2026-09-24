/** Mobile Chat Controller — full-featured chat with markdown, thinking, tool calls. */

import { wsManager } from './shared/ws-manager.js';
import { HistoryPager } from './shared/history-pager.js';
import { describeWireError } from './shared/wire-error.js';
import { api } from './shared/api-client.js';
import { copyToClipboard, escapeHtml, showToast } from './shared/utils.js';
import { sessionManager } from './shared/session-manager.js';
import { scopeChip } from './shared/scope-chip.js';
import { writeSwitch } from './shared/write-switch.js';
import { ImageHandler } from './shared/image-handler.js';
import { openImageLightbox } from './shared/image-lightbox.js';
import { renderRich } from './shared/rich-content.js';
import { i18n } from './shared/i18n.js';
import { getProviderBrand } from './shared/provider-brand.js';
import { confirmDialog, detailDialog } from './shared/dialog.js';
import { commandsChip } from './shared/commands-chip.js';
import { isTypeAheadKey } from './shared/type-ahead.js';
import { hasSelection, selectionInside, onSelectionChange } from './shared/selection.js';
import {
  saActions,
  saActivityFrame,
  saActivityIngest,
  saActivityInit,
  saActivityRows,
  saDigestView,
  saIsTerminal,
  saVisibleCards,
} from './shared/subagent-policy.js';

const TOOL_ICONS = {
  start: 'ti-loader-2',
  end: 'ti-check',
  error: 'ti-x',
};

/* Quante ancore chiede una pagina all'indietro. La *prima* pagina ne chiede 160
   e quel numero sta nella sua chiamata: v. il commento in `loadInitialHistory`. */
const HISTORY_PAGE_SIZE = 120;

/* Icona per kind di attività di un subagent. Tabella e non if/else perché è la
   sola cosa che distingue una riga dall'altra a colpo d'occhio: su un telefono
   tenuto in una mano si legge l'icona prima del testo, e "sta pensando" contro
   "ha finito un tool" deve essere una differenza di forma, non di lettura.
   `thinking` ha due icone perché ha due etichette dal server (ragionamento e
   testo che si sta formando): sono due attività diverse per chi guarda. */
const SA_EVENT_ICONS = {
  tool: 'ti-tool',
  thinking: 'ti-brain',
  writing: 'ti-pencil',
  iteration: 'ti-repeat',
  phase: 'ti-flag',
  message_in: 'ti-mail',
  result: 'ti-circle-check',
  error: 'ti-alert-triangle',
};

function initMarked() {
  if (window._markedReady) return;
  if (typeof marked === 'undefined') return;

  const renderer = new marked.Renderer();

  renderer.code = function ({ text, lang }) {
    const hasHljs = typeof hljs !== 'undefined';
    const language = lang && hasHljs && hljs.getLanguage(lang) ? lang : null;
    let highlighted;
    try {
      if (hasHljs) {
        highlighted = language
          ? hljs.highlight(text, { language }).value
          : hljs.highlightAuto(text).value;
      } else {
        highlighted = escapeHtml(text);
      }
    } catch {
      highlighted = escapeHtml(text);
    }
    const langLabel = language || 'text';
    return `<div class="chat-code-block">` +
      `<div class="chat-code-header">` +
        `<span class="chat-code-lang">${langLabel}</span>` +
        `<button class="chat-code-copy" type="button">${i18n.t('chat.copy')}</button>` +
      `</div>` +
      `<pre><code class="hljs language-${langLabel}">${highlighted}</code></pre>` +
    `</div>`;
  };

  marked.setOptions({
    renderer,
    gfm: true,
    breaks: true,
  });

  window._markedReady = true;
}

// C1: copy handled via event-delegation (see setupEventListeners) instead of
// an inline onclick, which DOMPurify strips from the sanitized markdown.
async function copyCodeFromButton(btn) {
  const pre = btn.closest('.chat-code-block')?.querySelector('pre code');
  if (!pre) return;
  // `copyToClipboard` e non `navigator.clipboard.writeText` nudo: quello non
  // esiste in ogni WebView, e senza `.catch` il pulsante restava muto mentre
  // la promise veniva rifiutata. Ora un fallimento lo dice.
  if (!(await copyToClipboard(pre.textContent))) {
    showToast(i18n.t('chat.copyFailed'), 'error');
    return;
  }
  btn.textContent = i18n.t('chat.copied');
  btn.classList.add('copied');
  setTimeout(() => {
    btn.textContent = i18n.t('chat.copy');
    btn.classList.remove('copied');
  }, 2000);
}

function renderMarkdown(text) {
  initMarked();
  if (typeof marked !== 'undefined') {
    try {
      // C1: sanitize model-generated HTML before it reaches innerHTML.
      // DOMPurify defaults already preserve highlight.js `class`, GFM tables,
      // <a href> (http/https/relative), <img>, and <pre class="mermaid">,
      // so no ADD_TAGS/ADD_ATTR are required.
      // Fail SAFE, not open: if the sanitizer vendor failed to load, degrade to
      // escaped plain text rather than injecting unsanitized HTML.
      if (typeof DOMPurify === 'undefined') return escapeHtml(text);
      return DOMPurify.sanitize(marked.parse(text));
    } catch (e) {
      console.error('Markdown parse error:', e);
      return escapeHtml(text);
    }
  }
  return escapeHtml(text);
}

/** Formule e diagrammi dentro una bolla appena disegnata.
 *
 *  Era `renderKaTeX`, e chiamava KaTeX per conto proprio. Il 21/09/2026 le
 *  librerie sono state cancellate «perche' le usava solo la wiki» — falso:
 *  questi quattro punti le usavano — e la funzione, che cominciava con «se la
 *  libreria c'e'», ha smesso di fare qualcosa **senza dirlo**. Adesso il come
 *  sta in un posto solo (`shared/rich-content.js`), condiviso col lettore delle
 *  pagine e con la chat di casa: e' l'unica forma in cui una libreria non puo'
 *  perdere meta' dei suoi lettori senza che nessuno se ne accorga.
 *
 *  **Niente `$...$` in riga, qui.** In chat si parla di prezzi, e «costa $5,
 *  forse $10» diventerebbe un tentativo di matematica. Nelle pagine della wiki,
 *  dove la skill impone `$f(x)$`, il lettore lo accende.
 */
function renderRichContent(container) {
  renderRich(container);
}

export class ChatController {
  constructor() {
    this.chatArea = document.getElementById('chat-area');
    this.identityEl = null;
    this.identityStatus = null;
    this._ensureIdentity();
    this.input = document.getElementById('chat-input');
    this.sendBtn = document.getElementById('btn-send');
    this.secondaryActions = document.getElementById('secondary-actions');

    this._deltaBuffer = '';
    /* Il sorgente markdown di ogni bolla, registrato dove la bolla nasce.
       `innerText` non basta come unica fonte: perde le recinzioni dei blocchi
       di codice e il loro linguaggio. WeakMap perché quando la vista viene
       buttata (`chatArea.innerHTML = ''`) le voci se ne vanno da sole. */
    this._msgSource = new WeakMap();
    this._reasoningBuffer = '';
    // Un turno può avere più segmenti di ragionamento (uno per richiesta al
    // modello): il flag segna il confine, non la fine.
    this._reasoningSegmentClosed = false;
    // Sticky bottom del box del ragionamento, gemello di `_autoScroll` per la
    // chat: vero finché l'utente non risale a leggere dentro al box.
    this._thinkStick = true;
    // Rendering dello streaming coalizzato a un frame: i delta aggiornano
    // sempre il buffer, ma il re-parse markdown (costoso, O(n) sul buffer
    // intero) avviene al massimo una volta per requestAnimationFrame. Evita
    // di saturare il main thread — e quindi di affamare il setInterval che
    // anima la mascotte — quando l'agente scrive muri di testo.
    this._pendingFrame = null;
    this._deltaDirty = false;
    this._reasoningDirty = false;
    this._currentMsg = null;
    this._currentThinking = null;
    this._currentContent = null;
    // Turno a cui appartiene la bolla che stiamo componendo (v.
    // `_applyTurnBoundary`). null = nessun turno in corso.
    this._currentTurnId = null;
    this._toolStates = {};
    this._goalBanner = null;
    this._goalTimer = null;

    /* Cursore, chiavistello e bottone della pagina precedente stanno nel modulo
       condiviso con la casa (`shared/history-pager.js`): qui restano gli
       appigli, cioe' le tre cose che i due gusci fanno in modo diverso — dove si
       ascolta lo scroll, come si chiede una pagina, come la si disegna.
       `historyCursor` / `hasMoreHistory` / `isLoadingHistory` restano leggibili
       col loro nome (v. gli accessori sotto): li legge il resync della
       riconnessione, e i test li nominano. */
    /* L'ultimo invio, finché il gateway non ha dimostrato di averlo preso.
       `null` = non c'è niente da riprendere. */
    this._pendingSend = null;
    this._pager = new HistoryPager({
      // Lo scroller e' il documento, ma l'evento `scroll` arriva a `window`:
      // qui le due cose non coincidono (v. il getter `_scroller`).
      scroller: () => this._scroller,
      listenOn: window,
      container: () => this.chatArea,
      pageSize: HISTORY_PAGE_SIZE,
      begin: () => this._beginHistoryPage(),
      prepend: (messages) => this._renderThreadMessagesToTop(messages),
      mount: (node) => this._insertAtTop(node),
      label: () => i18n.t('chat.loadPrevious'),
    });
    this._initialHistoryLoaded = false;
    /* Un caricamento iniziale è *in volo*, che non è la stessa cosa di
       `_initialHistoryLoaded` (quello è un latch: resta alzato dopo la fine, e
       torna giù su un fallimento). Serve al resync della riconnessione, che
       altrimenti butta la vista mentre una fetch sta per riempirla e ne fa
       partire una seconda: due risposte, un solo schermo, il thread scritto due
       volte. */
    this._loadingInitialHistory = false;

    this.imageHandler = new ImageHandler();
    this.imageHandler.onChange = (images) => this._renderAttachPreview(images);
    /* Un allegato che sfora i tetti: un toast e non una riga nel filo, perche'
       non e' un fatto della conversazione — e' una risposta a quel che stai
       facendo adesso nel composer, e se ne va da sola come il gesto. Le
       parole sono le stesse di un rifiuto del gateway: e' la stessa cosa
       detta un istante prima. */
    this.imageHandler.onReject = (reason) => {
      showToast(describeWireError({ reason }, (key) => i18n.t(key)).text, 'error');
    };

    this._voiceTimerInterval = null;

    this._autoScroll = true;
    this._userTouching = false;
    this._scrollThreshold = 60;
    // Ancora della posizione di lettura, come distanza dal fondo
    // (scrollHeight - scrollTop): a differenza di scrollTop resta valida anche
    // se nel frattempo arrivano messaggi, che allungano il contenuto in coda.
    // null = mai misurata (prima attivazione).
    this._scrollAnchor = null;
    this._unreadCount = 0;
    this._fabEl = document.getElementById('chat-scroll-fab');
    this._wsListenersBound = false;
    this._active = false;

    this._runtimeModel = null;
    this._sessionInfoPopover = null;
    this._sessionInfoTimer = null;
    this._fileEditPaths = new Map();

    // Pannello subagent: lo stato arriva dal frame WS `subagent_status` a ogni
    // transizione, e dal polling di /api/subagents mentre qualcosa gira (fra
    // due transizioni elapsed/idle invecchierebbero senza che nessuno lo dica).
    this.subagentsEl = document.getElementById('subagents');
    this.subagentsBody = document.getElementById('subagents-body');
    this.subagentsHead = document.getElementById('subagents-head');
    this.subagentsCount = document.getElementById('subagents-count');
    this._subagentSnapshot = { running: [], recent: [] };
    this._subagentsOpen = false;
    this._subagentPollTimer = null;
    // Sveglia dell'ultimo ri-render di una card terminale (v. _scheduleSubagentExpiry).
    this._subagentExpiryTimer = null;
    this._lastStalledIds = '';
    // Task id visti *vivi* in questo turno. È il filtro che tiene il pannello
    // sul lavoro vivo: una voce terminale si mostra solo se la sua transizione
    // è stata osservata qui, mai perché il server la serve in `recent`
    // (v. shared/subagent-policy.js::saVisibleCards).
    this._subagentLiveIds = new Set();
    this._subagentHasRunning = false;
    // Stream di attività della modale (uno solo: la modale è una). `null` = modale
    // chiusa, che è anche la condizione per cui il gateway non spinge nulla.
    this._saStream = null;
    this._saWatching = false;
    this._saStatus = 'idle';
    this._saStick = true;
    this._saResyncing = false;
    // Task per cui il blocco "cosa ha fatto davvero" è già in chat: la transizione
    // terminale la vediamo una volta, ma `_renderSubagents` gira a ogni poll.
    this._saDigestSeen = new Set();
    this._setupSubagentPanel();

    i18n.load(i18n.locale).then(() => this._updatePlaceholders());

    this.setupEventListeners();
    this.setupInfiniteScroll();
    this.setupWebSocket();
    this.ready = this._initOnSessionReady();
    this._initSessionInfo();
  }

  /** Identity line — first scrollable element of the chat (replaces the fixed header). */
  _ensureIdentity() {
    if (this.identityEl && this.chatArea.contains(this.identityEl)) return;
    const el = document.createElement('div');
    el.className = 'chat-identity';
    el.innerHTML = '<span class="chat-identity-flower">✿</span>' +
      '<span class="chat-identity-name">' + i18n.t('chat.jenny') + '</span>' +
      '<span class="chat-identity-status"></span>' +
      '<span class="chat-identity-label"></span>';
    this.chatArea.insertBefore(el, this.chatArea.firstChild);
    this.identityEl = el;
    this.identityStatus = el.querySelector('.chat-identity-status');
    this.identityLabel = el.querySelector('.chat-identity-label');
  }

  _insertAtTop(node) {
    this._ensureIdentity();
    this.chatArea.insertBefore(node, this.identityEl.nextSibling);
  }

  _setConnectionStatus(connected) {
    this._ensureIdentity();
    this.identityStatus.classList.toggle('on', connected);
    this.identityStatus.classList.toggle('off', !connected);
    if (this.identityLabel) this.identityLabel.textContent = connected ? i18n.t('chat.online') : i18n.t('chat.offline');
  }

  _updatePlaceholders() {
    const input = document.getElementById('chat-input');
    if (input) input.placeholder = i18n.t('chat.placeholder');
    const attachBtn = document.getElementById('btn-attach');
    if (attachBtn) attachBtn.title = i18n.t('chat.attach');
    const newChatBtn = document.getElementById('btn-new-chat');
    if (newChatBtn) newChatBtn.title = i18n.t('chat.newChat');
  }

  setupEventListeners() {
    this.sendBtn.addEventListener('click', () => this.sendMessage());
    document.getElementById('btn-attach').addEventListener('click', () => {
      this.imageHandler.trigger();
    });
    document.getElementById('btn-new-chat')?.addEventListener('click', () => {
      this.startNewChat();
    });
    /* La tendina dei comandi si accende qui e non in `mobile-app.js` come le
       altre due della riga: quelle hanno bisogno dello stato di sessione, questa
       ha bisogno solo di qualcuno a cui consegnare la scelta — cioè di questo
       controller. */
    commandsChip.onPick = (spec) => this.runCommand(spec);
    commandsChip.init();


    // Textarea auto-resize + send enable/disable + hide secondary actions
    this.input.addEventListener('input', () => {
      this._autoResize();
      this._updateSendState();
      this._updateActions();
    });

    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    // Sticky bottom stile WhatsApp: l'autoscroll segue lo stream solo se l'utente
    // è a fondo chat. Nessun guard sugli scroll programmatici: assegnano scrollTop
    // al fondo in modo istantaneo, quindi il loro evento ricalcola comunque
    // _autoScroll = true e non serve distinguerli da quelli dell'utente.
    // Lo scroller è il documento (v. `_scroller`): l'evento arriva a `window`.
    // Fuori dalla chat `html` è `overflow: hidden` e non scorre, quindi il
    // listener non scatta a vuoto sulle altre viste.
    window.addEventListener('scroll', () => {
      this._autoScroll = this._isNearBottom();
      this._updateScrollFab();
      this._rememberScrollAnchor();
    }, { passive: true });

    // Mentre il dito è giù non va MAI eseguito uno scroll programmatico
    // (combatterebbe il gesto: durante lo streaming il flush gira ogni frame).
    // Sulla vista intera, non su `.chat-area`: con lo scroller documento un
    // dito che parte dal composer scorre la chat come uno che parte dal testo.
    const touchSurface = document.getElementById('view-chat') || this.chatArea;
    touchSurface.addEventListener('touchstart', () => {
      this._userTouching = true;
    }, { passive: true });
    const onTouchDone = () => {
      this._userTouching = false;
      this._autoScroll = this._isNearBottom();
      this._updateScrollFab();
    };
    touchSurface.addEventListener('touchend', onTouchDone, { passive: true });
    touchSurface.addEventListener('touchcancel', onTouchDone, { passive: true });

    // Tastiera: la finestra si restringe e il fondo della chat finirebbe
    // sotto il composer. Chi era in fondo ci resta.
    window.visualViewport?.addEventListener('resize', () => {
      if (this._autoScroll) this.scrollToBottom(true);
    });

    // Rotella/tastiera (Titan 2 emette wheel dalla rotella capacitiva): un colpo
    // verso l'alto stacca subito, senza aspettare che superi la soglia dei 60px.
    this.chatArea.addEventListener('wheel', (e) => {
      if (e.deltaY < 0) {
        this._autoScroll = false;
        this._updateScrollFab();
      }
    }, { passive: true });

    if (this._fabEl) {
      this._fabEl.addEventListener('click', () => {
        this._autoScroll = true;
        this._unreadCount = 0;
        this.scrollToBottom(true);
      });
    }

    // Type-ahead focus: su device con tastiera fisica (Titan 2) il focus si perde
    // facilmente dall'input (tap su bolla/link/pulsante, scroll, ritorno da un'altra
    // vista) e i caratteri digitati vanno persi. Se l'utente inizia a scrivere un
    // carattere stampabile mentre la chat è attiva e il focus non è già in un campo
    // editabile, riportiamo il focus sull'input così il tasto ci finisce dentro.
    // Nota: agisce SOLO in reazione a un tasto fisico premuto, quindi non forza mai
    // la tastiera virtuale a comparire (non c'è keydown senza input già a fuoco).
    document.addEventListener('keydown', (e) => this._maybeTypeAheadFocus(e));

    /* Quando la selezione cade, il rendering congelato da `_flushRender`
       riparte: un frame e il testo è di nuovo allineato al buffer. */
    onSelectionChange((active) => {
      if (!active && (this._deltaDirty || this._reasoningDirty)) this._scheduleFlush();
    });

    // C1: delegated copy handler for code-block buttons (replaces inline onclick).
    this.chatArea.addEventListener('click', (e) => {
      // Un link dentro il markdown di una risposta è una navigazione di main
      // frame vera: nessun renderer riscrive gli `a` e DOMPurify conserva gli
      // href relativi. `[report](note.md)` o `[cerca](www.google.com)` risolvono
      // sull'origine del gateway, quindi la SPA verrebbe ricaricata SENZA il
      // fragment `#bs=` (de-autenticata: il segreto si legge una volta sola al
      // module-load) o, sotto `/api/`, sostituita da un 404 JSON — e con lei
      // spariscono `window.mobileApp`, il tasto Indietro e il dock. Il ramo va
      // in testa: qualunque altro handler viene dopo.
      // `e.defaultPrevented` distingue i link del modello da quelli che la chat
      // costruisce e cabla da sé (`.file-preview-action`, `.chat-file-path-link`):
      // i loro listener diretti girano in fase target e annullano già il click,
      // quindi quando la bolla arriva qui il click ha un padrone. Senza questo
      // controllo "Apri nell'editor" funzionava ma mostrava anche il toast del
      // link inerte, perché `#workspace` non è un'ancora della conversazione.
      const link = e.target.closest('a[href]');
      if (link && this.chatArea.contains(link)) {
        if (!e.defaultPrevented) this._handleContentLink(e, link);
        return;
      }
      const btn = e.target.closest('.chat-code-copy');
      if (btn && this.chatArea.contains(btn)) { copyCodeFromButton(btn); return; }
      // Riga di azioni della bolla. Delegato come tutto il resto: la CSP della
      // shell è `script-src 'self'`, niente `onclick` inline.
      const msgAction = e.target.closest('.chat-msg-action');
      if (msgAction && this.chatArea.contains(msgAction)) {
        this._copyMessage(msgAction.closest('.chat-msg'));
        return;
      }
      // Tap su un'immagine (media allegato o immagine markdown inline) → lightbox.
      const img = e.target.closest('img');
      if (img && this.chatArea.contains(img)) this._openLightbox(img.currentSrc || img.src, img.alt || '');
    });
  }

  /** Disciplina dei link dentro il markdown della chat. Tre esiti, e in nessuno
      dei tre il main frame naviga (v. il commento nel listener della chatArea):
      - ancora interna (`#id`) → scroll all'elemento, restando nella pagina;
      - http/https verso un'ALTRA origine (e mailto:/tel:) → apertura fuori dalla
        WebView, che è quel che fa già il guscio nativo per i link esterni;
      - tutto il resto — href relativi (che risolvono sull'origine del gateway),
        stessa origine, schemi non navigabili — → inerte, con un avviso, perché
        aprirlo dentro la WebView significherebbe perdere la SPA. */
  _handleContentLink(e, a) {
    e.preventDefault();
    const raw = a.getAttribute('href') || '';
    if (raw.startsWith('#')) { this._scrollToChatAnchor(raw.slice(1)); return; }
    let url = null;
    try { url = new URL(raw, window.location.href); } catch (_) { url = null; }
    const scheme = url?.protocol || '';
    const isWeb = scheme === 'http:' || scheme === 'https:';
    if ((isWeb && url.origin !== window.location.origin) || scheme === 'mailto:' || scheme === 'tel:') {
      this._openOutsideWebView(url.href);
      return;
    }
    showToast(i18n.t('common.linkNotOpenable'), 'info');
  }

  /** Scroll a un'ancora della conversazione. La ricerca è ristretta alla chat:
      un id qualsiasi della SPA (dock, drawer, dialog) non è un bersaglio
      legittimo per un link scritto dal modello. */
  _scrollToChatAnchor(id) {
    if (!id) return;
    let target = null;
    try {
      target = this.chatArea.querySelector(`#${CSS.escape(id)}, [name="${CSS.escape(id)}"]`);
    } catch (_) { target = null; }
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else showToast(i18n.t('common.linkNotOpenable'), 'info');
  }

  /** Apre un URL fuori dalla WebView. `window.open` qui non apre una finestra:
      la WebView non supporta le finestre multiple, quindi la richiesta ricade su
      `shouldOverrideUrlLoading`, che per un'origine non-gateway apre una Chrome
      Custom Tab (MainActivity#openExternalUrl) e lascia la SPA dov'è. Il bridge
      JennyNative oggi non espone un metodo per gli URL esterni: se ne verrà
      aggiunto uno, va provato qui per primo. */
  _openOutsideWebView(href) {
    try {
      window.open(href, '_blank', 'noopener');
    } catch (err) {
      console.warn('Could not open external link:', err);
      showToast(i18n.t('common.linkNotOpenable'), 'error');
    }
  }

  /** Overlay fullscreen per un'immagine: tap-per-zoom, tap sullo sfondo / Esc per chiudere. */
  _openLightbox(src, alt) {
    openImageLightbox(src, { alt, closeLabel: i18n.t('chat.close') || 'Close' });
  }

  /** Renderer condiviso degli allegati media (live + history), per tipo:
      image → <img> (lightbox via delegazione), video → <video>, altro → chip
      file che si apre col viewer di sistema via bridge nativo. */
  _renderMediaAttachments(msgNode, entries) {
    if (!entries?.length) return;
    const media = document.createElement('div');
    media.className = 'chat-media';
    for (const raw of entries) {
      const entry = typeof raw === 'string' ? { url: raw } : raw;
      if (!entry.url) continue;
      const name = entry.name || '';
      const kind = entry.kind || this._mediaKindFromName(name);
      if (kind === 'image') {
        const img = document.createElement('img');
        img.src = entry.url;
        img.loading = 'lazy';
        img.alt = name;
        img.title = name;
        media.appendChild(img);
      } else if (kind === 'video') {
        const video = document.createElement('video');
        video.src = entry.url;
        video.controls = true;
        video.preload = 'metadata';
        video.title = name;
        media.appendChild(video);
      } else {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chat-file-chip';
        chip.innerHTML = '<span class="chat-file-chip-icon">📄</span>' +
          '<span class="chat-file-chip-name"></span>';
        chip.querySelector('.chat-file-chip-name').textContent = name || 'file';
        chip.addEventListener('click', () => this._openMediaFile(entry));
        media.appendChild(chip);
      }
    }
    if (media.childElementCount) msgNode.appendChild(media);
  }

  _mediaKindFromName(name) {
    const ext = (name.match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || '';
    if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'].includes(ext)) return 'image';
    if (['mp4', 'mov', 'webm'].includes(ext)) return 'video';
    return 'file';
  }

  /** Apre un allegato non renderizzabile inline: sul telefono il bridge nativo
      (ACTION_VIEW con il viewer di sistema), fuori dalla WebView l'URL firmato
      in una scheda nuova.
   *
   *  `window.open` era il ripiego di *entrambi* i casi, e sotto Android non è
   *  un ripiego: la WebView non ha alcun `DownloadListener` e blocca
   *  l'apertura in una scheda nuova, quindi quando il bridge falliva il tap
   *  sul chip non produceva niente — nessun viewer, nessun errore, nessun
   *  segno che fosse successo qualcosa. Con il bridge presente il fallimento è
   *  un errore da dire, non da sostituire con un'apertura che non avverrà. */
  _openMediaFile(entry) {
    const bridge = window.JennyNative;
    if (bridge && typeof bridge.openFile === 'function') {
      try {
        if (entry.path && bridge.openFile(entry.path)) return;
      } catch (e) {
        console.warn('Native openFile failed:', e);
      }
      showToast(i18n.t('chat.couldNotOpen', { path: entry.name || entry.path || '' }), 'error');
      return;
    }
    window.open(entry.url, '_blank');
  }

  _autoResize() {
    this.input.style.height = 'auto';
    this.input.style.height = this.input.scrollHeight + 'px';
  }

  _updateSendState() {
    const hasText = this.input.value.trim().length > 0;
    this.sendBtn.disabled = !hasText;
    if (hasText) {
      this.sendBtn.classList.add('enabled');
    } else {
      this.sendBtn.classList.remove('enabled');
    }
  }

  _updateActions() {
    const hasText = this.input.value.trim().length > 0;
    this.secondaryActions.classList.toggle('hidden', hasText);
  }

  /**
   * Riporta il focus sull'input di chat quando l'utente inizia a digitare "nel
   * vuoto". Chiamato dall'handler keydown globale (vedi setupEventListeners).
   */
  _maybeTypeAheadFocus(e) {
    if (!this._active) return;
    // Le quattro guardie (carattere stampabile singolo, niente spazio, niente
    // combo, nessun furto a chi sta già scrivendo) vivono in
    // `shared/type-ahead.js`: il cassetto ha lo stesso bisogno, e una seconda
    // copia sarebbe divergita proprio sul caso raro per cui esistono — il
    // keydown con `key` undefined delle tastiere fisiche.
    if (!isTypeAheadKey(e, document.activeElement)) return;
    // Né se sopra la chat c'è un overlay: la guardia percorre la stessa catena
    // di livelli del tasto Indietro (MobileApp._overlayLayers). Guardare solo
    // `dialog[open]`, come faceva prima, lasciava scoperti lightbox, minichat,
    // mini-app e drawer: col composer coperto i caratteri finivano in un campo
    // invisibile.
    if (window.mobileApp?.hasOverlayAbove()) return;
    // focus() sincrono dentro il keydown: Chromium (WebView) recapita l'inserimento
    // del carattere sull'elemento appena messo a fuoco, quindi il tasto non va perso.
    this.input.focus();
  }

  setupWebSocket() {
    // Guard di registrazione singola: i listener vengono bindati una sola volta per tutta
    // la vita del controller (nessun teardown/re-setup ai cambi vista, vedi deactivate()).
    if (this._wsListenersBound) return;
    this._wsListenersBound = true;
    this._onChatMessage = (e) => this.handleMessage(e.detail);
    this._onChatOpen = () => {
      this._setConnectionStatus(true);
      this._resyncThreadAfterReconnect();
    };
    this._onChatClose = () => this._setConnectionStatus(false);
    wsManager.addEventListener('chat:message', this._onChatMessage);
    wsManager.addEventListener('chat:open', this._onChatOpen);
    wsManager.addEventListener('chat:close', this._onChatClose);
    wsManager.connectChat();
  }

  setupInfiniteScroll() {
    this._pager.bindInfiniteScroll();
  }

  async _initOnSessionReady() {
    try {
      await sessionManager.init();
      // Il chip cambia la conversazione; ridisegnarla è di chi la possiede,
      // cioè di qui. `switchTo` non tocca lo schermo apposta.
      scopeChip.onSwitch = (key) => this._switchConversation(key);
      if (sessionManager.currentKey) {
        await this.loadInitialHistory();
      }
    } catch (err) {
      console.error('Session init failed:', err);
    }
  }

  /* Cambio di conversazione: personale <-> progetto, o fra due progetti.
     `key` null vuol dire la personale — la chiave la conosce il session manager,
     non il chip.

     Due cambi ravvicinati si sovrappongono e devono: il secondo non aspetta che
     il primo abbia finito di caricare, altrimenti un tap resterebbe senza
     risposta per tutta la durata di una fetch. Chi perde è **il primo**, sempre,
     e non chi risponde per ultimo: se lo dimenticasse, il chip resterebbe sul
     progetto sbagliato mentre i messaggi vanno nell'altro. Lo garantisce la
     generazione che `switchTo` fa salire qui sotto, letta da
     `loadInitialHistory` prima delle sue attese. */
  async _switchConversation(key) {
    if (!sessionManager.switchTo(key || sessionManager.personalKey)) return;
    this.invalidateHistory();
    try {
      await this.loadInitialHistory();
    } catch (err) {
      console.error('Failed to load the conversation:', err);
    }
  }

  /* Valore iniziale del modello runtime dal payload di bootstrap: senza
     questo la riga "Modello" del popover Info sessione resta "—" finché non
     avviene uno switch a runtime. Un runtime_model_updated successivo vince. */
  _initRuntimeModelFromBootstrap() {
    if (this._runtimeModel) return;
    const info = api.getBootstrapInfo();
    if (!info?.model_name) return;
    this._runtimeModel = {
      provider: info.provider || null,
      model: info.model_name,
      preset: null,
    };
    this._updateSessionInfoModel();
  }

  /* Butta la vista renderizzata e forza il reload dello storico al prossimo
     activate() — usato quando la sessione cambia fuori da questa vista
     (es. uno scambio nella minichat di Jenny). */
  invalidateHistory() {
    /* Le bolle in composizione muoiono con il DOM che le conteneva: i
       riferimenti vanno azzerati **qui**, o restano a puntare nodi staccati e
       il turno seguente ci scrive dentro senza che si veda niente.

       Prima lo rimediava il caso: il `turn_end` del turno in volo arrivava
       comunque e azzerava tutto. Da quando i frame di un'altra conversazione
       vengono scartati non arriva più — cambiare chat a metà risposta è
       esattamente il caso in cui questo accade — quindi la chiusura del turno
       la fa chi butta la vista. */
    this._resetStreamState();
    this._clearGoalBanner();
    this.chatArea.innerHTML = '';
    this.identityEl = null;
    this._ensureIdentity();
    this._pager.reset();
    this._pendingSend = null;
    this._initialHistoryLoaded = false;
  }

  /* Una riconnessione può aver scavalcato dei messaggi, e nulla se ne accorgeva.
     Il thread si legge una volta sola per caricamento di pagina (il latch
     `_initialHistoryLoaded`), poi la vista vive di soli frame WebSocket: quelli
     pubblicati mentre il socket era giù non arrivano mai, e restano invisibili
     finché non si ricarica. Chi lo paga sono i comandi che rispondono in due
     tempi — `/atlas`, `/dream` — perché fra l'ack e l'esito passano secondi o
     minuti, cioè esattamente la finestra in cui lo schermo si spegne e il socket
     cade: in chat resta "Mapping the wiki..." e nessun risultato, che è
     indistinguibile da un comando piantato.

     Si scarta e si ricarica invece di riconciliare perché non c'è un'ancora su
     cui riconciliare: gli id del thread sono relativi alla finestra di fetch (lo
     stesso messaggio è `as-37-…` a limit=10 e `as-1853-…` a limit=160), e l'API
     ha `before` ma non un `after`. `invalidateHistory()` esiste già per questo —
     "la copia a schermo può essere stale, buttala" — ed è la stessa strada del
     cambio di sessione.

     Solo con la vista incollata in fondo, e non è un dettaglio: `invalidateHistory()`
     azzera anche le pagine già paginate indietro, quindi a chi sta leggendo la
     storia il resync porterebbe via ciò che stava guardando per rimediare a un
     messaggio in coda che non sta nemmeno guardando. Incollati in fondo, invece,
     l'operazione è invisibile — ed è il caso normale: telefono in tasca, schermo
     che si riaccende. Il gate è `_autoScroll`, lo stesso che decide il
     riallineamento al ritorno in foreground.

     **Non si esce più quando il thread non è mai stato caricato.** Era la
     guardia `!_initialHistoryLoaded`, scritta pensando al primo collegamento
     («ci pensa loadInitialHistory»), ma quel latch torna giù anche su un
     *fallimento*: e il caricamento che fallisce è precisamente quello che parte
     mentre il gateway non ha ancora finito di alzarsi, cioè il caso normale su
     un telefono. Il risultato era una chat vuota che non si riprendeva più —
     nemmeno alla riconnessione, l'unico momento in cui riprovarla ha senso —
     finché non si usciva dalla vista e si rientrava. La riconnessione è il
     segnale che il gateway c'è: è il momento giusto per il primo tentativo
     tanto quanto per il secondo.

     Al posto del latch c'è la domanda che quella guardia voleva fare davvero:
     *c'è una fetch in volo?* (`_loadingInitialHistory`). Con il latch, un
     `chat:open` arrivato mentre il caricamento iniziale era in attesa passava —
     il latch è alzato dal primo istante — e faceva partire una seconda fetch
     sulla stessa chiave: nessuna delle due è scaduta (la generazione non è
     cambiata), quindi entrambe disegnavano e il thread compariva due volte. */
  async _resyncThreadAfterReconnect() {
    if (this._loadingInitialHistory) return;
    if (this._resyncingThread || this.isLoadingHistory) return;
    if (!this._autoScroll) return;
    if (!sessionManager.currentKey) return;
    this._resyncingThread = true;
    try {
      this.invalidateHistory();
      await this.loadInitialHistory();
    } finally {
      this._resyncingThread = false;
    }
  }

  /* Il caricamento appartiene a una conversazione, e in mezzo ci sono due
     attese: `api.bootstrap()` e la fetch del thread. Se durante una delle due
     la conversazione aperta cambia, tutto quel che segue è di un'altra chat —
     le sue bolle, il nome sul chip, il suo interruttore di sola lettura, il suo
     cursore di paginazione — e va abbandonato in silenzio: a disegnare ci pensa
     il caricamento nuovo, che è già partito. La chiave si prende una volta,
     all'inizio: rileggerla dopo un'attesa vorrebbe dire chiedere il thread di
     una conversazione e mostrarlo come se fosse di un'altra. */
  async loadInitialHistory() {
    if (this._initialHistoryLoaded) return;
    this._initialHistoryLoaded = true;
    this._loadingInitialHistory = true;
    const generation = sessionManager.switchGeneration;
    const key = sessionManager.currentKey;
    const superseded = () => generation !== sessionManager.switchGeneration;
    try {
      if (!key) {
        this.hasMoreHistory = false;
        return;
      }
      await api.bootstrap();
      if (superseded()) return;
      this._initRuntimeModelFromBootstrap();
      // Il 160 è scritto qui e non in una costante del modulo di proposito: il
      // corpo di questo metodo viene *ritagliato come testo* ed eseguito in node
      // da tre test, che non si portano dietro le const del file. Un nome qui
      // diventa un ReferenceError dentro il try, cioè un fallimento muto.
      const { thread, scope } = await sessionManager.loadThread(key, 160);
      if (superseded()) return;
      // Lo scope mostrato sopra il composer deve venire dal backend, non da un
      // default del client: e' l'unica cosa che sta tra un messaggio mandato
      // allo scope sbagliato e l'utente. E viene dalla *risposta a questa*
      // richiesta, non dal campo condiviso che vince chi risponde per ultimo.
      scopeChip.syncFromSession(scope);
      // L'interruttore è per conversazione: la chiave la conosce il session
      // manager, non lui. Stesso punto del chip perché è lo stesso evento.
      writeSwitch.syncFromSession(key);
      // Il tentativo è andato: via la riga d'errore di quello prima, o resta
      // in cima a un thread che c'è (`_renderThreadMessages` accoda, non
      // sostituisce).
      this._clearHistoryError();
      this._renderThreadMessages(thread.messages || []);
      this._pager.adopt(thread.page);
      this._ensureHistoryReach();
      this.scrollToBottom(true);
    } catch (err) {
      // Anche il fallimento è di una conversazione sola: riaprire il latch
      // scavalcato porterebbe via al caricamento nuovo il diritto di caricare.
      // E per la stessa ragione non si dice niente a schermo: la riga d'errore
      // di una conversazione già lasciata comparirebbe sopra il thread di
      // un'altra, e cambiare progetto in fretta dipingerebbe errori per
      // conversazioni che l'utente non sta guardando.
      if (superseded()) return;
      this._initialHistoryLoaded = false;
      console.error('Failed to load history:', err);
      // Un fallimento muto è la cosa peggiore che possa fare questo
      // caricamento: lo schermo resta vuoto **mentre il chip nomina un
      // progetto**, cioè indistinguibile da un progetto senza storia. Da lì si
      // scrive credendo di ripartire da zero in un posto che invece ha un suo
      // passato che l'agente rileggerà.
      this._showHistoryError();
    } finally {
      this._loadingInitialHistory = false;
    }
  }

  /* Lo stato della paginazione vive nel modulo condiviso, ma il suo nome qui è
     pubblico: `isLoadingHistory` lo legge `_resyncThreadAfterReconnect` per non
     far partire una fetch sopra un'altra, e `historyCursor` / `hasMoreHistory`
     li scrive il caricamento iniziale. Tre accessori invece di tre campi: i
     punti di lettura restano quelli di prima, la verità sta in un posto solo. */
  get historyCursor() { return this._pager.cursor; }

  set historyCursor(value) { this._pager.cursor = value || null; }

  get hasMoreHistory() { return this._pager.hasMore; }

  set hasMoreHistory(value) { this._pager.hasMore = !!value; }

  get isLoadingHistory() { return this._pager.loading; }

  /* Apre una pagina: quale conversazione, e la guardia contro il cambio di
     conversazione. Una pagina vecchia arrivata dopo un cambio va buttata, non
     incollata in cima al thread di un'altra chat — è la stessa regola del
     caricamento iniziale, e questo è il pezzo che il modulo condiviso non può
     sapere da sé. */
  _beginHistoryPage() {
    if (!sessionManager.currentKey) return null;
    const generation = sessionManager.switchGeneration;
    const key = sessionManager.currentKey;
    return {
      fetch: async (limit, cursor) => {
        const { thread } = await sessionManager.loadThread(key, limit, cursor);
        return thread;
      },
      stale: () => generation !== sessionManager.switchGeneration,
    };
  }

  _ensureHistoryReach() {
    this._pager.ensureReach();
  }

  /* La riga «storia non caricata», al posto della chat vuota che mentiva.
     Una sola alla volta: due riconnessioni fallite di fila non impilano due
     righe identiche. */
  _showHistoryError() {
    this._clearHistoryError();
    const el = document.createElement('div');
    el.className = 'chat-error chat-history-error';
    el.textContent = i18n.t('chat.historyLoadFailed');
    this.chatArea.appendChild(el);
    this.scrollToBottom(true);
  }

  _clearHistoryError() {
    this.chatArea.querySelectorAll('.chat-history-error').forEach((el) => el.remove());
  }

  async loadMoreHistory() {
    await this._pager.loadMore();
  }

  // Ricostruisce l'array di turni normalizzati dai messaggi persistiti.
  // Voce user: {user:true, text, origin}; turno assistant:
  // {turnId, toolEvents, reasoning, content, fileEdits, media, latencyMs?}.
  _buildTurns(messages) {
    const turns = [];
    let currentTurn = null;
    for (const msg of messages) {
      if (msg.session_boundary) {
        // Confine di contesto (/new): chiude il turno in corso e si rende da
        // sé, senza diventare una bolla dell'assistente.
        if (currentTurn) turns.push(currentTurn);
        currentTurn = null;
        turns.push({ boundary: true, text: msg.text || msg.content || '' });
        continue;
      }
      const role = msg.role || (msg.kind === 'user' ? 'user' : 'assistant');
      if (role === 'user') {
        if (currentTurn) turns.push(currentTurn);
        currentTurn = null;
        turns.push({
          user: true,
          text: msg.text || msg.content || '',
          origin: msg.origin,
          media: Array.isArray(msg.media) ? msg.media : [],
        });
        continue;
      }
      const turnId = msg.turnId || msg.turn_id;
      // Un messaggio senza turno non entra MAI nel turno precedente. Prima la
      // condizione era `currentTurn.turnId !== turnId`, e con due id assenti
      // `undefined !== undefined` è falso: ogni messaggio assistant privo di id
      // veniva concatenato al precedente in un'unica bolla. Misurato sul
      // dispositivo il 2026-08-13: quattro avvisi heartbeat scritti fra 01:31 e
      // 05:02 (righe 17720-17723 del transcript, tutte con `turn_id: None`)
      // resi come una bolla sola da quattro paragrafi. La causa a monte è
      // corretta nel deliverer, che ora conia un id per ogni consegna
      // proattiva; questa guardia serve alla cronologia **già scritta**, che
      // quell'id non l'avrà mai. Dentro un turno vero tutte le parti portano lo
      // stesso id (ogni append al transcript passa una `phase` non nulla),
      // quindi qui non si spezza niente che fosse legittimamente unito.
      if (!currentTurn || !turnId || currentTurn.turnId !== turnId) {
        if (currentTurn) turns.push(currentTurn);
        currentTurn = { turnId, toolEvents: [], reasoning: '', content: '', fileEdits: [], media: [] };
      }
      if (msg.toolEvents || msg.tool_events) {
        currentTurn.toolEvents.push(...(msg.toolEvents || msg.tool_events));
      }
      if (msg.fileEdits || msg.file_edits) {
        currentTurn.fileEdits.push(...(msg.fileEdits || msg.file_edits));
      }
      if (Array.isArray(msg.media) && msg.media.length) {
        currentTurn.media.push(...msg.media);
      }
      const text = msg.text || msg.content || '';
      if (text) {
        if (msg.kind === 'trace' || msg.role === 'tool') {
          if (!currentTurn.toolEvents.length) {
            currentTurn.content += (currentTurn.content ? '\n\n' : '') + text;
          }
        } else {
          currentTurn.content += (currentTurn.content ? '\n\n' : '') + text;
        }
      }
      if (msg.reasoning) {
        currentTurn.reasoning += (currentTurn.reasoning ? '\n\n' : '') + msg.reasoning;
      }
      if (msg.latencyMs != null) {
        currentTurn.latencyMs = msg.latencyMs;
      }
    }
    if (currentTurn) turns.push(currentTurn);
    return turns;
  }

  _renderThreadMessages(messages) {
    for (const turn of this._buildTurns(messages)) {
      if (turn.boundary) {
        this._appendSessionBoundary(turn.text);
      } else if (turn.user) {
        this.addCompletedMessage(turn.text, 'user', turn.origin, turn.media);
      } else {
        this._flushPersistedTurn(turn);
      }
    }
  }

  _renderThreadMessagesToTop(messages) {
    for (const turn of this._buildTurns(messages).reverse()) {
      if (turn.boundary) {
        this._appendSessionBoundary(turn.text, true);
      } else if (turn.user) {
        this.addCompletedMessageToTop(turn.text, 'user', turn.origin, turn.media);
      } else {
        this._flushPersistedTurn(turn, true);
      }
    }
  }

  /* Separatore di contesto reso da /new. Non è una bolla: segna il punto in
     cui il modello riparte da zero, mentre tutto ciò che sta sopra resta
     leggibile — /new azzera la sessione, non il transcript. textContent e non
     innerHTML: il testo arriva dal server e non deve poter iniettare markup. */
  _appendSessionBoundary(text, toTop = false) {
    const el = document.createElement('div');
    el.className = 'chat-session-boundary';
    const label = document.createElement('span');
    label.textContent = text || 'New session started.';
    el.appendChild(label);
    if (toTop) {
      this._insertAtTop(el);
    } else {
      this.chatArea.appendChild(el);
    }
  }

  _flushPersistedTurn(turn, toTop = false) {
    if (!turn) return;
    const node = this._createBaseMessage('assistant');
    let hasContent = false;

    if (turn.toolEvents.length) {
      this._renderToolEvents(turn.toolEvents, node);
      hasContent = true;
    }

    if (turn.fileEdits.length) {
      this._appendFileEdits(node, turn.fileEdits);
      hasContent = true;
    }

    if (turn.reasoning.trim()) {
      this._appendReasoningBlock(node, turn.reasoning.trim(), true);
      hasContent = true;
    }

    if (turn.content.trim()) {
      const content = node.querySelector('.chat-content');
      if (content) {
        content.innerHTML = renderMarkdown(turn.content.trim());
        renderRichContent(content);
        this._makeFilePathsClickable(content);
      }
      this._setMessageSource(node, turn.content.trim());
      hasContent = true;
    }

    if (turn.media?.length) {
      this._renderMediaAttachments(node, turn.media);
      hasContent = true;
    }

    if (!hasContent) return;

    this._appendLatency(node, turn.latencyMs);
    this._appendMsgActions(node);

    if (toTop) {
      this._insertAtTop(node);
    } else {
      this.chatArea.appendChild(node);
    }
  }

  _createBaseMessage(role) {
    const msg = document.createElement('div');
    msg.className = `chat-msg chat-msg-${role === 'user' ? 'user' : 'ai'}`;

    const content = document.createElement('div');
    content.className = 'chat-content';
    msg.appendChild(content);

    return msg;
  }

  /** Slim pill row hosting tool calls, thinking and file edits (AI turns). */
  _ensureMetaRow(msg) {
    let meta = msg.querySelector('.chat-turn-meta');
    if (!meta) {
      meta = document.createElement('div');
      meta.className = 'chat-turn-meta';
      msg.insertBefore(meta, msg.querySelector('.chat-content'));
    }
    return meta;
  }

  /** La riga in coda alla bolla: il pulsante Copia e i secondi del turno.
   *
   *  **Una riga sola.** Erano due nodi impilati — i secondi sopra, i pulsanti
   *  sotto — e in coda a ogni risposta occupavano due righe per due dati che
   *  stanno in una. Chi la crea non e' uno solo, e nemmeno sempre lo stesso:
   *  i secondi arrivano col `turn_end`, il Copia quando il testo e' completo,
   *  e nella cronologia arrivano insieme. Quindi la riga si prende con questa
   *  funzione, che la crea se manca e in ogni caso **la rimette in fondo** —
   *  fra le due chiamate la bolla puo' essersi allungata.
   */
  _ensureMsgActions(msg) {
    let row = msg.querySelector(':scope > .chat-msg-actions');
    if (!row) {
      row = document.createElement('div');
      row.className = 'chat-msg-actions';
    }
    msg.appendChild(row);
    return row;
  }

  _appendLatency(msg, latencyMs) {
    if (!msg || latencyMs == null || msg.querySelector('.chat-meta')) return;
    const meta = document.createElement('span');
    meta.className = 'chat-meta';
    meta.textContent = (latencyMs / 1000).toFixed(1) + 's';
    this._ensureMsgActions(msg).appendChild(meta);
  }

  /* Registra (accumulando) il sorgente di una bolla. Una bolla AI può contenere
     più `.chat-content` — un turno testo → tool → testo apre un segmento nuovo
     a ogni stream — quindi i segmenti si separano con una riga vuota invece di
     sostituirsi. */
  _setMessageSource(msg, text) {
    if (!msg) return;
    const clean = String(text || '').trim();
    if (!clean) return;
    const prev = this._msgSource.get(msg);
    this._msgSource.set(msg, prev ? `${prev}\n\n${clean}` : clean);
  }

  /** Il testo copiabile di una bolla: il sorgente registrato se c'è, altrimenti
      la rete di `innerText` — che perde le recinzioni ma non lascia mai un
      pulsante Copia che non copia niente. */
  _messageText(msg) {
    if (!msg) return '';
    const recorded = this._msgSource.get(msg);
    if (recorded) return recorded;
    return [...msg.querySelectorAll('.chat-content')]
      .map((el) => (el.innerText || '').trim())
      .filter(Boolean)
      .join('\n\n');
  }

  _buildMsgActionButton(cls, icon, label) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `chat-msg-action ${cls}`;
    btn.setAttribute('aria-label', label);
    btn.title = label;
    btn.innerHTML = `<i class="ti ${icon}"></i>`;
    return btn;
  }

  /* Il pulsante Copia nella riga in coda alla bolla. Idempotente, e **primo**:
     i secondi possono essere gia' arrivati (`_handleTurnEnd` li posa prima di
     chiamare qui), e la riga va letta icona-poi-tempo in tutti e due i casi.

     Tre chiamanti — `_handleTurnEnd` (vivo), `_flushPersistedTurn` (storico) e
     il blocco `message` di `_handleMessage` (consegna proattiva): con il solo
     aggancio vivo, riaprire l'app lascerebbe zero pulsanti Copia.

     Sulle bolle utente non compare: sono corte, la selezione nativa riparata
     basta, e da qui non esce piu' niente d'altro — il `⋯` che le raggiungeva
     il foglio delle azioni se n'e' andato col foglio. */
  _appendMsgActions(msg) {
    if (!msg) return;
    if (msg.classList.contains('chat-msg-user')) return;
    // Un turno di soli tool non ha niente da copiare.
    if (!this._messageText(msg)) return;
    const row = this._ensureMsgActions(msg);
    if (row.querySelector('.chat-msg-copy')) return;
    row.insertBefore(
      this._buildMsgActionButton('chat-msg-copy', 'ti-copy', i18n.t('chat.copy')),
      row.firstChild);
  }

  /* Copia il sorgente, non il reso: le recinzioni dei blocchi di codice e il
     loro linguaggio sono esattamente cio' che serve quando una risposta si
     incolla altrove. La scelta fra sorgente e testo nudo era del foglio delle
     azioni, che non c'e' piu': ne resta una, ed e' questa. */
  async _copyMessage(msg) {
    const text = this._messageText(msg);
    if (!text) return;
    if (!(await copyToClipboard(text))) {
      showToast(i18n.t('chat.copyFailed'), 'error');
      return;
    }
    showToast(i18n.t('chat.copied'), 'success');
  }

  _appendFileEdits(msg, edits) {
    if (!edits.length || !msg) return;

    const fileMap = new Map();
    for (const edit of edits) {
      for (const p of (edit.paths || [edit.path])) {
        const stats = fileMap.get(p) || { added: 0, deleted: 0 };
        stats.added += edit.added || 0;
        stats.deleted += edit.deleted || 0;
        fileMap.set(p, stats);
      }
    }

    this._renderCollapsibleFileEdits(msg, fileMap);
  }

  async _openFileInWorkspace(filePath) {
    try {
      window.mobileApp.switchMode('workspace');
      await window.mobileApp.controllers.workspace.ready;
      await window.mobileApp.controllers.workspace.openFile(filePath);
    } catch (err) {
      console.error('Failed to open file in workspace:', err);
      showToast(i18n.t('chat.couldNotOpen', { path: filePath }), 'error');
    }
  }

  /* Badge di provenienza per i messaggi entrati da un altro canale
     (es. Telegram): piccola etichetta sopra il contenuto della bolla. */
  _appendOriginBadge(msg, origin) {
    if (!origin || origin === 'websocket') return;
    const badge = document.createElement('div');
    badge.className = 'chat-origin-badge';
    const icon = origin === 'telegram' ? 'ti-brand-telegram' : 'ti-arrows-exchange';
    const label = origin.charAt(0).toUpperCase() + origin.slice(1);
    badge.innerHTML = `<i class="ti ${icon}"></i>${escapeHtml(label)}`;
    msg.insertBefore(badge, msg.firstChild);
  }

  _buildCompletedMessage(text, role, origin, media) {
    const msg = document.createElement('div');
    msg.className = `chat-msg chat-msg-${role === 'user' ? 'user' : 'ai'}`;

    const content = document.createElement('div');
    content.className = 'chat-content';
    if (role === 'user') {
      content.textContent = text;
    } else {
      content.innerHTML = renderMarkdown(String(text || ''));
      renderRichContent(content);
      this._makeFilePathsClickable(content);
    }
    msg.appendChild(content);
    // Allegati dell'utente ripristinati dalla history (thumb immagini / chip
    // file), così la preview non si perde dopo un reload.
    if (media?.length) this._renderMediaAttachments(msg, media);
    if (role === 'user') this._appendOriginBadge(msg, origin);
    this._setMessageSource(msg, text);
    this._appendMsgActions(msg);
    return msg;
  }

  addCompletedMessage(text, role, origin, media) {
    this.chatArea.appendChild(this._buildCompletedMessage(text, role, origin, media));
  }

  addCompletedMessageToTop(text, role, origin, media) {
    this._insertAtTop(this._buildCompletedMessage(text, role, origin, media));
  }

  activate() {
    this._active = true;
    sessionManager.ensureAttached();
    // Rientro in chat: lo snapshot può essere invecchiato (le transizioni
    // avvenute a vista nascosta sono arrivate, ma elapsed/idle no se il poll
    // era spento perché non girava nulla).
    this._refreshSubagents();
    if (!this._initialHistoryLoaded) {
      this.loadInitialHistory();
    }

    /* `preventScroll`, e non e' un dettaglio.
     *
     *  Entrando in chat da uno scorrimento laterale, la vista sta ancora
     *  **fuori schermo**: `_animateSlideIn` le mette `translateX(100%)` e poi la
     *  riporta a zero in due decimi di secondo. Finche' e' spostata, la pagina
     *  e' larga il doppio — e in chat lo scroller **e' il documento**.
     *
     *  Mettere a fuoco un campo che sta fuori dallo scrollport fa una cosa
     *  sola, e la fa bene: il browser **scorre per raggiungerlo**. Di lato.
     *  Trascinandosi dietro tutto il guscio.
     *
     *  Misurato sul banco il 22/09/2026: vista a `translateX(100%)`, larghezza
     *  del documento da 590 a 1180, `focus()` → `.app` a **x -384**; con
     *  `preventScroll: true` → **x 0**. Sul telefono si vedeva come una pagina
     *  schiacciata a sinistra e tagliata, col footer ridotto a una voce sola —
     *  l'ultima, la sola rimasta dentro il pezzo visibile.
     *
     *  Ed e' per questo che il difetto aveva un verso solo: uscendo dalla chat
     *  nessuno mette a fuoco niente. */
    this.input.focus({ preventScroll: true });

    // I messaggi possono essere arrivati mentre la vista era nascosta (scrollHeight=0 rende
    // scrollToBottom() un no-op); riallinea lo scroll ora che la vista è di nuovo visibile.
    if (this._autoScroll) this.scrollToBottom(true);
    else this._restoreScrollAnchor();

    /* La chat è a schermo: gli avvisi di sistema che la annunciavano hanno
       finito il loro lavoro, e restare in coda vuol dire chiedere all'utente di
       scartare a mano notifiche di messaggi che ha davanti agli occhi. La regola
       completa e i suoi tre chiamanti stanno nella docstring di
       `NotifierBridge.clearAlerts`; il gemello di questa riga è in
       `MainActivity.onResume`, per il rientro a chat già attiva — dove nessun
       cambio vista avviene e quindi qui non si passa.

       Fuori dal guscio nativo (browser, test) `JennyNative` non esiste: l'optional
       chaining basta, il try è per un guscio che esponga il ponte ma sollevi. */
    try { window.JennyNative?.chatOpened?.(); } catch (e) { /* nessun guscio nativo */ }
  }

  /* Posizione di lettura della chat, misurata come distanza dal fondo.
     Si misura *qui*, nel listener dello scroll, e non in deactivate(): il
     cambio sezione mette il display:none sulla view **prima** di chiamarlo, e
     un contenitore senza box legge scrollTop e scrollHeight a 0 — l'ancora
     sarebbe sempre 0, cioè "vai in fondo", che è l'errore opposto a quello che
     si voleva correggere. */
  _rememberScrollAnchor() {
    // Contenitore nascosto: 0 non è una posizione di lettura, è l'assenza di
    // un box. Registrarlo cancellerebbe l'ancora buona.
    if (!this.chatArea.clientHeight) return;
    this._scrollAnchor = this._scroller.scrollHeight - this._scroller.scrollTop;
  }

  /* Rientro in chat di chi era risalito a leggere: prima si tornava sempre in
     fondo, e per ritrovare il punto bisognava riscorrere tutto. Il ripristino
     va al primo rAF utile — a display appena ripristinato le altezze non sono
     ancora quelle definitive. */
  _restoreScrollAnchor() {
    if (this._scrollAnchor == null) return;
    const anchor = this._scrollAnchor;
    requestAnimationFrame(() => {
      if (!this._active) return;  // già usciti di nuovo
      this._scroller.scrollTop = Math.max(0, this._scroller.scrollHeight - anchor);
      this._autoScroll = this._isNearBottom();
      this._updateScrollFab();
    });
  }

  /* Sotto-stato della sezione chat: il popover "Info sessione". È un livello a
     tutti gli effetti — copre la chat e ha una sua chiusura — ma il tasto
     Indietro lo scavalcava, uscendo dalla chat e lasciandolo a schermo. */
  handleBack() {
    if (!this._sessionInfoPopover) return false;
    this._hideSessionInfo();
    return true;
  }

  deactivate() {
    // Il popover Info sessione è appeso a document.body, non alla view: il
    // display:none del cambio sezione non lo tocca, quindi restava a schermo
    // sopra la sezione nuova, col suo setInterval da 1s ancora vivo.
    this._hideSessionInfo();
    // Spegne il type-ahead focus: fuori dalla vista chat non deve rubare i tasti.
    this._active = false;
    // Per il resto è un no-op intenzionale: i listener WS restano sempre attivi (vedi
    // setupWebSocket(), bindato una sola volta nel costruttore). Il socket è condiviso tra
    // le viste e gli eventi che arrivano a vista nascosta vanno comunque processati,
    // altrimenti si perdono per sempre (bug del banner "agent running" bloccato + risposta
    // mai renderizzata).
  }

  /* Eventi che appartengono al rendering di un turno, e che quindi passano dal
     confine di turno. Fuori: i frame fuori banda (subagent, modello runtime,
     goal) e `user`, che il proprio azzeramento se lo fa già. */
  static TURN_SCOPED_EVENTS = new Set([
    'delta', 'reasoning_delta', 'reasoning_end', 'stream_end',
    'message', 'file_edit', 'turn_end',
  ]);

  /* Eventi che appartengono a *una* conversazione, e che quindi si rendono solo
     se arrivano da quella aperta. Sono i frame di un turno più i due che
     descrivono lo stato della conversazione: `user` (un messaggio entrato da un
     altro canale) e `goal_status` (il banner, spinto per `chat_id`).

     Fuori restano i frame che parlano del runtime, non di una conversazione, e
     che vanno resi qualunque chat sia a schermo:

     - `subagent_status` porta lo snapshot *globale* dei subagent (identico a
       `GET /api/subagents`) e viaggia sul `chat_id` di chi li ha avviati:
       filtrarlo vorrebbe dire un pannello che non si aggiorna più;
     - `subagent_activity` porta un `chat_id` che **non è affidabile**: è
       mirato a una connessione e il campo lo riempie `ws_sender._chat_id_for`
       con `min(chats)`, cioè `default` per chiunque sia iscritto anche alla
       chat personale. Filtrarlo spegnerebbe la modale dell'attività a chi
       guarda un progetto;
     - `runtime_model_updated`, `app_data_changed`, `apps_list_changed` sono
       broadcast a ogni connessione e non portano `chat_id` affatto;
     - `error` è un errore di protocollo della connessione (`_send_event`), e
       nemmeno lui porta un `chat_id`. */
  static CHAT_SCOPED_EVENTS = new Set([
    ...ChatController.TURN_SCOPED_EVENTS, 'user', 'goal_status',
  ]);

  /* Un frame appartiene alla conversazione aperta?

     Il `turn_id` distingue due turni; il `chat_id` distingue due
     *conversazioni*, e prima delle sessioni-progetto non c'era niente da
     distinguere — una chat sola, quindi un filtro assente era un filtro
     inutile. Con i progetti diventa il punto in cui la risposta data in
     `project:patreon` si dipinge nel thread personale: delta, righe di
     `file_edit` e `turn_end` compresi, sotto un composer che può perfino
     dichiarare un'altra modalità di scrittura.

     Va valutato **prima** del confine di turno, e non è un dettaglio d'ordine:
     `_applyTurnBoundary` ha un effetto collaterale — adotta il turno del frame
     che vede — quindi un frame estraneo che passasse da lì si prenderebbe
     `_currentTurnId`, e il `turn_end` della risposta vera non combacerebbe più
     con niente. Filtrando prima, un turno di un'altra chat non tocca la
     contabilità di questa: non apre niente, e quindi non lascia niente di
     aperto.

     Permissivo su ciò che non sa: senza `chat_id` sul frame (il retry di una
     consegna parziale ne arriva privo) o senza conversazione nota, si rende. */
  _belongsToOpenChat(msg) {
    if (!ChatController.CHAT_SCOPED_EVENTS.has(msg.event)) return true;
    const open = sessionManager.currentChatId;
    if (!msg.chat_id || !open) return true;
    return msg.chat_id === open;
  }

  /* Confine di turno sui frame live: dice se questo frame va processato, e
     apre una bolla nuova quando il turno cambia.

     Ogni frame live porta il proprio `turn_id` — il recorder lo stampa sul
     payload prima che parta sul filo — ma il client lo leggeva solo nella
     cronologia. Nel live tutto finiva nella bolla corrente, e un turno può
     atterrare *dentro* un altro: un avviso proattivo (heartbeat, cron, Dream)
     è un turno a sé e non aspetta che la risposta in corso finisca. Da lì i due
     sintomi, con una causa sola: l'avviso riusava la bolla della risposta
     sovrascrivendone il testo, e il suo `turn_end` chiudeva un turno che non
     era il suo.

     Le regole seguono `_same_turn` lato server, con l'asimmetria che conta:

     - frame senza id → vale il turno corrente. Non è un caso teorico: il retry
       di una consegna parziale ricostruisce il payload con `skip_persist`, che
       salta l'annotazione, quindi arriva senza id (misurato: il turn_end
       ritrasmesso è `{event, chat_id}` e basta).
     - nessun turno in corso → questo frame lo apre, e ne adotta l'id.
     - id diverso da quello in corso → è un altro turno: `turn_end` non lo
       riguarda e va ignorato, tutto il resto apre una bolla nuova. La risposta
       interrotta a metà riprende nella propria: due bolle separate dall'avviso
       sono l'ordine in cui le cose sono davvero successe. */
  _applyTurnBoundary(msg) {
    if (!ChatController.TURN_SCOPED_EVENTS.has(msg.event)) return true;
    const turnId = msg.turn_id || msg.turnId || null;
    if (!turnId || turnId === this._currentTurnId) return true;
    if (this._currentTurnId === null) {
      this._currentTurnId = turnId;
      return true;
    }
    if (msg.event === 'turn_end') return false;
    this._resetStreamState();
    this._currentTurnId = turnId;
    return true;
  }

  handleMessage(msg) {
    if (!this._belongsToOpenChat(msg)) return;
    /* La prova che l'ultimo invio è entrato: il gateway sta rispondendo di
       qualcosa che non è un rifiuto. Da qui in poi quella bolla non è più in
       sospeso, e un errore che arrivasse dopo è un errore di altro. */
    if (msg.event !== 'error') this._pendingSend = null;
    if (!this._applyTurnBoundary(msg)) return;
    switch (msg.event) {
      case 'delta':
        this._handleDelta(msg.text || '');
        break;
      case 'reasoning_delta':
        this._handleReasoningDelta(msg.text || '');
        break;
      case 'reasoning_end':
        this._handleReasoningEnd();
        break;
      case 'stream_end':
        this._handleStreamEnd(msg.text);
        break;
      case 'turn_end':
        this._handleTurnEnd(msg.latency_ms);
        break;
      case 'message':
        this._handleMessage(msg);
        break;
      case 'user':
        this._handleExternalUser(msg);
        break;
      case 'file_edit':
        this._handleFileEdit(msg.edits || []);
        break;
      case 'goal_status':
        this._handleGoalStatus(msg.status, msg.started_at);
        break;
      case 'subagent_status':
        // Frame dedicato (ws_sender.send_subagent_status): mai una bolla, mai
        // persistito nel transcript. Stessa forma di GET /api/subagents.
        this._renderSubagents({ running: msg.running, recent: msg.recent });
        break;
      case 'subagent_activity':
        // Finestra di attività fine, spinta solo a chi ha mandato un watch:
        // arriva se e solo se la modale è aperta (v. _attachSubagentStream).
        this._handleSubagentActivity(msg);
        break;
      case 'subagent_unwatched':
        this._handleSubagentUnwatched(msg);
        break;
      case 'error':
        this._handleError(msg);
        break;
      case 'runtime_model_updated':
        // Campi del payload backend (ws_sender.send_runtime_model_updated):
        // model_name obbligatorio, model_preset/provider opzionali.
        this._runtimeModel = {
          provider: msg.provider || this._runtimeModel?.provider || null,
          model: msg.model_name || null,
          preset: msg.model_preset || null,
        };
        this._updateSessionInfoModel();
        break;
    }
  }

  _ensureAiMessage() {
    if (this._currentMsg) return;

    const msg = document.createElement('div');
    msg.className = 'chat-msg chat-msg-ai';

    this.chatArea.appendChild(msg);
    this._currentMsg = msg;
    this._toolStates = {};
    this._fileEditPaths = new Map();
  }

  // TODO(stream-id): il server manda stream_id con ogni delta/stream_end;
  // chiavare le bolle per stream_id renderebbe il rendering robusto anche
  // senza turn_end (oggi coperto da _resetStreamState su send + turn_end
  // garantito dal backend anche su /stop).
  _handleDelta(text) {
    this._ensureAiMessage();

    if (!this._currentContent) {
      const content = document.createElement('div');
      content.className = 'chat-content';
      this._currentMsg.appendChild(content);
      this._currentContent = content;
      this._deltaBuffer = '';
    }

    this._deltaBuffer += text;
    this._deltaDirty = true;
    this._scheduleFlush();
  }

  /* Programma un flush coalizzato del rendering per il prossimo frame.
     Un solo rAF condiviso tra testo e reasoning: se arrivano insieme,
     costano comunque un solo re-parse per frame. */
  _scheduleFlush() {
    if (this._pendingFrame !== null) return;
    this._pendingFrame = requestAnimationFrame(() => this._flushRender());
  }

  /* `innerHTML =` ricrea tutti i nodi di testo, quindi una selezione aperta
     dentro il blocco che si sta riscrivendo muore al frame dopo. Finché la
     selezione è lì dentro non si riscrive e il flag *resta sporco*: il buffer
     continua ad accumulare e il testo recupera in un frame solo quando la
     selezione cade (ci pensa `onSelectionChange` in `setupEventListeners`).
     Il guard è per blocco: una selezione in una bolla vecchia non congela
     niente, perché quella bolla nessuno la riscrive. */
  _flushRender() {
    this._pendingFrame = null;
    if (this._deltaDirty && this._currentContent && !selectionInside(this._currentContent)) {
      this._currentContent.innerHTML = renderMarkdown(this._deltaBuffer);
      this._deltaDirty = false;
    }
    if (this._reasoningDirty && this._currentThinking && !selectionInside(this._currentThinking)) {
      this._renderReasoningBody();
      this._reasoningDirty = false;
    }
    this.scrollToBottom();
  }

  _cancelPendingFrame() {
    if (this._pendingFrame !== null) {
      cancelAnimationFrame(this._pendingFrame);
      this._pendingFrame = null;
    }
  }

  /* Guscio di un blocco di ragionamento: chip d'intestazione + corpo che scorre
     dentro sé. Identico per lo storico e per il vivo — il vivo ci aggiunge la
     pillola e la disciplina dello scroll, così un blocco finito è
     indistinguibile da uno riletto dallo storico. */
  _buildThinkingBlock(collapsed) {
    const thinking = document.createElement('div');
    thinking.className = `chat-thinking${collapsed ? ' collapsed' : ''}`;

    const header = document.createElement('div');
    header.className = 'chat-thinking-header';
    header.innerHTML = '<i class="ti ti-brain"></i><span class="chat-thinking-label">' + i18n.t('chat.showThinking') + '</span><i class="ti ti-chevron-down chat-thinking-chevron"></i>';
    thinking.appendChild(header);

    const body = document.createElement('div');
    body.className = 'chat-thinking-body';
    thinking.appendChild(body);

    return { thinking, header, body };
  }

  _appendReasoningBlock(msg, text, collapsed = true) {
    if (!msg || !text) return;
    const { thinking, header, body } = this._buildThinkingBlock(collapsed);
    body.innerHTML = renderMarkdown(text);
    renderRichContent(body);

    header.addEventListener('click', () => {
      thinking.classList.toggle('collapsed');
    });

    this._ensureMetaRow(msg).appendChild(thinking);
  }

  /* ── Scroll del ragionamento in diretta ──────────────────────────────────
     Il corpo ha un `max-height`, quindi è un contenitore di scroll a sé: senza
     una disciplina propria restava fermo in cima mentre il testo cresceva sotto
     (e il rimpiazzo dell'innerHTML gli azzerava lo scroll a ogni frame). La
     regola è quella della chat e della lista dei subagent: si insegue l'ultima
     riga finché si è in fondo, si smette appena l'utente risale a leggere, e la
     pillola è il modo — dichiarato — di tornare giù. */

  /* Tolleranza di 24px come per la lista dei subagent: "in fondo" deve restare
     vero dopo che il dito ha rilasciato lo slancio a un pelo dal bordo. */
  _thinkAtBottom(body) {
    if (!body) return true;
    return (body.scrollHeight - body.scrollTop - body.clientHeight) <= 24;
  }

  _thinkScrollToLatest(body) {
    if (body) body.scrollTop = body.scrollHeight;
  }

  _syncThinkingJump() {
    const btn = this._currentThinking &&
      this._currentThinking.querySelector('.chat-thinking-jump');
    if (btn) btn.hidden = this._thinkStick;
  }

  /* Rende il buffer nel corpo tenendo lo scroll dov'è. Il rimpiazzo
     dell'innerHTML riporta scrollTop a zero: senza salvarlo e rimetterlo, chi è
     risalito a leggere verrebbe sbattuto in cima a ogni frame. */
  _renderReasoningBody() {
    const body = this._currentThinking &&
      this._currentThinking.querySelector('.chat-thinking-body');
    if (!body) return;
    // La misura vince sul flag: una WebView non emette `scroll` per uno scroll
    // programmatico, quindi il flag da solo si sgancerebbe da sé. A blocco
    // chiuso il corpo è `display: none` e misura zero — cioè "in fondo", che è
    // il default giusto per quando verrà aperto.
    this._thinkStick = this._thinkAtBottom(body);
    const top = body.scrollTop;
    body.innerHTML = renderMarkdown(this._reasoningBuffer);
    if (this._thinkStick) this._thinkScrollToLatest(body);
    else body.scrollTop = top;
    this._syncThinkingJump();
  }

  _handleReasoningDelta(text) {
    this._ensureAiMessage();

    if (!this._currentThinking) {
      const { thinking, header, body } = this._buildThinkingBlock(true);

      header.addEventListener('click', () => {
        const opened = !thinking.classList.toggle('collapsed');
        // Aprire atterra sull'ultima riga, come entrare in chat.
        if (opened && this._thinkStick) this._thinkScrollToLatest(body);
      });

      /* I blocchi dei turni precedenti restano nel DOM con i loro listener:
         senza questa guardia, scorrere dentro un ragionamento vecchio mentre ne
         arriva uno nuovo staccherebbe *quello nuovo*. Solo il blocco vivo
         comanda il flag. */
      const isLive = () => this._currentThinking === thinking;

      body.addEventListener('scroll', () => {
        if (!isLive()) return;
        this._thinkStick = this._thinkAtBottom(body);
        this._syncThinkingJump();
      }, { passive: true });

      // Rotella (Titan 2): un colpo verso l'alto stacca subito, senza aspettare
      // la soglia — gemello del `wheel` della chat.
      body.addEventListener('wheel', (e) => {
        if (!isLive()) return;
        if (e.deltaY < 0) {
          this._thinkStick = false;
          this._syncThinkingJump();
        }
      }, { passive: true });

      this._ensureMetaRow(this._currentMsg).appendChild(thinking);

      this._currentThinking = thinking;
      this._reasoningBuffer = '';
      this._reasoningSegmentClosed = false;
      this._thinkStick = true;
      this._setThinkingLive(thinking, true);
    }

    if (this._reasoningSegmentClosed) {
      this._reasoningSegmentClosed = false;
      // Stacco fra un segmento e il successivo: stessa giunzione con cui lo
      // storico ricompone il ragionamento di un turno (`_ingestHistory`), così
      // ricaricare la chat non cambia quello che si legge.
      if (this._reasoningBuffer) this._reasoningBuffer += '\n\n';
      // Ha ripreso a pensare: il blocco torna vivo (icona e pillola).
      this._setThinkingLive(this._currentThinking, true);
    }

    this._reasoningBuffer += text;
    this._reasoningDirty = true;
    this._scheduleFlush();
  }

  /* Vivo o concluso: icona della testata e presenza della pillola. Il passaggio
     deve funzionare nei due sensi — un blocco torna vivo ogni volta che il
     modello riprende a ragionare dopo un tool. */
  _setThinkingLive(thinking, live) {
    if (!thinking) return;
    const icon = thinking.querySelector('.chat-thinking-header i');
    if (icon) {
      icon.className = live ? 'ti ti-brain' : 'ti ti-check';
      // Stringa vuota e non un colore: toglie l'inline e rimette quello del CSS.
      icon.style.color = live ? '' : 'var(--ok)';
    }

    const existing = thinking.querySelector('.chat-thinking-jump');
    if (!live) {
      // Non arriva più testo: la pillola non ha nessun "dopo" da promettere e se
      // ne va. Da qui in poi il blocco è un box di scroll normale, uguale a
      // quelli riletti dallo storico.
      if (existing) existing.remove();
      return;
    }
    if (existing) return;

    const body = thinking.querySelector('.chat-thinking-body');
    const jump = document.createElement('button');
    jump.type = 'button';
    jump.className = 'chat-thinking-jump';
    jump.hidden = this._thinkStick;
    jump.innerHTML = '<i class="ti ti-arrow-down"></i>' +
      escapeHtml(i18n.t('chat.scrollToBottom'));
    jump.addEventListener('click', () => {
      this._thinkStick = true;
      this._thinkScrollToLatest(body);
      this._syncThinkingJump();
    });
    thinking.appendChild(jump);
  }

  _handleReasoningEnd() {
    // Flush finale del reasoning eventualmente in coda al frame corrente,
    // così l'ultimo delta non resta invisibile se lo stream chiude prima
    // che l'rAF pendente scatti.
    if (this._reasoningDirty && this._currentThinking) {
      this._renderReasoningBody();
      this._reasoningDirty = false;
    }
    /* Adesso il segmento e' chiuso, quindi formule e diagrammi si possono
       disegnare: `_renderReasoningBody` gira a ogni frame mentre il testo
       arriva, e li' una formula e' a meta'. Il ragionamento e' una piega che si
       apre per guardarci dentro — ed e' il posto dove una formula serve di
       piu', non di meno. */
    if (this._currentThinking) renderRichContent(this._currentThinking);
    this._setThinkingLive(this._currentThinking, false);
    /* Il buffer NON si azzera. `reasoning_end` chiude un *segmento*, non il
       ragionamento del turno: il modello ne apre uno nuovo ogni volta che
       riprende a pensare dopo un tool (`request_execution.py`, dove l'end parte
       appena il ragionamento cede il passo al testo). Azzerandolo, il primo
       delta del segmento dopo ripartiva da vuoto e il rendering rimpiazzava il
       testo di quello prima: il ragionamento già letto spariva dal box e
       tornava solo ricaricando lo storico — che infatti i segmenti li concatena.
       Qui si segna solo il confine; la riga vuota la mette il delta dopo. */
    this._reasoningSegmentClosed = true;
  }

  _handleStreamEnd(fullText) {
    // Annulla il frame di rendering eventualmente pendente: sotto facciamo
    // comunque il render finale completo (con KaTeX + path cliccabili), un
    // rAF in ritardo sovrascriverebbe con una versione parziale.
    this._cancelPendingFrame();
    this._deltaDirty = false;
    // `text` è OPZIONALE in stream_end: il server lo omette quando l'ultimo
    // delta è vuoto e non c'è stata riscrittura di immagini (ws_sender.py,
    // send_stream_delta) — cioè quasi sempre, dato che il client i delta li
    // ha già. Gatare la passata finale sulla sua presenza la faceva saltare
    // sulla maggior parte delle risposte: niente KaTeX (le formule restavano
    // `$$...$$` fino a un riavvio) e niente path cliccabili. Il buffer locale
    // è la stessa cosa, quindi fa da fallback.
    const finalText = fullText || this._deltaBuffer;
    if (this._currentContent && finalText) {
      this._currentContent.innerHTML = renderMarkdown(finalText);
      renderRichContent(this._currentContent);
      this._makeFilePathsClickable(this._currentContent);
      this._setMessageSource(this._currentMsg, finalText);
    }
    // Chiude il segmento. Un turno con testo → tool → testo produce più
    // stream, ma `_resetStreamState` scatta solo a turn_end: senza questo
    // azzeramento i delta del segmento successivo si accodavano al buffer
    // del precedente e finivano nella stessa bolla, incollati e senza
    // stacco ("...setup right.Alright, let me build this..."). La cronologia
    // li tiene separati, ed è per questo che un reload "riparava" il testo.
    this._currentContent = null;
    this._deltaBuffer = '';
    this._bumpUnread();
    this.scrollToBottom();
  }

  _resetStreamState() {
    // Annulla un frame pendente prima di azzerare i riferimenti alle bolle,
    // altrimenti scriverebbe su un contenitore ormai orfano.
    this._cancelPendingFrame();
    this._deltaDirty = false;
    this._reasoningDirty = false;
    this._currentMsg = null;
    this._currentThinking = null;
    this._currentContent = null;
    // Il turno finisce con la sua bolla: il frame dopo ne adotterà uno nuovo.
    this._currentTurnId = null;
    this._deltaBuffer = '';
    this._reasoningBuffer = '';
    this._reasoningSegmentClosed = false;
    this._thinkStick = true;
    this._toolStates = {};
    this._fileEditPaths = new Map();
  }

  _handleTurnEnd(latencyMs) {
    this._appendLatency(this._currentMsg, latencyMs);
    this._appendMsgActions(this._currentMsg);
    this._dropTerminatedSubagents();

    this._resetStreamState();
    // Niente scroll forzato: se l'utente è risalito a leggere, a fine turno
    // resta dove si trova (comportamento WhatsApp/Telegram).
    this.scrollToBottom();
  }

  /* Messaggio utente entrato da un altro canale (es. Telegram): il backend
     lo proietta live sulla vista WebUI. Nuova bolla utente con badge di
     provenienza; chiude l'eventuale stream orfano perché sta iniziando un
     turno nuovo (stessa disciplina di sendMessage). */
  _handleExternalUser(msg) {
    const text = msg.text || '';
    /* Gli allegati arrivano gia' firmati (`ws_sender::_user_echo_wire`): qui
       si passano a `addCompletedMessage`, che li rende con la **stessa**
       `_renderMediaAttachments` del ripristino da history — le due strade
       devono dipingere la stessa bolla, altrimenti una foto c'e' finche' non
       ricarichi, o compare solo dopo.
       E il testo non e' piu' obbligatorio: una foto senza didascalia e' un
       messaggio a tutti gli effetti, e con il vecchio guard non faceva bolla. */
    const media = msg.media_urls || [];
    if (!text.trim() && !media.length) return;
    this._resetStreamState();
    this.addCompletedMessage(text, 'user', msg.origin, media);
    this._bumpUnread();
    this.scrollToBottom();
  }

  _handleMessage(msg) {
    if (msg.session_boundary) {
      /* `/new` pulisce anche lo schermo, e lo fa **ricaricando il thread** invece
         di svuotare il DOM da sé.

         Il confine è anche il pavimento della cronologia visibile: il server
         ferma la prima pagina lì (`webui/transcript.py::_is_session_boundary_turn`),
         quindi quel che questa fetch riporta è esattamente il separatore e nulla
         sopra — e "nulla sopra" resta vero alla riapertura dell'app, che è ciò
         che il vecchio `/clear` prometteva senza mantenerlo. Scorrere in su
         ricarica la conversazione precedente come pagina più vecchia: non è una
         cancellazione, è un'interruzione di pagina.

         Ricaricare e non dipingere: così lo stato a schermo è per costruzione
         identico a quello di un reload, e non c'è una seconda strada da tenere
         d'accordo con la prima (`invalidateHistory` + `loadInitialHistory` è già
         quella del resync e del cambio di conversazione). Nessuna corsa con la
         scrittura: `ws_sender.send` persiste il record **prima** del fanout, e
         l'append è sincrono — quando questo frame arriva, la riga c'è.

         `_appendSessionBoundary` resta viva: la usa il replay, che è chi disegna
         il separatore che questa fetch riporta. */
      this.invalidateHistory();
      this.loadInitialHistory();
      return;
    }
    const isHint = msg.kind === 'tool_hint';
    if (isHint || msg.tool_events) {
      this._ensureAiMessage();
      if (msg.tool_events?.length) this._renderToolEvents(msg.tool_events);
      // Hint testuale senza tool_events — è il caso delle transizioni dei
      // subagent ("subagent started: fix parser"): riga di trace subordinata,
      // non una bolla. Prima di questo ramo il testo non veniva reso da
      // nessuna parte (il ramo sotto lo salta perché kind === 'tool_hint').
      else if (isHint && msg.text) this._renderTraceRow(msg.text);
    }

    if (msg.text && msg.kind !== 'tool_hint') {
      this._ensureAiMessage();
      /* Un `message` è un testo COMPLETO: va in un blocco suo, e quel blocco si
         chiude subito. Prima il ramo riusava `_currentContent` quando c'era e ce
         lo lasciava appeso, e da lì il difetto misurato il 27/08/2026 sul cron
         delle 20:00: l'avviso consegnato dal tool `message` (`turn_seq 4`) e poi
         i delta della narrazione del modello ("l'ho chiamato, aspetto la
         risposta") nello STESSO turno — stesso `turn_id`, quindi nessun confine
         di turno e nessun `_resetStreamState`. `_handleDelta` trovava il blocco
         già dipinto, non ne apriva uno nuovo, e `_flushRender` scriveva la
         narrazione SOPRA l'avviso: in chat restava il solo testo di servizio,
         mentre notifica Android e transcript avevano quello vero. Lo stesso
         riuso cancellava il primo di due `message` nello stesso turno.

         L'asimmetria che decide la forma della fix: un blocco che si chiude non
         perde niente, un blocco riusato perde tutto. È la stessa disciplina di
         `_handleStreamEnd`, e un segmento di stream ancora aperto si chiude
         proprio da lì — che il buffer lo rende per intero, quindi non se ne
         perde la coda. */
      if (this._deltaBuffer) this._handleStreamEnd();
      const content = document.createElement('div');
      content.className = 'chat-content';
      this._currentMsg.appendChild(content);
      content.innerHTML = renderMarkdown(msg.text);
      renderRichContent(content);
      this._makeFilePathsClickable(content);
      this._setMessageSource(this._currentMsg, msg.text);
      /* Una consegna proattiva può non avere un `turn_end` dietro: la riga di
         azioni si posa qui, e `_handleTurnEnd` la rimetterà in coda se il turno
         poi continua. */
      this._appendMsgActions(this._currentMsg);
      // `_currentContent` resta null: il delta successivo apre il proprio blocco.
    }

    if (msg.media_urls?.length) {
      this._ensureAiMessage();
      this._renderMediaAttachments(this._currentMsg, msg.media_urls);
    }

    if (msg.latencyMs != null && this._currentMsg) {
      this._appendLatency(this._currentMsg, msg.latencyMs);
    }

    if ((msg.text && msg.kind !== 'tool_hint') || msg.media_urls?.length) this._bumpUnread();
    this.scrollToBottom();
  }

  /* Riga di trace testuale nella meta-row del turno, accanto ai chip dei tool:
     stessa gerarchia visiva (subordinata alla risposta), nessuna bolla. */
  _renderTraceRow(text) {
    const msg = this._currentMsg;
    if (!msg) return;
    const row = document.createElement('div');
    row.className = 'chat-trace';
    row.textContent = text;
    this._ensureMetaRow(msg).appendChild(row);
  }

  _renderToolEvents(events, targetMsg = null) {
    const msg = targetMsg || this._currentMsg;
    if (!msg) return;

    let toolsContainer = msg.querySelector('.chat-tools');
    if (!toolsContainer) {
      toolsContainer = document.createElement('div');
      toolsContainer.className = 'chat-tools';
      this._ensureMetaRow(msg).appendChild(toolsContainer);
    }

    for (const ev of events) {
      const callId = ev.call_id;
      if (!callId) continue;

      let existing = toolsContainer.querySelector(`[data-call-id="${callId}"]`);

      if (ev.phase === 'start') {
        this._toolStates[callId] = 'running';
        if (!existing) {
          existing = this._createToolElement(toolsContainer, ev, 'running');
        }
      } else if (ev.phase === 'end') {
        this._toolStates[callId] = 'done';
        if (!existing) {
          existing = this._createToolElement(toolsContainer, ev, 'done');
        }
        const icon = existing.querySelector('.chat-tool-icon');
        if (icon) {
          icon.className = `chat-tool-icon done ti ${TOOL_ICONS.end}`;
        }

        if (ev.result != null) {
          const resultStr = typeof ev.result === 'string' ? ev.result : JSON.stringify(ev.result, null, 2);
          if (resultStr && resultStr !== 'null') {
            existing.dataset.result = resultStr;
            this._markToolExpandable(existing);
          }
        }
      } else if (ev.phase === 'error') {
        this._toolStates[callId] = 'error';
        if (!existing) {
          existing = this._createToolElement(toolsContainer, ev, 'error');
        }
        const icon = existing.querySelector('.chat-tool-icon');
        if (icon) {
          icon.className = `chat-tool-icon error ti ${TOOL_ICONS.error}`;
        }
        // L'errore e' il *corpo* del chip come lo e' un risultato, non un blocco
        // a parte: passa da `dataset.result`, quindi nasce chiuso e si apre al
        // tocco esattamente come gli altri. Appeso al DOM com'era prima restava
        // espanso per sempre (e il chip nemmeno rispondeva al tocco, perche'
        // `_toggleToolResult` legge solo `dataset.result`), e un secondo evento
        // sullo stesso `call_id` ne appendeva una copia; un dataset si sovrascrive.
        if (ev.error) {
          existing.dataset.result = String(ev.error);
          existing.dataset.errored = '1';
          this._markToolExpandable(existing);
        }
      }
    }
  }

  _createToolElement(container, ev, state) {
    const tool = document.createElement('div');
    tool.className = 'chat-tool';
    tool.dataset.callId = ev.call_id;
    tool.style.cursor = 'default';

    const header = document.createElement('div');
    header.className = 'chat-tool-header';

    const icon = document.createElement('i');
    icon.className = `chat-tool-icon ${state} ti ${state === 'running' ? TOOL_ICONS.start : (state === 'error' ? TOOL_ICONS.error : TOOL_ICONS.end)}`;
    header.appendChild(icon);

    const name = document.createElement('span');
    name.className = 'chat-tool-name';
    name.textContent = ev.name || 'tool';
    header.appendChild(name);

    tool.appendChild(header);

    tool.addEventListener('click', () => this._toggleToolResult(tool));

    container.appendChild(tool);
    return tool;
  }

  /* Il chip ha un corpo da mostrare: da qui in poi il tocco fa qualcosa, e il
     cursore lo dice. Prima era deciso alla creazione, quando un tool avviato non
     ha ancora ne' risultato ne' errore: un `end` con `result: null` sembrava
     apribile e non lo era, un tool nato in fase `start` e concluso dopo lo era e
     non lo sembrava. */
  _markToolExpandable(tool) {
    tool.style.cursor = 'pointer';
  }

  _toggleToolResult(tool) {
    let resultEl = tool.querySelector('.tool-result-text');
    if (resultEl) {
      resultEl.remove();
      return;
    }

    const resultStr = tool.dataset.result;
    if (!resultStr) return;

    resultEl = document.createElement('pre');
    resultEl.className = 'tool-result-text' + (tool.dataset.errored === '1' ? ' is-error' : '');
    resultEl.textContent = resultStr;
    tool.appendChild(resultEl);
  }

  _handleFileEdit(edits) {
    if (!edits.length) return;
    this._ensureAiMessage();

    for (const edit of edits) {
      if (edit.phase !== 'end') continue;
      for (const p of (edit.paths || [edit.path])) {
        const stats = this._fileEditPaths.get(p) || { added: 0, deleted: 0 };
        stats.added += edit.added || 0;
        stats.deleted += edit.deleted || 0;
        this._fileEditPaths.set(p, stats);
      }
    }

    this._renderCollapsibleFileEdits(this._currentMsg, this._fileEditPaths);
    this.scrollToBottom();
  }

  _renderCollapsibleFileEdits(msg, fileMap) {
    if (!fileMap.size || !msg) return;

    const paths = Array.from(fileMap.keys());

    let container = msg.querySelector('.tool-events-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'tool-events-container collapsed';

      const header = document.createElement('div');
      header.className = 'tool-events-header';
      header.innerHTML = `<i class="ti ti-file-code"></i>
        <span class="tool-events-label">${i18n.t('chat.filesModified', { count: String(paths.length) }).replace(/^\d+\s+/, '')}</span>
        <span class="tool-events-badge">${String(paths.length)}</span>
        <i class="ti ti-chevron-down tool-events-chevron"></i>`;
      header.addEventListener('click', () => {
        container.classList.toggle('collapsed');
      });

      const body = document.createElement('div');
      body.className = 'tool-events-body file-edits-body';

      container.appendChild(header);
      container.appendChild(body);

      this._ensureMetaRow(msg).appendChild(container);
    }

    const header = container.querySelector('.tool-events-header');
    const badgeEl = header.querySelector('.tool-events-badge');
    badgeEl.textContent = String(paths.length);

    const labelEl = header.querySelector('.tool-events-label');
    labelEl.textContent = i18n.t('chat.filesModified', { count: String(paths.length) }).replace(/^\d+\s+/, '');

    const body = container.querySelector('.tool-events-body');
    body.innerHTML = '';
    for (const path of paths) {
      const stats = fileMap.get(path) || { added: 0, deleted: 0 };

      let diffHtml = '';
      if (stats.added || stats.deleted) {
        const parts = [];
        if (stats.added > 0) parts.push(`<span class="file-diff-added">+${stats.added}</span>`);
        if (stats.deleted > 0) parts.push(`<span class="file-diff-deleted">–${stats.deleted}</span>`);
        diffHtml = `<span class="file-diff-stats">${parts.join('')}</span>`;
      }

      const item = document.createElement('div');
      item.className = 'chat-file-edit';
      item.innerHTML = `<i class="ti ti-file-code"></i><span class="chat-file-edit-name">${escapeHtml(path)}</span>${diffHtml}`;
      item.addEventListener('click', async (e) => {
        e.stopPropagation();
        await this._openFileInWorkspace(path);
      });
      body.appendChild(item);
    }
  }

  _handleGoalStatus(status, startedAt) {
    sessionManager.runStartedAt = status === 'running'
      ? (startedAt || Date.now() / 1000)
      : null;
    if (status === 'running') {
      if (!this._goalBanner) {
        this._goalBanner = document.createElement('div');
        this._goalBanner.className = 'chat-goal-banner';
        this._goalBanner.innerHTML = `<i class="ti ti-loader-2"></i><span>${i18n.t('chat.agentRunning')}</span><span class="chat-goal-timer"></span>`;
        this.chatArea.appendChild(this._goalBanner);
      }
      if (this._goalTimer) clearInterval(this._goalTimer);
      const timerEl = this._goalBanner.querySelector('.chat-goal-timer');
      const start = startedAt ? startedAt * 1000 : Date.now();
      this._goalTimer = setInterval(() => {
        const elapsed = Math.floor((Date.now() - start) / 1000);
        if (timerEl) timerEl.textContent = elapsed + 's';
      }, 1000);
      this.scrollToBottom();
    } else {
      this._clearGoalBanner();
    }
  }

  /* Toglie il banner "agent running" e ferma il suo cronometro.
     Chiamato dal `goal_status: idle` — e da chi butta la vista, perché il nodo
     muore col DOM ma `_goalBanner` no: restando non-null il banner non verrebbe
     mai più ricreato (v. la guardia in `_handleGoalStatus`) e l'intervallo
     continuerebbe a battere su un nodo staccato. */
  _clearGoalBanner() {
    if (this._goalTimer) {
      clearInterval(this._goalTimer);
      this._goalTimer = null;
    }
    if (this._goalBanner) {
      this._goalBanner.remove();
      this._goalBanner = null;
    }
  }

  /* ── Pannello subagent ──────────────────────────────────────────────────
     Jenny orchestra: il lavoro vero lo fanno i subagent, fino a cinque in
     parallelo. Senza questo pannello la chat resta muta per minuti e l'utente
     non sa né cosa sta girando né come sbloccarlo.

     Il pannello mostra il LAVORO VIVO, non lo storico. Una card terminale resta
     per il turno corrente — così la transizione si vede — e sparisce a
     `turn_end`, o al più tardi alla propria scadenza di orologio, perché
     `turn_end` è un frame live che a un client in Doze non arriva mai
     (v. SA_LINGER_MS); niente di un turno passato viene mai renderizzato,
     nemmeno dopo un reload (il filtro sta in shared/subagent-policy.js). Il
     duplicato era doppio danno: l'esito è già riassunto dall'orchestratore
     nella chat, e una card recente occupava spazio sopra il composer per
     sempre.

     Le due cose che il pannello deve rendere ovvie sono `idle` (fermo ≠ al
     lavoro) e Stop. Con più di un subagent le card stanno su UNA riga che scorre
     di lato (`.is-carousel`): su uno schermo quadrato la pila verticale mangiava
     metà della chat, e l'altezza è la stessa da uno a cinque. Il dettaglio
     completo non ci sta su una card: lo apre il tap (v. _openSubagentDetail). */

  _setupSubagentPanel() {
    if (!this.subagentsEl || !this.subagentsHead || !this.subagentsBody) return;
    this.subagentsHead.addEventListener('click', () => this._toggleSubagents());
    // Event delegation: le righe vengono ricostruite a ogni snapshot, un
    // listener per bottone si perderebbe insieme al nodo.
    this.subagentsBody.addEventListener('click', (e) => {
      const btn = e.target.closest('.sa-btn');
      if (btn) {
        // stopPropagation: il tap su Stop è un'azione, non una richiesta di
        // dettaglio. Senza questo il bottone fermerebbe il job E aprirebbe la
        // modale, che è il gesto più fastidioso possibile.
        e.stopPropagation();
        if (btn.disabled || !btn.dataset.taskId) return;
        if (btn.dataset.action === 'stop') this._stopSubagent(btn.dataset.taskId, btn);
        else this._restartSubagent(btn.dataset.taskId, btn);
        return;
      }
      const row = e.target.closest('.sa-row');
      if (row && row.dataset.taskId) this._openSubagentDetail(row.dataset.taskId);
    });
    // La card è role="button": Invio/Spazio devono aprire il dettaglio come il
    // tap, altrimenti da tastiera il contenuto della modale è inaccessibile.
    this.subagentsBody.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const row = e.target.closest('.sa-row');
      if (!row || !row.dataset.taskId || e.target.closest('.sa-btn')) return;
      e.preventDefault();
      this._openSubagentDetail(row.dataset.taskId);
    });
    // A vista nascosta non c'è nulla da invecchiare: il poll si spegne e riparte
    // con una lettura immediata al ritorno in foreground (v. _syncSubagentPolling).
    document.addEventListener('visibilitychange', () => {
      const visible = document.visibilityState === 'visible';
      this._syncSubagentPolling(this._subagentHasRunning);
      if (visible) this._refreshSubagents();
    });
    // Sopravvivere al reload è il caso comune, non l'eccezione: su Android il
    // processo della WebView muore e il frame WS di transizione è già passato.
    // Al reload solo i vivi compaiono: `_subagentLiveIds` è vuoto, quindi i
    // terminati che il server serve in `recent` non ripopolano il pannello.
    this._refreshSubagents();
  }

  _toggleSubagents(force) {
    if (!this.subagentsHead || !this.subagentsBody) return;
    this._subagentsOpen = force === undefined ? !this._subagentsOpen : !!force;
    this.subagentsHead.setAttribute('aria-expanded', this._subagentsOpen ? 'true' : 'false');
    this.subagentsBody.hidden = !this._subagentsOpen;
  }

  async _refreshSubagents() {
    try {
      this._renderSubagents(await api.getSubagents());
    } catch (_) {
      // Best-effort: lo stato è ricalcolabile, il prossimo frame o poll lo porta.
    }
  }

  /* Poll solo mentre c'è qualcosa che gira *e* la vista è davanti: il manager
     pubblica uno snapshot per *transizione di stato*, quindi fra due transizioni
     elapsed/idle invecchierebbero e un job piantato sembrerebbe fermo da 3
     secondi per sempre. A zero running non c'è nulla che invecchi: timer spento.

     Questi 5 secondi restano l'orologio del pannello *coarse* e nient'altro. Lo
     stream di attività della modale non li usa e non ne aggiunge un secondo: è
     tutto push (frame `subagent_activity` solo a chi guarda), e l'HTTP lo tocca
     solo per tappare un buco dichiarato o per un digest espanso a mano. La
     ragione per cui il poll non si può togliere è che elapsed/idle/stato delle
     card non stanno nello stream: quello racconta *un* task, il pannello ne
     mostra fino a tre, e li mostra anche a modale chiusa.

     A vista nascosta il timer si spegne: in background non c'è nessun numero da
     rinfrescare, e Android strozza comunque i timer di una WebView non visibile —
     al ritorno in foreground si fa una lettura sola, che è più fresca di quanto
     sarebbero stati venti tick recuperati. */
  _syncSubagentPolling(hasRunning) {
    this._subagentHasRunning = !!hasRunning;
    const wanted = !!hasRunning && document.visibilityState !== 'hidden';
    if (wanted && !this._subagentPollTimer) {
      this._subagentPollTimer = setInterval(() => this._refreshSubagents(), 5000);
    } else if (!wanted && this._subagentPollTimer) {
      clearInterval(this._subagentPollTimer);
      this._subagentPollTimer = null;
    }
  }

  _renderSubagents(snapshot) {
    if (!this.subagentsEl || !this.subagentsBody) return;
    const running = Array.isArray(snapshot?.running) ? snapshot.running : [];
    const recent = Array.isArray(snapshot?.recent) ? snapshot.recent : [];
    this._subagentSnapshot = { running, recent };

    // `recent` arriva sempre intero dal server (lo consumano anche il tool
    // subagent_status e GET /api/subagents): è qui che diventa "solo ciò che è
    // terminato sotto gli occhi dell'utente in questo turno".
    const view = saVisibleCards(this._subagentSnapshot, this._subagentLiveIds);
    this._subagentLiveIds = view.liveIds;
    const lingering = view.lingering;
    const total = running.length + lingering.length;
    this._scheduleSubagentExpiry(view.nextExpiryMs);

    // Zero card = pannello assente, non pannello chiuso: sopra il composer
    // nemmeno i 34px dell'header collassato sono gratis.
    this.subagentsEl.hidden = total === 0;
    const stalled = running.filter(e => e.state === 'stalled');
    this.subagentsEl.classList.toggle('has-stalled', stalled.length > 0);
    if (this.subagentsCount) {
      this.subagentsCount.textContent = lingering.length
        ? i18n.t('subagents.headCountFinished', {
            running: running.length,
            finished: lingering.length,
          })
        : i18n.t('subagents.headCount', { running: running.length });
    }

    // Uno stallo nuovo apre il pannello da sé: è esattamente il momento in cui
    // l'utente deve vederlo. Se lo richiude, non lo riapriamo per lo stesso job.
    const stalledIds = stalled.map(e => e.task_id).join(',');
    if (stalledIds && stalledIds !== this._lastStalledIds) this._toggleSubagents(true);
    this._lastStalledIds = stalledIds;

    // Prima i vivi, poi i terminati che stanno lingerando: nessuna intestazione
    // di sezione, il conteggio nell'header e il colore di stato di ogni card
    // bastano. Le intestazioni costavano due righe di pannello.
    const parts = [
      ...running.map(e => this._subagentRunningRow(e)),
      ...lingering.map(e => this._subagentRecentRow(e)),
    ];
    // Carosello solo da due card in su; con una sola non c'è niente da
    // scorrere e la riga prende tutta la larghezza.
    this.subagentsBody.classList.toggle('is-carousel', total > 1);
    this.subagentsBody.classList.toggle('is-single', total === 1);
    // innerHTML azzera lo scorrimento: senza questo, il poll ogni 5s
    // riporterebbe il carosello all'inizio mentre l'utente sta scorrendo.
    const scrollLeft = this.subagentsBody.scrollLeft;
    this.subagentsBody.innerHTML = parts.join('');
    this.subagentsBody.scrollLeft = scrollLeft;

    // Un subagent appena terminato guadagna in chat il suo blocco "cosa ha fatto
    // davvero"; se la modale è aperta, le sue righe statiche invecchiano a ogni
    // snapshot e vanno riscritte (elapsed/idle, e lo stato che può cambiare
    // mentre la si guarda).
    this._noteFinishedSubagents(lingering);
    this._refreshSubagentDetailStatic();

    this._syncSubagentPolling(running.length > 0);
  }

  /* Fine turno: le card terminali hanno finito di esistere. Restavano solo per
     mostrare la transizione, e l'esito è già nella risposta dell'orchestratore.
     Si azzera l'insieme dei vivi e si ri-renderizza: i subagent ancora in corso
     (un job può sopravvivere al turno che l'ha lanciato) si re-iscrivono da soli
     dentro saVisibleCards, quindi non perdono il diritto a lingerare dopo. */
  _dropTerminatedSubagents() {
    this._subagentLiveIds = new Set();
    this._renderSubagents(this._subagentSnapshot);
  }

  /* L'ultimo ri-render di una card terminale: quello che la fa sparire quando
     `turn_end` non arriva (v. SA_LINGER_MS). Un timer solo, riarmato a ogni
     render sulla prima scadenza, perché è l'unico evento che resta — il poll a
     zero running è spento per scelta, e senza sveglia la scadenza sarebbe una
     regola che nessuno applica.

     Il timer non chiede niente al gateway: ri-renderizza lo snapshot che si ha
     già, così una card scaduta cade anche offline. Se Android ha strozzato il
     timer mentre la vista era nascosta, al ritorno in foreground il
     visibilitychange fa comunque una lettura. */
  _scheduleSubagentExpiry(nextExpiryMs) {
    if (this._subagentExpiryTimer) {
      clearTimeout(this._subagentExpiryTimer);
      this._subagentExpiryTimer = null;
    }
    if (!Number.isFinite(nextExpiryMs)) return;
    // +50ms: un timer che scatta *sul* confine trova la card ancora fresca di
    // un millisecondo e si riarma a zero, in un ciclo che non finisce.
    this._subagentExpiryTimer = setTimeout(() => {
      this._subagentExpiryTimer = null;
      this._renderSubagents(this._subagentSnapshot);
    }, Math.max(0, nextExpiryMs) + 50);
  }

  /* Traduzione con fallback sul valore grezzo: phase e state arrivano dal
     backend e possono guadagnare valori nuovi prima dei file i18n. */
  _saEnum(group, value) {
    const raw = String(value || '');
    if (!raw) return '';
    const key = `subagents.${group}.${raw}`;
    const translated = i18n.t(key);
    return translated === key ? raw : translated;
  }

  _saDuration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    if (mins < 60) return `${mins}m ${String(total % 60).padStart(2, '0')}s`;
    return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`;
  }

  /* Riga alta della card: solo etichetta e stato. Il tipo di agente sta nella
     meta (v. _saTypeChip): in carosello la card è larga ~290px e la pillola qui
     rubava 50px all'etichetta, che è l'unica cosa che dice *quale* job è. */
  _saHead(entry) {
    const label = escapeHtml(entry.label || i18n.t('subagents.untitled'));
    return `<div class="sa-top">` +
      `<span class="sa-label">${label}</span>` +
      `<span class="sa-state">${escapeHtml(this._saEnum('state', entry.state))}</span>` +
    `</div>`;
  }

  _saTypeChip(entry) {
    const type = escapeHtml(entry.agent_type || '');
    return type ? `<span class="sa-type">${type}</span>` : '';
  }

  _subagentRunningRow(entry) {
    const state = String(entry.state || 'running');
    // elapsed e idle aprono la riga meta e non si comprimono: sono i due numeri
    // che rispondono a "è fermo o sta lavorando?", e accostati la risposta si
    // legge senza fare conti. Troncabile è solo la coda (fase, iterazione,
    // ultimo tool), perché l'ellipsis mangia sempre da destra.
    const rest = [
      this._saEnum('phase', entry.phase),
      `#${Number(entry.iteration) || 0}`,
      entry.last_tool ? String(entry.last_tool) : '',
    ].filter(Boolean).join(' · ');
    const idleWarn = state === 'stalled' ? ' sa-idle-warn' : '';
    const meta = `<div class="sa-meta">` +
      this._saTypeChip(entry) +
      `<span class="sa-clock">${escapeHtml(this._saDuration(entry.elapsed_s))}</span>` +
      `<span class="sa-idle${idleWarn}">` +
        escapeHtml(i18n.t('subagents.idle', { duration: this._saDuration(entry.idle_s) })) +
      `</span>` +
      (rest ? `<span class="sa-rest">${escapeHtml(rest)}</span>` : '') +
    `</div>`;
    const hint = state === 'stalled'
      ? `<div class="sa-hint">${escapeHtml(i18n.t('subagents.stalledHint'))}</div>`
      : '';
    return this._saCard(entry, state, this._saHead(entry) + meta + hint);
  }

  _subagentRecentRow(entry) {
    const state = String(entry.state || 'done');
    const rest = [
      i18n.t('subagents.attempt', { n: Number(entry.attempt) || 1 }),
      entry.stop_reason ? String(entry.stop_reason) : '',
    ].filter(Boolean).join(' · ');
    const meta = `<div class="sa-meta">` +
      this._saTypeChip(entry) +
      `<span class="sa-rest">${escapeHtml(rest)}</span>` +
    `</div>`;
    // Una riga di riassunto e nulla più: la card terminale vive un turno, e chi
    // vuole l'esito intero lo apre con un tap. La nota sul tetto dei rilanci
    // automatici è finita nella modale, dove sta accanto al bottone che spiega.
    const summary = entry.result_summary
      ? `<div class="sa-summary">${escapeHtml(String(entry.result_summary))}</div>`
      : '';
    return this._saCard(entry, state, this._saHead(entry) + meta + summary);
  }

  /* Card: testo a sinistra, azioni ammesse dallo stato a destra (v.
     shared/subagent-policy.js). Con zero azioni la colonna destra non viene
     emessa affatto e la card si accorcia ai suoi 40px di testo — è il guadagno
     di aver toccato la matrice, e serve un tap sulla card per il resto. */
  _saCard(entry, state, mainHtml) {
    const taskId = escapeHtml(String(entry.task_id || ''));
    const buttons = saActions(state, 'card').map(action => (
      action === 'stop'
        ? this._saButton(taskId, 'stop', 'ti-player-stop', 'subagents.stop')
        : this._saButton(taskId, 'restart', 'ti-refresh', 'subagents.relaunch')
    ));
    const actions = buttons.length ? `<div class="sa-actions">${buttons.join('')}</div>` : '';
    return `<div class="sa-row state-${escapeHtml(state)}" data-task-id="${taskId}" ` +
      `role="button" tabindex="0" aria-label="${escapeHtml(i18n.t('subagents.openDetail'))}">` +
      `<div class="sa-main">${mainHtml}</div>` +
      actions +
    `</div>`;
  }

  /* Bottone icona: l'etichetta vive in aria-label/title, non come testo, così
     i controlli stanno nella colonna destra della card senza rinunciare al
     target da 40px né al nome per lo screen reader. */
  _saButton(taskId, action, icon, labelKey) {
    const label = escapeHtml(i18n.t(labelKey));
    const cls = action === 'stop' ? 'sa-btn sa-btn-stop' : 'sa-btn';
    return `<button class="${cls}" type="button" data-action="${action}" ` +
      `data-task-id="${taskId}" title="${label}" aria-label="${label}">` +
      `<i class="ti ${icon}"></i></button>`;
  }

  /* ── Modale di dettaglio ────────────────────────────────────────────────
     La card sta su una riga e mostra etichetta, orologi e stato: tutto il resto
     — il task per intero, l'esito completo, e soprattutto lo *stream di ciò che
     il subagent sta facendo adesso* — sta qui. È ciò che giustifica una card
     così piccola. Le azioni sono quelle della matrice per la superficie 'modal':
     in più della card c'è Rilancia su un job fallito, che sulla card sarebbe un
     tap troppo facile per un'operazione che di solito rifallisce identica.

     La prima versione impilava tutto in fila: otto righe chiave/valore, lo
     stream, l'incarico, la coda tool, l'esito. Su uno schermo quadrato era
     illeggibile per tre motivi precisi, e sono i tre che questa forma toglie:
     niente dominava (tutto 10-11px dello stesso peso), la storia dei tool era
     raccontata DUE volte (la coda ferma dello snapshot e lo stream vivo), e i
     contenitori di scroll erano annidati tre volte (corpo, lista, `<pre>`).

     Adesso la modale è un TELAIO, non una pagina: un posto per ogni domanda, e
     una domanda per posto.

       #sa-sum     una riga  — stato, da quanto, fermo da quanto, tipo
       #sa-focus   l'esito   — solo a lavoro finito: è LA risposta, sta in alto
       #sa-stream  attività  — l'unica parte che scorre e l'unica che cresce
       #sa-more    pieghe    — incarico e diagnostica, chiuse per default

     Cosa è sparito, e perché non manca: fase e iterazione (lo stream le emette
     già come eventi, con l'ora), la coda tool dello snapshot come blocco a sé
     (ora è il contenuto di ripiego della lista quando lo stream non ha nulla),
     e la ginnastica di scroll per far vedere lo stream — in un telaio la parte
     viva è in vista per costruzione. */
  _openSubagentDetail(taskId) {
    // Una modale sola: se ne è già aperta una, `detailDialog` rifiuterebbe e
    // resteremmo con un watch appeso a un corpo che non è nostro.
    if (this._saStream) return;
    const entry = this._findSubagentEntry(taskId);
    if (!entry) return;
    const state = String(entry.state || '');
    const closed = detailDialog({
      title: String(entry.label || i18n.t('subagents.untitled')),
      bodyHtml: this._subagentDetailHtml(entry, state),
      actions: this._saDialogActions(state),
    });
    // La modale si è aperta ⇒ il nostro guscio è nel DOM. È il solo modo di
    // saperlo senza che detailDialog cambi contratto.
    if (!document.getElementById('sa-stream')) return;
    this._attachSubagentStream(taskId);
    // Prima passata sulle parti che vivono nel DOM (esito, nota sul tetto): il
    // markup le crea vuote, e riempirle da un posto solo evita che l'apertura e
    // il refresh possano dire due cose diverse.
    this._refreshSubagentDetailStatic();
    closed.then((chosen) => {
      // Ogni via d'uscita passa da qui: X, backdrop, Esc e gesto Indietro di
      // Android risolvono la stessa Promise (v. shared/dialog.js), quindi non
      // esiste una chiusura che lasci il gateway a spingere frame a nessuno.
      this._detachSubagentStream();
      if (chosen === 'stop') this._stopSubagent(taskId);
      else if (chosen === 'restart') this._restartSubagent(taskId);
    });
  }

  _findSubagentEntry(taskId) {
    // Id vuoto = nessuno, non "il primo senza id": un entry malformato non deve
    // poter diventare la voce che la modale mostra.
    if (!taskId) return null;
    const snap = this._subagentSnapshot || {};
    const all = [...(snap.running || []), ...(snap.recent || [])];
    return all.find(e => String(e?.task_id || '') === String(taskId)) || null;
  }

  _saDialogActions(state) {
    return saActions(state, 'modal').map(action => (
      action === 'stop'
        ? { id: 'stop', label: i18n.t('subagents.stop') }
        : { id: 'restart', label: i18n.t('subagents.relaunch'), variant: 'primary' }
    ));
  }

  /* Le quattro zone del telaio, in quest'ordine. `#sa-focus` nasce vuoto e
     nascosto perché l'esito esiste solo a lavoro finito — e un lavoro può
     finire con la modale aperta, quindi il posto deve essere già lì. */
  _subagentDetailHtml(entry, state) {
    return `<div class="sa-sum is-${escapeHtml(state)}" id="sa-sum">` +
        this._saSumHtml(entry, state) +
      `</div>` +
      `<div class="sa-focus" id="sa-focus" hidden></div>` +
      this._saStreamShellHtml() +
      `<div class="sa-more" id="sa-more">${this._saFoldsHtml(entry)}</div>`;
  }

  /* Riepilogo: una riga per le sole cose che si guardano SEMPRE — stato, da
     quanto va, da quanto è fermo, che tipo di agente è. Erano cinque righe
     chiave/valore incolonnate, e incolonnate costavano mezzo schermo per dire
     quello che accostato si legge in un colpo: "in corso · 4m 10s · fermo 2s".
     Il pallino ripete lo stato in forma, non in lettere: è il colore che si
     vede prima di leggere, ed è l'unico posto della modale che ce l'ha. */
  _saSumHtml(entry, state) {
    const parts = [
      `<span class="sa-sum-state"><i class="sa-sum-dot"></i>` +
        `${escapeHtml(this._saEnum('state', state))}</span>`,
    ];
    if (entry.elapsed_s !== undefined) {
      parts.push(`<span class="sa-clock">${escapeHtml(this._saDuration(entry.elapsed_s))}</span>`);
      // "Fermo da" solo mentre è in gioco: accostato all'elapsed distingue "al
      // lavoro da 4 minuti" da "piantato da 4 minuti", ed è la ragione per cui i
      // due numeri stanno vicini. Su un lavoro concluso non significa niente —
      // "concluso · 2m 04s · fermo 0s" è una riga che si legge due volte per
      // scoprire che la terza voce non diceva nulla.
      if (!saIsTerminal(state)) {
        parts.push(`<span class="sa-idle">` +
          escapeHtml(i18n.t('subagents.idle', { duration: this._saDuration(entry.idle_s) })) +
        `</span>`);
      }
    }
    if (entry.agent_type) {
      parts.push(`<span class="sa-type">${escapeHtml(String(entry.agent_type))}</span>`);
    }
    // Lo stallo è l'unico stato che chiede qualcosa a chi guarda: la riga che lo
    // spiega sta sotto il riepilogo, non in una piega.
    const hint = state === 'stalled'
      ? `<span class="sa-sum-hint">${escapeHtml(i18n.t('subagents.stalledHint'))}</span>`
      : '';
    return parts.join('') + hint;
  }

  /* Esito in alto, e solo a lavoro finito. Mentre gira, la domanda è "cosa sta
     facendo" e la risposta è lo stream; quando ha finito, la domanda è "com'è
     andata" e la risposta è questa — che prima era un `<pre>` monospazio in
     fondo, dopo l'incarico, cioè l'ultima cosa raggiungibile della modale.
     `error` vince su `result_summary`: se c'è, è l'esito. */
  _saFocusView(entry, state) {
    const error = String(entry.error || '');
    const text = error || String(entry.result_summary || '');
    if (!text || !saIsTerminal(state)) return { show: false, cls: '', text: '' };
    const bad = !!error || state === 'failed';
    return { show: true, cls: bad ? 'is-error' : (state === 'done' ? 'is-ok' : ''), text };
  }

  _saApplyFocus(entry, state) {
    const el = document.getElementById('sa-focus');
    if (!el) return;
    const view = this._saFocusView(entry, state);
    const sig = view.show ? `${view.cls}|${view.text}` : '';
    // Firma e non riscrittura cieca: l'esito è testo lungo che scorre dentro sé,
    // e rifarlo a ogni snapshot riporterebbe in cima chi lo sta leggendo.
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;
    el.hidden = !view.show;
    el.className = `sa-focus ${view.cls}`.trim();
    el.innerHTML = view.show
      ? `<div class="sa-focus-key">${escapeHtml(i18n.t('subagents.detail.outcome'))}</div>` +
        `<div class="sa-focus-text">${escapeHtml(view.text)}</div>`
      : '';
  }

  /* Pieghe: l'incarico (testo che l'utente ha già scritto lui) e la diagnostica
     (tipo, tentativo, lignaggio, fase, iterazione, motivo di uscita). Servono, ma
     non a colpo d'occhio: aperte in cima costavano la metà utile della modale
     per rispondere a domande che nessuno stava facendo.

     `<details>` nativo e non un toggle nostro: tiene lo stato aperto/chiuso da
     sé, e quindi sopravvive al refresh dello snapshot senza che il refresh debba
     saperlo. */
  _saFoldsHtml(entry) {
    let html = '';
    if (entry.task) {
      html += this._saFold('subagents.detail.task',
        `<pre class="sa-detail-pre">${escapeHtml(String(entry.task))}</pre>`);
    }
    html += this._saFold('subagents.detail.diagnostics',
      `<div id="sa-diag">${this._saDiagRowsHtml(entry)}</div>`);
    // Il rilancio manuale non è mai rifiutato: `can_restart` è il tetto dei
    // tentativi *automatici*. Quando è esaurito lo si dice qui, in fondo, accanto
    // al bottone che chiarisce.
    html += `<p class="sa-detail-note" id="sa-cap-note" hidden></p>`;
    return html;
  }

  _saFold(labelKey, innerHtml) {
    return `<details class="sa-fold">` +
      `<summary class="sa-fold-head">` +
        `<i class="ti ti-chevron-right sa-fold-chevron"></i>` +
        `<span>${escapeHtml(i18n.t(labelKey))}</span>` +
      `</summary>` +
      `<div class="sa-fold-body">${innerHtml}</div>` +
    `</details>`;
  }

  _saDiagRowsHtml(entry) {
    const rows = [];
    const row = (labelKey, value) => {
      if (value === '' || value === null || value === undefined) return;
      rows.push(
        `<div class="sa-detail-row">` +
          `<span class="sa-detail-key">${escapeHtml(i18n.t(labelKey))}</span>` +
          `<span class="sa-detail-val">${escapeHtml(String(value))}</span>` +
        `</div>`
      );
    };
    row('subagents.detail.type', entry.agent_type || '');
    row('subagents.detail.attempt', `${Number(entry.attempt) || 1} · ${entry.lineage_id || '—'}`);
    if (entry.phase) row('subagents.detail.phase', this._saEnum('phase', entry.phase));
    if (entry.iteration !== undefined) row('subagents.detail.iteration', Number(entry.iteration) || 0);
    if (entry.stop_reason) row('subagents.detail.stopReason', entry.stop_reason);
    return rows.join('');
  }

  /* Riscritto a ogni snapshot (5s) è SOLO ciò che invecchia: riepilogo, esito,
     diagnostica, nota sul tetto e i bottoni della testata. Le pieghe come nodi
     no — un `innerHTML` sul loro contenitore le richiuderebbe in faccia a chi sta
     leggendo l'incarico, che è esattamente quello che faceva la prima versione
     (e con esso azzerava lo scroll del `<pre>` ogni cinque secondi). */
  _refreshSubagentDetailStatic() {
    if (!this._saStream) return;
    const entry = this._findSubagentEntry(this._saStream.taskId);
    if (!entry) return;
    const state = String(entry.state || '');
    const sum = document.getElementById('sa-sum');
    if (sum) {
      sum.className = `sa-sum is-${state}`;
      sum.innerHTML = this._saSumHtml(entry, state);
    }
    this._saApplyFocus(entry, state);
    // Le righe di diagnostica non hanno stato da preservare (nessuno scroll,
    // nessun input): la piega che le contiene sì, e quella non si tocca.
    const diag = document.getElementById('sa-diag');
    if (diag) diag.innerHTML = this._saDiagRowsHtml(entry);
    this._saApplyCapNote(entry);
    this._saSyncActions(state);
  }

  _saApplyCapNote(entry) {
    const el = document.getElementById('sa-cap-note');
    if (!el) return;
    const text = entry.can_restart === false
      ? i18n.t('subagents.autoCapReached', { attempt: Number(entry.attempt) || 1 })
      : '';
    el.hidden = !text;
    if (el.textContent !== text) el.textContent = text;
  }

  /* I bottoni della testata seguono lo stato. Un subagent può concludersi con la
     modale aperta, e "Ferma" su un lavoro finito non è un bottone inutile: è una
     bugia, e su un job fallito nasconde l'unico bottone che serve (Rilancia).
     `detailDialog` delega il click sul contenitore, quindi rimpiazzare i bottoni
     non stacca nessun handler — l'unico accoppiamento è il `data-action-id`, che
     è già il suo contratto. */
  _saSyncActions(state) {
    const host = document.getElementById('oc-detail-actions');
    if (!host) return;
    const actions = this._saDialogActions(state);
    const sig = actions.map(a => a.id).join(',');
    if (host.dataset.saSig === sig) return;
    host.dataset.saSig = sig;
    host.innerHTML = actions.map(a => (
      `<button type="button" data-action-id="${escapeHtml(a.id)}" ` +
        `class="oc-btn ${a.variant === 'primary' ? 'oc-btn-confirm' : 'oc-btn-cancel'}">` +
        `${escapeHtml(a.label)}</button>`
    )).join('');
  }

  /* ── Stream di attività ─────────────────────────────────────────────────
     Il motivo per cui questa fase esiste: la modale aperta su un subagent al
     lavoro era quasi immobile, e non si capiva cosa stesse facendo. Adesso il
     gateway produce una telemetria fine (una riga curata per tool, per
     ragionamento, per iterazione) e la spinge SOLO a chi guarda: aprire la
     modale è ciò che accende il flusso, chiuderla è ciò che lo spegne.

     Le regole del filo — append o rimpiazza, cursore, buco dichiarato,
     accoppiamento start/end, collasso dei run di ragionamento — stanno tutte in
     shared/subagent-policy.js, senza DOM, dove un test le esegue. Qui c'è solo
     il DOM: guscio, righe, indicatore di stato, e la disciplina dello scroll. */

  _saStreamShellHtml() {
    const t = (key) => escapeHtml(i18n.t(`subagents.activity.${key}`));
    return `<div class="sa-stream" id="sa-stream">` +
      `<div class="sa-stream-head">` +
        `<span class="sa-stream-title">${t('title')}</span>` +
        `<span class="sa-stream-live" id="sa-stream-live">` +
          `<i class="sa-stream-dot"></i><span class="sa-stream-live-label"></span>` +
        `</span>` +
        `<span class="sa-stream-count" id="sa-stream-count"></span>` +
      `</div>` +
      `<div class="sa-stream-note" id="sa-stream-note" hidden>` +
        `<span class="sa-stream-note-text"></span>` +
        `<button class="sa-stream-resume" id="sa-stream-resume" type="button" hidden>` +
          `${t('resume')}</button>` +
      `</div>` +
      `<div class="sa-stream-list" id="sa-stream-list" aria-live="polite"></div>` +
      `<button class="sa-stream-jump" id="sa-stream-jump" type="button" hidden>` +
        `<i class="ti ti-arrow-down"></i>${t('jump')}</button>` +
    `</div>`;
  }

  _attachSubagentStream(taskId) {
    this._saStream = saActivityInit(taskId);
    this._saStick = true;
    this._saResyncing = false;
    this._saWatching = false;
    this._saListEl = document.getElementById('sa-stream-list');
    this._saShellEl = document.getElementById('sa-stream');

    /* Auto-scroll solo se l'utente è in fondo. Se è risalito a leggere una riga,
       strappargli la vista a ogni frame (2.5 volte al secondo) renderebbe lo
       stream inutilizzabile proprio quando serve: quello che si guadagna in
       "vivo" si perderebbe in "leggibile". La pillola in fondo è il modo di
       tornare, e dice che c'è roba nuova. */
    this._saOnScroll = () => {
      this._saStick = this._saAtBottom();
      this._saSyncJump();
    };
    if (this._saListEl) this._saListEl.addEventListener('scroll', this._saOnScroll);

    this._saOnClick = (e) => {
      if (e.target.closest('#sa-stream-jump')) {
        this._saStick = true;
        this._saScrollToLatest();
        this._saSyncJump();
        return;
      }
      // Il gateway ci ha sfrattati (tetto dei watch per connessione): la vista è
      // ferma e solo un nuovo watch la fa ripartire. Il bottone lo dice e lo fa.
      if (e.target.closest('#sa-stream-resume')) this._watchSubagent();
    };
    if (this._saShellEl) this._saShellEl.addEventListener('click', this._saOnClick);

    /* App in background: nessuno sta guardando, quindi si smette di guardare.
       Senza questo il gateway continuerebbe a spingere frame a una modale
       invisibile — e su Android l'app in background è la norma, non l'eccezione. */
    this._saOnVisibility = () => {
      if (!this._saStream) return;
      if (document.visibilityState === 'hidden') {
        this._unwatchSubagent();
        this._saStatus = 'paused';
        this._renderSubagentStream();
      } else {
        this._watchSubagent();
      }
    };
    document.addEventListener('visibilitychange', this._saOnVisibility);

    /* Reconnect: il gateway dimentica i watch di una connessione caduta
       (`_cleanup_connection`), quindi il watch va rifatto — e va rifatto dal
       cursore che abbiamo già, altrimenti o si duplica la lista o si perde il
       buco in mezzo. */
    this._saOnWsOpen = () => this._watchSubagent();
    wsManager.addEventListener('chat:open', this._saOnWsOpen);

    this._watchSubagent();
    this._renderSubagentStream();
  }

  _detachSubagentStream() {
    this._unwatchSubagent();
    if (this._saListEl && this._saOnScroll) {
      this._saListEl.removeEventListener('scroll', this._saOnScroll);
    }
    if (this._saShellEl && this._saOnClick) {
      this._saShellEl.removeEventListener('click', this._saOnClick);
    }
    if (this._saOnVisibility) {
      document.removeEventListener('visibilitychange', this._saOnVisibility);
    }
    if (this._saOnWsOpen) wsManager.removeEventListener('chat:open', this._saOnWsOpen);
    this._saStream = null;
    this._saListEl = null;
    this._saShellEl = null;
    this._saOnScroll = null;
    this._saOnClick = null;
    this._saOnVisibility = null;
    this._saOnWsOpen = null;
    this._saStatus = 'idle';
  }

  /* Watch (ri)mandato con il cursore corrente: idempotente lato gateway (una
     seconda watch aggiorna il cursore invece di duplicare l'iscrizione), quindi
     riaprire da foreground o da reconnect non costa nulla e non perde nulla. */
  _watchSubagent() {
    const stream = this._saStream;
    if (!stream) return;
    const sent = wsManager.sendSubagentWatch(stream.taskId, stream.cursor);
    this._saWatching = sent;
    // 'offline' non è un errore: il socket si riapre da sé e `chat:open` rifà il
    // watch. Dirlo è meglio di un indicatore verde che mente.
    this._saStatus = sent ? 'live' : 'offline';
    this._renderSubagentStream();
  }

  _unwatchSubagent() {
    const stream = this._saStream;
    if (stream && this._saWatching) wsManager.sendSubagentUnwatch(stream.taskId);
    this._saWatching = false;
  }

  _handleSubagentActivity(msg) {
    const applied = saActivityFrame(this._saStream, msg);
    if (!applied.applied) return;
    this._saStream = applied.state;
    // Un frame in volo non riporta la vista "in diretta" se intanto abbiamo
    // smesso di guardare (app in background, o sfratto dal gateway).
    if (this._saWatching) this._saStatus = 'live';
    this._renderSubagentStream();
    // Buco dichiarato dal server: si rilegge via HTTP dal cursore di *prima*
    // della finestra bucata. Se la risync non recupera (ring già sfrattato) il
    // marcatore resta, ed è giusto che resti.
    if (applied.resyncFrom !== null) this._resyncSubagentStream(applied.resyncFrom);
  }

  _handleSubagentUnwatched(msg) {
    const stream = this._saStream;
    if (!stream || String(msg.task_id || '') !== stream.taskId) return;
    if (msg.reason !== 'watch_limit') return;
    // 'client' è l'ack del nostro unwatch e non cambia nulla; 'watch_limit'
    // invece è uno sfratto: da adesso non arriva più niente, e una vista ferma
    // che si dichiara viva è peggio di una vista ferma.
    this._saWatching = false;
    this._saStatus = 'frozen';
    this._renderSubagentStream();
  }

  async _resyncSubagentStream(since) {
    if (this._saResyncing) return;
    const stream = this._saStream;
    if (!stream) return;
    this._saResyncing = true;
    try {
      const payload = await api.getSubagentActivity(stream.taskId, since);
      // La modale può essere stata chiusa o riaperta su un altro task mentre la
      // lettura era in volo: il cursore di allora non appartiene a questa lista.
      if (!this._saStream || this._saStream.taskId !== stream.taskId) return;
      const merged = saActivityIngest(this._saStream, payload);
      this._saStream = merged.state;
      this._renderSubagentStream();
    } catch (_) {
      // Best-effort: il buco resta segnato, che è l'informazione onesta.
    } finally {
      this._saResyncing = false;
    }
  }

  _renderSubagentStream() {
    const listEl = this._saListEl;
    if (!listEl || !this._saStream) return;
    // Si misura la posizione PRIMA di toccare la lista, e la misura vince
    // sull'evento `scroll`: una WebView non lo emette per uno scroll
    // programmatico, e con un frame ogni 0.4s un flag stantio significherebbe o
    // strappare la vista a chi legge o non seguire più chi è in fondo.
    this._saStick = this._saAtBottom();
    const view = saActivityRows(this._saStream);
    this._syncStreamRows(listEl, this._saStreamEntries(view));
    if (this._saStick) this._saScrollToLatest();
    this._saSyncJump();
    this._renderStreamStatus(view);
  }

  /* Tolleranza di 24px: "in fondo" deve restare vero dopo che il dito ha
     rilasciato lo slancio a un pelo dal bordo, altrimenti l'auto-scroll si
     sgancia da sé al primo swipe. */
  _saAtBottom() {
    const el = this._saListEl;
    if (!el) return true;
    return (el.scrollHeight - el.scrollTop - el.clientHeight) <= 24;
  }

  _saScrollToLatest() {
    const el = this._saListEl;
    if (el) el.scrollTop = el.scrollHeight;
  }

  _saSyncJump() {
    const btn = document.getElementById('sa-stream-jump');
    if (btn) btn.hidden = this._saStick;
  }

  /* Indicatore di stato: pallino + parola. È la differenza fra "lo stream è
     vivo" e "stai guardando una fotografia", e su quattro stati (live, in pausa
     perché l'app è in background, sfrattato dal gateway, socket giù) l'utente
     deve poter capire se aspettare o toccare qualcosa. */
  _renderStreamStatus(view) {
    // 'idle' è lo stato a modale chiusa e non ha una parola sua: se compare (un
    // render arrivato prima del primo watch) si legge come una pausa.
    const status = this._saStatus === 'idle' ? 'paused' : this._saStatus;
    const liveEl = document.getElementById('sa-stream-live');
    if (liveEl) {
      liveEl.className = `sa-stream-live is-${status}`;
      const label = liveEl.querySelector('.sa-stream-live-label');
      if (label) label.textContent = i18n.t(`subagents.activity.${status}`);
    }
    const countEl = document.getElementById('sa-stream-count');
    if (countEl) {
      countEl.textContent = view.count
        ? i18n.t('subagents.activity.count', { count: view.count })
        : '';
    }
    const noteEl = document.getElementById('sa-stream-note');
    const resumeEl = document.getElementById('sa-stream-resume');
    if (!noteEl) return;
    let note = '';
    if (status === 'frozen') note = i18n.t('subagents.activity.frozenNote');
    else if (status === 'offline') note = i18n.t('subagents.activity.offlineNote');
    noteEl.hidden = !note;
    const text = noteEl.querySelector('.sa-stream-note-text');
    if (text) text.textContent = note;
    if (resumeEl) resumeEl.hidden = status !== 'frozen';
  }

  /* Voci da rendere, chiave + firma + markup. La chiave è l'identità della riga
     (il `seq` che l'ha aperta), la firma è ciò che ne cambia: con le due, un
     frame ogni 0.4s riscrive solo la riga che si è mossa — riscrivere la lista
     intera farebbe ripartire lo spinner del tool in corso a ogni frame, cioè
     l'unica animazione che deve girare continua. */
  _saStreamEntries(view) {
    if (!view.rows.length) {
      const snapshot = this._saSnapshotEntries();
      if (snapshot.length) return snapshot;
    }
    if (view.waiting) return [this._saEmptyEntry()];
    return view.rows.map((row, index) => this._saRowEntry(row, index === 0));
  }

  /* Ripiego: lo stream non ha (ancora) nulla, ma lo snapshot porta la sua coda di
     tool. Prima era un blocco a sé — "Tool recenti", sotto l'incarico: la stessa
     storia raccontata due volte, una viva e una ferma, senza dire quale fosse
     quale. Adesso è il *contenuto* della lista quando la lista è vuota, e sparisce
     da sé al primo evento vero: una storia sola, nello stesso posto, sempre. */
  _saSnapshotEntries() {
    const entry = this._findSubagentEntry(this._saStream ? this._saStream.taskId : '');
    const events = Array.isArray(entry?.tool_events) ? entry.tool_events : [];
    return events.map((event, index) => {
      const bad = String(event?.status || '') === 'error';
      const name = String(event?.name || '');
      const detail = String(event?.detail || '');
      return {
        key: `snap${index}`,
        cls: `sa-ev kind-tool${bad ? ' is-error' : ''}`,
        sig: `${name}|${detail}|${bad ? 1 : 0}`,
        inner: `<div class="sa-ev-line">` +
          `<i class="ti ${bad ? 'ti-alert-triangle' : 'ti-check'} sa-ev-icon"></i>` +
          `<div class="sa-ev-body"><div class="sa-ev-main">` +
            `<span class="sa-ev-name">${escapeHtml(name)}</span>` +
            `<span class="sa-ev-text">${escapeHtml(detail)}</span>` +
          `</div></div>` +
        `</div>`,
      };
    });
  }

  /* Lista vuota: due frasi diverse perché sono due fatti diversi. Su un job che
     gira è un'attesa (e l'icona gira); su un job concluso senza traccia è un
     fatto compiuto, e uno spinner su un lavoro finito prometterebbe qualcosa che
     non arriverà mai. */
  _saEmptyEntry() {
    const entry = this._findSubagentEntry(this._saStream ? this._saStream.taskId : '');
    const over = saIsTerminal(String(entry?.state || ''));
    return {
      key: '__wait',
      cls: over ? 'sa-stream-wait is-over' : 'sa-stream-wait',
      sig: over ? 'over' : 'wait',
      inner: over
        ? `<i class="ti ti-minus sa-ev-icon"></i>` +
          `<span>${escapeHtml(i18n.t('subagents.digest.empty'))}</span>`
        : `<i class="ti ti-loader-2 sa-ev-icon"></i>` +
          `<span>${escapeHtml(i18n.t('subagents.activity.waiting'))}</span>`,
    };
  }

  _saRowEntry(row, isHead = false) {
    // `writing` (il testo della risposta che si sta formando) e `thinking` (il
    // ragionamento) arrivano con lo stesso kind ma sono due attività diverse per
    // chi guarda: l'etichetta del server le distingue, e qui diventano due icone.
    const kind = row.kind === 'thinking' && row.label === 'writing' ? 'writing' : row.kind;
    // `incomplete` esiste solo nel digest: il subagent è morto a metà chiamata,
    // quindi l'esito non è "fallito" ma "non lo sappiamo". Un'icona che gira
    // direbbe che sta ancora andando, che in un post-mortem è falso.
    const unknown = row.status === 'incomplete';
    let icon;
    if (row.kind === 'tool') {
      if (unknown) icon = 'ti-help-circle';
      else if (row.pending) icon = 'ti-loader-2';
      else icon = row.status === 'error' ? 'ti-alert-triangle' : 'ti-check';
    } else {
      icon = SA_EVENT_ICONS[kind] || 'ti-point';
    }
    const classes = ['sa-ev', `kind-${kind}`];
    if (unknown) classes.push('is-unknown');
    else if (row.pending) classes.push('is-pending');
    if (row.status === 'error' || row.kind === 'error') classes.push('is-error');
    if (row.kind === 'result') classes.push('is-ok');
    const duration = row.durationMs === null ? '' : this._saMs(row.durationMs);
    const hole = row.missing
      ? `<div class="sa-ev-hole">${escapeHtml(this._saHoleText(row.missing, isHead))}</div>`
      : '';
    const name = row.name && row.kind === 'tool'
      ? `<span class="sa-ev-name">${escapeHtml(row.name)}</span>`
      : '';
    // `summary` è già curato e capato a 160 caratteri dal server: si rende com'è,
    // mai ricostruito da name/status — quella riga è l'unica cosa che dice cosa
    // sta succedendo, e riscriverla qui significherebbe inventarla.
    const main = row.summary
      ? `<div class="sa-ev-main">${name}<span class="sa-ev-text">` +
        `${escapeHtml(row.summary)}</span></div>`
      : (name ? `<div class="sa-ev-main">${name}</div>` : '');
    const out = row.outcome
      ? `<div class="sa-ev-out">${escapeHtml(row.outcome)}</div>`
      : '';
    return {
      key: row.key,
      cls: classes.join(' '),
      sig: [
        row.lastSeq, row.status, row.pending ? 1 : 0, row.durationMs,
        row.repeats, row.missing, row.summary, row.outcome,
      ].join(' '),
      inner: hole +
        `<div class="sa-ev-line">` +
          `<i class="ti ${icon} sa-ev-icon"></i>` +
          `<div class="sa-ev-body">${main}${out}</div>` +
          (duration ? `<span class="sa-ev-dur">${escapeHtml(duration)}</span>` : '') +
        `</div>`,
    };
  }

  /* Eventi mancanti: quelli in testa alla lista sono "prima di qui non c'è"
     (sfratto dal ring o taglio locale), quelli in mezzo sono un buco vero —
     eventi esistiti che non siamo riusciti a recuperare nemmeno via HTTP. Sono
     due frasi diverse perché sono due fatti diversi, e nessuno dei due si tace. */
  _saHoleText(missing, isHead) {
    return i18n.t(`subagents.activity.${isHead ? 'earlier' : 'hole'}`, { count: missing });
  }

  _saMs(ms) {
    const total = Math.max(0, Math.round(Number(ms) || 0));
    if (total < 1000) return `${total}ms`;
    if (total < 60000) return `${(total / 1000).toFixed(1)}s`;
    const mins = Math.floor(total / 60000);
    return `${mins}m ${String(Math.floor((total % 60000) / 1000)).padStart(2, '0')}s`;
  }

  _saRowEl(entry) {
    const el = document.createElement('div');
    el.className = entry.cls;
    el.dataset.key = entry.key;
    el.dataset.sig = entry.sig;
    el.innerHTML = entry.inner;
    return el;
  }

  /* Diff per chiave, non innerHTML della lista: gli eventi sono append-only,
     quindi in pratica cambia solo la coda (l'ultima riga tool che si risolve, il
     run di ragionamento che aggiorna testo e durata). Riscrivere tutto azzererebbe
     lo scroll e riavvierebbe le animazioni due volte al secondo. */
  _syncStreamRows(listEl, entries) {
    const children = Array.from(listEl.children);
    let index = 0;
    for (; index < entries.length; index++) {
      const node = children[index];
      if (!node || node.dataset.key !== entries[index].key) break;
      if (node.dataset.sig !== entries[index].sig) {
        node.className = entries[index].cls;
        node.dataset.sig = entries[index].sig;
        node.innerHTML = entries[index].inner;
      }
    }
    for (let k = children.length - 1; k >= index; k--) children[k].remove();
    if (index >= entries.length) return;
    const frag = document.createDocumentFragment();
    for (; index < entries.length; index++) frag.appendChild(this._saRowEl(entries[index]));
    listEl.appendChild(frag);
  }

  /* ── Blocco "cosa ha fatto davvero" in chat ─────────────────────────────
     Il pannello mostra il lavoro vivo e sparisce; la chat è ciò che resta. Sotto
     la riga di transizione di un subagent finito compare un blocco richiudibile
     che, *solo se espanso*, chiede al gateway il digest — la condensa persistita
     con una riga per tool, durate e status. Lazy per un motivo di costo preciso:
     la maggior parte di questi blocchi non viene mai aperta, e caricarli tutti
     sarebbe una lettura da disco per riga di trace. */
  _noteFinishedSubagents(lingering) {
    for (const entry of lingering) {
      const taskId = String(entry?.task_id || '');
      // Solo transizioni *osservate* qui (è ciò che `lingering` garantisce) e una
      // volta sola: `_renderSubagents` rigira a ogni poll con lo stesso snapshot.
      if (!taskId || this._saDigestSeen.has(taskId)) continue;
      this._saDigestSeen.add(taskId);
      this._appendSubagentDigest(entry);
    }
  }

  _appendSubagentDigest(entry) {
    const taskId = String(entry.task_id || '');
    const label = String(entry.label || i18n.t('subagents.untitled'));
    const state = String(entry.state || '');
    const block = document.createElement('div');
    block.className = 'sa-digest collapsed';
    block.dataset.taskId = taskId;
    block.innerHTML =
      `<button class="sa-digest-head" type="button" aria-expanded="false">` +
        `<i class="ti ti-list-details"></i>` +
        `<span class="sa-digest-label">${escapeHtml(i18n.t('subagents.digest.toggle'))}</span>` +
        `<span class="sa-digest-sub">${escapeHtml(label)}</span>` +
        `<i class="ti ti-chevron-down sa-digest-chevron"></i>` +
      `</button>` +
      `<div class="sa-digest-body"></div>`;
    if (state === 'failed' || state === 'cancelled') block.classList.add('is-error');
    block.querySelector('.sa-digest-head')
      .addEventListener('click', () => this._toggleSubagentDigest(block));
    // Collocazione: subito sotto la riga di trace della stessa transizione
    // ("subagent done: <label>"), che il server pubblica per lo stesso evento —
    // così il blocco sta sotto il messaggio del subagent e non in fondo alla
    // chat. Senza quella riga (stato annullato, o turno già chiuso) il blocco
    // finisce nella meta-row del turno corrente.
    //
    // Il digest è metadata DI UN TURNO e ha una casa sola: una `.chat-turn-meta`,
    // esattamente come `_renderTraceRow` e `_renderToolEvents`. Se non c'è una
    // bolla corrente la si crea, invece di appendere in coda a `.chat-area`:
    // `.sa-digest` è `display: contents`, quindi lì il corpo diventerebbe un
    // flex item della colonna che scorre e — essendo esso stesso uno scroll
    // container, quindi con min-height automatica 0 — si schiaccerebbe sul
    // proprio padding assorbendo la compressione della colonna, mostrando la
    // punta dei glifi e nient'altro.
    const anchor = this._findSubagentTraceRow(label);
    if (anchor) anchor.insertAdjacentElement('afterend', block);
    else {
      this._ensureAiMessage();
      this._ensureMetaRow(this._currentMsg).appendChild(block);
    }
    this.scrollToBottom();
  }

  _findSubagentTraceRow(label) {
    const rows = this.chatArea.querySelectorAll('.chat-trace:not([data-sa-claimed])');
    for (let i = rows.length - 1; i >= 0; i--) {
      const text = rows[i].textContent || '';
      if (!/^subagent (done|failed):/.test(text)) continue;
      if (!text.endsWith(`: ${label}`)) continue;
      rows[i].dataset.saClaimed = '1';
      return rows[i];
    }
    return null;
  }

  async _toggleSubagentDigest(block) {
    const opening = block.classList.contains('collapsed');
    block.classList.toggle('collapsed', !opening);
    const head = block.querySelector('.sa-digest-head');
    if (head) head.setAttribute('aria-expanded', opening ? 'true' : 'false');
    if (!opening || block.dataset.loaded) return;
    block.dataset.loaded = '1';
    const body = block.querySelector('.sa-digest-body');
    body.innerHTML = `<div class="sa-digest-msg">` +
      `${escapeHtml(i18n.t('subagents.digest.loading'))}</div>`;
    try {
      const view = saDigestView(await api.getSubagentDigest(block.dataset.taskId));
      if (!view.show) {
        // `source: "none"`: niente da mostrare, quindi niente blocco. Un accordion
        // che si apre sul vuoto è peggio della sua assenza; il toast spiega il tap.
        block.remove();
        showToast(i18n.t('subagents.digest.empty'), 'info');
        return;
      }
      const rows = view.rows.map(row => {
        const entry = this._saRowEntry(row);
        return `<div class="${entry.cls}">${entry.inner}</div>`;
      }).join('');
      // `source: "live"` = condensa ricavata dal ring di un subagent ancora al
      // lavoro: è un'anteprima, e dirlo è l'unica differenza da una bugia.
      const note = view.live
        ? `<div class="sa-digest-live">` +
          `${escapeHtml(i18n.t('subagents.digest.live'))}</div>`
        : '';
      body.innerHTML = note + `<div class="sa-digest-rows">${rows}</div>`;
      const sub = block.querySelector('.sa-digest-sub');
      if (sub) sub.textContent = i18n.t('subagents.digest.count', { count: view.count });
    } catch (_) {
      // Ritentabile: il flag di caricamento torna giù, un secondo tap riprova.
      block.dataset.loaded = '';
      body.innerHTML = `<div class="sa-digest-msg is-error">` +
        `${escapeHtml(i18n.t('subagents.digest.failed'))}</div>`;
    }
  }

  /* `btn` è opzionale: la stessa azione arriva dalla card (e allora il bottone
     va disarmato finché la chiamata è in volo) e dalla modale, che si è già
     chiusa e non ha nulla da disabilitare. */
  async _stopSubagent(taskId, btn) {
    if (btn) btn.disabled = true;
    try {
      await api.cancelSubagent(taskId);
      showToast(i18n.t('subagents.stopped'), 'success');
    } catch (e) {
      showToast(e?.message || i18n.t('subagents.actionFailed'), 'error');
    } finally {
      if (btn) btn.disabled = false;
      this._refreshSubagents();
    }
  }

  async _restartSubagent(taskId, btn) {
    if (btn) btn.disabled = true;
    try {
      await api.restartSubagent(taskId);
      showToast(i18n.t('subagents.relaunched'), 'success');
    } catch (e) {
      showToast(e?.message || i18n.t('subagents.actionFailed'), 'error');
    } finally {
      if (btn) btn.disabled = false;
      this._refreshSubagents();
    }
  }

  /* Un rifiuto del gateway. Le parole e la famiglia le decide il modulo
     condiviso con la casa (`shared/wire-error.js`); qui si decide **dove va a
     finire**, che è la parte che i due gusci fanno uguale.

     Prima questa riga diceva `Errore: image_rejected`: il nome che quel rifiuto
     ha nel codice sorgente, mostrato a chi stava mandando una foto. E il motivo
     vero — `decode`, `size`, `too_many_files` — stava nel frame e veniva
     scartato, perché `detail || reason` non guarda mai il secondo. */
  _handleError(frame) {
    const { text, blocksSend } = describeWireError(frame, (key) => i18n.t(key));
    // Riprendere il messaggio azzera già lo stream — è un turno che non è mai
    // cominciato — quindi qui si azzera solo quando non si è ripreso niente.
    if (!blocksSend || !this._takeBackPendingSend()) this._resetStreamState();
    this._showChatError(text);
  }

  /* Il messaggio rifiutato torna indietro: la bolla se ne va e il testo torna
     nel campo, così puoi correggere invece di riscrivere.

     **Gli allegati no**, ed è voluto: l'allegato *è* la cosa che è stata
     rifiutata, e rimetterlo lì inviterebbe a rimandare lo stesso file che
     fallirà di nuovo. Il server rifiuta il lotto intero senza dire quale file
     fosse, quindi non c'è nemmeno modo di restituire solo i buoni.

     Torna `false` quando non c'è niente da riprendere (il rifiuto è arrivato
     tardi, o dopo un ricaricamento): in quel caso resta la sola riga. */
  _takeBackPendingSend() {
    const pending = this._pendingSend;
    this._pendingSend = null;
    if (!pending?.node?.isConnected) return false;
    pending.node.remove();
    // Se nel frattempo hai già scritto altro, quello vince: non si sovrascrive
    // mai il campo con del testo vecchio.
    if (!this.input.value.trim() && pending.text) {
      this.input.value = pending.text;
      this._updateSendState();
      this._updateActions();
    }
    this._resetStreamState();
    return true;
  }

  _renderAttachPreview(items) {
    const preview = document.getElementById('attach-preview');
    if (!items.length) {
      preview.style.display = 'none';
      preview.innerHTML = '';
      return;
    }
    preview.style.display = 'flex';
    // Immagini → thumbnail; qualsiasi altro file → chip con icona e nome.
    preview.innerHTML = items.map((item, i) => {
      const remove = `<button class="attach-remove" data-idx="${i}"><i class="ti ti-x"></i></button>`;
      if (item.isImage) {
        return `<div class="attach-thumb" data-idx="${i}">
            <img src="${item.data_url}" alt="${escapeHtml(item.name)}">${remove}
          </div>`;
      }
      const name = escapeHtml(item.name || 'file');
      return `<div class="attach-thumb attach-file" data-idx="${i}" title="${name}">
          <i class="ti ti-file"></i>
          <span class="attach-file-name">${name}</span>${remove}
        </div>`;
    }).join('');
    preview.querySelectorAll('.attach-remove').forEach(btn => {
      btn.addEventListener('click', () => this.imageHandler.remove(Number(btn.dataset.idx)));
    });
  }

  /* Un comando scelto dalla tendina.
   *
   * Due comportamenti, come sul menu di Telegram: quello che non prende
   * argomenti parte, quello che ne prende uno **precompila il composer** e
   * lascia scrivere. Mandare `/model` da solo non è sbagliato (stampa lo stato),
   * ma è quasi mai quel che si voleva scegliendolo da un elenco.
   *
   * `/new` passa dalla sua conferma: è la stessa azione del pulsante, e la
   * conferma vive in un posto solo. */
  runCommand(spec) {
    const command = spec?.command;
    if (!command) return;
    if (command === '/new') {
      this.startNewChat();
      return;
    }
    if (spec.arg_hint) {
      this.input.value = `${command} `;
      this.input.focus();
      // Il cursore in fondo: `focus()` da solo lo mette dove capita quando il
      // valore è stato appena riscritto.
      const end = this.input.value.length;
      this.input.setSelectionRange(end, end);
      this._autoResize();
      this._updateSendState();
      this._updateActions();
      return;
    }
    this._sendCommandLine(command);
  }

  /* Una riga di comando, senza portarsi via quel che c'era nel composer.
   *
   * Il testo che si stava scrivendo torna dov'era e gli allegati non partono
   * (v. `sendMessage`): un comando non è un messaggio, e il pulsante che lo
   * manda sta a un pollice dalla graffetta. Chi aveva scritto mezza domanda e
   * ha toccato «Nuova chat» la ritrova nella chat nuova, che è il posto giusto
   * per mandarla. */
  async _sendCommandLine(command) {
    const draft = this.input.value;
    this.input.value = command;
    await this.sendMessage({ attachments: false });
    if (!draft.trim()) return;
    this.input.value = draft;
    this._autoResize();
    this._updateSendState();
    this._updateActions();
  }

  /* `/new` con una domanda davanti.
   *
   * La conferma non è cerimonia: il pulsante sta a un pollice dalla graffetta,
   * e questa azione non si annulla — il modello dimentica, e non c'è un modo di
   * fargli ricordare. Quel che *resta* è il registro a schermo, un'interruzione
   * di pagina più in su: per questo la domanda parla di contesto e non di
   * cancellazione, che sarebbe falso.
   *
   * Passa da `sendMessage` invece di scrivere sul socket: è l'unica strada che
   * conosce lo scope, l'interruttore di scrittura e la riconnessione. La bolla
   * `/new` che disegna resta a schermo per il tempo del giro e poi sparisce con
   * lo schermo, che il confine ripulisce (v. `_handleMessage`). */
  async startNewChat() {
    if (!(await confirmDialog(
      i18n.t('chat.newChatConfirm'),
      i18n.t('chat.newChatConfirmOk'),
    ))) return;
    await this._sendCommandLine('/new');
  }

  /* `attachments: false` manda il testo e **lascia il composer com'è**: è la
     strada dei comandi (v. `_sendCommandLine`). Un comando è riconosciuto dal
     solo testo — `turn_states::_state_command` guarda `msg.content` e nient'altro
     — quindi i file allegati farebbero il viaggio fino al gateway per essere
     ignorati. Con `/new` era peggio che inutile: la riga utente di un comando
     sopravvive nel transcript quando porta media (`transcript_recorder`), e
     siccome sta nello stesso turno del confine la chat appena azzerata si
     riapriva con `/new` e le sue immagini appesi in cima. */
  async sendMessage({ attachments = true } = {}) {
    const text = this.input.value.trim();
    const media = attachments ? this.imageHandler.getImages() : [];
    if (!text && !media.length) return;

    sessionManager.ensureAttached();

    // Ogni invio apre una bolla AI nuova: se il turn_end del turno precedente
    // è andato perso (turno cancellato, riconnessione), la risposta non deve
    // accodarsi alla bolla vecchia.
    this._resetStreamState();

    /* **Prima si spedisce, poi si disegna.** Prima era il contrario: la bolla
       compariva, il campo si svuotava, gli allegati si buttavano, e *poi* si
       provava a mandare — così un socket chiuso ti lasciava una bolla che
       sembrava partita, un errore di fianco, e il testo perduto. Una bolla che
       compare e un messaggio che non arriva sono la stessa cosa vista da due
       parti, e la prima fa credere alla seconda. */
    if (!wsManager.sendToChat(sessionManager.currentKey, text, media)) {
      this._showChatError(i18n.t('chat.wsError'));
      this.input.focus();
      return;
    }

    const msg = document.createElement('div');
    msg.className = 'chat-msg chat-msg-user';
    const content = document.createElement('div');
    content.className = 'chat-content';
    content.textContent = text;
    msg.appendChild(content);
    // Renderizza gli allegati nella bolla appena inviata (thumb immagini / chip
    // file): senza questo l'anteprima del composer sparirebbe al clear.
    const entries = attachments ? this.imageHandler.getAttachmentEntries() : [];
    if (entries.length) this._renderMediaAttachments(msg, entries);
    this._setMessageSource(msg, text);
    this._appendMsgActions(msg);
    this.chatArea.appendChild(msg);
    this._autoScroll = true;
    this.scrollToBottom(true);

    if (attachments) this.imageHandler.clear();
    this.input.value = '';
    this.input.style.height = 'auto';
    this._updateSendState();
    this._updateActions();
    this.input.focus();

    /* Partito non vuol dire entrato: il gateway può ancora rifiutarlo (un
       allegato che non riesce ad aprire). Finché non arriva niente che dimostri
       il contrario, questa bolla è "in sospeso" — ed è così che un rifiuto sa
       *quale* bolla togliere, senza bisogno di un identificativo sul filo. */
    this._pendingSend = { node: msg, text };
  }

  _showChatError(text) {
    const el = document.createElement('div');
    el.className = 'chat-error';
    el.textContent = text;
    this.chatArea.appendChild(el);
    this._autoScroll = true;
    this.scrollToBottom(true);
  }

  /* Lo scroller della chat è il **documento**, non `.chat-area`
     (`:root.mode-chat` in mobile-style.css). Ragione: al tocco di un manico
     di selezione Chromium ri-deriva l'estremo fermo con un hit-test che
     ignora solo il ritaglio del viewport, mai quello di uno scroller interno;
     con la chat in un `div` scrollabile, l'estremo uscito di vista finiva sul
     composer e la selezione si prendeva tutto (v. .agent/chat-selection-root-plan.md).
     Ogni lettura e scrittura di scroll passa da qui: un solo punto da cambiare. */
  get _scroller() {
    return document.scrollingElement || document.documentElement;
  }

  scrollToBottom(force = false) {
    /* `hasSelection()` sta nella *stessa* uscita di `_userTouching`, non in una
       nuova: `_userTouching` torna false sul `touchend`, cioè nell'istante in
       cui la selezione compare, e il primo flush dopo si porterebbe via il testo
       selezionato mentre la barra di selezione è ancora su. Le chiamate con
       `force` restano tali: sono tutte intenzioni esplicite (FAB, invio,
       rientro nella vista). */
    if (!force && (!this._autoScroll || this._userTouching || hasSelection())) return;
    requestAnimationFrame(() => {
      this._scroller.scrollTop = this._scroller.scrollHeight;
    });
    this._unreadCount = 0;
    this._updateScrollFab();
  }

  /* Bottone flottante "vai in fondo": visibile solo quando l'utente è staccato
     dal fondo, con badge dei messaggi arrivati nel frattempo. */
  _updateScrollFab() {
    if (!this._fabEl) return;
    this._fabEl.classList.toggle('visible', !this._autoScroll);
    const badge = this._fabEl.querySelector('.chat-scroll-fab-badge');
    if (badge) {
      const show = !this._autoScroll && this._unreadCount > 0;
      badge.style.display = show ? '' : 'none';
      if (show) badge.textContent = this._unreadCount > 99 ? '99+' : String(this._unreadCount);
    }
  }

  /* Una tacca sul badge per ogni messaggio completato mentre si è staccati. */
  _bumpUnread() {
    if (this._autoScroll) return;
    this._unreadCount += 1;
    this._updateScrollFab();
  }

  _isNearBottom() {
    const { scrollTop, scrollHeight, clientHeight } = this._scroller;
    return scrollHeight - scrollTop - clientHeight < this._scrollThreshold;
  }

  async _renderFilePreview(filePath, container) {
    let previewEl = container.querySelector('.file-preview');
    if (previewEl) {
      previewEl.remove();
      return;
    }

    previewEl = document.createElement('div');
    previewEl.className = 'file-preview';
    previewEl.innerHTML = `<div class="file-preview-header"><i class="ti ti-loader-2 spin"></i> ${i18n.t('common.loading')}</div>`;
    container.appendChild(previewEl);

    try {
      const data = await api.fetchFilePreview(sessionManager.currentKey, filePath);
      const content = data.content || '';
      const language = data.language || 'text';
      const size = data.size || 0;
      const sizeLabel = size > 1024 ? (size / 1024).toFixed(1) + ' KB' : size + ' B';

      let highlighted;
      if (typeof hljs !== 'undefined') {
        try {
          const lang = hljs.getLanguage(language) ? language : null;
          highlighted = lang
            ? hljs.highlight(content, { language: lang }).value
            : hljs.highlightAuto(content).value;
        } catch {
          highlighted = escapeHtml(content);
        }
      } else {
        highlighted = escapeHtml(content);
      }

      const lines = highlighted.split('\n');
      const numberedLines = lines.map((line, i) =>
        `<div class="file-preview-line"><span class="file-preview-line-num">${i + 1}</span><span class="file-preview-line-code">${line}</span></div>`
      ).join('');

      previewEl.innerHTML = `
        <div class="file-preview-header">
          <span class="file-preview-path">${escapeHtml(filePath)}</span>
          <span class="file-preview-meta">${language} · ${sizeLabel}</span>
          <button class="file-preview-close" title="${i18n.t('common.close')}"><i class="ti ti-x"></i></button>
        </div>
        <div class="file-preview-content"><div class="file-preview-code">${numberedLines}</div></div>
        <div class="file-preview-actions">
          <a class="file-preview-action" href="#workspace" data-path="${escapeHtml(filePath)}"><i class="ti ti-external-link"></i> ${i18n.t('chat.openInEditor')}</a>
        </div>
      `;

      previewEl.querySelector('.file-preview-close').addEventListener('click', () => {
        previewEl.remove();
      });

      const editorLink = previewEl.querySelector('.file-preview-action');
      if (editorLink) {
        editorLink.addEventListener('click', async (e) => {
          e.preventDefault();
          await this._openFileInWorkspace(filePath);
        });
      }
    } catch (err) {
      previewEl.innerHTML = `<div class="file-preview-header"><span class="file-preview-path">${escapeHtml(filePath)}</span><span class="file-preview-meta" style="color:var(--error)">${i18n.t('chat.failedToLoad')}</span><button class="file-preview-close"><i class="ti ti-x"></i></button></div>`;
      previewEl.querySelector('.file-preview-close').addEventListener('click', () => previewEl.remove());
    }
  }

  _makeFilePathsClickable(container) {
    const filePattern = /(?<!\S)((?:\.\/|\.\.\/|[\w.-]+\/)+[\w.-]+\.\w{1,10})(?!\S)/g;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    for (const node of textNodes) {
      const text = node.textContent;
      if (!filePattern.test(text)) continue;
      filePattern.lastIndex = 0;

      const frag = document.createDocumentFragment();
      let lastIdx = 0;
      let match;
      while ((match = filePattern.exec(text)) !== null) {
        if (match.index > lastIdx) {
          frag.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
        }
        const link = document.createElement('a');
        link.className = 'chat-file-path-link';
        link.textContent = match[1];
        link.href = '#';
        link.addEventListener('click', (e) => {
          e.preventDefault();
          const msgEl = link.closest('.chat-msg');
          if (msgEl) this._renderFilePreview(match[1], msgEl);
        });
        frag.appendChild(link);
        lastIdx = match.index + match[0].length;
      }
      if (lastIdx < text.length) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      }
      node.parentNode.replaceChild(frag, node);
    }
  }

  handleAction(action) {}

  _initSessionInfo() {
    this._ensureIdentity();
    this.identityEl.style.cursor = 'pointer';
    this.identityEl.addEventListener('click', (e) => {
      e.stopPropagation();
      this._showSessionInfo();
    });
  }

  _showSessionInfo() {
    this._hideSessionInfo();

    const model = this._runtimeModel;
    const scope = sessionManager.currentScope;

    const popover = document.createElement('div');
    popover.className = 'session-info-popover';

    const channel = 'websocket';
    const sessionId = 'default';

    const brand = model ? getProviderBrand(model.provider) : null;
    const modelLabel = model
      ? `${brand?.label || model.provider || i18n.t('chat.unknown')} / ${model.model || '—'}`
      : '—';
    const modelColor = brand?.color || 'var(--text-faint)';

    const projectPath = scope?.project_path || '—';
    const accessMode = scope?.access_mode === 'full' ? i18n.t('chat.fullAccess') : scope?.access_mode ? i18n.t('chat.restricted') : i18n.t('chat.default');
    const accessIcon = scope?.access_mode === 'full' ? 'ti-lock-open' : 'ti-lock';
    const accessClass = scope?.access_mode === 'full' ? 'full' : scope?.access_mode ? 'restricted' : 'default';

    const runStartedAt = sessionManager.runStartedAt;
    const isRunning = !!runStartedAt;
    let statusTimerInterval = null;

    popover.innerHTML = `
      <div class="session-info-header">
        <span><i class="ti ti-info-circle"></i> ${i18n.t('session.info')}</span>
        <button class="session-info-close"><i class="ti ti-x"></i></button>
      </div>
      <div class="session-info-section">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.session')}</span>
          <span class="session-info-value" style="font-family:var(--font-mono);font-size:10px;">${escapeHtml(sessionId)}</span>
        </div>
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.channel')}</span>
          <span class="session-info-value">${escapeHtml(channel)}</span>
        </div>
      </div>
      <div class="session-info-section session-info-model">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.model')}</span>
          <span class="session-info-value" id="si-model-value" style="color:${modelColor}">${escapeHtml(modelLabel)}</span>
        </div>
        ${model?.preset ? `<div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.preset')}</span>
          <span class="session-info-value">${escapeHtml(model.preset)}</span>
        </div>` : ''}
      </div>
      <div class="session-info-section session-info-workspace">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.project')}</span>
          <span class="session-info-value" style="font-family:var(--font-mono);font-size:10px;word-break:break-all;">${escapeHtml(projectPath)}</span>
        </div>
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.access')}</span>
          <span class="session-info-value"><span class="scope-badge ${accessClass}"><i class="ti ${accessIcon}"></i> ${accessMode}</span></span>
        </div>
      </div>
      <div class="session-info-section session-info-status">
        <div class="session-info-row">
          <span class="session-info-label">${i18n.t('session.status')}</span>
          <span class="session-info-value" id="si-status-value">
            ${isRunning
              ? `<span style="color:var(--accent);display:inline-flex;align-items:center;gap:4px;"><i class="ti ti-loader-2 spin"></i> ${i18n.t('session.running')} <span class="session-info-timer" id="si-timer"></span></span>`
              : `<span style="color:var(--text-faint)">${i18n.t('session.idle')}</span>`}
          </span>
        </div>
      </div>
    `;

    document.body.appendChild(popover);
    this._sessionInfoPopover = popover;

    const anchor = this.identityEl;
    if (anchor) {
      const rect = anchor.getBoundingClientRect();
      popover.style.top = Math.max(8, rect.bottom + 4) + 'px';
      popover.style.left = Math.max(8, rect.left) + 'px';
    }

    if (isRunning) {
      const timerEl = popover.querySelector('#si-timer');
      const start = runStartedAt ? runStartedAt * 1000 : Date.now();
      statusTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - start) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        if (timerEl) timerEl.textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
      }, 1000);
      this._sessionInfoTimer = statusTimerInterval;
    }

    const closeBtn = popover.querySelector('.session-info-close');
    closeBtn.addEventListener('click', () => this._hideSessionInfo());

    this._sessionInfoOutsideHandler = (e) => {
      if (!popover.contains(e.target)) this._hideSessionInfo();
    };
    // Niente listener Escape locale: Esc passa dalla catena della shell
    // (handleHardwareBack -> handleBack di questo controller), che chiude già
    // il popover. Un secondo handler qui è esattamente la divergenza fra copie
    // dei livelli che quella catena esiste per eliminare.
    setTimeout(() => {
      document.addEventListener('pointerdown', this._sessionInfoOutsideHandler);
    }, 0);
  }

  _hideSessionInfo() {
    if (this._sessionInfoTimer) {
      clearInterval(this._sessionInfoTimer);
      this._sessionInfoTimer = null;
    }
    if (this._sessionInfoPopover) {
      this._sessionInfoPopover.remove();
      this._sessionInfoPopover = null;
    }
    if (this._sessionInfoOutsideHandler) {
      document.removeEventListener('pointerdown', this._sessionInfoOutsideHandler);
      this._sessionInfoOutsideHandler = null;
    }
  }

  _updateSessionInfoModel() {
    const el = this._sessionInfoPopover?.querySelector('#si-model-value');
    if (!el || !this._runtimeModel) return;
    const { provider, model } = this._runtimeModel;
    const brand = getProviderBrand(provider);
    el.textContent = `${brand.label || provider || i18n.t('chat.unknown')} / ${model || '—'}`;
    el.style.color = brand.color;
  }

}
