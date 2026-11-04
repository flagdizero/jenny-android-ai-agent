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
from jenny.cron.session_turns import monitor_session_key
from jenny.session.keys import UNIFIED_SESSION_KEY
from tests.agent.test_prompt_matches_offered_tools import (  # noqa: F401
    _loop_with_capture,
    _NamedTool,
    _offered_names,
    _system_prompt,
    workspace,
)

# Un turno silenzioso di lavoro interno: la sessione di un monitor del cron.
MONITOR_SESSION_KEY = monitor_session_key("rain-check")


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
    # Le app passano tutte da ``use_app``: la scrittura di un'app non posata la
    # rifiuta l'esecutore, non la lista (dal 03/11/2026).
    assert "use_app" in offered and not any(n.startswith(("etf_", "todo_")) for n in offered)
    assert "recall_history" not in offered and "nothing_to_report" not in offered
    # La vista e' ancora l'orchestratore: il suo prompt c'e'.
    assert "# Orchestrator Mode" in _system_prompt(captured)


async def test_the_personal_chat_keeps_its_app_tools(notebook: Path) -> None:
    loop, captured = _loop_with_capture(notebook)
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="ciao"))
    offered = _offered_names(captured)
    assert "use_app" in offered and "nothing_to_report" not in offered


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


async def test_placing_an_app_mid_turn_leaves_the_tool_list_alone(notebook: Path) -> None:
    """``app action=place`` a meta' turno non cambia la lista: la cache del provider resta.

    Fino al 03/11/2026 le azioni che scrivono comparivano alla posa, e la chiamata
    dopo ripagava tutta la conversazione.
    """
    from jenny.board.store import mutate_board
    from jenny.session.manager import Session

    loop, _captured = _loop_with_capture(notebook)
    loop.orchestrator_mode = True
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="project:soldi",
                         content="ciao")
    view = loop._turn_tool_view(msg, Session(key="project:soldi"), "project:soldi",
                                silent=False)
    before = (view or loop.tools).get_definitions()
    mutate_board(notebook / "wikis" / "soldi", lambda b: b["cards"].append(
        {"id": "todo", "title": "Todo", "pos": {"x": 2, "y": 1}, "app": {"slug": "todo"}}))
    assert (view or loop.tools).get_definitions() == before


async def test_the_move_offer_is_only_for_the_personal_chat(notebook: Path) -> None:
    """«Ci spostiamo?» e' un tasto della chat personale: un quaderno non ce l'ha.

    Da Telegram la chat e' la stessa, quindi la lista resta quella dell'app (una
    lista diversa faceva ripagare la conversazione al provider, dal 03/11/2026):
    e' il tool a rispondere che da li' non si puo'.
    """
    loop, captured = _loop_with_capture(notebook)
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="ciao"))
    from_the_app = _offered_names(captured)
    assert "suggest_notebook" in from_the_app
    captured.clear()
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="project:soldi", content="ciao"))
    assert "suggest_notebook" not in _offered_names(captured)
    captured.clear()
    await loop._process_message(InboundMessage(
        channel="telegram", sender_id="u", chat_id="42", content="ciao"))
    assert _offered_names(captured) == from_the_app


@pytest.mark.parametrize(
    ("channel", "chat_id", "key", "silent"),
    [
        ("websocket", "project:soldi", "project:soldi", False),   # quaderno, app non posata
        ("websocket", "project:codice", "project:codice", False),  # progetto, non quaderno
        ("websocket", "default", UNIFIED_SESSION_KEY, False),
        ("telegram", "42", UNIFIED_SESSION_KEY, False),
        ("websocket", "default", MONITOR_SESSION_KEY, True),
    ],
)
def test_a_system_turn_offers_the_tools_of_a_user_turn_in_that_chat(
    notebook: Path, channel: str, chat_id: str, key: str, silent: bool,
) -> None:
    """Il rientro di un aiutante vede gli strumenti del turno dell'utente in quella chat.

    Uno stesso filtro: con una sua copia delle regole, gli strumenti cambiavano
    fra un turno e il rientro dopo, e il provider non ritrovava niente in cache.
    """
    (notebook / "wikis" / "codice").mkdir(parents=True)
    loop, _captured = _loop_with_capture(notebook)
    loop.orchestrator_mode = True
    session = loop.sessions.get_or_create(key)
    user = loop._turn_tool_view(
        InboundMessage(channel=channel, sender_id="u", chat_id=chat_id, content="ciao",
                       session_key_override=key),
        session, key, silent=silent,
    )
    system = loop._system_turn_tools(
        InboundMessage(channel="system", sender_id="subagent", chat_id=f"{channel}:{chat_id}",
                       content="fatto", metadata={"subagent_task_id": "sub-1"},
                       session_key_override=key),
        key, session, silent=silent,
    )
    assert sorted((system or loop.tools).tool_names) == sorted((user or loop.tools).tool_names)


async def test_the_personal_chat_offers_one_tool_list_from_every_channel(notebook: Path) -> None:
    """App, Telegram, il fumetto della mascotte: la stessa chat, la stessa lista.

    La lista dei tool sta in testa a quel che il provider tiene in cache. Il
    03/11/2026, sul telefono di prova, un turno con un tool in piu' (la
    pianificazione) riprendeva dalla cache 8k token su 30k; il tocco sulla
    mascotte e una risposta da Telegram, calcolati, il 25-33%.
    """
    from jenny.agent.tools.screen import ScreenTool
    from jenny.channels.floating import FLOATING_CHANNEL

    loop, captured = _loop_with_capture(notebook)
    loop.tools.register(ScreenTool())
    lists = []
    for channel, chat_id in (("websocket", "default"), ("telegram", "42"),
                             (FLOATING_CHANNEL, "shade"), ("websocket", "default")):
        captured.clear()
        await loop._process_message(InboundMessage(
            channel=channel, sender_id="u", chat_id=chat_id, content="ciao"))
        lists.append(_offered_names(captured))
    assert all(names == lists[0] for names in lists), lists
    assert {"screen", "suggest_notebook", "use_app"} <= lists[0]
