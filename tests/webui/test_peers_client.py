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


def _fresh(before: object, after: object) -> object:
    script = function(PEERS, "peerLinkedFromQr") + f"""
console.log(JSON.stringify(peerLinkedFromQr({json.dumps(before)}, {json.dumps(after)})));
"""
    return json.loads(run_js(script))


@requires_node
def test_a_jenny_linked_from_this_qr_is_asked_a_name() -> None:
    # Chi mostrava il QR le dava il nome Tailscale, da rinominare a mano.
    old = {"id": "a" * 32, "name": "Casa"}
    new = {"id": "b" * 32, "name": "sdk-gphone16k-arm64-2"}
    assert _fresh({"pairing": True, "peers": [old]},
                  {"pairing": False, "peers": [old, new]}) == new


@requires_node
def test_no_name_is_asked_without_an_open_code() -> None:
    old = {"id": "a" * 32, "name": "Casa"}
    new = {"id": "b" * 32, "name": "Lavoro"}
    # Chi si collega col codice dell'altra il nome l'ha già dato nel modulo.
    assert _fresh({"pairing": False, "peers": [old]},
                  {"pairing": False, "peers": [old, new]}) is None
    assert _fresh(None, {"pairing": False, "peers": [new]}) is None
    assert _fresh({"pairing": True, "peers": [old]}, {"pairing": True, "peers": [old]}) is None


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


def test_the_act_keys_built_at_runtime_exist_in_both_languages() -> None:
    """Le chiavi composte dal pannello (livelli, stati, errori nuovi): la regex sopra non le vede."""
    needed = [f"act.{v}" for v in ("label", "off", "confirm", "free")]
    needed += [f"act.hint.{v}" for v in ("off", "confirm", "free")]
    needed += [f"actState.{s}" for s in ("answering", "working", "answered", "refused",
                                         "too_many", "unavailable", "failed", "undone")]
    needed += [f"errors.{e}" for e in ("still_working", "nothing_to_undo", "bad_level")]
    needed += [f"log.{k}" for k in ("confirmYes", "confirmNo", "undo", "undoAsked", "undone")]
    needed += ["linkedTitle", "renameTitle"]  # ``t(fresh ? … : …)`` in ``_rename``
    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        for key in needed:
            node = data["settings"]["peers"]
            for part in key.split("."):
                assert part in node, f"{lang}: settings.peers.{key} manca"
                node = node[part]
        status = data["home"]["peer"]["status"]
        for key in ("refused_act", "undone", "undo_refused", "undo_still_working",
                    "undo_not_found", "undo_failed"):
            assert key in status, f"{lang}: home.peer.status.{key} manca"


def _card(peer: dict) -> str:
    """La riga di un collegato come la disegna il pannello vero (``PeersPanel.render``)."""
    import re

    cls = re.search(r"(?ms)^export class PeersPanel \{.*?^\}$", PEERS)
    assert cls, "PeersPanel non trovata"
    script = (
        "const i18n = { locale: 'it', t: (k, p) => p ? `${k} ${JSON.stringify(p)}` : k };\n"
        "const t = (key, params) => i18n.t(`settings.peers.${key}`, params);\n"
        "const escapeHtml = (s) => String(s ?? '');\n"
        "const POLL_MS = 5000;\n"
        + function(PEERS, "timeText") + "\n"
        + function(PEERS, "isOpencodePeer") + "\n"
        + function(PEERS, "peerStateText") + "\n"
        + cls.group(0).removeprefix("export ")
        + f"""
const panel = Object.create(PeersPanel.prototype);
Object.assign(panel, {{ _acts: {{}}, _history: {{}}, _pair: null, _destroyed: false }});
let out = '';
panel._paint = (parts) => {{ out = parts.join(''); }};
panel.status = {{ enabled: true, tailscale: {{ state: 'up' }}, peers: [{json.dumps(peer)}], pending: [] }};
panel.render();
console.log(JSON.stringify(out));
"""
    )
    return json.loads(run_js(script))


@requires_node
def test_a_mac_with_opencode_is_marked_and_has_no_permission_rows() -> None:
    peer = {"id": "a" * 32, "name": "Mac", "kind": "opencode", "has_secret": True,
            "can_ask": True, "send_files": False, "act": "off", "last_seen_ms": 0, "queued": 0}
    html = _card(peer)
    assert "peer-card-opencode" in html and "ti-device-laptop" in html
    assert "settings.peers.kindOpencode" in html and "settings.peers.opencodeHint" in html
    assert 'data-perm="can_ask"' not in html and 'data-perm="send_files"' not in html
    assert "data-act" not in html
    for action in ("ping", "rename", "remove", "history"):
        assert f'data-peers="{action}"' in html


@requires_node
def test_another_jenny_keeps_her_permission_rows() -> None:
    peer = {"id": "b" * 32, "name": "Lavoro", "kind": "jenny", "has_secret": True,
            "can_ask": True, "send_files": False, "act": "off", "last_seen_ms": 0, "queued": 0}
    html = _card(peer)
    assert "peer-card-opencode" not in html and "kindOpencode" not in html
    assert 'data-perm="can_ask"' in html and 'data-perm="send_files"' in html
    assert "data-act" in html
