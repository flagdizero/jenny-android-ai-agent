# Formule e diagrammi tornano — e stavolta dove servono

*21/09/2026 — branch `feat/la-casa`, dopo lo sfoltimento dell'officina.*

## Da dove si parte, misurato

Il 21/09 ho cancellato KaTeX (1,4 MB) e Mermaid (3,3 MB) dal commit «la wiki
lascia l'officina», con questa motivazione scritta nel commit **e in un banco**:

> «avevano **un solo lettore ciascuno**, ed era la wiki, e la casa non carica
> nessuna delle due»

**Per KaTeX è falso.** Il lettore era anche la **chat dell'officina**:
`mobile-chat.js::renderKaTeX()` è chiamata da quattro punti (1050, 1245, 1782,
1910). Ho tolto la libreria e lasciato i quattro chiamanti. La funzione comincia
con `if (typeof renderMathInElement === 'function')`, quindi da quel commit **non
fa niente, in silenzio**: nessun errore, nessuna riga di log. Le formule in chat
si vedono come `$...$` grezzo.

**Per Mermaid è vero ma incompleto.** L'unico che disegnava era
`mobile-wiki.js::_renderMermaid`, uscito con la wiki. Ma il server **continua a
produrli**: `webui/wiki.py:643` marca ogni blocco come
`<pre class="mermaid-block"><code class="language-mermaid">`. Il lettore di casa
prende quell'HTML (`api.getPage` → `_safeHtml` → DOMPurify → `innerHTML`) e non
fa altro. Risultato: **una pagina con un diagramma mostra il sorgente del
diagramma.**

### E la roba c'è per obbligo

`jenny/skills/llm-wiki/SKILL.md`, principio numero 2:

> «**Ogni** flusso, sequenza, gerarchia o stato **deve** essere scritto in
> mermaid — mai ASCII art» — ed è un cancello nella *definition of done*, due
> volte: «nessun diagramma lasciato in ASCII».
>
> «**Ogni** formula deve essere scritta in KaTeX: inline `$f(x)$` o blocco `$$`».

Con otto wiki vere, quei blocchi ci sono già. Non è una funzione da valutare: è
contenuto scritto per regola che oggi non si legge.

### Perché nessun test l'ha preso

- Il banco che ho scritto io (`test_officina_cassetti_contract.py`, «un solo
  lettore ciascuno») controlla che **i file siano spariti**. Mai che non sia
  rimasto qualcuno a chiamarli.
- `test_message_bubble_client.py:103` **finge** la funzione:
  `const renderKaTeX = () => {};`. Il banco delle bolle non poteva accorgersene.

## La decisione

Tornano entrambe, in officina **e** in casa.

**Interpretazione di «sia officina che casa», da confermare se sbagliata:** ogni
superficie che mostra contenuto scritto — la chat dell'officina, la chat di
casa, il lettore delle pagine in casa — rende formule e diagrammi. Tre
superfici, stesso comportamento.

I diagrammi **in chat** sono una cosa **nuova**: non hanno mai disegnato, né
prima né dopo. `initMarked()` incarta *ogni* blocco recintato — mermaid compreso
— in `<div class="chat-code-block">`, e il commento lì accanto che parla di
`<pre class="mermaid">` è vecchio di un refactor: quella classe non la produce
nessuno.

## Il criterio: **pigro, non all'avvio**

Mermaid era già pigro: `ensureVendor(...)` e solo se nella pagina c'era davvero
un diagramma. Torna così.

KaTeX era **globale**: due `<script defer>` in `officina.html`, cioè 275 kB di
JS + 23 kB di CSS a ogni singolo avvio, anche solo per aprire la chat. **Non
torna così.** Torna pigro come mermaid: si carica solo se nel contenuto appena
disegnato c'è davvero una formula.

Questo cambia anche cosa vuol dire «pesa 1,4 MB»: nel pacchetto sì, all'avvio
no.

## I pezzi, in ordine di rischio

### 1. Il caricatore non sa caricare un foglio di stile

`shared/utils.js::ensureVendor(src)` crea un `<script>`. KaTeX ha bisogno anche
di `katex.min.css`, o le formule si disegnano senza glifi e senza spaziatura —
peggio del testo grezzo. Serve il gemello per gli stili, con lo stesso patto:
una promessa per URL, e **il fallimento non si mette in cache** (rete assente al
primo colpo, asset non ancora estratto).

### 2. Un modulo condiviso, non due copie

`assets/shared/rich-content.js`, importato dalle tre superfici:

- `renderMath(container)` — trova le formule e le disegna, caricando KaTeX solo
  se ce ne sono.
- `renderDiagrams(container)` — trova i diagrammi e li disegna, caricando
  Mermaid solo se ce ne sono.

Due copie di questo codice è come è cominciato il guaio: `mobile-chat.js` e
`mobile-wiki.js` avevano ciascuno il proprio `renderKaTeX`, e quando ne è morto
uno nessuno ha guardato l'altro.

### 3. I diagrammi hanno **due** forme, non una

| chi scrive | cosa produce |
|---|---|
| server, pagine wiki | `<pre class="mermaid-block"><code class="language-mermaid">` |
| `marked`, in chat | `<div class="chat-code-block">…<pre><code class="hljs language-mermaid">` |

`renderDiagrams` deve riconoscerle entrambe, o in chat i diagrammi restano
codice colorato. Il testo da dare a mermaid è il `textContent` del `<code>`, già
de-escapato dal browser in entrambi i casi.

### 4. Il dollaro singolo è una trappola, e non la stessa ovunque

`$...$` inline significa che «costa $5, forse $10» diventa un tentativo di
rendere «5, forse » come matematica. È il difetto classico di auto-render, e in
una chat dove si parla di soldi sbaglia più spesso di quanto azzecchi.

Ma la skill **impone a Jenny** l'inline `$f(x)$` nelle pagine.

**Proposta: delimitatori diversi per superficie.**

- **Lettore delle pagine** — tutti e quattro: `$$`, `$`, `\[`, `\(`. Lì il `$`
  inline è la regola della casa, e il testo è scritto da chi la conosce.
- **Chat, entrambe** — solo i non ambigui: `$$`, `\[`, `\(`. Una formula in
  chat si scrive lo stesso; un prezzo non si rompe.

E in ogni caso `ignoredTags` per `code`/`pre`: una formula dentro un blocco di
codice è codice, non matematica.

### 5. I font: 60 file, e il telefono ne usa un terzo

KaTeX porta ogni faccia in tre formati (`ttf`, `woff`, `woff2`), ~1 MB in tutto.
Il CSS li elenca in quest'ordine: **woff2 per primo**, e la WebView di Android
lo supporta — quindi `ttf` e `woff` non vengono mai chiesti. Restano ~700 kB di
pacchetto che non si aprono mai.

**Decisione da prendere, non da nascondere in un commit:** spedire solo i
`woff2` (~250 kB invece di ~1 MB) o rimettere tutto com'era. Io spedirei solo i
woff2 — il rischio è nullo finché la WebView è quella, e la WebView è l'unico
runtime che questo progetto ha. Ma è un cambiamento rispetto a «rimetti com'era»,
quindi lo chiedo.

### 6. Il tema

Mermaid senza `initialize()` usa la palette chiara: riquadri bianchi su fondo
scuro. Il codice vecchio (`mobile-wiki.js:561-573`) lo sapeva e passava il tema
corrente. Va ripreso — e adesso vale per due chat e un lettore, con la casa che
può cambiare tema **mentre** un diagramma è a schermo.

## Cosa si tocca

| file | cosa |
|---|---|
| `assets/vendor/katex@0.16.10/**` | ripristinati da `0116b1f^` (font: v. §5) |
| `assets/vendor/mermaid@10/**` | ripristinato da `0116b1f^` |
| `jenny/utils/android_assets.py` | le righe di manifesto tolte (69) tornano |
| `THIRD_PARTY_NOTICES.md` | le due voci tornano |
| `assets/shared/utils.js` | il caricatore di fogli di stile |
| `assets/shared/rich-content.js` | **nuovo** — i due renderer |
| `assets/mobile-chat.js` | i quattro `renderKaTeX` puntano al modulo; + diagrammi |
| `assets/casa-chat.js` | stessa chiamata, dove oggi non c'è niente |
| `assets/casa-reader.js` | dopo `innerHTML`: formule e diagrammi |
| `assets/mobile-style.css`, `casa-style.css` | lo stile dei blocchi diagramma |
| `officina.html` | **niente**: KaTeX non torna globale |

## I banchi

**Il primo è quello che ripara la bugia**, e va scritto perché prenda la *forma*
del difetto, non il caso singolo:

> per ogni libreria di vendor, o nessuno la chiama, o la libreria è spedita.

Cioè: si cercano i chiamanti (`renderMathInElement`, `mermaid.render`, …); se ce
n'è uno, il file dev'essere su disco **e** nel manifesto. Questo prende sia il
difetto di oggi (libreria tolta, chiamanti rimasti) sia il suo opposto (libreria
spedita che non chiama nessuno, cioè il peso morto che lo sfoltimento cercava).

Poi, in node su DOM finto:

1. tre superfici, stesso patto: chi disegna markdown chiama entrambi i renderer;
2. niente formule nel contenuto → **nessun caricamento** (il pigro è pigro);
3. una formula → si carica, e una sola volta anche con dieci messaggi;
4. il diagramma si riconosce in **entrambe** le forme (server e `marked`);
5. `$5 e $10` in chat non diventa matematica; `$f(x)$` in una pagina sì;
6. una formula dentro un blocco di codice resta codice;
7. libreria che non carica → il contenuto resta leggibile (niente pagina rotta);
8. tema scuro → mermaid inizializzato scuro.

Ogni banco provato rosso con una mutazione prima di dirlo fatto.

## Ordine, un commit per passo

1. **Il difetto, da solo**: i quattro chiamanti orfani in chat + il banco che
   dice la bugia. Senza ancora rimettere niente — così si vede che il banco
   nuovo è rosso sul codice di oggi.
2. Vendor, manifesto, NOTICES.
3. `ensureVendorStyle` + `shared/rich-content.js` + i banchi in node.
4. Le tre superfici, una alla volta.
5. Stili e tema.

Verifica a ogni passo: `ruff check jenny/ tests/`, il sottoinsieme `pyright`
bloccante, `pytest -q`.

**E alla fine sul telefono**, che è l'unico posto dove si vede davvero: una
pagina con un diagramma, una con una formula, un messaggio in chat con `$$`, e
un messaggio che parla di prezzi.

## Fuori da questo giro

- **La mappa di casa** (`casa-map.js`): resta la domanda aperta sul perché non
  risponda al dito. Non c'entra con queste due librerie — usa d3, che non ho mai
  toccato — e si guarda col telefono attaccato.
- **La chat di casa non ha `highlight.js`**: `index.html` non lo carica, quindi
  lì i blocchi di codice sono grigi. Misurato passando di qui, non deciso.

---

# Fatto, e misurato sul telefono — 21/09/2026 sera

## Quanto contenuto c'era davvero

Contato nel workspace vero, col telefono attaccato:

| | quanti |
|---|---|
| pagine con un diagramma mermaid | **22** |
| pagine con una formula `$$` | **3** (tutte in `quaderno-a1`) |

I 22 diagrammi chiudono la domanda: non era una funzione da valutare, era
contenuto gia' scritto che si vedeva come sorgente.

## L'ironia che conferma la scelta del dollaro

Le uniche formule stanno in un quaderno che parla anche di prezzi — cioe'
proprio dove `$` potrebbe voler dire dollari. Guardato dentro una sua pagina
(qui con una formula di esempio, della stessa forma):

```
$$v = \frac{d}{t}$$
Where $v$ = speed, $d$ = distance, $t$ = time.
```

Formule a blocco, formule in riga col dollaro singolo, e **nessun prezzo scritto
col dollaro**: nelle pagine quel simbolo e' matematica per regola della casa,
esattamente come la skill promette. La regola per superficie regge sui dati
veri, non solo sull'ipotesi.

## Cosa si e' visto a schermo

- **Diagramma** (`quaderno-a1` → una pagina con un flowchart): flowchart disegnato, tema
  scuro, ci sta in larghezza senza scorrere. Non serve toccare lo stile.
- **Formula a blocco** (la pagina di prima): frazione vera, tipografia KaTeX.
- **Formule in riga** nella stessa pagina: `v`, `d`, `t` dentro la frase.
- **Le percentuali accanto** («50%», «100%») intatte: nessun falso positivo.

## E la mappa non e' rotta

La domanda che ha aperto tutto il giro. Sul quaderno `jenny`: la mappa **si
disegna** (due nodi, un filo) e **si trascina** — swipe misurato con due
scatti, il contenuto si sposta col dito. Restano le due spiegazioni gia' date:
il trascinamento dei *nodi* non e' mai esistito in casa (era del grafo
dell'officina), e su un quaderno senza collegamenti la mappa non disegna niente
— e senza niente non c'e' niente da spostare.

## Cosa resta non visto

Formule e diagrammi **in chat**: le tre superfici condividono lo stesso modulo,
e i banchi coprono il cablaggio, ma a schermo non si sono viste — servirebbe un
messaggio che le contenga, cioe' scrivere nella conversazione vera.

## Nota sul registro: due sessioni, un albero solo

Il cablaggio della chat di casa, quello del lettore e i due blocchi di stile del
diagramma **stanno dentro `d60e2d4`**, che e' un commit di un'altra sessione
sulla camera della mappa. Non e' un errore di nessuno dei due: si lavora sullo
stesso working tree, e un `git add -A` raccoglie anche quel che l'altro ha a
meta'. Niente e' andato perso — l'albero e' giusto, la suite verde e le prove
sul telefono sono state fatte su quel codice.

Non si riscrive la storia con un'altra sessione dentro l'albero: la convenzione
qui e' nominare le righe prese in prestito nel commit dopo, ed e' questa nota.

