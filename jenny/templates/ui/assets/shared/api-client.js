/** Shared API Client — HTTP wrapper for backend APIs. */

// The Android WebView proves it's the app's own trusted WebView (not some
// other app hitting the same loopback gateway port) by passing the gateway's
// per-install bootstrap secret in via a URL *fragment* on the initial
// loadUrl() — fragments are never sent over the wire, so this never reaches
// the HTTP request line or any log. Read it once at module load, then strip
// it from the visible URL immediately; it's kept in memory for the life of
// this page so bootstrap() can still re-authenticate later (e.g. after the
// issued token expires) without needing the fragment again. Never logged.
function _consumeBootstrapSecretFromLocation() {
  if (typeof location === 'undefined' || !location.hash) return null;
  const params = new URLSearchParams(location.hash.slice(1));
  const secret = params.get('bs');
  if (secret) {
    params.delete('bs');
    const rest = params.toString();
    const newHash = rest ? `#${rest}` : '';
    if (typeof history !== 'undefined' && history.replaceState) {
      history.replaceState(null, '', location.pathname + location.search + newHash);
    }
  }
  return secret || null;
}

class ApiClient {
  constructor() {
    this._secret = null;
    this._bootstrapping = null;
    this._bootstrapSecret = _consumeBootstrapSecretFromLocation();
    this._bootstrapInfo = null;
  }

  async bootstrap() {
    if (this._secret) return this._secret;
    if (this._bootstrapping) return this._bootstrapping;
    this._bootstrapping = (async () => {
      const headers = {};
      if (this._bootstrapSecret) headers['X-Jenny-Auth'] = this._bootstrapSecret;
      const res = await fetch('/webui/bootstrap', { headers });
      if (!res.ok) throw new Error(`Bootstrap failed: ${res.status}`);
      // Metadati runtime del gateway (model_name, provider, ...): li teniamo
      // per chi vuole lo stato iniziale senza un giro API extra.
      this._bootstrapInfo = await res.json();
      this._secret = this._bootstrapSecret;
      return this._secret;
    })();
    try {
      return await this._bootstrapping;
    } finally {
      this._bootstrapping = null;
    }
  }

  /** Le estensioni di questa build (`jenny/extensions.py`), dalla risposta di
   *  `/webui/bootstrap`: `[{name, entry, css, i18n}]`. Vuoto prima del bootstrap. */
  extensions() {
    const list = this._bootstrapInfo?.extensions;
    return Array.isArray(list) ? list : [];
  }

  async _fetch(url, opts = {}) {
    if (!this._secret) await this.bootstrap();
    const headers = { ...(opts.headers || {}), 'Authorization': `Bearer ${this._secret}` };
    let res = await fetch(url, { ...opts, headers });
    if (res.status === 401 || res.status === 403) {
      this._secret = null;
      await this.bootstrap();
      const headers2 = { ...(opts.headers || {}), 'Authorization': `Bearer ${this._secret}` };
      res = await fetch(url, { ...opts, headers: headers2 });
    }
    return res;
  }

  getSecret() { return this._secret; }

  // Ultimo body di /webui/bootstrap ({model_name, provider, ...}), o null se
  // il bootstrap non è ancora avvenuto.
  getBootstrapInfo() { return this._bootstrapInfo; }

  /** `/webui/bootstrap` riletto adesso, senza toccare lo stato della pagina:
   *  quel che il gateway di *ora* serve, da confrontare con quello con cui la
   *  pagina è partita (`shared/update-watch.js`). `null` se non risponde. */
  async freshBootstrapInfo() {
    const headers = {};
    if (this._bootstrapSecret) headers['X-Jenny-Auth'] = this._bootstrapSecret;
    try {
      const res = await fetch('/webui/bootstrap', { headers, cache: 'no-store' });
      return res.ok ? await res.json() : null;
    } catch {
      return null;
    }
  }

  /** Aperta da un altro dispositivo (il Mac via Tailscale)? Lo dice il server
   *  nel bootstrap: non `!window.JennyNative`, che e' vero anche in un banco di
   *  prova sul Mac e non dice da dove arrivi. Da fuori la pagina nasconde
   *  quel che ha senso solo col telefono in mano (v. `jenny/webui/phone_only.py`). */
  async isRemote() {
    try {
      await this.bootstrap();
    } catch {
      return false;
    }
    return !!this._bootstrapInfo?.remote;
  }

  // Reload the page while re-injecting the bootstrap secret into the URL
  // fragment. A plain window.location.reload() would drop the secret: the
  // fragment was consumed and stripped at initial load (see
  // _consumeBootstrapSecretFromLocation) and lives only in this instance's
  // memory, which a reload destroys — so the fresh page's bootstrap() would
  // 401. Re-appending #bs= lets the reloaded page re-authenticate. Falls back
  // to a plain reload if we never had a secret (native passed none).
  reload() {
    if (this._bootstrapSecret && typeof location !== 'undefined') {
      const params = new URLSearchParams(location.hash.slice(1));
      params.set('bs', this._bootstrapSecret);
      // replaceState e non `location.hash = ...`: assegnare il fragment è a
      // tutti gli effetti una navigazione e lascerebbe nello stack una entry
      // fantasma per ogni reload, che poi si mangia una pressione di Indietro.
      // Lo stato va azzerato: dopo il reload la SPA riscrive la propria radice.
      history.replaceState(null, '', `${location.pathname}${location.search}#${params}`);
    }
    location.reload();
  }

  // Va a un ALTRO documento della WebUI portandosi dietro il segreto, che
  // altrimenti resterebbe qui. È la strada fra i due gusci — la casa e
  // l'officina — e senza questa il documento di destinazione farebbe un
  // `bootstrap()` senza credenziale e prenderebbe 401.
  //
  // Perché non basta `reload()`: quello ricarica *questa* pagina. Perché non
  // basta `location.href = path`: il fragment col segreto è stato consumato e
  // cancellato al primo caricamento (v. _consumeBootstrapSecretFromLocation) e
  // da lì in poi vive solo nella memoria di questa istanza, che la navigazione
  // distrugge.
  //
  // `assign` di norma: i due gusci sono documenti separati e il tasto Indietro
  // deve poter tornare da dove si è arrivati. `replace` per i passaggi da e
  // verso `onboarding.html`: la casa lasciata per il primo avvio e il wizard
  // concluso non sono posti in cui Indietro debba riportare.
  navigate(path, { replace = false } = {}) {
    if (typeof location === 'undefined') return;
    const go = (url) => (replace ? location.replace(url) : location.assign(url));
    if (!this._bootstrapSecret) {
      go(path);
      return;
    }
    const [base, rawHash = ''] = String(path).split('#');
    const params = new URLSearchParams(rawHash);
    params.set('bs', this._bootstrapSecret);
    go(`${base}#${params}`);
  }

  // Riporta un errore client-side nel log del gateway (fire-and-forget).
  // Best-effort: non deve mai lanciare né generare a sua volta errori globali,
  // e un cap per pagina evita flood in caso di errori ripetuti in loop.
  clientLog(level, source, message) {
    try {
      this._clientLogCount = (this._clientLogCount || 0) + 1;
      if (this._clientLogCount > 20) return;
      const qs = new URLSearchParams({
        level: String(level || 'error'),
        source: String(source || 'unknown').slice(0, 100),
        message: String(message || '').slice(0, 800),
      });
      this._fetch(`/api/client-log?${qs}`).catch(() => {});
    } catch (_) { /* mai propagare */ }
  }

  /** I comandi slash **di questa conversazione**, per la tendina del composer.
   *
   *  Fonte unica: `command/specs.py::BUILTIN_COMMAND_SPECS`, la stessa da cui
   *  esce `/help`. Il client non tiene un secondo elenco — e da quando la chiave
   *  viaggia con la richiesta non tiene nemmeno una copia della regola su quali
   *  voci mostrare: filtra il server (`ws_http._handle_webui_commands`). Senza
   *  chiave l'elenco torna intero, che è il comportamento di prima. */
  async getCommands(sessionKey) {
    const url = sessionKey
      ? `/api/webui/commands?key=${encodeURIComponent(sessionKey)}`
      : '/api/webui/commands';
    const res = await this._fetch(url);
    if (!res.ok) throw new Error(`Commands failed: ${res.status}`);
    return res.json();
  }


  /** Stato della programmazione: cosa e' armato, e cosa fa davvero.
   *  Questa e' la lettura. Da qui un job dell'utente si mette in pausa, si
   *  riprende o si elimina (`cronJobAction`), sullo stesso servizio che usa il
   *  tool `cron`; crearne uno o cambiarlo resta del tool. */
  async getCron() {
    const res = await this._fetch('/api/webui/cron');
    if (!res.ok) throw new Error(`Cron failed: ${res.status}`);
    return res.json();
  }

  /* Pausa, ripresa o eliminazione di un job dell'utente (`pause` | `resume` |
     `remove`). L'errore porta `status`: 409 vuol dire un promemoria singolo la
     cui ora e' passata durante la pausa, che si puo' solo eliminare. */
  async cronJobAction(jobId, action) {
    const res = await this._fetch(`/api/webui/cron/${encodeURIComponent(jobId)}/${action}`);
    if (!res.ok) {
      const err = new Error((await res.text().catch(() => '')) || `Cron ${action} failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  async getAndroidApps() {
    const res = await this._fetch('/api/webui/android-apps');
    if (!res.ok) throw new Error(`Android apps failed: ${res.status}`);
    return res.json();
  }

  async launchAndroidApp(packageName) {
    const res = await this._fetch(`/api/webui/android-apps/${encodeURIComponent(packageName)}/launch`);
    if (!res.ok) throw new Error(`Launch failed: ${res.status}`);
    return res.json();
  }

  async uninstallAndroidApp(packageName) {
    const res = await this._fetch(`/api/webui/android-apps/${encodeURIComponent(packageName)}/uninstall`);
    if (!res.ok) throw new Error(`Uninstall failed: ${res.status}`);
    return res.json();
  }

  async openAndroidAppInfo(packageName) {
    const res = await this._fetch(`/api/webui/android-apps/${encodeURIComponent(packageName)}/app-info`);
    if (!res.ok) throw new Error(`App info failed: ${res.status}`);
    return res.json();
  }



  async getJennyApps() {
    const res = await this._fetch('/api/webui/apps');
    if (!res.ok) throw new Error(`Jenny apps failed: ${res.status}`);
    return res.json();
  }

  /** Le skill, tutte: anche le spente e le `internal`, che Mani conta. */
  async listSkills() {
    const res = await this._fetch('/api/webui/skills');
    if (!res.ok) throw new Error(`Skills failed: ${res.status}`);
    return res.json();
  }

  /** Accende o spegne una skill **tua**. Su una integrata il gateway risponde
   *  403: l'avvio la ri-estrarrebbe, e la scelta sparirebbe al riavvio. */
  async setSkillDisabled(name, disabled) {
    const res = await this._fetch(
      `/api/webui/skills/${encodeURIComponent(name)}/update?disabled=${disabled ? 1 : 0}`,
    );
    if (!res.ok) throw new Error(`Skill update failed: ${res.status}`);
    return res.json();
  }

  /** Il token con cui si incornicia la Jenny App *slug* (v. `frameForApp`).
   *
   *  Non il segreto del gateway: quello apre ogni route e la WebSocket, e
   *  un'app — o un'iniezione dentro un'app — avrebbe avuto l'intera API. Questo
   *  vale solo per i file e le azioni di quell'app (`jenny/apps/token.py`).
   *  Deterministico lato server, quindi si chiede una volta per slug. */
  async appToken(slug) {
    this._appTokens ||= new Map();
    const cached = this._appTokens.get(slug);
    if (cached) return cached;
    const res = await this._fetch(`/api/webui/apps/${encodeURIComponent(slug)}/token`);
    if (!res.ok) throw new Error(`App token failed: ${res.status}`);
    const { token } = await res.json();
    if (!token) throw new Error('App token failed: empty');
    this._appTokens.set(slug, token);
    return token;
  }

  async deleteJennyApp(slug) {
    const res = await this._fetch(`/api/webui/apps/${encodeURIComponent(slug)}/delete`);
    if (!res.ok) throw new Error(`Jenny app delete failed: ${res.status}`);
    return res.json();
  }


  /** Progetti dello scope chip: un progetto e' una wiki, quindi e' l'elenco
   *  delle wiki. Ritorna `{ dir, projects: [{ name, modified }] }`. */
  async listProjects() {
    const res = await this._fetch('/api/projects');
    if (!res.ok) throw new Error(`Projects failed: ${res.status}`);
    return res.json();
  }

  /** Cosa porterebbe via la cancellazione di un progetto:
   *  `{ name, exists, is_project, files, conversation: {files, bytes, messages}, orphan }`.
   *  `null` se quel nome non e' un progetto. Serve alla conferma, che di una
   *  cancellazione e' la meta' che la rende sicura. */
  async describeProject(name) {
    const res = await this._fetch(`/api/project/describe?name=${encodeURIComponent(name)}`);
    if (res.status === 404 || res.status === 400) return null;
    if (!res.ok) throw new Error(`Project describe failed: ${res.status}`);
    return res.json();
  }


  /** Nodi, archi e indice full-text di *wiki*, in una risposta sola.
   *
   *  Il nome e' **obbligatorio**: la forma senza — il grafo a stella di tutte
   *  le wiki — non esiste piu' ne' qui ne' sul server, che risponde 400. */
  async getGraph(wiki) {
    const res = await this._fetch(`/api/graph?wiki=${encodeURIComponent(wiki)}`);
    if (!res.ok) throw new Error(`Graph failed: ${res.status}`);
    return res.json();
  }

  /** Crea un riscontro ancorato a un punto di una pagina.
   *
   *  `selStart`/`selEnd` sono offset nel **markdown sorgente**. Il server si
   *  rilegge il file da solo e calcola le tre ancore: il vecchio client gli
   *  mandava anche `rawMarkdown` e la rotta lo **ignorava**.
   *
   *  Viaggia sul WebSocket (`rpc.createAudit`, comando `audit.create`): il
   *  commento e' testo libero, e fino al 26/09/2026 stava nella query di una
   *  GET, sotto il tetto di 8192 byte della riga di richiesta. Import
   *  **dinamico** per la stessa ragione di `savePages`. */
  async createAudit({ wiki, target, selStart, selEnd, comment }) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.createAudit({ wiki, target, selStart, selEnd, comment });
  }

  /** La bacheca di un quaderno, gia' risolta dal server: schede coi loro
   *  segnali, post-it delle attese, fili, busta, «dall'ultima volta». */
  async getBoard(wiki) {
    const res = await this._fetch(`/api/board?wiki=${encodeURIComponent(wiki)}`);
    if (!res.ok) {
      const err = new Error(`Board failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  /** Il cassetto di una scheda: riassunto per sezioni, sezioni dell'utente,
   *  attese, fili, correzioni in corso. */
  async getBoardCard(wiki, card) {
    const params = new URLSearchParams({ wiki, card });
    const res = await this._fetch(`/api/board/card?${params}`);
    if (!res.ok) {
      const err = new Error(`Board card failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  /** Una foto di una sezione, come `blob:`: un `<img src>` non porterebbe il
   *  token, quindi la si chiede da qui e se ne fa un indirizzo locale. */
  async getBoardPhotoUrl(wiki, path) {
    const params = new URLSearchParams({ wiki, path });
    const res = await this._fetch(`/api/board/photo?${params}`);
    if (!res.ok) throw new Error(`Board photo failed: ${res.status}`);
    return URL.createObjectURL(await res.blob());
  }

  /** Un'immagine di una Jenny App (il blocco Foto della sua striscia), come
   *  `blob:`: la rotta vuole il segreto, che un `<img src>` non porterebbe. */
  async getAppMediaUrl(slug, name) {
    const res = await this._fetch(`/api/webui/apps/${encodeURIComponent(slug)}/media/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`App media failed: ${res.status}`);
    return URL.createObjectURL(await res.blob());
  }

  /** Esegue un'azione di una Jenny App da fuori della sua cornice: i verbi di
   *  una sezione mini app nel cassetto. Il segreto della SPA vale anche sulle
   *  rotte delle app (`check_app_secret`). */
  async runAppAction(slug, name, params = {}) {
    const url = `/api/apps/${encodeURIComponent(slug)}/actions/${encodeURIComponent(name)}`
      + `?params=${encodeURIComponent(JSON.stringify(params))}`;
    const res = await this._fetch(url);
    let data = null;
    try { data = await res.json(); } catch (_) { /* corpo non JSON */ }
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || `${name}: HTTP ${res.status}`);
    }
    return data;
  }

  async getPage({ wiki, page } = {}) {
    const params = new URLSearchParams();
    if (wiki) params.set('wiki', wiki);
    if (page) params.set('page', page);
    const res = await this._fetch(`/api/page?${params}`);
    if (!res.ok) throw new Error(`Page failed: ${res.status}`);
    return res.json();
  }



  // Workspace APIs
  async listWorkspace(path) {
    const res = await this._fetch(`/api/workspace/list?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error(`Workspace list failed: ${res.status}`);
    return res.json();
  }

  async readWorkspaceFile(path) {
    const res = await this._fetch(`/api/workspace/read?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      // status esposto per distinguere il 415 "binary file" (il viewer
      // delega all'app di sistema) dagli altri errori.
      const err = new Error(`Workspace read failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  getWorkspaceDownloadUrl(path) {
    return `/api/workspace/download?path=${encodeURIComponent(path)}`;
  }

  // Scarica il file come Blob con header di autenticazione (un <img src>
  // diretto verso /download non può portare il Bearer token).
  async downloadWorkspaceBlob(path) {
    const res = await this._fetch(this.getWorkspaceDownloadUrl(path));
    if (!res.ok) {
      const err = new Error(`Workspace download failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.blob();
  }

  async createWorkspaceFolder(path) {
    const res = await this._fetch(`/api/workspace/mkdir?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Workspace mkdir failed: ${res.status}`);
    }
    return res.json();
  }

  /* Rinomina, cancellazione e copia viaggiano sul WebSocket (`rpc`, comandi
     `workspace.rename`/`delete`/`copy`): cambiano il disco, e fino al
     26/09/2026 erano GET su /api/, che e' per letture e parametri corti,
     e un `<img src>` con il token nell'indirizzo poteva farli partire. Stanno qui con
     la stessa firma perche' i chiamanti non cambino; l'errore porta il `code`
     del comando e il messaggio del server. Import **dinamico** per la stessa
     ragione di `savePages`. */
  async renameWorkspace(oldPath, newPath) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.renameWorkspace(oldPath, newPath);
  }

  async deleteWorkspace(path) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.deleteWorkspace(path);
  }

  async copyWorkspace(path, dest) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.copyWorkspace(path, dest);
  }

  // ── Session APIs ──

  async fetchWebuiThread(key, { limit = 160, before = null } = {}) {
    const params = new URLSearchParams();
    params.set('limit', limit);
    if (before) params.set('before', before);
    const res = await this._fetch(`/api/sessions/${encodeURIComponent(key)}/webui-thread?${params}`);
    if (!res.ok) throw new Error(`Thread fetch failed: ${res.status}`);
    return res.json();
  }

  // ── Settings APIs ──

  async getSettings() {
    const res = await this._fetch('/api/settings');
    if (!res.ok) throw new Error(`Settings failed: ${res.status}`);
    return res.json();
  }

  /* I quattro metodi che portano un segreto — `getProviderModels`,
     `updateProvider`, `saveTelegramToken`, `saveSshHost` — viaggiano sul
     WebSocket (`rpc`): la chiave API, il token del bot e la password SSH
     stavano nella query di una GET, cioe' nella riga di richiesta che log e
     traceback vedono. Firma e forma della risposta sono
     quelle di prima, cosi' i chiamanti non cambiano; l'errore porta il
     messaggio del server e il `code` del comando. Import **dinamico** per la
     stessa ragione di `savePages`. */
  async getProviderModels(provider, apiKey, apiBase, format) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.providerModels({ provider, apiKey, apiBase, format });
  }

  _postWithQuery(url, params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== '') qs.set(k, String(v));
    }
    return this._fetch(`${url}?${qs}`);
  }

  async updateSettings(params) {
    const res = await this._postWithQuery('/api/settings/update', params);
    if (!res.ok) throw new Error(`Settings update failed: ${res.status}`);
    return res.json();
  }

  /* Il messaggio del server arriva nell'errore: il backend rifiuta il
     salvataggio spiegando *quale* file CA non ha potuto leggere e dove, e uno
     stato secco trasformerebbe quella spiegazione in un mistero. I valori vuoti
     si scartano come faceva `_postWithQuery`: la stringa vuota vuol dire «non
     toccare», e `ca_bundle_clear` e' il segnale a parte per svuotare. */
  async updateProvider(params) {
    const { rpc } = await import('./rpc-client.js');
    const clean = {};
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== null && v !== undefined && v !== '') clean[k] = String(v);
    }
    return rpc.updateProvider(clean);
  }

  async deleteProvider(params) {
    const res = await this._postWithQuery('/api/settings/provider/delete', params);
    if (!res.ok) throw new Error(`Provider delete failed: ${res.status}`);
    return res.json();
  }

  async updateWebSearch(params) {
    const res = await this._postWithQuery('/api/settings/web-search/update', params);
    if (!res.ok) throw new Error(`Web search update failed: ${res.status}`);
    return res.json();
  }

  /* Le manopole di Dream e i tetti della memoria lunga. Il messaggio d'errore
     del server è utile qui — nomina il range sforato — quindi viaggia
     nell'errore invece di essere sostituito da uno generico. */
  async updateMemorySettings(params) {
    const res = await this._postWithQuery('/api/settings/memory/update', params);
    if (!res.ok) throw new Error(await this._errorText(res, 'Memory settings update failed'));
    return res.json();
  }

  /* Il giardiniere e la compattazione delle chat di progetto. */
  async updateWorkerSettings(params) {
    const res = await this._postWithQuery('/api/settings/workers/update', params);
    if (!res.ok) throw new Error(await this._errorText(res, 'Worker settings update failed'));
    return res.json();
  }

  /* Il testo di un rifiuto, col ripiego sullo status. Il corpo di un 400 del
     gateway è **testo semplice** (`http_utils.http_error`) e contiene il perché
     — «gardener_idle_min must be between 0–1440» — che buttato via lascerebbe
     l'utente con "update failed". Tetto sulla lunghezza: quel corpo finisce in
     un toast, e su un 502 di mezzo potrebbe essere una pagina HTML. */
  async _errorText(res, fallback) {
    try {
      const body = (await res.text()).trim();
      if (body && body.length <= 200 && !body.startsWith('<')) return body;
    } catch { /* corpo illeggibile: resta il ripiego */ }
    return `${fallback}: ${res.status}`;
  }

  async updateLocation(params) {
    const res = await this._postWithQuery('/api/settings/location/update', params);
    if (!res.ok) throw new Error(`Location update failed: ${res.status}`);
    return res.json();
  }

  async updateFloating(params) {
    const res = await this._postWithQuery('/api/settings/floating/update', params);
    if (!res.ok) throw new Error(`Floating update failed: ${res.status}`);
    return res.json();
  }

  /** Apre Impostazioni → Accessibilità, dove si accende la lettura dello
   *  schermo: un'app non puo' accenderla da se'. */
  async openScreenSettings() {
    const res = await this._postWithQuery('/api/settings/floating/screen-settings', {});
    if (!res.ok) throw new Error(`Screen settings failed: ${res.status}`);
    return res.json();
  }

  async openFloatingPermission() {
    const res = await this._postWithQuery('/api/settings/floating/permission-settings', {});
    if (!res.ok) throw new Error(`Overlay permission settings failed: ${res.status}`);
    return res.json();
  }

  async updatePower(params) {
    const res = await this._postWithQuery('/api/settings/power/update', params);
    if (!res.ok) throw new Error(`Power update failed: ${res.status}`);
    return res.json();
  }

  async getPowerDiagnostics() {
    const res = await this._fetch('/api/settings/power/diagnostics');
    if (!res.ok) throw new Error(`Power diagnostics failed: ${res.status}`);
    return res.json();
  }

  // ── SSH APIs ──
  // Helper dedicato invece di _postWithQuery: quello scarta i valori vuoti, e
  // qui un campo svuotato (es. la descrizione di un host) deve poter arrivare
  // al server come stringa vuota, altrimenti cancellarlo diventa impossibile.
  // Lo `status` viene rimesso sull'errore perché la UI distingue il 409
  // "host key cambiata" dagli altri per decidere se offrire la sostituzione.
  async _sshCall(path, params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) qs.set(k, v == null ? '' : String(v));
    const res = await this._fetch(qs.toString() ? `${path}?${qs}` : path);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      const err = new Error(text || `SSH request failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  async getSsh() {
    return this._sshCall('/api/settings/ssh');
  }

  async updateSsh(params) {
    return this._sshCall('/api/settings/ssh/update', params);
  }

  // `params` accetta anche `auth` ('key' | 'password') e, solo con
  // `auth: 'password'`, la password in chiaro. Viaggia sul WebSocket (comando
  // `ssh.host.save`), non piu' nella query: v. il commento su
  // `getProviderModels`. Va omessa — non passata vuota — quando l'utente non
  // l'ha ridigitata, perché assente significa "tieni quella salvata". Il valore
  // non va mai loggato né tenuto in giro: la risposta non lo rimanda indietro
  // (porta `has_password`, un booleano) proprio perché non esista una copia da
  // cui possa ricomparire. I valori `null` diventano stringa vuota come in
  // `_sshCall`: un campo svuotato deve arrivare vuoto.
  async saveSshHost(params) {
    const { rpc } = await import('./rpc-client.js');
    const clean = {};
    for (const [k, v] of Object.entries(params || {})) clean[k] = v == null ? '' : String(v);
    return rpc.saveSshHost(clean);
  }

  async deleteSshHost(alias) {
    return this._sshCall('/api/settings/ssh/host/delete', { alias });
  }

  async generateSshKey(alias, { replace = false } = {}) {
    return this._sshCall(
      '/api/settings/ssh/key/generate',
      replace ? { alias, replace: '1' } : { alias },
    );
  }

  async probeSshHostKey(alias) {
    return this._sshCall('/api/settings/ssh/host-key/probe', { alias });
  }

  async acceptSshHostKey(alias, fingerprint, { replace = false } = {}) {
    const params = { alias, fingerprint };
    if (replace) params.replace = '1';
    return this._sshCall('/api/settings/ssh/host-key/accept', params);
  }

  /* La prima chiave API dell'utente: viaggia sul WebSocket come gli altri
     segreti (v. `getProviderModels`), non nella query di una GET. Stessa firma
     e stessa risposta di prima; l'errore porta il messaggio del server. */
  async saveOnboarding(params) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.saveOnboarding({
      provider_name: params.provider_name || params.provider || '',
      format: params.format || '',
      api_key: params.api_key || '',
      api_base: params.api_base || '',
      model: params.model || '',
      bot_name: params.bot_name || '',
      bot_icon: params.bot_icon || '',
      locale: params.locale || '',
    });
  }

  // ── Telegram APIs ──

  async _telegramGet(url) {
    const res = await this._fetch(url);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(text || `Telegram request failed: ${res.status}`);
    }
    return res.json();
  }

  async getTelegramStatus() {
    return this._telegramGet('/api/telegram/status');
  }

  /* Il token sul WebSocket (comando `telegram.save`), non nella query: v. il
     commento su `getProviderModels`. */
  async saveTelegramToken(token) {
    const { rpc } = await import('./rpc-client.js');
    return rpc.saveTelegramToken(token);
  }

  async unpairTelegram() {
    return this._telegramGet('/api/telegram/unpair');
  }

  /* Un solo metodo per i due versi del toggle. Il valore va scritto esplicito
     (`true`/`false`) e non omesso per dire "falso": `_postWithQuery` scarta le
     stringhe vuote, e qui l'assenza del parametro significherebbe "spegni" per
     via del `parse_flag` lato server — comodo per sbaglio, illeggibile a
     rileggerlo. */
  async setTelegramEnabled(enabled) {
    return this._telegramGet(`/api/telegram/update?enabled=${enabled ? 'true' : 'false'}`);
  }

  // ── Backup APIs ──
  // Il payload viaggia in un header custom come JSON base64 (il gateway non
  // legge i body HTTP; il base64 evita i limiti latin-1 degli header con
  // passphrase non-ASCII). Mai passphrase nella query string.

  _backupHeaders(payload) {
    const json = JSON.stringify(payload || {});
    return { 'X-Jenny-Backup-Data': btoa(unescape(encodeURIComponent(json))) };
  }

  async _backupPost(url, payload) {
    const res = await this._fetch(url, { headers: this._backupHeaders(payload) });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(text || `Backup request failed: ${res.status}`);
    }
    return res.json();
  }

  async exportBackup(passphrase) {
    return this._backupPost('/api/backup/export', { passphrase });
  }

  /* ── Le pagine della casa ────────────────────────────────────────────── */

  /** `{pages, order, fixed, max, kinds}`. Il tetto arriva dal server e non se lo tiene
   *  scritto il client: due copie di quel numero divergerebbero, e la seconda
   *  si scoprirebbe solo quando un salvataggio viene rifiutato. */
  async getPages() {
    const res = await this._fetch('/api/home/pages');
    if (!res.ok) throw new Error(`Pages read failed: ${res.status}`);
    return res.json();
  }

  /** Le pagine aggiunte **e** l'ordine di tutte, fisse comprese: `{ok,
   *  pages, order}` torna com'e' stato salvato. L'ordine deve nominare
   *  ogni pagina una volta sola, o il server lo rifiuta (`bad_request`) e qui
   *  si lancia l'errore, con il suo `code`.
   *
   *  Resta qui perche' e' la gemella di `getPages`, ma la scrittura viaggia
   *  sul WebSocket (`rpc.saveHomePages`): `/api/` e' per letture e parametri
   *  corti, e fino al 25/09/2026 questa era una GET col
   *  JSON nell'indirizzo. Import **dinamico**: `ws-manager.js` importa questo
   *  modulo, e uno statico chiuderebbe il cerchio al caricamento. */
  async savePages(pages, order) {
    const { rpc } = await import('./rpc-client.js');
    const body = await rpc.saveHomePages(pages, order);
    return { ok: body.ok, pages: body.pages, order: body.order };
  }

  /* «Il file e' stato salvato davvero». Il gateway non puo' saperlo: lui
     prepara il container cifrato in staging, e se quel file finisca su disco
     lo decide il picker SAF, che risponde solo di qua. */
  async noteBackupExported() {
    const res = await this._fetch('/api/backup/exported');
    if (!res.ok) throw new Error(`Backup record failed: ${res.status}`);
    return res.json();
  }

  async importBackup({ stagedPath, passphrase } = {}) {
    const payload = { passphrase };
    if (stagedPath) payload.staged_path = stagedPath;
    return this._backupPost('/api/backup/import', payload);
  }

  async getSnapshotHistory(limit = 100) {
    const res = await this._fetch(`/api/backup/snapshots?limit=${encodeURIComponent(limit)}`);
    if (!res.ok) throw new Error(`Snapshot list failed: ${res.status}`);
    return res.json();
  }

  async createSnapshot(label) {
    return this._backupPost('/api/backup/snapshots/create', label ? { label } : {});
  }

  async restoreSnapshot(snapshotId) {
    return this._backupPost('/api/backup/snapshots/restore', { snapshot_id: snapshotId });
  }

  async updateSnapshotRetention(maxAgeDays) {
    return this._backupPost('/api/backup/snapshots/retention', { max_age_days: maxAgeDays });
  }

  async fetchFilePreview(sessionKey, filePath) {
    const res = await this._fetch(`/api/sessions/${encodeURIComponent(sessionKey)}/file-preview?path=${encodeURIComponent(filePath)}`);
    if (!res.ok) throw new Error(`File preview failed: ${res.status}`);
    return res.json();
  }

  // ── Subagent APIs ──
  // Stessa forma servita dal frame WS `subagent_status`: {running, recent}.
  // Serve al pannello per ripartire dopo un reload di pagina (su Android il
  // processo della WebView muore spesso), non solo alla prossima transizione.

  // `sessionKey` (`websocket:default`, `project:<nome>`) è la conversazione da
  // cui arriva la domanda, ed è un argomento di tutte e cinque le chiamate.
  // Sullo snapshot la limita a quella conversazione, come il frame che il
  // gateway le manda. Sulle azioni e sulle letture fa rifiutare (404) un
  // subagent che non è suo. Senza chiave il server non filtra e non controlla
  // niente: entrambi i gusci la passano sempre.
  async getSubagents({ sessionKey } = {}) {
    const res = await this._fetch(subagentUrl('', { sessionKey }));
    if (!res.ok) throw new Error(`Subagents failed: ${res.status}`);
    return res.json();
  }

  // I lavori lunghi della conversazione (giardiniere, Dream): lo stesso payload
  // del frame `work_status`, per la bolla di lavoro della casa quando si riapre
  // a lavoro iniziato. `sessionKey` come sopra.
  async getWork({ sessionKey } = {}) {
    const qs = sessionKey ? `?session_key=${encodeURIComponent(sessionKey)}` : '';
    const res = await this._fetch(`/api/work${qs}`);
    if (!res.ok) throw new Error(`Work failed: ${res.status}`);
    return res.json();
  }

  // Il corpo di errore di queste route è testo semplice, non JSON: il messaggio
  // del manager è già scritto per essere mostrato all'utente, quindi lo si
  // propaga così com'è (409 = rilancio impossibile, 429 = niente slot liberi).
  async restartSubagent(taskId, { sessionKey } = {}) {
    const res = await this._fetch(subagentUrl(`/${encodeURIComponent(taskId)}/restart`, { sessionKey }));
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Restart failed: ${res.status}`);
    return res.json();
  }

  // Fa continuare una mini-me fermata dal budget: riprende con la sua storia
  // (`subagent_send` lato manager), invece di ripartire da zero come il
  // rilancio. Stessa forma ed errori di `restartSubagent`.
  async continueSubagent(taskId, { sessionKey } = {}) {
    const res = await this._fetch(subagentUrl(`/${encodeURIComponent(taskId)}/continue`, { sessionKey }));
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Continue failed: ${res.status}`);
    return res.json();
  }

  async cancelSubagent(taskId, { sessionKey } = {}) {
    const res = await this._fetch(subagentUrl(`/${encodeURIComponent(taskId)}/cancel`, { sessionKey }));
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Cancel failed: ${res.status}`);
    return res.json();
  }

  // Finestra di attività da un cursore. NON è un poll: la modale riceve i frame
  // `subagent_activity` dalla WebSocket, e questa lettura serve solo quando il
  // server ha dichiarato un buco (`gap`) — stessa forma del frame, quindi il
  // client ha un solo parser. Il tetto lato server è più alto di quello del
  // frame (200 contro 40), che è ciò che rende la risync capace di tappare
  // davvero il buco invece di aprirne un altro.
  async getSubagentActivity(taskId, since = 0, { sessionKey } = {}) {
    const res = await this._fetch(subagentUrl(`/${encodeURIComponent(taskId)}/activity`, {
      since: String(Number(since) || 0),
      sessionKey,
    }));
    if (!res.ok) throw new Error(`Subagent activity failed: ${res.status}`);
    return res.json();
  }

  // Condensa "cosa ha fatto davvero" di un subagent. Chiamata SOLO all'espansione
  // del blocco in chat: la maggior parte dei messaggi non viene mai espansa, e
  // farla in anticipo sarebbe una lettura da disco per riga di trace.
  async getSubagentDigest(taskId, { sessionKey } = {}) {
    const res = await this._fetch(subagentUrl(`/${encodeURIComponent(taskId)}/digest`, { sessionKey }));
    if (!res.ok) throw new Error(`Subagent digest failed: ${res.status}`);
    return res.json();
  }
}

/* L'indirizzo di una route `/api/subagents*`. `tail` è il pezzo dopo
   `/api/subagents` (vuoto per lo snapshot), già codificato; `sessionKey`
   diventa `session_key`, e un valore assente o vuoto non entra nella query —
   così la chiamata senza chiave resta byte per byte quella di prima. */
function subagentUrl(tail, { sessionKey, ...params } = {}) {
  const qs = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') qs.set(name, value);
  }
  if (sessionKey) qs.set('session_key', sessionKey);
  const query = qs.toString();
  return `/api/subagents${tail}${query ? `?${query}` : ''}`;
}

export const api = new ApiClient();
