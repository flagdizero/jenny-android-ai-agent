# Il ciclo di vita di un progetto — dare a `delete` il padrone che non ha

> *I nomi dei progetti in questo file sono inventati: la forma e i numeri sono
> quelli osservati davvero, il nome no. Il repo e' pubblico.*

> **Difetto trovato sul telefono il 24/08/2026**, riprodotto dall'interfaccia.
> Cancellando `wikis/quaderno-x` dal file browser e ricreando un progetto con
> lo stesso nome, la conversazione vecchia riappare intera. L'md5 del file di
> sessione e' identico prima e dopo: non e' stato ripristinato, non e' mai stato
> toccato.

## L'invariante, detto per intero

> **L'identita' di un progetto e' la sua cartella. Una conversazione appartiene a
> una cartella, non a un nome.**

Il nome e' l'*indirizzo* — `project:<nome>`, deterministico, e resta cosi' per
scelta (v. `session/project_rename.py`: i file si chiamano `project_esempio.jsonl`
perche' e' con quelli che si guarda cosa e' successo davvero). Il legame vero e'
l'**id della wiki**, che ogni sessione si annota al primo turno
(`PROJECT_WIKI_ID_KEY`).

Il legame pero' si verifica **solo quando la cartella manca**. Finche' al suo
nome c'e' *una* cartella, nessuno chiede se e' *quella* cartella. Da cui il
difetto: cancellare la cartella libera il nome ma non la conversazione, e il
prossimo progetto che prende quel nome eredita la memoria di uno sconosciuto.

E' esattamente il guasto che il rinomino gia' rifiuta di produrre — «meglio due
chat da rinominare a mano che due storie mescolate», `follow_renamed_project`
davanti a una destinazione occupata. La strada della cancellazione viola
l'invariante che la strada del rinomino difende.

## Perche' non e' un bug della delete

La `delete` del file browser fa il suo mestiere: `shutil.rmtree` su un percorso
validato (`webui/workspace_files.py::delete_path`). Non sa cosa sia un progetto e
non deve saperlo.

Il difetto e' che **la creazione ha un padrone e la cancellazione no**.
`webui/project_create.py::create_project` conosce l'albero, il seme e il
registro; il suo inverso non esiste, quindi l'unico gesto disponibile e' quello
generico, che raggiunge un solo dominio dei due:

| dominio | chi lo crea | chi lo cancella oggi |
| --- | --- | --- |
| l'albero della wiki, `wikis/<nome>/` | `create_project` | la delete generica |
| le quattro tracce della chat | il primo turno | **nessuno** |

Le quattro tracce erano gia' enumerate in un posto solo —
`project_trace_paths()`, con una docstring che diceva gia' che e' li' che se ne
aggiunge una quinta. Il fix non inventa un registro: **usa quello che c'e'.**
L'unica cosa che cambia e' la casa: l'elenco vive ora in
`session/project_traces.py`, perche' da quando anche la cancellazione lo chiede
non appartiene piu' a chi insegue un rinomino — quello ne e' un *utente*, come
la cancellazione.

## Tre cambi

### A — `delete_project()`: la meta' mancante del ciclo di vita

`jenny/webui/project_delete.py`, specchio di `project_create.py`. Toglie l'albero
**e** le tracce via `project_trace_paths()`, poi rigenera `_index.md` con lo
stesso `reindex_wikis` che usa la creazione. Esposto come `project.delete` in
`webui/commands.py::COMMANDS`, accanto a `project.create`.

**Niente giornale: l'ordine sceglie lo stato intermedio.** Il rinomino ne ha
uno perche' i suoi stati a meta' sono tutti cattivi (la sessione sotto un nome e
la trascrizione sotto un altro). Qui si puo' fare di meglio, e la sequenza *e'*
il disegno:

1. si sgombera la sessione dalla cache, o il primo salvataggio la riscriverebbe;
2. **prima le tracce** — interrotti qui restano un progetto e una chat vuota:
   visibile, non corrotto, si finisce ritentando;
3. **poi l'albero**, che e' la sola sorgente di tracce nuove;
4. **le tracce un'altra volta**, perche' solo adesso la sorgente e' chiusa: un
   turno partito nella finestra fra 2 e 3 ha riscritto la sessione, e questa e'
   la passata che la trova. Costa quattro `stat`;
5. il registro, che e' derivato e non fa fallire niente.

L'ordine opposto — albero prima — lascerebbe l'orfano, cioe' il difetto. Un
giornale che proteggesse uno stato gia' innocuo sarebbe cerimonia.

**Cancella davvero, non archivia.** La regola di casa e' «retrocedere, non
cancellare» (memoria, fase 2) ma li' a potare e' il *modello*, senza che nessuno
abbia acconsentito. Qui e' l'utente, con una conferma davanti. La sicurezza sta
nella conferma che dice il vero — «3 file e 68 messaggi» — non in una copia
nascosta che non libera spazio su un telefono e che nessuno sa raggiungere.

### B — chiudere la porta di servizio

`/api/workspace/delete` rifiuta quando il bersaglio e' una radice di progetto —
o la sua `wiki/`, che e' lo stesso guasto per un'altra porta, perche' senza
quella cartella il progetto sparisce dal picker con la chat ancora attaccata al
nome.

**Ma l'utente il rifiuto non lo vede, ed e' voluto.** Il file browser e' il posto
dove uno va gia' a cancellare una cartella; mandarlo altrove sarebbe un divieto
con un vicolo cieco in fondo. Quindi il client **riconosce un progetto e usa la
porta giusta** (`_projectAt` confronta il percorso intero con l'elenco dei
progetti, che porta con se' il nome configurabile della cartella delle wiki), e
la conferma diventa quella piena. Il rifiuto del server resta come **garanzia
meccanica**: vale per un client vecchio, per una chiamata diretta, e per
qualunque strada che non passi da quel codice.

### C — il rilevatore meccanico *(la parte portante)*

Su un turno di progetto in cui la cartella **c'e'**, confrontare il
`project_wiki_id` della sessione con `wiki_id()` della cartella. Diversi = questa
conversazione non e' di questa cartella.

E' la stessa domanda che il codice fa gia' quando la cartella manca, spostata di
un passo. Costa una lettura di frontmatter. E copre **ogni** strada che aggira A
e B — adb, un ripristino, una sincronizzazione, un difetto futuro, e gli orfani
che sul telefono ci sono gia' adesso.

Su discordanza si **rifiuta e si dice la scelta**, come il rifiuto del passo 6:
non si indovina quale delle due storie l'utente voglia.

E dove l'utente e' presente a decidere davvero — `create_project` su un nome che
ha ancora una conversazione — il rifiuto diventa una **scelta**: riprendila, o
riparti da zero. Riprendere e' legittimo: e' come si recupera un progetto
cancellato per sbaglio.

## Cosa rende il fix dimostrabile

`tests/session/test_project_lifecycle.py`:

1. crea → un turno → cancella → **nessun percorso sotto il workspace porta piu'
   quel nome**. Cammina l'albero, quindi si accorge della quinta traccia il
   giorno che nasce.
2. la cancellazione passa da `project_trace_paths()` e non da una lista copiata.
3. la delete generica rifiuta una radice di wiki.
4. crea → cancella → ricrea: la chat del progetto nuovo e' **vuota**. E' la
   riproduzione del difetto, come test.
5. id discorde → rifiuto; id concorde → turno normale.
6. giornale interrotto a meta' → l'avvio finisce.

Il punto 1 e' il deliverable vero: e' un invariante, non una checklist che
qualcuno deve ricordarsi di aggiornare.


---

## Com'e' finita

Fatto tutto, il 24/08/2026, sul ramo `jenny-memory`.

| | dove |
| --- | --- |
| l'elenco delle tracce, con la sua nuova casa | `jenny/session/project_traces.py` |
| A — la cancellazione vera | `jenny/webui/project_delete.py`, comando `project.delete` |
| il registro, estratto perche' ora lo chiamano in due | `jenny/webui/wiki_registry.py` |
| la lettura per la conferma | `/api/project/describe` |
| B — la porta di servizio chiusa | `workspace_routes.py::_project_delete_refusal` |
| C — il rilevatore | `AgentLoop._refuse_reincarnated_project` |
| la scelta alla creazione | `create_project(conversation=...)`, `status: conversation_exists` |
| il client | `mobile-workspace.js` (cancella), `scope-chip.js` (chiede) |
| i test | `tests/session/test_project_lifecycle.py`, 21 casi |

**Calibrazione** — nessuna garanzia e' stata data per buona senza vederla fallire:

| garanzia tolta | cosa si e' rotto |
| --- | --- |
| la cancellazione non tocca le tracce | 4 test, fra cui l'invariante e la riproduzione del difetto |
| il rifiuto della delete generica | 2 test |
| il confronto degli id | 1 test |

La prima calibrazione e' stata istruttiva di suo: togliendo **solo** la prima
passata sulle tracce i test restavano verdi, perche' la quarta le prende
comunque. Il doppio giro non e' cerimonia, e ora c'e' una prova che lavora.

## Verificato sul telefono, 24/08/2026, build PID 6866

Sei proprieta', tutte dall'interfaccia, nessuna dedotta dal codice.

| | esito |
| --- | --- |
| il rilevatore scatta su uno stato **preesistente** (sessione legata a `6ac443f3f2c9`, cartella `00fc9eb19d20`) | turno rifiutato, due strade nominate |
| la conferma dice il vero | *«the conversation with it: 10 messages»* — sul disco: 11 righe, 10 con `role` |
| dopo la cancellazione non resta niente col quel nome | `find -name "*quaderno-x*"` vuoto; `_index.md` a zero occorrenze |
| il nome e' davvero libero | ricreato con la stessa sequenza che prima riportava 10 messaggi: **chat vuota** |
| un nome con una conversazione **chiede** | dialogo a tre uscite, conto giusto (2 messaggi) |
| «riprendila» adotta l'id | cartella `896cc03188f5` = id ricordato dalla sessione; turno successivo **non** rifiutato |

Un capo sciolto trovato provando e chiuso: dopo aver cancellato il progetto in
cui si era, il chip continuava a nominarlo. Niente si rompeva — il messaggio
seguente sarebbe stato rifiutato dal server, che la cartella la cerca — ma e' uno
schermo che dice il falso, ed e' proprio il genere di falso che poi si scambia
per il difetto che questa cancellazione e' venuta a chiudere
(`ScopeChip.leaveIfSelected`).
