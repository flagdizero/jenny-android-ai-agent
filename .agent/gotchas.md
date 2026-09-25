# Common Gotchas

## Do not use `ruff format`

**Do not run `ruff format`** — it destroys git blame history. Only `ruff check` should be used (`docs/contribute/code-style.md` says the same; CONTRIBUTING.md does not mention ruff at all).

## Config `${VAR}` References

`config/loader.py` resolves `${VAR}` patterns in `config.json` at load time. This is **not** a shell-like default-value syntax. Interpolation is **not** part of `load_config`: it happens later, in `resolve_config_env_vars`, called from `gateway_runtime._load_runtime_config`. A missing variable is therefore not a degraded start — the `ValueError` is re-raised as a `RuntimeError` and the **gateway fails to boot**, retrying until `MAX_RETRIES`. Expect a boot loop, not defaults.

Example valid usage:
```json
{ "providers": { "openrouter": { "apiKey": "${OPENROUTER_KEY}" } } }
```

## Android-only runtime

Android is the only supported runtime target. There is no shell, no pip, and no CLI. Use `python_exec` for all code execution. Do not introduce shell execution tools or desktop-only workflows.

## Prompt Templates

Agent system prompts and scenario-specific instructions live in `jenny/templates/` as Jinja2 markdown files (`agent/identity.md`, `agent/platform_policy.md`, plus the workspace seeds `HEARTBEAT.md`, `SOUL.md`, `USER.md`, `AGENTS.md` at the top level). Changing these files alters agent behavior as directly as changing Python code. They are loaded by `utils/prompt_templates.py`.

**Non chiedere a un turno silenzioso di *scrivere* qualcosa in chiusura.** Il preambolo
dell'heartbeat ha detto per un'ora "call `nothing_to_report` … and close the turn with one
short line", e il 2026-09-03 alle 11:25 il modello ha chiuso rigurgitando l'intero preambolo
in coda alla risposta — 4.883 caratteri, con dentro i segnaposto `CHECK_FAILED <task number>:
<one short line naming what stopped you>` e `CHECK_WARNED <task number>`. Il parser li ha
letti come dichiarazioni: un controllo appena riuscito registrato come guasto **e** timbrato
`escalated`, senza che un avviso sia mai partito. Il rigurgito del template è una patologia
nota di questo modello (`strip_think` lo toglie da ciò che raggiunge l'utente dal 27/08); la
difesa lato parser è `could_not_check._is_specimen`. La regola di prompt resta: la risposta
finale di un turno silenzioso non la legge nessuno, quindi non chiederne una — ogni frase che
invita a "chiudere con una riga" è un invito a copiare la riga più vicina, e le righe più
vicine sono i tuoi segnaposto.

Tool descriptions, skills, and replayed session history also shape model behavior. Treat changes to those surfaces like runtime code: keep them narrow, add a focused regression test when possible, and avoid teaching the model to repeat internal markers, local paths, or tool-call text.

## Context Pollution Persists

Anything written into memory, session history, or prompt inputs can be replayed into future LLM calls. Metadata such as timestamps, local media paths, tool-call echoes, and raw fallback dumps must be bounded and sanitized before they become examples for the model to imitate.

## Transcript ≠ session history

Two different stores hold what looks like "the conversation", and only one of them reaches
the model:

- the **WebUI transcript** (`jenny/webui/transcript_store.py`, written by
  `channels/ws_sender.py`) is what the user sees and what a reload replays;
- the **session history** (`jenny/session/manager.py`) is what `turn_states._state_build`
  replays into the prompt. The transcript is never read back into context.

So any new path that sends the user a message must *also* write it into the session, or the
model will not know it ever spoke. Measured on device 2026-08-12: a heartbeat alert ("hps
non è raggiungibile") reached the user as an Android notification and a transcript row, then
the user's "sicura?" was answered as if nothing had been said — the alert lived in the
`heartbeat` session, and the reply was built from `unified:default`.

The supported way in is the `message` tool: a proactive send (cross-channel, or any silent
turn — cron/heartbeat/Dream) is marked `_record_channel_delivery`, and
`runtime/delivery.py::ChannelDeliverer` hands it to `AgentLoop.record_channel_delivery`,
which appends it as an `assistant` message with the `_channel_delivery` marker (the marker
the replay window helpers in `utils/helpers.py` look for so the alert stays attached to the
user's answer). Write it **through that hook**, not with a bare
`session.add_message` from another task: a turn holds the per-session lock for its whole
duration and `_save_turn` appends its block at the end, so an unlocked append can land
between an `assistant`/`tool_calls` message and its `tool` result — an illegal request for
the provider.

## Una riga `role: "user"` non è sempre l'utente

Nella storia di sessione quel ruolo è **anche** la forma con cui il sistema parla al modello,
e tre cose lo usano senza che nessuno abbia digitato niente: un turno di cron, il rientro di
un subagent iniettato a metà turno (`AgentLoop._drain_pending` normalizza qualunque messaggio
in coda a `{"role": "user", ...}`) e lo sprone a un sustained goal. Il marcatore che le
distingue, e l'elenco completo, stanno in `jenny/session/history_meta.py`:
`is_synthetic_history_row()`.

Chiunque legga la storia per rispondere a una domanda **sull'utente** deve passare da lì. I
tre lettori di oggi: la bolla da mostrare in chat
(`webui/transcript.py::_session_user_event`, che il backfill usa quando il transcript di
display non ha un evento `user` per quel turno), il titolo della conversazione
(`session/webui_turns.py::_title_inputs`) e «si è fatto vivo dopo che gli abbiamo scritto?»
(`session/manager.py::last_user_message_ms`, da cui dipende il riarmo dell'heartbeat).

Misurato sul device il 05/09/2026: solo il turno di cron era marcato. Un rientro di subagent
finito *dentro* il turno che lo aveva lanciato veniva quindi mostrato in chat come una bolla
dell'utente col prompt integrale del subagent dentro, e contava come «l'utente ha parlato»
per il riarmo. Lo stesso rientro arrivato *dopo* la fine del turno passava invece da
`_persist_subagent_followup`, che lo marcava, e non si vedeva: la visibilità dipendeva dal
tempismo del subagent.

Il marcatore vive **solo** nel JSONL. `SessionManager.get_history` ricostruisce i messaggi per
il modello con una whitelist di chiavi e i provider ne fanno un'altra prima del filo
(`LLMProvider._sanitize_request_messages`), quindi è lecito appenderlo a un dict che è anche
il payload della richiesta in corso. Le due whitelist sono verificate in
`tests/session/test_history_meta.py`: se una delle due smettesse di filtrare, il campo
finirebbe su un endpoint che non lo conosce.

## Skills as Extension Point

Built-in skills live in `jenny/skills/` (markdown + YAML frontmatter format). Agent capabilities that are "know-how" rather than code should be added as skills, not hardcoded into the agent loop. External skills can be published to and installed from ClawHub.

## Atomic writes: one helper, `utils/path.py::atomic_write`

Any write that replaces a **whole file of state Jenny reads back herself** — cursors,
skills, wiki entries, manifests, snapshot blobs, config — goes through
`jenny/utils/path.py::atomic_write` (unique temp file + fsync + rename + tolerant directory
fsync). Android kills processes freely, so a plain `write_text`/`open(..., "w")` leaves a
*visible* truncated file: a skill whose frontmatter no longer parses, a cursor that reads
back as 0, an unreadable manifest.

Two things that keep going wrong here, both already fixed once:

- **Do not hand-roll temp-file + `os.replace`.** Five places had done it, each subtly
  different and every one of them missing the `fsync` — atomic against a killed process,
  but not against power loss. If you need different behaviour, add a keyword argument to
  the helper; do not write a sixth copy.
- **Orphan temps are hidden, not absent.** A process killed mid-write leaves
  `name.ext.<hex>.tmp` behind for good; `*.tmp` is in `_DEFAULT_INTERNAL_PATTERNS`
  (`webui/workspace_files.py`) so the file browser does not offer it next to the real file.

The exception is deliberate: the agent's own file tools (`tools/filesystem.py`,
`apply_patch.py`, `python_exec_builtins.py`) write the *user's* files, where the write is
the requested effect and replacing the inode would change semantics (`apply_patch` keeps
`newline=""`, permissions and hardlinks must survive). Appends (`history.jsonl`,
transcripts, app collections, the cron action log) are a different failure mode — a partial
trailing line, which every reader already skips — and are not atomic-write candidates.

## Android WebView search/fetch

`jenny/agent/tools/android_web.py` implements `web_search`/`web_fetch` via Chaquopy calling the Kotlin `AgenticSearchBridge` (`android/app/src/main/java/com/flagdizero/jenny/AgenticSearchBridge.kt`), which drives a real hidden WebView to bypass bot detection.

- **Threading**: the Kotlin bridge call is blocking (`CountDownLatch`), so `_bridge_search`/`_bridge_fetch` run it via `asyncio.to_thread` wrapped in `asyncio.wait_for(timeout + 10)`. The extra 10s is an asyncio-level backstop independent of the Kotlin-side timeout, so a stuck WebView can never block the gateway loop.
- **Remote debugging**: `WebView.setWebContentsDebuggingEnabled(true)` is called by `AgenticSearchBridge.configureWebContentsDebugging()` from the bridge's `init`, and **only when the APK is debuggable** (`ApplicationInfo.FLAG_DEBUGGABLE`) — never on a release build (`tests/security/test_webview_debugging_is_gated.py` pins it). The setter is process-wide: once the bridge exists (the Python side builds it lazily, on the first `web_search`/`web_fetch`), every WebView of a debug build is inspectable — the hidden one and the main WebUI alike. Connect the emulator/device via adb and open `chrome://inspect/#devices` in desktop Chrome.
- **Timeout config**: default is 30s, configurable via `workspace/config.json` under `androidWeb.search.timeout`. Note `AndroidWebFetchTool` reuses this same `search.timeout` — there is no separate fetch timeout.
- **CAPTCHA/bot-block detection**: `_looks_like_captcha()` matches known Bing/Google/DuckDuckGo block-page markers and raises a clear error instead of returning garbage. The search engine is hardcoded to Bing (`search_engine != "bing"` raises `ValueError`); adding another engine requires new JS selectors in the Kotlin bridge.
- **Debug commands**:
  ```bash
  adb shell pidof com.flagdizero.jenny
  adb logcat -d --pid=$(adb shell pidof com.flagdizero.jenny) \
    | grep -iE "AgenticSearchBridge|_bridge|searchBing|fetchUrl|timeout|error"
  ```

## Static asset manifests (templates / skills / UI)

`sync_workspace_templates` extracts bundled files into the workspace using **hardcoded
manifests** in `jenny/utils/android_assets.py` (`_TEMPLATES_MANIFEST`, `_SKILLS_MANIFEST`,
`_UI_MANIFEST`). A new file under `jenny/templates/`, `jenny/skills/` or
`jenny/templates/ui/` that is not listed there **silently never reaches the device** —
and for UI assets the SPA fallback in `_serve_static` masks the failure by returning
`index.html` with a 200 for the missing path. When adding bundled files, add them to the
matching manifest and verify by checking the extracted file's *content*, not the HTTP status.

## Native JS dialogs do not work in the app WebView

`confirm()`, `prompt()` and `alert()` **never appear** in Jenny's WebView and resolve as
if the user had dismissed them — `confirm()` returns `false`, `prompt()` returns `null`.
The `WebChromeClient` in `MainActivity.loadWebView` only implements `onShowFileChooser`,
and nothing handles the JS-dialog callbacks.

The failure mode is the worst kind: the guarded action simply never runs. No dialog, no
request, no error, no log. Three features shipped broken this way — deleting a provider
from Settings, renaming a workspace entry, creating a file or folder — plus four error
messages that went nowhere. Note it works fine in a desktop browser pointed at the
gateway, so it survives any testing that is not done on the device.

Use the helpers in `jenny/templates/ui/assets/shared/dialog.js` — `confirmDialog()` and
`promptDialog()`, both `async`, with their markup already in `index.html` — and
`showToast(msg, 'error')` for failures. Grep before adding a new one:

```bash
grep -rn --include="*.js" -E "(^|[^.[:alnum:]_])(confirm|alert|prompt)\(" \
  jenny/templates/ui/assets/ | grep -vE "confirmDialog|promptDialog"
```

## Alzare un default nello schema non raggiunge un'installazione esistente

`config/loader.py` serializza con `model_dump(by_alias=True)` e **senza**
`exclude_defaults`, quindi il primo salvataggio di `config.json` — per qualunque
motivo, anche uno scritto da un'altra parte dello schema — **congela nel file il
valore di default di ogni knob esistente in quel momento**. Da lì in poi il file
vince sulla riga Python, per sempre: alzare il default nello schema non arriva
più su quel device.

Vale per **ogni** knob mai aggiunto a questo schema, non per quelli su cui è
capitato di accorgersene. Due volte finora:

- `AGENTS.md` fermo alla 0.3.0 sul Titan 2 — stessa forma, un layer sopra.
- `agents.defaults.dream.memoryBudgetChars: 0` congelato nel `config.json` del
  device. Quando il default Python è passato da 0 a 2000 (2026-08-16) il device
  è rimasto a 0, e l'unica via è il campo del tetto in Impostazioni → Memoria
  (allora era `/dream budget memory 2000`, prima che quelle manopole si
  spostassero — 31/08/2026).

Conseguenza pratica: **se un valore deve raggiungere un'installazione già viva,
serve una superficie che lo scriva** — un comando, una route, un
`_migrate_by_version` — non un default più alto. Quando ne aggiungi uno, decidi
subito quale delle due cose stai facendo; il default nuovo serve solo alle
installazioni nuove.

## La config che l'agente vede non è quella su disco

`store.mutate()` scrive `config.json`; **non** aggiorna l'oggetto `Config` che il
gateway ha caricato all'avvio. Sono due cose diverse, e chi legge la prima
credendo di leggere la seconda ottiene un bug che nessun test coglie perché in
un test l'oggetto e il file coincidono.

Chi legge la config *fresca* a ogni chiamata:

- il corpo dei tool SSH (`ssh_transport.resolve_target` fa `load_config()`);
- `SubagentManager._live_tools_config()`, tramite il `tools_config_provider`
  iniettato da `AgentLoop` — è ciò che decide **quali tool esistono** per il
  prossimo subagent.

Chi legge ancora la copia dell'avvio, e per cui serve un riavvio:

- **il registry dell'agente principale**, costruito una volta in
  `AgentLoop._register_default_tools()` (loop.py). Accendere `cron` o la ricerca
  web dalle impostazioni non li fa comparire finché il gateway non riparte.

Il sintomo è muto per costruzione: il tool semplicemente non c'è, il modello dice
"non era disponibile", e sembra una scusa. Se stai indagando un tool che "non
esiste" ma in `config.json` risulta acceso, guarda **quando** è stato acceso
rispetto all'avvio del processo prima di guardare altro.

Storia: un host SSH aggiunto alle 13:18 su un'app avviata alle 13:12 non è
arrivato al subagent `sysadmin` lanciato alle 13:32. Vedi
`tests/agent/test_subagent_config_freshness.py`.

## Il tasto Indietro chiude un `<dialog>` senza passare dal tuo `close()`

Ogni foglio della SPA è un `<dialog>` aperto con `showModal()`, e il tasto
Indietro di Android lo congeda da sé: nessun listener nostro viene chiamato,
perché il browser emette `cancel` e poi `close`, non un click sul pulsante
Annulla. Quindi tutto ciò che il tuo `close()` faceva **oltre** a `sheet.close()`
semplicemente non succede quando l'utente esce da lì.

Misurato il 13/09/2026 sul foglio "Seleziona testo": la pulizia della selezione
stava dentro `close()`, così uscire con Indietro lasciava la selezione viva —
barra di selezione di sistema appesa sopra la chat, e un `hasSelection()`
perennemente vero, che è esattamente la condizione che congela il rendering
dello streaming e l'autoscroll (`_flushRender`, `scrollToBottom`).

La regola: la pulizia va su `sheet.onclose`, che scatta da qualunque strada
arrivi la chiusura (pulsante, backdrop, Indietro, `close()` programmatico). Il
`close()` resta solo `sheet.close()`.

## Il testo selezionabile non vive mai in uno scroller interno

Al tocco di un manico di selezione Chromium ri-deriva l'estremo *fermo* con un
hit-test dalle sue coordinate di schermo (`TouchSelectionController::OnDragBegin`
→ `SelectBetweenCoordinates`). Quel hit-test porta `kIgnoreClipping`, che
ignora **solo** il ritaglio del viewport: il testo scrollato fuori da un
`overflow: auto` interno è irraggiungibile, e la base finisce su quello che
occupa quel punto — il composer, il dock, il titolo di un `<dialog>`. Se il
nodo colpito è `user-select: none`, la posizione è nulla e la selezione
collassa. Misurato il 13/09/2026 con tre pagine di prova in Chrome sul Titan 2
([`selection-rig/`](./selection-rig/)); il ragionamento completo sta in
[`chat-selection-root-plan.md`](./chat-selection-root-plan.md).

Conseguenze da rispettare:

- **in chat scorre il documento** (`:root.mode-chat` in `mobile-style.css`),
  e nessun antenato di `.chat-content` può ritagliare; il composer e il dock
  stanno fermi con `position: sticky`, non con uno scroller attorno alla chat;
- **finché c'è una selezione la chrome fissa è `pointer-events: none`**
  (`:root.has-selection`, classe messa da `shared/selection.js`): altrimenti
  vince lei nel hit-test. Il tap che così finirebbe sotto lo riconsegna
  `forwardTapsThroughChrome()`;
- **niente scrittura della selezione da JS**: `setBaseAndExtent`,
  `addRange`, `selectAllChildren` mettono `is_handle_visible=false` in Blink,
  i manici scompaiono e la barra di sistema viene congedata. Un "rimedio" che
  riscrive la selezione è sempre peggio del difetto;
- un nuovo foglio o pannello con testo selezionabile **e** uno scroller
  proprio riporta il difetto dentro di sé. `.chat-thinking-body`, i pannelli
  `.sa-*` e i `pre` con scroll orizzontale sono il confine dichiarato.

## La WebView principale si ispeziona solo in una build di debug

`setWebContentsDebuggingEnabled` vale per tutto il processo e scatta solo se
l'APK è debuggable (`FLAG_DEBUGGABLE`), la prima volta che il Python costruisce
`AgenticSearchBridge` (v. «Remote debugging» sopra): da lì, in una build di
debug, anche la WebView della WebUI compare in `chrome://inspect`. In una
release non c'è mai, e `console.log` della WebUI non arriva a logcat perché
`MainActivity` non implementa `onConsoleMessage` (lo fa solo `HiddenWebView`,
per la WebView nascosta). Per misurare *dentro* la pagina su una release il canale è un
overlay `position: fixed` scritto dal codice sotto misura (lo screenshot è il
log), oppure il JS vero dell'APK nel browser del Mac via `adb forward` e
`#bs=<token>` (v. la memoria di lavoro). Per un difetto del *motore* conviene
invece Chrome sul telefono con una pagina di prova: stessa
`TouchSelectionController`, trenta righe, nessuna build.

## Una chiamata LLM nuova deve dire a quale conversazione appartiene

`providers/opencode.py` tiene una ContextVar con la conversazione in corso, e il
provider la legge per firmare la richiesta con `x-opencode-session` quando il
base URL è OpenCode. Chi apre lo scope sono i **due** percorsi che chiamano il
provider: `AgentRunner.run` (il turno, e con lui cron, Dream e heartbeat, che
arrivano lì col loro `session_key_override`) e `Consolidator.archive`. Erano
tre finché l'umore della mascotte lo chiedeva al modello (`classify_mood`):
oggi si legge dagli emoji della risposta (`session/mascot_mood.py`), senza
nessuna richiesta.

Una terza chiamata LLM aggiunta altrove deve aprire il suo
`conversation_scope(session_key)`, altrimenti cade sul ripiego per-istanza. Il
sintomo non è un errore: è prompt caching mancato — cioè niente, finché qualcuno
non guarda il conto. È lo stesso difetto su cui questa integrazione si è rotta
negli altri client, sempre sulle chiamate ausiliarie fuori dal turno.

## Un metodo JS ritagliato per nome non porta con sé le costanti del suo file

La WebUI non ha un runner con DOM, quindi una manciata di test estrae il **testo**
di un metodo da `mobile-chat.js` (o da un altro asset) con una regex e lo esegue
in node dentro un finto minimo — `test_chat_switch_race_client.py`,
`test_history_load_failure_client.py`, `test_chat_scroller_client.py` e altri.

Il ritaglio è il solo corpo del metodo: **tutto ciò che il file gli metteva
intorno non c'è**. Una `const` di modulo referenziata da dentro diventa un
`ReferenceError` in node — e se il metodo ha un `try/catch` attorno (come
`loadInitialHistory`, che su fallimento dipinge una riga d'errore), l'eccezione
viene ingoiata e il test fallisce su un'asserzione lontana, del tipo «nessuna
fetch in volo». Successo il 18/09/2026: dieci minuti per capire che il difetto
era una costante nuova, non la modifica vera.

Regola pratica: prima di dare un nome a un numero dentro un metodo, `grep` per
il nome del metodo in `tests/webui/`. Se qualcuno lo ritaglia, il letterale resta
(con un commento che dice perché), oppure la costante va iniettata nel banco.
La stessa cosa vale per gli import: i banchi non li risolvono.

Nota che questi ritagli **falliscono rumorosamente** quando un metodo si sposta
(`assert m, f"{name} non trovato"`): spostare del codice fa diventare rossi i
loro test, non verdi a vuoto. È il contrario della trappola descritta in
[`local-build-and-test-env`] sui confronti a zero elementi — qui il rosso è il
comportamento giusto, e va risolto ripuntando il banco, non allentando la regex.
