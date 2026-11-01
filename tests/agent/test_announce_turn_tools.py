"""Il rientro di un aiutante vede gli stessi tool del turno dell'utente.

Il percorso di sistema costruiva il suo registro e il suo prompt per conto
proprio: ``nothing_to_report`` e ``screen`` restavano nella richiesta, e
l'inventario a fine prompt elencava il registro intero invece di quello del
turno. Il messaggio cambiava proprio li', e il provider non ritrovava niente
in cache: sul s22, il 28/10/2026, 0 token su ~21,7k al primo passo dopo ogni
aiutante; con lo stesso registro 20,8k.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.session_turns import monitor_session_key
from jenny.providers.base import LLMResponse
from jenny.session.keys import UNIFIED_SESSION_KEY

# Un turno silenzioso di lavoro interno: la sessione di un monitor del cron.
MONITOR_SESSION_KEY = monitor_session_key("rain-check")


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )
    return loop


async def _announce_turn(loop: AgentLoop, session_key: str) -> tuple[list[str], str]:
    """I tool e il prompt di sistema che il rientro di un aiutante manda al modello."""
    seen: dict = {}

    async def fake_run_agent_loop(initial_messages, **kwargs):
        seen["tools"] = kwargs["tools"] or loop.tools
        seen["system"] = initial_messages[0]["content"]
        return ("fatto", [], [*initial_messages], "stop", False)

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    await loop._process_message(InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="websocket:default",
        content="subagent completed successfully: ricerca",
        metadata={"subagent_task_id": "sub-1"},
        session_key_override=session_key,
    ))
    return sorted(seen["tools"].tool_names), seen["system"]


def _user_turn_tools(loop: AgentLoop) -> list[str]:
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="ciao")
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    view = loop._turn_tool_view(msg, session, UNIFIED_SESSION_KEY, silent=False)
    return sorted((view or loop.tools).tool_names)


async def test_a_visible_announce_has_the_user_turns_tools(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    tools, system = await _announce_turn(loop, UNIFIED_SESSION_KEY)

    assert tools == _user_turn_tools(loop)
    assert "nothing_to_report" not in tools
    # L'inventario del prompt dice gli stessi tool della richiesta.
    assert "nothing_to_report" not in system


async def test_a_silent_announce_keeps_nothing_to_report(tmp_path: Path) -> None:
    """Un controllo silenzioso decide qui se parlare: ``nothing_to_report`` e' la sua uscita."""
    loop = _make_loop(tmp_path)

    tools, system = await _announce_turn(loop, MONITOR_SESSION_KEY)

    assert "nothing_to_report" in tools
    assert "nothing_to_report" in system
