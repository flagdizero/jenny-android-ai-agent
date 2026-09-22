# La gravità esce dal formato, e la segnalazione atterra in chat

*22/09/2026 — branch `feat/la-casa`. Deciso dall'utente dopo aver provato sul
telefono il giro delle segnalazioni costruito lo stesso giorno.*

**Le note sono annullate.** Erano un'idea buona ma un'altra cosa; restano nella
storia di questa discussione e non in un file da mantenere. Qui c'è un solo
lavoro: **la segnalazione senza menù della gravità, che ti porta in chat**.

**Il vincolo dato a voce: nessun codice morto.** La gravità non si "lascia a un
valore fisso": esce dal formato, e con lei esce ogni riga che la scriveva, la
leggeva, la validava o la ordinava — skill compresa.

---

## Perché

Provando sul telefono sono venute fuori due cose.

**1. Il menù delle quattro gravità è il campo sbagliato.** *info / suggest /
warn / error*: nessuno vuole dare un voto alla propria lamentela. È un campo
nato per una coda di smistamento, cioè per una squadra. Qui chi segnala, chi
corregge e chi possiede il quaderno sono la stessa persona.

**2. Il giro finisce in un vicolo cieco.** Confermi, esce un avviso che svanisce,
e il file resta in una cartella che dal telefono non vedi. E **nessuno lo lavora
da solo**: misurato il 22/09 su tutti i lavori periodici del telefono
(giardiniere, Dream, heartbeat, i quattro cron dell'utente), nessuno tocca
`audit/`. Il giardiniere non potrebbe nemmeno: la sua cassetta scrive solo dentro
`wiki/`.

La cura è la stessa per tutti e due: **la segnalazione è una frase che comincia
una conversazione**, non un modulo che finisce in una cartella.

---

## La misura che rende tutto facile

**Non esiste nessun file di audit, da nessuna parte.** Contati il 22/09 sul
telefono, su tutti e quindici i quaderni, sia `audit/` che `audit/resolved/`:
**zero**.

Quindi **non c'è nessuna migrazione da scrivere**. La gravità si può togliere dal
formato senza lasciare dietro un solo file che la contiene. È la ragione per cui
questo piano toglie il campo invece di inchiodarlo a `warn`: inchiodarlo
vorrebbe dire una riga morta in ogni file futuro, più il codice che la valida e
la ordina avendo un valore solo.

E due cose sulla skill, che sembrano ostacoli e non lo sono:

- **`locked: true` non ci riguarda.** Lo legge solo
  `webui/skills_api.py::_visibility_fields`, e vuol dire «nella lista della
  WebUI niente modifica/disabilita/elimina fuori dalla Modalità avanzata». È una
  protezione per l'utente dall'interfaccia, non un vincolo sul sorgente.
- **Una modifica alla skill arriva sul telefono.** Le skill vengono estratte con
  `extract_package_dir("jenny.skills", …)` **senza `skip_existing`**
  (`utils/helpers.py`), quindi si riscrivono a ogni avvio: build, install, e la
  copia nel workspace è quella nuova.

---

## Parte 1 — la gravità esce dal formato

### Il nucleo Python

1. `jenny/webui/audit.py`
   - `_VALID_SEVERITIES` — via.
   - `AuditEntry.severity` — via il campo, e via la sua riga in
     `__post_init__` (`severity must be one of …`).
   - `to_markdown` — via la riga che la scrive.
   - `from_markdown` — via `("severity", "warn")` dalla lettura.
2. `jenny/webui/wiki.py`
   - `create_audit(…, severity: str, …)` — via il parametro e via il passaggio
     a `AuditEntry`.
   - `list_audits` — via `"severity": e.severity` dal dizionario che esce.
3. `jenny/webui/wiki_routes.py::_audit_create`
   - via `severity=query_first(query, "severity") or "warn"`. Il parametro di
     query smette di esistere: non si ignora in silenzio, **non si legge più**.

### La skill

4. `jenny/skills/llm-wiki/scripts/lint_wiki.py`
   - `VALID_SEVERITIES` — via.
   - `"severity"` fuori da `AUDIT_REQUIRED_FIELDS`.
   - via il blocco `if fm["severity"] not in VALID_SEVERITIES`.

   *(Nota utile: `AUDIT_REQUIRED_FIELDS` è un insieme di **chiavi obbligatorie**,
   non un insieme chiuso — il controllo è `REQUIRED - set(fm.keys())`. Quindi un
   eventuale file vecchio con dentro `severity` non verrebbe rifiutato. Non ce
   ne sono, ma vale saperlo: la rimozione non può rompere niente all'indietro.)*

5. `jenny/skills/llm-wiki/scripts/audit_review.py`
   - `SEVERITY_ORDER` — via.
   - la chiave di ordinamento perde il primo termine e resta `created`:
     **l'ordine di lavorazione diventa cronologico**, dalla più vecchia. È il
     prezzo di questo piano, ed è dichiarato: prima le segnalazioni si potevano
     mettere in fila per importanza, adesso si lavorano in ordine di arrivo. Per
     una persona sola con quindici quaderni è la fila giusta; se un giorno non lo
     fosse, il posto in cui cambia è questa riga.
   - via `sev` dalla riga stampata.

6. `jenny/skills/llm-wiki/references/audit-guide.md`
   - via la riga `severity` dalla tabella dei campi e dall'esempio di
     frontmatter.
7. `jenny/skills/llm-wiki/references/article-guide.md` e
   `jenny/skills/llm-wiki/SKILL.md`
   - via `severity` dalle frasi che elencano i campi di un audit.

**`_SKILLS_MANIFEST` in `utils/android_assets.py` non cambia**: nessun file
della skill nasce o muore, cambiano solo dentro. Da ricontrollare lo stesso a
fine giro.

### Cosa **non** si tocca, e perché

- **`author`.** Resta. Non è una domanda che si fa all'utente — il client manda
  `me` e basta — quindi non è attrito. È il campo che distingue una segnalazione
  tua da una che si è aperta lei.
- **`source`, `status`, le tre ancore, `target_lines`.** Sono la macchina, e la
  macchina va bene.
- **La cartella `audit/` e il giro aperto/risolto.** Il file resta, con la sua
  ancora esatta, e il linter continua a vederlo. È quel che rende una
  segnalazione più di una frase detta in chat.

---

## Parte 2 — la segnalazione atterra in chat

### Cosa cade nel client

1. `jenny/templates/ui/assets/casa-audit.js`
   - `SEVERITIES`, `_renderSeverities`, `_pickSeverity`, `this._severity` — via.
   - via `severity` dalla chiamata a `api.createAudit`.
2. `jenny/templates/ui/assets/shared/api-client.js`
   - via `severity` da `createAudit({…})` e dalla query.
3. `jenny/templates/ui/index.html` → via `#casa-audit-sev`.
4. `jenny/templates/ui/assets/casa-style.css` → via `.casa-audit-sev`,
   `.casa-audit-chip`.
5. i18n **it e en** → via `casa.audit.sev.*` (quattro chiavi ciascuna).

### Cosa nasce: l'atterraggio

Alla conferma, tre cose in un gesto solo:

1. nasce il file dell'audit (come adesso);
2. la casa passa alla **chat di quel quaderno**;
3. parte un messaggio con la citazione e le tue parole.

**Il meccanismo esiste già per intero.** Verificato il 22/09:

- `casa-app.js::_send()` legge da `this.input.value`, manda, e **disegna la
  bolla**. Per mandare da codice: si riempie `input.value` e si chiama `_send()`.
- `sessionManager.currentChatId` dal lettore **è già la sessione di quel
  quaderno**: la stanza è sua, non serve cambiarla.
- `_setView('chat')` è il cambio stanza che «Parlane» fa già.

Quindi questo è **«Parlane» che si porta dietro il pezzo di testo**, più il file.
Una giunzione, non una funzione nuova.

### La forma del messaggio

```
In «<titolo pagina>», dove dice «<il pezzo scelto>»:
<quello che hai scritto tu>
(segnalazione <id>)
```

- Il **titolo** perché lei sappia quale pagina senza aprire il file.
- La **citazione** perché la rivedi tu nella tua cronologia.
- **L'id** perché è l'unica cosa che le permette di chiudere la segnalazione
  quando ha finito; senza, corregge e il file resta aperto per sempre. È l'unico
  pezzo di vocabolario del file che passa, e passa tra parentesi.

**Parte da solo, non resta nella casella.** Il file è già nato in quel momento:
lasciarlo lì senza inviare riporterebbe nel vuoto proprio quella segnalazione.
Un gesto, un atto completo.

### Cosa si porta via

- **Niente riga nell'heartbeat.** Il grilletto è l'utente, dal vivo.
- **Niente lista delle segnalazioni aperte.** Ognuna ha la sua conversazione.
- `/api/audit` (l'elenco) e `audit.resolve` **restano senza cliente**. Non si
  tolgono in questo giro: `audit.resolve` è documentato in
  `docs/reference/websocket.md`, che è una superficie pubblica, e toglierlo è una
  decisione a sé. **Va deciso, non dimenticato** — v. in fondo.

---

## Parte 3 — i documenti pubblici

`docs/using/wiki.md` (112 righe) **descrive un'interfaccia che non esiste più**:
il grafo di tutte le wiki, l'albero delle cartelle, il cassetto «Audits» con le
due linguette, il bottone «Add audit», il menù delle quattro gravità, le
schermate vuote della home. Tutta roba uscita il 21/09 con
`0116b1f` («the wiki leaves the workshop»).

È il pezzo morto più grosso rimasto, **ed è quello con un lettore vero**: il
sito (`flagdizero/jenny-site`) genera le sue pagine `/docs/**` da questi file.

- Si **riscrive il contenuto**, sul posto.
- **Non si sposta e non si rinomina il file**: il percorso è un URL pubblico e la
  posizione nella barra laterale (v. `AGENTS.md`).
- La sezione «Feedback and audits» diventa il giro nuovo: selezioni, dici cosa
  non va, atterri in chat. Via il menù delle gravità, via il cassetto.

---

## Ordine, e come si verifica

Un pezzo = un commit, `ruff check jenny/ tests/` + `python3 -m pytest -q` verdi a
ogni passo, e **ogni banco nuovo o rimodellato provato rosso con una mutazione**
prima di dirlo fatto. Fra la mutazione e la misura buona si pulisce
`__pycache__`: il bytecode in cache ha già mentito una volta (22/09, passata
intera rossa su sorgente sano).

1. **Parte 1** (il formato + la skill) — è il taglio, e non dipende da niente.
2. **Parte 2** (il client + l'atterraggio).
3. **Parte 3** (i documenti) — per ultima, così descrive quel che c'è davvero.

### I banchi che vanno rimodellati, non cancellati

- `tests/webui/test_casa_audit_client.py`
  - `test_the_severities_are_the_four_the_format_allows` e
    `test_every_severity_has_a_word_in_both_languages`: quel che misuravano —
    che il client e il linter fossero d'accordo su un insieme chiuso — sparisce
    col campo. **Al loro posto serve la misura opposta**: che nessuno scriva più
    una gravità, cioè che il file creato non contenga quella chiave.
- `tests/webui/test_webui_http_routes.py` e `tests/skills/llm_wiki/test_scripts.py`
  - fixture con `severity:` nel frontmatter: vanno tolte dai file di prova, e un
    banco deve dire che il linter **non** si lamenta di un audit senza gravità.
- `tests/webui/test_wiki_multi.py`, `tests/webui/test_commands.py`
  - sette chiamate con `severity="warn"`: cadono col parametro.

### La prova sul telefono

Build release da worktree pulito, firmata, installata, e **controllare che il JS
nuovo sia davvero sul telefono** leggendo `files/workspace/ui/assets/` — e
stavolta anche `files/workspace/skills/llm-wiki/`, che è la metà che di solito
non si guarda.

- seleziono, «qui è sbagliato», scrivo, confermo: **atterro in chat** col
  messaggio già partito;
- il file esiste in `wikis/<q>/audit/` e **non contiene `severity`**;
- il linter della skill lo accetta («audit/ shape OK»);
- le chiedo di lavorarla: corregge e sposta il file in `audit/resolved/`;
- seleziono un testo che compare due volte: lo dice invece di ancorare a caso;
- dieci tocchi normali su una pagina non fanno comparire niente.

*(Aspettare 2-3 s prima di fotografare: il 22/09 ho letto «il dialogo non
compare» da uno scatto preso mentre la richiesta era ancora in volo.)*

### La lista «niente merda»

- [ ] `severity`, `SEVERITIES`, `VALID_SEVERITIES`, `SEVERITY_ORDER`,
      `casa.audit.sev`, `.casa-audit-chip`: **zero occorrenze** su `jenny/`,
      `tests/`, `docs/`, `android/app/src`.
- [ ] Nessuna rotta senza cliente. `/api/audit` e `audit.resolve` restano
      **di proposito** (v. domanda aperta); `/api/tree` e `/api/config` sono già
      in lista per essere tolte.
- [ ] Nessun export JS senza importatori, nessun metodo senza chiamanti — e
      **il consumatore può essere un banco**: la passata del 22/09 ha segnalato
      `tokenize`, che è vivo e lo tiene in piedi un test.
- [ ] `_SKILLS_MANIFEST` e i file della skill coincidono.
- [ ] Manifesto `android_assets.py` e asset su disco coincidono.
- [ ] Chiavi i18n pari fra `it.json` e `en.json`, nessuna orfana.
- [ ] `docs/using/wiki.md` non nomina più niente che non esista.

---

## La domanda aperta

**`/api/audit` e `audit.resolve` restano senza cliente.** Sono la metà «leggi e
chiudi» del giro, e con l'atterraggio in chat non servono più all'interfaccia:
ogni segnalazione ha la sua conversazione.

Ma `audit.resolve` è documentato in `docs/reference/websocket.md`, cioè su una
superficie pubblica, e Jenny oggi il file lo sposta a mano con i suoi strumenti
— quindi il comando non serve nemmeno a lei. **Togliere tutti e due è la scelta
coerente con «niente codice morto»**, e va presa esplicitamente perché tocca una
pagina pubblica. Insieme a `/api/tree` e `/api/config`, che sono già in attesa
della stessa decisione.
