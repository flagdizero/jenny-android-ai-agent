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


def _render(card: object) -> dict:
    script = (
        "import { createRequire } from 'module';\n"
        "const require = createRequire(import.meta.url);\n"
        "const { JSDOM } = require('jsdom');\n"
        "globalThis.document = new JSDOM('').window.document;\n"
        "const i18n = { locale: 'it', t: (k, p) => p ? `${k} ${JSON.stringify(p)}` : k };\n"
        "const STATUSES = new Set(['ok', 'refused', 'too_many', 'unavailable', 'failed', "
        "'expired']);\n"
        + function(DOM, "el") + "\n"
        + function(DOM, "icon") + "\n"
        + function(CARD, "timeOf") + "\n"
        + function(CARD, "peerCardNode") + "\n"
        + f"const node = peerCardNode({json.dumps(card)});\n"
        "console.log(JSON.stringify(node ? { cls: node.className, html: node.innerHTML, "
        "text: node.textContent } : null));\n"
    )
    return json.loads(run_js(script))


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
