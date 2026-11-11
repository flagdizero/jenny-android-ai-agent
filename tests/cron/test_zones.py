"""Le regole delle zone (``jenny/cron/zones.py``): dentro, fuori, fra quanto guardare."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jenny.cron import zones
from jenny.cron.zones import Zone

# Un grado di latitudine è ~111,2 km: 0,001° ≈ 111 m.
_M_PER_DEG = 111_195.0
_LAT, _LNG = 45.0, 9.0


@dataclass(frozen=True)
class _Fix:
    latitude: float
    longitude: float
    accuracy_m: float | None
    time_ms: int = 0


def _at(metres_north: float, accuracy: float | None = 10.0, time_ms: int = 0) -> _Fix:
    return _Fix(_LAT + metres_north / _M_PER_DEG, _LNG, accuracy, time_ms)


def _zone(inside: bool | None = False, radius: float = 200, every: bool = False) -> Zone:
    return Zone("z1", _LAT, _LNG, radius, inside, every)


def test_far_away_is_outside() -> None:
    assert zones.judge(_zone(), _at(5_000)) == "outside"


def test_arriving_from_outside_fires() -> None:
    assert zones.judge(_zone(inside=False), _at(50)) == "enter"


def test_already_inside_when_armed_does_not_fire() -> None:
    # «Quando arrivo al supermercato», detto al supermercato: non suona subito.
    assert zones.judge(_zone(inside=None), _at(50)) == "inside"


def test_staying_inside_does_not_fire_again() -> None:
    assert zones.judge(_zone(inside=True), _at(50)) == "inside"


def test_a_fix_too_vague_for_the_radius_is_unsure() -> None:
    # Al centro, ma con 800 m di incertezza su un raggio di 200: non si sa.
    assert zones.judge(_zone(), _at(0, accuracy=800)) == "unsure"


def test_just_past_the_edge_is_not_yet_out() -> None:
    # Il bordo balla: per uscire serve il raggio più il margine.
    assert zones.judge(_zone(inside=True), _at(200 + 50, accuracy=10)) == "unsure"
    assert zones.judge(_zone(inside=True), _at(200 + zones.EXIT_MARGIN_M + 50)) == "outside"


def test_unknown_accuracy_counts_as_vague() -> None:
    assert zones.judge(_zone(), _at(0, accuracy=None)) == "unsure"


def test_network_fix_touching_the_zone_asks_for_gps() -> None:
    assert zones.needs_precise([_zone()], _at(400, accuracy=300))


def test_network_fix_far_away_is_enough() -> None:
    assert not zones.needs_precise([_zone()], _at(5_000, accuracy=300))


def test_precise_fix_inside_needs_nothing_more() -> None:
    assert not zones.needs_precise([_zone()], _at(10, accuracy=20))


def test_zones_already_inside_do_not_ask_for_gps() -> None:
    assert not zones.needs_precise([_zone(inside=True)], _at(0, accuracy=800))


@pytest.mark.parametrize(
    ("metres", "expected"),
    [
        (100_000, zones.MAX_CHECK_S),     # lontanissimo: il tetto
        (1_000, zones.MIN_CHECK_S),       # vicino: il pavimento
        (40_000, (40_000 - 200 - 10) / zones.SPEED_MPS),
    ],
)
def test_the_next_check_follows_the_distance(metres: float, expected: float) -> None:
    assert zones.next_check_s([_zone()], _at(metres)) == pytest.approx(expected)


def test_inside_waits_for_the_exit_calmly() -> None:
    assert zones.next_check_s([_zone(inside=True, every=True)], _at(0)) == zones.INSIDE_CHECK_S


def test_the_nearest_zone_wins() -> None:
    far = Zone("far", _LAT + 1, _LNG, 200, False, False)
    near = _zone()
    assert zones.next_check_s([far, near], _at(1_000)) == zones.MIN_CHECK_S


def test_failed_reads_back_off_up_to_the_cap() -> None:
    waits = [zones.next_check_s([_zone()], None, failures=n) for n in range(1, 8)]
    assert waits[:3] == [120, 240, 480]
    assert waits[-1] == zones.MAX_CHECK_S


def test_an_old_fix_is_not_a_reading() -> None:
    fix = _at(0, time_ms=1_000_000)
    assert zones.is_fresh(fix, 1_000_000 + zones.MAX_FIX_AGE_S * 1000)
    assert not zones.is_fresh(fix, 1_000_000 + zones.MAX_FIX_AGE_S * 1000 + 1)


def test_two_close_fixes_mean_the_phone_is_still() -> None:
    assert zones.is_still(_at(0), _at(20))
    assert not zones.is_still(_at(0), _at(500))
    assert not zones.is_still(None, _at(0))


def test_fix_record_is_plain_data() -> None:
    record = zones.fix_record(_at(0, accuracy=12.345, time_ms=7))
    assert record == {"lat": 45.0, "lng": 9.0, "accuracy": 12.3, "timeMs": 7}
