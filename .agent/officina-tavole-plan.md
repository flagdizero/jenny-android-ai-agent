# L'officina come la disegnano le tavole — il giro minimo

Sei tavole nella riga dell'operatore del canvas: **Console, Stato,
Programmazione, Cervello, Mani, Cassetti**. Questo piano dice come arrivarci
spostando il meno possibile.

## La tesi, e la misura che la regge

**Le tavole non chiedono un'officina nuova: chiedono la stessa officina divisa
in cinque cassetti diversi.** Misurato voce per voce, di tutto quel che le sei
tavole disegnano esiste già **tutto tranne tre cose** — e una delle tre
propongo di tenerla fuori.

Cosa c'è oggi:

| | |
|---|---|
| guscio | `officina.html`, 450 righe, 7 viste (`view-<mode>`) |
| dock | 5 voci: chat, wiki, apps, workspace, settings (+ onboarding nascosta) |
| impostazioni | `mobile-settings.js`, 2 699 righe, **11 sezioni a fisarmonica** |
| le altre viste | chat 3 832 · apps 1 730 · workspace 1 108 · graph 943 · wiki 746 |

E la cosa che rende il giro economico: **`switchMode` è già generica** — si
regge su `controllerFactories` e su un `<div id="view-NOME">`, non su un elenco
di casi — e le undici sezioni sono undici funzioni `_render*` che tornano una
stringa e stanno fra le 10 e le 31 righe l'una. Non c'è niente da riscrivere:
c'è da rifilare.

## La corrispondenza, tavola per tavola

| Tavola | Da dove viene, oggi | Cosa manca davvero |
|---|---|---|
| **Console** | `view-chat` intera, con pensieri, tool e tempi già a schermo; l'intestazione la fa già `mobile-header.js` per vista | **la barra in fondo** (token del turno, cache, `ctx 16k/65k`): i dati ci sono in `/api/token-usage`, nella chat non sono mai stati disegnati |
| **Stato** | versione da `system`, contatori da `usage`, batteria dalla sua scheda, prossimi lavori da `cron-view`, SSH da `ssh` | **la pagina**. Oggi non esiste un posto che risponda «come sta adesso»: le cinque risposte stanno in cinque sezioni diverse |
| **Programmazione** | `settings→scheduling` (`shared/cron-view.js`), intera | niente — cambia solo da dove si apre: sotto-pagina di Stato |
| **Cervello** | `settings→models` + `memory` + `workers` | niente |
| **Mani** | `settings→tools` + `ssh` + `view-apps` + skill | **i permessi per scope e `/ro`** — v. sotto |
| **Cassetti** | `view-workspace` + `settings→backup` + `system` + trascrizione/audit | niente |

Quattro tavole su sei sono **zero codice nuovo**: sono le stesse funzioni di
render chiamate da un contenitore diverso.

## Le tre cose che non sono un raggruppamento

**1. Stato è una pagina nuova** — ma è composizione, non backend: ogni numero
che disegna esiste già in `/api/settings` (modello, provider, `usage`,
`power`, `version`, `runtime`) e in `/api/cron` (prossimi lavori, `uptime`,
`active_task_count`). Una funzione di render e una fetch che il controller fa
già. È il pezzo più grosso del giro e resta piccolo.

**2. La barra di Console** — token del turno e contesto. Dato esistente,
disegno nuovo. Piccola.

**3. I permessi di scrittura per scope, e `/ro`** — e questa **propongo di
lasciarla fuori dal giro minimo**, dicendolo invece di nasconderla.

Oggi esiste un interruttore scrittura/sola-lettura nel composer
(`shared/write-switch.js`): è **per conversazione**, vive nel client e il flag
viaggia **dentro il messaggio**, apposta — «un messaggio partito credendolo in
sola lettura non si ritira», e il server non tiene quello stato perché
altrimenti potrebbe raccontarne uno diverso da quello con cui il messaggio è
partito.

La tavola Mani disegna un'altra cosa: tre permessi **per scope** (personale,
quaderni, console) che vivono nelle impostazioni, e un `/ro` davanti al
messaggio per un turno solo — con la nota «il chip è sparito dalla chat».
Sono uno stato sul server, un comando nuovo, e la rimozione di un comando del
composer: cambia **cosa fa un messaggio**, non dove sta un'impostazione. È un
giro suo, e merita di essere pesato da solo.

Nel giro minimo, Mani mostra i permessi **come sono oggi** — cioè l'ambito di
scrittura reale (`security.restrict_to_workspace`) e l'interruttore dove sta —
e la tavola resta da finire.

## Cosa le tavole non nominano, e va deciso

- **Personalizzazione** (tema, mascotte, lingua): in nessuna delle sei. È
  giusto — da oggi vive in casa, «Tu e Jenny». Quindi: **si toglie
  dall'officina**, o resta come doppione? Propongo di toglierla: due posti che
  scrivono la stessa preferenza sono due posti che si disallineano.
- **Telegram**: in nessuna tavola. Da qualche parte deve stare. Propongo
  **Mani**: è un canale, cioè un modo in cui Jenny arriva fuori — la stessa
  famiglia di SSH e delle app.
- **SSH** compare due volte: in Stato come riga di stato («SSH acceso · 1
  host») e in Mani come impostazione. Non è un doppione se si legge così: **la
  manopola sta in Mani, Stato la legge**. Vale come regola generale fra le due
  tavole.
- **Onboarding**: vista nascosta, non è sul dock, resta dov'è.

## Il percorso, e perché in quest'ordine

La regola del giro: **l'app non deve mai essere rotta a metà, e non deve mai
esserci uno schermo finto.** Da cui la forma della migrazione — *la
fisarmonica si svuota*:

**Passo 0 — il guscio.** Il dock passa a cinque voci: Console, Stato, Cervello,
Mani, Cassetti. `view-settings` **resta**, e all'inizio è Stato: dentro ci sono
ancora tutte e undici le sezioni. Nessuna è sparita, nessuna è vuota.

Una sola cosa da costruire, ed è la chiave di tutto il giro: in `render()`
l'elenco delle undici sezioni diventa una **tabella `tab → sezioni`**, e il
controller disegna solo quelle del cassetto attivo. Niente controller nuovi,
niente file nuovi, `_wireSections` (222 righe) **non si spezza**: aggancia per
`id`, e gli id delle sezioni non disegnate semplicemente non ci sono.

*Da controllare prima:* `_wireBtn` è già a prova di assente (`if (btn)`), e in
`_wireSections` su 18 `querySelector` nove sono già protetti da un `if` e due
da `?.`. **Restano ~7 accessi da verificare** — mezz'ora, mica un refactor.

**Passo 1 — Cassetti.** `backup` e `system` escono dalla fisarmonica e si
uniscono a `view-workspace`. Due voci nella tabella. È il passo più facile e
serve a provare che la tabella funziona.

**Passo 2 — Cervello.** `models` + `memory` + `workers`. Tre voci nella
tabella, zero modifiche alle funzioni.

**Passo 3 — Mani.** `tools` + `ssh` + `telegram`, più la porta per le app e le
skill. Qui si decide dove finisce Telegram (v. sopra).

**Passo 4 — Stato, e Programmazione sotto.** Adesso la fisarmonica è vuota, e
quel che resta nella vista è **solo** ciò che Stato deve dire. Si riscrive come
pagina, e `scheduling` diventa la sua sotto-pagina — stessa catena di «indietro»
della casa, una riga in una tabella.

**Passo 5 — la barra di Console.** Token del turno e contesto sotto la chat.

Ogni passo è installabile da solo, e ogni passo lascia l'officina intera.

## Cosa non si tocca, ed è la parte che tiene basso il costo

- **Nessun controller si riscrive.** `mobile-settings.js` resta un file, con lo
  stesso nome: **28 file di banco** leggono i sorgenti dell'officina, e un
  rename li tocca tutti per niente.
- **Nessuna rotta nuova, nessun campo nuovo nei payload.** Tutti i numeri delle
  sei tavole sono già serviti. L'unica eccezione sarebbe il punto 3, che è
  fuori.
- **La casa non si tocca.** Sono due gusci che non si vedono fra loro.
- **Le funzioni `_render*` non si spostano di file.** Cambia chi le chiama.

## Le prove, per passo

- **Passo 0:** un banco che incrocia la tabella `tab → sezioni` con l'elenco
  delle sezioni esistenti — nessuna sezione senza cassetto, nessun cassetto che
  nomina una sezione che non c'è. È la stessa forma dell'invariante `BACK_TO`
  della casa, ed è l'unico modo di non scoprire una sezione sparita sul
  telefono.
- **Passi 1-3:** per ogni cassetto, un banco che apre la vista e conta le
  sezioni a schermo; e la suite esistente delle impostazioni, che deve restare
  verde senza modifiche — se cade, qualcosa si è spostato davvero invece di
  essere stato solo rifilato.
- **Passo 4:** Stato legge da `/api/settings` e `/api/cron`, quindi il banco è
  sui dati: ogni riga della tavola nasce da un campo esistente, e un campo che
  manca deve far fallire il banco, non comparire come `undefined` a schermo.
- **Passo 5:** i conti della barra contro `/api/token-usage`.

## Il peso

| Passo | Peso |
|---|---|
| 0 — guscio + tabella | mezza giornata, e ci sta dentro l'audit dei 7 accessi |
| 1 — Cassetti | un'ora |
| 2 — Cervello | un'ora |
| 3 — Mani | un'ora, più la decisione su Telegram |
| 4 — Stato + Programmazione | il pezzo vero: mezza giornata |
| 5 — barra di Console | un'ora |

Fuori: i permessi per scope e `/ro`, che sono un giro loro.
