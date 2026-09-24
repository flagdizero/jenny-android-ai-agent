"""Test per jenny/runtime/power.py (wakelock e risvegli, solo Android).

Il bridge Chaquopy non esiste nei test desktop: si verificano il degrado a
no-op senza contesto Android, il refcount annidato e il gating sulla modalità
con un bridge finto, iniettato come in ``tests/runtime/test_location.py``
(monkeypatch di ``get_android_context`` e ``_get_bridge``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jenny.config.schema import PowerConfig
from jenny.runtime import power


@pytest.fixture(autouse=True)
def _reset_state():
    """Ogni test parte da stato di modulo pulito."""
    power.reset_power_state()
    yield
    power.reset_power_state()


class _FakeBridge:
    """Controparte Python del ``PowerBridge`` Kotlin."""

    def __init__(self, *, ok: bool = True, boom: bool = False) -> None:
        self._ok = ok
        self._boom = boom
        self.acquired: list[tuple[str, int]] = []
        self.released: list[str] = []
        self.scheduled: list[tuple[int, int]] = []
        self.cancelled: list[int] = []
        self.service_lock: list[tuple[bool, int]] = []
        self.watchdog: list[tuple[bool, int]] = []
        self.alarm_clock: list[bool] = []

    def _result(self) -> bool:
        if self._boom:
            raise RuntimeError("bridge exploded")
        return self._ok

    def acquire(self, tag: str, timeout_ms: int) -> bool:
        self.acquired.append((tag, timeout_ms))
        return self._result()

    def release(self, tag: str) -> bool:
        self.released.append(tag)
        return self._result()

    def isHeld(self, tag: str) -> bool:  # noqa: N802
        return self._result()

    def isBatteryExempt(self) -> bool:  # noqa: N802
        return self._result()

    def scheduleWake(self, at_ms: int, request_code: int) -> bool:  # noqa: N802
        self.scheduled.append((at_ms, request_code))
        return self._result()

    def cancelWake(self, request_code: int) -> bool:  # noqa: N802
        self.cancelled.append(request_code)
        return self._result()

    def canScheduleExactAlarms(self) -> bool:  # noqa: N802
        return self._result()

    def setServiceLock(self, enabled: bool, rotate_min: int) -> bool:  # noqa: N802
        self.service_lock.append((enabled, rotate_min))
        return self._result()

    def setWatchdog(self, enabled: bool, interval_min: int) -> bool:  # noqa: N802
        self.watchdog.append((enabled, interval_min))
        return self._result()

    def setAlarmClockFallback(self, enabled: bool) -> bool:  # noqa: N802
        self.alarm_clock.append(enabled)
        return self._result()


def _install_bridge(monkeypatch: pytest.MonkeyPatch, bridge: _FakeBridge) -> _FakeBridge:
    monkeypatch.setattr(power, "get_android_context", lambda: object())

    async def fake_get_bridge(ctx: Any) -> Any:
        return bridge

    monkeypatch.setattr(power, "_get_bridge", fake_get_bridge)
    return bridge


def _install_mode(
    monkeypatch: pytest.MonkeyPatch, mode: str, *, rotate_min: int = 50
) -> None:
    """Modalità letta dal vero percorso di config (``load_config().power``)."""

    class _Cfg:
        power = PowerConfig(keep_awake=mode, wakelock_rotate_min=rotate_min)

    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: _Cfg())


def _install_power_config(monkeypatch: pytest.MonkeyPatch, **fields: Any) -> None:
    """Come ``_install_mode``, ma per i campi che non riguardano il wakelock."""

    class _Cfg:
        power = PowerConfig(**fields)

    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: _Cfg())


class TestWithoutAndroid:
    """Su desktop/CI: tutto no-op, niente eccezioni, nessun default sorprendente."""

    async def test_keep_awake_is_a_usable_no_op(self, monkeypatch):
        monkeypatch.setattr(power, "get_android_context", lambda: None)
        async with power.keep_awake("turn") as held:
            assert held is False
        assert power._REFCOUNTS == {}
        assert power._HELD == set()

    async def test_keep_awake_no_op_does_not_read_config(self, monkeypatch):
        # Fuori da Android non si deve nemmeno arrivare a caricare il config.
        monkeypatch.setattr(power, "get_android_context", lambda: None)

        def _boom(*a: Any, **k: Any) -> Any:
            raise AssertionError("config should not be read without an Android context")

        monkeypatch.setattr("jenny.config.loader.load_config", _boom)
        async with power.keep_awake("turn"):
            pass

    async def test_keep_awake_still_releases_on_exception(self, monkeypatch):
        monkeypatch.setattr(power, "get_android_context", lambda: None)
        with pytest.raises(ValueError):
            async with power.keep_awake("turn"):
                raise ValueError("boom")
        assert power._REFCOUNTS == {}

    async def test_accessors_return_safe_defaults(self, monkeypatch):
        monkeypatch.setattr(power, "get_android_context", lambda: None)
        assert await power.is_battery_exempt() is False
        assert await power.can_schedule_exact_alarms() is False
        assert await power.schedule_wake(1_700_000_000_000, 7) is False
        assert await power.cancel_wake(7) is False


class TestKeepAwakeModes:
    async def test_turns_acquires_and_releases(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("turn", timeout_s=30) as held:
            assert held is True
            assert bridge.acquired == [("turn", 30_000)]
            assert bridge.released == []

        assert bridge.released == ["turn"]
        assert power._REFCOUNTS == {}
        assert power._HELD == set()

    @pytest.mark.parametrize("mode", ["off", "always"])
    async def test_off_and_always_skip_the_per_turn_lock(self, monkeypatch, mode):
        # "off" = nessun lock; "always" = già coperto dal lock di servizio.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, mode)

        async with power.keep_awake("turn") as held:
            assert held is False

        assert bridge.acquired == []
        assert bridge.released == []

    async def test_unreadable_config_falls_back_to_turns(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())

        def _boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("no config on this device")

        monkeypatch.setattr("jenny.config.loader.load_config", _boom)

        async with power.keep_awake("turn"):
            pass

        assert bridge.acquired and bridge.released == ["turn"]

    async def test_timeout_is_always_passed_and_capped(self, monkeypatch):
        # Il timeout duro è ciò che impedisce un wakelock eterno se Python muore.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("huge", timeout_s=99_999):
            pass

        assert bridge.acquired == [("huge", int(power._MAX_TIMEOUT_S * 1000))]

    async def test_default_timeout_applies(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("turn"):
            pass

        assert bridge.acquired == [("turn", int(power._DEFAULT_TIMEOUT_S * 1000))]


class TestRefcount:
    async def test_nested_blocks_acquire_once_and_release_once(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("turn"):
            async with power.keep_awake("turn"):
                async with power.keep_awake("turn"):
                    assert power._REFCOUNTS["turn"] == 3
                # uscita del blocco più interno: il lock deve restare
                assert bridge.released == []
            assert bridge.released == []

        assert len(bridge.acquired) == 1
        assert bridge.released == ["turn"]
        assert power._REFCOUNTS == {}

    async def test_distinct_tags_are_independent(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("agent"):
            async with power.keep_awake("ssh"):
                assert power._HELD == {"agent", "ssh"}
            assert power._HELD == {"agent"}

        assert [tag for tag, _ in bridge.acquired] == ["agent", "ssh"]
        assert bridge.released == ["ssh", "agent"]


class TestReleaseOnFailure:
    """Il rilascio è in ``finally``: nessuna uscita dal blocco lo salta."""

    async def test_an_exception_inside_the_block_still_releases(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        with pytest.raises(ValueError):
            async with power.keep_awake("turn"):
                raise ValueError("boom")

        assert bridge.released == ["turn"]
        assert power._REFCOUNTS == {}
        assert power._HELD == set()

    async def test_an_exception_inside_a_nested_block_releases_once_at_the_top(
        self, monkeypatch
    ):
        # Il caso reale: un tool esplode dentro un turno. Il livello interno
        # scende di uno, quello esterno rilascia — una acquire, una release.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        with pytest.raises(ValueError):
            async with power.keep_awake("turn"):
                async with power.keep_awake("turn"):
                    raise ValueError("boom")

        assert len(bridge.acquired) == 1
        assert bridge.released == ["turn"]
        assert power._REFCOUNTS == {}

    async def test_cancellation_inside_the_block_still_releases(self, monkeypatch):
        # /stop cancella il task del turno: il wakelock non deve sopravvivergli.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        with pytest.raises(asyncio.CancelledError):
            async with power.keep_awake("turn"):
                raise asyncio.CancelledError()

        assert bridge.released == ["turn"]
        assert power._REFCOUNTS == {}


class TestApplyServiceLock:
    """Il lock di servizio: la modalità la decide Python, la tiene Kotlin."""

    async def test_always_turns_the_service_lock_on_with_the_rotation_period(
        self, monkeypatch
    ):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "always", rotate_min=45)

        assert await power.apply_service_lock() is True
        assert bridge.service_lock == [(True, 45)]

    @pytest.mark.parametrize("mode", ["turns", "off"])
    async def test_other_modes_turn_it_off(self, monkeypatch, mode):
        # Spegnere esplicitamente, non "non accendere": un passaggio da always a
        # turns deve smontare il lock, non lasciarne uno che nessuno ricorda.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, mode)

        await power.apply_service_lock()

        assert bridge.service_lock == [(False, 50)]

    async def test_rotation_disabled_is_passed_through_as_zero(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "always", rotate_min=0)

        await power.apply_service_lock()

        assert bridge.service_lock == [(True, 0)]

    async def test_without_android_it_is_a_silent_no_op(self, monkeypatch):
        monkeypatch.setattr(power, "get_android_context", lambda: None)
        assert await power.apply_service_lock() is False

    async def test_an_exploding_bridge_never_reaches_the_caller(self, monkeypatch):
        _install_bridge(monkeypatch, _FakeBridge(boom=True))
        _install_mode(monkeypatch, "always")
        assert await power.apply_service_lock() is False


class TestApplyWatchdogConfig:
    """Il watchdog: le impostazioni le legge Python, la catena la tiene Kotlin."""

    async def test_enabled_config_is_pushed_with_its_interval(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_power_config(monkeypatch, watchdog_enabled=True, watchdog_interval_min=20)

        assert await power.apply_watchdog_config() is True
        assert bridge.watchdog == [(True, 20)]

    async def test_disabled_config_is_pushed_too(self, monkeypatch):
        # Non "non chiamare": le sveglie vivono nell'AlarmManager di sistema e
        # sopravvivono al riavvio del gateway. Solo un push esplicito di False
        # smonta una catena armata da un avvio precedente.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_power_config(monkeypatch, watchdog_enabled=False, watchdog_interval_min=15)

        await power.apply_watchdog_config()

        assert bridge.watchdog == [(False, 15)]

    async def test_absurd_interval_falls_back_to_the_default(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())

        class _Cfg:
            class power:  # noqa: N801 - config finta, non uno schema
                watchdog_enabled = True
                watchdog_interval_min = "presto"

        monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: _Cfg())

        await power.apply_watchdog_config()

        assert bridge.watchdog == [(True, 15)]

    async def test_without_android_it_is_a_silent_no_op(self, monkeypatch):
        monkeypatch.setattr(power, "get_android_context", lambda: None)
        assert await power.apply_watchdog_config() is False

    async def test_an_exploding_bridge_never_reaches_the_caller(self, monkeypatch):
        _install_bridge(monkeypatch, _FakeBridge(boom=True))
        _install_power_config(monkeypatch, watchdog_enabled=True)
        assert await power.apply_watchdog_config() is False


class TestApplyAlarmClockConfig:
    """L'ultima rete: il flag lo legge Python, la sveglia la tiene Kotlin."""

    async def test_enabled_config_is_pushed(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_power_config(monkeypatch, alarm_clock_fallback=True)

        assert await power.apply_alarm_clock_config() is True
        assert bridge.alarm_clock == [True]

    async def test_disabled_config_is_pushed_too(self, monkeypatch):
        # Non "non chiamare": una sveglia già in coda vive nell'AlarmManager di
        # sistema e continuerebbe a mostrare l'icona nella barra di stato fino
        # allo scatto successivo. Solo un push esplicito di False la cancella.
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_power_config(monkeypatch, alarm_clock_fallback=False)

        await power.apply_alarm_clock_config()

        assert bridge.alarm_clock == [False]

    async def test_default_is_on_when_the_field_is_missing(self, monkeypatch):
        # Config di una versione precedente dello schema: la rete resta accesa,
        # allineata al default di PowerConfig e a quello di AlarmClockFallback.
        bridge = _install_bridge(monkeypatch, _FakeBridge())

        class _Cfg:
            class power:  # noqa: N801 - config finta, non uno schema
                keep_awake = "turns"

        monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: _Cfg())

        await power.apply_alarm_clock_config()

        assert bridge.alarm_clock == [True]

    async def test_without_android_it_is_a_silent_no_op(self, monkeypatch):
        monkeypatch.setattr(power, "get_android_context", lambda: None)
        assert await power.apply_alarm_clock_config() is False

    async def test_an_exploding_bridge_never_reaches_the_caller(self, monkeypatch):
        _install_bridge(monkeypatch, _FakeBridge(boom=True))
        _install_power_config(monkeypatch, alarm_clock_fallback=True)
        assert await power.apply_alarm_clock_config() is False


class TestWakeTick:
    """Handoff Kotlin -> loop asyncio: l'unico punto in cui un thread esterno
    tocca lo stato del gateway."""

    async def test_tick_wakes_the_bound_event(self):
        event = power.bind_wake_loop()
        assert event.is_set() is False

        assert power.on_wake_tick() is True

        # ``call_soon_threadsafe`` accoda: l'evento si accende al giro dopo.
        await asyncio.sleep(0)
        assert event.is_set() is True

    async def test_tick_from_another_thread_reaches_the_loop(self):
        event = power.bind_wake_loop()

        # Il chiamante vero è un thread di lavoro Chaquopy, non il loop.
        assert await asyncio.to_thread(power.on_wake_tick) is True

        await asyncio.wait_for(event.wait(), timeout=1.0)

    async def test_tick_without_a_bound_loop_is_dropped_not_raised(self):
        # Sveglia arrivata mentre il gateway sta ancora partendo: il tick si
        # perde di proposito e il recupero tocca a CronService.start.
        assert power.on_wake_tick() is False

    async def test_rebinding_replaces_the_previous_event(self):
        stale = power.bind_wake_loop()
        fresh = power.bind_wake_loop()

        power.on_wake_tick()
        await asyncio.sleep(0)

        assert fresh.is_set() is True
        assert stale.is_set() is False

    async def test_reset_unbinds_the_loop(self):
        power.bind_wake_loop()
        power.reset_power_state()

        assert power.on_wake_tick() is False


class TestBridgeFailures:
    async def test_exploding_bridge_never_reaches_the_caller(self, monkeypatch):
        _install_bridge(monkeypatch, _FakeBridge(boom=True))
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("turn") as held:
            assert held is False  # acquire fallita: non fingiamo di tenerlo

        assert power._REFCOUNTS == {}
        assert await power.schedule_wake(1, 2) is False

    async def test_refused_acquire_is_not_reported_as_held(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge(ok=False))
        _install_mode(monkeypatch, "turns")

        async with power.keep_awake("turn") as held:
            assert held is False

        # Nessuna release inventata per un lock che l'OS non ci ha dato.
        assert bridge.acquired and bridge.released == []


class TestAccessorsWithBridge:
    async def test_accessors_forward_to_the_bridge(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())

        assert await power.is_battery_exempt() is True
        assert await power.can_schedule_exact_alarms() is True
        assert await power.schedule_wake(1_700_000_000_000, 7) is True
        assert await power.cancel_wake(7) is True
        assert bridge.scheduled == [(1_700_000_000_000, 7)]
        assert bridge.cancelled == [7]


class TestResetPowerState:
    async def test_reset_clears_refcounts_bridge_and_held_tags(self, monkeypatch):
        bridge = _install_bridge(monkeypatch, _FakeBridge())
        _install_mode(monkeypatch, "turns")

        cm = power.keep_awake("turn")
        await cm.__aenter__()
        assert power._REFCOUNTS == {"turn": 1}
        assert power._HELD == {"turn"}
        power._BRIDGE.instance = bridge

        power.reset_power_state()

        assert power._REFCOUNTS == {}
        assert power._HELD == set()
        assert power._BRIDGE.instance is None

        # L'uscita del blocco superstite non deve sollevare né inventare release.
        await cm.__aexit__(None, None, None)
        assert bridge.released == []
