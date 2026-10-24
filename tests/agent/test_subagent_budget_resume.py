"""Un aiutante fermato al budget non butta il lavoro: si riprende con la sua storia.

Prova del 24/10/2026 (Piano a lista su jenny_s22): sei aiutanti su 41 fermati dal
budget, e nessuno ripreso davvero. Il record finiva ``failed``, la done-callback
cancellava la storia Tier-2 appena salvata, e ``subagent_send`` cadeva sempre nel
ramo «rilancia da zero». In piu l'annuncio portava solo gli ultimi tre tool: Jenny
non sapeva cosa fosse fatto e cosa restasse.
"""

from __future__ import annotations

from typing import Any

from jenny.agent.subagent import SubagentManager
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session.manager import SessionManager
from jenny.utils.runtime import SUBAGENT_BUDGET_FINALIZATION_PROMPT
from tests.support.aio import wait_until
from tests.support.subagent_provider_fakes import fake_provider

REPORT = "Report: read the tree. No files written. Remaining: write the summary page."


def _scripted(seen: list[dict[str, Any]]):
    """Tool call finché il run offre tool; il resoconto quando non li offre più.

    La chiamata di chiusura a budget esaurito è l'unica senza ``tools``.
    """
    call = ToolCallRequest(id="c1", name="list_dir", arguments={"path": "."})

    async def produce(**kwargs: Any) -> LLMResponse:
        seen.append(kwargs)
        if kwargs.get("tools"):
            return LLMResponse(content="", tool_calls=[call], finish_reason="tool_calls")
        return LLMResponse(content=REPORT, finish_reason="stop")

    return produce


def _manager(tmp_path, provider, **kw) -> SubagentManager:
    return SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(), model="m",
        max_tool_result_chars=16000, max_iterations=3, stall_threshold_s=0.0,
        session_manager=SessionManager(tmp_path), **kw,
    )


async def _stop_at_budget(manager: SubagentManager):
    await manager.spawn("scorri l'albero", session_key="unified:default",
                        origin_channel="websocket", origin_chat_id="default")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    announce = await manager.bus.consume_inbound()
    record = manager.list_records("unified:default")[-1]
    return announce, record


async def test_a_budget_stop_is_its_own_terminal_state_and_keeps_the_history(tmp_path):
    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))

    _, record = await _stop_at_budget(manager)

    assert record.state == "budget"
    assert record.stop_reason == "max_iterations"
    assert record.resumable is True
    # La storia c'è ancora dopo la done-callback: è il difetto di partenza.
    history = manager._history.load(record.lineage_id)
    assert history, "the done-callback dropped the history of a budget stop"


async def test_subagent_send_resumes_a_budget_stop_with_its_history(tmp_path):
    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)

    resumed_specs: list[Any] = []
    original_run = manager.runner.run

    async def _spy(spec):
        resumed_specs.append(spec)
        return await original_run(spec)

    manager.runner.run = _spy  # type: ignore[method-assign]
    outcome = await manager.send(record.task_id, "finish the summary page")

    assert outcome.mode == "resumed"
    assert outcome.task_id and outcome.task_id != record.task_id
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    spec = resumed_specs[0]
    contents = [str(m.get("content")) for m in spec.initial_messages]
    assert "scorri l'albero" in contents, "the original task is in the resumed conversation"
    assert any(m.get("role") == "tool" for m in spec.initial_messages), "it keeps what it read"
    assert contents[-1] == "finish the summary page"
    latest = manager.list_records("unified:default")[-1]
    assert latest.lineage_id == record.lineage_id
    assert latest.attempt == record.attempt


async def test_the_budget_announce_carries_the_report_and_says_how_to_continue(tmp_path):
    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))

    announce, record = await _stop_at_budget(manager)

    # La chiamata di chiusura usa il prompt del subagent, non quello «all'utente».
    closing = [kw for kw in seen if not kw.get("tools")]
    assert closing, "no finalization call at the budget"
    assert closing[-1]["messages"][-1]["content"] == SUBAGENT_BUDGET_FINALIZATION_PROMPT
    text = announce.content
    assert "iteration budget" in text.splitlines()[0]
    assert REPORT in text
    assert "Steps before the stop:" in text and "list_dir" in text
    assert "subagent_send" in text and record.task_id in text
    assert "Do not spawn a new helper" in text
    assert announce.metadata["subagent_status"] == "budget"
    assert announce.metadata["subagent_resumable"] is True
    assert REPORT in record.result_summary


async def test_a_budget_stop_too_long_to_resume_does_not_promise_a_continuation(tmp_path):
    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    manager.CONTINUE_MAX_HISTORY_CHARS = 10  # type: ignore[misc]

    announce, record = await _stop_at_budget(manager)

    assert record.state == "budget"
    assert record.resumable is False
    assert announce.metadata["subagent_resumable"] is False
    assert "Do not spawn a new helper" not in announce.content
    assert "too long to resume" in announce.content


async def test_the_snapshot_offers_the_continuation_only_on_the_latest_attempt(tmp_path):
    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)

    entry = manager.status_snapshot("unified:default")["recent"][0]
    assert entry["state"] == "budget"
    assert entry["resumable"] is True
    assert entry["handled_at"] is None

    await manager.send(record.task_id, "go on")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    recent = manager.status_snapshot("unified:default")["recent"]
    older = next(e for e in recent if e["task_id"] == record.task_id)
    assert older["resumable"] is False, "a newer attempt of the lineage exists"


# -- revisione del 24/10: doppia ripresa, tetto, rilancio silenzioso ---------


async def test_continue_from_the_ui_and_from_jenny_make_one_live_attempt(tmp_path):
    """Il tasto «continua» (col lineage) e un ``subagent_send`` di Jenny col task id
    vecchio: il secondo deve trovare la ripresa gia in volo e iniettarsi."""
    from jenny.webui.subagent_routes import SubagentRoutes

    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)

    first = await manager.send(SubagentRoutes._lineage_of(manager, record.task_id), "go on (ui)")
    assert manager.continuable(record.task_id) is True
    second = await manager.send(record.task_id, "finish (jenny)")

    live = [t for t, task in manager._running_tasks.items() if not task.done()]
    assert (first.mode, second.mode) == ("resumed", "injected")
    assert live == [first.task_id]
    assert second.task_id == first.task_id
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)


async def _resume_and_stop(manager: SubagentManager, target: str):
    outcome = await manager.send(target, "go on")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    announce = await manager.bus.consume_inbound()
    return outcome, announce, manager.list_records("unified:default")[-1]


async def test_budget_resumes_stop_at_the_cap_with_a_clear_refusal(tmp_path):
    import pytest

    from jenny.agent.subagent import MAX_BUDGET_RESUMES, SubagentSendError

    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)
    for n in range(1, MAX_BUDGET_RESUMES + 1):
        outcome, announce, record = await _resume_and_stop(manager, record.task_id)
        assert outcome.mode == "resumed"
        assert record.budget_resumes == n

    # Al tetto: niente piu «continua», ne nel record ne nello snapshot ne
    # nell'annuncio, che dice di dividere il lavoro.
    assert record.resumable is False
    assert manager.status_snapshot("unified:default")["recent"][0]["resumable"] is False
    assert announce.metadata["subagent_resumable"] is False
    assert "Split what remains into smaller helpers" in announce.content
    assert manager.continuable(record.task_id) is False
    with pytest.raises(SubagentSendError, match=f"budget {MAX_BUDGET_RESUMES + 1} times"):
        await manager.send(record.task_id, "once more")
    assert not manager._running_tasks, "no silent relaunch from scratch"


async def test_the_cap_survives_a_restart_of_the_gateway(tmp_path):
    import pytest

    from jenny.agent.subagent import MAX_BUDGET_RESUMES, SubagentSendError

    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)
    for _ in range(MAX_BUDGET_RESUMES):
        _, _, record = await _resume_and_stop(manager, record.task_id)

    reborn = _manager(tmp_path, fake_provider(_scripted(seen)))
    with pytest.raises(SubagentSendError):
        await reborn.send(record.task_id, "once more")


async def test_a_budget_stop_too_long_to_resume_is_refused_not_relaunched(tmp_path):
    import pytest

    from jenny.agent.subagent import SubagentSendError

    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    manager.CONTINUE_MAX_HISTORY_CHARS = 10  # type: ignore[misc]
    _, record = await _stop_at_budget(manager)

    with pytest.raises(SubagentSendError, match="can no longer be resumed"):
        await manager.send(record.task_id, "go on")
    assert not manager._running_tasks


async def test_continue_without_restart_refuses_a_failed_helper(tmp_path):
    import pytest

    from jenny.agent.runner import AgentRunResult
    from jenny.agent.subagent import SubagentSendError

    manager = _manager(tmp_path, fake_provider(_scripted([])))

    async def _fail(spec):
        return AgentRunResult(final_content=None, messages=[], stop_reason="error", error="x")

    manager.runner.run = _fail  # type: ignore[method-assign]
    await manager.spawn("x", session_key="unified:default")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    record = manager.list_records("unified:default")[-1]

    with pytest.raises(SubagentSendError, match="from scratch"):
        await manager.send(record.task_id, "go on", allow_restart=False)
    assert not manager._running_tasks


async def test_the_budget_notice_is_not_saved_in_the_history(tmp_path):
    from jenny.session.history_meta import INJECTED_EVENT_META
    from jenny.utils.runtime import BUDGET_NOTICE_EVENT

    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)

    # L'avviso c'era nel run (max 3: dopo la seconda chiamata ne resta una)...
    assert any("model calls left" in str(m.get("content"))
               for kw in seen for m in kw["messages"])
    # ...ma non torna in una ripresa, che ha un budget nuovo.
    history = manager._history.load(record.lineage_id)
    assert not any(m.get(INJECTED_EVENT_META) == BUDGET_NOTICE_EVENT for m in history)
    assert not any("model calls left" in str(m.get("content")) for m in history)


async def test_the_announce_carries_the_plan_step_without_being_a_plan_turn(tmp_path):
    from jenny.agent.loop_plan import is_plan_turn
    from jenny.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META

    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    token = bind_request_context(RequestContext(
        channel="websocket", chat_id="default", session_key="unified:default",
        metadata={PLAN_ID_META: "plan-1", PLAN_STEP_META: "s3"},
    ))
    try:
        announce, _ = await _stop_at_budget(manager)
    finally:
        reset_request_context(token)

    assert announce.metadata[PLAN_ID_META] == "plan-1"
    assert announce.metadata[PLAN_STEP_META] == "s3"
    assert is_plan_turn(announce.metadata) is False

    # Un aiutante lanciato dal turno d'annuncio eredita il passo.
    token = bind_request_context(RequestContext(
        channel="websocket", chat_id="default", session_key="unified:default",
        metadata=dict(announce.metadata),
    ))
    try:
        await manager.spawn("next plant", session_key="unified:default")
    finally:
        reset_request_context(token)
    running = manager.status_snapshot("unified:default")["running"][0]
    assert running["plan_step"] == "s3"
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)


async def test_a_pruned_history_is_not_offered_as_resumable(tmp_path):
    seen: list[dict[str, Any]] = []
    manager = _manager(tmp_path, fake_provider(_scripted(seen)))
    _, record = await _stop_at_budget(manager)
    assert manager.status_snapshot("unified:default")["recent"][0]["resumable"] is True

    manager._history.drop(record.lineage_id)

    assert manager.status_snapshot("unified:default")["recent"][0]["resumable"] is False
