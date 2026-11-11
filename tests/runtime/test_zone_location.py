"""La lettura di posizione della sentinella delle zone (``runtime/location.py``).

Diversa da quella del tool: mai il last-known al posto di una lettura fallita,
e la domanda «a schermo spento si vede?» al bridge.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.runtime import location


@pytest.fixture(autouse=True)
def _reset_state():
    location.reset_location_state()
    yield
    location.reset_location_state()


class _Bridge:
    def __init__(self, fresh: str | None = None, state: Any = "ok") -> None:
        self.fresh = fresh
        self.state = state
        self.calls: list[tuple[bool, int]] = []

    def getFreshFrom(self, low_power: bool, timeout_ms: int) -> str | None:  # noqa: N802
        self.calls.append((low_power, timeout_ms))
        return self.fresh

    def backgroundState(self) -> Any:  # noqa: N802
        return self.state


def _on_phone(monkeypatch, bridge: _Bridge) -> None:
    monkeypatch.setattr(location, "get_android_context", lambda: object())

    async def fake_get_bridge(ctx: Any) -> Any:
        return bridge

    monkeypatch.setattr(location, "_get_bridge", fake_get_bridge)


async def test_a_fresh_read_returns_the_fix_and_leaves_the_cache_alone(monkeypatch) -> None:
    bridge = _Bridge(fresh="45.0;9.0;30;1700000000000;network")
    _on_phone(monkeypatch, bridge)

    fix = await location.read_fresh_fix(low_power=True, timeout_s=20)

    assert fix is not None and fix.accuracy_m == 30 and fix.source == "network"
    assert bridge.calls == [(True, 20_000)]
    assert location._CURRENT is None


async def test_a_failed_read_is_none_not_an_old_position(monkeypatch) -> None:
    _on_phone(monkeypatch, _Bridge(fresh=None))
    assert await location.read_fresh_fix(low_power=False, timeout_s=5) is None


async def test_off_the_phone_nothing_is_read(monkeypatch) -> None:
    monkeypatch.setattr(location, "get_android_context", lambda: None)
    assert await location.read_fresh_fix(low_power=True, timeout_s=5) is None
    assert await location.background_state() == "off"


@pytest.mark.parametrize("state", ["ok", "blind", "no_permission"])
async def test_background_state_passes_through(monkeypatch, state) -> None:
    _on_phone(monkeypatch, _Bridge(state=state))
    assert await location.background_state() == state


async def test_an_unknown_background_state_reads_as_off(monkeypatch) -> None:
    _on_phone(monkeypatch, _Bridge(state="weird"))
    assert await location.background_state() == "off"
