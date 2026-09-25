# Seconda revisione di `feat/la-casa`: il piano delle nove correzioni

Stato: **fatto**, 25/09/2026 — tutte le voci, più la decima decisa lungo la strada; suite
verde su 3.14 (10.957) e 3.11 (10.947), build `d80229c` installata sul Titan 2. Gli esiti,
con quel che le prove sul telefono hanno cambiato del quadro, stanno in fondo
(«Esito, voce per voce»): **la voce 2 era un falso positivo**, e la 1 è più stretta di
come era scritta.

La seconda revisione (`/code-review` high, 25/09) ha riletto soprattutto gli otto commit
di correzione della prima (`41c7d20`..`359f205`) e ne ha tirato fuori nove voci: due
serie sul rinomino dei quaderni, sette minori. Questo file dice come si chiude ciascuna,
in che ordine, con quali prove. Ogni voce è un commit a sé, firmato (`-s`), come i
precedenti.

## Ordine

Prima le due voci che possono lasciare **un orfano su disco** (1, 2), poi quella che
cambia il comportamento dei job al cambio d'ora (5), poi il resto. Le voci 3+4 stanno
nello stesso commit perché toccano le stesse tre righe; la 9 viaggia con la 1, che crea
il metodo da provare.

| # | Voce | Gravità | File principali |
|---|------|---------|-----------------|
| 1 | La guardia del rinomino non vede subagent e giardiniere | seria | `agent/loop.py`, `agent/subagent.py`, `agent/gardener.py`, `webui/commands.py`, `runtime/container.py` |
| 2 | Il rinomino non sposta i job cron del quaderno | seria | `cron/service.py`, `webui/commands.py`, `webui/gateway_services.py` |
| 5 | «Fisso» contro «a ripetizione» si decide dal testo | media | `cron/cronexpr.py` |
| 3+4 | L'avviso per una pagina scartata si ripete, ed è in italiano | minore | `config/schema.py` |
| 6 | Il rifiuto per turno in corso non passa dall'i18n | minore | `casa-app.js`, `i18n/{it,en}.json` |
| 7 | Un `browser_open` abbandonato crea comunque la WebView | minore | `JennyBrowserBridge.kt` |
| 8 | Un anno esplicito oltre +50 anni è rifiutato | minore | `cron/cronexpr.py` |
| 9 | Il collegamento nel container non ha un test | test | `runtime/container.py`, `tests/runtime/` |

## 1 — Una guardia che veda tutto quel che scrive sotto il nome del quaderno

**Il difetto.** `project.rename` chiede `AgentLoop.active_session_keys()`, cioè le code
dei turni di chat in volo. Due scrittori ne restano fuori:

- **un subagent** lanciato dal quaderno sopravvive al turno che l'ha creato. Quando
  finisce scrive `subagents/records/project_<vecchio>.jsonl` e annuncia il risultato in
  `project:<vecchio>`, cioè ricrea proprio l'orfano che `41c7d20` doveva impedire;
- **una passata del giardiniere** gira sotto `gardener:<nome>-<ts>` e scrive le pagine
  nella cartella della wiki per percorso: rinominata la cartella a metà passata, le
  scritture ne ricreano una con il nome vecchio.

**La correzione.** Un concetto nuovo, «le chiavi occupate», distinto da quello che c'è:

- `SubagentManager.active_origin_session_keys()`, nuovo e pubblico: le chiavi d'origine
  con almeno un task vivo. La struttura c'è già (`_session_tasks` +
  `get_running_count_by_session`), manca l'elenco;
- `gardener.passes_in_flight() -> frozenset[str]`, nuovo e pubblico: i nomi in
  `_PASSES_IN_FLIGHT`;
- `AgentLoop.busy_session_keys()`: i turni in volo ∪ le origini dei subagent vivi ∪
  `project:<nome>` di ogni passata del giardiniere.

`active_session_keys` **non cambia**: l'autocompact e il giardiniere la leggono con il
significato «un turno di chat sta usando la sessione», e allargarla cambierebbe cosa fanno
loro. Il campo di `CommandContext` diventa `busy_session_keys`; il messaggio di rifiuto
resta uno solo, e la voce 6 lo traduce.

**Prove.**
- test sul metodo: un subagent vivo con origine `project:viaggio` fa comparire la chiave,
  uno finito no; una passata in `_PASSES_IN_FLIGHT` per `viaggio` fa comparire
  `project:viaggio`;
- il test del comando già esistente (`test_the_command_refuses_while_a_turn_is_running_there`)
  continua a passare con il campo rinominato;
- sul telefono: un subagent lungo lanciato da un quaderno di prova, rinomino durante:
  il toast di rifiuto, e nessuna traccia nuova sotto il nome vecchio a subagent finito.

## 2 — I job cron seguono il quaderno

**Il difetto.** Un job creato dentro un quaderno salva `payload.session_key` e
`payload.origin_chat_id` = `project:<nome>`. Il rinomino sposta cartella e chat ma non il
job: al primo scatto il turno parte su `project:<vecchio>` e ricrea sessione e trascrizione
sotto il nome vecchio, senza cartella.

**La correzione.**
- `CronService.retarget_session(old_key, new_key) -> int`: riscrive `session_key` e
  `origin_chat_id` dei job che portano la chiave vecchia (e le occorrenze in
  `origin_metadata`, se ci sono: da verificare sui job veri del telefono prima di
  scriverla). Stessa forma di `remove_job`: a servizio avviato `_save_store()` +
  `_arm_timer()`, a servizio fermo il giornale — con un'azione `update`, che
  `_merge_action` già sa rileggere (tutto ciò che non è `del` passa da `_update`);
- `CommandContext` prende il getter del servizio cron, che `build_gateway_services`
  riceve già (`get_cron_service`);
- in `project_rename`, **dopo** il rinomino riuscito e fuori dal thread, come
  `rinomina_pagine_di`: un fallimento lo scrive nel log e non disfa il rinomino.

Fuori da questo giro, di proposito: i job di un quaderno **cancellato**. È la stessa
famiglia, ma non è fra le nove voci; se ne decide a parte (v. «Domande aperte»).

**Prove.** Un test sul servizio (job con la chiave vecchia → chiave nuova; uno di un altro
quaderno intatto; servizio fermo → azione nel giornale che al caricamento si applica); un
test sul comando che il seguito cron viene chiamato solo a rinomino riuscito.

## 5 — «A ripetizione» si decide dai valori, non dal testo

**Il difetto.** `repeating` guarda se il minuto o l'ora *cominciano con* `*`. Così
`0 * * * *` e `0 0-23 * * *`, lo stesso lavoro orario, seguono regole opposte al cambio
d'ora, e il secondo resta zitto un'ora e dieci.

**La correzione.** Un campo conta come «stella» se il suo testo comincia con `*`
**oppure** se i suoi valori espansi sono una progressione regolare che parte dal minimo
del campo e copre il ciclo (`0-23`, `0-23/2`, `0,15,30,45`). Si calcola sui valori già
espansi da `parse`, quindi senza un secondo parser. È un allargamento della regola di
Vixie, non un'altra regola: ogni espressione che era «a ripetizione» lo resta.

**Prove.** Casi nuovi in `test_cronexpr.py` (le due grafie danno la stessa sequenza al
cambio d'ora d'autunno e di primavera); il confronto minuto per minuto dello script di
verifica sulle espressioni nuove; i campioni di croniter restano verdi (lontano dai cambi
d'ora le due regole coincidono).

## 3+4 — Un avviso per pagina, in inglese

**Il difetto.** `_pagine_che_si_disegnano` scarta la riga dal modello ma non dal file, e
`load_config()` non ha cache: circa 59 chiamanti, più d'uno per turno. Finché nessuno
riscrive la config lo stesso avviso esce a ogni lettura. E i tre messaggi sono in
italiano, contro la regola di AGENTS.md («log messages … English»).

**La correzione.** Il validatore non riscrive il file (il loader legge e basta, e ogni
scrittura passa da `store.mutate`), quindi si tiene a memoria di processo l'impronta delle
righe già segnalate: un avviso per riga storta per avvio, poi silenzio. Messaggi in
inglese: `"casa page dropped ({reason}): {row!r}"`.

**Prove.** Un test: due `CasaConfig` di fila con la stessa riga storta → un avviso solo
(catturato con il sink di loguru); una riga diversa → un secondo avviso.

## 6 — Il rifiuto per turno in corso nella lingua dell'utente

**Il difetto.** `casa-app.js` mette `err.message` dentro `casa.quaderno.renameFailed`: il
rifiuto «Jenny is still working…» esce in inglese in un toast italiano.

**La correzione.** `wsManager.request` porta già `err.code`. Se il codice è `conflict`,
`renameNotebook` mostra una chiave nuova, `casa.quaderno.renameBusy`, in `it.json` e
`en.json` (es. «Jenny sta ancora lavorando in «{name}»: rinominalo quando ha finito»).
Il testo del server resta in inglese: è il messaggio per i log e per gli altri client.

**Prove.** Un test client nello stile di `tests/webui/test_casa_*_client.py`: un rifiuto
`conflict` → la chiave nuova, un altro errore → quella di sempre; il test di parità delle
chiavi i18n resta verde.

## 7 — Un'apertura abbandonata non costruisce niente

**Il difetto.** Il blocco in ritardo chiama `ensureWebViewOnMain()` prima di trovare il
cancello chiuso: resta una WebView viva (`url=null`, visto con la sonda) fino a
`browser_close`.

**La correzione.** In testa al blocco, `if (gate.get() == GATE_ABANDONED) return@call false`.
Il `compareAndSet` più sotto resta: è lui a decidere, questa riga risparmia solo il
lavoro.

**Prove.** Il contratto sul sorgente (`test_browser_open_reports_a_failed_start.py`): la
riga sta prima di `ensureWebViewOnMain`. Sul telefono la stessa sonda usa-e-getta di oggi
(11 s di main thread occupato): dopo lo scatto, **nessuna** WebView creata, non più
`url=null`.

## 8 — Un anno esplicito si cerca dove sta

**Il difetto.** `next_after` si ferma a +50 anni anche quando l'espressione ha il campo
dell'anno, che arriva al 2099: `0 9 1 1 * 2080` è valida ma l'aggiunta la rifiuta come
«has no run before 2076».

**La correzione.** Con il campo dell'anno il limite della ricerca è la fine dell'ultimo
anno elencato, e il giorno di partenza salta al 1° gennaio del primo anno elencato che
non sia già passato (niente cinquant'anni di giorni scorsi a vuoto).

**Prove.** Test su `0 9 1 1 * 2080` e su un'espressione con anni tutti passati (rifiutata).
I campioni di croniter vanno riletti: se croniter su un anno oltre +50 dava un errore, qui
ora c'è un'esecuzione, e quella differenza va aggiunta alle **differenze volute** nel
cappello del modulo e del test (sarebbe la quarta), non nascosta.

## 9 — Il collegamento nel container ha un test

**Il difetto.** Il lambda del container che legge i turni in volo non ha un test: un
rinomino del metodo passerebbe verde e romperebbe `project.rename` solo sul telefono.

**La correzione.** Con la voce 1 il lambda diventa un metodo,
`GatewayContainer._busy_session_keys()`, che si prova su un container nudo con un agente
finto (lo stile di `test_container_snapshot_wiring.py`): senza agente `()`, con agente
quel che l'agente dice.

## Chiusura

1. suite intera su 3.14 **e** sul venv 3.11, ruff, pyright sul sottoinsieme bloccante;
2. commit firmati, uno per voce, `git branch --show-current` prima di ognuno;
3. `assembleRelease` dall'albero pulito, `grep '[jenny]'` sul log, verifica che i
   cambiamenti siano dentro `app.imy`, `adb install -r`, riavvio, gateway vivo;
4. le prove sul telefono delle voci 1 e 7 (7 con una build usa-e-getta, poi di nuovo la
   build vera);
5. `ReportFindings` con l'esito di ciascuna voce, e questo file aggiornato voce per voce
   (data, cosa è andato, cosa si è deciso lungo la strada).

## Domande aperte

- ~~**I job cron di un quaderno cancellato.** Cancellarli insieme al quaderno?
  Disabilitarli?~~ **Disabilitarli**, deciso dall'utente il 25/09/2026 (voce 10). Ma v.
  sotto: la domanda poggiava sul falso positivo della voce 2.
- **Tenere o togliere `00ab172`?** Spostare e spegnere job che portano la chiave di un
  quaderno protegge uno stato che dal 22/08/2026 non si può creare (v. voce 2). È codice
  innocuo e provato, ma è superficie in più (un campo obbligatorio di `CommandContext`, due
  metodi del servizio). Da decidere con l'utente.

## Esito, voce per voce

| # | Commit | Esito |
|---|--------|-------|
| 1+9 | `92d79d5` | Fatto. `AgentLoop.busy_session_keys()` = turni ∪ origini dei subagent vivi ∪ passate del giardiniere; `GatewayContainer._busy_session_keys()` provato. |
| 2+10 | `00ab172` | Fatto, ma **la voce 2 era un falso positivo**: v. sotto. |
| 5 | `1789307` | Fatto, con la regola più stretta del previsto: valgono come `*` solo i valori *identici* a un `*/n`. |
| 3+4 | `1c41a2e` | Fatto. |
| 6 | `bd6addb` | Fatto. |
| 7 | `c64f1f5` | Fatto, e provato sul telefono. |
| 8 | `d80229c` | Fatto; la quarta differenza voluta rispetto a croniter. |

**Voce 1, cosa hanno mostrato le prove (25/09, quaderni usa-e-getta `prova-rev*`, poi
cancellati).** Il rinomino è rifiutato con `conflict` per tutta la vita del lavoro e
passa solo dopo l'annuncio del subagent; il nome vecchio resta pulito (tre giri: subagent
da 100 s, da 90 s, da 6 min). Ma **in nessuno dei tre la parte nuova è servita**: un turno
che lancia un subagent lo aspetta (`loop.py::_drain_pending`, tetto di 300 s), e anche
scaduto il tetto il turno è rimasto vivo fino alla fine del subagent (RUN 373 s). Il rifiuto
è quindi venuto dalla guardia sui turni. Un subagent che sopravvive al suo turno sul
telefono non si è visto; la parte «subagent» resta una difesa, provata dai test. La parte
«giardiniere» è reale — la passata gira sotto `gardener:` e scrive nella cartella — e non
è stata provata sul telefono (servirebbe un diario con delta in un quaderno di prova).

**Voce 2, il falso positivo.** Il tool cron rifiuta add/list/remove dentro una
conversazione di progetto dal 22/08/2026 (`f24f3dd`, `agent/tools/cron.py::_PROJECT_REFUSAL`),
e `add_job` non ha altri chiamanti: un job con `project:<nome>` non si può creare. La
revisione non aveva guardato il tool; se n'è accorta la prova sul telefono, dove Jenny ha
risposto «da una conversazione di progetto non si può schedulare». Sul telefono nessun job
porta la chiave di un quaderno. Quindi anche la voce 10, che ne dipendeva, protegge uno
stato che oggi non nasce (v. «Domande aperte»).

**Voce 5, perché più stretta.** La prima stesura contava come `*` qualunque passo
regolare che coprisse il ciclo, e così `0 9,21 * * *` (passo 12 sfasato di 9) diventava «a
ripetizione», e `0 1-23/2` non teneva comunque il ritmo. La regola finale è «gli stessi
valori di un `*/n`»: parte dal minimo del ciclo e lo copre. `9-17`, `9,21`, `1-23/2`
restano a orario fisso, come in Vixie. Riferimento minuto per minuto con le grafie per
esteso: 4.300 partenze, nessuna differenza.

**Voce 7, la prova.** La stessa sonda usa-e-getta della prima revisione (11 s di main
thread occupato): dopo lo scatto `webView=false`, dove prima restava una WebView viva con
`url=null`. La build vera reinstallata dopo non contiene la sonda.

**Voce 8, un inciampo.** L'esempio della revisione (`0 9 1 1 * 2080`) era sbagliato: a sei
campi il sesto è il secondo. Il difetto c'è nella forma a sette campi
(`0 9 1 1 * 0 2080`: prima «no run before 2076», ora il 2080).
