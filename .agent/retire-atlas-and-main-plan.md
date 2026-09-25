# Togliere Atlas e la wiki `main` — il piano

> *I nomi delle wiki in questo file sono inventati, salvo `main`, che e' un default
> del codice. I numeri sono quelli misurati sul telefono il 03/09/2026. Il repo e'
> pubblico; la mappa con i nomi veri sta in `roadmap/smontaggio-main.md`, che e'
> gitignored.*

## Cosa si toglie

Due cose, ed e' una sola decisione perche' una esisteva per l'altra.

**`main`** e' la wiki che ha raccolto tutto quello che non aveva una casa: 65 pagine
e 35 file grezzi, e il suo stesso `wiki/index.md` la divide gia' in sei sezioni che
sono sei soggetti diversi. Il suo `AGENTS.md` di scope esclude tre destinazioni
(`wiki-blackberry/`, `projects/`, `tasks.md`) che in questo workspace **non
esistono**: e' lo scope di un'altra installazione, e uno scope che rimanda a un
albero che non c'e' non puo' respingere niente. Due dei suoi soggetti sono gia'
wiki a parte — 25 pagine di viaggio accanto a una wiki di viaggio da 7, e 22 file
grezzi le cui pagine compilate stanno **in un'altra wiki** — quindi non e' da
riordinare: e' da spezzare nelle wiki che il disegno dei progetti gia' prevede.

**Atlas** e' il job che compila `memory/WIKI.md`, la rubrica che entra in ogni
prompt della chat personale. Misurato sul file vero: le sue **12 voci vengono
tutte da `main`**, zero dalle altre nove wiki (150 pagine). Non e' un caso ma
[atlas.py:180](../jenny/agent/atlas.py): al modello arriva l'elenco pagine della
sola `default_wiki`, delle altre una riga di scope, e da una riga di scope non si
fa una voce. Atlas non e' la rubrica delle wiki: e' la rubrica di `main`. Spezzata
`main`, non ha piu' niente da compilare; puntato su un'altra wiki, fa la rubrica
di quella.

E la parte di `WIKI.md` che vale — l'elenco delle wiki con lo scope — e' l'**input**
di Atlas, non il suo output: la costruisce
[`AtlasStore.build_inventory`](../jenny/agent/atlas.py) in Python, il modello la
ricopia. Quella parte si tiene, resa al volo, e si smette di farla passare da un
modello ogni sei ore.

### La prova che non serve, presa dal transcript

Il 03/09 alle 22:24 l'utente ha chiesto di una persona che ha una pagina in una
wiki di viaggio. Un solo tool call, `read_file` sulla pagina giusta, senza
cercare. In `WIKI.md` quel nome **non c'era**. Il nome era entrato nella finestra
due ore prima, da un `list_dir` fatto per un altro motivo. Sei secondi dopo, un
nome che non sta da nessuna parte: `grep wikis`, `grep memory`, `recall_history`,
`grep history.jsonl` → *«non ce l'ho da nessuna parte, me lo sono controllato»*.
Entrambi i percorsi sono quelli che questo piano tiene; nessuno dei due passa da
Atlas. E nello stesso transcript, alle 20:11, il modello si era gia' procurato da
solo la lista delle wiki (`list_dir("wikis")` + `read_file("wikis/_index.md")`):
il blocco che si mette nel prompt gli risparmia due chiamate che fa comunque.

## L'invariante che si guadagna

> **Il prompt conosce le wiki per nome e scope. Il contenuto si legge.**
> Una wiki la cui riga non basta a decidere se aprirla e' una wiki da spezzare.

E' cio' che rende il cambio un miglioramento e non un risparmio: la regola si
applica da sola. Il giorno che si riforma un calderone, la sua riga diventa
impossibile da scrivere e lo si vede al primo prompt. Oggi niente impedisce a
`main` di rinascere sotto un altro nome; Atlas la compensava (una rubrica per
entita' serve *perche'* il nome della wiki non dice niente), e la compensazione
nascondeva il sintomo.

Tre conseguenze pratiche, in ordine di peso:

1. **Da tre copie a una fonte viva.** «Quali wiki esistono» era in `_index.md`
   (script), in `WIKI.md` (Atlas, ogni 6h) e sul disco. Resta il disco.
2. **Un attore interno in meno**: specie di sessione, job cron, file di stato,
   fingerprint, bucket token, slash command, scheda nelle impostazioni, i18n.
3. **Un `grep` sa dire «no»; una rubrica no.** Una voce assente e' indistinguibile
   da una pagina inesistente, ed e' la forma da cui nasce un'invenzione. Il
   percorso su richiesta e' l'unico che dimostra un'assenza.

Quel che si perde: il richiamo **spontaneo** a livello di entita' nei turni che
l'utente non guida (heartbeat, cron). Non misurato — `sessions/heartbeat.jsonl`
tiene un run solo. Se dopo il cambio manca, i **nomi** delle entita' sono un `ls`
di `wikis/*/wiki/entities/`, gratis e sempre aggiornato; quel che costava un
modello erano le descrizioni. Non si ricompra Atlas.

---

## A — Il sostituto: il blocco `## Wikis`

Un blocco reso a ogni build del system prompt, **solo nella chat personale**:
stesso cancello di oggi (`not is_project_session_key and not
is_gardener_session_key`, [context.py:799](../jenny/agent/context.py)), perche'
dentro un progetto l'elenco degli altri soggetti e' la versione di lato della
fuga che il confine dei progetti chiude (clash 3 in `roadmap/project-sessions.md`).

```
## Wikis
Your wikis live under `wikis/`. Open `wikis/<name>/wiki/index.md` before answering
about one of these subjects; to find out whether something is recorded anywhere,
grep `wikis/`.
- **quaderno-x** — un quaderno di prova → wikis/quaderno-x/wiki/index.md
- **orto** — (no scope set) → wikis/orto/wiki/index.md
```

Decisioni, con la ragione:

| | scelta | perche' |
| --- | --- | --- |
| sorgente | `discover_wiki_roots` + `read_wiki_scope`, gia' in `utils/wiki_paths.py` | e' l'input di Atlas: nessuna logica nuova, nessun LLM |
| ordine | alfabetico, esplicito | prefisso stabile per la cache del provider: il blocco cambia solo quando cambia una wiki |
| conteggio pagine | **no** | costa una camminata su `wiki/` per wiki per turno (12 ms su 11 wiki, misurato per il giardiniere); il blocco deve essere piatto nel numero di pagine |
| scope mancante | si stampa `(no scope set)` cosi' com'e' | e' il sintomo che fa riempire `summary:`; nasconderlo rimette il calderone al riparo |
| tetto | costante nel modulo, `truncate_text_to_tokens` | O(wiki), non serve una manopola; niente config |
| dove | sotto `# Memory`, al posto di `## Wiki Directory` | e' dove il modello ha imparato a cercare l'indice; la composizione a sottosezioni indipendenti resta |
| `wiki.enabled = false` | nessun blocco | la funzione e' spenta, il prompt non deve nominarla |
| prosa | **una frase**, quella sopra | e' l'unica promessa in un prompt e quindi l'unica cosa da calibrare a mano (v. verifica) |

Costo: una `listdir` piu' una lettura di `AGENTS.md` per wiki, per build. Sotto
il millisecondo su dieci wiki; da misurare una volta sul telefono, e se supera
i 5 ms si mette una cache su mtime della cartella — non prima.

Test (`tests/agent/test_context_wikis.py`, che sostituisce
`test_context_wiki_memory.py`): presente nella chat personale con ogni wiki in
ordine; assente in `project:` e `gardener:` (si asserisce l'**assenza del nome
di un'altra wiki**, non solo dell'intestazione); `(no scope set)` per un
`summary:` segnaposto; assente con `wiki.enabled = false`; identico su due build
consecutive; troncato al tetto con dieci wiki finte da 2 kB di scope; niente
`# Memory` doppio quando `MEMORY.md` e' ancora il template.

## B — Togliere Atlas

Si cancella e si modifica. Regola sui commenti, che vale per tutto il piano:
**nessun commento puo' rimandare a codice che non esiste piu'.** Dove Atlas era
l'analogia («per la stessa ragione di Atlas») si mette il gemello che resta
(Dream, il giardiniere) o si scrive la ragione. Dove era storia («misurato il
25/08, i bucket dream e atlas…») la misura resta e il nome cade.

| file | azione |
| --- | --- |
| `jenny/agent/atlas.py` | **cancellare** |
| `jenny/templates/agent/atlas.md` | **cancellare**; toglierlo da `_SYSTEM_PROMPT_TEMPLATES` in `utils/android_assets.py:41` |
| `config/schema.py` | via `AtlasConfig` (159-197) e il campo `atlas` di `AgentDefaults` (433); v. D per la ritirata della chiave |
| `runtime/container.py` 428-441 | via la registrazione; v. D per il job gia' scritto |
| `runtime/cron_dispatch.py` | `_SYSTEM_WORKERS` → `("dream", "gardener")` (260); via il ramo in `_dispatch` (390) e `_run_atlas` (407-418); i tre commenti che lo nominano |
| `webui/worker_settings.py` | via il blocco `atlas` del payload (171-182), `ATLAS_REARM_KEYS` (234), la scrittura (294-306); docstring di modulo (3-15) |
| `webui/settings_routes.py` | via l'import (45) e il rearm (207-210) |
| `command/builtin.py` | via `cmd_atlas`, `_format_atlas_outcome`, le due `router.*("/atlas")` (610-665, 1059-1060), l'import a 16 |
| `command/specs.py` 135-136, `command/scope.py` 6 | via la voce e la menzione |
| `agent/tools/cron.py` 404 | via la riga di `_system_job_purpose` |
| `agent/context.py` | via `_DEFAULT_WIKI_DIRECTORY_TOKENS` (77), `wiki_directory_max_tokens` (521-523) e il suo passaggio in `loop.py:490`; il blocco 766-802 diventa A |
| `agent/memory.py` | via `wiki_file` (198-202), `read_wiki_memory` (229-232), `get_wiki_memory_context` (287-299); il commento in `get_archive_context` che lo cita come ragione |
| `agent/autocompact.py` 43 | `_INTERNAL_SESSION_PREFIXES = (DREAM_SESSION_PREFIX,)` |
| `agent/token_usage.py` | via `"atlas"` da `_INTERNAL_KIND_TO_SOURCE` (44); **resta** in `_SOURCE_KEYS` (36) come bucket legacy — v. D |
| `session/keys.py` | via `ATLAS_SESSION_PREFIX` (83) e la sua riga in `_INTERNAL_KIND_BY_PREFIX` (107), le due docstring — v. D per i file di sessione gia' sul disco |
| `utils/wiki_paths.py` | via `wiki_fingerprint`, `_stat_line`, `iter_wiki_sources` (617-703): l'unico utente era Atlas. I loro test in `tests/utils/test_wiki_paths.py` cadono con loro |
| `templates/agent/dream.md:15`, `dream_review.md:11` | via le frasi «`memory/WIKI.md` is not yours» |
| `skills/memory/SKILL.md` 3, 15, 38 | la descrizione e le due righe su `WIKI.md` → «le wiki sono elencate nel prompt; segui `wikis/<nome>/wiki/index.md`» |
| `templates/ui/assets/mobile-settings.js` | via la scheda (830-838), la riga del toggle (861), il prefisso `atlas_` in `family` (884) e nella rilettura (923, 953-956); il commento 709-711 e la menzione a 325 |
| `templates/ui/assets/i18n/{it,en}.json` | via l'oggetto `atlas` (68) e le cinque chiavi `settings.workers.atlas*` (198-202) |
| `shared/api-client.js:402`, `shared/battery-exemption.js:5` | commenti |
| commenti-analogia | `hook.py` 90-100, `loop.py` 682-692 e 1317-1318, `loop_tasks.py` 186-196, `internal_run.py` 3-13 e 44, `gardener.py` 9, 26, 482, 768, 801, 1172, 1315, 1586, `gardener_state.py` 11 e 437, `dream_review.py` 12, 54, 143, `tools/filesystem.py` 125, 135, 276, 500, `webui/wiki_search.py` 264, `cron/service.py` 806 e 1208, `session/turn_visibility.py` 76, `skills/llm-wiki/scripts/lint_wiki.py` 463 |

Test da **cancellare** (7): `tests/agent/test_atlas_{prompt_path,run,store,tools}.py`,
`tests/command/test_builtin_atlas.py`, `tests/config/test_atlas_config.py`,
`tests/cron/test_cron_dispatch_atlas.py`.

Test da **modificare** — usano Atlas come *fixture* di «un job di sistema
qualunque», e vanno spostati sul giardiniere o su Dream senza perdere il caso:
`test_internal_session_eviction.py` (22 riferimenti: il caso «venti run non
lasciano venti voci» resta, sul giardiniere), `test_cron_schedule_survives_restart.py`,
`test_cron_store_recovery.py`, `test_prompt_matches_offered_tools.py`,
`test_container_registers_configured_jobs.py`, `test_worker_settings*.py` (3),
`test_settings_bounds_come_from_the_schema.py`, `test_settings_routes.py`,
`test_token_usage.py`, `test_internal_key_vocabulary.py`, `test_turn_visibility.py`,
`test_command_scope.py`, `test_gardener_settings_reach_the_dispatcher.py`,
`test_cron_dispatch_gardener.py`, i test di Dream che citano la frase tolta dal
template. I test che asseriscono `## Wiki Directory`
(`test_project_prompt_contract.py`, `test_project_boundary_end_to_end.py`,
`test_gardener_prompt_boundary.py`) passano a `## Wikis` **e** al nome di una
wiki: l'intestazione e' una stringa, il nome e' la fuga.

## C — Togliere `main` dal codice

`main` nel codice e' un default, non una wiki: sparisce il default.

| dove | azione |
| --- | --- |
| `config/schema.py:666` `WikiConfig.default_wiki` | via il campo; chiave ritirata (D) |
| `webui/wiki_routes.py:391` | via il caso speciale e `defaultWiki` dal payload di `/api/config`: **nessun client lo legge** (grep su `templates/ui/`, zero occorrenze in JS e HTML). `homePath` resta |
| `agent/tools/python_exec_builtins.py:480-481` | l'esempio in docstring usa un nome neutro |
| `skills/llm-wiki/SKILL.md` 52, 83; `scripts/reindex_wikis.py:16` | gli esempi usano un nome neutro |
| `docs/reference/configuration.md:398`, `docs/using/memory.md:236` | via le righe |

Il registro `wikis/_index.md` e la sua parte scritta a mano sono **dati**, sul
telefono: stanno nel file di roadmap.

## D — La ritirata pulita

E' la parte che fa di questo una migrazione e non una cancellazione. Ogni voce
lascia sul telefono uno stato che il codice nuovo non conosce, e ognuna ha un
modo preciso di far male in silenzio.

**D1 — il job cron `atlas` gia' scritto in `cron/jobs.json`.** E' un
`system_event`, quindi `remove_job` lo **rifiuta** (`service.py:1243`). Senza il
ramo in `_dispatch` cadrebbe su «unbound agent job» e produrrebbe un warning e
una `CronJobSkippedError` ogni sei ore, per sempre. Serve una
`CronService.retire_system_job(job_id)` che ignora la protezione — usata da un
posto solo, `GatewayContainer.build`, su un elenco chiuso
`_RETIRED_SYSTEM_JOBS = ("atlas",)`, prima di registrare quelli vivi — e che
pota i suoi `cron/runs/`. Log a INFO una volta. Test: uno store con il job
protetto → dopo `build` non c'e', i run non ci sono, un tick non avvisa; un job
utente con lo stesso nome **non** viene toccato (si ritira per id).

**D2 — le chiavi di config `agents.defaults.atlas` e `wiki.defaultWiki`.** Il
loader avvisa a ogni caricamento sulle chiavi sconosciute (`loader.py:64-71`) e
`mutate` le **conserva** per progetto (`_merge_unknown`): una chiave tolta dallo
schema e' indistinguibile da una di una versione futura, quindi resterebbe nel
file e nel log per sempre. Serve la nozione mancante: `RETIRED_KEY_PATHS` in
`config/loader.py`, che `_unknown_key_paths` non elenca e `_merge_unknown` non
riporta. Piu' `CURRENT_CONFIG_VERSION = 2` con un passo v2 vuoto di valori: serve
perche' `persist_schema_migrations` riscrive il file **una volta all'avvio** solo
se lo stamp e' indietro, e cosi' le chiavi cadono al primo boot invece che alla
prima impostazione che l'utente cambia. Test: un `config.json` con entrambe →
nessun warning, dopo `mutate` non ci sono; una chiave davvero sconosciuta
accanto a loro avvisa e sopravvive — la lista chiusa non deve diventare un
lasciapassare.

**D3 — i file.** `memory/WIKI.md`, `memory/.atlas_state.json`,
`sessions/atlas_*.jsonl` (nove sul telefono). Nessuno li rilegge piu' e nessuno
li pota piu' (`_prune_sessions` se ne va con Atlas). `WIKI.md` in particolare e'
uno **schermo che dice il falso**: il file browser lo mostra sotto `memory/`, e
Dream perde la frase che gli diceva di non toccarlo. Una spazzata all'avvio,
accanto a `_migrate_wikis` (`container.py:231`), con un elenco chiuso di
percorsi e pattern, idempotente, un log per file tolto. `memory/WIKI_POLICY.md`
**non** si tocca: e' scritto dall'utente (assente sul telefono; se c'e' resta
inerte). Test: i tre spariscono, un file dell'utente accanto no, la seconda
passata non fa niente e non logga.

**D4 — il ledger dei token.** `token-usage.json` sul telefono ha giorni con il
bucket `atlas`. Se `"atlas"` esce da `_SOURCE_KEYS`, `_clean_source` ripiega su
`"system"` e la spesa passata **cambia etichetta** alla prossima lettura. Un
registro e' l'unico posto dove la storia deve restare vera: `"atlas"` resta in
`_SOURCE_KEYS` come bucket legacy, con il commento, ed esce da
`_INTERNAL_KIND_TO_SOURCE` cosi' niente di nuovo puo' finirci. Test: un file con
un giorno `atlas` si rilegge identico.

**D5 — la specie di sessione.** Con D3 non restano file `atlas_*`, e Atlas non ha
mai scritto in `history.jsonl` (zero righe sul telefono; `internal_run.py:12`).
Quindi il prefisso si toglie del tutto; `_warn_unclassified` copre l'eventuale
residuo con un warning una volta.

Le **due occorrenze permesse** della parola in `jenny/` dopo il piano: il pattern
dei file in D3 e il bucket legacy in D4. Entrambe con il commento che dice
perche' la parola e' li'.

## Docs

`docs/` alimenta il sito (`flagdizero/jenny-site`): titolo dall'`# H1`, URL dal
percorso. **Nessun file si sposta o si rinomina**; i titoli si possono cambiare.

| file | cosa cambia |
| --- | --- |
| `using/memory.md` | H1 → *Memory and Dream*; via §«Atlas: the wiki side of memory» (73-86), §«Atlas» della config (212-237), le righe di `WIKI.md` e `.atlas_state.json` nell'albero (95, 100), la riga `/atlas` (138), i due gotcha (252-253), la riga 126; in cambio un paragrafo su come Jenny sa quali wiki ha (il blocco A) |
| `using/wiki.md:19` | il paragrafo su Atlas diventa il paragrafo sul blocco |
| `using/projects.md:39` | «plus Atlas's wiki directory» → «plus the list of your wikis»; l'ancora `memory.md#atlas-the-wiki-side-of-memory` non esistera' piu' |
| `using/scheduling.md` 98-108 | i job di sistema: `dream`, `heartbeat`, `gardener`, `update_check` |
| `using/slash-commands.md` 18, 40, 206-226, 281, 286, 310 | via `/atlas` |
| `using/gardener.md:5`, `using/chat.md:123`, `using/telegram.md:64`, `README.md:26` | testo dei link e frasi |
| `internals/architecture.md` 182-192, `internals/concepts.md` 97-101, `internals/agent-turn.md` 23, 54, 87, 106, 111, 116, `internals/privacy.md:36` | via Atlas e `WIKI.md`; la tabella delle sessioni interne perde la riga `atlas:` |
| `reference/configuration.md` 91-93, 103-105, 398; `reference/settings.md:123`; `reference/tools.md` 355, 578-600 | via le righe; §«The two internal registries» diventa «The internal registry: Dream» |

`.agent/security.md` 35 e 78: le due frasi si riscrivono sul blocco.
`.agent/memory-probes.md`: la quinta sonda (Atlas, ~140-181) si toglie e il
titolo torna a dire quattro. I **piani datati** (`stale-name-bindings-*`,
`memory-plan*`, `project-lifecycle-plan.md`) non si toccano: sono la storia di
quando Atlas c'era, e riscriverla e' il modo di perderla.

## Ordine di esecuzione

Il codice e i dati sono indipendenti: il blocco A elenca quel che c'e', `main`
compresa finche' esiste; e spezzare `main` non richiede il codice nuovo. Ma il
codice va **prima**, cosi' il prompt segue lo smontaggio in tempo reale invece
che al tick successivo, e cosi' i quattro `(no scope set)` sono davanti agli
occhi mentre si scrivono.

1. **Ramo** `feat/retire-atlas-and-main`. Sign-off dal primo commit (v. memoria
   DCO).
2. **A** — il blocco, con i suoi test, mentre Atlas e' ancora vivo: per un commit
   il prompt ha entrambi (`## Wikis` e `## Wiki Directory`), ed e' voluto — e' il
   solo momento in cui si puo' asserire che il blocco nuovo dice le stesse wiki
   del vecchio.
3. **D1–D5** — le ritirate, ognuna col suo test, **prima** di togliere il codice
   che le rende necessarie: cosi' ogni test ha ancora il modo di produrre lo
   stato vecchio con il codice vero invece che a mano.
4. **B** e **C** — la rimozione. `ruff`, `pyright` sul sottoinsieme bloccante,
   `pytest -q`.
5. **Docs** e i due file di `.agent/`.
6. **Accettazione statica**: `grep -ri atlas jenny/ tests/ docs/` → le due
   occorrenze di D piu' i loro test, nient'altro; `grep -rn "default_wiki\|defaultWiki"`
   → zero; `grep -rn "WIKI.md"` → zero fuori dai piani datati.
7. **Versione**: `pyproject.toml` e `android/app/build.gradle.kts:60` insieme
   (`tests/test_package_version.py` li tiene allineati). E' una rimozione di
   feature con migrazione di stato: minor, non patch.
8. **Build** a worktree pulito (v. memoria release), `installDebug`, poi la
   verifica sotto.
9. **PR** su `main`; il merge lo decide l'utente.
10. Poi lo **smontaggio dei dati**, dal file di roadmap, con `## Wikis` a fare da
    cruscotto.

## Verifica sul telefono

Nessuna dedotta dal codice; ognuna si guarda.

| cosa | come | atteso |
| --- | --- | --- |
| D1 | logcat all'avvio; `cron/jobs.json` via root | «retired system job atlas»; nessun job `atlas`; nessun warning «unbound» dopo 6h |
| D2 | logcat; `config.json` via root dopo il boot | nessun «Config keys not recognised»; `configVersion: 2`; le due chiavi assenti; `.bak` con la label giusta (e' la firma di una scrittura dell'app, v. memoria) |
| D3 | `ls memory/ sessions/` via root | niente `WIKI.md`, `.atlas_state.json`, `atlas_*.jsonl`; `MEMORY.md` e `history.jsonl` con lo stesso md5 di prima |
| D4 | pannello token in Impostazioni | i giorni vecchi mostrano ancora la loro spesa; nessun bucket nuovo `atlas` |
| A, il contenuto | in chat: *«quali wiki hai?»* | l'elenco esatto delle cartelle, **zero tool call** (si legge nel transcript) |
| A, la frase | in chat: un soggetto che ha una pagina → e uno che non esiste | il primo apre `wikis/<nome>/wiki/index.md` prima di rispondere; il secondo finisce in un `grep wikis` e in un no |
| A, il confine | dentro un progetto: *«quali altre wiki ho?»* | non le sa dal prompt: deve cercarle (il transcript mostra un `list_dir`) |
| A, il costo | un log a DEBUG con la durata del blocco, tolto dopo la misura | < 5 ms su dieci wiki |
| impostazioni | Impostazioni → lavoratori periodici | due schede, Dream e giardiniere; `/api/settings/workers` senza `atlas` |
| `/atlas` | in chat | «unknown command», come qualunque comando inesistente |

L'ultima, la frase in A, e' una promessa in un prompt: va rifatta su due modelli,
come le altre sonde di `memory-probes.md`, e **in una sessione fresca** — il
modello imita la propria cronologia (v. memoria sui test comportamentali), e la
sessione che ha visto Atlas misura Atlas.

## Rischi, detti prima

- **Il prefisso del prompt cambia** una volta per tutti gli utenti (via `## Wiki
  Directory`, dentro `## Wikis`): un giro di cache del provider fredda. Una volta.
- **La perdita del richiamo spontaneo** (sopra). Si misura dopo, non si previene
  con un Atlas piu' piccolo.
- **Il ledger** (D4): tenere `"atlas"` in `_SOURCE_KEYS` e' l'unica eccezione al
  «sparire del tutto», ed e' deliberata. Se l'utente preferisce che la spesa
  passata confluisca in `system`, e' una riga e un test da cambiare — ma va detto
  che riscrive la storia.
- **I test-fixture**: spostare Atlas sul giardiniere in `test_internal_session_eviction.py`
  cambia anche la cadenza del caso (6h → per progetto). Il caso da conservare e'
  «N run non lasciano N voci», non il numero.

---

## Com'e' finita

Fatto il 03/09/2026, in giornata, sul ramo `feat/retire-atlas-and-main` — dieci
commit firmati, nell'ordine del piano (A con Atlas vivo, D col codice vecchio,
poi B, C, docs, versione, e una correzione trovata sul telefono).

| | esito |
| --- | --- |
| suite | 9.156 verdi su 3.14, 9.155 su 3.11 (7/8 skip di piattaforma); ruff pulito; pyright 0 sul sottoinsieme bloccante |
| A, costo | 0,25 ms mediana su 10 wiki e 400 pagine (Mac), 1.137 caratteri ≈ 285 token |
| accettazione statica | `atlas` in `jenny/`: le tre voci di D (job ritirato, chiavi ritirate, pattern dei file), il bucket legacy, e il nome di questo piano. In `docs/`: tre frasi che dicono cosa e' stato ritirato |
| APK | 0.10.0 / versionCode 15, firmata, `assets/jenny_src` identico al repo |

**Verificato sul telefono, due avvii.** Primo avvio: `retired system job atlas (0
run records)`, undici file spazzati (`WIKI.md`, `.atlas_state.json`, nove sessioni),
`Config schema stamped at version 2` con `agents.defaults.atlas` e `wiki.defaultWiki`
assenti dal file e il `.bak` con le categorie MLS dell'app, `MEMORY.md` e
`history.jsonl` con lo stesso md5 di prima. `cron/jobs.json`: dream, gardener,
heartbeat, update_check. Secondo avvio: zero righe di ritiro (idempotente), zero
traceback, giornale del cron a 0 byte. `/api/settings`: `workers` = gardener +
compattazione. `/api/config`: niente `defaultWiki`. `/atlas`: comando sconosciuto.

**Il blocco, letto dal telefono.** Chiesto alla chat personale di copiare
testualmente il `## Wikis` del proprio prompt: 13 righe, una per cartella, in
ordine alfabetico, con quattro `(no scope set)` (le stesse quattro), zero tool
call, 11,7 s. Dentro un progetto la stessa domanda risponde `NESSUNO` in 2,3 s,
zero tool call: il cancello tiene.

### Due cose che sono andate diversamente da qui

**Il ritiro del job cron ha prodotto sei traceback in due minuti** al primo
avvio: `KeyError: 'atlas'` da `_merge_action`. `retire_system_job` girava prima di
`start()` e quindi scriveva nel giornale delle azioni, come fa `remove_job` a
servizio fermo; `register_system_job`, un attimo dopo, salvava lo store senza il
job; ogni `_load_store` rigiocava la riga «del» su uno store che il job non
l'aveva piu', `pop` sollevava e il `continue` saltava `changed = True` — quindi il
giornale non si svuotava mai. Innocuo (il cron ha continuato a scattare: Dream e'
girato alle 23:34) ma **per sempre**. Due correzioni, una generale e una locale:
un «del» per un job assente e' un no-op che conta come applicato, e il ritiro
salva lo store direttamente come la registrazione con cui condivide la fase. Il
test riproduce entrambe. La lezione e' quella di sempre: D1 aveva un test verde
che costruiva lo stato vecchio con il codice vero, ma non passava dal giro
completo «avvio, salva, ricarica» — cioe' misurava il meccanismo e non il montaggio.

**Il primo APK portava ancora `jenny/agent/atlas.py`.** `assets/jenny_src/` e'
un mirror scritto da un task Gradle `Copy`, che non toglie mai quel che e' sparito
dalla sorgente; `app.imy`, il codice che gira davvero, era pulito. Ora i due
mirror sono `Sync`, e il controllo e' una differenza di insiemi fra l'elenco
dell'APK e `find jenny -name '*.py'` (v. memoria *release-builds-need-a-clean-worktree*).

**La prima domanda di verifica ha misurato la cronologia, non il prompt.** «Quali
wiki hai?» sulla sessione personale calda ha risposto con le cinque wiki di cui
si era parlato negli ultimi turni, in 2,5 s e senza tool — sbagliato, e non per
colpa del blocco, che c'era intero (la seconda domanda, «copia il blocco», l'ha
dimostrato). E' esattamente l'avvertenza scritta sopra sulla sessione fresca, e
vale come misura: **la domanda giusta e' quella che chiede il prompt, non quella
che chiede la memoria**.

### Resta aperto

- **G.5 e G.7** (il soggetto con pagina apre l'indice prima di rispondere; il
  nome inesistente finisce in un `grep`; e tutto rifatto con un secondo modello) —
  vanno in una sessione fresca, e questa installazione ha un provider solo.
- **Il richiamo spontaneo** nei turni non guidati: non misurato, come previsto.
- **I dati.** Mentre il codice finiva, l'utente ha gia' cominciato lo smontaggio
  dal telefono: `quaderno-c`, `quaderno-b`, `quaderno-d`, `quaderno-e` esistono con
  il loro `summary:`, e `wikis/main` non c'e' piu'. Le altre quattro, ancora `(no scope set)`,
  sono da riempire.
