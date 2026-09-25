import asyncio
import functools
import json
import time

import pytest
from support.aio import wait_until
from support.cron import disable_job

from jenny.cron.service import CronJobSkippedError, CronService
from jenny.cron.types import CronJob, CronJobSilencedError, CronPayload, CronSchedule
from jenny.session.keys import UNIFIED_SESSION_KEY

# La scadenza di questo file: un secondo.
_wait_until = functools.partial(wait_until, timeout=1.0)


async def _settle(*, ignore: set[asyncio.Task] | None = None, timeout: float = 1.0) -> None:
    """Cede il controllo finché non resta nessun task pendente oltre a *ignore*.

    Non conta i giri di loop. Un ``for _ in range(N): await asyncio.sleep(0)``
    non fa passare il *tempo*, solo il controllo: quanti giri servano dipende da
    quante volte la catena di callback rimbalza, e un task che attraversa
    ``asyncio.to_thread`` può non essere finito dopo N giri qualunque.
    """
    ignore = (ignore or set()) | {asyncio.current_task()}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = {t for t in asyncio.all_tasks() if t not in ignore and not t.done()}
        if not pending:
            return
        await asyncio.wait(pending, timeout=max(0.0, deadline - time.monotonic()))


def _bound_chat(chat_id: str = "chat-1") -> dict[str, str]:
    return {
        "session_key": f"websocket:{chat_id}",
        "origin_channel": "websocket",
        "origin_chat_id": chat_id,
    }


def test_add_job_rejects_unknown_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown timezone 'America/Vancovuer'"):
        service.add_job(
            name="tz typo",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancovuer"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


@pytest.mark.parametrize(
    ("expr", "why"),
    [
        ("0 25 * * *", "out of range"),       # un'ora che non esiste
        ("0 9 * *", "5, 6 or 7 fields"),       # un campo in meno
        ("0 0 31 2 *", "has no run"),          # si legge, ma non scatta mai
    ],
)
def test_add_job_rejects_an_expression_that_would_never_fire(tmp_path, expr, why) -> None:
    """``_compute_next_run`` inghiotte l'errore e torna ``None``: senza questo
    rifiuto il job nasceva abilitato, senza prossima esecuzione, e taceva per
    sempre."""
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match=why):
        service.add_job(
            name="mai", schedule=CronSchedule(kind="cron", expr=expr), message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_the_cron_tool_reports_a_bad_expression_as_an_error(tmp_path) -> None:
    from jenny.agent.tools.context import RequestContext
    from jenny.agent.tools.cron import CronTool

    tool = CronTool(CronService(tmp_path / "cron" / "jobs.json"), default_timezone="Europe/Rome")
    tool.set_context(
        RequestContext(channel="websocket", chat_id="chat-1", session_key="websocket:chat-1")
    )

    result = tool._add_job(None, "Standup", None, "0 25 * * *", None, None)

    assert result.startswith("Error: invalid cron expression '0 25 * * *'"), result


def test_add_job_accepts_valid_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="tz ok",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
        message="hello",
        **_bound_chat(),
    )

    assert job.schedule.tz == "America/Vancouver"
    assert job.state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_unbound_agent_jobs_are_disabled_on_add(tmp_path) -> None:
    called: list[str] = []

    async def on_job(job):
        called.append(job.id)

    service = CronService(
        tmp_path / "cron" / "jobs.json",
        on_job=on_job,
    )
    job = service.add_job(
        name="unbound",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    assert job.enabled is False
    assert job.state.next_run_at_ms is None
    assert job.state.last_status == "error"
    assert "missing bound session delivery context" in (job.state.last_error or "")
    assert await service.run_job(job.id, force=True) is False
    assert called == []


def test_unbound_agent_jobs_are_disabled_on_load(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "unbound-1",
                        "name": "Unbound reminder",
                        "enabled": True,
                        "schedule": {"kind": "every", "everyMs": 60_000},
                        "payload": {
                            "kind": "agent_turn",
                            "message": "check status",
                        },
                        "state": {"nextRunAtMs": 1},
                        "createdAtMs": 1,
                        "updatedAtMs": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    job = CronService(store_path).get_job("unbound-1")

    assert job is not None
    assert job.enabled is False
    assert job.state.next_run_at_ms is None
    assert job.state.last_status == "error"
    assert "missing bound session delivery context" in (job.state.last_error or "")


def test_add_job_preserves_origin_delivery_context(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    metadata = {"thread": {"id": "1234567890.123456", "kind": "channel"}}

    job = service.add_job(
        name="bound thread",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        session_key="websocket:C123:1234567890.123456",
        origin_channel="websocket",
        origin_chat_id="C123",
        origin_metadata=metadata,
    )

    assert job.payload.origin_channel == "websocket"
    assert job.payload.origin_chat_id == "C123"
    assert job.payload.origin_metadata == metadata

    raw = json.loads((tmp_path / "cron" / "action.jsonl").read_text(encoding="utf-8"))
    payload = raw["params"]["payload"]
    assert payload["origin_channel"] == "websocket"
    assert payload["origin_chat_id"] == "C123"
    assert payload["origin_metadata"] == metadata

    reloaded = service.get_job(job.id)
    assert reloaded is not None
    assert reloaded.payload.origin_channel == "websocket"
    assert reloaded.payload.origin_chat_id == "C123"
    assert reloaded.payload.origin_metadata == metadata


@pytest.mark.asyncio
async def test_origin_survives_reload_and_a_legacy_key_is_migrated(tmp_path) -> None:
    """Lo store conserva quel che il chiamante ha scritto; il caricamento lo aggiorna.

    ``websocket:C123:...`` era una chiave *thread-scoped*: una sessione a sé,
    quando le sessioni erano molte. Oggi ``bound_runner`` la usa come chiave del
    turno, quindi un job che se la porta dietro girerebbe per sempre in un file
    di sessione fantasma invece che nella conversazione. Il caricamento la
    riporta alla conversazione unica; il file no, perché un caricamento non deve
    scrivere — si riallinea da sé alla prima modifica dello store.

    Quel che regge la consegna resta intatto, ed è la metà da provare qui:
    ``origin_channel`` / ``origin_chat_id`` / ``origin_metadata`` sono
    l'attaccamento alla chat e non c'entrano con la sessione.
    """
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    await service.start()
    meta = {"thread": {"id": "1234567890.123456", "kind": "channel"}}
    try:
        job = service.add_job(
            name="thread test",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
            session_key="websocket:C123:1234567890.123456",
            origin_channel="websocket",
            origin_chat_id="C123",
            origin_metadata=meta,
        )
    finally:
        service.stop()

    raw = json.loads(store_path.read_text(encoding="utf-8"))
    payload = raw["jobs"][0]["payload"]
    assert payload["sessionKey"] == "websocket:C123:1234567890.123456"
    assert payload["originChannel"] == "websocket"
    assert payload["originChatId"] == "C123"
    assert payload["originMetadata"] == meta

    reloaded = CronService(store_path).get_job(job.id)
    assert reloaded is not None
    assert reloaded.payload.session_key == UNIFIED_SESSION_KEY
    assert reloaded.payload.origin_channel == "websocket"
    assert reloaded.payload.origin_chat_id == "C123"
    assert reloaded.payload.origin_metadata == meta


@pytest.mark.asyncio
async def test_legacy_payload_with_channel_meta_key_still_loads(tmp_path) -> None:
    """Payload persistiti da versioni precedenti con la chiave rimossa
    ``channelMeta`` devono caricarsi senza errori (extra ignorato)."""
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "version": 1,
        "jobs": [
            {
                "id": "legacy01",
                "name": "legacy",
                "enabled": True,
                "schedule": {"kind": "every", "everyMs": 60_000},
                "payload": {
                    "kind": "agent_turn",
                    "message": "hello",
                    "channelMeta": {"thread": {"id": "42"}},
                    "sessionKey": "websocket:C1:42",
                },
                "state": {},
            }
        ],
    }
    store_path.write_text(json.dumps(legacy), encoding="utf-8")

    reloaded = CronService(store_path).get_job("legacy01")
    assert reloaded is not None
    assert reloaded.payload.message == "hello"
    # Migrata al caricamento, come ogni chiave utente della forma vecchia.
    assert reloaded.payload.session_key == UNIFIED_SESSION_KEY
    assert not hasattr(reloaded.payload, "channel_meta")


@pytest.mark.asyncio
async def test_execute_job_records_run_history(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="hist",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert loaded is not None
    assert len(loaded.state.run_history) == 1
    rec = loaded.state.run_history[0]
    assert rec.status == "ok"
    assert rec.duration_ms >= 0
    assert rec.error is None


@pytest.mark.asyncio
async def test_run_history_records_errors(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"

    async def fail(_):
        raise RuntimeError("boom")

    service = CronService(store_path, on_job=fail)
    job = service.add_job(
        name="fail",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "error"
    assert loaded.state.run_history[0].error == "boom"


@pytest.mark.asyncio
async def test_run_history_records_skipped_jobs(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"

    async def skip(_):
        raise CronJobSkippedError("missing session binding")

    service = CronService(store_path, on_job=skip)
    job = service.add_job(
        name="skip",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert loaded is not None
    assert loaded.state.last_status == "skipped"
    assert loaded.state.last_error == "missing session binding"
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "skipped"
    assert loaded.state.run_history[0].error == "missing session binding"


@pytest.mark.asyncio
async def test_a_monitor_that_stayed_quiet_is_recorded_as_silenced_not_failed(tmp_path) -> None:
    """Un monitor che tace è un esito riuscito: ``silenced``, senza ``last_error``."""
    store_path = tmp_path / "cron" / "jobs.json"

    async def silent(_):
        raise CronJobSilencedError("nothing to report")

    service = CronService(store_path, on_job=silent)
    job = service.add_job(
        name="monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="check the inbox",
        mode="monitor",
        **_bound_chat(),
    )
    await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert loaded is not None
    assert loaded.state.last_status == "silenced"
    assert loaded.state.last_error is None
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "silenced"
    assert loaded.state.run_history[0].error is None
    # Il job resta armato: tacere non è un motivo per smettere di controllare.
    assert loaded.enabled is True
    assert loaded.state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_silence_clears_the_error_left_by_the_previous_failed_run(tmp_path) -> None:
    """Il giro silenzioso deve azzerare ``last_error``, non ereditarlo dal giro rotto.

    Senza la pulizia, un monitor sano continuerebbe a mostrare per sempre
    l'errore di un ciclo precedente e sembrerebbe guasto.
    """
    store_path = tmp_path / "cron" / "jobs.json"
    outcomes: list[Exception | None] = [RuntimeError("provider down"), CronJobSilencedError()]

    async def flaky(_):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    service = CronService(store_path, on_job=flaky)
    job = service.add_job(
        name="monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="check the inbox",
        mode="monitor",
        **_bound_chat(),
    )

    await service.run_job(job.id)
    failed = service.get_job(job.id)
    assert failed is not None and failed.state.last_error == "provider down"

    await service.run_job(job.id)
    quiet = service.get_job(job.id)
    assert quiet is not None
    assert quiet.state.last_status == "silenced"
    assert quiet.state.last_error is None
    assert [r.status for r in quiet.state.run_history] == ["error", "silenced"]


def test_add_job_refuses_a_mode_it_cannot_execute(tmp_path) -> None:
    """Un modo ignoto da un chiamante vivo è un bug del chiamante: si solleva."""
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown cron mode 'telepathy'"):
        service.add_job(
            name="weird",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
            mode="telepathy",  # type: ignore[arg-type]
            **_bound_chat(),
        )

    assert service.list_jobs(include_disabled=True) == []


def test_omitting_the_mode_creates_a_reminder_without_complaining(tmp_path) -> None:
    """Il default omesso non deve mai sollevare: è il caso del 99% dei chiamanti."""
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="plain",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )

    assert job.payload.mode == "reminder"


def test_add_job_accepts_monitor_mode(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="watcher",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="check the inbox",
        mode="monitor",
        **_bound_chat(),
    )

    assert job.payload.mode == "monitor"


def test_mode_must_be_passed_by_keyword(tmp_path) -> None:
    """``mode`` è keyword-only: un positional in più non deve finirci dentro."""
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(TypeError):
        service.add_job(
            "positional",
            CronSchedule(kind="every", every_ms=60_000),
            "hello",
            False,
            "websocket:chat-1",
            "websocket",
            "chat-1",
            {},
            "monitor",  # type: ignore[misc]
        )


@pytest.mark.asyncio
async def test_run_history_records_job_cancellation(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"

    async def cancel(_):
        raise asyncio.CancelledError("turn cancelled")

    service = CronService(store_path, on_job=cancel)
    job = service.add_job(
        name="cancel",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )

    assert await service.run_job(job.id) is True

    loaded = service.get_job(job.id)
    assert loaded is not None
    assert loaded.state.last_status == "error"
    assert loaded.state.last_error == "turn cancelled"
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "error"
    assert loaded.state.run_history[0].error == "turn cancelled"
    assert loaded.state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_run_history_trimmed_to_max(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="trim",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    for _ in range(25):
        await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert len(loaded.state.run_history) == CronService._MAX_RUN_HISTORY


@pytest.mark.asyncio
async def test_run_history_persisted_to_disk(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="persist",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    await service.run_job(job.id)

    raw = json.loads(store_path.read_text())
    history = raw["jobs"][0]["state"]["runHistory"]
    assert len(history) == 1
    assert history[0]["status"] == "ok"
    assert "runAtMs" in history[0]
    assert "durationMs" in history[0]

    fresh = CronService(store_path)
    loaded = fresh.get_job(job.id)
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "ok"


@pytest.mark.asyncio
async def test_run_job_disabled_does_not_flip_running_state(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="disabled",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    disable_job(service, job.id)

    result = await service.run_job(job.id)

    assert result is False
    assert service._running is False


@pytest.mark.asyncio
async def test_run_job_preserves_running_service_state(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    service._running = True
    job = service.add_job(
        name="manual",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )

    result = await service.run_job(job.id, force=True)

    assert result is True
    assert service._running is True
    service.stop()


@pytest.mark.asyncio
async def test_running_service_honors_external_disable(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job) -> None:
        called.append(job.id)

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="external-disable",
        schedule=CronSchedule(kind="every", every_ms=200),
        message="hello",
        **_bound_chat(),
    )
    await service.start()
    try:
        # Disable before yielding back to the event loop. On slower Windows CI
        # a short sleep here can overrun the 200ms schedule and let the job fire
        # before the external update is written.
        external = CronService(store_path)
        updated = disable_job(external, job.id)
        assert updated.enabled is False

        await asyncio.sleep(0.35)
        assert called == []
    finally:
        service.stop()


def test_remove_job_refuses_system_jobs(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    service.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=CronSchedule(kind="cron", expr="0 */2 * * *", tz="UTC"),
        payload=CronPayload(kind="system_event"),
    ))

    result = service.remove_job("dream")

    assert result == "protected"
    assert service.get_job("dream") is not None


@pytest.mark.asyncio
async def test_start_server_not_jobs(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    called = []
    async def on_job(job):
        called.append(job.name)

    service = CronService(store_path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    assert len(service.list_jobs()) == 0

    service2 = CronService(tmp_path / "cron" / "jobs.json")
    service2.add_job(
        name="hist",
        schedule=CronSchedule(kind="every", every_ms=100),
        message="hello",
        **_bound_chat(),
    )
    assert len(service.list_jobs()) == 1
    await _wait_until(lambda: bool(called), timeout=0.8)
    assert len(called) != 0
    service.stop()


@pytest.mark.asyncio
async def test_subsecond_job_not_delayed_to_one_second(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    called = []

    async def on_job(job):
        called.append(job.name)

    service = CronService(store_path, on_job=on_job, max_sleep_ms=5000)
    service.add_job(
        name="fast",
        schedule=CronSchedule(kind="every", every_ms=100),
        message="hello",
        **_bound_chat(),
    )
    await service.start()
    try:
        await asyncio.sleep(0.35)
        assert called
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_running_service_picks_up_external_add(tmp_path):
    """A running service should detect and execute a job added by another instance."""
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job):
        called.append(job.name)

    service = CronService(store_path, on_job=on_job, max_sleep_ms=100)
    service.add_job(
        name="heartbeat",
        schedule=CronSchedule(kind="every", every_ms=100),
        message="tick",
        **_bound_chat("heartbeat"),
    )
    await service.start()
    try:
        await asyncio.sleep(0.05)

        external = CronService(store_path)
        external.add_job(
            name="external",
            schedule=CronSchedule(kind="every", every_ms=100),
            message="ping",
            **_bound_chat("external"),
        )

        await _wait_until(lambda: "external" in called, timeout=0.8)
        assert "external" in called
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_add_job_during_jobs_exec(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    run_once = True

    async def on_job(job):
        nonlocal run_once
        if run_once:
            service2 = CronService(store_path, on_job=lambda x: asyncio.sleep(0))
            service2.add_job(
                name="test",
                schedule=CronSchedule(kind="every", every_ms=150),
                message="tick",
                **_bound_chat("test"),
            )
            run_once = False

    service = CronService(store_path, on_job=on_job, max_sleep_ms=100)
    service.add_job(
        name="heartbeat",
        schedule=CronSchedule(kind="every", every_ms=100),
        message="tick",
        **_bound_chat("heartbeat"),
    )
    assert len(service.list_jobs()) == 1
    await service.start()
    try:
        await _wait_until(lambda: len(service.list_jobs()) == 2, timeout=0.8)
        jobs = service.list_jobs()
        assert len(jobs) == 2
        assert "test" in [j.name for j in jobs]
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_external_update_preserves_run_history_records(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="history",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    await service.run_job(job.id, force=True)

    external = CronService(store_path)
    disable_job(external, job.id)

    fresh = CronService(store_path)
    loaded = fresh.get_job(job.id)
    assert loaded is not None
    assert loaded.state.run_history
    assert loaded.state.run_history[0].status == "ok"

    fresh._running = True
    fresh._save_store()


# ── timer race regression tests ──


@pytest.mark.asyncio
async def test_timer_execution_is_not_rolled_back_by_list_jobs_reload(tmp_path):
    """list_jobs() during _on_timer should not replace the active store and re-run the same due job."""
    store_path = tmp_path / "cron" / "jobs.json"
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)
        # Simulate frontend polling list_jobs while the timer callback is mid-execution.
        service.list_jobs(include_disabled=True)
        await asyncio.sleep(0)

    service = CronService(store_path, on_job=on_job)
    service._running = True
    service._load_store()
    service._arm_timer = lambda: None

    job = service.add_job(
        name="race",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        **_bound_chat(),
    )
    job.state.next_run_at_ms = max(1, int(time.time() * 1000) - 1_000)
    service._save_store()

    await service._on_timer()
    await service._on_timer()

    assert calls == [job.id]
    loaded = service.get_job(job.id)
    assert loaded is not None
    assert loaded.state.last_run_at_ms is not None
    assert loaded.state.next_run_at_ms is not None
    assert loaded.state.next_run_at_ms > loaded.state.last_run_at_ms


@pytest.mark.asyncio
async def test_concurrent_job_mutation_does_not_cancel_inflight_job(tmp_path) -> None:
    """Regression: add_job/remove_job on a DIFFERENT job
    must not cancel a job whose execution is currently in-flight inside the
    timer task. Previously, _arm_timer() unconditionally cancelled
    self._timer_task -- the same task running the in-flight job's turn --
    which aborted it mid-execution and silently dropped its result, leaving
    next_run_at_ms stale so it double-fired on the next tick.
    """
    store_path = tmp_path / "cron" / "jobs.json"
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)
        entered.set()
        await release.wait()

    service = CronService(store_path, on_job=on_job, max_sleep_ms=100)
    await service.start()
    try:
        job_a = service.add_job(
            name="long-running",
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="tick",
            **_bound_chat("a"),
        )
        # Force job A to be immediately due, then trigger a tick.
        job_a.state.next_run_at_ms = int(time.time() * 1000) - 1000
        service._save_store()
        service._arm_timer()

        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert service._timer_active is True
        inflight_task = service._timer_task
        assert inflight_task is not None

        # Simulate an unrelated chat session mutating a DIFFERENT job while
        # job A's turn is still in-flight (mid-await on `release.wait()`).
        job_b = service.add_job(
            name="other-add",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
            **_bound_chat("b"),
        )
        service.remove_job(job_b.id)

        # ``Task.cancelling()`` conta le richieste di cancellazione ricevute e
        # non si azzera quando vengono consegnate: registra quindi se
        # ``cancel()`` sia MAI stato chiamato, senza dipendere da quando la
        # cancellazione arriverebbe. Le asserzioni qui sono negative, e un drain
        # a giri fissi le sostiene male: quanti giri bastino dipende dalla
        # catena di callback, non da questo test. ``_arm_timer`` è sincrona e la
        # sua guardia esce subito, quindi una cancellazione sarebbe già avvenuta
        # dentro le chiamate qui sopra — questo la coglie sul fatto, invece di
        # aspettare che si manifesti a valle.
        assert inflight_task.cancelling() == 0

        # E in più: nessun task pendente resta a poter cancellare più tardi.
        await _settle(ignore={inflight_task})

        assert inflight_task.cancelling() == 0
        assert service._timer_task is inflight_task
        assert not inflight_task.done()
        assert not inflight_task.cancelled()
        # Job A must not have been re-entered/restarted.
        assert calls == [job_a.id]

        release.set()
        await _wait_until(
            lambda: (loaded := service.get_job(job_a.id)) is not None
            and loaded.state.last_status == "ok",
            timeout=1.0,
        )

        loaded = service.get_job(job_a.id)
        assert loaded is not None
        assert loaded.state.last_status == "ok"
        assert len(loaded.state.run_history) == 1
        assert loaded.state.run_history[0].status == "ok"
        assert loaded.state.next_run_at_ms is not None
        assert loaded.state.next_run_at_ms > int(time.time() * 1000)
        # No silent double-fire caused by an aborted, unsaved execution.
        assert calls == [job_a.id]
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_list_jobs_during_on_job_does_not_cause_stale_reload(tmp_path) -> None:
    """Regression: if the bot calls list_jobs (which reloads from disk) during
    on_job execution, the in-memory next_run_at_ms update must not be lost.
    Previously this caused an infinite re-trigger loop."""
    store_path = tmp_path / "cron" / "jobs.json"
    execution_count = 0

    async def on_job_that_lists(job):
        nonlocal execution_count
        execution_count += 1
        # Simulate the bot calling cron(action=list) mid-execution
        service.list_jobs()

    service = CronService(store_path, on_job=on_job_that_lists, max_sleep_ms=100)
    await service.start()

    # Add two jobs scheduled in the past so they're immediately due
    now_ms = int(time.time() * 1000)
    for name in ("job-a", "job-b"):
        service.add_job(
            name=name,
            schedule=CronSchedule(kind="every", every_ms=3_600_000),
            message="test",
            **_bound_chat(name),
        )
    # Force next_run to the past so _on_timer picks them up
    for job in service._store.jobs:
        job.state.next_run_at_ms = now_ms - 1000
    service._save_store()
    service._arm_timer()

    # Let the timer fire once
    await asyncio.sleep(0.3)
    service.stop()

    # Each job should have run exactly once, not looped
    assert execution_count == 2

    # Verify next_run_at_ms was persisted correctly (in the future)
    raw = json.loads(store_path.read_text())
    for j in raw["jobs"]:
        next_run = j["state"]["nextRunAtMs"]
        assert next_run is not None
        assert next_run > now_ms, f"Job '{j['name']}' next_run should be in the future"
