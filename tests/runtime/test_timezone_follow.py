"""Kotlin dice che il fuso è cambiato: la chiamata arriva sul loop del gateway."""

from __future__ import annotations

import asyncio
import threading

import pytest

from jenny.runtime import timezone_follow


@pytest.fixture(autouse=True)
def _unbound():
    timezone_follow.reset_timezone_follow()
    yield
    timezone_follow.reset_timezone_follow()


def test_without_a_gateway_nothing_is_delivered() -> None:
    assert timezone_follow.on_timezone_changed("Asia/Tokyo") is False


async def test_the_change_reaches_the_loop_from_another_thread() -> None:
    seen: list[tuple[str | None, int]] = []
    arrived = asyncio.Event()

    def on_change(tz_id: str | None) -> None:
        seen.append((tz_id, threading.get_ident()))
        arrived.set()

    timezone_follow.bind(on_change)
    delivered: list[bool] = []
    worker = threading.Thread(
        target=lambda: delivered.append(timezone_follow.on_timezone_changed(" Asia/Tokyo ")))
    worker.start()
    await asyncio.wait_for(arrived.wait(), timeout=5)
    worker.join()

    assert delivered == [True]
    assert seen == [("Asia/Tokyo", threading.get_ident())]


async def test_a_change_without_a_name_asks_the_receiver_to_read_it() -> None:
    seen: list[str | None] = []
    arrived = asyncio.Event()

    def on_change(tz_id: str | None) -> None:
        seen.append(tz_id)
        arrived.set()

    timezone_follow.bind(on_change)

    assert timezone_follow.on_timezone_changed(None) is True
    await asyncio.wait_for(arrived.wait(), timeout=5)
    assert seen == [None]


def test_the_gateway_hands_the_new_timezone_to_whoever_keeps_one(monkeypatch) -> None:
    from types import SimpleNamespace

    from jenny.runtime.container import GatewayContainer
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    monkeypatch.setattr(ctx, "device_timezone", "Europe/Rome")
    monkeypatch.setattr(
        "jenny.config.loader.load_config",
        lambda: SimpleNamespace(agents=SimpleNamespace(
            defaults=SimpleNamespace(timezone=ctx.device_timezone))),
    )
    followed: list[str] = []
    agent = SimpleNamespace(context=SimpleNamespace(timezone="Europe/Rome"),
                            subagents=SimpleNamespace(timezone="Europe/Rome"))
    gateway = SimpleNamespace(
        _agent=agent,
        cron=SimpleNamespace(follow_timezone=lambda name: followed.append(name) or 2),
    )

    GatewayContainer.follow_timezone(gateway, "Asia/Tokyo")  # type: ignore[arg-type]

    assert ctx.device_timezone == "Asia/Tokyo"
    assert agent.context.timezone == "Asia/Tokyo"
    assert agent.subagents.timezone == "Asia/Tokyo"
    assert followed == ["Asia/Tokyo"]


def test_a_name_that_is_not_a_timezone_is_read_from_the_phone(monkeypatch) -> None:
    from types import SimpleNamespace

    from jenny.runtime.container import GatewayContainer
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    monkeypatch.setattr(ctx, "device_timezone", "Europe/Rome")
    monkeypatch.setattr("jenny.utils.device_timezone.detect_device_timezone",
                        lambda: "Europe/Lisbon")
    monkeypatch.setattr(
        "jenny.config.loader.load_config",
        lambda: SimpleNamespace(agents=SimpleNamespace(
            defaults=SimpleNamespace(timezone=ctx.device_timezone))),
    )
    followed: list[str] = []
    gateway = SimpleNamespace(
        _agent=None,
        cron=SimpleNamespace(follow_timezone=lambda name: followed.append(name) or 0),
    )

    GatewayContainer.follow_timezone(gateway, "not/a-zone")  # type: ignore[arg-type]

    assert followed == ["Europe/Lisbon"]
