"""Lo schiaccia e allunga del volo (``shared/mascot-squash.js``), in node.

Le formule sono pure: si provano su una griglia di ingressi, senza DOM. Quello
che conta e' che non possano sfondare i tetti approvati sul mockup «Jenny in
movimento» (il primo li sfondava, e Jenny sembrava di gomma), che il volume
resti, e che ogni deformazione torni a 1 da se'.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
SQUASH_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "shared" / "mascot-squash.js"

pytestmark = requires_node

_HEAD = """
import assert from 'node:assert/strict';
const m = await import(URL);
const speeds = [0, 50, 200, 800, 1500, 2500, 4000, 5000, 12000, -3000];
const times = [];
for (let t = 0; t <= 1200; t += 5) times.push(t);
const volume = ({ sx, sy }, what) => {
  const v = sx * sy;
  assert.ok(Math.abs(v - 1) <= 0.04, `${what}: volume ${v.toFixed(3)}`);
};
const stretchOf = ({ sx, sy }) => Math.max(Math.abs(sx - 1), Math.abs(sy - 1));
"""


def _run(body: str) -> None:
    run_js(_HEAD.replace("URL", json.dumps(SQUASH_JS.as_uri())) + body)


def test_the_caps_hold_for_any_speed() -> None:
    _run("""
        for (const v of speeds) {
          const h = m.heldStretch(v), a = m.airStretch(v);
          assert.ok(h.sy - 1 <= m.HELD_MAX + 1e-9, `appesa a ${v}: ${h.sy}`);
          assert.ok(a.sy - 1 <= m.AIR_MAX + 1e-9, `in aria a ${v}: ${a.sy}`);
          volume(h, `appesa ${v}`); volume(a, `in aria ${v}`);
          for (const t of times) {
            const i = m.impactSquash(v, t);
            assert.ok(stretchOf(i) <= m.IMPACT_MAX + 1e-9, `tonfo ${v} a ${t} ms: ${i.sx}`);
            volume(i, `tonfo ${v} a ${t} ms`);
          }
        }
    """)


def test_up_or_down_stretches_the_same() -> None:
    _run("""
        for (const v of speeds) assert.deepEqual(m.airStretch(v), m.airStretch(-v));
    """)


def test_every_shape_comes_back_to_rest() -> None:
    _run("""
        const rest = { sx: 1, sy: 1, dy: 0 };
        assert.equal(m.GETUP_MS, m.GETUP_LOAD_MS + m.GETUP_RISE_MS + m.GETUP_SETTLE_MS);
        assert.deepEqual(m.impactSquash(3000, m.IMPACT_MS), rest);
        assert.deepEqual(m.impactSquash(3000, -1), rest);
        assert.deepEqual(m.getUp(m.GETUP_MS), rest);
        assert.deepEqual(m.heldStretch(0), { sx: 1, sy: 1, dy: 0 });
        assert.deepEqual(m.airStretch(0), { sx: 1, sy: 1, dy: 0 });
        // Il tonfo si spegne prima della fine: niente scatto quando torna a 1.
        assert.ok(stretchOf(m.impactSquash(5000, m.IMPACT_MS - 5)) < 0.002);
    """)


def test_a_harder_landing_squashes_more() -> None:
    _run("""
        const soft = m.impactSquash(300, 0), hard = m.impactSquash(2600, 0);
        assert.ok(hard.sy < soft.sy, `${hard.sy} contro ${soft.sy}`);
        assert.ok(soft.sy <= 1 - m.IMPACT_MIN + 1e-9, 'anche un tonfo leggero si vede');
    """)


def test_get_up_and_walk_keep_volume_and_stay_small() -> None:
    _run("""
        let prev = m.getUp(0);
        for (const t of times) {
          const g = m.getUp(t), w = m.walkStep(t);
          volume(g, `rialzata a ${t}`); volume(w, `passo a ${t}`);
          assert.ok(stretchOf(g) <= 0.08 && stretchOf(w) <= 0.04);
          // Continua: nessun salto fra un fotogramma e il successivo.
          assert.ok(Math.abs(g.sy - prev.sy) < 0.01, `salto a ${t} ms`);
          prev = g;
        }
        // L'affondo cade sul cambio di piede, la punta a meta' passo.
        assert.ok(m.walkStep(0).sy < 1 && m.walkStep(m.STEP_MS / 2).sy > 1);
        assert.ok(m.walkStep(m.STEP_MS / 2).dy < 0, 'a meta passo si alza');
    """)


def test_around_feet_is_axis_aligned() -> None:
    """Una scala sola, fra due traslazioni verticali: nessuna rotazione."""
    _run("""
        const t = m.aroundFeet({ sx: 1.1, sy: 0.9, dy: -2 }, 40);
        assert.match(t, /^ translate\\(0px, 38\\.0px\\) scale\\(1\\.1000, 0\\.9000\\) translate\\(0px, -40\\.0px\\)$/);
        assert.equal(m.aroundFeet({ sx: 1, sy: 1, dy: 0 }, 40), '');
    """)
