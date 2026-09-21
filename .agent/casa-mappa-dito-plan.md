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

## Passo 0 — misurare sul telefono, prima di scrivere una riga

Due catture, e separano tre storie diverse:

1. Apri la casa → quaderno → linguetta della mappa. **Foto subito.**
   - Se si legge una delle tre frasi: la mappa non è disegnata, e non c'è niente
     da trascinare. Il seguito di questo piano non serve — serve capire perché
     (quasi certamente `noLinks`: pagine che non si linkano fra loro).
   - Se si vedono pallini e fili: la mappa c'è, e si prosegue.
2. Con i pallini a schermo, **aspetta sei secondi**, poi sposta. Poi riapri la
   linguetta e sposta **entro il primo secondo**.
   - Funziona dopo e non prima → è la camera che litiga, ed è il Passo 1.
   - Non funziona né prima né dopo → è altro, e va cercato con la console del
     pannello browser (`adb forward`, v. la nota «Guidare la WebUI del telefono
     dal Mac»): un errore dentro `_render` lascerebbe il disegno a metà.

Senza il Passo 0 tutto il resto è una scommessa: da qui non si distingue una
mappa che non risponde da una mappa che non c'è.

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

**Resta da provare sul telefono, e va riportato prima del Passo 2:** se la
risposta era «il pan tornava indietro», il trascinamento è una cosa che si
aggiunge per scelta e non per rimediare. Il Passo 0 resta valido così com'è
scritto — con una differenza: adesso la prova «sposta entro il primo secondo»
deve **funzionare**, ed è la conferma che questo passo era la causa.

## Passo 2 — il trascinamento dei nodi

Il codice viene da dove era, senza reinventarlo (`mobile-graph.js:402-420`,
recuperabile con `git show 0116b1f^:jenny/templates/ui/assets/mobile-graph.js`):

```js
const trascina = d3.drag()
  .on('start', (e, d) => {
    if (!e.active) this._sim.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  })
  .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
  .on('end', (e, d) => {
    if (!e.active) this._sim.alphaTarget(0);
    /* qui la decisione, v. sotto */
  });
dot.call(trascina);
```

### La decisione da prendere: molla o spillo

L'ultima riga cambia che **cosa sia** il trascinamento, e non è un dettaglio di
implementazione.

- **Molla** (`d.fx = d.fy = null`): il nodo torna dove la fisica lo vuole. È
  quello che faceva il grafo dell'officina. Serve a **guardare sotto** — sposti
  un pallino, vedi cosa c'era dietro, lo lasci andare. Su un quaderno annodato
  la matassa si richiude appena mollata.
- **Spillo** (`fx`/`fy` restano): il nodo sta dove l'hai messo. La mappa
  diventa una cosa che **si sistema**. Costa due domande in più: che fine fa la
  disposizione ricaricando (si perde, a meno di scriverla da qualche parte), e i
  nomi vanno ricollocati attorno alla nuova posizione di riposo.

**Consiglio: lo spillo.** In casa il pallino ha già un gesto — il tocco apre la
pagina — quindi trascinare è l'unico altro motivo per mettergli il dito sopra, e
«guarda sotto e poi lascia che si richiuda» non è un motivo. Con lo spillo, una
mappa di venti pagine si può aprire a mano una volta e leggerla; con la molla si
può solo sbirciare.

E la disposizione **non si persiste in questo giro**: sarebbe una terza
decisione (dove, con che chiave, e cosa succede quando una pagina cambia nome),
e va chiesta a parte.

## Passo 3 — il tocco contro il trascinamento, sullo stesso pallino

Il pallino ha già `.on('click', … _onOpenPage)`. Con un `d3.drag()` sopra, su
uno schermo che si tocca col pollice i due gesti si pestano: d3 sopprime il
click solo oltre la propria soglia, e un tocco con tre pixel di tremolio
diventa un trascinamento minuscolo che **si mangia l'apertura della pagina** —
che è il gesto principale di quella schermata.

Non si inventa una soglia: il repo ne ha già una misurata, per la mascotte, che
vive sullo stesso conflitto (si prende e si lancia, e sotto scorre il filo) —
`shared/mascot-drag.js`, `DRAG_THRESHOLD` e l'attesa della pressione. La mappa
prende **lo stesso patto**: sotto la soglia è un tocco e apre la pagina, sopra
è un trascinamento e non apre niente.

Va scritto sul posto perché è la cosa che si rompe per prima: un cambio di
soglia «per far trascinare meglio» è anche un cambio di quanto è facile
mancare una pagina.

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
