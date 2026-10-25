"""Il turno d'annuncio di un subagent: il risultato deve arrivare al modello.

Il 02/10/2026 un subagent finito a turno chiuso ha aperto il proprio turno, e il
risultato e' entrato nel prompt come ``assistant`` in coda: i provider lo tolgono
(prefill non supportato, ``enforce_role_alternation``), quindi il modello ha
risposto di nuovo «ancora in corso» con il dato vecchio del tool, e la sintesi
non e' mai arrivata. Qui la catena si percorre fino alla richiesta vera.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse
from jenny.providers.message_repair import enforce_role_alternation

RESULT = "[Subagent 'plant-cards' completed successfully]\n\nResult:\nSix pages written."


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Test title"))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    return loop


def _capture(loop: AgentLoop, reply: str = "Done: six cards.") -> dict:
    seen: dict = {"calls": 0}

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        seen["calls"] += 1
        seen["initial_messages"] = initial_messages
        return (reply, [], [*initial_messages, {"role": "assistant", "content": reply}], "stop", False)

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    return seen


def _announce(key: str, task_id: str = "sub-1") -> InboundMessage:
    return InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id=key,
        content=RESULT,
        metadata={"subagent_task_id": task_id},
    )


def _text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


async def test_the_result_survives_to_the_request_after_a_status_reply(tmp_path: Path) -> None:
    """La sequenza vista sul telefono: domanda, ``subagent_status`` running,
    risposta «ancora in corso», poi il rientro. Nella richiesta che parte verso il
    provider l'ultimo messaggio deve essere il rientro, come ``user``."""
    loop = _loop(tmp_path)
    key = "internal:plants"
    session = loop.sessions.get_or_create(key)
    session.add_message("user", "done, or are we waiting for the agents?")
    session.add_message(
        "assistant", "",
        tool_calls=[{
            "id": "call_status", "type": "function",
            "function": {"name": "subagent_status", "arguments": json.dumps({"task_id": "sub-1"})},
        }],
    )
    session.add_message(
        "tool", "Running:\n- [sub-1] plant-cards, state=running, elapsed=646s",
        tool_call_id="call_status", name="subagent_status",
    )
    session.add_message("assistant", "still running, it has been ~11 minutes")
    loop.sessions.save(session)
    seen = _capture(loop)

    await loop._process_message(_announce(key))

    wire = enforce_role_alternation([dict(m) for m in seen["initial_messages"]])
    assert wire[-1]["role"] == "user"
    assert "Six pages written." in _text(wire[-1])
    assert "Current Time:" in _text(wire[-1])


async def test_the_originating_question_stays_in_a_long_window(tmp_path: Path) -> None:
    """Un turno lungo (molti strumenti) prima del rientro: la finestra deve
    arrivare fino alla domanda che ha messo al lavoro il subagent, non fermarsi
    sulla riga sintetica del rientro."""
    loop = _loop(tmp_path)
    loop._max_messages = 6
    key = "internal:long"
    session = loop.sessions.get_or_create(key)
    session.add_message("user", "write the cards for the three new plants")
    for idx in range(5):
        session.add_message(
            "assistant", "",
            tool_calls=[{
                "id": f"call_{idx}", "type": "function",
                "function": {"name": "journal_append", "arguments": "{}"},
            }],
        )
        session.add_message("tool", "ok", tool_call_id=f"call_{idx}", name="journal_append")
    session.add_message("assistant", "a subagent is on it")
    loop.sessions.save(session)
    seen = _capture(loop)

    await loop._process_message(_announce(key))

    texts = [_text(m) for m in seen["initial_messages"] if m.get("role") != "system"]
    assert any("write the cards for the three new plants" in t for t in texts)
    assert "Six pages written." in texts[-1]


async def test_an_announce_already_answered_does_not_speak_again(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    key = "internal:twice"
    seen = _capture(loop)

    await loop._process_message(_announce(key))
    await loop._process_message(_announce(key))

    assert seen["calls"] == 1
    loop.sessions.invalidate(key)
    rows = loop.sessions.get_or_create(key).messages
    assert [m["role"] for m in rows] == ["user", "assistant"]


async def test_an_interrupted_announce_is_answered_once_on_retry(tmp_path: Path) -> None:
    """Il rientro scritto da un turno d'annuncio che non ha risposto: al nuovo
    giro e' gia' in storia, arriva al modello una volta sola e non si riscrive."""
    loop = _loop(tmp_path)
    key = "internal:retry"
    session = loop.sessions.get_or_create(key)
    session.add_message("user", "make the cards")
    session.add_message("assistant", "on it")
    loop._persist_subagent_followup(session, _announce(key))
    loop.sessions.save(session)
    seen = _capture(loop)

    await loop._process_message(_announce(key))

    non_system = [m for m in seen["initial_messages"] if m.get("role") != "system"]
    assert non_system[-1]["role"] == "user"
    assert _text(non_system[-1]).count("Six pages written.") == 1
    loop.sessions.invalidate(key)
    rows = loop.sessions.get_or_create(key).messages
    assert sum(1 for m in rows if m.get("injected_event") == "subagent_result") == 1
    assert rows[-1] == {**rows[-1], "role": "assistant", "content": "Done: six cards."}


async def test_a_refusal_after_the_result_retries_without_it(tmp_path: Path) -> None:
    """Il provider rifiuta di rispondere dopo il rientro: il runner lo riconosce
    come risultato d'aiutante e ritenta col segnaposto, tenendo il Runtime
    Context; la storia tiene il risultato com'era."""
    from jenny.agent.context import ContextBuilder
    from jenny.utils.runtime import PROVIDER_REFUSED_RESULT_PLACEHOLDER

    loop = _loop(tmp_path)
    key = "internal:plants"
    session = loop.sessions.get_or_create(key)
    session.add_message("user", "write the plant cards")
    session.add_message("assistant", "I started a helper for the cards.")
    loop.sessions.save(session)
    requests: list[list[dict]] = []

    async def chat(*, messages, **_kwargs):
        requests.append([dict(m) for m in messages])
        if len(requests) == 1:
            return LLMResponse(content=None, finish_reason="content_filter")
        return LLMResponse(content="Done: six cards.")

    loop.provider.chat_with_retry = chat
    loop.provider.chat_stream_with_retry = chat

    await loop._process_message(_announce(key))

    assert len(requests) == 2
    assert "Six pages written." in _text(requests[0][-1])
    retried = _text(requests[1][-1])
    assert retried.startswith(PROVIDER_REFUSED_RESULT_PLACEHOLDER)
    assert "Six pages written." not in retried
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in retried
    rows = loop.sessions.get_or_create(key).messages
    assert any(r.get("role") == "user" and r.get("content") == RESULT for r in rows)
    assert rows[-1]["content"] == "Done: six cards."
