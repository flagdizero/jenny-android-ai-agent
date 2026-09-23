# Le pagine si appendono dal posto dove vive la cosa

Piano del 23/09/2026. Segue `scorrimento-e-schermate-plan.md` (B0–B8) e
`casa-notebook-plan.md` (le pagine quaderno).

## La decisione

**Una cosa si appende dal posto dove vive.** Non esiste un negozio delle pagine
in cui scegliere da un elenco: è la regola dei launcher Android, dove l'icona si
prende nel cassetto e si porta sulla home. Da noi i posti sono due:

| la cosa | dove vive | la pressione lunga apre |
| --- | --- | --- |
| Jenny App | il cassetto | la scheda dell'app (esiste già) |
| quaderno | la tendina «Con chi parli» | la scheda del quaderno (nuova, gemella) |

Le due schede hanno **le stesse quattro righe, nello stesso ordine e con lo
stesso aspetto**:

1. **Apri**
2. **Metti come pagina** — oppure **Togli dalle pagine** se lo è già
3. **Modifica** (app) / **Rinomina** (quaderno)
4. **Elimina** — rossa, in fondo, come ogni azione distruttiva

La coerenza sta nella struttura, non nel costringere la stessa parola su due
atti diversi: «Modifica» di un'app chiede a Jenny di cambiarla, sul quaderno
l'atto è cambiargli nome, e chiamarlo col suo nome è più onesto.

**Le stanze escono dalle pagine.** Una stanza è un posto dove si va a sbrigare
una cosa (backup, aggiornamenti, modello) e si esce; una pagina è un posto dove
si sta. Restano app e quaderni.

**«Your home pages» se ne va del tutto**, e con lei la pressione lunga sui
pallini. Tutti i suoi mestieri si spostano sulla cosa: aggiungere, togliere,
accorgersi che una pagina punta a qualcosa che non c'è più. I pallini restano
pallini — toccarli cambia pagina — e il loro ridisegno è il giro dopo.

## Da confermare con l'utente

- **A1** — «Your home pages» tolta del tutto (non ridotta a elenco). Si perde la
  vista d'insieme; il riordino era già rimandato.
- **A2** — «Rinomina» per i quaderni. È **il solo passo caro** (v. P7) ed è
  messo per ultimo, tagliabile senza toccare il resto.
- **A3** — Dopo «Metti come pagina» si resta dove si è, con un avviso breve.
  È quel che fanno i launcher; saltarci dentro è l'alternativa.

## Parte 0 — I fatti misurati

- **Nel cassetto la pressione lunga c'è già.** `mobile-launcher.js:1066` →
  `detailEntry` → `showJennyAppSheet` (`shared/apps-actions.js:446`), che offre
  Apri · Modifica · Elimina. «Metti come pagina» è una riga in più.
- **Quella scheda è condivisa con l'officina**, che le pagine non le ha. La riga
  deve comparire solo se il guscio gliela offre: la scheda riceve la capacità,
  non la cerca.
- **Le app Android non possono essere pagine** (sono fuori da Jenny): la loro
  scheda non cambia. Nemmeno le Jenny App a vista esterna (telecomando,
  waterbot) né quelle rotte: la riga c'è, spenta, con il perché — una riga che
  manca fa chiedere «perché Todo sì e WaterBot no?».
- **La tendina «Con chi parli»** (`casa-who.js`): un tocco cambia conversazione,
  nessuna pressione lunga oggi. La conversazione personale non è un quaderno;
  le cartelle non apribili restano inerti.
- **Cancellare un quaderno esiste già**: `shared/project-delete.js::deleteProjectFlow`
  (la domanda dice anche quante conversazioni si porta via), usato
  dall'officina e dal chip dello scope. Anche **creare** esiste.
- **Rinominare no.** Esiste solo la metà di *riparazione*
  (`session/project_rename.py`): se qualcuno rinomina la cartella della wiki a
  mano, al turno dopo la chat la ritrova per id e la segue. Non c'è un comando
  che rinomini. E una pagina salva `project:<nome>`: un rinomino senza di lei la
  lascerebbe orfana.
- **La trappola più cara del piano.** Lo schema **rifiuta** una specie che non
  conosce (`schema.py`, `_specie_nota`), e il loader davanti a un config che non
  valida prova il `.bak` e poi **parte dai default** (`loader.py:113`). Togliere
  `"stanza"` dalla tupla così com'è vorrebbe dire: chi ha una stanza appesa, al
  prossimo avvio si ritrova senza provider, senza chiavi, senza niente. Sul
  telefono oggi le pagine sono app, app, quaderno — ma la regola non deve
  dipendere da questo.
- **Da dove passano le cancellazioni.** App: `webui/apps_api.py::delete_app`.
  Quaderno: `webui/project_delete.py::delete_project`. Jenny o una mano possono
  però togliere una cartella scavalcando entrambe.
- **L'opzione `onPressioneLunga` del modulo condiviso ha un solo chiamante**: i
  pallini. Tolti loro, esce anche lei — niente codice morto.

## I passi

### P1 — Le stanze escono, senza che nessun config ci rimetta la pelle

- Schema: `SPECIE_SCHERMATA = ("app", "conversazione")`, e davanti un validatore
  `before` su `CasaConfig` che **toglie** le voci `kind == "stanza"` invece di
  rifiutarle (migrazione dichiarata, come quella di `ssrf_whitelist`). Una
  specie davvero sconosciuta continua a essere rifiutata.
- Client: via `STANZE`, `prestaStanza`/`restituisciStanza`, la regola CSS
  `.casa-pagina[data-kind='stanza']`, la scelta delle stanze.
- `docs/reference/configuration.md`: la sezione `casa` dice le due specie e la
  migrazione.
- **Banco**: un `config.json` con una pagina stanza **passa dal loader vero** e
  ne esce intero meno quella pagina — provider e chiavi compresi. Mutazione:
  togliere la migrazione → il banco vede i default.

### P2 — Un registro solo per «è appesa?»

`CasaPagine` offre al guscio `appesa(kind, ref)`, `appendi(kind, ref)`,
`stacca(kind, ref)` e `piena`. Il guscio passa alle schede una capacità
`pagine`; nell'officina vale `null`. Una sola fonte per il testo della riga
(«Metti» o «Togli») e per il tetto.

### P3 — Il cassetto

La riga «Metti come pagina / Togli dalle pagine» nella scheda Jenny App, al
secondo posto. Spenta con il perché per: vista esterna, app rotta, tetto pieno.
**Banco**: nell'officina la scheda ha ancora tre righe; in casa quattro; la
riga cambia testo dopo aver appeso.

### P4 — La tendina

Pressione lunga su un quaderno → la scheda del quaderno: un `<dialog>` con lo
stesso markup e le stesse classi di `#jenny-app-sheet`, così è la stessa cosa a
vedersi. Righe: Apri · Metti come pagina · Rinomina · Elimina.

- La pressione lunga **non** deve anche cambiare conversazione: lo stesso segno
  `dataset.longpress` che usa già il cassetto.
- Nessuna pressione lunga sulla conversazione personale né sulle cartelle
  inerti.
- «Elimina» passa da `deleteProjectFlow`. Se era la conversazione corrente, si
  torna a quella personale; la tendina si ridisegna.

### P5 — Cancellare porta via la pagina

- Gateway: `delete_app` e `delete_project`, **dopo** aver cancellato, tolgono
  da `casa.schermate` le pagine con quel `ref` dentro `store.mutate` (la
  cancellazione, che è I/O lento, resta fuori dal lucchetto).
- Per quel che sparisce scavalcando le rotte: la pagina mostra al suo posto una
  scheda piccola — «Quest'app non c'è più» / «Questo quaderno non c'è più» — e
  un bottone «Togli la pagina». Prende il posto di `_segnaSparite` del foglio.

### P6 — Epurare «Your home pages»

Solo **dopo** P3 e P4: non deve esistere una build in cui una pagina non si
possa aggiungere da nessuna parte (la porta del cassetto è già sparita tre
volte in questo repo).

Via: il `<dialog id="casa-pagine-dialog">`, `_armaFoglio`, `apriFoglio`,
`chiudiFoglio`, `_disegnaFoglio`, `_riga`, `_rigaLibera`, `_scelta`,
`_apriScelta`, `_voci`, `_quaderni`, `_segnaSparite`; il CSS
`.casa-foglio-pagine`; le chiavi `casa.foglio.*` in `it.json` ed `en.json`
(quelle che servono alla scheda «non c'è più» cambiano casa, le altre spariscono);
la voce in `_closeOverlays`; `onPressioneLunga` dal modulo condiviso; i banchi
del foglio. Il ricordo va nel piano, non nel codice.

**Banco**: nessuna chiave i18n orfana, nessun id del foglio nel markup,
`test_no_ghost_methods_contract` verde.

### P7 — Rinomina (tagliabile)

- Un comando `project.rename` nuovo: valida il nome (`is_valid_project_name`),
  rifiuta se è già preso, sposta cartella e tracce della chat riusando la
  macchina del passo 7 (`_finish_move`, il giornale che rende il salto
  ripetibile dopo un crollo).
- Chi va dietro al nome, nello stesso giro: le pagine (`ref` rinominato dentro
  `store.mutate`), la conversazione corrente se era quella.
- Client: una domanda col nome attuale già scritto; poi si ridisegnano tendina,
  titolo e pagine.
- **Perché è l'ultimo**: la macchina è nata per riparare, non per andare avanti;
  usarla in avanti vuole un banco suo e un giro sul telefono suo.

### P8 — Giro sul telefono

- Cassetto: appendi Todo, la riga diventa «Togli»; il pallino compare.
- WaterBot: la riga c'è, spenta, e dice perché.
- Tendina: appendi un quaderno; la pressione lunga non cambia conversazione.
- Elimina un'app appesa: la pagina se ne va con lei.
- Una pagina con la cartella tolta a mano: compare «non c'è più», e si toglie.
- Rinomina un quaderno appeso: la pagina lo segue.
- Officina: la scheda Jenny App ha ancora tre righe.
- `config.json` letto dal telefono prima e dopo: niente di perso.

## L'ordine

| # | cosa | perché lì |
| --- | --- | --- |
| 1 | P1 — stanze fuori, con migrazione | toglie la parte più fragile e disinnesca la trappola |
| 2 | P2 — il registro | P3 e P4 ne hanno bisogno |
| 3 | P3 — il cassetto | la porta più usata |
| 4 | P4 — la tendina | la seconda porta |
| 5 | P5 — cancellare porta via la pagina | senza, le pagine morte si accumulano |
| 6 | P6 — via il foglio | solo quando le porte nuove ci sono |
| 7 | P7 — rinomina | il passo caro, tagliabile |
| 8 | P8 — telefono | |

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende.

## Quel che questo piano non fa

- Non ridisegna i pallini (linea sottile, nomi durante il gesto): giro dopo.
- Non riordina le pagine.
- Non offre le app Android come pagina.
- Non tocca l'officina, se non per garantire che la sua scheda resti com'è.

## Com'è andata (23/09/2026)

Fatto tutto, nell'ordine del piano, un commit per passo. Le decisioni rimaste
aperte sono state prese così: **A1** il foglio è tolto del tutto; **A2**
«Rinomina» c'è, col suo nome; **A3** dopo «Metti come pagina» si **atterra** sulla
pagina — lo diceva già il codice di prima, con un motivo (chi l'ha aggiunta
vuole vederla), e dal cassetto è anche quel che fa Android.

| passo | commit | mutazioni |
| --- | --- | --- |
| P1 stanze fuori, con migrazione | `1bbd6f3` | 2/2 |
| P2+P3 registro e cassetto | `50769de` | 10/10 |
| P4 tendina e scheda del quaderno | `3b69bf4` | 12/12 |
| P5 cancellare porta via la pagina; «non c'è più» | `15cb319` | 14/14 |
| P6 via il foglio | `3a4c07f` | — (un banco sulla pulizia) |
| P7 rinomina | `008099e` | 17/17 |
| P8 due difetti visti sul telefono | `c208469`, `6f6df76` | 1/1 |

**Quel che la mutazione ha trovato e nessuna rilettura avrebbe visto.**

- In P5 il controllo sulla **specie** nella potatura sopravviveva a tutto: i
  riferimenti di app e quaderni oggi non si toccano mai. Ma lo schema non vieta
  a una pagina app un `ref` a forma di quaderno — ora c'è il caso che lo prova.
- In P7 due controlli sopravvivevano, per la stessa ragione: un quaderno
  **senza chat** non passa dalla macchina di inseguimento, quindi le sue
  protezioni li' non valgono. Senza i controlli del modulo nuovo, `../fuori`
  avrebbe portato la cartella fuori da `wikis/` rispondendo «fatto», e una
  chat rimasta sotto il nome nuovo sarebbe stata adottata da un quaderno che
  non ne aveva.
- In P5 un banco usciva dalla pagina **prima** che la cornice fosse montata, e
  provava la guardia di un altro. Riscritto per uscire nel momento giusto.

**Quel che ha trovato il telefono.**

- Tenere premuto un quaderno faceva partire la **selezione del testo** di
  Chromium (a ~500 ms, prima dei 600 della pressione lunga) e la scheda non si
  apriva. Il cassetto non l'aveva mai avuto; la tendina non era mai stata
  tenuta premuta. Stesse tre regole CSS della riga d'attività.
- Dopo una rinomina la pastiglia delle pagine **perdeva il numero**: il titolo
  si ridisegnava prima che la tendina rileggesse. Invertito l'ordine.

**Provato col dito:** togli e rimetti Todo dal cassetto (si chiude tutto e si
atterra); WaterBot con la riga spenta e il perché; Indietro chiude prima la
scheda, poi il cassetto — e in tendina prima la scheda, poi la tendina; la
pressione su un quaderno non cambia conversazione; in officina la scheda di
un'app ha ancora tre righe; un quaderno di prova creato, appeso, rinominato (la
pagina segue, la pastiglia tiene il numero), cancellato (la pagina se ne va, si
torna alla personale); un secondo quaderno appeso e poi tolto **a mano**: la
sua pagina dice «non c'è più» e si toglie da sé.

Stato del telefono lasciato com'era trovato: pagine = `[todo]`. Dei quaderni di
prova non resta niente — l'unica traccia, una riga di `wikis/_index.md` lasciata
dalla cancellazione *a mano* (voluta, per simulare «sparito per altre strade»),
è stata tolta riscrivendo lo stesso file.

**Resta fuori, e lo si sa:** la pastiglia di un quaderno sparito per altre
strade mostra ancora il numero di pagine finche' la tendina non si rilegge —
e' la cache della tendina, e si sistema da sola alla prossima apertura.
