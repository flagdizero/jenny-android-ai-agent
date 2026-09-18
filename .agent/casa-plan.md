# La casa — piano

Jenny ha una interfaccia sola, e fa due mestieri diversi. Chi la usa vuole
parlarle; chi la costruisce vuole vedere cosa combina. Finora i due mestieri
hanno abitato lo stesso schermo, e ha vinto il secondo: la conversazione è
diventata un pannello di controllo con dentro anche delle frasi.

Questo piano costruisce **la casa**: una seconda interfaccia il cui unico
mestiere è la conversazione. L'interfaccia attuale resta intera e diventa
**l'officina**, raggiungibile dal menu. Non si toglie niente a nessuno: si
smette di chiedere a una schermata sola di essere due cose.

Il disegno di riferimento è l'artifact *Jenny UI: Utente e Operatore*. Questo
file è il piano; le tavole sono lì.

---

## Il primo giro è solo la chat

Deciso e ristretto in fase di domande: **la prima versione della casa è la
schermata di conversazione e nient'altro.** Non «Tu e Jenny», non i quaderni,
non il cassetto delle app, non «Chi risponde», non i ringraziamenti. Quelle
tavole restano valide e restano dopo.

Dentro la chat, deciso:

| Cosa | In casa | Perché |
|---|---|---|
| Scrivere e leggere | sì | è il mestiere |
| Allegati, foto in entrata e in uscita | sì | è la cosa più naturale che ci sia, e Telegram la fa già |
| Riga di lavoro | sì, nuova | vedi sotto: è il pezzo che questo piano inventa |
| Comandi slash | no | è linguaggio da terminale |
| Interruttore sola-lettura | no | è una garanzia da operatore, vive in officina |
| Scelta del modello al volo | no | ha una tavola sua, fuori dal primo giro |
| Pensieri, tool, subagent, token, tempi, latenza | no | è esattamente ciò da cui la casa esiste per proteggerti |

---

## Il vincolo che decide la forma

**Il guscio nativo chiede al JS cinque cose.** `onNativeReady()`, `goHome()`,
`onPackageChanged()`, `handleHardwareBack()` e `openChat()`, tutte su
`window.mobileApp`.

*Correzione:* al passo 1 ne avevo contate tre. Le due mancanti non compaiono
cercando `window.mobileApp.<metodo>` perché il Kotlin le invoca su una variabile
locale — `BACK_PRESS_JS` fa `var app = window.mobileApp; … app.handleHardwareBack()`
— e `openChat` arriva dallo stesso genere di script iniettato. Lezione: il ponte
verso il JS si conta sulle **chiamate**, non sul nome dell'oggetto.

`handleHardwareBack` è quella che conta di più, e non è una formalità: se manca,
il tasto Indietro non chiude l'immagine ingrandita, e la pressione ricade sul
sistema.

**Il gateway non dà 404: dà l'officina.** `_serve_static`
([`ws_http.py:779`](../jenny/webui/ws_http.py)) fa fallback su `index.html` per
ogni percorso sconosciuto, e serve i byte *impacchettati* solo per i file
elencati in `_UI_MANIFEST`
([`android_assets.py:210`](../jenny/utils/android_assets.py)). Un file nuovo non
messo in quella lista non fallisce in modo rumoroso: viene servita l'officina, o
una copia vecchia dal mirror su disco. È la trappola più cara di tutto il piano
ed è anche la più facile da evitare, purché sia scritta.

**Il fondamento è già condiviso e già senza DOM.** `shared/ws-manager.js` è un
`EventTarget` singolo che non tocca la pagina; `api-client`, `i18n`, `theme`,
`mascot`, `session-manager` sono nella stessa condizione. La casa li importa, non
li riscrive. Quello che riscrive è solo ciò che disegna.

**Il turno ha già un nome.** Ogni frame live porta `turn_id`, la chat di oggi
raggruppa per quello ([`mobile-chat.js:952`](../jenny/templates/ui/assets/mobile-chat.js)),
e il recorder lo stampa nel transcript. Il salto «questo turno, in officina» si
può fare. Nota: nella storia vecchia esistono righe con `turn_id` nullo
([`mobile-chat.js:958`](../jenny/templates/ui/assets/mobile-chat.js)), quindi il
salto deve degradare in «apri la conversazione» invece di non fare niente.

---

## Le decisioni, e perché

**Guscio nuovo, fondamenta vecchie.** La casa nasce come secondo ingresso —
`casa.html` + `assets/casa-*.js` + `assets/casa-style.css` — che importa
`assets/shared/*`. Non è una vista dentro l'app di oggi, e non è un'app separata.

Il motivo è la misura: la chat attuale è di 3.789 righe perché regge delta,
ragionamento, tool, subagent, allegati, selezione, badge di provenienza. La chat
di casa deve reggere delta e allegati. Scriverla dentro quel file significherebbe
aggiungere un secondo modo di disegnare a un file che ne ha già uno complicato, e
ogni modifica alla casa metterebbe a rischio l'officina — che è il telefono di
tutti i giorni, e deve restare usabile per tutta la durata del lavoro.

Il prezzo, dichiarato: passare da una parte all'altra è un caricamento di pagina,
e ogni modulo in `shared/` acquista un secondo chiamante, quindi va cambiato
pensando a due.

**Si costruisce a lato, si inverte alla fine.** Il guscio nativo carica la
costante `GATEWAY_PATH = "/html-mobile/"`
([`MainActivity.kt:56`](../android/app/src/main/java/com/flagdizero/jenny/MainActivity.kt)),
cioè `index.html`. Per tutta la costruzione la casa vive a `casa.html` e si
raggiunge a mano; quando convince, si scambiano i nomi dei due file —
`index.html` diventa la casa, l'officina diventa `officina.html` — e nessun
percorso di asset cambia, perché sono tutti assoluti.

*Correzione (18/09/2026, sul telefono):* **il Kotlin invece va toccato, una
riga.** `shouldOverrideUrlLoading` blocca ogni navigazione di primo livello
verso il gateway che non sia esattamente `GATEWAY_PATH`, come rete di sicurezza
contro un href risolto male sotto `/html-mobile/`. Le due porte sono proprio
navigazioni di quel tipo, quindi venivano bloccate: l'unica traccia era
`Blocked main-frame navigation to a non-SPA gateway path` in logcat. Il guard
ora conosce i due documenti-guscio per nome — un elenco chiuso, non un
prefisso, così `/api/…` resta fuori.

**Il segreto viaggia in un fragment, quindi la strada fra i gusci e' una sola.**
Il segreto di bootstrap arriva alla pagina in `#bs=` e viene consumato e
cancellato al primo caricamento
([`api-client.js:11`](../jenny/templates/ui/assets/shared/api-client.js)): da li'
in poi vive solo nella memoria di quella pagina. Una navigazione verso l'altro
guscio lo perderebbe, e il documento di destinazione prenderebbe 401 al primo
`bootstrap()`. `api.navigate(path)` fa per un altro documento quel che `reload()`
fa per questo: ri-inietta il segreto nel fragment. E' un metodo **aggiunto**, non
una modifica di uno esistente — la regola che tiene additivo tutto il lavoro.

**Il tasto Home non si tocca.** Resta com'è: `home-view.js` continua a decidere
dove atterra, l'impostazione resta in officina. Una sola nota per dopo, non per
ora: hai detto che di default Home deve riportarti dove stavi, ma il default nel
codice è `chat` e non `last` ([`home-view.js:18`](../jenny/templates/ui/assets/shared/home-view.js)).
È una riga, ed è un lavoro suo.

**La provenienza resta visibile.** Un messaggio scritto da Telegram, dalla
tendina o dal fumetto arriva in chat con la sua etichetta, e ce l'ha già oggi
([`mobile-chat.js:1264`](../jenny/templates/ui/assets/mobile-chat.js)). In casa
serve più che in officina: è ciò che rende la chat *il* posto dove c'è tutto.

---

## La riga di lavoro

È il pezzo nuovo, ed è il cuore della casa. Mentre Jenny lavora, sotto l'ultimo
messaggio compare una riga sola che dice cosa sta facendo, con una parola scelta
male apposta — *elucubra*, *rovista*, *scartabella*. Quando ha finito, sparisce.

**Le parole sono legate a cosa succede davvero.** Il frame `tool_events` porta
già il nome del tool ([`progress_events.py:61`](../jenny/agent/progress_events.py));
i nomi sono 43 e si raggruppano in sette famiglie. Ogni famiglia ha un suo
vocabolario, e si pesca da quello della famiglia in corso:

| Famiglia | Tool | Registro dei verbi |
|---|---|---|
| pensa | nessuno: flusso di ragionamento, testo che si forma | elucubra, rimugina, arzigogola |
| legge | `read_file` `list_dir` `get_source` `recall` `recall_history` `memory` `ui_view` `subagent_status` `list_exec_sessions` `update_status` `ssh_hosts` | sfoglia, spulcia, scartabella, ripassa |
| cerca | `grep` `find_files` `web_search` | rovista, setaccia, fruga, fiuta |
| scrive | `write_file` `edit_file` `apply_patch` `journal_append` | scrive, lima, ricopia, appunta |
| esce | `web_fetch` `browser_*` `download_file` `ssh_exec` `ssh_job` `ssh_transfer` `get_location` | sbircia fuori, esce un attimo, bussa |
| esegue | `python_exec` `write_stdin` | fa due conti, smanetta, armeggia |
| delega | `spawn` `subagent_*` `long_task` `cron` `message` `complete_goal` | chiama rinforzi, mette qualcuno al lavoro |

Un tool sconosciuto — ne arriveranno — cade su un vocabolario generico invece di
sparire: *si dà da fare*. La tabella sta in un file solo, accanto a quella delle
icone di attività che già esiste
([`mobile-chat.js:40`](../jenny/templates/ui/assets/mobile-chat.js)), perché è la
stessa specie di conoscenza.

**La regola dell'onestà: non si mostra mai un verbo di una famiglia che non sta
girando.** È l'unica cosa che distingue questa riga da una animazione di
caricamento, e il giorno che la violiamo tanto vale mettere tre puntini.

**Il tempo.** La riga compare dopo mezzo secondo di lavoro, così una risposta
istantanea non la fa lampeggiare. Cambia parola quando cambia famiglia, e dentro
una famiglia lunga ogni pochi secondi, perché una riga ferma sembra bloccata.
Sparisce quando arriva la risposta.

**La porta.** Finché la riga è lì, un tocco lungo apre *quel turno* in officina —
con pensieri, tool, tempi, tutto. Dopo, la riga non c'è più e non c'è nessuna
porta per turno: ma l'officina mostra la stessa identica conversazione, quindi ci
si arriva dal menu e si è già nel posto giusto. Il tocco lungo resta sulla riga e
non passa alle bolle, perché lì la pressione lunga è già promessa alla selezione
del testo (`.agent/chat-selection-plan.md`).

---

## I passi

Ognuno finisce con qualcosa che si guarda sul telefono. L'officina resta
funzionante dal primo all'ultimo.

**1. Il guscio.** ✅ `casa.html`, `casa-app.js`, `casa-style.css`, voci nel
`_UI_MANIFEST`, e la CSP estesa dal solo `index.html` ai due documenti della
shell. La pagina si carica, si attacca al websocket, implementa i tre metodi che
il guscio nativo chiama, e mostra una chat vuota.

**2. La conversazione.** Storia dal transcript, messaggi live, risposta che si
forma. Bolle, niente altro. *Prova:* la stessa conversazione aperta nelle due
interfacce dice le stesse cose, con in più i badge di provenienza.

**3. Il composer.** Scrivere, mandare, fermare. Tastiera fisica del Titan:
invio manda, shift-invio va a capo.

**4. La riga di lavoro.** Tabella delle famiglie, vocabolari, tempi, comparsa e
scomparsa, tocco lungo verso il turno in officina. *Prova:* una domanda che
obbliga a leggere, cercare ed eseguire, e la riga che le racconta nell'ordine
giusto.

**5. Gli allegati.** Foto in entrata e in uscita, ingrandimento.

**6. La mascotte.** Jenny presente anche in casa, dimensione e posizione come
nelle tavole (`sm` = 120 px, il personaggio riempie il 73% del quadrato).

**7. Le due porte.** Dal menu di casa si va in officina; dall'officina si torna.
Poi lo scambio dei nomi: la casa diventa l'ingresso.

---

## Le trappole, scritte prima di caderci

- **`_UI_MANIFEST`.** Ogni file nuovo va aggiunto a mano. Dimenticarlo non dà un
  errore: dà l'officina.
- **Le modifiche al JS vogliono una ricostruzione.** Toccare `workspace/ui/` sul
  telefono non fa niente, e l'md5 identico te lo fa credere.
- **`turn_id` nullo** nella storia vecchia: il salto in officina degrada, non
  fallisce.
- **Doppio chiamante in `shared/`.** Da qui in poi ogni modulo condiviso ha due
  padroni. Una modifica pensata per l'officina va provata anche in casa.
- **La CSP è applicata per nome di file**, solo a `index.html`
  ([`ws_http.py:819`](../jenny/webui/ws_http.py)). `casa.html` nascerebbe senza
  policy e se la prenderebbe addosso tutta insieme allo scambio dei nomi, cioè
  alla fine, cioè nel momento peggiore. Va estesa ai due documenti **al passo 1**,
  così la casa cresce sotto la stessa regola che erediterà.
- **Il resto non tocca Python.** I frame che servono alla casa esistono già e
  arrivano già sul canale websocket: tolti la riga del manifest e il nome nella
  CSP, è lavoro di solo client.

---

## Cosa resta aperto

- «Tu e Jenny» e il resto delle tavole: il giro dopo.
- Cosa fa la casa quando qualcosa va storto — provider giù, permesso mancante,
  aggiornamento fermo. Oggi un guasto del provider torna come testo in chat,
  quindi in casa si legge come una frase di Jenny. Può bastare per il primo giro.
- Il ruolo di launcher e il default di `home-view`: lavoro separato.
