"""Subagent forwards loop-provided LLM wall-timeout resolver into AgentRunSpec."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import SubagentManager, SubagentSpec, SubagentStatus
from jenny.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_subagent_forwards_resolver_to_agent_run_spec(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=64,
        llm_wall_timeout_for_session=lambda sk: 0.0 if sk == "internal:direct" else None,
    )

    mgr.runner.run = AsyncMock(
        return_value=AgentRunResult(final_content="ok", messages=[], stop_reason="completed")
    )
    mgr._announce_result = AsyncMock()

    status = SubagentStatus(
        task_id="t1",
        label="lbl",
        task_description="task",
        started_at=0.0,
    )
    await mgr._run_subagent(
        "t1",
        SubagentSpec(task="task", label="lbl", session_key="internal:direct"),
        status,
    )
    mgr.runner.run.assert_called_once()
    spec = mgr.runner.run.call_args[0][0]
    assert spec.session_key == "internal:direct"
    assert spec.llm_timeout_s == 0.0

