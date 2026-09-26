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
