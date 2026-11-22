"""La guida (``jenny/phone/drive.py``): si vede quando comincia, si toglie
quando il turno finisce, e «Ferma» ferma davvero."""

from __future__ import annotations

from typing import Any

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.phone import consent, drive


@pytest.fixture
def phone(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"pill": [], "notice": [], "cleared": [], "stopped": [],
                            "collapsed": 0, "chat": 0, "halted": 0}

    async def pill_driving(ident: str, text: str) -> bool:
        seen["pill"].append(("driving", text))
        return True

    async def pill_done(text: str | None) -> bool:
        seen["pill"].append(("done", text))
        return True

    async def open_chat() -> bool:
        seen["chat"] += 1
        return True

    async def halt() -> bool:
        seen["halted"] += 1
        return True

    async def collapse() -> bool:
        seen["collapsed"] += 1
        return True

    async def stopper(d: drive.Drive) -> None:
        seen["stopped"].append((d.channel, d.chat_id))

    from jenny.runtime import floating

    monkeypatch.setattr(drive.runtime, "pill_driving", pill_driving)
    monkeypatch.setattr(drive.runtime, "pill_done", pill_done)
    monkeypatch.setattr(drive.runtime, "open_chat", open_chat)
    monkeypatch.setattr(drive.runtime, "stop", halt)
    monkeypatch.setattr(floating, "collapse", collapse)
    drive.install(
        stopper=stopper,
        notice=lambda ident, text: seen["notice"].append(text),
        clear=seen["cleared"].append,
    )
    yield seen
    drive.install(stopper=None, notice=None, clear=None)
    consent.install(None, None)


def _ctx(channel: str = "websocket", turn: str = "t1") -> RequestContext:
    return RequestContext(channel=channel, chat_id="c1", session_key="unified:default",
                          turn_id=turn)


async def test_the_first_step_shows_it_and_later_ones_only_update(phone) -> None:
    first = await drive.begin(_ctx(), "apro l'orologio")
    second = await drive.begin(_ctx(), "metto la sveglia")
    assert first is second
    assert phone["collapsed"] == 1 and phone["notice"] == ["apro l'orologio"]
    assert phone["pill"] == [("driving", "apro l'orologio"), ("driving", "metto la sveglia")]


async def test_from_the_app_chat_the_end_brings_the_chat_back(phone) -> None:
    d = await drive.begin(_ctx("websocket"), "x")
    await drive.end_turn(_ctx("websocket"))
    assert phone["cleared"] == [d.ident] and phone["chat"] == 1
    assert phone["pill"][-1] == ("done", None) and drive.active() == []


async def test_from_the_mascot_the_answer_lands_on_the_pill_once(phone) -> None:
    await drive.begin(_ctx("floating"), "x")
    await drive.end_turn(_ctx("floating"))
    assert phone["chat"] == 0
    assert await drive.show_result("Fatto:\nsveglia alle 7") is True
    assert phone["pill"][-1] == ("done", "Fatto: sveglia alle 7")
    assert await drive.show_result("di nuovo") is False


async def test_a_turn_that_never_drove_ends_quietly(phone) -> None:
    await drive.end_turn(_ctx())
    assert phone["pill"] == [] and phone["cleared"] == [] and phone["chat"] == 0
    assert await drive.show_result("niente") is False


async def test_stop_halts_the_hands_denies_questions_and_stops_the_turn(phone) -> None:
    asks = consent.PhoneAsks()
    consent.install(None, asks)
    d = await drive.begin(_ctx(), "x")
    assert await drive.stop("0" * 32) is False
    assert await drive.stop(d.ident) is True
    assert phone["halted"] == 1 and phone["stopped"] == [("websocket", "c1")]
