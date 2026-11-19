"""La scheda della risposta di un'altra Jenny: disegnata dal codice, dal vivo e riletta.

La funzione vera (``shared/peer-card.js``) su un documento jsdom, con un
``i18n`` finto che restituisce la chiave e i parametri; e la rilettura del
transcript, che deve consegnare alla casa la scheda e non il suo ripiego.
"""

from __future__ import annotations

import json

from support.home_dom import requires_jsdom
from support.js_harness import ASSETS, function, requires_node, run_js

from jenny.webui.transcript_replay import replay_transcript_to_ui_messages

CARD = (ASSETS / "shared" / "peer-card.js").read_text(encoding="utf-8")
DOM = (ASSETS / "shared" / "dom.js").read_text(encoding="utf-8")


def _render(card: object, click: str | None = None) -> dict:
    script = (
        "import { createRequire } from 'module';\n"
        "const require = createRequire(import.meta.url);\n"
        "const { JSDOM } = require('jsdom');\n"
        "globalThis.document = new JSDOM('').window.document;\n"
        "const i18n = { locale: 'it', t: (k, p) => p ? `${k} ${JSON.stringify(p)}` : k };\n"
        "const calls = [];\n"
        "const rpc = { peer: async (m, p) => { calls.push([m, p]); return {}; } };\n"
        "const showToast = () => {};\n"
        + _consts() + "\n"
        + function(DOM, "el") + "\n"
        + function(DOM, "icon") + "\n"
        + function(DOM, "button") + "\n"
        + function(CARD, "timeOf") + "\n"
        + function(CARD, "headText") + "\n"
        + function(CARD, "doneNode") + "\n"
        + function(CARD, "undoButton") + "\n"
        + function(CARD, "peerCardNode") + "\n"
        + f"const node = peerCardNode({json.dumps(card)});\n"
        + (click or "")
        + "console.log(JSON.stringify(node ? { cls: node.className, html: node.innerHTML, "
        "text: node.textContent, calls } : null));\n"
    )
    return json.loads(run_js(script))


def _consts() -> str:
    """Le costanti in testa al modulo, prese dal file vero (``STATUSES`` e seguenti)."""
    start = CARD.index("const STATUSES")
    end = CARD.index("function timeOf")
    return CARD[start:end]


@requires_node
@requires_jsdom
def test_an_answer_is_quoted_as_text_not_markup() -> None:
    out = _render({
        "peer": "Lavoro", "status": "ok", "question": "È arrivata?", "at": 1_700_000_000_000,
        "text": "Sì <img src=x onerror=alert(1)> [link](http://evil)",
    })
    assert out["cls"] == "home-peer-card is-ok"
    assert "<img" not in out["html"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in out["html"]
    assert "[link](http://evil)" in out["text"]
    assert "«È arrivata?»" in out["text"]
    assert 'home.peer.fromAt {"name":"Lavoro"' in out["text"]


@requires_node
@requires_jsdom
def test_a_refusal_says_where_to_turn_it_on() -> None:
    out = _render({"peer": "Lavoro", "status": "refused", "question": "Agenda?", "text": ""})
    assert out["cls"] == "home-peer-card is-refused"
    assert 'home.peer.status.refused {"name":"Lavoro"}' in out["text"]


@requires_node
@requires_jsdom
def test_an_unknown_status_reads_as_failed_and_junk_is_no_card() -> None:
    assert _render({"peer": "Lavoro", "status": "weird"})["cls"] == "home-peer-card is-failed"
    assert _render(None) is None
    assert _render({"status": "ok"}) is None


@requires_node
@requires_jsdom
def test_files_that_did_not_arrive_are_named() -> None:
    out = _render({"peer": "Lavoro", "status": "ok", "text": "Eccolo", "missing": ["a.pdf"]})
    assert 'home.peer.missing {"files":"a.pdf"}' in out["text"]


def test_the_replay_hands_the_card_to_the_house() -> None:
    card = {"peer": "Lavoro", "status": "ok", "question": "?", "text": "Sì", "at": 1}
    lines = [
        {"event": "message", "chat_id": "default", "text": "Lavoro: «Sì»",
         "agent_ui": {"peer_answer": card}, "turn_id": "t1", "turn_phase": "answer"},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    assert messages[-1]["peerAnswer"] == card
    assert messages[-1]["content"] == "Lavoro: «Sì»"


def test_a_plain_message_carries_no_card() -> None:
    lines = [{"event": "message", "chat_id": "default", "text": "ciao", "turn_id": "t1",
              "turn_phase": "answer"}]
    assert "peerAnswer" not in replay_transcript_to_ui_messages(lines)[-1]


ACT = {
    "peer": "Lavoro", "status": "ok", "question": "Mettimi un promemoria", "text": "Fatto.",
    "at": 1_700_000_000_000, "mode": "act", "qid": "a" * 32, "peerId": "b" * 32,
    "working": False,
    "done": [
        {"kind": "file", "label": "note/bianchi.md", "undoable": True},
        {"kind": "job", "label": "Richiamare Bianchi", "undoable": True},
        {"kind": "egress", "label": "bianchi.it", "undoable": False, "allowed": False},
        {"kind": "weird", "label": "x"},
    ],
}


@requires_node
@requires_jsdom
def test_a_request_lists_what_it_touched_and_offers_to_undo() -> None:
    out = _render(ACT)
    assert out["cls"] == "home-peer-card is-ok is-act"
    assert 'home.peer.didAt {"name":"Lavoro"' in out["text"]
    assert "note/bianchi.md" in out["text"] and "Richiamare Bianchi" in out["text"]
    assert "bianchi.it" in out["text"] and "home.peer.denied" in out["text"]
    assert "weird" not in out["text"]
    assert "home.peer.askUndo" in out["text"]


@requires_node
@requires_jsdom
def test_the_undo_button_asks_the_other_jenny() -> None:
    click = ("node.querySelector('.home-peer-undo').click();\n"
             "await new Promise((r) => setTimeout(r, 0));\n")
    out = _render(ACT, click)
    assert out["calls"] == [["undo_remote", {"id": "b" * 32, "qid": "a" * 32}]]


@requires_node
@requires_jsdom
def test_still_working_has_no_undo_and_nothing_undoable_neither() -> None:
    out = _render({**ACT, "working": True})
    assert 'home.peer.workingAt' in out["text"]
    assert "home.peer.askUndo" not in out["text"]
    only_out = {**ACT, "done": [{"kind": "egress", "label": "x.it", "undoable": False}]}
    assert "home.peer.askUndo" not in _render(only_out)["text"]


@requires_node
@requires_jsdom
def test_a_refused_request_and_an_undo_say_so() -> None:
    refused = _render({"peer": "Lavoro", "status": "refused", "mode": "act", "question": "x"})
    assert 'home.peer.status.refused_act {"name":"Lavoro"}' in refused["text"]
    undone = _render({"peer": "Lavoro", "status": "undone", "mode": "act", "question": "x",
                      "undo": {"restored": 2, "kept": 1, "jobs": 1}})
    assert undone["cls"] == "home-peer-card is-undone is-act"
    assert '"files":2' in undone["text"] and '"jobs":1' in undone["text"]
    assert 'home.peer.kept {"count":1}' in undone["text"]
