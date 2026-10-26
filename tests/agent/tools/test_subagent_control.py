"""Tests per lo scope "orchestrator" e i tool di controllo dei subagent."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from jenny.agent.loop import AgentLoop
from jenny.agent.subagent import (
    SubagentConcurrencyLimitError,
    SubagentRestartError,
)
from jenny.agent.tools.context import RequestContext, ToolContext
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.tools.subagent_control import (
    SubagentCancelTool,
    SubagentRestartTool,
    SubagentSendTool,
    SubagentStatusTool,
)
from jenny.bus.queue import MessageBus
from jenny.config.schema import ToolsConfig

# Tool che l'orchestratore deve conservare e tool che deve perdere. Sono la
# ragione della fase: l'output dei secondi gonfia la sessione dell'utente.
ORCHESTRATOR_KEEPS = {
    "spawn", "subagent_status", "subagent_cancel", "subagent_restart",
    "subagent_send",
    "cron", "message", "ui_view", "long_task", "complete_goal",
    "get_source", "get_recent_logs", "read_file", "list_dir",
}
# Tutto cio che produce output grosso, o che scrive. ``grep`` non e piu qui:
# in modalita orchestratore esiste come solo indice — percorsi e conteggi, mai
# le righe — quindi trova senza gonfiare la conversazione. ``find_files`` resta
# fuori perche ``grep`` in modalita indice fa gia lo stesso mestiere meglio.
ORCHESTRATOR_LOSES = {
    "python_exec", "write_file", "edit_file", "apply_patch", "download_file",
    "web_search", "web_fetch", "list_exec_sessions", "write_stdin",
    "find_files",
}


def _ctx(tmp_path: Path, **kw: Any) -> ToolContext:
    defaults: dict[str, Any] = dict(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        file_states=FileStates(),
        bus=MessageBus(),
        subagent_manager=MagicMock(),
        cron_service=MagicMock(),
        sessions=MagicMock(),
        ui_query_service=MagicMock(),
        android_context=object(),
    )
    defaults.update(kw)
    return ToolContext(**defaults)


def _load(scope: str, tmp_path: Path) -> set[str]:
    registry = ToolRegistry()
    return set(ToolLoader().load(_ctx(tmp_path), registry, scope=scope))


def test_orchestrator_scope_keeps_control_and_read_only_tools(tmp_path: Path) -> None:
    names = _load("orchestrator", tmp_path)
    assert ORCHESTRATOR_KEEPS <= names, ORCHESTRATOR_KEEPS - names


def test_orchestrator_scope_drops_context_heavy_tools(tmp_path: Path) -> None:
    names = _load("orchestrator", tmp_path)
    assert not (names & ORCHESTRATOR_LOSES), names & ORCHESTRATOR_LOSES


def test_subagent_scope_has_no_control_tools(tmp_path: Path) -> None:
    """Un subagent non guida i fratelli: e cio che impedisce la ricorsione."""
    names = _load("subagent", tmp_path)
    assert "spawn" not in names
    assert not {n for n in names if n.startswith("subagent_")}


def test_core_scope_has_no_control_tools(tmp_path: Path) -> None:
    names = _load("core", tmp_path)
    assert not {n for n in names if n.startswith("subagent_")}


def test_orchestrator_mode_off_reproduces_the_old_registry(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    def loop_tools(orchestrator: bool) -> set[str]:
        loop = AgentLoop(
            bus=MessageBus(), provider=provider, workspace=tmp_path,
            model="test-model", orchestrator_mode=orchestrator,
        )
        return set(loop.tools.tool_names)

    core = loop_tools(False)
    orchestrated = loop_tools(True)

    # "core" = comportamento storico: nessun tool di controllo, tool pesanti
    # presenti (``my`` e le app restano registrati a mano in entrambi i casi).
    assert not {n for n in core if n.startswith("subagent_")}
    assert {"python_exec", "write_file", "apply_patch", "grep"} <= core
    assert {
        "subagent_status", "subagent_cancel", "subagent_restart", "subagent_send",
    } <= orchestrated
    # ``grep`` e l'unica eccezione, e non e un ripensamento sul principio: in
    # modalita orchestratore esiste solo come indice (percorsi, mai righe), che
    # e cio che serve per *trovare* senza pagare l'output grosso. Vedi
    # ``test_search_tools.py``.
    assert not (orchestrated & {"python_exec", "write_file", "apply_patch"})
    assert "grep" in orchestrated


def test_loop_tool_scope_follows_the_flag(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    on = AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model",
        orchestrator_mode=True,
    )
    off = AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model",
        orchestrator_mode=False,
    )
    assert on.tool_scope == "orchestrator"
    assert off.tool_scope == "core"


def test_orchestrator_mode_defaults_to_config_default(tmp_path: Path) -> None:
    from jenny.config.schema import AgentDefaults

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model",
    )
    assert loop.orchestrator_mode is AgentDefaults().orchestrator_mode is True


def test_system_prompt_follows_the_mode(tmp_path: Path) -> None:
    """Il prompt non deve descrivere tool che in quello scope non esistono."""
    from jenny.agent.context import ContextBuilder

    orchestrated = ContextBuilder(tmp_path, orchestrator=True).build_system_prompt()
    classic = ContextBuilder(tmp_path, orchestrator=False).build_system_prompt()

    assert "Orchestrator Mode" in orchestrated
    assert "Do not poll" in orchestrated
    assert "Process Execution" not in orchestrated
    assert "## File and Coding Workflows" not in orchestrated

    assert "Orchestrator Mode" not in classic
    assert "Process Execution" in classic
    assert "## File and Coding Workflows" in classic


# -- guardia anti-polling ------------------------------------------------------


class _OwnsTheTestIds:
    """I subagent dei test (``abc``, ``xyz``) sono della sessione del tool.

    ``cancel``/``restart``/``send`` controllano la sessione d'origine:
    i fake la dichiarano con gli stessi due metodi che
    il manager vero espone.
    """

    owned = ("abc", "xyz")

    def list_records(self, session_key: str | None = None) -> list[Any]:
        from types import SimpleNamespace

        return [SimpleNamespace(task_id=tid, lineage_id=tid) for tid in self.owned]


class _FakeManager(_OwnsTheTestIds):
    def __init__(self) -> None:
        self.snapshot: dict[str, Any] = {"running": [], "recent": []}
        self.cancelled: list[str] = []
        self.restarted: list[tuple[str, str | None]] = []
        self.cancel_result = True
        self.restart_error: Exception | None = None

    def status_snapshot(self, session_key: str | None = None) -> dict[str, Any]:
        return self.snapshot

    async def cancel_task(self, task_id: str, *, by: str = "user") -> bool:
        assert by == "jenny", "fermato dal suo strumento: la casa dice «fermata da Jenny»"
        self.cancelled.append(task_id)
        return self.cancel_result

    async def restart(self, target_id: str, *, extra_instructions=None, manual=False) -> str:
        assert manual is False, "l'orchestratore non scavalca il tetto dei tentativi"
        if self.restart_error is not None:
            raise self.restart_error
        self.restarted.append((target_id, extra_instructions))
        return f"Subagent restarted (id: {target_id})"


def _status_tool(manager: _FakeManager, registry: ToolRegistry) -> SubagentStatusTool:
    tool = SubagentStatusTool(manager, registry=registry)
    # Nessun ``message_id``: un messaggio della WebUI non ne porta mai uno. Il
    # turno e quello che l'AgentLoop lega (vedi test_turn_identity.py).
    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", session_key="unified:default",
        turn_id="unified:default:1",
    ))
    return tool


@pytest.mark.asyncio
async def test_second_consecutive_status_call_is_refused() -> None:
    registry = ToolRegistry()
    manager = _FakeManager()
    tool = _status_tool(manager, registry)
    registry.register(tool)

    first = await registry.execute("subagent_status", {})
    assert "Running subagents" in first

    second = await registry.execute("subagent_status", {})
    assert second.startswith("Refused:")
    assert "announced to you" in second


@pytest.mark.asyncio
async def test_status_allowed_again_after_another_tool_ran() -> None:
    registry = ToolRegistry()
    manager = _FakeManager()
    tool = _status_tool(manager, registry)
    registry.register(tool)
    cancel = SubagentCancelTool(manager)
    cancel.set_context(RequestContext(
        channel="websocket", chat_id="default", session_key="unified:default",
        turn_id="unified:default:1",
    ))
    registry.register(cancel)

    assert "Running subagents" in await registry.execute("subagent_status", {})
    await registry.execute("subagent_cancel", {"task_id": "abc"})
    again = await registry.execute("subagent_status", {})
    assert "Running subagents" in again, "un'altra tool call in mezzo riabilita la chiamata"


@pytest.mark.asyncio
async def test_refused_call_does_not_consume_the_guard() -> None:
    """Due rifiuti di fila: il polling non passa a chiamate alterne."""
    registry = ToolRegistry()
    tool = _status_tool(_FakeManager(), registry)
    registry.register(tool)

    await registry.execute("subagent_status", {})
    assert (await registry.execute("subagent_status", {})).startswith("Refused:")
    assert (await registry.execute("subagent_status", {})).startswith("Refused:")


@pytest.mark.asyncio
async def test_guard_is_per_turn() -> None:
    registry = ToolRegistry()
    tool = _status_tool(_FakeManager(), registry)
    registry.register(tool)

    await registry.execute("subagent_status", {})
    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", session_key="unified:default",
        turn_id="unified:default:2",
    ))
    assert "Running subagents" in await registry.execute("subagent_status", {})


@pytest.mark.asyncio
async def test_guard_without_turn_identity_is_permissive_but_loud() -> None:
    """Senza identita di turno la guardia non rifiuta, ma lo urla nei log.

    Rifiutare su un'identita che non delimita nulla negherebbe la prima,
    legittima ``subagent_status`` di un turno nuovo; restare zitti farebbe
    leggere come protezione qualcosa che non c'e (il bug originale).
    """
    registry = ToolRegistry()
    tool = SubagentStatusTool(_FakeManager(), registry=registry)
    tool.set_context(RequestContext(channel="internal", chat_id="direct"))
    registry.register(tool)

    errors: list[str] = []
    sink = logger.add(lambda m: errors.append(str(m)), level="ERROR")
    try:
        await registry.execute("subagent_status", {})
        assert "Running subagents" in await registry.execute("subagent_status", {})
    finally:
        logger.remove(sink)

    assert len(errors) == 1, "una riga per istanza, non una per chiamata"
    assert "no turn identity" in errors[0]
    assert "subagent_status" in errors[0]


# -- rendering / errori --------------------------------------------------------


@pytest.mark.asyncio
async def test_status_renders_running_and_recent() -> None:
    import time

    manager = _FakeManager()
    manager.snapshot = {
        "running": [{
            "task_id": "aaa11111", "lineage_id": "lll11111", "attempt": 2,
            "label": "price research", "agent_type": "researcher", "state": "running",
            "phase": "awaiting_tools", "iteration": 3, "elapsed_s": 42.0,
            "idle_s": 5.0, "last_tool": "web_search",
        }],
        "recent": [{
            "task_id": "bbb22222", "lineage_id": "mmm22222", "attempt": 3,
            "label": "fix parser", "agent_type": "coder", "state": "failed",
            "stop_reason": "tool_error", "result_summary": "boom",
            "ended_at": time.time() - 120, "can_restart": False,
        }],
    }
    tool = _status_tool(manager, ToolRegistry())
    out = await tool.execute()

    assert "[aaa11111] price research" in out
    assert "type=researcher" in out and "last_tool=web_search" in out and "attempt=2" in out
    assert "[bbb22222] fix parser" in out
    assert "stop_reason=tool_error" in out
    assert "restartable=no (attempt cap)" in out
    assert "boom" in out
    assert "Do not call subagent_status again to wait." in out


@pytest.mark.asyncio
async def test_status_detail_by_task_id_and_unknown_id() -> None:
    manager = _FakeManager()
    manager.snapshot = {
        "running": [{
            "task_id": "aaa11111", "lineage_id": "lll11111", "attempt": 1,
            "label": "job", "agent_type": "operator", "state": "running",
            "phase": "initializing", "iteration": 0, "elapsed_s": 1.0,
            "idle_s": 1.0, "last_tool": None,
        }],
        "recent": [],
    }
    tool = _status_tool(manager, ToolRegistry())
    assert "[aaa11111] job" in await tool.execute(task_id="aaa11111")

    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", turn_id="unified:default:9",
    ))
    assert "No subagent found" in await tool.execute(task_id="zzz")


@pytest.mark.asyncio
async def test_cancel_reports_both_outcomes() -> None:
    manager = _FakeManager()
    tool = SubagentCancelTool(manager)
    tool.set_context(RequestContext(channel="websocket", chat_id="default"))

    assert "Cancelled subagent [abc]" in await tool.execute(task_id="abc")
    manager.cancel_result = False
    assert "Nothing to cancel" in await tool.execute(task_id="abc")


@pytest.mark.asyncio
async def test_restart_wraps_manager_and_surfaces_errors_as_text() -> None:
    manager = _FakeManager()
    tool = SubagentRestartTool(manager)
    tool.set_context(RequestContext(channel="websocket", chat_id="default"))

    out = await tool.execute(task_id="abc", extra_instructions="try harder")
    assert "restarted" in out
    assert manager.restarted == [("abc", "try harder")]

    manager.restart_error = SubagentRestartError("attempts already used")
    out = await tool.execute(task_id="abc")
    assert out.startswith("Cannot restart subagent [abc]")
    assert "attempts already used" in out

    manager.restart_error = SubagentConcurrencyLimitError(4, 5, reserved=True)
    out = await tool.execute(task_id="abc")
    assert "concurrency limit reached (4/5 running)" in out
    assert "kept free for short tasks" in out


# -- subagent_send -------------------------------------------------------------


class _FakeSendManager(_OwnsTheTestIds):
    def status_snapshot(self, session_key: str | None = None) -> dict[str, Any]:
        return {"running": [], "recent": []}

    def __init__(self, mode: str = "injected") -> None:
        self.mode = mode
        self.sent: list[tuple[str, str, bool | None]] = []
        self.error: Exception | None = None

    async def send(self, target_id: str, message: str, *, quick=None):
        from jenny.agent.subagent import SubagentSendResult

        if self.error is not None:
            raise self.error
        self.sent.append((target_id, message, quick))
        return SubagentSendResult(self.mode, f"{self.mode} into [{target_id}]")


def _send_tool(manager: Any, *, turn_id: str | None = "unified:default:1") -> SubagentSendTool:
    tool = SubagentSendTool(manager)
    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", turn_id=turn_id,
        session_key="unified:default",
    ))
    return tool


@pytest.mark.asyncio
async def test_send_passes_through_and_reports_the_mode() -> None:
    for mode in ("injected", "resumed", "restarted"):
        manager = _FakeSendManager(mode)
        tool = _send_tool(manager)
        out = await tool.execute(task_id="abc", message="change the title")
        assert out == f"{mode} into [abc]"
        assert manager.sent == [("abc", "change the title", None)]


@pytest.mark.asyncio
async def test_send_forwards_quick_only_when_given() -> None:
    manager = _FakeSendManager()
    tool = _send_tool(manager)
    await tool.execute(task_id="abc", message="a")
    await tool.execute(task_id="abc", message="b", quick=True)
    assert [q for _, _, q in manager.sent] == [None, True]


@pytest.mark.asyncio
async def test_send_surfaces_errors_as_text_never_a_traceback() -> None:
    from jenny.agent.subagent import SubagentSendError

    manager = _FakeSendManager()
    tool = _send_tool(manager)

    manager.error = SubagentSendError("unknown subagent or lineage: abc")
    out = await tool.execute(task_id="abc", message="hi")
    assert out.startswith("Cannot send to subagent [abc]")
    assert "unknown subagent" in out

    manager.error = SubagentRestartError("attempts already used")
    out = await tool.execute(task_id="abc", message="hi")
    assert "no resumable" in out and "attempts already used" in out

    manager.error = SubagentConcurrencyLimitError(4, 5, reserved=True)
    out = await tool.execute(task_id="abc", message="hi")
    assert "4/5 running" in out and "quick" in out


@pytest.mark.asyncio
async def test_identical_send_is_refused_within_the_same_turn() -> None:
    manager = _FakeSendManager()
    tool = _send_tool(manager)

    assert not (await tool.execute(task_id="abc", message="same")).startswith("Refused:")
    refusal = await tool.execute(task_id="abc", message="same")
    assert refusal.startswith("Refused:")
    assert len(manager.sent) == 1

    # Un messaggio diverso, o un altro subagent, passano.
    assert not (await tool.execute(task_id="abc", message="other")).startswith("Refused:")
    assert not (await tool.execute(task_id="xyz", message="same")).startswith("Refused:")

    # E il turno successivo riparte pulito (e non accumula stato).
    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", turn_id="unified:default:2",
        session_key="unified:default",
    ))
    assert not (await tool.execute(task_id="abc", message="same")).startswith("Refused:")
    assert len(tool._sent) == 1


@pytest.mark.asyncio
async def test_duplicate_guard_without_turn_identity_is_permissive_but_loud() -> None:
    manager = _FakeSendManager()
    tool = _send_tool(manager, turn_id=None)

    errors: list[str] = []
    sink = logger.add(lambda m: errors.append(str(m)), level="ERROR")
    try:
        await tool.execute(task_id="abc", message="same")
        assert not (await tool.execute(task_id="abc", message="same")).startswith("Refused:")
    finally:
        logger.remove(sink)

    assert len(errors) == 1 and "subagent_send" in errors[0]
    # Il set non cresce senza un confine di turno in cui svuotarsi.
    assert tool._sent == set()


def test_send_is_orchestrator_only() -> None:
    """Un subagent che potesse guidare i fratelli romperebbe la non-ricorsione."""
    assert SubagentSendTool._scopes == {"orchestrator"}


@pytest.mark.asyncio
async def test_a_refused_send_does_not_arm_the_duplicate_guard() -> None:
    """Riprovare dopo un rifiuto e legittimo; ripetere un invio riuscito no."""
    manager = _FakeSendManager()
    tool = _send_tool(manager)

    manager.error = SubagentConcurrencyLimitError(4, 5)
    assert "concurrency" in await tool.execute(task_id="abc", message="same")

    manager.error = None
    out = await tool.execute(task_id="abc", message="same")
    assert not out.startswith("Refused:")
    assert manager.sent == [("abc", "same", None)]


def test_orchestrator_prompt_teaches_send_vs_spawn() -> None:
    from jenny.utils.prompt_templates import render_template

    prompt = render_template("agent/orchestrator.md", strip=True)
    assert "subagent_send" in prompt
    assert "Follow-ups" in prompt


# -- la sessione d'origine ------------------------------


class _TwoSessionsManager:
    """Un subagent vivo della chat personale e uno finito di un quaderno.

    ``status_snapshot``/``list_records`` filtrano per sessione come quelli veri;
    le azioni registrano chi le ha raggiunte.
    """

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.running = {"unified:default": [{"task_id": "live1", "lineage_id": "lin1"}]}
        self.records = {"project:p": [SimpleNamespace(task_id="done2", lineage_id="lin2")]}
        self.calls: list[tuple[str, str]] = []

    def status_snapshot(self, session_key: str | None = None) -> dict[str, Any]:
        return {"running": self.running.get(session_key, []), "recent": []}

    def list_records(self, session_key: str | None = None) -> list[Any]:
        return self.records.get(session_key, [])

    async def cancel_task(self, task_id: str, *, by: str = "user") -> bool:
        self.calls.append(("cancel", task_id))
        return True

    async def restart(self, target_id: str, *, extra_instructions=None, manual=False) -> str:
        self.calls.append(("restart", target_id))
        return f"Subagent restarted (id: {target_id})"

    async def send(self, target_id: str, message: str, *, quick=None):
        from jenny.agent.subagent import SubagentSendResult

        self.calls.append(("send", target_id))
        return SubagentSendResult("injected", f"injected into [{target_id}]")


def _in(tool: Any, session_key: str) -> Any:
    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", session_key=session_key,
        turn_id=f"{session_key}:1",
    ))
    return tool


@pytest.mark.asyncio
async def test_control_tools_only_reach_the_subagents_of_their_own_session() -> None:
    manager = _TwoSessionsManager()
    cancel = _in(SubagentCancelTool(manager), "project:p")
    restart = _in(SubagentRestartTool(manager), "project:p")
    send = _in(SubagentSendTool(manager), "project:p")

    # Dal quaderno, il subagent vivo della chat personale non si tocca…
    assert "Nothing to cancel" in await cancel.execute(task_id="live1")
    assert "Cannot restart" in await restart.execute(task_id="lin1")
    assert "Cannot send" in await send.execute(task_id="live1", message="fermati")
    assert manager.calls == []

    # …il proprio sì, per task id o per lineage.
    assert "restarted" in await restart.execute(task_id="done2")
    assert "injected" in await send.execute(task_id="lin2", message="continua")
    assert manager.calls == [("restart", "done2"), ("send", "lin2")]

    # E dalla chat personale vale il contrario.
    manager.calls.clear()
    cancel = _in(SubagentCancelTool(manager), "unified:default")
    assert "Cancelled subagent [live1]" in await cancel.execute(task_id="live1")
    assert "Nothing to cancel" in await cancel.execute(task_id="done2")
    assert manager.calls == [("cancel", "live1")]
