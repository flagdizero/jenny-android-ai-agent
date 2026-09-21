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

---

## Mani e Memoria — e la differenza più grossa di tutte

Fotografati entrambi (`hands-intero.png` 1436×4096, `memory-intero.png`
1436×6467). A–E valgono identiche anche qui. Ma il confronto ne fa emergere
**tre di struttura**, più grosse di tutte quelle già elencate.

### H. La tavola riassume dove il cassetto elenca

È la differenza che spiega perché Memoria è **alta 6 467 px**: quasi due terzi
sono l'elenco delle istantanee, una riga per ognuna.

| | la tavola | il cassetto |
| --- | --- | --- |
| storia locale | **una riga**: «N istantanee · la più vecchia 6 g fa» | menù «conserva per», bottone «crea adesso», **e l'elenco intero** |
| quando agisce da sola | righe compatte: nome · di chi è · quando | schede alte con «Next:» / «Last:» / pallino, più un bottone «aggiorna» e la riga «As of …» |
| canali | **una riga**: «Telegram collegato · @…» | scheda intera con interruttore, riquadro di accoppiamento e bottone «disaccoppia» |
| ssh | toggle, una riga di aiuto, **una riga per host** | toggle, aiuto lungo, scheda con targhetta, bottone «verifica impronta», matita e cestino |

Il criterio della tavola è coerente: **in cassetto ci sta quel che si legge, e
l'amministrazione è dietro un tocco.** Non è una scelta grafica — è quel che fa
la differenza fra una pagina che si scorre in tre gesti e una che ne chiede
venti.

### I. Le porte spariscono, e diventano righe dentro il gruppo giusto

Nelle tavole di Mani e Memoria **non c'è niente prima del primo gruppo**: nessuna
riga «Cassetto delle app», nessuna «Workspace», nessuna «Wiki».

Quelle destinazioni ci sono lo stesso, ma **dentro il gruppo a cui
appartengono**, come riga di riepilogo con una freccina:

- «Jenny Apps · 4, una nascosta» sta dentro *canali e abilità*;
- «Skill · 3 tue, 4 integrate» pure;
- i file del workspace sono il gruppo *i file veri*, che è l'elenco delle
  cartelle, non un rimando.

Il cassetto invece le mette come righe-porta in cima, staccate dal loro
argomento.

### J. Prima si legge, poi si modifica

In «quanto ricorda» la tavola mostra i tre file con le loro barre e **un solo
bottone, «Cambia i tetti»**. I campi numerici non sono a schermo.

Il cassetto mostra **tre campi numerici modificabili** subito, con la misura
sotto ognuno.

Stesso principio di H, applicato dentro un gruppo: la domanda «quanto ricorda» ha
una risposta da leggere; cambiarla è un'altra cosa e sta dietro un tocco.

### E le due voci che restano fuori

- **Mani / «permessi di scrittura»** — tre interruttori per ambito più la riga
  `/ro`. Funzione assente, già deciso di rimandarla.
- **Memoria / «i file veri»** — l'elenco delle cartelle del workspace. Richiede
  una lettura che questa schermata non fa.

### Cose del cassetto che la tavola non ha

Oltre a quelle di Cervello: la targhetta e il bottone dell'impronta SSH, il
riquadro di accoppiamento Telegram, il bottone «aggiorna» e la riga «As of …»
sotto i lavori, il menù «conserva per» e «crea istantanea adesso».

Non vanno **tolte**: vanno dietro il tocco che apre la riga di riepilogo.

---

## L'ordine consigliato

**Misura di partenza (21/09/2026, dalle foto intere):** Cervello ≈ 5 000 px,
Mani 4 096 px, Memoria 6 467 px. La tavola disegna pagine da 1 120–1 180 px.
Il divario non è di stile: è di **quanto c'è a schermo**.

### Primo giro — quel che vale per tutti e tre

1. **A — righe a due colonne** (`_field`, `_select`): etichetta a sinistra,
   controllo a destra. La differenza più ripetuta dei tre cassetti, e si tocca
   in un punto solo.
2. **E — i valori di macchina in monospazio.** Il token esiste già
   (`--font-mono`): è questione di dove applicarlo.
3. **H — riassumere invece di elencare.** Il più redditizio: la storia locale
   diventa una riga, i lavori diventano righe compatte, Telegram una riga. Da
   solo dimezza Memoria.

### Secondo giro — struttura

4. **I — le porte entrano nel loro gruppo** e smettono di stare in cima.
5. **J — «quanto ricorda» si legge**, e i tetti vanno dietro «Cambia i tetti».
6. **C — le marche diventano righe compatte** (pallino, pastiglia «risponde»,
   chevron) invece di schede alte.
7. **B — comando a segmenti** per «tenere sveglia la CPU»: il componente esiste.
8. **D — bottone principale pieno** + la riga che spiega cosa comporta.

### Terzo giro — dettagli

9. **F.2 — «Finestra di contesto»**: esporre un controllo che il config ha già
   e nessuna schermata mostra.
10. **F.3 — la pastiglia di stato** nell'intestazione.
11. **F.4 — le righe di rimando in fondo** («…sta in Memoria»).
12. **G — il superfluo**: via l'icona «aggiorna», e «CURRENT STATE» diventa
    interruttori invece di referto.

### Fuori da questo piano

- **La barra «contesto in uso»** (Cervello) e **la striscia dei numeri**
  (Console): la stessa cosa mancante nel protocollo, da decidere insieme.
- **«Permessi di scrittura»** e **«i file veri»**: due funzioni che non esistono.
- **`personalization` e `system`**: i due parcheggi, in attesa di una casa.

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende.

---

# Il piano preciso

L'elenco qui sopra è la **diagnosi**. Questa è la ricetta: cosa si tocca, come si
sa che è finita, cosa può rompersi.

## Il buco che va chiuso prima: dove atterra il tocco

Tre dei dodici punti — **H** (riassumere invece di elencare), **I** (le porte
dentro il gruppo), **J** (prima si legge, poi si modifica) — dicono la stessa
cosa: *in cassetto ci sta il riepilogo, il dettaglio sta dietro un tocco*.

**Ma la tavola non disegna mai dove quel tocco atterra.** L'officina ha quattro
tavole — Console, Cervello, Mani, Memoria — e nessuna per l'elenco delle
istantanee, per l'accoppiamento Telegram, per la scheda di un host SSH.

**Decisione presa qui, e dichiarata:** il dettaglio si apre in un **pannello che
sale dal fondo**, non in una vista nuova. Tre motivi:

1. Il meccanismo **esiste già e gira**: `assets/mobile-drawer.js`
   (`DrawerManager` — fondale, trascinamento, ritorno del fuoco), con quindici
   pannelli registrati in `officina.html`, fra cui `drawer-audit` e
   `drawer-files`.
2. Un pannello **non consuma una voce del dock**, e il dock è chiuso a quattro.
3. La freccina della tavola (`›` a fine riga) legge come «si apre qualcosa
   sopra», non come «cambia pagina».

Se questa non è l'intenzione, **cambia solo questo paragrafo** e i tre punti si
riscrivono; tutto il resto del piano regge lo stesso.

## Primo giro — vale per tutti e tre i cassetti

### 1. Righe a due colonne

- **Dove:** `_field` (`mobile-settings.js:1894`), `_select` (`:1902`),
  `_numberField` (`:854`); `.settings-field` / `.settings-label` /
  `.settings-input` nel foglio di stile.
- **Cosa:** etichetta a sinistra che si restringe, controllo a destra di
  larghezza fissa (92 px un numero, 150 px un menù), riga alta almeno 44 px.
  `_toggleRow` (`:841`) è **già** a due colonne: diventa la forma di riferimento.
- **Si sa che è finita quando:** nessun `.settings-label` è più largo della sua
  riga meno il controllo, e su 590 px di larghezza nessuna etichetta va a capo.
- **Rischio:** `_field`/`_select` li usano anche onboarding e casa. Il banco deve
  guardare **anche** quelle due, o si allinea l'officina e si storce il resto.

### 2. I valori di macchina in monospazio

- **Dove:** `.model-inuse-name`, `.provider-url`, `.provider-key`,
  `.settings-input[type=number]`, le misure sotto le barre dei tetti.
- **Cosa:** `font-family: var(--font-mono)`. Il token esiste in tutti e sette i
  temi.
- **Si sa che è finita quando:** nome del modello, endpoint, chiave, numeri e
  orari sono in monospazio; le frasi umane no.
- **Rischio:** nessuno. È solo tipografia.

### 3. Riassumere invece di elencare — il punto più redditizio

Tre posti, stessa forma: **una riga di riepilogo con la freccina**, e il dettaglio
nel pannello.

| dove | la riga che resta | cosa va nel pannello |
| --- | --- | --- |
| `_renderBackup` (`:1703`) | «N istantanee · la più vecchia *quando*» | l'elenco, «conserva per», «crea adesso» |
| `_renderTelegram` (`:698`) | «Telegram: collegato · *@nome*» oppure «non collegato» | interruttore, accoppiamento, «disaccoppia» |
| `_renderSsh` (`:1112`) | «N macchine registrate» + una riga per host | impronta, «verifica», modifica, elimina |

- **Si sa che è finita quando:** Memoria scende sotto i 3 000 px (oggi 6 467) e
  Mani sotto i 2 500 (oggi 4 096), misurati con lo stesso `cuci.py`.
- **Rischio:** i caricatori asincroni scrivono nel proprio segnaposto
  (`_loadSnapshotList`, `_loadSsh`, `_loadCron`). Se il segnaposto si sposta nel
  pannello, **devono cercarlo lì** — e un pannello chiuso non ha il nodo: la
  scrittura va fatta all'apertura, non al caricamento.

## Secondo giro — struttura

### 4. Le porte entrano nel loro gruppo

- **Dove:** `_renderPorte` (`:375`), `CASSETTI[*].porte`.
- **Cosa:** `porte` sparisce come blocco in cima; ogni destinazione diventa una
  riga dentro il gruppo che la riguarda — le app dentro i canali, il workspace
  dentro Memoria.
- **Attenzione:** `workspace` e `graph` sono **viste intere**, non pannelli:
  restano cambi di vista, si sposta solo la riga che ci porta.

### 5. «Quanto ricorda» si legge

- **Dove:** `_renderQuantoRicorda` (`:889`), `_renderBudget` (`:916`).
- **Cosa:** restano i tre file con barra e misura; i tre campi numerici vanno nel
  pannello dietro «Cambia i tetti».
- **Da aggiungere:** la frase che la tavola ha e il cassetto no — **quanti
  caratteri restano**, e il confronto con la casella delle regole della casa.
  I numeri ci sono già tutti nel payload.

### 6. Le marche diventano righe

- **Dove:** `_renderProviderListHtml` (`:771`).
- **Cosa:** da scheda alta a riga da 52 px: pallino della marca · nome in
  monospazio · pastiglia «risponde» sull'attiva · endpoint e chiave mascherata ·
  freccina. Modifica ed elimina vanno nel pannello che la freccina apre.
- **Nota:** il pallino vuole un colore per marca — una tabella nome→colore, con
  un colore neutro per le marche che non conosce.

### 7. Comando a segmenti per «tenere sveglia la CPU»

- **Dove:** `_renderKeepAwake` (`:461`).
- **Cosa:** da `<select>` a `.settings-seg` / `.settings-seg-btn`, che
  **esistono** (li usano taglia della mascotte e lingua).
- **Il criterio è già nel codice** (`:1651`): a segmenti quando le voci stanno in
  riga, a tendina quando no. Tre voci ci stanno.

### 8. Bottone principale pieno

- **Dove:** `.settings-btn-add`.
- **Cosa:** pastiglia piena d'accento a tutta larghezza, più la riga che spiega
  cosa comporta aggiungere una marca.
- **Attenzione al contrasto:** su Chanel e Fumetto `--accent` **è** il colore del
  testo; il testo del bottone va su `--on-accent`, che esiste apposta.

## Terzo giro — dettagli

9. **«Finestra di contesto»** (`context_window_tokens`): esiste nello schema e nel
   payload, non lo espone nessuno. Una riga in «Parametri».
10. **Pastiglia di stato** nell'intestazione (`mobile-header.js`, `cassetto()`).
11. **Righe di rimando in fondo** ai gruppi («…sta in Memoria»).
12. **Il superfluo:** via l'icona «aggiorna»; «CURRENT STATE» da referto a
    interruttori.

## Come si misura la fine

Non «sembra uguale», ma tre numeri e un banco:

1. **Altezza** dei tre cassetti con `cuci.py`, contro i 1 120–1 180 px delle
   tavole. Oggi: 5 000 / 4 096 / 6 467.
2. **Inventario dei controlli** per gruppo — quanti interruttori, numeri, menù,
   segmenti — confrontato con quello estratto dai `.dc.html`. Sono già stati
   estratti una volta per questo documento: l'estrazione diventa il banco.
3. **Nessuna regressione** su casa e onboarding, che condividono `_field` e
   `_select`.

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende.
