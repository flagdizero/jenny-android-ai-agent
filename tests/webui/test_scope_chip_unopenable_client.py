"""Una cartella che non si apre si **vede**, e la riga dice perché.

`/api/projects` divide le wiki in due: `projects`, che si possono aprire come
conversazione, e `unopenable`, quelle il cui nome non può essere una chiave di
sessione. Quella divisione esiste per una ragione precisa — su un telefono non
c'è un file manager con cui rinominare una cartella, la sola strada è chiederlo
all'agente dalla chat personale, e per chiederlo l'utente deve sapere che quella
cartella esiste. Sparire dall'elenco è indistinguibile dall'essere stata
cancellata.

`scope-chip.js` **ignorava** l'array. Quindi metà del lavoro era atterrata sul
server e dal lato dell'utente quella cartella era semplicemente assente, cioè
esattamente lo stato che quella decisione aveva giudicato il peggiore dei due.

Qui si verifica l'altra metà, e la forma della riga conta quanto la sua
presenza: **non è tappabile**. Una riga che si tocca e poi rifiuta è peggio di
una riga grigia che dice perché — il tocco è una promessa, e questa riga non ha
niente da mantenere. La spiegazione è una per motivo e non una per riga, e la
regola dei nomi **non viene riscritta**: è la stessa stringa che il dialogo di
creazione mostra già (`scope.invalidName`), interpolata dentro la nota. Di copie
a mano di quella regola ce n'erano già tre; una quarta, in prosa, si
desincronizzerebbe senza che nessun test la veda.

**Come sono fatti questi test.** I membri si estraggono dal sorgente e si
eseguono in **node**, come in ``test_scope_chip_load_failure_client.py``: niente
è riscritto a mano, nemmeno la mappa dei motivi né la ``t()`` di ``i18n.js`` —
così la nota che si legge nell'asserzione è la frase italiana vera, con la regola
vera dentro. I pochi test che invece guardano soltanto il testo del sorgente (una
regola CSS, la presenza di una chiave) lo dicono nel loro docstring con «Grep,
non comportamento», che è la convenzione di questa cartella.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import locale, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
CSS = ASSETS / "mobile-style.css"


pytestmark = requires_node


def _chip() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


def _const(source: str, name: str) -> str:
    """Un `const NOME = {...};` di modulo, dal sorgente."""
    m = re.search(rf"\n(?:export )?const {re.escape(name)} = \{{.*?\n\}};", source, re.S)
    assert m, f"const {name} non trovato"
    return m.group(0).replace("export const", "const")


_HARNESS = """
import assert from 'node:assert/strict';

const { ConversationList, ago } = await import('__LIST_URL__');
__HINT_KEYS__

/* La `t()` vera di `i18n.js` sulle traduzioni vere: la nota va letta come la
   legge l'utente, cioè con la regola dei nomi interpolata dentro. */
const TRANSLATIONS = __TRANSLATIONS__;
const i18n = {
  locale: 'it',
  translations: TRANSLATIONS,
  __T__
};

/* Un elemento è quel poco che la tendina tocca — più due registri: gli
   attributi scritti e i listener montati. Il secondo è il test di "non
   tappabile": una riga che non ascolta niente non può promettere niente. */
function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    children: [],
    dataset: {},
    attrs: {},
    listeners: [],
    appendChild(child) { el.children.push(child); return child; },
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type) { el.listeners.push(type); },
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
// Ogni listener montato sotto un nodo: la somma dice quante righe rispondono.
function listeners(el) {
  let n = el.listeners ? el.listeners.length : 0;
  for (const child of el.children) n += listeners(child);
  return n;
}

// La rete a mano: la lettura dell'elenco risolve o fallisce a comando.
let nextPayload = null;
const api = {
  listProjects() {
    if (nextPayload === 'fail') return Promise.reject(new Error('401'));
    return Promise.resolve(nextPayload);
  },
};

class Chip {
  constructor() {
    this.scope = { kind: 'personal', name: null };
    /* Le due liste stanno in `conversation-list.js`, importato vero: la
       divisione fra apribili e non — e il fatto che un guasto non la cancelli
       — e' la sua regola, e va misurata dove vive. */
    this._list = new ConversationList(() => api.listProjects());
    this.menu = makeEl('div');
    this.picked = [];
  }
  render() {}
  get personalLabel() { return i18n.t('scope.personal'); }
  select(scope) { this.picked.push(scope); }
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
        # La mappa motivo→chiave e' passata in `conversation-list.js` col resto
        # di quel che i due gusci dividono: `reason` arriva dal server e la sua
        # traduzione e' una sola.
        _HARNESS.replace("__HINT_KEYS__",
                         _const(LIST_JS.read_text(encoding="utf-8"),
                                "UNOPENABLE_HINT_KEYS"))
        .replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__PROJECTS__", member(src, "_projects"))
        .replace("__UNOPENABLE__", member(src, "_unopenable"))
        .replace("__LOAD_FAILED__", member(src, "_loadFailed"))
        .replace("__DIR__", member(src, "_dir"))
        .replace("__TRANSLATIONS__", json.dumps({"it": locale("it")}))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
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


# Le stringhe italiane vere, così l'asserzione parla della frase che si legge sul
# telefono e non della chiave. Lette **dentro** i test e non a livello di modulo:
# una chiave mancante deve far fallire i test che la riguardano, non impedire la
# raccolta di tutto il file — cioè della suite, che si interrompe su un errore di
# collect.
def _it(key: str) -> str:
    return locale("it")["scope"].get(key, f"(scope.{key} manca)")


def _rule() -> str:
    return _it("invalidName")


def _hint() -> str:
    return _it("unopenableInvalidName").replace("{rule}", _rule())


# ── 1. La cartella arriva dal payload e non si perde ────────────────────────


def test_the_unopenable_array_is_read_and_kept() -> None:
    """Il difetto in una riga: `unopenable` non veniva letto affatto."""
    _run_js("""
      const chip = new Chip();
      nextPayload = { dir: 'wikis', projects: [{ name: 'patreon', modified: 100 }],
        unopenable: [
          { name: 'Ricerca ETF', modified: 100, reason: 'invalid_name' },
          { name: 'università', modified: 300, reason: 'invalid_name' },
        ] };
      await chip._loadProjects();
      assert.deepEqual(chip._unopenable.map((f) => f.name), ['università', 'Ricerca ETF'],
                       'le cartelle non apribili sono state buttate, o non sono ordinate come le altre');
      assert.deepEqual(chip._unopenable.map((f) => f.reason), ['invalid_name', 'invalid_name'],
                       'il motivo non viaggia con la voce: la riga non saprebbe cosa dire');
    """)


def test_a_failed_read_does_not_erase_the_unopenable_cache() -> None:
    """Un 401 non deve rifare sparire dallo schermo una cartella che c'è."""
    _run_js("""
      const chip = new Chip();
      nextPayload = { dir: 'wikis', projects: [],
        unopenable: [{ name: 'Ricerca ETF', modified: 100, reason: 'invalid_name' }] };
      await chip._loadProjects();
      nextPayload = 'fail';
      await chip._loadProjects();
      assert.deepEqual(chip._unopenable.map((f) => f.name), ['Ricerca ETF'],
                       'un guasto di rete ha cancellato la cartella dallo schermo');
      assert.equal(chip._loadFailed, true);
    """)


# ── 2. La riga c'è, e dice perché ───────────────────────────────────────────


def test_the_row_appears_with_its_own_label_and_one_note() -> None:
    _run_js(f"""
      const chip = new Chip();
      nextPayload = {{ dir: 'wikis', projects: [{{ name: 'patreon', modified: 100 }}],
        unopenable: [
          {{ name: 'Ricerca ETF', modified: 100, reason: 'invalid_name' }},
          {{ name: 'università', modified: 90, reason: 'invalid_name' }},
        ] }};
      await chip._loadProjects();
      chip._renderMenu();

      const written = texts(chip.menu);
      assert.equal(written.includes('Ricerca ETF'), true,
                   'la cartella che il server manda come non apribile non compare');
      assert.equal(written.includes('università'), true);
      assert.equal(written.includes({json.dumps(_it("unopenableSection"))}), true,
                   'le righe che non si aprono non si distinguono dai progetti');
      // La spiegazione: **una**, non una per riga, e con la regola dentro.
      const notes = texts(chip.menu).filter((t) => t === {json.dumps(_hint())});
      assert.equal(notes.length, 1,
                   'la spiegazione manca, o è ripetuta su ogni riga');
      assert.equal(notes[0].includes({json.dumps(_rule())}), true,
                   'la nota non dice la regola dei nomi');
      assert.equal(notes[0].includes('{{rule}}'), false,
                   'la regola non è stata interpolata: a schermo resta il segnaposto');
      // E i progetti veri restano dove erano.
      assert.ok(written.indexOf('patreon') < written.indexOf('Ricerca ETF'),
                'le righe da sistemare vengono prima di quelle su cui si lavora');
    """)


def test_the_row_is_shown_even_when_there_are_no_openable_projects() -> None:
    """Il caso in cui l'assenza era più costosa: è la sola cartella che c'è."""
    _run_js("""
      const chip = new Chip();
      nextPayload = { dir: 'wikis', projects: [],
        unopenable: [{ name: 'Ricerca ETF', modified: 100, reason: 'invalid_name' }] };
      await chip._loadProjects();
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.equal(written.includes('Ricerca ETF'), true,
                   "l'unica cartella del workspace non è sullo schermo");
      // «Nessun progetto ancora» resta vero — nessuno di quelli si apre — ed è
      // la riga sotto a spiegare cos'è quella cartella.
      assert.equal(written.includes(i18n.t('scope.noProjects')), true);
    """)


def test_nothing_is_added_when_every_folder_opens() -> None:
    """Il rovescio: senza cartelle bloccate la tendina è quella di prima."""
    _run_js(f"""
      const chip = new Chip();
      nextPayload = {{ dir: 'wikis', projects: [{{ name: 'patreon', modified: 100 }}],
        unopenable: [] }};
      await chip._loadProjects();
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.equal(written.includes({json.dumps(_it("unopenableSection"))}), false,
                   "un'etichetta per una sezione vuota");
      assert.equal(byClass(chip.menu, 'is-unopenable').length, 0);
      assert.equal(written.some((t) => t.includes({json.dumps(_rule())})), false,
                   'la regola dei nomi viene detta a chi non ne ha bisogno');
    """)


# ── 3. Si legge, non si tocca ───────────────────────────────────────────────


def test_the_row_is_not_selectable() -> None:
    """Il cuore del disegno: niente bottone, niente listener, niente spunta.

    Una riga tappabile che poi rifiuta è peggio di una riga grigia che dice
    perché — e aprirla vorrebbe dire mandare il messaggio dopo in una
    conversazione che non esiste.
    """
    _run_js("""
      const chip = new Chip();
      nextPayload = { dir: 'wikis', projects: [{ name: 'patreon', modified: 100 }],
        unopenable: [{ name: 'Ricerca ETF', modified: 100, reason: 'invalid_name' }] };
      await chip._loadProjects();
      chip._renderMenu();

      const rows = byClass(chip.menu, 'is-unopenable');
      assert.equal(rows.length, 1);
      const row = rows[0];
      assert.notEqual(row.tag, 'button', 'la riga è un bottone: il tocco promette');
      assert.equal(row.listeners.length, 0, 'la riga ascolta un evento');
      assert.equal(listeners(row), 0, 'qualcosa dentro la riga risponde al tocco');
      assert.equal(row.attrs['aria-disabled'], 'true');
      assert.equal(row.attrs['role'], undefined,
                   "non è una delle opzioni fra cui scegliere: non ne porti il ruolo");
      assert.equal(byClass(row, 'scope-menu-check').length, 0);
      assert.equal(row.dataset.reason, 'invalid_name');

      // E il conto dei listener di tutta la tendina: personale + patreon +
      // "nuovo progetto", e nient'altro.
      assert.equal(listeners(chip.menu), 3,
                   'la tendina ha una riga tappabile in più del previsto');
      assert.deepEqual(chip.picked, []);
    """)


def test_the_row_still_says_when_the_folder_last_moved() -> None:
    """Riconoscere *quale* cartella è: è quel che serve per chiederne il nome."""
    _run_js("""
      const chip = new Chip();
      const twoHoursAgo = Math.floor(Date.now() / 1000) - 7200;
      nextPayload = { dir: 'wikis', projects: [],
        unopenable: [{ name: 'Ricerca ETF', modified: twoHoursAgo, reason: 'invalid_name' }] };
      await chip._loadProjects();
      chip._renderMenu();
      const written = texts(byClass(chip.menu, 'is-unopenable')[0]);
      assert.equal(written.includes(i18n.t('scope.ago.hours', { n: '2' })), true,
                   "la riga non dice da quando la cartella è ferma");
    """)


# ── 4. Un motivo che il client non conosce ──────────────────────────────────


def test_an_unknown_reason_does_not_get_told_the_name_rule() -> None:
    """Il server può aggiungere un motivo; la riga non deve inventare il perché.

    Raccontare la regola dei nomi a chi ha una cartella rifiutata per **altro**
    è peggio che non spiegare niente: è una frase falsa, e l'utente rinomina una
    cartella che aveva già un nome buono.
    """
    _run_js(f"""
      const chip = new Chip();
      nextPayload = {{ dir: 'wikis', projects: [],
        unopenable: [{{ name: 'qualcosa', modified: 100, reason: 'not_a_wiki' }}] }};
      await chip._loadProjects();
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.equal(written.includes('qualcosa'), true, 'la cartella è sparita comunque');
      assert.equal(written.some((t) => t.includes({json.dumps(_rule())})), false,
                   'a un motivo sconosciuto viene raccontata la regola dei nomi');
      assert.equal(written.includes(i18n.t('scope.unopenableOther')), true,
                   'un motivo sconosciuto non viene spiegato affatto');
    """)


def test_two_folders_on_the_same_reason_share_one_note() -> None:
    _run_js(f"""
      const chip = new Chip();
      nextPayload = {{ dir: 'wikis', projects: [{{ name: 'patreon', modified: 400 }}],
        unopenable: [
          {{ name: 'a b', modified: 300, reason: 'invalid_name' }},
          {{ name: 'c d', modified: 200, reason: 'invalid_name' }},
          {{ name: 'e f', modified: 100, reason: 'not_a_wiki' }},
        ] }};
      await chip._loadProjects();
      chip._renderMenu();
      const notes = byClass(chip.menu, 'scope-menu-note').map((n) => n.textContent);
      assert.deepEqual(notes, [{json.dumps(_hint())}, i18n.t('scope.unopenableOther')],
                       'una nota per riga invece di una per motivo, o le due invertite');
    """)


# ── 5. Un rename fa cambiare lato alla cartella ─────────────────────────────


def test_a_rename_moves_the_folder_to_the_openable_list() -> None:
    """La via d'uscita, dai due lati: prima non si apre, dopo si apre.

    È il seguito che la nota promette — chiedere il nome nuovo all'agente — e
    quel che deve succedere alla tendina quando la cartella l'ha ottenuto.
    """
    _run_js("""
      const chip = new Chip();
      nextPayload = { dir: 'wikis', projects: [],
        unopenable: [{ name: 'Ricerca ETF', modified: 100, reason: 'invalid_name' }] };
      await chip._loadProjects();
      chip._renderMenu();
      assert.equal(byClass(chip.menu, 'is-unopenable').length, 1);
      assert.equal(listeners(chip.menu), 2, 'personale + nuovo progetto, e nient\\'altro');

      // L'agente la rinomina, e la lettura dopo la trova dall'altro lato.
      nextPayload = { dir: 'wikis', projects: [{ name: 'ricerca-etf', modified: 200 }],
        unopenable: [] };
      await chip._loadProjects();
      chip._renderMenu();

      assert.equal(byClass(chip.menu, 'is-unopenable').length, 0,
                   'la riga grigia resta dopo il rename');
      const written = texts(chip.menu);
      assert.equal(written.includes('ricerca-etf'), true);
      assert.equal(written.includes('Ricerca ETF'), false, 'il nome vecchio è ancora a schermo');
      assert.equal(listeners(chip.menu), 3, 'la cartella rinominata non è diventata tappabile');

      // E ora si apre davvero: la riga chiama `select` col nome nuovo.
      const rows = byClass(chip.menu, 'scope-menu-item')
        .filter((r) => texts(r).includes('ricerca-etf'));
      assert.equal(rows.length, 1);
      assert.equal(rows[0].tag, 'button');
    """)


# ── 6. Il contratto coi byte veri del server ────────────────────────────────


def test_the_payload_the_route_builds_is_the_payload_the_chip_reads(tmp_path) -> None:
    """I due lati sullo stesso payload, non su uno scritto a mano nel test.

    `_collect_projects` gira davvero su una cartella davvero fatta così, e il suo
    JSON entra nel `_loadProjects` vero. Un nome di campo cambiato da un lato
    solo — `unopenable` → `unopenables`, `reason` → `why` — muore qui, che è
    l'unico posto in cui i due lati si guardano.
    """
    from jenny.webui.wiki_routes import _collect_projects

    wikis = tmp_path / "wikis"
    for name in ("Ricerca ETF", "patreon"):
        (wikis / name / "wiki").mkdir(parents=True)
        (wikis / name / "wiki" / "index.md").write_text(f"# {name}\n", encoding="utf-8")

    projects, unopenable = _collect_projects(wikis)
    payload = {"dir": wikis.name, "projects": projects, "unopenable": unopenable}
    assert [p["name"] for p in projects] == ["patreon"]

    _run_js(f"""
      const chip = new Chip();
      nextPayload = {json.dumps(payload)};
      await chip._loadProjects();
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.equal(written.includes('Ricerca ETF'), true,
                   'il chip non legge il payload che la route costruisce');
      assert.equal(written.includes({json.dumps(_hint())}), true,
                   "il motivo che il server manda non trova la frase che gli corrisponde");
      assert.equal(byClass(chip.menu, 'is-unopenable').length, 1);
      assert.equal(listeners(chip.menu), 3);
    """)


# ── 7. Le stringhe e la regola ──────────────────────────────────────────────


def test_the_new_strings_exist_in_both_locales() -> None:
    """Grep, non comportamento: nessuna delle due lingue resta con la chiave."""
    for lang in ("it", "en"):
        scope = locale(lang)["scope"]
        for key in ("unopenableSection", "unopenableInvalidName", "unopenableOther"):
            assert key in scope, f"chiave scope.{key} mancante in {lang}.json"
            assert scope[key].strip()
        assert "{rule}" in scope["unopenableInvalidName"], (
            f"{lang}: la nota non ha il posto in cui va la regola dei nomi"
        )
        assert "{rule}" not in scope["unopenableOther"], (
            f"{lang}: un motivo sconosciuto si prende la regola dei nomi"
        )


def test_the_name_rule_is_not_copied_a_fourth_time() -> None:
    """Grep, non comportamento: la regola vive in `scope.invalidName` e basta.

    Di copie a mano ce n'erano già tre (`session/keys.py`, lo scaffolder della
    skill, e la `VALID_NAME` del client). La nota non ne aggiunge una quarta in
    prosa: interpola quella che c'è.
    """
    for lang in ("it", "en"):
        scope = locale(lang)["scope"]
        for key in ("unopenableInvalidName", "unopenableOther", "unopenableSection"):
            lowered = scope[key].lower()
            for word in ("underscore", "64"):
                assert word not in lowered, (
                    f"{lang}: scope.{key} riscrive la regola dei nomi invece di citarla"
                )
    # I tre moduli che hanno a che fare con la regola, da quando è stata portata
    # dove stanno le conversazioni: chi la applica, chi la cita nella tendina,
    # chi la cita creando. La citano per chiave e non la **scrivono** mai.
    assets = ASSETS / "shared"
    trio = {
        name: (assets / name).read_text(encoding="utf-8")
        for name in ("conversation-list.js", "scope-chip.js", "project-create.js")
    }
    citazioni = sum(src.count("'scope.invalidName'") for src in trio.values())
    assert citazioni == 2, (
        f"la regola si cita per key, e i punti che la citano sono due (trovati {citazioni})"
    )
    for name, src in trio.items():
        assert "underscore" not in src.lower(), (
            f"{name}: la regola dei nomi è finita in prosa dentro il JS"
        )
    regex = sum(len(re.findall(r"A-Za-z0-9\]\[A-Za-z0-9", src)) for src in trio.values())
    assert regex == 1, "una seconda regex sulla forma dei nomi nel client"


def test_the_disabled_row_has_a_look_of_its_own() -> None:
    """Grep, non comportamento: che sia spenta e che non si accenda al tocco."""
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.scope-menu-item\.is-unopenable\s*\{([^}]*)\}", css)
    assert rule, "la riga che non si apre si disegna come una che si apre"
    assert "var(--text-faint)" in rule.group(1), "la riga non è spenta"
    assert "cursor: default" in rule.group(1)
    assert re.search(
        r"\.scope-menu-item\.is-unopenable:hover\s*\{[^}]*background:\s*transparent", css
    ), "sotto il dito il fondo si accende: è una promessa che la riga non mantiene"
