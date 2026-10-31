"""Il tasto Piano sul filo: ``plan: true`` diventa un turno di pianificazione, solo in un quaderno.

Il turno di pianificazione è in sola lettura comunque: lo decide il server, non il
``readonly`` che il client manda (o dimentica). Fuori dalla chat di un quaderno il
flag non vale niente — un piano fotografa un quaderno, e la chat personale non ne ha
uno.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services


def _channel() -> WebSocketChannel:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    cfg = {"enabled": True, "allowFrom": ["*"], "websocketRequiresToken": False}
    gateway = build_gateway_services(
        config=WebSocketConfig.model_validate(cfg),
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    channel = WebSocketChannel(cfg, bus, gateway=gateway)
    channel._handle_message = AsyncMock()  # type: ignore[method-assign]
    return channel


async def _metadata(envelope: dict[str, Any]) -> dict[str, Any]:
    channel = _channel()
    await channel._dispatch_envelope(AsyncMock(), "client-1", {"type": "message", **envelope})
    channel._handle_message.assert_awaited_once()
    return channel._handle_message.await_args.kwargs["metadata"]


async def test_plan_in_a_notebook_is_a_read_only_planning_turn() -> None:
    meta = await _metadata({"chat_id": "project:piante", "content": "Rifai la wiki", "plan": True})
    assert meta["plan_mode"] is True
    assert meta["readonly"] is True
    assert "plan_revise" not in meta


async def test_review_carries_the_plan_id() -> None:
    meta = await _metadata({
        "chat_id": "project:piante", "content": "Togli il passo 3",
        "plan": True, "plan_revise": "p_0123456789ab",
    })
    assert meta["plan_revise"] == "p_0123456789ab"


async def test_plan_outside_a_notebook_is_ignored() -> None:
    meta = await _metadata({"chat_id": "default", "content": "piano", "plan": True})
    assert "plan_mode" not in meta and "readonly" not in meta


async def test_only_true_turns_it_on() -> None:
    meta = await _metadata({"chat_id": "project:piante", "content": "x", "plan": "yes"})
    assert "plan_mode" not in meta
