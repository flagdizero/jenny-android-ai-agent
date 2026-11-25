"""Il registro di un turno ordinario e la sua riga (``agent.turn_work``).

Un turno con un id del filo che scrive ha il registro ``turn-<id>`` e una riga
in ``undo.sqlite``; uno fermo non lascia niente, nemmeno un registro vivo in
memoria. I turni dei piani e delle richieste fra Jenny restano nei registri
loro. Gli aiutanti e i promemoria nati dal turno stanno nella sua riga; dopo
14 giorni l'elenco resta, congelato, e i registri se ne vanno.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from support.agent import make_loop

from jenny import db as jdb
from jenny.agent import turn_work, write_journal
from jenny.agent.loop_plan import PLAN_ID_META, PLAN_STEP_META
from jenny.agent.subagent import SubagentManager
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.agent.write_journal import (
    bind_journal,
    journal_entries,
    journal_for,
    journal_key,
    live_journal,
)
from jenny.bus.events import WEBUI_TURN_METADATA_KEY
from jenny.bus.queue import MessageBus
from jenny.cron.service import CronService
from jenny.peers import gate
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.security.subagent_policy import check_write, settle_writes
from jenny.session.history_meta import INJECTED_EVENT_META
from jenny.session.keys import UNIFIED_SESSION_KEY
from tests.support.aio import wait_until
from tests.support.subagent_provider_fakes import fake_provider

TURN = "t-0001"


@pytest.fixture(autouse=True)
def _close_databases():
    yield
    jdb.close_all()


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text("prima", encoding="utf-8")
    return root


def _write(ws: Path, rel: str, text: str) -> None:
    """Una scrittura che passa dal recinto, come quelle degli strumenti."""
    path = ws / rel
    check_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    settle_writes("write_file")


# -- il turno, nel loop vero ------------------------------------------------------------


@tool_parameters(tool_parameters_schema(
    path=StringSchema("p"), content=StringSchema("c"), required=["path", "content"]))
class _Scrivi(Tool):
    """Uno strumento che scrive passando dal recinto, come quelli veri.

    In modalità orchestratore (il default) Jenny non ha ``write_file``: scrive
    con gli strumenti dei quaderni, che passano dallo stesso recinto.
    """

    def __init__(self, ws: Path) -> None:
        self._ws = ws

    @property
    def name(self) -> str:
        return "scrivi"

    @property
    def description(self) -> str:
        return "write"

    async def execute(self, path: str = "", content: str = "", **_kw: Any) -> str:
        target = self._ws / path
        check_write(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return "ok"


def _loop(ws: Path, *responses: LLMResponse):
    loop = make_loop(ws, bare=True, model=None, context_window_tokens=None)
    loop.provider.chat_with_retry = AsyncMock(side_effect=list(responses))
    loop.tools.register(_Scrivi(ws))
    return loop


def _writes(path: str = "notes/a.md") -> list[LLMResponse]:
    return [
        LLMResponse(content="", finish_reason="tool_calls", tool_calls=[ToolCallRequest(
            id="c1", name="scrivi", arguments={"path": path, "content": "dopo"})]),
        LLMResponse(content="Fatto.", finish_reason="stop"),
    ]


async def _turn(loop, ws: Path, metadata: dict) -> None:
    await loop._run_agent_loop(
        [{"role": "user", "content": "scrivi"}], channel="websocket", chat_id="default",
        session_key=UNIFIED_SESSION_KEY, metadata=metadata)


async def test_a_plain_turn_that_writes_gets_its_journal_and_a_row(ws: Path) -> None:
    loop = _loop(ws, *_writes())
    await _turn(loop, ws, {WEBUI_TURN_METADATA_KEY: TURN})
    assert (ws / "notes" / "a.md").read_text(encoding="utf-8") == "dopo"
    [entry] = journal_entries(ws, turn_work.turn_journal_key(TURN))
    assert entry["path"] == "notes/a.md" and entry["tool"] == "scrivi"
    row = turn_work.read_row(ws, TURN)
    assert row is not None
    assert (row["session"], row["chat"], row["state"]) == (
        UNIFIED_SESSION_KEY, "websocket:default", "open")
    # Finito il turno, il registro non resta vivo in memoria.
    assert live_journal(ws, turn_work.turn_journal_key(TURN)) is None
    assert turn_work.turn_items(ws, TURN) == [
        {"kind": "file", "label": "notes/a.md", "undoable": True}]


async def test_an_idle_turn_leaves_no_row_and_no_live_journal(ws: Path) -> None:
    loop = _loop(ws, LLMResponse(content="Ciao!", finish_reason="stop"))
    await _turn(loop, ws, {WEBUI_TURN_METADATA_KEY: TURN})
    assert turn_work.read_row(ws, TURN) is None
    assert journal_entries(ws, turn_work.turn_journal_key(TURN)) == []
    assert live_journal(ws, turn_work.turn_journal_key(TURN)) is None


async def test_a_turn_without_an_id_binds_no_journal(ws: Path) -> None:
    """Una risposta da una notifica o dalla mascotte: nessun turno del verbale."""
    loop = _loop(ws, *_writes())
    await _turn(loop, ws, {})
    assert (ws / "notes" / "a.md").read_text(encoding="utf-8") == "dopo"
    keys = [r["key"] for r in write_journal._undo_db(ws).read("SELECT key FROM journals")]
    assert keys == []


async def test_a_plan_step_turn_writes_in_the_plan_journal_only(ws: Path) -> None:
    loop = _loop(ws, *_writes())
    # Del driver serve solo dove sta il registro del piano; la chat non ha un
    # posto di piano (``place_of``), così il resto del loop non lo guarda.
    loop.plan_driver = SimpleNamespace(store=SimpleNamespace(workspace=ws),
                                       place_of=lambda _key: None)
    await _turn(loop, ws, {
        WEBUI_TURN_METADATA_KEY: TURN, PLAN_ID_META: "p_000000000001",
        PLAN_STEP_META: "s1", INJECTED_EVENT_META: "plan_step",
    })
    from jenny.plan.work import plan_journal_key

    [entry] = journal_entries(ws, plan_journal_key("p_000000000001"))
    assert entry["step"] == "s1"
    assert journal_entries(ws, turn_work.turn_journal_key(TURN)) == []
    assert turn_work.read_row(ws, TURN) is None


async def test_a_peer_request_turn_writes_in_the_request_journal_only(ws: Path) -> None:
    act = {"peer": "a" * 32, "qid": "b" * 32}
    loop = _loop(ws, *_writes())
    await _turn(loop, ws, {WEBUI_TURN_METADATA_KEY: TURN, gate.PEER_ACT_META: act})
    assert journal_entries(ws, journal_key("peer", act["qid"]))
    assert journal_entries(ws, turn_work.turn_journal_key(TURN)) == []
    assert turn_work.read_row(ws, TURN) is None


def test_an_id_that_is_not_a_transcript_id_is_no_turn() -> None:
    assert turn_work.turn_id_of({WEBUI_TURN_METADATA_KEY: "a/../b"}) is None
    assert turn_work.turn_id_of({WEBUI_TURN_METADATA_KEY: "x" * 129}) is None
    assert turn_work.turn_id_of({WEBUI_TURN_METADATA_KEY: "system:ab12"}) == "system:ab12"


# -- gli aiutanti -------------------------------------------------------------------------


def _manager(ws: Path, provider) -> SubagentManager:
    return SubagentManager(provider=provider, workspace=ws, bus=MessageBus(), model="m",
                           max_tool_result_chars=16000, max_iterations=10)


async def test_a_helper_launched_by_the_turn_is_listed_under_it(ws: Path) -> None:
    import asyncio

    release = asyncio.Event()

    calls = {"n": 0}

    async def _slow_then_write(**_kw):
        calls["n"] += 1
        if calls["n"] > 1:
            return LLMResponse(content="Fatto.", finish_reason="stop")
        await release.wait()
        return LLMResponse(content="", finish_reason="tool_calls", tool_calls=[ToolCallRequest(
            id="h1", name="write_file", arguments={"path": "notes/nuova.md", "content": "x"})])

    provider = fake_provider(_slow_then_write)
    manager = _manager(ws, provider)
    with turn_work.bound(turn_work.ref_for(ws, TURN, session=UNIFIED_SESSION_KEY)):
        await manager.spawn("scrivi una pagina", session_key=UNIFIED_SESSION_KEY,
                            origin_channel="websocket", origin_chat_id="default")
    await wait_until(lambda: turn_work.read_row(ws, TURN) is not None, timeout=5.0)
    assert manager.working_for_turn(TURN) and not manager.working_for_turn("altro")
    release.set()
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    assert not manager.working_for_turn(TURN)
    [record] = manager.list_records(UNIFIED_SESSION_KEY)
    assert record.spec.origin_turn == TURN
    row = turn_work.read_row(ws, TURN)
    assert row is not None and row["journals"] == [journal_key("helper", record.lineage_id)]
    assert turn_work.turn_items(ws, TURN) == [
        {"kind": "file", "label": "notes/nuova.md", "undoable": True}]
    assert turn_work.journals_of(ws, TURN) == [
        turn_work.turn_journal_key(TURN), journal_key("helper", record.lineage_id)]


async def test_a_helper_outside_a_turn_has_no_origin(ws: Path) -> None:
    provider = fake_provider([LLMResponse(content="Fatto.", finish_reason="stop")])
    manager = _manager(ws, provider)
    await manager.spawn("niente", session_key=UNIFIED_SESSION_KEY,
                        origin_channel="websocket", origin_chat_id="default")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    [record] = manager.list_records(UNIFIED_SESSION_KEY)
    assert record.spec.origin_turn is None


# -- i promemoria ------------------------------------------------------------------------


async def test_a_job_made_in_the_turn_is_listed_and_once_fired_cannot_be_undone(
    ws: Path,
) -> None:
    service = CronService(ws)
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(RequestContext(channel="websocket", chat_id="default",
                                    session_key=UNIFIED_SESSION_KEY))
    with turn_work.bound(turn_work.ref_for(ws, TURN, session=UNIFIED_SESSION_KEY)):
        out = await tool.execute(action="add", name="Chiamare Anna",
                                 message="chiamare Anna", date="2030-01-04", time="09:00")
    assert out.startswith("Created")
    [job] = service.list_jobs(include_disabled=True)
    items = turn_work.turn_items(ws, TURN, cron=service)
    assert items == [{"kind": "job", "label": "Chiamare Anna", "undoable": True}]
    # Un singolo che ha suonato sparisce: resta nell'elenco, e non si toglie più.
    assert service.remove_job(job.id) == "removed"
    items = turn_work.turn_items(ws, TURN, cron=service)
    assert items == [{"kind": "job", "label": "Chiamare Anna", "undoable": False}]


async def test_a_job_made_outside_a_turn_is_noted_nowhere(ws: Path) -> None:
    service = CronService(ws)
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(RequestContext(channel="websocket", chat_id="default",
                                    session_key=UNIFIED_SESSION_KEY))
    await tool.execute(action="add", message="x", date="2030-01-04", time="09:00")
    rows = turn_work._db(ws).read("SELECT turn FROM turn_work")
    assert rows == []


# -- la scadenza -------------------------------------------------------------------------


def _old_turn(ws: Path, turn: str, *, age_s: float, helper: str | None = None) -> None:
    ref = turn_work.TurnRef(ws, turn, session=UNIFIED_SESSION_KEY,
                            started_at=time.time() - age_s)
    with bind_journal(journal_for(ws, turn_work.turn_journal_key(turn))):
        _write(ws, f"notes/{turn}.md", "x")
    turn_work.end(ref, journal_for(ws, turn_work.turn_journal_key(turn)))
    if helper is not None:
        turn_work.helper_started(ws, turn, journal_key("helper", helper), helper)


def test_an_expired_turn_keeps_a_frozen_list_and_drops_its_journal(ws: Path) -> None:
    _old_turn(ws, "vecchio", age_s=turn_work.RETENTION_S + 60)
    _old_turn(ws, "nuovo", age_s=60)
    assert turn_work.sweep(ws) == 1
    row = turn_work.read_row(ws, "vecchio")
    assert row is not None and row["state"] == "expired"
    assert row["frozen"] == [{"kind": "file", "label": "notes/vecchio.md", "undoable": False}]
    assert turn_work.turn_items(ws, "vecchio") == row["frozen"]
    assert journal_entries(ws, turn_work.turn_journal_key("vecchio")) == []
    # Il turno recente resta com'era.
    assert turn_work.read_row(ws, "nuovo")["state"] == "open"
    assert journal_entries(ws, turn_work.turn_journal_key("nuovo"))


def test_an_orphan_turn_journal_goes_only_after_the_retention(ws: Path) -> None:
    key = turn_work.turn_journal_key("orfano")
    with bind_journal(journal_for(ws, key)):
        _write(ws, "notes/orfano.md", "x")
    write_journal.release_journal(ws, key)
    turn_work.sweep(ws)
    assert journal_entries(ws, key)
    turn_work.sweep(ws, now=time.time() + turn_work.RETENTION_S + 60)
    assert journal_entries(ws, key) == []


def test_rows_over_the_cap_go_oldest_first(ws: Path, monkeypatch) -> None:
    monkeypatch.setattr(turn_work, "MAX_ROWS", 2)
    for i in range(3):
        ref = turn_work.TurnRef(ws, f"r{i}", started_at=time.time() - 100 + i)
        turn_work._ensure(ref)
    turn_work.sweep(ws)
    turns = sorted(r["turn"] for r in turn_work._db(ws).read("SELECT turn FROM turn_work"))
    assert turns == ["r1", "r2"]


def test_the_sweep_keeps_helper_journals_a_live_turn_names(ws: Path) -> None:
    """Il record dell'aiutante è potato, la riga del turno no: il suo registro resta."""
    for lineage in ("del-turno", "orfano"):
        with bind_journal(journal_for(ws, journal_key("helper", lineage))):
            _write(ws, f"notes/{lineage}.md", "x")
    _old_turn(ws, "vivo", age_s=60, helper="del-turno")
    _old_turn(ws, "scaduto", age_s=turn_work.RETENTION_S + 60, helper="orfano")
    manager = SubagentManager(provider=fake_provider([]), workspace=ws, bus=MessageBus(),
                              model="m", max_tool_result_chars=16000)
    assert manager is not None
    assert journal_entries(ws, journal_key("helper", "del-turno"))
    assert journal_entries(ws, journal_key("helper", "orfano")) == []
    assert turn_work.read_row(ws, "scaduto")["state"] == "expired"


def test_notes_are_deduplicated_and_capped(ws: Path) -> None:
    with turn_work.bound(turn_work.ref_for(ws, TURN)):
        for _ in range(3):
            turn_work.note({"what": "web", "label": "meteo   Bologna"})
        for i in range(turn_work.MAX_ITEMS + 5):
            turn_work.note({"what": "web", "label": f"pagina {i}"})
    row = turn_work.read_row(ws, TURN)
    assert row is not None and len(row["notes"]) == turn_work.MAX_ITEMS
    assert row["notes"][0] == {"kind": "egress", "what": "web", "label": "meteo Bologna",
                               "undoable": False}
    assert json.dumps(row["notes"])  # serializzabile così com'è


# -- quel che non si annulla (``Tool.outside_undo``) ---------------------------------------


@tool_parameters(tool_parameters_schema(content=StringSchema("c"), required=["content"]))
class _Manda(Tool):
    """Uno strumento che fa uscire qualcosa: lo dichiara, come ``message``."""

    @property
    def name(self) -> str:
        return "manda"

    @property
    def description(self) -> str:
        return "send"

    def outside_undo(self, params: dict[str, Any]) -> dict[str, str] | None:
        return {"what": "message", "label": params["content"]}

    async def execute(self, content: str = "", **_kw: Any) -> str:
        return "Error: not delivered" if content == "rifiuta" else "sent"


async def _run_manda(content: str):
    from jenny.agent.runner import AgentRunner, AgentRunSpec
    from jenny.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(_Manda())
    spec = AgentRunSpec(initial_messages=[], tools=registry, model="m", max_iterations=2,
                        max_tool_result_chars=8000)
    return await AgentRunner(provider=None)._run_tool(
        spec, ToolCallRequest(id="c1", name="manda", arguments={"content": content}),
        external_lookup_counts={}, workspace_violation_counts={},
    )


async def test_the_runner_notes_what_a_tool_says_it_cannot_undo(ws: Path) -> None:
    with turn_work.bound(turn_work.ref_for(ws, TURN)):
        await _run_manda("ciao Anna")
        await _run_manda("rifiuta")  # non partito: niente da dire
    await _run_manda("fuori da un turno")
    row = turn_work.read_row(ws, TURN)
    assert row is not None
    assert row["notes"] == [
        {"kind": "egress", "what": "message", "label": "ciao Anna", "undoable": False}]
    assert turn_work.turn_items(ws, TURN) == row["notes"]
    assert [r["turn"] for r in turn_work._db(ws).read("SELECT turn FROM turn_work")] == [TURN]


def test_a_full_list_gives_a_search_s_place_to_a_message(ws: Path) -> None:
    with turn_work.bound(turn_work.ref_for(ws, TURN)):
        for i in range(turn_work.MAX_ITEMS):
            turn_work.note({"what": "web", "label": f"ricerca {i}"})
        turn_work.note({"what": "web", "label": "una di troppo"})
        turn_work.note({"what": "message", "label": "mandato dopo"})
    notes = turn_work.read_row(ws, TURN)["notes"]
    assert len(notes) == turn_work.MAX_ITEMS
    assert notes[-1]["label"] == "mandato dopo"
    labels = [n["label"] for n in notes]
    assert "ricerca 0" not in labels and "una di troppo" not in labels


def _declared(cls: type, params: dict[str, Any]) -> dict[str, str] | None:
    """La dichiarazione di *cls* senza costruirlo: non legge lo stato dello strumento."""
    return cls.outside_undo(object.__new__(cls), params)


def test_the_tools_say_what_leaves_and_reading_is_not_listed() -> None:
    from jenny.agent.tools.android_web import AndroidWebFetchTool, AndroidWebSearchTool
    from jenny.agent.tools.app_update import InstallUpdateTool
    from jenny.agent.tools.message import MessageTool
    from jenny.agent.tools.peer import PeerAskTool, PeerLineTool
    from jenny.agent.tools.phone import PhoneDoTool
    from jenny.agent.tools.self import MyTool
    from jenny.agent.tools.ssh import SshExecTool, SshJobTool, SshTransferTool

    assert _declared(MessageTool, {"content": "ciao"}) == {"what": "message", "label": "ciao"}
    assert _declared(AndroidWebSearchTool, {"query": "meteo"}) == {"what": "web",
                                                                   "label": "meteo"}
    assert _declared(AndroidWebFetchTool, {"url": "https://x.example"}) == {
        "what": "web", "label": "https://x.example"}
    assert _declared(PhoneDoTool, {"steps": [{"action": "open_app", "app": "Orari"}]}) == {
        "what": "phone", "label": "Orari"}
    assert _declared(PeerAskTool, {"peer": "Lavoro", "question": "come va?"}) == {
        "what": "peer", "label": "come va?"}
    assert _declared(PeerLineTool, {"peer": "Lavoro"}) == {"what": "peer", "label": "Lavoro"}
    assert _declared(SshExecTool, {"host": "nas", "command": "df -h"}) == {
        "what": "ssh", "label": "nas: df -h"}
    assert _declared(SshJobTool, {"host": "nas", "action": "poll", "job_id": "j1"}) is None
    assert _declared(SshJobTool, {"host": "nas", "action": "start", "command": "make"}) == {
        "what": "ssh", "label": "nas: start make"}
    assert _declared(SshTransferTool, {"host": "nas", "direction": "down",
                                       "remote_path": "/a"}) is None
    assert _declared(SshTransferTool, {"host": "nas", "direction": "up",
                                       "remote_path": "/a"}) == {"what": "ssh",
                                                                 "label": "nas: /a"}
    assert _declared(MyTool, {"action": "check", "key": "model"}) is None
    assert _declared(MyTool, {"action": "set", "key": "model_preset"}) == {
        "what": "settings", "label": "model_preset"}
    assert _declared(InstallUpdateTool, {"confirm": False}) is None
