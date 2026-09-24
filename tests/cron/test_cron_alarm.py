"""Cron guidato dalle sveglie di sistema (``config.power.alarm_driven_cron``).

Il bug che questi test presidiano: ``asyncio.sleep`` dorme su un orologio
monotono che non avanza mentre il SoC è sospeso, quindi a schermo spento un job
da 30 minuti scattava dopo 83. La sveglia RTC dell'OS è l'unica che arrivi
comunque; il timer asyncio resta accanto, e la parte delicata è che i due non
facciano girare due volte lo stesso job.

Il bridge Kotlin non esiste su desktop: si sostituiscono le funzioni del modulo
``power`` (stesso idioma di ``tests/runtime/test_power.py``, che monkeypatcha il
modulo e non i simboli importati).
"""

from __future__ import annotations

import asyncio
import functools
import time

import pytest
from support.aio import wait_until

from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule
from jenny.runtime import power


def _bound_chat(chat_id: str = "chat-1") -> dict[str, str]:
    return {
        "session_key": f"websocket:{chat_id}",
        "origin_channel": "websocket",
        "origin_chat_id": chat_id,
    }


# La scadenza di questo file: un secondo.
_wait_until = functools.partial(wait_until, timeout=1.0)


async def _settle(service=None, *, timeout: float = 5.0) -> None:
    """Aspetta i task delle sveglie finché non sono finiti.

    ``_set_wake_alarm`` dichiara l'obiettivo e delega l'applicazione a un task,
    che ``CronService`` tiene in ``_alarm_tasks``: è quell'insieme l'oggetto da
    aspettare, e si rilegge a ogni giro perché un task può aprirne un altro.

    Contare i giri di loop reggeva solo grazie alla fixture: ``alarms``
    sostituisce ``power.schedule_wake`` con una coroutine liscia, mentre il
    ``power._call`` vero passa da ``asyncio.to_thread``. Con quello in mezzo,
    cinque ``sleep(0)`` non garantiscono niente — è la forma che ha fatto rosso
    la CI su Dream il 28/08/2026. Senza *service* resta un singolo giro di cortesia,
    per i pochi punti che non hanno il servizio sotto mano.
    """
    if service is None:
        await asyncio.sleep(0)
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        pending = {t for t in service._alarm_tasks if not t.done()}
        if not pending:
            return
        remaining = deadline - loop.time()
        assert remaining > 0, f"task delle sveglie ancora in volo dopo {timeout}s: {pending}"
        await asyncio.wait(pending, timeout=remaining)


class _Alarms:
    """Controparte del bridge: registra sveglie programmate e annullate."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, int]] = []
        self.cancelled: list[int] = []

    async def schedule_wake(self, at_ms: int, request_code: int) -> bool:
        self.scheduled.append((at_ms, request_code))
        return True

    async def cancel_wake(self, request_code: int) -> bool:
        self.cancelled.append(request_code)
        return True


@pytest.fixture(autouse=True)
def _reset_power_state():
    power.reset_power_state()
    yield
    power.reset_power_state()


@pytest.fixture
def alarms(monkeypatch: pytest.MonkeyPatch) -> _Alarms:
    """Sveglie attive e osservabili. ``bind_wake_loop`` resta quello vero:
    l'aggancio del loop è metà del meccanismo sotto test."""
    recorder = _Alarms()
    monkeypatch.setattr(power, "alarm_driven_cron_enabled", lambda: True)
    monkeypatch.setattr(power, "schedule_wake", recorder.schedule_wake)
    monkeypatch.setattr(power, "cancel_wake", recorder.cancel_wake)
    return recorder


# ── lockstep fra timer asyncio e sveglia di sistema ──


async def test_alarm_is_armed_for_the_real_deadline(tmp_path, alarms: _Alarms) -> None:
    """La sveglia punta alla scadenza vera, non al tetto di ``max_sleep_ms``."""
    service = CronService(tmp_path / "cron" / "jobs.json", max_sleep_ms=100)
    await service.start()
    try:
        job = service.add_job(
            name="later",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat(),
        )
        await _settle(service)

        assert job.state.next_run_at_ms is not None
        assert (job.state.next_run_at_ms, power.WAKE_REQUEST_CODE_CRON) in alarms.scheduled
        # Sotto 9000: sopra ci sono i request code riservati a Kotlin
        # (auto-recovery del service, watchdog).
        assert power.WAKE_REQUEST_CODE_CRON < 9000
    finally:
        service.stop()


async def test_no_deadline_arms_no_alarm(tmp_path, alarms: _Alarms) -> None:
    """Senza job in agenda non si sveglia il telefono per niente."""
    service = CronService(tmp_path / "cron" / "jobs.json", max_sleep_ms=100)
    await service.start()
    try:
        await _settle(service)
        assert alarms.scheduled == []
    finally:
        service.stop()


async def test_stop_cancels_the_alarm(tmp_path, alarms: _Alarms) -> None:
    """La sveglia vive nell'AlarmManager: fermare il servizio deve smontarla."""
    service = CronService(tmp_path / "cron" / "jobs.json", max_sleep_ms=100)
    await service.start()
    service.add_job(
        name="later",
        schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="tick",
        **_bound_chat(),
    )
    await _settle(service)
    assert alarms.scheduled

    service.stop()
    await _settle(service)

    assert alarms.cancelled == [power.WAKE_REQUEST_CODE_CRON]


async def test_mutation_during_an_inflight_job_leaves_the_alarm_alone(
    tmp_path, alarms: _Alarms
) -> None:
    """Il no-op di ``_timer_active`` vale anche per la sveglia.

    Un ``add_job`` mentre un job gira non deve toccare né il task del timer
    (regressione già coperta in test_cron_service.py) né la sveglia che copre la
    prossima scadenza: disarmarla e riarmarla lascerebbe una finestra in cui il
    device può sospendersi senza nessuna sveglia in agenda.
    """
    entered = asyncio.Event()
    release = asyncio.Event()

    async def on_job(job):
        entered.set()
        await release.wait()

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job, max_sleep_ms=100)
    await service.start()
    try:
        job_a = service.add_job(
            name="long-running",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat("a"),
        )
        job_a.state.next_run_at_ms = int(time.time() * 1000) - 1_000
        service._save_store()
        service._arm_timer()

        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert service._timer_active is True
        await _settle(service)
        scheduled_before = list(alarms.scheduled)
        cancelled_before = list(alarms.cancelled)

        service.add_job(
            name="other",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
            **_bound_chat("b"),
        )
        await _settle(service)

        assert alarms.scheduled == scheduled_before
        assert alarms.cancelled == cancelled_before

        release.set()
        # A job finito il riarmo riprende, sveglia compresa.
        await _wait_until(lambda: len(alarms.scheduled) > len(scheduled_before))
    finally:
        release.set()
        service.stop()


# ── tick di sveglia ──


async def test_wake_tick_runs_due_jobs_like_a_timer_tick(tmp_path, alarms: _Alarms) -> None:
    """Un risveglio esterno esegue i job scaduti esattamente come il timer.

    Il timer qui dorme a lungo (100s): se il job gira, è perché il tick di
    sveglia — consegnato dal vero ``power.on_wake_tick``, cioè dal percorso che
    Kotlin usa — ha svegliato il servizio.
    """
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job, max_sleep_ms=100_000)
    await service.start()
    try:
        job = service.add_job(
            name="missed",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat(),
        )
        await _settle(service)
        # Scadenza mancata mentre il device dormiva, senza riarmare il timer:
        # il tick di sveglia deve essere l'unica cosa che lo rimette in moto.
        job.state.next_run_at_ms = int(time.time() * 1000) - 1_000
        service._save_store()
        assert calls == []

        assert power.on_wake_tick() is True

        await _wait_until(lambda: calls == [job.id])
    finally:
        service.stop()


async def test_timer_and_wake_tick_together_run_each_job_once(tmp_path, alarms: _Alarms) -> None:
    """Timer e sveglia che scattano insieme: ogni job scaduto gira UNA volta.

    È la corsa vera su un device: la sveglia RTC arriva e, un istante dopo, il
    ``asyncio.sleep`` scaduto durante la sospensione si sveglia anche lui.
    """
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job, max_sleep_ms=100_000)
    await service.start()
    try:
        job = service.add_job(
            name="due",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat(),
        )
        job.state.next_run_at_ms = int(time.time() * 1000) - 1_000
        service._save_store()

        # Il tick in attesa va tolto di mezzo *prima* di accendere l'evento,
        # altrimenti sarebbe lui a consumarlo e non ci sarebbe nessuna corsa da
        # osservare.
        assert service._timer_task is not None
        service._timer_task.cancel()
        assert power.on_wake_tick() is True
        await _settle(service)
        assert service._wake_event is not None and service._wake_event.is_set()

        # Da qui: evento GIÀ acceso e timer a delay zero (job scaduto). I due
        # rami di ``asyncio.wait`` sono pronti allo stesso giro di loop.
        service._arm_timer()

        await _wait_until(lambda: calls == [job.id])
        # Il giro successivo (evento azzerato, scadenza spostata avanti di un'ora)
        # non deve rieseguire nulla.
        await asyncio.sleep(0.1)
        assert calls == [job.id]
    finally:
        service.stop()


async def test_wake_tick_during_an_inflight_job_does_not_re_enter_it(
    tmp_path, alarms: _Alarms
) -> None:
    """La corsa che può davvero eseguire due volte lo stesso job.

    ``_on_timer`` protegge dal doppio giro solo perché ``_execute_job`` sposta
    ``next_run_at_ms`` avanti *prima* che il giro successivo rifiltri i job. Ma
    lo sposta alla FINE dell'esecuzione: due ``_on_timer`` concorrenti sullo
    stesso job scaduto lo vedrebbero entrambi da eseguire. Qui il tick di
    sveglia arriva mentre il job è a metà, ed è il caso in cui una versione che
    trattasse l'evento come un secondo trigger indipendente lo rieseguirebbe.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)
        entered.set()
        await release.wait()

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job, max_sleep_ms=100_000)
    await service.start()
    try:
        job = service.add_job(
            name="slow",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat(),
        )
        job.state.next_run_at_ms = int(time.time() * 1000) - 1_000
        service._save_store()
        service._arm_timer()

        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert power.on_wake_tick() is True
        await _settle(service)
        assert calls == [job.id]

        release.set()
        await _wait_until(
            lambda: (loaded := service.get_job(job.id)) is not None
            and loaded.state.last_status == "ok"
        )
        # Il tick rimasto acceso provoca un giro in più, che deve trovare
        # l'agenda vuota invece del job appena finito.
        await asyncio.sleep(0.1)
        assert calls == [job.id]
        loaded = service.get_job(job.id)
        assert loaded is not None
        assert len(loaded.state.run_history) == 1
    finally:
        release.set()
        service.stop()


# ── interruttore spento ──


async def test_disabled_alarm_driven_cron_falls_back_to_pure_asyncio(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``alarm_driven_cron`` spento: nessuna sveglia, nessun evento, timer puro."""
    recorder = _Alarms()
    monkeypatch.setattr(power, "alarm_driven_cron_enabled", lambda: False)
    monkeypatch.setattr(power, "schedule_wake", recorder.schedule_wake)
    monkeypatch.setattr(power, "cancel_wake", recorder.cancel_wake)

    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=on_job, max_sleep_ms=50)
    await service.start()
    try:
        job = service.add_job(
            name="plain",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat(),
        )
        job.state.next_run_at_ms = int(time.time() * 1000) - 1_000
        service._save_store()
        service._arm_timer()

        await _wait_until(lambda: calls == [job.id])
        assert service._wake_event is None
        assert recorder.scheduled == []
        assert recorder.cancelled == []
    finally:
        service.stop()
        await _settle(service)
        # Nemmeno lo stop parla col bridge quando l'interruttore è giù.
        assert recorder.cancelled == []


async def test_config_switch_is_read_through_the_real_power_gate(tmp_path, monkeypatch) -> None:
    """Il gate legge davvero ``config.power.alarm_driven_cron``, non un default."""
    from jenny.config.schema import PowerConfig

    class _Cfg:
        power = PowerConfig(alarm_driven_cron=False)

    monkeypatch.setattr(power, "get_android_context", lambda: object())
    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: _Cfg())

    assert power.alarm_driven_cron_enabled() is False

    class _CfgOn:
        power = PowerConfig(alarm_driven_cron=True)

    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: _CfgOn())
    assert power.alarm_driven_cron_enabled() is True

    # Fuori da Android resta spento comunque: non c'è nessun AlarmManager.
    monkeypatch.setattr(power, "get_android_context", lambda: None)
    assert power.alarm_driven_cron_enabled() is False
