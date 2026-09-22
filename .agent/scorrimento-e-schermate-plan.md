# Scorrere di lato: l'officina su tutte le linguette, la casa a pagine

**Aperto il 21/09/2026, riscritto il 22/09/2026** dopo che le tavole `Pagine`,
`PaginaApp` e `PagineGestione` hanno deciso la forma della parte casa. Dove
tavola e parola detta divergevano, **vince la tavola** (decisione dell'utente,
22/09).

Due lavori che sembrano uno: una parte in comune (il gesto) e due
comportamenti diversi (dove porta).

---

## Parte 0 — I fatti misurati (riverificati il 22/09/2026)

### L'officina ha già il gesto, ed è buono

`mobile-app.js::setupSwipeNav` è completo: elastico con sbirciata (`PEEK .13`,
`EDGE_PEEK .05`), velo grigio (`.swipe-scrim`), soglia `H_SLOP = 24` scelta
apposta (il touch slop di Android è ~8dp e sotto quella soglia `preventDefault`
uccide il long-press), e tre guardie giuste: cassetto aperto, testo selezionato,
scorrevole orizzontale sotto il dito (`_insideHScroll`).

### Ed è morto su tre linguette su quattro

```js
// mobile-app.js:923
view = document.getElementById(`view-${this.currentMode}`);
if (!view) return;                       // ← esce qui, in silenzio
```

Gli id nell'HTML sono solo `view-chat`, `view-onboarding`, `view-settings`,
`view-workspace`. **`view-cervello`, `view-mani` e `view-memoria` non
esistono**: i tre cassetti sono la stessa vista, e la tabella che lo dice è
`VISTA_DI` (`mobile-settings.js:116`). Da Console il gesto parte, dai tre
cassetti no.

Secondo sito, stessa forma: `mobile-app.js:994`,
`_animateSlideIn(document.getElementById(\`view-${target}\`))`.

**È la terza volta che esce questo errore.** La prima è registrata nel piano
dell'aspetto: «`_mount` cercava `title-<modo>` e i tre cassetti condividono
`title-settings`, quindi `setMode` usciva in silenzio». Tre siti, una forma:
qualcuno costruisce un id da `mode` invece che dalla tabella. **Quindi la
correzione non è una terza pezza.**

### La casa: com'è fatta davvero

- **Nessun `touchstart`**: nessuno scorrimento, da nessuna parte.
- Navigazione a pila di stanze (`BACK_TO`, `casa-app.js:72`), guidata da **un
  attributo solo**, `data-view` su `.casa-shell`, col CSS che decide chi occupa
  lo spazio.
- **Chat e stanze sono tutte sorelle** dentro `.casa-shell`: `casa-thread`,
  `casa-empty`, `casa-activity` da una parte; `casa-pages`, `casa-reader`,
  `casa-tu`, `casa-jenny-room`, `casa-model-room`, `casa-updates-room`,
  `casa-backup-room` dall'altra. Nessuna è figlia della chat.
- **Il cassetto si apre da un bottone** accanto al composer (`#casa-drawer`,
  `index.html:133`). L'officina ha **il suo**, separato (`#btn-launcher`,
  `officina.html:229`), sullo stesso foglio condiviso. Toglierne uno non tocca
  l'altro.
- **Non esiste nessuna striscia di pallini.**

### I tre vincoli della casa

1. **«Pagine» è una parola già presa.** `casa-pages.js` sono le pagine di un
   *quaderno*. Il concetto nuovo si chiama **`schermata`** nel codice; «pagina»
   resta solo nelle frasi tradotte che legge l'utente — che è come le chiama la
   tavola. Senza questa regola, fra un mese nessuno sa più quale `pages` è quale.
2. **Jenny sta sopra a tutto.** Il suo `z-index: 5` (`casa-style.css:614`) è
   ancora **l'unica regola** del foglio — ricontato il 22/09, gli altri due
   riscontri sono prosa dentro i commenti. La tavola dice la stessa cosa
   («unico z-index del disegno»), e in `PaginaApp` lei sta **sopra l'app**. Il
   carosello deve impilare con l'ordine del DOM, non con un secondo `z-index`.
3. **Il pavimento va dichiarato** dove il composer non c'è: `_setView` scrive
   `--casa-composer-h: 20px` (`FLOOR_NO_COMPOSER`), «senza, l'osservatore
   misurerebbe un elemento nascosto e lo troverebbe alto zero».

### Com'è fatta una Jenny App, oggi

`apps-actions.js::openApp(slug)` (`:145`) costruisce un
`<iframe sandbox="allow-scripts">` su `/apps/<slug>/index.html?token=…&theme=…`
e lo mette in un **velo a tutto schermo** con intestazione e bottone chiudi
(`:177`). Origine opaca apposta: l'app non deve raggiungere il DOM della SPA.

Due cose che tolgono lavoro: **il tema arriva già a caldo** alle app
(`MutationObserver` su `data-theme` → `jenny:theme`, `:61`), quindi cambiare
tema non obbliga a ricostruire le cornici; e il foglio del cassetto è già
condiviso fra i due gusci.

### Cos'è «una conversazione»

Una **chiave di sessione** (`sessionManager`, `shared/session-manager.js`):
`unified:default` è quella personale, i quaderni e i progetti hanno la loro.
`switchTo(key)` cambia conversazione e riaggancia il filo
(`wsManager.attachChat`). **La chat a schermo è una sola**: due conversazioni
non possono essere vive insieme.

---

## Parte A — L'officina: scorrere su tutte le linguette

**✅ Fatta il 22/09/2026 — `1baea1d`, provata sul telefono.**

### A1. Una sola ricerca, impossibile da dimenticare

Non si correggono i due siti: si toglie la possibilità di sbagliare.

- Accanto a `VISTA_DI`, **una funzione sola**: `elementoVista(mode)` →
  `getElementById('view-' + (VISTA_DI[mode] || mode))`. Idem
  `elementoTitolo(mode)`, così i due casi stanno in fila.
- I due siti (`:923`, `:994`) e quello dell'intestazione passano da lì.
- `mobile-app.js:350` (`setupKeyboardHelpers`) **non** si tocca: ha una lista
  sua (`['chat','workspace']`), non costruisce un id da un modo qualsiasi.

**Banco** `test_no_raw_view_lookup_contract.py`: fallisce se trova
`` getElementById(`view-${…}`) `` o `` `title-${…}` `` con dentro qualcosa che
non sia la funzione. Stessa famiglia di `test_no_ghost_methods_contract.py`, e
stessa giustificazione: un difetto che file valido e suite verde non vedono, e
che si manifesta solo col dito sul telefono.
**Da provare rosso** rimettendo `view-${this.currentMode}`.

### A2. Fra due cassetti non è uno scambio di viste

`cervello → mani` è **lo stesso nodo del DOM** che si ridisegna. Oggi si anima
«la vista di arrivo», che lì è anche quella di partenza: senza accorgimenti non
si vede nessun cambio, o si vede un salto.

`setCassetto(nuovo)` + `render()` sono sincroni, quindi: **ridisegnare prima,
animare dopo**, sullo stesso elemento. L'animazione resta una.

**Banco**: per ogni coppia ordinata dei quattro modi l'elemento passato
all'animazione non è mai `null`; per le coppie fra cassetti è lo stesso
elemento col contenuto cambiato.

### A3. I capi si richiudono

Con quattro linguette in fila, su Console manca il verso sinistro e su Memoria
quello destro; «destra e sinistra su tutte» è vero solo chiudendo il cerchio.
Memoria → Console e Console → Memoria.

**Banco**: `prev` e `next` definiti per tutti e quattro i modi.

### A4. Prova sul telefono — fatta

Build firmata, installata alle 15:22, e dentro l'APK verificato: `elementoVista`
c'e', ricerche grezze zero, i capi circolari ci sono.

Le otto mosse guidate con `adb input swipe`, partendo **da un tocco sul dock**
(riposizionarsi a scorrimenti fa derivare lo stato e il primo tentativo ha dato
letture false).

**Sette su otto al primo giro, e il buco non era dove sembrava.** Fallisce solo
la Console, in un verso o nell'altro a seconda della volta, **mai** i tre
cassetti. Misurato: a `y=250` — la fascia dell'intestazione, sopra i messaggi —
**sei su sei**, tre per verso. Sotto, in mezzo ai messaggi, capita che non vada.

**Non e' un difetto di questo passo: e' `_insideHScroll` che fa il suo
mestiere.** La Console e' l'unica vista con dentro roba dell'utente che puo'
scorrere di lato (un blocco di codice, una riga lunga), e li' il gesto **deve**
appartenere a quella e non al carosello. Spiega anche l'asimmetria: dipende se
quello scorrevole e' gia' a fondo corsa nel verso del dito.

> **Resta una domanda aperta, e non la chiudo da solo:** su un messaggio largo
> che occupa quasi tutto lo schermo, cambiare linguetta dalla Console diventa
> difficile — bisogna trovare un punto libero. E' il prezzo giusto per non
> rubare lo scorrimento a un blocco di codice, ma se da' fastidio si corregge
> (per esempio riservando al carosello una fascia sicura, come fa la tavola con
> la striscia dei pallini).

---

## Parte B — La casa a pagine, come la tavola

### Quel che la tavola ha deciso, e che il piano vecchio sbagliava

| | piano del 21/09 | **tavola (vale questo)** |
| --- | --- | --- |
| come si aggiunge | scorri oltre l'ultima → «Aggiungi pagina» | **pressione lunga sui pallini** → foglio «Le pagine di casa» |
| il cassetto come pagina | sì | **no**: «ce l'hai già tirando **su**. Due porte per la stessa cosa sono una di troppo» |
| le specie | cassetto, app | **app · stanza della casa · conversazione** |
| quanto resta vivo | corrente + due vicine | **solo la corrente**: «le altre si spengono, o te le paghi in batteria» |
| togliere | passo rimandato | **stesso foglio dell'aggiunta** (la ✕ su ogni pagina piena) |

Il motivo per cui la pressione lunga batte lo scorrimento non è solo che lo dice
la tavola: «scorri oltre l'ultima» mette il comando **in fondo a un asse che si
allunga** — con cinque pagine bisogna attraversarle tutte — mentre i pallini
stanno sempre nello stesso punto.

### B0. Il gesto diventa condiviso — ✅ `f8f87b3`

**Fatto il 22/09/2026.** Ed e' venuto fuori un taglio piu' pulito di quello
previsto: **si e' spostato il riconoscimento, non la risposta**. I due gusci
hanno due risposte visive diverse (l'officina trascina la vista corrente con
sbirciata smorzata e velo, e la vicina non si disegna mai; la casa fara'
scorrere una pista con le pagine affiancate), e metterle tutte e due nel modulo
avrebbe voluto dire un modulo con due modalita', cioe' due moduli in un file.

La prova che non e' cambiato niente sono le **dieci prove di
`test_officina_swipe_client.py`, intatte e verdi**, e le due mutazioni che le
rifanno rosse (ricerca grezza → 8 rosse; capi non piu' circolari → 3 rosse).

Presidio nuovo, `test_gesto_orizzontale_contract.py`: `gesto-orizzontale.js` e'
**l'unico file di tutta la UI che ascolta `touchmove`** e l'unico che decide un
asse — misurato subito dopo l'estrazione, era gia' vero. E non puo' nominare una
vista, una linguetta, un cassetto o una pagina: il giorno che lo fa smette di
essere condiviso.


I due gusci non si citano mai il DOM a vicenda e non devono cominciare adesso.
Ma il gesto è lo stesso e le sue costanti sono state pagate con delle misure.

Il gesto si sposta in `assets/shared/`, in un modulo che non sa niente né di
linguette né di pagine: riceve un contenitore, quante caselle, dov'è adesso, e
richiama «sto sbirciando di tanto», «concluso a destra», «tornato indietro».

**Regola di sicurezza:** è un rifacimento di codice funzionante. Dopo lo
spostamento l'officina deve comportarsi **identica** — stesse costanti, stesse
guardie, banchi verdi, più una foto di confronto.

### B1. Cos'è una pagina, e dove vive — ✅

**Fatto il 22/09/2026.** `SchermataConfig` / `CasaConfig` nello schema,
`MAX_SCHERMATE = 8`, e le rotte in `jenny/webui/casa_routes.py`.

**Un vincolo trovato costruendo, che vale per tutto quel che segue:** il livello
HTTP del gateway (`websockets` http11) **rifiuta qualunque metodo diverso da GET
e qualunque body**, al parser, prima delle rotte — lo dice gia' `apps_api` per
le azioni delle app. Quindi la scrittura e' una GET con l'elenco url-encoded in
`?v=`. Non e' una svista da correggere in POST: senza sostituire il livello HTTP
non puo' funzionare.

Due scelte prese scrivendo:

- **si manda l'elenco intero, non una riga.** Aggiungere, togliere e spostare
  diventano la stessa scrittura: tre rotte in meno e, soprattutto, niente caso
  in cui due di quelle si incrociano lasciando un ordine che nessuno ha chiesto.
- **la validazione sta al confine, non dentro `mutate`:** li' il lock e' preso
  per tutta la callback, e una `ValueError` alzata dentro diventa un 500 invece
  di un 400.

Tredici prove in `test_casa_schermate_routes.py`, provate rosse su due
mutazioni (`save_config` al posto del funnel; il cassetto rimesso fra le
specie). La sezione nuova e' documentata in `docs/reference/configuration.md`,
come pretende `test_configuration_doc_covers_the_schema`.


```
schermate: [ { id, kind: 'app' | 'stanza' | 'conversazione', ref }, … ]
```

`ref` è lo slug dell'app, il nome della stanza, o la chiave di sessione.
**La chat personale è la pagina 0**: non sta nell'elenco, non si sposta, non si
toglie. L'elenco contiene solo quelle aggiunte.

**Si salva in `config.json`**, non in `localStorage`: sono la schermata iniziale
del telefono, perderle a un ripristino sarebbe la sorpresa peggiore, e
`localStorage` non entra nel backup. Scrittura **dentro `store.mutate`**, come
ogni altra.

- schema: campo nuovo, default lista vuota, `kind` validato
- rotte piccole e dedicate, sul modello di `/api/backup/exported`
- **un tetto** al numero di pagine (proposta: 8). Oltre, i pallini non si
  leggono più e lo scorrimento diventa un viaggio.

**Banchi**: default vuoto; `kind` sconosciuto rifiutato; la scrittura passa da
`mutate` e non da `save_config`; un `ref` che non esiste più (app disinstallata,
quaderno cancellato) **non fa sparire la pagina** ma la disegna come «non c'è
più» — cancellare una pagina dell'utente perché il suo contenuto è sparito non
è una decisione del codice.

### B2. La geometria

La scoperta che cambia l'impianto: **chat e stanze sono sorelle**, non annidate.
Quindi il carosello **non** sta «dentro la vista chat»: è l'area principale del
guscio, e la pagina 0 mostra i pezzi della chat.

- una pista nel guscio, `transform: translateX(…)`, un pannello per pagina
- **niente `z-index` nuovo** (vincolo 2): si impila con l'ordine del DOM
- dove il composer non c'è, `--casa-composer-h` va dichiarato (vincolo 3)
- le stanze restano **sopra**, come oggi: entrare in `tu` o `model` copre le
  pagine e non le sposta

**La conseguenza sulle stanze, da dire adesso.** Una pagina di specie `stanza`
deve mostrare un elemento che è **unico** (`casa-pages` è uno solo) e che oggi
il CSS accende con `.casa-shell[data-view='pages'] .casa-pages { display:flex }`.
Servono due cose: **riparentare** quell'elemento dentro il pannello della sua
pagina (i controller lo prendono per id al momento della costruzione — v.
`casa-tu.js:54` — quindi spostarlo non gli fa niente), e **una seconda via** nel
CSS perché si accenda quando la sua pagina è quella corrente, non solo quando
`data-view` la nomina. È la parte più delicata di B2 e ha il suo banco.

### B3. La striscia dei pallini

La tavola: «I pallini prendono il posto della maniglia — e restano la presa per
tirare su il cassetto. **Su** il cassetto, **di lato** le pagine.»

Un asse, tre gesti:

| gesto | cosa fa |
| --- | --- |
| di lato | cambia pagina |
| **su** | apre il cassetto delle app |
| **pressione lunga** | apre il foglio «Le pagine di casa» |

**Non è «aggiungo dei puntini».** È sostituire il bottone `#casa-drawer` che sta
accanto al composer, quindi: la striscia va costruita, il gesto «su» va scritto
(oggi non esiste: il cassetto si apre con un click), e il bottone va tolto
**solo dalla casa** — l'officina tiene il suo, che è un elemento diverso sullo
stesso foglio.

Rischio dichiarato: si toglie un comando che funziona e lo si sostituisce con un
gesto che non si vede. Per questo i pallini ci sono **sempre**, anche con zero
pagine aggiunte: sono l'unica cosa che dice che il gesto esiste.

### B4. Il foglio «Le pagine di casa»

Pressione lunga sui pallini → foglio dal basso, come lo disegna
`PagineGestione`:

- titolo, e la riga che insegna il gesto: «Tieni premuto sui pallini per tornare
  qui»
- **una riga per pagina piena**: icona, nome, «pagina N · una tua Jenny App» /
  «· una stanza della casa», e la **✕** per toglierla
- **una riga tratteggiata per la prossima, vuota**: «pagina N · vuota — cosa ci
  metto?» con le tre scelte aperte
- le due note in fondo, che sono parte del disegno e non decorazione: il
  cassetto non è in elenco perché si tira su; e resta viva solo la pagina che
  guardi

Scegliere una specie apre il secondo passo (quale app / quale stanza / quale
conversazione).

**Togliere arriva con questo passo**, e non perché si sia cambiato idea sul
rimandarlo: nella tavola la ✕ è sulla stessa riga dell'aggiunta, e costruire il
foglio senza la ✕ costerebbe più che costruirlo intero. **Il riordino resta
fuori**: nella tavola non c'è.

Casi da dire, non da lasciare al caso: nessuna app installata; un'app **rotta**
(oggi `openApp` chiede conferma e propone la riparazione in chat — una pagina
fissa rotta è un'altra cosa, quindi non è scegliibile); un'app **esterna**
(`view_kind === 'external'`, apre un URL che il guscio non controlla: fuori da
questo giro, e il piano dice perché).

Chiavi i18n nuove in `it.json` **e** `en.json`.

### B5. Le tre specie, in quest'ordine

**1) `app` — una Jenny App.** La più autonoma, ed è quella con la tavola
completa (`PaginaApp`): intestazione di una riga (soprascritta «pagina N», nome
in serif, pastiglia **casa** per tornare), l'app a tutta pagina, i pallini sotto.
`openApp` si spezza in due — «costruisci la cornice per questo slug» e «mettila
in un velo a tutto schermo» — e la casa usa la prima. La cornice si costruisce
**solo per la pagina corrente** e si smonta uscendo (regola della tavola). Il
tema non obbliga a ricostruire (Parte 0).

**2) `stanza` — una stanza della casa.** Il riparentamento e la seconda via nel
CSS di B2. Esempio della tavola: Quaderni.

**3) `conversazione`.** La più economica in pixel e la più insidiosa: **non è
contenuto nuovo**, è la stessa chat riagganciata a un'altra chiave
(`switchTo`). Quindi una pagina `conversazione` che diventa corrente cambia
conversazione, esattamente come in officina passare da un cassetto all'altro
ridisegna la stessa vista (A2 — stessa forma, due volte). Da guardare: il
composer, il riaggancio del filo, e il fatto che la pagina 0 **è** già una
conversazione.

### B6. Prova sul telefono

Col dito finto: scorrere dalla chat, pressione lunga sui pallini, aggiungere
un'app, scorrere, tornare con la pastiglia **casa**, tirare su il cassetto dalla
striscia, togliere una pagina. Una foto per passo.

---

## L'ordine

| # | cosa | perché lì |
| --- | --- | --- |
| 1 | A1 — una sola ricerca + banco | sblocca tre linguette su quattro, ed è una riga |
| 2 | A2 — cassetto→cassetto | senza, il passo 1 si vede a metà |
| 3 | A3 — capi circolari | una riga |
| 4 | A4 — prova sul telefono | l'officina chiusa e verificata prima di aprire la casa |
| 5 | B0 — il gesto in condiviso | rifacimento a comportamento identico, con l'officina come prova |
| 6 | B1 — modello, config, rotte, tetto | senza, non c'è niente da disegnare |
| 7 | B2 — la pista e la pagina 0 | solo la chat: il carosello esiste ma ha una casella |
| 8 | B3 — la striscia e i tre gesti | qui il cassetto cambia comando |
| 9 | B4 — il foglio: aggiungi e togli | |
| 10 | B5.1 — pagine `app` | la prima specie vera, e la tavola ce l'ha intera |
| 11 | B5.2 — pagine `stanza` | riparentamento + seconda via CSS |
| 12 | B5.3 — pagine `conversazione` | |
| 13 | B6 — prova sul telefono | |

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende.

## Quel che questo piano non fa

- Non tocca **dove stanno le cose** nell'officina: i quattro cassetti restano
  quelli di `officina-tavole-plan.md`.
- **Non riordina** le pagine: nella tavola non c'è.
- Non offre il **cassetto** come pagina (la tavola lo esclude, con motivo).
- Non offre le **app esterne** come pagina.
- Non tocca il bottone del cassetto **dell'officina**.
