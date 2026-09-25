"""Jenny sta sempre a destra, e dopo un lancio ci torna.

Fino al 24/09/2026 il lato era il ricordo di dove l'avevi lasciata: caduta nella
metà sinistra, si riagganciava a sinistra, e lì testo, fumetti e riga di lavoro le
si allineavano male (v. ``.agent/tre-ritocchi-plan.md``, voce 1). Adesso il bordo
è uno solo. Qui si tiene fermo:

- che il lato non esista più come stato: niente getter, niente chiave in
  ``localStorage``, niente regole ``.side-left``, niente stringhe;
- che il volo, lasciata dovunque, **arrivi a piedi** al dock destro invece di
  teletrasportarsi alla scadenza — la camminata adesso può essere lo schermo
  intero, e la scadenza fissa di 6 s non la copre su uno schermo largo;
- che il gesto apri/chiudi abbia un verso solo: verso l'interno apre.

La fisica si importa **davvero** in node, con un finto elemento e un orologio
manuale al posto di ``requestAnimationFrame``: è l'unico modo di vedere dove
finisce lei, che non ha stato leggibile da fuori se non il ``transform``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
UI_ASSETS = UI / "assets"
MASCOT_JS = UI_ASSETS / "shared" / "mascot.js"
DRAG_JS = UI_ASSETS / "shared" / "mascot-drag.js"
ANDROID = (
    Path(__file__).resolve().parents[2]
    / "android" / "app" / "src" / "main" / "java" / "com" / "flagdizero" / "jenny"
)

node = requires_node


def _mascot_js() -> str:
    return MASCOT_JS.read_text("utf-8")


def test_the_side_is_no_longer_a_state() -> None:
    source = _mascot_js()
    assert "export function mascotSide" not in source
    assert "export function setMascotSide" not in source


def test_no_left_side_rules_or_hooks_remain() -> None:
    offenders = []
    for path in sorted(UI.rglob("*")):
        if path.suffix not in {".js", ".css", ".html", ".json"} or "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "mascot.js":
            # L'elenco delle chiavi ritirate le nomina per cancellarle: e'
            # l'opposto di un gancio, e il suo commento dice da dove vengono.
            text = re.sub(r"/\* Chiavi di preferenze ritirate.*?\];", "", text, flags=re.S)
        for needle in (
            "side-left", "onSideChange", "mascotSide", "_setSide", "mascotte-side",
            "mascotte-dock-side", "data-mascot-side",
        ):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert not offenders, offenders


def test_the_floating_mascot_has_one_edge_too() -> None:
    """La flottante è la stessa Jenny: nessun bordo sinistro, nessun lato letto."""
    flight = (ANDROID / "FloatingFlight.kt").read_text("utf-8")
    overlay = (ANDROID / "FloatingOverlayController.kt").read_text("utf-8")
    assert "dockPivotX: Float" in flight
    assert "chooseSide" not in flight
    assert "parkedRight" not in overlay
    assert "park_right" not in overlay


# ── Il volo, davvero ────────────────────────────────────────────────────────

# Un elemento finto al dock destro, un orologio manuale per rAF e timer, e un
# `fly()` che la prende, la trascina lungo `path` e la lascia andare. Ritorna
# l'ultimo `transform` disegnato prima della fine del volo, quanto tempo è
# passato, e le chiamate a `setOut`.
_HARNESS = r"""
import assert from 'node:assert/strict';
globalThis.window = globalThis;
globalThis.localStorage = {
  _s: new Map(), getItem(k) { return this._s.get(k) ?? null; },
  setItem(k, v) { this._s.set(k, String(v)); }, removeItem(k) { this._s.delete(k); },
};
globalThis.document = {
  documentElement: { style: { setProperty() {} } },
  addEventListener() {}, hidden: false,
};
let clock = 0;
let frames = [];
globalThis.performance = { now: () => clock };
globalThis.requestAnimationFrame = (cb) => { frames.push(cb); return frames.length; };
globalThis.cancelAnimationFrame = () => {};
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};

const { bindMascotDrag } = await import(DRAG_URL);

const classes = () => {
  const s = new Set();
  return { add: (...c) => c.forEach((x) => s.add(x)), remove: (...c) => c.forEach((x) => s.delete(x)),
           toggle: (c, on) => (on ?? !s.has(c)) ? s.add(c) : s.delete(c), contains: (c) => s.has(c) };
};

function fly(vw, path, { out = false } = {}) {
  globalThis.innerWidth = vw;
  globalThis.innerHeight = 640;
  const size = 120;
  const left = vw - size + size * 0.469;  // ancoraggio docked, come nel CSS
  const handlers = {};
  const el = {
    classList: classes(),
    addEventListener: (t, fn) => { handlers[t] = fn; },
    getBoundingClientRect: () => ({ left, top: 400, width: size, height: size }),
    setPointerCapture() {}, blur() {},
  };
  const pose = () => ({ classList: classes() });
  // Ogni transform scritto si registra: quello che conta è l'ultimo non vuoto,
  // cioè l'ultimo passo disegnato prima che endFlight() lo azzeri.
  let last = '';
  const style = {};
  Object.defineProperty(style, 'transform', {
    get: () => '', set: (v) => { if (v) last = v; },
  });
  const fly = { style };
  const setOutCalls = [];
  let ended = false;
  bindMascotDrag({
    el, fly,
    flyPose: { hang: pose(), fall: pose(), ground: pose(), walk1: pose(), walk2: pose() },
    isOut: () => out,
    setOut: (v) => setOutCalls.push(v),
    onFlightEnd: () => { ended = true; },
  });
  const pump = (n) => {
    for (let i = 0; i < n && frames.length; i++) {
      clock += 16;
      const due = frames; frames = [];
      due.forEach((cb) => cb(clock));
    }
  };
  const ev = (x, y) => ({ clientX: x, clientY: y, pointerId: 1, preventDefault() {} });
  const [x0, y0] = path[0];
  handlers.pointerdown(ev(x0, y0));
  for (const [x, y] of path.slice(1)) { handlers.pointermove(ev(x, y)); pump(20); }
  const [xe, ye] = path[path.length - 1];
  const releasedAt = clock;
  handlers.pointerup(ev(xe, ye));
  for (let i = 0; i < 2000 && !ended; i++) pump(1);
  assert.ok(ended, 'il volo non è mai finito');
  const [tx] = (last.match(/translate\(([-\d.]+)px/) || [, 'NaN']).slice(1).map(Number);
  return { tx, ms: clock - releasedAt, setOutCalls };
}
"""


def _run_flight(source: str) -> None:
    script = _HARNESS.replace("DRAG_URL", json.dumps(DRAG_JS.as_uri())) + source
    run_js(script, timeout=30)


@node
def test_dropped_at_the_left_edge_she_walks_all_the_way_back() -> None:
    """Uno schermo largo: la camminata supera i 6 s, e lei deve arrivare lo stesso."""
    _run_flight("""
const r = fly(1000, [[1000 - 30, 450], [600, 420], [200, 380], [20, 380]]);
// Arrivata vuol dire: l'ultimo passo disegnato è sul dock (entro i 5 px della
// condizione d'arrivo), non a metà schermo dove la lascerebbe uno snap.
assert.ok(Math.abs(r.tx) < 6, `si è fermata a ${r.tx}px dal dock`);
assert.ok(r.ms > 6000, `il volo è durato ${r.ms} ms: troppo poco per aver camminato`);
// Attraversare lo schermo non la apre.
assert.deepEqual(r.setOutCalls, []);
""")


@node
def test_on_the_phone_too_she_lands_on_the_right() -> None:
    """Il Titan 2 (575 px CSS): lasciata a sinistra, torna al dock destro."""
    _run_flight("""
const r = fly(575, [[575 - 30, 450], [300, 400], [40, 380]]);
assert.ok(Math.abs(r.tx) < 6, `si è fermata a ${r.tx}px dal dock`);
assert.deepEqual(r.setOutCalls, []);
""")


@node
def test_pulling_inward_a_little_still_opens_her() -> None:
    """Il verso del gesto è uno: tirarla un po' verso l'interno la apre."""
    _run_flight("""
const r = fly(575, [[575 - 30, 450], [575 - 90, 450], [575 - 110, 450]]);
assert.deepEqual(r.setOutCalls, [true]);
""")
