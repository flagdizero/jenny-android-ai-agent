# Nomi liberati a metà — quattro riscontri, e cosa farne

> *I nomi dei progetti in questo file sono inventati: la forma e i numeri sono
> quelli osservati davvero, il nome no. Il repo e' pubblico.*

Cercati il 24/08/2026 dopo il difetto dei progetti
([`project-lifecycle-plan.md`](./project-lifecycle-plan.md)), su tutte le entita'
del sistema. Questo file e' il piano; le prove sono qui sotto, riscontro per
riscontro.

## La forma che si cerca

> **Un'identita' e' un nome riusabile; lo stato indicizzato da quel nome vive in
> piu' di un deposito; un'operazione ne tocca solo una parte.**

Il nome torna libero in un deposito e resta occupato in un altro. Chi prende quel
nome dopo eredita lo stato di chi c'era prima — oppure, quando il nome non e'
riusabile, lo stato semplicemente si accumula per sempre.

Serve **tutti e tre** gli ingredienti. E' la ragione per cui la maggior parte dei
sottosistemi guardati e' immune: quasi tutti tengono il proprio stato *dentro*
la cosa che si cancella, o lo indicizzano con un id opaco.

---

## Riscontro 1 — `memory/WIKI.md` e la finestra fra due passate di Atlas

**Cosa e' stabilito.** Cancellato un progetto, `wikis/_index.md` si aggiorna
subito (lo rigenera il codice: `reindex_wikis`, che la cancellazione chiama), e
`memory/WIKI.md` no — quello lo riscrive Atlas, che gira ogni ~12h (03:57 e
15:57 sul telefono). In mezzo, un file caricato in **ogni prompt** nomina un
progetto che non c'e', col suo puntatore `→ wikis/<slug>/wiki/index.md` morto.
Osservato alle 16:05 del 24/08, dopo la cancellazione delle 16:04.

**Cosa NON e' stabilito, ed e' il punto.** Che Atlas la riga la tolga. La regola
c'e' — `atlas.md:38`, *«remove what disappeared from the wiki»*, piu' *«Keep the
`## Wikis` section complete»* — ma **e' una richiesta in un prompt e nessuno l'ha
mai vista scattare**. E' esattamente la classe di
[`behaviour-harness-plan.md`](./behaviour-harness-plan.md): se smette di valere,
niente diventa rosso.

Un primo controllo dei tempi mi aveva fatto credere che Atlas avesse girato
*senza* riconciliare. Non e' cosi': la passata delle 15:57 e' anteriore alla
cancellazione delle 16:04. La discordanza era mia.

### Quindi: prima si misura, poi semmai si aggiusta

**1a — la sonda** *(mezz'ora, nessun codice)*. Cancellare un progetto, aspettare
la passata di Atlas, guardare se la riga sparisce. Va nel formato di
[`memory-probes.md`](./memory-probes.md), accanto alle altre quattro: e' la
stessa specie di garanzia. Si puo' anticipare la passata invece di aspettare 12h
(v. la nota sull'apparato in quel file).

**1b — la correzione, solo se 1a fallisce** *(mezza giornata)*. Non si tocca il
file: si **calcola in Python** la deriva e la si mette nel prompt.

La sezione `## Wikis` e' membership derivata al 100% — `atlas.md` lo dice:
«ogni wiki dell'inventario ha una riga, sempre», e ne fissa pure il formato
(`→ wikis/<slug>/wiki/index.md` per le wiki, `→ [[Pagina]]` per le entita'). Il
testo della riga invece lo scrive il modello, quindi generare la sezione da
codice non si puo': si genera **la differenza fra due insiemi** e gliela si
consegna.

E' la mossa della fase 4 del lavoro sulla memoria — il blocco dei fatti noti
iniettato nel consolidatore: resta una richiesta, ma diventa una richiesta
facile invece che un compito di osservazione.

**Perche' NON cancellare le righe da codice.** E' l'opzione che suona pulita e
non lo e'. `WIKI.md` e' prosa scritta dal modello: una regex che sbaglia il match
cancella una voce vera, in un file di memoria, che e' il posto esatto in cui
questo progetto si e' gia' bruciato una volta. La lettura invece puo' fallire in
silenzio senza danno — al massimo non elenca una voce. **Leggere fallisce
piano, scrivere fallisce forte**, ed e' tutta la differenza fra le due forme.

- **Impegno:** 1a mezz'ora, 1b mezza giornata con la prova sul telefono.
- **Delicatezza:** 1a nessuna. 1b bassa nella forma scelta, **alta** in quella
  scartata.

### Esito di 1a — 24/08/2026, telefono, `deepseek` — **passa**

La sonda e' [`memory-probes.md`](./memory-probes.md), Probe 5. Girata una volta,
sull'unico modello che questa installazione ha configurato.

Partenza: 9 wiki su disco, 8 righe in `memory/WIKI.md` — `quaderno-x` elencato,
`quaderno-x2` presente e non elencato. Cancellato `quaderno-x` dal file
browser, poi `/atlas` senza `force` (la cancellazione cambia l'impronta, quindi
la passata parte da sola: e' la strada vera). Atlas gira alle 16:51:59, `WIKI.md`
riscritta alle 16:52.

| | prima | dopo |
| --- | --- | --- |
| `quaderno-x`, cancellato | elencato | **tolto** |
| `quaderno-x2`, presente e assente dall'elenco | assente | **aggiunto**, con la sua descrizione |
| le altre sette righe | | invariate parola per parola |

Le due direzioni della regola tengono entrambe, e tiene anche *«update by
difference»*: non ha riscritto quel che era gia' giusto.

**Quindi 1b non si fa.** Resta scritto qui perche' la sonda e' una misura di
oggi, non una garanzia: se un giorno da' l'altro esito, la correzione e' gia'
pensata e non va improvvisata.

**Quel che questa misura NON dice**, ed e' la meta' che il file delle sonde
chiama «la parte che si salta sempre»: **e' un modello solo**. Questa
installazione ha un provider soltanto e nessun preset, quindi non c'e' un
secondo lettore da provare senza aggiungere un provider e una chiave. Finche'
resta cosi', quel che si sa e' che *questo* modello onora la regola — non che la
regola stia nel testo. Il giorno che si cambia modello, questa sonda va rigirata
**prima** di fidarsi.

---

## Riscontro 2 — `cron/runs/` non viene potato da nessuno

**Stabilito.** `remove_job()` (`cron/service.py:1139`) toglie il job da
`jobs.json` e non sfiora `cron/runs/`. In tutto `jenny/cron/` non c'e' **nessuna**
potatura di quella directory: ne' `unlink`, ne' tetto, ne' scadenza. Sul telefono
il job `9cf778dc` ha un record e non esiste piu' fra i job vivi.

**Non e' reincarnazione**: gli id dei job sono opachi (`ca1fbb24`), quindi un job
nuovo non eredita mai. E' una perdita lenta — 24 file dal 12/08, ~2 al giorno,
scritti solo da `bound_runner` per i job dell'utente.

**Il rischio e' quasi nullo e questo e' misurato, non supposto:** quei record
**non li legge niente**, da nessuna parte — ne' il gateway, ne' le route WebUI,
ne' il client, ne' il tool `cron`. Sono solo scritture.

### Correzione

- `remove_job()` toglie anche i `runs/<job_id>_*.json` di quel job. Il prefisso
  combacia sul segmento dell'id e non su una sottostringa: il formato e'
  `<id>_<ms>_<rand>` e gli id sono esadecimali, quindi `f"{job_id}_"` basta.
- Una potatura al `start()`: si tengono i **500 record piu' recenti**, letti dal
  `<ms>` nel nome del file e non dal mtime — deterministico, e testabile senza
  toccare l'orologio (che in questo repo e' una regola, non un gusto).

Un numero solo invece di eta' + tetto: l'eta' non protegge da un job che gira
ogni minuto, il conteggio si'. A ~2 al giorno sono ~8 mesi; a uno ogni 5 minuti,
un giorno e mezzo. In entrambi i casi e' limitato per costruzione.

- **Impegno:** ~30 righe piu' i test.
- **Delicatezza:** bassa.

---

## Riscontro 3 — un preset che nomina un provider cancellato

**Stabilito.** `delete_provider()` (`webui/settings_api.py:1028`) fa meta' del
lavoro giusto: se il provider cancellato era `providers.default`, ripara il
default. Ma `config.model_presets` e' un `dict[str, ModelPresetConfig]` e ogni
preset nomina il suo provider per stringa (`schema.py:757`): quei riferimenti
restano appesi.

**Oggi e' inerte, e va detto:** `preset["provider"]` **non lo legge nessuno a
runtime**. `_apply_preset` cambia modello, finestra e parametri di generazione
sul provider *attivo*, e la sua docstring dice che i processi provider non si
scambiano a caldo. E' una trappola armata per il giorno in cui qualcuno inizia a
onorare quel campo, non un difetto vivo.

### Correzione

Dentro il `_apply(config)` che gia' esiste, quindi nella stessa transazione e
nello stesso funnel di scrittura: i preset che nominano il provider cancellato si
vedono azzerare il campo. **Azzerare e non cancellare il preset**: il preset
resta valido — usera' il provider attivo — e buttare via la configurazione di un
utente perche' una sua riga e' rimasta orfana sarebbe sproporzionato.

- **Impegno:** ~5 righe piu' un test.
- **Delicatezza:** bassa (dentro `store.mutate`, nessuna strada nuova).

---

## Riscontro 4 — `disabled_skills`: si scrive, non si fa

**Stabilito.** `delete_skill()` (`agent/skills.py:273`) fa `rmtree` della
cartella. `config.agents.defaults.disabled_skills` e' una lista di **nomi** e
nessuno la ripulisce. Cancelli una skill disabilitata, ne crei un'altra con lo
stesso nome: nasce disabilitata in silenzio. Reincarnazione vera.

**Ma non e' raggiungibile.** La disabilitazione dalla WebUI finisce nel
frontmatter della skill (`update_skill(..., disabled=...)`), che la cancellazione
porta via. Quella lista in config **dal codice non la scrive nessuno** — ci
arrivi solo a mano o via il tool `self`.

### Non si corregge, e il perche' e' misurato

`_delete` in `skills_routes.py` e' **sincrona**, `skills_api` non tocca la config
da nessuna parte, e il chiamante e' uno solo. Ripulire quella lista vuol dire far
scrivere la config a una route che oggi fa solo filesystem: renderla async, o
farla saltare sul loop. **Cambiare la forma di un layer per un difetto che
nessuno puo' innescare** e' manutenzione che si paga due volte.

Resta scritto qui. Diventa vero il giorno in cui qualcosa comincia a scrivere
quella lista, e quel giorno il posto in cui metterlo si conosce gia'.

---

## Guardati e risultati puliti

Vale elencarli: e' la stessa domanda fatta e risposta, e chi legge dopo non deve
rifarla.

| | perche' e' immune |
| --- | --- |
| **Jenny Apps** (todo compresi) | i dati stanno in `<app>/data/*.jsonl`, **dentro** la cartella |
| **Stato del giardiniere** | `wikis/<nome>/.jenny/gardener.json`, dentro il progetto — controllato apposta come possibile buco del fix dei progetti |
| **Obiettivi sostenuti** | vivono nei metadati della sessione, dentro il file |
| **Subagent** | id opachi, nessun riuso di nome |
| **Allegati / media** | prefisso `uuid4()[:12]`, niente collisioni |
| **Manifest dei segmenti** | `_rebuild_segment_manifest` lo ricostruisce dal disco |
| **Task dell'heartbeat** | identita' = hash del testo, **e** le voci stantie vengono potate (`heartbeat_tasks.py:421`). Il prezzo — cambiare il testo azzera lo storico — sta nella docstring: e' una scelta |

---

## Ordine

1. **3 e 2 insieme** — un'ora, indipendenti da tutto, chiudono due cose per
   sempre. Un commit ciascuno.
2. **1a, la sonda** — mezz'ora, e dice se 1b serve davvero.
3. **1b solo se 1a fallisce.**
4. **4** resta scritto e basta.

Nessuno dei tre tocca il codice del ciclo di vita dei progetti appena scritto, e
nessuno dei tre dipende dagli altri: si possono fare, o non fare, uno alla volta.
