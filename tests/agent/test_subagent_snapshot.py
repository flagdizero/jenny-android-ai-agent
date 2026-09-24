"""Tests per ``SubagentManager.status_snapshot`` e la pubblicazione sul bus.

Il contratto dello snapshot e consumato dal pannello della WebUI, che non importa
nulla da ``jenny/agent``: qui si pinnano forma, chiavi e serializzabilita JSON.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.aio import drain_nowait

from jenny.agent.subagent import (
    MAX_AUTO_ATTEMPTS,
    SubagentManager,
    SubagentSpec,
    SubagentStatus,
)
from jenny.agent.subagent_records import SubagentRecord
from jenny.bus.events import OUTBOUND_META_SUBAGENT_STATUS
from jenny.bus.queue import MessageBus

# ``task`` e ``tool_events`` sono i due campi che la modale di dettaglio del
# pannello mostra e la card, larga una riga, non puo: senza di loro l'unico modo
# di sapere *cosa* si e chiesto al subagent e come ci sta provando era leggere i
# log. ``tool_events`` solo sui vivi — un record terminale porta gia il suo
# ``result_summary``.
RUNNING_KEYS = {
    "task_id", "lineage_id", "attempt", "label", "task", "agent_type", "state",
    "phase", "iteration", "elapsed_s", "idle_s", "last_tool", "tool_events",
}
# ``cancel_reason`` solo sui terminati: e la provenienza di una cancellazione, e
# un subagent vivo non ne ha una. ``state="cancelled"`` da solo non distingue
# "fermato dall'utente" da "interrotto dallo shutdown", e dopo un riavvio del
# gateway lo snapshot e l'unica memoria che l'orchestratore ha.
RECENT_KEYS = {
    "task_id", "lineage_id", "attempt", "label", "task", "agent_type", "state",
    "stop_reason", "cancel_reason", "result_summary", "ended_at", "can_restart",
}


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    defaults = dict(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        max_concurrent_subagents=8,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _drain(bus: MessageBus) -> list:
    return drain_nowait(bus.outbound)


def _record(mgr: SubagentManager, **kw) -> SubagentRecord:
    spec = SubagentSpec(
        task="do it", label=kw.pop("label", "job"),
        agent_type=kw.pop("agent_type", "coder"),
        origin_channel="websocket", origin_chat_id="default",
        session_key=kw.pop("session_key", "unified:default"),
    )
    record = SubagentRecord(
        task_id=kw.pop("task_id", "t1"),
        lineage_id=kw.pop("lineage_id", "l1"),
        attempt=kw.pop("attempt", 1),
        spec=spec,
        state=kw.pop("state", "failed"),
        stop_reason=kw.pop("stop_reason", "tool_error"),
        result_summary=kw.pop("result_summary", "it broke"),
        ended_at=kw.pop("ended_at", time.time()),
    )
    mgr._records.append(record)
    return record


def test_snapshot_shape_and_keys(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    now = time.monotonic()
    mgr._task_statuses["abc12345"] = SubagentStatus(
        task_id="abc12345", label="price research",
        task_description="the whole assignment",
        started_at=now - 42, lineage_id="lin00001", attempt=2,
        agent_type="researcher", phase="awaiting_tools", iteration=3,
        tool_events=[{"name": "web_search", "status": "ok", "detail": "…"}],
    )
    _record(mgr, task_id="def67890", lineage_id="lin00002", attempt=3)

    snap = mgr.status_snapshot("unified:default")

    assert set(snap) == {"running", "recent"}
    # Nessun filtro per sessione sui vivi non tracciati: lo status vive in RAM
    # senza session key, il filtro passa da ``_session_tasks``.
    snap_all = mgr.status_snapshot()
    assert set(snap_all["running"][0]) == RUNNING_KEYS
    assert set(snap["recent"][0]) == RECENT_KEYS

    running = snap_all["running"][0]
    assert running["task_id"] == "abc12345"
    assert running["agent_type"] == "researcher"
    assert running["attempt"] == 2
    assert running["last_tool"] == "web_search"
    assert running["elapsed_s"] >= 42.0
    assert running["idle_s"] >= 0.0

    assert running["task"] == "the whole assignment"
    assert running["tool_events"] == [
        {"name": "web_search", "status": "ok", "detail": "…"},
    ]

    recent = snap["recent"][0]
    assert recent["state"] == "failed"
    assert recent["stop_reason"] == "tool_error"
    assert recent["result_summary"] == "it broke"
    assert recent["task"] == "do it"
    assert recent["can_restart"] is False, "attempt 3 = tetto automatico raggiunto"


def test_snapshot_is_json_serializable(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr._task_statuses["abc"] = SubagentStatus(
        task_id="abc", label="l", task_description="t", started_at=time.monotonic(),
    )
    _record(mgr)
    payload = json.dumps(mgr.status_snapshot())
    assert json.loads(payload)["running"][0]["task_id"] == "abc"


def test_can_restart_reflects_the_automatic_attempt_cap(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    _record(mgr, task_id="t1", lineage_id="l1", attempt=MAX_AUTO_ATTEMPTS - 1)
    _record(mgr, task_id="t2", lineage_id="l2", attempt=MAX_AUTO_ATTEMPTS)
    by_id = {e["task_id"]: e for e in mgr.status_snapshot()["recent"]}
    assert by_id["t1"]["can_restart"] is True
    assert by_id["t2"]["can_restart"] is False


def test_task_is_capped_in_both_lists(tmp_path: Path) -> None:
    """Lo snapshot viaggia su WS a ogni transizione: il task entra, ma troncato.

    Con cinque subagent in parallelo un task da 50 KB sarebbe un frame da spedire
    cinque volte per ogni cambio di stato.
    """
    from jenny.agent.subagent import _SNAPSHOT_TASK_CHARS

    mgr = _manager(tmp_path)
    huge = "x" * (_SNAPSHOT_TASK_CHARS * 3)
    mgr._task_statuses["t"] = SubagentStatus(
        task_id="t", label="l", task_description=huge, started_at=time.monotonic(),
    )
    spec = SubagentSpec(
        task=huge, label="job", origin_channel="websocket",
        origin_chat_id="default", session_key="unified:default",
    )
    mgr._records.append(SubagentRecord(
        task_id="r", lineage_id="lr", attempt=1, spec=spec, state="failed",
        ended_at=time.time(),
    ))

    snap = mgr.status_snapshot()
    for entry in (snap["running"][0], snap["recent"][0]):
        assert entry["task"].startswith("x" * _SNAPSHOT_TASK_CHARS)
        assert len(entry["task"]) < len(huge)
        assert len(entry["task"]) <= _SNAPSHOT_TASK_CHARS + 40


def test_tool_events_are_a_bounded_json_only_tail(tmp_path: Path) -> None:
    """Coda corta e ricostruita: nessuna chiave nuova, nessun valore non JSON."""
    from jenny.agent.subagent import _SNAPSHOT_TOOL_EVENTS_LIMIT

    mgr = _manager(tmp_path)
    events = [
        {"name": f"tool{i}", "status": "ok", "detail": "d", "secret": object()}
        for i in range(_SNAPSHOT_TOOL_EVENTS_LIMIT + 4)
    ]
    events.append("not a dict")  # type: ignore[arg-type]
    mgr._task_statuses["t"] = SubagentStatus(
        task_id="t", label="l", task_description="t",
        started_at=time.monotonic(), tool_events=events,
    )

    got = mgr.status_snapshot()["running"][0]["tool_events"]
    assert len(got) == _SNAPSHOT_TOOL_EVENTS_LIMIT
    assert all(set(e) == {"name", "status", "detail"} for e in got)
    # I più recenti, e in ordine di lettura (dal più vecchio al più nuovo).
    assert [e["name"] for e in got][-1] == f"tool{_SNAPSHOT_TOOL_EVENTS_LIMIT + 3}"
    json.dumps(got)


def test_a_running_subagent_without_tool_events_reports_an_empty_list(tmp_path: Path) -> None:
    """Chiave sempre presente: il pannello non deve distinguere assente da vuota."""
    mgr = _manager(tmp_path)
    mgr._task_statuses["t"] = SubagentStatus(
        task_id="t", label="l", task_description="t", started_at=time.monotonic(),
    )
    running = mgr.status_snapshot()["running"][0]
    assert running["tool_events"] == []
    assert running["last_tool"] is None


def test_recent_is_newest_first_and_bounded(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    base = time.time()
    for i in range(15):
        _record(mgr, task_id=f"t{i}", lineage_id=f"l{i}", ended_at=base + i)
    recent = mgr.status_snapshot()["recent"]
    assert len(recent) == 10
    assert [e["task_id"] for e in recent][:3] == ["t14", "t13", "t12"]


def test_snapshot_filters_running_by_session(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    for task_id, session in (("a", "unified:default"), ("b", "internal:cron")):
        mgr._task_statuses[task_id] = SubagentStatus(
            task_id=task_id, label=task_id, task_description="t",
            started_at=time.monotonic(),
        )
        mgr._session_tasks.setdefault(session, set()).add(task_id)

    ids = [e["task_id"] for e in mgr.status_snapshot("unified:default")["running"]]
    assert ids == ["a"]


def test_unknown_session_key_filters_everything_out(tmp_path: Path) -> None:
    """Una chiave sconosciuta filtra tutto, non disattiva il filtro.

    Regressione: con ``_session_tasks.get(key)`` una chiave stale restituiva i
    subagent di *tutte* le sessioni in ``running`` mentre ``recent`` restava
    correttamente filtrato — un pannello che mostrava lavori di altre sessioni.
    """
    mgr = _manager(tmp_path)
    mgr._task_statuses["a"] = SubagentStatus(
        task_id="a", label="a", task_description="t", started_at=time.monotonic(),
    )
    mgr._session_tasks.setdefault("unified:default", set()).add("a")
    _record(mgr, task_id="r1", session_key="unified:default")

    snap = mgr.status_snapshot("websocket:stale")
    assert snap["running"] == []
    assert snap["recent"] == []
    # Senza chiave il filtro resta disattivato, come prima.
    assert [e["task_id"] for e in mgr.status_snapshot()["running"]] == ["a"]


@pytest.mark.asyncio
async def test_spawn_and_terminal_transitions_publish_snapshot(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    await mgr.spawn(
        task="do it", label="job", origin_channel="websocket",
        origin_chat_id="default", session_key="unified:default",
    )
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)
    await asyncio.sleep(0)

    messages = _drain(mgr.bus)
    snapshots = [m for m in messages if OUTBOUND_META_SUBAGENT_STATUS in m.metadata]
    hints = [m for m in messages if m.metadata.get("_tool_hint")]

    # Una transizione allo spawn, una alla terminazione.
    assert len(snapshots) == 2
    assert all(m.channel == "websocket" and m.chat_id == "default" for m in snapshots)
    assert all(m.content == "" for m in snapshots)

    started, finished = (m.metadata[OUTBOUND_META_SUBAGENT_STATUS] for m in snapshots)
    assert len(started["running"]) == 1
    assert started["running"][0]["label"] == "job"
    # Alla terminazione il task e uscito da running ed entrato in recent: mai in
    # entrambi, altrimenti il pannello mostrerebbe un doppione.
    assert finished["running"] == []
    assert finished["recent"][0]["state"] == "done"

    assert [m.content for m in hints] == ["subagent started: job", "subagent done: job"]
    assert all(m.metadata.get("_progress") for m in hints)


@pytest.mark.asyncio
async def test_failed_subagent_emits_one_failed_hint(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        return SimpleNamespace(
            stop_reason="error", final_content=None, error="nope", tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    await mgr.spawn(task="do it", label="job", origin_channel="websocket",
                    origin_chat_id="default", session_key="unified:default")
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)
    await asyncio.sleep(0)

    hints = [m.content for m in _drain(mgr.bus) if m.metadata.get("_tool_hint")]
    assert hints == ["subagent started: job", "subagent failed: job"]


@pytest.mark.asyncio
async def test_stall_marking_publishes_one_hint(tmp_path: Path) -> None:
    """Il watchdog marca: la transizione va annunciata una volta sola."""
    mgr = _manager(tmp_path, stall_threshold_s=0.01, stall_check_interval_s=0.01)
    mgr._announce_result = AsyncMock()
    release = asyncio.Event()

    async def fake_run(spec):
        await release.wait()
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    await mgr.spawn(task="do it", label="job", origin_channel="websocket",
                    origin_chat_id="default", session_key="unified:default")
    await asyncio.sleep(0.08)
    stall_hints = [
        m.content for m in _drain(mgr.bus)
        if m.metadata.get("_tool_hint") and "stalled" in m.content
    ]
    release.set()
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)

    assert stall_hints == ["subagent stalled (no progress): job"]


@pytest.mark.asyncio
async def test_cancel_publishes_snapshot_before_the_task_dies(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr._announce_result = AsyncMock()
    started = asyncio.Event()

    async def fake_run(spec):
        started.set()
        await asyncio.Event().wait()

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    await mgr.spawn(task="do it", label="job", origin_channel="websocket",
                    origin_chat_id="default", session_key="unified:default")
    await started.wait()
    task_id = next(iter(mgr._task_statuses))
    _drain(mgr.bus)

    assert await mgr.cancel_task(task_id) is True
    await asyncio.sleep(0)

    payloads = [
        m.metadata[OUTBOUND_META_SUBAGENT_STATUS]
        for m in _drain(mgr.bus)
        if OUTBOUND_META_SUBAGENT_STATUS in m.metadata
    ]
    assert payloads, "una cancellazione e una transizione di stato"
    assert payloads[0]["running"][0]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_injects_an_explicit_cancellation_announce(tmp_path: Path) -> None:
    """Regressione osservata sul device: Stop lasciava il turno appeso.

    L'orchestratore che ha spawnato aspetta l'announce dentro la pending queue
    del proprio turno. Ripudiare quello naturale senza metterne uno al posto
    lasciava il turno in attesa fino al timeout di 300s: l'utente premeva Stop e
    la UI restava su "Agent running" per cinque minuti.
    """
    mgr = _manager(tmp_path)
    started = asyncio.Event()

    async def fake_run(spec):
        started.set()
        await asyncio.Event().wait()

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    await mgr.spawn(task="do it", label="job", origin_channel="websocket",
                    origin_chat_id="default", session_key="unified:default")
    await started.wait()
    task_id = next(iter(mgr._task_statuses))

    assert await mgr.cancel_task(task_id) is True

    injected = []
    while not mgr.bus.inbound.empty():
        injected.append(mgr.bus.inbound.get_nowait())
    announces = [m for m in injected if m.metadata.get("injected_event") == "subagent_result"]
    assert len(announces) == 1, "esattamente uno: né zero (turno appeso) né due"
    msg = announces[0]
    assert msg.session_key_override == "unified:default"
    assert msg.metadata["subagent_task_id"] == task_id
    assert "stopped" in msg.content.lower()


@pytest.mark.asyncio
async def test_publishing_failure_never_kills_the_subagent(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr._announce_result = AsyncMock()
    mgr.bus.try_publish_outbound = MagicMock(side_effect=RuntimeError("bus down"))

    async def fake_run(spec):
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    result = await mgr.spawn(task="do it", label="job")
    assert "started" in result
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)
    mgr._announce_result.assert_awaited_once()


# --- proiezione in chat: solo per un'origine visibile -------------------------
#
# Uno snapshot pushato è ciò che alimenta il pannello e il blocco "cosa ha fatto
# davvero" nella chat. Un subagent lanciato da un heartbeat o da un cron monitor
# è lavoro interno tanto quanto il turno che lo ha lanciato: annunciarlo in chat
# è la stessa violazione della risposta finale, vista da un'altra uscita.
# Misurato sul dispositivo: un ciclo heartbeat silenzioso lasciava comunque il
# chip "What it actually did waterbot-umidita-check" nella conversazione.


def _spec_for(session_key: str, *, channel: str = "websocket") -> SubagentSpec:
    return SubagentSpec(
        task="controlla l'umidità",
        label="waterbot-umidita-check",
        agent_type="sysadmin",
        origin_channel=channel,
        origin_chat_id="default",
        session_key=session_key,
    )


def test_a_visible_origin_still_gets_its_status_pushed(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    mgr._publish_status_snapshot(_spec_for("unified:default"))

    frames = _drain(mgr.bus)
    assert len(frames) == 1
    assert OUTBOUND_META_SUBAGENT_STATUS in frames[0].metadata


def test_an_internal_origin_pushes_nothing(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    mgr._publish_status_snapshot(_spec_for("heartbeat"))

    assert _drain(mgr.bus) == []


def test_a_monitor_origin_pushes_nothing(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    mgr._publish_status_snapshot(_spec_for("cron:job-m"))

    assert _drain(mgr.bus) == []


def test_an_internal_origin_publishes_no_transition_hint(tmp_path: Path) -> None:
    """La riga "subagent done: <label>" è progress, e il progress di un turno
    silenzioso non appartiene alla conversazione."""
    mgr = _manager(tmp_path)

    mgr._publish_transition(_spec_for("heartbeat"), "done", "waterbot-umidita-check")

    assert _drain(mgr.bus) == []


def test_a_visible_origin_keeps_its_transition_hint(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    mgr._publish_transition(_spec_for("unified:default"), "done", "job")

    frames = _drain(mgr.bus)
    hints = [f for f in frames if f.metadata.get("_tool_hint") is True]
    assert len(hints) == 1
    assert "job" in hints[0].content
