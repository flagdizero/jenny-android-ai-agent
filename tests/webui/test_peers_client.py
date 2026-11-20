"""Il lato WebUI delle altre Jenny: il link del QR e le righe del pannello.

Le funzioni vere, ritagliate dai sorgenti (``shared/peer-link.js``,
``mobile-peers.js``) e incollate in uno script con un ``i18n`` finto che
restituisce la chiave e i parametri: si prova che cosa decidono, non che
parole usano.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, function, requires_node, run_js

PEER_LINK = (ASSETS / "shared" / "peer-link.js").read_text(encoding="utf-8")
PEERS = (ASSETS / "mobile-peers.js").read_text(encoding="utf-8")


def _parse(link: object) -> object:
    script = function(PEER_LINK, "parsePairLink") + f"""
console.log(JSON.stringify(parsePairLink({json.dumps(link)})));
"""
    return json.loads(run_js(script))


@requires_node
def test_the_qr_link_gives_address_port_and_code() -> None:
    assert _parse("jenny://pair?host=pixel-personale&ip=100.101.1.2&port=18790&code=K7Q4MZP2") == {
        "host": "pixel-personale", "ip": "100.101.1.2", "port": 18790, "code": "K7Q4MZP2",
    }


@requires_node
def test_a_link_with_only_the_ip_is_enough() -> None:
    assert _parse("jenny://pair?ip=100.101.1.2&code=K7Q4MZP2") == {
        "host": "", "ip": "100.101.1.2", "port": None, "code": "K7Q4MZP2",
    }


@requires_node
def test_links_that_are_not_pairing_links_are_ignored() -> None:
    assert _parse("https://example.com/?code=K7Q4MZP2&host=x") is None
    assert _parse("jenny://pair?host=pixel") is None  # senza codice
    assert _parse("jenny://pair?code=K7Q4MZP2") is None  # senza indirizzo
    assert _parse(None) is None


@requires_node
def test_a_silly_port_is_dropped_not_trusted() -> None:
    parsed = _parse("jenny://pair?host=p&port=99999&code=K7Q4MZP2")
    assert isinstance(parsed, dict) and parsed["port"] is None


def _state(peer: dict, summary: dict | None = None) -> list:
    script = (
        "const i18n = { locale: 'it', t: (k, p) => p ? `${k} ${JSON.stringify(p)}` : k };\n"
        "const t = (key, params) => i18n.t(`settings.peers.${key}`, params);\n"
        + function(PEERS, "timeText")
        + "\n"
        + function(PEERS, "peerStateText")
        + "\n"
        + function(PEERS, "peersSummary")
        + f"""
console.log(JSON.stringify([
  peerStateText({json.dumps(peer)}),
  peersSummary({json.dumps(summary)}),
]));
"""
    )
    return json.loads(run_js(script))


@requires_node
def test_a_peer_without_its_secret_must_be_linked_again() -> None:
    state, _ = _state({"has_secret": False, "last_seen_ms": 1, "queued": 3})
    assert state == "settings.peers.stateRepair"


@requires_node
def test_a_failure_after_the_last_contact_reads_unreachable_with_the_queue() -> None:
    state, _ = _state({
        "has_secret": True, "last_seen_ms": 1000, "last_try_ms": 2000,
        "last_error": "unreachable", "queued": 2,
    })
    assert state.startswith("settings.peers.stateUnreachable")
    assert 'settings.peers.stateQueued {"count":2}' in state


@requires_node
def test_the_summary_names_a_single_peer() -> None:
    _, summary = _state(
        {"has_secret": True, "last_seen_ms": 0, "queued": 0},
        {"enabled": True, "peers": [{"name": "Lavoro"}]},
    )
    assert summary == 'settings.peers.summaryOne {"name":"Lavoro"}'
    _, off = _state({"has_secret": True}, {"enabled": False, "peers": []})
    assert off == "settings.peers.summaryOff"


def test_every_key_the_panel_uses_exists_in_both_languages() -> None:
    import re

    # Solo il ``t(...)`` del pannello, che antepone ``settings.peers.``: gli
    # ``i18n.t(...)`` hanno la chiave intera e li controlla ``test_i18n_keys_exist``.
    keys = set(re.findall(r"(?<![.\w])t\('([a-zA-Z.]+)'", PEERS))
    assert len(keys) > 20
    keys |= {f"errors.{k}" for k in re.findall(r"'(bad_code|bad_name|no_code_open|unreachable|"
                                               r"disabled|protocol)'", PEERS)}
    keys.add("errors.generic")
    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        block = data["settings"]["peers"]
        for key in keys:
            node = block
            for part in key.split("."):
                assert part in node, f"{lang}: settings.peers.{key} manca"
                node = node[part]
