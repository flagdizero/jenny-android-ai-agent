"""Chi si accende e chi spiega perché è spento: la tabella, tool per tool.

Le condizioni erano copiate identiche in più classi (tre per il web e il
browser, tre per python_exec e le sue sessioni). Questo banco fissa la
risposta di ciascuna su ogni combinazione che conta, prima e dopo averle
raccolte in un posto solo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jenny.agent.tools.android_web import AndroidWebFetchTool, AndroidWebSearchTool
from jenny.agent.tools.browser import (
    BrowserCloseTool,
    BrowserDoTool,
    BrowserOpenTool,
    BrowserReadTool,
    BrowserSnapshotTool,
)
from jenny.agent.tools.exec_session import ListExecSessionsTool, WriteStdinTool
from jenny.agent.tools.python_exec import PythonExecTool

WEB_TOOLS = [
    AndroidWebSearchTool, AndroidWebFetchTool, BrowserOpenTool, BrowserSnapshotTool,
    BrowserDoTool, BrowserReadTool, BrowserCloseTool,
]
PY_TOOLS = [PythonExecTool, WriteStdinTool, ListExecSessionsTool]

_OFF = "web access is off (Settings > Tools > Web Search)"

WEB_CASES = {
    # (android_context, android_web config) -> (enabled, disabled_reason)
    "no_android": (None, SimpleNamespace(enable=True), False, None),
    "no_android_and_off": (None, SimpleNamespace(enable=False), False, None),
    "no_config": (object(), None, False, None),
    "off": (object(), SimpleNamespace(enable=False), False, _OFF),
    "on": (object(), SimpleNamespace(enable=True), True, None),
}


@pytest.mark.parametrize("tool", WEB_TOOLS, ids=lambda t: t.__name__)
@pytest.mark.parametrize("case", sorted(WEB_CASES))
def test_web_tools_gate(tool, case: str) -> None:
    android, web, enabled, reason = WEB_CASES[case]
    ctx = SimpleNamespace(android_context=android, config=SimpleNamespace(android_web=web))
    assert bool(tool.enabled(ctx)) is enabled
    assert tool.disabled_reason(ctx) == reason


PY_CASES = {
    "no_config": (None, True),
    "off": (SimpleNamespace(enable=False), False),
    "on": (SimpleNamespace(enable=True), True),
}


@pytest.mark.parametrize("tool", PY_TOOLS, ids=lambda t: t.__name__)
@pytest.mark.parametrize("case", sorted(PY_CASES))
def test_python_exec_tools_gate(tool, case: str) -> None:
    cfg, enabled = PY_CASES[case]
    ctx = SimpleNamespace(config=SimpleNamespace(python_exec=cfg))
    assert bool(tool.enabled(ctx)) is enabled
