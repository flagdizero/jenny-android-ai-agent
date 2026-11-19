"""Gli strumenti delle altre Jenny si caricano davvero, anche da orchestratore.

I test delle domande costruiscono il registro a mano: questo passa dal loader
vero, che filtra per scope. Con ``orchestrator_mode`` (il default) un tool che
dichiara solo ``core`` non arriva mai nella chat personale.
"""

from __future__ import annotations

import pytest

from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.registry import ToolRegistry
from jenny.config.schema import ToolsConfig
from jenny.peers.questions import answer_tools


def _load(tmp_path, scope: str) -> ToolRegistry:
    registry = ToolRegistry()
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path))
    ToolLoader().load(ctx, registry, scope=scope)
    return registry


@pytest.mark.parametrize("scope", ["orchestrator", "core"])
def test_peer_tools_load_in_the_main_agent_scopes(tmp_path, scope):
    registry = _load(tmp_path, scope)
    assert registry.has("peer_ask")
    assert registry.has("peer_attach")


def test_the_answer_view_over_the_orchestrator_registry_keeps_peer_attach(tmp_path):
    offered = set(answer_tools(_load(tmp_path, "orchestrator")).tool_names)
    assert "peer_attach" in offered
    assert {"read_file", "list_dir", "grep", "recall_chat"} <= offered
    assert offered.isdisjoint({"message", "spawn", "peer_ask", "app"})


def test_helpers_never_get_the_peer_tools(tmp_path):
    registry = _load(tmp_path, "subagent")
    assert not registry.has("peer_ask")
    assert not registry.has("peer_attach")
