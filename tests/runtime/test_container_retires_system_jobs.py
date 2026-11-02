"""Un lavoratore periodico ritirato dal codice sparisce anche dallo store del cron.

``register_system_job`` e' idempotente al riavvio perche' il job resta scritto
in ``cron/jobs.json``. Il rovescio: quando una versione toglie un lavoratore, il
suo job resta li' con nessun ramo nel dispatcher che lo sappia eseguire, e ogni
scadenza diventa un warning «unbound agent job» piu' una ``CronJobSkippedError``
— per sempre, perche' ``remove_job`` **rifiuta** un ``system_event`` e nessun
tool dell'utente puo' toglierlo.

Il primo ritirato e' ``atlas``.
Il test lo nomina perche' e' la voce vera dell'elenco, non un esempio.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from jenny.config import paths as paths_mod
from jenny.config.schema import Config
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
    path = workspace / "cron" / "jobs.json"
    service = CronService(path)
    service.register_system_job(_retired_job())
    assert service._store is not None
    service._store.jobs.append(_user_job_with_the_same_name())
    service._save_store()
    runs = path.parent / "runs"
    runs.mkdir(exist_ok=True)
    (runs / "atlas_1725000000000_ab12.json").write_text("{}", encoding="utf-8")
    (runs / "a1b2c3_1725000000000_cd34.json").write_text("{}", encoding="utf-8")
    return path


def test_build_retires_the_job_and_its_run_records_and_keeps_the_users(workspace: Path) -> None:
    path = _seed(workspace)

    container = GatewayContainer(Config())
    container.build()

    ids = {job.id for job in container.cron.list_jobs(include_disabled=True)}
    assert "atlas" not in ids
    assert "a1b2c3" in ids, "il promemoria omonimo dell'utente e' stato portato via"
    assert "dream" in ids and "gardener" in ids, "i lavoratori vivi devono esserci comunque"
    left = sorted(p.name for p in (path.parent / "runs").iterdir())
    assert left == ["a1b2c3_1725000000000_cd34.json"]


def test_a_store_without_the_job_is_left_alone(workspace: Path) -> None:
    """A regime il ritiro costa zero: niente da togliere, niente scritto."""
    path = workspace / "cron" / "jobs.json"
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


def test_retiring_before_start_writes_the_store_not_the_journal(workspace: Path) -> None:
    """Misurato sul telefono al primo avvio della 0.10.0.

    Il ritiro gira in ``build``, prima di ``start``: passando dal giornale delle
    azioni lasciava una riga «del atlas» che ``register_system_job``, un attimo
    dopo, rendeva gia' vecchia salvando lo store senza quel job. Ogni
    ``_load_store`` la rigiocava su uno store che il job non l'aveva piu', e il
    ``pop`` sollevava — sei traceback in due minuti, e il giornale mai svuotato.
    """
    path = _seed(workspace)
    service = CronService(path)

    assert service.retire_system_job("atlas") is True

    journal = path.parent / "action.jsonl"
    assert not journal.exists() or journal.read_text(encoding="utf-8").strip() == ""
    on_disk = {j["id"] for j in json.loads(path.read_text(encoding="utf-8"))["jobs"]}
    assert "atlas" not in on_disk and "a1b2c3" in on_disk


def test_a_journal_del_for_a_job_that_is_gone_is_a_no_op(workspace: Path) -> None:
    """La meta' generale del difetto: cancellare cio' che non c'e' e' idempotente.

    Vale per qualunque «del» rimasto nel giornale — anche quello che
    ``remove_job`` scrive a servizio fermo — e la riga deve contare come
    applicata, o il giornale non si svuota mai.
    """
    path = workspace / "cron" / "jobs.json"
    service = CronService(path)
    service.register_system_job(
        CronJob(
            id="dream", name="dream",
            schedule=CronSchedule(kind="every", every_ms=2 * _HOUR_MS),
            payload=CronPayload(kind="system_event"),
        )
    )
    journal = path.parent / "action.jsonl"
    journal.write_text(json.dumps({"action": "del", "params": {"job_id": "atlas"}}) + "\n",
                       encoding="utf-8")
    records: list[str] = []
    handler = loguru_logger.add(lambda m: records.append(str(m)), level="ERROR")
    try:
        reloaded = CronService(path)
        assert {j.id for j in reloaded.list_jobs()} == {"dream"}
        # A servizio avviato il giornale applicato si svuota: e' la prova che la
        # riga e' stata contata come fatta e non saltata.
        reloaded._running = True
        try:
            reloaded._load_store()
        finally:
            reloaded._running = False
    finally:
        loguru_logger.remove(handler)

    assert records == [], records
    assert journal.read_text(encoding="utf-8").strip() == ""
