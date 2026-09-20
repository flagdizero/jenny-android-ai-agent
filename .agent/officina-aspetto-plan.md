# L'officina come la tavola: il passaggio definitivo

**Stato al 20/09/2026.** I cinque passi di [`officina-tavole-plan.md`](./officina-tavole-plan.md)
hanno spostato **cosa sta dove**: quattro cassetti, niente doppioni con la casa.
Non hanno toccato **come si vede**, e la differenza con la tavola è tutta lì.

## La cosa da capire prima di tutto: non è una riverniciata, è un **ritaglio diverso**

Il primo istinto — «apri le fisarmoniche e cambia i colori» — è sbagliato, e si
vede confrontando le soprascritte della tavola con le sezioni del codice.

| cassetto | i gruppi della tavola | le sezioni di `CASSETTI` |
| --- | --- | --- |
| **Cervello** | chi pensa · le marche · parametri · poter pensare a schermo spento | `models` · `battery` · `personalization` · `system` |
| **Mani** | permessi di scrittura · ricerca web · posizione · ssh · canali e abilità · quando agisce da sola | `tools` · `ssh` · `telegram` · `scheduling` |
| **Memoria** | quanto ricorda · dream · giardiniere · i file veri · storia | `memory` · `workers` · `backup` |

Non combaciano, e non per poco:

- **`models` diventa tre gruppi** (chi pensa, le marche, parametri).
- **`tools` diventa tre gruppi** (permessi, ricerca web, posizione).
- **`memory` diventa tre gruppi** (quanto ricorda, dream, i file veri).
- **`personalization` e `system` non esistono nella tavola.**
- **«permessi di scrittura» non esiste nel codice.**

Quindici gruppi contro undici sezioni. La tavola **non ridisegna le sezioni: le
ritaglia in un altro modo**, più piccole e tutte aperte. Quindi il lavoro
definitivo non è vestire quel che c'è: è cambiare l'unità di cui è fatta la
pagina.

**La buona notizia: è meno macchina, non di più.** La fisarmonica sparisce del
tutto — niente `_openSections`, niente testa cliccabile, niente chevron. Resta
un solo mattone, ripetuto quindici volte: *soprascritta + scheda aperta*.

## L'altra scoperta: la tavolozza esiste già

La tavola non usa colori inventati. È **il tema `kyoto`, token per token**:
`#201d1a` = `--bg`, `#2b2723` = `--surface`, `#3f3a33` = `--border`,
`#b2543f` = `--accent`, `#e5ddd0` = `--text`, `#8fa382` = `--ok`, e il serif dei
titoli è `--font-display` (Shippori Mincho, **già impacchettato** in
`assets/vendor/fonts/theme-fonts.css`). Il telefono è su `chanel`, il quasi-nero.

**Regola di conseguenza:** tutto si scrive in token. Inchiodare `#b2543f`
romperebbe gli altri sei temi. La tavola si ottiene *scegliendo* `kyoto`.

## Tre cose che non sono lavoro di vista, e vanno decise

### 1. Due sezioni senza casa nella tavola

`personalization` (tema, nome e icona di Jenny) e `system` (versione, modalità
sviluppatore, torna alla casa, utilizzo token) non compaiono in nessuna tavola.

Tre di quei pezzi la tavola li ha già risolti da sé, nella **cornice**: la
versione è una pastiglia nell'intestazione della Console, l'utilizzo token è la
striscia in fondo alla Console, e «torna alla casa» è il bottone `Jenny` in
testa a ogni cassetto.

Restano senza posto: **tema, nome e icona di Jenny, modalità sviluppatore**.

> **Proposta.** Tema, nome e icona vanno **in casa**: sono «come mi appare
> Jenny», e la casa ha già «Tu e Jenny» che parla di questo. La modalità
> sviluppatore resta in Cervello, come ultima riga discreta. Così la tavola non
> va toccata e niente si perde.

### 2. «Permessi di scrittura» è una funzione che non c'è

Il gruppo della tavola contiene quattro comandi:

- tre interruttori per ambito — **personale** · **quaderni** · **console**
- la riga su `/ro` (sola lettura per un turno)
- **«Resta dentro il workspace»**

Solo l'ultimo esiste (`security.restrict_to_workspace`). Gli altri tre più `/ro`
sono da costruire: non è pittura, è comportamento.

> **Proposta.** Il gruppo si fa ora **con il comando che esiste**, e i tre
> interruttori per ambito arrivano quando arriva la funzione. Un interruttore che
> non fa niente è peggio di un interruttore che manca — è la stessa regola con cui
> i bottoni del backup spariscono fuori da Android.

### 3. I numeri della Console sono un cambio di protocollo

La Console della tavola non è la chat: è la stessa sessione **esplosa**, un
turno per scheda, con le righe `tu` / `pensa` / `legge` / `esce` / `scrive` /
`jenny` e il tempo di ognuna.

Le righe e i tempi **si possono fare oggi**, dagli eventi degli strumenti che il
transcript già registra.

I numeri no: `12 671 in · 369 out` per turno e `ctx 17k/65k` **non esistono nel
protocollo** — `turn_end` non porta nessun campo di token (riverificato il
20/09), e nessuna rotta espone la stima del contesto.

> **Proposta.** La Console esplosa si fa senza la striscia dei numeri. La
> striscia arriva solo se decidi il cambio di protocollo, che resta una tua
> chiamata.

## L'ordine

Scelto perché nessun passo butta via il precedente, e perché il salto più grosso
si vede subito.

1. **Tema `kyoto`.** Zero codice. Porta colori, bordi e il serif.
2. **La cornice.** Intestazione di cassetto (soprascritta `officina`, nome in
   serif, la riga che dice a cosa serve, pastiglia di stato, bottone `Jenny`) e
   barra sotto con **etichette** e il pallino sulla voce attiva. Indipendente dal
   ritaglio, quindi si può fare prima; il gancio esiste già
   (`officina.html:209`, `.view-title-text` usa già `--font-display`).
3. **Il ritaglio.** Il mattone *soprascritta + scheda aperta*, la fisarmonica
   rimossa, i quindici gruppi. Un cassetto alla volta: Cervello → Mani → Memoria,
   ognuno col suo banco.
4. **Console esplosa**, senza i numeri.
5. **Le decisioni**, se e quando: i tre interruttori per ambito con `/ro`; i
   numeri della Console.

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende —
come nei cinque passi precedenti.

## Quel che non si tocca

- **Dove stanno le cose**: i quattro cassetti restano quelli decisi in
  `officina-tavole-plan.md`. Qui cambia il taglio interno, non l'indirizzo.
- **La casa**, salvo il trasloco proposto di tema/nome/icona.
- **Gli altri sei temi**: tutto in token, o il piano è sbagliato.
