"""``recall_chat``: le parole esatte delle chat, e il recinto dei quaderni.

Il recinto è quello di ``recall_history``: dalla chat di un quaderno si vede solo
quella chat, perché una sessione di progetto scrive nel progetto e la chat
personale non ci deve finire; dalla chat personale si cerca in tutte.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from jenny.agent.tools.memory_recall import ChatRecallTool
from jenny.security.workspace_access import enter_workspace_scope
from jenny.webui import transcript_store as ts


@dataclass
class _Scope:
    project_path: Path
    restrict_to_workspace: bool = False
    writable: bool = True


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    return ts._workspace()


def _say(key: str, user: str, answer: str) -> None:
    ts.append_transcript_object(key, {"event": "user", "text": user, "at": 1_793_000_000_000})
    for piece in answer.split(" "):
        ts.append_transcript_object(key, {"event": "delta", "text": piece + " ",
                                          "stream_id": "s", "turn_phase": "answer"})
    ts.append_transcript_object(key, {"event": "stream_end", "stream_id": "s"})
    ts.append_transcript_object(key, {"event": "turn_end"})


async def test_a_search_shows_the_exchange_its_chat_and_a_cursor(ws) -> None:
    _say("websocket:default", "Come chiamiamo il bot dell'acero?", "Lo chiamiamo Acerello.")
    out = await ChatRecallTool(ws).execute(query=["acerello", "bot"])
    assert "[0@websocket:default]" in out
    assert "personal chat" in out
    assert "[Acerello]" in out
    assert "user:" in out and "you:" in out


async def test_the_user_line_is_shown_when_only_the_answer_matches(ws) -> None:
    _say("websocket:default", "e il nome?", "Albinella, la monstera.")
    out = await ChatRecallTool(ws).execute(query=["albinella"])
    assert "user: e il nome?" in out


async def test_opening_a_cursor_shows_the_exchange_and_its_neighbours(ws) -> None:
    for n in range(3):
        _say("websocket:default", f"domanda {n}", f"risposta {n}")
    out = await ChatRecallTool(ws).execute(cursors=["1@websocket:default"])
    assert "domanda 0" in out and "domanda 2" in out
    assert "user <<: domanda 1" in out


async def test_a_cursor_that_does_not_exist_is_reported(ws) -> None:
    _say("websocket:default", "uno", "due")
    out = await ChatRecallTool(ws).execute(cursors=["9@websocket:default", "storto"])
    assert "No exchange to open for 9@websocket:default, storto" in out


async def test_no_match_never_reads_as_never_said(ws) -> None:
    _say("websocket:default", "uno", "due")
    out = await ChatRecallTool(ws).execute(query=["glicine"])
    assert "does not mean it was never said" in out


async def test_inside_a_notebook_only_its_chat(ws) -> None:
    _say("websocket:default", "il glicine di casa", "ok")
    _say("websocket:project:piante", "il glicine del quaderno", "ok")
    project = ws / "wikis" / "piante"
    project.mkdir(parents=True)
    tool = ChatRecallTool(ws)
    with enter_workspace_scope(_Scope(project_path=project)):
        out = await tool.execute(query=["glicine"])
        opened = await tool.execute(cursors=["0@websocket:default"])
    assert "quaderno" in out and "di casa" not in out
    assert "notebook piante" in out
    assert "No exchange to open" in opened


async def test_the_personal_chat_searches_every_chat(ws) -> None:
    _say("websocket:default", "il glicine di casa", "ok")
    _say("websocket:project:piante", "il glicine del quaderno", "ok")
    with enter_workspace_scope(_Scope(project_path=ws)):
        out = await ChatRecallTool(ws).execute(query=["glicine"])
    assert "di casa" in out and "quaderno" in out


def test_the_prompt_names_it_only_when_the_turn_has_it(tmp_path) -> None:
    from jenny.agent.context import ContextBuilder

    builder = ContextBuilder(tmp_path)
    with_tool = builder._get_identity(has=lambda name: True)
    without = builder._get_identity(has=lambda name: name != "recall_chat")
    assert "recall_chat" in with_tool
    assert "recall_chat" not in without
