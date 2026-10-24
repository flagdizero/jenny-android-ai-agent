"""Piu aiutanti in un piano, il passo sugli aiutanti, e ``handled_at``.

* Con ``max_concurrent_subagents=3`` un passo di piano aveva 2 aiutanti normali
  alla volta, e oltre non c'e coda: la prova del 24/10/2026 li ha messi in fila
  per quasi due ore. Il driver concede :data:`PLAN_HELPER_SLOTS` normali (piu
  uno breve) alla sessione col piano che gira, attraverso
  ``set_limit_provider``.
* Un aiutante porta il piano e il passo del turno che l'ha lanciato: la casa li
  raggruppa per passo.
* ``mark_handled`` dice alla casa che Jenny ha gia avuto il resoconto.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import (
    PLAN_HELPER_SLOTS,
    SubagentConcurrencyLimitError,
    SubagentManager,
)
from jenny.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from jenny.bus.events import OUTBOUND_META_SUBAGENT_STATUS
from jenny.bus.queue import MessageBus
from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META

PLAN_KEY = "project:piante"


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    defaults = dict(
        provider=provider, workspace=tmp_path, bus=MessageBus(), model="test-model",
        max_tool_result_chars=16_000, stall_threshold_s=0.0, max_concurrent_subagents=3,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _blocking(sm: SubagentManager) -> asyncio.Event:
    block = asyncio.Event()

    async def _slow_run(spec):
        await block.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _slow_run  # type: ignore[method-assign]
    return block


async def _drain(sm: SubagentManager) -> None:
    await asyncio.gather(*sm._running_tasks.values(), return_exceptions=True)
    await asyncio.sleep(0)


# -- slot -------------------------------------------------------------------


def test_the_plan_constant_is_five() -> None:
    # I prompt del piano dicono «up to 5 helpers at once»: il numero e questo.
    assert PLAN_HELPER_SLOTS == 5


async def test_a_running_plan_gets_five_normal_slots_and_one_quick(tmp_path) -> None:
    sm = _manager(tmp_path)
    sm.set_limit_provider(lambda key: PLAN_HELPER_SLOTS if key == PLAN_KEY else None)
    block = _blocking(sm)

    for i in range(5):
        await sm.spawn(f"plant {i}", session_key=PLAN_KEY)
    assert sm.get_running_count() == 5

    with pytest.raises(SubagentConcurrencyLimitError) as refused:
        await sm.spawn("plant 6", session_key=PLAN_KEY)
    assert refused.value.reserved is True
    assert refused.value.limit == 6

    await sm.spawn("quick check", session_key=PLAN_KEY, quick=True)
    with pytest.raises(SubagentConcurrencyLimitError) as full:
        await sm.spawn("another quick", session_key=PLAN_KEY, quick=True)
    assert full.value.reserved is False

    block.set()
    await _drain(sm)


async def test_outside_a_plan_the_config_limit_holds(tmp_path) -> None:
    sm = _manager(tmp_path)
    sm.set_limit_provider(lambda key: PLAN_HELPER_SLOTS if key == PLAN_KEY else None)
    block = _blocking(sm)

    await sm.spawn("a", session_key="unified:default")
    await sm.spawn("b", session_key="unified:default")
    with pytest.raises(SubagentConcurrencyLimitError) as refused:
        await sm.spawn("c", session_key="unified:default")
    assert refused.value.reserved is True
    await sm.spawn("quick", session_key="unified:default", quick=True)
    with pytest.raises(SubagentConcurrencyLimitError):
        await sm.spawn("fourth", session_key="unified:default", quick=True)

    block.set()
    await _drain(sm)


async def test_a_broken_limit_provider_falls_back_to_the_config(tmp_path) -> None:
    sm = _manager(tmp_path)

    def broken(_key):
        raise RuntimeError("boom")

    sm.set_limit_provider(broken)
    block = _blocking(sm)
    await sm.spawn("a", session_key=PLAN_KEY)
    await sm.spawn("b", session_key=PLAN_KEY)
    with pytest.raises(SubagentConcurrencyLimitError):
        await sm.spawn("c", session_key=PLAN_KEY)
    block.set()
    await _drain(sm)


# -- piano e passo ------------------------------------------------------------


async def test_a_helper_carries_the_plan_step_of_the_turn_that_started_it(tmp_path) -> None:
    sm = _manager(tmp_path)
    block = _blocking(sm)
    token = bind_request_context(RequestContext(
        channel="websocket", chat_id="project:piante", session_key=PLAN_KEY,
        metadata={PLAN_ID_META: "plan-1", PLAN_STEP_META: "s3"},
    ))
    try:
        await sm.spawn("write the Prunus page", session_key=PLAN_KEY)
    finally:
        reset_request_context(token)

    running = sm.status_snapshot(PLAN_KEY)["running"]
    assert (running[0]["plan_id"], running[0]["plan_step"]) == ("plan-1", "s3")

    block.set()
    await _drain(sm)
    record = sm.list_records(PLAN_KEY)[-1]
    assert (record.spec.plan_id, record.spec.plan_step) == ("plan-1", "s3")
    recent = sm.status_snapshot(PLAN_KEY)["recent"][0]
    assert (recent["plan_id"], recent["plan_step"]) == ("plan-1", "s3")
    # Riletto dal disco (come dopo un riavvio), i campi ci sono ancora.
    reloaded = _manager(tmp_path).list_records(PLAN_KEY)[-1]
    assert reloaded.spec.plan_step == "s3"


async def test_outside_a_plan_the_step_is_none(tmp_path) -> None:
    sm = _manager(tmp_path)
    block = _blocking(sm)
    await sm.spawn("look something up", session_key="unified:default")
    running = sm.status_snapshot("unified:default")["running"][0]
    assert running["plan_id"] is None and running["plan_step"] is None
    block.set()
    await _drain(sm)


# -- handled_at ---------------------------------------------------------------


async def test_mark_handled_writes_the_record_and_republishes(tmp_path) -> None:
    sm = _manager(tmp_path)

    async def _run(spec):
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn("x", session_key="unified:default",
                   origin_channel="websocket", origin_chat_id="default")
    await _drain(sm)
    task_id = sm.list_records("unified:default")[-1].task_id
    while not sm.bus.outbound.empty():
        sm.bus.outbound.get_nowait()

    assert sm.mark_handled(task_id) is True

    record = sm.list_records("unified:default")[-1]
    assert record.handled_at is not None
    assert sm.status_snapshot("unified:default")["recent"][0]["handled_at"] == record.handled_at
    published = []
    while not sm.bus.outbound.empty():
        published.append(sm.bus.outbound.get_nowait())
    assert any(OUTBOUND_META_SUBAGENT_STATUS in (m.metadata or {}) for m in published)

    first = record.handled_at
    assert sm.mark_handled(task_id) is True
    assert sm.list_records("unified:default")[-1].handled_at == first, "first mark wins"


async def test_a_mark_while_the_helper_is_still_exiting_lands_in_the_record(tmp_path) -> None:
    sm = _manager(tmp_path)
    block = _blocking(sm)
    await sm.spawn("x", session_key="unified:default")
    task_id = next(iter(sm._task_statuses))

    assert sm.mark_handled(task_id) is True
    block.set()
    await _drain(sm)

    assert sm.list_records("unified:default")[-1].handled_at is not None


def test_an_unknown_task_is_not_marked(tmp_path) -> None:
    sm = _manager(tmp_path)
    assert sm.mark_handled("nope1234") is False
    assert sm.mark_handled("") is False


async def test_a_plan_never_gets_fewer_slots_than_the_chat(tmp_path) -> None:
    """Con un limite di config piu largo dei posti di un piano, il piano tiene
    quello: ``max(PLAN_HELPER_SLOTS, configurati - 1)`` normali."""
    sm = _manager(tmp_path, max_concurrent_subagents=9)
    sm.set_limit_provider(lambda key: PLAN_HELPER_SLOTS if key == PLAN_KEY else None)
    block = _blocking(sm)

    for i in range(8):
        await sm.spawn(f"plant {i}", session_key=PLAN_KEY)
    with pytest.raises(SubagentConcurrencyLimitError) as refused:
        await sm.spawn("plant 9", session_key=PLAN_KEY)
    assert refused.value.reserved is True

    block.set()
    await _drain(sm)


async def test_a_mark_on_a_record_from_a_previous_process_goes_off_loop(tmp_path) -> None:
    sm = _manager(tmp_path)

    async def _run(spec):
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn("x", session_key="unified:default")
    await _drain(sm)
    task_id = sm.list_records("unified:default")[-1].task_id

    reborn = _manager(tmp_path)
    assert task_id not in reborn._record_keys
    assert reborn.mark_handled(task_id) is True
    for _ in range(200):
        if reborn._records.load("unified:default")[-1].handled_at is not None:
            break
        await asyncio.sleep(0.01)
    assert reborn._records.load("unified:default")[-1].handled_at is not None
    assert task_id in reborn._record_keys


async def test_a_known_record_is_marked_reading_only_its_file(tmp_path, monkeypatch) -> None:
    sm = _manager(tmp_path)

    async def _run(spec):
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    await sm.spawn("x", session_key="unified:default")
    await _drain(sm)
    task_id = sm.list_records("unified:default")[-1].task_id
    globbed: list[str] = []
    original_glob = Path.glob

    def _spy_glob(self, pattern):
        globbed.append(pattern)
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", _spy_glob)
    assert sm.mark_handled(task_id) is True
    assert globbed == [], "no scan of every record file on a turn end"
