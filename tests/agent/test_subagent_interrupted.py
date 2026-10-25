"""Un aiutante ucciso col processo si riprende da dove era, non si rifà.

Fino al 25/10/2026 la conversazione di un aiutante si salvava solo alla fine del
run: un force-stop di Android a meta lavoro la perdeva, e al riavvio nessuno
sapeva nemmeno che l'aiutante c'era — il passo del piano lo rilanciava da zero
(4 minuti persi su un passo di ricerca, 10-20 su uno di scrittura). Qui si
fissano i tre pezzi che lo evitano: la fotografia periodica della conversazione
(``interrupted``), il segnaposto su disco di un tentativo in corso, e la ripresa
dopo il riavvio (``interrupted_helpers`` + ``send``).
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from jenny.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from jenny.agent.subagent import SubagentManager, _await_thread
from jenny.agent.subagent_history import (
    KIND_BUDGET,
    KIND_DONE,
    KIND_INTERRUPTED,
    SubagentHistoryStore,
)
from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    SubagentRecordStore,
    SubagentSpec,
)
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.session.manager import SessionManager

SESSION = "unified:default"
BRIEF = "Write the care page of the maple in a pot."


def _manager(workspace: Path, **kw: Any) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    defaults: dict[str, Any] = dict(
        provider=provider,
        workspace=workspace,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        stall_threshold_s=0.0,
        session_manager=SessionManager(workspace),
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _round(i: int) -> tuple[dict[str, Any], dict[str, Any]]:
    call = {"id": f"c{i}", "type": "function",
            "function": {"name": "read_file", "arguments": f'{{"path": "p{i}.md"}}'}}
    assistant = {"role": "assistant", "content": "", "tool_calls": [call]}
    result = {"role": "tool", "tool_call_id": f"c{i}", "name": "read_file",
              "content": f"page {i} of the nursery"}
    return assistant, result


async def _tool_round(spec: AgentRunSpec, messages: list[dict[str, Any]], i: int) -> None:
    """Un giro di tool con i checkpoint del runner vero (``_emit_checkpoint``)."""
    emit = AgentRunner(MagicMock(spec=LLMProvider))._emit_checkpoint
    assistant, result = _round(i)
    messages.append(assistant)
    await emit(spec, {
        "phase": "awaiting_tools", "iteration": i, "assistant_message": assistant,
        "completed_tool_results": [], "pending_tool_calls": assistant["tool_calls"],
    }, messages=messages)
    messages.append(result)
    await emit(spec, {
        "phase": "tools_completed", "iteration": i, "assistant_message": assistant,
        "completed_tool_results": [result], "pending_tool_calls": [],
    }, messages=messages)


def _lineage(sm: SubagentManager) -> str:
    return next(iter(sm._task_statuses.values())).lineage_id


async def _settle(sm: SubagentManager) -> None:
    await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)


def _stored(sm: SubagentManager, lineage: str) -> list[dict[str, Any]]:
    history = sm._history.load(lineage)
    assert history is not None
    return history


# ---------------------------------------------------------------------------
# la fotografia periodica
# ---------------------------------------------------------------------------


async def test_checkpoint_is_throttled_and_taken_after_a_closed_tool_round(
    tmp_path: Path,
) -> None:
    sm = _manager(tmp_path)
    clock = _Clock()
    sm._clock = clock
    seen: list[tuple[str, tuple[str, float] | None, int]] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        lineage = _lineage(sm)
        messages = list(spec.initial_messages)
        for i, at in enumerate((10.0, 31.0, 45.0, 62.0)):
            clock.now = 1000.0 + at
            await _tool_round(spec, messages, i)
            entry = sm._history.entry_of(lineage)
            seen.append((f"t={at:.0f}", entry, len(sm._history.load(lineage) or [])))
        messages.append({"role": "assistant", "content": "page written"})
        return AgentRunResult(final_content="page written", messages=messages,
                              stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task=BRIEF, session_key=SESSION)
    lineage = _lineage(sm)
    await _settle(sm)

    # t=10: troppo presto. t=31: prima fotografia. t=45: freno. t=62: seconda.
    assert seen[0][1] is None
    assert seen[1][1] is not None and seen[1][1][0] == KIND_INTERRUPTED
    assert seen[1][2] == 2 + 2 * 2  # system, brief, due giri chiusi
    assert seen[2][2] == seen[1][2]
    assert seen[3][1] is not None and seen[3][1][0] == KIND_INTERRUPTED
    assert seen[3][2] == 2 + 2 * 4

    # La fine normale sostituisce la fotografia, sulla stessa chiave.
    entry = sm._history.entry_of(lineage)
    assert entry is not None and entry[0] == KIND_DONE
    assert _stored(sm, lineage)[-1] == {"role": "assistant", "content": "page written"}


async def test_the_checkpoint_never_leaves_a_tool_call_without_its_result(
    tmp_path: Path,
) -> None:
    sm = _manager(tmp_path)
    clock = _Clock()
    sm._clock = clock
    snapshots: list[list[dict[str, Any]]] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        lineage = _lineage(sm)
        messages = list(spec.initial_messages)
        clock.now += 100
        await _tool_round(spec, messages, 0)
        # Un'iniezione dell'orchestratore e un giro con l'assistente gia in coda
        # e i risultati ancora in volo: il checkpoint ``awaiting_tools`` non salva.
        messages.append({"role": "user", "content": "also check the soil"})
        clock.now += 100
        assistant, _ = _round(1)
        messages.append(assistant)
        await AgentRunner(MagicMock(spec=LLMProvider))._emit_checkpoint(spec, {
            "phase": "awaiting_tools", "iteration": 1, "assistant_message": assistant,
            "completed_tool_results": [], "pending_tool_calls": assistant["tool_calls"],
        }, messages=messages)
        snapshots.append(_stored(sm, lineage))
        await asyncio.Event().wait()  # ucciso qui
        raise AssertionError("unreachable")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task=BRIEF, session_key=SESSION)
    for _ in range(50):
        if snapshots:
            break
        await asyncio.sleep(0.01)
    history = snapshots[0]
    called = {c["id"] for m in history for c in (m.get("tool_calls") or [])}
    answered = {m["tool_call_id"] for m in history if m.get("role") == "tool"}
    assert called == answered == {"c0"}
    await sm.drain(timeout_s=0.01)


async def test_an_app_builder_never_checkpoints(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    sm._check_capabilities = lambda spec: None  # type: ignore[method-assign]
    clock = _Clock()
    sm._clock = clock
    entries: list[Any] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages)
        for i in range(3):
            clock.now += 100
            await _tool_round(spec, messages, i)
            entries.append(sm._history.entry_of(_lineage(sm)))
        return AgentRunResult(final_content="ok", messages=messages, stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task="Build a watering timer app.", session_key=SESSION,
                   agent_type="app-builder")
    await _settle(sm)
    assert entries == [None, None, None]


async def test_a_failing_checkpoint_does_not_break_the_helper(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    clock = _Clock()
    sm._clock = clock

    def _boom(*_a: Any, **_k: Any) -> bool:
        raise OSError("disk full")

    real_write = sm._history.write

    def _write(*a: Any, **k: Any) -> bool:
        # Solo la fotografia si rompe: il salvataggio finale ha il suo contratto.
        if k.get("kind") == KIND_INTERRUPTED:
            raise OSError("disk full")
        return real_write(*a, **k)

    sm._history.write = _write  # type: ignore[method-assign]
    sm._records.mark_running = _boom  # type: ignore[method-assign]

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages)
        for i in range(3):
            clock.now += 100
            await _tool_round(spec, messages, i)
        messages.append({"role": "assistant", "content": "done anyway"})
        return AgentRunResult(final_content="done anyway", messages=messages,
                              stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task=BRIEF, session_key=SESSION)
    await _settle(sm)
    [record] = sm.list_records(SESSION)
    assert record.state == "done"
    assert "done anyway" in (await sm.bus.consume_inbound()).content


async def test_a_cancelled_wait_still_waits_for_the_thread() -> None:
    """Un aiutante cancellato a meta scrittura esce a scrittura finita."""
    finished = threading.Event()

    def _slow() -> None:
        time.sleep(0.2)
        finished.set()

    task = asyncio.ensure_future(_await_thread(_slow))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and finished.is_set()


# ---------------------------------------------------------------------------
# il segnaposto e la ripresa dopo un riavvio
# ---------------------------------------------------------------------------


async def _helper_killed_mid_run(
    tmp_path: Path, *, rounds_before_kill: int, plan: bool = True,
) -> tuple[Path, str, str]:
    """Fa lavorare un aiutante, «uccide il processo» e ritorna il disco di quel momento.

    Il kill e una copia del workspace mentre l'aiutante e fermo a meta: e cio che
    trova il processo successivo. L'aiutante del primo manager poi si chiude, ma
    su un disco che il secondo non vede.
    """
    live = tmp_path / "live"
    live.mkdir()
    sm = _manager(live)
    clock = _Clock()
    sm._clock = clock
    paused = asyncio.Event()

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages)
        for i in range(rounds_before_kill):
            clock.now += 40
            await _tool_round(spec, messages, i)
        paused.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    sm.runner.run = _run  # type: ignore[method-assign]
    spec = SubagentSpec(
        task=BRIEF, label="maple care", agent_type="writer", session_key=SESSION,
        plan_id="plan-7" if plan else None, plan_step="step-2" if plan else None,
    )
    task_id, lineage = await sm._launch(spec)
    await asyncio.wait_for(paused.wait(), timeout=5)
    # Il segnaposto e scritto in un thread, in parallelo al run.
    for _ in range(200):
        if list((live / "subagents" / "running").glob("*.json")):
            break
        await asyncio.sleep(0.01)
    dead = tmp_path / "after-kill"
    shutil.copytree(live, dead)
    await sm.drain(timeout_s=0.01)
    return dead, task_id, lineage


async def test_a_helper_killed_mid_run_is_resumable_after_the_restart(tmp_path: Path) -> None:
    disk, task_id, lineage = await _helper_killed_mid_run(tmp_path, rounds_before_kill=2)
    assert list((disk / "subagents" / "running").glob("*.json"))

    sm = _manager(disk)
    assert not list((disk / "subagents" / "running").glob("*.json"))
    assert sm.interrupted_helpers(SESSION) == [{
        "task_id": task_id, "lineage_id": lineage, "label": "maple care",
        "agent_type": "writer", "plan_id": "plan-7", "plan_step": "step-2",
    }]
    [record] = sm.list_records(SESSION)
    assert (record.state, record.cancel_reason, record.resumable) == (
        "cancelled", CANCEL_REASON_SHUTDOWN, True,
    )
    assert "subagent_send" in record.result_summary
    [recent] = sm.status_snapshot(SESSION)["recent"]
    assert recent["resumable"] is True
    assert sm.interrupted_helpers("other:session") == []

    seen: list[AgentRunSpec] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        seen.append(spec)
        messages = list(spec.initial_messages) + [{"role": "assistant", "content": "done"}]
        return AgentRunResult(final_content="done", messages=messages, stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    outcome = await sm.send(lineage, "Go on from where you stopped.")
    assert outcome.mode == "resumed"
    # In volo, il lineage non e piu da riprendere.
    assert sm.interrupted_helpers(SESSION) == []
    await _settle(sm)

    seed = seen[0].initial_messages
    assert [m.get("tool_call_id") for m in seed if m.get("role") == "tool"] == ["c0", "c1"]
    assert seed[1]["content"] == BRIEF
    assert seed[-1] == {"role": "user", "content": "Go on from where you stopped."}
    assert sm.interrupted_helpers(SESSION) == []


async def test_a_helper_killed_before_its_first_checkpoint_is_not_resumable(
    tmp_path: Path,
) -> None:
    disk, task_id, _lineage_id = await _helper_killed_mid_run(tmp_path, rounds_before_kill=0)
    sm = _manager(disk)
    [record] = sm.list_records(SESSION)
    assert record.task_id == task_id
    assert (record.state, record.cancel_reason, record.resumable) == (
        "cancelled", CANCEL_REASON_SHUTDOWN, False,
    )
    assert sm.interrupted_helpers(SESSION) == []


async def test_marking_it_handled_dismisses_it(tmp_path: Path) -> None:
    disk, task_id, _ = await _helper_killed_mid_run(tmp_path, rounds_before_kill=1, plan=False)
    sm = _manager(disk)
    [entry] = sm.interrupted_helpers(SESSION)
    assert entry["plan_id"] is None and entry["plan_step"] is None
    sm.mark_handled(task_id)
    for _ in range(50):
        if not sm.interrupted_helpers(SESSION):
            break
        await asyncio.sleep(0.01)
    assert sm.interrupted_helpers(SESSION) == []


async def test_a_marker_whose_record_was_written_is_just_cleared(tmp_path: Path) -> None:
    """Kill fra la scrittura del record e la cancellazione del segnaposto."""
    sm = _manager(tmp_path)

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages) + [{"role": "assistant", "content": "ok"}]
        return AgentRunResult(final_content="ok", messages=messages, stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task=BRIEF, session_key=SESSION)
    await _settle(sm)
    [record] = sm.list_records(SESSION)
    stale = SubagentRecordStore(tmp_path)
    record.state = "running"
    stale.mark_running(record)

    sm2 = _manager(tmp_path)
    assert [r.state for r in sm2.list_records(SESSION)] == ["done"]
    assert not list((tmp_path / "subagents" / "running").glob("*.json"))


async def test_an_orderly_shutdown_keeps_the_checkpoint(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    clock = _Clock()
    sm._clock = clock
    paused = asyncio.Event()

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages)
        clock.now += 40
        await _tool_round(spec, messages, 0)
        paused.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task=BRIEF, session_key=SESSION)
    lineage = _lineage(sm)
    await asyncio.wait_for(paused.wait(), timeout=5)
    await sm.drain(timeout_s=0.01)

    entry = sm._history.entry_of(lineage)
    assert entry is not None and entry[0] == KIND_INTERRUPTED
    [record] = sm.list_records(SESSION)
    assert record.cancel_reason == CANCEL_REASON_SHUTDOWN and record.resumable
    assert not list((tmp_path / "subagents" / "running").glob("*.json"))
    assert [h["lineage_id"] for h in _manager(tmp_path).interrupted_helpers(SESSION)] == [
        lineage
    ]


async def test_a_user_stop_still_drops_the_checkpoint(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    clock = _Clock()
    sm._clock = clock
    paused = asyncio.Event()

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages)
        clock.now += 40
        await _tool_round(spec, messages, 0)
        paused.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn(task=BRIEF, session_key=SESSION)
    lineage = _lineage(sm)
    await asyncio.wait_for(paused.wait(), timeout=5)
    await sm.cancel_by_session(SESSION)
    await asyncio.sleep(0)
    assert sm._history.entry_of(lineage) is None
    assert sm.interrupted_helpers(SESSION) == []


# ---------------------------------------------------------------------------
# lo store
# ---------------------------------------------------------------------------


def test_an_interrupted_history_outlives_finished_ones_in_pruning(tmp_path: Path) -> None:
    store = SubagentHistoryStore(SessionManager(tmp_path), max_per_origin=2)
    msgs = [{"role": "user", "content": "x"}]
    store.save("live", SESSION, msgs, now=1.0, kind=KIND_INTERRUPTED)
    store.save("old-done", SESSION, msgs, now=2.0)
    store.save("new-done", SESSION, msgs, now=3.0)
    assert store.has("live", now=3.0) and store.has("new-done", now=3.0)
    assert not store.has("old-done", now=3.0)


def test_the_interrupted_kind_survives_a_restart(tmp_path: Path) -> None:
    msgs = [{"role": "user", "content": "x"}]
    SubagentHistoryStore(SessionManager(tmp_path)).save(
        "a", SESSION, msgs, kind=KIND_INTERRUPTED,
    )
    SubagentHistoryStore(SessionManager(tmp_path)).save("b", SESSION, msgs, kind=KIND_BUDGET)
    fresh = SubagentHistoryStore(SessionManager(tmp_path))
    assert (fresh.entry_of("a") or ("",))[0] == KIND_INTERRUPTED
    assert (fresh.entry_of("b") or ("",))[0] == KIND_BUDGET
