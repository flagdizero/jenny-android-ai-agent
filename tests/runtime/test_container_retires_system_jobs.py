"""Un lavoratore periodico ritirato dal codice sparisce anche dallo store del cron.

``register_system_job`` e' idempotente al riavvio perche' il job resta scritto
nello store del cron. Il rovescio: quando una versione toglie un lavoratore, il
suo job resta li' con nessun ramo nel dispatcher che lo sappia eseguire, e ogni
scadenza diventa un warning «unbound agent job» piu' una ``CronJobSkippedError``
— per sempre, perche' ``remove_job`` **rifiuta** un ``system_event`` e nessun
tool dell'utente puo' toglierlo.

Il primo ritirato e' ``atlas``.
Il test lo nomina perche' e' la voce vera dell'elenco, non un esempio.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from support.cron import saved_jobs

from jenny.config import paths as paths_mod
from jenny.config.schema import Config
from jenny.cron import store as cron_store
from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronPayload, CronSchedule
from jenny.runtime.container import GatewayContainer

_HOUR_MS = 3_600_000


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    previous = paths_mod.get_workspace_path()
    root = tmp_path / "workspace"
    root.mkdir()
    paths_mod.set_workspace_dir(str(root))
    try:
        yield root
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


def _retired_job() -> CronJob:
    return CronJob(
        id="atlas",
        name="atlas",
        schedule=CronSchedule(kind="every", every_ms=6 * _HOUR_MS),
        payload=CronPayload(kind="system_event"),
    )


def _user_job_with_the_same_name() -> CronJob:
    """Un promemoria dell'utente battezzato come il vecchio lavoratore.

    Il ritiro va per **id**: questo ha lo stesso ``name`` e un id suo, e deve
    restare — e' dell'utente.
    """
    return CronJob(
        id="a1b2c3",
        name="atlas",
        schedule=CronSchedule(kind="every", every_ms=8 * _HOUR_MS),
        payload=CronPayload(
            kind="agent_turn",
            message="leggi l'atlante",
            session_key="unified:default",
            origin_channel="websocket",
            origin_chat_id="webui",
        ),
    )


def _seed(workspace: Path) -> Path:
    """Lo store come lo lascia una versione precedente: il job ritirato, un run
    record suo, e un promemoria omonimo dell'utente."""
    service = CronService(workspace)
    service.register_system_job(_retired_job())
    assert service._store is not None
    service._store.jobs.append(_user_job_with_the_same_name())
    service._save_store()
    cron_store.write_run(service._db, "atlas:1725000000000:ab12", "atlas", 1, {})
    cron_store.write_run(service._db, "a1b2c3:1725000000000:cd34", "a1b2c3", 2, {})
    return workspace


def test_build_retires_the_job_and_its_run_records_and_keeps_the_users(workspace: Path) -> None:
    _seed(workspace)

    container = GatewayContainer(Config())
    container.build()

    ids = {job.id for job in container.cron.list_jobs(include_disabled=True)}
    assert "atlas" not in ids
    assert "a1b2c3" in ids, "il promemoria omonimo dell'utente e' stato portato via"
    assert "dream" in ids and "gardener" in ids, "i lavoratori vivi devono esserci comunque"
    left = [r["run_id"] for r in
            container.cron._db.read("SELECT run_id FROM cron_runs ORDER BY run_id")]
    assert left == ["a1b2c3:1725000000000:cd34"]


def test_a_store_without_the_job_is_left_alone(workspace: Path) -> None:
    """A regime il ritiro costa zero: niente da togliere, niente scritto."""
    path = workspace
    CronService(path).register_system_job(
        CronJob(
            id="dream", name="dream",
            schedule=CronSchedule(kind="every", every_ms=2 * _HOUR_MS),
            payload=CronPayload(kind="system_event"),
        )
    )

    service = CronService(path)
    assert service.retire_system_job("atlas") is False
    assert {j.id for j in service.list_jobs()} == {"dream"}


def test_remove_job_still_refuses_a_system_job(workspace: Path) -> None:
    """Il ritiro non allarga la porta: ``remove_job`` protegge come prima."""
    path = _seed(workspace)
    service = CronService(path)

    assert service.remove_job("atlas") == "protected"
    assert service.retire_system_job("atlas") is True
    assert service.retire_system_job("atlas") is False


def test_retiring_before_start_saves_the_store(workspace: Path) -> None:
    """Il ritiro gira in ``build``, prima di ``start``: salva lo stesso."""
    path = _seed(workspace)
    service = CronService(path)

    assert service.retire_system_job("atlas") is True

    saved = {j["id"] for j in saved_jobs(path)}
    assert "atlas" not in saved and "a1b2c3" in saved
