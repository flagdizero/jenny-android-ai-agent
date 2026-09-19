# Le pagine di un quaderno — il piano

La tavola è `Quaderno.dc.html`, «Pagine e mappa». Ma le righe che decidono
questo giro non stanno lì: stanno nelle due tavole grandi in cima al foglio, e
vanno lette per prime perché cambiano il perimetro, non i dettagli.

**`Concetto.dc.html`, colonna «La casa», terza riga:**

> Wiki, grafo e progetti sono una cosa sola: i quaderni, che sono conversazioni.

**`Mappa.dc.html`, riga «Wiki / Wikis / Graph (tre nomi)»:** lato utente, «"8
pagine" nell'intestazione della chat del quaderno». Lato operatore, **un
trattino.**

Quel trattino è la decisione. Nel disegno finale la wiki e il grafo **non
restano all'operatore**: escono dai tre nomi che hanno oggi (Wiki, Wikis,
Graph), diventano una porta sola in casa, e di là non ne resta niente. Quindi
la mappa non è un lusso da rimandare — è metà di questa pagina.

**La parte difficile non è l'elenco.** Un elenco è una `fetch` e un ciclo. È
che la casa, fino a stasera, è una schermata sola: un filo che scorre e un
composer sotto. Indietro ha due strati e **una sola cosa sotto da cui tornare**
(`casa-app.js::handleHardwareBack`), Jenny appoggia i piedi sul composer, e
tutto il guscio dà per scontato che ci sia una conversazione a schermo. Questo
giro introduce la seconda stanza della casa, e quasi tutto il costo sta lì.

## Il rilievo

**1. L'ingresso è già disegnato, e non esiste.** Nella tavola della chat di un
quaderno l'intestazione porta una pastiglia «📖 8 pagine» che apre questa
pagina, accanto a un tondo con l'avatar (`TuEJenny.dc.html`, un'altra tavola).
In `index.html:68` a destra c'è solo la porta dell'officina. Il conteggio per
quaderno era uno degli scarti dichiarati del giro scorso
(`.agent/casa-notebook-checklist.md`): è questo. L'«8» della tavola è un
segnaposto — il quaderno che disegna ne ha trentaquattro.

**2. Il conteggio esiste già lato server, nel posto sbagliato.**
`build_home_graph` (`jenny/webui/wiki.py:399`) mette `page_count` nel `degree`
del nodo di ogni wiki; `/api/projects` — la chiamata che il pannello fa già
ogni volta che si apre — torna solo `{name, modified}`
(`jenny/webui/wiki_routes.py:86`, servita da `_projects_list`, riga 399).
Misurato sul Titan: contare ricorsivamente i `.md` di **tutti e 14** i quaderni
(464 file) costa **20 ms** sulla flash. Il conteggio è gratis dove l'elenco c'è
già.

**3. Una chiamata sola porta elenco, ricerca e mappa.**
`/api/graph?wiki=<nome>` (`wiki_routes.py:249`) risponde con
`nodes[{id,label,path,group,degree,title}]`, `edges`, e `search`: l'indice
full-text impacchettato che `wiki_search.py` costruisce dalla **stessa** lettura
del disco — e il commento sul posto dice perché viaggiano insieme e non da due
endpoint (fra le due chiamate la wiki può cambiare, e il client accenderebbe i
nodi sbagliati). Gli `edges` sono già lì: l'elenco e la mappa **non sono due
caricamenti**, sono due rese della stessa risposta.

Quanto pesa, misurato sul quaderno più grosso che c'è (62 pagine, 163 kB di
markdown): **18.318** occorrenze di token, **3.727** termini unici, **32 kB** di
dizionario. Con postings, offset e pesi in base64 la risposta si assesta
intorno ai **110 kB**; il quaderno mediano — una ventina di pagine — sta sotto i
40. E il server la tiene in cache per fingerprint (`WikiSearchService`):
rientrare senza che nulla sia cambiato costa una passata di `stat`.

**4. Le postings sono indici di *posizione* nell'array `nodes`**, non id
(`wiki_search.py:150`). È il vincolo di progetto di quel modulo — la maschera
che torna è una `Uint8Array` indicizzata per posizione — e vuol dire che
**l'ordine in cui disegno l'elenco non può rinumerare i nodi**.

**5. Il vocabolario dei gruppi esiste già a metà.** `graph.concepts`,
`graph.entities` e `graph.other` sono tradotti in tutte e due le lingue;
`summaries` ha un colore (`mobile-style.css:3882`) ed è un gruppo che il grafo
riconosce (`mobile-graph.js:858`), ma **non ha un'etichetta**: la legenda
dell'officina (`officina.html:244-246`) ha tre righe, non quattro. Misurato sui
quaderni veri: **9 su 14** usano `concepts/entities/summaries`, **5 su 14 sono
piatti** — ogni pagina cade in `other`. Aggiungere `graph.summaries` ripara
anche la legenda di là, finché quella legenda esiste.

**6. I colori sono già quelli dell'officina.** `concepts` → `--accent`,
`entities` → `--error`, `summaries` → `--ok`, `other` → `--text-faint`
(`mobile-style.css:3880-3883`). Sono token di tema, quindi la casa li usa come
sono: un pallino verde vuol dire la stessa cosa nei due gusci, e non c'è un
secondo vocabolario da tenere allineato.

**7. La casa non ha viste.** `casa-app.js` monta un filo e un composer e basta.
`_closeOverlays()` conosce due strati — la tendina e l'immagine ingrandita — e
`handleHardwareBack` ha una cosa sola sotto da cui tornare, il quaderno. Una
seconda schermata è un concetto nuovo in questo guscio, e il tasto Indietro è il
posto dove si vede se è stato introdotto bene.

**8. Il pavimento di Jenny è il composer.** `--casa-composer-h` lo scrive un
`ResizeObserver` montato su `.casa-composer`, `.casa-pending`, `.casa-activity`
e `.casa-wire` (`casa-app.js::_bindComposer`). Su una schermata senza composer
quel token resterebbe fermo sul valore di un elemento nascosto, e Jenny
appoggerebbe i piedi su una riga che non c'è.

**9. E su questa pagina Jenny è al bordo.** La tavola la disegna a
`right: -56px`; nelle due tavole della chat sta a `right: -30px`. Sono
esattamente i due ancoraggi che abbiamo messo in comune ieri — `DOCK_RATIO` e
`OUT_RATIO` per una taglia di 120. Il disegno dice quindi una regola che ieri
non sapevamo di aver preparato: **fuori dalla chat, Jenny si mette via da
sola.** (La riga «Mascotte» di `Mappa.dc.html` dice «intera in chat, assente
altrove»: la tavola della pagina è più precisa della prosa, e vince lei —
assente e al bordo non sono la stessa cosa, e al bordo la si ritrova.)

**10. La mappa costa 280 kB, e adesso è di casa.** `vendor/d3@7/d3.min.js` pesa
279.706 byte, e `mobile-graph.js` sono 943 righe legate a
`graph-svg`/`graph-loading`/`graph-wrap` e a `window.mobileApp`. Il guscio della
casa oggi si porta dietro `marked` e `DOMPurify` e nient'altro, ed è scritto nel
suo commento perché (`index.html:8-12`): «la differenza non è estetica — è il
tempo che passa fra il tocco sull'icona e la prima riga leggibile». Il disegno
vuole la mappa in casa; quel commento vuole che non la paghi chi non la apre.
Le due cose stanno insieme in un modo solo: **D3 si carica al primo tocco su
«Mappa», mai prima.** `ensureVendor` in `shared/utils.js` fa già esattamente
questo per l'officina.

**11. Il lettore esiste già, ed è un arnese da officina.** `/api/page` torna
`{title, html, raw, frontmatter}` con l'HTML già reso dal server
(`wiki_routes.py:298`); `mobile-wiki.js` sono 746 righe che lo sanificano,
cablano i wikilink, risolvono i link relativi, disegnano le briciole di pane,
caricano KaTeX a richiesta e aprono il pannello degli audit. Alla casa serve la
prima riga di quell'elenco, non l'elenco.

## La forma

Quel che la tavola disegna, dall'alto:

- un occhiello che è un comando — **«‹ torna alla chat»** — e sotto il nome del
  quaderno;
- a destra una pastiglia piena **«Parlane»**, che riporta alla chat;
- una barra a due linguette, **Pagine | Mappa**;
- un campo **«Cerca nelle pagine…»** (che è già, parola per parola,
  `graph.searchPlaceholder`);
- l'elenco: pallino del gruppo, nome della pagina, etichetta del gruppo a
  destra, righe separate da una linea sottile;
- la maniglia del cassetto in fondo (tavola del giro dopo);
- Jenny, al bordo.

Due cose della tavola sono d'accordo fra loro e lo dico perché è il motivo per
cui questa pagina ha senso: l'occhiello e la pastiglia fanno **la stessa cosa**.
Non è una svista — è una pagina da cui si esce, e si esce da dove guardi: in
alto a sinistra se stai leggendo l'intestazione, in basso a destra col pollice.

## I passi

Sono tre atterraggi, in quest'ordine, perché ognuno lascia la casa in uno stato
in cui si può stare:

**Primo — la stanza e l'elenco.**
1. La pastiglia «N pagine» nell'intestazione, solo dentro un quaderno; il numero
   da `/api/projects`, che impara a contare.
2. La seconda schermata: `.casa-shell` scambia quel che sta fra intestazione e
   fondo, Indietro la sfoglia al contrario, il pavimento di Jenny segue la
   vista, e Jenny si mette al bordo quando esci dalla chat.
3. L'elenco da `/api/graph?wiki=<nome>`: pallino, nome, gruppo. La ricerca con
   l'indice arrivato nella stessa risposta.
4. Le parole: `graph.summaries` che manca da sempre, più le frasi di casa.

**Secondo — il lettore.** Una pagina si apre; i suoi wikilink aprono le altre
dello stesso quaderno. HTML del server, sanificato con DOMPurify — che il guscio
carica già per la chat. Niente audit, niente briciole di pane, niente LaTeX:
quelli sono l'officina, e l'officina resta dov'è finché il disegno non la
smonta.

**Terzo — la mappa.** La seconda linguetta, con D3 caricato al primo tocco e
`edges` che sono già in mano. Il disegno del grafo è **nuovo, non portato**:
`mobile-graph.js` porta con sé la vista home (i nodi sono le wiki), la legenda,
il pannello di un nodo e il pescaggio da `window.mobileApp`, e niente di tutto
questo esiste in casa. Quel che si riusa è la fisica delle forze di D3 e
`shared/wiki-search.js`, che è già condiviso e non importa niente.

## Le trappole, scritte prima di caderci

**L'ordine dell'elenco non può rinumerare i nodi** (rilievo 4). Disegnare
ordinato e cercare sull'array originale sono due cose diverse: l'indice della
ricerca va tenuto accanto alla riga, non dedotto dalla sua posizione. Vale
doppio quando arriva la mappa, che disegna i nodi in un terzo ordine ancora.

**Un quaderno piatto direbbe «Altro» a ogni riga.** Cinque quaderni su
quattordici non hanno gruppi: dalle 2 alle 18 righe che si ripetono.
Un'etichetta che vale per tutte non è un'informazione, è rumore: se il quaderno
ha un gruppo solo, l'etichetta e il pallino spariscono. E la mappa di un
quaderno piatto è una nuvola di puntini di un colore solo — se non ha nemmeno
archi, la linguetta deve dire che non c'è niente da vedere invece di disegnare
il vuoto.

**Il pavimento** (rilievo 8): il token va scritto da chi è a schermo adesso, o
Jenny galleggia.

**Indietro deve sbucciare, non saltare.** Lettore → elenco → chat, un tocco per
strato. Se dall'elenco Indietro uscisse dal quaderno, un gesto farebbe sparire
due cose — la stessa regola che abbiamo scritto ieri per la tendina.

**Cambiare conversazione mentre l'elenco è aperto.** Dalla tendina si può
saltare in un altro quaderno: o l'elenco si chiude, o parla di una stanza in cui
non sei più. `chat:switch` esiste già come segnale (`casa-app.js::init`).

**Due quaderni di fila.** `/api/graph` su una wiki grande non torna in un frame:
serve il token monotono di carico, o l'elenco del primo si disegna sopra il
secondo. È lo stesso difetto che `mobile-graph.js` si è già preso (`_loadToken`,
`_gen`) e che il filo della casa ha già risolto a modo suo.

**D3 caricato e poi abbandonato.** Due tocchi rapidi su «Mappa» non fanno due
richieste — `ensureVendor` (`shared/utils.js:71`) tiene la promessa in cache per
`src`, e un fallimento non lo tiene, cosi' un secondo tentativo riprova. Quel
che non copre e' uscire dalla pagina *mentre* i 280 kB arrivano: la
continuazione trova un contenitore staccato, e una simulazione a forze che
nessuno ferma continua a girare a rAF su nodi che non si vedono. `mobile-graph`
ha due contatori per questo (`_loadToken` e `_gen`) e il motivo del secondo e'
scritto li': il token da solo e' cieco all'uscita dalla sezione.

**Le wiki possono essere spente.** `/api/graph` fa 503 con `wiki.enabled` falso
(fail-closed, `wiki_routes.py:174`). La frase esiste già in casa —
`casa.who.create.wikiOff` — e va riusata invece che riscritta.

**Una cartella può non esserci più.** Il pannello tiene una cache che sopravvive
a una lettura fallita: il numero nella pastiglia può parlare di un quaderno
cancellato da un'altra parte. Un 404 qui è un caso normale, non un errore da
toast.

## Le decisioni, e da dove vengono

Quattro le ha già prese il disegno. Le scrivo lo stesso, perché il piano deve
dire *perché* una cosa è così e non perché l'ho scelta io.

| | | da dove |
|---|---|---|
| **La mappa c'è, e la barra a due linguette pure** | `Concetto`: «wiki, grafo e progetti sono una cosa sola»; `Mappa`: colonna operatore, trattino | tavola |
| **Una vista, non un `<dialog>`** | la tavola disegna Jenny e la maniglia del cassetto *dentro* la pagina; sotto un velo sarebbero coperti | tavola |
| **Una pagina si apre, e si apre in casa** | stessa riga di `Concetto`; e nella colonna operatore non resta niente da aprire | tavola |
| **Ordine per gruppo, alfabetico dentro** | la tavola elenca per gruppo, e il gruppo è già il pallino e l'etichetta | tavola |
| **D3 al primo tocco su «Mappa»** | 280 kB contro il commento in cima a `index.html`; `ensureVendor` fa già così | mia |
| **Elenco da `/api/graph`, conteggio da `/api/projects`** | una chiamata porta nodi, archi e indice; contare tutti i quaderni costa 20 ms | mia |

L'unica cosa che il disegno non dice e che andrà decisa guardandola sul
telefono è **quanto della mappa entra in 590×566**: con 62 nodi e la barra e il
campo di ricerca sopra, restano circa 430 px di altezza. Se non si legge, la
risposta non è rimpicciolire i nodi — è che la mappa di casa parte centrata
sulla pagina che stavi leggendo invece che su tutto il quaderno. Si decide con
uno screenshot, non adesso.

## Come si verifica

**Ai banchi**, con la forma che la casa usa già: i membri estratti dal sorgente
e girati in node su un DOM finto, più i test di contratto per quel che nessun
banco di comportamento vede (il markup, le parole cablate, la vista che esiste).
Le cose che devono diventare rosse toccandole: l'ordine che rinumera i nodi,
l'etichetta che resta in un quaderno piatto, Indietro che salta uno strato, il
token di carico tolto, il pavimento che non segue la vista, D3 caricato
all'avvio invece che al tocco.

**Sul banco finto** (`finto_gateway.py` sa già rispondere a `/api/projects` e ai
thread): un quaderno con gruppi, uno piatto, uno vuoto, uno senza archi, uno che
risponde 503, e due aperti di fila per la corsa.

**Sul telefono**, che è l'unico posto dove si vede se la casa è ancora la casa:
il tempo fra il tocco sulla pastiglia e la prima riga leggibile sul quaderno da
62 pagine; quello fra il tocco su «Mappa» e il primo disegno, che è il conto dei
280 kB; Indietro che sbuccia tre strati; e Jenny che si mette al bordo uscendo
dalla chat e torna fuori rientrando.
