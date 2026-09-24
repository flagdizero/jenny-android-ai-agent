"""Lo ``AgentRunSpec`` e i tool finti dei test del runner.

Quasi ogni test di ``tests/agent/test_runner_*.py`` costruiva lo spec con le
stesse due righe — ``model="test-model"`` e il tetto dei risultati dei tool al
default — e una buona parte il registro dei tool vuoto con le stesse tre.
:func:`make_spec` mette le due righe, :func:`empty_tools` le tre.

``max_iterations`` resta sempre scritto nel test: è lui che decide lo
``stop_reason`` che tanti di quei test verificano, e un default nascosto qui
cambierebbe l'esito senza che il test lo dica. Chi usa un tetto diverso dal
default (``test_tool_error_budget.py``) lo passa.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from jenny.agent.runner import AgentRunSpec
from jenny.config.schema import AgentDefaults

MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def make_spec(**fields: Any) -> AgentRunSpec:
    """``AgentRunSpec`` con ``model="test-model"`` e il tetto di default."""
    fields.setdefault("model", "test-model")
    fields.setdefault("max_tool_result_chars", MAX_TOOL_RESULT_CHARS)
    return AgentRunSpec(**fields)


def empty_tools(result: str = "tool result") -> MagicMock:
    """Un registro senza definizioni, la cui ``execute`` risponde *result*."""
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value=result)
    return tools
