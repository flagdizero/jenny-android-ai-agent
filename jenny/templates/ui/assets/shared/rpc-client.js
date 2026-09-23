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
 */

import { wsManager } from './ws-manager.js';

export const rpc = {
  /** Salva un file di testo del workspace (tetto 1 MB, lato server). */
  writeWorkspaceFile(path, content) {
    return wsManager.request('workspace.write', { path, content });
  },

  /** Salva le regole che l'utente ha dato a Jenny.
   *
   *  Non e' `workspace.write` su un path: la verita' va in un file che Dream
   *  non puo' riscrivere, e dentro `SOUL.md` ne resta una copia proiettata.
   *  Le due scritture sono una sola operazione, e stanno di la'
   *  (`jenny/agent/soul_rules.py`). */
  writeSoulRules(content) {
    return wsManager.request('soul.rules.write', { content });
  },

  /** Crea un progetto: una wiki nuova e vuota, piu' la riga di scope che
   *  l'utente ha scritto. Passa da qui e non da `api` proprio per quella riga:
   *  e' testo libero, e la superficie /api/ non sa trasportarne. */
  createProject(name, seed, conversation) {
    return wsManager.request('project.create', { name, seed, conversation });
  },

  /** Salva una pagina di quaderno modificata a mano dal lettore.
   *
   *  Non e' `writeWorkspaceFile` su `wikis/<q>/wiki/<pagina>`: la cartella dei
   *  quaderni la decide la config (`wiki.wikis_dir`) e il client non la
   *  conosce — comporla di qua vorrebbe dire indovinarla.
   *
   *  `base` e' il markdown da cui si e' partiti. **Queste pagine le scrive
   *  anche Jenny**: se il file e' cambiato sotto, il server risponde con
   *  `conflict` e non scrive niente. */
  writePage(wiki, page, content, base) {
    return wsManager.request('page.write', { wiki, page, content, base });
  },

  /** Cancella un progetto: l'albero della wiki **e** la sua conversazione.
   *
   *  Non e' `api.deleteWorkspace` su `wikis/<nome>`, ed e' il punto di tutto:
   *  quella toglie una cartella e non sa cosa sia un progetto, quindi lasciava
   *  la chat sotto un nome ormai libero e il progetto successivo con lo stesso
   *  nome se la riprendeva (difetto del 24/08/2026). Il server rifiuta ormai
   *  quella strada; questa e' l'altra. */
  deleteProject(name) {
    return wsManager.request('project.delete', { name });
  },

  /** Rinomina un quaderno: la cartella, la sua chat, le sue pagine in casa.
   *  Fra i comandi per la stessa ragione della cancellazione: cambia il disco
   *  (v. `webui/commands.py::project_rename`). */
  renameProject(name, newName) {
    return wsManager.request('project.rename', { name, new_name: newName });
  },

};
