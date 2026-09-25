"""La fila dei nomi in alto, e la modalita' ordina che si apre tenendone premuto uno.

Prende il posto di titolo, ingranaggio, bottone del cassetto e pallini (v.
`.agent/pagine-in-alto-plan.md`): quello dove sei e' grande, un tocco su un nome
ci va, e tenendo premuto le pagine si spostano. Qui si prova **cosa dice** e
**cosa chiede alla pista**; la pista vera ha il suo banco
(`test_home_track_client.py`), e il dito vero si prova sul telefono.

In node sul file vero, con i vicini finti: il DOM finto costruisce gli elementi
come fa il modulo, e il banco li legge.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node


_NEIGHBORS = {
    "i18n.js": """
export const i18n = {
  t: (k, v) => k + (v ? ':' + JSON.stringify(v) : ''),
};
""",
    "api-client.js": "export const api = { async listProjects() { return { projects: [] }; } };\n",
    # La pressione lunga finta: quella vera si prova dove vive. Qui conta **chi**
    # la arma, e che posi lo stesso segno di quella vera.
    "longpress.js": """
export const presses = [];
export function setupLongPress(el, cb) { presses.push({ el, cb }); }
""",
}

_FAKE_DOM = """
function createEl(tag) {
  const el = {
    tag, className: '', children: [], dataset: {}, style: {}, attrs: {},
    listeners: {}, textContent: '', tabIndex: -1,
    offsetLeft: 0, offsetWidth: 60, offsetTop: 0, scrollLeft: 0, scrollWidth: 300, clientWidth: 300,
    set innerHTML(v) { this._html = v; },
    get classList() {
      const e = this;
      const words = () => (e.className || '').split(' ').filter(Boolean);
      return {
        add(c) { if (!words().includes(c)) e.className = [...words(), c].join(' '); },
        remove(c) { e.className = words().filter((x) => x !== c).join(' '); },
        toggle(c, on) { if (on) this.add(c); else this.remove(c); },
        contains(c) { return words().includes(c); },
      };
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    append(...cs) { for (const c of cs) this.appendChild(c); },
    replaceChildren(...cs) { this.children = []; this.append(...cs); },
    insertBefore(c, ref) {
      this.children = this.children.filter((x) => x !== c);
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
      c.parent = this;
      return c;
    },
    getBoundingClientRect() { return { left: this.offsetLeft, top: 0, width: 60, height: 40 }; },
    querySelector(sel) {
      const m = sel.match(/data-id="([^"]+)"/);
      const find = (n) => {
        for (const c of n.children) {
          if (m && c.dataset.id === m[1]) return c;
          const r = find(c);
          if (r) return r;
        }
        return null;
      };
      return find(this);
    },
    focus() { globalThis.focus = this; },
    setPointerCapture() {},
  };
  return el;
}
const docListeners = {};
globalThis.document = {
  createElement: createEl,
  addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
  removeEventListener(t, fn) { docListeners[t] = (docListeners[t] || []).filter((x) => x !== fn); },
};
function onDocument(type, e) { for (const fn of [...(docListeners[type] || [])]) fn(e); }
globalThis.CSS = { escape: (s) => s };
function fire(el, type, e = {}) { for (const fn of el.listeners[type] || []) fn(e); }
function all(el, out = []) { for (const c of el.children) { out.push(c); all(c, out); } return out; }
"""


def _run(body: str) -> None:
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FAKE_DOM
        + textwrap.dedent(
            """
            const { HomeStrip } = await import('./home-strip.js');
            const { presses } = await import('./shared/longpress.js');
            const { dotColor } = await import('./home-who.js');
            /* La pista finta: le voci come le da' quella vera, e cosa le si chiede. */
            const requested = [];
            const homePages = {
              fixed: ['app', 'chat', 'notebooks', 'settings'],
              pages: [{ id: 'p1', kind: 'app', ref: 'todo' },
                          { id: 'q1', kind: 'conversation', ref: 'project:piante' }],
              order: ['app', 'chat', 'p1', 'q1', 'notebooks', 'settings'],
              index: 1,
              get entries() {
                return this.order.map((id) => this.fixed.includes(id)
                  ? { id, kind: id === 'app' ? 'drawer' : id, fixed: true }
                  : { ...this.pages.find((s) => s.id === id), fixed: false });
              },
              nameOf: (s) => (s.kind === 'conversation' ? s.ref.split(':')[1] : s.ref),
              goTo(i) { requested.push(['goTo', i]); this.index = i; },
              /* Come la vera: l'elenco salvato, o `false` se il server ha
                 rifiutato (l'avviso lo da' lei). `pending` tiene la
                 scrittura sospesa finche' il caso non la lascia andare. */
              refuses: false,
              pending: null,
              async save(s, o) {
                requested.push(['salva', s.map((x) => x.id), o]);
                if (this.pending) await this.pending;
                return this.refuses ? false : { pages: s, order: o };
              },
            };
            let chatName = { name: 'Jenny', color: null };
            const changes = [];
            const el = createEl('div');
            const strip = new HomeStrip(el, {
              homePages,
              chatName: () => chatName,
              onChange: (open) => changes.push(open),
            });
            strip.draw();
            const entries = () => el.children[0].children;
            const names = () => entries().map((b) => all(b).find((c) => c.className === 'home-strip-name').textContent);
            const pills = () => el.children[1].children;
            """
        )
        + textwrap.dedent(body)
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "home-strip.js", root / "home-strip.js")
        shutil.copy(ASSETS / "home-who.js", root / "home-who.js")
        shutil.copy(ASSETS / "shared" / "conversation-list.js", root / "shared" / "conversation-list.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


# ── La fila ─────────────────────────────────────────────────────────────────


def test_every_page_has_its_name_in_order() -> None:
    """Le fisse col nome delle traduzioni, la chat col nome della conversazione,
    le aggiunte col loro."""
    _run("""
      assert.deepEqual(names(), [
        'home.strip.app', 'Jenny', 'todo', 'piante', 'home.strip.notebooks', 'home.strip.settings',
      ]);
    """)


def test_the_page_you_are_on_is_the_big_one_and_says_so() -> None:
    """Grande per chi guarda, `aria-selected` per chi ascolta."""
    _run("""
      const active = entries().filter((b) => b.classList.contains('is-on'));
      assert.equal(active.length, 1);
      assert.equal(active[0].dataset.id, 'chat');
      assert.equal(active[0].attrs['aria-selected'], 'true');
      assert.equal(entries()[0].attrs['aria-selected'], 'false');
      homePages.index = 4;
      strip.draw();
      assert.equal(entries().find((b) => b.classList.contains('is-on')).dataset.id, 'notebooks');
    """)


def test_inside_a_notebook_the_chat_page_wears_its_name_and_dot() -> None:
    """Deciso con l'utente il 23/09/2026: il nome del quaderno al posto di
    «Jenny», col pallino dei Quaderni."""
    _run("""
      chatName = { name: 'ristrutturazione', color: dotColor('ristrutturazione') };
      strip.draw();
      const chat = entries()[1];
      assert.equal(names()[1], 'ristrutturazione');
      const dot = chat.children.find((c) => c.className === 'home-strip-dot');
      assert.ok(dot, 'la chat dentro un quaderno non ha il pallino');
      assert.equal(dot.style.background, dotColor('ristrutturazione'));
      const personal = entries()[0].children.find((c) => c.className === 'home-strip-dot');
      assert.equal(personal, undefined, 'il cassetto ha un pallino');
    """)


def test_a_notebook_page_has_the_dot_of_its_notebook() -> None:
    _run("""
      const q = entries()[3];
      const dot = q.children.find((c) => c.className === 'home-strip-dot');
      assert.equal(dot.style.background, dotColor('piante'));
    """)


def test_a_tap_on_a_name_goes_there() -> None:
    _run("""
      fire(entries()[4], 'click');
      assert.deepEqual(requested, [['goTo', 4]]);
    """)


def test_the_tap_that_follows_a_long_press_goes_nowhere() -> None:
    """Tenere premuto apre la modalita' ordina, e il click che segue non deve
    anche portarti su quella pagina."""
    _run("""
      const b = entries()[2];
      const p = presses.find((x) => x.el === b);
      assert.ok(p, 'un nome non si puo tenere premuto');
      b.dataset.longpress = 'true';
      p.cb();
      fire(b, 'click');
      assert.deepEqual(requested, [], 'il click dopo la pressione lunga ha cambiato pagina');
      assert.equal(strip.sorting, true);
    """)


# ── La modalita' ordina ─────────────────────────────────────────────────────


def test_holding_a_name_opens_the_moving_mode() -> None:
    """Le pagine diventano pastiglie, nell'ordine di adesso, e il guscio lo sa
    (la pagina sotto si spegne, la tastiera si chiude)."""
    _run("""
      strip.openSort();
      assert.equal(strip.sorting, true);
      assert.deepEqual(changes, [true]);
      assert.ok(el.classList.contains('is-sort'));
      assert.deepEqual(pills().map((p) => p.dataset.id), homePages.order);
    """)


def test_only_added_pages_have_the_cross() -> None:
    """Le quattro fisse si spostano ma non si tolgono: senza Impostazioni non ci
    sarebbe piu' una strada per tornarci."""
    _run("""
      strip.openSort();
      const withCross = pills()
        .filter((p) => p.children.some((c) => c.className === 'home-sort-remove'))
        .map((p) => p.dataset.id);
      assert.deepEqual(withCross, ['p1', 'q1']);
      strip.remove('settings');
      assert.equal(pills().length, 6, 'una pagina fissa si e tolta');
    """)


def test_done_writes_the_new_order_once() -> None:
    """Spostare e togliere sono una scrittura sola, e parte a «Fatto»."""
    _run("""
      strip.openSort();
      strip.move('p1', 0);
      strip.remove('q1');
      assert.deepEqual(requested, [], 'ha scritto prima di Fatto');
      await strip.closeSort({ save: true });
      assert.deepEqual(requested, [
        ['salva', ['p1'], ['p1', 'app', 'chat', 'notebooks', 'settings']],
      ]);
      assert.equal(strip.sorting, false);
      assert.deepEqual(changes, [true, false]);
    """)


def test_a_refused_done_keeps_the_moving_mode_and_the_draft() -> None:
    """La bozza si azzerava **prima** di scrivere: un rifiuto del server
    perdeva l'ordine in silenzio, e la modalita' ordina era gia' chiusa.
    Ora si resta dentro, con la bozza com'era, e «Fatto» si ripreme."""
    _run("""
      strip.openSort();
      strip.move('p1', 0);
      homePages.refuses = true;
      assert.equal(await strip.closeSort({ save: true }), false);
      assert.equal(strip.sorting, true, 'un salvataggio rifiutato ha chiuso la modalita\\u2019 ordina');
      assert.deepEqual(changes, [true], 'il guscio crede che la modalita\\u2019 ordina sia chiusa');
      strip.draw();
      assert.equal(pills()[0].dataset.id, 'p1', 'la bozza si e\\u2019 persa');
      homePages.refuses = false;
      assert.equal(await strip.closeSort({ save: true }), true);
      assert.equal(strip.sorting, false);
      assert.equal(requested.length, 2);
      assert.deepEqual(requested[1], requested[0], 'il secondo Fatto non ha riscritto la stessa bozza');
    """)


def test_a_second_done_while_the_first_is_writing_does_nothing() -> None:
    _run("""
      strip.openSort();
      strip.move('p1', 0);
      let release;
      homePages.pending = new Promise((r) => { release = r; });
      const first = strip.closeSort({ save: true });
      assert.equal(await strip.closeSort({ save: true }), false);
      release();
      assert.equal(await first, true);
      assert.equal(requested.filter((c) => c[0] === 'salva').length, 1, 'due Fatto, due scritture');
      assert.equal(strip.sorting, false);
    """)


def test_back_leaves_everything_as_it_was() -> None:
    _run("""
      strip.openSort();
      strip.move('settings', 0);
      strip.remove('p1');
      await strip.closeSort();
      assert.deepEqual(requested, []);
      assert.deepEqual(names()[0], 'home.strip.app', 'la fila mostra un ordine mai salvato');
    """)


def test_done_without_changes_writes_nothing() -> None:
    _run("""
      strip.openSort();
      await strip.closeSort({ save: true });
      assert.deepEqual(requested, []);
    """)


def test_the_arrows_move_a_page_too() -> None:
    """Chi non trascina — o non puo' — sposta con le frecce la pastiglia che ha
    il fuoco, e il fuoco la segue."""
    _run("""
      strip.openSort();
      const chat = pills().find((p) => p.dataset.id === 'chat');
      fire(chat, 'keydown', { key: 'ArrowLeft', preventDefault() {} });
      assert.deepEqual(pills().map((p) => p.dataset.id).slice(0, 2), ['chat', 'app']);
      assert.equal(globalThis.focus.dataset.id, 'chat', 'il fuoco non ha seguito la pastiglia');
      await strip.closeSort({ save: true });
      assert.deepEqual(requested.at(-1)[2].slice(0, 2), ['chat', 'app']);
    """)


def test_dragging_a_page_past_a_neighbour_swaps_them() -> None:
    """Il dito porta la pastiglia oltre la meta' della vicina: la bozza cambia,
    e al rilascio la pastiglia torna a posto senza `transform`.

    Il dito si segue **sul documento**: spostata nel DOM, Chromium toglie alla
    pastiglia la cattura del puntatore, e il rilascio arriva a chi sta sotto il
    dito — misurato sul telefono il 23/09/2026, la pastiglia restava sollevata.
    Qui il rilascio arriva al documento e basta, come la'."""
    _run("""
      strip.openSort();
      pills().forEach((p, i) => { p.offsetLeft = i * 70; });
      const app = pills()[0];
      fire(app, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
      assert.ok(app.classList.contains('is-lifted'));
      onDocument('pointermove', { pointerId: 1, clientX: 110, clientY: 20 });
      onDocument('pointerup', { pointerId: 1 });
      assert.equal(app.style.transform, '');
      assert.ok(!app.classList.contains('is-lifted'));
      assert.equal((docListeners.pointermove || []).length, 0, 'il documento ascolta ancora il dito');
      await strip.closeSort({ save: true });
      assert.deepEqual(requested.at(-1)[2].slice(0, 2), ['chat', 'app']);
    """)


# ── Il foglio di stile ──────────────────────────────────────────────────────


def test_names_cannot_be_selected_or_the_long_press_dies() -> None:
    """Visto sul telefono il 23/09/2026 sulle righe dei quaderni: senza, a meta'
    della pressione lunga Chromium seleziona la parola e annulla il puntatore."""
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    rule = css.split("\n.home-strip {", 1)[1].split("}", 1)[0]
    for declaration in ("user-select: none", "-webkit-user-select: none", "-webkit-touch-callout: none"):
        assert declaration in rule, declaration


def test_a_dragged_page_does_not_scroll_the_page() -> None:
    """Senza, Chromium si prende il movimento come uno scorrimento e manda
    `pointercancel` a meta' trascinamento."""
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    rule = css.split("\n.home-sort-pill {", 1)[1].split("}", 1)[0]
    assert "touch-action: none" in rule


def test_the_lifted_page_sits_under_the_finger() -> None:
    """La base si legge dal rettangolo vero, **senza** il `transform` di prima:
    con `offsetTop`, misurato dal guscio, la pastiglia finiva un'intestazione
    piu' in alto del dito (telefono, 23/09/2026)."""
    _run("""
      strip.openSort();
      const app = pills()[0];
      app.getBoundingClientRect = () => app.style.transform
        ? { left: 999, top: 999, width: 60, height: 40 }   // col transform: falso
        : { left: 0, top: 200, width: 60, height: 40 };    // senza: il posto vero
      fire(app, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 210 });
      onDocument('pointermove', { pointerId: 1, clientX: 15, clientY: 212 });
      assert.equal(app.style.transform, 'translate(5.0px, 2.0px)');
    """)


def test_the_row_is_shipped() -> None:
    manifest = (ROOT / "jenny" / "utils" / "android_assets.py").read_text(encoding="utf-8")
    assert '"assets/home-strip.js"' in manifest


def test_closing_the_moving_mode_mid_drag_lets_go_of_the_document() -> None:
    """Indietro col dito ancora sulla pastiglia: la chiusura azzerava il
    trascinamento ma lasciava i suoi ascoltatori sul documento, per sempre."""
    _run("""
      strip.openSort();
      const app = pills()[0];
      fire(app, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
      assert.equal(docListeners.pointermove.length, 1);
      await strip.closeSort();
      for (const type of ['pointermove', 'pointerup', 'pointercancel']) {
        assert.equal((docListeners[type] || []).length, 0, type + ' ancora ascoltato dopo la chiusura');
      }
    """)


def test_a_second_finger_does_not_start_a_second_drag() -> None:
    """Il secondo `pointerdown` sovrascriveva gli ascoltatori del primo, che
    nessuno poteva piu' togliere."""
    _run("""
      strip.openSort();
      const [a, b] = pills();
      fire(a, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
      fire(b, 'pointerdown', { button: 0, pointerId: 2, clientX: 80, clientY: 20 });
      assert.equal(docListeners.pointermove.length, 1, 'due trascinamenti insieme');
      assert.ok(!b.classList.contains('is-lifted'));
      onDocument('pointerup', { pointerId: 1 });
      assert.equal((docListeners.pointermove || []).length, 0, 'il documento ascolta ancora un dito');
      assert.ok(!a.classList.contains('is-lifted'));
    """)
