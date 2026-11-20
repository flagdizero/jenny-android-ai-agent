"""Chi c'è dall'altra parte: un'altra Jenny o opencode sul Mac (``PeerConfig.kind``).

Lo dice il campo ``agent`` di ``pair_challenge``/``pair_ok`` (vedi
``docs/internals/peer-protocol.md``); chi si abbina lo salva come ``kind``, lo
stato del pannello lo porta, e ``peer_ask`` lo dice al modello accanto al nome,
con frasi che valgono anche per un computer.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.peer import OPENCODE_WHAT, PeerAskTool
from jenny.config.loader import load_config
from jenny.config.schema import PeerConfig
from jenny.peers import link as link_module
from jenny.peers.link import PeerError
from jenny.peers.model import KIND_JENNY, KIND_OPENCODE, peer_kind
from tests.peers.harness import pair


async def test_two_jennys_know_each_other_as_jennys(two):
    a, b = two
    await pair(a, b)

    assert a.peers()[0].kind == KIND_JENNY
    assert b.peers()[0].kind == KIND_JENNY
    status = await b.link.status()
    assert status["peers"][0]["kind"] == KIND_JENNY


async def test_a_side_that_says_opencode_is_saved_as_opencode(two, monkeypatch):
    # Nello stesso processo le due parti leggono lo stesso modulo: così tutte e
    # due si presentano come opencode, e basta guardare cosa salva chi scrive il codice.
    monkeypatch.setattr(link_module, "SELF_AGENT", KIND_OPENCODE)
    a, b = two
    await pair(a, b)

    assert b.peers()[0].kind == KIND_OPENCODE
    assert (await b.link.status())["peers"][0]["kind"] == KIND_OPENCODE
    # Anche chi mostra il codice legge il campo, da ``pair``.
    assert a.peers()[0].kind == KIND_OPENCODE


def test_an_unknown_or_missing_agent_is_a_jenny():
    assert peer_kind(None) == KIND_JENNY
    assert peer_kind("") == KIND_JENNY
    assert peer_kind("toaster") == KIND_JENNY
    assert peer_kind(KIND_OPENCODE) == KIND_OPENCODE


def test_an_old_config_without_kind_reads_as_jenny(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"peers": {"enabled": True, "peers": [
        {"id": "0" * 32, "name": "Lavoro"},
    ]}}))
    peer = load_config(path).peers.peers[0]
    assert peer.kind == KIND_JENNY
    assert PeerConfig(id="1" * 32, name="Mac").kind == KIND_JENNY


async def test_a_kind_from_a_newer_version_does_not_break_the_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"peers": {"enabled": True, "peers": [
        {"id": "0" * 32, "name": "Lavoro", "kind": "toaster"},
    ]}}))
    peer = load_config(path).peers.peers[0]
    assert peer.name == "Lavoro"
    assert peer_kind(peer.kind) == KIND_JENNY


class _Questions:
    """Il servizio delle domande, ridotto a ciò che ``peer_ask`` gli chiede."""

    def __init__(self, kind: str, *, which: bool = False) -> None:
        self.kind = kind
        self.which = which

    async def ask(self, peer: str, question: str, mode: str) -> dict[str, Any]:
        if self.which:
            raise PeerError("which_peer")
        return {"peer": "Mac", "kind": self.kind}

    async def peers(self) -> list[Any]:
        return [
            PeerConfig(id="0" * 32, name="Lavoro"),
            PeerConfig(id="1" * 32, name="Mac", kind=KIND_OPENCODE),
        ]


def _tool(questions: _Questions) -> PeerAskTool:
    tool = PeerAskTool()
    tool.bind_questions(questions)
    return tool


async def test_asking_the_mac_says_what_it_is_and_no_her():
    out = await _tool(_Questions(KIND_OPENCODE)).execute(peer="Mac", question="CI of PR 85?")

    assert f"Mac ({OPENCODE_WHAT})" in out
    assert "The answer will appear" in out
    assert "computer is back online" in out
    assert "Her " not in out and " her " not in out


async def test_asking_another_jenny_keeps_the_name_plain():
    out = await _tool(_Questions(KIND_JENNY)).execute(peer="Mac", question="Did it arrive?")

    assert OPENCODE_WHAT not in out
    assert "phone is back online" in out


async def test_which_one_lists_each_peer_with_what_it_is():
    out = await _tool(_Questions(KIND_JENNY, which=True)).execute(question="Status?")

    assert out == f"Say which one to ask: Lavoro, Mac ({OPENCODE_WHAT})."


def test_the_description_does_not_depend_on_who_is_linked():
    # La cache del provider parte dalla lista dei tool: la descrizione è testo
    # fisso, i nomi e i tipi arrivano solo nei risultati.
    first = _tool(_Questions(KIND_JENNY)).description
    second = _tool(_Questions(KIND_OPENCODE)).description
    assert first == second
    assert "opencode" in first
