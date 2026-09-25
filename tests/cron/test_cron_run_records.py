"""I record di esecuzione del cron: uno per giro, e nessuno che li togliesse.

Fino al 24/08/2026 ``cron/runs/`` non aveva **nessuna** potatura — né tetto, né
scadenza, né una `unlink` da nessuna parte — e ``remove_job`` non li sfiorava: i
record di un job cancellato gli sopravvivevano per sempre. È la forma del difetto
trovato lo stesso giorno sui progetti (v. ``.agent/stale-name-bindings-plan.md``),
in versione più mite: gli id dei job sono opachi, quindi un job nuovo non eredita
mai quelli di uno vecchio. Non reincarnazione, perdita lenta.

**Nessuno legge quei file** — né il gateway, né le route WebUI, né il client, né
il tool ``cron``: sono una traccia per il post-mortem. È la ragione per cui si
può scegliere un tetto senza cerimonie.

I nomi qui si scrivono a mano invece di far girare dei job veri: l'ordinamento
legge il ``<ms>`` dentro il nome, quindi si prova esattamente quel che il codice
fa, e **senza toccare l'orologio**.
"""

from __future__ import annotations

from pathlib import Path

from jenny.cron.service import _RUN_RECORDS_KEEP, CronService
from jenny.cron.types import CronSchedule


def _bound_chat(chat_id: str = "chat-1") -> dict[str, str]:
    return {
        "session_key": f"websocket:{chat_id}",
        "origin_channel": "websocket",
        "origin_chat_id": chat_id,
    }


def _service(tmp_path: Path) -> CronService:
    service = CronService(tmp_path / "cron" / "jobs.json")
    (tmp_path / "cron" / "runs").mkdir(parents=True, exist_ok=True)
    return service


def _record(service: CronService, job_id: str, stamp: int, tag: str = "aaa") -> Path:
    path = service._run_records_dir / f"{job_id}_{stamp}_{tag}.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _names(service: CronService) -> set[str]:
    return {p.name for p in service._run_records_dir.iterdir()}


# ── Cancellare un job porta via i suoi record ────────────────────────────


def test_removing_a_job_takes_its_run_records_with_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job = service.add_job(
        name="da togliere",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="Europe/Rome"),
        message="ciao",
        **_bound_chat(),
    )
    _record(service, job.id, 1_000)
    _record(service, job.id, 2_000)

    assert service.remove_job(job.id) == "removed"
    assert _names(service) == set()


def test_it_does_not_take_another_job_s_records(tmp_path: Path) -> None:
    """Il controllo del test sopra: una cancellazione che svuota la cartella lo
    passerebbe lo stesso."""
    service = _service(tmp_path)
    job = service.add_job(
        name="da togliere",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="Europe/Rome"),
        message="ciao",
        **_bound_chat(),
    )
    _record(service, job.id, 1_000)
    foreign = _record(service, "deadbeef", 1_000)

    service.remove_job(job.id)

    assert _names(service) == {foreign.name}


def test_an_id_that_is_a_prefix_of_another_keeps_its_neighbour_intact(
    tmp_path: Path,
) -> None:
    """Il confronto è sul **segmento**, non sulla sottostringa.

    ``ab`` è prefisso di ``abcd``: senza il separatore nel confronto,
    cancellare ``ab`` porterebbe via anche i record di ``abcd``.
    """
    service = _service(tmp_path)
    _record(service, "ab", 1_000)
    near = _record(service, "abcd", 1_000)

    assert service._remove_run_records("ab") == 1
    assert _names(service) == {near.name}


# ── La potatura ──────────────────────────────────────────────────────────


def test_it_keeps_the_most_recent_and_drops_the_rest(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for stamp in range(_RUN_RECORDS_KEEP + 30):
        _record(service, "job", 1_000 + stamp)

    removed = service._prune_run_records()

    assert removed == 30
    kept = _names(service)
    assert len(kept) == _RUN_RECORDS_KEEP
    # I 30 più vecchi, e non trenta a caso.
    assert f"job_{1_000}_aaa.json" not in kept
    assert f"job_{1_000 + _RUN_RECORDS_KEEP + 29}_aaa.json" in kept


def test_under_the_cap_it_touches_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for stamp in range(10):
        _record(service, "job", 1_000 + stamp)

    assert service._prune_run_records() == 0
    assert len(_names(service)) == 10


def test_a_name_it_does_not_understand_is_left_alone(tmp_path: Path) -> None:
    """Quel che non si capisce non si cancella.

    Cancellare per non aver capito è il modo di trasformare un'igiene in una
    perdita di dati — e questi file non li rilegge nessuno, quindi non c'è
    nemmeno un premio a essere zelanti.
    """
    service = _service(tmp_path)
    for stamp in range(_RUN_RECORDS_KEEP + 5):
        _record(service, "job", 1_000 + stamp)
    odd = service._run_records_dir / "senza-la-forma-giusta.json"
    odd.write_text("{}", encoding="utf-8")
    pure_odd = service._run_records_dir / "job_nonunnumero_x.json"
    pure_odd.write_text("{}", encoding="utf-8")

    service._prune_run_records()

    assert odd.exists()
    assert pure_odd.exists()


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    """La potatura è igiene, e l'igiene non deve poter impedire un avvio."""
    service = CronService(tmp_path / "cron" / "jobs.json")
    assert service._prune_run_records() == 0
    assert service._remove_run_records("qualsiasi") == 0
