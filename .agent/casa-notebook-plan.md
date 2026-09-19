# La chat di un quaderno — il piano

Il pannello «con chi parli» elenca i quaderni e non ci si può entrare: è la
decisione D1 del giro scorso, scritta dentro al codice perché si veda — righe
inerti, niente chevron, niente spunta (`.agent/casa-who-plan.md`). Questo giro
la toglie: un tocco su un quaderno apre la sua conversazione.

**La parte difficile non è lo scambio.** `sessionManager.switchTo` esiste, lo
usa l'officina da mesi e costa una riga. Costa tutto il resto: quel che smette
di essere vero il giorno in cui la casa non ha più una conversazione sola. Il
titolo, il vuoto, il tasto Ferma, la mascotte che aspetta un `turn_end` che non
arriverà mai più, la bozza nel campo, la pagina precedente, e le frasi scritte
nel codice che dicono «in casa la conversazione è una sola».

## Non è una vista filtrata, è un'altra Jenny

Da dire prima del rilievo, perché decide il tono di tutto il resto. Una
sessione `project:` non è la conversazione personale guardata attraverso un
filtro:

- la radice di lavoro è la cartella del quaderno — `project:piante` →
  `<workspace>/wikis/piante` (`jenny/security/workspace_access.py:250`), non
  l'installazione intera;
- il contesto che Jenny si costruisce è un altro (`jenny/agent/context.py:615`);
- **non alimenta la memoria di lungo periodo.** È la ragione per cui esiste una
  terza categoria di chiave invece di un booleano (`jenny/session/keys.py:38`):
  quello che dici dentro un quaderno non passa da Dream e non finisce in
  `MEMORY.md`.

Quindi il titolo non è decorazione. È la sola cosa a schermo che dice a chi
stai parlando e dove va a finire quel che scrivi.

## Il rilievo

**1. Lo scambio è già scritto, e sa già le cose difficili.**
`SessionManager.switchTo` (`shared/session-manager.js:31`) cambia chiave, fa
l'attach della nuova *prima* della detach della vecchia, alza la generazione
(`switchGeneration`, riga 88) e annuncia `chat:switch` (riga 52). La chiave la
costruisce il chip: `project:${scope.name}` (`shared/scope-chip.js:535`).
L'officina ci si appoggia con sei righe (`mobile-chat.js:687`).

**2. La casa è già pronta a metà.** `_belongsHere` confronta già ogni frame con
`sessionManager.currentChatId` (`casa-chat.js:264`): i messaggi della
conversazione lasciata sono già scartati. `_beginHistoryPage` usa già la
guardia `stale` del session manager invece di un `false` scritto a mano, e il
commento che la spiega (`casa-chat.js:136`) dice: *«il giorno che le
conversazioni diventassero due non sarebbe una bugia da scoprire»*. È oggi.
`reload()` (`casa-chat.js:412`) fa già svuota-e-ricarica sulla chiave corrente.

**3. Due difetti dormienti che lo scambio sveglia.** Oggi `reload()` si chiama
solo su `session_boundary`, cioè quasi mai; con lo scambio diventa il gesto
normale.

- `this.pager.bindInfiniteScroll()` sta **dentro `load()`**
  (`casa-chat.js:172`) e il metodo non ha guardia (`history-pager.js:76`
  aggiunge un listener e basta): una ricarica per scambio è un listener in più
  per scambio. L'officina lo lega una volta sola, in `setupInfiniteScroll`.
- `reload()` non chiama `pager.reset()`, che esiste esattamente per questo — il
  suo commento dice *«Si chiama al cambio di conversazione»*
  (`history-pager.js:64`). Con la fetch che va a buon fine il danno si richiude
  da sé (`adopt` riscrive cursore e `hasMore`); con la fetch che fallisce lo
  schermo è vuoto e il cursore è ancora quello dell'altro quaderno, e una
  scorsa in su incolla in cima la storia della conversazione sbagliata.

**4. Il titolo è la fonte del nome personale.** Il pannello legge il nome dalla
sua intestazione apposta, perché i due non possano dirne due diversi:
`personalName: () => document.querySelector('.casa-who-name')?.textContent`
(`casa-app.js:63`). Se il titolo diventa «piante», la riga personale del
pannello dirà «piante». Il nome personale deve essere messo da parte al primo
avvio, prima che il titolo cominci a cambiare.

**5. Il gateway sa già dire di no, e dice bene.** Un `chat_id` nella forma
`project:<nome>` viene accettato solo se il nome passa `is_valid_project_name`;
nella forma giusta ma col nome impossibile il frame è **rifiutato ad alta
voce** invece di cadere sulla chat personale, con un testo già scritto che
spiega cosa fare (`channels/websocket.py:549-620`). Le cartelle che sbagliano
il nome sono precisamente quelle che `/api/projects` mette in `unopenable` e
che il pannello tiene già separate e inerti: le righe che non si possono aprire
restano righe, e la strada del rifiuto non si percorre. Resta una rete, non una
via.

**6. Gli avvisi proattivi vanno sempre alla chat personale.** Il fan-out
mette d'ufficio `WEBUI_DEFAULT_CHAT_ID` tra i bersagli
(`runtime/delivery.py:190-191`), e dentro un quaderno `_belongsHere` li
scarterà. La notifica di sistema parte comunque, e il tap chiama `openChat()`
(`MainActivity.kt:120`).

**7. Nessuno dei due gusci ricorda la conversazione aperta.** Non c'è nessun
`localStorage` per la chiave di sessione: ogni caricamento di pagina riparte da
`websocket:default`. L'officina è così oggi.

**8. La data di un quaderno è l'mtime della sua cartella**, non l'ultima volta
che ci hai parlato (`_collect_projects` lo dichiara, e il telefono l'ha
confermato: cinque quaderni su sei dicevano «15 giorni fa»). Non cambia con
questo lavoro; cambia il giorno in cui la conversazione di un progetto porta la
propria data — e quel giorno è vicino, perché da qui in poi quella
conversazione esiste davvero.

## La forma

L'intestazione della tavola `ChatQuaderno.dc.html`, ridotta a ciò che esiste:

```
 quaderno                                    ← occhiello (casa.kicker)
 ● piante  ⌄                                 ← pallino + nome + chevron
```

Il pallino è quello del pannello, stesso `dotColor(name)`: è l'unica cosa che
lega visivamente la riga toccata alla stanza in cui sei finito. Nella
conversazione personale non c'è pallino e l'occhiello torna «conversazione
personale», che è quel che dice già oggi.

Il campo del messaggio cambia invito: «Scrivi a Jenny, nel quaderno». Senza il
nome dentro — sta due centimetri più su, in caratteri grandi.

Il pannello cambia di poco: le righe dei quaderni diventano bottoni, la spunta
segue la conversazione in cui sei invece di stare sempre sulla personale, e la
riga corrente porta `aria-current`. Un tocco chiude il pannello e cambia
conversazione.

**Cosa non compare**, e non è dimenticanza: «8 pagine» (nessun payload porta il
numero delle pagine — D2 del giro scorso), l'avatar «Tu e Jenny» (è un'altra
tavola), «+ Nuovo quaderno» (crea cartelle: è il dialogo dell'officina, e qui
vorrebbe le sue tre schermate).

## I passi

**1. Il filo regge due conversazioni.** `bindInfiniteScroll` una volta sola;
`pager.reset()` in `reload()`. Due righe, e sono le due che fanno la differenza
fra uno scambio e uno scambio che lascia detriti.

**2. La casa cambia conversazione.** Un metodo solo in `CasaApp`, che fa nello
stesso ordine dell'officina: `switchTo` (e se torna `false`, non è successo
niente), poi azzera ciò che apparteneva al turno lasciato — `activity.stop()`,
`jenny.noteTurnRunning(false)`, `jenny.idle()`, `_setRunning(false)` — poi
`chat.reload()`. L'azzeramento passa da `chat:switch`, come fa la Jenny
flottante in officina (`mobile-jenny.js:144`), non da una chiamata diretta: è
lo stesso evento e lo stesso motivo.

**3. L'intestazione dice dove sei.** Nome, pallino, occhiello, invito del
campo; e il nome personale messo da parte al primo avvio (rilievo 4).

**4. Il pannello diventa un comando.** Righe-bottone, spunta che segue,
`aria-current`, chiusura al tocco. Le righe non apribili restano inerti come
sono.

**5. Le vie di ritorno.** Indietro, Home e `openChat()` — v. D1 e D3.

**6. Le parole.** Chiavi nuove in `it.json` e `en.json`: l'occhiello del
quaderno, l'invito del campo, il vuoto di un quaderno senza conversazione.

**7. I banchi.** Compreso il rovesciamento di quelli che oggi garantiscono D1
(v. le trappole).

## Le trappole, scritte prima di caderci

**La bozza che ti segue.** Scrivi mezza frase nella personale, apri il
pannello, entri in «piante», premi invio: la frase va nel quaderno. È la stessa
famiglia di guasto di cui parla il commento di `switchGeneration` — *«il fatto
che l'utente enuncia finisce nel diario dell'altro progetto»* — solo un attimo
prima. V. D2.

**La mascotte incantata.** Se lasci una conversazione mentre Jenny sta
rispondendo, il `turn_end` di quel turno arriverà a una chat che non guardi più
e verrà scartato: la faccia resterebbe in `think` per sempre. È il difetto che
`chat:switch` esiste per chiudere, ed è già successo una volta (memoria: gli
avvisi proattivi senza `turn_end`). Stesso discorso per la riga di lavoro e per
il bottone Ferma, che resterebbe «ferma» senza niente da fermare.

**L'avviso proattivo invisibile.** Dentro un quaderno, un avviso della chat
personale non si vede (rilievo 6). La notifica di sistema c'è, ma in casa non
c'è nessun segno di non letto sul titolo. Per questo giro si accetta e si
scrive; il pallino di non letto sul titolo è il naturale giro dopo.

**La porta dell'officina.** Da «piante», il bottone in alto a destra apre
l'officina sulla conversazione **personale**, perché nessuno dei due gusci
ricorda la chiave (rilievo 7). V. D3.

**Due tocchi ravvicinati.** Due righe toccate a 200 ms di distanza: vince chi
risponde per ultimo, che non è chi hai toccato per ultimo. Lo risolve la
generazione, che `casa-chat.load()` legge già attraverso `loadThread`. Da
provare, non da presumere.

**I banchi che oggi garantiscono il contrario.** Questi asseriscono D1 e devono
diventare il loro opposto — chi li vede passare deve sapere che non promettono
più l'inerzia:
`test_casa_who_client.py::test_a_notebook_row_is_not_a_button` e
`::test_only_the_row_you_are_on_carries_the_check` (che oggi controlla che la
spunta sia sempre sulla personale). Vanno riscritti, non cancellati: la riga
non apribile resta inerte, e quella proprietà va conservata in un banco suo.
Vanno aggiornate anche le due dichiarazioni in prosa: l'intestazione di
`casa-who.js` (righe 8-13) e il commento di `casa-chat.js:136`.

**Il telefono non vede una patch di `workspace/ui/`.** Serve il rebuild (v.
memoria). E una prova sul telefono scrive dentro una wiki vera: per la prova
finale serve un quaderno di scarto, o un messaggio che non lasci traccia utile
— il diario di un progetto è per metà personale.

## Decisioni prese

Tutte e cinque approvate come proposte, il 19/09/2026. Restano scritte com'erano
— la proposta e il suo perche' — perche' fra sei mesi la domanda non sara' «cosa
fa Indietro», che si legge nel codice, ma «perche' fa cosi'».

**D1 — Cosa fa Indietro dentro un quaderno.** Oggi la catena è: pannello,
immagine ingrandita, poi niente (e il niente è voluto: l'app è il launcher).
*Proposta: dentro un quaderno, Indietro torna alla conversazione personale*, e
solo alla radice non fa niente. Un quaderno diventa così uno stato da cui si
esce col gesto che su Android significa «esci da qui», senza mai chiudere il
task. Home (`goHome()`) fa lo stesso: «sei a casa» torna a voler dire qualcosa.

**D2 — La bozza, quando cambi conversazione.** Tre strade: la bozza ti segue
(è quel che fa l'officina, ed è la trappola qui sopra); si butta (niente si
perde in silenzio, mai); **si parcheggia con la conversazione in cui l'hai
scritta** e torna quando ci torni. *Proposta: la terza*, una mappa in memoria
chiave→testo, otto righe. Gli allegati in attesa **non** si parcheggiano: si
rimettono in fila da soli, sono visibili sopra il campo, e spostarli vorrebbe
dire tenere aperti dei blob per conversazioni che non guardi.

**D3 — La porta dell'officina da dentro un quaderno.** *Proposta: per questo
giro resta com'è — apre l'officina sulla personale — e lo si scrive nel codice
e nel checklist.* Farla bene vuol dire passare la chiave nel frammento
(`#chat=project:piante`) e insegnare all'officina a leggerlo: è lavoro
nell'altro guscio, e l'officina non legge ancora nemmeno il `#turn=` che la
casa le manda già. L'alternativa onesta, se si vuole chiudere il buco senza
toccare l'officina, è spegnere la porta dentro un quaderno — ma un bottone
spento è peggio di un bottone che ti porta a casa.

**D4 — Ricordare la conversazione aperta fra un avvio e l'altro.** *Proposta:
no.* La casa riapre sempre sulla personale, come l'officina. È coerente con
Home e con D1, e soprattutto: la casa è la schermata iniziale del telefono, e
una schermata iniziale che riparte dentro un quaderno aperto due giorni fa
sarebbe una sorpresa, non una comodità.

**D5 — Il vuoto di un quaderno senza conversazione.** Oggi il vuoto dice «Non
c'è ancora niente qui. Comincia tu.», che è vero anche lì. *Proposta: parole
sue* — che il quaderno esiste, che la conversazione no, e che quel che scrivi
qui resta qui. È l'unico punto in cui si può dire, senza spiegarlo, che questa
è un'altra stanza.

## Com'e' andata

Fatto, e verificato. Il registro sta in `.agent/casa-notebook-checklist.md`. Le
due cose che il rilievo non poteva prevedere: il banco dell'officina che ritaglia
`keyFor` andava ripuntato al nuovo `projectKey` (un ritaglio non porta con se'
gli import di chi lo ospita), e due mutazioni su venti sono passate verdi perche'
colpivano `init()` invece del metodo in prova — la mutazione sbagliata dice
«banco cieco» e non lo e'.

## Come si verifica

**Ai banchi** (`node --input-type=module`, come gli altri client di casa): lo
scambio azzera turno, riga di lavoro e bottone Ferma; la spunta segue la
conversazione; una riga non apribile resta inerte; il titolo e il pannello non
dicono due nomi diversi dopo uno scambio; la bozza torna dove l'hai lasciata;
due scambi ravvicinati lasciano vincere il secondo. **Ogni banco nuovo va
rimutato**: un banco che ripunta a codice nuovo può passare a vuoto, ed è già
successo in questo lavoro.

**Al contratto** (grep e struttura): `bindInfiniteScroll` chiamato una volta
sola; `pager.reset()` in `reload()`; Indietro chiude il pannello prima di
cambiare conversazione; nessuna stringa cablata a schermo.

**Nel browser**, col rig locale: i sette temi sul titolo lungo (un nome di
quaderno può essere lungo quanto vuole entro 64 caratteri — l'ellissi c'è già
nel pannello, serve anche nel titolo).

**Sul telefono**: entrare in un quaderno vero dal pannello, vedere la sua
storia, mandarci un messaggio innocuo, verificare dal transcript che è finito
nella sessione `project:` e non nella personale, tornare indietro e ritrovare
la conversazione di casa dov'era. E il contrario di quel che deve succedere: da
dentro un quaderno, un avviso proattivo non compare (e la notifica sì).
