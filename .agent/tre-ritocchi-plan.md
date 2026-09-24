# Tre ritocchi dalla todo — piano

**Stato: voce 1 fatta il 24/09/2026 (codice e test; manca la prova sul telefono). Voci 2 e 3 in attesa: l'utente ha chiesto «fai solo la 1».** Tre voci aperte della
app todo sul telefono, scelte dall'utente fra quelle veloci:

| voce della todo | id todo |
|---|---|
| «jenny deve poter stare solo a destra… ora se va a sinistra si rompe» | `18f5ba818291` |
| «officina chat falla coi messaggi espansi» | `23c947b78b90` |
| «metti orario ai messaggi» | `0a2296980c07` |

Ordine di lavoro: **1 → 2 → 3**, un commit per voce, build e install sul Titan 2
dopo ciascuna (v. memoria «installa senza chiedere»). **Attenzione: oggi al Mac
sono attaccati due device** (`TITAN20000002704` e `R3CT60QXVYF`): ogni `adb` e
l'install vanno col seriale (`ANDROID_SERIAL=TITAN20000002704`).

## Le decisioni

| voce | scelta | chi |
|---|---|---|
| 1 — lato | **sempre a destra, e ci torna sempre**: mai a sinistra, né in casa né in officina | utente, 24/09 |
| 1 — la flottante (Kotlin) | **anche lei** solo a destra: è la stessa Jenny | fatto così il 24/09; l'utente non ha risposto alla domanda, dato «fai solo la 1» come via libera al piano com'era |
| 2 — cosa vuol dire «espansi» | pensiero e risultati dei tool **aperti di default** | proposta, da confermare |
| 3 — dove | casa **e** officina, sotto ogni bolla, tue e di Jenny | proposta, da confermare |
| 3 — lo storico | **orari veri anche per il vecchio**, ricavati come sotto; dove non si ricava, niente orario (mai uno finto) | proposta, da confermare |

---

## 1 — Jenny sta solo a destra

**Com'è oggi.** Il lato non è un'impostazione: è il ricordo di dove l'hai lasciata.
- `shared/mascot.js:51-69` — `mascotSide()` legge `jenny-mascotte-dock-side`, e il
  **default è `left`** (:58); `setMascotSide()` lo scrive.
- `shared/mascot-drag.js:193-218` — `settle()`: all'atterraggio sceglie la metà
  dello schermo in cui è caduta (:197) e, se cambia, chiama `onSideChange`.
- `shared/jenny-mascot.js:439-465` — `_setSide()` mette `.side-left` e salva;
  `mobile-jenny.js:147-149` lo estende per la minichat.
- CSS del lato sinistro: `mobile-style.css:1728` (fab), `:6860-6866` (ancoraggio e
  specchio), `:6959-6990` (fumetto e nuvoletta della minichat); `casa-style.css:362-366`
  (la riga di lavoro che si scansa, commit `6687ed8`).
- La flottante: `FloatingFlight.kt:334` `chooseSide()` (metà schermo) e
  `FloatingOverlayController.kt:357` `PREF_RIGHT = "park_right"`.

Casa e officina usano **lo stesso** `shared/jenny-mascot.js` (da `b0567b8`), quindi
il cambio JS è uno solo per entrambe.

**Il cambio.**
1. `mascot.js`: `mascotSide()` ritorna sempre `'right'`; `setMascotSide()` sparisce.
   `jenny-mascotte-dock-side` va in `DEAD_KEYS`, così la chiave vecchia si pulisce.
2. `mascot-drag.js` `settle()`: niente scelta di lato. Il bordo è sempre il destro,
   quindi `fs.xT` è sempre il dock destro, e lei **ci torna a piedi** da dovunque
   l'hai lasciata. Se è caduta nella metà sinistra resta chiusa quando arriva
   (`fs.targetOut = false`), come succede oggi quando attraversa lo schermo.
   `onSideChange` esce dal contratto di `bindMascotDrag`.
3. `jenny-mascot.js` e `mobile-jenny.js`: via `_setSide` e il suo aggancio.
4. CSS: via tutte le regole `.side-left` elencate sopra, **compresa quella di
   `6687ed8`**, che esisteva solo per il caso a sinistra.
5. Flottante: `chooseSide()` punta sempre a `dockPivotX.second`; `PREF_RIGHT` si
   ignora in lettura (resta `true`), così chi l'aveva parcheggiata a sinistra se la
   ritrova a destra al primo avvio.

**Il punto da misurare: la camminata lunga.** A 150 px/s (`WALK_SPEED`, uguale
in Kotlin) attraversare i ~575 px CSS del Titan 2 costa ~3,5 s, contro una
`deadline` di rientro di 6 s (`RETURN_TIMEOUT_MS`). Ci sta, ma di poco se la lanci
in alto a sinistra: la caduta si somma. Se non ci sta, alla scadenza lei si
teletrasporta sul bordo. Decidere dopo averlo visto: alzare la deadline o
accelerare il passo sopra una certa distanza.

**Com'è andata (24/09/2026).** Fatto come sopra, con due aggiunte:
- **La scadenza si allunga con la strada.** `settle()` (JS) e `settleTarget()`
  (Kotlin, ex `chooseSide`) portano la scadenza a `GETUP_MS + strada/WALK_SPEED
  + 1,5 s` quando serve. Sul Titan 2 i 6 s sarebbero bastati (~4,8 s dal bordo
  sinistro), ma su uno schermo largo 1000 px no: il test di mutazione, senza
  allungamento, la lascia **a 217 px dal dock**.
- **Il verso del gesto è uno solo**: in `finish()` sparisce `sideSign`, e verso
  l'interno apre.

In Kotlin sparisce `parkedRight`: le bolle mettono il tuo messaggio sempre a
sinistra (`atStart = line.mine`), niente specchio in `syncFace`, `parkX` ha un
ramo solo, `dockPivotX` è un `Float`. La chiave `park_right` si cancella al primo
salvataggio (`PREF_DEAD_RIGHT`).

Test toccati: `test_mascot_side_contract.py` riscritto (contratto nuovo + tre
voli veri in node con orologio finto), `test_mascot_single_variant.py`
(`mascotSide` non è più esportato), `test_mascot_dock_contract.py` (due
ancoraggi, non quattro), `test_floating_chat_contract.py` (la regola delle
bolle). Suite: 10634 passed, 32 skipped.

**Test (piano originale).** `tests/webui/test_mascot_side_contract.py` e
`test_mascot_single_variant.py` parlano del lato sinistro: si riscrivono per il
contratto nuovo («nessuna regola `.side-left`, `mascotSide()` è sempre destra»),
non si cancellano.

**Prova sul telefono.** La Jenny in-app sul Titan 2 è spenta (`visible=0`, v.
memoria «La mascotte in-app è spenta»): per vederla va riaccesa **dall'utente**. La
flottante si prova da sé: lanciarla a sinistra e guardarla tornare.

## 2 — Chat dell'officina coi messaggi espansi

**Com'è oggi** (`mobile-chat.js`):
- pensiero: `_appendReasoningBlock(..., collapsed = true)` (:1615), richiamato dal
  replay con `true` (:1043); il blocco dal vivo nasce chiuso (`_buildThinkingBlock(true)`, :1676);
- risultato di un tool: si crea solo al tocco (`_toggleToolResult`, :2069);
- file modificati: il contenitore nasce `collapsed` (:2110).

**Il cambio.** Un'unica costante in cima al modulo (es. `EXPAND_BY_DEFAULT = true`)
che decide lo stato iniziale di tutti e tre. Il tocco continua a chiudere e riaprire.
Per il tool, «aperto» vuol dire creare il `<pre>` appena il risultato arriva, invece
che al primo tocco.

**Due cose da non rompere.**
- Lo scroll del pensiero dal vivo (`_thinkStick`, :1630-1665) oggi dà per scontato
  che il blocco sia chiuso, e quindi «in fondo». Aperto, deve inseguire l'ultima riga
  davvero: va provato con un ragionamento lungo.
- Gli errori: `test_tool_error_collapse_client.py` fissa come si comportano i tool
  in errore (voce todo `65bd91e1ab76`, «perché gli errori sono sempre espansi?»,
  chiusa). Va riletto prima di cambiare, per non riaprire quella decisione.

**Da confermare**: se «espansi» vuol dire anche i digest dei subagent (`sa-digest`, :3208).

## 3 — L'orario sui messaggi

**Com'è oggi.** Nessuna delle due chat mostra un'ora. La riga sotto la risposta ha
solo Copia e i secondi del turno (`casa-chat.js:541-566`; `_ensureMsgActions` in
`mobile-chat.js`). Il transcript (`.jenny/webui/websocket_default.jsonl`) **non
salva nessun orario** per riga, e il replay inventa `createdAt = ora di
caricamento + idx` (`webui/transcript_replay.py:50`, :177 e seguenti). Nessun
client legge `createdAt`.

**La scorciatoia per lo storico.** Ogni risposta di Jenny ha già la sua ora esatta,
dentro lo `stream_id`: `unified:default:<time_ns>:0`, generato all'inizio del turno
(`agent/loop.py:167` e `:1755`). Visto sul telefono: `1790243711489510866` → 24/09/2026.
La domanda dell'utente nello stesso `turn_id` precede quell'istante di una frazione
di secondo, quindi prende la stessa ora (per un'etichetta «14:22» basta). Un turno
senza stream (comandi, errori) resta senza orario. Non serve rileggere il file di
sessione.

**Il cambio.**
1. **Recorder**: ogni riga nuova del transcript porta `"ts": <epoch ms>` al momento
   dell'append (`transcript_store.py::_append_to_active_transcript`, o a monte nel
   recorder). Solo un campo in più: le righe vecchie restano valide.
2. **Replay**: ogni messaggio riceve un campo nuovo **`at`**, preso da `ts` se c'è,
   altrimenti dallo `stream_id` del turno, altrimenti assente. `createdAt` resta
   com'è, perché cambiarlo tocca ordinamenti e test che non c'entrano.
3. **Dal vivo**: la bolla prende `Date.now()` quando nasce. Il telefono è sia il
   server sia il client, quindi l'orologio è lo stesso.
4. **UI**: l'ora va sotto la bolla, nella riga delle azioni che c'è già. Oggi,
   «14:22»; più vecchio, «ieri 14:22» o una data breve, riusando `shared/when.js`.
   Stringhe in `i18n/{it,en}.json`.

**Test.** Replay: un record con `ts`, uno solo con `stream_id`, uno senza niente →
`at` giusto / derivato / assente. Client: la bolla mostra l'ora se `at` c'è, e
niente altrimenti.

---

## Checklist

- [x] 1.1 `mascot.js` + `mascot-drag.js` + `jenny-mascot.js` + `mobile-jenny.js`: solo destra
- [x] 1.2 CSS `.side-left` rimosso (mobile-style, casa-style)
- [x] 1.3 flottante Kotlin: `chooseSide` fisso a destra, `PREF_RIGHT` ignorato
- [x] 1.4 test del lato riscritti; `ruff`/`pytest` verdi
- [ ] 1.5 install sul Titan 2; camminata lunga misurata (deadline 6 s)
- [ ] 2.1 officina: pensiero, tool e file modificati aperti di default
- [ ] 2.2 scroll del pensiero dal vivo provato aperto; test sugli errori riletto
- [ ] 2.3 install e prova sul Titan 2
- [ ] 3.1 recorder: `ts` su ogni riga nuova
- [ ] 3.2 replay: campo `at` (ts → stream_id → assente) + test
- [ ] 3.3 casa e officina: ora sotto la bolla, dal vivo e da storico; i18n
- [ ] 3.4 install e prova sul Titan 2, su un pezzo di storico vecchio
- [ ] 4 spuntare le tre voci nella todo (`18f5ba818291`, `23c947b78b90`, `0a2296980c07`)
