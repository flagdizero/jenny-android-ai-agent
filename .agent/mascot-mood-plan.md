# La mascotte che sente — il piano

> **Superato in parte dal 08/09/2026** da
> [`mascot-faces-plan.md`](./mascot-faces-plan.md), che porta l'arte a due
> livelli (corpo + faccia). Non valgono più: **D7** (le etichette: `worried` e
> `surprised` sono uscite, è entrata `angry`), **D13** (l'arte non arriva più
> per ultima e non c'è nessuna mappa provvisoria) e tutta la sezione
> **Standby** (`mascotMood` è tornato `true`, `MOOD_STANDBY` non esiste più).
> Il resto — il sidecar, il costo, il frame, la scelta del modello — è ancora
> il documento di riferimento.

La mascotte (`JennyCompanion`) oggi ha tre stati, tutti *fisiologici*: `idle`,
`thinking`, `talking`. Sa se Jenny sta lavorando, non sa *come* Jenny sta. Questo
piano le dà un umore — felice, triste, preoccupata, sorpresa — con due vincoli
che decidono tutta la forma: **il prompt dell'agente principale non cambia di un
byte**, e **il costo per turno resta dell'ordine di una riga di testo, non di una
conversazione**.

Nello stesso lavoro si toglie il generatore di titoli della chat: un residuo
dell'upstream multi-sessione che oggi spende una chiamata al modello per scrivere
un titolo che nessuno legge, ed è l'unico codice che già fa la cosa di cui l'umore
ha bisogno — una chiamata piccola, in background, dopo la fine del turno.

---

## Il rilievo (05/09/2026)

Misurato sul codice, non dedotto.

**La mascotte.** [`mobile-jenny.js`](../jenny/templates/ui/assets/mobile-jenny.js):
`_setAgentState(idle|thinking|talking)` → `_syncArt()` → `_setArt(key)` con
`ART = {idle, side, think, sideTalk}`, più `TALK_ANIMS` (parlato animato) e
`FLY_POSES` (volo Pegman). Lo stato è pilotato **solo dal tipo dei frame WebSocket**
(`delta`, `stream_end`, `message`, `reasoning_delta`, `goal_status`, `turn_end`,
`error`) in `_handleWsMessage` (fuori dalla chat) e `_handleChatStream` (in chat).
Segue un turno alla volta (`_trackedTurnMatches`), e in chat è sempre `out`
(angolo sopra il composer); nelle altre viste è `side` (metà fuori dal bordo)
finché non la si richiama. Le pose `hello1/hello2` sono esportate ma le usa solo
l'onboarding.

**Il backend non conosce la mascotte.** Zero occorrenze di mascot/mood/umore in
`jenny/**/*.py` fuori da un commento in `delivery.py`. L'unico canale
backend → mascotte è il frame WebSocket, e i frame "di controllo" (senza bolla)
nascono tutti allo stesso modo: un `OutboundMessage` con `content=""` e un flag
nei metadata (`_turn_end`, `_goal_status`, `_session_updated`,
`_runtime_model_updated`…) che [`ws_sender.send`](../jenny/channels/ws_sender.py)
intercetta con un early-return e traduce in un frame dedicato. Il dispatcher li
lascia passare: `_should_suppress_outbound` non tocca un contenuto vuoto
(`fingerprint` falso → `False`), e il canale è scelto da `msg.channel`, quindi
un frame indirizzato a `websocket` non arriva mai a Telegram.

**Il titolo.** [`webui_turns.py`](../jenny/session/webui_turns.py):
`WebuiTurnCoordinator._handle_turn_completed_event` → `handle_turn_end` (emette il
`_turn_end`) → `_schedule_title_update_from_event` → in background
`maybe_generate_webui_title_after_turn` → `provider.chat_with_retry` con il primo
scambio della sessione troncato a 1.000 caratteri per lato, `max_tokens=96`,
`reasoning_effort="none"`, e poi un `OutboundMessage` `_session_updated` con scope
`metadata`. Si ferma appena `session.metadata["title"]` esiste, e `Session.clear()`
(cioè `/new`) non lo toglie: una chiamata per `unified:default` e una per ogni
`project:<id>`. **Nessun lettore**: nessun JS legge `title` dai metadati di
sessione, nessuna route `/api/` lo espone. Non è conteggiato nei token: il
`TokenUsageHook` vive nel runner, e questa è una chiamata diretta al provider.

**Quello che il titolo lascia in eredità e va tenuto.** `TurnCompleted.runtime`
(un `LLMRuntime(provider, model)` fotografato in `_state_build` dal turno stesso,
[`turn_states.py:313`](../jenny/agent/turn_states.py)) esiste *per* il titolo, ed
è esattamente ciò che serve all'umore: il provider e il modello **del turno**,
non quelli del loop dopo un eventuale `/model` a metà. La sessione è già salvata
con la riga assistant quando l'evento parte (`_finalize_turn`: `_save_turn` →
`record_turn_latency` → `sessions.save`, e solo dopo, in `loop.py:1825`,
`turn_completed`). I turni-comando (`/model`, `/new`…) **emettono**
`TurnCompleted` anche loro, ma con `runtime=None`: prendono la scorciatoia in
`COMMAND` e `_state_build`, che è dove il runtime viene fotografato, non gira mai.
Era la guardia del titolo (`isinstance(runtime, LLMRuntime)`), e il passo 1 lo ha
misurato smentendo la prima stesura di questo paragrafo, che li dava per non
emessi (test `test_webui_command_turn_completes_without_runtime`).
`webui_view_target` filtra già i turni silenziosi e interni e proietta quelli
Telegram su `websocket:default`.

**L'arte.** Sorgenti PNG 3000×3000 in `android/image_source/`, due per posa
(line-art e `<stem>_color.PNG`), esportati da `gen_pose_webp.py` in
`jenny/templates/ui/assets/jenny-<name>[-color].webp`. **Ogni webp nuovo va anche
in `_UI_MANIFEST`** ([`android_assets.py`](../jenny/utils/android_assets.py)) o
sul telefono non arriva (404 silenzioso). Vedere le pose sul device richiede una
build dell'APK, non un riavvio.

**I test del client** esistono in due forme, entrambe senza DOM: contratti che
leggono il sorgente con regex (`test_mascot_size_contract.py`) e metodi estratti
dal sorgente ed eseguiti in `node` su un `this` finto
(`test_live_turn_boundary_client.py`, che copre già `_trackedTurnMatches` della
mascotte).

---

## Le decisioni, e perché

**D1 — Il titolo si toglie, non si spegne.** Un flag lascerebbe in piedi 120
righe e 30 test per una funzione senza lettori. Sparisce tutto:
`clean_generated_title`, `_title_inputs`, `maybe_generate_webui_title[_after_turn]`,
`_schedule_title_update_from_event`, le cinque costanti `TITLE_*`/`WEBUI_TITLE_*`
e la notifica `_session_updated` scope `metadata`. Resta `WEBUI_SESSION_METADATA_KEY`
(`mark_webui_session` lo usa per altro) e resta `TurnCompleted.runtime` (v. sopra).
Un `title` già scritto nei file di sessione esistenti è un metadato ignoto e
innocuo: non si migra, non si pulisce.

**D2 — L'umore nasce nel coordinatore, dopo il turno, in background.** Stesso
punto e stessa disciplina del titolo: `_handle_turn_completed_event` → un
`_schedule_mood_from_event` che passa da `schedule_background` (cioè
`agent._schedule_background`, che il drain di spegnimento già aspetta). Fuori dal
percorso critico: la risposta è già consegnata e il `turn_end` già in coda quando
la classificazione parte. Un errore o un timeout della chiamata si loggano a
`debug` e non producono niente — la mascotte resta `idle`, che è il comportamento
di oggi.

**D3 — Quali turni.** Quelli per cui `webui_view_target` restituisce un target:
turni utente WebSocket e turni Telegram proiettati sulla vista (la mascotte
reagisce anche a una conversazione fatta da Telegram, ed è giusto: è la stessa
conversazione). Mai turni interni, silenziosi, comando — e il comando si
riconosce da `event.runtime is None` (v. il rilievo), che è la **prima** guardia
del sidecar, prima ancora di leggere la sessione. **Mai il ramo d'errore**
(`loop.py:1881` emette `TurnCompleted` anche lì): l'errore ha già la sua faccia,
gratis, dal frame `error` (D10). Il coordinatore non ha bisogno di sapere che il
turno è fallito: l'input (D4) richiede che **l'ultima riga** della sessione sia una
assistant non sintetica, e dopo un errore l'ultima riga è la user — si esce senza
chiamare nessuno.

**D4 — L'input è l'ultimo scambio, non il primo.** Il titolo leggeva il *primo*
user/assistant della sessione (descrive la chat); l'umore legge l'**ultimo**
(descrive adesso). Si scorre `session.messages` dalla coda: si saltano `_command`
e `is_synthetic_history_row` (la regola è quella di `_title_inputs`, e va tenuta
identica: un rientro di subagent o uno sprone a un goal non sono "l'utente ha
detto"), si prende la prima assistant e la prima user che la precede,
`strip_think` su entrambe. Troncamento: **300 caratteri la domanda, 600 la
risposta**, tagliando dall'inizio della risposta — la coda è dove sta il tono
("…spero ti sia utile", "…purtroppo non ci sono riuscita"). Sotto **40
caratteri** di risposta non si chiama nessuno: "Ok." non ha un umore che valga
una richiesta.

**D5 — Una lettera, non una parola.** Il prompt chiede una sola lettera fra un
insieme chiuso; si legge il primo carattere `A-E` della risposta, tutto il resto
(vuoto, parola, spiegazione) vale `neutra`. `max_tokens=3` e non 1, perché un
provider può spendere il primo token in uno spazio o in un a-capo; con 3 la
lettera arriva e la divagazione no. `temperature=0`, `reasoning_effort="none"`
(sui modelli Anthropic disattiva il thinking, `anthropic_provider.py:214`; sugli
OpenAI-compatibili spegne il thinking **solo per i modelli con uno stile mappato**
in `openai_compat_helpers.py` — ~~sugli altri è ignorato o equivalente~~ **no:
misurato il 05/09 sul telefono con `deepseek-v4-flash`, che pensa di default e
non era mappato: due richieste, 3 token di uscita ciascuna, contenuto vuoto,
`finish_reason=length`, verdetto neutro sempre, e l'unico testimone era il
bucket dei token. Ora DeepSeek V4 è mappato su `thinking_type` e il sidecar
avverte una volta per modello quando il budget finisce senza lettera), `retry_mode="standard"` come il titolo — è una
chiamata piccola, un retry non fa danno, e la retry policy è del provider.

**D6 — Il prompt è in prima persona, ed è qui che vive il roleplay.** Non "classifica
il sentiment di questo testo" ma: *"Sei Jenny, {bot_name}, l'assistente personale
di questa persona. Hai appena risposto così. Come ti senti?"* seguito dalle
cinque lettere. `bot_name` da `config.agents.defaults.bot_name`. **Niente SOUL.md
né altri file di persona**: costerebbero centinaia di token per turno, e la
domanda non li richiede — il tono della risposta è già la persona. Il system
prompt del sidecar sta sotto le 60 parole; il costo per turno è **150–350 token
in ingresso e 1–3 in uscita**, il grosso dei quali è il troncato della risposta.
(Per confronto: il solo schema di un tool `set_mood` peserebbe di più su *ogni*
richiesta dell'agente, e il tag in coda alla risposta finirebbe in cronologia.)

**D7 — Le etichette v1.** `A` felice → `happy`, `B` triste → `sad`, `C`
preoccupata → `worried`, `D` sorpresa → `surprised`, `E` niente di particolare →
`neutral`. Cinque perché la sesta e la settima costerebbero una posa ciascuna
(D13) e perché la distinzione che conta per il roleplay è positivo / negativo /
allarmato / colpito. **`neutral` non produce frame**: la mascotte resta `idle`,
zero byte sul filo per il caso più frequente.

**D8 — Il frame.** `{"event": "mascot_mood", "chat_id", "mood", "turn_id"}`. Nasce
come `OutboundMessage(channel=<view channel>, chat_id=<view chat>, content="",
metadata={"_mascot_mood": True, "mood": "happy", WEBUI_TURN_METADATA_KEY: <id>,
…ctx.metadata})` e `ws_sender.send` lo intercetta **prima del percorso generico**
(accanto a `_goal_status`), con la stessa disciplina di `goal_status`: solo agli
iscritti della chat, `skip_persist` implicito (mai nel transcript — un reload
riparte da `idle`, l'umore è del momento), nessun retry (`_fanout` e via: il
prossimo turno lo sostituisce). Il `turn_id` è quello del turno classificato,
preso da `ctx.metadata[WEBUI_TURN_METADATA_KEY]`: senza id il client lo accetta
comunque, come fa per gli altri frame (regola di `_trackedTurnMatches`).

**D9 — Il modello.** Due campi in `AgentDefaults` (i preset, invece, stanno su
`Config.model_presets`, non sotto `agents.defaults`: `resolve_mood_model` prende
il `Config` intero):
`mascot_mood: bool = True` (alias `mascotMood`) e
`mascot_mood_model_preset: str | None = None` (alias `mascotMoodModelPreset`).
Con il preset a `None` si usa `event.runtime` (provider e modello del turno);
con un preset si prende **solo il campo `model`** di quel preset, sullo stesso
provider — i preset non cambiano provider a runtime (`loop_provider.py:92`), e
l'umore non deve essere il primo a provarci. Un preset inesistente si logga a
`warning` una volta e si ricade sul runtime. Default acceso: costa quanto una
riga e senza è una feature che non esiste; chi non la vuole ha il flag e, dal
passo 6, l'interruttore in Impostazioni. La lettura va fatta al momento della
chiamata dal config caricato (come `worker_settings_payload`), così un cambio
vale dal turno dopo senza riavvio. *Com'è uscito (05/09):* il coordinatore ha un
campo `config_loader` con default `load_config`, letto **dentro** il task in
background — il gestore dell'evento resta a costo zero e in test si inietta un
lettore che non tocca il disco. Il flag spento quindi non evita la schedulazione
ma la richiesta: è la richiesta che costa.

**D10 — Livello 0: le reazioni che non costano niente, lato client.** Ortogonali
al sidecar e sempre attive, anche a `mascotMood=false`:
- frame `error` → `sad`, subito;
- `thinking` da più di 20 s senza frame → `worried` (si toglie al primo frame utile);
- ~~arrivo di un turno non suo mentre è `idle` → saluto `hello1/hello2`~~
  **Rinviato (05/09).** Nel codice quel caso non esiste: a mascotte ferma il
  primo frame che apre un turno viene *adottato* (`_trackedTurnMatches`) e
  animato come parlato, avviso proattivo compreso. Un saluto *prima* del parlato
  vuole una coda di animazioni che oggi non c'è; l'arte c'è, il posto no.
Non si fa l'euristica lessicale sul testo (emoji, "purtroppo"…): con il sidecar
acceso è ridondante e con il sidecar spento darebbe una faccia sbagliata abbastanza
spesso da rompere l'illusione — meglio nessuna faccia che quella sbagliata.

*Com'è uscito (05/09):* `error` → `sad` in entrambe le viste; il pensa oltre
20 s → `worried` via un timer armato in `_setAgentState('thinking')` e disarmato
da ogni altro stato. Un cambio di stato verso `thinking`/`talking` azzera
l'umore precedente (una risposta neutra non manderebbe niente e la faccia
vecchia riapparirebbe a parlato finito); la preoccupazione nasce dopo
l'azzeramento e sopravvive.

**D11 — L'umore è uno strato sopra lo stato, non un quarto stato.** In
`JennyCompanion`: `this._mood = null`, `this._moodUntil = 0`, `this._moodTurnId`.
`_syncArt` cambia in un punto solo: se non c'è parlato in corso, non è `thinking`,
non sta volando (`_abortFlight` nullo) **ed è `out`**, e c'è un umore vivo, la posa
è `MOOD_ART[mood]`; altrimenti la logica di oggi. Da `side` (metà fuori dal bordo)
l'umore non si mostra — una faccia a metà non si legge, e le pose `side-*`
d'umore non esistono; si conserva e riappare se la si richiama entro il tempo.
**Durata: 12 s** (`MOOD_HOLD_MS`), poi `idle`. Un nuovo invio dell'utente
(`chat:sent`) azzera l'umore: sta ascoltando, non sta ancora reagendo. Un frame
`mascot_mood` che arriva **con un turno in corso** o con `turn_id` diverso
dall'ultimo turno chiuso si scarta: è la reazione a una risposta che non è più
l'ultima. Con `prefers-reduced-motion` non cambia nulla: le pose d'umore sono
statiche.

**D12 — Il conteggio dei token è onesto.** Il titolo non veniva contato; l'umore
sì, con `record_response_token_usage(response, source="mascot")` e `"mascot"`
aggiunto a `_SOURCE_KEYS` in `token_usage.py`. Se il pannello Uso token della
WebUI enumera i bucket da una lista fissa, si aggiunge l'etichetta i18n
(`it`/`en`); se li itera dal payload, basta il bucket. ~~Da verificare al passo
2.~~ **Verificato il 05/09/2026: nessuna delle due.** `token_usage_payload`
espone solo totali (token, richieste, giorni attivi, streak): i bucket per
sorgente stanno nel file di stato e non arrivano al client. Il bucket `mascot`
basta, l'i18n non si tocca; se un giorno il pannello mostrerà le sorgenti, il
dato c'è già.

**D13 — L'arte arriva per ultima, con una mappa provvisoria prima.** Per provare
il meccanismo sul telefono senza disegnare: `happy → hello1`, `surprised → talk1a`
(bocca aperta, mano alzata), `worried → think`, `sad → ground` (a terra, stordita).
Sono approssimazioni dichiarate, in una costante `MOOD_ART` con un commento che
dice quale voce è provvisoria. **Deciso il 05/09/2026: le immagini non sono
pronte e questo lavoro non le prevede** — nessuna chiave nuova in `ART`, nessuna
riga in `FILES` o nel manifest; un contratto in `tests/webui` pretende che ogni
posa presa in prestito esista e sia già nel manifest. Il passo 7 resta scritto
per quando l'arte arriverà, fuori da questo PR. L'arte vera: 4 pose × 2 varianti =
**8 PNG 3000×3000** (`mood_happy.PNG`, `mood_happy_color.PNG`, …), 4 righe in
`FILES`, 8 righe in `_UI_MANIFEST`, 4 chiavi in `ART`, e la tabella in
`COLORARE_LE_POSE.md`. Nessuna variante `side`: v. D11.

**D14 — Cosa NON entra in v1**, scritto per non rilitigarlo:
- umore sugli avvisi proattivi (cron, heartbeat, Dream): il loro turno non ha
  target di vista (`webui_view_target` → `None`) e il saluto di D10 li copre;
- persistenza dell'umore fra reload o fra device;
- il tag in coda alla risposta con strip in `finalize_content`: è il piano B se
  il sidecar alla prova risulta "freddo", e va aperto come piano suo;
- euristica lessicale sul testo (v. D10).

---

## I passi

Ramo: `feat/mascot-mood` da `main`. **Prima di aprirlo, controllare lo stato di
`fix/synthetic-user-rows`** (ramo attivo nel working tree al momento del piano,
di un'altra sessione): non si parte da lì e non si tocca finché non è mergiato.

**Passo 1 — Via il titolo** *(un commit, nessun comportamento nuovo)*
- `jenny/session/webui_turns.py`: rimuovere tutto ciò che elenca D1; verificare
  che `re`, `strip_think`, `truncate_text` restino usati o togliere gli import.
- `tests/session/test_webui_turns.py`: via le sezioni `clean_generated_title`,
  `_title_inputs`, `maybe_generate_webui_title*` e i test del coordinatore sul
  titolo; **tenere** `test_subscribe_registers_and_unsubscribe_removes_all_handlers`
  e i test di proiezione. `test_handle_turn_completed_event_schedules_title_from_event_runtime`
  diventa il test del passo 2.
- `tests/agent/test_loop_save_turn.py`: via i 4 test sul titolo e gli import.
- `tests/agent/test_loop_progress.py`: i due test che monkeypatchano il titolo
  si riscrivono su ciò che *resta vero* e va protetto: (a) `TurnCompleted` porta
  `runtime` = provider/modello **del turno** anche se il loop cambia modello
  dopo; (b) un turno-comando non emette `TurnCompleted`.
- Grep finale: `title` in `jenny/session/`, `webui_turns` in `docs/`.

**Passo 2 — Il sidecar** *(backend; un commit)*
- Nuovo modulo `jenny/session/mascot_mood.py`, tutto testabile senza rete:
  `MOODS`, `mood_inputs(session) -> (user, assistant) | None` (D3/D4),
  `build_mood_request(user, assistant, bot_name) -> list[dict]` (D6),
  `parse_mood(text) -> str` (D5), `async classify_mood(provider, model, session,
  bot_name) -> str | None`, `resolve_mood_model(config, runtime) -> str` (D9).
- `webui_turns.py`: `_schedule_mood_from_event(event)` dopo `handle_turn_end`;
  pubblica il frame di D8 solo se `mood != "neutral"`; registra l'uso (D12).
- `config/schema.py`: i due campi di D9 su `AgentDefaults`, con alias camelCase.
- `channels/ws_sender.py`: early-return `_mascot_mood` → `send_mascot_mood(chat_id,
  mood, turn_id)`; il ramo "no active subscribers" lo tratta come `_goal_status`
  (debug, non warning).
- `agent/token_usage.py`: `"mascot"` in `_SOURCE_KEYS` (+ etichetta i18n se serve).
- Test: `tests/session/test_mascot_mood.py` (inputs: salta sintetici e comandi,
  esce su ultima riga user, tronca dalla testa, soglia 40; parse: lettera, lettera
  con spazio, parola, vuoto; request: contiene bot_name, non contiene altro
  contesto; resolve: preset assente/inesistente/presente),
  `tests/session/test_webui_turns.py` (coordinatore: schedula, pubblica con il
  runtime del turno, non pubblica su `neutral`, non pubblica su errore del
  provider, non schedula con `mascot_mood=false`),
  `tests/channels/test_websocket_channel.py` o file nuovo (frame: forma, solo
  iscritti, non persistito), `tests/config/` (alias e default).

**Passo 3 — Il client** *(un commit)*
- `mobile-jenny.js`: `MOOD_ART` provvisoria (D13), `MOOD_HOLD_MS`, stato di D11,
  `case 'mascot_mood'` in `_handleWsMessage` **prima** della guardia `onScreen`
  (un umore va tenuto anche se arriva mentre è `side`) e in `_handleChatStream`;
  `_syncArt` esteso; livello 0 (D10); preload delle pose d'umore accanto a
  `think`/`talk`.
- `mobile-chat.js`: nessuna modifica attesa (lo switch ignora gli eventi che non
  conosce); verificare che non logghi.
- Test: `tests/webui/test_mascot_mood_client.py` sul modello di
  `test_live_turn_boundary_client.py` — estrarre `_syncArt`/`_applyMood` ed
  eseguirli in node su un `this` finto: umore mostrato solo `out` e `idle`;
  scartato con turno in corso; scartato con `turn_id` diverso; azzerato da
  `chat:sent`; scaduto dopo `MOOD_HOLD_MS`. Più un contratto: ogni valore di
  `MOOD_ART` è una chiave di `ART`/`FLY_POSES`/onboarding esistente, e ogni
  `mood` di `MOODS` (Python) ha una voce in `MOOD_ART` (JS) — due liste, un test.

**Passo 4 — Documentazione** *(nello stesso PR dei passi 2–3)*
- `docs/reference/websocket.md`: il frame `mascot_mood` accanto a `goal_status`.
- `docs/reference/configuration.md`: `agents.defaults.mascotMood`,
  `agents.defaults.mascotMoodModelPreset`.
- `docs/using/themes-mascot.md`: sezione *Espressioni* — cosa vede l'utente, che
  costa una richiesta piccola per turno, che non entra nella conversazione, e
  che i turni da Telegram la fanno reagire lo stesso.
- `android/image_source/README.md`: le pose d'umore (con la mappa provvisoria
  finché l'arte non c'è).
- **Deriva già presente, da correggere nello stesso passaggio** perché si toccano
  le stesse due pagine: `themes-mascot.md` e `settings.md` dichiarano default
  *Medium* e *Right* e un controllo "Mascot position"; `shared/mascot.js` ha
  `'sm'` (120 px) e `'left'`, e il lato non è più una preferenza ma il ricordo
  dell'ultimo atterraggio (`setMascotSide`, scritto dalla companion in `settle`).
  Verificare in `mobile-settings.js` se il controllo posizione esiste ancora: se
  no, via la riga e il "three mascot options" diventa il numero giusto.
- **Regola del sito**: nessun file di `docs/` si sposta o si rinomina.

**Passo 5 — Sul telefono** *(nessun codice)* — **girato il 05/09/2026**, v. le
misure in D5 e nelle incognite; screenshot in sessione. I due turni di prova
(uno positivo, uno negativo) hanno dato entrambi `C` = preoccupata, e a leggere
le risposte è giusto: Jenny si era accorta di essere messa alla prova e lo
diceva. La faccia sul telefono è cambiata (posa provvisoria `think`). Restano
non provati sul device: l'errore → triste, il turno da Telegram, la quota di
neutri su una giornata.
- Build e installazione dell'APK; poi con il client WS (`adb forward` + python
  `websockets`) un turno con risposta chiaramente positiva e uno chiaramente
  negativo: leggere il frame `mascot_mood`, il suo ritardo rispetto a `turn_end`
  (atteso: sotto i 2 s), e il bucket `mascot` nel payload uso token.
- Screenshot della faccia entro i 12 s. Verificare che dopo un `error` la faccia
  sia `sad` senza frame `mascot_mood`.
- Un turno da Telegram: la mascotte reagisce nella WebUI.
- Scrivere qui le misure.

**Passo 6 — L'interruttore in Impostazioni** *(dopo il 5, se il 5 convince)*
- Sezione Personalizzazione → Mascotte, sotto le quattro preferenze locali, una
  riga **backend** "Espressioni" che scrive `mascotMood` via `store.mutate()`
  (modello: `worker_settings.py::update_worker_settings`). Va detto sul posto che
  questa, a differenza delle altre quattro, finisce in `config.json` e nel backup.
- `docs/reference/settings.md`.

**Passo 7 — L'arte** *(indipendente dal 6)*
- 8 PNG, `FILES`, `_UI_MANIFEST`, `ART`, `MOOD_ART` definitiva,
  `COLORARE_LE_POSE.md`, build APK, screenshot delle quattro facce.

---

## Standby (08/09/2026)

Deciso dall'utente dopo la prova sul telefono: **finché le espressioni non sono
disegnate, l'umore non deve avere nessun effetto visivo** — né le facce prese in
prestito, né le reazioni gratis del livello 0. Il meccanismo resta intero e
provato; si spegne in due punti gemelli, e riaccenderlo è togliere due valori:

- backend: `agents.defaults.mascotMood` ha default **`false`** (era `true`).
  Niente richiesta, niente frame, niente token. Chi lo mette a `true` in
  `config.json` paga una richiesta per turno e non vede niente, perché…
- client: `MOOD_STANDBY = true` in `mobile-jenny.js`, letto da `_applyMood`,
  l'unico punto da cui passa ogni cambio di posa per umore (frame e livello 0).
  Un test pinna che il valore spedito sia `true`; l'harness dei test node lo
  mette a `false` per misurare il meccanismo.

Per uscire dallo standby: default a `true`, `MOOD_STANDBY = false`, il test
`test_the_shipped_switch_is_standby` rovesciato, e l'arte del passo 7 al posto
della mappa provvisoria. La pagina utente e `configuration.md` dicono già che è
spento e perché.

**Provato sul Titan 2 l'08/09/2026**, subito dopo l'installazione (processo
riavviato alle 14:19:20): un turno utente normale, chiuso in 5,5 s, e poi 15 s di
ascolto — **zero frame `mascot_mood`**, bucket `mascot` fermo a 6 richieste
(identico prima e dopo), nessuna riga del sidecar nel log dopo il riavvio. La
mascotte resta nella sua posa di riposo.

## Dopo: il parlato espressivo (idea registrata, non pianificata)

Chiesto l'08/09: "c'è parla felice / parla triste?". No: l'umore vale a riposo,
perché il sidecar legge la risposta completa dopo il `turn_end`. Per averlo
mentre parla servono due cose: sapere l'umore prima che finisca — la strada che
non tocca l'agente è **classificare a metà flusso**, al primo pezzo di risposta
oltre ~120 caratteri, stessa richiesta da una lettera ma sulla testa invece che
sulla coda (4–5 s di risposta contro 0,5 s di verdetto: il resto del parlato lo
fa con la faccia giusta; il prezzo è che il tono sta spesso in coda) — e l'arte
del parlato per umore: una posa per umore × 2 bocche × 2 varianti = 16 file,
oltre alle 8 a riposo. Da riaprire come piano suo, dopo il passo 7.

## Incognite aperte

- ~~Il pannello Uso token enumera i bucket o li itera?~~ Nessuna delle due: mostra
  solo totali (chiuso il 05/09/2026, v. D12).
- **Quanto spesso il modello risponde `E`?** Se sopra l'80% dei turni, la
  soglia di D4 può salire (meno chiamate) o il prompt va rivisto; se sotto il
  20%, la mascotte è troppo espressiva e il roleplay stanca.
  **Primo dato d'uso reale, non sintetico (08/09/2026):** la build pre-standby è
  stata in mano all'utente per una giornata, e il log porta **5 verdetti — 4 `A`
  (felice) e 1 `E` (neutro)** su 6 richieste contate nel bucket (una senza riga
  nel buffer di logcat). Il contrario delle due prove costruite del 05/09, che
  avevano dato `C` entrambe perché Jenny si era accorta di essere messa alla
  prova: nell'uso vero è **espansiva, non allarmata**. Neutri al ~20%, cioè
  esattamente il confine sotto il quale questa riga temeva che stancasse.
  Campione minuscolo: da rimisurare su una settimana quando si riaccende.
- ~~La latenza del sidecar su un modello di ragionamento~~ Misurata il
  05/09/2026 su `deepseek-v4-flash` con il thinking spento: **0,50 s e 0,65 s**
  dopo il `turn_end`, ~200–250 token in ingresso e 1–3 in uscita per richiesta
  (bucket `mascot`: 4 richieste, 904 token in ingresso, 8 in uscita). Il rischio
  vero non era la latenza ma il thinking acceso, v. D5.
- **Un'installazione senza `bot_name` personalizzato** dice "Jenny" due volte nel
  prompt: cosmetico, si sistema nel template.

## Trovato strada facendo, portato dentro

- La deriva dei default della mascotte nelle due pagine di documentazione (v.
  passo 4). Il 05/09/2026 era stata proposta come task separato; l'utente ha
  chiesto di tenerla nel piano, e ha senso: sono le stesse due pagine che il passo
  4 riscrive, e una sezione *Espressioni* accanto a una tabella sbagliata sarebbe
  mezza correzione.
