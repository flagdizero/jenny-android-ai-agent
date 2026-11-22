"""Il cancello del consenso alle mani di Jenny (``jenny/phone/consent.py``).

La tabella del piano, una riga per test: spento, da confermare, app su
«Sempre», yolo, tasto che costa (anche in yolo), password, nessuna risposta,
nessuno davanti.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jenny.agent.tools.context import (
    RequestContext,
    bind_request_context,
)
from jenny.phone import consent


class _Settings:
    def __init__(self, mode: str = "confirm", trusted: set[str] | None = None) -> None:
        self._mode = mode
        self._trusted = set(trusted or ())

    def mode(self) -> str:
        return self._mode

    def trusted(self) -> set[str]:
        return set(self._trusted)

    async def trust(self, target: str) -> None:
        self._trusted.add(target)


@pytest.fixture
def phone(monkeypatch: pytest.MonkeyPatch):
    """Un telefono finto: servizio acceso, WhatsApp davanti, due tasti letti."""
    rt = consent.runtime

    async def state() -> str:
        return "on"

    async def foreground() -> str:
        return "com.whatsapp"

    async def resolve(app: str) -> str | None:
        return {"whatsapp": "com.whatsapp", "clock": "com.clock"}.get(app.lower())

    async def label(package: str) -> str:
        return {"com.whatsapp": "WhatsApp", "com.clock": "Clock"}.get(package, package)

    async def screen() -> dict[str, bool]:
        return {"on": True, "locked": False, "secure": False, "deviceLocked": False}

    monkeypatch.setattr(rt, "screen_state", screen)
    index = {"1:e1": ("button", "Send"), "1:e2": ("button", "Paga ora"),
             "1:e3": ("password", "")}
    monkeypatch.setattr(rt, "phone_state", state)
    monkeypatch.setattr(rt, "foreground", foreground)
    monkeypatch.setattr(rt, "resolve_app", resolve)
    monkeypatch.setattr(rt, "app_label", label)
    monkeypatch.setattr(rt, "ref_name", lambda ref: index.get(ref))
    monkeypatch.setattr(rt, "last_snapshot", lambda: None)
    settings = _Settings()
    asks = consent.PhoneAsks(ttl_s=5)
    consent.install(settings, asks)
    yield settings, asks
    consent.install(None, None)


@pytest.fixture
def turn():
    """Lega un turno: ``make(session_key, channel, turn_id)``. Il legame vive
    nel contesto del test che lo chiama e muore con lui: niente da disfare."""
    def make(session_key: str = "unified:default", channel: str = "websocket",
             turn_id: str = "t1") -> None:
        bind_request_context(RequestContext(
            channel=channel, chat_id="default", session_key=session_key, turn_id=turn_id,
        ))

    return make


def _answer(asks: consent.PhoneAsks, choice: str, seen: list[dict[str, Any]]) -> None:
    """Risponde *choice* a ogni domanda appena compare, e la ricorda."""
    def poster(ask: consent.Ask) -> None:
        seen.append(ask.public())
        asyncio.get_running_loop().call_soon(asks.resolve, ask.cid, choice)

    asks.posters.append(poster)


TAP = {"goal": "rispondere a Marco", "steps": [{"action": "tap", "ref": "1:e1"}]}


async def test_other_tools_pass_untouched(phone, turn) -> None:
    turn()
    assert await consent.before_tool("phone_snapshot", {}) is None
    assert await consent.before_tool("web_fetch", {"url": "https://x"}) is None


async def test_a_reminder_reads_only_the_screen_of_a_trusted_app(phone, turn) -> None:
    settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn(session_key="cron:abc")
    refusal = await consent.before_tool("phone_snapshot", {})
    assert refusal is not None and "WhatsApp" in refusal and seen == []
    settings._trusted = {"com.whatsapp"}
    assert await consent.before_tool("phone_snapshot", {}) is None


async def test_mode_off_refuses_and_asks_nothing(phone, turn) -> None:
    settings, asks = phone
    settings._mode = "off"
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    refusal = await consent.before_tool("phone_do", TAP)
    assert refusal is not None and "turned off" in refusal
    assert seen == []


async def test_service_off_says_where_to_turn_it_on(phone, turn, monkeypatch) -> None:
    async def off() -> str:
        return "off"

    monkeypatch.setattr(consent.runtime, "phone_state", off)
    turn()
    refusal = await consent.before_tool("phone_do", TAP)
    assert refusal is not None and "Accessibility" in refusal


async def test_a_pin_locked_phone_is_never_unlocked(phone, turn, monkeypatch) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)

    async def locked() -> dict[str, bool]:
        return {"on": False, "locked": True, "secure": True, "deviceLocked": True}

    monkeypatch.setattr(consent.runtime, "screen_state", locked)
    turn()
    refusal = await consent.before_tool("phone_do", TAP)
    assert refusal == consent.LOCKED and seen == []


async def test_confirm_asks_once_per_app_per_turn(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    assert await consent.before_tool("phone_do", TAP) is None
    assert await consent.before_tool("phone_do", TAP) is None
    assert len(seen) == 1
    assert seen[0]["app"] == "WhatsApp" and seen[0]["goal"] == "rispondere a Marco"
    assert seen[0]["options"] == ["ok", "always", "no"]


async def test_ok_does_not_carry_to_the_next_turn(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn(turn_id="t1")
    assert await consent.before_tool("phone_do", TAP) is None
    turn(turn_id="t2")
    assert await consent.before_tool("phone_do", TAP) is None
    assert len(seen) == 2


async def test_always_trusts_the_app_for_good(phone, turn) -> None:
    settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "always", seen)
    turn(turn_id="t1")
    assert await consent.before_tool("phone_do", TAP) is None
    assert settings.trusted() == {"com.whatsapp"}
    turn(turn_id="t2")
    assert await consent.before_tool("phone_do", TAP) is None
    assert len(seen) == 1


async def test_no_is_remembered_for_the_turn(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "no", seen)
    turn()
    first = await consent.before_tool("phone_do", TAP)
    second = await consent.before_tool("phone_do", TAP)
    assert first is not None and "said no" in first
    assert second is not None and len(seen) == 1


async def test_no_answer_counts_as_no(phone, turn, monkeypatch) -> None:
    _settings, asks = phone
    asks._ttl_s = 0.0
    turn()
    refusal = await consent.before_tool("phone_do", TAP)
    assert refusal is not None and "did not answer" in refusal
    assert asks.pending() == []


async def test_free_mode_asks_nothing_for_an_ordinary_tap(phone, turn) -> None:
    settings, asks = phone
    settings._mode = "free"
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    assert await consent.before_tool("phone_do", TAP) is None
    assert seen == []


@pytest.mark.parametrize("mode,trusted", [("free", set()), ("confirm", {"com.whatsapp"})])
async def test_a_costly_tap_always_asks(phone, turn, mode, trusted) -> None:
    settings, asks = phone
    settings._mode, settings._trusted = mode, set(trusted)
    seen: list[dict[str, Any]] = []
    _answer(asks, "no", seen)
    turn()
    refusal = await consent.before_tool(
        "phone_do", {"goal": "pagare", "steps": [{"action": "tap", "ref": "1:e2"}]},
    )
    assert refusal is not None and "Paga ora" in refusal
    assert [a["kind"] for a in seen] == ["costly"]
    assert seen[0]["options"] == ["ok", "no"]


async def test_a_costly_tap_goes_through_on_ok(phone, turn) -> None:
    settings, asks = phone
    settings._mode = "free"
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    assert await consent.before_tool(
        "phone_do", {"goal": "pagare", "steps": [{"action": "tap", "ref": "1:e2"}]},
    ) is None
    assert seen[0]["what"] == "Paga ora"


async def test_always_is_not_an_answer_to_a_costly_question(phone) -> None:
    _settings, asks = phone
    task = asyncio.ensure_future(asks.ask(
        kind="costly", target="", app="Shop", goal="", what="Paga", origin="app",
        options=("ok", "no"), ctx=None,
    ))
    await asyncio.sleep(0)
    cid = asks.pending()[0]["cid"]
    assert asks.resolve(cid, "always") is False
    assert asks.resolve(cid, "ok") is True
    assert await task == "ok"
    assert asks.resolve(cid, "ok") is False


async def test_passwords_are_never_typed(phone, turn) -> None:
    settings, asks = phone
    settings._mode = "free"
    turn()
    refusal = await consent.before_tool(
        "phone_do", {"goal": "", "steps": [{"action": "type", "ref": "1:e3", "text": "x"}]},
    )
    assert refusal is not None and "password" in refusal


async def test_open_app_asks_for_the_app_it_opens(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    steps = [{"action": "open_app", "app": "Clock"}, {"action": "tap", "ref": "1:e1"}]
    assert await consent.before_tool("phone_do", {"goal": "sveglia", "steps": steps}) is None
    assert [a["app"] for a in seen] == ["Clock"]


async def test_a_ref_belongs_to_the_app_that_was_read_not_to_the_one_in_front(
    phone, turn, monkeypatch,
) -> None:
    """Fra un turno e l'altro davanti torna la chat: il tocco è sull'app letta."""
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "no", seen)

    async def jenny_in_front() -> str | None:
        return None

    monkeypatch.setattr(consent.runtime, "foreground", jenny_in_front)
    monkeypatch.setattr(consent.runtime, "last_snapshot", lambda: {"package": "com.clock"})
    turn()
    await consent.before_tool(
        "phone_do", {"goal": "", "steps": [{"action": "tap", "ref": "1:e2"}]},
    )
    assert seen[0]["app"] == "Clock"


async def test_back_is_an_action_in_the_app_in_front(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    assert await consent.before_tool("phone_do", {"goal": "", "steps": [{"action": "back"}]}) is None
    assert [a["app"] for a in seen] == ["WhatsApp"]


async def test_home_and_recents_ask_nothing(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "no", seen)
    turn()
    steps = [{"action": "home"}, {"action": "recents"}, {"action": "notifications"}]
    assert await consent.before_tool("phone_do", {"goal": "", "steps": steps}) is None
    assert seen == []


async def test_open_link_asks_for_its_host(phone, turn) -> None:
    _settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn()
    steps = [{"action": "open_link", "url": "https://maps.example.org/x?y"}]
    assert await consent.before_tool("phone_do", {"goal": "", "steps": steps}) is None
    assert [a["app"] for a in seen] == ["maps.example.org"]


async def test_a_reminder_uses_only_trusted_apps(phone, turn) -> None:
    settings, asks = phone
    seen: list[dict[str, Any]] = []
    _answer(asks, "ok", seen)
    turn(session_key="cron:abc")
    refusal = await consent.before_tool("phone_do", TAP)
    assert refusal is not None and "No one is there" in refusal and seen == []
    settings._trusted = {"com.whatsapp"}
    assert await consent.before_tool("phone_do", TAP) is None


async def test_a_reminder_never_taps_a_costly_button(phone, turn) -> None:
    settings, asks = phone
    settings._mode = "free"
    turn(session_key="cron:abc")
    refusal = await consent.before_tool(
        "phone_do", {"goal": "", "steps": [{"action": "tap", "ref": "1:e2"}]},
    )
    assert refusal is not None and "no one is there" in refusal


async def test_stop_turns_every_question_into_a_no(phone, turn) -> None:
    _settings, asks = phone
    turn()
    task = asyncio.ensure_future(consent.before_tool("phone_do", TAP))
    await asyncio.sleep(0.01)
    assert asks.deny_all() == 1
    refusal = await task
    assert refusal is not None and "said no" in refusal


@pytest.mark.parametrize("session_key,channel,expected", [
    ("unified:default", "websocket", "app"),
    ("unified:default", "floating", "floating"),
    ("unified:default", "telegram", "telegram"),
    ("unified:default", "notification", "notification"),
    ("unified:default", "wear", "wear"),
    ("cron:abc", "websocket", "cron"),
])
def test_origin(session_key: str, channel: str, expected: str) -> None:
    ctx = RequestContext(channel=channel, chat_id="default", session_key=session_key)
    assert consent.origin_of(ctx) == expected


def test_a_reminder_in_the_personal_chat_is_still_a_reminder() -> None:
    """Il giro di un promemoria personale gira in ``unified:default``: lo si
    riconosce dal segno sul turno, che deve restare quello di ``jenny.cron.session_turns``."""
    from jenny.cron.session_turns import CRON_TRIGGER_META

    assert consent._CRON_TRIGGER_META == CRON_TRIGGER_META
    ctx = RequestContext(channel="websocket", chat_id="default",
                         session_key="unified:default", metadata={CRON_TRIGGER_META: {}})
    assert consent.origin_of(ctx) == "cron"
