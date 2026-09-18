# La casa — registro

Piano: [`casa-plan.md`](./casa-plan.md). Ramo: `feat/la-casa`.

## Passo 1 — Il guscio ✅

- [x] `jenny/templates/ui/casa.html` — documento-guscio. Due librerie in tutto
      (marked, DOMPurify) contro le quindici dell'officina.
- [x] `assets/casa-app.js` — bootstrap, i18n, attach al websocket, e i metodi
      che il guscio nativo chiama. **Erano tre, sono cinque**: v. passo 5.
- [x] `assets/casa-style.css` — la colonna: filo che scorre, composer fermo,
      niente dock.
- [x] `assets/i18n/{it,en}.json` — sezione `casa`.
- [x] `_UI_MANIFEST` — le tre voci nuove. Verificato: 199 voci, nessuna senza
      file su disco.
- [x] CSP estesa ai due documenti (`_SHELL_DOCUMENTS` in `ws_http.py`), con test
      che fallisce se si torna al confronto col nome singolo.
- [x] `api.navigate(path)` — la strada fra i due gusci col segreto di bootstrap.

**Misurato**, a 590x566 (viewport vera del Titan 2): il filo prende 469,5 px e il
composer 64. Nell'officina gli stessi 566 px pagano dock e intestazione.

**Verificato:** `ruff` pulito, `pyright` sul sottoinsieme bloccante 0 errori,
**9698 test passati** / 7 saltati. Pagina caricata alla misura vera: tema,
lingua e i tre metodi del contratto a posto, nessun errore JS (i soli errori di
rete sono il gateway assente nel banco di prova).

**Da sapere per i passi dopo:** il banco di prova locale serve `jenny/templates/ui`
su `http://127.0.0.1:8777` sia sotto `/html-mobile/` sia sotto `/assets/`, perche'
`i18n.load` chiede `/assets/i18n/<lingua>.json` **senza** il prefisso — sul
gateway vero funziona, con un solo montaggio no.

## Passo 2 — La conversazione ✅

- [x] `assets/casa-chat.js` (392 righe, contro le 3.789 di `mobile-chat.js`).
      Storia dal transcript, frame dal vivo, risposta che si forma.
- [x] Etichette di provenienza: *da Telegram*, *dalla tendina*, *dal fumetto* —
      nomi per chi legge, non identificatori di canale come in officina.
- [x] Forma dal disegno (`Main.dc.html`): quel che dici tu e' una bolla a
      destra, quel che risponde Jenny e' testo sulla pagina.
- [x] Markdown con marked + DOMPurify, che fallisce **chiuso**: senza
      sanificatore si degrada a testo semplice, non si inietta HTML crudo.
- [x] Stato di lettura fallita distinto dallo stato vuoto: una conversazione
      vuota e una irraggiungibile non sono la stessa cosa.

**Le quattro regole del filo, verificate una per una dal vivo** (sono quelle che
`mobile-chat.js` ha imparato sbagliando, e descrivono il protocollo, non il
disegno):

| Regola | Prova |
|---|---|
| `stream_end` puo' arrivare senza testo, vale il buffer | il primo segmento sopravvive |
| un `message` apre un blocco suo e lo chiude | l'avviso non viene sovrascritto dalla narrazione dopo |
| piu' segmenti nello stesso turno non si incollano | due blocchi distinti, non "…segmento.Secondo…" |
| un messaggio senza `turn_id` non entra nel turno prima | due avvisi restano due |

In piu': un `turn_end` di un *altro* turno non chiude quello aperto, e un
`tool_hint` non produce niente.

**Due difetti trovati e corretti guardando la pagina**, non leggendola:

1. La riga di traccia (`read_file: sensori.json`) finiva nella risposta. La
   condizione "tienila solo se non c'e' altro testo" **non e' valutabile** dove
   la scrivevo: il testo vero arriva in un frame successivo dello stesso turno.
   In casa le tracce si buttano e basta, e un turno muto non fa bolla.
2. Lo stato vuoto restava a schermo *sopra* la conversazione: `[hidden]` e' una
   regola del browser a specificita' bassissima e il `display: grid` della
   classe la scavalcava.

**Aggancio al fondo verificato con contenuto che scorre davvero** (il primo
tentativo, con dodici righe, non riempiva il filo e non dimostrava niente):
in fondo insegue, risaliti smette, tornati in fondo riprende.

## Passo 3 — Il composer ✅

- [x] Invio manda, shift-invio va a capo (`isComposing` esclude l'invio che
      chiude un accento o una composizione IME).
- [x] Il campo cresce col testo e **torna piccolo**: misurato 41 → 104 → 41 px.
- [x] La bolla del proprio messaggio la disegna il client. Non e' una
      scorciatoia: il gateway rimanda l'eco solo dei messaggi entrati da *altri*
      canali, quindi senza questo la propria domanda comparirebbe solo dopo un
      ricaricamento.
- [x] Socket chiuso = nessuna bolla. Una bolla che compare e un messaggio che
      non arriva sono la stessa cosa vista da due parti, e la prima fa credere
      alla seconda.
- [x] Fermare: `goal_status` (`running` / `idle`) trasforma **lo stesso**
      bottone da manda a ferma. Un secondo bottone accanto sarebbe due bersagli
      dove ne serve uno, e quello sbagliato sempre a portata di pollice.
- [x] `/stop` scritto a mano non diventa una bolla: in casa i comandi non ci
      sono, ma digitarne uno non deve mostrare in chat cio' che il transcript
      esclude apposta dall'eco.
- [x] Messaggio vuoto o di soli spazi: non parte.

**Il banco di prova ora e' un gateway finto** (`scratchpad/finto_gateway.py`):
parla bootstrap, thread e websocket con `attach`/`message`, e serve gli statici.
Non imita Jenny, imita **il filo** — ed e' la ragione per cui esiste: qui
l'interruzione a meta' risposta si produce a comando, su un gateway vero si
aspetta che capiti.

**Giro completo misurato:** messaggio mandato, bolla disegnata, risposta che si
forma in markdown; poi *ferma* a meta' — il bottone passa a *manda* e il testo si
interrompe dopo 50 caratteri, senza bolla per `/stop`.

**Due trappole del banco, non della casa**, annotate perche' costano tempo:
`connection.respond()` di `websockets` fissa `Content-Length` sul testo che gli
passi, e assegnare `resp.body` dopo lascia la lunghezza a zero — 200 con corpo
vuoto, pagina bianca, **zero errori in console**. E il `key: "Return"`
dell'automazione del browser non arriva alla pagina come `key === 'Enter'`: un
invio sintetico funziona, quindi un invio che "non manda" li' non prova niente.

## Passo 4 — La riga di lavoro ✅

- [x] `assets/casa-activity.js`: 43 strumenti in sette famiglie, ognuna col suo
      vocabolario in `it`/`en`.
- [x] Comparsa dopo mezzo secondo (una risposta immediata non fa lampeggiare
      niente), rotazione della parola ogni 4 s, scomparsa a fine turno.
- [x] Mentre la risposta si forma la riga **si toglie di mezzo**: dirti che sto
      scrivendo mentre leggi quel che scrivo e' rumore. Torna se dopo il testo
      ricominciano gli strumenti.
- [x] Tocco lungo → `/html-mobile/index.html#turn=<id>` via `api.navigate`
      (senza id: la sola officina). L'officina il frammento non lo legge
      ancora, e non fa danno; quando lo leggera', da questa parte non c'e'
      niente da cambiare.

**Il giro delle famiglie, campionato ogni mezzo secondo su un turno vero:**

```
  501ms  off  think                 ← famiglia decisa, non ancora a schermo
 1004ms  ON   think     Arzigogolo…
 1506ms  ON   read      Spulcio…
 2510ms  ON   search    Scandaglio…
 3512ms  ON   out       Vado a vedere…
 4515ms  ON   run       Metto alla prova…
 5519ms  ON   delegate  Sveglio un aiutante…
 6524ms  ON   busy      Mi do da fare…     ← strumento sconosciuto
 7530ms  off  —                            ← la risposta ha cominciato ad arrivare
```

**La regola dell'onestà regge:** in sette parole diverse estratte dentro la
stessa famiglia, la famiglia non e' mai cambiata. Uno strumento fuori tabella
cade sul vocabolario generico invece di raccontare una cosa che non sta facendo:
e' l'unica differenza fra questa riga e un'animazione di caricamento.

Provato anche il ritorno: testo → strumento → testo, e la riga riappare come
`write`.

**Un test del repo ha preso un difetto vero**
(`test_every_long_press_caller_consumes_the_flag`): chi chiama `setupLongPress`
deve consumare il flag che l'helper posa. La riga non fa niente a un tocco
breve, quindi non avevo messo nessun gestore — ma il flag restava attaccato
**per sempre** dopo la prima pressione lunga, e il giorno che a un tocco breve
si volesse far fare qualcosa, quel qualcosa partirebbe anche dopo ogni
pressione lunga. Nota per chi tocchera' questo file: la guardia va scritta
`if (line.dataset.longpress)` con un identificatore semplice — il test la cerca
con una regex, e `this.el.dataset.longpress` non la soddisfa pur essendo
corretto.

## Passo 5 — Gli allegati ✅

- [x] Graffetta **dentro** la pastiglia del campo: allegare e' una cosa che si
      fa al messaggio che stai scrivendo. Mandare sta fuori, perche' quella
      chiude il messaggio.
- [x] `ImageHandler` condiviso, con i tetti del server (4 immagini, 8 MB): se
      divergessero, il gateway rifiuterebbe il messaggio intero.
- [x] Striscia degli allegati in attesa, ognuno con la sua X — senza, l'unico
      modo di disfare uno sbaglio sarebbe mandare il messaggio.
- [x] **Una foto senza didascalia parte**: la bolla e' l'immagine. Col solo
      controllo sul testo non sarebbe partita — e' la stessa regola che il
      gateway applica all'eco di Telegram.
- [x] Immagini in arrivo e in uscita nel filo; tocco per ingrandire, con la
      lightbox condivisa e il suo pinch-zoom (lo zoom del viewport e' spento in
      tutta l'app, quindi senza quella un'immagine si guarda solo grande come
      la miniatura).

### Il contratto col guscio nativo era di tre metodi. Sono cinque.

`onNativeReady`, `goHome`, `onPackageChanged`, **`handleHardwareBack`** e
**`openChat`**. Le due mancanti non si trovano cercando `window.mobileApp.<nome>`:
il Kotlin le chiama su una variabile locale (`var app = window.mobileApp; …
app.handleHardwareBack()`). **Il ponte verso il JS si conta sulle chiamate, non
sul nome dell'oggetto.**

`handleHardwareBack` e' quella che conta: senza, il tasto Indietro non chiude
l'immagine ingrandita e la pressione ricade sul sistema. In casa la catena e'
corta — c'e' una cosa sola sopra la conversazione — e **alla radice non fa
niente**, perche' questa app e' il launcher e Indietro non deve mai chiudere il
task. `openChat` (il tocco su un avviso proattivo) toglie di mezzo cio' che
copre e riporta in fondo al filo.

Verificato: ingrandimento aperto → Indietro lo chiude → Indietro di nuovo non
rompe niente → nessun residuo nel DOM (il `body` ha solo il guscio, il modulo e
l'input nascosto del selettore).

## Passo 6 — La mascotte ✅

- [x] `assets/casa-mascot.js` (164 righe, contro le 1.353 di `mobile-jenny.js`).
- [x] **In casa Jenny e' presenza, non controllo** (`pointer-events: none`).
      La minichat esiste perche' in officina la chat puo' non essere a schermo;
      in casa la chat *e'* lo schermo, e un secondo posto dove scriverle sarebbe
      una porta che da' sulla stanza in cui sei gia'. In piu' sta sopra un filo
      che scorre: ogni suo gesto sarebbe un gesto rubato allo scorrimento.
- [x] Tre stati piu' l'umore: riposo, pensa (corpo che oscilla), parla (la bocca
      si apre e chiude ogni 260 ms finche' il testo arriva).
- [x] Guardia sull'umore, come in officina: **rifiutato** se un turno sta
      girando o se e' la reazione a un turno che non e' piu' l'ultimo. Una
      faccia per una risposta superata confonde piu' di nessuna faccia.
      Verificato: durante un turno no, altro turno no, turno giusto si',
      umore sconosciuto no.
- [x] Geometria misurata: taglia 120 (`sm`), `right: -30px` = **-0,25 × taglia**
      esatto, come nella tavola.
- [x] Il composer cresce col testo e Jenny gli sta sopra: l'altezza vera finisce
      in `--casa-composer-h` via `ResizeObserver`, invece di un `bottom` fisso
      che al terzo capoverso la farebbe finire dentro il campo.

**Le tabelle degli sprite sono una copia**, e `tests/webui/test_casa_mascot_contract.py`
e' cio' che la tiene onesta: stessi file dell'officina, tutti presenti e tutti
nel manifest, e gli umori allineati a `MOODS` del backend. Verificato che il
test abbia denti (un percorso sbagliato lo fa diventare rosso).

**Corretto un commento sbagliato in `mobile-style.css`**: diceva che il
personaggio occupa ~45% del quadrato. E' il rapporto dell'arte a 3000 px prima
dell'impacchettamento; nei webp veri e' il **73% dell'altezza**. Fidarsene fa
disegnare la mascotte grande il doppio del vero — e' successo, all'inizio di
questo lavoro.

## Passo 7 — Le due porte ✅

- [x] Intestazione della casa dalla tavola `Main.dc.html`, ridotta a cio' che
      esiste: nessun chevron sul titolo, perche' la scelta di con chi parli e'
      del giro dopo e un chevron che non apre niente e' una bugia.
- [x] Porta di andata: il bottone in alto a destra. Portera' a «Tu e Jenny»
      quando quella pagina esistera'; oggi va dritto in officina, e l'icona lo
      dice.
- [x] Porta di ritorno: in fondo a Impostazioni → Sistema, non nel dock. Il
      dock e' la navigazione *dentro* l'officina, e la casa non e' una sua
      schermata: e' l'altro documento.
- [x] **Scambio dei nomi**: `index.html` e' la casa (ed e' cio' che il guscio
      nativo carica), l'officina ha il suo nome.
- [x] Installato e verificato sul Titan 2 (0.11.0, aggiornamento sul posto).

### Lo scambio ha rotto 21 test, e uno era gia' rotto

Venti erano contratti di markup che leggevano `index.html` perche' l'officina
*era* `index.html`: ora leggono `officina.html`. Il contratto non e' cambiato,
il file si'.

Il ventunesimo no. `test_static_serves_index_when_dist_present` scriveva un
`favicon.svg` in una cartella `dist` **mai collegata al canale** —
`static_dist_path` e' `workspace/ui`, non una cartella del test — quindi quel
file non veniva mai servito: la richiesta cadeva sul fallback SPA e
l'asserzione `"<svg" in text` passava perche' la shell dell'officina conteneva
`<svg id="graph-svg">`. **Verde per una coincidenza**, e lo scambio ha tolto la
coincidenza. Ora sonda un asset che esiste e pretende che *non* sia la shell.

### E ha richiesto una riga di Kotlin, che il piano diceva di no

`shouldOverrideUrlLoading` bloccava ogni navigazione di primo livello verso il
gateway che non fosse esattamente `GATEWAY_PATH`. E' una rete di sicurezza
giusta — impedisce a un href risolto male sotto `/html-mobile/` di sostituire
la SPA con `/api/…` e nessuna via di ritorno — ma e' precedente all'esistenza
di un secondo guscio, e bloccava **entrambe** le porte. L'unica traccia era
`Blocked main-frame navigation to a non-SPA gateway path` in logcat: il bottone
semplicemente non faceva niente. Il guard ora conosce i due documenti per nome,
in un elenco chiuso e non un prefisso.

### Misurato sul telefono, sulla conversazione vera

- La storia si e' ridisegnata con le etichette di provenienza: un messaggio
  entrato dal fumetto porta *from the bubble*.
- Mandato un messaggio: bolla disegnata, **«Ruminating…»** sotto la
  conversazione, bottone diventato ferma, Jenny nella posa del pensa.
- Risposta arrivata — e Jenny aveva delegato a un subagent. In casa non se n'e'
  vista traccia: solo il numero. Nell'officina, lo stesso turno mostra la
  pastiglia `spawn`, «Show thinking» e i tempi (4,1 s e 8,9 s).
- Il campo cresciuto a due righe ha alzato Jenny: il `ResizeObserver` funziona
  sul dispositivo vero.
- Giro completo delle porte: casa → officina → casa, senza 401 e senza blocchi.

Nota: l'interfaccia sul telefono e' in inglese perche' lo e' la lingua scelta
li' — vale per entrambe, non e' un difetto della casa.
