"""Uno store cron illeggibile non fa sparire i promemoria in silenzio.

Contratto cambiato in 0.6.6, sulla falsariga di quello di `config.json` in
0.3.2. Prima: il file rotto veniva messo da parte e il gateway ripartiva senza
job, senza dirlo a nessuno — l'utente scopriva di aver perso i suoi promemoria
solo quando non suonavano. Ora, nell'ordine: si prova il ``.bak``, poi si
riparte vuoti, e in entrambi i casi lo si registra sul ``RuntimeContext``
perché la WebUI lo mostri. Si rifiuta di partire in un caso solo, quello in cui
ripartire distruggerebbe l'ultima copia: il file rotto non si è potuto spostare.
"""

import json
from pathlib import Path

import pytest

from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronPayload, CronSchedule
from jenny.runtime.context import get_runtime_context

_HOUR_MS = 3_600_000
_BROKEN = "{ questo non e' JSON "


@pytest.fixture(autouse=True)
def _reset_recovery_flags():
    """Il ``RuntimeContext`` è un singleton di processo: va ripulito a mano."""
    ctx = get_runtime_context()
    ctx.cron_recovered_from = None
    ctx.cron_quarantine_path = None
    yield
    ctx.cron_recovered_from = None
    ctx.cron_quarantine_path = None


def _reminder() -> CronJob:
    """Un promemoria dell'utente, non un job di sistema: è quello che si perde.

    Un job di sistema lo riscrive ``GatewayContainer.build`` a ogni avvio; il
    promemoria no, e nessuno se ne accorge finché non suona.
    """
    return CronJob(
        id="pillola",
        name="pillola",
        schedule=CronSchedule(kind="every", every_ms=8 * _HOUR_MS),
        # Contesto di consegna completo: senza, ``_enforce_agent_binding`` lo
        # disabilita all'avvio e il test misurerebbe quello invece del recupero.
        payload=CronPayload(
            kind="agent_turn",
            message="ricordami la pillola",
            session_key="unified:default",
            origin_channel="websocket",
            origin_chat_id="webui",
        ),
    )


def _dream_job() -> CronJob:
    return CronJob(
        id="dream",
        name="dream",
        schedule=CronSchedule(kind="every", every_ms=6 * _HOUR_MS),
        payload=CronPayload(kind="system_event"),
    )


def _backup_of(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _with_reminder_in_the_backup(path: Path) -> CronService:
    """Store con un promemoria dentro, e un ``.bak`` che lo contiene.

    Servono due salvataggi dopo l'aggiunta perché il backup conserva il
    contenuto *precedente* — stessa semantica di ``config/loader.py``, e per lo
    stesso motivo: così protegge anche da una scrittura valida ma sbagliata,
    non solo da un file danneggiato.
    """
    service = CronService(path)
    service.register_system_job(_dream_job())
    assert service._store is not None
    service._store.jobs.append(_reminder())
    service._save_store()
    service._save_store()
    return service


def test_healthy_store_records_no_recovery(tmp_path) -> None:
    """Il caso normale non deve accendere nessun avviso."""
    path = tmp_path / "cron" / "jobs.json"

    service = CronService(path)
    service.register_system_job(_dream_job())

    assert CronService(path).list_jobs()
    assert get_runtime_context().cron_recovered_from is None


def test_corrupt_store_recovers_the_reminders_from_the_backup(tmp_path) -> None:
    """Il ``.bak`` esiste apposta: i job dell'utente tornano indietro."""
    path = tmp_path / "cron" / "jobs.json"
    _with_reminder_in_the_backup(path)
    assert "pillola" in _backup_of(path).read_text(encoding="utf-8")

    path.write_text(_BROKEN, encoding="utf-8")

    reloaded = CronService(path)
    ids = {j.id for j in reloaded.list_jobs()}

    assert ids == {"dream", "pillola"}
    ctx = get_runtime_context()
    assert ctx.cron_recovered_from == "backup"
    assert ctx.cron_quarantine_path is not None
    # Il file rotto resta accanto, leggibile: è l'unico modo di capire cosa è
    # successo dopo il fatto.
    assert ctx.cron_quarantine_path.read_text(encoding="utf-8") == _BROKEN
    # E il backup è stato promosso a file vivo, altrimenti ogni avvio rifarebbe
    # il recupero e il primo salvataggio partirebbe da un grezzo rotto.
    assert {j["id"] for j in json.loads(path.read_text(encoding="utf-8"))["jobs"]} == ids


def test_corrupt_store_without_backup_starts_empty_and_says_so(tmp_path) -> None:
    """Senza backup si riparte vuoti — ma registrandolo, non in silenzio.

    È il caso che questo giro chiude: prima l'app partiva esattamente così, e
    nessuna schermata diceva che i promemoria non c'erano più.
    """
    path = tmp_path / "cron" / "jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(_BROKEN, encoding="utf-8")

    service = CronService(path)
    service.register_system_job(_dream_job())

    assert [j.id for j in service.list_jobs()] == ["dream"]
    ctx = get_runtime_context()
    assert ctx.cron_recovered_from == "empty"
    assert ctx.cron_quarantine_path is not None
    assert ctx.cron_quarantine_path.read_text(encoding="utf-8") == _BROKEN


def test_unusable_backup_falls_through_to_empty(tmp_path) -> None:
    """Un ``.bak`` rotto quanto il file vivo non deve bloccare l'avvio."""
    path = tmp_path / "cron" / "jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(_BROKEN, encoding="utf-8")
    _backup_of(path).write_text(_BROKEN, encoding="utf-8")

    service = CronService(path)
    service.register_system_job(_dream_job())

    assert [j.id for j in service.list_jobs()] == ["dream"]
    assert get_runtime_context().cron_recovered_from == "empty"


def test_corruption_after_a_good_start_warns_nobody(tmp_path) -> None:
    """Se in memoria c'è già tutto, l'utente non ha perso niente da avvisare.

    Il file può corrompersi dopo un avvio riuscito (lettura parziale, processo
    ucciso a metà): lì lo snapshot vivo vince sul ripiego, e mostrare "i tuoi
    promemoria non ci sono più" sarebbe falso. Un avviso falso è il modo più
    veloce per far ignorare quelli veri.
    """
    path = tmp_path / "cron" / "jobs.json"
    service = _with_reminder_in_the_backup(path)

    path.write_text(_BROKEN, encoding="utf-8")
    jobs = service.list_jobs()

    assert {j.id for j in jobs} == {"dream", "pillola"}
    assert get_runtime_context().cron_recovered_from is None


def test_refuses_to_start_only_when_the_broken_file_cannot_be_set_aside(
    tmp_path, monkeypatch
) -> None:
    """L'unico rifiuto rimasto, e il motivo per cui è rimasto.

    Se il rename fallisce il file rotto è ancora al suo posto: il primo
    salvataggio ci scriverebbe sopra e i job dell'utente non esisterebbero più
    da nessuna parte. Ripartire vuoti è recuperabile solo *perché* la copia
    forense esiste; senza quella, non partire è la risposta giusta.
    """
    path = tmp_path / "cron" / "jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(_BROKEN, encoding="utf-8")

    def _no_rename(src, dst):
        raise OSError("read-only filesystem")

    # La quarantena è ``utils.path.quarantine``, che sposta con ``os.replace``.
    monkeypatch.setattr("jenny.utils.path.os.replace", _no_rename)

    service = CronService(path)

    with pytest.raises(RuntimeError, match="could not be set aside"):
        service.register_system_job(_dream_job())

    # E il file rotto è ancora lì, intatto: nessuno ci ha scritto sopra.
    assert path.read_text(encoding="utf-8") == _BROKEN


def test_a_broken_live_file_does_not_destroy_the_good_backup(tmp_path) -> None:
    """La guardia dentro ``_rotate_backup``.

    Copiare il file vivo nel ``.bak`` senza prima rileggerlo come JSON
    trasformerebbe il primo salvataggio partito da uno store rotto nella
    distruzione dell'unica copia buona rimasta.
    """
    path = tmp_path / "cron" / "jobs.json"
    service = _with_reminder_in_the_backup(path)
    good_backup = _backup_of(path).read_text(encoding="utf-8")

    path.write_text(_BROKEN, encoding="utf-8")
    service._rotate_backup()

    assert _backup_of(path).read_text(encoding="utf-8") == good_backup


def test_two_corruptions_do_not_overwrite_each_other(tmp_path) -> None:
    """``rename`` non chiede il permesso: due prove nello stesso secondo restano due."""
    path = tmp_path / "cron" / "jobs.json"
    path.parent.mkdir(parents=True)

    path.write_text(_BROKEN, encoding="utf-8")
    CronService(path).list_jobs()
    path.write_text(_BROKEN + "e ancora", encoding="utf-8")
    CronService(path).list_jobs()

    kept = sorted(p.read_text(encoding="utf-8") for p in path.parent.glob("jobs.corrupt-*.json"))
    assert kept == [_BROKEN, _BROKEN + "e ancora"]
