"""Gli strumenti di un turno: quelli del loop, meno cio' che li' non serve.

Il 07/10/2026, nella chat ETF del Titan, 23 strumenti su 42 erano azioni di app
posate altrove (todo, mtg, piante) che scrivevano e a runtime rispondevano «Not
from here»; c'erano ``recall_history``, che nei quaderni rifiuta, e
``nothing_to_report``, che serve ai giri silenziosi. Un modello piccolo li vedeva
tutti e doveva capire da solo quali non usare.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.registry import ToolRegistry, ToolRegistryView
from jenny.bus.events import InboundMessage
from tests.agent.test_prompt_matches_offered_tools import (  # noqa: F401
    _loop_with_capture,
    _NamedTool,
    _offered_names,
    _system_prompt,
    workspace,
)


def _app(ws: Path, slug: str) -> None:
    d = ws / "apps" / slug
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({
        "name": slug.title(), "description": f"{slug} app",
        "actions": [
            {"name": "add", "description": "Add", "kind": "storage", "op": "append",
             "collection": "items", "params": {"what": {"type": "string"}}},
            {"name": "list", "description": "List", "kind": "storage", "op": "query",
             "collection": "items"},
        ],
        "summary": {"collection": "items", "view": "list", "columns": {"what": {"label": "W"}}},
    }))


@pytest.fixture
def notebook(workspace: Path) -> Path:  # noqa: F811
    from jenny.board.store import mutate_board

    root = workspace / "wikis" / "soldi"
    (root / "wiki").mkdir(parents=True)
    _app(workspace, "etf")
    _app(workspace, "todo")
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "etf", "title": "ETF", "pos": {"x": 1, "y": 1}, "app": {"slug": "etf"}}))
    return workspace


async def test_a_notebook_turn_sees_only_what_it_can_use(notebook: Path) -> None:
    loop, captured = _loop_with_capture(notebook)
    loop.orchestrator_mode = True
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="project:soldi", content="ciao"))
    offered = _offered_names(captured)
    assert {"etf_add", "etf_list", "todo_list"} <= offered
    assert "todo_add" not in offered
    assert "recall_history" not in offered and "nothing_to_report" not in offered
    # La vista e' ancora l'orchestratore: il suo prompt c'e'.
    assert "# Orchestrator Mode" in _system_prompt(captured)
    # Il registro del loop non e' stato toccato.
    assert loop.tools.get("todo_add") is not None


async def test_the_personal_chat_keeps_its_app_tools(notebook: Path) -> None:
    loop, captured = _loop_with_capture(notebook)
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="ciao"))
    offered = _offered_names(captured)
    assert {"todo_add", "etf_add"} <= offered and "nothing_to_report" not in offered


def test_the_view_is_live_and_counts_on_the_loop_registry() -> None:
    base = ToolRegistry()
    base.register(_NamedTool("keep"))
    base.register(_NamedTool("hide_me"))
    view = ToolRegistryView(base, lambda name, _tool: name.startswith("hide"))
    assert view.tool_names == ["keep"]
    base.register(_NamedTool("new_app_action"))
    assert "new_app_action" in view.tool_names
    assert [d["function"]["name"] for d in view.get_definitions()] == ["keep", "new_app_action"]
    before = base.exec_seq
    tool, _params, error = view.prepare_call("keep", {})
    assert tool is not None and error is None and base.exec_seq == before + 1
    hidden: Any = view.prepare_call("hide_me", {})
    assert hidden[0] is None


async def test_an_app_placed_mid_turn_is_usable_in_the_same_turn(notebook: Path) -> None:
    """``app action=place`` a meta' turno: le azioni che scrivono si vedono subito."""
    from jenny.board.store import mutate_board
    from jenny.session.manager import Session

    loop, _captured = _loop_with_capture(notebook)
    loop.orchestrator_mode = True
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="project:soldi",
                         content="ciao")
    view = loop._turn_tool_view(msg, Session(key="project:soldi"), "project:soldi",
                                silent=False)
    assert view is not None and "todo_add" not in view.tool_names
    mutate_board(notebook / "wikis" / "soldi", lambda b: b["cards"].append(
        {"id": "todo", "title": "Todo", "pos": {"x": 2, "y": 1}, "app": {"slug": "todo"}}))
    assert "todo_add" in view.tool_names


async def test_the_move_offer_is_only_for_the_personal_chat_in_the_app(notebook: Path) -> None:
    """«Ci spostiamo?» e' un tasto: dove non c'e' un tasto da toccare, non c'e'."""
    loop, captured = _loop_with_capture(notebook)
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="ciao"))
    assert "suggest_notebook" in _offered_names(captured)
    captured.clear()
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="project:soldi", content="ciao"))
    assert "suggest_notebook" not in _offered_names(captured)
    captured.clear()
    await loop._process_message(InboundMessage(
        channel="telegram", sender_id="u", chat_id="42", content="ciao"))
    assert "suggest_notebook" not in _offered_names(captured)
