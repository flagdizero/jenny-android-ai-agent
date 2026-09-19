# «Con chi parli» — registro

Piano: [`casa-who-plan.md`](./casa-who-plan.md). Ramo: `feat/la-casa`.

## Passo 1 — L'estrazione ✅

- [x] `assets/shared/conversation-list.js`: la fetch, l'ordine (dal più recente,
      spareggio sul nome), la divisione apribili/non apribili, la cache che
      sopravvive a una lettura fallita, `ago()` e la mappa motivo→chiave.
      **Non importa niente** — né rete né traduzioni: la fetch arriva dal
      costruttore, il traduttore da chi chiama. È la forma di `wire-error.js`,
      e serve a far sì che un banco di prova eserciti *questo* codice.
- [x] `scope-chip.js` ci si appoggia: i quattro campi sono diventati accessori
      (in sola lettura; la cache si butta con `invalidate()`), `_loadProjects` e
      `_ago` delegano. Il resto del file non è stato toccato — era il punto.
- [x] Voce nel `_UI_MANIFEST`.

**Sei file di test hanno seguito il codice**, e non era prevedibile in quel
numero: al piano ne avevo contati due. Cinque banchi node ritagliano metodi
**dal testo** di `scope-chip.js` (`load_failure`, `unopenable`, `init`,
`delete`, `pin_and_create`) più il contratto che inchioda l'ordinamento.
Falliscono forte — `_member` è un assert, non un `if` — e ripuntarli ha voluto
dire importare il modulo vero nel banco invece di rifarne una sagoma.

**Rimutati dopo il ripuntamento**, che è la parte che conta: svuotare la cache
su un guasto → 5 rossi; togliere l'ordinamento → 2; buttare `unopenable` → 10.

## Passo 2-4 — Il pannello ✅

- [x] Il titolo è il comando: `<button>` **dentro** l'`<h1>` (un h1 dentro un
      button non è markup valido), con chevron, `aria-haspopup` e
      `aria-expanded`.
- [x] `assets/casa-who.js` (~210 righe): `<dialog>` + `showModal()`, quindi top
      layer — sopra la mascotte senza contenderle uno `z-index`. Nasce alla
      prima apertura, come il dialogo dei provider in officina: il guscio resta
      senza nodi inerti.
- [x] Tre vie d'uscita: velo, Esc, Indietro. L'ultima passa da
      `handleHardwareBack`, **prima** della lightbox.
- [x] Le due sezioni, i tre stati (sto leggendo / non ce n'è / non sono riuscita
      a leggere), le cartelle non apribili in fondo con una nota per motivo.
- [x] `casa.who.*` in `it`/`en`; date relative e note delle non apribili sono
      quelle che l'officina ha già.
- [x] Voce nel `_UI_MANIFEST`.

### Le decisioni, come sono finite

- **D1, righe inerti.** Nessun chevron, nessuna spunta, nessun listener: in
  questo giro un tocco non cambia conversazione, e la riga non lo promette. Un
  test lo inchioda, così il giorno che lo scambio arriva quel test si cambia di
  proposito.
- **D2, «N pagine» fuori.** Nessun payload lo porta.
- **D3, `<dialog>`.** Come proposto.
- **D4, pallino derivato dal nome.** `hsl(h, 38%, 58%)` e non un token del tema:
  un pallino `--error` su un quaderno sano si legge come un allarme.

### Misurato, non stimato

- **Il velo della tavola non si vede.** È `rgba(20,16,12,.28)`, tarato sulla sua
  tavolozza chiara; sui temi scuri sparisce e il pannello sembra galleggiare su
  uno schermo ancora vivo. Messo quello che l'app usa già per i suoi dialoghi e
  per la tendina laterale, `rgba(0,0,0,.55)`.
- **L'anello del fuoco stava sulla stanza invece che sul comando.** Un `<dialog>`
  modale prende il fuoco all'apertura e Chrome gli disegna attorno un anello
  bianco, che si legge come un bordo acceso. Spostato sul titolo, che è il
  comando.
- **Il `top` si misura** dall'intestazione a ogni apertura: l'altezza del titolo
  dipende dal font del tema, e tre temi su sette ne cambiano uno. Misurato 72 px
  su Chanel.
- **Esc non si riesce a provare da un'automazione**: un tasto sintetico non
  arriva alla chiusura del browser. Il `<dialog>` lo chiuderebbe da sé, ma una
  via d'uscita che *forse* funziona non è una via d'uscita: c'è anche un
  `keydown` nostro, e il secondo `close` è un no-op.
- Provato su tema scuro e chiaro: elenco, troncamento di un nome da 54
  caratteri, stato vuoto, lettura fallita (nota rossa in cima e le righe vecchie
  ancora sotto), Indietro.

### Un errore di processo, da non rifare

Per annullare una mutazione ho usato `git checkout <file>` su un file con lavoro
**non commesso**, e mi sono portato via l'aggancio del pannello in `casa-app.js`.
Le mutazioni si annullano con la copia di backup, mai con git, finché il passo
non è commesso.
