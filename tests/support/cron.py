"""Stati di un job cron che la produzione crea da sé, costruiti per un test.

``CronService.enable_job`` e ``update_job`` non esistono più (24/09/2026): nessuna
superficie di Jenny li chiamava — il tool ``cron`` fa add/list/remove, le rotte
WebUI sono in sola lettura per scelta — e i test li usavano soprattutto per
*preparare* uno stato. Gli stati invece esistono davvero: un job spento è quello
che resta di un ``at`` già eseguito, e una scadenza passata a processo morto è
il caso che il catch-up esiste per coprire. Qui si scrivono sul disco come li
scrive il servizio.

**Come li scrive dipende da se gira.** Un servizio avviato salva ``jobs.json``;
uno fermo non lo tocca e accoda l'azione al giornale (``action.jsonl``), che
ogni caricamento riapplica finché qualcuno che gira non lo assorbe. Un
``_save_store`` da fermo verrebbe quindi riscritto dall'``add`` ancora nel
giornale al primo caricamento. Il giornale tratta tutto ciò che non è ``del``
come un upsert: la riga si accoda come ``add``, lo stesso verbo di
``add_job`` da fermo.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any


def _job_in_store(service: Any, job_id: str) -> Any:
    store = service._load_store()
    job = next((j for j in store.jobs if j.id == job_id), None)
    assert job is not None, f"job {job_id} non trovato"
    return job


def _persist(service: Any, job: Any) -> None:
    if service._running:
        service._save_store()
    else:
        service._append_action("add", asdict(job))


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
