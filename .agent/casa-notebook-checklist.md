# La chat di un quaderno — cos'è atterrato

Piano: [`casa-notebook-plan.md`](./casa-notebook-plan.md). Giro precedente:
[`casa-who-checklist.md`](./casa-who-checklist.md).

## Fatto

- [x] **Il filo regge due conversazioni.** `bindInfiniteScroll()` si chiama una
      volta sola, dal costruttore (stava in fondo a `load()`, e il metodo non ha
      guardia: era un ascoltatore in più per ogni ricarica — invisibile finché
      una ricarica capitava solo dopo un `session_boundary`, uno per cambio da
      oggi). `reload()` chiama `pager.reset()` e azzera la bolla in sospeso.
- [x] **La forma della chiave sta in un posto solo.** `PROJECT_PREFIX`,
      `projectKey()` e `projectNameOf()` in `shared/conversation-list.js`, che è
      il modulo che tiene l'elenco di cui quella chiave è l'indirizzo.
      `ScopeChip.keyFor` ci si appoggia; la casa pure.
- [x] **`CasaApp.switchConversation(key)`**: parcheggia la bozza, cambia chiave,
      riprende la bozza di là, riscrive l'intestazione, rilegge il filo. Un
      fallimento di lettura si dice; una lettura riuscita ritira l'errore di
      prima.
- [x] **Il turno lasciato si chiude a mano**, da `chat:switch` (come
      `mobile-jenny._releaseTrackedTurn` in officina): riga di lavoro, faccia di
      Jenny e bottone Ferma.
- [x] **L'intestazione**: occhiello, nome, pallino (stesso `dotColor` della riga
      nel pannello), invito del campo, e il vuoto con parole sue. Il nome
      personale è messo da parte al primo avvio.
- [x] **Il pannello è un comando**: righe-bottone, spunta e `aria-current` sulla
      conversazione in cui sei, chiusura prima dello scambio. Le cartelle non
      apribili restano `<div>` inerti.
- [x] **Le vie di ritorno**: Indietro esce dal quaderno (dopo gli strati, una
      cosa per pressione), Home è la conversazione personale, il tocco su un
      avviso ci riporta perché è lì che quell'avviso è.
- [x] **Le parole**, in italiano e in inglese: `casa.kickerNotebook`,
      `casa.placeholderNotebook`, `casa.emptyNotebook`.
- [x] **I banchi**: 14 nuovi in `test_casa_switch_client.py`, il banco del
      pannello rovesciato dov'era da rovesciare, tre controlli di contratto
      nuovi. Suite intera 9803 passati / 7 saltati, `ruff` pulito.

## Le decisioni, e come sono finite

| | Deciso | Dov'è |
|---|---|---|
| D1 | Indietro e Home tornano alla personale | `casa-app.js::handleHardwareBack`, `goHome` |
| D2 | La bozza resta con la conversazione in cui l'hai scritta | `casa-app.js::switchConversation` |
| D3 | La porta dell'officina apre sulla personale — buco noto, scritto | commento in `_openInWorkshop` |
| D4 | Nessun ricordo fra un avvio e l'altro | niente da scrivere: è l'assenza |
| D5 | Il vuoto di un quaderno ha parole sue | `casa.emptyNotebook` |

## Cosa ha detto la prova

**Al banco finto** (i sei quaderni del rig, compreso un nome da 54 caratteri e
due cartelle non apribili): il titolo passa a «QUADERNO / ● piante», il filo
diventa quello del quaderno, la spunta si sposta, Indietro riporta a casa con la
bozza personale ancora nel campo, e rientrando nel quaderno torna la sua. Il
nome lungo tronca con l'ellissi e non spinge fuori la porta dell'officina — era
la ragione del `min-width: 0`. Provato su tema chiaro e su `kyoto` (scuro).

**Due cose che il piano non poteva prevedere:**

1. **Un ritaglio non porta con sé gli import di chi lo ospita.** Il banco
   dell'officina ritaglia `ScopeChip.keyFor` e lo esegue in node: dal momento in
   cui `keyFor` chiama `projectKey`, quel banco doveva importarlo a sua volta.
   Dodici rossi, tutti dello stesso guasto, tutti rumorosi. È la stessa lezione
   del giro scorso vista dall'altro verso: lì si ripuntavano i banchi al modulo
   nuovo, qui è il modulo vecchio che ha cominciato a dipenderne.
2. **Due mutazioni su venti sono passate verdi per colpa della mutazione, non
   del banco.** `this._showThreadError();` e `this._threadFailed = false;`
   esistono due volte nel file, e un `replace(..., 1)` ha colpito `init()` e il
   costruttore — che nel banco non ci sono. Rifatte ancorandole a una stringa
   unica (`'casa.switch'`), sono diventate rosse tutte e due. **Un verde in una
   campagna di mutazione va guardato due volte: la prima domanda non è «il banco
   è cieco?» ma «la mutazione è arrivata dove credo?».**

## Cosa resta fuori, di proposito

- Il numero delle pagine accanto al nome: nessun payload lo porta (D2 del giro
  scorso, ancora vera).
- «+ Nuovo quaderno»: crea cartelle, e vuole le sue tre schermate.
- Il pallino di non letto sul titolo. Dentro un quaderno, un avviso proattivo
  arriva alla conversazione personale e non si vede: la notifica di sistema c'è,
  ma in casa non c'è niente che lo dica. È il giro naturale dopo questo.
- La porta dell'officina che si porta dietro la chiave (`#chat=`): lavoro
  nell'altro guscio, che non legge ancora nemmeno il `#turn=`.
- La data di un quaderno resta l'mtime della cartella. Da oggi però la
  conversazione di un progetto esiste davvero, quindi una data sua è a portata.
