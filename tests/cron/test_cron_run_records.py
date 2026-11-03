"""I record di esecuzione del cron: uno per giro, e se ne vanno col loro job.

Fino al 24/08/2026 non c'era **nessuna** potatura, e ``remove_job`` non li
sfiorava: i record di un job cancellato gli sopravvivevano per sempre. Nessuno
li legge (gateway, WebUI, tool): sono una traccia per il post-mortem, ed è la
ragione per cui il tetto si sceglie senza cerimonie (``cron/store.RUNS_KEEP``).
"""

from __future__ import annotations

from pathlib import Path

from jenny.cron import store as cron_store
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule


def _bound_chat(chat_id: str = "chat-1") -> dict[str, str]:
    return {
        "session_key": f"websocket:{chat_id}",
        "origin_channel": "websocket",
        "origin_chat_id": chat_id,
    }


def _record(service: CronService, job_id: str, stamp: int, tag: str = "aaa") -> str:
    run_id = f"{job_id}:{stamp}:{tag}"
    cron_store.write_run(service._db, run_id, job_id, stamp, {"job_id": job_id})
    return run_id


def _ids(service: CronService) -> set[str]:
    return {r["run_id"] for r in service._db.read("SELECT run_id FROM cron_runs")}


def _job(service: CronService, name: str):
    return service.add_job(
        name=name,
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="Europe/Rome"),
        message="ciao",
        **_bound_chat(),
    )


def test_removing_a_job_takes_its_run_records_with_it(tmp_path: Path) -> None:
    service = CronService(tmp_path / "workspace")
    job = _job(service, "da togliere")
    _record(service, job.id, 1)
    _record(service, job.id, 2)
    assert service.remove_job(job.id) == "removed"
    assert _ids(service) == set()


def test_it_does_not_take_another_job_s_records(tmp_path: Path) -> None:
    service = CronService(tmp_path / "workspace")
    gone, kept = _job(service, "via"), _job(service, "resta")
    _record(service, gone.id, 1)
    kept_run = _record(service, kept.id, 2)
    service.remove_job(gone.id)
    assert _ids(service) == {kept_run}


def test_write_run_record_keeps_what_the_runner_sends(tmp_path: Path) -> None:
    service = CronService(tmp_path / "workspace")
    service.write_run_record("j1:5:ab", {"job_id": "j1", "status": "ok"})
    [record] = cron_store.read_runs(service._db, "j1")
    assert record["status"] == "ok" and record["run_id"] == "j1:5:ab"
    assert isinstance(record["updated_at_ms"], int)


def test_it_keeps_the_most_recent_and_drops_the_rest(tmp_path: Path) -> None:
    service = CronService(tmp_path / "workspace")
    for stamp in range(cron_store.RUNS_KEEP + 3):
        _record(service, "j", stamp, tag=f"{stamp:04d}")
    assert service._prune_run_records() == 3
    left = _ids(service)
    assert len(left) == cron_store.RUNS_KEEP
    assert "j:0:0000" not in left and f"j:{cron_store.RUNS_KEEP + 2}:" \
        f"{cron_store.RUNS_KEEP + 2:04d}" in left


def test_under_the_cap_it_touches_nothing(tmp_path: Path) -> None:
    service = CronService(tmp_path / "workspace")
    _record(service, "j", 1)
    assert service._prune_run_records() == 0
    assert len(_ids(service)) == 1
