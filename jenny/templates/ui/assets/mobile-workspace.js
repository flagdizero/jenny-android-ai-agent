/** Mobile Workspace Controller — Finder-style folder browser with CodeMirror editor. */

import { api } from './shared/api-client.js';
import { rpc } from './shared/rpc-client.js';
import { escapeHtml, getFileExtension, showToast } from './shared/utils.js';
import { confirmDialog, promptDialog } from './shared/dialog.js';
import { i18n } from './shared/i18n.js';
import { currentTheme } from './shared/theme.js';
import { openImageLightbox } from './shared/image-lightbox.js';
import { setupLongPress } from './shared/longpress.js';
import { scopeChip } from './shared/scope-chip.js';
import { deleteProjectFlow } from './shared/project-delete.js';

const CM_THEMES = { dark: 'darcula', light: 'eclipse' };

// ── Spiegazione dei file che l'utente possiede ──
// Quasi tutti questi file nascono vuoti e il loro nome non dice a cosa servono
// né cosa *non* ci va scritto. Quella prosa stava nel template, dove non la
// leggeva nessuno (sul telefono non si apre un editor markdown) e la pagava il
// modello in ogni prompt finché il file restava uguale al template. Vive qui:
// path relativo al workspace → chiave i18n del testo.
// `SOUL.md` fa eccezione: nasce pieno, ma tre degli altri quattro testi lo
// indicano come destinazione — senza una voce sua, chi segue l'indicazione
// arriva sull'unico file del gruppo che non si spiega.
const FILE_HELP_KEYS = {
  'AGENTS.md': 'workspace.fileHelp.agents',
  'USER.md': 'workspace.fileHelp.user',
  'SOUL.md': 'workspace.fileHelp.soul',
  'HEARTBEAT.md': 'workspace.fileHelp.heartbeat',
  'memory/MEMORY.md': 'workspace.fileHelp.memory',
};

/** Testo di aiuto per un path del workspace, o '' se quel file non ne ha. */
function fileHelpText(path) {
  const key = FILE_HELP_KEYS[path];
  if (!key) return '';
  const text = i18n.t(key);
  // i18n.t() ritorna la chiave grezza quando manca la traduzione: meglio
  // niente sheet che "workspace.fileHelp.agents" stampato addosso all'utente.
  return text === key ? '' : text;
}

// ── Apple-style SVG icons for grid view ──
// Gradients defined once in a hidden SVG container injected on first use.

let _gridDefsInjected = false;

function ensureGridDefs() {
  if (_gridDefsInjected) return;
  _gridDefsInjected = true;
  const container = document.createElement('div');
  container.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden';
  container.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute">
  <defs>
    <linearGradient id="gFolderGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#F7C948"/>
      <stop offset="100%" stop-color="#E8912D"/>
    </linearGradient>
    <linearGradient id="gFolderFront" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#F5A623"/>
      <stop offset="100%" stop-color="#D4800E"/>
    </linearGradient>
    <linearGradient id="gDocGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#FAFAFA"/>
      <stop offset="100%" stop-color="#E8E8EC"/>
    </linearGradient>
    <linearGradient id="gDocFold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#D4D4D8"/>
      <stop offset="100%" stop-color="#B8B8C0"/>
    </linearGradient>
    <filter id="gShadow" x="-10%" y="-10%" width="130%" height="140%">
      <feDropShadow dx="0" dy="0.8" stdDeviation="0.6" flood-opacity="0.18"/>
    </filter>
  </defs>
</svg>`;
  document.body.appendChild(container);
}

const FOLDER_ICON_SVG = `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <g filter="url(#gShadow)">
    <path d="M3 6c0-1.1.9-2 2-2h4.5l1.5 2H19c1.1 0 2 .9 2 2v10c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V6z" fill="url(#gFolderGrad)" stroke="#D4800E" stroke-width="0.6" stroke-linejoin="round"/>
    <path d="M3 9h18v8c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V9z" fill="url(#gFolderFront)" stroke="#C47008" stroke-width="0.4"/>
  </g>
</svg>`;

function createFileIcon(bg, fg, text) {
  return `<svg class="file-icon-grid" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <g filter="url(#gShadow)">
      <path d="M6 2c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2H6z" fill="url(#gDocGrad)" stroke="#A1A1AA" stroke-width="0.5"/>
      <path d="M14.5 2v5.5H20" fill="url(#gDocFold)" stroke="#A1A1AA" stroke-width="0.5" stroke-linejoin="round"/>
    </g>
    <rect x="6" y="11" width="12" height="7" rx="1.5" fill="${bg}"/>
    <text x="12" y="16.2" text-anchor="middle" font-size="6" font-weight="700" fill="${fg}" font-family="Inter,system-ui,sans-serif">${text}</text>
  </svg>`;
}

const FILE_ICONS = {
  js:   createFileIcon('#f7df1e', '#000', 'JS'),
  ts:   createFileIcon('#3178c6', '#fff', 'TS'),
  py:   createFileIcon('#3776ab', '#fff', 'PY'),
  md:   createFileIcon('#3B82F6', '#fff', 'MD'),
  json: createFileIcon('#888',    '#fff', '{}'),
  txt:  createFileIcon('#a0a0a0','#fff', 'TXT'),
  html: createFileIcon('#e34c26', '#fff', 'HTML'),
  css:  createFileIcon('#264de4', '#fff', 'CSS'),
  sh:   createFileIcon('#4eaa25', '#fff', 'SH'),
  yaml: createFileIcon('#cb171e', '#fff', 'YML'),
  yml:  createFileIcon('#cb171e', '#fff', 'YML'),
  log:  createFileIcon('#555',    '#fff', 'LOG'),
  go:   createFileIcon('#00ADD8','#fff', 'GO'),
  rs:   createFileIcon('#dea584','#000', 'RS'),
  xml:  createFileIcon('#e34c26','#fff', 'XML'),
  sql:  createFileIcon('#f29111','#fff', 'SQL'),
};

const FILE_GENERIC_ICON = `<svg class="file-icon-grid" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <g filter="url(#gShadow)">
    <path d="M6 2c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2H6z" fill="url(#gDocGrad)" stroke="#A1A1AA" stroke-width="0.5"/>
    <path d="M14.5 2v5.5H20" fill="url(#gDocFold)" stroke="#A1A1AA" stroke-width="0.5" stroke-linejoin="round"/>
    <line x1="8" y1="12" x2="16" y2="12" stroke="#D4D4D8" stroke-width="0.8" stroke-linecap="round"/>
    <line x1="8" y1="15" x2="14" y2="15" stroke="#D4D4D8" stroke-width="0.8" stroke-linecap="round"/>
  </g>
</svg>`;

// I valori sono ciò che CodeMirror ha davvero registrato, non il nome del
// linguaggio: un modo sconosciuto non solleva, ripiega in silenzio sul modo
// nullo — ed è per questo che cinque di queste voci non evidenziavano niente
// senza che nessuno se ne accorgesse. `json`, `typescript` e `html` non hanno
// un nome nudo ma hanno un MIME, servito da un modo che è già caricato
// (javascript per i primi due, xml per il terzo); `rust` idem. Chi aggiunge una
// riga qui controlli in `index.html` che quel modo sia fra gli script caricati,
// e nel file del modo che il nome esista: `test_workspace_editor_modes.py` fa
// entrambe le cose.
const EXT_LANG = {
  js: 'javascript', ts: 'text/typescript', py: 'python', md: 'markdown',
  json: 'application/json', jsonl: 'application/json',
  html: 'text/html', css: 'css', sh: 'shell',
  yaml: 'yaml', yml: 'yaml', log: 'text', txt: 'text',
  go: 'go', rs: 'text/x-rustsrc', c: 'clike', cpp: 'clike', java: 'clike',
  xml: 'xml',
};

// Scorciatoia per i binari ovvi: si apre direttamente l'app di sistema
// senza tentare la lettura (evita di scaricare fino a 1 MB per un 415).
// NON è un gate di leggibilità: per ogni altra estensione decide il
// backend sniffando il contenuto (415 = binario → app di sistema).
// Immagini renderizzabili dalla WebView: il tap apre il lightbox interno
// (long-press → app di sistema). heic/heif potrebbero non decodificare:
// l'onerror del lightbox ripiega sull'app di sistema.
const IMAGE_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif', 'heic', 'heif',
]);

// Sopra questa soglia niente thumbnail nell'explorer (si terrebbe in memoria
// l'intera immagine solo per un'icona): resta l'icona generica.
const MAX_THUMB_BYTES = 10 * 1024 * 1024;

const KNOWN_BINARY_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'heic', 'avif',
  'mp3', 'ogg', 'wav', 'm4a', 'flac', 'opus', 'aac',
  'mp4', 'mkv', 'webm', 'avi', 'mov', '3gp',
  'pdf', 'zip', 'gz', 'tar', 'bz2', 'xz', '7z', 'rar', 'jar',
  'apk', 'so', 'db', 'sqlite', 'sqlite3',
  'woff', 'woff2', 'ttf', 'otf', 'eot',
  'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp',
  'jbk',
]);

function getFileIcon(ext) {
  ensureGridDefs();
  return FILE_ICONS[ext] || FILE_GENERIC_ICON;
}

function parentPath(path) {
  const idx = path.lastIndexOf('/');
  return idx > 0 ? path.substring(0, idx) : '';
}

export class WorkspaceController {
  constructor() {
    this.viewerEl = document.getElementById('workspace-viewer');
    /* Il breadcrumb dell'**editor**: quello dell'esploratore vive nella scheda
       di Memoria, che si ridisegna, quindi non si puo' tenere per riferimento
       qui. Questo invece sta fermo nella vista del file aperto, ed e' anche
       dove compare il bottone «Salva». */
    this.editorCrumbEl = document.getElementById('ws-breadcrumb');
    /* L'esploratore — briciole, griglia, stato vuoto — vive dentro la scheda
       «I file veri» di Memoria, che `SettingsController.render()` riscrive per
       intero a ogni apertura e a ogni salvataggio. Quindi questi tre non si
       cercano una volta sola nel documento: li riaggancia `mount()`, ed e'
       null finche' la scheda non c'e'. */
    this.breadcrumbEl = null;
    this.gridEl = null;
    this.emptyEl = null;
    this.editor = null;
    this.currentDir = '';
    this.currentPath = '';
    this.viewMode = 'explorer';
    // Monotonic navigation token: guards against stale-response races when the
    // user navigates rapidly (only the latest navigateTo() writes the grid).
    this._navToken = 0;
    // Object URL delle thumbnail correnti, revocati a ogni re-render della
    // griglia per non accumulare blob in memoria.
    this._thumbUrls = [];
    // Buffer dell'editor modificato e non salvato. Il segnale esisteva già
    // (la classe `dirty` sul pulsante Salva) ma viveva solo nel DOM: nessun
    // percorso di uscita lo leggeva, e il testo modificato finiva in un viewer
    // nascosto irraggiungibile, sovrascritto alla riapertura del file.
    this._dirty = false;
  }

  /** Aggancia l'esploratore al contenitore che la scheda di Memoria ha appena
   *  disegnato, e ricarica la cartella corrente.
   *
   *  **Riaggancia invece di ricordare.** La scheda si ridisegna per intero a
   *  ogni apertura del cassetto e dopo ogni salvataggio: i nodi di prima sono
   *  stati buttati, e un riferimento tenuto dal costruttore scriverebbe in un
   *  DOM che non e' piu' a schermo — la griglia si popolerebbe e non si
   *  vedrebbe niente.
   *
   *  La cartella invece **si ricorda**: `currentDir` sta nel controller, non
   *  nel DOM, quindi un salvataggio in un'altra scheda di Memoria non
   *  rimbalza l'utente alla radice. A tornare alla radice e' solo Home
   *  (`collapseToRoot`), che e' una richiesta esplicita.
   */
  mount(host) {
    if (!host) return;
    this.breadcrumbEl = host.querySelector('[data-ws-crumb]');
    this.gridEl = host.querySelector('[data-ws-grid]');
    this.emptyEl = host.querySelector('[data-ws-empty]');
    host.querySelector('[data-ws-new]')
      ?.addEventListener('click', () => this._showNewMenu());
    this.navigateTo(this.currentDir);
  }

  /** Questa vista adesso **e' il file aperto**, e basta: l'esploratore sta
   *  nella scheda di Memoria. Arrivarci senza un file aperto vuol dire una
   *  schermata bianca, e ci si arriva davvero — una entry `mode: workspace`
   *  rimasta nella history di un WebView mai chiuso. Non e' una destinazione
   *  lecita: si torna da dove si viene. */
  activate() {
    if (this.viewMode !== 'editor') {
      window.mobileApp?.navigateBack('memoria');
      return;
    }
    this.showEditorView();
    this._syncHeaderBack();
  }

  /* Tasto Home: collassa alla radice anche *dentro* la sezione. Senza questo
     l'editor resta montato — `activate()` lo ripropone fedelmente al rientro —
     e la griglia riapre l'ultima sottocartella invece della radice: Home
     smontava gli overlay e lasciava intatto il sotto-stato di sezione.

     L'eccezione è il buffer sporco. Home non è una richiesta di buttare via il
     lavoro, e una conferma che spunta *sopra* la schermata home chiederebbe di
     un file che non è più a schermo: l'editor resta dov'è, con dentro quello
     che c'era. Il guard di `_closeEditor` copre comunque tutte le uscite vere. */
  collapseToRoot() {
    if (this.viewMode === 'editor') {
      if (this._dirty) return;
      // `stay`: si va a casa, non si torna indietro. Senza, il teardown
      // rimanderebbe in Memoria e Home ci passerebbe sopra un istante dopo.
      this._closeEditor({ dir: '', stay: true });
      return;
    }
    if (!this.currentDir) return;
    this.currentDir = '';
    // Se la scheda non e' a schermo non c'e' niente da ridisegnare: la
    // cartella e' gia' tornata alla radice, e `mount()` legge di li'.
    if (this.gridEl) this.navigateTo('');
  }

  /* Tasto Indietro hardware, invocato dalla shell prima di toccare la history.
     Ritorna true se la pressione è stata consumata qui dentro. */
  handleBack() {
    // Il ramo «risali di una cartella» stava qui finche' l'esploratore era
    // questa vista. Adesso e' una scheda di Memoria, e quella pressione la
    // raccoglie `handleCardBack()` per conto del cassetto.
    return this._closeEditor({ hardwareBack: true });
  }

  /** Indietro premuto **dentro Memoria**, girato qui dal cassetto.
   *
   *  Risalire di una cartella viene prima di uscire dal cassetto: senza questo
   *  una sola pressione porterebbe via dall'intera schermata da tre livelli di
   *  profondita', e tutto il cammino fatto sparirebbe in un colpo. */
  handleCardBack() {
    if (this.viewMode === 'editor' || !this.currentDir) return false;
    this.navigateTo(parentPath(this.currentDir));
    return true;
  }

  /** Unico punto di smontaggio dell'editor: ci passano il back hardware, la
   *  freccia ← dell'header, i crumb del breadcrumb e qualunque reset esterno
   *  (Home). È unico apposta: il guard sul buffer sporco vale solo se non
   *  esiste una seconda strada — e prima ce n'erano due che non lo guardavano
   *  affatto.
   *
   *  `dir` è la cartella su cui atterrare (i crumb ne scelgono una precisa);
   *  null lascia quella da cui si è aperto il file.
   *
   *  `stay` vuol dire «smonta e basta, alla navigazione ci penso io»: lo passa
   *  solo Home, che porta in chat per conto suo.
   *
   *  Ritorna true se la pressione è stata consumata qui dentro:
   *   - buffer sporco → la conferma è a schermo, l'editor resta aperto e la
   *     pressione è comunque consumata (il cambiamento visibile è il dialog);
   *   - `hardwareBack` → false: sotto c'è già la entry di Memoria, da cui il
   *     file è stato aperto, e la catena ci arriva da sé; uno switchMode
   *     impilerebbe una entry *in avanti* mentre si sta andando indietro.
   *     Senza `hardwareBack` (freccia dell'header) nessuno naviga al posto
   *     nostro: si torna là a mano.
   *
   *  Qui c'era un campo che diceva *da quale sezione* si era aperto il file, e
   *  che andava azzerato lasciando la vista perché la entry promessa poteva non
   *  essere più lì sotto. Dal 21/09/2026 l'origine è una sola — l'esploratore è
   *  la scheda di Memoria, e nient'altro apre un file — quindi quel campo aveva
   *  un valore solo: uno stato che finge di variare costa i suoi azzeramenti e
   *  non paga niente. */
  _closeEditor({ hardwareBack = false, dir = null, stay = false } = {}) {
    if (this.viewMode !== 'editor') return false;

    if (this._dirty) {
      this._confirmDiscard({ dir, stay });
      return true;
    }

    /* Nella cartella da cui si è aperto il file, sempre. Qui c'era un
       `ret ? '' : this.currentDir`: con un'origine esterna si ripartiva dalla
       radice, perché l'esploratore non era la schermata da cui si veniva.
       Adesso lo è, e buttare via il cammino fatto per aprire un file sarebbe
       la cosa che l'esploratore nella scheda esiste per evitare. */
    this._resetToExplorerAt(dir !== null ? dir : this.currentDir);
    if (stay) return true;
    if (hardwareBack) return false;
    window.mobileApp?.navigateBack('memoria');
    return true;
  }

  /** Conferma di scarto delle modifiche non salvate; alla risposta affermativa
   *  ripassa dallo stesso teardown, stavolta con il buffer pulito.
   *
   *  La chiusura differita non è mai `hardwareBack`: la pressione che l'ha
   *  aperta è stata consumata dal dialog e nessuno naviga più al posto nostro,
   *  quindi tornare alla sezione d'origine tocca a noi. */
  async _confirmDiscard({ dir = null, stay = false } = {}) {
    /* La tastiera software va fatta scendere *prima* della modale. Un <dialog>
       chiuso ripristina il fuoco all'elemento che ce l'aveva prima — qui
       l'input di CodeMirror — e con quello risale l'IME: la pressione di
       Indietro successiva se la mangia la tastiera per richiudersi, e a schermo
       non cambia niente. Una pressione a vuoto in mezzo alla sequenza, cioè
       proprio ciò che la catena esiste per evitare.

       Trovato solo sul dispositivo (Titan 2): nel log di ImeTracker si vede
       `onShown` scattare subito dopo la chiusura del dialog. Nessun test sul
       sorgente poteva vederlo. */
    this.editor?.getInputField?.()?.blur();
    const confirmed = await confirmDialog(i18n.t('workspace.discardConfirm'));
    if (!confirmed) return;
    if (this.viewMode !== 'editor') return;  // uscito da un altro percorso nel frattempo
    this._dirty = false;
    this._closeEditor({ dir, stay });
  }

  // ── Navigation ──

  async navigateTo(dirPath) {
    const token = ++this._navToken;
    this.currentDir = dirPath;
    this.viewMode = 'explorer';
    this.showExplorerView();
    this._syncHeaderBack();
    // Chiuso l'editor si passa di qui anche quando la scheda non e' ancora
    // stata ridisegnata: la cartella e' registrata, il disegno lo fara'
    // `mount()`. Andare avanti a DOM staccato riempirebbe nodi gia' buttati.
    if (!this.gridEl) return;

    this.renderBreadcrumb(dirPath);

    try {
      const data = await api.listWorkspace(dirPath);
      if (token !== this._navToken) return;  // superseded by a newer navigation
      this.renderGrid(data.items || []);
    } catch (err) {
      if (token !== this._navToken) return;  // don't surface errors of stale requests
      this.gridEl.innerHTML = '';
      const sub = this.emptyEl.querySelector('.ws-empty-sub');
      if (sub) sub.textContent = i18n.t('workspace.failedToLoad') + err.message;
      this.emptyEl.style.display = '';
    }
  }

  showExplorerView() {
    this.viewerEl.classList.remove('active');
  }

  showEditorView() {
    this.viewerEl.classList.add('active');
  }

  _syncHeaderBack() {
    const header = window.mobileApp?.header;
    if (!header) return;
    if (this.viewMode === 'editor') header.showAction('ws-back');
    else header.hideAction('ws-back');
  }

  // ── Breadcrumb ──

  /** Le briciole del percorso, su **due barre diverse**.
   *
   *  Senza un nome di file sono la testa dell'esploratore, dentro la scheda di
   *  Memoria; con un nome di file sono l'intestazione del file aperto, che sta
   *  in un'altra schermata e porta anche il bottone «Salva». E' il parametro a
   *  dire quale delle due si sta disegnando: dedurlo da `viewMode` sarebbe
   *  vero oggi e falso al primo chiamante che lo imposta dopo. */
  renderBreadcrumb(dirPath, fileName) {
    const barra = fileName ? this.editorCrumbEl : this.breadcrumbEl;
    if (!barra) return;
    barra.innerHTML = '';

    const rootCrumb = document.createElement('span');
    rootCrumb.className = 'ws-crumb';
    rootCrumb.textContent = i18n.t('workspace.root');
    rootCrumb.addEventListener('click', () => this.backToExplorerAt(''));
    barra.appendChild(rootCrumb);

    const parts = dirPath ? dirPath.split('/').filter(Boolean) : [];
    let accumulated = '';

    for (let i = 0; i < parts.length; i++) {
      const sep = document.createElement('span');
      sep.className = 'ws-sep';
      sep.textContent = '\u203a';
      barra.appendChild(sep);

      accumulated = accumulated ? accumulated + '/' + parts[i] : parts[i];
      const crumb = document.createElement('span');
      crumb.className = 'ws-crumb';
      crumb.textContent = parts[i];

      const targetPath = accumulated;
      crumb.addEventListener('click', () => this.backToExplorerAt(targetPath));

      barra.appendChild(crumb);
    }

    if (fileName) {
      const sep = document.createElement('span');
      sep.className = 'ws-sep';
      sep.textContent = '\u203a';
      barra.appendChild(sep);

      const fileCrumb = document.createElement('span');
      fileCrumb.className = 'ws-crumb';
      fileCrumb.textContent = fileName;
      barra.appendChild(fileCrumb);

      const saveBtn = document.createElement('button');
      saveBtn.className = 'ws-save-btn';
      saveBtn.textContent = i18n.t('workspace.save');
      saveBtn.addEventListener('click', () => this.saveFile());
      barra.appendChild(saveBtn);
    }

    barra.scrollLeft = barra.scrollWidth;
  }

  // ── Grid rendering ──

  renderGrid(items) {
    /* I file di servizio non si elencano mai. C'era un interruttore —
       «modalità sviluppatore» — che li faceva comparire: tolto il 21/09/2026,
       e con lui l'unica condizione davanti a questo filtro. Il flag lo mette
       il server file per file (`webui/workspace_files.py`): sparisce
       l'interruttore, non la distinzione. */
    items = items.filter(i => !i.internal);
    this._thumbUrls.splice(0).forEach((u) => URL.revokeObjectURL(u));
    this.gridEl.innerHTML = '';

    if (!items.length) {
      this.emptyEl.style.display = '';
      return;
    }
    this.emptyEl.style.display = 'none';

    const dirs = items.filter(i => i.type === 'directory').sort((a, b) => a.name.localeCompare(b.name));
    const files = items.filter(i => i.type === 'file').sort((a, b) => a.name.localeCompare(b.name));

    for (const item of dirs) {
      this.gridEl.appendChild(this._createDirItem(item));
    }
    for (const item of files) {
      this.gridEl.appendChild(this._createFileItem(item));
    }
  }

  _createDirItem(item) {
    ensureGridDefs();
    const itemPath = this.currentDir ? this.currentDir + '/' + item.name : item.name;

    const el = document.createElement('div');
    el.className = 'ws-item ws-item-dir';
    el.dataset.path = itemPath;
    el.dataset.kind = 'dir';

    el.innerHTML =
      `<div class="ws-item-icon folder-icon">${FOLDER_ICON_SVG}</div>` +
      `<div class="ws-item-name">${escapeHtml(item.name)}</div>`;

    el.addEventListener('click', () => {
      // Il tap sintetico che segue il long-press non deve navigare nella
      // cartella *sotto* lo sheet appena aperto: il flag lo posa
      // setupLongPress, qui lo si consuma.
      if (el.dataset.longpress) { delete el.dataset.longpress; return; }
      this.navigateTo(itemPath);
    });

    setupLongPress(el, () => {
      this.showContextSheet({ path: itemPath, kind: 'dir', name: item.name });
    });

    return el;
  }

  _createFileItem(item) {
    const itemPath = this.currentDir ? this.currentDir + '/' + item.name : item.name;
    const ext = item.extension ? item.extension.replace('.', '') : getFileExtension(item.name);
    const icon = getFileIcon(ext);

    const el = document.createElement('div');
    el.className = 'ws-item ws-item-file';
    el.dataset.path = itemPath;
    el.dataset.kind = 'file';
    el.dataset.ext = ext;

    el.innerHTML =
      `<div class="ws-item-icon">${icon}</div>` +
      `<div class="ws-item-name">${escapeHtml(item.name)}</div>`;

    el.addEventListener('click', () => {
      // Come per le cartelle: il tap sintetico del long-press aprirebbe il file
      // sotto lo sheet appena comparso.
      if (el.dataset.longpress) { delete el.dataset.longpress; return; }
      this.openFile(itemPath, ext);
    });

    setupLongPress(el, () => {
      this.showContextSheet({ path: itemPath, kind: 'file', name: item.name });
    });

    if (IMAGE_EXTS.has(ext) && (item.size ?? 0) <= MAX_THUMB_BYTES) {
      this._loadThumb(el, itemPath);
    }

    return el;
  }

  /** Sostituisce l'icona generica con una thumbnail dell'immagine (36×36,
   *  object-fit: cover → niente stretch). Best-effort: su qualsiasi errore
   *  (fetch, formato non decodificabile tipo HEIC) resta l'icona. */
  _loadThumb(el, itemPath) {
    api.downloadWorkspaceBlob(itemPath).then((blob) => {
      const holder = el.querySelector('.ws-item-icon');
      if (!holder || !el.isConnected) return;
      const url = URL.createObjectURL(blob);
      this._thumbUrls.push(url);
      const img = document.createElement('img');
      img.className = 'ws-thumb';
      img.alt = '';
      img.addEventListener('load', () => {
        if (!el.isConnected) return;
        holder.replaceChildren(img);
      });
      img.src = url;
    }).catch(() => { /* icona generica invariata */ });
  }

  // ── Context menu ──

  showContextSheet(info) {
    document.getElementById('ws-context-title').textContent = info.name;

    // Lo sheet è uno solo e viene riusato: senza azzerare, il testo del file
    // precedente resterebbe attaccato al prossimo che ne è privo (la regola
    // `.oc-sheet-desc:empty` lo nasconde solo se è davvero vuoto).
    const descEl = document.getElementById('ws-context-desc');
    if (descEl) descEl.textContent = info.kind === 'file' ? fileHelpText(info.path) : '';

    const actions = [];

    // Tap su un file "spiegato": lo sheet è lì per il testo, non per il menu
    // completo. Una sola azione, che prosegue nell'editor — il file resta
    // raggiungibile, la spiegazione non è un muro.
    if (info.mode === 'help') {
      actions.push({ icon: 'ti-edit', label: i18n.t('workspace.fileHelp.open'), action: 'openEditor' });
    } else {
      if (info.kind === 'dir') {
        actions.push({ icon: 'ti-file-plus', label: i18n.t('workspace.newFile'), action: 'newFile' });
        actions.push({ icon: 'ti-folder-plus', label: i18n.t('workspace.newFolder'), action: 'newFolder' });
      }
      if (info.kind === 'file') {
        actions.push({ icon: 'ti-external-link', label: i18n.t('workspace.openWithSystemApp'), action: 'openExternal' });
        actions.push({ icon: 'ti-share', label: i18n.t('workspace.share'), action: 'share' });
        actions.push({ icon: 'ti-download', label: i18n.t('workspace.saveToDownloads'), action: 'saveDownloads' });
      }
      actions.push({ icon: 'ti-edit', label: i18n.t('workspace.rename'), action: 'rename' });
      actions.push({ icon: 'ti-copy', label: i18n.t('workspace.clone'), action: 'clone' });
      actions.push({ icon: 'ti-trash', label: i18n.t('workspace.delete'), action: 'delete', danger: true });
    }

    this._apriFoglio(
      actions.map(a =>
        `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
          <i class="ti ${a.icon}"></i>${a.label}
        </button>`
      ).join(''),
      (action) => this.handleSheetAction(action, info),
    );
  }

  /* Il foglio delle azioni (`ws-context-sheet`) è uno e lo usano due menu, il
     contestuale e «Nuovo»: qui si mettono i pulsanti, si aggancia la scelta
     (*onPick* riceve il `data-action`), Annulla e il backdrop, e si apre. */
  _apriFoglio(actionsHtml, onPick) {
    const sheet = document.getElementById('ws-context-sheet');
    const actionsEl = document.getElementById('ws-context-actions');
    actionsEl.innerHTML = actionsHtml;

    const cancelBtn = document.getElementById('ws-context-cancel');
    const closeSheet = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        sheet.close();
        onPick(btn.dataset.action);
      });
    });

    cancelBtn.onclick = closeSheet;
    // Ignora per un attimo il tap sintetico che segue il long-press, così non
    // richiude subito dal backdrop lo sheet appena aperto (stessa finestra di
    // grazia della sezione App). Vale anche per «Nuovo», che non nasce da un
    // long-press ma condivide il <dialog>.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) closeSheet(); };
    sheet.addEventListener('close', () => {
      cancelBtn.onclick = null;
      sheet.onclick = null;
    }, { once: true });

    sheet.showModal();
  }

  async handleSheetAction(action, info) {
    const path = info.path;

    switch (action) {
      case 'openEditor': {
        this.openFile(path, null, { skipHelp: true });
        break;
      }
      case 'openExternal': {
        this.openWithSystemApp(path, info.name);
        break;
      }
      case 'share': {
        this.shareFile(path);
        break;
      }
      case 'saveDownloads': {
        this.saveToDownloads(path);
        break;
      }
      case 'newFile':
      case 'newFolder': {
        await this._createEntry(action, path);
        break;
      }
      case 'rename': {
        // `promptDialog`, non la prompt() nativa: nella WebView dell'app i
        // dialoghi JS nativi non compaiono e ritornano null, quindi rinominare
        // non faceva niente — senza nemmeno un messaggio.
        const newName = await promptDialog(i18n.t('workspace.newName'), { initial: info.name });
        if (!newName || newName === info.name) return;
        const base = parentPath(path);
        const newPath = base ? `${base}/${newName}` : newName;
        try {
          await api.renameWorkspace(path, newPath);
          if (this.currentPath === path) {
            this.currentPath = newPath;
          }
          await this.navigateTo(this.currentDir);
        } catch (err) {
          showToast(i18n.t('workspace.error') + err.message, 'error');
        }
        break;
      }
      case 'clone': {
        try {
          await api.copyWorkspace(path);
          await this.navigateTo(this.currentDir);
        } catch (err) {
          showToast(i18n.t('workspace.error') + err.message, 'error');
        }
        break;
      }
      case 'delete': {
        /* **Un progetto non e' una cartella qualunque, e cancellarlo non e' una
           `rmtree`.** La sua conversazione vive fuori da questo albero (quattro
           file, v. `session/project_traces.py`), quindi togliere la cartella da
           qui liberava il *nome* senza liberare la chat: il progetto successivo
           creato con lo stesso nome se la riprendeva intera. Riprodotto sul
           telefono il 24/08/2026.

           Il server rifiuta ormai `/api/workspace/delete` su una radice di
           progetto, e quel rifiuto resta la garanzia meccanica — vale anche per
           un client vecchio o per una chiamata diretta. Qui non si aspetta di
           essere rifiutati: si usa la porta giusta, e la conferma dice **anche
           quanta conversazione** sta per sparire, che di una cancellazione e' la
           meta' che la rende sicura. */
        const project = await this._projectAt(path);
        if (project) return this._deleteProject(project, path);

        const confirmed = await confirmDialog(i18n.t('workspace.deleteConfirm', { name: info.name }));
        if (!confirmed) return;
        try {
          await api.deleteWorkspace(path);
          if (this.currentPath === path || this.currentPath.startsWith(path + '/')) {
            // Il file aperto è stato appena eliminato su richiesta esplicita:
            // non c'è più niente da salvare, quindi niente conferma di scarto
            // (il buffer si azzera qui, non si scavalca il teardown).
            this._dirty = false;
            this.currentPath = '';
            this.backToExplorer();
            return;
          }
          await this.navigateTo(this.currentDir);
        } catch (err) {
          showToast(i18n.t('workspace.error') + err.message, 'error');
        }
        break;
      }
    }
  }

  /** Il nome del progetto che vive esattamente in *path*, o `null`.

   *  Il confronto e' col percorso **intero**, non col nome del file: una
   *  cartella `output/viaggio` ha lo stesso basename di un progetto, e
   *  scambiarli vorrebbe dire cancellare il progetto al posto suo. La cartella
   *  delle wiki e' configurabile (`config.wiki.wikis_dir`), quindi la si chiede
   *  al server invece di scriverla qui: `dir` viaggia gia' con l'elenco.
   */
  async _projectAt(path) {
    try {
      const { dir, projects } = await api.listProjects();
      const match = (projects || []).find(it => `${dir}/${it.name}` === path);
      return match ? match.name : null;
    } catch (err) {
      // Non sapere non deve bloccare una cancellazione: si prosegue per la
      // strada generica, e se quella cartella era un progetto il rifiuto del
      // server lo dice. Fallire *chiuso* qui vorrebbe dire che un gateway
      // lento rende incancellabile qualunque cartella.
      console.warn('project lookup failed, falling back to the generic delete:', err);
      return null;
    }
  }

  /** Cancella un progetto per intero, dopo averlo detto per intero.
   *
   *  La domanda e la chiamata stanno in `shared/project-delete.js`: da quando
   *  si cancella anche dal chip dello scope i chiamanti sono due, e la frase
   *  che dice quante conversazioni si porta via deve essere la stessa in
   *  entrambi. Qui resta il *seguito*, che è di questa vista: il file aperto
   *  nell'editor e la cartella su cui si sta.
   */
  async _deleteProject(name, path) {
    if (!(await deleteProjectFlow(name))) return;
    // Il progetto non esiste piu': se la chat era la sua, il chip lo deve
    // smettere di nominare. Prima del ritorno anticipato, perche' quel ramo
    // riguarda il file aperto nell'editor e non ha niente a che vedere con lo
    // scope della conversazione.
    scopeChip.leaveIfSelected(name);
    if (this.currentPath === path || this.currentPath.startsWith(path + '/')) {
      this._dirty = false;
      this.currentPath = '';
      this.backToExplorer();
      return;
    }
    showToast(i18n.t('workspace.deletedProject', { name }), 'success');
    await this.navigateTo(this.currentDir);
  }

  // ── File editor ──

  async openFile(fullPath, ext, opts = {}) {
    ext = ext || getFileExtension(fullPath);
    const name = fullPath.split('/').pop();

    // I file spiegati mostrano prima a cosa servono: chi apre AGENTS.md senza
    // saperlo trova un editor vuoto e nessun indizio. L'azione dello sheet
    // richiama questo stesso metodo con skipHelp, quindi l'editor non diventa
    // irraggiungibile.
    if (!opts.skipHelp && fileHelpText(fullPath)) {
      this.showContextSheet({ path: fullPath, kind: 'file', name, mode: 'help' });
      return;
    }

    // Immagini: lightbox interno (la vista corrente non cambia).
    if (IMAGE_EXTS.has(ext)) {
      this.previewImage(fullPath, name);
      return;
    }

    // Binari noti: direttamente all'app di sistema, senza toccare la
    // vista corrente (l'explorer resta dov'è).
    if (KNOWN_BINARY_EXTS.has(ext)) {
      this.openWithSystemApp(fullPath, name);
      return;
    }

    let data;
    try {
      data = await api.readWorkspaceFile(fullPath);
    } catch (err) {
      // 415 = il backend ha sniffato contenuto binario → app di sistema.
      if (err.status === 415) {
        this.openWithSystemApp(fullPath, name);
        return;
      }
      this._enterEditorView(fullPath, name);
      this.renderError(err.message);
      return;
    }

    this._enterEditorView(fullPath, name);
    this.renderCodeViewer(name, data.content, ext);
  }

  /** Apre il file: e' l'unico gesto che porta fuori da Memoria.
   *
   *  Girare tra le cartelle resta nella scheda; **leggere un file** e' una
   *  schermata sua, come in qualunque gestore file — ed e' l'unica cosa
   *  rimasta in `view-workspace`. */
  _enterEditorView(fullPath, name) {
    this._dirty = false;
    this.currentPath = fullPath;
    this.viewMode = 'editor';
    this.renderBreadcrumb(this.currentDir, name);
    this.showEditorView();
    // Push, non replace: la entry di Memoria resta sotto, ed e' quella su cui
    // atterra il tasto Indietro.
    window.mobileApp?.switchMode('workspace');
  }

  /** Apre il file col viewer di sistema Android via bridge nativo.
   *  Fallback (bridge assente, es. debug da browser desktop): la vecchia
   *  schermata con il link di download. */
  openWithSystemApp(fullPath, name) {
    const bridge = window.JennyNative;
    if (bridge && typeof bridge.openFile === 'function') {
      try {
        if (bridge.openFile(fullPath)) return;
      } catch (e) { /* bridge rotto: si ripiega sul download */ }
    }
    this._enterEditorView(fullPath, name);
    this.renderBinary(name, fullPath);
  }

  /** Condivide il file con lo share sheet di sistema (bridge nativo). */
  shareFile(fullPath) {
    const bridge = window.JennyNative;
    if (bridge && typeof bridge.shareFile === 'function') {
      try {
        if (bridge.shareFile(fullPath)) return;
      } catch (e) { /* fall through */ }
    }
    showToast(i18n.t('workspace.actionFailed'), 'error');
  }

  /** Copia il file nella cartella Download di sistema (bridge nativo). */
  saveToDownloads(fullPath) {
    const bridge = window.JennyNative;
    let ok = false;
    if (bridge && typeof bridge.saveToDownloads === 'function') {
      try {
        ok = bridge.saveToDownloads(fullPath);
      } catch (e) { ok = false; }
    }
    showToast(
      ok ? i18n.t('workspace.savedToDownloads') : i18n.t('workspace.actionFailed'),
      ok ? 'success' : 'error',
    );
  }

  /** Lightbox interno per le immagini: stesso overlay della chat più una
   *  barra azioni (app di sistema / condividi / salva in Download). Se la
   *  WebView non decodifica il formato (es. HEIC) ripiega sull'app di
   *  sistema. */
  async previewImage(fullPath, name) {
    let objectUrl;
    try {
      const blob = await api.downloadWorkspaceBlob(fullPath);
      objectUrl = URL.createObjectURL(blob);
    } catch (err) {
      this.openWithSystemApp(fullPath, name);
      return;
    }

    openImageLightbox(objectUrl, {
      alt: name,
      closeLabel: i18n.t('common.close'),
      actions: [
        { act: 'open', icon: 'ti-external-link', label: i18n.t('workspace.openWithSystemApp') },
        { act: 'share', icon: 'ti-share', label: i18n.t('workspace.share') },
        { act: 'save', icon: 'ti-download', label: i18n.t('workspace.saveToDownloads') },
      ],
      onAction: (act, close) => {
        if (act === 'open') { close(); this.openWithSystemApp(fullPath, name); }
        else if (act === 'share') this.shareFile(fullPath);
        else if (act === 'save') this.saveToDownloads(fullPath);
      },
      onError: () => this.openWithSystemApp(fullPath, name),
      onClose: () => URL.revokeObjectURL(objectUrl),
    });
  }

  backToExplorer() {
    this.backToExplorerAt(this.currentDir);
  }

  /** Ingresso pubblico "torna all'explorer in questa cartella". Con un editor
   *  aperto è un'uscita dall'editor come tutte le altre, quindi passa dal
   *  teardown unico: i crumb del breadcrumb restano visibili durante la
   *  modifica di un file, e prima portavano via il buffer sporco in silenzio. */
  backToExplorerAt(dirPath) {
    if (this.viewMode === 'editor') {
      this._closeEditor({ dir: dirPath });
      return;
    }
    this._resetToExplorerAt(dirPath);
  }

  /** Smontaggio meccanico dell'editor, senza alcun guard: lo chiama solo
   *  `_closeEditor`, che il guard l'ha già applicato. */
  _resetToExplorerAt(dirPath) {
    this.currentPath = '';
    this.viewMode = 'explorer';
    this._dirty = false;
    if (this.editor) {
      this.editor.toTextArea();
      this.editor = null;
    }
    this.viewerEl.innerHTML = '<div id="code-editor-mobile"></div>';
    this.showExplorerView();
    this.navigateTo(dirPath);
  }

  async saveFile() {
    if (!this.editor || !this.currentPath) return;
    const confirmed = await confirmDialog(i18n.t('workspace.saveConfirm', { path: this.currentPath }));
    if (!confirmed) return;
    const content = this.editor.getValue();
    try {
      const btn = document.querySelector('.ws-save-btn');
      if (btn) btn.disabled = true;
      await rpc.writeWorkspaceFile(this.currentPath, content);
      this._dirty = false;
      if (btn) { btn.textContent = i18n.t('workspace.saved'); btn.classList.remove('dirty'); }
      setTimeout(() => { if (btn) btn.textContent = i18n.t('workspace.save'); }, 2000);
    } catch (err) {
      // Il motivo va mostrato, non inghiottito: un bottone che dice solo
      // "Errore" ha tenuto nascosto per mesi un salvataggio che non poteva
      // riuscire (contenuto in un header HTTP, v. ws-manager.request).
      showToast(i18n.t('workspace.error') + (err?.message || ''), 'error');
      const btn = document.querySelector('.ws-save-btn');
      if (btn) { btn.textContent = i18n.t('workspace.save'); btn.disabled = false; }
    }
  }

  renderCodeViewer(filename, content, ext) {
    const lang = EXT_LANG[ext] || 'text';

    this.viewerEl.innerHTML = '<div id="code-editor-mobile"></div>';

    if (this.editor) {
      this.editor.toTextArea();
      this.editor = null;
    }

    const textarea = document.createElement('textarea');
    textarea.value = content;
    document.getElementById('code-editor-mobile').appendChild(textarea);

    this.editor = CodeMirror.fromTextArea(textarea, {
      mode: lang,
      theme: CM_THEMES[currentTheme().scheme],
      lineNumbers: true,
      lineWrapping: true,
    });

    if (!this._themeListener) {
      this._themeListener = (e) => {
        this.editor?.setOption('theme', CM_THEMES[e.detail.scheme]);
      };
      window.addEventListener('themechange', this._themeListener);
    }

    this.editor.refresh();

    this.editor.setOption('extraKeys', {
      'Ctrl-S': () => this.saveFile(),
      'Cmd-S': () => this.saveFile(),
    });
    this.editor.on('change', () => {
      this._dirty = true;
      const btn = document.querySelector('.ws-save-btn');
      if (btn) { btn.textContent = i18n.t('workspace.save'); btn.disabled = false; btn.classList.add('dirty'); }
    });
  }

  renderBinary(filename, path) {
    this.viewerEl.innerHTML = `
      <div style="flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; color: var(--text-faint);">
        <div style="font-size: 48px; opacity: 0.5;">&#128196;</div>
        <div>${i18n.t('workspace.binaryFile')}</div>
        <a href="${escapeHtml(api.getWorkspaceDownloadUrl(path))}" download="${escapeHtml(filename)}"
           style="padding: 8px 20px; background: var(--accent); color: var(--on-accent); text-decoration: none; border-radius: var(--radius); font-size: 12px;">
          ${i18n.t('workspace.download')}
        </a>
      </div>
    `;
  }

  renderError(message) {
    this.viewerEl.innerHTML = `<div style="padding: 20px; color: var(--error);">${i18n.t('workspace.error')}${escapeHtml(message)}</div>`;
  }

  // ── Header action handler ──

  /* Un'azione sola: questa vista e' il file aperto. «Aggiorna» e «nuovo»
     erano dell'esploratore e se ne sono andati con lui — «nuovo» nel bottone
     accanto alle briciole della scheda, che chiama `_showNewMenu` da se'. */
  handleAction(action) {
    if (action !== 'ws-back') return;
    // Stesso teardown del back hardware: il guard sul buffer sporco è uno
    // solo, e da qui nessuno naviga al posto nostro.
    this._closeEditor();
  }

  _showNewMenu() {
    document.getElementById('ws-context-title').textContent = i18n.t('workspace.new');
    // Stesso foglio del menu contestuale: la spiegazione dell'ultimo file
    // aperto non deve restare sotto «Nuovo» (v. `showContextSheet`).
    const descEl = document.getElementById('ws-context-desc');
    if (descEl) descEl.textContent = '';
    this._apriFoglio(`
      <button class="oc-sheet-action" data-action="newFile">
        <i class="ti ti-file-plus"></i>${i18n.t('workspace.newFile')}
      </button>
      <button class="oc-sheet-action" data-action="newFolder">
        <i class="ti ti-folder-plus"></i>${i18n.t('workspace.newFolder')}
      </button>
    `, (action) => this._handleNewAction(action));
  }

  async _handleNewAction(action) {
    await this._createEntry(action, this.currentDir);
  }

  /** Crea un nuovo file o cartella sotto `baseDir` chiedendo il nome all'utente. */
  async _createEntry(action, baseDir) {
    const isFile = action === 'newFile';
    // Come per il rename: la prompt() nativa non compare nella WebView.
    const name = await promptDialog(
      i18n.t(isFile ? 'workspace.fileName' : 'workspace.folderName'),
    );
    if (!name) return;
    const newPath = baseDir ? `${baseDir}/${name}` : name;
    try {
      if (isFile) {
        await rpc.writeWorkspaceFile(newPath, '');
      } else {
        await api.createWorkspaceFolder(newPath);
      }
      await this.navigateTo(this.currentDir);
    } catch (err) {
      showToast(i18n.t('workspace.error') + err.message, 'error');
    }
  }
}
