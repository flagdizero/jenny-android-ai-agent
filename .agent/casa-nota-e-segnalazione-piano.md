# Due verbi sopra una frase: «Nota» e «Qui è sbagliato»

*22/09/2026 — branch `feat/la-casa`. Deciso dall'utente dopo aver provato sul
telefono il giro delle segnalazioni costruito lo stesso giorno.*

**Il vincolo dato a voce: niente moduli.** L'utente non deve incontrare il
vocabolario del file — gravità, stato, coda, id. Quella roba esiste ed è giusta,
ma è di Jenny.

---

## Da dove si parte, e cosa non va

Il giro delle segnalazioni funziona ed è provato sul telefono (v.
`.agent/casa-quaderno-piano.md`). Ma provandolo sono venute fuori due cose:

**1. Finisce in un vicolo cieco.** Confermi, esce un avviso che svanisce, e il
file resta in una cartella che dal telefono non vedi. Nessuno lo lavora da solo:
il giardiniere batte ogni mezz'ora ma fa un altro mestiere — trasforma il diario
in pagine — e **non può nemmeno scrivere** in `audit/`, che sta fuori dalla sua
cassetta apposta. Misurato il 22/09 su tutti i lavori periodici del telefono
(giardiniere, Dream, heartbeat, i quattro cron dell'utente): **nessuno tocca le
segnalazioni.** L'unico modo è chiederglielo in chat.

**2. Il menù della gravità è il campo sbagliato.** *nota / idea / attenzione /
errore*: nessuno vuole dare un voto alla propria lamentela. È un campo che
esiste per una coda di smistamento, cioè per una squadra.

Il secondo punto ha aperto la domanda vera, ed è quella da cui esce questo
piano: **quel menù non era una gravità, erano due verbi diversi schiacciati in
uno.**

| | dove va | quando succede | quando finisce |
|---|---|---|---|
| **Nota** | resta sulla pagina | mai, sta lì | mai |
| **Qui è sbagliato** | parte una chat | adesso | quando è corretta |

Scegliere fra questi due non è dare un voto: è dire cosa vuoi che succeda, e lo
sai già prima di aver selezionato il testo. Due bottoni sulla stessa barra
raccolgono quella scelta invece di chiedertela.

---

## Parte 1 — «Qui è sbagliato» diventa lineare

**La macchina resta com'è.** Il file dell'audit nasce lo stesso, con la sua
ancora esatta, e il linter continua a vederlo. Cambiano solo le due estremità:
niente modulo davanti, niente vuoto dietro.

### Cosa cade

1. `casa-audit.js` → il menù delle quattro gravità (`SEVERITIES`, `_renderSeverities`,
   `_pickSeverity`, `this._severity`) e i chip nel foglio. Il campo `severity`
   del file **resta**: parte a `warn`, che è già il valore di partenza della
   rotta. È una cosa che il file ha, non una domanda che si fa.
2. I chip nel markup (`#casa-audit-sev`) e i loro stili (`.casa-audit-sev`,
   `.casa-audit-chip`).
3. Le chiavi `casa.audit.sev.*` in **it e en**, e il banco che le tiene pari
   (`test_casa_audit_client.py::test_every_severity_has_a_word_in_both_languages`).
   Il banco che confronta `SEVERITIES` col linter della skill **si riscrive, non
   si cancella**: quel che misurava — che il valore scritto nel file sia uno di
   quelli che il linter accetta — resta vero e va misurato sul valore di
   partenza.

### Cosa nasce: l'atterraggio

Alla conferma, tre cose in un gesto solo:

1. nasce il file dell'audit (come adesso);
2. la casa passa alla **chat di quel quaderno**;
3. parte un messaggio che porta la citazione e le tue parole.

**Il meccanismo esiste già, per intero.** Verificato il 22/09:

- `casa-app.js::_send()` legge da `this.input.value`, manda, e **disegna la
  bolla**. Per mandare da codice: si riempie `input.value` e si chiama `_send()`.
  Niente percorso nuovo, niente eco da aspettare.
- `sessionManager.currentChatId` dal lettore **è già la sessione di quel
  quaderno**: ci sei dentro, la stanza è sua.
- `_setView('chat')` è il cambio stanza che «Parlane» fa già.

Quindi «Qui è sbagliato» è **«Parlane» che si porta dietro il pezzo di testo**,
più il file. Non è una funzione nuova, è una giunzione.

### La forma del messaggio

Tre cose, e ognuna serve a qualcosa di preciso:

```
In «<titolo pagina>», dove dice «<il pezzo scelto>»:
<quello che hai scritto tu>
(segnalazione <id>)
```

- Il **titolo** perché lei deve sapere quale pagina senza aprire il file.
- La **citazione** perché la rivedi tu nella tua cronologia, e perché è il modo
  in cui un umano dice «questa frase qui».
- **L'id** perché è l'unica cosa che le permette di chiudere la segnalazione
  quando ha finito — altrimenti corregge e il file resta aperto per sempre.
  È l'unico pezzo di vocabolario del file che passa, e passa tra parentesi.

**Parte da solo, non resta nella casella.** Il file è già nato in quel momento:
lasciarlo lì senza inviare ti riporterebbe nel vuoto proprio per quella
segnalazione. Un gesto, un atto completo.

### Cosa si porta via

- **Niente riga nell'heartbeat.** Il grilletto sei tu, dal vivo.
- **Niente lista delle segnalazioni aperte.** Ognuna ha la sua conversazione: per
  sapere che fine ha fatto, scorri indietro.
- Quindi `/api/audit` (l'elenco) resta senza cliente. **Non si toglie**: serve
  alla Parte 2 e alla domanda aperta in fondo. `audit.resolve` idem — lei oggi
  sposta il file a mano, ma quel comando è documentato in
  `docs/reference/websocket.md`, che è una seconda copia pubblica.

---

## Parte 2 — «Nota»

**Cos'è.** Un appunto tuo su un pezzo di pagina. Non è lavoro di nessuno, non si
chiude mai, non ha un destinatario. *«verificare quando arriva»*, *«questa non mi
torna»*, *«qui manca il pezzo sul costo»*.

**Perché si merita di esistere, e non è «modifica la pagina».** Quelle pagine le
scrive lei. Una nota è **l'unica cosa su quella pagina che è tua** — e sta
**fuori dal testo**: se scrivi l'appunto dentro la pagina modificandola, la
passata successiva se lo può mangiare, riformulare, o promuoverlo nella voce
della wiki. Un margine no: è un altro strato.

### Dove vivono

`wikis/<quaderno>/notes/<id>.md`, gemello di `audit/`, un file per nota.

**Dentro il quaderno e non in `.jenny/`**, e la differenza col precedente degli
spilli della mappa va detta perché sembra lo stesso caso e non lo è: uno spillo
è una **posizione su uno schermo**, non ha senso fuori dal telefono che l'ha
disegnato. Una nota è **contenuto**. Deve viaggiare col quaderno — nel backup,
nella cancellazione di un progetto, e sotto gli occhi di Jenny, che è il
requisito che l'utente ha dato esplicitamente.

**Verificato che si può:** `lint_wiki.py` non ha nessun controllo sulle cartelle
inattese nella radice di una wiki (controlla la forma di `audit/`, i nomi del
diario, le operazioni del log — niente altro). Una `notes/` non fa scattare
niente.

### Cosa c'è dentro

Frontmatter come un audit, meno quel che non ha senso:

```yaml
id: 20260922-143012-a1b2
target: concepts/qualcosa.md
target_lines: [14, 14]
anchor_before: "…"
anchor_text: "il pezzo scelto"
anchor_after: "…"
created: '2026-09-22T14:30:12'
```

**Niente `severity`, niente `status`, niente `author`**: una nota non ha
gravità, non si risolve, e l'ha scritta l'unica persona che c'è.

**Le ancore le calcola `compute_anchor`** (`jenny/webui/audit.py`), la stessa
degli audit: prende il testo del file e i due offset, e torna righe +
prima/testo/dopo. È già provata, e dà alla nota la stessa robustezza — se la
pagina cambia sotto, l'ancora dice ancora abbastanza per ritrovare il punto.

### La pillina

Alla **fine del pezzo selezionato**, dentro il testo reso, una pastiglia piccola
che dice *nota*. La tocchi e si apre un foglietto col testo.

**È la metà cara, e va fatta degradando.** Per disegnarla devi ritrovare il
pezzo scelto *dentro la pagina già resa* — cioè il problema dell'ancora al
contrario. Su una frase con un grassetto dentro non lo ritrovi, perché il reso
dice `molto importante` dove il sorgente dice `**molto** importante`.

La regola è quella che il giro delle segnalazioni usa già: **si disegna solo
quando il pezzo si ritrova una volta sola.** Quando non si ritrova, o si ritrova
due volte, **la nota esiste lo stesso** e la vedi nell'elenco della pagina. Non
si rompe niente, si perde solo la pastiglia.

### Il foglietto

Il testo della nota, e due comandi:

- **Parlane** → chat del quaderno, con lo stesso messaggio della Parte 1 ma con
  un altro verbo davanti: *«Parliamo di questa cosa: in «X», dove dice «Y» — ho
  scritto: Z»*. Stesso meccanismo, nessun codice in più.
- **Cancella** → la nota è tua, si butta. **È la differenza con una
  segnalazione**, che invece non si cancella mai nemmeno quando è rifiutata (v.
  `references/audit-guide.md`): quella è un atto verso qualcun altro, questa no.
  Per ritirare una segnalazione glielo dici in chat, dove sei già.

### Come ci arriva Jenny

Due livelli, e **solo il primo è gratis**:

1. **Quando gliene parli** — «Parlane», o qualunque discorso su quella pagina:
   apre il file e lo legge. Funziona da subito, non serve niente.
2. **Quando lavora da sola su quella pagina** — deve sapere che `notes/` esiste.
   La skill `llm-wiki` è `locked: true`, quindi non si tocca. Il posto giusto è
   l'**`AGENTS.md` del quaderno** (`WIKI_SCHEMA_FILENAME`), che è il file delle
   premesse che l'utente possiede e che lei legge già. Una riga: *«`notes/`
   contiene appunti dell'utente ancorati a un punto: leggili come contesto
   quando lavori su quella pagina, non come compiti da eseguire.»*

**Il «non come compiti» è la parte che conta.** Senza quella frase hai
reinventato l'audit, con la differenza che questo non si chiude mai — quindi lei
ci ritorna sopra a ogni passata.

### Le rotte

- **Elenco**: `GET /api/notes?wiki=&target=` — parametri corti, è una lettura,
  sta su `/api/` come `/api/audit`.
- **Creare e cancellare**: comandi RPC `note.create` / `note.delete` in
  `webui/commands.py`. La creazione **porta testo libero**, e quella superficie
  il testo non lo trasporta: è servita dall'hook di handshake di `websockets`,
  che non legge mai il body (8192 byte per riga, solo ISO-8859-1). È la stessa
  lezione di `page.write`. La cancellazione ci sta accanto perché le due mezze
  operazioni della stessa cosa in due posti diversi sono il modo di farle
  divergere.

---

## Cosa questo piano **non** fa

- **Non** mette una lista delle segnalazioni aperte. Ognuna ha la sua chat.
- **Non** mette una riga nell'heartbeat. Il grilletto è l'utente.
- **Non** insegna gli audit al giardiniere: gli sfonderebbe la regola sulla
  superficie di scrittura, che è lì per una ragione buona («una porta nella
  cassetta è una via d'uscita»).
- **Non** tocca la skill `llm-wiki`, che è `locked`.
- **Non** fa partire niente al salvataggio di una modifica. Lì l'LLM che
  "migliora" quel che hai appena scritto a mano è il contrario del motivo per cui
  l'hai scritto. Quel che servirebbe è **un appunto**, non una riscrittura — e
  non a ogni salvataggio. Resta aperta, v. sotto.

---

## Ordine, e come si verifica

Un pezzo = un commit, `ruff check jenny/ tests/` + `python3 -m pytest -q` verdi
a ogni passo, e **ogni banco nuovo provato rosso con una mutazione** prima di
dirlo fatto. Fra una mutazione e la misura buona si pulisce `__pycache__`: il
bytecode in cache ha già mentito una volta.

1. **Parte 1** — è quasi tutto codice che si **toglie**, e non ha domande
   aperte: si fa per prima.
2. **Parte 2a** — il deposito: rotta, comandi, file su disco, elenco in fondo
   alla pagina. Funziona senza la pastiglia.
3. **Parte 2b** — la pastiglia inline, con la regola dell'unicità e il degrado.
   Se si rivela fragile sul telefono, si ferma qui e resta l'elenco: **2a vale
   da sola**, ed è il motivo per cui è divisa così.

Alla fine, sul telefono (build release da worktree pulito, firmata, installata,
e **controllare che il JS nuovo sia davvero sul telefono** leggendo
`files/workspace/ui/assets/`, non il repo):

- seleziono, «qui è sbagliato», scrivo, confermo: **atterro in chat** col
  messaggio già partito, e il file esiste in `audit/`;
- lei risponde e chiude: il file si sposta in `audit/resolved/`;
- seleziono, «nota», scrivo: la pastiglia compare **alla fine di quel pezzo**;
- riapro la pagina: la pastiglia è ancora lì;
- la tocco: si apre il foglietto; «Parlane» mi porta in chat col contesto;
- seleziono un pezzo con un grassetto dentro e ci metto una nota: **niente
  pastiglia, ma la nota c'è** nell'elenco della pagina;
- cancello una nota: sparisce da entrambi i posti;
- dieci tocchi normali su una pagina non fanno comparire niente.

### La lista «niente merda»

- [ ] `SEVERITIES`, `casa.audit.sev.*`, `.casa-audit-chip`: zero occorrenze.
- [ ] Nessuna rotta senza un cliente. `/api/audit` e `audit.resolve` restano
      **di proposito** (v. domanda aperta 1); `/api/tree` e `/api/config` sono
      già in lista per essere tolte.
- [ ] Nessun export JS senza importatori, nessun metodo senza chiamanti — e
      **il consumatore può essere un banco**: la stessa passata del 22/09 ha
      segnalato `tokenize`, che è vivo e lo tiene in piedi un test.
- [ ] Manifesto `android_assets.py` e file su disco coincidono.
- [ ] Chiavi i18n pari fra `it.json` e `en.json`, nessuna orfana.

---

## Le domande aperte, che decide l'utente

1. **Jenny legge le note da sola?** Il livello 1 (gliene parli, le legge) è
   gratis. Il livello 2 (se ne accorge da sola lavorando sulla pagina) costa una
   riga nell'`AGENTS.md` di **ogni** quaderno. Da fare a mano, o da mettere nel
   modello con cui nascono i quaderni nuovi — e allora i quindici che esistono
   già restano indietro finché non li si tocca.
2. **Dopo una modifica a mano, va lasciato un appunto?** La riga che hai
   corretto viene da una fonte che dice ancora la versione vecchia: alla passata
   successiva può tornare indietro. Un appunto lo eviterebbe. Ma **non a ogni
   salvataggio**, e resta da capire dove va — una nota automatica in `notes/`
   sarebbe un margine che non hai scritto tu, e sporcherebbe la cosa che rende
   le note tue.
3. **Quanto dura la prova.** Due settimane con tutti e due i bottoni, e poi la
   domanda è una sola: quale dei due hai usato. Se «qui è sbagliato» non l'hai
   toccato mai perché apri la matita e sistemi, esce — e resta la nota.
