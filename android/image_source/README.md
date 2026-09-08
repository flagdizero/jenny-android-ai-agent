# image_source/

Sorgenti "grezze" (disegnate dall'artista) dell'arte di Jenny e i due script
che le esportano verso il resto del repo. Niente in questa cartella viene
letto a runtime: è solo il punto di partenza della build degli asset.

## Cosa c'è

- `icon.png` — sorgente dell'icona app (viso + linee, sfondo trasparente).
- `jenny-side.PNG`, `jenny-side-talk.PNG`, `jenny-hang.PNG`, `jenny-fall.PNG`,
  `jenny-ground.PNG`, `jenny-walk1.PNG`, `jenny-walk2.PNG`, `hello1/2.PNG`,
  `idle.PNG`, `think.PNG`, `talk_1a/1b/2a/2b.PNG` — pose della mascotte,
  canvas 3000×3000, tutte cablate in `gen_pose_webp.py`.
  **Una variante per posa**, a colori. Fino all'08/09/2026 ogni posa aveva
  un gemello `<stem>_color.PNG` e il client rimappava il suffisso `-color` su
  una preferenza dell'utente; la preferenza è stata ritirata e la line-art coi
  gemelli B/N è uscita dal repo (recuperabile dalla storia — v.
  `.agent/mascot-faces-plan.md`, F9). L'icona app resta line-art: `icon.png` è
  un sorgente a sé e non c'entra con le pose.
- `body_*.PNG` / `face_*.PNG` — l'arte **a due livelli**: corpi senza faccia e
  facce da sola, sullo stesso canvas 3000×3000 delle pose. Si compongono a
  runtime, due `<img>` impilate, e la registrazione è tutta sul canvas: qui
  nessuno scala, ricentra o compone niente. V. la sezione *Due livelli* sotto.
- `gen_icons.py` — genera le icone Android da `icon.png`.
- `gen_pose_webp.py` — esporta le pose della mascotte in webp per la WebUI.

Convenzione dei nomi `talk_*`: il **numero è la bocca** (1=aperta, 2=chiusa),
la **lettera è la posa** (a=mano alzata, b=braccia giù). Le coppie di
animazione a runtime sono quindi per posa: `talk_2a↔talk_1a` e
`talk_2b↔talk_1b`.

## Due livelli: corpo + faccia

Le 15 pose `FILES` hanno la faccia disegnata dentro ("cotte"). Accanto, dal
settembre 2026, c'è una seconda famiglia di sorgenti in cui **il corpo è senza
faccia** e la faccia è un livello a sé: a mascotte intera la companion le
sovrappone, così l'espressione è ortogonale al gesto e "triste mentre pensa"
non è un disegno in più ma una composizione. Il ragionamento completo sta in
[`.agent/mascot-faces-plan.md`](../../.agent/mascot-faces-plan.md).

**Orientamento.** `front` è la posa dritta (mascotte intera, `out`), `side` è
quella diagonale che sporge dal bordo. Le facce di un orientamento valgono solo
per i corpi dello stesso.

**Il nome dice cosa è, non con cosa si accoppia.** `face_front_happy` è la
faccia **di riposo** di quell'espressione — per `happy` è un sorriso a bocca
aperta, ed è giusto così: un chibi felice riposa sorridendo. `face_front_happy_talk`
è **l'altra bocca**. All'animatore del parlato serve la coppia, e l'ordine non
si vede.

| Cablati in `LAYERS` (esportati) | | |
|---|---|---|
| corpi | `body_front_idle`, `body_front_hand`, `body_front_think` | riposo, gesto del parlato, pensa |
| facce | `face_front_normal`, `face_front_normal_talk` | la coppia del parlato |
| | `face_front_thinking` | mentre aspetta la risposta |
| | `face_front_happy`, `face_front_sad`, `face_front_angry` | le tre reazioni |

In riserva, **importati e non esportati** (14): `face_front_{happy,sad,angry}_talk`
(le bocche alternative degli umori: servono al parlato espressivo, che non c'è
ancora), i sette `face_side_*` e i corpi `body_side_idle`, `body_side_hand`,
`body_front_wave1`, `body_front_wave2`. Non sono webp e non sono nel manifest:
un asset che nessun ramo del client può mostrare marcisce. Quando serviranno,
si aggiunge la riga in `LAYERS` e in `_UI_MANIFEST`.

Attenzione ai nomi del saluto: `body_front_wave1` è il corpo di `hello1` e
`body_front_wave2` quello di `hello2` — nei file dell'artista arrivavano
incrociati, e all'import si sono raddrizzati.

**Manca la coppia neutra `side`** (`face_side_normal` e il suo `_talk`): non
serve, perché da docked la faccia non si legge e l'umore lì non si mostra. Se
un giorno servisse, **si deriva dall'arte cotta** invece di disegnarla:
`body_side_idle` è `jenny-side` senza faccia, quindi basta tenere di
`jenny-side.PNG` i pixel che si discostano dal corpo e azzerare l'alfa
altrove. Verificato: ricomposta torna con uno scarto massimo di 15 su 13 pixel
di frangia (bocca chiusa) e di 2 su nessun pixel (bocca aperta).

**Il test che tiene la registrazione.** `tests/webui/test_mascot_layer_sources.py`
pretende che `body_front_idle + face_front_normal_talk` ricomponga `talk_1b` e
`body_front_think + face_front_thinking` ricomponga `think` **al pixel** (soglia
8/255, cioè frangia di antialias), che i riquadri d'inchiostro dei corpi
coincidano con quelli delle pose da cui vengono, e che ogni faccia cada nella
finestra del suo orientamento. Uno spostamento di un pixel in export lo sfonda
di un ordine di grandezza — è la guardia che nessuno screenshot dà.

## Regola generale: chi decide cosa

La scala, il crop e l'allineamento sono **responsabilità dell'artista sul
canvas**, mai dello script o del codice a runtime. Gli script qui dentro
fanno solo operazioni meccaniche (resize uniforme, crop al bounding box,
composizione su sfondo); non raddrizzano, non ricentrano e non correggono
proporzioni tra una posa e l'altra. Se una posa sembra fuori scala rispetto
alle altre, il problema è nel sorgente PNG, non nello script.

## 1. Icona app — `icon.png` → `gen_icons.py`

Regola del sorgente: viso bianco opaco con line-art nera, sfondo trasparente.
Su sfondo nero l'arte va bene così com'è (il bianco fluttua, i tratti neri
restano sopra), quindi le icone grandi **non invertono mai i colori**.

Lo script:
1. Ritaglia `icon.png` al bounding box del contenuto non trasparente.
2. Genera tre famiglie di output sotto `android/app/src/main/res/`:
   - **A. Adaptive foreground** (`mipmap-<dpi>/ic_launcher_foreground.png`):
     mascotte scalata al 54% del canvas — valore scelto perché la maschera
     circolare del launcher misura ~76% del canvas e la sua sagoma quadrata
     inscritta limita la dimensione massima della mascotte a quella cifra;
     sotto questa soglia niente viene tagliato dalla maschera.
   - **B. Silhouette status bar** (`drawable-<dpi>/ic_stat_jenny.png`):
     bianco pieno con i tratti scuri "bucati" a trasparente. Le forme sottili
     (contorno, capelli, ciglia) vengono rimosse con un'apertura morfologica
     (erode+dilate, kernel 9) che invece preserva le masse spesse (occhi,
     bocca) — quindi la silhouette non è un semplice threshold, è
     "solo le macchie scure abbastanza larghe".
   - **C. Notification large icon** (`drawable-nodpi/ic_notification_large.png`):
     sfondo nero pieno + mascotte all'80% del canvas.
3. Nessuna icona raster legacy (`ic_launcher.png`/`ic_launcher_round.png`):
   `minSdk 26` usa sempre l'adaptive icon, quindi le legacy aggiungerebbero
   solo un secondo rendering (ritagliato quadrato) che confligge con quello
   mascherato. L'adaptive icon è l'unica fonte di verità.

Rilancia lo script dopo ogni modifica a `icon.png` o alle costanti di tuning
(`FOREGROUND`, `STAT`, le frazioni 0.54/0.80/0.90): è idempotente.

## 2. Pose della mascotte — `gen_pose_webp.py`

Regola del canvas: tutti i sorgenti sono **canvas quadrati 3000×3000**,
disegnati dall'artista già alla scala giusta e coerenti tra loro (stesso
personaggio, stessa dimensione, teste allineate sullo stesso canvas). Lo
script **non scala, non ritaglia e non normalizza nulla**: ogni webp è il
quadrato intero ridotto a 768×768 (`SIZE`) con lo stesso fattore per tutti,
qualità 80. La scala relativa fra le pose non viene mai toccata a valle.

A runtime (`jenny/templates/ui/assets/mobile-jenny.js`) il layer di volo
`.jenny-fly` coincide esattamente col box della mascotte — tutte le img sono
`width:100%` dello stesso quadrato condiviso, quindi nessuna scala o offset
viene calcolata lì. L'unica costante calcolata a **build time** in
`gen_pose_webp.py` è il pivot della posa appesa (`HAND_PIVOT`): la punta
della manica alzata ("la mano") su `jenny-hang.png`, misurata a mano perché
la sagoma in quella zona è ambigua (le ciocche superano la manica in
altezza). Lo script stampa `PIVOT_X`/`PIVOT_Y` come frazione del canvas: quei
due valori vanno copiati a mano nelle costanti `PIVOT_X`/`PIVOT_Y` di
`mobile-jenny.js` se `HAND_PIVOT` cambia.

### Regole di utilizzo delle pose (runtime, non generazione)

Gli stati "in posizione" (`mobile-jenny.js`):

- **idle**: ferma e visibile (all'angolo in chat, o out in overlay).
- **think**: sta aspettando la risposta (minichat, o chat principale con lei
  out; da docked il "pensa" si salta).
- **talk1a/1b/2a/2b**: parlato animato mentre la risposta arriva — la bocca
  sbatte (chiusa↔aperta) a posa fissa, la posa (mano alzata / braccia giù)
  cambia ogni `TALK_ANIM_SWITCH_MS`.
- **side / side-talk**: riposo sul bordo, metà fuori schermo; da lì il
  parlato è la versione semplificata `side↔side-talk` (posa unica).

L'**umore** (frame `mascot_mood`, v. `MOOD_ART` in `mobile-jenny.js`) al
momento **non ha sorgenti propri**: ogni etichetta (`happy`, `sad`, `worried`,
`surprised`) prende in prestito una posa qui sopra. Quando arriveranno le pose
dedicate andranno cablate come tutte le altre (`FILES`, `_UI_MANIFEST`, `ART`) e
`MOOD_ART` va ripuntata; fino ad allora niente da generare per l'umore.

Le pose `hang`/`fall`/`ground`/`walk1`/`walk2` sono il "volo Pegman" quando
la mascotte viene trascinata:

- **hang**: pendolo appeso al pivot mentre è tenuta — ruota di `-θ` attorno
  al pivot, **mai flippata** (l'arte resta nel suo verso originale, solo la
  caduta si specchia).
- **fall**: caduta dopo il rilascio — posa dritta, **flip** in base al verso
  del moto orizzontale (isteresi: sotto `DIR_MIN` px/s il facing non cambia,
  anti-jitter).
- **ground**: atterrata (rimbalzo + pausa "rialzati"), stessa regola di flip
  di `fall` congelata al momento del contatto.
- **walk1**/**walk2**: alternate ogni `WALK_FRAME_MS` (500ms) durante il
  rientro verso il bordo.
- **hello1**/**hello2**: saluto a due frame usato dalla mini Jenny
  dell'onboarding (`mobile-onboarding.js`): cade dall'alto (`fall`), atterra
  stordita (`ground`), poi alterna hello1/hello2 e si ferma in `idle`.

### Output

`FILES` mappa nome-posa → PNG sorgente e scrive **15 webp** cotti in
`jenny/templates/ui/assets/`, uno per posa:
`jenny-{side,side-talk,hang,fall,ground,walk1,walk2,hello1,hello2,idle,think,
talk1a,talk1b,talk2a,talk2b}.webp`. `LAYERS` ne aggiunge **9 a due livelli**,
`jenny-<stem coi trattini>.webp` (per esempio `body_front_idle.PNG` →
`jenny-body-front-idle.webp`). Ogni sorgente deve essere esattamente 3000×3000
(assert esplicito) o lo script si ferma.

## Rigenerare

Per il flusso pratico "sostituisco un sorgente → rigenero → carico sul
telefono" (con tabella nomi file e checklist) vedi
[`SOSTITUIRE_UNA_POSA.md`](./SOSTITUIRE_UNA_POSA.md).

```bash
# dalla cartella android/image_source/
python3 gen_icons.py        # -> ../app/src/main/res/**
python3 gen_pose_webp.py    # -> ../../jenny/templates/ui/assets/*.webp
```

**Regola del manifest**: ogni webp nuovo va aggiunto anche a `_UI_MANIFEST`
in `jenny/utils/android_assets.py`. Su Android gli asset della WebUI vengono
estratti dall'APK seguendo quella lista statica: un file non elencato esiste
nel bundle ma non arriva mai in `workspace/ui/` sul device (la `<img>` fa
404 in silenzio).

Dopo aver rigenerato le pose (o le icone), **serve una build/installazione
dell'APK** per vederle sul dispositivo: Chaquopy ri-estrae il bundle
`jenny/templates/ui` dentro l'APK a ogni installazione, quindi un semplice
riavvio dell'app non basta.

```bash
./gradlew app:installDebug
```

## Stato attuale

Tutta l'arte in cartella è cablata (mappata in `FILES` e referenziata dalla
WebUI). Nota: `idle.PNG` è byte-identica a `talk_2b.PNG` — scelta voluta, la
posa di riposo coincide col frame "braccia giù, bocca chiusa" del parlato.
Per cambiarla basta sostituire `idle.PNG`, rilanciare `gen_pose_webp.py` e
fare `./gradlew app:installDebug` — nessun'altra modifica.

Se si cablano nuovi sorgenti, aggiornare `FILES` (o `LAYERS`) qui e i
riferimenti runtime (`ART`/`TALK_ANIMS`/`FLY_POSES`/`BODY`/`FACE` in
`mobile-jenny.js`, `JENNY_POSES` in `mobile-onboarding.js`).
