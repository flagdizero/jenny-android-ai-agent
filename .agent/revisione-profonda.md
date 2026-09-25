# Revisione profonda di `feat/la-casa` (25/09/2026)

Stato: **fase 1 fatta** (25/09/2026): le voci alte, medie e basse sono corrette in 104
commit su `feat/la-casa` (`09f43fc..0f4fab9`, più `48d2e18`), suite verde su 3.14 (11.284)
e 3.11 (11.274). Fase 2 (rinomino in inglese) in corso su `rename/english`. Esiti e
prove sul telefono in fondo, «Avanzamento».

Stato iniziale: **registro**, nessuna voce ancora corretta. Otto revisioni parallele in sola
lettura sul diff `198da6c...HEAD` (222 commit, ~48.000 righe), divise per fetta: core
Python dell'agente, resto del Python, casa JS, officina/shared JS, CSS, Android, test,
convenzioni+documenti. Ogni revisore doveva **dimostrare** ogni voce (codice, comando,
mutazione); le voci marcate ✔ sono state riverificate a mano dopo. Le voci senza ✔ sono
verificate dal revisore ma non ancora da una seconda lettura.

Perché esiste: le due revisioni precedenti leggevano un campione (gli ultimi ~60 commit)
e ogni giro ne trovava altri. Questa copre tutto il ramo.

## Decisioni (25/09/2026, dall'utente)

- **D1 → sì, alla fine**: rebase con firma e un solo force-push come ultimo passo, dopo
  tutte le correzioni e prima della PR.
- **D2 → rinominare tutto** in inglese, persistiti compresi (con migrazione del
  `config.json`), in un passaggio unico dopo le correzioni.
- **D3 → Jenny sempre sopra**, anche a mini-app e immagini, nella casa e nell'officina.
- **D4 → comando RPC** `casa.schermate.set` (poi rinominato con D2); la GET di scrittura
  sparisce, quella di lettura resta.

## Piano di esecuzione

1. **Correzioni per fetta**, in parallelo, ognuna in un worktree isolato su file che non si
   sovrappongono: (F1) Android + `tools/browser.py`; (F2) Python; (F3) casa JS; (F4)
   officina/shared JS; (F5) CSS; (F6) test e documenti. Ogni correzione con il suo test e
   un commit firmato; i log nuovi in inglese li porta ogni fetta nei suoi file. Poi unione,
   conflitti, suite.
2. **Rinomino in inglese** (D2), dopo l'unione.
3. **Verifica**: suite 3.14 e 3.11, build, installazione, prove sul telefono (H4 profilo,
   H3 bottone Ferma, D3 Jenny sopra la mini-app).
4. **DCO** (D1): rebase `--signoff`, force-push.

## Le decisioni come erano state poste

| # | Decisione | Perché ora |
|---|-----------|------------|
| D1 | **DCO**: 87 commit su 222 senza `Signed-off-by` corretto (67 senza, 20 con indirizzo diverso). Il job `dco` blocca il merge. La correzione è `git rebase --signoff` + force-push di `feat/la-casa`. | Riscrive la storia già su `origin`. ✔ (`scripts/check_dco.sh 198da6c HEAD` → 87 FAIL) |
| D2 | **Identificatori italiani** (AGENTS.md: «Identifiers … English»). Nel Python della base erano 0 su 5.048; il ramo ne porta 22, e nel front-end 430 dei 649 nuovi. Circa 20 sono **persistiti o esposti**: `config.json` (`casa.schermate`, `casa.ordine`, valori `conversazione`/`quaderni`/`impostazioni`), rotte `/api/casa/schermate*` (campi `fisse`, `specie`), il protocollo `postMessage` `jenny:gesto` (`fase`/`verso`/`conferma`) esposto alle app. Rinominare **o** scrivere in AGENTS.md un'eccezione per la WebUI. | I persistiti costano una migrazione: meno prima del merge che dopo. |
| D3 | **Jenny sopra la mini-app?** Nella casa lo sprite ha `z-index: 5` e `.app-frame-overlay`/`.image-lightbox` (da `mobile-style.css`, caricato anche dalla casa) la coprono — misurato con `elementFromPoint`. La regola del progetto (e `casa-style.css:1092`) dice «sopra a tutto»; un commento dell'officina (`mobile-style.css:2840`) chiama invece «difetto» Jenny sopra la mini-app. | Le due intenzioni si contraddicono: va scelta una. |
| D4 | **Le pagine della casa si salvano con una GET** che scrive `config.json` col JSON nella query (`/api/casa/schermate/set`). `design.md`: «/api/ is for reads and short scalar parameters». La docstring dice che non si può fare altrimenti, ma l'RPC WebSocket esiste e `project.rename` lo usa. Spostarlo su un comando `casa.schermate.set`. | Cambia un'API del ramo; meglio prima del merge. |

## Alte

| id | Dove | Cosa | Verifica |
|----|------|------|----------|
| H1 | `tests/webui/test_graph_search_contract.py:121` | Importa `mobile-graph.js`, cancellato in `0116b1f`. In locale si salta (manca jsdom), in CI jsdom c'è: **la prima PR è rossa**. Il commento in `ci.yml:76-80` è falso. | ✔ file assente |
| H2 | `mobile-app.js:479-480, 663` | In **officina** Indietro/Home non chiudono una mini-app e i broadcast dei pacchetti si perdono: `this.controllers.apps` non esiste (nessuna factory `apps`); servono `_appsActions`/`_appsSource`. `test_back_navigation_contract.py:207` fissa la riga rotta. Preesistente al ramo. | ✔ |
| H3 | `casa-app.js:1418-1473` | La casa legge `goal_status` e l'attività **senza filtrare per `chat_id`**: un turno di un'altra conversazione spegne il bottone Ferma o lo accende su una chat ferma (e Ferma manda `/stop` al quaderno). La chat filtra (`_belongsHere`), il guscio no. | ✔ |
| H4 | `JennyBrowserBridge.kt:285` | Il profilo «incognito» del browser dell'agente **non si cancella**: `deleteProfile` è `@UiThread` e viene chiamato dal thread Python; il `catch` muto inghiotte l'errore. Cookie e login sopravvivono a `browser_close` e ai riavvii. Da confermare sul telefono con un log. | ✔ codice; device da provare |
| H5 | `mobile-chat.js:1193` + `mobile-workspace.js:241` | Regressione del ramo: aprire un file da un link in chat, con il workspace fresco, fa navigare `activate()` in modo sincrono dentro `switchMode`: la history perde la voce chat e `AppState` dice `workspace` mentre si è su Memoria. | revisore (harness node) |
| H6 | `tests/webui/test_no_raw_view_lookup_contract.py:51` + `mobile-ui-query.js:77` | `getElementById('view-' + view)` sfugge alla regex del test: nei cassetti `cervello/mani/memoria` lo strumento `ui_view` manda a Jenny un HTML vuoto. Difetto vivo. | ✔ |

## Medie — correttezza

| id | Dove | Cosa | Verifica |
|----|------|------|----------|
| M1 | `agent/loop.py::busy_session_keys` | Manca un quarto scrittore: la raccolta del diario dell'autocompact (`autocompact.py:386-398`) rilegge e **salva** la sessione di progetto dopo la chiamata LLM. Un rinomino in quella finestra lascia una chat sotto il nome vecchio. | ✔ |
| M2 | `webui/commands.py::project_delete` | Nessuna guardia sugli scrittori in volo (il rinomino ce l'ha): cancellare mentre Jenny scrive lascia l'orfano. | revisore |
| M3 | `casa-app.js:1109-1219` | `goHome`/`openChat` chiudono uno strato con la logica di Indietro: una mini-app con navigazione interna resta aperta, e `isChatOnScreen` fa cancellare avvisi non visti. | revisore |
| M4 | `casa-audit.js:76-79` | `.replace('{quote}', quote)` interpreta `$$`, `$'`, `$&`: una formula citata in una segnalazione arriva storpiata a Jenny. | ✔ (node) |
| M5 | `casa-map.js:504-545` | Qualunque errore di lettura degli spilli si mette in cache come `{}`; il salvataggio dopo **cancella le disposizioni di tutti i quaderni**. | ✔ |
| M6 | `casa-app.js:705-715` | `_portaInChat`: se l'invio fallisce, la bozza ripristinata sovrascrive il messaggio della segnalazione. | revisore |
| M7 | `casa-pagine.js:524-544` | Pagina di un quaderno cancellato: con la tastiera fisica il fuoco va sul campo sotto l'avviso e si scrive a `project:<cancellato>`. | revisore |
| M8 | `casa-app.js:536-561` | Rinomina/cancella dalla pagina Quaderni dirotta la pagina chat e perde la bozza. | revisore |
| M9 | `casa-pagine.js:174-183` | Errori di `salvaPagine` non gestiti: «Fatto» in ordina perde l'ordine in silenzio, «Metti/Togli pagina» senza avviso. | revisore |
| M10 | `casa-app.js:218`, `index.html:65` | Il nome della conversazione personale è il testo fisso «Jenny»: `bot_name` ignorato in fila e Quaderni. (+ cache `_settings` col nome vecchio.) | revisore |
| M11 | `casa-app.js:1247` | `onPackageChanged() {}` vuoto: un'app installata dal Play Store non compare nella pagina App fino al riavvio (Jenny è il launcher). | revisore |
| M12 | `AgenticSearchBridge.kt:174-227` | `handler.post` senza `try`: il costruttore della WebView che solleva (aggiornamento del provider) abbatte il processo, gateway compreso. Stesso caso già chiuso nel browser. | revisore |
| M13 | `JennyBrowserBridge.kt:365` | `open()` azzera il recinto all'inizio; i ritorni d'errore lo lasciano `null` con la pagina precedente ancora caricata. | revisore |
| M14 | `tools/browser.py` | `destroy_browser()` (che blocca fino a 10 s) gira sul thread del loop asyncio; `BrowserCloseTool` senza `_BROWSER_LOCK`. | revisore |
| M15 | `mobile-settings.js:252`, `mobile-workspace.js:288` | Indietro «mangiato» in Cervello/Mani: risale le cartelle invisibili di Memoria. | revisore |
| M16 | `mobile-settings.js:1019-1036, 1248` | Regressione `013f93c`: dopo un salvataggio la scheda «Quanto ricorda» mostra la misura due volte e perde «restano N». | revisore |
| M17 | `mobile-settings.js:1473-1620, 2778` | Pannelli SSH/marca: lo stato «occupato» cerca il bottone fuori dal pannello; dopo «Genera chiave»/«Elimina» il pannello resta sull'oggetto vecchio. | revisore |
| M18 | `mobile-settings.js:879-883, 1390` | Etichette sbagliate: «(nessuna chiave) sk-…» e «impronta» davanti a `utente@host:22`. | revisore |
| M19 | `shared/gesto-orizzontale.js:254` | Un secondo dito durante uno scorrimento laterale lascia vista/pista a metà (niente `onAnnulla`). | revisore (node) |
| M20 | `casa-style.css:1487` | Nodi della mappa in cartelle non previste: nessun `fill`, neri/invisibili nel tema scuro. | revisore |
| M21 | `webui/commands.py` (`page_write`) | Salvare una pagina converte in silenzio tutto il file da CRLF a LF. | revisore |

## Medie — test che non provano

Ogni voce è stata dimostrata con una mutazione in una copia: il codice rotto, il test verde.

| id | Test | Cosa lascia passare |
|----|------|---------------------|
| T1 | `test_casa_pista_client.py:816` | Cornice di un'app costruita prima del segreto (`token=undefined`). |
| T2 | `test_officina_file_client.py:393` | Risposta vecchia che sovrascrive una nuova (le due arrivano in ordine). |
| T3 | `test_ws_events_have_listeners_contract.py:75` | Gestori di `'user'`/`'error'` spariti (la stringa compare comunque altrove). |
| T4 | `test_casa_switch_client.py:980` | Home che cambia la chat di un quaderno (esercita solo `switchConversation`). |
| T5 | `test_chat_focus_no_scroll_contract.py:78` | `preventScroll: false`. |
| T6 | `test_setup_wizard_contract.py:227` | Uscita dal wizard senza provider. |
| T7 | `test_rich_content_client.py:192` | KaTeX e auto-render caricati in parallelo. |
| T8 | `test_casa_trasloco_client.py:345` | Lettura vecchia che finisce dopo una nuova. |
| T9 | `test_is_path_within.py:53` | `assert result in (True, False)`: accetta tutto. |
| T10 | **Ordine della suite** | Invertita: 31 rossi; mescolata: 17. Stato globale non ripristinato (`test_providers_init` su `sys.modules`, `_WEBSOCKET_TURN_WALL_STARTED_AT`, `RuntimeContext.cron_recovered_from`). Preesistente, latente. |

## Medie — struttura, convenzioni, documenti

| id | Dove | Cosa |
|----|------|------|
| Q1 | `webui/commands.py:440,496` | Il modulo dei comandi (che «di trasporti non sa niente») importa da `casa_routes` (rotte HTTP); l'involucro «stacca la pagina» è duplicato con `apps_routes._stacca_la_pagina`. |
| Q2 | `casa-pages.js` vs `casa-pagine.js` | Due classi che differiscono per il plurale in due lingue; lo stesso concetto si chiama `schermata`/`pagine`/`page`. (Si lega a D2.) |
| Q3 | `docs/reference/websocket.md:308,319` | `conflict` documentato come «file cambiato sotto», usato anche per «Jenny sta lavorando». Pagina pubblica. |
| Q4 | `casa-app.js:532` | `renameFailed` mette nel toast il messaggio inglese del server («a folder named … already exists»). |
| Q5 | `docs/using/webui-tour.md` | Descrive l'officina come l'intera interfaccia (e «cinque icone», sono quattro); la casa non c'è. Pagina pubblica. |
| Q6 | log | 5 log Python e 7 `console.*` nuovi in italiano (`project_rename.py`, `browser.py:248`, `casa-*.js`, `rich-content.js`); i messaggi di `ValueError` dello schema, in italiano, finiscono nei log e nei 400. |

## Basse (in blocco)

- **Android**: `evaluate`/`act` senza cancello (click tardivo); commento del cancello falso se `loadUrl` solleva dopo il CAS; WebView di ricerca senza filtro SSRF sulle sottorisorse; DNS rebinding sulla cache dei verdetti (da scrivere in `security.md`); `isIsolated()` KDoc falsa; KDoc orfane in `FloatingOverlayController`; `MainActivity.chatOpened` KDoc; `.agent/gotchas.md:135,281` sul debugging.
- **Python**: `every_seconds` negativo accettato (job muto); `RuntimeError` su loop di symlink in 3.11 non catturato (`wiki_provenance`, `wiki_routes:446`, `wiki.py:567`, `ws_http:760`, `media_api`, `apps_routes:277`); `DreamTurnResult.resp` morto e `advanced` ridondante; `_silent` in quattro copie; `_session_isolated` prende il lock globale per una costante; commento di `apply_patch` falso sul conteggio righe; commenti orfani in `memory_entries`/`self.py` (`MyTool`); `IntegerSchema` accetta float; `SchermataConfig` non valida `ref`/`id` delle app; commento su `mascotMoodModelPreset` falso (è ritirata); docstring di `WikiRoutes` falsa; `fetch.py` `**extra` (4 errori pyright); `_handle_error` di OpenAI rilegge il payload a mano; mixin dei canali contro `design.md`; messaggio d'errore di `settings_api` scritto a mano; righe vuote (ruff preview E3); `casa_routes.py:187` commento `noqa` falso.
- **Casa JS**: titolo della pagina scritto dopo Indietro; doppio tocco su Salva/Invia; `offsetsIn` controllato solo all'invio; commenti falsi in `casa-map.js` e `index.html`; listener del trascinamento della fila mai tolti; `scrollTop` dei Quaderni perso a ogni ridisegno; pagine fuori schermo non `inert`; doppia `CasaMap`; id dei fogli scritti due volte; codice morto (`_pagina`, `_staccaGesto`, `fotoIn`, `MAX_COMMENT`); fila disegnata prima di `i18n.load`; `querySelector('.casa-composer')` prende la foto del trasloco; Home con editor modificato cambia conversazione sotto la conferma.
- **Officina JS**: SDK delle app che si prende lo scorrimento anche nel velo; riepilogo Telegram non riletto; colore delle marche duplicato con `provider-brand.js`; stringhe lette prima di `i18n.load` (header, minichat); commenti orfani e riferimenti a cose rimosse; codice morto dei modi rimossi; chiavi `localStorage` ritirate non in `DEAD_KEYS`; skill `locked` etichettata «Viene con l'app».
- **CSS**: regole morte (`.update-check`, `.settings-field[data-settings-off]`, `.model-list`, `.ws-item.selected`, bordo di `cron-card-*`, `.casa-pages-open` sovrascritta); commenti falsi su `[hidden]` e rimandi rotti; `.ibtn:active` perso con `0116b1f`; gemelle ancora separate; `.chat-history-more` duplicata; `--danger`/`--danger-bg`/`--code-bg` mai definite; `!important` evitabili; `@media (max-height: 500px)` nasconde Jenny anche nella casa; font scritti a mano (il tema Fumetto non tocca la casa).
- **Test**: contratti Kotlin che cercano sottostringhe anche nei commenti; `test_wire_ids.py:27` senza asserzioni; parità dei parser SSE tautologica; mock sul punto sbagliato in `test_webui_turns.py:242`; `_settle` nuovo che scade senza fallire; campioni di croniter dipendenti dalla versione di tzdata; duplicazioni residue (`make_request` ×7, `make_handler` ×5, `_drain`, `_make_loop`); `test_casa_trasloco_client` lento per un timer non cancellato.
- **Documenti e privacy**: `AGENTS.md` «two channels» (sono quattro) e `memory_entry` (si chiama `memory`); `configuration.md` §casa esempio contraddittorio; rimandi a «tavole» di design che non stanno nel repo; commit `76b707b` col soggetto in italiano; **seriali dei dispositivi e contenuti reali del workspace in alcuni piani `.agent/`** (repo pubblico).

## Avanzamento

**Fase 1 (25/09/2026).** Sei fette corrette in parallelo in worktree isolati, unite senza
conflitti testuali con `cherry-pick` sopra `09f43fc` (la correzione dello scorrimento
orizzontale della chat, di un'altra sessione): F1 Android + browser (7 commit), F2 Python
(21), F3 casa JS (23), F4 officina/shared JS (22), F5 CSS (11), F6 test e documenti (17),
più quattro commit d'integrazione: un solo helper per leggere il Kotlin nei contratti
(`tests/support/kotlin_source.py`; `kotlin.py` tolto), la cancellazione rifiutata
per «Jenny sta lavorando» detta con una frase sua, i residui fuori fetta (commento
orfano in `api-client.js`, un nome reale di quaderno e gli orari reali dei job del
telefono tolti dai commenti, docstring dei canali, righe jsdom inutili in CI), e il test
del tetto della chat che distingue `max-width: 100%` (la colonna) da un tetto.
Voce non corretta per scelta: il mixin dei canali contro `design.md`. Voci rimaste da
decidere: filtro SSRF sulle sottorisorse della WebView di ricerca e DNS rebinding sulla
cache dei verdetti del browser.

Prove sul telefono (build `48d2e18`, installata alle 19:51):
- **H4 confermato e chiuso.** Prima: il profilo `jenny-browser-session` esisteva dal 29/08
  con **95 cookie di 21 domini**. Alla prima apertura dopo la build: «Discarded browser
  profile left by a previous process», la cartella vecchia sparisce e ne nasce una nuova;
  alla chiusura la sessione si svuota e il rifiuto atteso di `deleteProfile` («Cannot
  delete in-use profile») finisce nel log invece che in un `catch` muto. Resta su disco
  un profilo `probe-incognito` di una sonda del 29/08, che nessuno usa.
- **D3 non osservabile sul telefono**: lì la mascotte in-app è spenta e si vede la
  flottante (Kotlin). La misura è quella della fetta CSS in Chrome headless
  (`elementFromPoint`, 590×566).
- **H3** (bottone Ferma) coperto dai test comportamentali nuovi, non messo in scena sul
  telefono: servono due conversazioni in volo insieme.

Da notare: nel turno principale Jenny dice che `browser_open`/`browser_close` non sono nel
suo registro e li usa attraverso un subagent. Non è in questo registro: da capire se è
voluto.

**Fase 2, D2 eseguita (25/09/2026)**, ramo `rename/english` da `48d2e18`: 11 commit per area; 2.331 nomi della mappa (2.269 identificatori più 61 chiavi i18n), 66 file rinominati (20 moduli della casa e `casa-style.css` → `home-*`, `officina.html` → `workshop.html`, `gesto-orizzontale.js` → `horizontal-swipe.js`, 2 moduli Python, 42 test); persistiti con migrazione (`casa` → `home` nel `config.json`, config v3; chiavi `jenny-mascotte-*`, temi `fumetto`/`pietra`, cassetti in `mobile-last-mode`) e rotta/RPC/`postMessage` in inglese. Residuo misurato col dizionario di `conventions/`: 0 identificatori italiani in JS, CSS, HTML e Kotlin (a `48d2e18` erano 383, 209 e 72, di cui 76, 136 e 53 già prima del ramo); in Python 3 su 5.112 (erano 23), apposta (i nomi della migrazione: `_migrate_casa_to_home`, `_CASA_KINDS`, `_CASA_FIXED_IDS`).
Dopo l'unione, un dodicesimo commit (`9114bab`): 19 prefissi dei `console.warn` della casa
(`'casa.pages:'`, `'casa:'`…) che il dizionario non vedeva perché sono testo di log.
Su `feat/la-casa` a `9114bab`: 11.293 passati e 31 saltati con 3.14, 11.283 e 33 con 3.11,
ruff pulito, pyright bloccante a 0 errori, Kotlin compila.

**Fase 3 (25/09/2026), build `9114bab` installata alle 23:20.** La migrazione del file vero:
prima `configVersion` 2 con `casa: {schermate: [un'app, todo], ordine: [app, chat, <id>,
quaderni, impostazioni]}`; dopo il primo avvio `configVersion` 3, `home: {pages: [la stessa
riga], order: [app, chat, <id>, notebooks, settings]}`, `casa` sparito, e nessun'altra chiave
del file cambiata (confronto chiave per chiave). Nessun `config.corrupt-*`. Nel log le due righe
attese: «Config migration v3» e «Config schema stamped at version 3». `GET /api/home/pages`
risponde coi campi nuovi, `/api/casa/schermate` è 404. Sullo schermo: la fila in alto con le
cinque pagine nell'ordine di prima, il tema scelto rimasto (Kyoto, che non cambiava nome),
Impostazioni → Workshop apre `workshop.html` (la `MainActivity` lo lascia passare) e la pillola
«Jenny» riporta alla casa. Nessun errore JS o Python nel log dopo l'installazione.
