import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.session_turns import monitor_session_key
from jenny.providers.base import GenerationSettings, LLMResponse
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.turn_visibility import TurnVisibility
from jenny.session.webui_turns import WebuiTurnCoordinator

# Un turno silenzioso di lavoro interno: la sessione di un monitor del cron.
MONITOR_SESSION_KEY = monitor_session_key("rain-check")


def _make_loop(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=0)
    provider.estimate_prompt_tokens.return_value = (0, "test-counter")
    response = LLMResponse(content="done", tool_calls=[])
    provider.chat_with_retry = AsyncMock(return_value=response)
    provider.chat_stream_with_retry = AsyncMock(return_value=response)

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    WebuiTurnCoordinator(
        bus=bus,
        sessions=loop.sessions,
        schedule_background=lambda coro: loop._schedule_background(coro),
    ).subscribe(loop.runtime_events)
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


async def _goal_statuses(loop: AgentLoop) -> list[dict]:
    events = []
    while loop.bus.outbound_size:
        events.append(await loop.bus.consume_outbound())
    return [
        event.metadata
        for event in events
        if event.metadata.get("_goal_status") is True
    ]


@pytest.mark.asyncio
async def test_process_direct_websocket_clears_run_status(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    response = await loop.process_direct(
        "deliver reminder",
        session_key=UNIFIED_SESSION_KEY,
        channel="websocket",
        chat_id="chat-1",
    )

    assert response is not None
    assert response.content == "done"

    statuses = await _goal_statuses(loop)
    assert [status["goal_status"] for status in statuses] == ["running", "idle"]
    assert isinstance(statuses[0].get("started_at"), float)
    assert "started_at" not in statuses[1]


@pytest.mark.asyncio
async def test_a_silent_turn_projects_no_run_status_into_the_chat(tmp_path) -> None:
    """Lavoro interno su un canale utente: nessuno spinner nella conversazione.

    È il caso di un monitor, che gira *su* ``websocket:default`` perché è il
    target a cui consegnerà se una condizione scatta. Lo spinner "in esecuzione"
    e il marcatore di fine turno appartengono alla conversazione dell'utente,
    non a un controllo che non ha chiesto.
    """
    loop = _make_loop(tmp_path)

    response = await loop.process_direct(
        "controlla la pioggia",
        session_key=MONITOR_SESSION_KEY,
        channel="websocket",
        chat_id="default",
        visibility=TurnVisibility.SILENT,
    )

    # Nessuna consegna implicita: l'unico modo di parlare era il tool ``message``.
    assert response is None
    assert await _goal_statuses(loop) == []


@pytest.mark.asyncio
async def test_an_internal_session_on_a_user_channel_is_silent_without_asking(
    tmp_path,
) -> None:
    """La provenienza basta: nessun parametro esplicito, stesso silenzio."""
    loop = _make_loop(tmp_path)

    response = await loop.process_direct(
        "annuncio di un subagent interno",
        session_key=MONITOR_SESSION_KEY,
        channel="websocket",
        chat_id="default",
    )

    assert response is None
    assert await _goal_statuses(loop) == []


@pytest.mark.asyncio
async def test_process_direct_reuses_existing_session_lock(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session_key = "api:fixed"
    lock = loop._session_locks.get(session_key)
    entered = asyncio.Event()

    async def _process_message(msg, **_kwargs):
        entered.set()
        return TurnOutcome.delivered(
            OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=msg.content)
        )

    loop._process_message = _process_message
    task = None
    try:
        # Il test tiene il lock condiviso: process_direct (task separato) deve
        # riusare lo STESSO lock e quindi bloccarsi finché non lo rilasciamo.
        async with lock:
            task = asyncio.create_task(loop.process_direct("direct", session_key=session_key))
            await asyncio.sleep(0.02)
            assert not entered.is_set()
        # lock rilasciato all'uscita dell'async with

        response = await asyncio.wait_for(task, timeout=1.0)
        assert entered.is_set()
        assert response is not None
        assert response.content == "direct"
    finally:
        if task is not None and not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
