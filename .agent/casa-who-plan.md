# «Con chi parli» — il piano

L'intestazione della casa dice già *conversazione personale · **Jenny***, e si
ferma lì. Il titolo non ha il chevron, e `index.html` scrive sul posto perché:
«la scelta di con chi parli è del giro dopo, e un chevron che non apre niente è
una bugia». Questo piano costruisce ciò che quel chevron apre.

**Il confine di questo giro, deciso: si fa il pannello e basta. Un tocco su un
quaderno non cambia conversazione.** Non è una dimenticanza da recuperare in
fretta: è il taglio, e il pannello deve essere onesto su ciò che non fa —
altrimenti la bugia è la stessa di prima, solo un tocco più in là.

Tavola di riferimento: `Conversazioni.dc.html` nell'artifact *Jenny UI: Utente e
Operatore*. Stato: **proposto, niente implementato.** Scritto: 19/09/2026.

---

## Due tavole che si somigliano, e non sono la stessa cosa

Vale la pena scriverlo perché ci sono quasi cascato:

- **«con chi parli»** (`Conversazioni.dc.html`) è *quale conversazione*: la
  personale, o uno dei quaderni. La tendina che questo piano costruisce.
- **«Chi risponde»** (`Modello.dc.html`) è *quale modello e quale provider*
  rispondono. Vive dentro «Tu e Jenny», ed è un altro lavoro.

Le due non condividono niente: la prima cambia la chiave di sessione di *questo*
guscio, la seconda scrive `agents.defaults.model` e `providers.default` nel
config — cioè cambia chi risponde anche a Telegram. Averle confuse avrebbe
voluto dire costruire un pannello che tocca il config dal posto sbagliato.

---

## Il rilievo

Misurato sul codice, non dedotto.

**Un quaderno è un progetto, e un progetto è una wiki.**
`/api/projects` ([`wiki_routes.py:399`](../jenny/webui/wiki_routes.py)) ritorna
`{dir, projects: [{name, modified}], unopenable: [{name, modified, reason}]}`.
`modified` è l'mtime della radice della wiki, e `_collect_projects` lo dichiara
per quello che è: *oggi è il solo segnale di attività disponibile*, e un `lint`
non è attività dell'utente.

**Il conteggio delle pagine non esiste.** Nella tavola ogni quaderno porta
«8 pagine»; in nessun payload c'è quel numero. Per averlo servirebbe una
`/api/tree?wiki=<nome>` **per quaderno** — N richieste per disegnare un pannello
— oppure un campo nuovo in `_collect_projects`. V. D2.

**Tutta quella famiglia di rotte è dietro `config.wiki.enabled`, fail-closed**
([`wiki_routes.py:174`](../jenny/webui/wiki_routes.py)). Wiki spente ⇒
`/api/projects` risponde errore. Il pannello deve restare vero anche lì: la riga
personale c'è comunque, ed è l'unica che conta davvero in questo giro.

**L'officina sa già le parti difficili, e le ha pagate.** `scope-chip.js` (786
righe) ha, con i commenti che spiegano da quale guasto vengono:

- una lettura fallita **non è** «nessun progetto»: la cache non si tocca e il
  fallimento si dichiara in una nota sua. La risposta ovvia a un elenco vuoto è
  rifare il progetto, ed è così che nascono due wiki con la storia divisa;
- ordine per `modified`, pareggio sul nome, così l'elenco non cambia fra
  un'apertura e l'altra e non contraddice la data che ogni riga stampa;
- le cartelle **non apribili** si mostrano, in fondo, con *una nota per motivo*:
  su un telefono non c'è un file manager, e sparire dall'elenco è
  indistinguibile dall'essere state cancellate;
- la riga attiva si porta in vista all'apertura;
- `_ago()` e le stringhe `scope.ago.*`, che sono già tradotte.

Questa è conoscenza, non codice da ricopiare. V. *La forma*.

**Il tasto Indietro passa sempre dal JS.** `BACK_PRESS_JS`
([`MainActivity.kt:83`](../android/app/src/main/java/com/flagdizero/jenny/MainActivity.kt))
chiama `window.mobileApp.handleHardwareBack()` e ritorna `true` appena il metodo
esiste: il guscio nativo non sa se qualcosa è stato chiuso. In casa quel metodo
oggi chiude l'immagine ingrandita e **alla radice non fa niente di proposito**
(questa app è il launcher). Un pannello aperto che non entra in quella catena è
un pannello che Indietro non chiude.

**La trappola del manifest adesso ha una rete.** Il `casa-plan.md` diceva che
dimenticare `_UI_MANIFEST` non dà errore ma dà l'officina: vero a runtime, ma
`test_js_module_imports_are_in_manifest` e
`test_ui_active_files_on_disk_are_in_manifest`
([`tests/webui/test_ui_manifest.py`](../tests/webui/test_ui_manifest.py)) lo
prendono prima, per ogni import statico. La voce va messa lo stesso; non è più
il rischio più caro del lavoro.

**La mascotte sta a `z-index: 5`** ([`casa-style.css:498`](../jenny/templates/ui/assets/casa-style.css)),
dentro `.casa-shell`. Il pannello le deve stare sopra. V. D3.

**Niente di questo tocca Python.** L'unica rotta che serve esiste, risponde già
al guscio della casa, e il resto è disegno.

---

## La forma

**Il pannello è una tendina ancorata, non un foglio centrato.** Nella tavola:
`left: 18px`, `top: 74px`, larghezza 330 su 590, angoli 16, ombra lunga, sopra
un velo `rgba(20,16,12,.28)` che è anche il bersaglio per chiudere. Il titolo
prende il chevron e diventa il comando che lo apre.

Dentro, due sezioni e niente altro:

| Sezione | Righe | Da dove |
|---|---|---|
| **con chi parli** | una: *Jenny · personale*, con la spunta | `sessionManager.personalKey` |
| **quaderni** | nome, e quanto è passato | `/api/projects` |
| (in fondo, se ce ne sono) | le cartelle non apribili, con una nota per motivo | `unopenable` |

**Si estrae, non si copia.** Nasce `assets/shared/conversation-list.js` con *i
dati e le regole*: la fetch, l'ordine, la divisione fra apribili e non, la cache
che sopravvive a una lettura fallita, e `ago()`. `ScopeChip` perde
`_loadProjects` e `_ago` e comincia a usarlo — a comportamento **identico**,
come è stato per `mascot-drag.js` e `history-pager.js`. La casa disegna, e non
riscopre nessuna di quelle cinque regole.

Quello che la casa scrive per sé è `assets/casa-who.js`: il pannello, le sue vie
d'uscita, e le righe. Stimo 120-150 righe, contro le 786 del chip — che sono
786 perché lì dentro ci sono anche creazione, cancellazione, placeholder del
composer e il chip stesso.

**Le stringhe che esistono non si reinventano.** Le date relative sono
`scope.ago.*`, le note delle cartelle non apribili sono `scope.unopenable*`:
sono già tradotte e già giuste. Nasce solo `casa.who.*` per ciò che è nuovo —
le due etichette di sezione e la riga personale.

**I nomi si scrivono con `textContent`.** Un nome di quaderno è un nome di
cartella, ed è già la regola del resto della casa: nel pannello non entra
`innerHTML` con dentro un nome.

---

## I passi

**1. L'estrazione.** `shared/conversation-list.js`, e l'officina che ci si
appoggia. **Due banchi di prova si spostano con il codice:**
`test_scope_chip_load_failure_client.py` e
`test_scope_chip_unopenable_client.py` ritagliano `_loadProjects`, `_ago` e
`_renderMenu` **dal testo** di `scope-chip.js`. I primi due se ne vanno, quindi
quei test vanno ripuntati sul modulo nuovo — e poi ri-mutati, perché un banco
ripuntato male passa a vuoto, che è il modo in cui tre test di questo lavoro
sono già stati verdi senza guardare niente. *Prova:* la tendina dell'officina si
comporta identica sul telefono — elenco, ordine, date, cartelle non apribili.

**2. Il chevron e il guscio del pannello.** Il titolo diventa un comando con
`aria-expanded`; il pannello si apre vuoto e si chiude in tre modi: velo, Esc,
Indietro. Voce nel `_UI_MANIFEST`. *Prova:* si apre, si chiude nei tre modi, e
Indietro con il pannello chiuso continua a non fare niente.

**3. Le righe.** Riga personale con la spunta; quaderni ordinati; i tre stati
distinti — *sto leggendo*, *non sono riuscita a leggere* (con quel che resta in
cache sotto), *non ce n'è nessuno*; le non apribili in fondo con la loro nota.

**4. i18n** `it`/`en`, e il giro a lingua cambiata con il pannello aperto.

**5. La prova sul telefono.** Con le wiki accese e quaderni veri, e con le wiki
spente.

---

## Le trappole, scritte prima di caderci

- **Il pannello deve entrare nella catena di Indietro**, o il tasto non lo
  chiude e la pressione ricade sul sistema.
- **«Elenco non letto» non è «nessun quaderno».** È la regola più costosa fra
  quelle che l'officina ha già pagato: se la casa la perde nell'estrazione, la
  perdono tutte e due.
- **Le wiki spente** non sono un guasto del pannello: la riga personale resta, e
  la nota lo dice senza spaventare.
- **Un nome lungo.** Un progetto arriva a 64 caratteri, e il pannello è largo
  330: la riga tronca, non manda a capo e non spinge fuori la data.
- **Doppio chiamante in `shared/`.** Da qui in poi `conversation-list.js` ha due
  padroni: una modifica pensata per l'officina va provata anche in casa.
- **I banchi che ritagliano per nome.** Spostare un metodo lo toglie da sotto i
  test che lo estraggono dal sorgente. Falliscono forte (`_member` è un assert,
  non un `if`), ma vanno ripuntati **e rimutati**: v. passo 1.

---

## Decisioni da prendere

**D1 — Cosa fa un tocco su un quaderno, oggi.** Deciso che la chat non switcha,
restano tre modi di dirlo:

1. **Righe inerti, e che lo sembrino**: sono testo, non bottoni. Il pannello di
   questo giro dice *cosa esiste*, non *dove puoi andare*. Quando arriva lo
   scambio diventano bottoni e niente altro cambia.
2. Righe che portano quel progetto **in officina** (`api.navigate` con un
   frammento). Ma l'officina i frammenti non li legge ancora — è la stessa
   sospensione di `#turn=` — quindi oggi atterrerebbero sulla chat personale:
   una promessa non mantenuta, che è peggio di una riga che non promette.
3. Costruire lo scambio adesso, contro l'istruzione.

**Proposta: la 1.** E una differenza dalla tavola: nessun chevron e nessuna
spunta sulle righe dei quaderni. La spunta resta solo sulla riga personale,
perché lì è vera.

**D2 — «N pagine».** Toglierlo da questo giro (la riga porta nome e data, come
in officina), oppure aggiungere un campo `pages` a `_collect_projects` — un
elenco di cartella in più per quaderno, dove la discovery già cammina.
**Proposta: toglierlo.** È decorazione finché la riga non si tocca, e `modified`
risponde già alla domanda che ci si fa davvero: *quale stavo usando*. Aggiungerlo
resta additivo, e si fa nel giro dello scambio.

**D3 — `<dialog>` o un velo qualunque.** **Proposta: `<dialog>` +
`showModal()`.** Regala il *top layer* — cioè sopra la mascotte senza una guerra
di `z-index` —, `::backdrop` per il velo ed Esc per il banco di prova. L'unica
cosa che *non* regala qui è Indietro, perché il guscio nativo lo intercetta
prima della WebView: lo chiude `handleHardwareBack`, e un test lo inchioda.
L'elemento lo crea il modulo alla prima apertura, come fa il dialogo dei
provider in officina: il guscio resta senza nodi inerti.

**D4 — Un pallino colorato per quaderno.** La tavola ne dà uno a ciascuno; nel
payload non c'è nessun colore. Derivarlo dal nome è gratis e stabile, e rende
scorribile un elenco di cinque nomi. **Proposta: derivarlo**, dicendo qui che
non significa niente oltre l'identità — il giorno che qualcuno ci legge uno
stato, è una bugia nata da una decorazione.

---

## Come si verifica

Oltre a `ruff`, `pyright` sul sottoinsieme bloccante e la suite intera:

- **Banco node sui metodi del pannello** (lo stesso ritaglio per testo degli
  altri test della casa): ordine e pareggio sul nome; lettura fallita che
  **tiene** la cache e aggiunge la nota; elenco vuoto ≠ elenco fallito; le non
  apribili in fondo con una nota sola per motivo; la spunta solo sulla riga
  personale.
- **Contratto**: il titolo è il comando che apre; `handleHardwareBack` chiude il
  pannello; le voci nel manifest; nessuna stringa inglese cablata nel JS.
- **Mutazione**: tolto l'ordinamento, rosso. Sostituita la cache con `[]` al
  fallimento, rosso. Se non diventano rossi, i test guardano il meccanismo e non
  il risultato — è già successo tre volte in questo lavoro.
- **Sul telefono**: apertura e le tre vie d'uscita; un quaderno con un nome
  lungo; le wiki spente; e la tendina dell'officina, che dopo l'estrazione deve
  essere indistinguibile da com'era.
