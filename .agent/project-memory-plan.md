# La memoria dei progetti — riaprire il verso che manca

> *I nomi dei progetti in questo file sono inventati, e i fatti di esempio anche: la forma, i
> numeri e le proporzioni sono quelli osservati davvero sul telefono, i nomi e i contenuti no. Il
> repo e' pubblico.* Stessa convenzione di [`project-lifecycle-plan.md`](./project-lifecycle-plan.md).

> **Misurato sul Titan 2 l'08/09/2026**, in sola lettura, sui dieci progetti veri
> dell'installazione: 72 righe di journal, 177 turni utente, `USER.md` e `MEMORY.md` correnti,
> `history.jsonl` intero.

## Il difetto, in una riga

Una cosa che l'utente dice **di se'** dentro un progetto non arriva da nessuna parte: non entra
nel diario personale, e nel journal del progetto ci entra nel posto sbagliato.

## Il difetto, misurato

**Meta' di quel che i journal contengono non riguarda i progetti.** 39 righe su 72 (54%) sono
fatti sulla persona. E la divisione **non e' per riga, e' per progetto**:

| progetto | sulla persona | sul progetto |
| --- | ---: | ---: |
| `taccuino` — chat-diario su un tema personale ricorrente | 11 | 1 |
| `serata` — chat-diario | 18 | 6 |
| `itinerario` — pianificazione personale | 7 | 4 |
| `vivaio` — cura di cose possedute | 2 | 4 |
| `redazione` — progetto di lavoro | **0** | 15 |
| tre wiki di sola documentazione | 0 | 3 |

**E quei fatti non ci sono.** Campionati 23 fatti dai journal e cercati in `USER.md` +
`MEMORY.md`: **18 assenti**. I 5 presenti sono esattamente quelli detti *anche* nella
conversazione personale — nessuno e' arrivato passando dal progetto.

**Il cancello funziona al 100%, ed e' questo il punto.** `history.jsonl` conteneva 154 voci,
**tutte** `unified:default`: `MemoryStore.append_history` rifiuta una chiave `project:`
(`memory.py`), quindi Dream non ha mai letto una parola di progetto. Non e' un filtro che
sbaglia: e' un canale che non esiste.

**E la perdita e' piu' grande dei journal.** 177 turni utente hanno prodotto 72 righe, e la
cattura manca proprio dove il materiale e' piu' personale: un progetto con 15 turni ha prodotto
**una** riga, due altri con 12 e 6 turni ne hanno prodotte **zero**. La cattura era abilitata —
dipende solo dal permesso di scrittura del turno (`context.py`, `capture=_turn_is_writable()`),
non esiste un interruttore per progetto.

## L'invariante, e l'asse su cui e' implementato

`.agent/security.md` dichiara: **«chi sei viaggia, dove altro lavori no»**. E' una regola sulla
*categoria del fatto*. E' pero' implementata come un cancello sull'*origine della sessione*.

Nel verso in uscita le due coincidono, perche' esce solo l'identita' (`SOUL.md`, `USER.md`). Nel
verso in entrata **no**: un fatto identitario detto dentro un progetto e' identita', cioe'
esattamente la classe autorizzata a viaggiare, e viene fermato lo stesso. Il cancello blocca
troppo perche' guarda da dove arriva invece che di cosa parla.

Questo piano non sposta la riga: **la applica anche nel verso che oggi manca.**

## Due strade scartate, e la misura che le scarta

### Un interruttore per progetto («questo progetto parla di me»)

Scartata. Una memoria che funziona solo se sai che va accesa e' rotta — e la misura la smentisce
anche tecnicamente: **la divisione vera e' fra fatti, non fra progetti**. `redazione` ha zero
fatti personali perche' li' non ne sono stati detti, non perche' e' «un progetto di lavoro». Il
giorno che in `redazione` arriva una frase sulla famiglia, l'interruttore spento la butta via.

### Il gesto in-turno (dare al modello del turno una seconda destinazione)

Scartata, e questa la scarta un numero. Si appoggerebbe allo stesso giudizio che ha appena
prodotto **una riga su 15 turni** su una conversazione fittissima di materiale personale. Stesso
modello, stesso momento, stessa fretta: sbaglierebbe alla stessa frequenza, e sui fatti che
contano di piu'.

Il giudizio va spostato **dove costa poco sbagliare**: una passata notturna, che vede la
conversazione intera invece di un messaggio alla volta, ha gia' il blocco dei duplicati, ha gia'
un review pass sopra, e se sbaglia costa una notte invece del turno dell'utente.

## I cambi

### 0 — il tetto di `USER.md`, che va prima di tutto il resto

> **Chiusa l'08/09/2026: il tetto e' 4.000**, nello schema e sul telefono (scritto via
> `/api/settings/memory/update`, cioe' dal write funnel dell'app: etichette SELinux intatte,
> `.bak` allineato). Resta un residuo dichiarato: 2.466 + 1.749 = 4.215, cioe' **215 caratteri
> oltre il tetto** se tutti i fatti misurati atterrassero. Non e' il muro di prima — 1.215 di
> sforamento contro un file gia' pieno — ed e' la quantita' di potatura che il review pass fa di
> mestiere. Va guardato: se `USER.md` resta appiccicato al tetto per piu' di qualche giro, il
> numero e' sbagliato e non il meccanismo.

**Questa fase e' bloccante e non e' un dettaglio di budget.** L'estrazione provata (v. sotto)
produce **1.749 caratteri** di voci nuove da 72 righe di journal. `USER.md` sta a **2.466
caratteri su un tetto di 3.000** (`dream.userBudgetChars`): la corsia lo porterebbe a **4.215,
il 140% del tetto**, cioe' **1.215 caratteri di sforamento la prima notte**.

Quel che succede allo sforamento e' il review pass, e del review pass si sa gia' che *«una
passata forzata e' esemplare, la seconda si mangia i fatti personali»*. Aprire la corsia sopra un
file gia' pieno vuol dire consegnare venti fatti nuovi al meccanismo noto per buttarne.

Le tre strade, in ordine di preferenza:

1. **Alzare il tetto, con la misura davanti.** Il tetto e' un numero misurato, non sacro (v. il
   commento in `config/schema.py`), e si paga in *ogni* prompt di *ogni* sessione: alzarlo da
   3.000 a 4.500 costa ~375 token per turno ovunque. Va misurato prima di deciderlo, e va deciso
   **sapendo** cosa compra: e' il prezzo dei fatti che oggi non ci sono.
2. **Potare prima, non dopo.** Far girare una passata di review *prima* di aprire la corsia, per
   arrivare all'apertura con margine invece che in sforamento. Non alternativo a 1: complementare.
3. **Accettare lo sforamento e lasciar fare al review pass.** Sconsigliata: e' la strada che la
   misura dice che perde roba.

**Finche' questa fase non e' chiusa, A non si apre.** Aprirla prima non e' «un po' di pressione
sul budget»: e' scambiare i fatti nuovi con quelli vecchi, cioe' esattamente niente.

### A — la corsia di diario per i progetti

`append_history` smette di rifiutare una chiave `project:` e scrive la voce **con la sua
chiave**. Un cancello solo da togliere, e la sua docstring da riscrivere: l'isolamento smette di
essere «un'assenza» e diventa **«una chiave piu' una destinazione»**.

**Il verso di lettura e' gia' chiuso, e questa e' la ragione per cui A e' piccola.**
`read_recent_history_for_prompt` esclude gia' oggi una voce `project:` da **ogni** prompt: non e'
la sua sessione, e non e' personale. Quindi la corsia non inquina nessuna chat, ne' quella
personale ne' un altro progetto. **Va pero' provato con un test e non assunto**: fino a ieri
quella riga difendeva una condizione che non poteva verificarsi, e da domani e' l'unica cosa fra
la corsia e ogni prompt dell'installazione.

`.agent/security.md` dichiara oggi il contrario, e va riscritto nello stesso commit: una regola
di sicurezza che descrive il codice di ieri e' peggio di nessuna regola.

### B — l'estrazione ristretta, e la destinazione unica

Due meta', e la seconda e' quella che regge.

**Il prompt** (`agent/dream_project.md`, nuovo): Dream, sulle voci `project:`, non usa il proprio
prompt ma quello provato in questo piano. Le quattro regole che lo fanno funzionare, nell'ordine
in cui sono state misurate:

1. **una domanda sola** — «sarebbe ancora vero, e varrebbe ancora la pena saperlo, se il progetto
   fosse cancellato domani?»;
2. **attuale non e' passeggero** — una cura in corso, una misura, un obiettivo, un'abitudine
   appena presa o appena lasciata sono fatti, e sono i piu' utili; l'umore di un giorno no;
3. **togli alla frase i vestiti del progetto** e guarda cosa resta in piedi;
4. **una frase porta piu' di un fatto** — copri la principale con la mano e guarda cosa nomina il
   resto; piu' il divieto di aggiungere tratti che nessuno ha detto, e la regola che **un'ipotesi
   non e' una diagnosi** (un sospetto si registra col suo stato o non si registra).

**La destinazione, che e' meccanica e non una preghiera nel prompt**: una voce che nasce da una
chiave `project:` puo' essere scritta **solo in `USER.md`**. `MEMORY.md` e' inventario — «dove
altro lavori» — ed e' esattamente la classe che non deve viaggiare; per di piu' non viene nemmeno
iniettato nelle sessioni di progetto (24/08), quindi sarebbe pagato da tutti e letto da uno.
`MemoryEntryTool` ha gia' le due destinazioni come dizionario (`MEMORY_TARGETS`): qui si passa
l'insieme ammesso e si **rifiuta** l'altra, con log. Un rifiuto si prova con un test; una regola
nel prompt no.

### C — la cattura di progetto smette di prendersi i fatti personali

Indipendente da A e da B, **e va fatta comunque**. `agent/project.md` dice «se sara' ancora vero
la settimana prossima, scrivilo prima di rispondere» e non dice mai *sul progetto*. E' cosi' che
un fatto sulla famiglia di chi parla diventa una riga di `raw/journal/`, e poi una pagina della
wiki sbagliata: il fatto non si perde soltanto, si **archivia male**.

Una riga in piu' nella regola di cattura: un fatto che riguarda la persona e non il progetto **non
va nel journal**. Senza questa, B non sposta niente — aggiunge una seconda copia accanto alla
prima, che resta dov'e'.

### D — la consolidazione dei progetti deve girare anche a tempo

> **Chiusa l'08/09/2026, e non con la manopola.** `AutoCompact._harvest_project_diary` legge i
> messaggi nuovi di un progetto scaduto, ne mette un riassunto nella coda e **non tocca la
> sessione**. La manopola resta spenta: la misura che ha deciso e' che **3 sessioni di progetto
> su 9** erano mai state consolidate — fra le sei escluse quelle da 54, 64, 78 e 80 messaggi —
> quindi appoggiarsi alla compattazione avrebbe lasciato la corsia vuota proprio sulle
> conversazioni piu' ricche. E accendere la manopola per ottenere un riassunto avrebbe pagato con
> l'unica cosa che una sessione di progetto ha in piu' della sua cartella.

Oggi una conversazione di progetto viene riassunta **solo per lunghezza**
(`maybe_consolidate_by_tokens`); l'archiviazione per inattivita' la salta, e la manopola che la
accende (`compact_projects_when_idle`) e' spenta di default. Senza questa fase una chat di
progetto **corta** — cioe' proprio quella dove si dice una cosa e si cambia argomento — non
produce mai la voce che A trasporta.

Va coordinata con `_pages_carry_the_project`, il secondo cancello che rimanda la compattazione
finche' il giardiniere non ha promosso il journal. Quel cancello difende il contenuto del
progetto e resta giusto; quel che va deciso e' se la **corsia di diario** debba aspettarlo, e la
risposta e' no: sono due depositi diversi con due orologi diversi, e legare il diario al
giardinaggio di un altro deposito e' la corsa fra orologi che quel metodo esiste per evitare.

### E — la varianza, che e' il problema aperto vero

Misurato: sul batch **ambiguo** — quello dove ogni fatto e' vestito da progetto — quattro
esecuzioni identiche hanno dato **sottoinsiemi diversi**: 1, 2, 3 e 2 fatti su 4, **intersezione
vuota, unione completa**. Sul batch non ambiguo, invece, 10 su 10 stabile a ogni giro.

Oggi Dream consuma la coda **una volta sola** e avanza il cursore. Su materiale ambiguo questo
vuol dire perdere ogni notte una meta' diversa, per sempre. Due strade:

1. **finestra sovrapposta** per le sole voci `project:` — il cursore arretra di N voci, cosi' la
   stessa materia viene vista piu' volte e il blocco dei duplicati impedisce la doppia scrittura.
   Costa token e niente altro, ed e' misurabile: si sceglie N guardando la curva 1→4 passate;
2. **accettare** che il fatto entri la seconda volta che l'utente ne parla.

Proposta: (1) con N piccolo, perche' il costo e' noto e piccolo (~2.400 token per progetto per
passata) mentre la perdita di (2) non e' osservabile — nessuno si accorge di un fatto che non e'
mai stato scritto.

> **Chiusa l'08/09/2026 con N = 3, e nella forma che non puo' stallare.** Non un cursore che
> arretra — arretrare di N su un batch di N o meno vuol dire non avanzare mai, cioe' livelock
> silenzioso — ma una **finestra di rilettura**: le ultime tre voci di progetto gia' consumate
> tornano nel prompt come contesto, sotto un'intestazione che dice cosa sono, dentro un batch che
> resta quello nuovo. Il cursore avanza sempre. La deduplica non e' nuova: un fatto gia' atterrato
> viene riproposto e `memory add` risponde "already present" senza scrivere.

## Cosa questo piano non fa, e non per dimenticanza

- **Non tocca il verso in uscita.** `SOUL.md` e `USER.md` continuano ad arrivare a ogni tipo di
  sessione dalla radice dell'installazione. La cautela di `_load_bootstrap_files` resta valida.
- **Non allarga la lettura del giardiniere**, ne' gli da' il puntatore a `MEMORY.md`. La sua
  cassetta rifiuta quei path per scelta di qualcuno (T4.5), e questo piano non e' l'occasione per
  disfarla di straforo.
- **Non rimette `MEMORY.md` dentro i progetti.** La misura del 24/08 regge e non e' contestata qui.
- **Non introduce nessuna superficie utente**: niente interruttore, niente chip, niente da sapere.
  Quel che l'utente puo' toccare e' il **risultato** — le voci di `USER.md`, leggibili e
  correggibili dal browser dei file — non la configurazione.

## Verifica

> ### Esito, misurato sul Titan 2 l'08/09/2026
>
> Build di release installata, tetto portato a 4.000 dal write funnel dell'app.
>
> - **Fase C, su un turno vero.** Un messaggio che portava insieme una regola di wiki e un fatto
>   sulla persona: nel journal del progetto e' finita **solo la regola**. Prima il fatto sarebbe
>   diventato una riga di diario e poi una pagina della wiki sbagliata.
> - **Fase D, al primo riavvio.** La raccolta e' scattata su tutti e nove i progetti — 110, 80,
>   78, 64, 61, 54, 12 e 2 messaggi letti — e i nove riassunti sono nella coda, **uno per
>   progetto, ognuno con la propria chiave**. Contati dopo: **nessuna sessione ha perso un
>   messaggio**, e ognuna porta l'indice di raccolta pari al proprio totale.
> - **Il verso di lettura, sui dati veri.** Il `history.jsonl` del telefono (165 voci, 9 di
>   progetto) passato a `read_recent_history_for_prompt` per tutti e quattro i rami: **0 voci di
>   progetto** in ognuno.
> - **L'omogeneita' del batch, senza averla cercata.** Oltre il cursore c'era una voce personale
>   seguita da nove di progetto: il primo `/dream` ha avanzato di **uno** e si e' fermato al cambio
>   di tipo, il secondo ha preso i nove. Il prezzo previsto — un run in piu' — pagato e visibile.
> - **La destinazione.** Attraverso due run di Dream su materiale di progetto, `memory/MEMORY.md`
>   e' rimasto **byte-identico**. `USER.md` e' passato da 2.466 a 3.612 caratteri (90% del tetto)
>   con sette voci nuove, tutte della classe giusta — e l'ipotesi clinica registrata **con il suo
>   stato** invece che come diagnosi, che era la regola aggiunta per ultima.
> - **La prova controllata.** Il messaggio di prova conteneva due regole di wiki e un fatto sulla
>   persona: il fatto e' in `USER.md`, le due regole non sono in nessuno dei due file.
> - **Nessuna voce preesistente persa**: 20 su 20 ancora li', una *sostituita* da una versione piu'
>   completa, e le voci uscite archiviate in `memory/archive/`.
>
> Restano da guardare sul campo: il conto pieno sui 24 fatti di riferimento dopo qualche notte, e
> se `USER.md` resta appiccicato al 90% (nel qual caso e' il numero a essere sbagliato, non il
> meccanismo).

**Test (bloccanti, prima di aprire A):**

- una voce con chiave `project:` non compare in **nessuno** dei quattro rami di
  `read_recent_history_for_prompt`: personale, interna, giardiniere, altro progetto;
- una scrittura di voce originata da una chiave `project:` verso `memory` viene **rifiutata**, e
  la stessa verso `user` passa;
- `build_dream_prompt` costruisce il prompt di progetto sulle voci `project:` e quello normale
  sulle personali, senza mescolarle nello stesso batch;
- il budget: con `USER.md` al tetto, una voce nuova non silenziosamente sparisce.

**Sul telefono, e va rigirata la stessa misura di partenza** (e' il solo modo di sapere se ha
funzionato, e i dati di riferimento sono gia' presi):

- dopo N notti, quanti dei 24 fatti di riferimento sono in `USER.md`;
- **il controllo negativo**: `redazione` non deve aver prodotto **nessuna** voce. Se ne produce
  una, il piano ha fallito anche se il resto ha funzionato;
- `USER.md` sta sotto il tetto deciso in fase 0, e le voci che c'erano prima ci sono ancora.

## I numeri di riferimento della prova del prompt

Quattro versioni, sei batch di materiale vero, modello Haiku (il pavimento della scala):

| | recuperati su 24 | falsi positivi |
| --- | ---: | ---: |
| v1 | 11 — 45% | 0 |
| v2 (attuale≠passeggero, togli la cornice) | 20 — 83% | 0 |
| v3 (+ una frase porta piu' fatti) | 21 — 87% | 0 |
| v3, unione di 4 passate | 23 — 95% | 0 |
| v4 (+ un'ipotesi non e' una diagnosi) | come v3, senza la riga che trasformava un sospetto in una diagnosi | 0 |

**Zero falsi positivi in ogni versione**, e il controllo negativo — 15 righe fitte di decisioni di
lavoro — ha risposto `NESSUNO` in 4 esecuzioni su 4. Costo: ~2.400 token per progetto per
passata, ~14.000 per una notte su sei progetti.

Resta fuori un fatto su 24, e la ragione e' nota: compare dentro una frase che serve a **negarlo**
come opzione, e la negazione se lo porta via. Caso singolo: non vale una quinta regola.

**Due avvertenze sull'onestà del numero.** Haiku e' il pavimento, quindi 87% e' un minimo. Ma il
materiale erano i *journal*, gia' condensati e in forma di fatto: nella pipeline vera arriva il
riassunto grezzo della conversazione, piu' sporco. Il test e' pessimista sul modello e ottimista
sull'input, e i due errori non si annullano — vanno rimisurati sul campo (v. Verifica).
