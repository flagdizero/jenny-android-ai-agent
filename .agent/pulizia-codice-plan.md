# Pulizia del codice — quello che resta dopo l'audit del 24/09/2026

> Stato: **in corso** — fase 0 avviata il 24/09; le decisioni della fase 1
> sono prese (tutte come da raccomandazione). Le cancellazioni a rischio zero sono già atterrate
> (`94cdd49`…`0060788`, otto commit, ~1.300 righe in meno, installate sul
> Titan 2). Qui c'è tutto il resto: tre difetti, le decisioni che spettano a te,
> i duplicati di Python, WebUI, Kotlin e test.
> Si spunta qui, passo per passo, man mano che il lavoro atterra — e l'esito
> (cosa è cambiato davvero, cosa si è scoperto) si riporta nel passo, non solo
> la spunta.

## Da dove viene

Un audit completo (vulture + un rilevatore di cloni a finestre di righe +
verifica a mano di ogni candidato, su Python, JS/CSS/i18n, Kotlin e test) ha
trovato: codice morto per ~1.700 righe, duplicati per ~600 righe di produzione
e ~1.000 di test, e **tre difetti veri** travestiti da codice morto. Il morto
sicuro è stato tolto il 24/09; tutti i riferimenti qui sotto sono stati
riverificati su `0060788`, dopo quei commit.

Già deciso il 24/09 e **fuori dal piano** (v. l'ultima sezione): gli helper
usati solo dai test su cui molti test si appoggiano, `vendorize_ui.py`,
`_formatGapWhen`, i fake Dream/Telegram, i frame `ready`/`attached`.

## Regole di lavoro (valgono per ogni passo)

- **Un tema per commit**, firmato (`git commit -s`), messaggio che dice cosa
  cambia di comportamento quando cambia. I passi marcati **[SICUREZZA]** stanno
  da soli nel loro commit.
- Prima del commit: `ruff check jenny/ tests/`, `npx pyright jenny/bus
  jenny/command jenny/runtime jenny/session`, i test toccati su **3.14 e sul
  venv 3.11** (`/tmp/py311/bin/python -m pytest`; se `pyvenv.cfg` manca va
  ricreato). A fine fase la suite intera su entrambi.
- **Mai `pytest … | tail && git commit`**: la catena guarda l'esito di `tail`.
  Salvare l'output e controllare `$?` (è successo il 24/09).
- Ogni fase che tocca WebUI o Kotlin finisce con `assembleRelease`,
  `adb install -r`, verifica di `lastUpdateTime`, screenshot.
- `grep -a` su `jenny/templates/ui/assets/*.js` (mobile-chat.js greppa come
  binario).
- Un difetto si corregge **con il test che l'avrebbe preso**, scritto prima e
  visto fallire.
- Refactor dei test: la collezione non deve cambiare (v. Fase 5, protocollo).

---

## Fase 0 — I tre difetti (prima di tutto)

Tutti e quattro si fanno nella sessione dell'audit (i due task separati che
erano stati proposti per 0.3 e 0.4 sono stati ritirati il 24/09).

- [x] **0.1 `/dream` senza storia nuova rompe la chiusura del ciclo** — fatto
  24/09. Il test nuovo (`TestNothingNewToDream`) ha fallito esattamente come
  previsto: due messaggi, il secondo «Dream failed after 0.0s: cannot access
  local variable 'dream_file_states'». Il cron verificato sano: il suo turno è
  una funzione a sé che rende una tupla, senza `finally` che legga il nome.
  - `command/builtin.py:330` fa `return` dentro il `try` quando
    `build_dream_prompt` torna `None`; il `finally` (`:417-427`) legge
    `dream_file_states`, assegnata solo a `:352` → `UnboundLocalError`.
    Conseguenze: `finish_dream_cycle` non gira, `runs_since_review` non
    avanza, e l'utente riceve **anche** «Dream failed after …: cannot access
    local variable» dopo il messaggio «niente di nuovo».
  - Fix: `dream_file_states = None` prima del `try` interno.
  - Test nuovo in `tests/command/test_dream_command.py` (oggi nessun test copre
    il ramo): `build_dream_prompt → None` ⇒ un solo messaggio in uscita e
    `runs_since_review` +1.
  - Il cron (`runtime/cron_dispatch.py`) non ha il difetto: verificarlo con lo
    stesso scenario, e il passo 2.1 unifica comunque i due percorsi.

- [x] **0.2 Banco di guardia sui frame WebSocket** (commit a sé, prima di 0.3)
  — fatto 24/09. Invece di un commit rosso, i due eventi da riparare sono
  `xfail(strict=True)`: 0.3 li fa passare e lo strict obbliga a togliere il
  segno. Scansione: `"event": "…"` e `_send_event(conn, "…")` in
  `jenny/channels`, più `ACTIVITY_FRAME_EVENT`; 21 eventi, 19 con ascoltatore.
  - Nuovo `tests/webui/test_ws_events_have_listeners_contract.py`: ogni
    `"event": "<nome>"` emesso in `jenny/channels/*.py` deve comparire come
    stringa in `assets/*.js` o `assets/shared/*.js`.
  - Eccezioni esplicite, ognuna col suo perché scritto nel test: `ready`,
    `attached` (conferme di protocollo, documentate in
    `docs/reference/websocket.md`), e `session_updated` **finché** non si
    decide D5.
  - Deve fallire su `app_data_changed` e `apps_list_changed` finché 0.3 non
    atterra. È il banco che avrebbe preso `98a0230`.

- [ ] **0.3 Le Jenny App tornano ad accorgersi dei cambi**
  - Il gateway emette `app_data_changed` (slug) e `apps_list_changed`
    (`channels/ws_sender.py:219-225`, `:966-982`; da
    `agent/tools/app_actions.py:102` e `agent/turn_states.py:211-224`), ma
    dal `98a0230` nessuno li ascolta: `apps/jenny-sdk.js:205-208`
    (`jenny:data-changed`) non scatta mai, e il cassetto non si rinfresca.
  - Dove: nel costruttore di `AppsSource` (`shared/apps-source.js`), con
    `wsManager.addEventListener('chat:message', …)`. Basta perché ogni cornice
    di app esiste solo dopo che la sorgente è stata costruita (casa:
    `_controllaApp`, `casa-pagine.js:477`; officina: `AppsActions`).
    - `apps_list_changed` → `loadJennyApps()` **solo se** `_jennyLoaded`, e
      senza rimetterlo a `false` (niente lampo di «caricamento»; il confronto
      di firma in `mobile-launcher.js:745-771` evita di ricostruire le righe).
      Scarto voluto dal vecchio `_reloadJennyApps`: scriverlo nel commit.
    - `app_data_changed` → nuovo `onAppDataChanged(fn)`; iscritti:
      `AppsActions` (la mini-app aperta, `_openApp`, sullo schema
      dell'observer del tema a `:88-93`) e `CasaPagine` (la pagina app
      corrente, via `_finestraPagina()` se `kind === 'app'` e `ref === slug`).
      Posta `{type: 'jenny:data-changed', slug}` all'iframe.
  - Trappola: i fake `fonte` (`test_app_sheet_pin_client.py:105`) e
    `appsSource` (`test_casa_pista_client.py:254`) non hanno il metodo nuovo →
    aggiornarli (preferito) o chiamare con `?.`.
  - Pulizia nello stesso commit: KDoc orfane a `apps-source.js:252`, `:308`
    (metodi tolti in `5c31cb6`), import inutile di `wsManager` in
    `apps-actions.js:26`, commento falso in `mobile-launcher.js:23-24`.
  - Test nuovo `test_apps_live_refresh_client.py` (node, moduli veri,
    ws-manager finto come `EventTarget`): lista letta + `apps_list_changed` ⇒
    una fetch; lista mai letta ⇒ nessuna; `app_data_changed` slug giusto ⇒
    `postMessage`, slug diverso ⇒ niente. Più un caso in
    `test_casa_pista_client` per la pagina app.
  - Sul telefono: un'app aperta, chiedere a Jenny di cambiarne i dati, l'app
    si aggiorna senza riaprirla.

- [ ] **0.4 Nella casa gli avvisi si cancellano quando guardi la chat**
  - `MainActivity.kt:139-144` (`CHAT_ON_SCREEN_JS`) legge
    `window.mobileApp.currentMode === 'chat'`; `casa-app.js:293` si registra
    come `window.mobileApp` ma `currentMode` non ce l'ha. `JennyNative
    .chatOpened()` lo chiama solo `mobile-chat.js:1314`. In casa, la shell di
    default, gli avvisi in coda non si cancellano mai.
  - Contratto nuovo, uguale nei due gusci: `isChatOnScreen()`.
    - officina: `return this.currentMode === 'chat'`;
    - casa: `this.view === 'chat' && this._voce?.kind === 'chat'` — la pagina
      chat personale, non un quaderno (l'avviso proattivo arriva lì, v.
      docstring di `openChat`); `_voce` nullo al boot ⇒ `false`, la direzione
      d'errore giusta;
    - Kotlin: `typeof app.isChatOnScreen === 'function' &&
      app.isChatOnScreen()`, come `OPEN_CHAT_JS`.
  - Casa: `_segnalaChatAschermo()` chiamato da `onPaginaCambiata` (`:740`) e
    dal ramo chat di `_setView`; chiama `JennyNative.chatOpened()`.
  - Aggiornare il «contratto col guscio nativo» documentato in
    `casa-app.js:11-17`.
  - Test: `test_native_shell_contract.py:311-336` parametrizzato su
    `mobile-app.js` **e** `casa-app.js`; caso node in
    `test_casa_switch_client.py` (pagina chat ⇒ `true` e `chatOpened`; pagina
    app/lettore ⇒ `false`).
  - Sul telefono: arriva un avviso, si apre la casa sulla chat → la notifica
    sparisce; su un'altra pagina resta.

---

## Fase 1 — Le decisioni tue

Ogni riga ha una raccomandazione. **Decise il 24/09/2026: tutte come da
raccomandazione** («ok alle raccomandazioni»), quindi 1.1 è eseguibile.

| # | Cosa | Fatti | Raccomandazione | Scelta |
|---|---|---|---|---|
| D1 | `CronService.enable_job` / `update_job` / `run_job` (`cron/service.py:1318,1338,1381`) | Nessuna produzione: il tool cron espone solo add/list/remove, le rotte sono in sola lettura per scelta (`cron_routes.py:1-13`). `run_job` è l'innesco di ~30 test; `update_job` è l'unico scrittore di azioni `"update"` in `action.jsonl` | Cancellare `enable_job`, `update_job` e il ramo `update` di `_merge_action`; **tenere** `run_job` (o spostarlo in un helper dei test) || raccomandazione (24/09) |
| D2 | `WikiConfig.extensions` (`config/schema.py:855`) + `create_renderer(extensions=)` | Mai letto: l'unica chiamata non lo passa. La tabella pubblica `docs/reference/configuration.md:426` lo dà per configurabile — oggi la doc mente | Cancellare campo, parametro e riga di doc (collegarlo è una riga, ma un'estensione Markdown scelta dall'utente è superficie da validare che nessuno ha chiesto) || raccomandazione (24/09) |
| D3 | `IntegerSchema` / `NumberSchema` primo argomento posizionale (`agent/tools/schema.py:96,132`) | Salvato in `_value`, mai emesso. Metà delle chiamate lo intendeva come default (`android_web.py:352` → 5), l'altra metà è riempitivo o contraddittorio (`android_web.py:456` → 0 con `minimum=100`; `filesystem.py:882-894` → 1 su campi opzionali) | Cancellare il parametro e i valori alle chiamate; se serve, `default=` keyword come `BooleanSchema`, solo dove è vero || raccomandazione (24/09) |
| D4 | Rotta `/api/webui/skills/{n}/delete` + `delete_workspace_skill` + `SkillsLoader.delete_skill` | Nessun client; la decisione del 21/09 toglie la cancellazione dalla UI; `62e55ed` l'ha rinforzata col 403 sulle integrate. Nessun tool la usa | Cancellare (una capacità HTTP senza client, contro una decisione presa) || raccomandazione (24/09) |
| D5 | Frame `session_updated` (`ws_sender.py:310`, flag in `bus/events.py:62`) | Esce a **ogni** `turn_end` verso tutte le connessioni, nessun JS lo legge dal 0.3.0; il produttore via metadata è morto con `de79d28`. `docs/reference/websocket.md:229` promette motivi che nessuno emette | Cancellare frame, flag, test e sezione di doc || raccomandazione (24/09) |
| D6a | Kotlin `UpdateBridge.canSelfUpdateSilently()` (`:173`) | Mai chiamata; l'esito silent/prompt è già gestito dopo il commit; nessun «aggiorna di notte» in piano | Cancellare || raccomandazione (24/09) |
| D6b | Kotlin `JennyBrowserBridge.isIsolated()` (`:331`) | Mai chiamata; il commento a `:216-219` dice «lo diciamo a Python invece di fingere isolamento» e non lo dice nessuno | **Collegarla**: `browser.py` la chiede su `browser_open` e, se `false`, aggiunge un avviso («cookie condivisi con web_fetch») — poche righe, e il commento diventa vero || raccomandazione (24/09) |
| D7 | `i18n.setLocale` / `onLocaleChange` e i 10 iscritti | Nessun chiamante dal `3d57980`; la lingua viene da `localStorage.locale` poi `navigator.language`. Chi aveva scelto col vecchio selettore **resta bloccato** su quel valore. `casa-tu-e-jenny-checklist.md:25-41`: «La lingua non c'è, ed è una decisione misurata»; aperta solo l'idea di `android:localeConfig` | Cancellare `setLocale`, `onLocaleChange`, gli iscritti; **smettere di leggere `localStorage.locale`** così nessuno resta bloccato. Con `localeConfig` l'Activity si ricrea, il cambio dal vivo non serve || raccomandazione (24/09) |
| D8 | `power.is_device_idle` + `PowerBridge.isDeviceIdleMode` | Solo test; `Watchdog.kt:247` usa `pm.isDeviceIdleMode` direttamente | Cancellare entrambi || raccomandazione (24/09) |
| D9 | `template_sync_error`, `MessageBus.dropped_outbound`, `soul_rules.extract_rules` | Il primo lo leggono solo i test (il log ERROR c'è già); il secondo solo un test (il conteggio è già nel log ogni 100); il terzo è l'oracolo di ~10 asserzioni | Cancellare i primi due (tenere il contatore interno); tenere `extract_rules`; tenere `outbound_size` (helper di ~12 test) || raccomandazione (24/09) |
| D10 | MyTool: `workspace_sandbox` in `READ_ONLY`, in `_inspect_all`, e in `RuntimeState` | `AgentLoop` quell'attributo non l'ha mai avuto: `check` risponde «not found», l'inspect lo salta | Cancellare i tre punti e la riga del mock (`test_self_tool.py:38`). Alternativa, se vuoi che Jenny veda il sandbox: una property su `AgentLoop` che rende `workspace_scopes.sandbox_status` || raccomandazione (24/09) |
| D11 | `/api/casa/schermate/set` accetta ancora la lista nuda (`casa_routes.py:159-181`) | L'unico mittente (`setSchermate`) è uscito con `03b1e15`; UI e server escono dallo stesso APK | Cancellare il ramo e `test_the_bare_list_keeps_the_saved_order`, `ordine` obbligatorio || raccomandazione (24/09) |
| D12 | `display_name` sui quattro canali | Nessuno lo legge (quello in `settings_api.py:490` è un altro) | Cancellare attributi e assegnazioni nei mock || raccomandazione (24/09) |
| D13 | `android/image_source/talk_2a.PNG`, `talk_2b.PNG` (1,65 MB) + README | Nessun riferimento; `talk_2b` = `idle` byte per byte. Il README dice «15 webp» (sono 10 + 9 a livelli) e cita pose uscite da `FILES` con `9d6c603`; `SOSTITUIRE_UNA_POSA.md:17-23` elenca pose che non si esportano più | Cancellare le due PNG; **correggere** README e tabella (talk_1a/1b/think segnate «solo riferimento dei test», `test_mascot_layer_sources.py:50-59`) || raccomandazione (24/09) |

- [ ] **1.1 Esecuzione delle decisioni.** Un commit per decisione, nell'ordine
  della tabella; ogni commit porta con sé i test che asserivano la cosa tolta
  e la doc pubblica che la descriveva (`docs/` è la fonte del sito: modifiche
  di contenuto, nessun rename di file). D5 toglie anche `session_updated`
  dalle eccezioni del banco 0.2. D6b ha un test in `tests/agent/tools/
  test_browser.py` sull'avviso. D7 va provato sul telefono (lingua di sistema
  it ↔ en).

---

## Fase 2 — Duplicati Python

Ordine per valore/rischio. Le trappole sono le differenze fra le copie: ognuna
va o conservata o scelta consapevolmente, e scritta nel commit.

- [ ] **2.1 `run_dream_turn` unico in `agent/dream_cycle.py`** (dopo 0.1)
  - Copie: `command/builtin.py:318-414` e `runtime/cron_dispatch.py:689-809`.
  - `run_dream_turn(agent, store, prologue, *, take_snapshot) ->
    DreamTurnResult(outcome, resp, refused, last_cursor)`, con `DreamOutcome =
    NO_INPUT | ADVANCED | HELD_BATCH | BLOCKED | INCOMPLETE`. I testi restano ai
    chiamanti (frase all'utente in `/dream`, log nel cron).
  - Trappole: `held_batch` va controllato **prima** di `dream_run_completed`;
    lo snapshot entra come parametro (`snapshot_before_dream` via getattr in
    `/dream`, `self._snapshot_before_dream` nel cron); `refused` si normalizza
    con `_int_or_zero` (il cron usa `isinstance(int)`, che lascia passare un
    `bool`) — spostarla in `dream_cycle`.
  - Test nuovo parametrizzato su ogni `DreamOutcome`: cursore e contatori
    identici lanciando da `/dream` e dal cron. ~60 righe, rischio medio.

- [ ] **2.2 [SICUREZZA] Fetch con redirect rivalidati a ogni hop**
  - Copie: `runtime/update_check.py:419-445`, `runtime/update_install.py:
    280-305`, `agent/tools/download.py:100-130`, `webui/media_ingest.py:70-100`;
    `MAX_REDIRECTS` ×4, User-Agent browser ×2.
  - Nuovo `jenny/security/fetch.py` — **non** `security/network.py`, che
    `config/loader.py` importa al boot e si porterebbe dietro `httpx`. Contiene
    `MAX_REDIRECTS`, `BROWSER_USER_AGENT` e un context manager asincrono
    `open_validated_stream(client, url, *, headers=None, https_only=False)`
    che rende `(response_200, final_url)` o solleva `ValueError`.
  - Trappole: `update_check` oggi controlla https solo sul primo URL (un
    redirect https→http passa) → `https_only=True` anche lì, **irrigidimento
    scritto nel commit**; l'helper chiama `network.validate_url_target` per
    modulo, quindi ~13 `monkeypatch.setattr(<modulo>, "validate_url_target")`
    si spostano su `jenny.security.network`; il `_FakeClient` di
    `test_media_ingest.py` va esteso; i messaggi d'errore si unificano.
  - Test nuovo in `tests/security/`: redirect verso la LAN, downgrade a http
    con `https_only`, sesto hop, `Location` mancante. ~60 righe.

- [ ] **2.3 [SICUREZZA] `is_path_within` pubblico**
  - Siti a mano: `webui/ws_http.py:761`, `media_api.py:214`,
    `wiki_routes.py:323,447`, `transcript_markdown.py:58`, `commands.py:249`,
    `wiki.py:565`, `apps_routes.py:279`, `agent/wiki_provenance.py:162,214,358`
    (fuori `media_api.py:115`, che usa il relativo come valore).
  - Trappole: `_is_path_within` (`security/workspace_policy.py:146`) mette in
    cache le radici e la invalida solo all'ingresso di python_exec — wiki e
    progetti cambiano a runtime, quindi la versione pubblica risolve senza
    cache; `path_resolved=True` dove il chiamante ha già risolto;
    `wiki_routes:323` oggi lascia uscire `OSError` (500) e dopo dà 403 —
    scriverlo.
  - Test nuovo: fuga via symlink e via `..` su una rotta wiki e una apps.

- [ ] **2.4 Metadati d'errore dei provider** (copie già divergenti)
  - `anthropic_provider.py:138-198` ↔ `openai_compat_provider.py:644-731` →
    `LLMProvider._error_response(e, *, partial_content, message=None)`.
  - Scelte da fare e fissare con un test: guardia `ResponseNotRead` (c'è solo
    in Anthropic → tenerla); ordine degli header (OpenAI legge prima
    `e.headers` → tenerlo); `retry_after` anche dal testo del messaggio (solo
    Anthropic) → decidere; il messaggio speciale di `ProviderHTTPError` resta
    a OpenAI.
  - Test nuovo: la stessa eccezione finta dà lo stesso `LLMResponse` coi due
    provider, anche su una response in streaming non letta. ~45 righe.

- [ ] **2.5 `stream_timeout_response` e parser SSE unico**
  - «Stream stalled» ×2 (`anthropic_provider.py:604-615`,
    `openai_compat_provider.py:1037-1047`) → helper in `base.py`.
  - `_iter_chat_completion_sse` (`openai_compat_provider.py:757-790`) =
    `openai_responses/parsing.iter_sse`: resta come metodo sottile che delega,
    perché `test_stream_first_output_timeout.py:45` lo patcha. L'SSE di
    Anthropic resta suo (legge `event:`). ~45 righe.

- [ ] **2.6 Mixin di abilitazione dei tool**
  - `enabled()`/`disabled_reason()` identici in `android_web.py:371,474` e
    `browser.py:303` → `AndroidWebGateMixin`; `enabled()` di python_exec in
    `exec_session.py:487,609` e `python_exec.py:3175` → `PythonExecGateMixin`;
    `__init__`/`create` delle due classi exec_session in una base.
  - Il mixin sta prima di `Tool` nell'MRO; la docstring italiana di
    `disabled_reason` si sposta sul mixin. ~45 righe.

- [ ] **2.7 Diff di righe e path delle modifiche file**
  - `apply_patch.py:42-61` → `file_edit_events.line_diff_stats`. Unica
    differenza misurata: con `before == ""` i separatori Unicode (`\f`, `\x85`,
    ` `) contano come a capo per `splitlines` e non per
    `_text_line_count` (su `\r` coincidono). Adottare `line_diff_stats`,
    fissarlo con un test.
  - `resolve_file_edit_path` (`file_edit_events.py:50-73`) delega a
    `_resolve_raw_file_edit_path` (`:239`). ~30 righe.

- [ ] **2.8 `utils/file_edit_streaming.py`**
  - I due decoder di stringa JSON (`:496`, `:537`) → uno con `partial: bool`;
    su `\u` troncato / stringa aperta il «prefix» rende il parziale, il
    «complete» `None`; entrambi lasciano `\b`, `\f`, `\/` letterali (da
    conservare). Tabella di casi come test.
  - Throttle ×3 (`:339`, `:418`, `:436`) → `_EmitThrottle`; trappola:
    `_StreamingPatchFileState.should_emit` aggiorna `last_added/last_deleted`,
    letti da `flush` (`:164`). ~40 righe.

- [ ] **2.9 Persistenza minuta**
  - `.dream_review` letto ×3 in `agent/memory.py` (`:959`, `:994`, `:1024`) →
    `_read_review_state()`.
  - Append JSONL durevole ×3 (`memory.py:621`, `consolidator.py:729`,
    `transcript_store.py:332`) → `append_jsonl_durable(path, records, *,
    tolerate_fsync_error=False)`; solo il transcript tollera un `fsync` fallito
    — nel consolidator deve propagarsi, il chiamante cancella gli originali
    subito dopo. ~25 righe.

- [ ] **2.10 Alias e helper web** (rischio minimo)
  - `QueryParams` ridefinito in 7 moduli webui → da `channels/http_utils.py`;
    `_query_first` in `settings_api:316` e `ssh_api:81` →
    `http_utils.query_first`; `apps_routes.APP_ACTION_RE` →
    `apps/manifest.ACTION_NAME_RE` (non toccare lo script autonomo della skill
    `app-creator`). ~20 righe.

- [ ] **2.11 [SICUREZZA] Regex degli id sul wire**
  - `subagent_routes.py:52`, `ws_rpc.py:28`, `ui_query.py:27`,
    `subagent_activity_wire.py:108` usano `^…$` con `.match` — `$` accetta un
    `\n` finale; `ssh_jobs.py:70` usa `\A…\Z`. Un `WIRE_ID_RE` unico con
    `\A…\Z` (irrigidimento, scritto nel commit). Test: `"abc\n"` rifiutato.

- [ ] **2.12 Costanti e utility minori**
  - `CHARS_PER_TOKEN = 4` in `utils/helpers.py` (usi: `helpers.py:357,600,634`,
    `consolidator.py:84`, `memory.py:499`; il consolidator dipende dalla stessa
    convenzione di `truncate_text_to_tokens`).
  - `now_ms()` in `utils/helpers.py` al posto dei 4 `_now_ms`; gli `int(time.
    time()*1000)` inline solo dove non sono parametri iniettabili (non
    `cron_dispatch.py:331`).
  - Troncamento testa+coda ×2 (`exec_session.py:226`, `python_exec.py:3097`) →
    `truncate_head_tail`, con un test che oggi non c'è. ~25 righe.

- [ ] **2.13 Facoltativi** (rinviabili senza perdita)
  - Base `_NumericSchema` (dopo D3); `NonStreamingChannelMixin` per gli stub
    di floating/notification/telegram; `BridgeCache.call_bool` per
    `floating._call`/`power._call` (log `warning` vs `debug` e timeout diversi
    → parametri).

---

## Fase 3 — WebUI

- [ ] **3.1 Metodi usati solo dai test**: `casa-pagine.js:186` `riordina`,
  `casa-app.js:583` `openTu`, `mobile-chat.js:908` `loadMoreHistory`; i test
  passano al percorso vero (`salva(schermate, ordine)`; `_setView('chat')` +
  `pagine.vaiAId('impostazioni')`; `_pager.loadMore()`).

- [ ] **3.2 `renderRichContent`** (`mobile-chat.js:152-154`, involucro di una
  riga) → `renderRich` nelle 6 chiamate; aggiornare il fake in
  `test_message_bubble_client.py:108`.

- [ ] **3.3 `renderMarkdown` condiviso**: `shared/markdown.js`, fallisce chiuso;
  copre `casa-chat.js:70-82` e `mobile-chat.js:118-135`. `initMarked()` (hljs +
  «Copia») resta all'officina. Il nome `renderMarkdown` resta nei due file
  (`test_rich_surfaces_contract.py:31-34`).

- [ ] **3.4 `breaks: true` anche in casa** — commit a sé perché **cambia
  l'aspetto**: oggi la casa non chiama `initMarked` e gira con `breaks:false`,
  quindi una lista a righe singole si fonde in un paragrafo in casa ma non in
  officina né nella bolla flottante. Spostare `gfm/breaks` in `markdown.js`,
  riallineare `test_floating_markdown_contract.py:92`. Screenshot prima/dopo.

- [ ] **3.5 La minichat eredita la macchina a stati** — `mobile-jenny.js:293-352`
  ricopia `shared/jenny-mascot.js:562-613`. Il genitore chiama in fondo un
  gancio `_afterChatFrame(msg)`; la minichat sovrascrive solo quello, con le
  sue differenze (`delta` accumula e mostra; `stream_end` mostra; `turn_end`
  azzera `awaiting` e `_replyTimer`, mostra `✿` se non c'è stata risposta,
  invalida la cronologia; `error` mostra il testo). **Cambio voluto**: un
  `message` senza testo né `tool_events` oggi nella minichat non fa niente e
  nel genitore passa a `thinking` → si allinea al genitore, scritto nel commit.
  Rompe `test_chat_dead_ends_contract.py:98-116` (conta `_pendingTurn = false`
  in `_handleFrame`) → puntarlo al gancio. Test node nuovo: minichat chiusa,
  `turn_end` ⇒ `invalidateHistory`; `error` ⇒ testo e umore `sad`.

- [ ] **3.6 L'aggancio dei chip**: `commands-chip.js:58-82` e
  `scope-chip.js:82-106` → `armComposeMenu(chip, id)` in `shared/state.js`.
  `readonlyTurn` resta a scope-chip. Il banco di `test_scope_chip_init_client
  .py:183` deve definire l'helper.

- [ ] **3.7 Il velo delle mini-app**: `apps-actions.js:199-213` e `:230-267` →
  `_montaVelo(slug, app, iframe, {external})`.

- [ ] **3.8 I fogli del workspace**: `mobile-workspace.js:614-643` e
  `:1057-1079` → `_apriFoglio(azioniHtml, onPick)`. Facoltativo: la grazia da
  400 ms (anche `apps-actions.js:429`, `casa-quaderno.js:92`) →
  `graziaBackdrop` in `shared/dialog.js`. Riallineare
  `test_keyboard_a11y_contract.py:218-229` (cerca `openedAt` nei due metodi).

- [ ] **3.9 CSS — le righe**: `.marca-riga`, `.ssh-riga`, `.cron-riga`
  (`mobile-style.css:4478,4547,4426`) in un gruppo con override di padding
  per `.cron-riga` (6px vs 4px). `test_officina_righe_contract.py:294,432,586`
  cercano `^\.X-riga \{` con `min-height`: o si lascia `min-height` in ogni
  regola, o si passa a un helper che trova il selettore dentro una lista.

- [ ] **3.10 CSS — le pillole**: `.scope-chip`, `.write-switch`,
  `.commands-chip` (`:514,567,607`), `.scope-chip` primo nella lista
  (`test_scope_menu_contract.py:69` lo trova via `,\n`). `flex`/`min-width` e
  `flex-shrink` restano nelle regole singole (`test_write_switch_contract.py:
  142-148`). **Difetto piccolo**: in `prefers-reduced-motion` (`:848-851`)
  manca `.commands-chip:active { transform: none; }` — va aggiunto.
  `.commands-chip` si allinea a `.16s` e `user-select: none`.

- [ ] **3.11 CSS — il resto**: intestazioni pieghevoli (`:1697`, `:1811`,
  `:2649`; `.chat-tool-header` tiene il colore e non ha `font-weight`),
  `.file-preview-close`/`.session-info-close` (`:5144`, `:5295`), in casa
  `.casa-tab`/`.casa-seg-btn` e contenitori (`casa-style.css:1323`, `:2001`).
  Nessun test li cerca per regex.

- [ ] **3.12 Fine fase**: rilanciare il rilevatore di cloni su
  `jenny/templates/ui/*.css` e `*.js` (è uno script usa-e-getta
  dell'audit, finestra di 8 righe normalizzate, fuori dal repo: si riscrive
  in dieci righe) per vedere che le coppie sono sparite e non ne sono nate di
  nuove; poi build, install, screenshot di casa, officina, cassetto,
  impostazioni.

---

## Fase 4 — Kotlin

Il Kotlin non gira in CI: ogni passo è build + prova sul telefono.

- [ ] **4.1 `WakeReceiver` passa da `GatewayStarter`**: `WakeReceiver.kt:196-217`
  è la copia di `GatewayStarter.ensureUp` (`:64-99`), il cui ramo `wakeTick`
  oggi è morto (l'unico chiamante passa `false`). Le chiamate a `:54`, `:57`,
  `:166` diventano `ensureUp(ctx, reason, wakeTick = …)` con
  `alarmFallback = false`. Cambia il TAG di logcat (`Jenny` →
  `GatewayStarter`): aggiornare le note che lo cercano. Prova: forzare una
  sveglia (v. memoria «Forzare un job di sistema») e leggere il log.

- [ ] **4.2 Le altre `startForegroundService` dirette**:
  `BootReceiver.kt:98` → `ensureUp` (identico); `Watchdog.kt:161` →
  `ensureUp`, tenendo la sua diagnosi e il `finally { arm() }`;
  `MainActivity.kt:735` — oggi un'eccezione risale, con `GatewayStarter`
  verrebbe inghiottita e si vedrebbe `showError()` al timeout: **accettarlo
  esplicitamente** o lasciarla; `ReplyReceiver.kt:90-104` resta come
  eccezione documentata (lock senza `EXTRA_WAKE_TICK`, extra propri,
  `postReplyFailure`). Correggere la KDoc di `GatewayStarter:8-22`, che oggi
  si dice l'unico ingresso.

- [ ] **4.3 `FloatingOverlayController`**: `mascotWinParams` (`:981`) e
  `gripParams` (`:997`) → `parkedSquareParams(ctx)`, restando **due istanze**
  (`:859-867`); la KDoc della maniglia (`:971-979`) torna al suo posto.

- [ ] **4.4 La WebView nascosta**: `HiddenWebView.create(appContext, tag)` con
  le impostazioni comuni e un solo `USER_AGENT_MOBILE`
  (`AgenticSearchBridge.kt:35`, `JennyBrowserBridge.kt:57`). Fuori
  dall'helper: `sourceId` nel log console (solo AgenticSearch), il
  `sessionClient()` e il profilo MULTI_PROFILE del browser, il cambio di
  `webViewClient` a ogni chiamata. `configureWebContentsDebugging` resta dov'è
  (`test_webview_debugging_is_gated.py`).

- [ ] **4.5 Il salto bloccante sul main thread**: `MainHop.call(timeoutMs,
  fallback, tag) { … }` (esegue sul posto se già sul main) per
  `FloatingBridge.onMain` (`:52`, 3 s, `false`), `JennyBrowserBridge.close`
  (`:293`, 10 s, timeout ignorato), `currentUrlAndTitle` (`:368`, 5 s),
  `open` (`:382`, 10 s). Fuori: `evaluate` (`:351`) e
  `AgenticSearchBridge.evaluateOnPage` (`:194`), che si sbloccano in una
  callback. Cambio: col try/catch un'eccezione nel blocco non abbatte più il
  processo — scriverlo.

---

## Fase 5 — Impalcature dei test

Collezione di oggi: **10.649** test. `tests/support/` esiste già (import
`from support.X`, la conftest di root mette `tests/` in `sys.path`);
`--strict-markers` è attivo, quindi niente marker nuovi: alias di `skipif`.

**Protocollo per ogni commit**
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --collect-only -q -p
  no:cacheprovider | grep '::' | sort` prima e dopo → `diff` vuoto (o solo i
  rinomini attesi, con la mappa preparata prima).
- Stessi skip (`-rs`); per la famiglia JS anche con node nascosto
  (`PATH=/usr/bin:/bin`).
- 3.14 e 3.11; `ruff check tests/`.
- Script d'equivalenza usa-e-getta per gli estrattori: `vecchio(src, nome) ==
  nuovo(src, nome)` su ogni chiamata esistente, sugli asset veri.
- Mutazione a campione (≥2 file per famiglia): rompere la produzione, il test
  migrato va rosso, ripristinare — con `PYTHONDONTWRITEBYTECODE=1` e una
  modifica che **cambia la lunghezza** del file (o `sleep 1; touch`), v. la
  memoria sul `.pyc` stantio.

- [ ] **5.1 Harness JS** (~850 righe nette) — `tests/support/js_harness.py`:
  `NODE`, `requires_node`, `ASSETS`, `I18N_DIR`, `run_js(script, *, prelude="",
  timeout=60, env=None) -> str`, `member(src, name, *, prefixes=("async ",
  "get "), body_only=False)`, `function(src, name)`, `locale(name)`,
  `brace_block(src, start)`.
  - Trappole: `env=` **sostituisce** l'ambiente (`test_gesto_componenti_client
    .py:136`, `test_cron_view_client.py:52` con `TZ`), non va fuso con
    `os.environ`; 4 `test_casa_*` accettano solo `async` in `_member` (allargare
    a `get` potrebbe prendere un getter omonimo) → passare `prefixes`;
    `test_casa_who_contract.py:24` vuole solo il corpo; i timeout 30/120 s
    restano espliciti; `_const` **non** si unifica (7 forme vere, una legge
    Kotlin); `_corpo` è lo stesso nome per tre cose diverse.
  - Fuori dalla migrazione di `run_js`: `test_every_module_parses.py`,
    `test_graph_search_contract.py` (jsdom), `test_node_is_available.py`.
  - Ordine: le 27 copie delle due varianti principali di `_run_js`, poi le
    altre, poi `_member` standard, `_function`, `_locale`, `brace_block`.
  - Facoltativo: meta-test che vieta nuove copie di `_NODE = shutil.which`.

- [ ] **5.2 Attese asincrone** — `tests/support/aio.py`: `wait_until(pred, *,
  timeout=5.0, interval=0.01, msg=None)` che alla scadenza **fallisce**,
  `settle_tasks(get_tasks, *, timeout=5.0)` che rilegge i task a ogni giro,
  `drain_outbound(bus)`.
  - Prima i 5 polling «a 50 giri»: `agent/test_subagent_watchdog.py:234,289`,
    `channels/test_websocket_subagent_activity.py:554,571`, e
    **`channels/test_dispatcher_reasoning.py:271`, che dopo 50 giri prosegue
    senza nessuna asserzione** — oggi quel test può passare senza verificare
    niente. Confrontare con la memoria «Un drain da 50 yield non aspetta un
    thread» e aggiornarla.
  - Poi i 4 drain di `bus.outbound` e le 3 copie di `_wait_until` (timeout 1 s
    e 15 s: restano espliciti, mai abbassati). `_spin` di
    `test_telegram_typing.py:64` resta: verifica che una cosa **non** accada.
  - Il rafforzamento di `_drain_background_tasks` in un sotto-commit: può far
    emergere difetti veri.

- [ ] **5.3 `make_loop` / `make_provider`** (~200 righe) — spostati in
  `tests/support/agent.py`, `conftest.py` li reimporta (la fixture
  `loop_factory` resta). Correggere i tre import `tests.agent.*`
  (`test_model_preset.py:7`, `test_loop_provider.py:22`, `test_task_cancel.py`)
  che funzionano solo col repo nel path e caricano la conftest due volte;
  spostare anche `subagent_provider_fakes.py`.
  - Trappole: 19 copie su 20 usano un `MagicMock()` nudo → `bare=True`;
    `model` e `context_window_tokens` devono poter valere `None` e allora non
    essere passati; `patch=(…)` configurabile (`test_session_routing.py:33`
    ne patcha due). `test_task_cancel.py:27` resta locale.
  - Ordine: la tripla identica (`test_loop_runner_integration.py:17`,
    `test_runner_governance.py:16`, `test_runner_injections.py:28`), le coppie,
    le singole.

- [ ] **5.4 Runner** (~200 righe, rischio medio) — `support/runner.py` con
  `empty_tools()` e `make_spec(**overrides)`; `max_iterations` sempre esplicito
  dove si verifica lo `stop_reason`; `_MAX_TOOL_RESULT_CHARS` non è sempre il
  default (`test_tool_error_budget.py:36` usa 16.000). `script_provider` solo
  dove il test sostituiva già sia `chat_with_retry` sia lo streaming, o un
  test che deve restare sul non-streaming passerebbe a torto.

- [ ] **5.5 `GatewayHTTPHandler`** (~150 righe) — `support/gateway_http.py`:
  `AUTH_SECRET`, `make_request(path, token=AUTH_SECRET, headers=None, *,
  always_append=False)`, `make_handler(skills_path, **extra)`. Fuori:
  `test_websocket_http_routes.py:21` (costruisce `GatewayServices`, altra
  cosa), la coppia CSP/integrity. Tre `_make_request` accodano il token anche
  se è già nel path → `always_append`.

- [ ] **5.6 Sessioni finte heartbeat/cron** (~80 righe) — `support/sessions.py`:
  `FakeSession(key="")`, `FakeSessions(*, shared=False)` con `saved:
  list[str]`, `.session`, `.save_count`. In `test_cron_dispatch_heartbeat.py:60`
  e `_update.py:50` ogni chiave dà **la stessa** sessione e `saved` è un int;
  in `test_bound_runner.py:81` dipende dalla chiave. Fuori
  `test_heartbeat_user_rearm.py:111` (usa `Session` veri).

- [ ] **5.7 `parametrize`** (per ultimo, rischio più alto) — candidati
  verificati: la tripla `*_keeps_task_local_context` in
  `test_tool_contextvars.py:17,49,100`, i `test_save_turn_drops_*` in
  `test_loop_save_turn.py`. `ids=` riprende i vecchi nomi; il diff della
  collezione deve mostrare solo i rinomini della mappa. Gli altri file
  (`test_loop_progress.py`, `test_subagent_tools.py`,
  `test_websocket_channel.py`) si valutano uno per uno.

---

## Fuori dal piano, deciso

- **Helper usati solo dai test ma su cui i test si appoggiano**:
  `get_running_count`, `silent_turn_metadata`, `build_graph`,
  `list_statuses`, `known_tools`, `pending_cron_job_ids_for_session`,
  `validate_value`, `outbound_size`, `extract_rules`, `is_watching`/
  `watch_count`. Restano: sono punti di osservazione, non codice perso.
- **`scripts/vendorize_ui.py`**: strumento di manutenzione (si mette un URL CDN
  in `index.html`, lo si lancia, scarica in `vendor/`). Che oggi non trovi
  niente è normale.
- **`_formatGapWhen`** (`mobile-settings.js:686`): non è una copia di
  `whenText` — oltre «ieri» un buco nel battito va detto con data e ora.
  Basta un commento che rimandi da uno all'altro.
- **`_FakeAgent`, `_FakeMemory`, `FakeAPI` di Telegram**: tutte le copie sono
  diverse, e `agent/test_dream_cycle.py:173` difende un fake per file.
- **Frame `ready` e `attached`**: conferme di protocollo documentate come API
  (`docs/reference/websocket.md`), usate dai test d'integrazione.
- **Codice ereditato che non è duplicato ma parallelo per scelta**: firme dei
  provider, `process_direct`/`process_direct_outcome`, gli stub
  `TYPE_CHECKING`, `reindex_wikis.py` (copia documentata), i canali di
  notifica Kotlin con parametri diversi.

## Ordine consigliato e peso

| Fase | Commit | Righe (circa) | Rischio | Telefono |
|---|---|---|---|---|
| 0 — difetti | 4 | +100 | basso–medio | sì (0.3, 0.4) |
| 1 — decisioni | fino a 13 | −400 | basso | sì (D6b, D7) |
| 2 — Python | 13 | −450 | medio sui [SICUREZZA] | no |
| 3 — WebUI | 12 | −200 | basso–medio | sì |
| 4 — Kotlin | 5 | −100 | medio | sì |
| 5 — test | 7+ | −1.500 | basso col protocollo | no |

Fase 0 subito; la 1 appena la tabella ha le scelte; poi 2 e 5 si possono
alternare (nessuna tocca l'altra), 3 e 4 si chiudono ognuna con una build.
