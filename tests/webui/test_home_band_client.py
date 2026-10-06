"""La fascia in fondo al filo e la sagoma di Jenny, eseguite sotto node.

`home-band.js` ha preso il posto di `shared/jenny-gap.js` il 06/10/2026.
Quello dava un margine ai messaggi che le finivano dietro **a scorrimento
fermo**, e il testo tornava a capo sotto il dito ogni volta che ti fermavi.
Adesso lo spazio per lei sta tutto in fondo: una fascia alta meta' di lei, in
cui scende la coda dell'ultima risposta, e sopra una sagoma attorno alla sua
testa che sta nel testo e scorre con lui.

Qui si tiene fermo:

- il conto della sagoma (`shapeOf`), puro;
- che la sagoma vada **solo** nell'ultima risposta, e solo se e' subito sopra
  la fascia;
- che niente si ricalcoli piu' scorrendo, e che il vecchio modulo sia sparito
  davvero, classi e variabili comprese.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
BAND_JS = ASSETS / "home-band.js"
MASCOT_JS = ASSETS / "shared" / "mascot.js"
HOME_CHAT_JS = ASSETS / "home-chat.js"
HOME_APP_JS = ASSETS / "home-app.js"
HOME_CSS = ASSETS / "home-style.css"


pytestmark = requires_node


def _run_js(script: str) -> str:
    """`home-band.js` importa da `mascot.js`, che al caricamento tocca il DOM e
    `localStorage`: i due rapporti si mettono a mano, e sono gli stessi che il
    primo banco qui sotto confronta con `mascot.js`."""
    source = (
        BAND_JS.read_text(encoding="utf-8").replace(
            "import { ART_HEIGHT_RATIO, OUT_RATIO } from './shared/mascot.js';",
            "const ART_HEIGHT_RATIO = 0.73;\nconst OUT_RATIO = 0.25;",
        )
        + "\nimport assert from 'node:assert/strict';\n"
        + _FAKE_DOM
        + script
    )
    return run_js(source)


# Un DOM finto quanto basta: figli, classi, fratelli, e un blocco che si allunga
# quando la sagoma gli fa andare a capo una riga.
_FAKE_DOM = r"""
class El {
  constructor(cls = '') {
    this.className = cls; this.children = []; this.parentElement = null;
    this.hidden = false; this.style = {}; this.offsetWidth = 0; this._h = 0;
  }
  get classList() {
    const el = this;
    const list = () => el.className.split(/\s+/).filter(Boolean);
    return {
      contains: (c) => list().includes(c),
      add: (c) => { if (!list().includes(c)) el.className = [...list(), c].join(' '); },
      remove: (c) => { el.className = list().filter((x) => x !== c).join(' '); },
    };
  }
  append(...kids) { for (const k of kids) { k.parentElement = this; this.children.push(k); } }
  prepend(...kids) { for (const k of kids) k.parentElement = this; this.children.unshift(...kids); }
  remove() {
    const p = this.parentElement;
    if (p) p.children = p.children.filter((c) => c !== this);
    this.parentElement = null;
  }
  get previousElementSibling() {
    const sib = this.parentElement?.children || [];
    const i = sib.indexOf(this);
    return i > 0 ? sib[i - 1] : null;
  }
  querySelectorAll(sel) {
    const names = [...sel.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
    return this.children.filter((c) => names.some((n) => c.classList.contains(n)));
  }
  get offsetHeight() {
    if (this.className.includes('home-block')) {
      /* Il testo e' alto `_h`; con la sagoma accanto alle ultime righe ne va a
         capo una in piu' (20 px), e coi float dentro il blocco e' alto almeno
         quanto loro. */
      const push = this.children.find((c) => c.className === 'home-shape-push');
      const shape = this.children.find((c) => c.className === 'home-shape');
      if (!shape) return this._h;
      const floats = parseFloat(push.style.height) + parseFloat(shape.style.height);
      return Math.max(this._h + 20, floats);
    }
    return this._h;
  }
  getBoundingClientRect() { return { bottom: this._bottom ?? 0 }; }
}
globalThis.document = { createElement: () => new El() };
globalThis.getComputedStyle = () => ({ paddingRight: '18px' });

/* Il filo: [risposta, posto vivo, fascia]. La fascia finisce 44 px sotto il
   fondo del blocco (e' alta 44: niente coda in mezzo, la coda e' nella fascia). */
function thread({ last = 'jenny', liveOn = false, text = 120 } = {}) {
  const t = new El('home-thread');
  const msg = new El(last === 'jenny' ? 'home-msg home-msg-jenny' : 'home-msg home-msg-user');
  const block = new El('home-block');
  block._h = text;
  block._bottom = 456;
  const tail = new El('home-tail');
  msg.append(block, tail);
  const live = new El('home-live');
  live.hidden = !liveOn;
  const band = new El('home-band');
  band._bottom = 500;
  t.append(msg, live, band);
  const mascot = new El('jenny-duo');
  mascot.offsetWidth = 120;
  return { t, msg, block, live, band, mascot };
}
const shapeIn = (block) => block.children.find((c) => c.className === 'home-shape');
"""


def test_the_ratios_still_say_what_this_module_assumes() -> None:
    """I due rapporti vengono da `mascot.js`: se l'arte cambia, la sagoma e il
    `--jenny-dodge` del CSS vanno rivisti, e questo banco lo dice."""
    mascot = MASCOT_JS.read_text(encoding="utf-8")
    assert "export const ART_HEIGHT_RATIO = 0.73;" in mascot
    assert "export const OUT_RATIO = 0.25;" in mascot
    assert "il 45% centrale del canvas quadrato" in mascot
    band = BAND_JS.read_text(encoding="utf-8")
    assert "export const SIDE_MARGIN = (1 - 0.45) / 2;" in band
    assert "export const FIGURE_FROM_EDGE = 1 - OUT_RATIO - SIDE_MARGIN;" in band


def test_the_shape_is_her_figure_not_her_square() -> None:
    """A taglia 120, fuori, la figura comincia 57 px dal bordo: col padding del
    filo (18) e 6 di respiro la sagoma e' larga 45. In altezza tocca il blocco
    solo sopra la fascia: 87,6 di lei, meno i 44 fra il blocco e il pavimento,
    piu' il respiro."""
    out = _run_js("""
assert.ok(Math.abs(FIGURE_FROM_EDGE - 0.475) < 1e-9);
const s = shapeOf({ size: 120, padRight: 18, distance: 44, blockHeight: 200 });
assert.deepEqual(s, { width: 45, height: 50 });
console.log('ok');
""")
    assert out.strip() == "ok"


def test_a_short_reply_is_wrapped_whole_and_not_stretched() -> None:
    out = _run_js("""
assert.deepEqual(shapeOf({ size: 120, padRight: 18, distance: 44, blockHeight: 21 }),
                 { width: 45, height: 21 });
// Sopra la sua testa, o senza testo, niente sagoma.
assert.equal(shapeOf({ size: 120, padRight: 18, distance: 200, blockHeight: 80 }), null);
assert.equal(shapeOf({ size: 120, padRight: 18, distance: 44, blockHeight: 0 }), null);
console.log('ok');
""")
    assert out.strip() == "ok"


def test_the_last_reply_gets_the_shape_at_its_bottom() -> None:
    """Il primo float spinge il secondo in fondo: alla fine la sagoma finisce
    esattamente col blocco, anche dopo la riga in piu' che fa andare a capo."""
    out = _run_js("""
const { block, band, mascot } = thread({ text: 120 });
new HomeBand(band, mascot).refresh();
assert.ok(block.classList.contains('has-jenny-shape'));
const shape = shapeIn(block);
assert.equal(shape.style.width, '45px');
assert.equal(shape.style.height, '50px');
const push = block.children[0];
assert.equal(push.className, 'home-shape-push');
assert.equal(parseFloat(push.style.height) + 50, block.offsetHeight,
             'la sagoma non finisce col blocco');
console.log('ok');
""")
    assert out.strip() == "ok"


def test_no_shape_when_someone_is_working_or_you_spoke_last() -> None:
    """Il posto vivo acceso separa la risposta dalla fascia, e una bolla tua
    e' una scatola: la scansa il CSS, non la sagoma."""
    out = _run_js("""
for (const opts of [{ liveOn: true }, { last: 'user' }]) {
  const { block, band, mascot } = thread(opts);
  new HomeBand(band, mascot).refresh();
  assert.equal(shapeIn(block), undefined, JSON.stringify(opts));
  assert.ok(!block.classList.contains('has-jenny-shape'));
}
console.log('ok');
""")
    assert out.strip() == "ok"


def test_the_shape_leaves_the_reply_that_is_no_longer_last() -> None:
    out = _run_js("""
const { t, msg, block, band, mascot } = thread();
const homeBand = new HomeBand(band, mascot);
homeBand.refresh();
assert.ok(shapeIn(block));
// Arriva una bolla tua sotto la risposta.
const mine = new El('home-msg home-msg-user');
mine.parentElement = t;
t.children.splice(t.children.indexOf(msg) + 1, 0, mine);
homeBand.refresh();
assert.equal(shapeIn(block), undefined);
assert.ok(!block.classList.contains('has-jenny-shape'));
assert.equal(block.children.length, 0);
console.log('ok');
""")
    assert out.strip() == "ok"


def test_scrolling_recomputes_nothing() -> None:
    """**La ragione del cambio.** Lo scorrimento della chat non tocca piu' lo
    spazio di Jenny, e il guscio non chiede piu' misure a pista ferma."""
    chat = HOME_CHAT_JS.read_text(encoding="utf-8")
    on_scroll = member(chat, "_onScroll")
    code = re.sub(r"/\*.*?\*/", "", on_scroll, flags=re.S)
    assert "this.band" not in code and "refresh" not in code and "gap" not in code
    app = HOME_APP_JS.read_text(encoding="utf-8")
    assert "settleAfter" not in app
    band = BAND_JS.read_text(encoding="utf-8")
    assert "'scroll'" not in band and '"scroll"' not in band


def test_the_old_dodge_is_gone_everywhere() -> None:
    assert not (ASSETS / "shared" / "jenny-gap.js").exists()
    for path in ASSETS.rglob("*"):
        if path.suffix not in {".js", ".css", ".html"} or path == BAND_JS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for word in ("is-under-jenny", "--jenny-gap", "JennyGap", "jenny-gap.js"):
            assert word not in text, f"{path.name} parla ancora di {word}"


def test_the_band_and_the_tail_are_css() -> None:
    """La fascia e' alta meta' di lei, e la coda dell'ultima risposta ci scende
    con una regola sola, senza JS. Il `--jenny-dodge` delle scatole e' lo
    stesso conto della larghezza di `shapeOf`."""
    css = HOME_CSS.read_text(encoding="utf-8")
    assert "--home-band-h: calc(var(--jenny-size) * var(--jenny-art-h) / 2);" in css
    assert re.search(r"\.home-band \{[^}]*height: var\(--home-band-h\);", css)
    last = r"\.home-msg-jenny:has\(\+ \.home-live\[hidden\] \+ \.home-band\)"
    assert re.search(last + r" > \.home-tail \{[^}]*position: absolute;", css)
    assert ("--jenny-dodge: calc(var(--jenny-size) * (1 - var(--jenny-out) - 0.275) - 18px + 6px);"
            in css)
    chat = HOME_CHAT_JS.read_text(encoding="utf-8")
    ctor = member(chat, "constructor")
    assert ctor.index("this.live") < ctor.index("this.bandEl"), "la fascia va dopo il posto vivo"
