"""Un promemoria one-shot scaduto a app spenta parte in ritardo, non sparisce.

Prima di questi test un job ``at`` la cui scadenza cadeva mentre l'app era giù
finiva in uno stato che nessun altro percorso sa creare: ``enabled`` a True con
``next_run_at_ms`` a None. All'occhio è identico a un one-shot già eseguito, e
significa l'opposto — ma nessuno lo ripuliva: il filtro dei job dovuti lo
saltava, ``_get_next_wake_ms`` lo saltava, e il promemoria restava lì per
sempre senza partire né dare errore.

È il caso "il telefono era spento alle 15", cioè proprio quello per cui un
promemoria esiste.
"""

import time

import pytest
from support.cron import reschedule_job

from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

_MINUTE_MS = 60_000


def _bound_chat(chat_id: str = "chat-1") -> dict[str, str]:
    return {
        "session_key": f"websocket:{chat_id}",
        "origin_channel": "websocket",
        "origin_chat_id": chat_id,
    }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _past() -> int:
    return _now_ms() - 30 * _MINUTE_MS


def _future() -> int:
    return _now_ms() + 30 * _MINUTE_MS


def test_add_job_with_a_past_deadline_is_scheduled_not_orphaned(tmp_path) -> None:
    """Anche creato in ritardo (il modello calcola l'ora, poi il turno dura),
    il job deve avere una scadenza da onorare, non ``None``."""
    service = CronService(tmp_path / "cron" / "jobs.json")
    at_ms = _past()

    job = service.add_job(
        name="dentista",
        schedule=CronSchedule(kind="at", at_ms=at_ms),
        message="chiama il dentista",
        **_bound_chat(),
    )

    assert job.enabled is True
    assert job.state.next_run_at_ms == at_ms


def test_future_deadline_is_left_alone(tmp_path) -> None:
    """Il recupero non deve toccare chi non è in ritardo."""
    service = CronService(tmp_path / "cron" / "jobs.json")
    at_ms = _future()

    job = service.add_job(
        name="più tardi",
        schedule=CronSchedule(kind="at", at_ms=at_ms),
        message="fra mezz'ora",
        **_bound_chat(),
    )

    assert job.state.next_run_at_ms == at_ms


@pytest.mark.asyncio
async def test_deadline_missed_while_the_app_was_down_runs_at_the_next_start(tmp_path) -> None:
    """Il caso vero: scadenza passata mentre il processo era morto."""
    path = tmp_path / "cron" / "jobs.json"
    at_ms = _past()

    # Primo avvio: il job viene creato con scadenza futura, poi l'app muore.
    first = CronService(path)
    job = first.add_job(
        name="promemoria",
        schedule=CronSchedule(kind="at", at_ms=_future()),
        message="ricorda",
        **_bound_chat(),
    )
    # La scadenza arriva e passa a processo spento.
    reschedule_job(first, job.id, CronSchedule(kind="at", at_ms=at_ms), next_run_at_ms=at_ms)

    # Riavvio.
    fired: list[str] = []

    async def on_job(j):
        fired.append(j.id)

    second = CronService(path, on_job=on_job)
    second._arm_timer = lambda: None
    await second.start()
    try:
        reloaded = second.get_job(job.id)
        assert reloaded.enabled is True
        assert reloaded.state.next_run_at_ms == at_ms, "la scadenza scaduta va tenuta, non azzerata"

        await second._on_timer()
        assert fired == [job.id]

        # E finisce nello stato terminale normale di un one-shot.
        done = second.get_job(job.id)
        assert done.enabled is False
        assert done.state.next_run_at_ms is None
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_a_late_one_shot_does_not_re_arm_itself_forever(tmp_path) -> None:
    """Il rischio dell'intera fix: se anche il post-esecuzione recuperasse la
    scadenza passata, il job ripartirebbe a ogni tick per sempre."""
    path = tmp_path / "cron" / "jobs.json"
    fired: list[str] = []

    async def on_job(j):
        fired.append(j.id)

    service = CronService(path, on_job=on_job)
    service._arm_timer = lambda: None
    service._running = True
    service._load_store()
    job = service.add_job(
        name="una volta sola",
        schedule=CronSchedule(kind="at", at_ms=_past()),
        message="una volta",
        **_bound_chat(),
    )

    await service._on_timer()
    await service._on_timer()
    await service._on_timer()

    assert fired == [job.id], "un one-shot in ritardo deve restare one-shot"


@pytest.mark.asyncio
async def test_delete_after_run_still_removes_the_late_job(tmp_path) -> None:
    path = tmp_path / "cron" / "jobs.json"

    async def on_job(j):
        return None

    service = CronService(path, on_job=on_job)
    service._arm_timer = lambda: None
    service._running = True
    service._load_store()
    job = service.add_job(
        name="usa e getta",
        schedule=CronSchedule(kind="at", at_ms=_past()),
        message="via",
        delete_after_run=True,
        **_bound_chat(),
    )

    await service._on_timer()

    assert service.get_job(job.id) is None


@pytest.mark.asyncio
async def test_an_already_orphaned_job_on_disk_heals_itself(tmp_path) -> None:
    """Nessuna migrazione: gli orfani scritti dalle versioni precedenti
    ripartono da soli, perché il recupero rilegge ``schedule.at_ms`` e non lo
    stato salvato."""
    path = tmp_path / "cron" / "jobs.json"
    at_ms = _past()

    seed = CronService(path)
    job = seed.add_job(
        name="orfano",
        schedule=CronSchedule(kind="at", at_ms=at_ms),
        message="dimenticato",
        **_bound_chat(),
    )
    # Lo stato esatto che scriveva la versione con il bug.
    stored = seed.get_job(job.id)
    stored.state.next_run_at_ms = None
    seed._running = True
    seed._save_store()

    revived = CronService(path)
    revived._arm_timer = lambda: None
    await revived.start()
    try:
        healed = revived.get_job(job.id)
        assert healed.enabled is True
        assert healed.state.next_run_at_ms == at_ms
    finally:
        revived.stop()
