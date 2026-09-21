# Allineare i cassetti alla tavola, differenza per differenza

**Come è stato fatto il confronto.** Foto **intere** dei cassetti dal telefono
(non la sola schermata visibile): si scorre a passi piccoli, si scatta, e si
cuce misurando lo scorrimento vero invece di assumerlo. Script in
`scratchpad/cuci.py`. Due insidie pagate:

- **lo scorrimento non è l'ampiezza dello swipe** — Android ci mette inerzia e
  clamp: 650 px di dito danno fra 200 e 620 px di pagina;
- **la mascotte non scorre** — è un overlay fisso in basso a destra, e dentro la
  striscia di riconoscimento fa da àncora falsa. La prima cucitura ha **perso una
  schermata intera** fra «Le marche» e «Parametri» senza segnalare niente.
  Escludendo la sua colonna, tutte le giunzioni combaciano a scarto 0,00.

**Stato dello schermo al momento della foto:** tema `synthwave`, lingua inglese.
La tavola è `kyoto` e italiano. Le differenze di **colore e carattere** qui sotto
non sono quindi difetti: sono la conseguenza del tema scelto, e `kyoto` le
annulla da sé. Tutto il resto è forma, e quella non dipende dal tema.

---

## Cervello / Brain — confronto completo

### A. La forma delle righe: etichetta sopra, non accanto

**Tavola:** ogni riga è `etichetta a sinistra · controllo a destra`, alta 44 px,
con il controllo di larghezza fissa (92 px per un numero, 150 px per un menù).

**App:** `_field` e `_select` mettono l'etichetta **sopra** il campo, e il campo
occupa tutta la larghezza.

È la differenza che si ripete più volte, e non è solo di Cervello: «Parametri»,
«Ricerca web», i campi di Dream e del giardiniere sono tutti così. **Una sola
modifica a `_field`/`_select` allinea tre cassetti.**

### B. Il comando a tre scelte

**Tavola:** «Tenere sveglia la CPU» è un comando a segmenti — tre bottoni in
riga, quello attivo pieno.

**App:** è un menù a tendina.

**Il pezzo esiste già**: `settings-seg` / `settings-seg-btn`, usato per la taglia
della mascotte e per la lingua. È una sostituzione, non un componente nuovo. E il
criterio è già scritto nel codice (`mobile-settings.js:1651`): a segmenti quando
le voci stanno in riga, a tendina quando non ci stanno. Tre voci ci stanno.

### C. Le marche: schede grandi contro righe compatte

**Tavola:** una riga per marca, alta 52 px — pallino del colore della marca ·
nome in monospazio · pastiglia «risponde» su quella attiva · sotto
`openai_compat · opencode.ai/zen/v1` · a destra la chiave mascherata
`sk-k…hxBW` e un chevron.

**App:** una scheda grande per marca, con nome, URL e chiave su tre righe, e due
bottoni-icona (matita, cestino) in basso a destra.

Mancano: **il pallino**, **la pastiglia «risponde»**, **il chevron**. E la scheda
occupa circa il triplo dell'altezza della riga.

### D. Il bottone principale

**Tavola:** «Aggiungi una marca» è una **pastiglia piena d'accento**, a tutta
larghezza, seguita da una riga che spiega cosa comporta aggiungerne una.

**App:** è un bottone con solo il contorno, e la riga di spiegazione non c'è.

### E. I valori vanno in monospazio

**Tavola:** tutto ciò che è un valore di macchina — nome del modello, endpoint,
chiave, numeri, orari — è in Fira Code. Il testo umano è in Inter.

**App:** il nome del modello è in grassetto proporzionale, gli endpoint pure.

Il token c'è già (`--font-mono`): è una questione di dove applicarlo.

### F. Cose della tavola che nel cassetto non ci sono

1. **La barra «Contesto in uso»** (`17k / 65k · 36%`) dentro «chi pensa».
   ⚠️ **Stessa impossibilità della Console**: la stima del contesto non esiste nel
   protocollo. Non è disegno, è un cambio di protocollo.
2. **«Finestra di contesto»**, il menù 65 536 / 262 144.
   `context_window_tokens` **esiste nello schema e nel payload** ma **non è
   esposto da nessuna schermata** — né officina né casa (verificato 21/09/2026).
   Questo si può fare: è un controllo mancante, non un dato mancante.
3. **La pastiglia di stato** (`● idle`) nell'intestazione.
4. **La riga di rimando in fondo**: «Chi riempie la memoria — Dream e il
   giardiniere — sta accanto a quel che riempie, in Memoria».

### G. Cose del cassetto che nella tavola non ci sono

| nel cassetto | che farne |
| --- | --- |
| icona «aggiorna» nell'intestazione | la tavola non ce l'ha; le pagine si ricaricano da sole all'apertura |
| «CURRENT STATE» (esenzione, sveglie precise, CPU ora) come elenco di ✓/✗ | la tavola li fa **interruttori**, non referto |
| «RECORDED OUTAGES» | non c'è nella tavola |
| PERSONALIZATION, SYSTEM | i due parcheggi già noti: decisione aperta |

---

## Mani e Memoria

**Non ancora fotografati.** Il telefono si è scollegato a metà della raccolta
(`adb` non lo vede più). Lo script è pronto: appena torna, due comandi.

Dalla lettura delle tavole, le differenze attese sono **le stesse di A–E** — la
forma delle righe, i valori in monospazio, il bottone pieno — più:

- **Mani**: il gruppo «permessi di scrittura» (funzione assente, già deciso di
  rimandarlo) e la riga `/ro`.
- **Memoria**: «i file veri» (elenco delle cartelle, lettura che la schermata non
  fa) e, in «quanto ricorda», la frase che calcola **quanti caratteri restano**
  e li confronta con la casella delle regole della casa.

## L'ordine consigliato

Prima i tre cambiamenti **condivisi**, perché ognuno allinea tutti e tre i
cassetti in un colpo e riduce quel che resta:

1. **A — righe a due colonne** (`_field`, `_select`). La differenza più ripetuta.
2. **E — i valori in monospazio.** Poche righe, cambia molto la somiglianza.
3. **B — comando a segmenti per «tenere sveglia la CPU».** Componente esistente.

Poi i pezzi di Cervello:

4. **C — le marche diventano righe compatte** (pallino, pastiglia, chevron).
5. **D — bottone pieno + riga di spiegazione.**
6. **F.2 — «Finestra di contesto»**: esporre un controllo che il config ha già.
7. **F.4 — la riga di rimando a Memoria.**
8. **G — togliere il superfluo** (icona aggiorna) e trasformare «CURRENT STATE»
   in interruttori.

Infine, solo dopo aver fotografato gli altri due, i pezzi di Mani e Memoria.

**Fuori da questo piano** (cambio di protocollo, decisione dell'utente):
la barra del contesto in Cervello e la striscia dei numeri in Console — sono la
stessa cosa mancante, e vanno decise insieme.
