# L'officina in quattro cassetti — il piano intero

Sostituisce la prima stesura di questo file, che pianificava **sei** tavole
(Console, Stato, Programmazione, Cervello, Mani, Cassetti). Le tavole adesso
sono quattro, e le tre che sono cadute sono cadute per una ragione ciascuna,
scritta qui sotto perché non tornino.

Tavole: canvas «Jenny UI: Utente e Operatore», riga dell'operatore.

## Le quattro, e la domanda a cui ognuna risponde

| Cassetto | La domanda |
|---|---|
| **Console** | cosa sta facendo, adesso e per esteso |
| **Cervello** | con che testa pensa — e quali marche esistono |
| **Mani** | cosa può fare, e quando parte da sola |
| **Memoria** | cosa ricorda, chi gliela riempie, dov'è su disco |

Una console e tre facoltà: **pensa · fa · ricorda**. Non è un elenco di
sottosistemi — è il motivo per cui si capisce dov'è una cosa senza averlo
imparato.

## Le tre che sono cadute

**Stato.** Le sue sei caselle erano **sei link**: modello e contesto →
Cervello, oggi e attività → Console, prossimi lavori e sfondo →
Programmazione. Una scheda in cui ogni cella porta altrove non è un posto: è
un menu, e il menu esiste già ed è il dock. Quel che serviva davvero (`/status`)
resta un comando in Console.

**Programmazione.** Non è una famiglia. I quattro lavori di sistema finiscono
in tre posti diversi — `dream` e `gardener` in Memoria (riempiono i file),
`heartbeat` in Mani (legge `HEARTBEAT.md`, cioè le cose che le lasci),
`update_check` è dell'app — più i promemoria dell'utente, che sono Mani senza
dubbio. Hanno in comune solo di avere un orologio: raggrupparli per quello è
raggruppare per implementazione. **Il payload del cron porta già
`kind: system|user` per riga**, quindi dividerli è un filtro, non un refactor.

**Cassetti.** Era il ripostiglio degli avanzi: file, trascrizione, audit,
backup, «rifai la configurazione». Quelle cose non hanno niente in comune se
non di non stare altrove. Diventa **Memoria**, che è quel che erano: i file
*sono* la sua memoria su disco.

## La regola che decide tutto il resto

> **Se ce l'ha la casa, l'officina non lo rifà.**

E il criterio per sapere di chi è una cosa lo aveva già scritto il canvas:
*se un'impostazione ha bisogno di un paragrafo per spiegarsi, sta in officina.*

Cosa resta della casa, per intero e senza copie qui:

| | |
|---|---|
| **Aggiornamenti** | il giro intero. In officina resta l'etichetta `v0.11.0` in Console |
| **Backup cifrato** | esporta e ripristina. In officina resta la **storia locale** degli snapshot — ed è quel che la casa già dice: «si sfoglia in officina» |
| **Chi risponde** | scegliere il modello (e la marca viene con lui) fra quelle configurate |
| **Tema, mascotte, le sue regole** | tutto della casa |

E la divisione che ha richiesto più giri, sui provider:

* **casa** — *scegli fra quel che c'è*: tocchi un modello, e `model` +
  `default_provider` si salvano insieme.
* **officina, Cervello** — *decidi cosa c'è*: aggiungi o togli una marca, col
  suo formato, endpoint, CA bundle. «Aggiungi una marca» raccoglie anche **il
  primo modello** e ha «usala adesso» (acceso di suo, spegnibile): una marca
  senza un modello che funziona non è una marca che c'è, e senza questo
  aggiungerne una vorrebbe dire andare in officina e poi in casa per finire.
* **la chiave sta in tutti e due**: in casa come «sostituisci», in officina
  come campo dell'anagrafica. È l'unica manutenzione che diventa urgente —
  quando scade, Jenny smette di rispondere, e attraversare due schermate in
  quel momento è la cosa sbagliata.

## Cosa contiene ogni cassetto, e da dove viene

Niente di tutto questo è codice nuovo, tranne dove è detto.

**Console** — la chat dell'officina (`view-chat`), con pensieri, tool e tempi
già a schermo. *Da aggiungere:* la testata per turno (token in/out, durata) e
la barra in fondo (turno + contesto), che leggono `/api/token-usage` e il
runtime. Più l'etichetta della versione nell'intestazione. **Niente
impostazioni qui dentro**: è una conversazione.

**Cervello** — `settings→models` **meno il catalogo** (che è della casa), cioè
l'anagrafica dei provider e i parametri; più il blocco batteria
(`_renderBatterySection`). Il dialogo che raccoglie i campi di un provider
esiste già (`_showAddProviderDialog`).

**Mani** — `settings→tools` + `ssh` + `telegram`, interi; la porta per app e
skill; e l'elenco cron filtrato su `kind: user` più `heartbeat`.

**Memoria** — `settings→memory` + `workers`, interi (Dream, i tetti, il
giardiniere, l'archiviazione); la vista `view-workspace`; gli snapshot da
`settings→backup` (senza esporta/importa); i referti.

## Il percorso

La regola del giro: **l'app non è mai rotta a metà, e non c'è mai uno schermo
finto.** Da cui la forma — *la fisarmonica si svuota*.

**Passo 0 — il guscio. ✅ fatto il 20/09/2026.** Il dock passa a quattro voci. `view-settings`
**resta** e all'inizio è il contenitore: dentro ci sono ancora tutte e undici
le sezioni. L'unica cosa da costruire è la chiave del giro: in `render()`
l'elenco delle sezioni diventa una **tabella `cassetto → sezioni`**, e il
controller disegna solo quelle del cassetto attivo.

Niente controller nuovi, niente file nuovi, `_wireSections` (222 righe) **non
si spezza**: aggancia per `id`, e gli id delle sezioni non disegnate non ci
sono.

*Com'è andata.* Il rischio dei ~7 `querySelector` non protetti non si è
materializzato: `render()` costruisce **solo** le sezioni del cassetto, quindi
`_wireSections` gira su un DOM dove gli id assenti sono assenti — e `_wireBtn`
era già a prova di assente. Due cose in più che il piano non aveva previsto:

* **le porte.** Togliendo tre voci dal dock, app, file e wiki restavano vive e
  irraggiungibili. Ogni cassetto ha un elenco `porte`, e la prima è speciale:
  il **cassetto delle app** non è un modo, è un foglio che saliva dalla voce
  «Apps» — senza una maniglia nuova sarebbe sparito in silenzio, con tutti i
  banchi verdi.
* **due parcheggi dichiarati.** `personalization` e `system` stanno in Cervello
  e non ci resteranno: il primo perde tema e mascotte (li ha la casa) e lascia
  il nome di Jenny; del secondo la versione diventa un'etichetta in Console al
  passo 4. Parcheggiarli è ciò che tiene l'app intera mentre i cassetti si
  riempiono.

*Il rig ha trovato una cosa che i banchi non vedevano:* la porta del grafo
mostrava **`nav.graph`**, la chiave grezza — il modo si chiama `graph` e
l'utente la conosce come Wiki. Adesso c'è una tabella per i nomi, e un banco
che chiede a ogni porta di avere un nome in tutte e due le lingue.

Otto banchi nuovi (`test_officina_cassetti_contract.py`), otto mutazioni
rosse.

**Passo 1 — Mani. ✅ fatto il 20/09/2026.** `tools`, `ssh` e `telegram` erano
già nel cassetto dal passo 0 — la tabella li aveva assegnati tutti e undici —
quindi il lavoro vero era l'altro pezzo della riga: **l'elenco dei lavori
periodici**.

`buildCronView` prende un `tieni`, e Mani mostra `LAVORI_DI_MANI`: i tuoi
promemoria più `heartbeat`. `dream` e `gardener` se ne vanno con la memoria che
riempiono; `update_check` è dell'app, e il suo giro è in casa.

Due cose che il filtro ha portato con sé, e che sarebbero state difetti
silenziosi:

* **il banner si calcola sul filtrato, non sul payload.** Nomina i lavori
  fermi: sopra un elenco filtrato direbbe «dream è fermo» dove dream non c'è, e
  chi legge cerca una riga che non esiste.
* **i conteggi si ricontano.** Venivano dal server e descrivevano tutti i
  lavori: un «4 lavori» sopra due righe si legge come un guasto.

La sezione si chiama «Quando agisce da sola» invece di «Programmazione»: dice
cosa ci trovi, non com'è fatta sotto. Cinque banchi nuovi, otto mutazioni
rosse — comprese le due sul predicato, che al primo giro erano verdi perché
nessuno lo misurava.

**Passo 2 — Memoria. ✅ fatto il 20/09/2026.** `memory` e `workers` erano già
nel cassetto dal passo 0; la vista workspace ci arriva dalla sua porta. Il
lavoro era il pezzo delicato: **dividere `backup`**.

Esportare e ripristinare da file se ne sono andati — sono in casa, e ci sono
arrivati col giro di «Tu e Jenny». Qui resta la storia locale, che è quel che
la casa manda a sfogliare qui: la sua frase lo dice già, «si sfoglia in
officina. Vive però su questo telefono — di un telefono perso non salva
niente».

La sezione si chiama «Storia locale» e non più «Backup e ripristino»: il nome
vecchio prometteva due gesti che lì non ci sono più. Anche l'import del modulo
è sparito — lasciarlo vorrebbe dire tenere quei due gesti a portata di un
bottone dimenticato.

Il banco nuovo guarda il confine **da tutte e due le parti**: che l'officina
non li rifaccia, e che la casa ce li abbia davvero. Un banco che guardi solo il
primo verso difende un buco invece di un confine — il giorno che sparissero
dalla casa resterebbe verde.

**Passo 3 — Cervello. ✅ fatto il 20/09/2026.** L'unica sezione **divisa**.

Il catalogo se n'è andato in casa: «Cambia modello», l'elenco per provider, il
filtro, e i sei metodi che li servivano. Al loro posto una riga: *quale modello
risponde si sceglie in casa; qui si decide quali marche esistono*. Resta
l'anagrafica — formato, endpoint, CA bundle — e i tre parametri.

E «Aggiungi una marca» adesso **finisce**: primo modello e «usala adesso»,
acceso di suo. Senza, aggiungerne una vorrebbe dire uscire, andare in casa e
sceglierne uno — una cosa sola in due posti, cioè il difetto che questo giro
esiste per togliere. Le due scritture restano due, e in quest'ordine: se la
prima fallisce la marca non c'è, e attivarla prima farebbe puntare
`default_provider` a un provider che non esiste; se fallisce la seconda, la
marca resta salvata e quel che si perde è l'attivazione, non cinque campi
compilati.

**Tre banchi guardavano il catalogo di qua**, e non si cancellano: si spostano
a guardare il confine. Quello del tasto Indietro difendeva un sotto-livello che
non esiste più — `handleBack` adesso deve tornare `false`, e un `true` di
troppo si mangerebbe una pressione senza chiudere niente. Quello del ripristino
dello scroll perde `_fillCatalogGroup` dall'elenco dei caricatori asincroni, ma
il meccanismo resta perché SSH e gli snapshot atterrano allo stesso modo. Il
terzo è diventato «il catalogo è migrato, e l'officina non deve riprenderselo»,
con il puntatore a chi tiene la promessa adesso.

Sei mutazioni rosse.

**Passo 4 — Console.** La testata per turno e la barra in fondo. La
fisarmonica adesso è vuota: `view-settings` sparisce.

Ogni passo è installabile da solo.

## Fuori dal giro, dichiarato

* **I permessi per ambito e `/ro`.** Oggi c'è un interruttore per
  conversazione, client-side, col flag **dentro il messaggio** — apposta: un
  messaggio partito credendolo in sola lettura non si ritira, e il server non
  tiene quello stato per non poterne raccontare uno diverso. La tavola disegna
  tre permessi per ambito e un `/ro` per un turno: è uno stato sul server, un
  comando nuovo e un comando in meno nel composer. Cambia **cosa fa un
  messaggio**, non dove sta un'impostazione. Giro suo.
* **«Resta dentro il workspace».** Oggi è uno *stato* nel payload
  (`advanced.workspace_sandbox`), non una manopola. Disegnarlo come
  interruttore è una feature, non un raggruppamento.
* **Il nome e l'icona di Jenny.** Il nome si cambia nelle impostazioni
  vecchie, l'icona solo nel giro iniziale. Sono persona, quindi casa — ma è
  una riga nuova in «Tu e Jenny».
* **«Riesegui la configurazione» non torna.** `save_onboarding` fa
  `config.providers.providers = [uno]`: **sostituisce** l'elenco invece di
  aggiungere. In una schermata pro quel bottone può solo toglierti roba.

## Le prove

* **Passo 0:** un banco che incrocia la tabella `cassetto → sezioni` con le
  sezioni che esistono — nessuna sezione senza cassetto, nessun cassetto che
  ne nomina una che non c'è. Stessa forma dell'invariante `BACK_TO` della casa.
* **Passi 1-3:** per ogni cassetto, un banco che conta le sezioni a schermo; e
  la suite delle impostazioni deve restare verde **senza modifiche** — se cade,
  qualcosa si è spostato davvero invece di essere stato solo rifilato.
* **Passo 3, in più:** un banco che il catalogo dei modelli **non** compaia in
  officina. È l'unico punto in cui il doppione può rientrare di soppiatto.
* **Passo 4:** i conti della barra contro `/api/token-usage`.

## Il peso

| Passo | |
|---|---|
| 0 — guscio e tabella | mezza giornata, audit dei 7 accessi compreso |
| 1 — Mani | un'ora |
| 2 — Memoria | due ore |
| 3 — Cervello | due ore |
| 4 — Console | due ore |

Poco più di una giornata. Era una giornata e mezza con sei tavole: Stato era
mezza giornata, e adesso non esiste.
