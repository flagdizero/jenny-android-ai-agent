"""La domanda delle mani di Jenny che attraversa il collegamento
(``jenny/peers/phone_consent.py``): chi ha chiesto da lontano risponde dal suo
telefono, solo se chi agisce glielo concede."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.peers.phone_consent import (
    KIND_PHONE_ASK,
    KIND_PHONE_CLOSED,
    KIND_PHONE_REPLY,
    PeerPhoneConsent,
)
from jenny.phone import consent

PEER = "a" * 32
OTHER = "b" * 32


class _Link:
    def __init__(self, *, phone_confirm: bool = True, act: str = "confirm") -> None:
        self.handlers: dict[str, Any] = {}
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.peer = SimpleNamespace(id=PEER, name="Personale", phone_confirm=phone_confirm, act=act,
                                    line=False)

    def register(self, kind: str, handler: Any) -> None:
        self.handlers[kind] = handler

    async def _config(self) -> Any:
        return SimpleNamespace(enabled=True, peers=[self.peer])

    async def send(self, peer_id: str, kind: str, body: dict[str, Any]) -> str:
        self.sent.append((peer_id, kind, body))
        return "m"


@pytest.fixture
def rig():
    link = _Link()
    asks = consent.PhoneAsks(ttl_s=5)
    shown: list[tuple[str, str, dict[str, Any]]] = []
    hidden: list[str] = []
    tasks: list[asyncio.Task[Any]] = []
    bridge = PeerPhoneConsent(
        link, asks, show=lambda cid, name, ask: shown.append((cid, name, ask)),
        hide=hidden.append, spawn=lambda coro: tasks.append(asyncio.ensure_future(coro)),
    )

    async def settle() -> None:
        while tasks:
            await tasks.pop(0)

    return SimpleNamespace(link=link, asks=asks, bridge=bridge, shown=shown, hidden=hidden,
                           settle=settle)


def _ctx(peer: str = PEER) -> RequestContext:
    return RequestContext(channel="websocket", chat_id="x", session_key=f"peer:{peer}:act",
                          metadata={"_peer_act": {"peer": peer, "qid": "c" * 32}})


async def _ask(rig, **kw: Any) -> tuple[asyncio.Task[str], str]:
    task = asyncio.ensure_future(rig.asks.ask(
        kind="app", target="com.mail", app="Gmail", goal="leggere le email", what="",
        origin=kw.get("origin", "peer"), options=("ok", "always", "no"), ctx=kw.get("ctx", _ctx()),
    ))
    await asyncio.sleep(0)
    await rig.settle()
    return task, rig.asks.pending()[0]["cid"]


async def test_the_question_travels_to_the_jenny_that_asked(rig) -> None:
    task, cid = await _ask(rig)
    peer, kind, body = rig.link.sent[0]
    assert (peer, kind) == (PEER, KIND_PHONE_ASK)
    assert body["cid"] == cid and body["app"] == "Gmail" and body["options"] == ["ok", "always", "no"]
    await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": cid, "choice": "ok"})
    assert await task == "ok"
    await rig.settle()
    assert rig.link.sent[-1][1] == KIND_PHONE_CLOSED


async def test_it_stays_home_without_the_permission(rig) -> None:
    rig.link.peer.phone_confirm = False
    task, cid = await _ask(rig)
    assert rig.link.sent == []
    await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": cid, "choice": "ok"})
    assert not task.done()
    rig.asks.resolve(cid, "no")
    assert await task == "no"


async def test_a_local_question_never_leaves_the_phone(rig) -> None:
    ctx = RequestContext(channel="websocket", chat_id="x", session_key="unified:default")
    task, cid = await _ask(rig, origin="app", ctx=ctx)
    assert rig.link.sent == []
    rig.asks.resolve(cid, "ok")
    await task


async def test_only_the_jenny_that_asked_may_answer(rig) -> None:
    task, cid = await _ask(rig)
    await rig.link.handlers[KIND_PHONE_REPLY](OTHER, {"cid": cid, "choice": "ok"})
    assert not task.done()
    rig.asks.resolve(cid, "no")
    await task


async def test_a_permission_removed_midway_is_a_closed_door(rig) -> None:
    task, cid = await _ask(rig)
    rig.link.peer.phone_confirm = False
    await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": cid, "choice": "always"})
    assert not task.done()
    rig.asks.resolve(cid, "no")
    await task


async def test_a_malformed_reply_is_refused(rig) -> None:
    with pytest.raises(ValueError):
        await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": "nope", "choice": "ok"})
    with pytest.raises(ValueError):
        await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": "d" * 32, "choice": "yes"})


async def test_the_asking_side_shows_it_and_sends_the_tap_back(rig) -> None:
    cid = "e" * 32
    await rig.link.handlers[KIND_PHONE_ASK](PEER, {
        "cid": cid, "kind": "app", "app": "Gmail", "goal": "leggere le email",
        "what": "", "options": ["ok", "always", "no"],
    })
    assert rig.shown == [(cid, "Personale", {
        "kind": "app", "app": "Gmail", "goal": "leggere le email", "what": "",
        "options": ["ok", "always", "no"],
    })]
    assert rig.bridge.answer("f" * 32, "ok") is False
    assert rig.bridge.answer(cid, "always") is True
    await rig.settle()
    assert rig.link.sent == [(PEER, KIND_PHONE_REPLY, {"cid": cid, "choice": "always"})]
    assert rig.hidden == [cid] and rig.bridge.answer(cid, "ok") is False


async def test_a_closed_question_disappears_on_the_asking_side(rig) -> None:
    cid = "e" * 32
    await rig.link.handlers[KIND_PHONE_ASK](PEER, {"cid": cid, "options": ["ok", "no"]})
    await rig.link.handlers[KIND_PHONE_CLOSED](OTHER, {"cid": cid})
    assert rig.hidden == []
    await rig.link.handlers[KIND_PHONE_CLOSED](PEER, {"cid": cid})
    assert rig.hidden == [cid] and rig.bridge.answer(cid, "ok") is False


# -- Una linea passata: chi scrive è l'utente, dall'altro telefono ------------


def _line_ctx(peer: str = PEER) -> RequestContext:
    return RequestContext(channel="websocket", chat_id="default", session_key="unified:default",
                          metadata={"_line_peer": peer, "_line_lid": "d" * 32})


async def test_a_question_in_a_line_turn_travels_to_the_phone_the_user_holds(rig) -> None:
    # Per una linea non serve ``phoneConfirm``: basta che la linea sia permessa.
    rig.link.peer.phone_confirm = False
    rig.link.peer.line = True
    task, cid = await _ask(rig, origin="app", ctx=_line_ctx())
    peer, kind, body = rig.link.sent[0]
    assert (peer, kind) == (PEER, KIND_PHONE_ASK) and body["cid"] == cid
    await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": cid, "choice": "ok"})
    assert await task == "ok"


async def test_a_line_that_is_no_longer_allowed_carries_no_answer(rig) -> None:
    rig.link.peer.line = False
    task, cid = await _ask(rig, origin="app", ctx=_line_ctx())
    assert rig.link.sent == []
    await rig.link.handlers[KIND_PHONE_REPLY](PEER, {"cid": cid, "choice": "ok"})
    assert not task.done()
    rig.asks.resolve(cid, "no")
    assert await task == "no"


async def test_the_question_is_also_drawn_in_the_line_thread(rig) -> None:
    drawn: list[tuple[str, str, Any]] = []
    hidden: list[tuple[str, str]] = []

    async def show(peer_id: str, cid: str, ask: dict[str, Any]) -> None:
        drawn.append((peer_id, cid, ask))

    async def hide(peer_id: str, cid: str) -> None:
        hidden.append((peer_id, cid))

    rig.bridge.line_show, rig.bridge.line_hide = show, hide
    cid = "e" * 32
    await rig.link.handlers[KIND_PHONE_ASK](PEER, {
        "cid": cid, "kind": "app", "app": "Gmail", "goal": "leggere le email",
        "options": ["ok", "always", "no"],
    })
    await rig.settle()
    assert drawn[0][:2] == (PEER, cid) and drawn[0][2]["app"] == "Gmail"
    assert rig.bridge.answer(cid, "always") is True
    await rig.settle()
    assert hidden == [(PEER, cid)]
    assert rig.link.sent[-1] == (PEER, KIND_PHONE_REPLY, {"cid": cid, "choice": "always"})
