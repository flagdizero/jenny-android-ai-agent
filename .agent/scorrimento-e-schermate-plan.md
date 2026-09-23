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

### B2. La geometria — ✅

**Fatto il 22/09/2026.** `casa-pista` con dentro un `casa-pagina` per pagina, la
chat nel primo; `CasaPagine` in `casa-pagine.js`; `getSchermate`/`setSchermate`
nel client.

Tre cose decise costruendo, diverse da come le avevo scritte:

- **L'intestazione resta fuori dalla pista.** E' gia' cromatura del guscio —
  fuori dalla chat diventa il titolo della stanza col tasto indietro — e
  duplicarla in ogni pannello vorrebbe dire tenerne N allineate. Il suo
  *contenuto* cambiera' con la pagina, come in officina fra i cassetti.
- **I pallini non si ripetono per pagina**, anche se le tavole li disegnano
  dentro ognuna: quelle sono schermate autoportanti e dovevano. Il comando che
  dice dove sei non deve muoversi mentre ti muovi.
- **Sei regole CSS diventate una.** Nascondere la chat fuori dalla sua vista
  nominava filo, stato vuoto, riga di lavoro, stato del filo, allegati e
  composer; adesso si nasconde la pista. Dimenticarne una avrebbe lasciato quel
  pezzo a occupare spazio dentro una stanza.

**Un effetto collaterale dichiarato:** `.casa-empty` e' `position:absolute;
inset:0` e finora si ancorava al guscio. Adesso si ancora al pannello — e
doveva comunque, perche' appena la pista prende un `transform` il contenitore
cambia da se' e lo stato vuoto salterebbe a meta' gesto. Il centro della frase
scende di mezza intestazione.

**Provato:** 16 casi in node **sui file veri** (import compresi, con un finto
client API), e **quattro mutazioni**. Due sono sopravvissute alla prima
stesura — la guardia «con la sola chat il gesto non parte» e «il pannello della
chat non si ridisegna» — perche' il DOM finto non aveva il pannello della chat
e `querySelectorAll` ignorava il selettore. Corretto il finto, tutte e quattro
cadono. Geometria verificata anche nel browser: intestazione 64px, pista 502px
esatti sotto, composer in fondo, tre pannelli a 0/590/1180 e ognuno che atterra
a zero.


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

### B3. La striscia dei pallini — ✅

**Fatto il 22/09/2026.** `casa-pallini` sotto la pista, un pallino per casella,
quello corrente allungato (5px → 16px come la tavola). Il bottone `#casa-drawer`
non c'e' piu'; l'officina tiene il suo, che e' un elemento diverso.

**Tre gesti, e uno in piu' che la tavola non chiedeva:** di lato cambia pagina,
**su** apre il cassetto, e — aggiunto — **toccare un pallino** ci va. I pallini
sono `<button role="tab">` veri: chi i gesti non li fa, o non puo' farli, cambia
pagina lo stesso, e chi legge lo schermo sente «Pagina 1 di 2» invece di
silenzio.

**Il «tira su» non usa `touchmove`,** e non e' una scorciatoia: si misura fra
`touchstart` e `touchend`. Il foglio del cassetto non e' trascinabile — si apre
e basta — quindi seguire il dito prometterebbe un movimento che poi non c'e'. E
cosi' il riconoscimento di un trascinamento resta **tutto** in
`shared/gesto-orizzontale.js`, che e' l'invariante di B0.

**Il pavimento di Jenny conta anche la striscia** (`h + striscia`): senza, si
appoggerebbe sopra la presa con cui si tira su il cassetto.

**Il rischio, dichiarato.** `test_launcher_sheet_contract` porta la storia di un
cassetto diventato irraggiungibile **due volte**. Questa e' la quinta variazione
ed e' la piu' rischiosa: un pulsante si vede, un gesto no. Quel che regge
l'invariante e' che la striscia **c'e' sempre**, anche con la sola chat, e il
banco adesso chiede quello — piu' che il vecchio pulsante non torni, perche' due
ingressi renderebbero la striscia muta.

**Da verificare sul telefono e solo li':** lo swipe verso l'alto dal fondo dello
schermo e' anche il gesto di home di Android. La striscia sta sopra
`env(safe-area-inset-bottom)`, ma se sul Titan 2 le due zone si toccano il
cassetto non si aprira' mai e sembrera' un difetto del codice.

**Provato:** 24 casi (8 nuovi sulla striscia), quattro mutazioni tutte prese —
via il controllo verticale/orizzontale, soglia a zero, pallini non ridipinti,
pallino non cliccabile.


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

### B4. Il foglio «Le pagine di casa» — ✅

**Fatto il 22/09/2026.** `<dialog>` + `showModal()`, come il foglio di
«Segnala» e per lo stesso motivo: il top layer sta **sopra la mascotte** senza
un secondo `z-index`. Elenco, ✕ per togliere, riga tratteggiata con le tre
scelte, e il secondo passo (quale) **nello stesso foglio** — due fogli impilati
su un telefono lasciano mezzo schermo di velo e nessuno sa piu' quale
«indietro» chiuda cosa.

**La pressione lunga e' entrata nel modulo condiviso**, non in `casa-pagine.js`:
annullarla quando il dito si muove vuol dire guardare i movimenti, e i movimenti
si guardano in un posto solo. Cosi' tirare su il cassetto o cambiare pagina non
fa comparire il foglio.

**Una stanza della tavola non esiste.** `PagineGestione` da' «Quaderni» come
esempio di stanza, ma `openPages()` legge il quaderno **dalla conversazione
corrente**: appesa a una pagina mostrerebbe cose diverse a seconda di dov'eri
prima. Le stanze appendibili sono le cinque autonome — Tu e Jenny, Jenny, Chi
risponde, Aggiornamenti, Backup — e chi vuole un quaderno sotto il pollice ci
mette **la sua conversazione**, che e' la stessa cosa detta bene. Stessa
famiglia di «permessi di scrittura»: la tavola disegna una cosa che il prodotto
non ha.

**Niente conferma sul togli:** una pagina si rimette con tre tocchi, e una
conferma per un gesto annullabile e' solo un tocco in piu' ogni volta.

**Sul banco sono cadute due cose mie.** Il DOM finto teneva **un ascoltatore per
tipo**, e sulla striscia adesso ce ne sono due: si sovrascrivevano, e il banco
sarebbe stato verde proprio sul caso in cui i due gesti si pestano i piedi. E
una `disarmaPressione()` dentro `muove` non uccideva nessuna mutazione — era
ridondante con le due guardie vere — quindi e' uscita invece di essere
puntellata.

**Una correzione che mi sono fatto da solo:** avevo scritto che senza il
`clearTimeout` in `azzera()` due tocchi rapidi avrebbero aperto il foglio da
soli. Falso — la guardia su `inAscolto` copre quel caso. Quel che davvero
impedisce e' che una pressione **arrivi in anticipo** riusando il contatore del
tocco precedente. La prova adesso dice quello, ed e' l'unica formulazione che
uccide la mutazione.

**Provato:** 37 casi, e sei mutazioni di cui due sopravvissute alla prima
stesura e poi chiuse.


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

**1) `app` — una Jenny App. ✅ Fatto il 22/09/2026.** La più autonoma, ed è quella con la tavola
completa (`PaginaApp`): intestazione di una riga (soprascritta «pagina N», nome
in serif, pastiglia **casa** per tornare), l'app a tutta pagina, i pallini sotto.
`openApp` si spezza in due — «costruisci la cornice per questo slug» e «mettila
in un velo a tutto schermo» — e la casa usa la prima. La cornice si costruisce
**solo per la pagina corrente** e si smonta uscendo (regola della tavola). Il
tema non obbliga a ricostruire (Parte 0).

**2) `stanza` — una stanza della casa. ✅ Fatto il 22/09/2026.** Il riparentamento e la seconda via nel
CSS di B2. Esempio della tavola: Quaderni.

**3) `conversazione`. ❌ Non si fa — decisione dell'utente, 22/09/2026.**

Le altre due specie hanno contenuto proprio: un'app e' una cornice sua, una
stanza e' un elemento suo. Una conversazione no: **la chat in casa e' una
sola** — un filo, un campo di scrittura, un collegamento — quindi una pagina
del genere non avrebbe niente da mostrare e potrebbe solo far cambiare
conversazione a quella che c'e' gia'.

Le strade erano quattro, tutte con un prezzo visibile: far sparire la pagina a
meta' scorrimento (la chat che si sposta), rimbalzare indietro dopo averci
scorso sopra, costruire una **seconda chat** vera (una funzione, non una specie
di pagina), o farne una **porta**: una scheda col nome del quaderno che al
tocco ci entra e riporta alla pagina 0.

L'utente ha scelto di non farne niente per ora. La specie e' uscita **anche
dallo schema**, non solo dal foglio: una pagina che il prodotto non sa
disegnare non deve poter esistere nemmeno in un `config.json` scritto a mano —
stessa regola del cassetto.

### B6. Prova sul telefono — ✅ fatta il 22/09/2026, e ha trovato due difetti

**1. «Non hai Jenny App» a chi ne ha quattro.** `AppsSource.ensureLoaded()` non
e' asincrona: avvia le due fetch e torna. Il foglio leggeva `jennyApps` mentre
era ancora l'array vuoto di partenza. Corretto con `attendiJennyApps()`, e il
banco lo prende adesso perche' la fonte finta risponde **dopo un giro**, come la
rete vera.

**2. Il «tira su» della tavola non puo' funzionare.** Con la navigazione a gesti
— `navigation_mode = 2`, il caso normale — Android si prende lo swipe dal bordo
basso per il gesto di home: all'app arriva `touchcancel`, **mai** `touchend`.
Provato con una build diagnostica che apriva il foglio proprio su
`touchcancel`, e il foglio si apriva.

La zona di quel gesto **non e' escludibile**: `setSystemGestureExclusionRects`
vale per il gesto indietro, sui bordi laterali. Quindi non c'era niente da
aggiustare nel codice: la tavola chiede una cosa che il sistema non concede.

**Conseguenza: il bottone del cassetto e' tornato.** Per un giro il cassetto e'
stato **irraggiungibile dalla casa** — la terza volta che questa porta sparisce,
e stavolta l'ho tolta io. La striscia resta con i due gesti che funzionano:
toccare un pallino per cambiare pagina, tenere premuto per il foglio.

**3. Due difetti nel montaggio, trovati scrivendo il banco che la pagina bianca
ha chiesto.** La cornice di una Jenny App porta il token nell'indirizzo, e
`openApp` da sempre si assicura che il segreto ci sia prima di costruirne una:
estraendo `cornicePerApp` quella precondizione era rimasta indietro — senza
segreto l'indirizzo prende `token=undefined`, cioe' un 401 e una pagina bianca.
E aspettare il segreto rende il montaggio asincrono, quindi un dito veloce fra
due pagine faceva riempi → svuota → riempi e finiva con **due cornici**: la
stessa app viva due volte. Un flag condiviso non distingue i due tentativi, e
adesso ognuno porta il proprio numero.

**4. La pagina bianca: trovata, ed era la piu' insidiosa.** Con
`overflow: hidden` sulla **pista** — l'elemento che porta anche il `transform` —
un `<iframe>` dentro una pagina **si carica e non dipinge**. Bisezionato sul
telefono un passo per volta: `load` arriva, l'elemento e' `visible`, opacita' 1,
`display: block`, misura 574x450, sta nel documento, ha un `contentWindow`. E a
schermo resta nero. La stessa app aperta dal cassetto funziona benissimo, il che
ha escluso l'app.

Spostata la cornice nel corpo del documento si vede subito; tolto `overflow`
alla pista, pure. **Non** bastano i rimedi soliti: `translateZ(0)` sulla cornice
o sul pannello non cambia niente, e nemmeno montare a scivolata finita.

Il ritaglio non puo' salire al guscio: Jenny e' `position:absolute` con un
`right` negativo — sporge apposta — e li' verrebbe tagliata. Quindi un elemento
in mezzo: `.casa-vetrina` ritaglia, `.casa-pista` si muove.

**5. Indietro non chiudeva il foglio.** Un `<dialog>` modale si chiude da se'
con Escape, ma li' Indietro arriva dal guscio nativo come un evento suo: va
nominato in `_closeOverlays()`. Il foglio di «Segnala» accanto porta **lo stesso
commento** da prima, e non e' bastato a farmelo ricordare — adesso c'e' un
banco. Aggiunto anche il tocco fuori, che un `<dialog>` non fa da se'.

**Chiuso il giro (22/09/2026, sera).** Sul telefono: striscia, foglio, scelta
app, scelta stanza, pagina con dentro la Jenny App **che si vede**, pagina con
dentro la stanza Backup, ritorno alla chat, togliere le pagine, e — la prova che
piu' mi premeva — **la stanza prestata torna a casa sua**: Backup si riapre dal
suo percorso normale. Le impostazioni dell'utente sono state rimesse com'erano
(zero pagine, tema Kyoto).

**Quel che invece ha funzionato al primo colpo:** la striscia, il foglio, la
scelta, il salvataggio (sopravvive al riavvio dell'app), il ritorno alla chat,
il tocco sul pallino.


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

## B7 — Lo scorrimento arriva dentro le app (22/09/2026, dopo il giro)

Chiuso B6, l'utente ha provato a scorrere **stando dentro una Jenny App** e non
succedeva niente. Misurato con `adb`: **in nessuna delle due direzioni**, non
solo in una. La pagina di una app è tutta l'app, intestazione compresa, e il
dito che la tocca al guscio non ci arriva mai.

**Come è diviso.** Il gesto lo riconosce la app — solo lì dentro si vede il suo
DOM, quindi solo lì si può cedere il gesto a una sua tabella larga. Cosa farne
lo decide il guscio, che è l'unico a sapere se una pagina di fianco c'è, e lo
decide **a ogni gesto**: fra l'uno e l'altro il cassetto può aprirsi. Il kit
importa lo stesso `shared/gesto-orizzontale.js` della casa e dell'officina: le
soglie restano in un posto solo.

**La trappola, che nessun banco poteva vedere.** Il kit è servito da un'altra
origine e senza `crossorigin`, quindi per Chromium è uno «script
CORS-cross-origin»: la base per il suo `import()` è `about:blank`, e
`import('/html-mobile/...')` muore su *Failed to resolve module specifier*
prima di toccare la rete. Il primo giro sul telefono è stato verde in tutti i
banchi e morto sullo schermo. Misurato su Chrome del telefono — stesso motore
della WebView — con un banco di quattro file: col percorso l'import non parte,
con l'indirizzo intero (`new URL(..., location.href)`) passa. Il pannello del
browser del Mac **non** serve a questa prova: blocca lui gli iframe
(`ERR_BLOCKED_BY_CLIENT`).

**Provato sul telefono, non dedotto:** chat → todo scorrendo sulla chat; todo →
chat scorrendo **dentro** l'app; un gesto corto e lento resta dov'è; lo scroll
verticale dentro l'app va giù e torna su intatto; il tocco sui controlli
dell'app funziona ancora.

**Quel che resta fuori.** Una app che non carica il kit resta com'era — niente
scorrimento, nessun errore. E una app che si facesse un suo aggeggio a dito
orizzontale (un cursore, uno scorri-per-cancellare) non verrebbe riconosciuta:
`dentroScorrevoleOrizzontale` cede solo a chi *scorre*. Nessuna delle quattro
app installate usa il dito, quindi il caso non ha ancora un proprietario e non
gli è stata data una via d'uscita: il giorno che serve, è una riga nel kit.


## B8 — Il righello (22/09/2026, subito dopo B7)

Con B7 addosso, trascinare **dentro** una app faceva vibrare tutta la schermata.
Non erano fotogrammi persi (`gfxinfo`: 0,22% di janky frames — la prima misura
è stata sulla cosa sbagliata): era la schermata che inseguiva se stessa.

`clientX` è relativo alla finestra di chi ascolta. Per l'officina e per il
guscio della casa è indifferente — la loro finestra sta ferma, si muove solo la
pista dentro. Ma dentro una Jenny App **la finestra è la cornice che la pista
sta trascinando**: il dito si sposta di 20, la pista si sposta di 20, la
cornice va con lei, e al battito dopo il dito «è tornato indietro di 20».
Avanti, indietro, avanti.

Misurato su Chrome del telefono con i due righelli fianco a fianco dentro una
cornice trascinata, un solo scorrimento:

| dx client | dx screen | | dx client | dx screen |
| --- | --- | --- | --- | --- |
| 237 | 284 | | 257 | 320 |
| 268 | 296 | | 302 | 328 |
| 243 | 300 | | 271 | 336 |
| 288 | 311 | | 309 | 340 |

Il primo oscilla, il secondo sale dritto. Lo scorrimento era 850 punti fisici a
dpr 2,5: **340** — cioè `screenX` è in pixel CSS come `clientX`, e le soglie,
che si confrontano con `clientWidth`, conservano il significato.

**Una regola sola per tutti e tre i posti**, non due con un'eccezione: il giorno
che qualcun altro trascina la cornice che lo contiene, non deve riscoprirlo.
Vale finché non si muove la *finestra* a metà gesto, cosa che nessuno dei tre fa.

Ogni dito finto dei banchi porta adesso **due righelli**, sfalsati apposta, e
due casi nuovi li fanno litigare come in produzione — uno per verso, perché
solo il secondo uccide la mutazione al contrario.

**Provato sul telefono:** chat → pagina scorrendo sul guscio; pagina → chat e
pagina → pagina scorrendo **dentro** l'app, nei due versi. L'officina non è
stata riaperta col dito: la coprono i suoi banchi (che girano sul suo codice
vero e diventano rossi mutando il righello) più il fatto che `screenX` funziona
in quella stessa WebView al livello del guscio.

## B5.3 riaperto — pagine conversazione (23/09/2026)

Tagliato il 22/09 come «seconda chat viva»; riaperto il 23/09 in un'altra forma
— una scorciatoia che cambia conversazione, travestita da pagina. Piano a sé:
`.agent/pagine-conversazione-plan.md`.

## B9 — Chi si tiene il gesto dentro una pagina (23/09/2026) — ✅ `c0988df`

**Il difetto.** Dentro una app il riconoscimento cedeva il gesto a una cosa sola,
lo scorrevole nativo. Un componente col suo gesto — un carosello, una mappa, il
«tieni premuto» di Life Counter — si muoveva **insieme** alla pagina.

**La regola, dell'utente:** «se swipe orizzontale è swipe, se click è click. Se però
ci sono eventi di swipe su pagina vince lo swipe sul componente». Prima c'era stata
una proposta sbagliata, «chi prende il dito per primo se lo tiene», e l'utente l'ha
bocciata con la domanda giusta: «se il bottone mi ruba lo swipe non potrò mai
swipare?». Un bottone reagisce subito anche lui: decide il **movimento**, non il tocco.

**Come si riconosce chi si trascina** (`gestoDiUnComponente` + il `muove`):

- scorrevole nativo che può ancora scorrere in quel verso (com'era, e la striscia
  dei temi in Impostazioni ci conta);
- `<input type="range">`;
- un `touch-action` che l'orizzontale non lo lascia al browser (`none`, `pan-y`,
  `pinch-zoom`) **su qualcosa che non è un comando**. I − e + di Life Counter hanno
  `touch-action: none` e sono bottoni: non contano. La risalita arriva al `body`,
  dove un gioco a tutto schermo lo dichiara;
- un `preventDefault` sul `touchmove` prima che l'asse sia deciso: è così che un
  carosello scritto a mano blocca il browser, e un bottone non lo fa mai;
- più testo selezionato e secondo dito, che fermano la pagina in tutti e tre i gusci.

**Quando vince la pagina, dentro una app** (`esclusivo`, solo il kit): l'app riceve
`pointercancel` + `touchcancel` e fino al rilascio non sente più il dito — fermato in
discesa sulla finestra. È l'ACTION_CANCEL di Android. Il kit ora ascolta sulla
finestra e non sulla radice, per sentire il dito **dopo** l'app.

**Resta fuori:** un `+1` che un'app conta al `pointerdown` è già contato quando si
capisce che era uno scorrimento. Non si rimedia dal guscio: la skill `app-creator`
ora dice di agire al `click` e di dichiarare con `pan-y` i componenti che si trascinano.
Life Counter conta ancora all'appoggio.

**Telefono, 23/09/2026 18:22:** da dentro Todo, uno scorrimento partito su una casella
cambia pagina e la casella resta com'era (47/79 prima e dopo); il filo di Todo scorre
ancora in verticale; Impostazioni ↔ Quaderni ↔ Todo ↔ chat scorrono. Banco:
`tests/webui/test_gesto_componenti_client.py`, 10 test, 18 mutazioni tutte uccise.
