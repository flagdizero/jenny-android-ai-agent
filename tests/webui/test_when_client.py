"""`shared/when.js`: «oggi alle», «ieri alle», detto una volta per tutti.

Erano quattro coppie di chiavi uguali e due modi di contare i giorni (a blocchi
di 24 ore dalla mezzanotte, o sul calendario): il cassetto, gli snapshot, i
buchi di batteria e l'ultimo controllo degli aggiornamenti. Qui la regola che
resta, il calendario.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
WHEN_JS = (ASSETS / "shared" / "when.js").read_text(encoding="utf-8")
I18N_JS = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")

pytestmark = requires_node


def test_today_yesterday_and_farther() -> None:
    it = json.loads((ASSETS / "i18n" / "it.json").read_text(encoding="utf-8"))
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(it)} }}, {member(I18N_JS, 't')} }};\n"
        + "\n".join(function(WHEN_JS, n) for n in ("dayOffset", "dayAndTime"))
        + """
const at = (y, m, d, h, min) => new Date(y, m, d, h, min).getTime();
const now = at(2026, 10, 2, 0, 10);
assert.equal(dayOffset(at(2026, 10, 1, 23, 50), now), 1, 'venti minuti prima di mezzanotte e\\u2019 ieri');
assert.equal(dayOffset(now, now), 0);
assert.equal(dayOffset(at(2026, 10, 3, 9, 0), now), -1);
assert.equal(dayOffset(at(2026, 9, 24, 12, 0), now), 9, 'attraverso il cambio d\\u2019ora');
assert.match(dayAndTime(at(2026, 10, 2, 0, 5), { now }), /^oggi alle 00:05$/);
assert.match(dayAndTime(at(2026, 10, 1, 23, 50), { now }), /^ieri alle 23:50$/);
assert.equal(dayAndTime(at(2026, 9, 31, 12, 0), { now }), '');
assert.match(dayAndTime(at(2026, 10, 3, 9, 0), { now }), /^oggi alle /, 'il futuro conta come oggi');
assert.match(dayAndTime(at(2026, 10, 2, 9, 5), { now: at(2026, 10, 2, 10, 0), hour: 'numeric' }), /^oggi alle 9:05$/);
"""
    )
