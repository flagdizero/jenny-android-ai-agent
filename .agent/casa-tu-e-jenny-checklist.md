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

## Tre cose che lo schermo ha corretto

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

## Fuori da questo giro, e sono decisioni

| Riga della tavola | Perché no |
|---|---|
| Chi risponde | ha una tavola sua; è anche l'unica schermata dove un tocco sbagliato lascia Jenny senza risposte, e una chiave API si incolla in officina |
| Avvisi di sua iniziativa | **la tavola disegna un interruttore dove l'app ha tre permessi e una scelta a tre valori** (esenzione batteria, sveglie precise, `power.keepAwake`): è il blocco batteria dell'officina, ed è una diagnosi |
| Backup | un backup cifrato con passphrase è un'operazione da operatore |
| Aggiornamenti | giro suo; qui c'è solo il numero di versione |
| «grazie a [n] sostenitori» | non c'è nessun posto da cui prendere quel numero |
| Maniglia del cassetto | come nei giri precedenti |
