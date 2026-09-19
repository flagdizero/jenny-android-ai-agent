# Tu e Jenny — piano

La quarta stanza della casa: la pagina delle impostazioni di chi la usa, non di
chi la costruisce. Le tavole di riferimento sono `TuEJenny.dc.html` (la pagina)
e `Main.dc.html` (da dove ci si arriva); l'officina resta intera e resta dove è.

Le stanze di oggi sono `chat → pagine → lettore`, su un solo `data-view` con
una catena di ritorno lineare (`casa-app.js#_setView`). Questo giro ne aggiunge
due sullo stesso meccanismo: `tu` e, dentro, `jenny`.

---

## Quel che le tavole hanno già deciso

| Cosa | La tavola | Conseguenza sul codice |
|---|---|---|
| Da dove si entra | `Main.dc.html`: in alto a destra c'è **l'avatar**, non la chiave inglese | il bottone `casa-door` cambia icona e destinazione |
| Dove finisce l'officina | `TuEJenny.dc.html`: una scheda scura **in fondo** alla pagina, «osserva, regola, ripara · oppure tieni premuto l'avatar» | la porta non sparisce, si sposta; il tocco lungo sull'avatar resta la scorciatoia (`shared/longpress.js`) |
| Dove sta Jenny | disegnata al bordo, `right:-56px` come nelle pagine | gratis: `_setView` mette già `setOut(false)` fuori dalla chat |
| Le righe | una scheda sola, righe da 42 px con etichetta a sinistra e **valore corrente** a destra | il valore fa parte della riga: una riga che non dice come sta adesso è un link, non un'impostazione |
| Il cassetto | la maniglia in basso, su ogni tavola | fuori da questo giro, come dalle tavole precedenti (`PiuUsate.dc.html`) |

Il commento sopra `casa-door` in `index.html` questa pagina la prevedeva già:
«nel disegno finale porterà a «Tu e Jenny», che quella porta se la tiene in
fondo». Questo giro è quel finale.

---

## Le due modifiche chieste

### 1. La lingua esce dalla pagina

Chiesto: niente riga, si segue la lingua di sistema. **Misurato sul dispositivo
di prova prima di decidere**, perché la conseguenza non era quella che sembrava:

| Misura | Valore |
|---|---|
| `persist.sys.locale` | `en-IT` |
| `cmd locale get-app-locales` (lingua per-app) | `[]` — nessuna |
| `locale` nel `localStorage` della WebView | `en` |
| Cosa si legge a schermo adesso | «PERSONAL CONVERSATION», «Write to Jenny» |

Cioè: **l'interfaccia è già in inglese**, e togliere la riga non cambia niente
su questo telefono — segue il sistema perché il valore salvato dice la stessa
cosa che direbbe il sistema. Ma va detto per intero: dopo, non esiste più un
posto in casa dove rimetterla in italiano, e la casa continuerà a dire «Write
to Jenny» a chi scrive in italiano.

La riga andava comunque tolta, e la ragione è più forte della sua scomodità:
**cambia le scritte dei bottoni, non la lingua di Jenny.** Quella non viene da
lì — viene da `SOUL.md`, da `USER.md` e dal modello. `config.agents.defaults.language`
la scrive l'onboarding una volta sola e colora tre messaggi del backend (il
saluto di benvenuto, l'avviso di aggiornamento, Telegram). Una riga «Lingua»
che lascia Jenny a rispondere come prima promette più di quello che fa.

**Regola**: in casa non si sceglie. `i18n.detectLocale()` legge già
`navigator.language`; se in officina qualcuno ha scelto, quella scelta vale —
è una app sola, non due.

**Aperto, e vale la pena**: Android ha già il posto giusto per questa scelta —
la lingua per-app nelle impostazioni di sistema. Non compare perché il manifest
non dichiara `android:localeConfig`; dichiararlo è un file XML e un attributo,
e la scelta tornerebbe senza tornare in casa. Da verificare prima di prometterlo:
**se la WebView legga davvero la lingua per-app**, o se resti su quella del
processo fino al riavvio. Si misura con `cmd locale set-app-locales` e una
lettura di `navigator.language`, e si rimette com'era.

### 2. «Mascotte» diventa una stanza, e cambia nome

Chiesto: sottomenu con dimensione, mascotte flottante, `SOUL.md` modificabile.
Le prime due stanno insieme senza discussione. La terza no, e non per il posto:
**per il nome della porta.**

La mascotte è come Jenny *appare*: quanto è grande, se sta sopra le altre app.
`SOUL.md` è chi *è*, e decide come parla dappertutto — in chat, nel fumetto
flottante, nella tendina, su Telegram. Sotto un'etichetta «Mascotte» quel testo
sembra una preferenza di disegno.

Quindi la riga si chiama **«Jenny»**, col valore corrente a destra (es.
«piccola · flottante»), e dentro due blocchi:

- **Com'è fatta** — mostrala o no, dimensione (3 tagli), mascotte flottante;
- **Chi è** — le regole che le hai dato tu (sotto).

Tutto il meccanismo esiste già e non va costruito:

| Pezzo | Dove vive oggi |
|---|---|
| visibile / taglia | `shared/mascot.js` (localStorage, condiviso con l'officina); la taglia va anche alla finestra flottante nativa via `JennyNative.setMascotSize` |
| flottante | `config.floating`, `/api/settings` (con `available` e `active`) e `/api/settings/floating/update`, che applica subito e **dice se Android ha concesso il permesso** |
| le parole | `settings.mascotVisible`, `settings.mascotSize*`, `settings.floatingEnabled`, `settings.floatingHint`, `settings.floatingBlocked` — già scritte, in due lingue |

---

## Le regole che le hai dato tu

Qui c'è l'unica decisione vera di questo giro, e una misura che la decide.

**Dream riscrive `SOUL.md`.** Non in teoria: contato sugli snapshot del
dispositivo di prova, che il runtime scatta prima di ogni passata.

| Finestra | 58 snapshot, 6,6 giorni |
|---|---|
| Valori distinti di `SOUL.md` | 7 |
| Riscritture | **6** |
| Cadenza | una ogni 1,1 giorni (tutte e sei negli ultimi 3 giorni) |

Una casella di testo che salva `SOUL.md` così com'è, quindi, offre di scrivere
qualcosa che **dura circa un giorno**. Chi scrive «chiamami per nome, niente
emoji» se lo ritrova riscritto dalla consolidazione notturna, e sembrerà un
difetto dell'app. L'aiuto dell'officina lo dice già a modo suo — «lo mantiene
Dream, di solito non serve scriverci a mano» — ma in casa quella frase non è una
premessa: è la descrizione di un campo che si svuota da solo.

**La casa non modifica il file: modifica la tua parte del file.** Un blocco
marcato dentro `SOUL.md` — l'unico pezzo che l'editor di casa legge e riscrive
— e tutto il resto resta di Dream, che continua a consolidare come fa adesso.

Il blocco non si difende col prompt: si difende riscrivendolo.

**Come è finita — due misure hanno spostato il disegno, e la via di riserva è
diventata la via.** Confrontando le sette versioni di `SOUL.md`: in tutti e sei
i cambi **nessuna intestazione è stata tolta o aggiunta**, mentre le righe
dentro sì (+4/−8 nel più grosso). Dream pota *dentro* le sezioni, quindi un
blocco sopravvive come intestazione ma le sue righe no. E leggendo il codice:
`MemoryStore` **non è lo scrittore** — Dream scrive quel file con
`write_file`/`edit_file`/`apply_patch`, e un guardiano lì non avrebbe visto
passare niente.

Quindi: la verità sta in `.jenny/soul_rules.md`, fuori dal registro di
scrittura di Dream (che ammette esattamente `SOUL.md`, `USER.md`,
`memory/MEMORY.md` e `skills/<nome>/SKILL.md`), e dentro `SOUL.md` ne resta un
blocco **proiettato** — così il prompt lo legge dov'è sempre stato, senza una
voce in più nel bootstrap. La proiezione si rifà in `finish_dream_cycle`, che
il chiamante invoca nel `finally`: vale anche per un turno crashato a metà. Il
blocco si riconosce dai due marcatori e, se mancano, dall'intestazione — il
ripiego è per la forma di potatura che le misure mostrano.

Trasporto: niente endpoint nuovi. Si legge con `/api/workspace/read?path=SOUL.md`
e si salva con `rpc.writeWorkspaceFile` — l'RPC sul WebSocket, che esiste
**proprio perché** salvare `SOUL.md` dalla superficie `/api/` era impossibile
(header ISO-8859-1: un'emoji viene rifiutata prima di partire). Serve
`workspace.allow_write` acceso; se è spento il salvataggio torna `forbidden`, e
va detto invece che perso.

---

## Cosa entra in questo giro, cosa no

| Riga della tavola | In questo giro | Perché |
|---|---|---|
| Tema | **sì** | i sette temi sono già in `mobile-style.css`, che la casa carica apposta, e `bootstrap.js` li applica prima del primo paint: è una striscia di pastiglie, non un lavoro di CSS. La tavola ne disegna quattro perché quattro ci stanno |
| Jenny (taglia, flottante, regole) | **sì** | è la richiesta |
| Officina | **sì** | è già una porta che funziona: si sposta in fondo e si veste da scheda |
| Versione | **sì, come testo** | `/api/settings` la porta già; il giro di aggiornamento no |
| Chi risponde | no | ha una tavola sua (`Modello.dc.html`): provider, chiave API e modello. È anche l'unica schermata dove un tocco sbagliato lascia Jenny senza risposte, e una chiave API si incolla in officina |
| Avvisi di sua iniziativa | no | **la tavola disegna un interruttore dove l'app ha tre permessi e una scelta a tre valori** (esenzione batteria, sveglie precise, `power.keepAwake`). Non è una riga: è il blocco batteria dell'officina, ed è una diagnosi, non una preferenza |
| Backup | no | `shared/backup-flow.js` esiste, ma un backup cifrato con passphrase è un'operazione da operatore |
| Aggiornamenti | no | giro suo |
| «grazie a [n] sostenitori» | no | non c'è nessun posto da cui prendere quel numero |
| Maniglia del cassetto | no | come nei giri precedenti |

---

## Come si incastra

1. **Le stanze.** `_setView` accetta due nomi nuovi; la catena di ritorno
   diventa `jenny → tu → chat`, accanto a `lettore → pagine → chat`. Lo stesso
   `goBackOneRoom` serve il tasto Indietro del telefono (`handleHardwareBack`).
2. **L'intestazione.** L'avatar al posto della chiave inglese, tocco lungo per
   l'officina. `_applyHead` sa già mostrare e nascondere per stanza.
3. **Il pavimento.** Fuori dalla chat non c'è composer: `--casa-composer-h`
   passa a `FLOOR_NO_COMPOSER` e Jenny va al bordo. Già così per le pagine.
4. **I moduli nuovi**: `casa-tu.js` (la pagina) e `casa-jenny.js` (la stanza di
   lei). Entrambi nel `_UI_MANIFEST`, o sul telefono non arrivano — e non
   falliscono: viene servita l'officina.
5. **Il server** lo si tocca in un punto solo: il guardiano del blocco in
   `MemoryStore`.

## Trappole note

- **`_UI_MANIFEST`**: un file nuovo non elencato non dà 404, dà l'officina.
- **`[hidden]` perde**: `.casa-back[hidden]` è già costato un giro. Ogni
  elemento nuovo che nasce nascosto si porta la sua riga `[hidden]`.
- **La taglia non è solo CSS**: `setMascotSize` spinge i pixel anche alla
  finestra flottante nativa. Cambiarla mentre il fumetto è acceso va provato.
- **`floating.active ≠ floating.enabled`**: a permesso negato la config resta
  accesa e il payload lo dice. L'interruttore deve raccontarlo, non rimbalzare.
- **La lingua in due posti**: `localStorage` e il sistema. La casa non scrive
  mai il primo.

## Come si prova

- **Banco**: il modello delle stanze (catena di ritorno, Indietro), la
  fusione del blocco dentro un `SOUL.md` che Dream ha riscritto nel frattempo,
  e il guardiano — Python, con Dream simulato che butta via il blocco.
- **Telefono**: la porta e il tocco lungo, il permesso della flottante negato
  e concesso, un giro di Dream vero con un blocco scritto a mano dentro.
- **Mutazioni**: come sempre, e con la stessa domanda a ogni verde — la
  mutazione è atterrata dove credo?

## Ordine di lavoro

1. **La stanza e la porta** — `tu` sul meccanismo esistente, l'avatar al posto
   della chiave inglese, il tocco lungo, la scheda dell'officina in fondo, la
   riga della versione. Da qui in poi la pagina esiste e si può guardare.
2. **Tema** — la striscia dei sette, applicata al tocco.
3. **Jenny: com'è fatta** — mostrala, taglia, flottante col suo permesso.
4. **Jenny: chi è** — il blocco marcato, il guardiano su `MemoryStore`, e il
   giro di Dream vero sul telefono a dire se ha tenuto.

I primi tre non toccano il server. Il quarto sì, ed è l'unico che va misurato
sul dispositivo prima di dichiararlo fatto: una passata di Dream, e il blocco
ancora lì.


---

## Com'è andata

Tutto atterrato, in quattro commit: [`casa-tu-e-jenny-checklist.md`](casa-tu-e-jenny-checklist.md).
