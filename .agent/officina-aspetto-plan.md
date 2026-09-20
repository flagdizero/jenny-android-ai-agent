# L'officina: dall'impalcatura all'aspetto della tavola

**Stato al 20/09/2026.** I cinque passi di [`officina-tavole-plan.md`](./officina-tavole-plan.md)
hanno spostato **cosa sta dove**: quattro cassetti, niente doppioni con la casa,
il giro degli aggiornamenti tornato a casa. Quel piano non ha mai toccato
**come si vede**, e la differenza con la tavola è tutta lì — misurata sul
telefono il 20/09.

Questo documento è il secondo tempo: l'aspetto.

## La scoperta che cambia il conto

La tavola non usa una tavolozza inventata. È **il tema `kyoto`, token per
token**:

| nella tavola | in `mobile-style.css` |
| --- | --- |
| `#201d1a` fondo | `--bg` |
| `#2b2723` schede | `--surface` |
| `#191714` campi | `--surface-2` |
| `#3f3a33` bordi | `--border` |
| `#b2543f` accento | `--accent` |
| `#e5ddd0` testo | `--text` |
| `#efe7d8` titoli | `--heading` |
| `#8fa382` pallino stato | `--ok` |
| Shippori Mincho | `--font-display` |

Il carattere è **già impacchettato** (`assets/vendor/fonts/theme-fonts.css`).
Il telefono è su `chanel` (quasi-nero, accento = testo), e questo da solo
spiega una fetta grossa del «totalmente diversa».

**Conseguenza sul progetto:** l'aspetto della tavola **non va scritto a mano nel
CSS**. Sette temi esistono e l'utente li cambia; inchiodare `#b2543f` ne
romperebbe sei. Tutto quel che segue si scrive in token, e la tavola si ottiene
scegliendo `kyoto`.

## Il salto, in quattro pezzi

Misurato confrontando `project/Console.dc.html` e `project/Cervello.dc.html`
della tavola con quel che disegna la build `b14c2e0`.

### 1. Il tema — nessun codice

Passare a `kyoto` porta colori, bordi e il serif dei titoli. Zero righe.
È il primo passo perché è quello che cambia di più a parità di rischio.

### 2. L'intestazione — il gancio c'è, il contenuto no

La tavola ha, in cima a ogni cassetto: soprascritta `officina`, il nome in
serif, **una riga che dice a cosa serve il cassetto**, una pastiglia di stato e
il bottone `Jenny` che torna a casa.

Oggi: **niente**. Ma `officina.html:209` ha già `<div class="view-title-mount"
id="title-settings">`, e `.view-title-text` (`mobile-style.css:574`) usa già
`--font-display`. Manca chi ci scrive dentro.

Da fare: far disegnare l'intestazione a `SettingsController` in funzione del
cassetto aperto — quattro sottotitoli nuovi in i18n, la pastiglia di stato, il
bottone per la casa.

### 3. La barra sotto — icone nude

Tavola: icona **+ etichetta** + pallino sotto la voce attiva, barra rialzata
con bordo in cima.
Oggi: `<div class="dock-item">` con dentro solo l'icona.

Le etichette esistono già come `title`/`data-i18n-title`: vanno rese visibili.
Piccolo, ma è metà di quel che si guarda.

### 4. I cassetti — fisarmoniche chiuse contro schede aperte

**È questo che fa dire «totalmente diversa».**

Tavola: gruppi con una soprascritta maiuscola (`chi pensa`, `le marche`,
`parametri`, `poter pensare a schermo spento`) e le schede **tutte aperte**, una
sotto l'altra, da scorrere.
Oggi: quattro righe chiuse su uno schermo vuoto, e il contenuto si vede solo
toccando.

È il pezzo più grosso: tocca la macchina delle sezioni a fisarmonica, non solo
il foglio di stile. Dentro ci sono anche i dettagli della tavola che oggi non
esistono — il pallino colorato per marca, la chiave mascherata `sk-k…hxBW`, la
barra del contesto in uso, il comando a tre scelte per la CPU, le righe di
rimando in fondo («Dream e il giardiniere stanno in Memoria»).

### 5. La Console — è un'altra vista, e per metà non è disegnabile

La Console della tavola **non è la chat**: è la stessa sessione **esplosa**, un
turno per scheda, con l'intestazione `21:52:04 · minimax-m3 · 12 671 in · 369
out · 8,4 s` e le righe etichettate `tu` / `pensa` / `legge` / `esce` /
`scrive` / `jenny`, ognuna col suo tempo a destra. Sotto, la striscia
`in · out · cache` + `ctx 17k/65k`, e un composer col bottone `/`.

**Metà si può fare oggi**: le righe esplose e i loro tempi si ricavano dagli
eventi degli strumenti che il transcript già registra.

**Metà no.** I numeri per turno (`12 671 in · 369 out`) e la stima del contesto
(`ctx 17k/65k`) **non esistono nel protocollo**: `turn_end` non porta nessun
campo di token (verificato il 20/09), e non c'è una rotta che esponga la stima
del contesto. Sono un **cambio di protocollo**, non un riordino — e restano una
decisione dell'utente, non mia.

## L'ordine consigliato

1. **Tema `kyoto`** — zero codice, cambia di più.
2. **Barra sotto con le etichette** — piccolo, visibile subito.
3. **Intestazione dei cassetti** — il gancio esiste già.
4. **Schede aperte al posto delle fisarmoniche** — il pezzo grosso.
5. **Console esplosa, senza i numeri** — quel che si può fare senza toccare il
   protocollo.
6. **I numeri della Console** — solo se l'utente decide il cambio di protocollo.

Ogni passo si chiude col suo banco, e ogni banco va provato rosso mutando il
codice che difende — come nei cinque passi precedenti.

## Quel che questo piano **non** cambia

- Dove stanno le cose: i quattro cassetti e il loro contenuto restano quelli
  decisi in `officina-tavole-plan.md`. Qui si cambia solo l'aspetto.
- La casa. `index.html` e i suoi `casa-*.js` non si toccano.
- Gli altri sei temi: tutto in token, o il piano è sbagliato.
