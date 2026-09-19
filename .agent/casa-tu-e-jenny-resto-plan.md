# Tu e Jenny — il resto della pagina

Il primo giro ha costruito la stanza e tre righe. Le altre cinque le ho tolte
io, con un elenco di ragioni mie ([`casa-tu-e-jenny-plan.md`](casa-tu-e-jenny-plan.md),
tabella «Cosa entra in questo giro, cosa no»). **Quelle ragioni non reggono.**
Una era perfino paternalistica — «una chiave API si incolla in officina» — e la
verità è che in casa l'utente ha diritto a scegliere chi risponde, esattamente
come sceglie il tema.

Questo piano è il resto della tavola `TuEJenny.dc.html`. Per ciascuna riga:
cosa disegna la tavola, cosa esiste già sotto, e cosa manca davvero.

---

## 0. Il difetto, già corretto

Jenny finita **dietro la chat**: `casa-block` era già la bolla di un messaggio
(`casa-chat.js`), e chiamando così le schede della pagina le loro regole —
sfondo, bordo, e lo `z-index` che serviva a coprirla — sono atterrate su ogni
riga della conversazione. Le schede sono `casa-card`, e un banco incrocia i due
insiemi di classi: un nome che vuol dire due cose lo dice prima del telefono.

---

## 1. Officina: un bottone, e di un altro colore — **fatto**

**La tavola.** Una scheda **scura su pagina chiara**, con l'icona d'accento e
il sottotitolo muto. È l'unica cosa invertita della pagina, e lo è perché di là
si va a fare un altro mestiere.

**Cos'è adesso.** Una scheda come le altre. L'ho appiattita io, e per una
ragione reale ma risolta male: era `--overlay`, semi-trasparente, e Jenny si
vedeva attraverso. L'ho resa `--surface` — opaca ma identica alle altre.

**Cosa fare.** Invertirla davvero: fondo `--text`, testo `--bg`, icona
`--accent`. Sui sette temi l'inversione è per costruzione — dove la pagina è
chiara la scheda è scura, e viceversa — ma **va guardata su tutti e sette**
prima di dire che funziona: «Jenny Fumetto» e «Y2K» hanno fondi molto chiari e
testi molto scuri, e lì l'inversione è la più violenta.

**Com'è andata.** Invertita: fondo `--text`, inchiostro `--bg`, opaca. Il
pezzo che non era minuti è l'icona. `--accent` su un fondo `--text` è
**invisibile in Chanel — che è il tema di partenza — e in Fumetto**, perché in
tutti e due l'accento *è* il testo. Misurato sul rig con `--accent` su tutti e
sette:

    chanel 1,00 · fumetto 1,00   l'accento è il fondo della scheda
    sticker 2,41 · pietra 2,69   sotto la soglia di 3:1 per un oggetto grafico
    synthwave 3,06 · kyoto 3,68 · y2k 4,58

Da cui `--accent-on-text`: i tre che passano tengono la tinta, gli altri
quattro prendono `--bg`, che sta sopra 10:1 per costruzione. Un tema nuovo
eredita il default sicuro. Un banco rifà quel conto su ogni blocco di tema,
cascata compresa.

---

## 2. Chi risponde — **fatto, versione (a)**

**La tavola** (`Modello.dc.html`) disegna tre cose: una fila di **mattonelle
di marca** (OpenCode Go, OpenAI, Anthropic, DeepSeek, Groq, OpenRouter,
Ollama), una riga **«Chiave di …» — «salvata ••••» — «Cambia»**, e l'elenco dei
**modelli** con una parola ciascuno («veloce, economico», «ragiona a lungo»).

**Cosa esiste già.** Tutto il retro, e più di quanto sembri:

| Pezzo | Dove |
|---|---|
| l'elenco dei provider configurati, con `api_key_hint` mascherato | `/api/settings` → `providers[]` |
| i modelli di un provider, **chiesti al provider** | `/api/settings/provider-models?provider=…` |
| cambiare provider o chiave | `/api/settings/provider/update` |
| cambiare modello | `/api/settings/update` |
| colore e nome di marca per ~24 provider | `shared/provider-brand.js` |
| se il cambio richiede un riavvio | `requires_restart` nel payload |

**Cosa manca, e qui c'è una scelta vera.** Le mattonelle della tavola non sono
i provider *configurati*: sono **marche fra cui scegliere**, e scegliere una
marca vuol dire conoscerne l'endpoint. Quel meccanismo ha già un piano suo —
[`provider-presets-plan.md`](provider-presets-plan.md), «l'utente sceglie un
nome da un elenco, incolla una chiave, e non viene mai a sapere che sotto ci
sono tre voci» — ed è **proposto, non implementato**. Per OpenCode Go servono
tre campi che Settings non mostra affatto.

Quindi due strade, e propongo di farle in quest'ordine:

* **(a) Subito, senza dipendenze.** La riga «Chi risponde» apre una stanza che
  mostra i provider **già configurati** (mattonelle con colore e nome di
  marca), quello attivo acceso, e sotto l'elenco dei modelli che quel provider
  dichiara. Un tocco cambia modello, un tocco su un'altra mattonella cambia
  provider. La chiave si vede mascherata e si può **sostituire** — il campo c'è
  e lo riempi tu, non io: non digito chiavi al posto tuo, ma è un limite mio,
  non del prodotto.
* **(b) Dopo, col piano dei preset.** Aggiungere una marca *nuova* da casa,
  senza sapere cos'è un endpoint. È quello che la tavola disegna per intero, e
  costa il piano dei preset.

Due cose da decidere mentre si fa (a):

1. **Le parole accanto ai modelli non esistono.** «veloce, economico» non viene
   da nessuna API: i provider restituiscono id e basta. O si mostra solo l'id,
   oppure quelle frasi le scriviamo noi per i modelli che conosciamo — e allora
   invecchiano. Proposta: id, e sotto il nome del provider.
2. **`requires_restart`.** Alcuni cambi valgono dal turno dopo, altri no. La
   stanza deve dirlo con una riga, non lasciarlo indovinare.

**Com'è andata.** La stanza c'è: `casa-model.js`, 18 banchi in node, 13
mutazioni rosse. Le decisioni prese strada facendo:

* **Toccare una mattonella non cambia chi risponde**, mostra i suoi modelli.
  Il cambio è il tocco su un modello, e salva `model` e `default_provider`
  **insieme** — è il punto del redesign dell'officina, e vale anche qui: fra
  due chiamate separate esisterebbe davvero una config con un modello che il
  provider attivo non conosce.
* **Nessuna parola accanto ai modelli**, come previsto: c'è l'id, e il titolo
  dice di chi è l'elenco.
* **Il modello in uso sta in cima anche se il provider non lo elenca** — un id
  battuto a mano in officina, o un elenco che non è arrivato.
* **Due provider della stessa marca** (`opencode_go` e `opencode_zen` sono
  entrambi «OpenCode») tengono i loro nomi configurati: due pastiglie identiche
  di cui una accesa sono peggio di due nomi tecnici.

**Tre difetti li ha trovati il rig, non i banchi** — ed è la ragione per cui il
rig si guarda:

1. la riga «Chiave» si vedeva **senza nessuna marca da guardare**: `[hidden]`
   è a specificità zero e `display: flex` lo scavalca. La casa l'aveva già
   pagata due volte (`.casa-back`, `.casa-version`); adesso c'è un banco che
   cerca il caso da solo, in tutte e tre le stanze;
2. la scheda diceva **«Modelli di OpenCode» sopra i modelli di Anthropic**: il
   titolo leggeva chi *risponde* invece di chi stai *guardando*;
3. nei temi chiari `--overlay` e `--overlay-strong` sono quasi lo stesso
   bianco, e **non si distingueva quale elenco stessi leggendo**. Adesso la
   pastiglia guardata è piena e quella che risponde porta un segno di spunta —
   l'anello d'accento da solo sparisce dove l'accento è il testo.

Resta **(b)**, aggiungere una marca nuova da casa: costa il piano dei preset.

---

## 3. Aggiornamenti

**La tavola.** Una riga col pallino verde, «v0.11.0 · aggiornata», e un chevron.

**Cosa esiste.** Il giro intero, in officina: `/api/updates/check`,
`/api/updates/install`, `/api/updates/status`, più ~200 righe di controller con
le fasi (idle → scarico → prompt del gestore pacchetti → riavvio), il polling,
e i due casi che ingannano — la connessione che cade *perché* l'app si sta
riavviando, e il rifiuto «niente da installare» che non deve sporcare la fase.

**Cosa fare.** Non riscriverlo: **estrarlo**, come è già successo per il
backup. `shared/update-flow.js` con le fasi e il polling; l'officina e la casa
ne diventano due viste. È il pezzo di lavoro più onesto dei cinque, perché
l'alternativa è una seconda copia di una macchina a stati che sbaglia in
silenzio.

Peso: medio-grande, quasi tutto nell'estrazione.

---

## 4. Backup

**La tavola.** Riga «Backup — ieri, 23:10».

**Cosa esiste.** `shared/backup-flow.js` — già condiviso fra Impostazioni e
onboarding — con esportazione cifrata (passphrase), importazione, e il riavvio
dell'app. Il lato nativo espone i due picker SAF.

**Cosa manca: proprio «ieri, 23:10».** Non c'è **nessun record dell'ultimo
backup**, in nessun file. Cercato: niente `last_backup` da nessuna parte. Serve
scriverlo quando un'esportazione va a buon fine (un campo in `config.snapshot`,
scritto dal funnel `store.mutate`).

Da decidere: la riga mostra l'ultimo **backup esportato** (che è quello che la
parola dice) oppure l'ultimo **snapshot locale** (che esiste già, è automatico,
ed è quello che ti salva davvero se cancelli una cosa per sbaglio)? Propongo il
primo come valore della riga e il secondo dentro la stanza, in una frase.

Peso: medio.

---

## 5. Jenny esiste grazie a [n] sostenitori

**La tavola** (`Grazie.dc.html`). La riga in fondo, e una pagina con i nomi in
corpo variabile — «i nomi più grandi sono con lei da più tempo» — un bottone
«Sostieni Jenny», e una frase che è anche la specifica tecnica: *«l'elenco si
aggiorna da solo a ogni versione»*.

**Cosa esiste.** Il manifest delle release (`latest.json`, pubblicato da
`scripts/release.py`), che l'updater scarica ogni 24 ore e **cache** in
`update_state.json`. È esattamente il canale che «si aggiorna a ogni versione».

**Cosa manca, e non è codice.** I nomi. Servono tre decisioni tue:

1. **Da dove vengono.** Patreon non si interroga da qui (servirebbe un token
   nell'app, che non ci va). La via pulita: `scripts/release.py` mette nel
   manifest un elenco che **tu** gli dai — un file nel repo, aggiornato quando
   pubblichi.
2. **Cosa si pubblica di una persona.** Un nome scelto da lei, e nient'altro.
   Il corpo variabile chiede anche «da quanto» — una data di inizio, o solo un
   ordine.
3. **Dove porta «Sostieni Jenny».** Un link esterno apre Chrome dal guscio
   nativo: è già quel che fa il lettore per i link esterni.

Finché quei nomi non esistono, la riga **non si mette**: «grazie a 0
sostenitori» è peggio di niente. Il codice è il pezzo più piccolo dei cinque; è
la fonte che va decisa.

Peso: piccolo il codice, tua la decisione.

---

## Ordine, e a che punto è

1. ~~**Officina invertita**~~ — fatta.
2. ~~**Chi risponde (a)**~~ — fatta.
3. **Aggiornamenti** — con l'estrazione del flusso condiviso.
4. **Backup** — col record dell'ultimo export.
5. **Sostenitori** — quando l'utente dice da dove vengono i nomi.

Le prime quattro non hanno bisogno di niente da lui per partire. La quinta sì.
