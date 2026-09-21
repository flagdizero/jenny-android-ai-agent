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

1. ~~**Tema `kyoto`.**~~ **Non serve un passo.** La tavolozza della tavola è un
   tema che il prodotto ha già, e sceglierlo è dell'utente, non del codice. Ma
   c'è un fatto in più, scoperto leggendo `Main.dc.html`: **la casa della tavola
   è chiara** (`#ebe6de`/`#8b6a47`) e corrisponde a `pietra`, mentre l'officina è
   scura e corrisponde a `kyoto`. Due temi diversi nello stesso disegno, e il
   prodotto ne tiene uno alla volta: se casa-chiara-officina-scura è l'intento,
   è una decisione a sé, non una riverniciatura.
2. ✅ **La cornice** — `2a16f98`. Soprascritta `officina`, nome in serif, la riga
   che dice a cosa serve, il pill `Jenny`; barra con le etichette e il puntino.
   Il difetto era una tabella mancante: `_mount` cercava `title-<modo>` e i tre
   cassetti condividono `title-settings`, quindi `setMode` usciva in silenzio.
   `VISTA_DI` si sposta accanto a `CASSETTI` e resta una copia sola.
3. ✅ **Le schede aperte** — `673285c`. `_section` → `_gruppo`: soprascritta
   fuori, scheda dentro, niente chevron né stato. Se ne vanno **due pezze** che
   aprivano una sezione d'ufficio perché «un accordion chiuso è esattamente il
   posto in cui il problema resta invisibile».
4. ✅ **Il ritaglio fine** — `18c9bc3`. Undici sezioni → **quindici gruppi**:
   Modello si spezza in Chi pensa · Le marche · Parametri, Strumenti in Ricerca
   web · Posizione, Memoria in Quanto ricorda · Dream. Otto chiavi i18n orfane
   rimosse. Due gruppi della tavola **non** arrivano perché non esistono nel
   prodotto (v. sopra), e `personalization`/`system` restano parcheggiati.
5. **Console esplosa**, senza i numeri. ← *il prossimo*
   - ✅ **L'intestazione** (21/09/2026). Era l'unica vista dell'officina a
     partire dal bordo dello schermo: nessun nome, e — visto che il pill
     `Jenny` vive nella cornice — nessuna porta verso casa che non passasse da
     un altro cassetto. Adesso ha la cornice come le altre, con due differenze
     volute: niente soprascritta (la vista sta gia' dentro l'officina) e il
     nome e' **Console**, la stessa stringa `nav.console` che l'etichetta in
     fondo usa gia'. Unico suo: il mount e' `sticky`, perche' in chat a
     scorrere e' il documento intero e senza quello il titolo se ne andrebbe
     al primo dito.
6. **Le decisioni**, se e quando: dove vanno tema/nome/icona; i tre interruttori
   per ambito con `/ro`; l'elenco dei file veri; i numeri della Console.

**Stato a schermo (banco locale, 21/09/2026).** Cervello: Chi pensa · Le marche ·
Parametri · Attività in background · Personalizzazione · Sistema. Mani: Ricerca
web · Posizione · SSH · Telegram · Quando agisce da sola. Memoria: Quanto ricorda
· Dream · Giardiniere · Storia locale. Zero errori, zero schede vuote.
**Non ancora provato sul telefono.**

Ogni passo si chiude col suo banco, provato rosso mutando il codice che difende —
come nei cinque passi precedenti.

## Quel che non si tocca

- **Dove stanno le cose**: i quattro cassetti restano quelli decisi in
  `officina-tavole-plan.md`. Qui cambia il taglio interno, non l'indirizzo.
- **La casa**, salvo il trasloco proposto di tema/nome/icona.
- **Gli altri sei temi**: tutto in token, o il piano è sbagliato.
