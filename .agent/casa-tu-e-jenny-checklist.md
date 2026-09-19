# Tu e Jenny — cosa è atterrato

Piano: [`casa-tu-e-jenny-plan.md`](casa-tu-e-jenny-plan.md). Quattro atterraggi
nell'ordine previsto, e il perimetro è quello che il piano aveva deciso — con
una correzione sul pezzo che regge, presa da una misura e non da un'opinione.

## La casa adesso ha cinque stanze

La conversazione, le pagine di un quaderno, una pagina, «Tu e Jenny», lei.
Quale sia a schermo lo dice sempre **un attributo solo** su `.casa-shell`.

Quel che è cambiato è la catena del ritorno: tre `if` in fila sono diventati
una tabella (`BACK_TO`), perché con quattro stanze l'occhiello mentiva — diceva
«torna alla chat» sopra il lettore, che torna alle pagine. Adesso la frase e il
salto vengono dalla stessa tabella e non possono divergere, e un banco di
contratto fallisce se il CSS sa accendere una stanza da cui `BACK_TO` non sa
uscire: quello è un difetto silenzioso alla scrivania e rumoroso in mano —
Indietro ricade sul ramo «esci dal quaderno» e cambia conversazione.

**L'avatar al posto della chiave inglese.** In testa c'era una chiave che
portava dritto in officina; la tavola ci disegna un avatar, e l'officina sta in
fondo a «Tu e Jenny». Per chi in officina ci va dieci volte al giorno resta la
scorciatoia che la tavola scrive sulla scheda: tenere premuto l'avatar.

## La lingua non c'è, ed è una decisione misurata

Prima di toglierla ho guardato il telefono: lingua di sistema `en-IT`, nessuna
lingua per-app, `locale` nel `localStorage` a `en`. **L'interfaccia era già in
inglese** — «PERSONAL CONVERSATION», «Write to Jenny» — quindi togliere la riga
non cambia niente lì.

La ragione vera però è un'altra: quella riga cambia le scritte dei bottoni, non
la lingua in cui Jenny risponde. Quella viene da `SOUL.md`, da `USER.md` e dal
modello; `config.agents.defaults.language` la scrive l'onboarding una volta
sola e colora tre messaggi del backend. La sceglie il sistema, che è ciò che
`i18n.detectLocale()` fa già quando nessuno ha scelto.

Resta aperto, e vale la pena: `android:localeConfig` nel manifest farebbe
comparire la lingua per-app nelle impostazioni di sistema, che è il posto dove
Android quella scelta ce l'ha già. Da verificare prima di prometterlo — se la
WebView la legga davvero o resti su quella del processo fino al riavvio.

## «Mascotte» si chiama «Jenny», e non è una sfumatura

Il sottomenu chiesto c'è: mostrala, taglia, flottante. Ma il nome della porta è
«Jenny», perché lì dentro c'è anche `SOUL.md`, e quello non è come appare —
decide come parla in chat, nel fumetto, nella tendina e su Telegram. Sotto
un'etichetta che dice «mascotte» quel testo sembrerebbe una preferenza di
disegno.

`enabled` e `active` non sono la stessa cosa, ed è l'unica parte delicata:
a permesso negato la config resta accesa e il payload lo dice, quindi
l'interruttore **resta acceso e spiega** invece di rimbalzare. Si muove anche
prima che il server risponda — il giro passa da `store.mutate` e da un ponte
verso Kotlin — e poi prende per buona la risposta.

Un payload solo per due stanze: `/api/settings` porta provider, contatori e
lavoratori periodici, e di quel peso le due stanze leggono un campo per uno. Il
fallimento **non** si ricorda: una versione che manca è una riga vuota, ma una
riga della flottante nascosta fino al riavvio è un'impostazione sparita.

## Il pezzo che regge: le regole che le hai dato tu

Il piano proponeva un blocco dentro `SOUL.md` difeso da un guardiano su
`MemoryStore`. Due misure hanno spostato il disegno, e sono la parte di questo
giro che vale la pena rileggere.

**Prima misura — quanto vive quel che scrivi.** Gli snapshot del dispositivo di
prova, che il runtime scatta prima di ogni passata: 58 snapshot in 6,6 giorni,
sette versioni distinte di `SOUL.md`, **sei riscritture**, una ogni 1,1 giorni.
Una casella che salvasse il file così com'è offrirebbe di scrivere qualcosa che
dura circa un giorno.

**Seconda misura — di che forma sono le riscritture.** Confrontando le sette
versioni: in tutti e sei i cambi **nessuna intestazione è stata tolta o
aggiunta**, mentre le righe dentro sì (+4/−8 nel cambio più grosso). Dream pota
*dentro* le sezioni. Quindi un blocco marcato sopravvive come intestazione, ma
le sue righe no — e un guardiano che lo rimette ogni giorno sarebbe un
guardiano che litiga ogni giorno.

**Terza cosa, trovata leggendo il codice e non misurando:** `MemoryStore` non è
lo scrittore. Dream scrive `SOUL.md` con `write_file`/`edit_file`/`apply_patch`,
che hanno quei tre file in `extra_write_allowed_files`. Un guardiano lì non
avrebbe visto passare niente.

Da qui il disegno finale, che è due posti e una proiezione:

* `.jenny/soul_rules.md` è **la verità**. Il registro di scrittura di Dream
  ammette esattamente `SOUL.md`, `USER.md`, `memory/MEMORY.md` e
  `skills/<nome>/SKILL.md`: quel file non è nessuno di quelli, e nessuna
  passata può toccarlo. Non è una richiesta gentile nel prompt, è un perimetro.
* dentro `SOUL.md` c'è un **blocco proiettato**, ed è lì che il prompt lo legge
  — senza aggiungere un file al bootstrap, cioè senza aggiungere una voce al
  prompt di ogni turno.

La proiezione si rifà in `finish_dream_cycle`, che il chiamante invoca nel
`finally`: vale quindi anche per un turno crashato a metà, che è il caso in cui
il file ha più probabilità di essere rimasto monco. Il blocco si riconosce dai
due marcatori e, se mancano, dall'intestazione: il ripiego è per il giorno in
cui una potatura porta via un commento e lascia il testo — che è esattamente la
forma di modifica che le misure mostrano.

Salvare è **un comando** e non `workspace.write` su un path, perché salvare
vuol dire due scritture: se la casa ne facesse una, la copia comincerebbe a
divergere dalla verità al primo salvataggio.

## Cinque cose che lo schermo ha corretto

1. **Sei nomi di tema su sette cominciano per «Jenny».** A 56 px quella parola
   si mangiava quella che distingue: la striscia diceva «Jenny Ky…», «Jenny
   Sti…», «Jenny Fu…» — tre pastiglie diverse che dicono la stessa cosa. Sulle
   pastiglie resta la parola che le separa; i nomi interi restano nelle schede
   larghe dell'officina, dove ci stanno.
2. **A taglia grande lei si sedeva sull'ultima riga.** Il fondo delle stanze di
   impostazioni è adesso alto quanto lei: `--jenny-art-h`, il 73% del quadrato,
   che è l'altezza misurata dell'arte e non il 45% che è la sua larghezza —
   confonderli è un errore già fatto una volta.
3. **Il rig serve la copia estratta all'avvio**, non il file che stai
   modificando: un cambio al JS non si vede finché non lo riavvii. È la stessa
   trappola del telefono, un gradino più in basso.
4. **«Synthwave» non ci stava**, e il rig non lo diceva: a 56 px sul Titan si
   leggeva «Synthwa…» perché là il font rende più largo. Le pastiglie sono 64,
   e sette da 64 con dodici di aria stanno nei 554 px che restano a schermo.
5. **Le schede devono essere opache per coprirla.** Quella dell'officina era
   `--overlay`, che è semi-trasparente: lei si vedeva *attraverso*, con
   «osserva, regola, ripara» letto sopra la sua faccia. E il `z-index` va sulle
   schede e non sul contenitore che scorre — quello prenderebbe anche le
   pressioni sopra la sua metà scoperta, che è il modo in cui la si mette via.

## Sul Titan 2

Build release dal worktree al commit, firmata (`CN=flagDiZero`), zero `[jenny]`
nel log, installata sopra la precedente. Poi, in ordine:

* **l'avatar al posto della chiave**, «You and Jenny» coi sette temi leggibili
  e «Jenny — small · floating ›»: quella riga legge il config vero, e su questo
  telefono la finestra flottante è accesa e il permesso c'è;
* **una regola scritta nella casella e salvata.** «Salva» compare solo dopo il
  primo carattere. Dopo il tocco, sul telefono:
  `.jenny/soul_rules.md` con le parole, e dentro `SOUL.md` il blocco marcato —
  log: `SOUL.md: user rules re-projected (34 chars)`;
* **la potatura simulata**: tolto il blocco da `SOUL.md` con `sed` via `su`,
  cioè esattamente quel che una passata può fare;
* **una passata di Dream vera, forzata** portando `nextRunAtMs` al passato
  (scattata dopo 4 min e 28 s, dentro i 5 del `max_sleep_ms`):

  ```
  16:33:46.111  Cron: executing job 'dream'
  16:33:46.144  Dream memory budget: … SOUL.md 2597/3000 (86%) | runs since review: 8
  16:33:46.151  Dream: nothing to process
  16:33:46.171  SOUL.md: user rules re-projected (34 chars)
  16:33:46.193  Cron: job 'dream' completed
  ```

  Il blocco è tornato **identico**, e `memory/.dream_review` è delle 16:33 —
  la passata ha chiuso il suo ciclo davvero, non è stato un tick respinto;
* **la casella svuotata**: `soul_rules.md` sparisce e il blocco esce da
  `SOUL.md` (`re-projected (0 chars)`), con le 27 righe di lei intatte.

**Una cosa aperta, vista in quella riga di log.** Su questa installazione
`SOUL.md` ha un budget di 3 000 caratteri ed è all'86%: restano ~400 caratteri
di margine, mentre il tetto delle regole è 2 000. Un testo lungo ci passerebbe
sopra, e a quel punto le scritture *di Dream* verrebbero rifiutate dal budget —
`stuck` sale e parte un review forzato. La via pulita non è abbassare il tetto:
è **non contare il blocco dell'utente** dentro quel budget, perché quel budget
esiste per limitare ciò che Dream scrive, e quelle righe non sono sue. Con i
default (budget a 0, non applicato) il caso non esiste.

## Le mutazioni

18, tutte rosse. Tre erano verdi al primo giro, e le tre ragioni sono diverse:

* **la risposta del server era identica all'ipotesi.** `toggleFloating` accende
  l'interruttore prima di chiedere e poi prende per buona la risposta; il banco
  gliene passava una uguale a quel che la stanza aveva già indovinato, quindi
  buttarla via non si vedeva. Adesso il server risponde il contrario.
* **la striscia dei temi si ridisegnava solo riaprendo la stanza**, e il banco
  non la riapriva: la guardia si poteva togliere e le sette pastiglie
  diventavano quattordici senza che nessuno lo notasse.
* **due marcatori in disordine** — chiusura prima dell'apertura — non erano
  provati da niente, e prenderli per un blocco vuol dire tagliare il file al
  contrario.

## Il secondo giro: le quattro righe che avevo tolto

Piano: [`casa-tu-e-jenny-resto-plan.md`](casa-tu-e-jenny-resto-plan.md). La
tabella qui sotto era la lista dei tagli, ed è stata ribaltata dall'utente.
Quattro delle cinque voci sono atterrate; la quinta aspetta tre decisioni sue.

**«Tu e Jenny» adesso ha quattro righe e una scheda:** chi risponde, Jenny,
aggiornamenti, backup — e l'officina, invertita in fondo. La riga muta della
versione non c'è più: quel numero è il valore della riga che apre gli
aggiornamenti, perché un numero e basta è un'etichetta, non un'impostazione.

Tre cose di questo giro vale la pena rileggerle:

1. **L'accento non è sempre l'accento.** Su una scheda invertita `--accent` è
   invisibile in Chanel — il tema di partenza — e in Fumetto, dove l'accento
   *è* il testo. Misurato su tutti e sette e deciso con una soglia (3:1, quella
   di un oggetto grafico): tre temi tengono la tinta, quattro prendono `--bg`.
   Il conto lo rifà un banco a ogni ritocco di un tema.
2. **Una macchina a stati si estrae, non si ricopia.** Il giro degli
   aggiornamenti è uscito dall'officina in `shared/update-flow.js`, e il
   guadagno vero non è la stanza in casa: è che quella macchina, che viveva nel
   controller senza un banco che la esercitasse, adesso ne ha 18 — coi due casi
   che ingannano in testa.
3. **Una data che nessuno scriveva.** «Ultimo backup» non aveva fonte, e non
   poteva averla dal lato che prepara il file: fra il container cifrato e il
   file su disco c'è un picker di sistema annullabile, e l'esito lo conosce
   solo il client.

**Il rig ha trovato quattro difetti che i banchi non vedevano**, ed è il
motivo per cui vale la pena guardarlo a ogni stanza: la riga della chiave
visibile senza nessuna marca (`[hidden]` a specificità zero, la terza volta per
questa casa — adesso c'è un banco che cerca il caso da solo); «Modelli di
OpenCode» sopra i modelli di Anthropic; due pastiglie indistinguibili nei temi
chiari; e «Sei alla , ed è l'ultima» prima che il payload rispondesse.

## Fuori dal primo giro, ed erano decisioni ribaltate

| Riga della tavola | Perché no |
|---|---|
| Chi risponde | ha una tavola sua; è anche l'unica schermata dove un tocco sbagliato lascia Jenny senza risposte, e una chiave API si incolla in officina |
| Avvisi di sua iniziativa | **la tavola disegna un interruttore dove l'app ha tre permessi e una scelta a tre valori** (esenzione batteria, sveglie precise, `power.keepAwake`): è il blocco batteria dell'officina, ed è una diagnosi |
| Backup | un backup cifrato con passphrase è un'operazione da operatore |
| Aggiornamenti | giro suo; qui c'è solo il numero di versione |
| «grazie a [n] sostenitori» | non c'è nessun posto da cui prendere quel numero |
| Maniglia del cassetto | come nei giri precedenti |

**Questa tabella è stata ribaltata dall'utente il 19/09/2026, e aveva ragione.**
Quattro di quelle sei righe adesso esistono; la quinta — «grazie a [n]
sostenitori» — aspetta tre decisioni sue (da dove vengono i nomi, cosa si
pubblica di una persona, dove porta «Sostieni Jenny»), e finché quei nomi non
esistono la riga non si mette: «grazie a 0 sostenitori» è peggio di niente.
