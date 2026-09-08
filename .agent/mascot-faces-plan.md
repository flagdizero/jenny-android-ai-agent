# Le facce di Jenny — il piano

L'arte passa da **una posa per stato** a **corpo × faccia**: due immagini
sovrapposte sullo stesso quadrato 3000². L'umore smette di essere una posa e
diventa la faccia, cioè ortogonale al corpo — e le espressioni le decide
l'arte, non il codice. Nello stesso giro **il bianco/nero si ritira**: una sola
variante per posa, e con lei sparisce la rimappatura dei path.

Gemello di [`mascot-mood-plan.md`](./mascot-mood-plan.md), che resta il
documento del *sidecar* (come si chiede l'umore al modello, quanto costa, come
arriva al client). Di quel piano questo supera tre punti, segnati là:
**D7** (le etichette), **D13** (l'arte per ultima, con la mappa provvisoria) e
la sezione **Standby**.

Stato di esecuzione: [`mascot-faces-checklist.md`](./mascot-faces-checklist.md).

Decisioni dell'utente che questo piano recepisce (08/09/2026): l'arte comanda,
le espressioni sono quelle disegnate, `thinking` vale solo mentre si aspetta la
risposta, il bianco/nero si toglie, e l'umore dopo il turno va bene com'è.

---

## Il rilievo (08/09/2026, misurato sui PNG)

L'artista ha consegnato 23 PNG in `JENNY_IMG_NEW/`, tutti 3000×3000 RGBA.
Cosa sono, misurato e non dedotto dai nomi:

**I 7 corpi sono le pose che già esistono, con la faccia cancellata.** Fuori
dal riquadro della faccia sono identici *bit per bit* ai `_color.PNG` di
`android/image_source/`. Confronto con la regione della faccia esclusa,
differenza in pixel su un campione 375²:

| nuovo | vecchio | differenza fuori dalla faccia |
|---|---|---|
| `dritta_idle` | `idle` = `talk_1b` = `talk_2b` | 0 |
| `dritta_hand_up` | `talk_1a` = `talk_2a` | 0 |
| `pensa` | `think` | 0 |
| `dritta_wave1` / `dritta_wave2` | `hello2` / `hello1` (**incrociati**) | 0 |
| `diagonale_idle` | `jenny-side` = `jenny-side-talk` | 0 |
| `diagonale_hand_up` | — | posa nuova |

Quei segni di uguale sono la scoperta che dà la misura del guadagno: **delle 15
pose di oggi solo 10 sono corpi diversi**, e delle 6 a schermo (fuori dal volo)
tutte e 6 stanno nel set nuovo. Nelle coppie del parlato l'unica cosa che
cambiava era la bocca — quattro file per due gesti.

**Le 16 facce sono livelli puri**, occhi + naso + bocca + i tratti sulle
guance, su ~1,5% di inchiostro, registrate sullo stesso canvas: si compongono
in alpha **senza offset, senza scala, senza pivot**. Provato ricomponendo:

- `dritta_idle` + `faccia_dritta_talk` = `talk_1b_color` — **identico**;
- `pensa` + `thinking_dritta` = `think_color` — **identico**;
- `dritta_hand_up` + `faccia_dritta_talk` = `talk_1a_color` — identico a meno
  di una frangia di antialias (85×67 px, sotto soglia);
- `dritta_idle` + `faccia_dritta_normal` vs `idle_color` — 75 px di differenza
  nella bocca: la faccia neutra è stata ridisegnata un filo, non è la cotta.

**Il nome non è invertito, dice a riposo o alternativa.** Zoomando sulle
bocche: `normal` è un trattino chiuso e `normal_talk` una bocca tonda aperta;
`sad` chiusa e `sad_talk` aperta; ma `happy` è **il sorrisone aperto** e
`happy_talk` la bocca chiusa. Cioè: il file senza suffisso è la faccia **di
riposo** di quell'espressione, `_talk` è **l'altra bocca**. Un chibi felice a
riposo sorride con la bocca aperta, e va bene: all'animatore serve solo una
coppia di fotogrammi, l'ordine non si vede.

**Da docked una faccia non si legge.** Misurato ritagliando quello che resta
davvero a schermo (`right: -0.469 × size`, quindi il 53,1% del quadrato):
si vede **un occhio e mezza bocca**. Fianco a fianco, `sad`, `angry` e
`thinking` in versione diagonale sono indistinguibili; solo `happy` si intuisce
dall'occhio chiuso. La vecchia guardia `out` aveva ragione, e ora ha una
misura.

**Il bianco/nero è un disegno diverso, non una desaturazione**: in `idle.PNG` i
capelli sono *bianchi* con il contorno nero e gli occhi neri pieni con la
scintilla bianca, e l'alfa stesso non combacia col `_color`. Non è derivabile
dai 23 file nuovi, che sono tutti la variante colore (saturazione media 0,21,
come i `_color`). Ed è la ragione per cui si ritira invece di essere esteso
(v. F9). Prima di toccarlo, verificato che **tutte e 15 le pose sono davvero
colorate** (md5 diverso dal gemello B/N, cioè nessuna è rimasta segnaposto):
togliere il B/N non fa perdere nessuna posa.

**La coppia neutra diagonale manca** (ci sono `happy_normal`, `happy_talk`,
`sad`, `sad_talk`, `angry`, `angry_talk`, `thinking`, ma nessun `normal`). Non
serve in questo giro — v. F2 — e se un giorno servisse **è derivabile
dall'arte cotta**, con verifica: siccome `diagonale_idle` è `jenny-side` senza
faccia, basta tenere di `jenny-side_color` i pixel che si discostano dal corpo
e azzerare l'alfa altrove. Provato: la ricomposizione torna con `max_delta=15`
su 13 pixel di frangia (chiusa) e `max_delta=2` su 0 pixel (aperta).

**Pesi.** In webp 768² q80: un corpo 22 kB, una faccia 5–8 kB. Oggi le 30 pose
pesano 675 kB (320 B/N + 355 colore). Dopo: 355 kB delle 15 pose che restano +
~105 kB dei 9 livelli = **~460 kB**, cioè meno di adesso.

**Due consumatori dell'arte, non uno.** Oltre alla companion,
`mobile-onboarding.js` usa `hello1`/`hello2`/`idle`/`fall`/`ground` su una sola
`img`: con corpi senza faccia mostrerebbe una Jenny senza volto.

**Un difetto che c'era già.** `@keyframes jenny-bob` scrive `transform:
translateY(...)` sulle stesse `img` su cui `.jenny-duo.side-left img.jenny-art`
scrive `scaleX(-1)`: l'animazione vince, quindi **da sinistra e out lo specchio
si annulla** e Jenny guarda fuori dallo schermo invece che dentro, contro
l'intenzione scritta nel commento del CSS.

**Un doppione morto.** `icon_color.png` è byte per byte identico a `icon.png`, e
`gen_icons.py` legge solo il secondo: se ne va col B/N.

---

## Le decisioni, e perché

**F1 — Due livelli, non una posa per combinazione.** Con l'umore come posa
servivano ~20 disegni per coprire umore × gesto × bocca; a livelli servono 3
corpi e 6 facce. E il guadagno non è solo di file: l'umore diventa ortogonale
al gesto, quindi "triste mentre pensa" o "felice mentre parla" non sono
combinazioni da disegnare ma da comporre.

**F2 — Il livello vale solo a mascotte intera.** Restano *esattamente come
oggi*, arte cotta a una sola `img`: il **docked** (`jenny-side`,
`jenny-side-talk` — da lì la faccia non si legge, misurato), il **volo** (le 5
pose pegman, dove l'umore è già escluso) e l'**onboarding**. È la scelta che
tiene il raggio d'azione dentro un solo stato visivo e non tocca nessun altro
consumatore.

**F3 — Il nome del file dice la bocca, non l'accoppiamento.** Si importano coi
nomi dell'artista (`face_front_<expr>` e `face_front_<expr>_talk`), e nel codice
la coppia del parlato è sempre `(plain, _talk)`. Nessun caso speciale per
`happy`: sono due fotogrammi e l'animatore li alterna.

**F4 — Le espressioni sono quelle dell'arte: `happy`, `sad`, `angry`,
`normal`, `thinking`.** Dal backend spariscono `worried` e `surprised`: non
hanno faccia, e inventarne una prendendo in prestito è ciò che questo giro
smette di fare. `MOODS` diventa `("happy", "sad", "angry", "neutral")`.

**F5 — `thinking` è uno stato, non un umore.** La faccia del pensa la decide
`_agentState === 'thinking'`, dura quanto l'attesa e **vince sull'umore**.
Spariscono `MOOD_WORRY_AFTER_MS`, `_armWorry`, `_disarmWorry` e la guardia
"durante il pensa solo `worried`": l'attesa lunga ha già la sua faccia, e non
serve un timer per darle un umore che non esiste più.

**F6 — `angry` prende la lettera C.** Il prompt resta in prima persona e passa
da cinque lettere a quattro: `A` felice o fiera, `B` triste o dispiaciuta,
`C` arrabbiata o infastidita, `D` niente in particolare. Nota: l'arrabbiatura
per la *propria* risposta è un sentimento strano, e la si aspetta rara — la
distribuzione dei verdetti va rimisurata su una settimana (v. incognite).

**F7 — L'espressione la decide lo stato JS, non le classi CSS.** Oggi
`_moodPose` legge `classList.contains('thinking')`; le classi servono alle
animazioni e non sono la fonte di verità dello stato dell'agente. Dopo:
l'espressione esce da `_agentState` + `_mood`, e dalle classi si leggono solo
`out` e `flying`, che sono fatti di posizione.

**F8 — Un wrapper `.jenny-art-stack`, con lo specchio sul wrapper e il respiro
sulle `img`.** Le due immagini vanno specchiate come un solo disegno, e
`transform` non si compone con sé stesso sullo stesso elemento: mettendo
`scaleX(-1)` sul wrapper e lasciando `jenny-bob`/`jenny-wobble` sulle `img`, i
due transform stanno su elementi diversi e si compongono. Effetto collaterale
voluto: **corregge il difetto esistente** dello specchio annullato dal respiro.
Le due `img` restano due istanze della stessa animazione: partono nello stesso
ricalcolo (la classe `.out` arriva una volta) e per tenerle allineate la faccia
non va **mai** nascosta con `display:none` — solo `visibility`.

**F9 — Il bianco/nero si ritira, e con lui `poseUrl`.** Decisione dell'utente,
e la strada che semplifica: i gemelli line-art delle 9 famiglie nuove non
esistono, e disegnarli costerebbe più di quanto la variante rendesse. Quindi
**una sola variante per posa**, col nome piano (`jenny-idle.webp`), e il
suffisso `-color` sparisce dai file e dai path — un suffisso che distingue da
niente è una bugia che resta nel codice per anni. Conseguenze, tutte in
riduzione:

- `shared/mascot.js`: via `poseUrl`, `mascotColor`, `setMascotColor`,
  `COLOR_KEY`, e `color` dal detail di `mascotchange`. La chiave morta in
  `localStorage` si ripulisce come già si fa con `LEGACY_SIDE_KEY` — chi aveva
  scelto il B/N si ritrova a colori, che è l'unica cosa che resta.
- `mobile-jenny.js`: ogni `poseUrl(x)` diventa `x`, e il ciclo di
  `_applyMascotPrefs` che ricablava le `img` del volo **sparisce del tutto**:
  esisteva solo per lo switch di variante.
- `mobile-settings.js` + i18n `it`/`en`: via la riga "Mascotte a colori".
- 15 webp B/N cancellati, 15 rinominati al nome piano; sorgenti `<stem>.PNG`
  line-art e `icon_color.png` cancellati (recuperabili dalla storia: il commit
  li nomina). L'icona app resta line-art: `gen_icons.py` legge `icon.png` e non
  c'entra con la mascotte.

**F10 — Si esporta solo ciò che si può vedere.** Le tre bocche alternative
degli umori (`happy_talk`, `sad_talk`, `angry_talk`) in questo giro sono
irraggiungibili: l'umore si azzera all'inizio del turno (`_setAgentState` →
`_clearMood`), quindi Jenny non è mai felice *mentre* parla. Restano sorgenti
in `image_source/`, fuori dalla tabella del generatore e fuori dal manifest —
`test_ui_active_files_on_disk_are_in_manifest` non tollera asset orfani, e un
webp che nessuno può mostrare è esattamente ciò che marcisce.

**F11 — Lo standby si toglie.** `MOOD_STANDBY` sparisce dal client e
`agents.defaults.mascotMood` torna a `true`: la ragione dello spegnimento era
l'assenza dell'arte, e l'arte c'è.

**F12 — Il parlato espressivo resta fuori, e va bene così** (deciso
dall'utente). Non è più un problema di disegno — le tre bocche ci sono — ma di
**tempo**: la lettera arriva ~0,5 s *dopo* `turn_end`, cioè quando il parlato è
finito. Servirebbe una classificazione a metà flusso, cioè una seconda
richiesta per turno e un modello di costo diverso. Registrato, non pianificato.

**F13 — Delle pose cotte non si cancella nessuna.** Le 15 restano (rinominate,
v. F9): le usano il docked, il volo e l'onboarding. Il ritorno indietro sul
livello faccia è spegnere una classe, non ripristinare degli asset.

---

## I nomi

Sorgenti in `android/image_source/`, convenzione inglese come il resto:

| sorgente artista | importato come | esportato |
|---|---|---|
| `dritta_idle.PNG` | `body_front_idle.PNG` | `jenny-body-front-idle.webp` |
| `dritta_hand_up.PNG` | `body_front_hand.PNG` | `jenny-body-front-hand.webp` |
| `pensa.PNG` | `body_front_think.PNG` | `jenny-body-front-think.webp` |
| `faccia_dritta_normal.PNG` | `face_front_normal.PNG` | `jenny-face-front-normal.webp` |
| `faccia_dritta_talk.PNG` | `face_front_normal_talk.PNG` | `jenny-face-front-normal-talk.webp` |
| `thinking_dritta.PNG` | `face_front_thinking.PNG` | `jenny-face-front-thinking.webp` |
| `faccia_dritta_happy.PNG` | `face_front_happy.PNG` | `jenny-face-front-happy.webp` |
| `faccia_dritta_sad.PNG` | `face_front_sad.PNG` | `jenny-face-front-sad.webp` |
| `faccia_dritta_angry.PNG` | `face_front_angry.PNG` | `jenny-face-front-angry.webp` |

In riserva, importati e **non** esportati: `face_front_{happy,sad,angry}_talk`
(parlato espressivo, F12), i 7 `face_side_*` e i corpi `body_side_idle`,
`body_side_hand`, `body_front_wave1`, `body_front_wave2` (attenzione:
`dritta_wave1` è il corpo di `hello2`, quindi in import si incrociano).

Due nomi da correggere all'import: `faccia_dritta_happy_talk .PNG` ha uno
spazio prima dell'estensione, `faccia_diagonaly_angry_talk.PNG` dice
*diagonaly*.

Le 15 pose cotte perdono il suffisso: sorgente `idle_color.PNG` → `idle.PNG`,
asset `jenny-idle-color.webp` → `jenny-idle.webp` (F9).

---

## La macchina, dopo

Il corpo:

| stato | corpo |
|---|---|
| idle, o con un umore addosso | `body_front_idle` |
| pensa | `body_front_think` |
| parla | alterna `body_front_idle` / `body_front_hand` ogni `TALK_ANIM_SWITCH_MS` |

La faccia, in precedenza stretta (la prima che si applica vince):

1. `flying` → nessuna faccia (l'arte del volo è cotta, il wrapper è nascosto);
2. non `out` → nessuna faccia (arte cotta del docked, comportamento di oggi);
3. `_agentState === 'thinking'` → `face_front_thinking`;
4. `_agentState === 'talking'` → `face_front_normal` / `_talk`, alternate
   ogni `MOUTH_FRAME_MS`;
5. un umore vivo (entro `MOOD_HOLD_MS`) → `face_front_<mood>`;
6. altrimenti → `face_front_normal`.

La classe `layered` sul `.jenny-duo` è quindi solo `out && !flying`: senza il
B/N non ha più la terza condizione.

Un effetto collaterale gradito: durante il parlato cambia solo la faccia
(~6 kB) invece di un corpo intero (~22 kB) ogni 260 ms.

---

## I passi

Un commit per passo, sempre con `-s` (DCO: la CI lo controlla al primo push).
Verifica prima di ogni commit:

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && python3 -m pytest -q
```

### Passo 0 — il ramo
`git switch -c feat/mascot-faces main`, con l'albero pulito.

### Passo 1 — il bianco/nero si ritira *(un commit, prima di tutto)*
Va prima dell'arte nuova: dopo, i nomi si scrivono una volta sola.
1. Asset: cancellati i 15 `jenny-*.webp` B/N, rinominati i 15 `-color` al nome
   piano; `_UI_MANIFEST` da 30 voci a 15.
2. Sorgenti: cancellati i 15 `<stem>.PNG` line-art e `icon_color.png`;
   rinominati i 15 `<stem>_color.PNG` a `<stem>.PNG`.
3. `gen_pose_webp.py`: un export per posa, docstring senza le due varianti.
4. `shared/mascot.js`, `mobile-jenny.js`, `mobile-onboarding.js`,
   `mobile-settings.js`, i18n `it`/`en`: v. F9.
5. Test: la parità i18n copre le due lingue; aggiungere che `localStorage` con
   `jenny-mascotte-color = '0'` **non** produce più path B/N e che la chiave si
   ripulisce.
6. `docs/using/themes-mascot.md` (tabella a due righe, "Three preferences" →
   due), `docs/reference/settings.md` (via la riga *Color mascot*, e "the two
   mascot options" diventa singolare), `android/image_source/README.md` +
   `COLORARE_LE_POSE.md` (una variante per posa).
7. Verifica verde; commit `-s`.

### Passo 2 — i sorgenti nuovi e la pipeline *(un commit, nessun cambio a runtime)*
1. I 23 PNG da `JENNY_IMG_NEW/` a `android/image_source/` coi nomi della
   tabella; `JENNY_IMG_NEW/` via.
2. `gen_pose_webp.py`: una seconda tabella `LAYERS` per le 9 famiglie a livelli
   (stessa funzione di export, cambia solo chi la chiama).
3. `_UI_MANIFEST`: +9 voci.
4. `README.md` e `COLORARE_LE_POSE.md`: il modello a due livelli, la tabella
   dei nomi, la riserva e il perché, e la ricetta di derivazione della coppia
   neutra diagonale.
5. Test nuovo `tests/webui/test_mascot_layer_sources.py`, con
   `pytest.importorskip("PIL")` come i test client fanno con `node`:
   - i 9 webp esistono, sono 768² e stanno nel manifest;
   - **la registrazione**: `body_front_idle` + `face_front_normal_talk` ricompone
     `talk_1b`, `body_front_think` + `face_front_thinking` ricompone `think`,
     `body_front_hand` + `face_front_normal_talk` ricompone `talk_1a` — a meno
     di una soglia di antialias. È la guardia che nessuno screenshot dà: se un
     domani un sorgente arriva spostato o riscalato, questo test lo dice al
     posto dell'occhio.

### Passo 3 — il livello faccia nel client *(un commit, umore ancora fermo)*
1. CSS: `.jenny-art-stack` (`position: relative`), `img.jenny-face` assoluta a
   `0,0`, lo specchio `side-left` e il `visibility:hidden` del volo spostati
   dall'`img` al wrapper, `.jenny-duo:not(.layered) img.jenny-face
   { visibility: hidden }`. Bob e wobble **non si toccano**.
2. `_buildDom`: il wrapper e la seconda `img`; il layer del volo resta
   fratello del wrapper.
3. `BODY`/`FACE`, `_setBody`/`_setFace` con la guardia su `getAttribute('src')`
   che ha `_setSrc` oggi, e la classe `layered`.
4. `_syncArt` e `_talkTick` riscritti sulla precedenza qui sopra, con il ramo
   cotto invariato quando `layered` è falsa.
5. Preload: 3 corpi + 6 facce.
6. Test: il ramo cotto non cambia (i test esistenti passano invariati) e la
   precedenza della faccia, punto per punto.

### Passo 4 — il vocabolario *(un commit, backend)*
`MOODS` a quattro, `_LETTER_TO_MOOD` A–D, la riga `C` del prompt,
`mascot_mood` di default `True`. Test di `tests/session/` e `tests/config/`
aggiornati; il frame non cambia forma, quindi `tests/channels/` resta.

### Passo 5 — l'umore sulle facce *(un commit, client)*
Via `MOOD_ART`, `MOOD_STANDBY`, `MOOD_WORRY_AFTER_MS`, `_armWorry`,
`_disarmWorry` e la guardia `worried` in `_moodPose`. L'errore continua a fare
`_applyMood('sad')`, che ora ha una faccia vera. Il contratto in
`tests/webui/test_mascot_mood_client.py` diventa `MOODS` meno `neutral` ⊆
chiavi di `FACE`, più le vecchie regole (scadenza, turno stantio, un solo
turno) che non cambiano.

### Passo 6 — documentazione
`docs/using/themes-mascot.md` (le quattro espressioni, il modello a due
livelli; via il paragrafo dello standby), `docs/reference/configuration.md`
(`mascotMood` torna `true`), `docs/reference/websocket.md` (le etichette del
frame). E le note di superamento su `mascot-mood-plan.md` (D7, D13, Standby) e
sulla sua checklist (il passo 7 punta qui). Nessun file di `docs/` spostato o
rinominato.

### Passo 7 — sul telefono
Albero pulito (Chaquopy impacchetta l'albero, non HEAD), `installDebug`, e poi:
1. le quattro facce a schermo, screenshot: idle, pensa, parlato, e un umore;
2. il parlato: la bocca sbatte e il corpo cambia gesto ogni 2,6 s, **senza
   che la faccia si scolli dal corpo** — è il rischio vero del wrapper;
3. lo specchio: da sinistra e out Jenny guarda *dentro* lo schermo (il difetto
   di F8 corretto), alle tre taglie;
4. docked: arte di oggi, identica — confronto con uno screenshot pre-modifica;
5. il volo: nessuna faccia appesa addosso alla pegman;
6. **niente 404 in logcat** dopo la rinomina degli asset, e su un'app che
   *prima* aveva il B/N scelto (`localStorage` sporco);
7. Impostazioni → Personalizzazione: la riga dei colori non c'è più e le altre
   due funzionano ancora;
8. un turno finito in errore → faccia triste;
9. un turno da Telegram fa reagire la mascotte nella WebUI;
10. l'onboarding, con `workspace/` azzerato: Jenny ha la faccia.

### Passo 8 — l'interruttore *(solo dopo il 7)*
La riga "Espressioni" in Personalizzazione → Mascotte, via `store.mutate()`,
i18n `it`/`en`, `docs/reference/settings.md`, test di route. Ora ha senso:
c'è qualcosa da spegnere.

---

## Le misure sul telefono (08/09/2026, Titan 2, APK release dal ramo)

- **Il frame arriva** a **+0,51 s** e **+0,75 s** da `turn_end`, con il
  `turn_id` che combacia. Su cinque turni guidati dal client WS i verdetti
  sono stati quattro `happy` e nessun `neutral`.
- **Tutti gli asset esistono**: i 19 path che il client sa nominare
  (`mobile-jenny.js` + `mobile-onboarding.js`) rispondono **200** dal gateway
  del telefono, e in logcat non c'è nessun 404 di asset. La rinomina del
  passo 1 non ha lasciato niente per strada.
- **I quattro stati si vedono**: riposo, pensa (corpo del pensa + faccia del
  pensa), parlato, felice — e la faccia felice regge anche a +1,5 s dal frame.
- **Le due sveglie del parlato** sono separate e visibili: 23 scatti su 9,2 s
  di parlato, la bocca alterna e il gesto alterna. Ma **solo dopo una
  correzione**: v. sotto.
- **La faccia non si scolla dal corpo** in nessuno dei 23 scatti, né a
  mascotte specchiata. Il wrapper regge (F8), e con lui lo specchio: out a
  sinistra la ciocca e il logo sono ribaltati, cioè guarda dentro lo schermo.
  Provata la taglia Small, non tutte e tre.
- **Il volo non porta facce addosso**: la pegman è un'immagine sola,
  all'atterraggio si torna ai livelli senza lampi di posa cotta.
- **Il blocco Mascotte in Impostazioni ha due righe.**

### Il difetto che il telefono ha trovato

Cercando il braccio alzato non l'ho trovato: 23 scatti su 9 secondi di
parlato, sempre a braccia giù. Il motivo non era nei livelli. Un flusso lungo
manda `talking` a *ogni* delta e `_setAgentState` li scartava tutti tranne il
primo, perché lo stato non cambiava: `lastTextAt` non si aggiornava più, dopo
un secondo l'animatore decideva che il flusso era muto, tornava al pensa in
mezzo alla frase e — ripartendo — rimetteva `animIdx` a zero. Il gesto non
cambiava **mai**, e `BODY.hand` era un asset che nessuno poteva vedere.

È un difetto **di prima dei livelli**: con l'arte vecchia faceva alternare la
posa fra parlato e pensa circa una volta al secondo, che si leggeva come
vivacità. Corretto (un segnale di parlato aggiorna `lastTextAt` prima della
guardia sullo stato invariato), con un test che fallisce sull'asserzione — non
sulla sintassi — se la riga si toglie.

## Incognite aperte

- **La distribuzione dei verdetti a quattro lettere.** Con cinque, un giorno
  d'uso reale ha dato 4 felice + 1 neutro su 6. Con `angry` al posto di
  `worried`/`surprised` va rimisurata su una settimana: se `angry` non
  compare mai, la lettera C è un costo senza resa e si può tagliare.
- **Se la faccia si scolla dal corpo** durante il respiro o il parlato. Le due
  animazioni partono insieme in teoria (F8); si vede solo sul telefono.
- **Se il sorriso aperto a riposo funziona.** `happy` è un sorrisone: 12
  secondi di sorrisone dopo ogni risposta positiva potrebbero essere troppi.
  Se lo sono, la leva è `MOOD_HOLD_MS`, non l'arte.
- **La coppia neutra diagonale**, se un giorno si volesse l'umore anche da
  docked: derivabile, ricetta e verifica nel rilievo qui sopra.
- **Il livello 0 sull'errore forse non scatta mai.** Provato a spegnere le
  radio del telefono: il fallimento del provider torna come **testo in chat**
  («Error calling LLM: All connection attempts failed») e non come frame
  `error`, quindi `_applyMood('sad')` non viene chiamato e la faccia resta
  normale. La faccia triste ha quindi una sola strada certa, il verdetto `B`
  del modello. Se la si vuole anche sul fallimento, il gancio è quel messaggio
  in banda, non l'evento — misurato, non risolto.

## Fuori da questo giro

- Il **parlato espressivo** (F12): serve la classificazione a metà flusso.
- L'**onboarding a livelli**: i corpi `wave1`/`wave2` senza faccia ci sono, e
  Jenny potrebbe sorridere al benvenuto. Nessuno l'ha chiesto.
- L'**umore da docked** e il **gesto docked** (`body_side_hand`): l'arte c'è,
  la misura dice che non si legge.
