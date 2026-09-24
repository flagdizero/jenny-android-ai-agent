from __future__ import annotations

import asyncio

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.message import MessageTool
from jenny.agent.tools.spawn import SpawnTool
from jenny.cron.service import CronService
from jenny.session.keys import UNIFIED_SESSION_KEY


async def _race(tool, first: RequestContext, second: RequestContext, run_first, run_second):
    """Due task sullo stesso tool, ognuno col suo contesto, intrecciati apposta:
    il primo imposta il suo e si ferma, il secondo imposta l'altro, poi il primo
    esegue. Se il contesto non fosse locale al task, il primo userebbe quello
    del secondo."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def task_one() -> str:
        tool.set_context(first)
        entered.set()
        await release.wait()
        return await run_first()

    async def task_two() -> str:
        await entered.wait()
        tool.set_context(second)
        release.set()
        return await run_second()

    return await asyncio.gather(task_one(), task_two())


@pytest.mark.asyncio
async def test_message_tool_keeps_task_local_context() -> None:
    seen: list[tuple[str, str, str]] = []

    async def send_callback(msg):
        seen.append((msg.channel, msg.chat_id, msg.content))
        return None

    tool = MessageTool(send_callback=send_callback)

    result_one, result_two = await _race(
        tool,
        RequestContext(channel="test-channel", chat_id="chat-a"),
        RequestContext(channel="other-channel", chat_id="chat-b"),
        lambda: tool.execute(content="one"),
        lambda: tool.execute(content="two"),
    )

    assert result_one == "Message sent to test-channel:chat-a"
    assert result_two == "Message sent to other-channel:chat-b"
    assert ("test-channel", "chat-a", "one") in seen
    assert ("other-channel", "chat-b", "two") in seen


@pytest.mark.asyncio
async def test_spawn_tool_keeps_task_local_context() -> None:
    seen: list[tuple[str, str, str]] = []

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(
            self,
            *,
            task: str,
            label: str | None,
            origin_channel: str,
            origin_chat_id: str,
            session_key: str,
            origin_message_id: str | None = None,
            temperature: float | None = None,
            workspace_scope=None,
            agent_type: str = "operator",
            quick: bool = False,
        ) -> str:
            seen.append((origin_channel, origin_chat_id, session_key))
            return f"{origin_channel}:{origin_chat_id}:{task}"

    tool = SpawnTool(_Manager())

    result_one, result_two = await _race(
        tool,
        RequestContext(channel="test-channel", chat_id="chat-a"),
        RequestContext(channel="other-channel", chat_id="chat-b"),
        lambda: tool.execute(task="one"),
        lambda: tool.execute(task="two"),
    )

    assert result_one == "test-channel:chat-a:one"
    assert result_two == "other-channel:chat-b:two"
    assert ("test-channel", "chat-a", "test-channel:chat-a") in seen
    assert ("other-channel", "chat-b", "other-channel:chat-b") in seen


@pytest.mark.asyncio
async def test_cron_tool_keeps_task_local_context(tmp_path) -> None:
    tool = CronTool(CronService(tmp_path / "jobs.json"))

    result_one, result_two = await _race(
        tool,
        RequestContext(channel="test-channel", chat_id="chat-a", session_key="test-channel:chat-a"),
        RequestContext(
            channel="other-channel", chat_id="chat-b", session_key="other-channel:chat-b"
        ),
        lambda: tool.execute(action="add", message="first", every_seconds=60),
        lambda: tool.execute(action="add", message="second", every_seconds=60),
    )

    assert result_one.startswith("Created job")
    assert result_two.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert {job.payload.session_key for job in jobs} == {
        "test-channel:chat-a",
        "other-channel:chat-b",
    }
    assert {(job.payload.origin_channel, job.payload.origin_chat_id) for job in jobs} == {
        ("test-channel", "chat-a"),
        ("other-channel", "chat-b"),
    }


# --- Basic single-task regression tests ---


@pytest.mark.asyncio
async def test_message_tool_basic_set_context_and_execute() -> None:
    """Single task: set_context then execute should route correctly."""
    seen: list[tuple[str, str, str]] = []

    async def send_callback(msg):
        seen.append((msg.channel, msg.chat_id, msg.content))

    tool = MessageTool(send_callback=send_callback)
    tool.set_context(RequestContext(channel="websocket", chat_id="chat-123", message_id="msg-456"))

    result = await tool.execute(content="hello")
    assert result == "Message sent to websocket:chat-123"
    assert seen == [("websocket", "chat-123", "hello")]


@pytest.mark.asyncio
async def test_message_tool_default_values_without_set_context() -> None:
    """Without set_context, constructor defaults should be used."""
    seen: list[tuple[str, str, str]] = []

    async def send_callback(msg):
        seen.append((msg.channel, msg.chat_id, msg.content))

    tool = MessageTool(
        send_callback=send_callback,
        default_channel="websocket",
        default_chat_id="general",
    )

    result = await tool.execute(content="hi")
    assert result == "Message sent to websocket:general"
    assert seen == [("websocket", "general", "hi")]


@pytest.mark.asyncio
async def test_spawn_tool_basic_set_context_and_execute() -> None:
    """Single task: set_context then execute should pass correct origin."""
    seen: list[tuple[str, str, str]] = []

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(
            self,
            *,
            task,
            label,
            origin_channel,
            origin_chat_id,
            session_key,
            origin_message_id=None,
            temperature=None,
            workspace_scope=None,
            agent_type="operator",
            quick=False,
        ):
            seen.append((origin_channel, origin_chat_id, session_key))
            return f"ok: {task}"

    tool = SpawnTool(_Manager())
    tool.set_context(RequestContext(channel="websocket", chat_id="chat-abc"))

    result = await tool.execute(task="do something")
    assert result == "ok: do something"
    assert seen == [("websocket", "chat-abc", "websocket:chat-abc")]


@pytest.mark.asyncio
async def test_spawn_tool_default_values_without_set_context() -> None:
    """Without set_context, default internal:direct should be used."""
    seen: list[tuple[str, str, str]] = []

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(
            self,
            *,
            task,
            label,
            origin_channel,
            origin_chat_id,
            session_key,
            origin_message_id=None,
            temperature=None,
            workspace_scope=None,
            agent_type="operator",
            quick=False,
        ):
            seen.append((origin_channel, origin_chat_id, session_key))
            return "ok"

    tool = SpawnTool(_Manager())

    await tool.execute(task="test")
    assert seen == [("internal", "direct", "internal:direct")]


@pytest.mark.asyncio
async def test_cron_tool_basic_set_context_and_execute(tmp_path) -> None:
    """Single task: set_context then add job should use correct target."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    tool.set_context(
        RequestContext(channel="websocket", chat_id="user-789", session_key="websocket:user-789")
    )

    result = await tool.execute(action="add", message="standup", every_seconds=300)
    assert result.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].payload.session_key == "websocket:user-789"
    assert jobs[0].payload.origin_channel == "websocket"
    assert jobs[0].payload.origin_chat_id == "user-789"


@pytest.mark.asyncio
async def test_webui_cron_job_is_attached_to_the_chat_but_runs_in_the_conversation(
    tmp_path,
) -> None:
    """L'attaccamento alla chat è ``origin_*``; la sessione è la conversazione.

    Prima questo test si chiamava *uses_origin_session* e pretendeva
    ``sessionKey == "websocket:chat-123"``: il tool, su un turno della
    conversazione unica, si fabbricava la chiave dal canale. Ma quel valore non
    è un'etichetta — ``bound_runner`` lo usa come chiave del turno — quindi il
    promemoria girava in un file di sessione tutto suo, con il suo
    consolidamento, accanto alla conversazione a cui appartiene.

    Le due cose restano entrambe, separate: la consegna sa a quale chat tornare
    (``origin_channel`` / ``origin_chat_id``, e i metadata con cui è nato), la
    sessione è quella in cui l'utente sta parlando.
    """
    tool = CronTool(CronService(tmp_path / "jobs.json"))

    class _Tools:
        tool_names = ["cron"]

        def get(self, name: str):
            return tool if name == "cron" else None

    loop = object.__new__(AgentLoop)
    loop.tools = _Tools()
    loop._set_tool_context(
        "websocket",
        "chat-123",
        metadata={"webui": True},
        session_key=UNIFIED_SESSION_KEY,
    )

    result = await tool.execute(action="add", message="standup", every_seconds=300)
    assert result.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].payload.session_key == UNIFIED_SESSION_KEY
    assert jobs[0].payload.origin_channel == "websocket"
    assert jobs[0].payload.origin_chat_id == "chat-123"
    assert jobs[0].payload.origin_metadata == {"webui": True}


@pytest.mark.asyncio
async def test_cron_tool_preserves_thread_scoped_session_key(tmp_path) -> None:
    """Channel-provided thread session keys should remain the cron owner."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="C123",
            metadata={"thread": {"thread_ts": "1700.42"}},
            session_key="websocket:C123:1700.42",
        )
    )

    result = await tool.execute(action="add", message="check thread", every_seconds=300)
    assert result.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].payload.session_key == "websocket:C123:1700.42"
    assert jobs[0].payload.origin_channel == "websocket"
    assert jobs[0].payload.origin_chat_id == "C123"
    assert jobs[0].payload.origin_metadata == {"thread": {"thread_ts": "1700.42"}}


@pytest.mark.asyncio
async def test_cron_tool_no_context_returns_error(tmp_path) -> None:
    """Without set_context, add should fail with a clear error."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))

    result = await tool.execute(action="add", message="test", every_seconds=60)
    assert result == "Error: scheduled cron jobs must be created from a chat session"
