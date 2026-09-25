"""La fila dei nomi in alto, e la modalita' ordina che si apre tenendone premuto uno.

Prende il posto di titolo, ingranaggio, bottone del cassetto e pallini (v.
`.agent/pagine-in-alto-plan.md`): quello dove sei e' grande, un tocco su un nome
ci va, e tenendo premuto le pagine si spostano. Qui si prova **cosa dice** e
**cosa chiede alla pista**; la pista vera ha il suo banco
(`test_casa_pista_client.py`), e il dito vero si prova sul telefono.

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


_VICINI = {
    "i18n.js": """
export const i18n = {
  t: (k, v) => k + (v ? ':' + JSON.stringify(v) : ''),
};
""",
    "api-client.js": "export const api = { async listProjects() { return { projects: [] }; } };\n",
    # La pressione lunga finta: quella vera si prova dove vive. Qui conta **chi**
    # la arma, e che posi lo stesso segno di quella vera.
    "longpress.js": """
export const premute = [];
export function setupLongPress(el, cb) { premute.push({ el, cb }); }
""",
}

_FINTO_DOM = """
function creaEl(tag) {
  const el = {
    tag, className: '', children: [], dataset: {}, style: {}, attrs: {},
    ascolto: {}, textContent: '', tabIndex: -1,
    offsetLeft: 0, offsetWidth: 60, offsetTop: 0, scrollLeft: 0, scrollWidth: 300, clientWidth: 300,
    set innerHTML(v) { this._html = v; },
    get classList() {
      const e = this;
      const parole = () => (e.className || '').split(' ').filter(Boolean);
      return {
        add(c) { if (!parole().includes(c)) e.className = [...parole(), c].join(' '); },
        remove(c) { e.className = parole().filter((x) => x !== c).join(' '); },
        toggle(c, on) { if (on) this.add(c); else this.remove(c); },
        contains(c) { return parole().includes(c); },
      };
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, fn) { (this.ascolto[t] = this.ascolto[t] || []).push(fn); },
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    append(...cs) { for (const c of cs) this.appendChild(c); },
    replaceChildren(...cs) { this.children = []; this.append(...cs); },
    insertBefore(c, rif) {
      this.children = this.children.filter((x) => x !== c);
      const i = rif ? this.children.indexOf(rif) : -1;
      if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
      c.parent = this;
      return c;
    },
    getBoundingClientRect() { return { left: this.offsetLeft, top: 0, width: 60, height: 40 }; },
    querySelector(sel) {
      const m = sel.match(/data-id="([^"]+)"/);
      const cerca = (n) => {
        for (const c of n.children) {
          if (m && c.dataset.id === m[1]) return c;
          const r = cerca(c);
          if (r) return r;
        }
        return null;
      };
      return cerca(this);
    },
    focus() { globalThis.fuoco = this; },
    setPointerCapture() {},
  };
  return el;
}
const docAscolto = {};
globalThis.document = {
  createElement: creaEl,
  addEventListener(t, fn) { (docAscolto[t] = docAscolto[t] || []).push(fn); },
  removeEventListener(t, fn) { docAscolto[t] = (docAscolto[t] || []).filter((x) => x !== fn); },
};
function sulDocumento(tipo, e) { for (const fn of [...(docAscolto[tipo] || [])]) fn(e); }
globalThis.CSS = { escape: (s) => s };
function lancia(el, tipo, e = {}) { for (const fn of el.ascolto[tipo] || []) fn(e); }
function tutti(el, out = []) { for (const c of el.children) { out.push(c); tutti(c, out); } return out; }
"""


def _run(corpo: str) -> None:
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FINTO_DOM
        + textwrap.dedent(
            """
            const { CasaFila } = await import('./casa-fila.js');
            const { premute } = await import('./shared/longpress.js');
            const { dotColor } = await import('./casa-who.js');
            /* La pista finta: le voci come le da' quella vera, e cosa le si chiede. */
            const chieste = [];
            const pagine = {
              fixed: ['app', 'chat', 'notebooks', 'settings'],
              pages: [{ id: 'p1', kind: 'app', ref: 'todo' },
                          { id: 'q1', kind: 'conversation', ref: 'project:piante' }],
              order: ['app', 'chat', 'p1', 'q1', 'notebooks', 'settings'],
              indice: 1,
              get voci() {
                return this.order.map((id) => this.fixed.includes(id)
                  ? { id, kind: id === 'app' ? 'cassetto' : id, fissa: true }
                  : { ...this.pages.find((s) => s.id === id), fissa: false });
              },
              nomeDi: (s) => (s.kind === 'conversation' ? s.ref.split(':')[1] : s.ref),
              vaiA(i) { chieste.push(['vaiA', i]); this.indice = i; },
              /* Come la vera: l'elenco salvato, o `false` se il server ha
                 rifiutato (l'avviso lo da' lei). `inAttesa` tiene la
                 scrittura sospesa finche' il caso non la lascia andare. */
              rifiuta: false,
              inAttesa: null,
              async salva(s, o) {
                chieste.push(['salva', s.map((x) => x.id), o]);
                if (this.inAttesa) await this.inAttesa;
                return this.rifiuta ? false : { pages: s, order: o };
              },
            };
            let nomeChat = { nome: 'Jenny', colore: null };
            const cambi = [];
            const el = creaEl('div');
            const fila = new CasaFila(el, {
              pagine,
              nomeChat: () => nomeChat,
              onCambia: (aperta) => cambi.push(aperta),
            });
            fila.disegna();
            const voci = () => el.children[0].children;
            const nomi = () => voci().map((b) => tutti(b).find((c) => c.className === 'casa-fila-nome').textContent);
            const pastiglie = () => el.children[1].children;
            """
        )
        + textwrap.dedent(corpo)
    )
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "casa-fila.js", radice / "casa-fila.js")
        shutil.copy(ASSETS / "casa-who.js", radice / "casa-who.js")
        shutil.copy(ASSETS / "shared" / "conversation-list.js", radice / "shared" / "conversation-list.js")
        for nome, testo in _VICINI.items():
            (radice / "shared" / nome).write_text(testo, encoding="utf-8")
        entry = radice / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


# ── La fila ─────────────────────────────────────────────────────────────────


def test_every_page_has_its_name_in_order() -> None:
    """Le fisse col nome delle traduzioni, la chat col nome della conversazione,
    le aggiunte col loro."""
    _run("""
      assert.deepEqual(nomi(), [
        'casa.fila.app', 'Jenny', 'todo', 'piante', 'casa.fila.notebooks', 'casa.fila.settings',
      ]);
    """)


def test_the_page_you_are_on_is_the_big_one_and_says_so() -> None:
    """Grande per chi guarda, `aria-selected` per chi ascolta."""
    _run("""
      const accese = voci().filter((b) => b.classList.contains('is-on'));
      assert.equal(accese.length, 1);
      assert.equal(accese[0].dataset.id, 'chat');
      assert.equal(accese[0].attrs['aria-selected'], 'true');
      assert.equal(voci()[0].attrs['aria-selected'], 'false');
      pagine.indice = 4;
      fila.disegna();
      assert.equal(voci().find((b) => b.classList.contains('is-on')).dataset.id, 'notebooks');
    """)


def test_inside_a_notebook_the_chat_page_wears_its_name_and_dot() -> None:
    """Deciso con l'utente il 23/09/2026: il nome del quaderno al posto di
    «Jenny», col pallino dei Quaderni."""
    _run("""
      nomeChat = { nome: 'ristrutturazione', colore: dotColor('ristrutturazione') };
      fila.disegna();
      const chat = voci()[1];
      assert.equal(nomi()[1], 'ristrutturazione');
      const dot = chat.children.find((c) => c.className === 'casa-fila-dot');
      assert.ok(dot, 'la chat dentro un quaderno non ha il pallino');
      assert.equal(dot.style.background, dotColor('ristrutturazione'));
      const personale = voci()[0].children.find((c) => c.className === 'casa-fila-dot');
      assert.equal(personale, undefined, 'il cassetto ha un pallino');
    """)


def test_a_notebook_page_has_the_dot_of_its_notebook() -> None:
    _run("""
      const q = voci()[3];
      const dot = q.children.find((c) => c.className === 'casa-fila-dot');
      assert.equal(dot.style.background, dotColor('piante'));
    """)


def test_a_tap_on_a_name_goes_there() -> None:
    _run("""
      lancia(voci()[4], 'click');
      assert.deepEqual(chieste, [['vaiA', 4]]);
    """)


def test_the_tap_that_follows_a_long_press_goes_nowhere() -> None:
    """Tenere premuto apre la modalita' ordina, e il click che segue non deve
    anche portarti su quella pagina."""
    _run("""
      const b = voci()[2];
      const p = premute.find((x) => x.el === b);
      assert.ok(p, 'un nome non si puo tenere premuto');
      b.dataset.longpress = 'true';
      p.cb();
      lancia(b, 'click');
      assert.deepEqual(chieste, [], 'il click dopo la pressione lunga ha cambiato pagina');
      assert.equal(fila.ordinando, true);
    """)


# ── La modalita' ordina ─────────────────────────────────────────────────────


def test_holding_a_name_opens_the_moving_mode() -> None:
    """Le pagine diventano pastiglie, nell'ordine di adesso, e il guscio lo sa
    (la pagina sotto si spegne, la tastiera si chiude)."""
    _run("""
      fila.apriOrdina();
      assert.equal(fila.ordinando, true);
      assert.deepEqual(cambi, [true]);
      assert.ok(el.classList.contains('is-ordina'));
      assert.deepEqual(pastiglie().map((p) => p.dataset.id), pagine.order);
    """)


def test_only_added_pages_have_the_cross() -> None:
    """Le quattro fisse si spostano ma non si tolgono: senza Impostazioni non ci
    sarebbe piu' una strada per tornarci."""
    _run("""
      fila.apriOrdina();
      const conCroce = pastiglie()
        .filter((p) => p.children.some((c) => c.className === 'casa-ordina-togli'))
        .map((p) => p.dataset.id);
      assert.deepEqual(conCroce, ['p1', 'q1']);
      fila.togli('settings');
      assert.equal(pastiglie().length, 6, 'una pagina fissa si e tolta');
    """)


def test_done_writes_the_new_order_once() -> None:
    """Spostare e togliere sono una scrittura sola, e parte a «Fatto»."""
    _run("""
      fila.apriOrdina();
      fila.sposta('p1', 0);
      fila.togli('q1');
      assert.deepEqual(chieste, [], 'ha scritto prima di Fatto');
      await fila.chiudiOrdina({ salva: true });
      assert.deepEqual(chieste, [
        ['salva', ['p1'], ['p1', 'app', 'chat', 'notebooks', 'settings']],
      ]);
      assert.equal(fila.ordinando, false);
      assert.deepEqual(cambi, [true, false]);
    """)


def test_a_refused_done_keeps_the_moving_mode_and_the_draft() -> None:
    """La bozza si azzerava **prima** di scrivere: un rifiuto del server
    perdeva l'ordine in silenzio, e la modalita' ordina era gia' chiusa.
    Ora si resta dentro, con la bozza com'era, e «Fatto» si ripreme."""
    _run("""
      fila.apriOrdina();
      fila.sposta('p1', 0);
      pagine.rifiuta = true;
      assert.equal(await fila.chiudiOrdina({ salva: true }), false);
      assert.equal(fila.ordinando, true, 'un salvataggio rifiutato ha chiuso la modalita\\u2019 ordina');
      assert.deepEqual(cambi, [true], 'il guscio crede che la modalita\\u2019 ordina sia chiusa');
      fila.disegna();
      assert.equal(pastiglie()[0].dataset.id, 'p1', 'la bozza si e\\u2019 persa');
      pagine.rifiuta = false;
      assert.equal(await fila.chiudiOrdina({ salva: true }), true);
      assert.equal(fila.ordinando, false);
      assert.equal(chieste.length, 2);
      assert.deepEqual(chieste[1], chieste[0], 'il secondo Fatto non ha riscritto la stessa bozza');
    """)


def test_a_second_done_while_the_first_is_writing_does_nothing() -> None:
    _run("""
      fila.apriOrdina();
      fila.sposta('p1', 0);
      let lascia;
      pagine.inAttesa = new Promise((r) => { lascia = r; });
      const primo = fila.chiudiOrdina({ salva: true });
      assert.equal(await fila.chiudiOrdina({ salva: true }), false);
      lascia();
      assert.equal(await primo, true);
      assert.equal(chieste.filter((c) => c[0] === 'salva').length, 1, 'due Fatto, due scritture');
      assert.equal(fila.ordinando, false);
    """)


def test_back_leaves_everything_as_it_was() -> None:
    _run("""
      fila.apriOrdina();
      fila.sposta('settings', 0);
      fila.togli('p1');
      await fila.chiudiOrdina();
      assert.deepEqual(chieste, []);
      assert.deepEqual(nomi()[0], 'casa.fila.app', 'la fila mostra un ordine mai salvato');
    """)


def test_done_without_changes_writes_nothing() -> None:
    _run("""
      fila.apriOrdina();
      await fila.chiudiOrdina({ salva: true });
      assert.deepEqual(chieste, []);
    """)


def test_the_arrows_move_a_page_too() -> None:
    """Chi non trascina — o non puo' — sposta con le frecce la pastiglia che ha
    il fuoco, e il fuoco la segue."""
    _run("""
      fila.apriOrdina();
      const chat = pastiglie().find((p) => p.dataset.id === 'chat');
      lancia(chat, 'keydown', { key: 'ArrowLeft', preventDefault() {} });
      assert.deepEqual(pastiglie().map((p) => p.dataset.id).slice(0, 2), ['chat', 'app']);
      assert.equal(globalThis.fuoco.dataset.id, 'chat', 'il fuoco non ha seguito la pastiglia');
      await fila.chiudiOrdina({ salva: true });
      assert.deepEqual(chieste.at(-1)[2].slice(0, 2), ['chat', 'app']);
    """)


def test_dragging_a_page_past_a_neighbour_swaps_them() -> None:
    """Il dito porta la pastiglia oltre la meta' della vicina: la bozza cambia,
    e al rilascio la pastiglia torna a posto senza `transform`.

    Il dito si segue **sul documento**: spostata nel DOM, Chromium toglie alla
    pastiglia la cattura del puntatore, e il rilascio arriva a chi sta sotto il
    dito — misurato sul telefono il 23/09/2026, la pastiglia restava sollevata.
    Qui il rilascio arriva al documento e basta, come la'."""
    _run("""
      fila.apriOrdina();
      pastiglie().forEach((p, i) => { p.offsetLeft = i * 70; });
      const app = pastiglie()[0];
      lancia(app, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
      assert.ok(app.classList.contains('is-sollevata'));
      sulDocumento('pointermove', { pointerId: 1, clientX: 110, clientY: 20 });
      sulDocumento('pointerup', { pointerId: 1 });
      assert.equal(app.style.transform, '');
      assert.ok(!app.classList.contains('is-sollevata'));
      assert.equal((docAscolto.pointermove || []).length, 0, 'il documento ascolta ancora il dito');
      await fila.chiudiOrdina({ salva: true });
      assert.deepEqual(chieste.at(-1)[2].slice(0, 2), ['chat', 'app']);
    """)


# ── Il foglio di stile ──────────────────────────────────────────────────────


def test_names_cannot_be_selected_or_the_long_press_dies() -> None:
    """Visto sul telefono il 23/09/2026 sulle righe dei quaderni: senza, a meta'
    della pressione lunga Chromium seleziona la parola e annulla il puntatore."""
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    regola = css.split("\n.casa-fila {", 1)[1].split("}", 1)[0]
    for dichiarazione in ("user-select: none", "-webkit-user-select: none", "-webkit-touch-callout: none"):
        assert dichiarazione in regola, dichiarazione


def test_a_dragged_page_does_not_scroll_the_page() -> None:
    """Senza, Chromium si prende il movimento come uno scorrimento e manda
    `pointercancel` a meta' trascinamento."""
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    regola = css.split("\n.casa-ordina-pastiglia {", 1)[1].split("}", 1)[0]
    assert "touch-action: none" in regola


def test_the_lifted_page_sits_under_the_finger() -> None:
    """La base si legge dal rettangolo vero, **senza** il `transform` di prima:
    con `offsetTop`, misurato dal guscio, la pastiglia finiva un'intestazione
    piu' in alto del dito (telefono, 23/09/2026)."""
    _run("""
      fila.apriOrdina();
      const app = pastiglie()[0];
      app.getBoundingClientRect = () => app.style.transform
        ? { left: 999, top: 999, width: 60, height: 40 }   // col transform: falso
        : { left: 0, top: 200, width: 60, height: 40 };    // senza: il posto vero
      lancia(app, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 210 });
      sulDocumento('pointermove', { pointerId: 1, clientX: 15, clientY: 212 });
      assert.equal(app.style.transform, 'translate(5.0px, 2.0px)');
    """)


def test_the_row_is_shipped() -> None:
    manifest = (ROOT / "jenny" / "utils" / "android_assets.py").read_text(encoding="utf-8")
    assert '"assets/casa-fila.js"' in manifest


def test_closing_the_moving_mode_mid_drag_lets_go_of_the_document() -> None:
    """Indietro col dito ancora sulla pastiglia: la chiusura azzerava il
    trascinamento ma lasciava i suoi ascoltatori sul documento, per sempre."""
    _run("""
      fila.apriOrdina();
      const app = pastiglie()[0];
      lancia(app, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
      assert.equal(docAscolto.pointermove.length, 1);
      await fila.chiudiOrdina();
      for (const tipo of ['pointermove', 'pointerup', 'pointercancel']) {
        assert.equal((docAscolto[tipo] || []).length, 0, tipo + ' ancora ascoltato dopo la chiusura');
      }
    """)


def test_a_second_finger_does_not_start_a_second_drag() -> None:
    """Il secondo `pointerdown` sovrascriveva gli ascoltatori del primo, che
    nessuno poteva piu' togliere."""
    _run("""
      fila.apriOrdina();
      const [a, b] = pastiglie();
      lancia(a, 'pointerdown', { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
      lancia(b, 'pointerdown', { button: 0, pointerId: 2, clientX: 80, clientY: 20 });
      assert.equal(docAscolto.pointermove.length, 1, 'due trascinamenti insieme');
      assert.ok(!b.classList.contains('is-sollevata'));
      sulDocumento('pointerup', { pointerId: 1 });
      assert.equal((docAscolto.pointermove || []).length, 0, 'il documento ascolta ancora un dito');
      assert.ok(!a.classList.contains('is-sollevata'));
    """)
