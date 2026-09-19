# Le pagine di un quaderno — cosa è atterrato

Piano: [`casa-pages-plan.md`](casa-pages-plan.md). Tre atterraggi, nell'ordine
previsto, e il perimetro è quello che il disegno aveva già deciso — la mappa
compresa, che nel piano prima di leggere `Concetto` e `Mappa` avevo proposto di
rimandare.

## La casa adesso ha tre stanze

La conversazione, le pagine di un quaderno, una pagina. Quale sia a schermo lo
dice **un attributo solo** su `.casa-shell`, scritto anche nell'HTML: senza,
il primo frame impila le tre stanze finché `casa-app.js` non è stato valutato.
Il resto lo fa il CSS, così la geometria sta in un posto solo.

**Indietro sbuccia una stanza per pressione** — lettore, pagine, chat — e solo
quando la casa è tornata alla conversazione vale come «esci dal quaderno». Gli
strati (la tendina, l'immagine ingrandita) restano prima di tutto: `showModal()`
li mette nel top layer, ed è quel che l'occhio si aspetta da quel tasto.

**Il pavimento di Jenny segue la stanza.** Nella chat è il composer e si misura;
nelle pagine il composer è `display:none` e il suo `offsetHeight` è zero, quindi
il token si dichiara. E fuori dalla chat lei si mette al bordo: lo dice la
tavola, che qui la disegna a `right:-56px` contro i `-30px` di quelle della
chat — gli stessi due ancoraggi messi in comune il giro prima. Al ritorno torna
com'era, non «fuori» d'ufficio: se l'avevi messa via tu, era una tua decisione.

## Una risposta, tre rese

`/api/graph?wiki=<nome>` porta nodi, archi e indice full-text insieme, e il
commento in `wiki_routes.py` dice perché non sono due endpoint. Elenco, ricerca
e mappa non sono tre caricamenti: sono tre modi di disegnare la stessa
risposta, e la mappa non ne chiede una seconda.

**Le postings sono indici di posizione** nell'array `nodes` del server. A
schermo le righe vanno per gruppo e alfabetiche dentro, e la mappa in un terzo
ordine ancora: ogni riga si porta dietro il suo `index` invece di dedurlo da
dove sta. Il banco che lo prova accende la **posizione 0** — che a video è la
terza riga: se si cercasse per posizione si accenderebbe la prima. Plausibile,
silenzioso, sbagliato.

## Le sei decisioni, e com'è finita

| | | |
|---|---|---|
| La mappa c'è, e le due linguette pure | dalla tavola | fatta |
| Una vista, non un `<dialog>` | dalla tavola | fatta |
| Una pagina si apre, e si apre in casa | dalla tavola | fatta |
| Ordine per gruppo, alfabetico dentro | dalla tavola | fatta |
| D3 al primo tocco su «Mappa» | mia | fatta, e misurata: `window.d3` è ancora `undefined` dopo che l'elenco ha disegnato |
| Elenco da `/api/graph`, conteggio da `/api/projects` | mia | fatta |

## Due cose che il banco ha corretto

**`summaries/` non arriva mai al grafo.** `WIKI_PAGES_SKIP_DIRS` lo toglie a
tutte e quattro le camminate — è il livello di citazione del pattern di ricerca,
non materia di cui la wiki parla. Nel piano l'avevo contato come quarto gruppo e
avevo pure «riparato» la legenda dell'officina aggiungendole una riga: quel conto
era **giusto**, tre gruppi e tre righe. Misurato costruendo il grafo vero di una
wiki che ha `summaries/` dentro: sei nodi su sette, e il settimo era quello. La
riga è stata tolta, la parola pure, e `GROUPS` ne ha tre.

**La pastiglia diceva 7 e la stanza dietro ne elencava 6.** Il conteggio era una
`rglob` nuda; adesso conta con `is_wiki_page_rel`, la regola di chi quelle pagine
le elenca. Un numero su una porta deve contare quel che c'è dall'altra parte.

## Tre cose che ha corretto il telefono

**«Torna alla chat» restava a schermo dentro la chat.** `[hidden]` è una regola
del browser a specificità bassissima, e il `display: inline-flex` messo sulla
classe la scavalcava: `hidden` era vero e l'elemento si vedeva, senza un errore
da nessuna parte. Gli altri due comandi se ne salvavano solo perché li copre
`.casa-actions [hidden]`, che di specificità ne ha una in più. Nessun banco di
comportamento poteva vederlo: girano tutti su un DOM finto, che la cascata non
ce l'ha. Il banco nuovo cerca la **famiglia** — ogni classe che il guscio
nasconde con l'attributo deve avere la sua regola o un antenato che ce l'ha.

**Le etichette della mappa erano una macchia.** Su un quaderno vero da 31
pagine, con titoli che sono frasi («Coltivazione-Monstera-Roma — Sostegno,
fertilizzazione, crescita»), scriverli tutti e interi non lascia leggere niente.
Adesso portano il nome **le dieci pagine più collegate**, tagliato a 22
caratteri, e le altre restano pallini: è l'unica domanda a cui una mappa
risponde meglio di un elenco — dove si annoda il quaderno — e l'elenco è nella
linguetta accanto, che le ha tutte e le cerca. A parità di collegamenti decide
il nome, così l'insieme è lo stesso a ogni apertura.

Stesso scatto: con `distance(58)` e `charge(-120)` i nodi si impilavano in una
matassa che nemmeno lo zoom apriva. Adesso 72 e −230.

**E la nota stava sul bordo.** L'elenco è `flex: 1`, quindi da vuoto si prendeva
tutta l'altezza e spingeva «nessuna pagina con queste parole» in fondo allo
schermo, dove sembra un piede di pagina invece della risposta alla ricerca
appena fatta. Spostata sopra l'elenco.

## I pallini dovevano dividere, e non dividevano

`--accent` e `--error` — i due colori che il grafo dell'officina usa per
concetti ed entità — distano **26** in kyoto e 58 in pietra: due pallini
identici a occhio, in un elenco dove il pallino è metà dell'informazione.
Misurati tutti e sei i temi: `--accent` e `--ok` addirittura **coincidono** in
chanel e fumetto, mentre `--error` e `--ok` non scendono mai sotto 101. Quindi
in casa i concetti prendono `--ok`, che nel grafo è assegnato a `summaries` e
non viene mai disegnato. Il grafo dell'officina ha lo stesso scontro e non l'ho
toccato: là la legenda mette la pastiglia accanto alla parola, quindi si
decodifica lo stesso.

Il banco che tiene la regola non guarda i colori di oggi: calcola la distanza
fra le tre coppie **in ogni tema**, componendo l'alfa sullo sfondo dove serve, e
vale per i temi che verranno.

## Misurato

- Contare i `.md` di tutti e quattordici i quaderni sul Titan: **20 ms**.
- La risposta di `/api/graph` sul quaderno più grosso (62 pagine, 163 kB di
  markdown): 18.318 occorrenze, 3.727 termini unici, 32 kB di dizionario →
  ~110 kB. Il mediano sta sotto i 40.
- La mappa riceve **566×442 px** su una viewport di 590×566: la stima del piano
  («circa 430») era giusta.
- D3 arriva e disegna in **meno di un secondo** sul telefono: i 280 kB vengono
  dal server degli asset locale.
- `casa-pages.js` 10,2 kB, `casa-reader.js` 6,5 kB, `casa-map.js` 8,1 kB.

## Provato sul banco finto, su una wiki finta ma vera

`finto_gateway.py` costruisce grafo e indice **col codice del repo** su una wiki
scritta apposta: un indice full-text impacchettato non si scrive a mano, e uno
finto proverebbe il banco invece del modulo. Quattro quaderni — con gruppi,
piatto, vuoto, guasto — più la corsa di due aperture di fila. La ricerca vera:
«fred» trova il basilico (*teme il freddo*), «ann» come prefisso accende tre
pagine, «zzz» nessuna e lo dice.

## Provato sul telefono, sui quaderni veri

Entrare in un quaderno da 31 pagine, la pastiglia col numero giusto, l'elenco
per gruppo, la ricerca full-text, una pagina aperta, un wikilink seguito dentro
lo stesso quaderno, la mappa, e Indietro che sbuccia le stanze fino a uscire.

**35 mutazioni, 35 rosse.** Tre erano verdi al primo giro, e nessuna per colpa
di un banco cieco: la corsa fra due caricamenti aveva ritardi uguali (quindi la
risposta «lenta» arrivava comunque per prima), la linguetta della mappa veniva
provata solo prima che i dati ci fossero, e il conteggio nella voce dell'elenco
non lo esercitava nessuno. Quarta volta in tre giri che la domanda giusta
davanti a un verde è «dove è caduta la mutazione?».

## Lasciato fuori, dichiarato

- **La maniglia del cassetto** in fondo alla tavola: è la tavola dopo.
- **L'avatar** accanto alla pastiglia, che porta a «Tu e Jenny»: altra tavola.
  Finché non c'è, l'intestazione tiene la porta dell'officina.
- **Audit, briciole di pane e LaTeX** nel lettore: sono arnesi da operatore, e
  l'officina resta dov'è.
- L'anti-collisione delle etichette era il quarto atterraggio: v. sotto.

## Ritocco — ogni nome al suo posto

Le due etichette che si sfioravano al centro della mappa. I nomi si misurano a
fisica ferma e si collocano in ordine di quanto è collegata la loro pagina:
sotto il pallino, se no sopra, se no **non si scrivono**. Due parole
sovrapposte non sono due informazioni, sono zero; il pallino resta, si tocca
lo stesso, e il nome sta nell'elenco accanto.

**I pallini non sono ostacoli, ed è misurato e non assunto.** Ci ho provato per
primo. Un nome sta dieci pixel sotto il bordo del suo cerchio — cioè dentro
l'aria che separa due riquadri — quindi litigava perfino col *proprio* pallino
e sparivano tutte le etichette: preso dal banco, prima che lo vedesse il
telefono, dove sarebbe stato «la mappa non ha più i nomi». Escluso il proprio,
restava che un nome largo fino a 90 px su nodi distanti 40 tocca sempre il
cerchio del vicino: tre pagine in fila ne conservavano **una su tre**. Un nome
che sfiora un pallino si legge; un nome che non c'è no.

Il posto di riserva è più largo di quanto sembri: `y` di un testo SVG è la
linea di base, quindi andando in alto il riquadro scende di un paio di pixel
sotto l'ancora, e con lo stesso numero di sotto il fondo del nome finiva dentro
l'aria del pallino vicino — il ripiego non era più un ripiego.

### E poi il telefono ha detto che non era cambiato niente

Primo scatto dopo il collocamento: identico a prima. Non era rotto — **era
presto**. I nomi si mettono a posto solo quando la fisica si ferma, e il
default di D3 sono ~300 tick: cinque secondi a 60 fps, e il Titan con 31 nodi e
80 archi ne fa una trentina al secondo, quindi **dieci**. Misurato con due
scatti: a 5 s accavallati, a 16 s a posto. Con `alphaDecay(0.045)` i tick sono
~150 e a **6 secondi** sullo stesso quaderno le dieci etichette sono tutte
leggibili e nessuna si tocca — la nuvola è assestata lo stesso, perché a quel
punto le forze spostano i nodi di frazioni di pixel.

**11 mutazioni, 11 rosse.** Cinque erano verdi al primo giro, e le prime tre
per lo stesso motivo: il banco provava solo nomi lontanissimi o esattamente
sovrapposti, mai a un pixel di distanza — e l'aria fra due riquadri è due
condizioni diverse, di cui una sola morde a seconda di chi sta a destra. Le
altre due erano il *disegno*, che nessun banco toccava: adesso una selezione di
D3 finta esercita anche quello.
