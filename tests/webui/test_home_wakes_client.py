"""«Troppi risvegli» in casa: quando compare la pastiglia, e cosa dice il foglio.

Jenny e l'utente programmano quel che vogliono; l'unico freno è un avviso. Qui
si misura la regola dell'avviso (``shouldAlert``) e le righe del foglio
(``wakeRows``, ``wakesNote``): il resto è disegno.
"""

from __future__ import annotations

import json
import re

from support.js_harness import ASSETS, function, requires_node, run_js

WAKES_JS = ASSETS / "home-wakes.js"

pytestmark = requires_node


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^(?:export )?const {re.escape(name)} = (.+);$", source)
    assert m, f"const {name} non trovata"
    return f"const {name} = {m.group(1)};"


def _run(script: str) -> dict:
    source = WAKES_JS.read_text(encoding="utf-8")
    harness = "\n".join([
        "const tr = (key, params) => (params ? key + ':' + JSON.stringify(params) : key);",
        "const i18n = { locale: 'it', t: tr };",
        "const describeSchedule = (s) => 'ogni ' + (s.every_ms / 60000) + ' minuti';",
        _const(source, "ACK_GROWTH"),
        _const(source, "MAX_ROWS"),
        function(source, "shouldAlert"),
        function(source, "wakeRows"),
        function(source, "wakesNote"),
    ])
    return json.loads(run_js(harness + "\n" + script))


def test_the_pill_shows_only_above_the_threshold() -> None:
    out = _run("""
      const w = (total) => ({ total, threshold: 50 });
      console.log(JSON.stringify({
        below: shouldAlert(w(49), null),
        at: shouldAlert(w(50), null),
        noThreshold: shouldAlert({ total: 500 }, null),
        nothing: shouldAlert(null, null),
      }));
    """)
    assert out == {"below": False, "at": True, "noThreshold": False, "nothing": False}


def test_fine_like_this_holds_until_the_wakes_double() -> None:
    out = _run("""
      const w = (total) => ({ total, threshold: 50 });
      const ack = { total: 84 };
      console.log(JSON.stringify({
        same: shouldAlert(w(84), ack),
        more: shouldAlert(w(167), ack),
        doubled: shouldAlert(w(168), ack),
      }));
    """)
    assert out == {"same": False, "more": False, "doubled": True}


def test_the_rows_join_the_count_with_the_job() -> None:
    out = _run("""
      const payload = {
        jobs: [
          { id: 'a', name: 'Controllo del volo', schedule: { kind: 'every', every_ms: 300000 },
            actions: ['pause', 'remove'] },
          { id: 'b', name: 'Piante', schedule: { kind: 'every', every_ms: 600000 },
            paused_at_ms: 5, actions: ['resume', 'remove'] },
        ],
        wakes: { total: 70, threshold: 50, jobs: [
          { id: 'a', name: 'vecchio nome', autonomous: true, count: 61 },
          { id: 'gone', name: 'Treno per Nara', autonomous: true, count: 6 },
          { id: 'b', name: 'Piante', autonomous: false, count: 3 },
        ] },
      };
      const rows = wakeRows(payload, { locale: 'it' });
      console.log(JSON.stringify({
        rows,
        noteOwn: wakesNote(rows, 70),
        noteYours: wakesNote([{ count: 60, autonomous: false }], 70),
        noteSpread: wakesNote([{ count: 20, autonomous: true }], 70),
        many: wakeRows({ jobs: [], wakes: { jobs: Array.from({ length: 12 }, (_, i) => (
          { id: 'j' + i, name: 'n' + i, count: 1 })) } }).length,
      }));
    """)
    flight, train, plants = out["rows"]
    assert flight["name"] == "Controllo del volo" and flight["count"] == 61
    assert flight["autonomous"] is True and flight["schedule"] == "ogni 5 minuti"
    assert flight["canPause"] and flight["canRemove"] and not flight["gone"]
    # Un singolo già scattato: resta col suo nome, senza gesti.
    assert train["gone"] and train["name"] == "Treno per Nara"
    assert not (train["canPause"] or train["canResume"] or train["canRemove"])
    assert plants["paused"] and plants["canResume"] and not plants["canPause"]
    assert out["noteOwn"] == "home.wakes.note home.wakes.noteOwn"
    assert out["noteYours"] == "home.wakes.note home.wakes.noteYours"
    assert out["noteSpread"] == "home.wakes.note"
    assert out["many"] == 6
