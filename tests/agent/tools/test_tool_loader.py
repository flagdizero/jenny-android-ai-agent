"""Tests for tool plugin architecture: ToolLoader, ToolContext, metadata."""
from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.tools import loader as loader_mod
from jenny.agent.tools.base import Tool
from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.loader import ToolLoader, ToolLoadError
from jenny.agent.tools.registry import ToolRegistry


class _MinimalTool(Tool):
    @property
    def name(self) -> str:
        return "test_minimal"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return "ok"


def test_tool_default_enabled_is_true():
    assert _MinimalTool.enabled(None) is True


def test_tool_default_create_returns_instance():
    tool = _MinimalTool.create(None)
    assert isinstance(tool, _MinimalTool)
    assert tool.name == "test_minimal"


# --- ToolContext tests ---


def test_tool_context_has_required_fields():
    field_names = {f.name for f in fields(ToolContext)}
    required = {
        "config", "workspace", "bus", "subagent_manager",
        "cron_service", "file_states",
        "timezone",
    }
    assert required <= field_names


def test_tool_context_defaults():
    ctx = ToolContext(config=None, workspace="/tmp")
    assert ctx.bus is None
    assert ctx.subagent_manager is None
    assert ctx.cron_service is None
    assert ctx.timezone == "UTC"


# --- ToolLoader tests ---


def test_discover_finds_concrete_tools():
    loader = ToolLoader()
    discovered = loader.discover()
    class_names = {cls.__name__ for cls in discovered}
    assert "ApplyPatchTool" in class_names
    assert "PythonExecTool" in class_names
    assert "MessageTool" in class_names
    assert "SpawnTool" in class_names
    assert "WriteStdinTool" in class_names
    assert "UiViewTool" in class_names


def test_discover_excludes_abstract_and_mcp():
    loader = ToolLoader()
    discovered = loader.discover()
    class_names = {cls.__name__ for cls in discovered}
    assert "_FsTool" not in class_names
    assert "_SearchTool" not in class_names
    assert "MCPToolWrapper" not in class_names
    assert "MCPResourceWrapper" not in class_names
    assert "MCPPromptWrapper" not in class_names


def test_discover_skips_private_classes():
    loader = ToolLoader()
    discovered = loader.discover()
    for cls in discovered:
        assert not cls.__name__.startswith("_")


def test_loader_registers_exec_with_real_tools_config(tmp_path):
    """Real config objects catch bad ctx.config attribute paths that mocks hide."""
    from types import SimpleNamespace

    from jenny.agent.tools.registry import ToolRegistry
    from jenny.config.schema import ToolsConfig

    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        bus=None,
        subagent_manager=SimpleNamespace(
            get_running_count=lambda: 0,
            max_concurrent_subagents=4,
        ),
        cron_service=None,
        timezone="UTC",
    )
    registry = ToolRegistry()
    registered = ToolLoader().load(ctx, registry)

    assert "python_exec" in registered
    assert registry.has("python_exec")


# --- Task 4: _FsTool.create() ---


def test_fs_tool_create_builds_from_context():
    from jenny.agent.tools.filesystem import ReadFileTool
    mock_config = MagicMock()
    mock_config.restrict_to_workspace = False
    ctx = ToolContext(config=mock_config, workspace="/tmp/test")
    tool = ReadFileTool.create(ctx)
    assert isinstance(tool, ReadFileTool)
    assert tool._workspace == Path("/tmp/test")


def test_fs_tool_create_respects_restrict_to_workspace():
    from jenny.agent.tools.filesystem import ReadFileTool
    mock_config = MagicMock()
    mock_config.restrict_to_workspace = True
    ctx = ToolContext(config=mock_config, workspace="/tmp/test")
    tool = ReadFileTool.create(ctx)
    assert tool._allowed_dir == Path("/tmp/test")


def test_fs_tool_create_respects_sandbox():
    from jenny.agent.tools.filesystem import ReadFileTool
    mock_config = MagicMock()
    mock_config.restrict_to_workspace = False
    ctx = ToolContext(config=mock_config, workspace="/tmp/test")
    tool = ReadFileTool.create(ctx)
    assert tool._allowed_dir is None


# --- Task 5: MessageTool, SpawnTool, CronTool ---


async def test_message_tool_create():
    from jenny.agent.tools.message import MessageTool
    mock_bus = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", bus=mock_bus)
    tool = MessageTool.create(ctx)
    assert isinstance(tool, MessageTool)


def test_spawn_tool_create():
    from jenny.agent.tools.spawn import SpawnTool
    mock_mgr = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", subagent_manager=mock_mgr)
    tool = SpawnTool.create(ctx)
    assert isinstance(tool, SpawnTool)


def test_cron_tool_enabled_without_service():
    from jenny.agent.tools.cron import CronTool
    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", cron_service=None)
    assert CronTool.enabled(ctx) is False


def test_cron_tool_enabled_with_service():
    from jenny.agent.tools.cron import CronTool
    mock_service = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(config=mock_config, workspace="/tmp", cron_service=mock_service)
    assert CronTool.enabled(ctx) is True


def test_cron_tool_create():
    from jenny.agent.tools.cron import CronTool
    mock_service = MagicMock()
    mock_config = MagicMock()
    ctx = ToolContext(
        config=mock_config, workspace="/tmp",
        cron_service=mock_service, timezone="Asia/Shanghai",
    )
    tool = CronTool.create(ctx)
    assert isinstance(tool, CronTool)


# --- Task 6: PythonExecTool, WebTools ---


def test_python_exec_tool_enabled():
    from jenny.agent.tools.python_exec import PythonExecTool
    mock_config = MagicMock()
    mock_config.python_exec.enable = True
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    assert PythonExecTool.enabled(ctx) is True
    mock_config.python_exec.enable = False
    assert PythonExecTool.enabled(ctx) is False


def test_python_exec_tool_create():
    from jenny.agent.tools.python_exec import PythonExecTool
    mock_config = MagicMock()
    mock_config.python_exec.enable = True
    mock_config.python_exec.timeout = 120
    mock_config.python_exec.max_output_chars = 10_000
    mock_config.python_exec.allowed_modules = ["os", "json"]
    mock_config.python_exec.blocked_modules = ["subprocess"]
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    tool = PythonExecTool.create(ctx)
    assert isinstance(tool, PythonExecTool)
    assert tool.timeout == 120



# --- Task 7: MyToolConfig ---


def test_my_tool_enabled():
    from jenny.agent.tools.self import MyTool
    mock_config = MagicMock()
    mock_config.my.enable = True
    ctx = ToolContext(config=mock_config, workspace="/tmp")
    assert MyTool.enabled(ctx) is True
    mock_config.my.enable = False
    assert MyTool.enabled(ctx) is False


# --- Task 10: Integration test ---


def test_loader_registers_same_tools_as_old_hardcoded():
    """Verify the loader produces the same tool set as the old _register_default_tools."""
    from jenny.agent.tools.loader import ToolLoader
    from jenny.agent.tools.registry import ToolRegistry

    mock_config = MagicMock()
    mock_config.python_exec.enable = True
    mock_config.python_exec.timeout = 60
    mock_config.python_exec.max_output_chars = 10_000
    mock_config.python_exec.allowed_modules = []
    mock_config.python_exec.blocked_modules = []
    mock_config.restrict_to_workspace = False
    mock_config.android_web.enable = True
    mock_config.android_web.search = MagicMock()
    mock_config.android_web.fetch = MagicMock()
    mock_config.android_web.proxy = None
    mock_config.android_web.user_agent = None
    mock_config.my.enable = True

    ctx = ToolContext(
        config=mock_config,
        workspace="/tmp",
        bus=MagicMock(),
        subagent_manager=MagicMock(),
        cron_service=MagicMock(),
        timezone="UTC",
    )
    registry = ToolRegistry()
    loader = ToolLoader()
    registered = loader.load(ctx, registry)

    expected = {
        "read_file", "write_file", "edit_file", "list_dir",
        "find_files", "grep", "python_exec", "write_stdin", "list_exec_sessions",
        "message", "spawn", "cron",
    }
    actual = set(registered)
    assert expected <= actual, f"Missing tools: {expected - actual}"


# --- Load failure policy: fatal vs tolerated ---------------------------------
#
# A name collision (and, in discover(), a module without TOOLS) is a
# programming error: it must abort startup instead of being logged and
# skipped. A failing enabled()/create() depends on the runtime environment:
# the tool is dropped for that run, but loudly and inspectably.


def _tool_named(class_name: str, tool_name: str, **attrs: Any) -> type[Tool]:
    """Build a throwaway Tool subclass with a fixed public tool name."""
    body: dict[str, Any] = {
        "name": property(lambda self: tool_name),
        "description": property(lambda self: "test tool"),
        "parameters": property(lambda self: {"type": "object", "properties": {}}),
        "execute": lambda self, **kwargs: "ok",
    }
    body.update(attrs)
    return type(class_name, (Tool,), body)


def _fake_tool_package(monkeypatch: pytest.MonkeyPatch, **modules: ModuleType) -> ModuleType:
    """Register a synthetic tool package and point the loader's module list at it."""
    pkg = ModuleType("jenny_test_toolpkg")
    pkg.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, pkg.__name__, pkg)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, f"{pkg.__name__}.{name}", module)
    monkeypatch.setattr(loader_mod, "_HARDCODED_TOOL_MODULES", list(modules))
    return pkg


def test_load_raises_on_name_collision():
    """A duplicate tool name aborts load() instead of being logged and skipped."""
    first = _tool_named("FirstTool", "dup_name")
    second = _tool_named("SecondTool", "dup_name")
    registry = ToolRegistry()

    with pytest.raises(ToolLoadError) as excinfo:
        ToolLoader(test_classes=[first, second]).load(None, registry)

    message = str(excinfo.value)
    assert "dup_name" in message
    assert "SecondTool" in message


def test_tool_load_error_is_a_runtime_error():
    assert issubclass(ToolLoadError, RuntimeError)


def test_collision_is_not_swallowed_by_the_tolerant_path():
    """The tolerant except must not catch the collision raise (the original bug)."""
    tool_cls = _tool_named("OnlyTool", "same_name")
    registry = ToolRegistry()
    registry.register(tool_cls.create(None))

    loader = ToolLoader(test_classes=[tool_cls])
    with pytest.raises(ToolLoadError):
        loader.load(None, registry)
    assert loader.failures == []


def test_discover_raises_when_module_declares_no_tools(monkeypatch):
    """A tool module without a TOOLS list is still a hard startup error."""
    module = ModuleType("jenny_test_toolpkg.no_tools")  # no TOOLS attribute
    pkg = _fake_tool_package(monkeypatch, no_tools=module)

    with pytest.raises(ToolLoadError) as excinfo:
        ToolLoader(package=pkg).discover()

    assert "TOOLS" in str(excinfo.value)


def test_load_raises_when_module_declares_no_tools(monkeypatch):
    """The abort propagates through load(), not just discover()."""
    module = ModuleType("jenny_test_toolpkg.no_tools")
    pkg = _fake_tool_package(monkeypatch, no_tools=module)

    with pytest.raises(ToolLoadError):
        ToolLoader(package=pkg).load(None, ToolRegistry())


def test_discover_raises_on_non_tool_entry(monkeypatch):
    module = ModuleType("jenny_test_toolpkg.bad_entry")
    module.TOOLS = ["not_a_class"]  # type: ignore[attr-defined]
    pkg = _fake_tool_package(monkeypatch, bad_entry=module)

    with pytest.raises(ToolLoadError) as excinfo:
        ToolLoader(package=pkg).discover()

    assert "not a Tool subclass" in str(excinfo.value)


def test_normal_load_registers_every_tool_without_failures():
    """The happy path is unchanged: distinct names all register, no failures."""
    alpha = _tool_named("AlphaTool", "alpha")
    beta = _tool_named("BetaTool", "beta")
    registry = ToolRegistry()

    loader = ToolLoader(test_classes=[alpha, beta])
    registered = loader.load(None, registry)

    assert registered == ["alpha", "beta"]
    assert registry.has("alpha") and registry.has("beta")
    assert loader.failures == []


def test_real_tool_set_loads_without_silent_failures(tmp_path):
    """The shipped tool modules must all construct — no tool quietly missing."""
    from types import SimpleNamespace

    from jenny.config.schema import ToolsConfig

    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        subagent_manager=SimpleNamespace(
            get_running_count=lambda: 0,
            max_concurrent_subagents=4,
        ),
        timezone="UTC",
    )
    loader = ToolLoader()
    loader.load(ctx, ToolRegistry())

    assert loader.failures == [], [
        (f.tool, f.stage, repr(f.error)) for f in loader.failures
    ]


def test_create_failure_is_tolerated_and_recorded():
    """An environment-dependent create() failure drops one tool, not the boot."""
    def _boom(cls, ctx):
        raise RuntimeError("no android context")

    broken = _tool_named("BrokenTool", "broken", create=classmethod(_boom))
    healthy = _tool_named("HealthyTool", "healthy")
    registry = ToolRegistry()

    loader = ToolLoader(test_classes=[broken, healthy])
    registered = loader.load(None, registry)

    assert registered == ["healthy"]
    assert not registry.has("broken")
    assert [(f.tool, f.stage) for f in loader.failures] == [("BrokenTool", "create")]
    assert isinstance(loader.failures[0].error, RuntimeError)


def test_enabled_failure_is_tolerated_and_recorded():
    def _boom(cls, ctx):
        raise ValueError("missing config key")

    broken = _tool_named("BrokenTool", "broken", enabled=classmethod(_boom))
    registry = ToolRegistry()

    loader = ToolLoader(test_classes=[broken])
    registered = loader.load(None, registry)

    assert registered == []
    assert [(f.tool, f.stage) for f in loader.failures] == [("BrokenTool", "enabled")]


def test_failures_are_reset_between_loads():
    def _boom(cls, ctx):
        raise RuntimeError("boom")

    broken = _tool_named("BrokenTool", "broken", create=classmethod(_boom))
    loader = ToolLoader(test_classes=[broken])

    loader.load(None, ToolRegistry())
    assert len(loader.failures) == 1
    loader.load(None, ToolRegistry())
    assert len(loader.failures) == 1
