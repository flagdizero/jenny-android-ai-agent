"""Un aiutante fermato da un errore passeggero del provider si riprende, non si rifà.

Nella riprova del 25/10/2026 nove richieste sono fallite con un 429 di OpenCode
Go dopo i tentativi: otto aiutanti chiusi in ``failed``, la loro conversazione
cancellata, e Jenny li ha rilanciati da zero. Qui si fissa che gli aiutanti
usano la scala di tentativi lunga, che un errore passeggero lascia la storia
salvata e l'annuncio dice di continuare, e che un errore vero resta com'era.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.runner import AgentRunResult, AgentRunSpec
from jenny.agent.subagent import SubagentManager
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.session.manager import SessionManager

BRIEF = "Search the pruning of the maple in a pot."


def _manager(tmp_path: Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        stall_threshold_s=0.0,
        session_manager=SessionManager(tmp_path),
    )


def _failing_runner(sm: SubagentManager, error_kind: str) -> list[AgentRunSpec]:
    """Il primo run legge un file e poi si ferma su un errore del provider; i
    successivi finiscono bene."""
    seen: list[AgentRunSpec] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        seen.append(spec)
        if len(seen) == 1:
            messages = list(spec.initial_messages) + [
                {"role": "assistant", "content": "I read three nursery pages so far."},
            ]
            return AgentRunResult(
                final_content="rate limited", messages=messages, stop_reason="error",
                error="Error calling LLM: HTTP 429", error_kind=error_kind,
            )
        messages = list(spec.initial_messages) + [{"role": "assistant", "content": "done"}]
        return AgentRunResult(final_content="done", messages=messages, stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    return seen


async def _settle(sm: SubagentManager) -> None:
    await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)


async def _announces(sm: SubagentManager) -> list[str]:
    out: list[str] = []
    while not sm.bus.inbound.empty():
        out.append((await sm.bus.consume_inbound()).content)
    return out


async def test_helpers_use_the_long_retry_scale(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    seen = _failing_runner(sm, "rate_limit")
    await sm.spawn(task=BRIEF, session_key="unified:default")
    await _settle(sm)
    assert seen[0].provider_retry_mode == "background"


@pytest.mark.parametrize("kind", ["rate_limit", "server", "timeout", "connection"])
async def test_a_transient_provider_error_keeps_the_history_and_resumes(
    tmp_path: Path, kind: str,
) -> None:
    sm = _manager(tmp_path)
    seen = _failing_runner(sm, kind)
    await sm.spawn(task=BRIEF, session_key="unified:default")
    lineage = next(iter(sm._lineages))
    await _settle(sm)

    announce = (await _announces(sm))[-1]
    assert "provider kept failing for a while" in announce
    assert "subagent_send" in announce
    assert sm._history.load(lineage) is not None

    outcome = await sm.send(lineage, "Go on from where you stopped.")
    await _settle(sm)
    assert outcome.mode == "resumed"
    seed = seen[-1].initial_messages
    assert any("three nursery pages" in str(m.get("content")) for m in seed)
    assert seed[-1] == {"role": "user", "content": "Go on from where you stopped."}


async def test_a_real_error_still_drops_the_history(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    _failing_runner(sm, "auth")
    await sm.spawn(task=BRIEF, session_key="unified:default")
    lineage = next(iter(sm._lineages))
    await _settle(sm)

    announce = (await _announces(sm))[-1]
    assert "provider kept failing" not in announce
    assert sm._history.load(lineage) is None


async def test_a_provider_wait_counts_as_progress(tmp_path: Path) -> None:
    """Le attese dei tentativi tengono vivo l'aiutante per il watchdog (180 s)."""
    sm = _manager(tmp_path)
    seen = _failing_runner(sm, "rate_limit")
    await sm.spawn(task=BRIEF, session_key="unified:default")
    await _settle(sm)
    callback = seen[0].retry_wait_callback
    assert callback is not None

    from jenny.agent.subagent import SubagentStatus

    status = SubagentStatus(task_id="t", label="x", task_description="y", started_at=0.0)
    status.state = "stalled"
    from jenny.agent.subagent import _retry_wait_touch

    await _retry_wait_touch(status)("waiting 60s")
    assert status.state == "running" and status.last_progress_at > 0.0
