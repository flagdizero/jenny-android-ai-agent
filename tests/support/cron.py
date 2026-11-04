"""Stati di un job cron che la produzione crea da sé, costruiti per un test.

``CronService.enable_job`` e ``update_job`` non esistono più (24/09/2026): nessuna
superficie di Jenny li chiamava — il tool ``cron`` fa add/list/remove, le rotte
WebUI sono in sola lettura per scelta — e i test li usavano soprattutto per
*preparare* uno stato. Gli stati invece esistono davvero: un job spento è quello
che resta di un ``at`` già eseguito, e una scadenza passata a processo morto è
il caso che il catch-up esiste per coprire. Qui si salvano come li salva il
servizio.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


def _job_in_store(service: Any, job_id: str) -> Any:
    store = service._load_store()
    job = next((j for j in store.jobs if j.id == job_id), None)
    assert job is not None, f"job {job_id} non trovato"
    return job


def _persist(service: Any, job: Any) -> None:
    service._save_store()


def disable_job(service: Any, job_id: str) -> Any:
    """Spegne un job e lo salva: niente prossima esecuzione, come un ``at`` già
    scattato con ``delete_after_run`` falso."""
    job = _job_in_store(service, job_id)
    job.enabled = False
    job.state.next_run_at_ms = None
    _persist(service, job)
    return job


def reschedule_job(service: Any, job_id: str, schedule: Any, next_run_at_ms: int) -> Any:
    """Cambia la pianificazione di un job già salvato e la sua prossima
    esecuzione, così come le lascerebbe un processo che poi muore."""
    job = _job_in_store(service, job_id)
    job.schedule = schedule
    job.state.next_run_at_ms = next_run_at_ms
    _persist(service, job)
    return job


@asynccontextmanager
async def running_cron(workspace: Path) -> AsyncIterator[Any]:
    """Un ``CronService`` avviato sul workspace, fermato all'uscita.

    Avviato, e non solo costruito: chi legge i job per conto suo (``cron_link.
    notebook_jobs_on_disk``, i prompt, la bacheca fuori dal loop) li trova
    salvati, come in produzione.
    """
    from jenny.cron.service import CronService

    service = CronService(Path(workspace))
    await service.start()
    try:
        yield service
    finally:
        service.stop()


def seed_jobs(workspace: Path, jobs: list[dict[str, Any]]) -> None:
    """Righe di job già salvate (la forma di ``CronService._job_to_dict``, anche
    vecchia o monca), come le troverebbe uno scheduler che parte."""
    from jenny.cron import store as cron_store

    cron_store.save_rows(cron_store.cron_database(Path(workspace)), jobs)


def saved_jobs(workspace: Path) -> list[dict[str, Any]]:
    """Le righe dei job come le ha salvate lo scheduler."""
    from jenny.cron import store as cron_store

    return cron_store.load_rows(cron_store.cron_database(Path(workspace)))
