"""«I file veri»: la scheda di Memoria *e'* il gestore file.

Era una voce della tavola che nel prodotto non esisteva. Il 21/09/2026 e'
nata come riassunto — le prime otto voci della radice, poi un bottone verso il
gestore vero — ed e' durata un giorno: un elenco troncato che non si tocca non
risponde a nessuna domanda, e il bottone sotto rendeva due gesti quel che ne
vale uno. Adesso la scheda **contiene** l'esploratore: le cartelle si aprono
li' dentro, e l'unica uscita e' aprire un file.

Quel che gira qui in node, su un DOM finto, e' quello che leggere il sorgente
non dimostra:

* **I file di servizio non si elencano, e l'ordine e' uno solo.** Il flag
  ``internal`` lo mette il server file per file.
* **Non c'e' nessun campione.** Un tetto di righe rimesso qui rifarebbe il
  difetto che questa passata toglie.
* **Riaprire il cassetto non riporta alla radice.** La scheda si ridisegna a
  ogni apertura *e dopo ogni salvataggio* in Memoria: se la cartella vivesse
  nel DOM, salvare un'impostazione butterebbe via tre livelli di cammino.
* **Indietro risale, poi lascia andare.** Uscire dal cassetto e' quel che fa
  alla radice, non da dentro una cartella.
* **Chiudere un file riporta nella sua cartella**, non alla radice.
* **Una cartella illeggibile lo dice.** Uguale a una vuota e' il modo in cui un
  guasto di rete si traveste da cartella vuota.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
SETTINGS_JS = ASSETS / "mobile-settings.js"
WORKSPACE_JS = ASSETS / "mobile-workspace.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


# I metodi del gestore file che il banco fa girare davvero. Gli altri sono
# stub: costruire una tessera della griglia o un breadcrumb e' DOM, e il DOM
# finto misurerebbe se' stesso.
_VERI = (
    "mount",
    "navigateTo",
    "renderGrid",
    "handleCardBack",
    "collapseToRoot",
    "_closeEditor",
    "_resetToExplorerAt",
    "showExplorerView",
    "_handleNewAction",
    "_esploratoreASchermo",
)

_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

const escapeHtml = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

/* `renderGrid` revoca le object URL delle miniature: qui non ce ne sono. */
const URL = { revokeObjectURL() {} };
const parentPath = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');

/* Quel che il server risponde a `/api/workspace/list`, deciso dal banco. */
let risposta = null;
let errore = null;
const chiamate = [];
const api = {
  listWorkspace(path) {
    chiamate.push(path);
    return errore ? Promise.reject(errore) : Promise.resolve(risposta);
  },
};

/* Un nodo finto: quel tanto che serve a `navigateTo` e `renderGrid`. */
function nodo(sotto = {}) {
  return {
    innerHTML: '', style: {}, figli: [], ascolti: {},
    textContent: '',
    /* Nel documento finche' la scheda non si ridisegna: allora il banco lo
       mette a false, come fa il DOM vero con i nodi buttati. */
    isConnected: true,
    appendChild(c) { this.figli.push(c); },
    querySelector(sel) { return sotto[sel] ?? null; },
    addEventListener(ev, cb) { (this.ascolti[ev] ||= []).push(cb); },
    clic() { (this.ascolti.click || []).forEach((cb) => cb()); },
  };
}

/* La scheda appena disegnata da `SettingsController.render()`. */
function scheda() {
  const crumb = nodo();
  const griglia = nodo();
  const sub = nodo();
  const vuoto = nodo({ '.ws-empty-sub': sub });
  const nuovo = nodo();
  const host = nodo({
    '[data-ws-crumb]': crumb, '[data-ws-grid]': griglia,
    '[data-ws-empty]': vuoto, '[data-ws-new]': nuovo,
  });
  return { host, crumb, griglia, vuoto, sub, nuovo };
}

/* Il guscio: `switchMode` e `navigateBack` sono i due modi di lasciare
   Memoria, e questo banco conta quante volte accadono. */
const guscio = { mosse: [] };
const window_ = {
  mobileApp: {
    switchMode: (m) => guscio.mosse.push(['switchMode', m]),
    navigateBack: (m) => guscio.mosse.push(['navigateBack', m]),
    header: null,
  },
};
globalThis.window = window_;

class WorkspaceController {
  constructor() {
    this.currentDir = '';
    this.currentPath = '';
    this.viewMode = 'explorer';
    this._navToken = 0;
    this._thumbUrls = [];
    this._dirty = false;
    this.editor = null;
    this.viewerEl = { innerHTML: '', classList: { add() {}, remove() {} } };
    this.breadcrumbEl = null;
    this.gridEl = null;
    this.emptyEl = null;
    /* Quel che la griglia ha costruito, nell'ordine in cui l'ha costruito. */
    this.tessere = [];
    this.menuNuovo = 0;
    this.creati = [];
  }
  _createDirItem(i) { this.tessere.push('cartella:' + i.name); return {}; }
  _createFileItem(i) { this.tessere.push('file:' + i.name); return {}; }
  renderBreadcrumb() {}
  showEditorView() {}
  _syncHeaderBack() {}
  _showNewMenu() { this.menuNuovo++; }
  _createEntry(azione, base) { this.creati.push([azione, base]); }
  _confirmDiscard() { this.scartoChiesto = true; }
__METODI__
}

function voce(name, type, size, internal) {
  return { name, type, size: size ?? null, internal: !!internal };
}

/* Un giro completo: la scheda si disegna, il gestore ci si aggancia, e la
   radice arriva. */
async function apri(items, { rotto = false } = {}) {
  risposta = items === null ? null : { items, path: '' };
  errore = rotto ? new Error('gateway giu') : null;
  chiamate.length = 0;
  guscio.mosse.length = 0;
  const s = scheda();
  const c = new WorkspaceController();
  c.mount(s.host);
  await new Promise((r) => setTimeout(r, 0));
  return { c, ...s };
}
"""


def _harness() -> str:
    ws = WORKSPACE_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    metodi = "\n".join("  " + member(ws, nome) for nome in _VERI)
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__METODI__", metodi)
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


def test_outside_memoria_nothing_is_read() -> None:
    """La scheda vive in un cassetto solo. Aprendo Cervello o Mani il suo
    contenitore non esiste, e una richiesta al workspace partita comunque
    sarebbe traffico per un disegno che nessuno vedra'."""
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    monta = member(settings, "_montaFile")
    run_js("""
import assert from 'node:assert/strict';
let montaggi = 0;
globalThis.window = { mobileApp: {
  ensureController: () => ({ mount: () => { montaggi++; } }),
} };
class C {
  constructor(el) { this.contentEl = el; }
  """
            + monta
            + """
}
// Cassetto senza la scheda dei file: il contenitore non c'e'.
new C({ querySelector: () => null })._montaFile();
assert.equal(montaggi, 0, 'il gestore file si monta fuori da Memoria');
// E in Memoria si monta, o il banco sopra passerebbe per un refuso.
new C({ querySelector: () => ({}) })._montaFile();
assert.equal(montaggi, 1, 'in Memoria il gestore non si monta affatto');
""",)


def test_service_files_are_never_listed() -> None:
    """Il flag lo mette il server; la griglia lo rispetta senza chiedere
    permesso a nessuno. C'era un interruttore — «modalita' sviluppatore» — che
    li faceva comparire: tolto il 21/09/2026, e con lui l'unica condizione
    davanti a questo filtro."""
    _run_js("""
const { c } = await apri([
  voce('USER.md', 'file', 120),
  voce('config.json', 'file', 80, true),
  voce('sessions', 'directory', null, true),
  voce('progetti', 'directory'),
]);
assert.deepEqual(c.tessere, ['cartella:progetti', 'file:USER.md']);
""")


def test_folders_come_first_then_files_each_alphabetical() -> None:
    """Un ordine solo per gli stessi dati. Due schermate che li ordinano
    diversamente sembrano parlare di due cartelle diverse."""
    _run_js("""
const { c } = await apri([
  voce('zeta.md', 'file', 1), voce('foto', 'directory'),
  voce('alfa.md', 'file', 1), voce('archivio', 'directory'),
]);
assert.deepEqual(c.tessere, [
  'cartella:archivio', 'cartella:foto', 'file:alfa.md', 'file:zeta.md',
]);
""")


def test_the_card_shows_every_file_and_not_a_sample() -> None:
    """**Il difetto che questa passata toglie.** La scheda nasceva con un tetto
    di otto righe e un bottone verso l'elenco vero: un campione che non risponde
    a nessuna domanda, e sotto il gesto che serviva davvero. Un tetto rimesso
    qui lo rifarebbe identico, e sarebbe invisibile finche' qualcuno non apre
    una cartella con dentro piu' roba del tetto."""
    _run_js("""
/* Cartelle **e** file, e tante di entrambi: un tetto rimesso su una sola
   delle due famiglie passerebbe inosservato contando solo l'altra. */
const molte = [
  ...Array.from({ length: 20 }, (_, i) =>
    voce('c' + String(i).padStart(3, '0'), 'directory')),
  ...Array.from({ length: 20 }, (_, i) =>
    voce(String(i).padStart(3, '0') + '.md', 'file', 10)),
];
const { c } = await apri(molte);
assert.equal(c.tessere.filter((t) => t.startsWith('cartella:')).length, 20,
  'le cartelle sono un campione invece di tutte');
assert.equal(c.tessere.filter((t) => t.startsWith('file:')).length, 20,
  'i file sono un campione invece di tutti');
""")


def test_reopening_the_card_stays_in_the_folder_you_were_in() -> None:
    """La scheda si ridisegna a ogni apertura di Memoria **e dopo ogni
    salvataggio** in quel cassetto: `render()` riscrive `contentEl` per intero.

    Se la cartella corrente vivesse nel DOM, cambiare un'impostazione qualsiasi
    di Memoria rimbalzerebbe alla radice chi stava a tre livelli di profondita'.
    Vive nel controller, e il riaggancio la rilegge da li'."""
    _run_js("""
const { c } = await apri([voce('progetti', 'directory')]);
await c.navigateTo('progetti/casa');
assert.equal(c.currentDir, 'progetti/casa');

// Memoria si ridisegna: nodi nuovi, stesso controller.
const s2 = scheda();
chiamate.length = 0;
c.mount(s2.host);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(chiamate, ['progetti/casa'],
  'il riaggancio riparte dalla radice invece che da dove si era');
assert.equal(c.gridEl, s2.griglia, 'la griglia vecchia e\\' rimasta agganciata');
""")


def test_walking_into_a_folder_never_leaves_memoria() -> None:
    """E' la ragione per cui il gestore e' entrato nella scheda: girare tra le
    cartelle non deve cambiare schermata. Solo **aprire** un file lo fa."""
    _run_js("""
const { c } = await apri([voce('progetti', 'directory')]);
await c.navigateTo('progetti');
await c.navigateTo('progetti/casa');
assert.deepEqual(guscio.mosse, [],
  'girare tra le cartelle ha lasciato il cassetto');
""")


def test_back_walks_up_one_folder_and_then_lets_go() -> None:
    """Risalire viene prima di uscire. Alla radice invece non c'e' piu' niente
    da sbucciare e la pressione deve proseguire la catena: un `true` di troppo
    la mangerebbe senza cambiare niente a schermo."""
    _run_js("""
const { c } = await apri([voce('progetti', 'directory')]);
await c.navigateTo('progetti/casa/note');

assert.equal(c.handleCardBack(), true);
assert.equal(c.currentDir, 'progetti/casa');
assert.equal(c.handleCardBack(), true);
assert.equal(c.currentDir, 'progetti');
assert.equal(c.handleCardBack(), true);
assert.equal(c.currentDir, '');
assert.equal(c.handleCardBack(), false,
  'alla radice il cassetto si mangia la pressione');

// Con un file aperto la pressione non e' del cassetto: e' dell'altra
// schermata, e la raccoglie `handleBack`.
c.viewMode = 'editor';
c.currentDir = 'progetti';
assert.equal(c.handleCardBack(), false);
""")


def test_closing_a_file_returns_to_the_folder_it_was_opened_from() -> None:
    """Il cammino fatto per arrivare a un file non si butta via aprendolo. Qui
    c'era un `ret ? '' : this.currentDir`, giusto finche' l'origine era
    un'altra sezione e l'esploratore non era la schermata da cui si veniva."""
    _run_js("""
const { c } = await apri([voce('progetti', 'directory')]);
await c.navigateTo('progetti/casa');
c.viewMode = 'editor';
c.currentPath = 'progetti/casa/note.md';
guscio.mosse.length = 0;

assert.equal(c._closeEditor({ hardwareBack: true }), false,
  'col back hardware la history riporta indietro da se\\'');
assert.equal(c.currentDir, 'progetti/casa');
assert.deepEqual(guscio.mosse, [], 'il back hardware ha anche navigato');

// La freccia dell'header invece naviga: nessuno lo fa al posto suo.
c.viewMode = 'editor';
assert.equal(c._closeEditor(), true);
assert.deepEqual(guscio.mosse, [['navigateBack', 'memoria']]);
""")


def test_home_dismounts_the_file_without_a_second_destination() -> None:
    """Home porta in chat per conto suo. Se lo smontaggio navigasse anche lui,
    una pressione produrrebbe due destinazioni di fila."""
    _run_js("""
const { c } = await apri([voce('progetti', 'directory')]);
await c.navigateTo('progetti/casa');
c.viewMode = 'editor';
guscio.mosse.length = 0;
c.collapseToRoot();
assert.deepEqual(guscio.mosse, [], 'Home ha navigato due volte');
assert.equal(c.currentDir, '', 'Home non e\\' tornata alla radice');
""")


def test_an_unreadable_folder_says_so_instead_of_looking_empty() -> None:
    """Un guasto di rete che si traveste da cartella vuota e' il modo in cui si
    dice all'utente che i suoi file non ci sono piu'."""
    _run_js("""
const vuota = await apri([]);
assert.equal(vuota.vuoto.style.display, '');
assert.equal(vuota.sub.textContent, '', 'una cartella vuota si e\\' inventata un guasto');

const rotta = await apri([], { rotto: true });
assert.equal(rotta.vuoto.style.display, '');
assert.match(rotta.sub.textContent, /gateway giu/,
  'la cartella illeggibile si presenta come vuota');
""")


def test_the_new_button_creates_where_you_are_looking() -> None:
    """«Nuovo» e' passato dall'intestazione della vista alla scheda, e crea
    nella cartella che si sta guardando — non in quella da cui si e' partiti."""
    _run_js("""
const { c, nuovo } = await apri([voce('progetti', 'directory')]);
nuovo.clic();
assert.equal(c.menuNuovo, 1, 'il bottone «nuovo» non e\\' agganciato');

await c.navigateTo('progetti/casa');
await c._handleNewAction('newFile');
assert.deepEqual(c.creati, [['newFile', 'progetti/casa']]);
""")


def test_a_stale_answer_never_overwrites_a_newer_one() -> None:
    """Due tocchi in fretta su due cartelle: vince l'ultimo partito, non
    l'ultimo arrivato. Senza il gettone, una risposta lenta della cartella
    lasciata riempirebbe la griglia di quella aperta."""
    _run_js("""
const { c } = await apri([voce('a', 'directory'), voce('b', 'directory')]);
const vecchia = c.navigateTo('a');
risposta = { items: [voce('dentro-b.md', 'file', 1)], path: 'b' };
const nuova = c.navigateTo('b');
await Promise.all([vecchia, nuova]);
assert.deepEqual(c.tessere.slice(-1), ['file:dentro-b.md'],
  'la risposta della cartella lasciata ha riempito quella aperta');
""")


def test_a_redrawn_card_does_not_eat_back() -> None:
    """Cartelle aperte in Memoria, poi Cervello: la scheda si ridisegna e la
    griglia di prima resta in mano al gestore, staccata. Risalire li' dentro
    era una pressione spesa su una griglia che nessuno vede."""
    _run_js("""
const { c, griglia } = await apri([voce('progetti', 'directory')]);
await c.navigateTo('progetti/casa');
griglia.isConnected = false;
const prima = chiamate.length;
assert.equal(c.handleCardBack(), false, 'Indietro mangiato da una griglia staccata');
assert.equal(c.currentDir, 'progetti/casa');
assert.equal(chiamate.length, prima, 'letta una cartella per una griglia staccata');
""")


def test_only_the_drawer_with_the_files_card_hands_back_to_it() -> None:
    """``SettingsController.handleBack`` gira la pressione al gestore file solo
    nel cassetto che ha la scheda «I file veri»."""
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    cassetti = re.search(r"(?ms)^export const CASSETTI = \{.*?^\};$", settings)
    assert cassetti, "CASSETTI non trovata"
    back = member(settings, "handleBack")
    run_js(
        "import assert from 'node:assert/strict';\n"
        + cassetti.group(0).replace("export ", "")
        + """
let girate = 0;
globalThis.window = { mobileApp: { controllers: { workspace: {
  handleCardBack: () => { girate++; return true; },
} } } };
class C {
  constructor(cassetto) { this._cassetto = cassetto; }
"""
        + back
        + """
}
assert.equal(new C('cervello').handleBack(), false);
assert.equal(new C('mani').handleBack(), false);
assert.equal(girate, 0, 'la pressione e\\' andata al gestore file fuori da Memoria');
assert.equal(new C('memoria').handleBack(), true);
assert.equal(girate, 1);
"""
    )
