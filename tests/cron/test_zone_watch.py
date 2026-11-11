"""La sentinella delle zone (``jenny/cron/zone_watch.py``) con un telefono finto.

Uno scheduler vero su un workspace temporaneo; posizione, sveglie e orologio
finti. Il giro si chiama a mano (``check_once``): il ciclo con le attese ha i
suoi test in fondo.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from jenny.cron import zones
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule
from jenny.cron.zone_watch import ZoneWatcher

_M_PER_DEG = 111_195.0
_LAT, _LNG = 45.0, 9.0


@dataclass(frozen=True)
class _Fix:
    latitude: float
    longitude: float
    accuracy_m: float | None
    time_ms: int


class _Phone:
    """Dove sta il telefono, e cosa rispondono rete e GPS."""

    def __init__(self) -> None:
        self.north_m: float | None = 5_000.0
        self.network_accuracy: float | None = 40.0
        self.gps_ok = True
        self.network_ok = True
        self.background = "ok"
        self.reads: list[str] = []
        self.notices: list[str] = []
        self.wakes: list[int] = []
        self.cancels = 0
        self.clock_ms = int(time.time() * 1000)

    def now(self) -> int:
        return self.clock_ms

    async def read_fix(self, *, low_power: bool, timeout_s: float):
        self.reads.append("network" if low_power else "gps")
        if self.north_m is None:
            return None
        if low_power and not self.network_ok:
            return None
        if not low_power and not self.gps_ok:
            return None
        acc = self.network_accuracy if low_power else 8.0
        return _Fix(_LAT + self.north_m / _M_PER_DEG, _LNG, acc, self.clock_ms)

    async def background_state(self) -> str:
        return self.background

    async def schedule_wake(self, at_ms: int) -> None:
        self.wakes.append(at_ms)

    async def cancel_wake(self) -> None:
        self.cancels += 1


def _bound_chat() -> dict[str, str]:
    return {"session_key": "websocket:chat-1", "origin_channel": "websocket",
            "origin_chat_id": "chat-1"}


@pytest.fixture
def cron(tmp_path) -> CronService:
    async def ring(job):
        return "detto"

    service = CronService(tmp_path / "workspace", on_job=ring)
    service._arm_timer = lambda: None  # type: ignore[method-assign]
    service._running = True
    service._load_store()
    return service


@pytest.fixture
def phone() -> _Phone:
    return _Phone()


def _watcher(cron: CronService, phone: _Phone, *, enabled: bool = True) -> ZoneWatcher:
    return ZoneWatcher(
        cron,
        read_fix=phone.read_fix,
        background_state=phone.background_state,
        enabled=lambda: enabled,
        schedule_wake=phone.schedule_wake,
        cancel_wake=phone.cancel_wake,
        notify_blind=phone.notices.append,
        now_ms=phone.now,
    )


def _arm(cron: CronService, *, every: bool = False, radius: float = 200, name: str = "Stazione"):
    return cron.add_job(
        name=name, message="dimmi il binario", delete_after_run=not every,
        schedule=CronSchedule(kind="zone", lat=_LAT, lng=_LNG, radius_m=radius, zone_name=name),
        **_bound_chat(),
    )


async def test_no_zones_no_reads(cron, phone) -> None:
    watcher = _watcher(cron, phone)
    assert await watcher.check_once() is None
    assert phone.reads == []


async def test_location_off_reads_nothing(cron, phone) -> None:
    _arm(cron)
    watcher = _watcher(cron, phone, enabled=False)
    assert await watcher.check_once() is None
    assert phone.reads == [] and watcher.state == "off"


async def test_far_away_the_network_is_enough(cron, phone) -> None:
    job = _arm(cron)
    watcher = _watcher(cron, phone)
    delay = await watcher.check_once()

    assert phone.reads == ["network"]
    assert cron.get_job(job.id).state.zone_inside is False
    assert delay == pytest.approx((5_000 - 200 - 40) / zones.SPEED_MPS)


async def test_close_but_vague_asks_the_gps(cron, phone) -> None:
    _arm(cron)
    phone.north_m, phone.network_accuracy = 250.0, 300.0
    await _watcher(cron, phone).check_once()
    assert phone.reads == ["network", "gps"]


async def test_no_network_falls_back_to_gps(cron, phone) -> None:
    _arm(cron)
    phone.network_ok = False
    await _watcher(cron, phone).check_once()
    assert phone.reads == ["network", "gps"]


async def test_arriving_fires_once_and_the_timer_runs_it(cron, phone) -> None:
    job = _arm(cron)
    watcher = _watcher(cron, phone)
    await watcher.check_once()                  # fuori
    phone.north_m = 30.0
    await watcher.check_once()                  # arrivato

    fired = cron.get_job(job.id)
    assert fired.state.zone_inside is True
    assert fired.state.next_run_at_ms is not None
    assert fired.state.zone_fix["accuracy"] == 40.0

    await watcher.check_once()                  # ancora lì: niente di nuovo
    assert watcher.stats.arrivals == 1

    await cron._on_timer()
    assert cron.get_job(job.id) is None, "una volta: consegnata e sparita"


async def test_armed_while_already_there_waits_for_a_real_arrival(cron, phone) -> None:
    job = _arm(cron, every=True)
    phone.north_m = 30.0
    watcher = _watcher(cron, phone)

    await watcher.check_once()
    assert cron.get_job(job.id).state.next_run_at_ms is None
    assert cron.get_job(job.id).state.zone_inside is True

    phone.north_m = 2_000.0                     # uscito
    await watcher.check_once()
    phone.north_m = 30.0                        # rientrato
    await watcher.check_once()
    assert cron.get_job(job.id).state.next_run_at_ms is not None


async def test_every_arrival_fires_on_each_return(cron, phone) -> None:
    job = _arm(cron, every=True)
    watcher = _watcher(cron, phone)
    for where in (5_000.0, 30.0, 40.0, 3_000.0, 20.0):
        phone.north_m = where
        await watcher.check_once()
        await cron._on_timer()
    assert watcher.stats.arrivals == 2
    assert cron.get_job(job.id) is not None


async def test_a_failed_read_backs_off(cron, phone) -> None:
    _arm(cron)
    phone.north_m = None
    watcher = _watcher(cron, phone)
    waits = [await watcher.check_once() for _ in range(3)]
    assert waits == [120, 240, 480]
    assert watcher.state == "no_fix"


async def test_an_old_fix_is_a_failed_read(cron, phone) -> None:
    _arm(cron)
    watcher = _watcher(cron, phone)
    original = phone.read_fix

    async def stale(**kw):
        fix = await original(**kw)
        return _Fix(fix.latitude, fix.longitude, fix.accuracy_m,
                    phone.clock_ms - zones.MAX_FIX_AGE_S * 1000 - 1)

    watcher._read_fix = stale
    await watcher.check_once()
    assert watcher.stats.reads_failed == 2 and watcher.stats.reads_ok == 0


async def test_blind_for_long_warns_once(cron, phone) -> None:
    _arm(cron)
    phone.north_m, phone.background = None, "blind"
    changes: list[str] = []
    watcher = _watcher(cron, phone)
    watcher._on_state_change = changes.append

    await watcher.check_once()
    assert watcher.state == "blind" and phone.notices == []

    phone.clock_ms += zones.BLIND_NOTICE_AFTER_S * 1000
    await watcher.check_once()
    await watcher.check_once()
    assert phone.notices == ["blind"]
    assert changes == ["blind"]

    # Ci vede di nuovo: l'episodio è chiuso, e uno nuovo riavviserà.
    phone.north_m = 5_000.0
    await watcher.check_once()
    assert watcher.state == "ok" and changes == ["blind", "ok"]


async def test_expired_zones_go_before_reading(cron, phone) -> None:
    job = cron.add_job(
        name="z", message="x", delete_after_run=True, **_bound_chat(),
        schedule=CronSchedule(kind="zone", lat=_LAT, lng=_LNG, radius_m=200, zone_name="z",
                              until_ms=phone.clock_ms + 1_000),
    )
    phone.clock_ms += 2_000
    assert await _watcher(cron, phone).check_once() is None
    assert cron.get_job(job.id) is None and phone.reads == []


async def test_the_loop_sleeps_on_the_alarm_and_a_poke_wakes_it(cron, phone) -> None:
    watcher = _watcher(cron, phone)
    rounds: list[float | None] = []
    real = watcher.check_once

    async def counted():
        result = await real()
        rounds.append(result)
        return result

    watcher.check_once = counted  # type: ignore[method-assign]
    await watcher.start()
    try:
        await asyncio.sleep(0.05)
        assert rounds == [None] and phone.cancels == 1

        _arm(cron)
        watcher.poke()
        await asyncio.sleep(0.05)
        assert len(rounds) == 2 and rounds[1] is not None
        assert phone.wakes and phone.wakes[-1] > phone.clock_ms
    finally:
        await watcher.stop()
    assert watcher._task is None


async def test_the_cron_pokes_the_watcher_when_zones_change(cron, phone) -> None:
    watcher = _watcher(cron, phone)
    cron.on_zones_changed = watcher.poke
    _arm(cron)
    assert watcher._poke.is_set()


# ── telefono fermo ──────────────────────────────────────────────────────────


class _Motion:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.armed = False
        self.arms = 0
        self.fired = False

    async def arm(self) -> bool:
        self.arms += 1
        self.armed = self.available
        return self.available

    async def disarm(self) -> None:
        self.armed = False


def _still_watcher(cron, phone, motion: _Motion) -> ZoneWatcher:
    watcher = _watcher(cron, phone)
    watcher._arm_motion = motion.arm
    watcher._disarm_motion = motion.disarm
    watcher._woke_by_motion = lambda: motion.fired
    return watcher


async def test_a_still_phone_reads_ever_less_and_arms_the_sensor(cron, phone) -> None:
    _arm(cron)
    phone.north_m = 1_000.0                     # vicino: 2 minuti
    motion = _Motion()
    watcher = _still_watcher(cron, phone, motion)

    waits = [await watcher.check_once() for _ in range(6)]

    assert waits[0] == zones.MIN_CHECK_S
    assert waits[1:4] == [240, 480, 960]
    assert waits[-1] == zones.MAX_CHECK_S
    assert motion.armed and motion.arms == 1


async def test_without_the_sensor_a_still_phone_stops_at_ten_minutes(cron, phone) -> None:
    _arm(cron)
    phone.north_m = 1_000.0
    watcher = _still_watcher(cron, phone, _Motion(available=False))
    waits = [await watcher.check_once() for _ in range(8)]
    assert max(waits) == zones.STILL_CAP_NO_SENSOR_S


async def test_moving_again_resets_the_cadence_and_disarms(cron, phone) -> None:
    _arm(cron)
    phone.north_m = 1_000.0
    motion = _Motion()
    watcher = _still_watcher(cron, phone, motion)
    for _ in range(3):
        await watcher.check_once()
    assert motion.armed

    phone.north_m = 700.0                       # si è mosso di 300 m
    assert await watcher.check_once() == zones.MIN_CHECK_S
    assert not motion.armed


async def test_a_motion_wake_counts_as_moving(cron, phone) -> None:
    _arm(cron)
    phone.north_m = 1_000.0
    motion = _Motion()
    watcher = _still_watcher(cron, phone, motion)
    for _ in range(3):
        await watcher.check_once()

    motion.fired = True
    watcher._wake_event = asyncio.Event()
    watcher._wake_event.set()
    await watcher._wait(1.0)

    assert watcher.stats.wakes == {"motion": 1}
    assert watcher._still == 0 and not watcher._motion_armed
