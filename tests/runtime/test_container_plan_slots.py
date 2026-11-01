"""Il container da al manager degli aiutanti il limite dei piani.

Il manager non sa cosa sia un piano: ``_wire_plan_driver`` gli inietta una
funzione che chiede al driver se la sessione ha un piano che gira, e in quel caso
concede :data:`PLAN_HELPER_SLOTS` aiutanti normali (piu uno breve).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.agent.subagent import PLAN_HELPER_SLOTS, SubagentManager
from jenny.bus.queue import MessageBus
from jenny.plan.driver import PlanDriver
from jenny.runtime.container import GatewayContainer


class _Agent:
    def __init__(self, workspace: Path) -> None:
        provider = MagicMock()
        provider.get_default_model.return_value = "m"
        self.subagents = SubagentManager(
            provider=provider, workspace=workspace, bus=MessageBus(), model="m",
            max_tool_result_chars=16_000, stall_threshold_s=0.0,
        )
        self.driver: PlanDriver | None = None

    def attach_plan_driver(self, driver: PlanDriver) -> None:
        self.driver = driver


def test_the_plan_driver_sets_the_helper_limit(tmp_path: Path, monkeypatch) -> None:
    # Gli agganci globali del driver restano fuori: questo test guarda solo il limite.
    monkeypatch.setattr("jenny.session.plan_probe.set_plan_probe", lambda _probe: None)
    monkeypatch.setattr(
        "jenny.security.workspace_access.set_held_notebook_probe", lambda _probe: None,
    )
    monkeypatch.setattr(PlanDriver, "watch_drafts", lambda self: None)
    container = GatewayContainer.__new__(GatewayContainer)
    container.bus = MessageBus()
    agent = _Agent(tmp_path)

    container._wire_plan_driver(agent, SimpleNamespace(workspace_path=tmp_path))

    assert agent.driver is not None
    limit_for = agent.subagents._limit_provider
    assert limit_for is not None
    assert limit_for("project:piante") is None, "no plan, the config limit"
    agent.driver.plan_running = lambda key: key == "project:piante"  # type: ignore[method-assign]
    assert limit_for("project:piante") == PLAN_HELPER_SLOTS
    assert limit_for("unified:default") is None
    assert limit_for(None) is None
