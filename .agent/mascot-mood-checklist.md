# La mascotte che sente — lista di esecuzione

Stato di [`mascot-mood-plan.md`](./mascot-mood-plan.md). Il ragionamento sta là,
qui c'è solo cosa è fatto. Si spunta quando è **girato** (test verdi, o visto sul
telefono per i passi 5 e 7), non quando è scritto.

Ramo: `feat/mascot-mood`, aperto il 05/09/2026 da `main` (a9349ac, con
`fix/synthetic-user-rows` già mergiato come PR #31). **Passi 0 e 1 girati il
05/09/2026**: 9.145 test verdi su 3.14, i tre file toccati verdi anche su 3.11,
lint e pyright puliti. **Passo 2 girato il 05/09/2026**: 9.178 test su 3.14,
366 su 3.11 nelle aree toccate; le due righe di `configuration.md` (4.2) sono
entrate qui perché la pagina promette ogni chiave. **Passo 5 girato il
05/09/2026** (5.1–5.4): la prima prova ha trovato il thinking di DeepSeek acceso
nei 3 token, corretto in `e1c2e0e`; la seconda ha dato frame a 0,5–0,65 s e la
faccia sul telefono. **Standby girato e provato sul telefono l'08/09/2026**
(S.1–S.4): niente richieste, niente facce. Del passo 5 restano aperte solo
l'errore → triste, il turno da Telegram e il PR — e le prime due non si possono
misurare in standby, quindi aspettano il passo 7. **Passi 3 e 4 girati il 05/09/2026**:
9.190 test su 3.14, 276 su 3.11 (client, sessione); `MOOD_ART` provvisoria sulle
pose esistenti per decisione dell'utente — niente arte prevista, il passo 7 resta
per dopo, fuori da questo PR.

Comandi di verifica per ogni passo (da `AGENTS.md`, con la correzione locale:
`python3 -m pytest`, non `pytest`):

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && python3 -m pytest -q
```

Commit sempre con `-s` (DCO): la CI lo controlla al primo push e dopo servirebbe
un force-push.

---

## Passo 0 — il ramo

- [x] **0.1** `git status` pulito e `fix/synthetic-user-rows` non toccato
- [x] **0.2** `git switch -c feat/mascot-mood main`

## Passo 1 — via il titolo *(un commit)*

- [x] **1.1** `webui_turns.py`: rimosse costanti `TITLE_*`/`WEBUI_TITLE_*`,
      `clean_generated_title`, `_title_inputs`, `maybe_generate_webui_title`,
      `maybe_generate_webui_title_after_turn`, `_schedule_title_update_from_event`
      e la sua chiamata; import inutilizzati tolti
- [x] **1.2** `tests/session/test_webui_turns.py`: via le sezioni del titolo;
      restano subscribe/unsubscribe (con il conteggio handler aggiornato se cambia)
      e la proiezione dei turni esterni
- [x] **1.3** `tests/agent/test_loop_save_turn.py`: via i 4 test e gli import
- [x] **1.4** `tests/agent/test_loop_progress.py`: i tre test del titolo riscritti
      su (a) un turno WebUI = una sola richiesta, niente dopo `turn_end`;
      (b) `TurnCompleted.runtime` = provider/modello del turno anche con uno switch
      a metà; (c) turno-comando → `TurnCompleted` **con `runtime=None`** (l'ipotesi
      "non emette" era falsa: misurato, e corretto nel piano)
- [x] **1.5** Grep: `title` in `jenny/session/`, `webui_turns` in `docs/` — niente
      da aggiornare, o aggiornato
- [x] **1.6** Verifica completa verde; commit `-s` — 807 righe tolte, 3 test nuovi

## Passo 2 — il sidecar *(un commit)*

- [x] **2.1** `jenny/session/mascot_mood.py`: `MOODS`, `mood_inputs`,
      `build_mood_request`, `parse_mood`, `classify_mood`, `resolve_mood_model`
- [x] **2.2** `config/schema.py`: `mascot_mood` (`mascotMood`, default `True`),
      `mascot_mood_model_preset` (`mascotMoodModelPreset`, default `None`)
- [x] **2.3** `webui_turns.py`: `_schedule_mood_from_event` dopo `handle_turn_end`;
      frame solo se `mood != "neutral"`; uso registrato con `source="mascot"`
- [x] **2.4** `ws_sender.py`: early-return `_mascot_mood` → `send_mascot_mood`;
      "no active subscribers" a `debug`
- [x] **2.5** `token_usage.py`: `"mascot"` in `_SOURCE_KEYS`
- [x] **2.6** Incognita chiusa: il pannello mostra **solo totali**, nessun bucket
      arriva al client → i18n non toccata (scritto nel piano, D12)
- [x] **2.7** Test: `tests/session/test_mascot_mood.py`
- [x] **2.8** Test: coordinatore in `tests/session/test_webui_turns.py`
      (schedula; runtime del turno; niente frame su `neutral`; niente frame su
      errore provider; niente **richiesta** con `mascot_mood=false` — il flag si
      legge nel task, non nel gestore; niente richiesta quando l'ultima riga è
      user; niente schedule con `runtime=None`, cioè un turno-comando; turno
      Telegram → frame sulla vista `websocket:default`; config illeggibile →
      silenzio)
- [x] **2.9** Test: frame in `tests/channels/` (forma, solo iscritti, non persistito)
- [x] **2.10** Test: `tests/config/` (alias camelCase e default)
- [x] **2.11** Verifica completa verde, **anche con il venv 3.11** (il telefono è
      3.11; `cat /tmp/py311/pyvenv.cfg` prima di fidarsi); commit `-s`

## Passo 3 — il client *(un commit)*

- [x] **3.1** `mobile-jenny.js`: `MOOD_ART` provvisoria con commento, `MOOD_HOLD_MS`,
      `_mood`/`_moodUntil`/`_moodTurnId`
- [x] **3.2** `case 'mascot_mood'` in `_handleWsMessage` **prima** della guardia
      `onScreen`, e in `_handleChatStream`
- [x] **3.3** `_syncArt`: umore solo se `out`, `idle`, non in volo, non in parlato
- [x] **3.4** Livello 0: `error` → `sad`; `thinking` > 20 s → `worried`; ~~turno
      estraneo a mascotte `idle` → saluto~~ rinviato: quel caso nel codice non
      esiste, il turno viene adottato e parlato (v. piano, D10)
- [x] **3.5** `chat:sent` azzera l'umore; frame scartato con turno in corso o
      `turn_id` diverso dall'ultimo chiuso
- [x] **3.6** Preload delle pose d'umore
- [x] **3.7** `mobile-chat.js` non logga sul frame nuovo (verificato, nessuna modifica)
- [x] **3.8** Test: `tests/webui/test_mascot_mood_client.py` (node, `this` finto)
- [x] **3.9** Test: contratto `MOODS` (Python) ↔ `MOOD_ART` (JS) ↔ chiavi arte esistenti
- [x] **3.10** Verifica completa verde; commit `-s`

## Passo 4 — documentazione *(stesso PR)*

- [x] **4.1** `docs/reference/websocket.md`: frame `mascot_mood`
- [x] **4.2** `docs/reference/configuration.md`: i due campi (fatto col passo 2)
- [x] **4.3** `docs/using/themes-mascot.md`: sezione *Espressioni*
- [x] **4.4** `android/image_source/README.md`: pose d'umore e mappa provvisoria
- [x] **4.5** Deriva dei default corretta in `themes-mascot.md` e `settings.md`:
      taglia *Small* (120 px), lato *sinistro* come ricordo dell'ultimo
      atterraggio; riga "Mascot position" tolta se il controllo non esiste più
      (verificato in `mobile-settings.js`), conteggio delle opzioni aggiornato
- [x] **4.6** Nessun file di `docs/` spostato o rinominato

## Passo 5 — sul telefono *(nessun codice; misure nel piano)*

- [x] **5.1** APK installato dal ramo (working tree pulito: Chaquopy impacchetta
      l'albero, non HEAD) — due volte: `c7bc337` (frame mai arrivato) e `e1c2e0e`
- [x] **5.2** Con il client WS: frame `mascot_mood` letto su un turno positivo e su
      uno negativo; ritardo da `turn_end` **0,50 s / 0,65 s**, `turn_id` combacia
      (prima prova: nessun frame — DeepSeek pensava nei 3 token, v. piano D5)
- [x] **5.3** Bucket `mascot` nel file di stato: 4 richieste, 904 in / 8 out
- [x] **5.4** Screenshot della faccia a 0 e 1,5 s dal frame: posa `think` (= `worried` provvisoria)
- [ ] **5.5** Dopo un `error`: faccia `sad`, nessun frame `mascot_mood`
- [ ] **5.6** Un turno da Telegram fa reagire la mascotte nella WebUI
- [x] **5.7** Una giornata d'uso (08/09, build pre-standby): **4 felice + 1
      neutro su 6 richieste**, scritto nel piano fra le incognite
- [ ] **5.8** PR aperto verso `main` con i passi 1–4 (il merge è dell'utente)

## Standby — 08/09/2026

- [x] **S.1** `agents.defaults.mascotMood` default `false`; test e doc aggiornati
- [x] **S.2** `MOOD_STANDBY = true` in `mobile-jenny.js`, in `_applyMood`; test node
      con harness a `false` più un test con standby acceso e uno che pinna il valore
- [x] **S.3** `themes-mascot.md` e `configuration.md` dicono che è spento e perché
- [x] **S.4** APK dallo standby sul telefono (08/09/2026): un turno chiuso in
      5,5 s + 15 s d'ascolto → **0 frame**, bucket `mascot` fermo a 6, nessuna
      riga del sidecar dopo il riavvio alle 14:19, mascotte nella posa di riposo

## Passo 6 — l'interruttore → **spostato** in [`mascot-faces-checklist.md`](./mascot-faces-checklist.md) (passo 8)

- [ ] **6.1** Riga backend "Espressioni" in Personalizzazione → Mascotte, via
      `store.mutate()`, con la nota "finisce in config.json e nel backup"
- [ ] **6.2** i18n `it`/`en`
- [ ] **6.3** `docs/reference/settings.md`
- [ ] **6.4** Test route + verifica verde; commit `-s`

## Passo 7 — l'arte → **spostato** in [`mascot-faces-checklist.md`](./mascot-faces-checklist.md)

L'arte è arrivata l'08/09/2026 in una forma diversa da quella prevista qui: non
otto pose d'umore ma **corpo + faccia** a due livelli, che rende l'espressione
ortogonale al gesto. I punti sotto sono quindi superati — restano per storia, la
lista viva è l'altra.

## ~~Passo 7 — l'arte~~ *(superato)*

- [ ] **7.1** 8 PNG 3000×3000 in `android/image_source/` (`mood_<x>.PNG` + `_color`)
- [ ] **7.2** `gen_pose_webp.py::FILES` (+4), rigenerato
- [ ] **7.3** `android_assets.py::_UI_MANIFEST` (+8)
- [ ] **7.4** `ART` (+4) e `MOOD_ART` definitiva; commento "provvisoria" rimosso
- [ ] **7.5** `COLORARE_LE_POSE.md` e `README.md` aggiornati
- [ ] **7.6** APK, screenshot delle quattro facce, a colori e B/N
