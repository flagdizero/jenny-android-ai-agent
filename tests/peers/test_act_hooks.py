"""Dove il cancello delle richieste di fare si aggancia al resto di Jenny.

Il runner chiama il cancello prima di ogni strumento (e prima del suo tetto di
tempo); la politica di rete di ``python_exec`` lo legge anche dai fili del
codice; un promemoria nato da una richiesta suona nella chat personale; la
lista degli strumenti di una richiesta non ha ``install_update``.
"""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from jenny.agent.runner import AgentRunner, AgentRunSpec
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.peers import gate
from jenny.providers.base import ToolCallRequest
from jenny.security.subagent_policy import egress_refusal, socket_egress_refusal
from jenny.session.keys import UNIFIED_SESSION_KEY

ACT = {"peer": "a" * 32, "qid": "b" * 32}


@pytest.fixture(autouse=True)
def _no_authority():
    yield
    gate.install(None)


class _Authority:
    def __init__(self, level: str, answer: bool = False) -> None:
        self._level = level
        self.answer = answer
        self.asked = 0

    def level(self, peer_id: str) -> str:
        return self._level

    def name(self, peer_id: str) -> str:
        return "Personale"

    async def confirm(self, peer_id: str, qid: str, action: str, target: str) -> bool:
        self.asked += 1
        await asyncio.sleep(0.05)
        return self.answer

    def note(self, peer_id: str, qid: str, item: dict[str, Any]) -> None:
        pass


@tool_parameters(tool_parameters_schema(url=StringSchema("u"), required=["url"]))
class _Fetch(Tool):
    def __init__(self) -> None:
        self.ran = 0

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "fetch"

    async def execute(self, **kwargs: Any) -> str:
        self.ran += 1
        return "page"


async def _run_fetch(tool: _Fetch):
    registry = ToolRegistry()
    registry.register(tool)
    spec = AgentRunSpec(initial_messages=[], tools=registry, model="m", max_iterations=2,
                        max_tool_result_chars=8000)
    return await AgentRunner(provider=None)._run_tool(
        spec, ToolCallRequest(id="c1", name="web_fetch", arguments={"url": "https://x.example"}),
        external_lookup_counts={}, workspace_violation_counts={},
    )


async def test_the_runner_asks_before_the_tool_and_a_no_never_runs_it():
    auth = _Authority("confirm", answer=False)
    gate.install(auth)
    tool = _Fetch()
    with gate.entered(ACT):
        payload, event, exc = await _run_fetch(tool)
    assert auth.asked == 1
    assert tool.ran == 0
    assert payload.startswith("Refused")
    assert event["detail"] == "refused: peer request"
    assert exc is None


async def test_a_yes_runs_the_tool_and_outside_a_request_nothing_is_asked():
    auth = _Authority("confirm", answer=True)
    gate.install(auth)
    tool = _Fetch()
    with gate.entered(ACT):
        payload, _event, _exc = await _run_fetch(tool)
    assert payload == "page"
    await _run_fetch(tool)
    assert auth.asked == 1 and tool.ran == 2


def test_code_network_is_closed_even_from_a_worker_thread():
    gate.install(_Authority("confirm"))
    with gate.entered(ACT):
        ctx = contextvars.copy_context()
    with ThreadPoolExecutor(1) as pool:
        refused = pool.submit(ctx.run, egress_refusal, "https://x.example").result()
        raw = pool.submit(ctx.run, socket_egress_refusal).result()
    assert refused and "web_fetch" in refused
    assert raw and "web_fetch" in raw
    # Con ``free`` la rete dal codice passa.
    gate.install(_Authority("free"))
    with gate.entered(ACT):
        assert egress_refusal("https://x.example") is None


class _Cron:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []

    def add_job(self, **kwargs: Any):
        self.added.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(id="j1", name=kwargs.get("name") or "x", schedule=kwargs["schedule"],
                               state=SimpleNamespace(next_run_at_ms=None), payload=None)

    def list_jobs(self, **_kwargs: Any):
        return []


async def test_a_reminder_asked_by_another_jenny_rings_in_the_personal_chat():
    service = _Cron()
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(RequestContext(
        channel="websocket", chat_id="default", session_key=f"peer:{'a' * 32}:act",
        metadata={gate.PEER_ACT_META: dict(ACT), "_taint": ["peer:Personale"]},
    ))
    await tool.execute(action="add", message="richiamare Bianchi", date="2027-01-04",
                       time="09:00")
    assert service.added, "the job was not created"
    job = service.added[0]
    assert job["session_key"] == UNIFIED_SESSION_KEY
    assert gate.act_of(job["origin_metadata"]) == ACT


def test_the_request_tool_list_drops_install_update_only():
    from jenny.agent.loop import AgentLoop
    from jenny.bus.events import InboundMessage
    from jenny.session.manager import Session

    loop = AgentLoop.__new__(AgentLoop)
    loop.tools = ToolRegistry()
    for name in ("install_update", "spawn", "message", "cron", "peer_ask"):
        loop.tools.register(_named(name))
    loop.peer_questions = None
    loop.plan_driver = None
    loop._plan_tool_filter = lambda _msg, _key: (lambda _name: None)  # type: ignore[method-assign]
    key = f"peer:{'a' * 32}:act"
    msg = InboundMessage(channel="websocket", sender_id="peer", chat_id="default", content="x")
    view = loop._turn_tool_view(msg, Session(key=key), key, silent=True)
    assert view is not None
    assert set(view.tool_names) == {"spawn", "message", "cron"}


def _named(name: str) -> Tool:
    class _T(Tool):
        @property
        def name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return name

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: Any) -> str:
            return "ok"

    return _T()
