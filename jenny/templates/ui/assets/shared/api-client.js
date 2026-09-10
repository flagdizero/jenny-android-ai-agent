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

  async getSkills() {
    const res = await this._fetch('/api/webui/skills');
    if (!res.ok) throw new Error(`Skills failed: ${res.status}`);
    return res.json();
  }

  /** Stato della programmazione: cosa e' armato, e cosa fa davvero.
   *  Sola lettura — le scritture passano dal tool `cron`, che e' l'unico imbuto
   *  sullo store dei job. */
  async getCron() {
    const res = await this._fetch('/api/webui/cron');
    if (!res.ok) throw new Error(`Cron failed: ${res.status}`);
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

  async getHiddenApps() {
    const res = await this._fetch('/api/webui/hidden-apps');
    if (!res.ok) throw new Error(`Hidden apps failed: ${res.status}`);
    return res.json();
  }

  async setHiddenApps(packages) {
    const state = encodeURIComponent(JSON.stringify({ packages }));
    const res = await this._fetch(`/api/webui/hidden-apps/update?state=${state}`);
    if (!res.ok) throw new Error(`Hidden apps update failed: ${res.status}`);
    return res.json();
  }

  async getJennyApps() {
    const res = await this._fetch('/api/webui/apps');
    if (!res.ok) throw new Error(`Jenny apps failed: ${res.status}`);
    return res.json();
  }

  async deleteJennyApp(slug) {
    const res = await this._fetch(`/api/webui/apps/${encodeURIComponent(slug)}/delete`);
    if (!res.ok) throw new Error(`Jenny app delete failed: ${res.status}`);
    return res.json();
  }

  async getConfig() {
    const res = await this._fetch('/api/config');
    if (!res.ok) return {};
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

  async getTree(wiki) {
    const url = wiki ? `/api/tree?wiki=${encodeURIComponent(wiki)}` : '/api/tree';
    const res = await this._fetch(url);
    if (!res.ok) throw new Error(`Tree failed: ${res.status}`);
    return res.json();
  }

  async getGraph(wiki) {
    const url = wiki ? `/api/graph?wiki=${encodeURIComponent(wiki)}` : '/api/graph';
    const res = await this._fetch(url);
    if (!res.ok) throw new Error(`Graph failed: ${res.status}`);
    return res.json();
  }

  async getPage({ wiki, page } = {}) {
    const params = new URLSearchParams();
    if (wiki) params.set('wiki', wiki);
    if (page) params.set('page', page);
    const res = await this._fetch(`/api/page?${params}`);
    if (!res.ok) throw new Error(`Page failed: ${res.status}`);
    return res.json();
  }

  async getAudits({ wiki, targetPath, mode = 'open' } = {}) {
    const params = new URLSearchParams({ target: targetPath || '', mode });
    if (wiki) params.set('wiki', wiki);
    const res = await this._fetch(`/api/audit?${params}`);
    if (!res.ok) throw new Error(`Audits failed: ${res.status}`);
    return res.json();
  }

  async createAudit({ wiki, target, rawMarkdown, selStart, selEnd, comment, severity, author }) {
    const params = new URLSearchParams({ wiki, target, selStart, selEnd, comment, severity, author });
    const res = await this._fetch(`/api/audit/create?${params}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Audit create failed: ${res.status}`);
    }
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

  async renameWorkspace(oldPath, newPath) {
    const params = new URLSearchParams();
    params.set('oldPath', oldPath);
    params.set('newPath', newPath);
    const res = await this._fetch(`/api/workspace/rename?${params}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Workspace rename failed: ${res.status}`);
    }
    return res.json();
  }

  async deleteWorkspace(path) {
    const res = await this._fetch(`/api/workspace/delete?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Workspace delete failed: ${res.status}`);
    }
    return res.json();
  }

  async copyWorkspace(path, dest) {
    const params = new URLSearchParams();
    params.set('path', path);
    if (dest) params.set('dest', dest);
    const res = await this._fetch(`/api/workspace/copy?${params}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Workspace copy failed: ${res.status}`);
    }
    return res.json();
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

  async getProviderModels(provider, apiKey, apiBase, format) {
    let url = `/api/settings/provider-models?provider=${encodeURIComponent(provider)}`;
    if (apiKey) url += `&api_key=${encodeURIComponent(apiKey)}`;
    if (apiBase) url += `&api_base=${encodeURIComponent(apiBase)}`;
    if (format) url += `&format=${encodeURIComponent(format)}`;
    const res = await this._fetch(url);
    if (!res.ok) throw new Error(`Provider models failed: ${res.status}`);
    return res.json();
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

  async updateProvider(params) {
    const res = await this._postWithQuery('/api/settings/provider/update', params);
    if (!res.ok) {
      // Il corpo dell'errore va propagato: il backend rifiuta il salvataggio
      // spiegando *quale* file CA non ha potuto leggere e dove, e uno stato
      // secco ("400") trasformerebbe quella spiegazione in un mistero. Le rotte
      // dei settings rispondono in `text/plain` (`http_error`), non in JSON come
      // quelle delle app: qui si legge il testo, non `err.error`.
      const detail = await res.text().catch(() => '');
      throw new Error(detail.trim() || `Provider update failed: ${res.status}`);
    }
    return res.json();
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
  // `auth: 'password'`, la password in chiaro. Due cose da sapere prima di
  // toccarla: il parametro si deve chiamare esattamente `password`, perché è
  // quel nome che `http_utils.redact_query_secrets` riconosce e maschera nel
  // log del path lato gateway; e va omesso — non passato vuoto — quando
  // l'utente non l'ha ridigitata, perché assente significa "tieni quella
  // salvata". Il valore non va mai loggato né tenuto in giro: la risposta non
  // lo rimanda indietro (porta `has_password`, un booleano) proprio perché non
  // esista una copia da cui possa ricomparire.
  async saveSshHost(params) {
    return this._sshCall('/api/settings/ssh/host/save', params);
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

  async saveOnboarding(params) {
    const qs = new URLSearchParams({
      provider_name: params.provider_name || params.provider || '',
      format: params.format || '',
      api_key: params.api_key || '',
      api_base: params.api_base || '',
      model: params.model || '',
      bot_name: params.bot_name || '',
      bot_icon: params.bot_icon || '',
      locale: params.locale || '',
    });
    const res = await this._fetch(`/api/onboarding/save?${qs}`);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(text || `Onboarding save failed: ${res.status}`);
    }
    return res.json();
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

  async saveTelegramToken(token) {
    return this._telegramGet(`/api/telegram/save?token=${encodeURIComponent(token)}`);
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

  async getSubagents() {
    const res = await this._fetch('/api/subagents');
    if (!res.ok) throw new Error(`Subagents failed: ${res.status}`);
    return res.json();
  }

  // Il corpo di errore di queste route è testo semplice, non JSON: il messaggio
  // del manager è già scritto per essere mostrato all'utente, quindi lo si
  // propaga così com'è (409 = rilancio impossibile, 429 = niente slot liberi).
  async restartSubagent(taskId) {
    const res = await this._fetch(`/api/subagents/${encodeURIComponent(taskId)}/restart`);
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Restart failed: ${res.status}`);
    return res.json();
  }

  async cancelSubagent(taskId) {
    const res = await this._fetch(`/api/subagents/${encodeURIComponent(taskId)}/cancel`);
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Cancel failed: ${res.status}`);
    return res.json();
  }

  // Finestra di attività da un cursore. NON è un poll: la modale riceve i frame
  // `subagent_activity` dalla WebSocket, e questa lettura serve solo quando il
  // server ha dichiarato un buco (`gap`) — stessa forma del frame, quindi il
  // client ha un solo parser. Il tetto lato server è più alto di quello del
  // frame (200 contro 40), che è ciò che rende la risync capace di tappare
  // davvero il buco invece di aprirne un altro.
  async getSubagentActivity(taskId, since = 0) {
    const qs = new URLSearchParams({ since: String(Number(since) || 0) });
    const res = await this._fetch(
      `/api/subagents/${encodeURIComponent(taskId)}/activity?${qs}`
    );
    if (!res.ok) throw new Error(`Subagent activity failed: ${res.status}`);
    return res.json();
  }

  // Condensa "cosa ha fatto davvero" di un subagent. Chiamata SOLO all'espansione
  // del blocco in chat: la maggior parte dei messaggi non viene mai espansa, e
  // farla in anticipo sarebbe una lettura da disco per riga di trace.
  async getSubagentDigest(taskId) {
    const res = await this._fetch(`/api/subagents/${encodeURIComponent(taskId)}/digest`);
    if (!res.ok) throw new Error(`Subagent digest failed: ${res.status}`);
    return res.json();
  }
}

export const api = new ApiClient();
