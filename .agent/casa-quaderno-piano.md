# Il quaderno in casa: una cosa da togliere, due da mettere

*22/09/2026 — branch `feat/la-casa`. Deciso dall'utente dopo l'analisi di cosa
si era perso nel passaggio della wiki dall'officina alla casa.*

**Il vincolo dato a voce: «non ci deve restare merda».** Nessun orfano — né
codice senza chiamanti, né rotte senza clienti, né cartelle create per una cosa
che non esiste più.

---

## Da dove si parte

La wiki è uscita dall'officina il 21/09 (`0116b1f`) e vive in casa come
«quaderno»: elenco pagine + mappa (`casa-pages.js`), lettore
(`casa-reader.js`), mappa (`casa-map.js`). Nel passaggio sono rimaste indietro
cinque cose; l'analisi le ha pesate una per una e la decisione è:

| cosa | esito | perché |
|---|---|---|
| grafo di **tutti** i quaderni | **togliere** | è una stella senza collegamenti fra quaderni: l'unica informazione è quante pagine ha ciascuno, e l'elenco la dà già con nomi, date e ricerca |
| **camminare** nel grafo (tocca un nodo → ricentra) | già fatto | era solo lato interfaccia, se n'è andato con `mobile-graph.js`. Niente da rimuovere |
| **albero** delle cartelle | non si rifà | su 590 px un elenco ordinato e raggruppato si legge meglio, e si aprono pagine, non cartelle |
| **ricerca dentro la mappa** | non ora | vale (v. in fondo), ma viene dopo queste tre |
| **audit** | **non si toccano** | v. sotto |

### L'errore da non rifare, scritto qui perché è successo due volte in due giorni

Avevo raccomandato di **togliere gli audit**, sulla base di due misure giuste
(zero file in quindici quaderni; nessun chiamante nell'interfaccia) e di una
domanda mai fatta: **qualcun altro li consuma?**

Sì. `audit` è una delle **cinque operazioni** della skill `llm-wiki`
(`compile`, `ingest`, `query`, `lint`, `audit`), che è `locked: true`. Ha una
guida (`references/audit-guide.md`), uno script che Jenny esegue a inizio
passata (`scripts/audit_review.py`) e 41 righe di controlli in
`scripts/lint_wiki.py`. E la guida risponde per iscritto all'alternativa che
avevo proposto:

> «Il feedback in chat si perde nel momento in cui la conversazione finisce. La
> cartella audit dà alle correzioni una casa permanente e ancorata al punto, che
> l'AI e il linter capiscono.»

È lo stesso sbaglio di KaTeX il giorno prima («un lettore ciascuno, ed era la
wiki» — e invece il lettore era `shared/rich-content.js`, usato anche dalla
casa). **La regola che ne esce, e vale per la Parte 1 di questo piano: prima di
togliere, cercare il consumatore fuori dall'interfaccia** — agente, skill,
template, guscio Android, script. Una misura di «non usato» presa solo sul
client non è una misura di non usato.

E «zero file» qui non vuol dire «non lo vuole»: vuol dire che **quel giro non è
mai partito**, plausibilmente perché l'unica porta comoda era proprio quella
cancellata il 21/09. Da cui la Parte 3.

---

## Parte 1 — via il grafo di tutti i quaderni

**Verificato prima di scrivere:** `build_home_graph` lo usa **solo** la sua
rotta, più due test. Nessun consumatore agente, nessuna skill, nessun template.

### Cosa cade

1. `jenny/webui/wiki.py` → `build_home_graph()` per intero (docstring: *«Star
   graph: central hub node + one node per wiki»*). **Non** toccare
   `discover_wikis`, che serve ancora ad `_audit_create` e al resto.
2. `jenny/webui/wiki_routes.py` → nel gestore di `/api/graph`: l'`import` di
   `build_home_graph`, il ramo `else` che lo chiama, e il commento a ~riga 99
   che lo cita. Il nome del quaderno diventa **obbligatorio**: senza, la rotta
   risponde `400`, come già fa `/api/audit/create`.
3. `jenny/templates/ui/assets/shared/api-client.js` → `getGraph(wiki)` perde la
   forma senza parametro (`const url = wiki ? … : '/api/graph'`). Il parametro
   diventa obbligatorio.
4. `tests/webui/test_wiki_multi.py` → le due prove che lo coprono (~righe 347 e
   359). **Non si cancellano e basta**: quel che misuravano — «scoprire i
   quaderni» — resta vero e va misurato su `discover_wikis`, che sopravvive. Se
   dopo la riscrittura non resta niente da asserire, si dice nel commit.

**Fatto il 22/09.** Le due prove del grafo a stella dicevano due cose, e solo
una non era gia' detta altrove: la cartella vuota la copriva gia'
`test_discover_returns_empty_for_empty_dir`, mentre «un quaderno **con pagine
dentro** si scopre» no — le prove vicine costruivano i quaderni con una `mkdir`
nuda. E' diventata una prova sola in `TestDiscoverWikis`.

In piu' e' nata una prova che prima non c'era: **senza `wiki=` la rotta risponde
400**, e non 404 (un 404 direbbe «quel quaderno non c'e'» e manderebbe a cercare
un nome che non e' stato mandato). Provata rossa con due mutazioni: tolta la
guardia, e 404 al posto di 400.

**La cache del bytecode ha mentito una volta**, come da nota in memoria: la
prima mutazione ripristinata nello stesso secondo e alla stessa lunghezza ha
lasciato in giro un `.pyc` mutato, e la passata intera e' uscita rossa su
sorgente sana. Si pulisce `__pycache__` fra una mutazione e la misura buona.

### La verifica «niente merda»

Dopo il taglio, questi devono dare **zero** su `jenny/`, `tests/`, `docs/`,
`android/`:

```
build_home_graph        _home        group="home"        group-home
```

E `grep -rn "'/api/graph'" jenny/templates/ui/` non deve trovare la forma senza
query.

---

## Parte 2 — «Modifica» nel lettore

**Perché.** Le pagine sono `.md` in `workspace/wikis/<quaderno>/wiki/`, e
`wikis` non è fra le cartelle nascoste: quindi **già oggi** si possono
modificare — dal gestore file, in officina, sei tocchi e un altro guscio, dal
lato opposto del telefono rispetto a dove ti accorgi dell'errore. Funziona e non
lo fa nessuno.

Il lettore **non ha mai avuto** un editor: né in casa, né nella vecchia wiki
dell'officina (verificato su `mobile-wiki.js`: zero). Non è una cosa che si
rimette, è una che non c'era.

### Quel che già esiste, e va usato invece di rifarlo

`/api/page` restituisce già tutto quel che serve:

| campo | cos'è |
|---|---|
| `wiki` | il nome del quaderno |
| `page` | il percorso della pagina **dentro** il quaderno |
| `path` | il percorso relativo a `wikis_dir` |
| `html` | il reso |
| `raw` | **il markdown sorgente** |

`casa-reader.js` lo riceve già intero: `_safeHtml(page.html, page.raw)`. Quindi
**il testo da modificare è già in mano** — nessuna seconda richiesta.

### Quel che manca: la scrittura

Serve una rotta nuova, **speculare alla lettura**: `POST /api/page/write` con
`wiki`, `page`, `content`, che risolve il percorso server-side esattamente come
`_wiki_page` (stessa `relative_to` come guardia contro le uscite di cartella).

**Non** si usa `workspace.write` con un percorso costruito dal client: la
cartella dei quaderni la decide la config (`wiki.wikis_dir`) e il client non la
conosce — costruirla vorrebbe dire indovinarla. È lo stesso ragionamento che ha
portato gli spilli della mappa in `.jenny/` invece che dentro `wikis/<nome>/`.

### Il conflitto con Jenny, che è la parte che morde

**Le stesse pagine le scrive anche lei**, con gli strumenti file di sempre (non
c'è uno strumento wiki dedicato: verificato). Quindi fra il momento in cui apri
l'editor e quello in cui salvi, il file può essere cambiato sotto.

La richiesta porta anche `base`: il `raw` che il client aveva caricato. Il
server confronta col contenuto attuale e se differisce risponde **409**, senza
scrivere. Il lettore dice «Jenny l'ha cambiata mentre la modificavi» e offre di
ricaricare. Salvare a occhi chiusi qui vuol dire cancellare il lavoro di
qualcun altro senza che nessuno se ne accorga.

### La forma

- Un bottone **Modifica** nell'intestazione del lettore, accanto a «Talk about
  it».
- Premuto: il corpo reso lascia il posto a una `<textarea>` col `raw`. Più
  **Salva** e **Annulla**.
- **Niente CodeMirror.** Sono 200+ kB per evidenziare del markdown su 590 px, e
  la casa è rimasta magra apposta. Una textarea è la cosa onesta; se un giorno
  servirà di più, si vedrà con una misura.
- Salvato: si ricarica la pagina dal server (non si fida di quel che si è
  scritto: il reso lo fa il server, ed è lui che deve dire com'è venuta).
- **Uscire con modifiche non salvate chiede conferma.** Il repo ha già la
  lezione scritta, e cara: v. `_closeEditor`/`_confirmDiscard` del gestore file,
  dove il guard sul buffer sporco vale «solo se non esiste una seconda strada» —
  e prima ce n'erano due che non lo guardavano.

---

## Parte 3 — «Segnala» nel lettore

**Perché.** Il giro degli audit è vivo da entrambi i lati tranne uno: Jenny lo
legge, il linter lo controlla, ogni quaderno ha le sue cartelle — e **tu non hai
più come metterci niente**, da quando il 21/09 è sparita la selezione del testo
nel lettore. Il canale esiste ed è muto.

E non è un doppione di «Modifica»: sono due atti diversi.

- **Modifica** = *lo aggiusto io adesso*. Refuso, riga storta.
- **Segnala** = *è sbagliato nel merito, aggiustalo tu* — e resta scritto dove
  il linter lo vede, con l'ancora al punto esatto.

### Quel che già esiste

`/api/audit/create` è intatta e **fa quasi tutto da sola**. Vuole in query:

```
wiki, target, selStart, selEnd, comment, severity, author
```

Il server **si rilegge il markdown da solo** (`raw_path.read_text`) e calcola le
ancore (`anchor_before` / `anchor_text` / `anchor_after`). Il vecchio client
passava anche `rawMarkdown`: **la rotta lo ignora**, e il client nuovo non deve
mandarlo.

Il file che nasce: `<wiki-root>/audit/YYYYMMDD-HHMMSS-<slug>.md`, frontmatter
YAML con `target`, `target_lines`, le tre ancore, `severity`, `author`,
`created`, `status: open`. Risolti → `audit/resolved/`, e **niente si cancella
mai**, nemmeno i rifiuti.

### Quel che manca: dalla selezione agli offset

`selStart`/`selEnd` sono **offset nel markdown sorgente**, ma la selezione
avviene nel reso. Il vecchio codice risolveva con `_resolveSelectionToOffsets`.

Qui è più semplice, perché `raw` è già in mano: `raw.indexOf(testoSelezionato)`
con un **controllo di unicità** — se quel testo compare più di una volta
l'ancora è ambigua, e allora si dice invece di ancorare alla cieca (era già la
scelta del vecchio codice: `wiki.createAuditAnyway`).

### La forma

- Selezioni del testo nella pagina → compare un bottoncino **Segnala**.
- Premuto: un foglio con l'anteprima del testo scelto, un campo commento e la
  gravità (`info` / `suggerimento` / `avviso` / `errore` — i quattro valori che
  il formato già prevede).
- `author`: il nome che Jenny ha in casa, o `anonymous`.
- Inviato: un toast, e basta. Non serve una lista degli audit aperti nella casa
  — quella la legge Jenny, ed è il suo mestiere, non una schermata da fare.

**Attenzione al gesto.** La selezione del testo su questo telefono ha già i suoi
conflitti noti (`shared/selection.js`, `TAP_SLOP = 12`, e la chrome che esce dal
hit-test finché c'è una selezione). Il bottoncino non deve rubare il tocco alla
selezione stessa, ed è la cosa da provare col pollice, non al banco.

### Le chiavi i18n

Tutte e 29 le `wiki.*` sono state cancellate il 21/09 e vanno ricreate **solo**
per quel che si ridisegna, sotto `casa.audit.*`, in **it** e **en** (il banco di
parità delle lingue fallisce se ne manca una).

---

## Cosa questo piano **non** fa

- **Non** rimette la lista degli audit aperti/risolti nell'interfaccia.
- **Non** rimette il grafo di tutti i quaderni, l'albero, il camminare.
- **Non** tocca la skill `llm-wiki`, che è `locked`.
- **Non** mette un modo per togliere uno spillo dalla mappa, né pota le chiavi
  dei quaderni cancellati dal file degli spilli (v. fondo del piano della
  mappa): restano aperte da ieri.

---

## Ordine, e come si verifica

Un pezzo = un commit, `ruff check jenny/ tests/` + `python3 -m pytest -q` verdi a
ogni passo, e **ogni banco nuovo provato rosso con una mutazione** prima di
dirlo fatto.

1. **Parte 1** (togliere) — è indipendente e non rischia niente: si fa per prima
   e libera il campo.
2. **Parte 2** (modifica) — prima la rotta col suo 409, poi il bottone.
3. **Parte 3** (segnala) — per ultima, perché è quella con il gesto delicato.

Alla fine, sul telefono (build release da worktree pulito, firmata, installata,
e **controllare che il JS nuovo sia davvero sul telefono** leggendo
`files/workspace/ui/assets/`, non il repo):

- modifico una pagina, salvo, riapro: la modifica c'è;
- modifico, e intanto cambio il file da fuori: al salvataggio esce il 409 e
  **non** si perde niente;
- esco con modifiche non salvate: chiede conferma;
- seleziono, segnalo, e **il file compare** in `wikis/<q>/audit/` con
  l'ancora giusta;
- seleziono un testo che compare due volte: lo dice invece di ancorare a caso;
- e dieci tocchi normali su una pagina non fanno comparire niente.

---

## Fatto il 22/09/2026 — e cosa e' cambiato per strada

Tre commit, uno per parte, piu' una passata di pulizia:

| | commit | |
|---|---|---|
| 1 | `e2ab6ad` | via il grafo di tutti i quaderni |
| 2 | `d125531` | «Modifica» nel lettore, col 409 |
| 3 | `4e79223` | «Segnala» nel lettore |
| + | `c2d74f2` | passata sugli export morti |

**Costruito, firmato e installato** (release da worktree pulito, cert
`CN=flagDiZero`), e il JS nuovo e' **davvero sul telefono**: `casa-audit.js`
esiste in `files/workspace/ui/assets/` e `rpc-client.js` li' dentro contiene
`writePage`. La prova col pollice — le sei righe qui sopra — **non e' stata
fatta**: il telefono era bloccato, e le password non le scrivo io.

### Le tre cose che il piano non sapeva

1. **`POST /api/page/write` non si poteva fare.** Quella superficie la serve
   l'hook di handshake di `websockets`, che **non legge mai il body**: e' un
   trasporto di sola lettura, 8192 byte per riga e solo ISO-8859-1. Il
   contenuto di una pagina e' esattamente la cosa che non ci passa. Il
   comando vive dove vivono gia' `workspace.write` e `audit.resolve`:
   `webui/commands.py`, esposto dall'RPC WebSocket. Stessa firma, stesso 409
   (che li' si chiama `conflict`).
2. **`safe_wiki_page_path` ha cambiato casa.** Stava nell'adapter HTTP, e
   `commands.py` non puo' importarlo — di trasporti non sa niente. E' andata in
   `utils/wiki_paths.py`, il layer neutro, dove stanno le altre funzioni di
   percorso.
3. **Il commento di un audit ha un tetto, ed e' di trasporto.** Viaggia nella
   query string, cioe' nella riga di richiesta, dove stanno 8192 byte in tutto;
   un'emoji percent-encodata ne costa 12. Tetto a 500 caratteri lato client.

### Quel che il giro ha trovato, e che decide qualcun altro

**Tre rotte wiki senza nessun cliente**, verificate su tutto l'albero — le due
interfacce, il guscio Android, gli script, i documenti (solo i banchi le
toccano):

- `/api/config` (`_wiki_config`) — torna `author: "me"` fisso e l'elenco delle
  wiki, che `/api/projects` da' gia';
- `/api/tree` (`_wiki_tree` + `build_tree`/`build_home_tree`) — l'albero delle
  cartelle, che questo piano ha deciso di **non** rifare;
- `/api/audit` (`_audit_list`) — l'elenco degli audit aperti, che questo piano
  ha deciso di **non** mettere nell'interfaccia.

Le ultime due sono orfane **perche' il piano le ha rese tali**: sono la coda di
due decisioni prese qui. Non le ho tolte — e' una scelta, e va presa, non
dedotta. `/api/config` invece era gia' orfana prima.

### L'errore, la terza volta in tre giorni

Nella passata di pulizia ho tolto `tokenize` da `shared/wiki-search.js` perche'
nessun modulo lo importa. **Il banco e' andato rosso**: il suo consumatore e'
un test che lo estrae dal file per confrontarlo con `wiki_search.py::tokenize`
— le due devono spezzare le parole allo stesso modo, o la ricerca dal telefono
non trova quel che il server ha indicizzato. Rimesso, con un commento che lo
dice.

Prima KaTeX, poi gli audit, ora questo. La regola va allargata di una voce: il
consumatore puo' essere **anche un banco**, e una passata che guarda solo gli
`import` non lo vede. `hashString` in `utils.js` invece era morto davvero, ed e'
uscito.

### La lista «niente merda», da ripassare a fine giro

- [x] `build_home_graph`, `_home`, `group-home`: zero occorrenze ovunque
      (i soli residui stanno in `android/app/build/`, che e' ignorato).
- [x] Nessuna rotta server senza un cliente — **contate, non chiuse**: tre
      restano orfane e la decisione e' di chi legge (v. sopra).
      `/api/audit/create` e `page.write` adesso un cliente ce l'hanno.
- [x] Nessun export JS senza importatori: due trovati, uno tolto
      (`hashString`), uno rimesso col motivo scritto (`tokenize`).
- [x] Manifesto `android_assets.py` e file su disco coincidono: 178 voci, zero
      sbilanci in entrambi i versi.
- [x] Chiavi i18n: `it.json` e `en.json` pari, nessuna chiave nuova orfana.
- [x] **La prova col pollice, fatta il 22/09** su `piante/entities/Pothos.md`,
      con la pagina rimessa a posto alla fine (md5 identico, etichetta SELinux
      invariata) e l'audit di prova cancellato:

      - modifico, salvo, riapro: la modifica c'e' (`SALVATO-DAL-TELEFONO`
        scritto su disco, e il lettore ricaricato dal server);
      - cambio il file da fuori mentre l'editor e' aperto: al salvataggio esce
        il conflitto, **il file non viene toccato** (il gateway ha loggato
        `rpc page.write failed: conflict`), e rispondendo «no» il testo resta
        nell'editor;
      - esco con modifiche non salvate: chiede conferma (dall'Indietro del
        telefono), e un «no» tiene tutto;
      - seleziono, segnalo: il file nasce in `wikis/piante/audit/` con l'ancora
        giusta (`anchor_text: coltivato`, riga 14) e la gravita' scelta col
        chip. **Il linter della skill lo accetta**: «audit/ shape OK»,
        «All open-audit targets exist»;
      - seleziono un testo che compare due volte (`Epipremnum`): lo dice invece
        di ancorare a caso, e il foglio resta aperto col commento dentro;
      - dieci tocchi normali su una pagina non fanno comparire niente.

### Quel che la prova ha trovato

- **L'editor si apriva in fondo alla pagina** (`focus()` porta il cursore dopo
  l'ultimo carattere e ci trascina la vista). Corretto in `35c9522`, con banco,
  **e riprovato sul telefono**: adesso apre sul frontmatter, cioe' in cima. Nello
  stesso giro: Annulla senza aver scritto niente chiude subito e non chiede, e
  il file resta bit per bit quello di prima; dieci tocchi normali sulla pagina
  non fanno comparire la barra di «Segnala».
- **Il primo tocco dopo aver scritto se lo mangia la tastiera.** Vale per
  Indietro *e* per Salva: la prima pressione chiude l'IME, la seconda fa la
  cosa. E' il comportamento normale di Android, non un difetto del codice —
  ma sul Titan, che la tastiera fisica ce l'ha, si nota.
