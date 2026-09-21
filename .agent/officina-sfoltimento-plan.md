# Sfoltimento dell'officina — cinque tagli

*21/09/2026 — branch `feat/la-casa`, dopo il giro di allineamento alla tavola
(`.agent/officina-allineamento-tavola.md`).*

## Da dove si parte, misurato

Foto intere sul telefono, dopo il terzo giro:

| cassetto | altezza |
|---|---|
| Cervello | 6.704 px |
| Mani     | 3.162 px |
| Memoria  | 4.373 px |

E la misura che apre questo giro: in Cervello `PERSONALIZATION` comincia a
y≈3.360. Cioè **metà del cassetto è i due gruppi parcheggiati**, che in nessuna
tavola esistono.

## Il criterio, in una riga

**Quel che la casa tiene già, l'officina non lo tiene una seconda volta. E
quel che resta deve avere una maniglia.**

Il secondo mezzo non è ornamentale: il banco
`test_officina_cassetti_contract.py::test_the_views_that_left_the_dock_are_still_reachable`
esiste proprio per quello, ed è lui a dire quando un taglio lascia una
schermata viva e irraggiungibile.

---

## Passo 1 — Via la pastiglia «connessa»

**Adesso.** `cassetto()` (`assets/mobile-header.js:71`) mette `stato: true`;
il mount disegna `.view-title-stato` con dentro `.view-title-punto`
(riga 216); `_dipingiStato()` (244-251) legge `wsManager.chatConnected` e due
listener nel costruttore (167-170) la seguono su `chat:open`/`chat:close`.

**Si toglie.**
- `mobile-header.js`: la chiave `stato`, il ramo `config.stato ? … : null`,
  `_dipingiStato()`, i due listener, il nodo nel markup e l'import di
  `wsManager` (è il suo unico lettore in questo file — da verificare col grep
  prima di toglierlo).
- `mobile-style.css:605-628`: `.view-title-stato`, `.view-title-punto`,
  `.is-giu`.
- i18n: `officina.stato.viva` e `officina.stato.giu`, in **it e en** (il banco
  di parità li vuole allineati).

**Banchi.** `test_officina_righe_contract.py::test_la_pastiglia_dice_una_cosa_che_il_guscio_sa_davvero`
si cancella e al suo posto va l'inverso: l'intestazione di un cassetto non ha
più nessun indicatore di connessione, e `mobile-header.js` non importa più
`ws-manager`. La mutazione che lo prova rossa: rimettere `stato: true`.

---

## Passo 2 — Via la Personalizzazione, il nome va in casa, Home è la chat

### 2a. Il gruppo sparisce

`_renderPersonalization` (`mobile-settings.js:427`) è quattro cose, e **tre
esistono già in casa**:

| cosa | dov'è già |
|---|---|
| temi | `casa-tu.js` — la passerella dei temi |
| mascotte (vista, taglia, finestra flottante) | `casa-jenny.js` — la stanza «Jenny» |
| nome di Jenny | **da nessuna parte** (solo nel wizard del primo avvio) |
| lingua dell'interfaccia | da nessuna parte — v. il rischio 1 in fondo |

Si cancellano, coi loro cablaggi e i loro import:
`_renderPersonalization`, `_renderTheme` (1772), `_renderMascot` (1798),
`_renderFloating` (1846), `_renderLanguage` (1892), `_renderHomeView` (1871),
la voce `personalization:` nella tabella dei gruppi (265), la stringa
`personalization` da `CASSETTI.cervello.sezioni` (101), e in `_wireSections`
i blocchi del toggle mascotte (2654), della taglia (2661), dei temi (2698) e
della lingua (2706).

Import che restano senza lettore e se ne vanno:
`THEMES, DEFAULT_THEME, setTheme`, tutto il blocco `mascot.js`,
`homeView, setHomeView, HOME_VIEW_CHOICES`, e `AppState` (il suo unico uso in
questo file è `AppState.theme` dentro `_renderTheme`).

i18n: `settings.personalization` e ogni chiave rimasta senza lettore — da
decidere **col grep, non a memoria**: `settings.mascot*` per esempio le legge
`casa-jenny.js` e **restano**.

### 2b. Il nome va in casa

Nuova riga nella stanza «Jenny» (`index.html`, sezione `casa-jenny-room`),
prima dell'interruttore «mostra»: etichetta + campo di testo, salvataggio con
`api.updateSettings({ bot_name })` — la stessa chiamata che usa già
`casa-model.js:177`, e il server la gestisce a `settings_api.py:1110`.

Perché lì e non in «Tu e Jenny»: quella stanza dice **com'è fatta e chi è**
(c'è già `SOUL.md`, la tua parte). Il nome è la stessa domanda.

### 2c. Home è sempre la chat

`goHome()` (`mobile-app.js:624-648`) legge `homeView()` e sa fare `'last'`.
Diventa: smonta gli overlay, collassa i controller, `switchMode('chat', false)`,
`_navPos = 0`. Il ramo `'last'` sparisce.

Poi si cancella **il modulo intero**: `assets/shared/home-view.js` e la sua
riga nel manifesto (`jenny/utils/android_assets.py:274`). Un file non estratto
e un file non esistente sono due cose diverse: la riga del manifesto va tolta,
o resta un 404 silenzioso.

**Banchi.** `test_section_collapse_contract.py` ha già
`assert "homeView()" not in body` su `openChat`: la stessa riga va aggiunta su
`goHome`, più un banco che il modulo non esiste più e che nessuno lo importa.

---

## Passo 3 — Via la modalità sviluppatore

Il giro è piccolo: **un solo lettore vero**.

- `assets/shared/advanced-mode.js` — si cancella, con la sua riga di manifesto
  (`android_assets.py:263`).
- `mobile-workspace.js:474` — `items = advancedMode() ? items : items.filter(…)`
  diventa `items = items.filter(i => !i.internal)`. Il filtro **resta acceso
  sempre**: è il comportamento a modalità spenta, che è quello che chiede
  l'utente.
- `mobile-workspace.js:214` — il listener `advancedmodechange` e il commento
  che lo spiega se ne vanno con lui.
- `_renderSystem` (`mobile-settings.js:2016-2024`) — via l'interruttore e la
  riga che lo spiega. Il gruppo `system` **sopravvive**: resta la versione e il
  consumo di token, e nessuno ha chiesto di toglierli.
- `mobile-settings.js:2647` — il cablaggio del toggle.
- i18n: `settings.advancedMode`, `settings.advancedModeHint`, it **e** en.

**Quel che non si tocca.** Il flag `internal` che il server mette su ogni file
(`webui/workspace_files.py:129`) e su ogni skill (`skills_api.py:86`) resta:
è quello che rende possibile il filtro. Sparisce l'interruttore, non la
distinzione.

**Banchi.** `test_unsaved_work_contract.py` cita `advancedmodechange` nella
sua intestazione e fra le quattro strade che smontavano l'editor: la strada
non esiste più, il banco si aggiorna spiegando **perché** ne restano tre — non
si indebolisce, si racconta. Più un banco nuovo: nel workspace il filtro
sugli `internal` non ha più una condizione davanti (mutazione che lo prova
rosso: togliere il `.filter`).

---

## Passo 4 — Via il cassetto delle app da Mani

**Adesso.** `CASSETTI.mani.porte = { telegram: ['launcher'] }` mette in fondo
alla card di Telegram una riga «Cassetto delle app». Non c'entra niente con
Telegram: ci è finita perché Telegram era l'ultimo gruppo di Mani.

**E non serve.** La maniglia vera esiste già ed è nel posto giusto: il
pulsante `#btn-launcher` accanto alla graffetta del composer della Console
(`officina.html:270`, cablato a `mobile-app.js:159`). La casa ne ha uno
identico. Il commento lì sopra racconta già questa storia.

**Si toglie.** La voce da `porte`, e con lei `PORTE_LANCIO`
(`mobile-settings.js:75`) e il suo ramo in `_wirePorte` (2503).

**Banchi.** `test_launcher_sheet_contract.py` deve dire che il foglio ha
**una** maniglia e che è quella del composer.

---

## Passo 5 — Memoria: via la wiki, e i file diventano una card

### 5a. La wiki esce dall'officina

**Adesso.** `CASSETTI.memoria.porte = { workers: ['graph', 'workspace'] }`:
in fondo alla card del giardiniere ci sono due righe, «Wiki» e «File».

Togliere la riga «Wiki» **lascia due schermate vive e senza entrata**:
`wiki` si apre solo dal grafo e `graph` solo dalla wiki — è un anello chiuso,
e il banco della raggiungibilità esenta `wiki` proprio dicendo «si apre da
dentro, da una pagina del grafo». Tolta la porta, quell'esenzione diventa
falsa.

**Decisione presa: escono tutte e due.** In casa ci sono già — l'elenco e la
mappa (`casa-pages.js`, `casa-map.js`) e il lettore (`casa-reader.js`) — e in
officina sono 1.689 righe.

Inventario del taglio:

| dove | cosa |
|---|---|
| `assets/mobile-wiki.js` | 746 righe, intero |
| `assets/mobile-graph.js` | 943 righe, intero |
| `officina.html` | `view-wiki`, `view-graph` (ricerca, legenda, svg), `drawer-audit`, `drawer-files`, `wiki-feedback-popover`, `wiki-feedback-dialog`, il commento su mermaid |
| `mobile-app.js` | i due import, le due fabbriche (81-82), i rami `wikiPage`/`wiki`/`graph` del popstate (181-201), `initialWiki` e il ramo «wiki senza mode» (238-243), il ramo `initialMode === 'graph'` (296-300), le due voci della mappa dei titoli (325-326), `wiki` in `_navStateFor` e nell'URL (422-432), `requestGraph`/`takePendingGraph`/`_pendingGraph` (100, 724-742), il commento sullo stato attivo del dock (834) |
| `mobile-header.js` | le config `wiki` e `graph`, le loro righe in `_refreshTitles`, l'azione `graph` (368-382), il ramo `currentMode === 'graph'` (414), e `projectChatAction()` (37), che dopo il taglio non ha piu' nessun lettore: le sue uniche due chiamate sono proprio queste due config |
| `mobile-style.css` | i blocchi `.wiki-*`, `.graph-*`, `.legend-*` |
| i18n (it+en) | `wiki.*`, `graph.*`, `header.audits`, `header.files`, `header.graph`, `header.pages` — ognuna col grep prima |
| `android_assets.py` | le righe 253 e 261, e **mermaid** (428-429): `mobile-wiki.js` è il suo unico lettore in tutto il prodotto |

**Quel che resta vivo, e perché.** `shared/wiki-search.js` (la legge
`casa-pages.js`), `vendor/d3@7` (la mappa di casa lo carica al primo tocco),
e **tutte le route del server** — `/api/graph`, `/api/page`, i riscontri: la
casa le usa.

**Quel che si perde davvero, detto qui e non scoperto dopo.** Gli arnesi da
operatore che la casa non ha mai portato dentro, e che `casa-reader.js`
elenca nel suo commento: il pannello dei riscontri, la sfoglia-file per
quaderno, il LaTeX a richiesta, la risoluzione dei link relativi fra quaderni
diversi. Restano nella storia di git.

**Banchi toccati** — nove file, ognuno o ripuntato sul gemello di casa o
cancellato con una nota che dice dove è finita la regola:
`test_async_continuation_contract.py`, `test_back_navigation_contract.py`,
`test_casa_pages_contract.py`, `test_content_link_contract.py`,
`test_graph_search_contract.py`, `test_project_views_contract.py`,
`test_section_collapse_contract.py`, `test_wiki_routes_server_scope.py`,
`test_wiki_to_project_button_client.py`.
Attenzione: `test_wiki_routes_server_scope.py` è **del server** — quello
quasi certamente resta intero.

### 5b. I file diventano una card aperta

**Adesso** sono una riga-porta in fondo al giardiniere. **Diventano un gruppo
suo**, con dentro quel che c'è davvero — la voce «i file veri» della tavola,
che finora non esisteva (il commento in cima a `mobile-settings.js` lo dice:
«vuole una lettura che questa schermata non fa»).

- `CASSETTI.memoria.sezioni` → `['quantoRicorda', 'dream', 'workers', 'file', 'backup']`.
  Subito dopo il giardiniere: è lui che li riempie.
- `_renderFile(d)`: segnaposto, e `_caricaFile()` chiama `api.listWorkspace('')`
  (→ `/api/workspace/list?path=`), butta gli `internal`, cartelle prima,
  massimo 8 righe e poi «…e altre N», ognuna col nome e cosa c'è dentro.
- L'ultima riga della card apre il gestore file intero. **È l'unica porta che
  sopravvive al giro**, quindi non vale più tenere il meccanismo generico: si
  cancellano `porte` da `CASSETTI`, `PORTE_ICONE`, `PORTE_ETICHETTE`,
  `_portePerGruppo`, `_wirePorte`, il quarto argomento di `_gruppo` e le classi
  CSS `.settings-porte`/`.settings-porta`. La riga della card se la disegna da
  sé.

**Banchi.** `test_officina_cassetti_contract.py` va riscritto in due punti: il
parser di `_cassetti()` non deve più cercare `porte`, e la raggiungibilità si
misura sulle maniglie nuove (dock + la riga della card dei file); `wiki` e
`graph` escono dall'elenco dei modi perché non esistono più. Più banchi nuovi:
la card non mostra file `internal` (mutazione: togliere il filtro), e non
stampa più di N righe (mutazione: alzare il tetto).

---

## Cosa questo giro **non** tocca

- Il gruppo `system` (versione + consumo token). Nessuno ha chiesto di
  toglierlo, e le due decisioni sospese — dove va il consumo token, e la
  modifica di protocollo per i conteggi per turno — restano sospese.
- Le route del server, tutte.
- Il flag `internal`: sparisce l'interruttore, non la distinzione.

## Rischi, in ordine di quanto mordono

1. **La lingua dell'interfaccia perde il suo unico interruttore.** Dopo il
   passo 2 la decide `i18n.detectLocale()` al primo avvio e basta. È coerente
   con la decisione già scritta in `casa-tu.js` (la riga della lingua è fuori
   dalla casa, con la misura che lo motiva), ma è un cambiamento di prodotto,
   non un riordino. Rimetterla è un blocco da dieci righe.
2. **Il passo 5a è il taglio che può rompere la navigazione del guscio**:
   popstate, link profondi, stato della history. Va fatto **da solo, per
   ultimo**, con la suite intera in mezzo e una prova a mano sul telefono
   (Indietro da ogni vista, Home da ogni vista, riapertura dell'app).
3. **Le chiavi i18n si tolgono in due lingue o il banco di parità salta.**
4. **Cancellare un file senza togliere la sua riga dal manifesto** dà un 404
   silenzioso sul telefono e niente in locale.

## Ordine, e come si verifica

Un passo = un commit, suite verde a ogni passo (`ruff` + il sottoinsieme
`pyright` bloccante + `pytest -q`), e **ogni banco nuovo provato rosso con una
mutazione** prima di dirlo fatto.

1. pastiglia · 2. personalizzazione + nome + Home · 3. sviluppatore ·
4. cassetto app · 5b. card dei file · 5a. wiki fuori (per ultimo, da solo).

Alla fine: build release dal worktree pulito, `adb install -r`, e foto intere
dei tre cassetti con `cuci.py` da confrontare con quelle di oggi. La misura
che conta è Cervello: deve scendere di circa 3.000 px.

---

# Coda: la scheda dei file smette di essere un riassunto

*21/09/2026, stesso giorno, dopo i cinque tagli e la rimozione di `pinnedWiki`.*

## La misura che l'ha aperta

La scheda nata al passo 5b mostrava **otto righe della radice**, poi «e altre N
voci», poi un bottone verso il gestore file. L'utente, aprendola: «ha una lista
inutilissima e poi un tasto che rimanda alla lista completa».

Ha ragione, e il rimedio non era togliere il tetto: un elenco piatto della sola
radice, che non si tocca, non risponde a nessuna domanda — lungo o corto.
**Quel che serviva era il contenuto, non un campione più grande.**

Tre cose lette prima di decidere:

- il gestore file esiste già, completo (icone, miniature, tieni-premuto per
  rinomina/cancella/condividi, «nuovo»), ed è **1.106 righe**;
- **quel bottone era la sua unica porta**: nessun altro punto dell'app apriva
  più `view-workspace` — `_returnMode`, scritto per l'origine «Apps → modifica
  skill», era già sempre `null`;
- l'esploratore è una **griglia** (`minmax(88px, 1fr)`, ~6 per riga sul Titan
  2), non un elenco: 40 voci sono 7 righe, cioè una schermata. La scheda non
  diventa un mostro.

Quindi: il gestore **entra nella scheda**, invece di stare dietro di essa.

## La forma scelta

Una sola cosa era davvero da decidere, ed è andata all'utente: cosa succede
toccando un file. Risposta: **schermata intera, e Indietro riporta lì**.
Girare tra le cartelle non lascia mai Memoria; aprire un documento è una
schermata sua, come in qualunque gestore file.

- `view-workspace` **è** il file aperto, e basta. Il breadcrumb che gli resta
  porta il nome del file e il tasto «Salva».
- L'esploratore (briciole + griglia + stato vuoto) è il corpo della scheda «I
  file veri», ultima in Memoria — la sua altezza dipende da quanti file ci
  sono, e sotto non deve esserci niente da sotterrare.
- `WorkspaceController.mount(host)` **riaggancia** i tre nodi a ogni ridisegno
  della scheda. `render()` riscrive `contentEl` per intero a ogni apertura del
  cassetto e dopo ogni salvataggio: un riferimento tenuto dal costruttore
  scriverebbe in un DOM buttato via. La **cartella** invece vive nel
  controller, quindi salvare un'impostazione di Memoria non rimbalza alla
  radice chi stava a tre livelli.
- Indietro: `SettingsController.handleBack()` gira la pressione a
  `handleCardBack()`, che risale di una cartella e solo alla radice lascia
  proseguire la catena.
- «Nuovo» è passato dall'intestazione della vista al bottone accanto alle
  briciole. «Aggiorna» non è tornato: `cassetto()` non ce l'ha per scelta
  scritta, e la scheda si ricarica a ogni apertura.

## Quel che è caduto per conseguenza

- **`_returnMode`**, il campo che diceva da quale sezione si era aperto il
  file. Con una sola origine aveva un valore solo, e si portava dietro
  l'azzeramento in `deactivate()` che *era* la superficie del difetto già
  corretto una volta. Sostituito da una destinazione scritta dove si usa e da
  un parametro `stay` per Home, che è la stessa richiesta detta a voce alta.
  Stessa lezione di `pinnedWiki`, poche ore prima.
- **`_wirePorte` e `data-porta`**: zero porte rimaste. Un cassetto contiene;
  mandare altrove era l'unica cosa che le porte sapessero fare.
- `refreshGrid`, `showLoading`/`hideLoading`, `viewEl`, `init()`/`ready`:
  senza lettori dopo il trasloco.
- Le chiavi i18n del riassunto (`cartella`, `altri`, `vuoto`, `errore`), in
  entrambe le lingue.

## Come si verifica

Banco `test_officina_file_client.py` **riscritto**: non misurava più niente,
perché il riassunto che misurava non esiste. Dodici prove sul gestore vero, in
node su un DOM finto — filtro `internal`, ordine, **nessun campione**,
riaggancio che non torna alla radice, girare senza lasciare Memoria, Indietro
che risale e poi lascia andare, chiudere un file che torna nella sua cartella,
Home che non naviga due volte, cartella illeggibile ≠ cartella vuota, «nuovo»
che crea dove si sta guardando, risposta vecchia che non sovrascrive la nuova.

Sette mutazioni, tutte rosse — e una prima passata **verde**, che ha trovato il
banco debole: il tetto rimesso sulle sole cartelle non si vedeva, perché la
prova contava soltanto file. Corretta contando entrambe le famiglie.

**Resta da fare: la prova sul telefono.** Il dispositivo non era collegato alla
fine di questa passata. Da provare a mano: Indietro da dentro una cartella, da
un file aperto, da un file modificato e non salvato; Home dagli stessi tre
punti; riapertura dell'app con un file aperto (deve ripartire da Memoria, non
da una schermata bianca).
