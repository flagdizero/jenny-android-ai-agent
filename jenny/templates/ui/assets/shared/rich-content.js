/** Formule e diagrammi dentro contenuto gia' disegnato.
 *
 *  **Un modulo solo, ed e' il punto.** Prima ce n'erano due copie — una nella
 *  chat dell'officina, una nel lettore della wiki — e il 21/09/2026 e' morta
 *  quella della wiki senza che nessuno guardasse l'altra: le librerie sono
 *  state cancellate «perche' aveva un lettore solo», e i quattro chiamanti
 *  rimasti in chat hanno smesso di fare qualcosa **in silenzio**, perche'
 *  cominciavano tutti con «se la libreria c'e'».
 *
 *  **Pigro, non all'avvio.** KaTeX stava in due `<script defer>` in
 *  `workshop.html`: 275 kB di codice piu' il CSS a ogni singola partenza, anche
 *  solo per aprire la chat. Qui si carica quando nel contenuto appena disegnato
 *  c'e' davvero una formula, come ha sempre fatto mermaid. Nel pacchetto pesa,
 *  all'avvio no.
 */

import { ensureVendor, ensureVendorStyle } from './utils.js';

const KATEX_JS = '/html-mobile/assets/vendor/katex@0.16.10/dist/katex.min.js';
const KATEX_AUTO = '/html-mobile/assets/vendor/katex@0.16.10/dist/contrib/auto-render.min.js';
const KATEX_CSS = '/html-mobile/assets/vendor/katex@0.16.10/dist/katex.min.css';
const MERMAID_JS = '/html-mobile/assets/vendor/mermaid@10/dist/mermaid.min.js';

/* I delimitatori che non possono voler dire altro. */
const SAFE_DELIMITERS = [
  { left: '$$', right: '$$', display: true },
  { left: '\\[', right: '\\]', display: true },
  { left: '\\(', right: '\\)', display: false },
];

/* Il dollaro singolo, che la skill `llm-wiki` impone a Jenny per le formule in
   riga (`$f(x)$`) e che **fuori da una pagina e' una trappola**: «costa $5,
   forse $10» diventa un tentativo di scrivere «5, forse » in matematica. Sta
   qui da solo perche' si accende per superficie, non ovunque. */
const DOLLAR_IN_LINE = { left: '$', right: '$', display: false };

/* Dentro un blocco di codice un dollaro e' un prompt di shell, e una barra
   rovesciata e' una barra rovesciata. KaTeX li salta. */
const IGNORED_TAGS = ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'];

/** Il testo di *root* saltando codice e blocchi di codice.
 *
 *  Serve **prima** di caricare, non dopo: cercare il dollaro nel `textContent`
 *  intero vuol dire che un messaggio con dentro `cd ~ && $EDITOR file` tira giu'
 *  300 kB per niente — e in una chat con chi scrive software quello e' il caso
 *  normale, non il raro. Salta gli stessi tag che poi KaTeX ignora: se il
 *  criterio per accendere non e' lo stesso del criterio per disegnare, si carica
 *  la libreria per contenuto che non verra' toccato.
 */
function textOutsideCode(root) {
  const skip = new Set(IGNORED_TAGS.map((t) => t.toUpperCase()));
  let text = '';
  const walk = (node) => {
    for (const child of node.childNodes || []) {
      if (child.nodeType === 3) text += child.nodeValue;
      else if (child.nodeType === 1 && !skip.has(child.tagName)) walk(child);
    }
  };
  walk(root);
  return text;
}

/** C'e' davvero una formula da disegnare in *testo*? */
function containsFormula(text, dollar) {
  if (/\$\$[\s\S]+?\$\$/.test(text)) return true;
  if (/\\\[[\s\S]+?\\\]/.test(text)) return true;
  if (/\\\([\s\S]+?\\\)/.test(text)) return true;
  // Il dollaro in riga si cerca solo dove e' acceso, e su una riga sola: una
  // formula in riga non va a capo, mentre due prezzi in due paragrafi diversi
  // si', ed e' esattamente la coppia che non deve accendere niente.
  return dollar && /\$[^$\n]+\$/.test(text);
}

/** Disegna le formule dentro *container*, caricando KaTeX solo se ce n'e'.
 *
 *  `inlineDollar` accende `$...$`: **vero nel lettore delle pagine**, dove la
 *  skill impone quella forma e chi scrive conosce la regola della casa; falso
 *  in chat, dove si parla di prezzi.
 *
 *  Un fallimento di rete non rompe niente: il contenuto e' gia' a schermo e
 *  resta leggibile, con le formule in chiaro. Era cosi' anche prima, ed e' il
 *  motivo per cui la cosa e' passata inosservata — quindi qui il fallimento si
 *  **dice** nel log, invece di uscire in punta di piedi.
 */
async function renderMath(container, { inlineDollar = false } = {}) {
  if (!container) return;
  if (!containsFormula(textOutsideCode(container), inlineDollar)) return;
  try {
    await Promise.all([
      ensureVendorStyle(KATEX_CSS),
      ensureVendor(KATEX_JS).then(() => ensureVendor(KATEX_AUTO)),
    ]);
  } catch (err) {
    console.warn('rich-content: KaTeX failed to load', err);
    return;
  }
  if (typeof renderMathInElement !== 'function') {
    console.warn('rich-content: KaTeX loaded but renderMathInElement is missing');
    return;
  }
  const delimiters = inlineDollar
    ? [...SAFE_DELIMITERS, DOLLAR_IN_LINE]
    : SAFE_DELIMITERS;
  try {
    renderMathInElement(container, {
      delimiters,
      ignoredTags: IGNORED_TAGS,
      throwOnError: false,
    });
  } catch (err) {
    console.warn('rich-content: KaTeX render', err);
  }
}

/** Disegna i diagrammi dentro *container*, caricando Mermaid solo se ce n'e'.
 *
 *  **Un selettore per due forme.** Il server, che disegna le pagine della wiki,
 *  scrive `<pre class="mermaid-block"><code class="language-mermaid">`;
 *  `marked`, che disegna la chat, scrive
 *  `<div class="chat-code-block">…<code class="hljs language-mermaid">`. Il
 *  `<code>` porta la stessa classe in tutti e due i casi, quindi si cerca
 *  quello e si risale al blocco da sostituire. Cercare `pre.mermaid-block`,
 *  come faceva il lettore vecchio, lascia i diagrammi in chat come codice
 *  colorato.
 */
export async function renderDiagrams(container) {
  if (!container) return;
  const blocks = [...container.querySelectorAll('code.language-mermaid')];
  if (!blocks.length) return;
  try {
    // 3,3 MB: qui dentro ci sta comodamente un cambio di schermata, e chi
    // chiama deve poter dire che non e' piu' il suo turno (v. `stale`).
    await ensureVendor(MERMAID_JS);
  } catch (err) {
    console.warn('rich-content: Mermaid failed to load', err);
    return;
  }
  if (typeof mermaid === 'undefined') return;
  /* Senza `initialize` mermaid usa la palette chiara: riquadri lavanda su
     fondo scuro. Dei sette temi quattro sono scuri, quindi la scelta segue
     `color-scheme` invece di essere fissata — e si rifa' a ogni passata, cosi'
     un cambio di tema a contenuto aperto viene raccolto. */
  const light = getComputedStyle(document.documentElement).colorScheme === 'light';
  mermaid.initialize({
    startOnLoad: false,
    theme: light ? 'default' : 'dark',
    // I diagrammi arrivano dal modello: `strict` tiene l'HTML fuori dalle
    // etichette. E' gia' il default di mermaid, lo rendiamo esplicito.
    securityLevel: 'strict',
  });
  await Promise.all(blocks.map(async (code, i) => {
    // Il nodo da sostituire e' l'involucro intero, non il `<code>`: in chat
    // quello porta con se' l'intestazione con «mermaid» e il tasto Copia, che
    // accanto a un disegno non vogliono dire piu' niente.
    const block = code.closest('.chat-code-block') || code.closest('pre') || code;
    try {
      const { svg } = await mermaid.render(`mermaid-${Date.now()}-${i}`, code.textContent);
      // Staccato mentre mermaid disegnava (cambio pagina, ridisegno): non si
      // riscrive un nodo che non e' piu' a schermo.
      if (!block.isConnected) return;
      const home = document.createElement('div');
      home.className = 'diagram';
      home.innerHTML = svg;
      block.replaceWith(home);
    } catch (err) {
      // Un diagramma con un errore di sintassi resta il suo sorgente, che e'
      // leggibile: mermaid altrimenti pianta a schermo il proprio messaggio
      // d'errore in inglese, piu' grande del diagramma.
      console.warn('rich-content: Mermaid render', err);
    }
  }));
}

/** Le due cose insieme, che e' come le vuole ogni chiamante. */
export async function renderRich(container, opts) {
  await Promise.all([renderMath(container, opts), renderDiagrams(container)]);
}
