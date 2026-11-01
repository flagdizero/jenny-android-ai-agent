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
    assert "on the card «Annunci»" in out
    job = CronService(store).list_jobs()[0]
    # Nessuna sezione nel cassetto: la bacheca lo deriva dal legame del job.
    assert read_board(root)["cards"][0]["userSections"] == []
    assert job.payload.board == {"notebook": "casa", "card": "annunci", "section": "@auto"}
    # Consegna nella chat del quaderno.
    assert (job.payload.origin_channel, job.payload.origin_chat_id) == ("websocket", "project:casa")
    listed = await tool.execute(action="list")
    assert job.id in listed and "on the card `annunci`" in listed


async def test_without_a_card_the_job_is_for_the_whole_notebook(notebook, tmp_path) -> None:
    """Jenny sceglie, e non chiede: senza scheda il cron è di tutto il quaderno."""
    store = tmp_path / "jobs.json"
    tool = _tool(store)
    out = await tool.execute(action="add", message="guarda se gela", every_seconds=3600)
    assert "for the whole notebook" in out
    job = CronService(store).list_jobs()[0]
    assert job.payload.board == {"notebook": "casa", "card": "@notebook", "section": "@auto"}
    assert "for the whole notebook" in await tool.execute(action="list")


async def test_a_card_that_is_not_there_is_named_back(notebook, tmp_path) -> None:
    store = tmp_path / "jobs.json"
    out = await _tool(store).execute(action="add", card="Cantina", message="x",
                                     every_seconds=3600)
    assert "No card «Cantina»" in out and "«Annunci»" in out and "empty" in out
    assert CronService(store).list_jobs() == []


async def test_a_board_without_cards_takes_a_whole_notebook_cron(notebook, tmp_path) -> None:
    _ws, root = notebook
    mutate_board(root, lambda b: b.__setitem__("cards", []))
    store = tmp_path / "jobs.json"
    out = await _tool(store).execute(action="add", message="x", every_seconds=3600)
    assert out.startswith("Created job") and "for the whole notebook" in out


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


async def test_a_notebook_without_a_board_takes_a_job_for_the_whole_notebook(
    tmp_path, monkeypatch,
) -> None:
    root = tmp_path / "workspace" / "wikis" / "vuoto"
    (root / "wiki").mkdir(parents=True)
    monkeypatch.setattr("jenny.board.cron_link.notebook_root",
                        lambda name: root if name == "vuoto" else None)
    tool = _tool(tmp_path / "jobs.json", "project:vuoto")
    token = bind_workspace_scope(build_workspace_scope(root, "restricted"))
    try:
        out = await tool.execute(action="add", message="x", every_seconds=60)
    finally:
        reset_workspace_scope(token)
    assert out.startswith("Created job"), out
    job = tool._cron.list_jobs()[0]
    assert job.payload.board["notebook"] == "vuoto"


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
    result = board["cronResults"][job.id]
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


async def test_a_whole_notebook_monitor_keeps_the_notebook_fence(notebook, tmp_path) -> None:
    """Il segnaposto del quaderno non fa del job un cron personale: il turno resta nel recinto."""
    from jenny.board import cron_link, model
    from jenny.cron.types import CronSchedule

    service = CronService(tmp_path / "jobs.json")
    job = service.add_job("annunci", CronSchedule(kind="every", every_ms=3_600_000), "cerca",
                          session_key="project:casa", origin_channel="websocket",
                          origin_chat_id="project:casa", mode="monitor",
                          board=cron_link.make_link("casa", model.NOTEBOOK))
    agent = _Agent("niente di nuovo")
    await run_bound_cron_job(job, agent=agent, cron=_Recorder())
    assert agent.received[0].session_key_override == notebook_cron_session_key("casa", job.id)


async def test_a_run_tells_the_open_board(notebook, tmp_path, monkeypatch) -> None:
    """L'ultimo giro sta sull'etichetta: la bacheca aperta si ridisegna dopo ogni giro."""
    told: list[str] = []

    async def note(name):
        told.append(name)

    monkeypatch.setattr("jenny.board.notify.board_changed", note)
    store = tmp_path / "jobs.json"
    await _tool(store).execute(action="add", card="annunci", message="cerca", every_seconds=3600)
    job = CronService(store).list_jobs()[0]
    told.clear()
    await run_bound_cron_job(job, agent=_Agent("x"), cron=_Recorder())
    assert told == ["casa"]


def test_an_old_result_key_is_read_and_moved_on_the_next_run(notebook) -> None:
    from jenny.board import cron_link

    _ws, root = notebook
    old = {"notebook": "casa", "card": "annunci", "section": "s-cron"}
    mutate_board(root, lambda b: b.__setitem__("cronResults", {
        "annunci/s-cron": {"at": "2026-10-01T09:00:00+02:00", "status": "ok", "text": "vecchio"}}))
    assert cron_link.result_of(read_board(root)["cronResults"], "j1", old)["text"] == "vecchio"
    cron_link.record_result(old, job_id="j1", status="silent", text="", job_name="Annunci")
    results = read_board(root)["cronResults"]
    assert "annunci/s-cron" not in results
    # Un giro muto non cancella l'ultimo esito che diceva qualcosa.
    assert results["j1"]["text"] == "vecchio" and results["j1"]["status"] == "silent"


def test_a_whole_notebook_result_has_its_own_line(notebook) -> None:
    from jenny.board import cron_link, model

    _ws, root = notebook
    link = cron_link.make_link("casa", model.NOTEBOOK)
    cron_link.record_result(link, job_id="j2", status="ok", text="gelo stanotte", job_name="Gelo")
    board = read_board(root)
    assert board["cronResults"]["j2"]["text"] == "gelo stanotte"
    assert board["since"][-1]["kind"] == "cron_result_notebook"
    assert board["since"][-1]["app"] == "Gelo" and "card" not in board["since"][-1]


async def test_removing_a_notebook_cron_from_anywhere_cleans_its_board(notebook, tmp_path) -> None:
    """Anche dalla chat personale o dall'officina: niente sezioni orfane nel cassetto."""
    from jenny.cron.types import CronSchedule

    _ws, root = notebook
    store = tmp_path / "jobs.json"
    service = CronService(store)
    old = {"notebook": "casa", "card": "annunci", "section": "s-cron"}
    job = service.add_job("annunci", CronSchedule(kind="every", every_ms=3_600_000), "cerca",
                          session_key="project:casa", board=old)
    mutate_board(root, lambda b: (
        b["cards"][0]["userSections"].append({"id": "s-cron", "kind": "cron", "job": job.id}),
        b.__setitem__("cronResults", {"annunci/s-cron": {"at": "x", "status": "ok"}})))
    personal = _tool(store, key="unified:default")
    out = await personal.execute(action="remove", job_id=job.id)
    assert out == f"Removed job {job.id}"
    board = read_board(root)
    assert board["cards"][0]["userSections"] == [] and board["cronResults"] == {}
