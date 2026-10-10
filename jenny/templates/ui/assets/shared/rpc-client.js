/** Comandi con payload verso il gateway (RPC sul WebSocket).
 *
 *  Gemello di `api-client.js` e divisione del lavoro precisa:
 *
 *    - `api`  → letture e operazioni con parametri corti, su /api/ (HTTP GET);
 *    - `rpc`  → operazioni che portano *contenuto* (il testo di un file, una
 *               nota libera), sul WebSocket.
 *
 *  Il motivo non è stilistico. La superficie /api/ del gateway è servita
 *  dall'hook di handshake di `websockets`, che non legge mai il body di una
 *  richiesta: i parametri possono viaggiare solo nella query string o negli
 *  header, dove stanno 8192 byte per riga e solo caratteri ISO-8859-1 —
 *  `new Headers()` rifiuta un'emoji prima ancora di spedire. Salvare `SOUL.md`
 *  da lì era impossibile. Un frame WebSocket invece è framed e UTF-8.
 *
 *  Ogni metodo qui corrisponde a un comando in `jenny/webui/commands.py`.
 *
 *  Qui passano anche le operazioni **distruttive** del file manager
 *  (`workspace.delete`/`rename`/`copy`), che non portano contenuto ma cambiano
 *  il disco: fino al 26/09/2026 erano GET di /api/, superficie di sola lettura
 *  che un `<img src>` con il token nell'indirizzo poteva raggiungere.
 */

import { wsManager } from './ws-manager.js';

// Quanto aspettare che il socket si apra prima di lasciar rispondere `request`
// (che, a socket chiuso, rifiuta con «gateway offline»).
const OPEN_TIMEOUT_MS = 8000;
// `WebSocket.OPEN`, scritto come numero: fuori dal browser (i banchi node)
// `WebSocket` non esiste.
const WS_OPEN = 1;

/** Il socket aperto, se si puo' aprire.
 *
 *  Il WebSocket della chat lo apre il controller della chat, e i controller
 *  dell'officina nascono pigri: nell'onboarding, o in un'officina aperta
 *  direttamente sul file manager, nessuno lo aveva ancora aperto e ogni comando
 *  rifiutava con «gateway offline» su un gateway vivo. Da quando qui passano
 *  anche le chiavi del provider e i comandi del file manager, quel caso non
 *  e' piu' raro. `connectChat` e' idempotente. Un
 *  `wsManager` senza `connectChat` (i finti dei banchi) si usa com'e'. */
function whenOpen() {
  if (typeof wsManager.connectChat !== 'function') return Promise.resolve();
  if (wsManager.chatWs?.readyState === WS_OPEN) return Promise.resolve();
  wsManager.connectChat();
  if (wsManager.chatWs?.readyState === WS_OPEN) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      wsManager.removeEventListener('chat:open', done);
      resolve();
    };
    const timer = setTimeout(done, OPEN_TIMEOUT_MS);
    wsManager.addEventListener('chat:open', done);
  });
}

async function send(method, params, opts) {
  await whenOpen();
  return opts ? wsManager.request(method, params, opts) : wsManager.request(method, params);
}

export const rpc = {
  /** Una scrittura della bacheca di un quaderno: i comandi `board.*`
   *  (`jenny/webui/board_commands.py`). `wiki` e' il nome del quaderno; il
   *  resto dei parametri dipende dal comando. */
  board(method, wiki, params = {}) {
    return send(`board.${method}`, { wiki, ...params });
  },

  /** Il cestino delle app (`apps.trash`, `apps.restore`, `apps.purge`):
   *  `jenny/webui/apps_commands.py`. */
  apps(method, params = {}) {
    return send(`apps.${method}`, params);
  },

  /** Salva un file di testo del workspace (tetto 1 MB, lato server).
   *
   *  `base`, facoltativo, e' il testo da cui l'editor e' partito: se il file su
   *  disco non e' piu' quello il server risponde `conflict` e non scrive. Per
   *  `config.json` e' obbligatorio, e la risposta porta in `content` il testo
   *  che il server ha scritto davvero — la base del salvataggio successivo. */
  writeWorkspaceFile(path, content, base) {
    return send('workspace.write', base === undefined ? { path, content } : { path, content, base });
  },

  /** Cancella un file o una cartella del workspace. Un progetto no: il server
   *  lo rifiuta e la strada e' `deleteProject`. */
  deleteWorkspace(path) {
    return send('workspace.delete', { path });
  },

  /** Rinomina (o sposta) un file o una cartella del workspace. */
  renameWorkspace(oldPath, newPath) {
    return send('workspace.rename', { old_path: oldPath, new_path: newPath });
  },

  /** Copia un file o una cartella. Senza `dest` la copia va accanto
   *  all'originale con un nome libero, scelto dal server. */
  copyWorkspace(path, dest) {
    return send('workspace.copy', dest ? { path, dest } : { path });
  },

  /** Salva le regole che l'utente ha dato a Jenny.
   *
   *  Non e' `workspace.write` su un path: la verita' va in un file che Dream
   *  non puo' riscrivere, e dentro `SOUL.md` ne resta una copia proiettata.
   *  Le due scritture sono una sola operazione, e stanno di la'
   *  (`jenny/agent/soul_rules.py`). */
  writeSoulRules(content) {
    return send('soul.rules.write', { content });
  },

  /** Crea un progetto: una wiki nuova e vuota, piu' la riga di scope che
   *  l'utente ha scritto. Passa da qui e non da `api` proprio per quella riga:
   *  e' testo libero, e la superficie /api/ non sa trasportarne. */
  createProject(name, seed, conversation) {
    return send('project.create', { name, seed, conversation });
  },

  /** Il primo messaggio di Jenny in un quaderno appena creato. Il testo arriva
   *  alla chat dal filo; la risposta dice solo se c'e' stato (`greeted`). */
  greetProject(name, lang) {
    return send('project.greet', { name, lang });
  },

  /** Salva una pagina di quaderno modificata a mano dal lettore.
   *
   *  Non e' `writeWorkspaceFile` su `wikis/<q>/wiki/<page>`: la cartella dei
   *  quaderni la decide la config (`wiki.wikis_dir`) e il client non la
   *  conosce — comporla di qua vorrebbe dire indovinarla.
   *
   *  `base` e' il markdown da cui si e' partiti. **Queste pagine le scrive
   *  anche Jenny**: se il file e' cambiato sotto, il server risponde con
   *  `conflict` e non scrive niente. */
  writePage(wiki, page, content, base) {
    return send('page.write', { wiki, page, content, base });
  },

  /** Cancella un progetto: l'albero della wiki **e** la sua conversazione.
   *
   *  Non e' `api.deleteWorkspace` su `wikis/<name>`, ed e' il punto di tutto:
   *  quella toglie una cartella e non sa cosa sia un progetto, quindi lasciava
   *  la chat sotto un nome ormai libero e il progetto successivo con lo stesso
   *  nome se la riprendeva (difetto del 24/08/2026). Il server rifiuta ormai
   *  quella strada; questa e' l'altra. */
  deleteProject(name) {
    return send('project.delete', { name });
  },

  /** Rinomina un quaderno: la cartella, la sua chat, le sue pagine in casa.
   *  Fra i comandi per la stessa ragione della cancellazione: cambia il disco
   *  (v. `webui/commands.py::project_rename`). */
  renameProject(name, newName) {
    return send('project.rename', { name, new_name: newName });
  },

  /** Apre una segnalazione su un punto di una pagina di quaderno. Fra i
   *  comandi per il commento, che e' testo libero (v.
   *  `webui/commands.py::audit_create`). `author` e' la costante che
   *  `/api/wiki/config` dichiarava per questo campo: l'audit lo scrive chi
   *  legge, non lei. */
  createAudit({ wiki, target, selStart, selEnd, comment }) {
    return send('audit.create', {
      wiki, target, sel_start: selStart, sel_end: selEnd, comment, author: 'me',
    });
  },

  /** Salva le pagine della casa: l'elenco intero e l'ordine di tutte. Lo chiama
   *  `api.savePages`, gemella della lettura `api.getPages`
   *  (v. `webui/commands.py::home_pages_set`). */
  saveHomePages(pages, order) {
    return send('home.pages.set', { pages, order });
  },

  /* ── I segreti ──────────────────────────────────────────────────────────
     Chiave del provider, token Telegram, password SSH: viaggiavano nella
     query di una GET, cioe' nella riga di richiesta che log e traceback
     vedono. Qui stanno nel frame. Li chiamano i
     metodi omonimi di `api`, con la stessa firma di prima. */

  /** L'elenco dei modelli di un provider, anche con una chiave non salvata. */
  providerModels({ provider, apiKey, apiBase, format }) {
    return send('settings.provider.models', {
      provider, api_key: apiKey || '', api_base: apiBase || '', format: format || '',
    });
  },

  /** Crea o aggiorna un provider; `params` come la vecchia query
   *  (`name`, `format`, `api_key`, `api_base`, `ca_bundle`, `ca_bundle_clear`). */
  updateProvider(params) {
    return send('settings.provider.update', params);
  },

  /** Salva il token del bot Telegram (il server lo valida con `getMe`). */
  saveTelegramToken(token) {
    return send('telegram.save', { token }, { timeoutMs: 30000 });
  },

  /** Crea o aggiorna un host SSH. `password` va omessa, non mandata vuota,
   *  quando l'utente non l'ha ridigitata: assente vuol dire «tieni quella
   *  salvata». */
  saveSshHost(params) {
    return send('ssh.host.save', params);
  },

  /** Il primo avvio: provider con la sua chiave, modello, nome di Jenny. Stava
   *  nella query di `/api/onboarding/save`; a salvataggio riuscito il server
   *  sveglia l'agente e risponde col saluto (`chat_id`, `welcome_message`). */
  saveOnboarding(params) {
    return send('onboarding.save', params);
  },

};
