# Le pagine di casa

Disegno del 21/09/2026. Tavole: `Pagine`, `PaginaApp`, `PagineGestione` nel
canvas «Jenny UI: Utente e Operatore».

## Perché non è il carosello dell'officina

L'officina è un **anello di quattro sorelle** (Console, Cervello, Mani,
Memoria): scorri di lato e passi alla prossima, e ha senso perché sono pari
grado.

La casa è un **albero**. `BACK_TO` in `casa-app.js:70` lo dice:

```
pages → chat      tu → chat
reader → pages    jenny, model, updates, backup → tu
```

«Tu e Jenny» *contiene* Chi risponde, Aggiornamenti e Backup. Scorrere di lato
fra due stanze dove una sta dentro l'altra non vuol dire niente.

**Conseguenza:** non si porta il carosello in casa. Si fa la cosa che la casa è
davvero — la schermata iniziale di un launcher — e si aggiungono **pagine
accanto**, non stanze di fianco.

Lo swipe vale **solo al piano di casa** (la vista `chat`). Dentro una stanza
non c'è: lì si torna indietro.

## Due assi, due significati

- **Su** → il cassetto delle app. **Esiste già**: la maniglia sopra il
  composer, tavola `PiuUsate`.
- **Di lato** → le pagine.

Il cassetto **non** entra nell'elenco delle cose che si mettono su una pagina:
avrebbe due ingressi per la stessa cosa.

## La striscia in fondo

Oggi è una maniglia (44×4). Con le pagine configurate diventa **i pallini**, e
resta la presa per tirare su il cassetto: un solo posto, due gesti.

Chi non configura nessuna pagina vede la maniglia di sempre. Il default non
cambia niente a nessuno — stessa regola di `shared/home-view.js`, che già oggi
lascia scegliere su quale vista atterra il tasto Home.

## Cosa può stare su una pagina

Quattro cose, e tutte e quattro **esistono già**: il costo è il carosello, non
il contenuto, e si paga una volta sola.

| | c'è già | note |
| --- | --- | --- |
| una **Jenny App** | sì (iframe sandboxed, `apps/jenny-sdk.js`) | il caso più utile e il più caro: è una finestra viva |
| una **stanza** (Quaderni, Tu e Jenny) | sì (`BACK_TO`) | praticamente gratis |
| una **conversazione** | sì (`switchConversation`) | il filo di un quaderno preciso |
| il **cassetto app** | sì | **escluso di proposito**: è il gesto verso l'alto |

## Quante

**Casa più tre.** Oltre le tre, su uno schermo così le pagine si perdono: su Mac
c'è Mission Control a ricordartele, qui no. Tre pallini si leggono in un colpo
d'occhio; sei no.

Si gestiscono con un **tocco lungo sui pallini** → foglio con gli slot, cosa c'è
sopra, e la scelta per quelli vuoti (tavola `PagineGestione`).

## Il vincolo che cambia il disegno

**Resta viva solo la pagina che guardi.** Su Mac tutte le scrivanie restano
accese; qui una Jenny App è un iframe vero, e tre pagine accese sono tre
processi che consumano. I vicini si montano quando sbirciano e si smontano dopo.

Va misurato quanto costa montarne uno a metà gesto: se si vede, il vicino si
monta al primo pixel di trascinamento e non alla soglia.

## Il motore c'è già

`mobile-app.js:922 setupSwipeNav()` è fatto bene e sa già difendersi da:
trascinamento verticale, contenuto che scorre di lato (`_insideHScroll`),
testo selezionato (`hasSelection`), multi-touch, cassetto aperto, onboarding.

Oggi è incastrato nell'officina: legge `this._visibleModes()`,
`view-${this.currentMode}`, `this.drawer.activeDrawer`, `this._firstRun`.

**Si estrae in `shared/` con un host**, esattamente come è stato fatto per
`shared/mascot-drag.js` — e per lo stesso motivo, scritto nella sua
intestazione: due gusci, e una seconda copia di quella roba diverge in silenzio
perché nessun test se ne accorge.

In casa l'host aggiunge una guardia che l'officina non ha: **la mascotte**. Un
trascinamento che parte dal suo sprite è suo, non del carosello
(`casa-mascot.js` usa `bindMascotDrag`).

## Da decidere quando ci si arriva

- Lo stato sta in `localStorage` come tema e mascotte, o passa dal backend? Il
  precedente (`home-view.js`) dice localStorage. Ma una pagina che punta a una
  Jenny App cancellata va gestita: la pagina resta vuota e lo dice.
- Il tocco lungo sui pallini: da verificare che non litighi con lo swipe-up del
  cassetto, che parte dalla stessa striscia.
