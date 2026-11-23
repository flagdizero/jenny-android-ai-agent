"""La linea passata in casa: le regole che il disegno segue.

Chi compare nel foglio, cosa scrive la testa, quando si dice «non risponde»
(``home-line.js``); e nella stanza «Altre Jenny» il livello di «Domande e
richieste», lo stato di una collegata, chi è la Jenny appena collegata
(``home-peers.js``). Il resto è disegno, e lo guarda chi lo prova a schermo.
"""

from __future__ import annotations

import json
import re

from support.js_harness import ASSETS, function, requires_node, run_js

LINE_JS = ASSETS / "home-line.js"
PEERS_JS = ASSETS / "home-peers.js"

pytestmark = requires_node


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^(?:export )?const {re.escape(name)} = (.+?);\n", source)
    assert m, f"const {name} non trovata"
    return f"const {name} = {m.group(1)};"


def _run(script: str) -> dict:
    line = LINE_JS.read_text(encoding="utf-8")
    peers = PEERS_JS.read_text(encoding="utf-8")
    harness = "\n".join([
        "const i18n = { locale: 'it', t: (key, params) => (params ? key + ':' + JSON.stringify(params) : key) };",
        "const t = (key, params) => i18n.t('home.peers.' + key, params);",
        function(line, "isComputer"),
        function(line, "peerGlyph"),
        function(line, "headParts"),
        function(line, "isStuck"),
        function(line, "linePeers"),
        _const(peers, "ASK_LEVELS"),
        function(peers, "askLevel"),
        function(peers, "isUnreachable"),
        function(peers, "stateText"),
        function(peers, "peersSummary"),
        function(peers, "freshPeer"),
    ])
    return json.loads(run_js(harness + "\n" + script))


def test_the_head_says_the_jenny_and_the_notebook_when_there_is_one() -> None:
    out = _run("""
      console.log(JSON.stringify({
        none: headParts(null),
        main: headParts({ name: 'Lavoro', place: '' }),
        notebook: headParts({ name: 'Lavoro', place: 'Fornitori' }),
      }));
    """)
    assert out == {"none": [], "main": ["Lavoro"], "notebook": ["Lavoro", "Fornitori"]}


def test_not_answering_needs_something_waiting_to_leave() -> None:
    out = _run("""
      console.log(JSON.stringify({
        idle: isStuck({ unreachable: true, queued: 0 }),
        waiting: isStuck({ unreachable: true, queued: 2 }),
        slow: isStuck({ unreachable: false, queued: 2 }),
        none: isStuck(null),
      }));
    """)
    assert out == {"idle": False, "waiting": True, "slow": False, "none": False}


def test_the_sheet_lists_only_the_ones_that_can_be_reached_at_all() -> None:
    out = _run("""
      const peers = [
        { id: 'a', name: 'Lavoro', has_secret: true },
        { id: 'b', name: 'Vecchia', has_secret: false },
        { id: 'c', name: 'Mac', has_secret: true, kind: 'opencode' },
      ];
      console.log(JSON.stringify({
        on: linePeers({ enabled: true, peers }).map((p) => p.name),
        off: linePeers({ enabled: false, peers }),
        unknown: linePeers(null),
        glyphs: peers.map(peerGlyph),
      }));
    """)
    assert out["on"] == ["Lavoro", "Mac"]
    assert out["off"] == [] and out["unknown"] == []
    assert out["glyphs"] == ["ti-arrows-exchange", "ti-arrows-exchange", "ti-device-laptop"]


def test_questions_and_requests_is_one_level_out_of_two_permissions() -> None:
    out = _run("""
      console.log(JSON.stringify({
        none: askLevel({ can_ask: false, act: 'off' }),
        ask: askLevel({ can_ask: true, act: 'off' }),
        confirm: askLevel({ can_ask: true, act: 'confirm' }),
        free: askLevel({ can_ask: false, act: 'free' }),
        ids: ASK_LEVELS.map((l) => l.id),
        writes: ASK_LEVELS.map((l) => [l.can_ask, l.act]),
      }));
    """)
    assert out["none"] == "none" and out["ask"] == "ask"
    assert out["confirm"] == "confirm" and out["free"] == "free"
    assert out["ids"] == ["none", "ask", "confirm", "free"]
    assert out["writes"] == [[False, "off"], [True, "off"], [True, "confirm"], [True, "free"]]


def test_the_state_of_a_linked_jenny_in_a_line() -> None:
    out = _run("""
      console.log(JSON.stringify({
        repair: stateText({ has_secret: false }),
        down: stateText({ has_secret: true, last_error: 'x', last_try_ms: 9, last_seen_ms: 5 }),
        never: stateText({ has_secret: true, last_seen_ms: 0 }),
        seen: stateText({ has_secret: true, last_seen_ms: 1700000000000 }).split(':')[0],
        back: isUnreachable({ last_error: 'x', last_try_ms: 5, last_seen_ms: 9 }),
      }));
    """)
    assert out["repair"] == "home.peers.state.repair"
    assert out["down"] == "home.peers.state.unreachable"
    assert out["never"] == "home.peers.state.never"
    assert out["seen"] == "home.peers.state.seen"
    assert out["back"] is False


def test_the_settings_row_names_the_linked_ones() -> None:
    out = _run("""
      const peers = [{ name: 'Lavoro' }, { name: 'Mac' }];
      console.log(JSON.stringify({
        names: peersSummary({ enabled: true, peers }),
        off: peersSummary({ enabled: false, peers }),
        empty: peersSummary({ enabled: true, peers: [] }),
      }));
    """)
    assert out == {"names": "Lavoro, Mac", "off": "home.peers.none", "empty": "home.peers.none"}


def test_the_jenny_that_used_the_qr_is_the_new_one() -> None:
    out = _run("""
      const before = { peers: [{ id: 'a' }] };
      console.log(JSON.stringify({
        fresh: freshPeer(before, { peers: [{ id: 'a' }, { id: 'b', name: 'nuova' }] }),
        same: freshPeer(before, { peers: [{ id: 'a' }] }),
        first: freshPeer(null, { peers: [{ id: 'z' }] }),
      }));
    """)
    assert out["fresh"] == {"id": "b", "name": "nuova"}
    assert out["same"] is None
    assert out["first"] == {"id": "z"}
