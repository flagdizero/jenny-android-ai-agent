"""Identita del turno: dal dispatch dell'AgentLoop fino ai tool.

Perche questo file esiste. La guardia anti-polling di ``subagent_status``
delimitava il turno sul ``message_id`` del messaggio in ingresso, che il canale
WebSocket non imposta mai: in produzione era sempre ``None`` e la guardia si
disarmava da sola. I test la coprivano iniettando un ``message_id`` a mano —
cioe esattamente l'unico modo in cui non arriva mai. Qui i turni partono dagli
ingressi veri (``_dispatch`` per il bus, ``process_direct`` per cron/comandi) con
i metadati che il canale WebSocket produce davvero.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from support.agent import make_loop, make_provider

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.base import Tool
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse, ToolCallRequest


def _webui_metadata() -> dict[str, Any]:
    """Metadati di un messaggio della WebUI, come li costruisce il canale.

    Copia fedele di ``WebSocketChannel._dispatch_envelope`` + ``_handle_message``
    (jenny/channels/websocket.py): ``webui_turn_id`` viene dal client,
    ``_wants_stream`` dal canale. Nessun ``message_id``: e il punto del test.
    """
    return {
        "remote": ("127.0.0.1", 51234),
        "webui": True,
        "webui_turn_id": "client-turn-abc",
        "conn_id": "conn-1",
        "_wants_stream": True,
    }


_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_websocket_channel_still_sends_no_message_id() -> None:
    """Pin del presupposto, non della guardia.

    La guardia si delimita sul turn id, quindi non dipende da questo; ma questo
    e il motivo per cui non puo dipendere dal ``message_id``. Se un giorno il
    canale iniziasse a mandarlo, e questo test a dover essere riletto: il
    ``message_id`` resta un id di *routing* (reply/announce), non l'identita di
    un turno, e le guardie per-turno non vanno riagganciate la.
    """
    for relative in ("jenny/channels/websocket.py", "jenny/channels/ws_parsing.py"):
        source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert '"message_id"' not in source, relative


class _ContextProbe(Tool, ContextAware):
    """Tool inerte che registra il ``RequestContext`` consegnato dal loop."""

    def __init__(self) -> None:
        self.seen: list[RequestContext] = []

    @property
    def name(self) -> str:
        return "context_probe"

    @property
    def description(self) -> str:
        return "probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"

    def set_context(self, ctx: RequestContext) -> None:
        self.seen.append(ctx)


def _loop(tmp_path: Path, *, orchestrator: bool = False) -> tuple[AgentLoop, MessageBus]:
    provider = make_provider(bare=True)
    provider.supports_progress_deltas = False
    # Letta dal Consolidator per il budget di token: un MagicMock qui farebbe
    # esplodere il secondo turno di una sessione con history.
    provider.generation.max_tokens = 4096
    loop = make_loop(
        tmp_path, context_window_tokens=None, provider=provider,
        orchestrator_mode=orchestrator,
    )
    return loop, loop.bus


def _script(loop: AgentLoop, responses: list[LLMResponse]) -> list[list[dict[str, Any]]]:
    """Fa rispondere il provider con *responses* e registra i messaggi inviati."""
    seen: list[list[dict[str, Any]]] = []
    it = iter(responses)

    def _next(*args: Any, **kwargs: Any) -> LLMResponse:
        seen.append(kwargs.get("messages") or [])
        return next(it)

    loop.provider.chat_with_retry = AsyncMock(side_effect=_next)
    loop.provider.chat_stream_with_retry = AsyncMock(side_effect=_next)
    return seen


def _probe(loop: AgentLoop) -> _ContextProbe:
    probe = _ContextProbe()
    loop.tools.register(probe)
    return probe


@pytest.mark.asyncio
async def test_a_webui_turn_carries_a_turn_id_and_no_message_id(tmp_path: Path) -> None:
    loop, _bus = _loop(tmp_path)
    probe = _probe(loop)
    _script(loop, [LLMResponse(content="hi", tool_calls=[])])

    msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="default",
        content="ciao", metadata=_webui_metadata(),
    )
    assert "message_id" not in msg.metadata
    await loop._dispatch(msg)

    assert probe.seen, "il loop deve consegnare il contesto ai tool"
    ctx = probe.seen[-1]
    assert ctx.message_id is None
    assert ctx.turn_id and ctx.turn_id.startswith("unified:default:")


@pytest.mark.asyncio
async def test_each_turn_gets_a_distinct_identity(tmp_path: Path) -> None:
    """Il confine fra turni e cio che riabilita le guardie per-turno."""
    loop, _bus = _loop(tmp_path)
    probe = _probe(loop)
    _script(loop, [
        LLMResponse(content="a", tool_calls=[]),
        LLMResponse(content="b", tool_calls=[]),
    ])

    for _ in range(2):
        await loop._dispatch(InboundMessage(
            channel="websocket", sender_id="u1", chat_id="default",
            content="ciao", metadata=_webui_metadata(),
        ))

    turn_ids = [c.turn_id for c in probe.seen]
    assert all(turn_ids)
    assert len(set(turn_ids)) == 2, turn_ids


@pytest.mark.asyncio
async def test_a_subagent_announce_turn_carries_a_turn_id(tmp_path: Path) -> None:
    """Turno interno (annuncio di subagent): identita coerente, non assente."""
    loop, _bus = _loop(tmp_path)
    probe = _probe(loop)
    _script(loop, [LLMResponse(content="letto", tool_calls=[])])

    await loop._dispatch(InboundMessage(
        channel="system", sender_id="subagent", chat_id="websocket:default",
        content="Subagent finished: ok",
        metadata={"origin_message_id": "spawn-1"},
    ))

    assert probe.seen
    assert probe.seen[-1].turn_id


@pytest.mark.asyncio
async def test_a_cron_style_direct_turn_carries_a_turn_id(tmp_path: Path) -> None:
    loop, _bus = _loop(tmp_path)
    probe = _probe(loop)
    _script(loop, [LLMResponse(content="fatto", tool_calls=[])])

    await loop.process_direct("heartbeat", session_key="internal:cron")

    assert probe.seen
    assert probe.seen[-1].turn_id.startswith("internal:cron:")


@pytest.mark.asyncio
async def test_second_subagent_status_is_refused_in_a_real_webui_turn(
    tmp_path: Path,
) -> None:
    """Il caso visto sul device: due ``subagent_status`` di fila, entrambe passate.

    Turno vero (metadati del canale WebSocket, nessun ``message_id``), registry
    vero, path di esecuzione del runner: la guardia deve mordere qui, non solo
    quando un test le regala un'identita di turno.
    """
    loop, _bus = _loop(tmp_path, orchestrator=True)
    assert loop.tools.get("subagent_status") is not None
    responses = [
        LLMResponse(content="", tool_calls=[
            ToolCallRequest(id="c1", name="subagent_status", arguments={}),
        ]),
        LLMResponse(content="", tool_calls=[
            ToolCallRequest(id="c2", name="subagent_status", arguments={}),
        ]),
        LLMResponse(content="Nothing is running.", tool_calls=[]),
    ]
    seen = _script(loop, responses)

    await loop._dispatch(InboundMessage(
        channel="websocket", sender_id="u1", chat_id="default",
        content="cosa sta girando?", metadata=_webui_metadata(),
    ))

    assert len(seen) == 3, "il provider deve aver visto entrambe le tool call"
    conversation = json.dumps(seen[-1], default=str)
    assert conversation.count("Running subagents") == 1, "la prima chiamata passa"
    assert "Refused: subagent_status was already called in this turn" in conversation


@pytest.mark.asyncio
async def test_subagent_status_is_allowed_again_after_another_tool(tmp_path: Path) -> None:
    """Stesso turno, ma con un'altra tool call in mezzo: nessun rifiuto."""
    loop, _bus = _loop(tmp_path, orchestrator=True)
    responses = [
        LLMResponse(content="", tool_calls=[
            ToolCallRequest(id="c1", name="subagent_status", arguments={}),
        ]),
        LLMResponse(content="", tool_calls=[
            ToolCallRequest(id="c2", name="list_dir", arguments={"path": "."}),
        ]),
        LLMResponse(content="", tool_calls=[
            ToolCallRequest(id="c3", name="subagent_status", arguments={}),
        ]),
        LLMResponse(content="Nothing is running.", tool_calls=[]),
    ]
    seen = _script(loop, responses)

    await loop._dispatch(InboundMessage(
        channel="websocket", sender_id="u1", chat_id="default",
        content="cosa sta girando?", metadata=_webui_metadata(),
    ))

    assert len(seen) == 4
    conversation = json.dumps(seen[-1], default=str)
    assert "Refused:" not in conversation
    assert conversation.count("Running subagents") == 2
