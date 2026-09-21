# Scorrere di lato: l'officina su tutte le linguette, la casa a schermate

**Aperto il 21/09/2026.** Due lavori che sembrano uno solo — «lo stesso
scorrimento in tutte e due i gusci» — e che invece hanno una parte in comune
(il gesto) e due comportamenti diversi (dove porta).

---

## Parte 0 — Quel che ho misurato prima di pianificare

### L'officina ha già il gesto, ed è buono

`mobile-app.js::setupSwipeNav` è completo: elastico con sbirciata (`PEEK .13`,
`EDGE_PEEK .05`), velo grigio (`.swipe-scrim`), soglia `H_SLOP = 24` scelta
apposta (il touch slop di Android è ~8dp e sotto quella soglia `preventDefault`
uccide il long-press), e tre guardie giuste: cassetto aperto, testo selezionato,
scorrevole orizzontale sotto il dito (`_insideHScroll`).

### Ed è morto su tre linguette su quattro. So perché

```js
// mobile-app.js:980
view = document.getElementById(`view-${this.currentMode}`);
if (!view) return;                       // ← esce qui, in silenzio
```

Nell'HTML esistono solo `view-chat`, `view-workspace`, `view-wiki`,
`view-graph`, `view-settings`, `view-onboarding`. **`view-cervello`,
`view-mani` e `view-memoria` non esistono**: i tre cassetti sono la stessa
vista, e la tabella che lo dice è `VISTA_DI` in `mobile-settings.js:125`.
Quindi da Console lo scorrimento parte (perché `view-chat` c'è) e dai tre
cassetti non parte affatto.

Secondo sito dello stesso errore:

```js
// mobile-app.js:1051
this._animateSlideIn(document.getElementById(`view-${target}`), goingPrev);
```

**È la terza volta che questo errore esce.** Il piano dell'aspetto registra la
prima: «`_mount` cercava `title-<modo>` e i tre cassetti condividono
`title-settings`, quindi `setMode` usciva in silenzio». Tre siti, stessa forma:
qualcuno costruisce un `id` da `mode` invece che dalla tabella. **Quindi la
correzione non può essere una terza pezza.**

### La casa non ha nessuno scorrimento

La navigazione è una pila lineare di stanze (`BACK_TO` in `casa-app.js:70`),
tutta guidata da **un attributo solo**, `data-view` su `.casa-shell`, col CSS
che decide chi occupa lo spazio. È un impianto pulito su cui appoggiarsi.

### Tre vincoli della casa che il piano deve rispettare

1. **«Pagine» è una parola già presa.** `casa-pages.js` sono le pagine di un
   quaderno. Il concetto nuovo si chiama **schermata** nel codice
   (`schermate`, `schermataCorrente`), e «pagina» resta solo nelle frasi
   tradotte che legge l'utente. Senza questa regola, fra un mese nessuno capisce
   più quale `pages` è quale.
2. **Jenny sta sopra a tutto**, e nella casa il suo è l'unico `z-index` che
   esista (`casa-style.css`). Il carosello deve ottenere la sovrapposizione con
   l'ordine del DOM, **non** introducendo un secondo `z-index`: c'è già un banco
   che conta gli `z-index` e diventerebbe rosso.
3. **Il pavimento va dichiarato** fuori dalla chat. `_setView` scrive
   `--casa-composer-h: 20px` quando il composer non c'è, «senza, l'osservatore
   misurerebbe un elemento nascosto e lo troverebbe alto zero». Una schermata
   senza composer ha lo stesso problema.

### Com'è fatta una Jenny App, oggi

`apps-actions.js::openApp(slug)` costruisce un `<iframe sandbox="allow-scripts">`
su `/apps/<slug>/index.html?token=…&theme=…&lang=…&tokens=…` e lo mette in un
**velo a tutto schermo** con intestazione e bottone chiudi. Origine opaca
apposta: l'app non deve raggiungere il DOM della SPA.

Due cose buone che tolgono lavoro al piano:

- **Il tema è già gestito a caldo.** Un `MutationObserver` su `data-theme`
  manda `jenny:theme` all'iframe (`apps-actions.js:47-50`), quindi cambiare tema
  **non** obbliga a ricostruire le cornici.
- Il cassetto (`mobile-launcher.js`) esiste in **tutti e due** i gusci
  (`index.html:448`, `officina.html:380`), con lo stesso foglio e lo stesso CSS.

E una che ne aggiunge: il cassetto è un **foglio modale**
(`launcher-sheet`, `role="dialog"`, `aria-modal`), e `open()` esce subito se il
foglio non c'è. Metterlo *dentro* una schermata è un rifacimento vero, non una
chiamata diversa.

---

## Parte A — L'officina: scorrere su tutte le linguette

### A1. Una sola ricerca, impossibile da dimenticare

Non si correggono i due siti: si toglie la possibilità di sbagliare.

- Accanto a `VISTA_DI`, una funzione sola: `elementoVista(mode)` che fa
  `document.getElementById('view-' + (VISTA_DI[mode] || mode))`. Stessa cosa per
  l'intestazione, `elementoTitolo(mode)`, così i due casi stanno in fila.
- I due siti (`:980`, `:1051`) e quello dell'intestazione passano da lì.
- `mobile-app.js:376` (`setupKeyboardHelpers`) **non** si tocca: ha una lista
  sua (`['chat','workspace']`) e non costruisce un id da un modo qualsiasi.

**Banco** `test_no_raw_view_lookup_contract.py`: legge i sorgenti e fallisce se
trova `getElementById(\`view-${…}\`)` o `` `title-${…}` `` con dentro qualcosa
che non sia la funzione. È lo stesso tipo di guardia di
`test_no_ghost_methods_contract.py`, ed è giustificata dallo stesso motivo: un
difetto che il file valido e la suite verde non vedono, e che si manifesta solo
col dito sul telefono.

**Da provare rosso**: rimettere `view-${this.currentMode}`, il banco deve
fallire.

### A2. Scorrere fra due cassetti non è uno scambio di viste

`cervello → mani` è **lo stesso nodo del DOM** che si ridisegna. Oggi la parte
che conclude il gesto anima «la vista di arrivo», che in quel caso è la vista di
partenza: senza accorgimenti non si vede nessun cambio, o peggio si vede un
salto.

Quel che serve: a gesto concluso, `setCassetto(nuovo)` + `render()` girano in
modo sincrono, quindi si può **ridisegnare prima e animare dopo** sullo stesso
elemento — l'animazione resta una sola (`_animateSlideIn`), cambia solo che il
contenuto è già quello nuovo quando parte.

**Banco**: per ogni coppia ordinata dei quattro modi, l'elemento passato
all'animazione non è mai `null`, e per le coppie fra cassetti è lo stesso
elemento con contenuto cambiato.

### A3. I due capi: decisione

Con quattro linguette in fila, su Console manca il verso sinistro e su Memoria
quello destro. «Destra e sinistra su tutte le linguette» è letteralmente vero
solo se i capi si richiudono.

> **Proposta: sì, circolare.** Memoria → Console e Console → Memoria. Quattro
> voci sono poche: il giro è corto e non ci si perde. L'alternativa — lasciare i
> capi morti con la sbirciata elastica che già c'è — resta a una riga di
> distanza se lo provi e non ti piace.

**Banco**: `prev` e `next` definiti per tutti e quattro i modi.

### A4. Provare sul telefono

Costruire, installare, e guidare le quattro linguette con `adb input swipe` nei
due versi (le tre trappole sono in memoria, `driving-touch-gestures-over-adb`),
una foto per linguetta. Un gesto sintetico **non** prova che il dito ci arrivi —
ma qui il difetto era una guardia che usciva subito, e quella un `input swipe`
la vede.

---

## Parte B — La casa: le schermate

### B0. Il gesto diventa condiviso

I due gusci non si citano mai il DOM a vicenda, e non devono cominciare adesso.
Ma il gesto è lo stesso, e le sue costanti sono state pagate con delle misure
(il `24` del long-press, l'elastico, le guardie).

Quindi: **il gesto si sposta in `assets/shared/`**, in un modulo che non sa
niente di linguette né di schermate. Prende un contenitore, quante caselle ci
sono, dov'è adesso, e chiama indietro: «sto sbirciando di tanto», «ho
concluso verso destra», «sono tornato indietro». L'officina e la casa gli danno
due risposte diverse.

**Regola di sicurezza:** è un rifacimento di codice che funziona. Il
comportamento dell'officina dopo lo spostamento deve essere **identico** —
stesse costanti, stesse guardie, stessi banchi verdi, più una foto di confronto.

### B1. Cos'è una schermata, e dove vive

```
schermate: [ { id, kind: 'drawer' | 'app', slug? }, … ]
```

La **chat è la schermata 0**, non sta nell'elenco, non si sposta e non si
toglie. L'elenco contiene solo quelle aggiunte.

**Dove si salva: in `config.json`**, non in `localStorage`. Le schermate sono la
schermata iniziale del telefono dell'utente: perderle a un ripristino o a una
reinstallazione sarebbe la sorpresa peggiore, e `localStorage` non entra nel
backup. Si scrive **dentro `store.mutate`**, come ogni altra scrittura.

- schema: un campo nuovo con default lista vuota, e validazione di `kind`
- rotte: lettura e scrittura, sul modello di `/api/backup/exported` che è già il
  precedente di una rotta piccola e dedicata

**Banchi**: default vuoto; un `kind` sconosciuto rifiutato; la scrittura passa
da `mutate` (e non da `save_config`); uno `slug` che non corrisponde a nessuna
app non fa sparire la schermata ma la fa disegnare come «app non più
installata» — cancellare una schermata dell'utente perché un'app è sparita è una
decisione che non spetta al codice.

### B2. La geometria

Le schermate vivono **dentro** `data-view='chat'`: sono la casa, non una stanza.
Le stanze (`tu`, `model`, `backup`…) restano sopra e non cambiano di una riga.

- una pista dentro la vista chat, `transform: translateX(…)`, un pannello per
  schermata
- **niente `z-index`**: la sovrapposizione si ottiene con l'ordine del DOM
  (vincolo 2)
- sulle schermate senza composer, `--casa-composer-h` va dichiarato come fa
  `_setView` (vincolo 3)
- **i puntini**: quante schermate ci sono e dove sei. Non è un ornamento — senza,
  una funzione che si attiva solo con un gesto è invisibile, e chi non sa che
  c'è non la trova.

### B3. Il gesto, e «Aggiungi pagina»

Sull'**ultima** schermata, tirando ancora, al posto dell'elastico di fine corsa
compare il pannello di aggiunta, nell'area che si scopre.

Gli stati, in fila:

1. dito giù → in ascolto
2. oltre `H_SLOP` (24) e orizzontale → si scopre l'area, che porta scritto
   **«Aggiungi pagina»**
3. oltre la soglia di conferma → l'area si accende (è il momento in cui dici
   «se mollo, succede»)
4. dito su oltre soglia → si apre la scelta
5. dito su sotto soglia → torna indietro, niente

Le stesse guardie dell'officina valgono qui: cassetto aperto, testo selezionato,
scorrevole orizzontale sotto il dito — e una in più, **non in una stanza**: da
`tu` o da `model` il gesto non deve fare niente.

### B4. La scelta

Un foglio con due voci, nel vocabolario che la casa ha già (niente widget nuovi):

- **Cassetto delle app**
- **Una Jenny App** → secondo passo, quale, dalla lista di `AppsSource`

Casi da dire, non da lasciare al caso:

- nessuna app installata → si dice, e si offre il cassetto
- un'app **rotta** non si può scegliere (`openApp` oggi chiede conferma e
  propone la riparazione in chat: una schermata rotta fissa è un'altra cosa)
- un'app **esterna** (`view_kind === 'external'`, che apre un URL) **non** è
  offribile come schermata in questo primo giro, e il piano dice perché: apre
  una destinazione che il guscio non controlla, e incastonarla in una schermata
  fissa è una decisione di sicurezza a sé

Chiavi i18n nuove in `it.json` **e** `en.json`.

### B5. Disegnare una schermata

**`kind: 'drawer'`** — il cassetto oggi è un foglio modale. Serve una seconda
modalità: disegnare la stessa griglia **dentro un contenitore dato**, senza velo,
senza `aria-modal`, senza maniglia. Il riempimento (`_renderList`) non cambia;
cambia dove finisce e cosa gli sta intorno.
**È il pezzo più delicato della parte B**, perché il cassetto è condiviso con
l'officina e lì deve restare identico.

**`kind: 'app'`** — la stessa cornice di `openApp`, senza il velo e senza
l'intestazione. Quindi `openApp` si spezza in due: «costruisci la cornice per
questo slug» e «mettila in un velo a tutto schermo». La casa usa la prima.

- **pigrizia obbligatoria**: la cornice si costruisce solo per la schermata
  corrente e le due vicine, e si smonta quando ci si allontana. Tre app che
  girano insieme su un telefono non sono un dettaglio.
- il tema **non** obbliga a ricostruire: c'è già `jenny:theme` (Parte 0)
- il token sta nell'URL: è già così oggi, non cambia nulla

### B6. Togliere e riordinare

Non è rimandabile: senza, una scelta sbagliata è per sempre.

Minimo di questo giro: **pressione lunga su una schermata → togli**, con
conferma. Il riordino può aspettare, e il piano lo dice invece di fingere che
non serva.

### B7. Provare sul telefono

Costruire, installare, e col dito finto: scorrere dalla chat, vedere «Aggiungi
pagina», completare, scegliere il cassetto, tornare, aggiungere un'app,
scorrere fra le tre, toglierne una. Una foto per passo.

---

## L'ordine

| # | cosa | perché lì |
| --- | --- | --- |
| 1 | A1 — una sola ricerca + banco | sblocca lo scorrimento sui tre cassetti, ed è una riga |
| 2 | A2 — cassetto→cassetto | senza, il passo 1 si vede a metà |
| 3 | A3 — capi circolari | decisione tua, una riga |
| 4 | A4 — prova sul telefono | l'officina è finita e verificata prima di aprire la casa |
| 5 | B0 — il gesto in condiviso | rifacimento a comportamento identico, con l'officina come prova |
| 6 | B1 — il modello e dove si salva | senza, non c'è niente da disegnare |
| 7 | B2 — geometria e puntini | |
| 8 | B3 — il gesto e «Aggiungi pagina» | |
| 9 | B4 — la scelta | |
| 10 | B5 — cassetto inline, poi app inline | il pezzo grosso, e in quest'ordine perché il cassetto non ha iframe |
| 11 | B6 — togliere | |
| 12 | B7 — prova sul telefono | |

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende.

## Quel che questo piano non fa

- Non tocca **dove stanno le cose** nell'officina: i quattro cassetti e il loro
  contenuto restano quelli di `officina-tavole-plan.md`.
- Non tocca le stanze della casa (`tu`, `model`, `updates`, `backup`, i
  quaderni): le schermate vivono dentro la chat.
- Non riordina le schermate (B6 le toglie soltanto).
- Non offre le app esterne come schermata.
