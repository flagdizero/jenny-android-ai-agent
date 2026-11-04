"""Lo store del cron in ``state.sqlite`` (``jenny/cron/store.py``).

Quel che il file ``jobs.json`` doveva difendere a mano — una scrittura a metà,
un file rotto che diventava una lista vuota — lo fa la transazione: o entra
tutto, o resta quello di prima. Qui si prova questo, il giro intero fra due
istanze, e che una riga illeggibile costa solo sé stessa.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jenny.cron import store as cron_store
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule


def _service(tmp_path: Path) -> CronService:
    service = CronService(tmp_path / "workspace")
    service.add_job(
        name="Daily Loving Message",
        schedule=CronSchedule(kind="cron", expr="0 10 * * *", tz="Asia/Kuwait"),
        message="hello",
    )
    return service


def test_a_job_added_while_stopped_is_saved(tmp_path: Path) -> None:
    _service(tmp_path)
    again = CronService(tmp_path / "workspace")
    assert [j.name for j in again.list_jobs(include_disabled=True)] == ["Daily Loving Message"]


def test_a_failed_save_leaves_the_previous_jobs(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    service._load_store()
    service._store.jobs[0].name = "a metà"

    def boom(job):  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(CronService, "_job_to_dict", staticmethod(boom))
    with pytest.raises(sqlite3.OperationalError):
        service._save_store()
    monkeypatch.undo()
    assert [j.name for j in CronService(tmp_path / "workspace").list_jobs(include_disabled=True)] == [
        "Daily Loving Message"]


def test_an_unreadable_row_costs_only_itself(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with service._db.write() as conn:
        conn.execute("INSERT INTO cron_jobs(id, pos, data) VALUES ('rotto', 9, '{non json')")
        conn.execute("INSERT INTO cron_jobs(id, pos, data) VALUES ('monco', 10, '{}')")
    assert [j.name for j in service.list_jobs(include_disabled=True)] == ["Daily Loving Message"]


def test_full_round_trip_survives_repeated_save_load(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._load_store()
    first = [CronService._job_to_dict(j) for j in service._store.jobs]
    service._save_store()
    service._save_store()
    rows = cron_store.load_rows(service._db)
    assert rows == first
