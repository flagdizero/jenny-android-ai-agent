"""Le pagine di un quaderno: l'ordine, i gruppi, e la ricerca che accende le righe giuste.

Tre cose si misurano qui, e la prima e' la sola che non si vedrebbe mai
guardando lo schermo.

**Le postings dell'indice sono indici di posizione** nell'array ``nodes`` che
il server ha spedito (v. ``wiki_search.py``), non id. A schermo le righe vanno
in un altro ordine — per gruppo, alfabetiche dentro — quindi ogni riga si porta
dietro il suo indice di partenza. Se lo perdesse, la ricerca accenderebbe
righe plausibili e sbagliate: nessun errore, nessun vuoto, solo le pagine di
qualcun altro.

**Il gruppo si mostra solo se divide qualcosa.** Cinque quaderni su quattordici
sono piatti; la' l'etichetta comparirebbe identica su ogni riga.

**E solo l'ultimo caricamento disegna.** Dalla tendina si salta in un altro
quaderno mentre ``/api/graph`` e' ancora in volo, e su una wiki grossa quella
risposta non torna in un frame.

I membri si ritagliano dal sorgente e girano in node su un DOM finto, come gli
altri banchi della casa: ``casa-pages.js`` importa ``api``, ``i18n`` e
l'indice, e importarlo davvero vorrebbe dire montare mezzo guscio per provare
l'ordine di un elenco.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
PAGES_JS = ASSETS / "casa-pages.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


def _const_block(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export const {re.escape(name)} = (?:\{{.*?^\}}|\[.*?\]);$", source)
    assert m, f"const {name} non trovata"
    return m.group(0).removeprefix("export ")


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

/* Un DOM finto con la sola meccanica che il disegno usa. `children` e' un
   array vero: `_applySearch` ci cammina sopra, ed e' li' che si vede se la
   riga accesa e' quella giusta. */
function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    value: '',
    placeholder: '',
    hidden: false,
    dataset: {},
    attrs: {},
    children: [],
    listeners: {},
    isFragment: tag === '#fragment',
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
    closest() { return null; },
    appendChild(child) {
      if (child.isFragment) { el.children.push(...child.children); child.children = []; }
      else el.children.push(child);
      return child;
    },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (!v) el.children.length = 0; },
  });
  return el;
}

const nodi = {};
const document = {
  createElement: (tag) => makeEl(tag),
  createDocumentFragment: () => makeEl('#fragment'),
  getElementById: (id) => (nodi[id] ||= makeEl('div')),
};

/* La risposta del server, decisa dal banco. */
let risposta = null;
let errore = null;
/* Quanto ci mette *questa* chiamata. Una wiki grossa torna dopo una piccola,
   ed e' l'unico ordine in cui la corsa si vede: con ritardi uguali il primo
   caricamento finisce per primo e sovrascriverlo non serve a nessuno. */
let ritardo = 0;
const chiamate = [];
const api = {
  getGraph(wiki) {
    chiamate.push(wiki);
    if (errore) return Promise.reject(errore);
    const mia = risposta;
    const attesa = ritardo;
    return new Promise((r) => setTimeout(() => r(mia), attesa));
  },
};

/* L'indice vero e' un motore di maschere di byte; qui basta la sua *forma*:
   una maschera indicizzata per **posizione nell'array `nodes` del server**. */
let maschera = null;
const WikiSearchIndex = {
  from(wire) {
    if (!wire) return null;
    return { query: () => (maschera === null ? null : { mask: maschera, count: 0 }) };
  },
};

__GROUPS__
__GROUP_KEYS__
__SANITIZE_GROUP__
__LABEL_OF__
__ORDER_PAGES__
__GROUPS_MEANINGFUL__

class Pages {
  constructor() {
    this.el = document.getElementById('casa-pages');
    this.listEl = document.getElementById('casa-page-list');
    this.noteEl = document.getElementById('casa-pages-note');
    this.queryEl = document.getElementById('casa-pages-q');
    this.tabListEl = document.getElementById('casa-tab-list');
    this.tabMapEl = document.getElementById('casa-tab-map');
    this.mapEl = document.getElementById('casa-map');
    this.notebook = null;
    this.data = null;
    this.rows = [];
    this._index = null;
    this._tab = 'list';
    this._token = 0;
    this.mappe = 0;
    this._onOpenPage = (path, label) => this.aperte.push([path, label]);
    this._onNeedMap = () => { this.mappe += 1; };
    this.aperte = [];
  }
  __APPLY_TRANSLATIONS__
  __LOAD__
  __SHOW_TAB__
  __SAY__
  __RENDER__
  __ROW__
  __APPLY_SEARCH__
}

function pagine() {
  for (const k of Object.keys(nodi)) delete nodi[k];
  risposta = null;
  errore = null;
  ritardo = 0;
  maschera = null;
  chiamate.length = 0;
  return new Pages();
}

/* Un quaderno con i gruppi, scritto in un ordine che NON e' quello a schermo:
   e' cosi' che si vede se ordinare rinumera i nodi. */
const CON_GRUPPI = {
  nodes: [
    { id: 'c/zucche', path: 'concepts/zucche.md', label: 'zucche', group: 'concepts', degree: 2 },
    { id: 'e/orto', path: 'entities/orto.md', label: 'orto', group: 'entities', degree: 3 },
    { id: 'i/index', path: 'index.md', label: 'index', group: 'other', degree: 0 },
    { id: 'e/annaffi', path: 'entities/annaffi.md', label: 'annaffi', group: 'entities', degree: 1 },
  ],
  edges: [],
  search: { terms: 'x' },
};

const PIATTO = {
  nodes: [
    { id: 'due', path: 'due.md', label: 'due', group: 'other', degree: 0 },
    { id: 'uno', path: 'uno.md', label: 'uno', group: 'other', degree: 0 },
  ],
  edges: [],
  search: null,
};
"""


def _harness() -> str:
    src = PAGES_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", _member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__GROUPS__", _const_block(src, "GROUPS"))
        .replace("__GROUP_KEYS__", _const_block(src, "GROUP_KEYS"))
        .replace("__SANITIZE_GROUP__", _function(src, "sanitizeGroup"))
        .replace("__LABEL_OF__", _function(src, "labelOf"))
        .replace("__ORDER_PAGES__", _function(src, "orderPages"))
        .replace("__GROUPS_MEANINGFUL__", _function(src, "groupsAreMeaningful"))
        .replace("__APPLY_TRANSLATIONS__", _member(src, "applyTranslations"))
        .replace("__LOAD__", _member(src, "load"))
        .replace("__SHOW_TAB__", _member(src, "showTab"))
        .replace("__SAY__", _member(src, "_say"))
        .replace("__RENDER__", _member(src, "_render"))
        .replace("__ROW__", _member(src, "_row"))
        .replace("__APPLY_SEARCH__", _member(src, "_applySearch"))
    )


def _run(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── L'ordine, e l'indice che non si perde ───────────────────────────────────


def test_the_rows_are_grouped_and_alphabetical_inside() -> None:
    """Le cose prima delle idee, i riassunti in fondo; dentro, in ordine."""
    _run("""
      const rows = orderPages(CON_GRUPPI.nodes);
      assert.deepEqual(rows.map((r) => r.label), ['annaffi', 'orto', 'zucche', 'index']);
      assert.deepEqual(
        rows.map((r) => r.group),
        ['entities', 'entities', 'concepts', 'other'],
      );
    """)


def test_every_row_keeps_the_index_the_server_gave_it() -> None:
    """La chiave con cui si legge la maschera della ricerca. Ordinare la
    cambierebbe di posizione, non di valore: qui si controlla il valore."""
    _run("""
      const rows = orderPages(CON_GRUPPI.nodes);
      const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.index]));
      assert.deepEqual(byLabel, { zucche: 0, orto: 1, index: 2, annaffi: 3 });
    """)


def test_the_search_lights_the_row_the_server_meant() -> None:
    """Il banco che vale tutto il modulo.

    La maschera accende la **posizione 0** dell'array del server, che e'
    `zucche` — e che a schermo e' la *terza* riga. Se la riga si cercasse per
    posizione a video, si accenderebbe `annaffi`: plausibile, silenzioso e
    sbagliato.
    """
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      await p.load('orto');
      assert.deepEqual(p.listEl.children.map((r) => r.dataset.label),
                       ['annaffi', 'orto', 'zucche', 'index']);

      maschera = new Uint8Array([1, 0, 0, 0]);
      p.queryEl.value = 'zuc';
      p._applySearch();
      const viste = p.listEl.children.filter((r) => !r.hidden).map((r) => r.dataset.label);
      assert.deepEqual(viste, ['zucche'], 'accesa la riga sbagliata: ' + viste);
    """)


def test_an_empty_query_shows_everything_again() -> None:
    """`query()` torna `null` quando non c'e' nessun vincolo: e' «tutto», non
    «niente»."""
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      await p.load('orto');
      maschera = new Uint8Array([0, 1, 0, 0]);
      p._applySearch();
      assert.equal(p.listEl.children.filter((r) => !r.hidden).length, 1);
      maschera = null;
      p._applySearch();
      assert.equal(p.listEl.children.filter((r) => !r.hidden).length, 4);
    """)


def test_a_query_that_matches_nothing_says_so() -> None:
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      await p.load('orto');
      maschera = new Uint8Array([0, 0, 0, 0]);
      p._applySearch();
      assert.equal(p.noteEl.hidden, false);
      assert.equal(p.noteEl.textContent, i18n.t('casa.pages.noMatch'));
    """)


# ── I gruppi, quando dicono qualcosa ────────────────────────────────────────


def test_a_flat_notebook_shows_no_group_at_all() -> None:
    """Cinque quaderni su quattordici sono piatti: la' «Altro» su ogni riga
    non informa, occupa. Spariscono l'etichetta **e** il pallino."""
    _run("""
      assert.equal(groupsAreMeaningful(orderPages(PIATTO.nodes)), false);
      const p = pagine();
      risposta = PIATTO;
      await p.load('diario');
      for (const row of p.listEl.children) {
        assert.equal(row.children.length, 1, 'una riga piatta ha piu\\u2019 di un pezzo');
        assert.equal(row.children[0].className, 'casa-page-name');
      }
    """)


def test_a_notebook_with_groups_shows_the_dot_and_the_word() -> None:
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      await p.load('orto');
      const prima = p.listEl.children[0];
      assert.equal(prima.children.length, 3);
      assert.ok(prima.children[0].className.includes('casa-group-entities'));
      assert.equal(prima.children[2].textContent, i18n.t('graph.entities'));
      const ultima = p.listEl.children[3];
      assert.equal(ultima.children[2].textContent, i18n.t('graph.other'));
    """)


def test_an_unknown_group_falls_back_like_the_workshop_does() -> None:
    """E `summaries` e' fra gli sconosciuti, di proposito.

    Una wiki di ricerca ha `wiki/summaries/`, ma quelle pagine da `/api/graph`
    non escono — `WIKI_PAGES_SKIP_DIRS` le toglie a tutte e quattro le
    camminate. Se un giorno uscissero, non romperebbero niente: cadrebbero in
    «Altro» invece di trovare un'etichetta che non c'e'.
    """
    _run("""
      assert.equal(sanitizeGroup('inventato'), 'other');
      assert.equal(sanitizeGroup(undefined), 'other');
      assert.equal(sanitizeGroup('summaries'), 'other');
      assert.equal(GROUPS.length, 3, 'i gruppi che il grafo manda sono tre');
    """)


def test_the_title_wins_over_the_file_name() -> None:
    """Il frontmatter dice come si chiama la pagina; il file dice come e'
    scritta su disco. A schermo va la prima."""
    _run("""
      assert.equal(labelOf({ title: 'Il rosmarino', label: 'rosmarino' }), 'Il rosmarino');
      assert.equal(labelOf({ label: 'rosmarino' }), 'rosmarino');
      assert.equal(labelOf({ id: 'wiki/x.md' }), 'wiki/x.md');
    """)


# ── Il caricamento ──────────────────────────────────────────────────────────


def test_only_the_last_load_draws() -> None:
    """Due quaderni di fila, e **il primo torna dopo il secondo**.

    E' l'ordine che conta: su una wiki grossa `/api/graph` puo' metterci cento
    volte il tempo di una piccola, e dalla tendina si salta da una all'altra
    mentre la prima e' ancora in volo. Con ritardi uguali questo banco non
    misurerebbe niente — la risposta lenta arriverebbe comunque per prima, e il
    token di carico potrebbe sparire senza che nessuno se ne accorga.
    """
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      ritardo = 40;                 // il quaderno grosso, lento
      const primo = p.load('orto');
      risposta = PIATTO;
      ritardo = 0;                  // quello piccolo, subito
      const secondo = p.load('diario');
      await Promise.all([primo, secondo]);
      assert.equal(p.notebook, 'diario');
      assert.deepEqual(
        p.listEl.children.map((r) => r.dataset.label), ['due', 'uno'],
        'l\\u2019elenco del quaderno lasciato si e\\u2019 disegnato sopra quello nuovo',
      );
      assert.deepEqual(chiamate, ['orto', 'diario']);
    """)


def test_wikis_switched_off_say_the_sentence_the_house_already_has() -> None:
    """503 non e' un guasto: e' un'impostazione. E la frase esiste gia' — la
    usa il giro di creazione per dire esattamente la stessa cosa."""
    _run("""
      const p = pagine();
      errore = new Error('Graph failed: 503');
      await p.load('orto');
      assert.equal(p.noteEl.textContent, i18n.t('casa.who.create.wikiOff'));
    """)


def test_a_real_failure_is_not_the_same_sentence() -> None:
    _run("""
      const p = pagine();
      errore = new Error('Graph failed: 500');
      await p.load('orto');
      assert.equal(p.noteEl.textContent, i18n.t('casa.pages.failed'));
    """)


def test_a_notebook_with_no_pages_yet_says_so() -> None:
    _run("""
      const p = pagine();
      risposta = { nodes: [], edges: [], search: null };
      await p.load('nuovo');
      assert.equal(p.noteEl.hidden, false);
      assert.equal(p.noteEl.textContent, i18n.t('casa.pages.none'));
    """)


# ── Le due linguette ────────────────────────────────────────────────────────


def test_the_map_is_asked_for_only_when_you_tap_its_tab() -> None:
    """280 kB di D3: chi non apre la mappa non la paga."""
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      await p.load('orto');
      assert.equal(p.mappe, 0, 'la mappa e\\u2019 stata chiesta senza toccarla');
      /* E nemmeno ritoccando la linguetta dell'elenco, che e' il gesto con cui
         si torna indietro: i dati ci sono gia', e chiedere «disegna la mappa»
         da qui tirerebbe giu' i 280 kB di D3 per una schermata che non li usa. */
      p.showTab('list');
      assert.equal(p.mappe, 0, 'la linguetta dell\\u2019elenco ha chiesto la mappa');
      p.showTab('map');
      assert.equal(p.mappe, 1);
      assert.equal(p.listEl.hidden, true);
      assert.equal(p.mapEl.hidden, false);
      p.showTab('list');
      assert.equal(p.listEl.hidden, false);
      assert.equal(p.mapEl.hidden, true);
    """)


def test_a_new_notebook_comes_back_to_the_list() -> None:
    """Aprire un altro quaderno con la mappa accesa mostrerebbe la mappa di
    prima mentre l'elenco nuovo arriva."""
    _run("""
      const p = pagine();
      risposta = CON_GRUPPI;
      await p.load('orto');
      p.showTab('map');
      risposta = PIATTO;
      await p.load('diario');
      assert.equal(p._tab, 'list');
      assert.equal(p.listEl.hidden, false);
    """)
