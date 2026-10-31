"""Il loop dice al manager quando Jenny ha avuto il resoconto di un aiutante.

La scheda «si e fermato» della casa restava anche dopo che Jenny aveva gia
ripreso il lavoro (prova del 24/10/2026): niente diceva che il resoconto le era
arrivato. ``SubagentManager.mark_handled`` lo scrive nel record; qui si
verificano i tre punti del loop che lo chiamano — fine del turno d'annuncio,
annuncio doppio gia risposto, resoconto consumato a meta turno — e la regola di
chat di un aiutante fatto continuare.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.turn_types import TurnDisposition
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse, ToolCallRequest
from tests.support.agent import make_loop, make_provider

RESULT = "[Subagent 'plant-cards' completed successfully]\n\nResult:\nSix pages written."
KEY = "unified:default"


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Test title"))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    return loop


def _spy(loop: AgentLoop) -> list[str]:
    marked: list[str] = []
    loop.subagents.mark_handled = marked.append  # type: ignore[method-assign]
    return marked


def _fake_turn(loop: AgentLoop, reply: str = "Done: six cards.", tools: list[str] | None = None):
    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return (reply, list(tools or []),
                [*initial_messages, {"role": "assistant", "content": reply}], "stop", False)

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]


def _announce(key: str, task_id: str = "sub-1", **meta) -> InboundMessage:
    return InboundMessage(
        channel="system", sender_id="subagent", chat_id=key, content=RESULT,
        metadata={"subagent_task_id": task_id, **meta},
    )


async def test_the_announce_turn_marks_its_result_handled(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    marked = _spy(loop)
    _fake_turn(loop)

    await loop._process_message(_announce("internal:plants"))

    assert marked == ["sub-1"]


async def test_a_failed_announce_turn_does_not_mark(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    marked = _spy(loop)

    async def boom(initial_messages, **_kwargs):
        raise RuntimeError("provider down")

    loop._run_agent_loop = boom  # type: ignore[method-assign]
    try:
        await loop._process_message(_announce("internal:plants"))
    except RuntimeError:
        pass

    assert marked == []


async def test_an_announce_already_answered_marks_too(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    marked = _spy(loop)
    _fake_turn(loop)

    await loop._process_message(_announce("internal:twice"))
    await loop._process_message(_announce("internal:twice"))

    assert marked == ["sub-1", "sub-1"]


@pytest.mark.parametrize("meta", [
    {"subagent_status": "budget"},
    # Fermato da un provider che non rispondeva: sul telefono, il 25/10/2026, in
    # chat era arrivato «si è arenato su un errore di rete, l'ho ripreso».
    {"subagent_status": "error", "subagent_interrupted": True},
])
async def test_a_continued_helper_does_not_speak_in_chat(
    tmp_path: Path, meta: dict[str, object],
) -> None:
    """Jenny fa continuare l'aiutante con ``subagent_send``: come per un rilancio,
    in chat parla solo il lavoro ripreso (``subagent_announce.md``)."""
    loop = _loop(tmp_path)

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        # Il silenzio vuole un invio riuscito proprio a questo aiutante.
        sent = [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "subagent_send",
                             "arguments": '{"task_id": "sub-1", "message": "go on"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "name": "subagent_send",
             "content": "Resumed subagent [plant-cards] from its saved conversation."},
            {"role": "assistant", "content": ""},
        ]
        return ("", ["subagent_send"], [*initial_messages, *sent], "stop", False)

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    outcome = await loop._process_message(_announce("websocket:default", **meta))

    assert outcome.disposition is TurnDisposition.SILENT
    assert outcome.message is None


@pytest.mark.parametrize("meta", [
    {"subagent_status": "budget"},
    {"subagent_status": "error", "subagent_interrupted": True},
])
async def test_a_stopped_helper_answered_without_continuing_still_speaks(
    tmp_path: Path, meta: dict[str, object],
) -> None:
    loop = _loop(tmp_path)
    _fake_turn(loop, reply="It stopped halfway; here is what it found.")

    outcome = await loop._process_message(_announce("websocket:default", **meta))

    assert outcome.disposition is TurnDisposition.DELIVERED


async def test_a_result_consumed_mid_turn_is_marked_after_the_turn(tmp_path: Path) -> None:
    calls = {"n": 0}
    marked_during: list[list[str]] = []

    async def chat(**_kw):
        calls["n"] += 1
        marked_during.append(list(marked))
        if calls["n"] == 1:
            queue.put_nowait(InboundMessage(
                channel="websocket", sender_id="subagent", chat_id="default",
                content="risultato dell'aiutante",
                metadata={"injected_event": "subagent_result", "subagent_task_id": "mid-1"},
            ))
            return LLMResponse(
                content="", finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="c1", name="list_dir", arguments={"path": "."})],
            )
        return LLMResponse(content="ecco cosa ha trovato")

    provider = make_provider()
    provider.chat_with_retry = chat
    provider.chat_stream_with_retry = chat
    loop = make_loop(tmp_path, provider=provider)
    marked = _spy(loop)
    loop.subagents.get_running_ids_by_session = lambda key: frozenset()
    loop.subagents.get_running_count_by_session = lambda key: 0
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    loop._pending_queues[KEY] = queue

    await asyncio.wait_for(loop._dispatch(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="lancia",
    ), queue), timeout=5.0)

    assert calls["n"] == 2
    assert marked_during == [[], []], "not marked while the turn is still working"
    assert marked == ["mid-1"]
