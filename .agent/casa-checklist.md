# La casa — registro

Piano: [`casa-plan.md`](./casa-plan.md). Ramo: `feat/la-casa`.

## Passo 1 — Il guscio ✅

- [x] `jenny/templates/ui/casa.html` — documento-guscio. Due librerie in tutto
      (marked, DOMPurify) contro le quindici dell'officina.
- [x] `assets/casa-app.js` — bootstrap, i18n, attach al websocket, e i metodi
      che il guscio nativo chiama. **Erano tre, sono cinque**: v. passo 5.
- [x] `assets/casa-style.css` — la colonna: filo che scorre, composer fermo,
      niente dock.
- [x] `assets/i18n/{it,en}.json` — sezione `casa`.
- [x] `_UI_MANIFEST` — le tre voci nuove. Verificato: 199 voci, nessuna senza
      file su disco.
- [x] CSP estesa ai due documenti (`_SHELL_DOCUMENTS` in `ws_http.py`), con test
      che fallisce se si torna al confronto col nome singolo.
- [x] `api.navigate(path)` — la strada fra i due gusci col segreto di bootstrap.

**Misurato**, a 590x566 (viewport vera del Titan 2): il filo prende 469,5 px e il
composer 64. Nell'officina gli stessi 566 px pagano dock e intestazione.

**Verificato:** `ruff` pulito, `pyright` sul sottoinsieme bloccante 0 errori,
**9698 test passati** / 7 saltati. Pagina caricata alla misura vera: tema,
lingua e i tre metodi del contratto a posto, nessun errore JS (i soli errori di
rete sono il gateway assente nel banco di prova).

**Da sapere per i passi dopo:** il banco di prova locale serve `jenny/templates/ui`
su `http://127.0.0.1:8777` sia sotto `/html-mobile/` sia sotto `/assets/`, perche'
`i18n.load` chiede `/assets/i18n/<lingua>.json` **senza** il prefisso — sul
gateway vero funziona, con un solo montaggio no.

## Passo 2 — La conversazione ✅

- [x] `assets/casa-chat.js` (392 righe, contro le 3.789 di `mobile-chat.js`).
      Storia dal transcript, frame dal vivo, risposta che si forma.
- [x] Etichette di provenienza: *da Telegram*, *dalla tendina*, *dal fumetto* —
      nomi per chi legge, non identificatori di canale come in officina.
- [x] Forma dal disegno (`Main.dc.html`): quel che dici tu e' una bolla a
      destra, quel che risponde Jenny e' testo sulla pagina.
- [x] Markdown con marked + DOMPurify, che fallisce **chiuso**: senza
      sanificatore si degrada a testo semplice, non si inietta HTML crudo.
- [x] Stato di lettura fallita distinto dallo stato vuoto: una conversazione
      vuota e una irraggiungibile non sono la stessa cosa.

**Le quattro regole del filo, verificate una per una dal vivo** (sono quelle che
`mobile-chat.js` ha imparato sbagliando, e descrivono il protocollo, non il
disegno):

| Regola | Prova |
|---|---|
| `stream_end` puo' arrivare senza testo, vale il buffer | il primo segmento sopravvive |
| un `message` apre un blocco suo e lo chiude | l'avviso non viene sovrascritto dalla narrazione dopo |
| piu' segmenti nello stesso turno non si incollano | due blocchi distinti, non "…segmento.Secondo…" |
| un messaggio senza `turn_id` non entra nel turno prima | due avvisi restano due |

In piu': un `turn_end` di un *altro* turno non chiude quello aperto, e un
`tool_hint` non produce niente.

**Due difetti trovati e corretti guardando la pagina**, non leggendola:

1. La riga di traccia (`read_file: sensori.json`) finiva nella risposta. La
   condizione "tienila solo se non c'e' altro testo" **non e' valutabile** dove
   la scrivevo: il testo vero arriva in un frame successivo dello stesso turno.
   In casa le tracce si buttano e basta, e un turno muto non fa bolla.
2. Lo stato vuoto restava a schermo *sopra* la conversazione: `[hidden]` e' una
   regola del browser a specificita' bassissima e il `display: grid` della
   classe la scavalcava.

**Aggancio al fondo verificato con contenuto che scorre davvero** (il primo
tentativo, con dodici righe, non riempiva il filo e non dimostrava niente):
in fondo insegue, risaliti smette, tornati in fondo riprende.

## Passo 3 — Il composer ✅

- [x] Invio manda, shift-invio va a capo (`isComposing` esclude l'invio che
      chiude un accento o una composizione IME).
- [x] Il campo cresce col testo e **torna piccolo**: misurato 41 → 104 → 41 px.
- [x] La bolla del proprio messaggio la disegna il client. Non e' una
      scorciatoia: il gateway rimanda l'eco solo dei messaggi entrati da *altri*
      canali, quindi senza questo la propria domanda comparirebbe solo dopo un
      ricaricamento.
- [x] Socket chiuso = nessuna bolla. Una bolla che compare e un messaggio che
      non arriva sono la stessa cosa vista da due parti, e la prima fa credere
      alla seconda.
- [x] Fermare: `goal_status` (`running` / `idle`) trasforma **lo stesso**
      bottone da manda a ferma. Un secondo bottone accanto sarebbe due bersagli
      dove ne serve uno, e quello sbagliato sempre a portata di pollice.
- [x] `/stop` scritto a mano non diventa una bolla: in casa i comandi non ci
      sono, ma digitarne uno non deve mostrare in chat cio' che il transcript
      esclude apposta dall'eco.
- [x] Messaggio vuoto o di soli spazi: non parte.

**Il banco di prova ora e' un gateway finto** (`scratchpad/finto_gateway.py`):
parla bootstrap, thread e websocket con `attach`/`message`, e serve gli statici.
Non imita Jenny, imita **il filo** — ed e' la ragione per cui esiste: qui
l'interruzione a meta' risposta si produce a comando, su un gateway vero si
aspetta che capiti.

**Giro completo misurato:** messaggio mandato, bolla disegnata, risposta che si
forma in markdown; poi *ferma* a meta' — il bottone passa a *manda* e il testo si
interrompe dopo 50 caratteri, senza bolla per `/stop`.

**Due trappole del banco, non della casa**, annotate perche' costano tempo:
`connection.respond()` di `websockets` fissa `Content-Length` sul testo che gli
passi, e assegnare `resp.body` dopo lascia la lunghezza a zero — 200 con corpo
vuoto, pagina bianca, **zero errori in console**. E il `key: "Return"`
dell'automazione del browser non arriva alla pagina come `key === 'Enter'`: un
invio sintetico funziona, quindi un invio che "non manda" li' non prova niente.

## Passo 4 — La riga di lavoro ✅

- [x] `assets/casa-activity.js`: 43 strumenti in sette famiglie, ognuna col suo
      vocabolario in `it`/`en`.
- [x] Comparsa dopo mezzo secondo (una risposta immediata non fa lampeggiare
      niente), rotazione della parola ogni 4 s, scomparsa a fine turno.
- [x] Mentre la risposta si forma la riga **si toglie di mezzo**: dirti che sto
      scrivendo mentre leggi quel che scrivo e' rumore. Torna se dopo il testo
      ricominciano gli strumenti.
- [x] Tocco lungo → `/html-mobile/index.html#turn=<id>` via `api.navigate`
      (senza id: la sola officina). L'officina il frammento non lo legge
      ancora, e non fa danno; quando lo leggera', da questa parte non c'e'
      niente da cambiare.

**Il giro delle famiglie, campionato ogni mezzo secondo su un turno vero:**

```
  501ms  off  think                 ← famiglia decisa, non ancora a schermo
 1004ms  ON   think     Arzigogolo…
 1506ms  ON   read      Spulcio…
 2510ms  ON   search    Scandaglio…
 3512ms  ON   out       Vado a vedere…
 4515ms  ON   run       Metto alla prova…
 5519ms  ON   delegate  Sveglio un aiutante…
 6524ms  ON   busy      Mi do da fare…     ← strumento sconosciuto
 7530ms  off  —                            ← la risposta ha cominciato ad arrivare
```

**La regola dell'onestà regge:** in sette parole diverse estratte dentro la
stessa famiglia, la famiglia non e' mai cambiata. Uno strumento fuori tabella
cade sul vocabolario generico invece di raccontare una cosa che non sta facendo:
e' l'unica differenza fra questa riga e un'animazione di caricamento.

Provato anche il ritorno: testo → strumento → testo, e la riga riappare come
`write`.

**Un test del repo ha preso un difetto vero**
(`test_every_long_press_caller_consumes_the_flag`): chi chiama `setupLongPress`
deve consumare il flag che l'helper posa. La riga non fa niente a un tocco
breve, quindi non avevo messo nessun gestore — ma il flag restava attaccato
**per sempre** dopo la prima pressione lunga, e il giorno che a un tocco breve
si volesse far fare qualcosa, quel qualcosa partirebbe anche dopo ogni
pressione lunga. Nota per chi tocchera' questo file: la guardia va scritta
`if (line.dataset.longpress)` con un identificatore semplice — il test la cerca
con una regex, e `this.el.dataset.longpress` non la soddisfa pur essendo
corretto.

## Passo 5 — Gli allegati ✅

- [x] Graffetta **dentro** la pastiglia del campo: allegare e' una cosa che si
      fa al messaggio che stai scrivendo. Mandare sta fuori, perche' quella
      chiude il messaggio.
- [x] `ImageHandler` condiviso, con i tetti del server (4 immagini, 8 MB): se
      divergessero, il gateway rifiuterebbe il messaggio intero.
- [x] Striscia degli allegati in attesa, ognuno con la sua X — senza, l'unico
      modo di disfare uno sbaglio sarebbe mandare il messaggio.
- [x] **Una foto senza didascalia parte**: la bolla e' l'immagine. Col solo
      controllo sul testo non sarebbe partita — e' la stessa regola che il
      gateway applica all'eco di Telegram.
- [x] Immagini in arrivo e in uscita nel filo; tocco per ingrandire, con la
      lightbox condivisa e il suo pinch-zoom (lo zoom del viewport e' spento in
      tutta l'app, quindi senza quella un'immagine si guarda solo grande come
      la miniatura).

### Il contratto col guscio nativo era di tre metodi. Sono cinque.

`onNativeReady`, `goHome`, `onPackageChanged`, **`handleHardwareBack`** e
**`openChat`**. Le due mancanti non si trovano cercando `window.mobileApp.<nome>`:
il Kotlin le chiama su una variabile locale (`var app = window.mobileApp; …
app.handleHardwareBack()`). **Il ponte verso il JS si conta sulle chiamate, non
sul nome dell'oggetto.**

`handleHardwareBack` e' quella che conta: senza, il tasto Indietro non chiude
l'immagine ingrandita e la pressione ricade sul sistema. In casa la catena e'
corta — c'e' una cosa sola sopra la conversazione — e **alla radice non fa
niente**, perche' questa app e' il launcher e Indietro non deve mai chiudere il
task. `openChat` (il tocco su un avviso proattivo) toglie di mezzo cio' che
copre e riporta in fondo al filo.

Verificato: ingrandimento aperto → Indietro lo chiude → Indietro di nuovo non
rompe niente → nessun residuo nel DOM (il `body` ha solo il guscio, il modulo e
l'input nascosto del selettore).

## Passo 6 — La mascotte ✅

- [x] `assets/casa-mascot.js` (164 righe, contro le 1.353 di `mobile-jenny.js`).
- [x] **In casa Jenny e' presenza, non controllo** (`pointer-events: none`).
      La minichat esiste perche' in officina la chat puo' non essere a schermo;
      in casa la chat *e'* lo schermo, e un secondo posto dove scriverle sarebbe
      una porta che da' sulla stanza in cui sei gia'. In piu' sta sopra un filo
      che scorre: ogni suo gesto sarebbe un gesto rubato allo scorrimento.
- [x] Tre stati piu' l'umore: riposo, pensa (corpo che oscilla), parla (la bocca
      si apre e chiude ogni 260 ms finche' il testo arriva).
- [x] Guardia sull'umore, come in officina: **rifiutato** se un turno sta
      girando o se e' la reazione a un turno che non e' piu' l'ultimo. Una
      faccia per una risposta superata confonde piu' di nessuna faccia.
      Verificato: durante un turno no, altro turno no, turno giusto si',
      umore sconosciuto no.
- [x] Geometria misurata: taglia 120 (`sm`), `right: -30px` = **-0,25 × taglia**
      esatto, come nella tavola.
- [x] Il composer cresce col testo e Jenny gli sta sopra: l'altezza vera finisce
      in `--casa-composer-h` via `ResizeObserver`, invece di un `bottom` fisso
      che al terzo capoverso la farebbe finire dentro il campo.

**Le tabelle degli sprite sono una copia**, e `tests/webui/test_casa_mascot_contract.py`
e' cio' che la tiene onesta: stessi file dell'officina, tutti presenti e tutti
nel manifest, e gli umori allineati a `MOODS` del backend. Verificato che il
test abbia denti (un percorso sbagliato lo fa diventare rosso).

**Corretto un commento sbagliato in `mobile-style.css`**: diceva che il
personaggio occupa ~45% del quadrato. E' il rapporto dell'arte a 3000 px prima
dell'impacchettamento; nei webp veri e' il **73% dell'altezza**. Fidarsene fa
disegnare la mascotte grande il doppio del vero — e' successo, all'inizio di
questo lavoro.

## Passo 7 — Le due porte ✅

- [x] Intestazione della casa dalla tavola `Main.dc.html`, ridotta a cio' che
      esiste: nessun chevron sul titolo, perche' la scelta di con chi parli e'
      del giro dopo e un chevron che non apre niente e' una bugia.
- [x] Porta di andata: il bottone in alto a destra. Portera' a «Tu e Jenny»
      quando quella pagina esistera'; oggi va dritto in officina, e l'icona lo
      dice.
- [x] Porta di ritorno: in fondo a Impostazioni → Sistema, non nel dock. Il
      dock e' la navigazione *dentro* l'officina, e la casa non e' una sua
      schermata: e' l'altro documento.
- [x] **Scambio dei nomi**: `index.html` e' la casa (ed e' cio' che il guscio
      nativo carica), l'officina ha il suo nome.
- [x] Installato e verificato sul Titan 2 (0.11.0, aggiornamento sul posto).

### Lo scambio ha rotto 21 test, e uno era gia' rotto

Venti erano contratti di markup che leggevano `index.html` perche' l'officina
*era* `index.html`: ora leggono `officina.html`. Il contratto non e' cambiato,
il file si'.

Il ventunesimo no. `test_static_serves_index_when_dist_present` scriveva un
`favicon.svg` in una cartella `dist` **mai collegata al canale** —
`static_dist_path` e' `workspace/ui`, non una cartella del test — quindi quel
file non veniva mai servito: la richiesta cadeva sul fallback SPA e
l'asserzione `"<svg" in text` passava perche' la shell dell'officina conteneva
`<svg id="graph-svg">`. **Verde per una coincidenza**, e lo scambio ha tolto la
coincidenza. Ora sonda un asset che esiste e pretende che *non* sia la shell.

### E ha richiesto una riga di Kotlin, che il piano diceva di no

`shouldOverrideUrlLoading` bloccava ogni navigazione di primo livello verso il
gateway che non fosse esattamente `GATEWAY_PATH`. E' una rete di sicurezza
giusta — impedisce a un href risolto male sotto `/html-mobile/` di sostituire
la SPA con `/api/…` e nessuna via di ritorno — ma e' precedente all'esistenza
di un secondo guscio, e bloccava **entrambe** le porte. L'unica traccia era
`Blocked main-frame navigation to a non-SPA gateway path` in logcat: il bottone
semplicemente non faceva niente. Il guard ora conosce i due documenti per nome,
in un elenco chiuso e non un prefisso.

### Misurato sul telefono, sulla conversazione vera

- La storia si e' ridisegnata con le etichette di provenienza: un messaggio
  entrato dal fumetto porta *from the bubble*.
- Mandato un messaggio: bolla disegnata, **«Ruminating…»** sotto la
  conversazione, bottone diventato ferma, Jenny nella posa del pensa.
- Risposta arrivata — e Jenny aveva delegato a un subagent. In casa non se n'e'
  vista traccia: solo il numero. Nell'officina, lo stesso turno mostra la
  pastiglia `spawn`, «Show thinking» e i tempi (4,1 s e 8,9 s).
- Il campo cresciuto a due righe ha alzato Jenny: il `ResizeObserver` funziona
  sul dispositivo vero.
- Giro completo delle porte: casa → officina → casa, senza 401 e senza blocchi.

Nota: l'interfaccia sul telefono e' in inglese perche' lo e' la lingua scelta
li' — vale per entrambe, non e' un difetto della casa.

## Ritocco — Jenny appoggia, e sta ferma

I piedi sul bordo superiore del composer, che fa da pavimento. Il numero e'
misurato, non stimato: l'arte e' un quadrato con margini trasparenti e sotto i
piedi ce n'e' il **12,24% del lato** (bbox alpha dei webp a 768 px — personaggio
y 112..674, x 226..576 — identico nelle tre pose del corpo). Quindi
`bottom = <pavimento> − 0,1224 × lato`. Verificato a **scarto zero**, e regge
quando il campo cresce a quattro righe (pavimento a 127 px, scarto sempre zero).

Il pavimento e' il composer e non il bordo del filo: e' l'unica linea che sta
sempre li'. La riga di lavoro e la striscia degli allegati vanno e vengono, e
Jenny non deve saltellare quando lo fanno.

**Niente piu' `jenny-bob`.** In officina quell'animazione non e' il riposo:
appartiene a `.out`, cioe' a quando e' venuta fuori. Una che galleggia non sta
appoggiata a niente. Il `wobble` del pensa resta — ruota attorno al 90%
dell'altezza, quasi sui piedi: e' un dondolio sul posto, non un volo.

## Ritocco — il volo pegman anche in casa

Era una decisione, scritta al passo 6, non una dimenticanza: *presenza, non
controllo*. Ma una delle due ragioni era debole — sostenevo che ogni suo gesto
avrebbe rubato lo scorrimento, mentre in officina quel conflitto e' gia' risolto
dalle soglie del gesto. Tolta quella, restava solo la minichat, che non basta.

**La fisica e' stata estratta, non copiata**: `shared/mascot-drag.js`, 427 righe
spostate identiche riga per riga. `mobile-jenny.js` passa da 1.353 a 908 righe e
le da' i propri appigli. Il concetto `out` — la mascotte che sta al bordo ed
"esce" per parlare — esiste solo in officina: un host che dichiara
`hasOut: false` percorre gli stessi rami con lo scarto d'ancoraggio a zero,
invece di avere un codice suo.

> Superato il 19/09/2026: `out` ce l'hanno tutti e due e `hasOut` non esiste
> piu' — v. *Ritocco — toccarla per metterla via*, in fondo.

**Un difetto preso in tempo.** Avevo specchiato il *contenitore* per il lato
sinistro. L'officina specchia solo l'arte di riposo (`.jenny-art-stack`), ed e'
la cosa giusta: il livello del volo e' un fratello dell'arte, e la fisica ci
scrive traslazioni in coordinate schermo — con lo specchio sul contenitore,
lanciandola a destra sarebbe volata a sinistra. Ora la casa ha il suo
`.casa-jenny-art`, come l'officina.

**Il lato e' un ricordo condiviso** (`shared/mascot.js`): dove la lasci in
officina la ritrovi in casa. E' la stessa persona nello stesso telefono.

### Due cose che il banco di prova mi ha quasi fatto sbagliare

**Nel pannello del browser il rAF non gira**: misurato, *zero frame in un
secondo*, `document.hidden` vero. La prima prova sull'officina mostrava la posa
ferma su `hang` e il volo chiuso solo dopo 7 s per scadenza — sembrava una
regressione da estrazione, ed era il banco. Con una pompa manuale
(`requestAnimationFrame` sostituito da un timer) tutte e cinque le pose
compaiono e il volo chiude in 3,2 s. **La prima prova non provava niente.**

**C'e' un terzo consumatore di questa fisica**, che non avevo in mente: la
mascotte flottante in Kotlin. `tests/runtime/test_floating.py` tiene allineate
le costanti di `FloatingFlight.kt` con quelle JS, e leggeva da
`mobile-jenny.js`. L'ha preso la sua stessa guardia — *«un test che non
confronta niente passa sempre»* — che e' scattata a zero confronti. Ripuntato
al modulo condiviso, e insegnato a leggere anche `export const`: ne confronta
dodici.

### Il difetto che solo un dito vero poteva prendere

Nel CSS avevo scritto il commento *«si prende e si lancia, quindi i tocchi le
arrivano»* e lasciato la riga `pointer-events: none` due righe sotto. Sul
telefono il trascinamento scorreva il filo invece di prenderla.

**Il mio test nel browser non poteva accorgersene**: sparavo gli eventi con
`dispatchEvent` direttamente sull'elemento, che scavalca il hit-testing. Un
evento sintetico dimostra che i gestori funzionano, **non** che un dito ci
arriva. Per quello serve il dispositivo, o un test che parta dalle coordinate.

E prima ancora mi ero perso un'installazione: l'esito di `adb install` era
finito dentro una catena di comandi e non l'avevo letto. L'APK era delle 22:27
e sul telefono c'era ancora quello delle 22:25, quindi stavo provando la
versione vecchia e concludendo sulla nuova. Si controlla in un secondo:
`dumpsys package … | grep lastUpdateTime` contro la data dell'APK.

**Verificato sul Titan 2**, dove il rAF gira davvero: presa, volo, atterraggio,
rientro a piedi al suo posto coi piedi sulla linea; e con un lancio lento verso
sinistra passa di lato, specchiata, appoggiata allo stesso pavimento.

### «Appena la tocco sparisce»

Le regole del volo in `mobile-style.css` sono due meta' e **una sola e' senza
prefisso**: `.jenny-fly { display: none }` vale per tutti, ma quella che lo
accende e' `.jenny-duo.flying .jenny-fly`, legata alla classe dell'officina. In
casa lo sprite e' `.casa-jenny`: nascondevo l'arte di riposo e non accendevo
niente, quindi al tocco Jenny spariva e basta.

**Di nuovo un test che guardava il meccanismo invece del risultato**: contavo le
classi `.on` sulle pose, che il JS mette comunque. La misura giusta e' *quanti
pezzi di Jenny sono davvero visibili* — `visibility`, `display`, `opacity`, e il
`display` del contenitore. A riposo 1, in mano 1, in volo 1, atterrata 1; col
difetto rimesso apposta, 0.

## Ritocco — la pagina precedente, in tutte e due le case

Prima la casa chiedeva una pagina e buttava il cursore che il server le mandava
insieme: si arrivava in cima e la conversazione *sembrava cominciare li'*. Niente
bordo, niente bottone, nessun modo di sapere che sotto c'era altro.

**La macchina a stati e' una sola** (`shared/history-pager.js`), estratta
dall'officina come la fisica del pegman: cursore, chiavistello, ancoraggio dello
scorrimento e bottone di ripiego sono lo stesso codice, cambia solo da dove
prende gli appigli. Resta nei gusci quel che i gusci fanno diverso: dove si
ascolta lo scroll contro dove si misura, come si chiede una pagina, come si
disegna.

**Due modi, e servono entrambi.** Lo scorrimento infinito e' il gesto; il bottone
e' cio' che resta quando il filo non trabocca e quindi non emette **nessun**
evento `scroll`. In officina e' un caso limite (due `/new` di fila); in casa,
che butta tracce e strumenti, una pagina puo' disegnare niente — quindi il
bottone non e' la versione ridotta dello scorrimento, e' il pezzo obbligatorio.

**50 e non 160, e il motivo e' il costo, non il contenuto.** Avevo scritto (e
detto) che il budget conta le righe degli strumenti: **e' falso**.
`_count_anchor_messages` conta solo `user`, `stream_end` e `message`; l'attivita'
degli strumenti arriva come record `activity` e non entra nel conto, e comunque
le tracce consecutive di un turno si fondono in un messaggio solo. Quindi 50
sono ~25 scambi. A calare e' il numero di turni scelti, e con loro i record
grezzi che il gateway rigioca a ogni apertura.

### Tre cose che il censimento ha salvato in anticipo

- `_insertAtTop` ha **quattro** chiamanti: non si sposta, diventa un appiglio.
- `isLoadingHistory` non e' interno alla paginazione — lo legge il resync della
  riconnessione, e un test lo pretende per nome. I tre campi restano leggibili
  col loro nome, come accessori sul pager.
- **Cinque file di test ritagliano questi metodi per nome** da `mobile-chat.js` e
  li eseguono in node. Tutti asseriscono `«<nome> non trovato»`, quindi lo
  spostamento ha fatto 23 rossi rumorosi invece di verdi silenziosi — l'opposto
  della trappola di `test_floating.py`. Ora guidano il modulo vero, e provano
  piu' catena di quanta ne provasse il ritaglio.

### La trappola nuova, che ha morso subito

`loadInitialHistory` viene **ritagliato come testo** dentro tre banchi node. Ci
avevo messo una costante di modulo (`HISTORY_FIRST_PAGE_SIZE`): in node quel
nome non esiste, il `ReferenceError` e' finito nel `try/catch` del metodo, e
l'unico sintomo era «nessuna fetch in volo». Un fallimento muto in un test che
esiste per non averne. Il 160 resta un letterale li', col commento che dice
perche'.

### Misurato sul Titan 2, sulla conversazione vera

- **La casa ha chiesto una seconda pagina.** Non dedotto dallo scorrimento: la
  50esima ancora dalla fine del transcript e' il messaggio «si', mi piace — e'
  il gesto che tutti gia' conoscono», cioe' dove finisce la prima pagina; lo
  schermo mostrava roba molto piu' vecchia (il backup notturno, la Marranella).
- **L'ancoraggio non salta**: un trascinamento lento di 400 px ha spostato
  «E altre cinque righe diverse» da y 543 a y 950 — 407 px, esattamente il
  trascinamento. *Con una passata veloce non si misura niente*: l'inerzia
  continua a scorrere dentro la pagina appena arrivata, e sembra un salto.
- **L'officina regge**: scorsa all'indietro oltre un confine `NEW SESSION
  STARTED` fino ai `/dream` di prima, che sta oltre la sua prima pagina.

**Quel che sul telefono non ho provato:** il bottone. Compare solo quando c'e'
altro e il filo non trabocca, cioe' subito dopo `/new` — e `/new` sulla
conversazione vera dell'utente azzera il suo contesto. Lo coprono i sette test
del modulo condiviso, che girano sul codice vero.

## Ritocco — un rifiuto del gateway, detto a parole

Prima: l'officina scriveva `Errore: image_rejected` — il nome che quel rifiuto
ha nel codice sorgente, mostrato a chi stava mandando una foto — e la casa non
diceva niente del tutto. Il motivo vero (`decode`, `size`, `too_many_videos`)
stava nel frame e veniva buttato, perche' `detail || reason` non guarda mai il
secondo.

**La radice era che `detail` faceva due mestieri**: a volte un codice, a volte
una frase inglese intera, a volte un `repr` da debug. L'invariante ora e':
`reason` e' per la macchina e c'e' **sempre**, `detail` e' per il log. Quattro
righe nel gateway e un contratto sull'albero sintattico che impedisce al
prossimo errore di nascere senza una parola.

**Le parole stanno in `shared/wire-error.js`**, tradotte, una tabella sola per
i due gusci. `i18n.t` ritorna **la chiave** quando non la trova, quindi una voce
dimenticata metterebbe a schermo `common.wireError.decode`: tre test lo
impediscono, e uno confronta l'elenco del client con quello del server.

**Due famiglie, non una.** «Il tuo messaggio non e' entrato» appartiene al
messaggio: la bolla se ne va, il testo torna nel campo, una riga dice perche'.
«Qualcosa non ha funzionato» resta una riga e basta. La famiglia non si decide
dal solo `reason`: ogni rifiuto di allegato porta `detail: "image_rejected"`
qualunque sia il motivo, quindi un motivo inventato domani torna indietro lo
stesso invece di farti perdere quel che avevi scritto.

**Quale bolla** si sa per stato e non per orologio: la bolla resta *in sospeso*
finche' un frame che non e' un rifiuto non dimostra che il gateway l'ha presa.
Sul filo non c'e' un identificativo, e aggiungerlo toccherebbe protocollo, due
client e i loro test per un caso che oggi non lo chiede.

### L'officina aveva la stessa malattia in due posti, e la casa ne aveva curato uno

`sendMessage` disegnava la bolla, svuotava il campo, **buttava gli allegati** e
*poi* provava a spedire: un socket chiuso ti lasciava una bolla che sembrava
partita, un errore di fianco, e il testo perduto. La casa faceva gia' il
contrario dal passo 3. Quindi meta' del lavoro e' andata nella direzione
opposta al solito: **la regola della casa e' passata in officina.**

Gli allegati non tornano, ed e' voluto: l'allegato *e'* la cosa rifiutata, e il
server butta il lotto intero senza dire quale file fosse.

### La scoperta che rende il tutto non teorico

Credevo che il rifiuto fosse quasi irraggiungibile dall'app, perche' i tetti
client e server coincidono. **Non coincidono.** Il client ha due secchi
(immagini <=4, tutto il resto <=4) e **non sa cosa sia un video**: `grep -c
video` in `image-handler.js` da' zero. Il server ne ha tre e cappa i video a
**1** (`_MAX_VIDEOS_PER_MESSAGE`).

Quindi **due video allegati passano il telefono e li rifiuta il gateway**, con
`too_many_videos`. E' ordinario, e prima di oggi in casa era silenzio assoluto.
Il lavoro lo trasforma in «Troppi video in un messaggio solo» col messaggio che
torna indietro — ma la divergenza resta, e la cura vera sarebbe un terzo secchio
nel client, che avvisa *prima* di mandare. Non l'ho fatto: e' un'altra cosa.

### Misurato sul Titan 2, con un rifiuto vero

Due `.mp4` da 2 kB in Download, allegati tutti e due — il telefono li accetta
(non ha un secchio per i video) e il gateway li rifiuta. E' il giro vero, non un
frame iniettato.

**In casa:** nessuna bolla, la riga «Too many videos in one message» sobria
sotto la conversazione, e **«guarda questi due video» tornato nel campo**. Poi
lo stesso testo mandato senza allegati: bolla disegnata, campo svuotato, Jenny
nella posa del pensa — l'invio normale non si e' rotto dal riordino.

**In officina:** identico. Riga d'errore, nessuna bolla, «prova in officina»
tornato nel campo.

E una conferma che non avevo cercato: alla domanda mandata dopo, Jenny ha
risposto *«non mi e' arrivato niente, il messaggio e' solo testo, zero
allegati»*. Gli allegati rifiutati erano stati tolti davvero, non solo dallo
schermo.

L'interfaccia sul telefono e' in inglese perche' lo e' la lingua scelta li'.

Test verdi (9.741) e provati contro nove difetti messi apposta.

## Ritocco — il telefono sa cosa sia un video, e lo dice prima

`image-handler.js` aveva **due** secchi (immagini <=4, tutto il resto <=4) e un
commento che diceva «cap per-tipo allineati al server»: non lo erano. Il gateway
ne ha **tre** e i video li accetta a **uno**. Quindi due video passavano di qui
e li rifiutava il server a messaggio gia' partito — cioe' esattamente il caso
che quei tetti esistono per evitare.

Ora i secchi sono tre, specchio di `classify_media_item` ordine compreso. Il
ripiego sul nome resta **solo per le immagini**, come di la': darlo anche ai
video rifarebbe nascere la divergenza al contrario.

E **niente sparisce piu' in silenzio**. Il `continue` muto era la seconda meta'
del difetto: sceglievi cinque foto, ne comparivano quattro, e nessuno diceva
quale mancasse. Il rifiuto locale usa il codice del gateway, quindi si legge
identico a uno remoto: **toast in officina** (che ce l'ha, ed e' la forma giusta
per una risposta al composer) e **la stessa riga nel filo in casa**, che il
toast non ce l'ha.

Il contratto confronta numeri, insiemi di MIME e vocabolario delle due parti.
Rimettendo i due secchi diventano rossi sette test: e' la misura che avrebbe
trovato la divergenza senza doverci inciampare.

### Due difetti trovati mentre lo provavo, e sono quelli veri

**La riga finiva sotto il bordo.** `.casa-pending` e' *fratello* del composer,
non figlio, quindi il `ResizeObserver` messo sul composer per la geometria di
Jenny non la vedeva comparire. Il filo e' `flex: 1`: ogni riga che spunta sotto
gli toglie altezza e il fondo scivola fuori schermo. Valeva anche per l'ultimo
messaggio quando alleghi una foto, e c'era da sempre.

**E sotto c'era di peggio.** Il primo rimedio non mordeva, perche' la bandierina
era gia' abbassata: quando il contenitore si accorcia il browser emette uno
`scroll` che nessun dito ha causato, e `_stick = _atBottom()` lo prendeva per un
gesto. Da quel momento la chat **smetteva di seguire i messaggi nuovi** — bastava
allegare una foto. I due casi si distinguono dalla direzione: solo un dito porta
`scrollTop` indietro.

Il listener e' uscito dal costruttore perche' una chiusura anonima li' dentro
non si puo' esercitare — e il primo banco che ci ho provato **passava a vuoto**:
non agganciava niente, `_stick` restava vero, ed era proprio quello che
asseriva. Terza volta in questo lavoro che un test guarda il meccanismo invece
del risultato.

### Misurato sul Titan 2

Due `.mp4` selezionati insieme: ne entra **uno**, e comparo «Too many videos in
one message» — riga sobria nel filo in casa (visibile senza scorrere), pastiglia
rossa in officina. Prima dello stesso giro, con due video allegati e mandati, il
rifiuto arrivava dal gateway: nessuna bolla, riga, e il testo tornato nel campo.

## Ritocco — toccarla per metterla via (19/09/2026)

«Su officina se la clicchi si nasconde ed esce, sulla casa no vero?» No.
Misurato invece che dedotto, sui due gusci veri: in officina un tocco porta lo
sprite da `right: -30px` a `-56,28px` e scambia l'arte frontale con la posa di
profilo; in casa il tocco arrivava e non faceva niente, perche' `casa-mascot.js`
non passava nessun `onTap` e il modulo condiviso ne ha uno che tace.

Era una decisione scritta — *«il tocco secco quindi non fa niente: e' l'unico
gesto che in officina apre la minichat, e qui non ha un equivalente da
aprire»* — ed era **mezza giusta**. Vera sulla minichat, che resta dell'officina
per la ragione di sempre. Falsa su tutto il resto: in officina quel tocco fa
*due* cose, e la seconda — toglierla di mezzo — in casa serve **di piu'**, non di
meno, perche' qui Jenny sta appoggiata sopra il filo che stai leggendo.

**Cosa e' diventato comune, e perche' proprio quello.** Non lo stato in se': e'
una classe che si gira, e un giro di classe non vale un modulo. Quello che non
poteva restare in due copie sono i **due ancoraggi** — quanto del suo quadrato
resta fuori dallo schermo al bordo (0,469) e venuta fuori (0,25). Erano gia'
scritti tre volte (due nel foglio dell'officina, una nel JS che decide dove far
finire la camminata di rientro dopo un lancio) e la casa stava per aggiungerne
altre due. Ora stanno in `shared/mascot.js` e arrivano ai due fogli come
`--jenny-dock` e `--jenny-out`: **una copia sola, e le altre non sono piu'
possibili** — non solo rilevabili.

Scritte **all'import** e non da un costruttore, ed e' la trappola del giro:
`mobile-jenny.js` attacca lo sprite al documento *prima* di chiamare
`applyMascotSize()`, e un `calc()` con una variabile che non esiste ancora non
e' «il valore di prima», e' una dichiarazione invalida — Jenny comparirebbe per
un frame dove la mette il flusso. Un modulo viene valutato prima che qualunque
elemento esista.

**E `hasOut` e' sparito.** Esisteva per dire «in casa questo stato non c'e'»:
adesso c'e' in tutti e due, quindi l'interruttore aveva un valore solo. Un ramo
morto che il prossimo lettore prende per una possibilita' vera costa piu' di
quanto renda, e `shared/mascot-drag.js` ci guadagna tre righe e un concetto in
meno. Il suo cappello, che quell'eccezione la spiegava, adesso racconta che non
c'e'.

**Il pavimento ha retto per un pelo, e lo so perche' l'ho misurato.** In casa i
piedi appoggiano sulla riga del composer, e il numero (12,24% del lato sotto i
piedi) veniva dal bbox alpha delle tre pose frontali. La posa del bordo e' una
quarta arte: **12,50%**. Differenza 0,26 punti — 0,3 px a taglia 120, 0,5 a 210.
Sotto il pixel, quindi mettendola via non saltella. Con due punti di scarto
avrebbe galleggiato, e nessun test l'avrebbe detto.

Tre regole prese dall'officina invece che reinventate: al bordo il disegno e'
**uno solo** (la posa di profilo ha la faccia dentro, e tenere acceso il volto
frontale sopra meno di meta' Jenny le incolla una faccia sulla nuca); **la bocca
si muove lo stesso**, perche' messa via non vuol dire zittita; **il dondolio del
pensa no**, che contro il bordo somiglia a un guasto della pagina.

Da `<div aria-hidden>` a `<button aria-label="Jenny" tabindex="-1">`: da quando
toccarla fa qualcosa, `aria-hidden` e' una bugia detta a chi non la vede. Fuori
dal percorso della tastiera come in officina — e' un ornamento che si puo'
toccare, non una tappa fra il titolo e il campo.

### Com'e' stato provato

Sul banco locale, con la stessa CSS e lo stesso JS che finiscono nell'APK:

* **tocco** — click per coordinate (non `dispatchEvent`: il hit-testing e' la
  meta' che nel pannello si perde) → `out` via, `jenny-side.webp`, faccia
  spenta, `x` da −30 a −56. Ritocco: tutto indietro;
* **mentre pensa** → posa di profilo e la classe `thinking` tolta;
* **mentre parla** → `jenny-side.webp` e `jenny-side-talk.webp` che si
  alternano, campionate per 12 secondi;
* **trascinamento**, con la pompa manuale del rAF (nel pannello non gira):
  verso il bordo la mette via, verso l'interno la tira fuori, e **lanciata
  dall'altra parte atterra al bordo nuovo a `x = 526`** — che a 590 di viewport
  e' l'ancoraggio esatto, non un'approssimazione;
* **l'officina non si e' mossa**: gli stessi due numeri di prima, −56,28 px e
  −30 px, letti dal computed style a transizione disattivata.

21 mutazioni, 21 rosse. Due erano verdi al primo giro e **nessuna delle due per
colpa del banco**: una cadeva sulla classe iniziale, che il banco si riscriveva
invece di leggerla dal sorgente (ora la legge); l'altra sostituiva la costante
con il numero *dentro la fisica*, mentre il test si accontentava di vederla
nell'`import`. Terza volta che la domanda giusta davanti a un verde e' «dove e'
caduta la mutazione?».

### Sul Titan 2

APK delle 12:58:10, installato alle 12:59:02, firmato `CN=flagDiZero`.

Il tocco la mette via e la rimette fuori, e quel che c'era sotto si legge: nello
scatto di prima Jenny copriva l'inizio di due righe dell'ultima risposta, in
quello dopo no. **Non sparisce** — resta il quarto di quadrato che l'officina
lascia al bordo, ed e' quel che deve essere: e' parcheggiata, non spenta, e da
li' la si ritrova per ritoccarla.

**L'area esclusa dalle gesture di sistema segue l'ancoraggio**, che era la meta'
che fuori dall'APK non si puo' provare — il ponte nativo non c'e'. Letta da
`dumpsys window`: `(0,994,179,1334)` al bordo, `(0,994,245,1334)` fuori. I 66 px
di differenza sono esattamente lo scarto d'ancoraggio, `(0,469 - 0,25) × 120 CSS
× 2,5`; e ciascun bordo destro torna al pixel con `(r.right + 8) × dpr`. Senza il
gancio su `transitionend` si sarebbe dichiarato ad Android il posto da cui se ne
stava andando.

**Il trascinamento con `input motionevent`**, non con `input swipe`: il volo
intero e' girato sul telefono — presa, caduta, tonfo, camminata di rientro — e
lei e' finita al bordo, con il rettangolo tornato a 179. E' la parte che sul
banco esiste solo con la pompa manuale del rAF.

Non provato sul telefono, di proposito: **la bocca che si muove dal bordo**.
Vorrebbe dire far girare un turno vero nella conversazione dell'utente per
guardare due immagini alternarsi; quella e' stata campionata sul banco per 12
secondi.

## Ritocco — la coda di una risposta, che è anche il suo confine (21/09/2026)

«Nella chat casa non c'è separazione tra i vari messaggi di jenny, sembra un
messaggione unico.» Vero, e non è spaziatura. In casa Jenny non ha una bolla
attorno al testo — è la decisione del Passo 2, «è testo sulla pagina, come una
lettera» — quindi quattro risposte di fila sono quattro gruppi di paragrafi a
10 px l'uno dall'altro, e l'occhio le legge come una.

Il confine è **la coda**: il pulsante Copia e i secondi del turno, su una riga
sola. Due cose che servono, invece di una linea che non serve a niente. La
stessa riga dell'officina, dove Copia e secondi erano **due** nodi impilati e
adesso sono uno.

Quel che è servito, ed è quasi tutto nel «quando», non nel «cosa»:

- **La coda si posa in `_resetTurn`, non in `_turnEnd`.** Un turno finisce in
  più modi: il `turn_end` del gateway, ma anche un frame di un turno nuovo che
  scavalca quello aperto (`_crossesTurn`) e un invio partito da qui
  (`appendOwn`). Attaccata al solo `turn_end`, **la prima di due risposte
  consecutive restava senza** — cioè proprio il caso da separare.
- **I secondi arrivano da `turn_end.latency_ms`** e vanno azzerati dopo l'uso,
  o la risposta dopo mostra il tempo di quella prima. Dalla cronologia li porta
  `_buildTurns`, che fin qui li buttava con tutto il resto dell'officina.
- **Si copia il sorgente, non il reso.** `_registra` nei tre punti in cui il
  testo di un segmento è definitivo — `_streamEnd`, `_message`,
  `_appendAssistant` — e `innerText` come rete, così un Copia non copia mai il
  vuoto. `WeakMap` sulla bolla: una ricarica del filo li butta tutti.
- **La coda cresce dopo la misura.** Nel percorso vivo la bolla è già nel filo
  quando la riga si aggiunge, quindi `gap.aggiorna()` e `_follow()` vanno
  richiamati: il margine per scansare la mascotte era stato calcolato su una
  bolla più corta.

In officina, insieme: **il `⋯` se n'è andato** con il foglio «Copia testo /
Copia come Markdown» che era l'unica cosa che apriva. Resta un Copia solo, e
copia il sorgente — cioè la voce per cui quel foglio era stato scritto. Le
bolle utente restano senza riga: il `⋯` era la loro unica azione.

Banchi: `test_casa_message_tail_client.py` (16, nuovo) e
`test_chat_copy_client.py` riscritto; venti mutazioni, tutte rosse.

## Ritocco — anche le nostre bolle si scansano (21/09/2026)

`shared/jenny-gap.js` cercava `.casa-msg-jenny`, perché il tetto che stava
sostituendo (`max-width: 88%`) era sulle risposte. Ma le bolle di chi scrive
sono `align-self: flex-end` — nella colonna destra, che è la sua — e la più
recente è anche la più in basso: **l'unica cosa che copriva sempre era quello
che avevi appena scritto tu**. Il selettore ora è `.casa-msg`.

Il *come* scansare resta diverso, e sta nel CSS. La risposta non ha sfondo:
`padding-right` le stringe il testo e non si vede. La bolla ce l'ha, e con il
padding si allungherebbe fin sotto di lei col vuoto dentro — si sposta tutta
intera, `margin-right`. Stesso numero, stesso conto, fatto una volta sola.

Banchi: 3 nuovi in `test_jenny_gap_client.py`, di cui due eseguono `aggiorna()`
su un DOM finto (il difetto stava nell'aggancio, non nella geometria pura).
Quattro mutazioni, tutte rosse. Misurato sul Titan 2: la bolla si sposta di
145 px device e si ferma 52 px prima di lei, dove prima le finiva sotto.

## Ritocco — una Jenny sola (24/09/2026)

«Possibile che non funziona più l'animazione di Jenny? C'era un frame con la
mano alzata.» In casa **non c'era mai stata**: `casa-mascot.js` aveva il frame
nella tabella (`BODY.hand`) e nessuna riga che lo usasse. Era il sintomo, non il
difetto. Il difetto era che le mascotte erano **due**: fisica e ancoraggi in
comune (`shared/`), ma il cervello — stati, parlato, umore, lettura dei frame —
scritto una volta in `mobile-jenny.js` e un'altra, «più leggera», in
`casa-mascot.js` più uno `switch` in `casa-app.js`. Il test sulla copia
(`test_casa_mascot_contract.py`) confrontava i nomi dei file e basta.

Confrontate riga per riga, la casa divergeva in otto punti, nessuno scritto
come scelta: niente mano nel parlato; **un umore vivo sostituiva la bocca** per
12 s, quindi riscrivendole subito rispondeva con la faccia felice ferma; al
silenzio fra due delta si fermava invece di tornare a pensare; l'errore non
aveva la faccia triste; all'invio non pensava finché il server non parlava; un
avviso proattivo in mezzo a una risposta la chiudeva (niente tracciamento del
turno); i frame di un'altra conversazione non erano filtrati; «riduci
animazioni» ignorato.

**Decisione dell'utente: codice unico, base l'officina, cambia solo il
pavimento.**

- `shared/jenny-mascot.js` (`JennyMascot`) è Jenny nella chat vera: arte,
  stati, parlato con la mano, umore, filtro della conversazione, tracciamento
  del turno, tocco/volo, lato, visibilità. È il cervello dell'officina spostato
  com'era, commenti compresi.
- `mobile-jenny.js`: `JennyCompanion extends JennyMascot` — solo le viste che
  non sono la chat e la minichat (`setMode`, `_handleFrame`, `_onOutChange`,
  `_onDragCommit`, `handleBack`, `_send`…).
- `casa-app.js` crea `new JennyMascot(.casa-shell)` e **non la pilota più**:
  `_readActivity` guida solo la riga di lavoro. `casa-mascot.js` è cancellato.
- CSS: lo sprite è `.jenny-duo` in tutti e due i gusci, con lo stile di
  `mobile-style.css` (che la casa carica). In `casa-style.css` resta una regola
  sola, `.casa-shell .jenny-duo { bottom; z-index }`: il pavimento sul composer
  e l'unico livello del foglio.
- Due miglioramenti della casa sono passati all'officina invece di perdersi:
  il dondolio del pensa solo a Jenny **fuori** (`.out.thinking`, e solo
  sull'arte di riposo, non sulle pose del volo), e `appearance`/`touch-callout`
  azzerati sul bottone.
- Cosa cambia a vista in casa, oltre ai difetti: il **respiro** (`jenny-bob`,
  4 px su e giù a Jenny fuori) prima era tolto apposta — «una che galleggia non
  sta appoggiata a niente». Adesso c'è, perché è dell'officina. Se non piace,
  è una regola sola da togliere per entrambe.

Banchi: `test_one_mascot_contract.py` (nuovo) tiene la frase «una sola»: l'arte
nominata in un file solo, il parlato con la mano, la casa che non la pilota, il
foglio della casa che dice solo `bottom` e `z-index`, niente dondolio dal
bordo. `test_mascot_mood_client.py` e `test_live_turn_boundary_client.py`
girano sul modulo condiviso, quindi adesso provano anche la casa.
`test_casa_dock_client.py` e il vecchio contratto della copia sono cancellati:
provavano la seconda Jenny. `test_no_ghost_methods_contract.py` segue
`extends` fino al file della madre (un metodo tolto da lì lo fa diventare
rosso — provato).

Banco nel pannello (modulo vero, i due fogli veri, frame iniettati): mano alzata
al cambio di gesto, faccia felice dopo `turn_end`, cancellata dal turno nuovo
con la bocca che riparte, triste sull'errore, posa di profilo messa via. Piedi
sul composer: bordo basso a 516,7 px = 566 − 64 + 0,1224 × 120.

## Ritocco — il fuoco resta sul campo (24/09/2026)

Segnalato dall'utente: «se clicco da una parte si toglie il focus e non posso
più scrivere». Riprodotto sul Titan 2: tocco sul filo, poi un tasto, e il tasto
non arrivava da nessuna parte. La casa non aveva il type-ahead dell'officina
(`_maybeTypeAheadFocus`), né altro che rimettesse il fuoco sul campo; e ogni
tocco — filo, bolla, tasto manda, fila — glielo toglieva.

- [x] `casa-fuoco.js`: **type-ahead** (un carattere nel vuoto rimette il fuoco
      sul campo, stesse guardie di `shared/type-ahead.js`) e **il tocco non
      ruba il fuoco** (`preventDefault` sul `mousedown` di `#casa-chat` e
      `#casa-fila`, fuorché su un altro campo) — il secondo solo con la
      tastiera fisica, perché altrove il fuoco è una tastiera a schermo alzata.
- [x] `JennyNative.hasHardwareKeyboard()`: `qwerty` e `hardKeyboardHidden=NO`
      da `Configuration`, letto a ogni chiamata.
- [x] Il fuoco torna da sé rientrando sulla pagina chat, uscendo da ordina,
      rientrando nella stanza chat, con Home e tornando all'app da un'altra.
      Sempre con `preventScroll`: la pista può essere a metà scivolata.
- [x] Mai quando la chat non è a schermo o c'è qualcosa sopra
      (`_composerAttivo`: stanza, pagina, strati, `dialog[open]`, lightbox).

Sul Titan 2 (IME Pastiera, tastiera fisica), dopo l'installazione: tocco sul
filo → tasto nel campo; tocco su «Jenny» nella fila → idem; tasto manda a campo
vuoto → il fuoco resta; graffetta → selettore → Indietro → il fuoco resta;
Home, e ritorno dalle Impostazioni di Android → il tasto arriva senza toccare;
avvio a freddo → idem; pagina App → i tasti vanno alla ricerca, non alla chat;
pressione lunga su una bolla → la selezione c'è ancora, e un tocco la chiude.
La striscia da 30 px in fondo che compare col campo a fuoco è di Pastiera
(`requestedHeight = 30`), non una tastiera a schermo: c'è anche toccando il
campo a mano.

Banco: `test_casa_fuoco_client.py`, il modulo vero sotto node.
