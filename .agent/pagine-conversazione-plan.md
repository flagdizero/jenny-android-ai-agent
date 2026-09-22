# Pagine conversazione — piano

> «Come your home pages devo poter mettere anche le chat quaderni. In quel caso
> è solo uno shortcut per switchare chat ma comunque deve fare effettino di
> swipe (mascherato all'utente)» — l'utente, 23/09/2026.

Riapre il passo B5.3 di `scorrimento-e-schermate-plan.md`, tagliato il 22/09 con
«non supportiamo chat come schermate extra». Il taglio era giusto per come la
domanda era posta allora — *una seconda chat viva* — e non vale per questa, che
è un'altra cosa: **una scorciatoia che cambia conversazione, travestita da
pagina**. Non c'è una seconda chat. Ce n'è una sola, che si sposta.

---

## Parte 0 — I fatti, letti nel codice il 23/09/2026

1. **Cambiare conversazione esiste già e regge.** `casa-app.js:381`
   `switchConversation(key)`: tiene la bozza per conversazione, alza la
   generazione (due cambi ravvicinati: perde sempre il primo), rilegge il filo,
   scarica lo stato del turno in volo (`chat:switch` → `_releaseTurn`). Non va
   riscritto: va *chiamato al momento giusto*.
2. **Il cambio lascia un vuoto visibile.** `casa-chat.js:485` `reload()` toglie
   tutti i messaggi e *poi* aspetta la rete. Oggi quel vuoto si vede sotto il
   titolo; con uno scorrimento si vedrebbe **dopo** che la pagina è entrata. È
   la prima cosa che la maschera deve coprire.
3. **La chat è un elemento solo, pieno di id.** `index.html:124-190`: il
   pannello `data-pagina="chat"` contiene filo, vuoto, attività, filo di rete,
   allegati, composer — `#casa-thread`, `#casa-empty`, … presi per id dai
   controller alla costruzione. **Non si duplica. Si sposta**, come già le
   stanze prestate (`prestaStanza`, `casa-app.js:611`), che sul telefono
   tornano a casa intere (verificato il 22/09).
4. **L'intestazione sta fuori dalla pista.** Non scorre: cambia testo
   all'arrivo (`onPaginaCambiata` → `_applyHead`, `casa-app.js:626/700`). Così
   già oggi per app e stanze.
5. **La casa parte sempre dalla conversazione personale.** Il session manager
   non ricorda niente fra due avvii (`session-manager.js:17`).
6. **Lo schema l'aveva già previsto.** `schema.py:521`: `ref` è «lo slug
   dell'app, il nome della stanza, **o la chiave di sessione**». E il commento
   di `STANZE` in `casa-pagine.js` lo dice a parole: chi vuole un quaderno a
   portata di pollice «ci mette la sua conversazione».
7. **Un quaderno cancellato non si ricrea.** Il gateway rifiuta già il turno di
   una conversazione la cui cartella manca, e sa inseguire un rinomino
   (`loop.py:775`). Una pagina fissata su un quaderno sparito non fa danni: al
   massimo mostra una storia a cui non si può più rispondere.
8. **Il gesto non va toccato.** Riconoscimento condiviso, righello dello
   schermo, pista: tutto già a posto (B0, B7, B8).

---

## Il disegno

### Il trucco, in una frase

Ogni pagina «di chat» — la pagina 0 e ogni pagina conversazione — ha **la sua
foto**. La chat vera sta sempre nella pagina che guardi; nelle altre c'è la
foto di com'era l'ultima volta che l'hai vista. Scorrendo, la pagina che entra
è una foto: sembra una chat, e lo è stata. All'arrivo la chat vera ci scivola
sotto, cambia conversazione, e quando ha finito di leggere la foto se ne va.

### Chi sta dove

- **La pagina 0 è la chat libera**: mostra la conversazione che scegli dal
  titolo, come oggi. Se ne ricorda una sua (`_conversazioneCasa`), che parte
  personale a ogni avvio (fatto 5).
- **Una pagina conversazione mostra sempre e solo il suo quaderno.** È
  l'invariante di tutto il piano, e ha un banco suo (C4).
- **Pagine app e stanza non toccano la chat.** Passarci attraverso non cambia
  conversazione: la chat resta dov'era, fuori schermo.

### La foto (la «maschera»)

- **Cos'è:** una copia statica del contenuto della chat — filo e composer —
  presa *un attimo prima* di cambiare conversazione. `inert`, `aria-hidden`,
  **senza nessun id** (vedi Trappole).
- **Dove vive:** in memoria, una per conversazione. Non sopravvive a un riavvio
  e non deve: è una comodità visiva, non uno stato.
- **Quanto pesa:** gli ultimi ~20 messaggi, non il filo intero. Le immagini
  sono gli stessi indirizzi già in cache.
- **La prima volta non c'è.** Un quaderno fissato e mai aperto da quando l'app
  è partita non ha una foto: la pagina entra come **chat vuota** (lo scheletro:
  composer e filo senza messaggi), e i messaggi compaiono subito dopo l'arrivo.
  Dalla seconda volta è senza cuciture. È l'unico punto in cui il trucco si
  vede, ed è una scelta (v. Decisioni).

### La sequenza di un arrivo

Sei sulla pagina 0 (personale), scorri verso la pagina 2 (quaderno «piante»).

1. **Durante il trascinamento** entra la foto di «piante», esce la chat vera.
   Due chat affiancate: l'effettino c'è già, e non costa niente.
2. **Al rilascio confermato**, *subito* e non a fine animazione:
   a. si fotografa la chat vera (personale) e la foto va nella pagina 0;
   b. la chat vera si sposta nella pagina 2, **sotto** la foto di «piante»
      (che le resta sopra, in `position: absolute`);
   c. `switchConversation('project:piante')` — la lettura di rete parte mentre
      la pista sta ancora scorrendo (220 ms guadagnati).
3. **Quando la lettura finisce** (o dopo un tetto, ~600 ms, se la rete tarda):
   la foto di «piante» se ne va e sotto c'è la chat vera, già piena.
4. **Tornando indietro** è lo stesso giro al contrario: la foto personale era
   già nella pagina 0 dal passo 2a.

Casi:

| da → a | cosa succede |
| --- | --- |
| chat → chat (conversazioni diverse) | il giro completo qui sopra |
| chat → app/stanza | niente: la chat resta dov'è, fuori schermo |
| app/stanza → chat | il giro completo, se quella pagina ha un'altra conversazione di quella che la chat mostra; altrimenti la chat si sposta e basta |
| stessa pagina, stessa conversazione | niente |

### L'intestazione

Su una pagina conversazione l'intestazione è **quella di una chat**, non di
una pagina: occhiello, nome del quaderno, pastiglia delle pagine del quaderno.
Niente «Torna alla chat» — *sei* in una chat. L'Indietro di Android riporta
comunque alla pagina 0 (`goBackOneRoom`, invariato). Il chevron del titolo
**non** apre la tendina: da una pagina fissa non si cambia conversazione (v.
Decisioni).

---

## I passi

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende.

### C0 — Misure (prima di scrivere)

- Quanto ci mette `reload()` sul telefono per un quaderno vero: dimensiona il
  tetto del passo 3. Si misura dagli screenshot a distanza fissa dopo un cambio
  dal titolo, non si stima.
- Che la chat vera **perda lo scroll** quando la si sposta nel DOM (atteso: sì,
  un contenitore staccato e riattaccato riparte da 0) — decide se basta il
  `keepBottom()` che `switchConversation` fa già dopo la lettura.

### C1 — Schema e rotte

- `SPECIE_SCHERMATA = ("app", "stanza", "conversazione")`.
- Validatore in `SchermataConfig`: per `conversazione`, `ref` deve essere
  `project:<nome>` con `<nome>` valido per `session/keys.py::is_valid_project_name`.
  Nello schema e non nella rotta, così un `config.json` scritto a mano non lo
  aggira — è la regola già usata per le specie.
- Solo quaderni, **non** la conversazione personale: l'utente ha detto «chat
  quaderni». La pagina 0 la contiene già.
- Banco: accettata; `ref` senza prefisso, con `..`, o col prefisso sbagliato →
  400; la rotta elenca `conversazione` fra le specie.

### C2 — Il foglio: scegliere un quaderno

- `casa-pagine.js:518`: la terza scelta, `conversazione`, icona `ti-notebook`.
- `_voci('conversazione')` (`:583`): `api.listProjects()` → solo `projects`,
  mai `unopenable` (aprirne uno aprirebbe *un'altra* conversazione — il guasto
  per cui quella divisione esiste); esclusi i quaderni **già fissati**.
- `nomeDi` (`:609`): il nome del quaderno, non la chiave (`projectNameOf`).
- Banco: l'elenco viene dalla fonte finta *dopo un giro* (la lezione di «non
  hai Jenny App»); un `unopenable` non compare; uno già fissato non compare;
  aggiungere scrive `{kind: 'conversazione', ref: 'project:piante'}`.

### C3 — La chat si sposta tutta intera

- `index.html`: filo, vuoto, attività, rete, allegati e composer dentro **un
  solo** involucro `#casa-chat`, così si sposta come un nodo. CSS: l'involucro
  prende le regole di flusso che oggi ha il pannello.
- `casa-app.js`: `portaLaChat(pannello)` accanto a `prestaStanza`, stesso
  principio — un elemento solo, spostato e mai copiato.
- Banco: dopo lo spostamento `getElementById('casa-thread')` è ancora il filo
  vero; le stanze prestate funzionano come prima; il pavimento del composer
  (`--casa-composer-h`) resta misurato.

### C4 — La foto e il giro dell'arrivo

- `casa-pagine.js`: `_riempi`/`_svuota` (`:193/:242`) imparano la terza specie
  — una pagina conversazione non si «svuota»: tiene la foto, o la chat.
- `casa-app.js`: la foto (`_fotografa(chiave)`), la sequenza 1–4, e
  `_conversazioneCasa` per la pagina 0.
- **L'invariante**: se qualunque strada chiama `switchConversation` verso
  un'altra conversazione mentre sei su una pagina fissa (oggi nessuna ci
  arriva — il chevron è spento — ma «Parlane», «Segnala» e «Nuovo quaderno»
  sono tre strade e la quarta arriverà), si va alla pagina 0 e il cambio lo fa
  lei.
- Banchi, ognuno con la sua mutazione:
  - arrivare su una pagina conversazione chiama `switchConversation` col suo
    `ref`; tornare alla pagina 0 rimette `_conversazioneCasa`; attraversare
    un'app non cambia niente;
  - la foto si prende **prima** del cambio (mutazione: dopo → la foto della
    pagina 0 mostrerebbe «piante»);
  - la foto non ha id (mutazione: clonare senza pulire → `getElementById`
    restituisce la copia inerte);
  - la foto se ne va quando la lettura finisce, **e** dopo il tetto se la
    lettura non finisce mai;
  - **il dito veloce**: A → B → A prima che la lettura di B finisca. Vince
    l'ultimo arrivo; il «fine lettura» di B non deve togliere la foto di A. È
    la stessa famiglia del doppio montaggio delle app (22/09) e si risolve
    allo stesso modo: un segno per tentativo;
  - l'invariante qui sopra.

### C5 — L'intestazione

- `_applyHead` (`:700`): `inChat` vale anche su una pagina conversazione;
  chevron spento lì; niente occhiello «Torna alla chat».
- Banco: su una pagina conversazione la pastiglia delle pagine del quaderno
  c'è, il chevron no, il titolo è il nome del quaderno.

### C6 — Il quaderno che non c'è più

- Nel foglio, la riga di un quaderno fissato che `listProjects` non restituisce
  più dice «non c'è più» e resta togliibile. La pagina resta visitabile: la
  sicurezza ce la mette già il gateway (fatto 7).

### C7 — Prova sul telefono

- Trascinamento lento pagina 0 → quaderno, screenshot a metà: **due chat
  affiancate**, quella che entra coi messaggi del quaderno (dopo averlo aperto
  una volta).
- Nessun lampo vuoto all'arrivo: screenshot ravvicinati dopo il rilascio.
- Indietro dal quaderno → pagina 0 personale.
- Un'app in mezzo: pagina 0 → app → quaderno → app → pagina 0.
- La bozza resta al suo quaderno (si scrive, si scorre, si torna, **si
  cancella**).
- **Non si manda nessun messaggio dal telefono dell'utente** senza chiederglielo:
  la risposta di Jenny finirebbe nel suo quaderno vero.

---

## Trappole note

- **Gli id nella foto.** Un `cloneNode(true)` della chat porta con sé
  `#casa-thread` & co. Se la foto sta nel DOM *prima* della chat vera,
  `getElementById` restituisce la foto e un controller comincia a scrivere
  dentro una copia inerte. Si puliscono `id`, `for`, `aria-*` che puntano a id.
- **Lo scroll della foto.** Un clone parte dall'alto; la chat sta in fondo. La
  foto va portata in fondo appena inserita, o entra mostrando i messaggi di
  una settimana fa.
- **Lo scroll della chat vera** dopo lo spostamento (C0).
- **Il composer della foto** mostrerebbe la bozza di chi l'ha scattata: va
  svuotato, o peggio mostra testo che non è di quella conversazione.
- **Il turno in volo.** Scorrere via mentre Jenny risponde è già gestito come
  un cambio dal titolo (`_releaseTurn`): la risposta si ritrova al ritorno.
  Ma scorrere è molto più *casuale* del titolo — si faranno avanti e indietro —
  e ogni arrivo è una lettura. Da tenere d'occhio in C7, non da risolvere
  prima.

## Cosa questo piano non fa

- Non fissa la **conversazione personale** come pagina (l'utente ha detto
  «chat quaderni»).
- Non rende vive due chat insieme: ce n'è sempre una.
- Non ricorda le foto fra due avvii.
- Non riordina le pagine (rimandato dall'utente il 22/09).

## Decisioni (prese dall'utente il 23/09/2026)

1. **La prima visita senza foto** entra come chat vuota e si riempie
   all'arrivo. Accettato: niente foto preparate all'avvio.
2. **Dal titolo della pagina 0, un quaderno che ha già la sua pagina si apre
   nella pagina 0, come oggi.** Niente scorrimento verso la sua pagina. Quindi
   lo stesso quaderno *può* stare in due posti — la pagina 0 e la sua — e il
   disegno lo regge: arrivare su una pagina la cui conversazione è già quella
   a schermo sposta la chat senza cambiarla.
