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
const loaded = [];
let loadFails = false;
/* Quando c'e', ogni script resta in attesa finche' il banco non lo risolve a
   mano: e' cosi' che si vede *quando* parte una richiesta, non solo che parte. */
let suspended = null;
function ensureVendor(src) {
  loaded.push(src);
  if (suspended) return new Promise((ok) => suspended.set(src, ok));
  return loadFails ? Promise.reject(new Error('giu')) : Promise.resolve();
}
function ensureVendorStyle(href) {
  loaded.push(href);
  return loadFails ? Promise.reject(new Error('giu')) : Promise.resolve();
}

/* Un DOM finto con quel poco che il modulo tocca: camminata sui figli,
   `code.language-mermaid`, `closest`, `replaceWith`. */
function _match(node, sel) {
  const [tag, ...classes] = sel.split('.');
  if (tag && node.tagName !== tag.toUpperCase()) return false;
  return classes.every((c) => node.className.split(/\\s+/).includes(c));
}
function el(tag, children = [], className = '') {
  const n = {
    nodeType: 1, tagName: tag.toUpperCase(), className, childNodes: [],
    isConnected: true, parentNode: null,
    get textContent() {
      let t = '';
      const down = (x) => x.childNodes.forEach((f) =>
        f.nodeType === 3 ? (t += f.nodeValue) : down(f));
      down(n);
      return t;
    },
    set innerHTML(v) { n._html = v; n.childNodes = []; },
    get innerHTML() { return n._html || ''; },
    appendChild(f) { f.parentNode = n; n.childNodes.push(f); return f; },
    querySelectorAll(sel) {
      const out = [];
      const down = (x) => x.childNodes.forEach((f) => {
        if (f.nodeType !== 1) return;
        if (_match(f, sel)) out.push(f);
        down(f);
      });
      down(n);
      return out;
    },
    closest(sel) {
      let x = n;
      while (x) { if (_match(x, sel)) return x; x = x.parentNode; }
      return null;
    },
    replaceWith(fresh) {
      const p = n.parentNode;
      if (!p) return;
      p.childNodes[p.childNodes.indexOf(n)] = fresh;
      fresh.parentNode = p;
      n.isConnected = false;
    },
  };
  for (const f of children) n.appendChild(f);
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
const renderedFormulas = [];
globalThis.renderMathInElement = (container, opts) => {
  renderedFormulas.push({ container, delimiters: opts.delimiters.map((d) => d.left), opts });
};
const renderedDiagrams = [];
globalThis.mermaid = {
  _config: null,
  initialize(c) { globalThis.mermaid._config = c; },
  render(id, code) {
    renderedDiagrams.push(code);
    return Promise.resolve({ svg: `<svg data-da="${code.trim()}"></svg>` });
  },
};

__MODULE__

/* Le due forme che un diagramma puo' avere a schermo. */
function serverBlock(code) {
  return el('pre', [el('code', [txt(code)], 'language-mermaid')], 'mermaid-block');
}
function chatBlock(code) {
  return el('div', [
    el('div', [txt('mermaid')], 'chat-code-header'),
    el('pre', [el('code', [txt(code)], 'hljs language-mermaid')]),
  ], 'chat-code-block');
}
function body(children) { return el('div', children); }
function reset() {
  loaded.length = 0; renderedFormulas.length = 0; renderedDiagrams.length = 0;
  loadFails = false; schema = 'dark'; suspended = null;
}
"""


def _run(script: str) -> None:
    module = RICH.read_text(encoding="utf-8").replace(_IMPORT, "")
    assert _IMPORT not in module, "l'import di rich-content e' cambiato: il banco non lo stubba piu'"
    source = _HARNESS.replace("__MODULE__", module) + "\n" + script
    run_js(source)


def test_plain_text_loads_nothing_at_all() -> None:
    """Il punto di tutto il giro. KaTeX stava in due `<script>` all'avvio: 275 kB
    piu' il foglio di stile a ogni partenza, per una cosa che compare in un
    messaggio su cento."""
    _run("""
reset();
await renderRich(body([txt('ciao, come va? tutto bene')]));
assert.deepEqual(loaded, [], 'ha caricato qualcosa per del testo semplice');
""")


def test_a_shell_prompt_is_not_a_formula() -> None:
    """Il caso che rende inutile cercare il dollaro nel testo intero: in una chat
    con chi scrive software, un messaggio con dentro `$EDITOR` o `$PATH` e' il
    caso normale. Cercarlo anche nel codice vuol dire 275 kB su quasi ogni
    messaggio — e per contenuto che KaTeX poi salta comunque."""
    _run("""
reset();
const msg = body([
  txt('esporta cosi:'),
  el('pre', [el('code', [txt('export $EDITOR=vim && echo $PATH')])]),
]);
await renderRich(msg, { inlineDollar: true });
assert.deepEqual(loaded, [], 'un prompt di shell ha tirato giu KaTeX');
""")


def test_money_is_not_maths_in_chat_but_inline_is_in_a_page() -> None:
    """La regola che cambia per superficie, e la ragione per cui esiste: «costa
    $5, forse $10» in chat diventerebbe un tentativo di scrivere «5, forse » in
    matematica. Nelle pagine la skill `llm-wiki` **impone** `$f(x)$`, e chi le
    scrive conosce la regola della casa."""
    _run("""
reset();
await renderRich(body([txt('costa $5, forse $10')]));
assert.deepEqual(loaded, [], 'in chat due prezzi hanno acceso la matematica');

reset();
await renderRich(body([txt('la somma $f(x) = w_i x_i$ pesa')]), { inlineDollar: true });
assert.ok(loaded.length, 'in una pagina il dollaro in riga non ha acceso niente');
assert.ok(renderedFormulas[0].delimiters.includes('$'), 'manca il delimitatore in riga');

// E in chat una formula vera si scrive lo stesso, coi delimitatori non ambigui.
reset();
await renderRich(body([txt('vale $$E = mc^2$$ sempre')]));
assert.ok(renderedFormulas.length === 1, 'in chat $$ non ha disegnato');
assert.ok(!renderedFormulas[0].delimiters.includes('$'),
  'la chat ha acceso anche il dollaro in riga');
""")


def test_the_stylesheet_comes_with_the_code() -> None:
    """Senza il CSS, KaTeX disegna con i font del documento e senza spaziatura:
    una formula sbriciolata, peggio del `$...$` da cui si parte. Si chiedono
    insieme, non in fila: sono due richieste indipendenti."""
    _run("""
reset();
await renderRich(body([txt('vale $$E = mc^2$$')]));
assert.ok(loaded.some((s) => s.endsWith('katex.min.css')), 'manca il foglio di stile');
assert.ok(loaded.some((s) => s.endsWith('katex.min.js')), 'manca il codice');
assert.ok(loaded.some((s) => s.endsWith('auto-render.min.js')), 'manca auto-render');
""")


def test_auto_render_is_asked_for_only_after_katex_has_arrived() -> None:
    """``auto-render`` chiama ``katex`` appena si carica: chiederlo insieme a
    KaTeX vuol dire una corsa, persa ogni volta che il file piccolo arriva prima
    di quello grande. Qui KaTeX si risolve a mano, e fino ad allora auto-render
    non deve essere stato nemmeno chiesto."""
    _run("""
reset();
suspended = new Map();
const tick = () => new Promise((r) => setTimeout(r, 0));
const resolve = (fine) => [...suspended].find(([src]) => src.endsWith(fine))[1]();
const done = renderRich(body([txt('vale $$E = mc^2$$')]));
await tick();
assert.ok(loaded.some((s) => s.endsWith('katex.min.js')), 'KaTeX non chiesto');
assert.ok(!loaded.some((s) => s.endsWith('auto-render.min.js')),
  'auto-render chiesto prima che KaTeX fosse arrivato');
resolve('katex.min.js');
await tick();
assert.ok(loaded.some((s) => s.endsWith('auto-render.min.js')), 'auto-render mai chiesto');
resolve('auto-render.min.js');
await done;
assert.equal(renderedFormulas.length, 1);
""")


def test_code_blocks_stay_code() -> None:
    """Una formula dentro un blocco di codice e' codice. Lo dice `ignoredTags`, e
    deve dirlo con gli stessi tag che la ricerca salta: se il criterio per
    accendere e' piu' largo di quello per disegnare, si carica la libreria per
    roba che non verra' toccata."""
    _run("""
reset();
await renderRich(body([txt('vale $$x$$')]));
const ignored = renderedFormulas[0].opts.ignoredTags.map((t) => t.toLowerCase());
assert.ok(ignored.includes('code') && ignored.includes('pre'),
  'KaTeX entrerebbe nei blocchi di codice');
""")


def test_a_diagram_is_recognised_in_both_shapes() -> None:
    """Il server scrive `<pre class="mermaid-block">`, `marked` scrive
    `<div class="chat-code-block">`. Il lettore vecchio cercava solo la prima, ed
    e' il motivo per cui in chat un diagramma non ha mai disegnato niente."""
    _run("""
reset();
const page = body([serverBlock('flowchart LR\\n A --> B')]);
await renderDiagrams(page);
assert.deepEqual(renderedDiagrams.map((c) => c.trim()), ['flowchart LR\\n A --> B']);

reset();
const chat = body([chatBlock('graph TD\\n X --> Y')]);
await renderDiagrams(chat);
assert.deepEqual(renderedDiagrams.map((c) => c.trim()), ['graph TD\\n X --> Y'],
  'in chat il diagramma resta codice colorato');
""")


def test_the_whole_code_block_is_replaced_not_just_the_code() -> None:
    """In chat il blocco porta un'intestazione con la parola «mermaid» e il tasto
    Copia: accanto a un disegno non vogliono dire piu' niente, e lasciarli fa
    sembrare che il disegno sia dentro un blocco di codice."""
    _run("""
reset();
const chat = body([chatBlock('graph TD\\n X --> Y')]);
await renderDiagrams(chat);
assert.equal(chat.childNodes.length, 1);
assert.equal(chat.childNodes[0].className, 'diagram',
  "l'involucro del blocco di codice e' rimasto intorno al disegno");
""")


def test_nothing_is_loaded_for_a_page_without_diagrams() -> None:
    """Mermaid sono 3,3 MB: aprire una pagina di solo testo non deve costarli."""
    _run("""
reset();
await renderDiagrams(body([el('pre', [el('code', [txt('print(1)')], 'language-python')])]));
assert.deepEqual(loaded, [], 'ha caricato mermaid per del python');
""")


def test_the_dark_theme_reaches_mermaid() -> None:
    """Senza `initialize`, mermaid usa la palette chiara: riquadri lavanda su
    fondo scuro. Dei sette temi quattro sono scuri, quindi segue `color-scheme`
    invece di essere fissato — e si rifa' a ogni passata, cosi' un cambio di tema
    a contenuto aperto viene raccolto."""
    _run("""
reset();
await renderDiagrams(body([serverBlock('graph TD\\n A --> B')]));
assert.equal(mermaid._config.theme, 'dark');
assert.equal(mermaid._config.securityLevel, 'strict',
  "i diagrammi arrivano dal modello: l'HTML sta fuori dalle etichette");

reset(); schema = 'light';
await renderDiagrams(body([serverBlock('graph TD\\n A --> B')]));
assert.equal(mermaid._config.theme, 'default');
""")


def test_a_library_that_will_not_load_leaves_the_content_readable() -> None:
    """Rete assente, o asset non ancora estratto: il contenuto e' gia' a schermo
    e deve restarci. Una pagina rotta perche' un diagramma non si e' scaricato
    sarebbe peggio del diagramma non disegnato."""
    _run("""
reset(); loadFails = true;
const notices = [];
const realWarn = console.warn;
console.warn = (msg) => notices.push(String(msg));
const page = body([txt('vale $$E$$'), serverBlock('graph TD\\n A --> B')]);
await renderRich(page, { inlineDollar: true });
console.warn = realWarn;
assert.deepEqual(renderedFormulas, []);
assert.deepEqual(renderedDiagrams, []);
assert.equal(page.childNodes.length, 2, 'il contenuto e stato smontato');
// Il fallimento si dice nel log, e i log sono in inglese (AGENTS.md).
assert.deepEqual(notices.sort(), [
  'rich-content: KaTeX failed to load', 'rich-content: Mermaid failed to load',
]);
""")
