# La mappa di casa torna a rispondere al dito

*21/09/2026 — branch `feat/la-casa`. Nasce da una domanda dell'utente: «su la
casa la wiki non è più interagibile, né drag né pan niente».*

## Da dove si parte, misurato sul codice

`casa-map.js` (~390 righe, ultimo tocco il 19/09 con `c2deec3`) disegna la
mappa del quaderno. Ecco cosa c'è e cosa non c'è.

**C'è lo spostamento.** `d3.zoom()` con `scaleExtent([0.4, 4])`, agganciato
all'SVG con `svg.call(zoom)`. E sull'SVG c'è `touch-action: none`
(`casa-style.css`), senza cui il browser si prenderebbe il gesto prima di d3 —
cioè la causa tipica di «non si muove», ed è già coperta.

**Non c'è il trascinamento dei nodi.** `d3.drag()` non compare da nessuna
parte. Il grafo dell'officina lo aveva, in due punti
(`mobile-graph.js:402` e `:600`, cancellato con `0116b1f`); la mappa di casa è
un oggetto più semplice per scelta: si sposta, si avvicina, e toccando un
pallino si apre la pagina.

**La mappa può non disegnarsi affatto.** `draw()` esce prima di `_render()` in
tre casi, e tutti e tre **svuotano l'SVG** lasciando una scritta nella nota
sotto (`_say`, che fa `svgEl.innerHTML = ''`):

| condizione | nota |
|---|---|
| nessuna pagina | `casa.pages.none` |
| pagine che non si rimandano l'una all'altra | `casa.map.noLinks` |
| D3 non caricato | `casa.map.failed` |

Tutte e tre esistono in italiano e in inglese, quindi a schermo si legge una
frase e non una chiave.

**E i nomi si collocano una volta sola, a fisica ferma.** `_placeLabels` fa una
`getComputedTextLength()` per etichetta; il commento sul posto registra la
misura: farlo a ogni frame sul Titan non si regge. Gira da `sim.on('end')`.

## Il sospetto principale, ed è anche il motivo per cui l'ordine conta

Dentro lo stesso `sim.on('end')`, subito dopo i nomi, c'è questo:

```js
svg.call(zoom.transform, t);   // inquadra la nuvola
```

**La camera si ripiazza quando la fisica si ferma.** Con `alphaDecay(0.045)`
sono ~150 tick, cioè circa cinque secondi sul telefono. Chi apre la linguetta e
prova subito a spostare la mappa la vede **tornare indietro da sola** appena la
simulazione si assesta. Non è «il pan non funziona»: è il pan che funziona e
viene sovrascritto.

Questo va prima di qualunque trascinamento, e non solo per l'ordine logico:
**un `d3.drag()` messo ora peggiorerebbe il difetto invece di aggiungere una
funzione.** Il trascinamento fa `alphaTarget(0.3).restart()`, quindi la fisica
riparte, quindi `end` scatta **di nuovo** — e la camera si ripiazza nell'istante
in cui alzi il dito. Ogni nodo trascinato costerebbe un salto della vista.

## Passo 0 — misurato sul telefono ✅ *21/09/2026, ore 20:21*

La build installata (`versionName 0.11.0`, aggiornata alle 20:02) **portava già
la correzione del Passo 1**: verificato leggendo il JS che il telefono serve
davvero, non il repo — `_inquadrataDaTe` compare 4 volte in
`files/workspace/ui/assets/casa-map.js`. Quindi la misura non e' «com'era
prima», e' «com'e' adesso».

**La mappa si disegna.** Nessuna delle tre uscite anticipate: quaderno
«etf-finance», 21 pagine, fili in abbondanza, nomi sui nodi piu' collegati.
Quindi l'ipotesi `noLinks` cade, e il sospetto principale era quello giusto.

Le tre meta' dell'invariante, ognuna con la sua misura:

| prova | atteso | misurato |
|---|---|---|
| sposto **entro il primo secondo**, poi aspetto 9 s (la fisica si ferma dentro) | la vista resta dove l'ho messa | resta. Differenza media 7,31/255, e il riquadro che cambia e' solo quello dei pallini che si assestano: la camera non si muove |
| torno su «Pagine» e poi sulla «Mappa» | la vista e' quella che avevo lasciato | **identica pixel per pixel** (differenza 0,0) |
| esco dal quaderno e lo riapro (disegno nuovo) | si inquadra da se' | centro verticale della nuvola a **0,474** — meta' esatta del pannello — e occupa da 0,071 a 0,982 |

**Un limite da dire:** il difetto **non** l'ho visto coi miei occhi. La build
sul telefono era gia' corretta, quindi «prima la vista tornava indietro» resta
dedotto dal codice — dove la riga girava senza condizioni — e non osservato.
Quel che e' osservato e' che adesso non torna indietro, e che l'inquadratura
automatica funziona ancora dove deve.

## Passo 1 — la camera smette di litigare col dito ✅ *fatto, senza telefono*

**Fatto il 21/09/2026.** Non serviva la misura: che l'inquadratura girasse senza
condizioni e' scritto nel codice, quindi e' un difetto sui suoi termini —
qualunque cosa mostri lo schermo. Quel che resta da misurare e' se *questa* era
tutta la risposta alla domanda che ha aperto il piano.

Come e' venuto: due decisioni con un nome invece di una chiusura sola.
`_onZoom(e, root)` distingue il dito (`e.sourceEvent` c'e' solo per un gesto
vero) e alza il flag; `_inquadra(svg, zoom, nodes, w, h)` porta la guardia in
cima. La ricollocazione dei nomi resta **fuori** dalla guardia: dipende da dove
stanno i nodi, non da dove guarda l'utente — ed e' la riga che un trascinamento
rende necessaria a ogni quiete.

Banco: sei prove nuove in `test_casa_map_client.py`, con un D3 finto che conta
quante volte l'inquadratura viene applicata. Sette mutazioni rosse — e una
prima passata **verde**, che ha trovato il banco cieco: togliendo la
ricollocazione dei nomi dal gestore della quiete non se ne accorgeva nessuno.
Chiusa con una settima prova.

**La regola, in una riga:** si inquadra una volta, e mai più dopo che l'utente
ha toccato la mappa. Il flag si azzera in `_render()` e non in `draw()` — un
quaderno nuovo merita la sua inquadratura, ma tornare sulla linguetta non
ridisegna (`if (this._drawn === data) return`) e non deve buttare via dove
l'utente aveva guardato.

**Provato sul telefono il 21/09/2026 (v. Passo 0): tiene.** Resta il senso della
frase che segue: se la
risposta era «il pan tornava indietro», il trascinamento è una cosa che si
aggiunge per scelta e non per rimediare. Il Passo 0 resta valido così com'è
scritto — con una differenza: adesso la prova «sposta entro il primo secondo»
deve **funzionare**, ed è la conferma che questo passo era la causa.

## Passo 2 — il trascinamento dei nodi ✅ *fatto: spillo*

**Scelto dall'utente il 21/09/2026: «si deve restare, sono d'accordo».**

Il codice viene da dove era (`mobile-graph.js:402-420`), con una riga in meno:
l'`end` **non** rilascia `fx`/`fy`. È tutto lo spillo. Le forze continuano a
tirare gli altri, e il pallino spillato sta fermo.

Dura quanto il disegno: i nodi nascono da `toSimulation` a ogni `_render`,
quindi una mappa ridisegnata riparte senza spilli. Ricordarli resta una terza
decisione, non presa (dove, con che chiave, e cosa succede quando una pagina
cambia nome).

**E il trascinamento prende la mappa in mano.** Questa non era nel piano ed è
venuta fuori scrivendolo: un trascinamento riaccende la fisica, quindi la quiete
arriva di nuovo, quindi l'inquadratura automatica **sposterebbe sotto gli occhi
il pallino appena messo a posto**. La guardia del Passo 1 copriva solo lo
spostamento, e il flag si chiamava `_inquadrataDaTe` — un nome che un
trascinamento rendeva falso. Rinominato `_presaInMano`: spostare sceglie da dove
guardare, trascinare dove sta una pagina, e in entrambi i casi chi decide cosa
c'è a schermo è l'utente.

## Passo 3 — il tocco contro il trascinamento ✅ *fatto*

Se ne occupa `clickDistance` di D3, che è il meccanismo giusto e non una soglia
scritta a mano: sotto, il gesto resta un tocco e la pagina si apre; sopra, il
click viene soppresso e il pallino si è solo spostato.

**Il numero.** Il piano diceva «il repo ne ha già una misurata». Mezzo vero: ne
ha **tre**, una per gesto — la mascotte 6 px (sta sopra un filo che scorre, e un
trascinamento involontario porta via la lettura), `pinch-zoom` 10 px («movimento
massimo perché un gesto conti come tap»), `selection` 12 px. Preso 10, che è
quella che risponde alla stessa domanda, e **dichiarato sul posto invece di
importato**: due gesti diversi che oggi condividono un numero sono due numeri,
non uno.

L'errore non è simmetrico, e il commento sul posto lo dice: un tocco che non
apre è un colpo a vuoto che si ripete, ma un trascinamento che apre *anche* la
pagina ti porta nel lettore proprio mentre stavi sistemando la mappa. Quindi
meglio stretta che larga. È anche l'unica cosa di tutta questa passata che solo
un pollice su un vetro può giudicare.

## Passo 4 — i nomi dopo un trascinamento

Qui non c'è quasi niente da fare, ed è utile sapere perché: `_placeLabels` gira
da `sim.on('end')`, e un trascinamento **fa ripartire e poi rifermare** la
fisica. Quindi i nomi si ricollocano da sé alla nuova quiete.

Le due cose da guardare, entrambe già dette sopra: che il Passo 1 tolga di
mezzo **solo** l'inquadratura e non anche la ricollocazione, e che con lo
spillo il nome segua il nodo spillato invece di restare dove stava.

## Cosa questo giro **non** tocca

- **La mappa dell'officina non torna.** Elenco, mappa e lettore stanno in casa
  dal 21/09, e il grafo cancellato aveva anche ricerca, legenda, pannello del
  nodo e messa a fuoco: sono un'altra schermata, non un trascinamento.
- **La disposizione non si salva** (v. Passo 2).
- **Lo zoom con due dita** non si tocca: `d3.zoom` lo fa già.
- **Le tre uscite anticipate di `draw()`** restano come sono. Se il Passo 0 dice
  `noLinks`, il problema è il quaderno e non la mappa, ed è un'altra
  conversazione.

## Rischi, in ordine di quanto mordono

1. **Il Passo 3 è quello che si nota se sbagliato**, e dalla parte peggiore: non
   «il trascinamento va male» ma «toccando una pagina non si apre». È il gesto
   più usato di quella schermata.
2. **La fisica che riparte a ogni trascinamento costa**, e il Titan fa ~30
   tick/s su un quaderno da una trentina di pagine: le costanti attuali
   (`alphaDecay(0.045)`) sono state scelte per farla *finire* in fretta. Con lo
   spillo un trascinamento ne riavvia ~150. Da guardare sul telefono, non in
   locale.
3. **Un flag sull'inquadratura è stato che può restare acceso** dove non deve.
   Vive nel disegno (`_render`), non nella scheda, ed è azzerato lì: la lezione
   di `pinnedWiki` e di `_returnMode` è recente.

## Come si verifica

Un passo = un commit, `ruff` + `pytest -q` verdi a ogni passo, e **ogni banco
nuovo provato rosso con una mutazione** prima di dirlo fatto.

I banchi esistenti (`test_casa_map_client.py`, 19 prove) misurano le funzioni
pure: nomi, raggi, `toSimulation`. Dei gesti non sanno niente, perché lì d3 non
c'è. Per il Passo 1 serve **un d3 finto** in node che registri le chiamate:
quante volte la camera viene ripiazzata, e che dopo un evento con
`sourceEvent` non lo sia mai più. È la forma giusta dell'invariante — «una
volta, e mai contro il dito» — e non si può leggere dal sorgente.

Mutazioni che devono far rosso:

- l'inquadratura torna a girare a ogni `end`;
- il flag si azzera in `draw()` invece che in `_render()` (ritornare sulla
  linguetta butta via dove guardavi);
- la soglia del tocco va a zero (ogni tocco diventa un trascinamento);
- con lo spillo, `fx`/`fy` vengono rilasciati.

E alla fine, sul telefono: apri la mappa, sposta subito, sposta dopo, trascina
un pallino, **tocca un pallino dieci volte di fila e conta quante pagine si
aprono** — dieci.

**Provato tutto sul telefono il 21/09/2026, ore 20:46–20:52.** Build da worktree
pulito su `9924828`, firmata (`CN=flagDiZero`), installata alle 20:36:30, e il JS
nuovo confermato *sul telefono* (`clickDistance` due volte in
`files/workspace/ui/assets/casa-map.js`, riestratto alle 20:36:31). Quaderno
«travel-wiki», tre pagine tutte collegate — tre bersagli chiari.

| prova | atteso | misurato |
|---|---|---|
| trascino un pallino e aspetto 9 s (la fisica riparte e si riferma) | resta dove l'ho messo | **0,0 px di spostamento.** Fermo a (1141,9 / 625,2) in entrambe le catture |
| e intanto gli altri? | si riassestano attorno allo spillo | l'altro verde è migrato da centro-basso a basso-sinistra: le forze lavorano, lo spillo no |
| il trascinamento apre anche la pagina? | no | no: restiamo sulla mappa |
| **tocco il pallino dieci volte** | dieci pagine | **10 su 10.** E lo spillo a (1141,9 / 625,2) dopo *ogni* ritorno — la linguetta non ridisegna, quindi la disposizione sopravvive ai dieci viaggi |
| tocco **tremolante** di ~8 px (sotto la soglia) | è un tocco, apre | apre |
| gesto di ~29 px (sopra la soglia) | è un trascinamento, non apre | non apre, e il pallino si sposta di 30,0 px — atterra dove il dito l'ha lasciato |

Le ultime due sono la prova vera della soglia: un `input tap` è perfettamente
immobile, cioè il caso facile, e non dice niente su un pollice che tremola. Il
tremolio finto da 8 px sì.

**Un limite che resta:** un dito vero non è né 8 px né 29. Il numero è
verificato ai due lati, non nel mezzo — se un giorno «tocco un pallino e non si
apre» torna, il sospetto è questo e si stringe.


---

# Coda: i nomi e gli spilli che restano

*21/09/2026, sera. Due richieste dell'utente in una riga: «alzalo a 40 allora.
E poi i nodi devono rimanere salvati».*

## Il tetto dei nomi: da regola a rete

Vedi il commento su `MAX_LABELS`. In breve: nato a 10 su una misura vera (31
pagine, titoli che sono frasi, 566 px), ma il rimedio sbagliava bersaglio —
escludeva **prima** che qualcuno misurasse lo spazio, e `placeLabels` ordina già
per collegamenti. A 40 lo spazio decide su un quaderno normale e il tetto ferma
il muro di testo su uno enorme.

Con lui se n'è andato `LABEL_ALL_UNDER = 13`: una scorciatoia che col tetto a 10
cambiava la risposta e a 40 non ne cambia nessuna.

**Misurato sul telefono**, stesso quaderno da 21 pagine: **8 nomi prima, 15
dopo** — e gli altri restano muti perché non ci stanno davvero, non per decreto.

## Gli spilli: dove, e perché non `localStorage`

Il posto è `workspace/.jenny/map-layout.json`, un file per tutti i quaderni, una
chiave per ciascuno. Le tre ragioni, in ordine di quanto pesano:

1. **`localStorage` non regge.** Il repo l'ha già misurato — il commento di
   `MainActivity.kt` che cita `launcher-usage-store.js`: «il localStorage della
   WebView non sopravvive al kill (persistenza asincrona di Chromium)». Jenny è
   il launcher e il sistema la uccide di routine. Una mappa che *ogni tanto*
   dimentica non sembra rotta: sembra che il salvataggio non funzioni.
2. **Il ponte nativo ha un cassetto solo**, ed è del cassetto delle app.
3. **Il workspace è dove stanno i quaderni**: sopravvive al kill, alla
   reinstallazione, e se lo porta dietro un backup.

Un file solo e non uno dentro `wikis/<nome>/` perché quella cartella la decide la
config (`wiki.wikis_dir`) e il client non la conosce: cercarla sarebbe
indovinarla.

**Gli spilli si applicano prima che la fisica parta**, o la simulazione
comincerebbe da posizioni casuali e strattonerebbe i nodi a posto sotto gli
occhi. E si scrive quel che è a schermo adesso: una pagina cancellata esce dal
file al primo trascinamento successivo. Una pagina **rinominata** cambia id,
quindi il suo spillo resta orfano e viene buttato allo stesso giro — è il prezzo
di una chiave che è il percorso, ed è scritto sul posto.

## Provato sul telefono, 21/09/2026 ore 21:13–21:16

Build da worktree pulito su `81d41e0`, firmata, installata alle 21:12:14, codice
nuovo confermato sul telefono.

| prova | misurato |
|---|---|
| il tetto morde ancora? | no: **15 nomi** dove prima erano 8 |
| trascino un pallino → il file | `{"etf-finance":{"wiki/index.md":[166,301]}}`, 43 byte |
| esco dal quaderno e rientro (rilettura del grafo, `_render` da capo) | il pallino è dove l'ho messo |
| **`am force-stop` e riapro** | la mappa è **identica pixel per pixel** a prima del kill (differenza 0,0) |

L'ultima è quella che giustifica la scelta del posto: è esattamente il caso in
cui `localStorage` avrebbe perso tutto.

## Quel che resta aperto

- **Non c'è modo di togliere uno spillo** se non trascinando il pallino altrove.
  Con la disposizione che adesso è permanente, un «rimetti a posto» avrebbe
  senso — ma è UI nuova e non è stata chiesta.
- Un quaderno cancellato lascia la sua chiave nel file: pochi byte, e nessuno li
  legge più. Si potano solo le pagine del quaderno che si sta salvando.

## La molla, tarata sul telefono — 22/09/2026, ore 08:44

Build `f77b8ea`, installata alle 08:42:21, codice confermato sul telefono.

**Il caso peggiore**, scelto apposta: il nodo più collegato del quaderno
(`wiki/index.md`, l'indice — è quello che i collegamenti tirano di più).

| | px |
|---|---|
| il dito l'ha portato a | 540 |
| la fisica se l'è ripreso di | 203 |
| **quanto ha tenuto** | **62 %** |

Cioè: si muove, si fa spingere dai vicini, e resta nel quartiere dove l'hai
messo. Con `FORZA_ANCORA = 0.3`, e sul nodo che tira di più — su uno normale
tiene di più.

**Una misura buttata per strada, e la stessa lezione di ieri.** Il primo
tentativo dava numeri assurdi (la maschera di colore prendeva 16.000 pixel, una
ventina di volte un pallino): raccoglieva anche lo sfondo. E il gesto prima
ancora aveva *spostato la mappa* invece del nodo, perché la coordinata l'avevo
stimata a occhio da una miniatura e sbagliata di 280 px. Risolto misurando il
blob connesso più grande invece di un centroide su tutta l'immagine — e il
pallino è risultato un cerchio da 116 px di diametro, non quello che credevo.

## Uno spavento, e come si è chiuso

Dopo il trascinamento il file aveva **12 ancore** dove io ne avevo fatta una.
Sembrava che qualcosa le creasse da sé.

Provato in modo controllato invece di dedurre: file cancellato, app riavviata,
mappa aperta (**non scrive niente**), un trascinamento → **1 ancora**, un
secondo → **2**. Il codice conta i gesti. Le altre erano dell'utente, che aveva
il telefono in mano nello stesso quarto d'ora.

**E per fare quella prova ho cancellato il suo file**, che è una cosa da
copiare *prima*, non da rimpiangere dopo. Rimesso identico — 12 ancore di
`etf-finance` più la Monstera di `piante` — con etichetta SELinux uguale a
quella di `config.json`, e verificato che l'app lo rilegga e la mappa torni su
disposta.
