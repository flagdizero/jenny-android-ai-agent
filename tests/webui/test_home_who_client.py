"""«Con chi parli»: cosa mette in vetrina la pagina Quaderni, e cosa non promette.

Il pannello dice due cose: con quale conversazione stai parlando, e quali
quaderni ci sono. Le regole dell'elenco — l'ordine, la divisione fra apribili e
non, la cache che sopravvive a una lettura fallita — stanno in
`conversation-list.js` e hanno i loro test dalla parte dell'officina; qui si
misura **quel che finisce a schermo**, che e' l'altra meta' e non si deduce
dalla prima: un elenco giusto disegnato nell'ordine sbagliato resta sbagliato.

E si misura cosa fa un tocco: apre quella conversazione, e la spunta segue
quella in cui sei. Per un giro non e' stato cosi' — le righe erano inerti e i
banchi garantivano l'inerzia — e quei due
banchi sono stati **rovesciati**, non cancellati: chi li vede passare oggi deve
sapere che promettono il contrario di ieri. Le righe delle cartelle non
apribili sono rimaste inerti, e quella proprieta' ha un banco suo.

Dal 05/10/2026 le righe sono copertine su mensole («la vetrina»), con la
ricerca, l'ordine, l'archivio e la tastiera. Le promesse di prima valgono per
le copertine una per una; quelle nuove hanno i loro banchi in fondo.

`home-who.js` si importa **vero** da una cartella temporanea, con accanto i
suoi vicini: veri quelli puri (`conversation-list.js`, `spines.js`,
`type-ahead.js`), finti la rete, la pressione lunga e il DOM. La `t()` e'
quella di `i18n.js` sulle traduzioni vere: quel che si legge nelle asserzioni
e' la frase italiana che legge l'utente.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import locale, member, requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
WHO_JS = ASSETS / "home-who.js"
I18N_JS = ASSETS / "shared" / "i18n.js"

pytestmark = requires_node


def _neighbors() -> dict[str, str]:
    return {
        # La `t()` vera sulle traduzioni vere.
        "i18n.js": (
            "export const i18n = {\n"
            "  locale: 'it',\n"
            f"  translations: {json.dumps({'it': locale('it')}, ensure_ascii=False)},\n"
            f"  {member(I18N_JS.read_text(encoding='utf-8'), 't')}\n"
            "};\n"
        ),
        # La rete a mano: l'elenco risolve o fallisce a comando.
        "api-client.js": """
export const net = { next: null };
export const api = {
  listProjects() {
    if (net.next === 'fail') return Promise.reject(new Error('500'));
    return Promise.resolve(net.next);
  },
};
""",
        # La pressione lunga finta: quella vera sta in `shared/longpress.js` e
        # si prova li'. Qui conta **quali** copertine la armano.
        "longpress.js": """
export const presses = [];
export function setupLongPress(el, cb) { presses.push({ el, cb }); }
""",
    }


_FAKE_DOM = r"""
/* Un DOM finto quanto basta: alberi, classi, stile con le proprieta' CSS,
   `closest`/`querySelector` per classe, il fuoco. `:focus-visible` lo decide
   il banco, perche' e' proprio la differenza fra dito e tastiera. */
let focused = null;
let focusVisible = false;
function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', hidden: false, value: '', placeholder: '',
    parentElement: null, attrs: {}, dataset: {}, listeners: [], children: [],
    style: { setProperty(k, v) { this[k] = v; } },
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { el.listeners.push({ type, fn }); },
    appendChild(child) { child.parentElement = el; el.children.push(child); return child; },
    replaceChildren() { el.children.length = 0; },
    focus() { focused = el; },
    click() { fire(el, 'click', {}); },
    matches(sel) { return sel === ':focus-visible' ? focused === el && focusVisible : false; },
    closest(sel) {
      const cls = sel.slice(1);
      for (let n = el; n; n = n.parentElement) if (hasClass(n, cls)) return n;
      return null;
    },
    querySelectorAll(sel) { return all(el).filter((n) => hasClass(n, sel.slice(1))); },
    querySelector(sel) {
      const [cls, pseudo] = sel.slice(1).split(':');
      return all(el).find((n) => hasClass(n, cls) && (pseudo !== 'focus' || focused === n)) || null;
    },
    classList: {
      add(...names) {
        const have = String(el.className).split(' ').filter(Boolean);
        el.className = [...have, ...names.filter((n) => !have.includes(n))].join(' ');
      },
      contains(name) { return hasClass(el, name); },
    },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (!v) el.children.length = 0; },
  });
  return el;
}
function hasClass(el, name) { return String(el.className).split(' ').includes(name); }
function all(el, out = []) { for (const c of el.children) { out.push(c); all(c, out); } return out; }
function fire(el, type, ev) {
  for (const l of el.listeners) if (l.type === type) l.fn(ev);
}
const docListeners = [];
globalThis.document = {
  createElement: (tag) => makeEl(tag),
  addEventListener(type, fn) { docListeners.push({ type, fn }); },
  get activeElement() { return focused; },
};
"""

_SETUP = """
const { WhoPanel, ARCHIVE_AFTER_DAYS } = await import('./home-who.js');
const { dotColor, spineOf } = await import('./shared/spines.js');
const { net } = await import('./shared/api-client.js');
const { presses } = await import('./shared/longpress.js');

const NOW = Math.floor(Date.now() / 1000);
const DAY = 86400;
const LIST = {
  dir: 'wikis',
  projects: [
    { name: 'piante', modified: NOW - 3 * DAY, pages: 12 },
    { name: 'memory', modified: NOW - 60, pages: 4 },
    { name: 'etna', modified: NOW - 2 * 3600 },
  ],
  unopenable: [],
};

/* Cosa e' successo e in che ordine. Dal 23/09/2026 e' una pagina: non c'e'
   niente da chiudere prima di cambiare conversazione. */
let history = [];
let current = null;
let active = true;
let personal = 'Jenny';
const search = makeEl('input');
const searchClear = makeEl('button');
const plate = makeEl('div');

async function open(payload) {
  history = [];
  net.next = payload;
  const panel = new WhoPanel(makeEl('div'), {
    personalName: () => personal,
    currentProject: () => current,
    onPick: (name) => history.push('scelto:' + name),
    onHold: (name) => history.push('tenuto:' + name),
    onCreate: (name) => history.push('crea:' + name),
    search, searchClear, plate,
    active: () => active,
  });
  await panel.show();
  return panel;
}

const covers = (panel) => panel._body.querySelectorAll('.home-who-cover');
const nameOf = (cover) => {
  const el = cover.children.find((c) => hasClass(c, 'home-who-cover-name'));
  return el.children.length ? el.children.map((c) => c.textContent).join('') : el.textContent;
};
const coverOf = (panel, name) => covers(panel).find((c) => nameOf(c) === name);
const shelves = (panel) => panel._body.querySelectorAll('.home-who-shelf');
const onShelf = (shelf) => shelf.children.filter((c) => hasClass(c, 'home-who-cover')).map(nameOf);

/* Quel che si legge nella pagina, dall'alto in basso: ogni nodo con il suo
   ruolo, cosi' l'asserzione parla di cio' che vede l'utente. */
function readout(panel) {
  const out = [];
  for (const child of panel._body.children) {
    const cls = String(child.className);
    if (cls.startsWith('home-who-head')) {
      out.push('mensola: ' + child.children.filter((c) => !hasClass(c, 'home-who-sort'))
        .map((c) => c.textContent).join(' '));
    } else if (cls.startsWith('home-who-label')) out.push('etichetta: ' + child.textContent);
    else if (cls.startsWith('home-who-note')) {
      out.push((cls.includes('is-error') ? 'guasto: ' : 'nota: ') + child.textContent);
    } else if (cls.startsWith('home-who-shelf')) {
      out.push('[' + child.children.map((c) => (hasClass(c, 'home-who-cover') ? nameOf(c)
        : (hasClass(c, 'home-who-slot') ? '+' : '·'))).join(', ') + ']');
    } else if (cls.startsWith('home-who-row')) out.push('x ' + child.children.map((c) => c.textContent).join(' · '));
    else if (cls.startsWith('home-who-create')) out.push('crea: ' + child.textContent);
  }
  return out;
}

function tap(el) {
  const click = el.listeners.find((l) => l.type === 'click');
  assert.ok(click, 'quella copertina non risponde a un tocco');
  click.fn();
}
function keep(cover) {
  const p = presses.find((x) => x.el === cover);
  assert.ok(p, 'quella copertina non si puo tenere premuta');
  cover.dataset.longpress = 'true';
  p.cb();
}
function key(panel, target, k, extra = {}) {
  let prevented = false;
  const ev = { key: k, target, preventDefault() { prevented = true; }, ...extra };
  fire(panel._body, 'keydown', ev);
  return prevented;
}
"""


def _run(body: str) -> None:
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FAKE_DOM
        + _SETUP
        + textwrap.dedent(body)
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(WHO_JS, root / "home-who.js")
        for name in ("conversation-list.js", "spines.js", "type-ahead.js"):
            shutil.copy(ASSETS / "shared" / name, root / "shared" / name)
        for name, text in _neighbors().items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


# ── Cosa si legge ───────────────────────────────────────────────────────────


def test_the_page_opens_on_the_conversation_you_are_in() -> None:
    """La prima copertina e' la casa, ed e' quella con la spunta."""
    _run("""
      const panel = await open(LIST);
      const rows = readout(panel);
      assert.equal(rows[0], 'mensola: In uso 3');
      const first = covers(panel)[0];
      assert.ok(hasClass(first, 'is-personal'));
      assert.equal(nameOf(first), 'Jenny');
      assert.ok(hasClass(first, 'is-current'));
    """)


def test_the_personal_cover_keeps_its_own_name() -> None:
    """Il nome arriva dal guscio e non si legge dal titolo.

    Lo leggeva di li', ed era giusto finche' nel titolo c'era sempre lo stesso
    nome. Da quando il titolo porta il quaderno aperto, quella fonte farebbe
    dire «piante» alla copertina della conversazione personale.
    """
    _run("""
      personal = 'Qualcunaltra';
      const panel = await open(LIST);
      assert.equal(nameOf(covers(panel)[0]), 'Qualcunaltra');
    """)


def test_notebooks_come_down_from_the_most_recent() -> None:
    _run("""
      const panel = await open(LIST);
      assert.deepEqual(onShelf(shelves(panel)[0]), ['Jenny', 'memory', 'etna', 'piante']);
    """)


def test_no_notebooks_is_said_with_words() -> None:
    _run("""
      const panel = await open({ dir: 'wikis', projects: [], unopenable: [] });
      assert.ok(readout(panel).includes('nota: Non hai ancora nessun quaderno.'),
                readout(panel).join(' | '));
    """)


# ── I tre stati, che sono tre ───────────────────────────────────────────────


def test_a_list_never_read_says_nothing_about_being_empty() -> None:
    """Aperto prima che la lettura torni: «sto leggendo», non «non ce n'e'»."""
    _run("""
      net.next = LIST;
      const panel = new WhoPanel(makeEl('div'), { personalName: () => 'Jenny' });
      panel.render();
      const rows = readout(panel).join(' | ');
      assert.ok(rows.includes('Caricamento'), rows);
      assert.ok(!rows.includes('Non hai ancora nessun quaderno'), rows);
      assert.equal(covers(panel).length, 0, 'mentre legge disegna copertine che non conosce');
    """)


def test_a_failed_read_keeps_the_notebooks_and_says_so_on_top() -> None:
    """Il difetto da cui nasce tutto: un guasto non deve cancellare l'elenco.

    E la nota va **sopra** le copertine, perche' quelle possono essere vecchie
    e questa e' l'unica cosa che lo dice.
    """
    _run("""
      const panel = await open(LIST);
      net.next = 'fail';
      await panel._list.load();
      panel.render();
      const rows = readout(panel);
      const broken = rows.findIndex((r) => r.startsWith('guasto: '));
      const firstShelf = rows.findIndex((r) => r.startsWith('['));
      assert.ok(broken !== -1, 'il guasto non si dichiara: ' + rows.join(' | '));
      assert.ok(broken < firstShelf, 'la nota del guasto sta sotto le copertine che spiega');
      assert.equal(covers(panel).length, 4, 'un guasto ha cancellato i quaderni');
      assert.ok(hasClass(shelves(panel)[0], 'is-stale'), 'le copertine vecchie non si distinguono');
    """)


def test_a_failure_is_never_told_as_an_empty_list() -> None:
    """Prima lettura fallita: non si sa niente, e non si dice il contrario."""
    _run("""
      const panel = await open('fail');
      const rows = readout(panel).join(' | ');
      assert.ok(rows.includes('Non sono riuscita a leggere'), rows);
      assert.ok(!rows.includes('Non hai ancora nessun quaderno'), rows);
      assert.ok(!rows.includes('Caricamento'), rows);
    """)


# ── L'archivio ──────────────────────────────────────────────────────────────


def test_a_notebook_untouched_for_a_month_goes_to_the_archive_shelf() -> None:
    """Un raggruppamento a schermo, non uno spostamento: il quaderno resta
    dov'e', apribile come gli altri, su una mensola sua sotto «In uso»."""
    _run("""
      const old = { name: 'tesi', modified: NOW - (ARCHIVE_AFTER_DAYS + 5) * DAY };
      const panel = await open({ ...LIST, projects: [...LIST.projects, old] });
      const rows = readout(panel);
      assert.equal(rows[0], 'mensola: In uso 3');
      const at = rows.findIndex((r) => r.startsWith('mensola: Archivio'));
      assert.ok(at > 0, rows.join(' | '));
      assert.equal(rows[at], 'mensola: Archivio 1 fermi da più di un mese');
      assert.equal(rows[at + 1], '[tesi]');
      assert.ok(hasClass(shelves(panel)[1], 'is-archive'));
      tap(coverOf(panel, 'tesi'));
      assert.deepEqual(history, ['scelto:tesi'], 'un quaderno in archivio non si apre piu');
    """)


# ── Le cartelle che non si aprono ───────────────────────────────────────────


def test_folders_that_do_not_open_are_shown_last_with_one_note_each_reason() -> None:
    """Si mostrano: sul telefono non c'e' un file manager, e questa e' proprio
    la chat da cui si chiede di rinominarle. Una spiegazione per motivo, non
    per riga."""
    _run("""
      const panel = await open({
        dir: 'wikis',
        projects: [{ name: 'piante', modified: NOW - 100 }],
        unopenable: [
          { name: 'Ricerca ETNA', modified: NOW - 200, reason: 'invalid_name' },
          { name: 'università', modified: NOW - 300, reason: 'invalid_name' },
        ],
      });
      const rows = readout(panel);
      assert.deepEqual(rows.filter((r) => r.startsWith('x ')).map((r) => r.split(' · ')[0]),
                       ['x Ricerca ETNA', 'x università']);
      assert.ok(rows.indexOf('etichetta: Non apribili') > rows.findIndex((r) => r.startsWith('[')),
                'le non apribili vanno dopo i quaderni veri');
      assert.equal(rows.filter((r) => r.startsWith('nota: Queste cartelle')).length, 1,
                   'una nota per riga invece che una per motivo');
    """)


def test_an_unknown_reason_is_not_told_the_name_rule() -> None:
    """Raccontare la regola dei nomi per una cartella rifiutata per altro e'
    peggio che non spiegare niente."""
    _run("""
      const panel = await open({
        dir: 'wikis', projects: [],
        unopenable: [{ name: 'boh', modified: 1, reason: 'qualcosaltro' }],
      });
      const note = readout(panel).find((r) => r.startsWith('nota: Queste cartelle'));
      assert.ok(note, readout(panel).join(' | '));
      assert.ok(!note.includes('64 caratteri'), note);
    """)


def test_a_folder_that_does_not_open_is_not_a_command_and_has_no_cloth() -> None:
    """Il gateway rifiuta quella chiave (`_envelope_chat_id`): un bottone qui
    sarebbe una promessa gia' smentita dall'altra parte. E non ha una tela:
    non e' una scelta fra cui scegliere, e' una cosa da sistemare."""
    _run("""
      const panel = await open({
        dir: 'wikis', projects: [{ name: 'piante', modified: NOW - 1 }],
        unopenable: [{ name: 'Ricerca ETNA', modified: 1, reason: 'invalid_name' }],
      });
      const blocked = panel._body.querySelectorAll('.is-blocked');
      assert.equal(blocked.length, 1);
      assert.notEqual(blocked[0].tag, 'button');
      assert.deepEqual(blocked[0].listeners, [], 'una cartella rotta risponde al tocco');
      assert.equal(blocked[0].style['--cover'], undefined, 'una cartella che non si apre ha una tela');
      assert.ok(!presses.some((p) => p.el === blocked[0]), 'una cartella inerte si puo tenere premuta');
    """)


# ── Cosa fa un tocco ────────────────────────────────────────────────────────


def test_a_notebook_cover_is_a_command() -> None:
    """Per un giro non lo e' stato (D1), e il banco di allora garantiva
    l'inerzia: e' stato rovesciato di proposito, non cancellato."""
    _run("""
      const panel = await open(LIST);
      assert.equal(covers(panel).length, 4, 'la personale piu\\' i tre quaderni');
      for (const cover of covers(panel)) {
        assert.equal(cover.tag, 'button', 'una copertina ha smesso di essere un comando');
      }
    """)


def test_the_name_you_touch_is_the_name_that_comes_back() -> None:
    """Il pannello non sa cosa sia una chiave di sessione: dice il nome, e la
    conversazione la apre chi lo ospita."""
    _run("""
      const panel = await open(LIST);
      tap(coverOf(panel, 'etna'));
      assert.deepEqual(history, ['scelto:etna']);
    """)


def test_the_personal_cover_takes_you_home() -> None:
    """Senza, da dentro un quaderno la pagina saprebbe solo portarti altrove."""
    _run("""
      current = 'etna';
      const panel = await open(LIST);
      tap(covers(panel).find((c) => hasClass(c, 'is-personal')));
      assert.deepEqual(history, ['scelto:null']);
    """)


def test_only_the_cover_you_are_on_carries_the_check() -> None:
    """Una sola, e su quella giusta: la spunta dice dove sei, non cos'e' la
    copertina personale."""
    _run("""
      const checks = (p) => all(p._body).filter((n) => String(n.className).includes('ti-check'));
      const marked = (p) => covers(p).find((c) => hasClass(c, 'is-current'));

      let panel = await open(LIST);
      assert.equal(checks(panel).length, 1);
      assert.ok(hasClass(marked(panel), 'is-personal'));

      current = 'etna';
      panel.render();
      assert.equal(checks(panel).length, 1, 'due spunte: una delle due mente');
      assert.equal(nameOf(marked(panel)), 'etna');
      assert.equal(marked(panel).attrs['aria-current'], 'true',
                   'la spunta e\\' decorativa: chi non la vede deve saperlo lo stesso');
    """)


def test_a_notebook_that_is_gone_leaves_no_check_behind() -> None:
    """Sei dentro un quaderno che l'elenco non porta piu' (rinominato, o
    cancellato da un'altra superficie): meglio nessuna spunta che una spunta
    sulla casa, dove non sei."""
    _run("""
      current = 'sparito';
      const panel = await open(LIST);
      const checks = all(panel._body).filter((n) => String(n.className).includes('ti-check'));
      assert.equal(checks.length, 0, 'la spunta e\\' finita su una conversazione che non e\\' tua');
    """)


# ── La tela ─────────────────────────────────────────────────────────────────


def test_the_cloth_is_an_identity_and_nothing_else() -> None:
    """Stabile nel nome, e su token suoi: un pallino `--error` su un quaderno
    sano si leggerebbe come un allarme."""
    _run("""
      assert.equal(dotColor('piante'), dotColor('piante'));
      assert.match(dotColor('piante'), /^var\\(--spine-[1-6]\\)$/);
      assert.equal(dotColor(''), dotColor(undefined));
      const seen = new Set(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j'].map(spineOf));
      assert.ok(seen.size > 3, 'dieci nomi su due o tre tele: il conto non distribuisce');
    """)


def test_a_cover_wears_the_same_cloth_as_the_notebooks_dot() -> None:
    """Un quaderno ha una tela sola: quella della copertina e' quella del
    pallino della fila e della pastiglia. Due colori per lo stesso quaderno
    sarebbero due quaderni."""
    _run("""
      const panel = await open(LIST);
      for (const name of ['piante', 'memory', 'etna']) {
        const cover = coverOf(panel, name);
        assert.equal(cover.style['--cover'], dotColor(name));
        assert.equal(cover.style['--cover-ink'], `var(--spine-${spineOf(name)}-ink)`);
      }
      const home = covers(panel)[0];
      assert.equal(home.style['--cover'], undefined, 'la casa ha preso la tela di un quaderno');
    """)


def test_the_house_cover_carries_jennys_flower() -> None:
    """Non è un colore assegnato a un nome: è il segno che Jenny ha già nel dock
    e nella riga d'identità dell'officina. Per questo la copertina personale
    non ha un'iniziale — non è un quaderno fra i quaderni."""
    _run("""
      const panel = await open(LIST);
      const home = covers(panel)[0];
      const flower = home.children.find((c) => hasClass(c, 'home-who-flower'));
      assert.ok(flower, 'la casa ha perso il fiore');
      assert.equal(flower.textContent, '✿');
      assert.equal(flower.attrs['aria-hidden'], 'true');
    """)


def test_a_cover_says_its_whole_name_to_the_screen_reader() -> None:
    """Sulla copertina il nome puo' essere tagliato a due righe e l'iniziale
    e' un segno: TalkBack legge l'etichetta, con il nome intero, le pagine e
    quando."""
    _run("""
      const panel = await open(LIST);
      const label = coverOf(panel, 'piante').attrs['aria-label'];
      assert.match(label, /^piante, 12 pagine, /);
      const etna = coverOf(panel, 'etna');
      assert.ok(!etna.attrs['aria-label'].includes('pagine'), 'pagine sconosciute dette come zero');
      assert.ok(!etna.children.some((c) => hasClass(c, 'home-who-cover-pages')),
                'un numero di pagine che non si sa scritto lo stesso');
      const glyph = etna.children.find((c) => hasClass(c, 'home-who-cover-glyph'));
      assert.equal(glyph.textContent, 'E');
      assert.equal(glyph.attrs['aria-hidden'], 'true');
    """)


# ── «Nuovo quaderno» ────────────────────────────────────────────────────────


def test_the_page_draws_a_new_notebook_only_on_an_empty_shelf() -> None:
    """Dal 23/09/2026 «Nuovo quaderno» e' il + tondo della pagina, fermo sopra
    l'elenco: una riga in fondo, con tanti quaderni, finiva sotto il bordo.
    L'unica eccezione e' il posto vuoto sulla mensola quando non c'e' nessun
    quaderno — ne' con l'elenco pieno, ne' con una lettura fallita."""
    _run("""
      const slots = (p) => p._body.querySelectorAll('.home-who-slot');
      assert.equal(slots(await open(LIST)).length, 0);
      assert.equal(slots(await open('fail')).length, 0);
      const empty = await open({ dir: 'wikis', projects: [], unopenable: [] });
      assert.deepEqual(readout(empty).slice(0, 2), ['mensola: In uso 0', '[Jenny, +]']);
      tap(slots(empty)[0]);
      assert.deepEqual(history, ['crea:undefined']);
    """)


# ── Quante pagine ha un quaderno ────────────────────────────────────────────


def test_the_list_carries_the_page_count_it_is_given() -> None:
    """La pastiglia «N pagine» dell'intestazione legge di qui.

    Il conteggio lo manda `/api/projects` insieme al nome e alla data;
    `ConversationList` mappa la voce campo per campo, e un campo non nominato
    si perde in silenzio — la porta resterebbe senza numero per sempre, senza
    un errore da nessuna parte.
    """
    _run("""
      const { ConversationList } = await import('./shared/conversation-list.js');
      const list = new ConversationList(async () => ({
        dir: 'wikis',
        projects: [{ name: 'orto', modified: 20, pages: 34 }],
        unopenable: [],
      }));
      assert.equal(await list.load(), true);
      assert.equal(list.projects[0].pages, 34, 'il conteggio non arriva alla voce');
    """)


def test_a_gateway_that_does_not_send_the_count_says_it_does_not_know() -> None:
    """`null` e non zero: «non lo so» e «e' vuoto» sono due cose diverse, e
    solo la seconda si scrive a schermo."""
    _run("""
      const { ConversationList } = await import('./shared/conversation-list.js');
      const list = new ConversationList(async () => ({
        dir: 'wikis', projects: [{ name: 'orto', modified: 20 }], unopenable: [],
      }));
      await list.load();
      assert.equal(list.projects[0].pages, null);
    """)


def test_the_panel_answers_how_many_pages_and_reads_the_list_if_it_has_to() -> None:
    """La pastiglia compare entrando in un quaderno, che di solito e' prima
    che la pagina sia stata guardata anche una volta: se la cache e' vuota, la
    domanda la riempie invece di rispondere «non lo so» per sempre."""
    _run("""
      const { ConversationList } = await import('./shared/conversation-list.js');
      const panel = new WhoPanel(makeEl('div'), {});
      let reads = 0;
      panel._list = new ConversationList(async () => {
        reads += 1;
        return { dir: 'wikis', projects: [{ name: 'orto', modified: 1, pages: 7 }],
                 unopenable: [] };
      });
      assert.equal(await panel.pagesOf('orto'), 7);
      assert.equal(reads, 1, 'la cache vuota non e’ stata riempita');
      // La seconda volta la cache c'e': non si rilegge.
      assert.equal(await panel.pagesOf('orto'), 7);
      assert.equal(reads, 1, 'riletto l’elenco per una risposta che sapeva gia’');
      // Un quaderno che l'elenco non conosce: «non lo so», non zero.
      assert.equal(await panel.pagesOf('sconosciuto'), null);
      assert.equal(await panel.pagesOf(''), null);
    """)


# ── La pressione lunga: la scheda del quaderno (23/09/2026) ─────────────────
#
# Una cosa si appende dal posto dove vive, e i quaderni vivono qui: tenerne
# premuto uno apre la sua scheda, come un'app nel cassetto.


def test_holding_a_notebook_asks_for_its_sheet() -> None:
    _run("""
      const panel = await open(LIST);
      keep(coverOf(panel, 'etna'));
      assert.deepEqual(history, ['tenuto:etna']);
    """)


def test_the_tap_after_a_hold_does_not_switch_conversation() -> None:
    """Senza, tenere premuto un quaderno aprirebbe la scheda **e** cambierebbe
    conversazione sotto. E il tocco dopo quello torna a essere un tocco."""
    _run("""
      const panel = await open(LIST);
      const cover = coverOf(panel, 'etna');
      keep(cover);
      tap(cover);
      assert.deepEqual(history, ['tenuto:etna'], 'il tocco dopo la pressione ha cambiato conversazione');
      tap(cover);
      assert.deepEqual(history.slice(1), ['scelto:etna']);
    """)


def test_the_personal_cover_cannot_be_held() -> None:
    """Non e' un quaderno: e' gia' la pagina 0, e non si cancella."""
    _run("""
      const panel = await open(LIST);
      const home = covers(panel)[0];
      assert.ok(!presses.some((p) => p.el === home), 'la copertina personale si puo tenere premuta');
    """)


def test_every_openable_notebook_can_be_held() -> None:
    _run("""
      const panel = await open(LIST);
      const notebooks = covers(panel).filter((c) => !hasClass(c, 'is-personal'));
      assert.equal(notebooks.length, 3);
      assert.ok(notebooks.every((q) => presses.some((p) => p.el === q)));
    """)


def test_holding_a_cover_does_not_select_its_text() -> None:
    """**Trovato sul telefono il 23/09/2026.** La selezione di Chromium scatta a
    ~500 ms, prima dei 600 della pressione lunga, e il suo `pointercancel` la
    spegne: sopra il nome compariva «Copy, Share, Select all», e la scheda non
    si apriva. Nessun banco in node lo vede — il DOM finto non seleziona niente
    — quindi lo tiene questo, sul foglio di stile."""
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    cover = css.split("\n.home-who-cover {", 1)[1].split("}", 1)[0]
    for rule in ("user-select: none;", "-webkit-user-select: none;", "-webkit-touch-callout: none;"):
        assert rule in cover, f"la copertina ha perso `{rule}`"


def test_a_redraw_keeps_where_you_had_scrolled() -> None:
    """La pagina Quaderni scorre dentro il proprio contenitore, e il ridisegno
    lo svuota: come nel DOM vero, svuotato torna in cima. Arrivava anche
    mentre la guardavi — l'elenco riletto, un rinomino — e ti riportava su."""
    _run("""
      const panel = await open(LIST);
      const body = panel._body;
      /* Il DOM vero: svuotato, il contenitore non ha piu' altezza e lo
         scorrimento torna a zero. */
      let last = 0;
      body.children = new Proxy([...body.children], {
        set(t, k, v) {
          if (k === 'length' && v === 0) last = 0;
          t[k] = v;
          return true;
        },
      });
      Object.defineProperty(body, 'scrollTop', {
        get() { return last; },
        set(v) { last = body.children.length ? v : 0; },
      });
      body.scrollTop = 240;
      panel.render();
      assert.equal(body.scrollTop, 240, 'il ridisegno ha riportato l\\u2019elenco in cima');
    """)


# ── La ricerca (05/10/2026) ─────────────────────────────────────────────────


def test_searching_shows_only_the_notebooks_whose_name_matches() -> None:
    """Le mensole lasciano il posto a «Risultati»: la casa non c'e' (non e' un
    quaderno da cercare), la parte trovata e' segnata, e l'archivio e' dentro
    — e' una ricerca, non un filtro della mensola che vedi."""
    _run("""
      const old = { name: 'pietre', modified: NOW - 90 * DAY };
      const panel = await open({ ...LIST, projects: [...LIST.projects, old] });
      search.value = 'PI';
      fire(search, 'input', {});
      const rows = readout(panel);
      assert.deepEqual(rows, ['mensola: Risultati 2', '[piante, pietre]'], rows.join(' | '));
      const name = coverOf(panel, 'pietre').children.find((c) => hasClass(c, 'home-who-cover-name'));
      assert.deepEqual(name.children.map((c) => [c.tag, c.textContent]), [['mark', 'pi'], ['span', 'etre']]);
      assert.ok(coverOf(panel, 'pietre').attrs['aria-label'].endsWith('archivio'));
      assert.equal(searchClear.hidden, false, 'la ✕ non compare con qualcosa da pulire');
    """)


def test_a_search_that_finds_nothing_offers_that_name_to_create() -> None:
    """Il nome cercato e' spesso il nome che volevi: si crea con quello gia'
    scritto, e la regola dei nomi la fa il giro di sempre."""
    _run("""
      const panel = await open(LIST);
      search.value = 'giardino';
      fire(search, 'input', {});
      assert.deepEqual(readout(panel), ['nota: Nessun quaderno per «giardino».', 'crea: Crea «giardino»']);
      tap(panel._body.querySelector('.home-who-create'));
      assert.deepEqual(history, ['crea:giardino']);
    """)


def test_clearing_the_search_brings_the_shelves_back() -> None:
    _run("""
      const panel = await open(LIST);
      search.value = 'etna';
      fire(search, 'input', {});
      tap(searchClear);
      assert.equal(search.value, '');
      assert.equal(readout(panel)[0], 'mensola: In uso 3');
      assert.equal(searchClear.hidden, true);
      assert.equal(document.activeElement, search, 'dopo la ✕ il fuoco non torna al campo');
    """)


def test_the_order_changes_but_the_colours_do_not() -> None:
    """A–Z rimette in fila per nome; la tela e' del nome, quindi resta la
    stessa — un colore che cambia con l'ordine non e' un'identita'."""
    _run("""
      const panel = await open(LIST);
      const before = Object.fromEntries(covers(panel).slice(1).map((c) => [nameOf(c), c.style['--cover']]));
      tap(panel._body.querySelector('.home-who-sort'));
      assert.deepEqual(onShelf(shelves(panel)[0]), ['Jenny', 'etna', 'memory', 'piante']);
      for (const c of covers(panel).slice(1)) assert.equal(c.style['--cover'], before[nameOf(c)]);
      assert.equal(panel._body.querySelector('.home-who-sort').textContent, 'A–Z');
    """)


# ── La tastiera fisica ──────────────────────────────────────────────────────


def test_a_letter_typed_on_the_page_goes_to_the_search() -> None:
    """Sul Titan la tastiera e' fisica: una lettera premuta sulla pagina e'
    l'inizio di una ricerca. Solo con la pagina a schermo — con un quaderno
    aperto dentro i Quaderni quella lettera e' per la sua chat."""
    _run("""
      const panel = await open(LIST);
      const typeAhead = docListeners.find((l) => l.type === 'keydown').fn;
      covers(panel)[1].focus();
      typeAhead({ key: 'e' });
      assert.equal(document.activeElement, search);
      covers(panel)[1].focus();
      active = false;
      typeAhead({ key: 'e' });
      assert.notEqual(document.activeElement, search, 'la ricerca ruba una lettera alla chat');
      active = true;
      covers(panel)[1].focus();
      typeAhead({ key: 'ArrowDown' });
      assert.notEqual(document.activeElement, search, 'una freccia non e\\' testo');
    """)


def test_arrows_move_between_covers_and_shift_enter_opens_the_sheet() -> None:
    """←→ di una copertina, ↑ dalla prima torna al campo, ⇧⏎ apre la scheda
    come nel cassetto; ⏎ da solo resta il clic del bottone."""
    _run("""
      const panel = await open(LIST);
      const all4 = covers(panel);
      all4[1].focus();
      assert.ok(key(panel, all4[1], 'ArrowRight'));
      assert.equal(document.activeElement, all4[2]);
      key(panel, all4[2], 'ArrowLeft');
      assert.equal(document.activeElement, all4[1]);
      key(panel, all4[0], 'ArrowUp');
      assert.equal(document.activeElement, search, '↑ dalla prima fila non torna alla ricerca');
      assert.ok(key(panel, all4[2], 'Enter', { shiftKey: true }));
      assert.deepEqual(history, ['tenuto:etna']);
      key(panel, all4[0], 'Enter', { shiftKey: true });
      assert.equal(history.length, 1, 'la casa ha aperto una scheda da quaderno');
      assert.equal(key(panel, all4[1], 'Enter'), false, '⏎ non e\\' piu\\' il clic del bottone');
    """)


def test_the_plate_tells_the_whole_name_only_to_the_keyboard() -> None:
    """Chi sceglie con le frecce non vede il nome tagliato: la targhetta lo
    dice intero. Al dito no — il tocco apre, e una targhetta sotto un dito che
    sta gia' andando altrove sarebbe rumore."""
    _run("""
      const long = { name: 'tesi-magistrale-economia-circolare', modified: NOW - 10 };
      const panel = await open({ ...LIST, projects: [long, ...LIST.projects] });
      const cover = coverOf(panel, long.name);
      cover.focus();
      focusVisible = false;
      fire(panel._body, 'focusin', { target: cover });
      assert.equal(plate.hidden, true, 'la targhetta e\\' comparsa sotto il dito');
      focusVisible = true;
      fire(panel._body, 'focusin', { target: cover });
      assert.equal(plate.hidden, false);
      const name = all(plate).find((n) => hasClass(n, 'home-who-plate-name'));
      assert.equal(name.textContent, long.name);
      assert.equal(plate.style['--cover'], dotColor(long.name));
      fire(panel._body, 'focusout', {});
      assert.equal(plate.hidden, true);
      focusVisible = false;
    """)
