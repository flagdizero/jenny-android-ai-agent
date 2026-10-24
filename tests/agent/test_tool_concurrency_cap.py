"""Letture a gruppi, con un tetto, anche per gli aiutanti.

Prova del 24/10/2026: l'aiutante della bacheca ha fatto 39 ``read_file`` in 39
iterazioni, perche lo spec del subagent non accendeva ``concurrent_tools``. Ora lo
accende; e il gruppo concorrente, che ``asyncio.gather`` lanciava tutto insieme,
ha un tetto (:data:`MAX_CONCURRENT_TOOLS`) per non affogare il telefono.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from support.runner import make_spec

from jenny.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from jenny.agent.subagent import SubagentManager
from jenny.agent.tool_execution import MAX_CONCURRENT_TOOLS
from jenny.agent.tools.base import Tool
from jenny.agent.tools.registry import ToolRegistry
from jenny.bus.queue import MessageBus
from jenny.providers.base import ToolCallRequest


class _CountingTool(Tool):
    """Tool che misura quanti come lui sono in volo insieme."""

    def __init__(self, name: str, gauge: dict[str, int], *, read_only: bool = True) -> None:
        self._name = name
        self._gauge = gauge
        self._read_only = read_only

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"n": {"type": "integer"}}, "required": []}

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, n: int = 0, **kwargs):
        self._gauge["now"] += 1
        self._gauge["peak"] = max(self._gauge["peak"], self._gauge["now"])
        if not self._read_only:
            self._gauge["writes_overlapping"] = max(
                self._gauge.get("writes_overlapping", 0), self._gauge["now"],
            )
        await asyncio.sleep(0.01)
        self._gauge["now"] -= 1
        return f"{self._name}-{n}"


def _registry(gauge: dict[str, int]) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(_CountingTool("read", gauge))
    tools.register(_CountingTool("write", gauge, read_only=False))
    return tools


async def test_a_large_concurrent_batch_is_capped() -> None:
    gauge = {"now": 0, "peak": 0}
    calls = [ToolCallRequest(id=f"c{i}", name="read", arguments={"n": i}) for i in range(20)]

    results, events, fatal = await AgentRunner(MagicMock())._execute_tools(
        make_spec(initial_messages=[], tools=_registry(gauge), max_iterations=1,
                  concurrent_tools=True),
        calls, {}, {},
    )

    assert fatal is None
    assert gauge["peak"] == MAX_CONCURRENT_TOOLS == 8
    assert results == [f"read-{i}" for i in range(20)], "results keep the call order"
    assert [e["status"] for e in events] == ["ok"] * 20


async def test_reads_overlap_and_writes_stay_alone() -> None:
    gauge = {"now": 0, "peak": 0}
    calls = [
        ToolCallRequest(id="r1", name="read", arguments={"n": 1}),
        ToolCallRequest(id="r2", name="read", arguments={"n": 2}),
        ToolCallRequest(id="r3", name="read", arguments={"n": 3}),
        ToolCallRequest(id="w1", name="write", arguments={"n": 4}),
    ]

    results, _, _ = await AgentRunner(MagicMock())._execute_tools(
        make_spec(initial_messages=[], tools=_registry(gauge), max_iterations=1,
                  concurrent_tools=True),
        calls, {}, {},
    )

    assert gauge["peak"] == 3, "the three reads ran together"
    assert gauge["writes_overlapping"] == 1, "the write ran alone"
    assert results == ["read-1", "read-2", "read-3", "write-4"]


async def test_a_subagent_runs_its_tools_concurrently(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    manager = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(), model="m",
        max_tool_result_chars=16_000, stall_threshold_s=0.0,
    )
    seen: list[AgentRunSpec] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        seen.append(spec)
        return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

    manager.runner.run = _run  # type: ignore[method-assign]
    await manager.spawn("read three files", session_key="unified:default")
    await asyncio.gather(*manager._running_tasks.values(), return_exceptions=True)

    assert seen and seen[0].concurrent_tools is True
    assert seen[0].finalize_on_max_iterations is True
    assert seen[0].iteration_notice is not None


class _MaybeMissingTool(_CountingTool):
    """``read_file`` finto: per gli ``n`` in *missing* risponde con un errore."""

    def __init__(self, gauge: dict[str, int], missing: set[int]) -> None:
        super().__init__("read", gauge)
        self._missing = missing

    async def execute(self, n: int = 0, **kwargs):
        await asyncio.sleep(0.001 * (n % 5))
        if n in self._missing:
            return f"Error: File not found: page-{n}.md"
        return f"read-{n}"


async def test_a_batch_with_misses_spends_one_error_unit() -> None:
    """Trenta letture, sette mancate, budget 3: prima ognuna scalava per conto suo
    e il run moriva alla quarta, secondo l'ordine di arrivo."""
    from jenny.agent.tool_execution import ToolErrorBudget

    tools = ToolRegistry()
    tools.register(_MaybeMissingTool({"now": 0, "peak": 0}, {2, 5, 9, 13, 17, 21, 28}))
    spec = make_spec(initial_messages=[], tools=tools, max_iterations=1,
                     concurrent_tools=True, fail_on_tool_error=True, tool_error_budget=3)
    budget = ToolErrorBudget.from_spec(spec)
    calls = [ToolCallRequest(id=f"c{i}", name="read", arguments={"n": i}) for i in range(30)]

    results, events, fatal = await AgentRunner(MagicMock())._execute_tools(
        spec, calls, {}, {}, tool_errors=budget,
    )

    assert fatal is None
    assert (budget.consecutive, budget.total) == (1, 1)
    assert sum(1 for e in events if e["status"] == "error") == 7
    assert results[0] == "read-0"


async def test_batches_with_misses_still_end_a_flailing_run() -> None:
    from jenny.agent.tool_execution import ToolErrorBudget

    tools = ToolRegistry()
    tools.register(_MaybeMissingTool({"now": 0, "peak": 0}, {0, 1}))
    spec = make_spec(initial_messages=[], tools=tools, max_iterations=1,
                     concurrent_tools=True, fail_on_tool_error=True, tool_error_budget=1)
    budget = ToolErrorBudget.from_spec(spec)
    calls = [ToolCallRequest(id=f"c{i}", name="read", arguments={"n": i}) for i in range(3)]

    _, _, first = await AgentRunner(MagicMock())._execute_tools(spec, calls, {}, {},
                                                                tool_errors=budget)
    _, _, second = await AgentRunner(MagicMock())._execute_tools(spec, calls, {}, {},
                                                                 tool_errors=budget)

    assert first is None
    assert second is not None and "2 failed calls in one batch" in str(second)


async def test_a_clean_batch_resets_the_consecutive_count() -> None:
    from jenny.agent.tool_execution import ToolErrorBudget

    tools = ToolRegistry()
    tools.register(_MaybeMissingTool({"now": 0, "peak": 0}, set()))
    spec = make_spec(initial_messages=[], tools=tools, max_iterations=1,
                     concurrent_tools=True, fail_on_tool_error=True, tool_error_budget=3)
    budget = ToolErrorBudget.from_spec(spec)
    budget.consecutive = 2
    calls = [ToolCallRequest(id=f"c{i}", name="read", arguments={"n": i}) for i in range(3)]

    await AgentRunner(MagicMock())._execute_tools(spec, calls, {}, {}, tool_errors=budget)

    assert budget.consecutive == 0
