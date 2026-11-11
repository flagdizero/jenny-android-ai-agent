"""Le zone nello scheduler: si salvano, si validano, scattano dal timer, si sistemano.

La sentinella (``zone_watch``) non esegue niente: chiama ``fire_zone``, che rende
il job dovuto, e il giro del timer lo esegue come qualunque altro.
"""

from __future__ import annotations

import time

import pytest

from jenny.cron import zones
from jenny.cron.service import _ONE_SHOT_RETRY_MS, CronJobSkippedError, CronService, can_resume
from jenny.cron.types import CronSchedule


def _bound_chat() -> dict[str, str]:
    return {"session_key": "websocket:chat-1", "origin_channel": "websocket",
            "origin_chat_id": "chat-1"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _service(tmp_path, on_job=None) -> CronService:
    async def ring(job):
        return "detto"

    service = CronService(tmp_path / "workspace", on_job=on_job or ring)
    service._arm_timer = lambda: None  # type: ignore[method-assign]
    service._running = True
    service._load_store()
    return service


def _zone(name: str = "Stazione", *, until_ms: int | None = None, radius: float = 200):
    return CronSchedule(kind="zone", lat=45.0, lng=9.0, radius_m=radius, zone_name=name,
                        until_ms=until_ms)


def _add(service: CronService, *, every: bool = False, **kw):
    return service.add_job(name="binario", schedule=_zone(**kw), message="dimmi il binario",
                           delete_after_run=not every, **_bound_chat())


def test_a_zone_round_trips_through_the_store(tmp_path) -> None:
    service = _service(tmp_path)
    until = _now_ms() + 3_600_000
    job = _add(service, until_ms=until)
    service.set_zone_inside(job.id, False)

    fresh = CronService(tmp_path / "workspace")
    loaded = fresh.get_job(job.id)
    assert loaded is not None
    assert loaded.schedule == _zone(until_ms=until)
    assert loaded.state.zone_inside is False
    assert loaded.state.next_run_at_ms is None
    assert loaded.delete_after_run is True


def test_other_jobs_keep_their_exact_shape(tmp_path) -> None:
    service = _service(tmp_path)
    job = service.add_job(name="ogni ora", schedule=CronSchedule(kind="every", every_ms=3600_000),
                          message="x", **_bound_chat())
    row = CronService._job_to_dict(service.get_job(job.id))
    assert "lat" not in row["schedule"] and "zoneInside" not in row["state"]


def test_a_zone_without_its_circle_is_not_loaded(tmp_path) -> None:
    # Come la lascerebbe un Jenny più vecchio che ha riscritto lo store.
    row = CronService._job_to_dict(_service(tmp_path).add_job(
        name="z", schedule=_zone(), message="x", **_bound_chat()))
    for key in ("lat", "lng", "radiusM"):
        row["schedule"].pop(key)
    with pytest.raises(ValueError):
        CronService._parse_job(row)


@pytest.mark.parametrize(
    ("schedule", "match"),
    [
        (CronSchedule(kind="zone", lat=95.0, lng=9.0, radius_m=200, zone_name="x"), "latitude"),
        (CronSchedule(kind="zone", lat=45.0, lng=9.0, radius_m=20, zone_name="x"), "radius"),
        (CronSchedule(kind="zone", lat=45.0, lng=9.0, radius_m=200, zone_name=" "), "name"),
        (CronSchedule(kind="zone", lat=45.0, lng=9.0, radius_m=200, zone_name="x",
                      until_ms=1), "already past"),
        (CronSchedule(kind="at", at_ms=_now_ms() + 5_000, until_ms=_now_ms() + 10_000),
         "not a one-time"),
    ],
)
def test_bad_zones_are_refused(tmp_path, schedule, match) -> None:
    with pytest.raises(ValueError, match=match):
        _service(tmp_path).add_job(name="z", schedule=schedule, message="x", **_bound_chat())


def test_a_zone_is_never_a_monitor(tmp_path) -> None:
    with pytest.raises(ValueError, match="always speaks"):
        _service(tmp_path).add_job(name="z", schedule=_zone(), message="x", mode="monitor",
                                   **_bound_chat())


def test_a_zone_takes_no_condition(tmp_path) -> None:
    cond = {"app": "a", "field": "f", "op": "gt", "value": 1}
    with pytest.raises(ValueError, match="condition"):
        _service(tmp_path).add_job(name="z", schedule=_zone(), message="x", condition=cond,
                                   **_bound_chat())


def test_there_is_a_cap_on_armed_zones(tmp_path) -> None:
    service = _service(tmp_path)
    for i in range(zones.MAX_ZONES):
        _add(service, name=f"posto {i}")
    with pytest.raises(ValueError, match="too many zones"):
        _add(service)


async def test_an_arrival_is_run_by_the_timer_and_a_one_time_zone_goes(tmp_path) -> None:
    ran: list[str] = []

    async def on_job(job):
        ran.append(job.id)
        return "detto"

    service = _service(tmp_path, on_job)
    job = _add(service)
    await service._on_timer()
    assert ran == [], "una zona non ha un'ora: il timer non la tocca"

    assert service.fire_zone(job.id, entered_at_ms=_now_ms(), fix={"lat": 45.0, "lng": 9.0})
    await service._on_timer()

    assert ran == [job.id]
    assert service.get_job(job.id) is None


async def test_an_every_arrival_zone_stays_and_waits(tmp_path) -> None:
    service = _service(tmp_path)
    job = _add(service, every=True)
    service.fire_zone(job.id, entered_at_ms=_now_ms())
    await service._on_timer()

    kept = service.get_job(job.id)
    assert kept is not None and kept.enabled
    assert kept.state.next_run_at_ms is None
    assert kept.state.zone_inside is True


async def test_an_undelivered_arrival_retries_then_stays_armed(tmp_path) -> None:
    async def offline(job):
        raise CronJobSkippedError("no network")

    service = _service(tmp_path, offline)
    job = _add(service)
    service.fire_zone(job.id, entered_at_ms=_now_ms())
    await service._on_timer()

    kept = service.get_job(job.id)
    assert kept is not None
    assert 0 < kept.state.next_run_at_ms - _now_ms() <= _ONE_SHOT_RETRY_MS

    # Un'ora dopo l'arrivo non è più «sei appena arrivato»: niente più tentativi,
    # ma la zona resta armata per il prossimo arrivo.
    kept.state.zone_entered_at_ms = _now_ms() - zones.ZONE_RETRY_WINDOW_MS - 1
    kept.state.next_run_at_ms = _now_ms() - 1
    service._save_store()
    await service._on_timer()

    kept = service.get_job(job.id)
    assert kept is not None and kept.enabled
    assert kept.state.next_run_at_ms is None
    assert kept.state.zone_inside is True


def test_a_pending_arrival_survives_a_restart(tmp_path) -> None:
    service = _service(tmp_path)
    job = _add(service)
    service.fire_zone(job.id, entered_at_ms=_now_ms())
    pending = service.get_job(job.id).state.next_run_at_ms

    restarted = CronService(tmp_path / "workspace")
    restarted._load_store()
    restarted._recompute_next_runs()
    assert restarted.get_job(job.id).state.next_run_at_ms == pending


def test_a_paused_zone_resumes_as_freshly_armed(tmp_path) -> None:
    service = _service(tmp_path)
    job = _add(service)
    service.set_zone_inside(job.id, True)

    assert service.set_paused(job.id, True) == "paused"
    assert service.zone_jobs() == []
    assert service.fire_zone(job.id, entered_at_ms=_now_ms()) is False

    assert service.set_paused(job.id, False) == "resumed"
    resumed = service.get_job(job.id)
    assert resumed.state.zone_inside is None
    assert resumed.state.next_run_at_ms is None


def test_an_expired_zone_cannot_resume(tmp_path) -> None:
    service = _service(tmp_path)
    job = _add(service, until_ms=_now_ms() + 60_000)
    service.set_paused(job.id, True)
    paused = service.get_job(job.id)
    assert can_resume(paused, _now_ms())
    assert not can_resume(paused, _now_ms() + 120_000)


async def test_expired_zones_go_away(tmp_path) -> None:
    service = _service(tmp_path)
    old = _add(service, until_ms=_now_ms() + 1_000, name="vecchia")
    keep = _add(service, name="senza scadenza", every=True)

    dropped = await service.drop_expired_zones(_now_ms() + 2_000)

    assert dropped == [old.id]
    assert service.get_job(old.id) is None
    assert service.get_job(keep.id) is not None


async def test_an_arrival_due_is_delivered_before_expiring(tmp_path) -> None:
    service = _service(tmp_path)
    job = _add(service, until_ms=_now_ms() + 1_000)
    service.fire_zone(job.id, entered_at_ms=_now_ms())
    assert await service.drop_expired_zones(_now_ms() + 2_000) == []


def test_zone_changes_poke_the_watcher(tmp_path) -> None:
    service = _service(tmp_path)
    pokes: list[int] = []
    service.on_zones_changed = lambda: pokes.append(1)

    job = _add(service)
    service.set_paused(job.id, True)
    service.set_paused(job.id, False)
    service.remove_job(job.id)
    service.add_job(name="ogni ora", schedule=CronSchedule(kind="every", every_ms=3600_000),
                    message="x", **_bound_chat())

    assert len(pokes) == 4, "add, pausa, ripresa, rimozione; un cron a orario no"


def test_zone_payload_on_the_wire() -> None:
    from jenny.cron.types import schedule_payload

    wire = schedule_payload(_zone(until_ms=5))
    assert wire["kind"] == "zone"
    assert wire["zone"] == {"name": "Stazione", "lat": 45.0, "lng": 9.0, "radiusM": 200}
    assert wire["until_ms"] == 5
    assert "zone" not in schedule_payload(CronSchedule(kind="every", every_ms=1000))
