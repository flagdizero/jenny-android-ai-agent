"""I cron del quaderno: nascono nel cassetto di una scheda, e ci restano.

«Se dico aggiungi cron devo vedere che c'e' il cron nella scheda»: il tool, dentro
un quaderno con la bacheca, crea il job **legato** a quaderno, scheda e sezione;
il turno gira col recinto del quaderno; l'esito torna nel cassetto; togliere la
sezione toglie il job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.sessions import FakeSessions

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.turn_types import TurnOutcome
from jenny.board.store import mutate_board, read_board
from jenny.cron.bound_runner import run_bound_cron_job
from jenny.cron.service import CronService
from jenny.security.workspace_access import (
    WorkspaceScopeResolver,
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from jenny.session.keys import notebook_cron_session_key, notebook_of_cron_session


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch):
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "casa"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "annunci", "title": "Annunci", "pages": [], "pos": {"x": 1, "y": 1}}))
    # Il quaderno lo trova ``cron_link`` dalla radice del workspace; qui la
    # radice e' quella del test (senza toccare ``get_workspace_path``, che i
    # template del prompt usano per cercare le loro copie).
    monkeypatch.setattr("jenny.board.cron_link.notebook_root",
                        lambda name: root if name == "casa" and (root / "wiki").is_dir() else None)
    token = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    try:
        yield ws, root
    finally:
        reset_workspace_scope(token)


def _tool(store: Path, key: str = "project:casa") -> CronTool:
    tool = CronTool(CronService(store))
    tool.set_context(RequestContext(channel="websocket", chat_id=key, session_key=key))
    return tool


async def test_a_cron_asked_in_a_notebook_lands_in_a_drawer(notebook, tmp_path) -> None:
    _ws, root = notebook
    store = tmp_path / "cron" / "jobs.json"
    tool = _tool(store)
    out = await tool.execute(action="add", card="annunci", message="cerca annunci nuovi",
                             cron_expr="0 9 * * 1", mode="monitor")
    assert "in the drawer of «Annunci»" in out
    job = CronService(store).list_jobs()[0]
    section = read_board(root)["cards"][0]["userSections"][0]
    assert section["kind"] == "cron" and section["job"] == job.id
    assert job.payload.board == {"notebook": "casa", "card": "annunci", "section": section["id"]}
    # Consegna nella chat del quaderno.
    assert (job.payload.origin_channel, job.payload.origin_chat_id) == ("websocket", "project:casa")
    listed = await tool.execute(action="list")
    assert job.id in listed and "in the drawer of `annunci`" in listed


async def test_without_a_card_it_asks_which_drawer(notebook, tmp_path) -> None:
    tool = _tool(tmp_path / "jobs.json")
    out = await tool.execute(action="add", message="x", every_seconds=3600)
    assert "Say which card" in out and "«Annunci»" in out
    assert CronService(tmp_path / "jobs.json").list_jobs() == []


async def test_a_notebook_sees_and_removes_only_its_own_jobs(notebook, tmp_path) -> None:
    _ws, root = notebook
    store = tmp_path / "jobs.json"
    personal = CronTool(CronService(store))
    personal.set_context(RequestContext(channel="websocket", chat_id="default",
                                        session_key="unified:default"))
    await personal.execute(action="add", message="sveglia", every_seconds=3600)
    tool = _tool(store)
    await tool.execute(action="add", card="Annunci", message="cerca", every_seconds=3600)
    jobs = {j.payload.board is None: j for j in CronService(store).list_jobs()}
    assert jobs[True].id not in await tool.execute(action="list")
    assert "No job" in await tool.execute(action="remove", job_id=jobs[True].id)
    out = await tool.execute(action="remove", job_id=jobs[False].id)
    assert "Removed" in out or "removed" in out
    assert read_board(root)["cards"][0]["userSections"] == []
    assert [j.id for j in CronService(store).list_jobs()] == [jobs[True].id]


async def test_a_notebook_without_a_board_still_refuses(tmp_path) -> None:
    root = tmp_path / "workspace" / "wikis" / "vuoto"
    (root / "wiki").mkdir(parents=True)
    token = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    try:
        out = await _tool(tmp_path / "jobs.json", "project:vuoto").execute(
            action="add", message="x", every_seconds=60)
    finally:
        reset_workspace_scope(token)
    assert out.startswith("Not here")


def test_the_monitor_turn_gets_the_notebook_fence(tmp_path) -> None:
    ws = tmp_path / "workspace"
    (ws / "wikis" / "casa" / "wiki").mkdir(parents=True)
    key = notebook_cron_session_key("casa", "ab12cd34")
    assert notebook_of_cron_session(key) == "casa"
    resolver = WorkspaceScopeResolver(default_workspace=ws, default_restrict_to_workspace=True)
    scope = resolver.for_turn(channel="websocket", message_metadata={}, session_metadata={},
                              session_key=key)
    assert scope.write_root() == (ws / "wikis" / "casa").resolve()


class _Agent:
    def __init__(self, sent: str) -> None:
        self.tools = ToolRegistry()
        self.sessions = FakeSessions()
        self.received = []
        self._sent = sent

    async def submit_cron_turn(self, msg):
        self.received.append(msg)
        session = self.sessions.get_or_create(msg.session_key_override)
        session.messages.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "message", "arguments": '{"content": "%s"}' % self._sent}}]})
        return TurnOutcome.spoke_via_tool(final_text="ok")


class _Recorder:
    def write_run_record(self, run_id, record):
        pass


async def test_the_result_comes_back_to_the_drawer(notebook, tmp_path) -> None:
    _ws, root = notebook
    store = tmp_path / "jobs.json"
    await _tool(store).execute(action="add", card="annunci", message="cerca annunci",
                               every_seconds=3600, mode="monitor")
    job = CronService(store).list_jobs()[0]
    agent = _Agent("3 annunci nuovi in via Roma")
    await run_bound_cron_job(job, agent=agent, cron=_Recorder())
    assert agent.received[0].session_key_override == notebook_cron_session_key("casa", job.id)
    board = read_board(root)
    result = board["cronResults"][f"annunci/{job.payload.board['section']}"]
    assert result["text"] == "3 annunci nuovi in via Roma" and result["status"] == "ok"
    assert board["since"][-1]["kind"] == "cron_result"


async def test_a_cron_whose_card_is_gone_does_not_run(notebook, tmp_path) -> None:
    _ws, root = notebook
    store = tmp_path / "jobs.json"
    await _tool(store).execute(action="add", card="annunci", message="cerca", every_seconds=3600)
    job = CronService(store).list_jobs()[0]
    mutate_board(root, lambda b: b.__setitem__("cards", []))
    with pytest.raises(ValueError, match="notebook gone"):
        await run_bound_cron_job(job, agent=_Agent("x"), cron=_Recorder())
