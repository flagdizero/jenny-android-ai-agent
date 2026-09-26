# Le pagine in alto

Piano del 23/09/2026. Segue `pagine-dal-posto-plan.md`. Mockup:
un mockup fuori dal repo (pagina «Quattro pagine»), descritto qui sotto.

## Checklist

- [x] P0 piano nel repo
- [x] P1 `ordine`: schema, rotte, client API, test, docs
- [x] P2 modello di casa-pagine: la chat ovunque, pannelli fissi statici
- [x] P3 la fila al posto della testa, via i pallini
- [x] P4 pagina App: launcher incorporato, via il cassetto, pastiglia wiki nella barra, overlay app
- [x] P5 pagina Quaderni
- [x] P6 pagina Impostazioni
- [x] P7 modalità ordina
- [x] P8 pulizia

  P2–P7 sono finiti in **un commit solo** (`361a129`), non sei: il modello della
  pista, la fila e le tre pagine fisse si toccano negli stessi file e negli
  stessi banchi, e a meta' la casa non si reggeva (tre pagine vuote, o una testa
  senza le sue porte). I passi restano come elenco di cosa c'e' dentro.
- [x] P9 giro sul Titan


## Context

Oggi la casa ha tre porte nascoste e un indicatore che non si capisce:

- la tendina «Jenny ⌄»;
- l'ingranaggio verso «Tu e Jenny»;
- il bottone del cassetto nella barra dove scrivi;
- i pallini in basso, che sul Titan 2 (590×566) si prendono 26 px di altezza su ogni pagina.

L'utente ha scelto una sola struttura (un mockup fuori dal repo, pagina «Quattro pagine»; quel che conta e' scritto qui):

- **L'intestazione è la fila dei nomi delle pagine.** ~~Quella dove sei è grande, in serif; le altre sono piccole, maiuscole e spaziate.~~ Dal 26/09/2026 sono tutte piccole, maiuscole e spaziate, e quella dove sei è chiara con la riga d'accento sotto (v. «Ritocco del 26/09/2026» in fondo). Un tocco su un nome ti porta lì, e il gesto di lato resta.
- **Quattro pagine di base: App · Jenny · Quaderni · Impostazioni.** App è il cassetto, Quaderni è la tendina, Impostazioni è «Tu e Jenny». Si parte da Jenny.
- **L'utente decide le pagine, come gli spazi del Mac:**
  - le aggiunge dalla pressione lunga «Metti come pagina», che esiste già;
  - le **sposta tutte**, comprese Jenny e le tre di base: tiene premuto un nome, le pagine diventano pastiglie e le trascina;
  - **toglie** con la × solo quelle aggiunte. Le tre di base si spostano ma non si tolgono.
- **L'ordine cambia solo per mano sua.** Indietro riporta sempre a Jenny.
- **Decisioni prese con l'utente:**
  - dentro un quaderno, al posto di «Jenny» c'è il nome del quaderno col suo pallino, tagliato con «…» se è lungo;
  - la pastiglia delle pagine wiki del quaderno («📖 12») va nella barra dove scrivi, al posto del bottone del cassetto, e compare solo dentro un quaderno.

**Questa decisione ribalta alcune scelte registrate. Ognuna va riscritta, non aggirata:**

- «la chat non è in elenco, non si sposta» (`schema.py` CasaConfig, `configuration.md:402-414`);
- «il cassetto non è una pagina, il suo bottone sta accanto a dove scrivi» (`schema.py:496`, `test_launcher_sheet_contract.py:716`);
- «le stanze non sono pagine» (22-23/09). Quella regola però parlava di stanze **appendibili**, prese in prestito a runtime. Qui Impostazioni è una pagina **fissa, disegnata nell'HTML**: il prestito, che era il punto fragile, non torna.

**Una cosa della lezione sul cassetto resta vera:** l'ingresso deve vedersi. «APP» nella fila si vede sempre, quindi la regola è rispettata.

## L'approccio

### 1. Dati: un campo `ordine` accanto a `schermate`, non nuove specie

In `jenny/config/schema.py`, dentro `CasaConfig`:

- **`ordine: list[str]`.** Contiene gli id delle pagine nell'ordine visibile.
  - Id riservati: `chat`, `app`, `quaderni`, `impostazioni`.
  - Gli altri sono gli `id` delle `schermate`.
- **`schermate` non cambia.** Così `stacca_pagine_di` e `rinomina_pagine_di` (`jenny/webui/casa_routes.py:36,70`) restano uguali, e `SPECIE_SCHERMATA` resta `("app","conversazione")`.
- **Un validatore normalizza e non rifiuta mai**, per la lezione di `_stanze_uscite`: un errore di validazione costa l'intero `config.json`.
  - Toglie gli id sconosciuti e i doppioni.
  - Aggiunge le fisse mancanti nell'ordine di default.
  - Mette le schermate che mancano subito dopo `chat`, che è dove stanno oggi.
- **Default e migrazione:** `[app, chat, <schermate…>, quaderni, impostazioni]`. Chi oggi ha `[todo]` si ritrova `App · Jenny · Todo · Quaderni · Impostazioni`.
- **Il tetto `MAX_SCHERMATE = 8` vale solo per le aggiunte.** Il commento va aggiornato: il motivo non sono più i pallini ma la larghezza della fila.

In `casa_routes.py`:

- La GET risponde anche con `ordine`.
- `/set?v=` accetta sia l'array di oggi sia `{schermate, ordine}`.
- La validazione **severa** sta nella rotta, prima di `store.mutate`, con 400 su un ordine malformato. La normalizzazione tollerante sta nello schema, per i file scritti a mano.

`stacca_pagine_di` non deve toccare `ordine`: l'id che resta orfano lo toglie il validatore.

### 2. Il modello in `casa-pagine.js`: la chat può stare ovunque

- **Una sola lista ordinata**, `this._pagine = [{id, kind}]`. `kind` è una fra `chat`, `app-cassetto`, `quaderni`, `impostazioni`, `app`, `conversazione`.
- **Via ogni ipotesi «0 = chat» e «i-1»**, sostituite da `indiceDi(id)` e `this._indiceChat`:
  - dentro il file: `pannelloDi`, `conversazioneDi`, `_accendiSolo`, `_finestraPagina`, la matematica degli indici in `ricarica`, il limite in `salva`, `appendi`, e il `vaiA(0)` di `apriConversazione`;
  - fuori dal file: `goBackOneRoom` (`casa-app.js:702`), `_applyHead` con `_pagina > 0` (:795), e il selettore CSS `:not([data-pagina='0'])` (css:1287).
- **I pannelli fissi sono statici in `index.html`**, come la chat oggi: `data-pagina="app"`, `quaderni`, `impostazioni`.
  - `_disegna` li **riordina** con `appendChild` nell'ordine della lista (spostarli non li distrugge) e ricrea solo i pannelli delle schermate, come fa già.
  - Riordinare succede solo al «Fatto» della modalità ordina. Lì l'unica pagina viva è quella corrente, quindi nessun iframe si ricarica di nascosto.
- **Accendere e spegnere:** ogni pannello fisso ha un gancio che scatta quando diventa la pagina corrente.
  - Impostazioni legge le impostazioni, come fa oggi `openTu`.
  - App fa scorrere l'elenco in cima e prende la tastiera per la ricerca.
- **Il Trasloco della chat** (`casa-trasloco.js`) resta com'è: `conversazioneCasa` diventa la conversazione del pannello `chat`, dovunque sia.
- **Indietro:** da qualunque pagina diversa da `chat` porta a `vaiA(indiceChat)`. All'avvio si parte da `indiceChat`.
- **Via `_pallini` e `#casa-pallini`.** Il posto di «unico segno che i gesti esistono» lo prende la fila.

### 3. La fila: nuovo modulo `casa-fila.js`

- **Mostra i nomi dalla lista.**
  - I nomi delle fisse vengono dall'i18n.
  - Per `chat` c'è «Jenny», o il nome del quaderno col pallino e il taglio con «…», con una `max-width` perché le altre restino visibili.
  - Per le app appese c'è `nomeDi`.
- ~~**Quella attiva è grande.** Il passaggio si anima su `transform: scale` e non su `font-size`, che fa scattare il layout.~~ Superato il 26/09/2026: la voce attiva ha lo stesso carattere e la stessa taglia delle altre (v. in fondo). La voce attiva resta sempre in vista, e l'eccesso sfuma a destra con una `mask-image`.
- **I gesti:**
  - il tocco chiama `vaiA`;
  - la pressione lunga (`shared/longpress.js`, 600 ms, con la guardia `dataset.longpress`) apre la modalità ordina.
- **CSS senza selezione.** Servono `user-select:none; -webkit-touch-callout:none`, per il difetto misurato sul telefono il 23/09: senza, Chromium seleziona il testo e annulla il puntatore.
- **L'intestazione ha due forme:**
  - in `data-view="chat"`, la fila;
  - nelle stanze (`pages`, `reader`, le sottostanze di Impostazioni), la riga di oggi con indietro, titolo, `#casa-talk` e `#casa-edit`. Restano, e `_applyHead` si semplifica nel solo ramo delle stanze.
- **Vanno via** `#casa-who` (titolo + chevron), il kicker, `#casa-door` e `#casa-pages-open` in testa.

### 4. La modalità ordina (dentro `casa-fila.js`)

- **L'intestazione si apre** e mostra pastiglie da 40 px in `flex-wrap`, con la maniglia a puntini, la × solo sulle aggiunte e il bottone «Fatto». Il contenuto sotto si spegne.
- **Il trascinamento** usa pointer events con `setPointerCapture`. Il punto d'inserimento è la pastiglia col centro più vicino, un calcolo in 2D perché le pastiglie vanno a capo. Il buco tratteggiato fa da segnaposto.
- **La × chiama `stacca`**, che esiste già (`casa-pagine.js`).
- **«Fatto» salva e ridisegna.** `salva` manda `{schermate, ordine}` e poi chiama `_disegna`, restando sulla pagina dov'eri.
- **Indietro chiude la modalità senza salvare.** È un livello in più in `_closeOverlays`, messo per primo.

### 5. Le tre pagine fisse

**App.** `LauncherController` (`mobile-launcher.js`) prende una modalità **incorporata**:

- riceve un contenitore;
- salta velo, `inert`, trascinamento per chiudere, `_syncViewport` e `html.launcher-open`;
- `_onKeyDown` lavora solo mentre la pagina App è quella corrente e il fuoco non sta in un campo. Sul Titan, con la tastiera fisica, scrivere filtra le app.

L'officina resta sul foglio, che è il default: il contratto dice che `mobile-launcher.js` non conosce gli id del guscio, e resta vero perché il contenitore glielo passa `casa-app.js`. La pressione lunga sulle righe apre le schede che ci sono già. Il `#launcher-sheet` in `index.html` della casa se ne va, e anche `openLauncher`.

**Quaderni.** `WhoPanel` (`casa-who.js`) prende un contenitore invece di `_ensure()`, e perde `showModal`, `::backdrop` e la posizione presa da `.casa-head`.

- `_pick` non chiude più niente: cambia conversazione e poi fa `vaiA(indiceChat)`.
- La pressione lunga apre `#casa-quaderno-sheet`, come ora.
- `render()` si chiama anche quando la pagina diventa quella corrente, così i conteggi non invecchiano. Questo chiude anche il resto noto del giro scorso.

**Impostazioni.** La `<section id="casa-tu">` entra **nell'HTML** del pannello `impostazioni`: niente prestito a runtime. `CasaTu` la trova per id come oggi.

- `openTu()` diventa `vaiA(indice impostazioni)`.
- Le sottostanze (jenny, model, updates, backup) restano stanze `data-view`, e da lì Indietro torna a `chat` sull'indice di Impostazioni. `BACK_TO['jenny'…]` va aggiornato.
- L'Officina ci si arriva dalla riga `casa-workshop` che c'è già. La scorciatoia con la pressione lunga sull'avatar sparisce: la pressione lunga su un nome ora apre la modalità ordina, e i due gesti non possono condividerla. `workshopHint` perde «oppure tieni premuto l'avatar».

### 6. La barra per scrivere e le app aperte a tutto schermo

- **Via `#casa-drawer`.** Al suo posto c'è `#casa-pages-open`, spostato lì, visibile solo in un quaderno e col conteggio di `_updatePagesCount`.
- **Un difetto da correggere adesso.** Oggi la casa non chiude mai l'`app-frame-overlay` di un'app aperta dal cassetto (`apps-actions.js:176-214`, dove `handleBack` chiama lo `switchMode` dell'officina). Con la pagina App diventa la strada principale, quindi va chiuso per primo in `_closeOverlays`.
- **`FLOOR_NO_COMPOSER` per Jenny** vale su tutte le pagine senza la barra per scrivere, e le tre fisse lo sono.

## I passi (un commit firmato ciascuno, e ognuno lascia l'app funzionante)

- **P0** Il piano nel repo, in `.agent/pagine-in-alto-plan.md`, con la checklist da spuntare e l'esito scritto passo per passo.
- **P1** `ordine`: schema, validatore, rotte, `api-client.js` `getSchermate`/`setSchermate`, i test di config e delle rotte, e `configuration.md`.
- **P2** Il modello di `casa-pagine.js` con le fisse come pannelli statici, per ora vuoti, e il riordino di `_disegna`, con Indietro verso la chat. I pallini restano ancora qui, per poter provare.
- **P3** La fila al posto della testa in vista chat, e via i pallini.
- **P4** La pagina App: launcher incorporato, via il bottone del cassetto, la pastiglia wiki nella barra, la chiusura dell'overlay app.
- **P5** La pagina Quaderni: WhoPanel dentro la pagina.
- **P6** La pagina Impostazioni: `casa-tu` nel pannello, via `#casa-door`.
- **P7** La modalità ordina.
- **P8** La pulizia: CSS, chiavi i18n morte (le controlla `test_launcher_sheet_contract.py:860`), commenti con le date, e `docs/reference/configuration.md`.
- **P9** Il giro sul Titan (vedi Verifica), poi l'esito nel piano.

## I file principali

- `jenny/config/schema.py` (CasaConfig), `jenny/webui/casa_routes.py`, `jenny/templates/ui/assets/shared/api-client.js`
- `jenny/templates/ui/index.html` (testa, pista, `#casa-tu`, `#launcher-sheet`, barra per scrivere)
- `assets/casa-pagine.js`, `assets/casa-app.js` (`_applyHead`, `goBackOneRoom`, `_closeOverlays`, `openTu`, `BACK_TO`), `assets/casa-who.js`, `assets/casa-tu.js`, `assets/mobile-launcher.js`, `assets/shared/apps-actions.js`
- Nuovo: `assets/casa-fila.js`, da aggiungere al manifest `jenny/utils/android_assets.py`
- `assets/casa-style.css`, `assets/i18n/{it,en}.json`, `docs/reference/configuration.md`

Da riusare: `shared/longpress.js`, `shared/gesto-orizzontale.js` (il gesto non cambia), `casa-trasloco.js`, `stacca`, `appendi` e `ricarica` in casa-pagine, `SchedaQuaderno`, `showJennyAppSheet`.

## I test da riscrivere (le decisioni vecchie, non da aggirare)

- `test_casa_pista_client.py`: circa 60 test su «0 = chat». Diventano «indice della chat» e riordino, e le regole su striscia e pallini diventano regole sulla fila.
- `test_launcher_sheet_contract.py:716`: il cassetto ha una porta visibile, «APP» nella fila, più il launcher incorporato. Restano: nessun id del guscio in `mobile-launcher.js`, e nessun `openLauncher` in casa-pagine.
- `test_casa_pages_contract.py`, `test_casa_tu_contract.py:37` (porta e pressione lunga), `test_casa_who_contract.py` (dialog, h1, chevron), `test_casa_who_client.py`, `test_casa_switch_client.py` (Indietro, pastiglia, testa), `test_casa_schermate_routes.py`, `tests/config/test_casa_pages_config.py`.
- Nuovi: normalizzazione di `ordine` (sconosciuti, doppioni, fisse mancanti, schermate orfane, migrazione da `[todo]`), rotta con array vecchio e con oggetto nuovo, fila (attiva, taglio del quaderno, tocco, pressione lunga), modalità ordina (inserimento, ×, Fatto, Indietro senza salvare), launcher incorporato (tastiera solo quando la sua pagina è corrente, niente `inert`), overlay app chiuso da Indietro.

## Verifica

- Il ciclo veloce: `ruff check jenny/ tests/` e `python3 -m pytest -q`. Poi sul 3.11 del telefono con `/tmp/py311/bin/python -m pytest -q` (prima `cat /tmp/py311/pyvenv.cfg`, e se serve ricreare il venv). Per i contratti del grafo, `NODE_PATH` con jsdom, e poi `node_modules` si cancella.
- La prova per mutazione sulle guardie nuove, con `PYTHONDONTWRITEBYTECODE` e `__pycache__` cancellato:
  - il validatore di `ordine`;
  - `indiceChat` in Indietro;
  - la tastiera del launcher legata alla pagina;
  - Indietro della modalità ordina che non salva.
- **Il giro sul Titan**, in un worktree pulito:
  - build: `keystore.properties` copiato, `ANDROID_HOME=$HOME/Library/Android/sdk ./gradlew app:assembleRelease`, grep di `[jenny] WARNING`, `apksigner verify`;
  - install: `ANDROID_SERIAL=<seriale del telefono di prova> adb install -r`, poi confronto di `lastUpdateTime`; si installa senza chiedere;
  - screenshot catturati sul telefono e poi `adb pull`.
- **Cosa provare col dito** (`adb shell input`, pressione lunga con `swipe x y x y 800`):
  - si parte su Jenny, e la fila mostra `APP · Jenny · TODO · QUADERNI · IMPOSTAZIONI`, cioè la migrazione del `[todo]` reale;
  - il tocco su ogni nome e il gesto di lato su ogni pagina, anche dentro Todo;
  - Indietro da ogni pagina porta a Jenny;
  - sulla pagina App, la tastiera fisica filtra le app; un'app aperta si chiude con Indietro;
  - la pressione lunga su un'app apre la scheda, e «Metti come pagina» funziona;
  - sulla pagina Quaderni, il tocco su un quaderno porta a Jenny col nome del quaderno in alto e la pastiglia wiki nella barra; la pressione lunga apre la scheda del quaderno;
  - la pagina Impostazioni apre una sottostanza, e Indietro torna a Impostazioni; l'Officina si apre;
  - la modalità ordina: portare Todo in prima posizione, Fatto, riavviare l'app e l'ordine è rimasto; la × toglie Todo; Indietro annulla;
  - l'officina è invariata, col cassetto a foglio.
- **Il telefono si lascia com'era:** pagine `[todo]` nell'ordine di default, sulla chat personale.


## Com'è andata (23/09/2026)

Tutto fatto e provato col dito sul Titan 2, release firmata installata sopra.

**Commit:** `2a4872e` piano · `148bb16` P1 (`casa.ordine`) · `361a129` P2–P7 in uno
(v. la nota nella checklist) · `bf8b9ac` trascinamento e commenti · `761fcfa`
nomi delle app nella fila.

**Sul telefono, nell'ordine in cui l'ho provato:**

- La migrazione del file vero: `[todo]` si e' letto come `APPS · Jenny · TODO ·
  NOTEBOOKS · SETTINGS`, e la casa si apre su Jenny.
- Tocco su ogni nome e scorrimento di lato su ogni pagina, **anche da dentro
  Todo** (il gesto raccontato dall'app). I temi in Impostazioni scorrono senza
  cambiare pagina: il riconoscitore cede allo scorrevole.
- Pagina App: la tastiera fisica cerca (`tel` → Telecomando, Telegram X);
  Indietro svuota la ricerca, poi riporta a Jenny. Pressione lunga su un'app →
  la scheda di sempre, con «Remove from pages» / «Add as a page». «Open» apre
  l'app a tutto schermo e **Indietro la chiude** — prima era un TypeError.
- Pagina Quaderni: tocco su «quaderno-b» → Jenny col nome del quaderno e il suo
  pallino nella fila, e la pastiglia «31 pages» nella barra; le pagine wiki si
  aprono da li'; Indietro rifa' la strada una stanza per volta.
- Pagina Impostazioni: una sottostanza (Updates) torna su Impostazioni, e da
  li' Indietro va a Jenny. L'Officina si apre dalla sua riga, e il suo cassetto
  e' ancora il foglio di prima.
- Modalita' ordina: si apre tenendo premuto un nome; trascinare, «Fatto» salva
  e l'ordine resta dopo un riavvio; Indietro annulla; la × toglie Todo, e «Add as
  a page» lo rimette **subito dopo Jenny** e ci atterra.

**Difetti trovati sul telefono, tutti corretti con un banco:**

1. La pastiglia trascinata restava sollevata al rilascio e stava un'intestazione
   sopra il dito: spostata nel DOM perdeva la cattura del puntatore, e la base
   era letta da `offsetTop` (misurato dal guscio). Ora il dito si segue sul
   documento e la base si legge dal rettangolo senza `transform`.
2. Le app appese si chiamavano col loro slug («todo») finche' nessuno leggeva
   l'elenco: ora la casa legge **solo** quello delle Jenny App, una volta, se ce
   n'e' una appesa.

**Prova per mutazione:** 25 mutazioni sulle guardie nuove (cassetto incorporato,
fila, pista, Indietro, overlay app), 25 uccise.

**Il telefono e' come l'ho trovato:** Todo unica pagina aggiunta, ordine di
partenza, conversazione personale. La pagina di Todo ha un id nuovo, perche'
l'ho tolta e rimessa per provare la ×.

**Resta aperto:**

- La modalita' ordina ha frecce da tastiera ma nessun annuncio a voce di dove
  e' finita la pastiglia; TalkBack non l'ho provato.
- `casa-who.js` non ha piu' la forma a tendina. Se un giorno servisse di nuovo
  un elenco a comparsa, e' da rifare, non da riaccendere.

## Ritocco del 26/09/2026: la voce accesa non cambia più carattere

> «il fatto che la pagina selezionata cambia font non mi piace» — l'utente, 26/09/2026.

La voce accesa passava da Inter maiuscolo 11,5px al serif del tema a 28px:
due caratteri, due casse e due taglie nella stessa fila. E siccome la sua
larghezza cambiava, a ogni cambio di pagina le altre voci scivolavano di lato.
(Il `transform: scale` previsto al punto 3 non era mai stato scritto: il
cambio era su `font-size`, cioè proprio quello che il punto 3 voleva evitare.)

Fra tre proposte disegnate (tutte in maiuscoletto; tutte in serif alla stessa
taglia; tutte in serif con l'attiva più grande) l'utente ha scelto la prima:

- **tutte le voci nello stesso stile**, piccolo, maiuscolo e spaziato;
- **quella dove sei** ha `--heading` al posto di `--text-muted` e una riga
  d'accento di 2px **sotto la parola**, non sul bordo del bottone da 40px;
- lo spazio e l'ombra della riga ci sono su **tutte** le voci, trasparenti:
  accenderne una non sposta niente, e la fila sta ferma;
- il pallino resta di 7px anche sulla voce accesa;
- `max-width: 62%` resta, per i nomi lunghi delle pagine conversazione.

Solo CSS (`home-style.css`) e commenti: la fila non ha cambiato né classi né
struttura, e i test di `test_home_strip_client.py` guardano solo chi ha `is-on`.

«Jenny» così perde l'unico serif che aveva nell'intestazione. È voluto.

**Resta aperto:** il secondo problema segnalato lo stesso giorno. Aprire un
quaderno rinomina la voce «Jenny» col nome del quaderno (`_chatName()` in
`home-app.js`), e questo non torna: la fila è una mappa di posti, e un posto
non cambia nome a seconda di cosa ci guardi dentro. È da decidere.

## Ritocco del 26/09/2026, secondo: le stanze parlano la lingua della fila

> «Questa pagina e quelle interne non sono coerenti» — l'utente, 26/09/2026,
> guardando le pagine di un quaderno dopo il ritocco della fila.

Dopo la fila in maiuscoletto, le stanze (pagine del quaderno, lettore,
Jenny, Model, Updates, Backup) avevano ancora l'intestazione di prima: un
occhiello a parole («‹ BACK TO THE CHAT», «‹ BACK TO THE PAGES»,
«‹ SETTINGS»: tre grammatiche) e sotto un titolo in serif da 26px. Entrando
in una stanza cambiavano carattere e altezza, il contenuto scendeva di 14px,
e nel lettore il titolo della pagina compariva due volte, uno sull'altro.

Disegnate due strade: la fila che resta anche nelle stanze, con una riga di
stanza sotto (circa 48px in più), e la fila che **diventa il percorso**.
L'utente ha scelto la seconda, perché occupa meno spazio in verticale:

- **una riga sola, alta quanto la fila** (10 + 40 + 6 px): freccia, percorso,
  comandi della stanza a destra;
- **il percorso nella tipografia della fila**: la radice spenta, «›», dove sei
  acceso con la riga d'accento. La radice dice **di che posto** è la stanza e
  ci porta con un tocco: `SETTINGS › UPDATES`, `NOTEBOOKS › ● quaderno`,
  `● quaderno › pagina`. Il pallino e la riga prendono il colore del quaderno
  (`dotColor`, lo stesso della sua riga nei Quaderni);
- **la freccia resta dov'era nel senso**: segue `BACK_TO`. Dalle pagine torna
  alla chat del quaderno, mentre la radice porta alla pagina Quaderni: la
  freccia dice da dove sei venuto, il percorso dove sta la stanza. La frase
  «torna a…» è rimasta, come `aria-label` della freccia;
- **il serif resta ai contenuti**: il titolo della pagina nel lettore sì,
  l'intestazione no. La regola ora è una sola: serif per i contenuti,
  maiuscoletto per la navigazione;
- nel lettore «Parlane» perde la parola e resta la pastiglia piena, solo icona:
  accanto al percorso e alla matita la riga non basterebbe;
- «Pages | Map» non si tocca: è un interruttore di vista, non un posto.

Due difetti visti sul telefono alla prima build e corretti nella seconda:

1. la radice era tagliata («NOTEBO…», «PIAN…») con mezzo schermo libero. Un
   `max-width: 45%` misurato su un percorso largo quanto il suo contenuto: ora
   cede con `flex-shrink`;
2. il pallino e la «›» stavano 2px sotto la riga del testo, perché il nome ha
   sotto i 4px della riga d'accento. Lo stesso scarto c'era nella fila dal
   ritocco di prima (il pallino di un quaderno), e l'ha avuto la stessa
   correzione.

Provato sul Titan 2, release firmata: Updates, Jenny, le pagine e il lettore di
un quaderno, la radice dal lettore (torna alle pagine) e dalle pagine (va ai
Quaderni). In ogni stanza il contenuto parte alla stessa altezza di una pagina
della fila.

**Resta aperto, come prima:** aprire un quaderno rinomina ancora la voce
«Jenny» nella fila. La proposta sul tavolo è che la chat di un quaderno viva
sotto NOTEBOOKS (lo stesso posto in cui il percorso mette già le sue pagine), e
che JENNY resti sempre la conversazione personale. Se si fa, anche
`home-ui-query.js` deve cambiare: oggi per la pista riporta l'id della pagina,
e una chat di quaderno sotto NOTEBOOKS gli farebbe dire «l'elenco dei
quaderni».
