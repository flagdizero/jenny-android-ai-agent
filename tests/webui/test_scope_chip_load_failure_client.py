"""«Nessun progetto ancora» non si dice quando non lo si sa.

`_loadProjects` legge `/api/projects` e il suo `catch` faceva una cosa sola:
`this._projects = []`. Cioè trasformava *qualunque* guasto — 401, 500, gateway a
metà avvio, telefono offline — nella frase «Nessun progetto ancora», e nel farlo
buttava via l'elenco buono letto un minuto prima. La risposta ovvia a quello
schermo è ricreare il progetto: è così che nasce un doppione, due wiki con lo
stesso scopo e la storia divisa fra le due, che nessuna delle due poi contiene.
E un doppione non si ritira: quel che il gardener ha già promosso in pagina resta
dove è finito.

Quindi: la cache non si tocca su un fallimento, e la tendina lo dichiara con una
nota sua (`scope.loadFailed`) distinta dall'elenco vuoto — sopra le righe, perché
quelle possono essere vecchie e questa nota è l'unica cosa che lo dice.

I metodi si estraggono dal sorgente e si eseguono in node, come in
``test_chat_switch_race_client.py``. Serve un DOM finto perché `_renderMenu`
costruisce nodi veri: qui un elemento è un oggetto che sa fare `appendChild`,
`className`, `textContent`, e niente più — quanto basta a leggere cosa la tendina
ha scritto.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
CSS = ASSETS / "mobile-style.css"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _chip() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


_HARNESS = """
import assert from 'node:assert/strict';

const i18n = { t: (key) => 'i18n:' + key };
const { ConversationList, ago } = await import('__LIST_URL__');

/* Un elemento è quel poco che `_renderMenu` e i suoi aiutanti toccano. */
function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    children: [],
    dataset: {},
    appendChild(child) { el.children.push(child); return child; },
    setAttribute() {},
    addEventListener() {},
    scrollIntoView() {},
    querySelector() { return makeEl('span'); },
  };
  el.classList = {
    add(c) { el.className = (el.className + ' ' + c).trim(); },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (v === '') el.children.length = 0; },
  });
  return el;
}
const document = { createElement: (tag) => makeEl(tag) };

// Tutto il testo scritto nella tendina, in ordine di comparsa.
function texts(el) {
  const out = [];
  for (const child of el.children) {
    if (child.textContent) out.push(child.textContent);
    out.push(...texts(child));
  }
  return out;
}
// I nodi che portano una classe, a qualunque profondità.
function byClass(el, cls) {
  const out = [];
  for (const child of el.children) {
    if (String(child.className).split(/\\s+/).includes(cls)) out.push(child);
    out.push(...byClass(child, cls));
  }
  return out;
}

// La rete a mano: la lettura dell'elenco può risolvere o fallire a comando.
let nextProjects = null;
const api = {
  listProjects() {
    if (nextProjects === 'fail') return Promise.reject(new Error('401'));
    return Promise.resolve(nextProjects);
  },
};

class Chip {
  constructor() {
    this.scope = { kind: 'personal', name: null };
    /* I quattro campi di prima stanno in `conversation-list.js`, che questo
       banco importa **vero**: la regola che si misura qui — una lettura
       fallita non cancella la cache — adesso vive li', e riscriverne una
       copia in questo file vorrebbe dire misurare la copia. */
    this._list = new ConversationList(() => api.listProjects());
    this.menu = makeEl('div');
    this.rendered = 0;
  }
  render() { this.rendered++; }   // il chip in sé non è oggetto di questi test
  get personalLabel() { return i18n.t('scope.personal'); }
  select() {}
  /* La riga di un progetto avvolge la scelta e il tasto elimina. Qui il
     contenitore c'è (i test contano i nodi per classe, e uno in mezzo cambia
     l'albero) ma il tasto no: il flusso di cancellazione non è oggetto di
     questi test, che guardano *cosa* la tendina scrive. Il tasto ha i suoi in
     `test_scope_chip_delete_client.py`. */
  _projectRow(item) {
    const row = makeEl('div');
    row.className = 'scope-menu-row';
    row.appendChild(item);
    return row;
  }
  __PROJECTS__
  __UNOPENABLE__
  __LOAD_FAILED__
  __DIR__
  __LOAD_PROJECTS__
  __RENDER_MENU__
  __LABEL__
  __SEP__
  __NOTE__
  __ITEM__
  __AGO__
}
"""


def _harness() -> str:
    src = _chip()
    return (
        _HARNESS.replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__PROJECTS__", member(src, "_projects"))
        .replace("__UNOPENABLE__", member(src, "_unopenable"))
        .replace("__LOAD_FAILED__", member(src, "_loadFailed"))
        .replace("__DIR__", member(src, "_dir"))
        .replace("__LOAD_PROJECTS__", member(src, "_loadProjects"))
        .replace("__RENDER_MENU__", member(src, "_renderMenu"))
        .replace("__LABEL__", member(src, "_label"))
        .replace("__SEP__", member(src, "_sep"))
        .replace("__NOTE__", member(src, "_note"))
        .replace("__ITEM__", member(src, "_item"))
        .replace("__AGO__", member(src, "_ago"))
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    run_js(source)


# ── 1. La cache sopravvive al guasto ────────────────────────────────────────


def test_a_failed_read_does_not_erase_a_list_that_was_good() -> None:
    """Il cuore del difetto: l'elenco letto prima non deve sparire."""
    _run_js("""
      const chip = new Chip();
      nextProjects = { dir: 'wikis', projects: [
        { name: 'bordi', modified: 200 }, { name: 'patreon', modified: 100 },
      ] };
      await chip._loadProjects();
      assert.deepEqual(chip._projects.map((p) => p.name), ['bordi', 'patreon']);
      assert.equal(chip._loadFailed, false);

      // Seconda apertura, gateway caduto.
      nextProjects = 'fail';
      await chip._loadProjects();
      assert.deepEqual(chip._projects.map((p) => p.name), ['bordi', 'patreon'],
                       'un 401 ha cancellato i progetti dell\\'utente');
      assert.equal(chip._loadFailed, true);
      assert.equal(chip._dir, 'wikis',
                   'il nome della cartella letto dal backend è stato riportato al default');

      // E torna a posto quando la lettura riesce di nuovo.
      nextProjects = { dir: 'wikis', projects: [{ name: 'bordi', modified: 200 }] };
      await chip._loadProjects();
      assert.equal(chip._loadFailed, false);
    """)


def test_a_first_read_that_fails_leaves_the_list_unknown_not_empty() -> None:
    """Senza cache il fallimento resta *non lo so* (`null`), non *vuoto* (`[]`).

    È la differenza fra le due frasi che la tendina può scrivere.
    """
    _run_js("""
      const chip = new Chip();
      nextProjects = 'fail';
      await chip._loadProjects();
      assert.equal(chip._projects, null,
                   'un fallimento si è dichiarato "elenco vuoto"');
      assert.equal(chip._loadFailed, true);
    """)


# ── 2. Quel che la tendina scrive ───────────────────────────────────────────


def test_the_menu_says_load_failed_and_never_no_projects() -> None:
    """Con la cache vuota: la nota del guasto prende il posto delle altre due."""
    _run_js("""
      const chip = new Chip();
      nextProjects = 'fail';
      await chip._loadProjects();
      chip._renderMenu();

      const written = texts(chip.menu);
      assert.equal(written.includes('i18n:scope.loadFailed'), true,
                   'la tendina non dice che la lettura è fallita');
      assert.equal(written.includes('i18n:scope.noProjects'), false,
                   'la tendina afferma che l\\'utente non ha progetti');
      assert.equal(written.includes('i18n:scope.loading'), false,
                   'un caricamento finito male resta "Caricamento..."');
      // Il guasto si distingue a occhio da uno stato vuoto.
      const notes = byClass(chip.menu, 'scope-menu-note');
      assert.equal(notes.length, 1);
      assert.equal(notes[0].className.split(/\\s+/).includes('is-error'), true);
      // E "Nuovo progetto..." resta raggiungibile: è l'unica via d'uscita se
      // davvero non ce n'è nessuno.
      assert.equal(written.includes('i18n:scope.newProject'), true);
    """)


def test_the_cached_list_is_still_offered_with_a_note_on_top() -> None:
    """Con la cache piena: le righe restano, e la nota dice che sono vecchie."""
    _run_js("""
      const chip = new Chip();
      nextProjects = { dir: 'wikis', projects: [
        { name: 'bordi', modified: 200 }, { name: 'patreon', modified: 100 },
      ] };
      await chip._loadProjects();
      nextProjects = 'fail';
      await chip._loadProjects();
      chip._renderMenu();

      const written = texts(chip.menu);
      assert.equal(written.includes('bordi'), true, 'i progetti in cache sono spariti');
      assert.equal(written.includes('patreon'), true);
      assert.equal(written.includes('i18n:scope.loadFailed'), true);
      assert.equal(written.includes('i18n:scope.noProjects'), false);
      // La nota sta *sopra* le righe che mette in dubbio.
      assert.ok(written.indexOf('i18n:scope.loadFailed') < written.indexOf('bordi'),
                'la nota compare dopo le righe che dovrebbe qualificare');
    """)


def test_an_empty_list_that_was_read_successfully_still_says_no_projects() -> None:
    """Il rovescio: quando lo sappiamo, lo stato vuoto resta quello di prima."""
    _run_js("""
      const chip = new Chip();
      nextProjects = { dir: 'wikis', projects: [] };
      await chip._loadProjects();
      chip._renderMenu();

      const written = texts(chip.menu);
      assert.equal(written.includes('i18n:scope.noProjects'), true);
      assert.equal(written.includes('i18n:scope.loadFailed'), false);
    """)


def test_the_menu_still_says_loading_before_the_first_answer() -> None:
    """Prima di qualunque risposta la nota è ancora "Caricamento...".

    `_projects === null` significava due cose diverse e adesso ne significa una
    sola: il flag decide quale.
    """
    _run_js("""
      const chip = new Chip();
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.equal(written.includes('i18n:scope.loading'), true);
      assert.equal(written.includes('i18n:scope.loadFailed'), false);
    """)


# ── 3. Chiave e stile ───────────────────────────────────────────────────────


def test_the_note_string_is_translated_in_both_locales() -> None:
    for locale in ("it", "en"):
        data = json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert "loadFailed" in data["scope"], f"chiave mancante in {locale}.json"
        assert data["scope"]["loadFailed"].strip()
        # Non deve dire "nessun progetto" con altre parole.
        assert data["scope"]["loadFailed"] != data["scope"]["noProjects"]


def test_the_error_note_has_a_rule_of_its_own() -> None:
    """Grep, non comportamento: che la classe `is-error` sia colorata."""
    css = CSS.read_text(encoding="utf-8")
    # Il selettore può essere elencato insieme a quello della tendina dei
    # comandi, che riusa la stessa regola: è lo stesso corpo, ed è quel che il
    # test guarda.
    assert re.search(r"\.scope-menu-note\.is-error[^{]*\{[^}]*var\(--error\)", css), (
        "la nota del guasto non si distingue da una nota qualsiasi"
    )


def test_the_catch_no_longer_empties_the_cache() -> None:
    """Grep, non comportamento: la riga che causava il doppione non torni.

    Il `catch` si e' spostato in `conversation-list.js` insieme al resto della
    lettura; la riga da cui nasceva il doppione e' la stessa, e il grep la cerca
    dove sta adesso.
    """
    m = re.search(r"\n  async load\(\)\s*\{(.*?)\n  \}",
                  LIST_JS.read_text(encoding="utf-8"), re.S)
    assert m, "load() non trovato in conversation-list.js"
    src = m.group(1)
    catch = src[src.index("} catch"):]
    # Senza i commenti: il commento accanto *cita* la riga rimossa per dire
    # perché è stata rimossa, e un grep ingenuo la ritroverebbe lì.
    catch = re.sub(r"/\*.*?\*/", "", catch, flags=re.S)
    catch = re.sub(r"^\s*//.*$", "", catch, flags=re.M)
    assert "this.projects = []" not in catch, (
        "un guasto torna a dichiarare che l'utente non ha progetti"
    )
    assert "this.loadFailed = true" in catch
