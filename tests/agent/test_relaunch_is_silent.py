"""Un subagent caduto e rilanciato: in chat arriva solo l'esito del rilancio.

Il 07/10/2026 sul Titan un costruttore di app e' caduto, Jenny l'ha rilanciato e
ha scritto «piccolo intoppo, l'ho rilanciato»; poi e' arrivato il risultato vero.
L'utente ha chiesto che resti solo il secondo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.base import Tool
from jenny.agent.turn_types import TurnDisposition
from jenny.bus.events import InboundMessage
from jenny.providers.base import LLMResponse, ToolCallRequest
from tests.agent.test_turn_identity import _loop, _script


class _FakeRestart(Tool):
    @property
    def name(self) -> str:
        return "subagent_restart"

    @property
    def description(self) -> str:
        return "relaunch"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"task_id": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return "Subagent [App] started (attempt 2) (id: b2)."


def _announce(status: str) -> InboundMessage:
    return InboundMessage(
        channel="system", sender_id="subagent", chat_id="websocket:default",
        content=f"[Subagent 'App' {status}]\n\nResult:\nlist_dir: workspace_violation",
        metadata={"injected_event": "subagent_result", "subagent_status": status,
                  "subagent_task_id": "b1", "subagent_lineage_id": "l1",
                  "subagent_attempt": 1},
    )


def _relaunching() -> list[LLMResponse]:
    return [
        LLMResponse(content="", tool_calls=[
            ToolCallRequest(id="c1", name="subagent_restart", arguments={"task_id": "b1"}),
        ]),
        LLMResponse(content="piccolo intoppo, l'ho rilanciato", tool_calls=[]),
    ]


@pytest.mark.asyncio
async def test_a_relaunch_after_a_failure_says_nothing(tmp_path: Path) -> None:
    loop, _bus = _loop(tmp_path)
    loop.tools.register(_FakeRestart())
    _script(loop, _relaunching())
    outcome = await loop._process_system_message(_announce("error"))
    assert outcome.disposition is TurnDisposition.SILENT
    # Nella storia resta: Jenny sa di averlo rilanciato.
    session = loop.sessions.get_or_create("websocket:default")
    assert any("rilanciato" in str(m.get("content")) for m in session.messages)


@pytest.mark.asyncio
async def test_a_failure_without_a_relaunch_is_told(tmp_path: Path) -> None:
    loop, _bus = _loop(tmp_path)
    _script(loop, [LLMResponse(content="non e' riuscito: la fonte rifiuta", tool_calls=[])])
    outcome = await loop._process_system_message(_announce("error"))
    assert outcome.disposition is TurnDisposition.DELIVERED


@pytest.mark.asyncio
async def test_a_success_is_always_told(tmp_path: Path) -> None:
    loop, _bus = _loop(tmp_path)
    loop.tools.register(_FakeRestart())
    _script(loop, _relaunching())
    outcome = await loop._process_system_message(_announce("ok"))
    assert outcome.disposition is TurnDisposition.DELIVERED
