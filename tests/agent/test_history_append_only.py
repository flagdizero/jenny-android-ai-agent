"""Il turno dopo rimanda, byte per byte, quello che il turno prima ha mandato.

La cache dei provider (Anthropic con i breakpoint, DeepSeek e OpenAI col
prefisso automatico) vale solo su un prefisso identico. Misurato sul banco A/B
dell'11/10/2026: il turno che segue un turno con dei tool ripartiva dal
messaggio utente del turno prima, e ripagava per intero la catena dei tool
(9,8k token su DeepSeek, 14k su Haiku 5.5). Le cause erano due: il Runtime
Context tolto al salvataggio e il ``[Message Time: …]`` aggiunto solo in
replay. Qui la proprietà si misura su turni veri, con un provider finto che
registra le richieste.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.base import Tool
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.session.keys import UNIFIED_SESSION_KEY

# Le chiavi che un provider mette sul filo: il resto dei dict (metadati di
# riga) non arriva all'API, quindi non conta per la cache.
_WIRE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content")


class _Probe(Tool):
    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "result of the probe"


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.supports_progress_deltas = False
    provider.generation.max_tokens = 4096
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path,
                     model="test-model")
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.tools.register(_Probe())
    return loop


def _script(loop: AgentLoop, responses: list[LLMResponse]) -> list[list[dict[str, Any]]]:
    seen: list[list[dict[str, Any]]] = []
    it = iter(responses)

    def _next(*args: Any, **kwargs: Any) -> LLMResponse:
        seen.append([dict(m) for m in kwargs.get("messages") or []])
        return next(it)

    loop.provider.chat_with_retry = AsyncMock(side_effect=_next)
    loop.provider.chat_stream_with_retry = AsyncMock(side_effect=_next)
    return seen


def _wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: m[k] for k in _WIRE_KEYS if k in m} for m in messages]


def _tool_turn(n: int) -> list[LLMResponse]:
    calls = [
        LLMResponse(content="", tool_calls=[ToolCallRequest(id=f"call_{n}_{i}", name="probe",
                                                            arguments={})])
        for i in range(3)
    ]
    return [*calls, LLMResponse(content=f"done {n}", tool_calls=[])]


def _user(text: str) -> InboundMessage:
    return InboundMessage(channel="websocket", sender_id="u1", chat_id="default", content=text,
                          metadata={"webui": True})


def _assert_prefix(before: list[dict[str, Any]], final_reply: str,
                   after: list[dict[str, Any]], *, reasoning: str | None = None) -> None:
    sent, resent = _wire(before), _wire(after)
    # Il system prompt e' fuori discussione qui: lo fissano altri test.
    assert resent[1:len(sent)] == sent[1:], _first_difference(sent, resent)
    reply = {"role": "assistant", "content": final_reply}
    if reasoning is not None:
        reply["reasoning_content"] = reasoning
    assert resent[len(sent)] == reply


def _first_difference(sent: list[dict[str, Any]], resent: list[dict[str, Any]]) -> str:
    for i, (a, b) in enumerate(zip(sent, resent)):
        if i and a != b:
            return f"message {i} differs:\nsent   {a!r}\nresent {b!r}"
    return "lengths differ"


async def test_the_turn_after_a_tool_turn_resends_it_unchanged(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    seen = _script(loop, [*_tool_turn(1), LLMResponse(content="ok", tool_calls=[])])

    await loop._dispatch(_user("read the probe three times"))
    last_of_first = seen[-1]
    await loop._dispatch(_user("thanks"))

    _assert_prefix(last_of_first, "done 1", seen[-1])


async def test_three_turns_in_a_row_keep_the_prefix(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    seen = _script(loop, [*_tool_turn(1), *_tool_turn(2), LLMResponse(content="ok",
                                                                      tool_calls=[])])

    await loop._dispatch(_user("first"))
    first = seen[-1]
    await loop._dispatch(_user("second"))
    second_start, second_end = seen[-4], seen[-1]
    await loop._dispatch(_user("third"))

    _assert_prefix(first, "done 1", second_start)
    _assert_prefix(second_end, "done 2", seen[-1])


async def test_the_clean_content_stays_clean_for_human_readers(tmp_path: Path) -> None:
    """``content`` resta quello che l'utente ha scritto: lo leggono la WebUI,
    ``/history``, il consolidatore. Il testo inviato sta a parte."""
    loop = _loop(tmp_path)
    _script(loop, [*_tool_turn(1)])

    await loop._dispatch(_user("read the probe three times"))

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    [row] = [m for m in session.messages if m.get("role") == "user"]
    assert row["content"] == "read the probe three times"
    plain = session.get_history(max_messages=0, include_timestamps=True)
    assert "[Runtime Context" not in plain[0]["content"]


async def test_a_subagent_announce_is_resent_as_it_was_sent(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    seen = _script(loop, [LLMResponse(content="noted", tool_calls=[]),
                          LLMResponse(content="ok", tool_calls=[])])

    await loop._dispatch(InboundMessage(
        channel="system", sender_id="subagent", chat_id="websocket:default",
        content="[Subagent 'x' completed successfully]\n\nResult: 42",
        metadata={"origin_message_id": "spawn-1", "subagent_task_id": "t1"},
        session_key_override=UNIFIED_SESSION_KEY,
    ))
    announce = seen[-1]
    await loop._dispatch(_user("thanks"))

    _assert_prefix(announce, "noted", seen[-1])


async def test_an_active_goal_is_stated_in_full_once(tmp_path: Path) -> None:
    """Il blocco del goal resta in storia con il messaggio: ripeterlo pieno a
    ogni turno gonfierebbe la cronologia di quattro kB a turno."""
    loop = _loop(tmp_path)
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    objective = "Plan the trip to Lisbon, step by step, checking every booking. " * 10
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": objective,
                                        "started_at": datetime.now().isoformat()}
    loop.sessions.save(session)
    seen = _script(loop, [LLMResponse(content="a", tool_calls=[]),
                          LLMResponse(content="b", tool_calls=[]),
                          LLMResponse(content="c", tool_calls=[])])

    for text in ("one", "two", "three"):
        await loop._dispatch(_user(text))

    last = seen[-1]
    full = sum(str(m.get("content")).count(objective.strip()) for m in last)
    assert full == 1, f"the objective appears {full} times"
    assert "unchanged" in last[-1]["content"]
    _assert_prefix(seen[-2], "b", last)


def test_a_mid_turn_message_is_resent_with_its_label() -> None:
    """L'etichetta la mette il governatore a ogni richiesta del turno: il turno
    dopo la deve ritrovare, mentre ``content`` resta come l'utente l'ha scritto."""
    from jenny.agent.context_governor import MID_TURN_LABEL
    from jenny.config.schema import AgentDefaults
    from jenny.session.history_meta import MID_TURN_META
    from jenny.session.manager import Session

    loop = AgentLoop.__new__(AgentLoop)
    loop.max_tool_result_chars = AgentDefaults().max_tool_result_chars
    session = Session(key="unified:default")
    session.add_message("user", "start")
    loop._save_turn(session, [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "probe", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "probe", "content": "r"},
        {"role": "user", "content": "also this", MID_TURN_META: True},
        {"role": "assistant", "content": "ok"},
    ], 0)

    row = session.messages[3]
    assert row["content"] == "also this"
    [replayed] = [m for m in session.get_history(max_messages=0, include_timestamps=True,
                                                  replay_sent=True)
                  if m["role"] == "user" and "also this" in m["content"]]
    assert replayed["content"] == f"{MID_TURN_LABEL}\nalso this"


def test_a_lengthened_objective_is_stated_again() -> None:
    from jenny.agent.context import GOAL_UNCHANGED_LINE, _goal_lines_once

    history = [{"role": "user", "content": "x\n[Runtime Context]\nGoal (active):\nPlan the trip\n"
                                           "[/Runtime Context]"}]
    assert _goal_lines_once(["Goal (active):", "Plan the trip"], history) == [
        GOAL_UNCHANGED_LINE]
    assert _goal_lines_once(["Goal (active):", "Plan the trip to Lisbon"], history) == [
        "Goal (active):", "Plan the trip to Lisbon"]


def _deepseek_loop(tmp_path: Path) -> AgentLoop:
    loop = _loop(tmp_path)
    loop.provider.closed_reasoning_can_be_blank = lambda model: True
    return loop


def _reasoned_tool_turn(n: int, reasoning: str = "thinking it over") -> list[LLMResponse]:
    calls = [
        LLMResponse(content="", reasoning_content=f"{reasoning} {n}.{i}",
                    tool_calls=[ToolCallRequest(id=f"call_{n}_{i}", name="probe", arguments={})])
        for i in range(3)
    ]
    return [*calls, LLMResponse(content=f"done {n}", reasoning_content=f"final {n}",
                                tool_calls=[])]


async def test_deepseek_keeps_closed_reasoning_while_the_cache_holds(tmp_path: Path) -> None:
    """Svuotare il ragionamento del turno appena chiuso rompeva il prefisso alla
    sua prima assistant con tool: finche' la cache tiene, si rimanda com'era."""
    loop = _deepseek_loop(tmp_path)
    seen = _script(loop, [*_reasoned_tool_turn(1), *_reasoned_tool_turn(2),
                          LLMResponse(content="ok", tool_calls=[])])

    await loop._dispatch(_user("first"))
    first = seen[-1]
    await loop._dispatch(_user("second"))
    second_start, second_end = seen[-4], seen[-1]
    await loop._dispatch(_user("third"))

    _assert_prefix(first, "done 1", second_start, reasoning="final 1")
    _assert_prefix(second_end, "done 2", seen[-1], reasoning="final 2")
    assert any(m.get("reasoning_content") == "thinking it over 1.0" for m in seen[-1])


async def test_deepseek_drops_closed_reasoning_when_the_cache_is_lost(tmp_path: Path) -> None:
    loop = _deepseek_loop(tmp_path)
    seen = _script(loop, [*_reasoned_tool_turn(1), LLMResponse(content="a", tool_calls=[]),
                          LLMResponse(content="b", tool_calls=[])])

    await loop._dispatch(_user("first"))
    await loop._dispatch(_user("second"))
    assert any(m.get("reasoning_content") == "thinking it over 1.0" for m in seen[-1])

    # Un consolidamento, Dream, la finestra che scorre: il prefisso cambia.
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.metadata["_closed_reasoning_fingerprint"] = "something else"
    loop.sessions.save(session)
    await loop._dispatch(_user("third"))
    before_change = seen[-1]
    assert not any(m.get("reasoning_content") for m in before_change[:-1]
                   if m.get("role") == "assistant")


async def test_deepseek_drops_closed_reasoning_past_the_cap(tmp_path: Path) -> None:
    from jenny.agent.context_governor import CLOSED_REASONING_KEEP_CHARS

    loop = _deepseek_loop(tmp_path)
    long = "x" * (CLOSED_REASONING_KEEP_CHARS // 2)
    seen = _script(loop, [*_reasoned_tool_turn(1, long), *_reasoned_tool_turn(2, long),
                          LLMResponse(content="ok", tool_calls=[])])

    await loop._dispatch(_user("first"))
    await loop._dispatch(_user("second"))
    await loop._dispatch(_user("third"))

    sent = sum(len(str(m.get("reasoning_content") or "")) for m in seen[-1])
    assert sent <= CLOSED_REASONING_KEEP_CHARS
