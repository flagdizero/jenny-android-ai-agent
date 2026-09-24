"""Formule e diagrammi: quando si caricano, e cosa toccano.

Sei cose che leggendo il sorgente non si dimostrano, e che girano qui in node su
un DOM finto.

**La prima e' la ragione per cui questo modulo esiste.** Le librerie erano
caricate da due copie di codice — una in chat, una nel lettore della wiki — e
quando e' morta una copia le librerie sono state cancellate «perche' le usava
solo la wiki». Le chiamate rimaste erano tutte dietro un «se la libreria c'e'»,
quindi si sono spente senza un errore. Il patto che lo impedisce e' che il
*come* stia in un posto solo, e che il caricamento sia **misurabile**: qui si
conta.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
RICH = ROOT / "jenny" / "templates" / "ui" / "assets" / "shared" / "rich-content.js"

pytestmark = requires_node

_IMPORT = "import { ensureVendor, ensureVendorStyle } from './utils.js';"

_HARNESS = """
import assert from 'node:assert/strict';

/* Chi e' stato chiesto, e in che ordine. E' la misura di «pigro». */
const caricati = [];
let caricamentoFallisce = false;
function ensureVendor(src) {
  caricati.push(src);
  return caricamentoFallisce ? Promise.reject(new Error('giu')) : Promise.resolve();
}
function ensureVendorStyle(href) {
  caricati.push(href);
  return caricamentoFallisce ? Promise.reject(new Error('giu')) : Promise.resolve();
}

/* Un DOM finto con quel poco che il modulo tocca: camminata sui figli,
   `code.language-mermaid`, `closest`, `replaceWith`. */
function _match(nodo, sel) {
  const [tag, ...classi] = sel.split('.');
  if (tag && nodo.tagName !== tag.toUpperCase()) return false;
  return classi.every((c) => nodo.className.split(/\\s+/).includes(c));
}
function el(tag, figli = [], className = '') {
  const n = {
    nodeType: 1, tagName: tag.toUpperCase(), className, childNodes: [],
    isConnected: true, parentNode: null,
    get textContent() {
      let t = '';
      const giu = (x) => x.childNodes.forEach((f) =>
        f.nodeType === 3 ? (t += f.nodeValue) : giu(f));
      giu(n);
      return t;
    },
    set innerHTML(v) { n._html = v; n.childNodes = []; },
    get innerHTML() { return n._html || ''; },
    appendChild(f) { f.parentNode = n; n.childNodes.push(f); return f; },
    querySelectorAll(sel) {
      const out = [];
      const giu = (x) => x.childNodes.forEach((f) => {
        if (f.nodeType !== 1) return;
        if (_match(f, sel)) out.push(f);
        giu(f);
      });
      giu(n);
      return out;
    },
    closest(sel) {
      let x = n;
      while (x) { if (_match(x, sel)) return x; x = x.parentNode; }
      return null;
    },
    replaceWith(nuovo) {
      const p = n.parentNode;
      if (!p) return;
      p.childNodes[p.childNodes.indexOf(n)] = nuovo;
      nuovo.parentNode = p;
      n.isConnected = false;
    },
  };
  for (const f of figli) n.appendChild(f);
  return n;
}
const txt = (v) => ({ nodeType: 3, nodeValue: v, childNodes: [] });

globalThis.document = {
  documentElement: {},
  createElement: (tag) => el(tag),
};
let schema = 'dark';
globalThis.getComputedStyle = () => ({ colorScheme: schema });

/* Le librerie, quando arrivano. */
const reseFormule = [];
globalThis.renderMathInElement = (container, opts) => {
  reseFormule.push({ container, delimitatori: opts.delimiters.map((d) => d.left), opts });
};
const reseDiagrammi = [];
globalThis.mermaid = {
  _config: null,
  initialize(c) { globalThis.mermaid._config = c; },
  render(id, codice) {
    reseDiagrammi.push(codice);
    return Promise.resolve({ svg: `<svg data-da="${codice.trim()}"></svg>` });
  },
};

__MODULO__

/* Le due forme che un diagramma puo' avere a schermo. */
function bloccoServer(codice) {
  return el('pre', [el('code', [txt(codice)], 'language-mermaid')], 'mermaid-block');
}
function bloccoChat(codice) {
  return el('div', [
    el('div', [txt('mermaid')], 'chat-code-header'),
    el('pre', [el('code', [txt(codice)], 'hljs language-mermaid')]),
  ], 'chat-code-block');
}
function corpo(figli) { return el('div', figli); }
function reset() {
  caricati.length = 0; reseFormule.length = 0; reseDiagrammi.length = 0;
  caricamentoFallisce = false; schema = 'dark';
}
"""


def _run(script: str) -> None:
    modulo = RICH.read_text(encoding="utf-8").replace(_IMPORT, "")
    assert _IMPORT not in modulo, "l'import di rich-content e' cambiato: il banco non lo stubba piu'"
    sorgente = _HARNESS.replace("__MODULO__", modulo) + "\n" + script
    run_js(sorgente)


def test_plain_text_loads_nothing_at_all() -> None:
    """Il punto di tutto il giro. KaTeX stava in due `<script>` all'avvio: 275 kB
    piu' il foglio di stile a ogni partenza, per una cosa che compare in un
    messaggio su cento."""
    _run("""
reset();
await renderRich(corpo([txt('ciao, come va? tutto bene')]));
assert.deepEqual(caricati, [], 'ha caricato qualcosa per del testo semplice');
""")


def test_a_shell_prompt_is_not_a_formula() -> None:
    """Il caso che rende inutile cercare il dollaro nel testo intero: in una chat
    con chi scrive software, un messaggio con dentro `$EDITOR` o `$PATH` e' il
    caso normale. Cercarlo anche nel codice vuol dire 275 kB su quasi ogni
    messaggio — e per contenuto che KaTeX poi salta comunque."""
    _run("""
reset();
const msg = corpo([
  txt('esporta cosi:'),
  el('pre', [el('code', [txt('export $EDITOR=vim && echo $PATH')])]),
]);
await renderRich(msg, { inlineDollar: true });
assert.deepEqual(caricati, [], 'un prompt di shell ha tirato giu KaTeX');
""")


def test_money_is_not_maths_in_chat_but_inline_is_in_a_page() -> None:
    """La regola che cambia per superficie, e la ragione per cui esiste: «costa
    $5, forse $10» in chat diventerebbe un tentativo di scrivere «5, forse » in
    matematica. Nelle pagine la skill `llm-wiki` **impone** `$f(x)$`, e chi le
    scrive conosce la regola della casa."""
    _run("""
reset();
await renderRich(corpo([txt('costa $5, forse $10')]));
assert.deepEqual(caricati, [], 'in chat due prezzi hanno acceso la matematica');

reset();
await renderRich(corpo([txt('la somma $f(x) = w_i x_i$ pesa')]), { inlineDollar: true });
assert.ok(caricati.length, 'in una pagina il dollaro in riga non ha acceso niente');
assert.ok(reseFormule[0].delimitatori.includes('$'), 'manca il delimitatore in riga');

// E in chat una formula vera si scrive lo stesso, coi delimitatori non ambigui.
reset();
await renderRich(corpo([txt('vale $$E = mc^2$$ sempre')]));
assert.ok(reseFormule.length === 1, 'in chat $$ non ha disegnato');
assert.ok(!reseFormule[0].delimitatori.includes('$'),
  'la chat ha acceso anche il dollaro in riga');
""")


def test_the_stylesheet_comes_with_the_code() -> None:
    """Senza il CSS, KaTeX disegna con i font del documento e senza spaziatura:
    una formula sbriciolata, peggio del `$...$` da cui si parte. Si chiedono
    insieme, non in fila: sono due richieste indipendenti."""
    _run("""
reset();
await renderRich(corpo([txt('vale $$E = mc^2$$')]));
assert.ok(caricati.some((s) => s.endsWith('katex.min.css')), 'manca il foglio di stile');
assert.ok(caricati.some((s) => s.endsWith('katex.min.js')), 'manca il codice');
assert.ok(caricati.some((s) => s.endsWith('auto-render.min.js')), 'manca auto-render');
""")


def test_code_blocks_stay_code() -> None:
    """Una formula dentro un blocco di codice e' codice. Lo dice `ignoredTags`, e
    deve dirlo con gli stessi tag che la ricerca salta: se il criterio per
    accendere e' piu' largo di quello per disegnare, si carica la libreria per
    roba che non verra' toccata."""
    _run("""
reset();
await renderRich(corpo([txt('vale $$x$$')]));
const ignorati = reseFormule[0].opts.ignoredTags.map((t) => t.toLowerCase());
assert.ok(ignorati.includes('code') && ignorati.includes('pre'),
  'KaTeX entrerebbe nei blocchi di codice');
""")


def test_a_diagram_is_recognised_in_both_shapes() -> None:
    """Il server scrive `<pre class="mermaid-block">`, `marked` scrive
    `<div class="chat-code-block">`. Il lettore vecchio cercava solo la prima, ed
    e' il motivo per cui in chat un diagramma non ha mai disegnato niente."""
    _run("""
reset();
const pagina = corpo([bloccoServer('flowchart LR\\n A --> B')]);
await renderDiagrams(pagina);
assert.deepEqual(reseDiagrammi.map((c) => c.trim()), ['flowchart LR\\n A --> B']);

reset();
const chat = corpo([bloccoChat('graph TD\\n X --> Y')]);
await renderDiagrams(chat);
assert.deepEqual(reseDiagrammi.map((c) => c.trim()), ['graph TD\\n X --> Y'],
  'in chat il diagramma resta codice colorato');
""")


def test_the_whole_code_block_is_replaced_not_just_the_code() -> None:
    """In chat il blocco porta un'intestazione con la parola «mermaid» e il tasto
    Copia: accanto a un disegno non vogliono dire piu' niente, e lasciarli fa
    sembrare che il disegno sia dentro un blocco di codice."""
    _run("""
reset();
const chat = corpo([bloccoChat('graph TD\\n X --> Y')]);
await renderDiagrams(chat);
assert.equal(chat.childNodes.length, 1);
assert.equal(chat.childNodes[0].className, 'diagramma',
  "l'involucro del blocco di codice e' rimasto intorno al disegno");
""")


def test_nothing_is_loaded_for_a_page_without_diagrams() -> None:
    """Mermaid sono 3,3 MB: aprire una pagina di solo testo non deve costarli."""
    _run("""
reset();
await renderDiagrams(corpo([el('pre', [el('code', [txt('print(1)')], 'language-python')])]));
assert.deepEqual(caricati, [], 'ha caricato mermaid per del python');
""")


def test_the_dark_theme_reaches_mermaid() -> None:
    """Senza `initialize`, mermaid usa la palette chiara: riquadri lavanda su
    fondo scuro. Dei sette temi quattro sono scuri, quindi segue `color-scheme`
    invece di essere fissato — e si rifa' a ogni passata, cosi' un cambio di tema
    a contenuto aperto viene raccolto."""
    _run("""
reset();
await renderDiagrams(corpo([bloccoServer('graph TD\\n A --> B')]));
assert.equal(mermaid._config.theme, 'dark');
assert.equal(mermaid._config.securityLevel, 'strict',
  "i diagrammi arrivano dal modello: l'HTML sta fuori dalle etichette");

reset(); schema = 'light';
await renderDiagrams(corpo([bloccoServer('graph TD\\n A --> B')]));
assert.equal(mermaid._config.theme, 'default');
""")


def test_a_library_that_will_not_load_leaves_the_content_readable() -> None:
    """Rete assente, o asset non ancora estratto: il contenuto e' gia' a schermo
    e deve restarci. Una pagina rotta perche' un diagramma non si e' scaricato
    sarebbe peggio del diagramma non disegnato."""
    _run("""
reset(); caricamentoFallisce = true;
const pagina = corpo([txt('vale $$E$$'), bloccoServer('graph TD\\n A --> B')]);
await renderRich(pagina, { inlineDollar: true });
assert.deepEqual(reseFormule, []);
assert.deepEqual(reseDiagrammi, []);
assert.equal(pagina.childNodes.length, 2, 'il contenuto e stato smontato');
""")
