"""Ogni cassetto dell'officina riapre alla sua quota, non a quella del vicino.

Cervello, Mani e Memoria sono un solo ``SettingsController`` su un solo
contenitore. Con una posizione di lettura sola, entrare in Mani dopo aver letto
Cervello fino in fondo apriva Mani a meta' pagina: visto sul Titan 2, in tutti e
tre i cassetti, il 26/09/2026.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, member, requires_node, run_js

_SOURCE = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")


def _accessors() -> str:
    found = re.findall(r"(?m)^  ((?:get|set) _scrollTop\(.*)$", _SOURCE)
    assert len(found) == 2, found
    return "\n".join("  " + f for f in found)


@requires_node
def test_each_drawer_keeps_its_own_reading_position() -> None:
    run_js(f"""
import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = (fn) => fn();

class Fake {{
  constructor() {{
    this._scrollTops = {{}};
    this._drawer = null;
    this._restorePending = false;
    this._restoringScroll = false;
    this.data = {{}};
    this.contentEl = {{ scrollTop: 0, clientHeight: 500 }};
  }}
  // Quel che fa il listener di `scroll` del costruttore.
  userScrolls(y) {{
    this.contentEl.scrollTop = y;
    this._restorePending = false;
    this._scrollTop = y;
  }}
  // La coda di `render()`: il contenitore riscritto tiene la quota di prima.
  render() {{
    this._restorePending = true;
    this._restoreScrollTop();
  }}
{_accessors()}
{member(_SOURCE, "setDrawer")}
{member(_SOURCE, "_restoreScrollTop")}
}}

const s = new Fake();
s.setDrawer('brain');
s.userScrolls(900);

s.setDrawer('hands');
assert.equal(s.contentEl.scrollTop, 0, 'Mani mai scorso si apre in cima');
s.userScrolls(120);

s.setDrawer('brain');
assert.equal(s.contentEl.scrollTop, 900, 'Cervello ritrova la sua quota');

s.setDrawer('hands');
assert.equal(s.contentEl.scrollTop, 120, 'e Mani la sua');
""")


def _scroll_listener() -> str:
    m = re.search(
        r"addEventListener\('scroll', \(\) => \{(.*?)\n    \}, \{ passive: true \}\);", _SOURCE, re.S
    )
    assert m, "listener di scroll non trovato"
    return m.group(1)


def test_only_a_gesture_cancels_a_pending_restore() -> None:
    assert "for (const type of ['touchstart', 'wheel', 'pointerdown', 'keydown'])" in _SOURCE


@requires_node
def test_a_clamp_while_blocks_land_does_not_lose_the_position() -> None:
    """In fondo a Mani la lista dei job atterra per ultima: finché non c'è, la
    pagina è corta e Blink clampa. L'evento di quel clamp arrivava a flag
    abbassato, passava per «ha scorso l'utente» e si portava via la quota."""
    run_js(f"""
import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = (fn) => fn();

class Fake {{
  constructor() {{
    this._scrollTops = {{}};
    this._drawer = null;
    this._restorePending = false;
    this._restoringScroll = false;
    this.data = {{}};
    let top = 0;
    this.max = 5000;
    const self = this;
    this.contentEl = {{
      clientHeight: 500,
      get scrollTop() {{ return top; }},
      set scrollTop(v) {{ top = Math.min(v, self.max); }},
    }};
  }}
  onScroll() {{
{_scroll_listener()}
  }}
  gesture() {{ this._restorePending = false; }}
  render() {{
    this.max = 400;  // i blocchi in ritardo sono ancora segnaposto
    this._restorePending = true;
    this._restoreScrollTop();
  }}
{_accessors()}
{member(_SOURCE, "setDrawer")}
{member(_SOURCE, "_restoreScrollTop")}
}}

const s = new Fake();
s.setDrawer('hands');
s.max = 5000;  // Mani caricata per intero, e letta fino in fondo
s.gesture();
s.contentEl.scrollTop = 2400;
s.onScroll();

s.setDrawer('brain');
s.setDrawer('hands');
assert.equal(s.contentEl.scrollTop, 400, 'la pagina corta clampa');
s.onScroll();  // l'evento del clamp, arrivato a flag gia' abbassato

s.max = 5000;  // la lista dei job atterra, e il suo caricatore riapplica
s._restoreScrollTop();
assert.equal(s.contentEl.scrollTop, 2400, 'Mani torna in fondo');
""")
