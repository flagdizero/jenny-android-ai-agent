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
le copertine una per una; quelle nuove hanno i loro banchi in fondo. Lo stesso
giorno la conversazione personale e' uscita dalla pagina: e' gia' la voce
«Jenny» della fila, e qui era un doppione.

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
const search = makeEl('input');
const searchClear = makeEl('button');
const plate = makeEl('div');

async function open(payload) {
  history = [];
  net.next = payload;
  const panel = new WhoPanel(makeEl('div'), {
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


def test_the_page_is_only_notebooks() -> None:
    """La conversazione personale non e' una copertina: e' la voce «Jenny»
    della fila in alto, a un tocco da qualunque pagina, e qui sarebbe un
    doppione. Un tocco su una copertina apre sempre un quaderno."""
    _run("""
      const panel = await open(LIST);
      assert.equal(readout(panel)[0], 'mensola: In uso 3');
      assert.equal(covers(panel).length, 3, 'la pagina disegna qualcosa che non e\\' un quaderno');
      assert.ok(!covers(panel).some((c) => nameOf(c) === 'Jenny' || hasClass(c, 'is-personal')));
      for (const cover of covers(panel)) tap(cover);
      assert.ok(history.every((h) => h !== 'scelto:null'), 'un tocco ha portato alla chat personale');
    """)


def test_notebooks_come_down_from_the_most_recent() -> None:
    """Dal 06/10/2026 in testa c'e' il quaderno con piu' pagine (la tessera
    grande, in alto a sinistra); gli altri scendono dal piu' recente."""
    _run("""
      const panel = await open(LIST);
      assert.deepEqual(onShelf(shelves(panel)[0]), ['piante', 'memory', 'etna']);
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
      const panel = new WhoPanel(makeEl('div'), {});
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
      assert.equal(covers(panel).length, 3, 'un guasto ha cancellato i quaderni');
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


def test_a_redraw_after_invalidate_reads_the_list_again() -> None:
    """Creato un quaderno (`invalidate`), il ridisegno che arriva senza `show()`
    — il cambio di conversazione, il ResizeObserver al ritorno sui Quaderni —
    non resta su «Caricamento…»: va a rileggere l'elenco, una volta sola.
    Visto su jenny_square il 10/10/2026, pagina ferma sugli scheletri."""
    _run("""
      const panel = await open(LIST);
      let reads = 0;
      const fetch = panel._list._fetch;
      panel._list._fetch = () => { reads += 1; return fetch(); };
      net.next = { ...LIST, projects: [...LIST.projects, { name: 'nuovo', modified: NOW }] };
      panel.invalidate();
      panel.render();
      panel.render();
      assert.ok(readout(panel).join(' | ').includes('Caricamento'));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(reads, 1, 'due ridisegni, ' + reads + ' letture');
      assert.ok(coverOf(panel, 'nuovo'), 'il quaderno nuovo non compare: ' + readout(panel).join(' | '));
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
      assert.equal(covers(panel).length, 3);
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


def test_the_page_draws_no_check_and_tells_talkback_where_you_are() -> None:
    """La spunta sul quaderno in cui sei e' stata tolta il 06/10/2026: sulla
    pagina dei quaderni non diceva niente che servisse. Resta `aria-current`,
    su una tessera sola e su quella giusta, per chi legge con TalkBack; su
    nessuna quando sei con Jenny o in un quaderno che l'elenco non porta piu'."""
    _run("""
      const checks = (p) => all(p._body).filter((n) => String(n.className).includes('check'));
      const marked = (p) => covers(p).filter((c) => c.attrs['aria-current'] === 'true');

      let panel = await open(LIST);
      assert.equal(marked(panel).length, 0, 'segnato un quaderno mentre sei con Jenny');

      current = 'etna';
      panel.render();
      assert.equal(checks(panel).length, 0, 'la spunta e\\' tornata');
      assert.deepEqual(marked(panel).map(nameOf), ['etna']);

      current = 'sparito';
      panel.render();
      assert.equal(marked(panel).length, 0, 'segnata una conversazione che non e\\' tua');
      current = null;
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
      assert.deepEqual(readout(empty).slice(0, 2), ['mensola: In uso 0', '[+]']);
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


def test_every_openable_notebook_can_be_held() -> None:
    _run("""
      const panel = await open(LIST);
      const notebooks = covers(panel);
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
    """Le mensole lasciano il posto a «Risultati»: la parte trovata e'
    segnata, e l'archivio e' dentro — e' una ricerca, non un filtro della
    mensola che vedi."""
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
      const before = Object.fromEntries(covers(panel).map((c) => [nameOf(c), c.style['--cover']]));
      tap(panel._body.querySelector('.home-who-sort'));
      assert.deepEqual(onShelf(shelves(panel)[0]), ['etna', 'memory', 'piante']);
      for (const c of covers(panel)) assert.equal(c.style['--cover'], before[nameOf(c)]);
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
      const [first, second] = covers(panel);
      first.focus();
      assert.ok(key(panel, first, 'ArrowRight'));
      assert.equal(document.activeElement, second);
      key(panel, second, 'ArrowLeft');
      assert.equal(document.activeElement, first);
      key(panel, first, 'ArrowUp');
      assert.equal(document.activeElement, search, '↑ dalla prima fila non torna alla ricerca');
      assert.ok(key(panel, second, 'Enter', { shiftKey: true }));
      assert.deepEqual(history, ['tenuto:' + nameOf(second)]);
      assert.equal(key(panel, second, 'Enter'), false, '⏎ non e\\' piu\\' il clic del bottone');
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


# ── Il bento (06/10/2026) ───────────────────────────────────────────────────
#
# Scelto dall'utente fra dieci varianti: il quaderno piu' recente e' la
# tessera grande, i due con piu' pagine sono alti e il terzo e' largo. Solo
# nell'ordine per data.

_SHELF = """
const many = { dir: 'wikis', unopenable: [], projects: [
  { name: 'etf', modified: NOW - 60, pages: 30 },
  { name: 'umore', modified: NOW - 2 * 3600, pages: 6 },
  { name: 'orto', modified: NOW - 3 * 3600, pages: 41 },
  { name: 'viaggi', modified: NOW - 4 * 3600, pages: 26 },
  { name: 'salute', modified: NOW - 5 * 3600, pages: 39 },
  { name: 'nuovo', modified: NOW - 6 * 3600, pages: 0 },
  { name: 'muto', modified: NOW - 7 * 3600 },
]};
const sizeOf = (cover) => ['hero', 'tall', 'wide'].find((s) => hasClass(cover, 'is-' + s)) || '';
const sizes = (panel) => Object.fromEntries(covers(panel).map((c) => [nameOf(c), sizeOf(c)]));
const part = (cover, cls) => cover.children.find((c) => hasClass(c, cls));
"""


def test_the_thickest_notebook_is_the_big_tile() -> None:
    """La grandezza dice le pagine: la tessera grande e' il quaderno che ne ha
    di piu', in testa. Per un giro era il piu' recente, con «Il più recente» e
    «Riprendi»: tolti tutti e due il 06/10/2026 — l'utente si aspettava il
    quaderno piu' lungo, e la tessera intera gia' apre."""
    _run(_SHELF + """
      const panel = await open(many);
      const orto = coverOf(panel, 'orto');
      assert.equal(covers(panel)[0], orto, 'la tessera grande non apre la mensola');
      assert.equal(sizeOf(orto), 'hero');
      assert.match(part(orto, 'home-who-cover-meta').textContent, /^41 pagine · /);
      for (const gone of ['home-who-cover-kicker', 'home-who-cover-resume']) {
        assert.ok(!covers(panel).some((c) => part(c, gone)), `${gone} e' tornato`);
      }
      assert.match(orto.attrs['aria-label'], /^orto, 41 pagine, /);
    """)


def test_the_thickest_notebooks_get_the_bigger_tiles() -> None:
    """Dopo la grande: i due con piu' pagine alti, il terzo largo, gli altri
    semplici. Un quaderno vuoto o di cui non si sanno le pagine resta
    semplice, anche quando mancano candidati."""
    _run(_SHELF + """
      const panel = await open(many);
      assert.deepEqual(sizes(panel), {
        orto: 'hero', etf: 'tall', umore: '', viaggi: 'wide', salute: 'tall', nuovo: '', muto: '',
      });
      // Dietro la grande l'ordine resta quello per data.
      assert.deepEqual(onShelf(shelves(panel)[0]),
                       ['orto', 'etf', 'umore', 'viaggi', 'salute', 'nuovo', 'muto']);
    """)


def test_a_small_tile_says_one_thing_and_a_big_one_says_both() -> None:
    """Sulla tessera semplice la riga si ferma prima dell'iniziale: c'e' posto
    per le pagine, o per la data quando le pagine non si sanno. Le grandi
    dicono tutte e due."""
    _run(_SHELF + """
      const panel = await open(many);
      const meta = (name) => part(coverOf(panel, name), 'home-who-cover-meta').textContent;
      assert.equal(meta('umore'), '6 p');
      assert.equal(meta('nuovo'), '0 p', 'zero pagine e\\' un numero, non un «non so»');
      assert.ok(!meta('muto').includes(' p'), 'pagine scritte senza saperle');
      assert.ok(meta('muto').length > 0, 'senza pagine la tessera non dice nemmeno quando');
      assert.match(meta('salute'), /^39 p · /);
    """)


def test_a_to_z_results_and_archive_are_plain_tiles() -> None:
    """In A–Z, nei risultati e in archivio conta la fila: tessere tutte uguali,
    nessuna «piu' recente» che non lo e'."""
    _run(_SHELF + """
      const old = { name: 'ortaggi', modified: NOW - 90 * DAY, pages: 60 };
      const panel = await open({ ...many, projects: [...many.projects, old] });
      assert.equal(sizeOf(coverOf(panel, 'ortaggi')), '', 'in archivio una tessera grande');
      assert.ok(hasClass(shelves(panel)[1], 'is-archive'));
      tap(panel._body.querySelector('.home-who-sort'));
      assert.ok(Object.values(sizes(panel)).every((s) => s === ''), 'A–Z con le tessere grandi');
      tap(panel._body.querySelector('.home-who-sort'));
      search.value = 'ort';
      fire(search, 'input', {});
      assert.ok(Object.values(sizes(panel)).every((s) => s === ''), 'risultati con le tessere grandi');
      assert.ok(hasClass(coverOf(panel, 'ortaggi'), 'is-archived'), 'un risultato d\\'archivio non si smorza');
      tap(searchClear);
    """)


def test_arrows_follow_the_tiles_on_screen() -> None:
    """Col bento l'ordine del DOM non e' quello che si vede: ↓ dalla tessera
    grande va a quella che le sta sotto, non alla seconda per data. Le misure
    sono quelle di una griglia a quattro colonne da 131 × 104, con gap 10."""
    _run(_SHELF + """
      const panel = await open(many);
      const at = (name, col, row, w = 1, h = 1) => {
        const left = col * 141, top = row * 114;
        coverOf(panel, name).getBoundingClientRect = () => ({
          left, top, right: left + w * 131 + (w - 1) * 10, bottom: top + h * 104 + (h - 1) * 10,
          width: w * 131 + (w - 1) * 10, height: h * 104 + (h - 1) * 10,
        });
      };
      // etf 2x2 | orto alta | salute alta ; viaggi larga | umore | nuovo ; muto
      at('etf', 0, 0, 2, 2); at('orto', 2, 0, 1, 2); at('salute', 3, 0, 1, 2);
      at('viaggi', 0, 2, 2, 1); at('umore', 2, 2); at('nuovo', 3, 2); at('muto', 0, 3);
      const etf = coverOf(panel, 'etf');
      etf.focus();
      key(panel, etf, 'ArrowDown');
      assert.equal(nameOf(document.activeElement), 'viaggi');
      key(panel, document.activeElement, 'ArrowRight');
      assert.equal(nameOf(document.activeElement), 'umore');
      key(panel, document.activeElement, 'ArrowUp');
      assert.equal(nameOf(document.activeElement), 'orto');
      key(panel, document.activeElement, 'ArrowUp');
      assert.equal(document.activeElement, search, '↑ dalla prima fila non torna al campo');
    """)


def test_one_page_is_said_in_the_singular() -> None:
    """Le traduzioni non conoscono il plurale: «1 pagine» si e' visto
    sull'emulatore, sulla tessera grande, il 06/10/2026."""
    _run(_SHELF + """
      const one = { name: 'appena', modified: NOW, pages: 1 };
      const panel = await open({ ...many, projects: [one] });
      const cover = coverOf(panel, 'appena');
      assert.match(part(cover, 'home-who-cover-meta').textContent, /^1 pagina · /);
      assert.match(cover.attrs['aria-label'], /^appena, 1 pagina, /);
    """)


# ── Un rettangolo pieno (06/10/2026) ────────────────────────────────────────
#
# Visto sul Titan: con le misure decise solo dalle pagine la colonna di destra
# restava vuota per due file. Le misure ora le sceglie `packBento`, sulle
# colonne vere, perche' la griglia si chiuda.

_PACK = """
const { packBento, bentoOrder } = await import('./home-who.js');
const SHAPE = { hero: [2, 2], tall: [1, 2], wide: [2, 1] };
/* Mette le tessere come la griglia `dense`: ognuna nella prima cella libera
   in cui sta. Dice quante file servono e se resta un buco. */
function lay(items, sizes, columns) {
  const taken = new Set();
  let rows = 0;
  for (const it of items) {
    const [w, h] = SHAPE[sizes.get(it.name)] || [1, 1];
    for (let at = 0; ; at += 1) {
      const r = Math.floor(at / columns), c = at % columns;
      if (c + w > columns) continue;
      const cells = [];
      for (let y = 0; y < h; y += 1) for (let x = 0; x < w; x += 1) cells.push((r + y) * columns + c + x);
      if (cells.some((k) => taken.has(k))) continue;
      for (const k of cells) taken.add(k);
      rows = Math.max(rows, r + h);
      break;
    }
  }
  return { rows, holes: rows * columns - taken.size };
}
const shelfOf = (n) => Array.from({ length: n }, (_, i) => ({ name: 'q' + i, pages: (i * 7) % 23 }));
/* La larghezza delle griglie, a comando: 0 e' «non ancora a schermo». */
let width = 0;
{
  const make = document.createElement;
  document.createElement = (tag) => {
    const el = make(tag);
    Object.defineProperty(el, 'clientWidth', { get: () => width });
    return el;
  };
}
"""


def test_the_bento_closes_into_a_full_rectangle() -> None:
    """Per ogni numero di quaderni e di colonne in cui si puo', nessun buco e
    l'ultima fila piena; la tessera grande e' sempre il primo, in alto a
    sinistra."""
    _run(_SHELF + _PACK + """
      for (const columns of [2, 3, 4, 5]) {
        for (let n = 2; n <= 24; n += 1) {
          const items = shelfOf(n);
          const packed = packBento(items, columns);
          if (!packed) continue;
          assert.equal(packed.get('q0'), 'hero', `${n} su ${columns}: la grande non e' il primo`);
          const { holes } = lay(items, packed, columns);
          assert.equal(holes, 0, `${n} quaderni su ${columns} colonne: ${holes} celle vuote`);
        }
      }
      // Dove si puo', si deve potere: dal terzo quaderno in su, su quattro colonne.
      for (let n = 3; n <= 24; n += 1) assert.ok(packBento(shelfOf(n), 4), `${n} su 4: rinuncia`);
    """)


def test_the_titan_shelf_has_no_empty_column() -> None:
    """Il caso visto sul telefono: dieci quaderni su quattro colonne lasciavano
    vuota la colonna di destra, e piante — la piu' lunga — era piccola in
    fondo. Ora piante e' la grande in alto a sinistra, e i piu' lunghi dopo di
    lei restano tessere grandi: alte se c'e' posto, larghe nell'ultima fila."""
    _run(_SHELF + _PACK + """
      const titan = [
        { name: 'etf', pages: 30 }, { name: 'umore', pages: 6 }, { name: 'editoriale', pages: 18 },
        { name: 'travel', pages: 3 }, { name: 'produttivita', pages: 11 }, { name: 'nomadismo', pages: 26 },
        { name: 'allergie', pages: 39 }, { name: 'salute', pages: 10 }, { name: 'piante', pages: 41 },
        { name: 'viaggio', pages: 7 },
      ];
      const shelf = bentoOrder(titan);
      assert.equal(shelf[0].name, 'piante', 'la piu\\' lunga non e\\' in testa');
      const packed = packBento(shelf, 4);
      assert.deepEqual(lay(shelf, packed, 4), { rows: 4, holes: 0 });
      assert.equal(packed.get('piante'), 'hero');
      // Chi cade nell'ultima fila non puo' essere alto senza sporgere: si
      // allarga invece. Ma i piu' lunghi dopo piante restano tessere grandi.
      for (const name of ['allergie', 'etf']) {
        assert.ok(['tall', 'wide'].includes(packed.get(name)), `${name}: ${packed.get(name)}`);
      }
    """)


def test_plain_grids_widen_only_to_close_the_last_row() -> None:
    """Archivio, A–Z e risultati non hanno la grande: si allarga solo quanto
    basta a chiudere l'ultima fila, e un quaderno solo resta com'e'."""
    _run(_SHELF + _PACK + """
      const seven = shelfOf(7);
      const packed = packBento(seven, 4, { hero: false });
      assert.deepEqual(lay(seven, packed, 4), { rows: 2, holes: 0 });
      assert.equal(packed.size, 1, 'allargate piu\\' tessere del necessario');
      assert.equal(packBento(shelfOf(8), 4, { hero: false }).size, 0, 'una griglia gia\\' piena toccata');
      assert.equal(packBento(shelfOf(1), 4, { hero: false }), null);
    """)


def test_the_panel_packs_on_the_columns_it_sees() -> None:
    """Il pannello conta le colonne dalla larghezza della griglia, le impone
    alla griglia e chiude il rettangolo su quelle: calcolo e griglia contano
    lo stesso numero. Senza una larghezza (non ancora a schermo) restano i
    desideri."""
    _run(_SHELF + _PACK + """
      width = 554;
      const panel = await open(many);
      const items = covers(panel).map((c) => ({ name: nameOf(c) }));
      const shaped = new Map(covers(panel).map((c) => [nameOf(c), sizeOf(c)]).filter(([, s]) => s));
      assert.equal(lay(items, shaped, 4).holes, 0, JSON.stringify([...shaped]));
      assert.equal(shelves(panel)[0].style.gridTemplateColumns, 'repeat(4, minmax(0, 1fr))',
                   'la griglia non ha le colonne del calcolo: possono tornare i buchi');
      width = 0;
      panel.render();
      assert.deepEqual(sizes(panel), {
        orto: 'hero', etf: 'tall', umore: '', viaggi: 'wide', salute: 'tall', nuovo: '', muto: '',
      });
    """)


def test_a_rectangle_made_for_other_columns_is_made_again() -> None:
    """Visto sul Titan il 06/10/2026: disegnato mentre la larghezza non era
    ancora quella vera, il rettangolo era fatto per cinque colonne e mostrato
    su quattro — piante piccola in fondo e una cella vuota. Quando il
    contenitore cambia misura e le colonne non sono quelle del calcolo, si
    rifa'; se sono le stesse, non si tocca."""
    _run(_SHELF + _PACK + """
      const watchers = [];
      globalThis.ResizeObserver = class {
        constructor(cb) { watchers.push(cb); }
        observe() {}
      };
      width = 610;
      const panel = await open(many);
      assert.equal(watchers.length, 1, 'nessuno guarda la larghezza del contenitore');
      const shaped = () => new Map(covers(panel).map((c) => [nameOf(c), sizeOf(c)]).filter(([, s]) => s));
      const shown = () => covers(panel).map((c) => ({ name: nameOf(c) }));
      assert.equal(lay(shown(), shaped(), 5).holes, 0);
      width = 554;
      watchers[0]();
      assert.equal(lay(shown(), shaped(), 4).holes, 0, 'il rettangolo e\\' rimasto quello da cinque');
      let redraws = 0;
      const render = panel.render.bind(panel);
      panel.render = () => { redraws += 1; render(); };
      watchers[0]();
      assert.equal(redraws, 0, 'ridisegnato senza che le colonne fossero cambiate');
      delete globalThis.ResizeObserver;
    """)


def test_one_drawing_counts_its_columns_once() -> None:
    """Sul Titan, il 06/10/2026, «In uso» e' uscita a cinque colonne e
    l'archivio a quattro nello stesso disegno: fra le due misure era comparsa
    la barra di scorrimento. Le griglie di un disegno hanno le stesse colonne,
    e se poi la larghezza vera ne vuole altre, si ridisegna."""
    _run(_SHELF + _PACK + """
      const watchers = [];
      globalThis.ResizeObserver = class {
        constructor(cb) { watchers.push(cb); }
        observe() {}
      };
      const old = [1, 2, 3].map((i) => ({ name: 'vecchio' + i, modified: NOW - 90 * DAY, pages: i }));
      width = 610;
      const panel = await open({ ...many, projects: [...many.projects, ...old] });
      const cols = () => shelves(panel).map((s) => s.style.gridTemplateColumns);
      assert.equal(new Set(cols()).size, 1, 'due griglie, due colonne: ' + cols().join(' / '));
      width = 554;  // la barra di scorrimento e' comparsa
      watchers[0]();
      assert.deepEqual(cols(), ['repeat(4, minmax(0, 1fr))', 'repeat(4, minmax(0, 1fr))']);
      delete globalThis.ResizeObserver;
    """)
