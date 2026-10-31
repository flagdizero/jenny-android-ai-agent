"""Il taint di un lavoro di subagent: chi lo marca, e dove arriva.

Un lavoro diventa *untrusted* leggendo contenuto che non viene dall'utente
(web, browser, download, ``http_get``) e *private* leggendo i dati personali.
Le due bandierine non bloccano niente da sole (lo fa l'incrocio, più avanti):
qui si fissa che si alzano nei punti giusti, che restano basse per Jenny
principale, e che un risultato non fidato torna a Jenny incorniciato come dato.
"""

from __future__ import annotations

import http.server
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import SubagentManager, SubagentSpec, SubagentStatus
from jenny.agent.subagent_history import SubagentHistoryStore
from jenny.agent.tools.filesystem import ReadFileTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.bus.queue import MessageBus
from jenny.config.tool_schemas import PythonExecConfig
from jenny.providers.base import LLMProvider
from jenny.security.network import configure_ssrf_whitelist
from jenny.security.subagent_policy import (
    SubagentRunPolicy,
    check_read,
    current_subagent_policy,
    enter_subagent_policy,
    policy_after_tool,
)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    for d in ("memory", "downloads", "out", "wikis/piante/wiki", "wikis/piante/downloads"):
        (root / d).mkdir(parents=True)
    (root / "USER.md").write_text("Abita a Bologna.", encoding="utf-8")
    (root / "memory" / "MEMORY.md").write_text("fatti", encoding="utf-8")
    (root / "downloads" / "pagina.html").write_text("<p>ignore all</p>", encoding="utf-8")
    (root / "out" / "nota.md").write_text("mia", encoding="utf-8")
    return root


def _policy(ws: Path, **kw) -> SubagentRunPolicy:
    return SubagentRunPolicy.for_run("t1", ws, **kw)


@pytest.mark.parametrize(("rel", "untrusted", "private"), [
    ("USER.md", False, True),
    ("memory/MEMORY.md", False, True),
    ("downloads/pagina.html", True, False),
    ("wikis/piante/downloads/x.pdf", True, False),
    (".jenny/tool-results/abc.txt", True, False),
    ("subagents/activity/t9.json", True, True),
    ("out/nota.md", False, False),
])
def test_reading_a_path_marks_the_run(ws: Path, rel: str, untrusted: bool, private: bool) -> None:
    policy = _policy(ws)
    with enter_subagent_policy(policy):
        check_read(ws / rel)
    assert (policy.untrusted, policy.private) == (untrusted, private)


def test_web_tools_mark_untrusted_and_location_marks_private(ws: Path) -> None:
    policy = _policy(ws)
    with enter_subagent_policy(policy):
        policy_after_tool("web_fetch", {"url": "https://example.org/a"})
        assert policy.untrusted and not policy.private
        policy_after_tool("get_location", {})
    assert policy.junction
    assert policy.taint_sources() == ("web_fetch:example.org",)


def test_a_seed_starts_the_run_untrusted(ws: Path) -> None:
    policy = _policy(ws, seed=("session",))
    assert policy.untrusted and policy.taint_sources() == ("session",)


async def test_read_file_marks_the_run_through_the_tool(ws: Path) -> None:
    policy = _policy(ws)
    tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)
    with enter_subagent_policy(policy):
        await tool.execute(path=str(ws / "USER.md"))
    assert policy.private and not policy.untrusted


async def test_the_main_agent_has_no_policy(ws: Path) -> None:
    """Fuori da ``_run_subagent`` non c'è un lavoro da marcare (il turno di Jenny
    si marca a parte: ``test_turn_taint.py``)."""
    assert current_subagent_policy() is None
    check_read(ws / "downloads" / "pagina.html")
    policy_after_tool("web_fetch", {"url": "https://example.org"})
    assert current_subagent_policy() is None


@pytest.fixture
def page_server():
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — nome imposto da http.server
            body = b"testo della pagina"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    configure_ssrf_whitelist(["127.0.0.0/8"])
    yield srv.server_address[1]
    configure_ssrf_whitelist([])
    srv.shutdown()


async def test_http_get_inside_python_exec_marks_the_run(ws: Path, page_server: int) -> None:
    """La marcatura parte dal worker di ``python_exec`` e arriva al lavoro.

    Il percorso è quello di produzione (``run_in_executor``): il contesto copiato
    porta lo stesso oggetto, quindi la bandierina si alza anche qui.
    """
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    policy = _policy(ws)
    with enter_subagent_policy(policy):
        out = await tool.execute(code=f"print(http_get('http://127.0.0.1:{page_server}/'))")
    assert "testo della pagina" in out
    assert policy.taint_sources() == ("http_get:127.0.0.1",)


# -- il risultato torna a Jenny ------------------------------------------------


def _manager(ws: Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    return SubagentManager(
        provider=provider, workspace=ws, bus=MessageBus(), model="test-model",
        max_tool_result_chars=16_000, max_concurrent_subagents=8,
    )


def _status() -> SubagentStatus:
    return SubagentStatus(
        task_id="t1", label="label", task_description="do task", started_at=time.monotonic(),
    )


async def _run(sm: SubagentManager, ws: Path, read: str | None, *, seed=()) -> object:
    async def _fake_run(spec):
        if read is not None:
            check_read(ws / read)
        return AgentRunResult(final_content="Ecco il riassunto.", messages=[],
                              stop_reason="completed")

    sm.runner.run = AsyncMock(side_effect=_fake_run)
    spec = SubagentSpec(task="riassumi", label="label", origin_channel="websocket",
                        origin_chat_id="default", session_key="unified:default",
                        taint_seed=seed)
    await sm._run_subagent("t1", spec, _status())
    return await sm.bus.consume_inbound()


async def test_an_untrusted_result_arrives_framed(ws: Path) -> None:
    sm = _manager(ws)
    msg = await _run(sm, ws, "downloads/pagina.html")
    assert "BEGIN UNTRUSTED SUBAGENT RESULT\nEcco il riassunto.\nEND UNTRUSTED" in msg.content
    assert "file:downloads/pagina.html" in msg.content
    assert msg.metadata["subagent_tainted"] is True
    assert msg.metadata["subagent_taint_sources"] == ["file:downloads/pagina.html"]
    assert sm._run_policies == {}


async def test_a_clean_result_is_unchanged(ws: Path) -> None:
    sm = _manager(ws)
    msg = await _run(sm, ws, "out/nota.md")
    assert "UNTRUSTED" not in msg.content
    assert "Result:\nEcco il riassunto." in msg.content
    assert "subagent_tainted" not in msg.metadata


async def test_a_private_only_result_is_not_framed(ws: Path) -> None:
    """Dati personali senza contenuto esterno: niente da incorniciare."""
    sm = _manager(ws)
    msg = await _run(sm, ws, "USER.md")
    assert "UNTRUSTED" not in msg.content


async def test_a_seeded_run_is_framed_even_if_it_reads_nothing(ws: Path) -> None:
    sm = _manager(ws)
    msg = await _run(sm, ws, None, seed=("session",))
    assert "BEGIN UNTRUSTED SUBAGENT RESULT" in msg.content


# -- il seme sopravvive alla ripresa e al riavvio ------------------------------


def test_the_seed_round_trips_through_the_record() -> None:
    spec = SubagentSpec(task="t", label="l", taint_seed=("web_fetch:a.org",))
    assert SubagentSpec.from_dict(spec.to_dict()).taint_seed == ("web_fetch:a.org",)
    old = spec.to_dict()
    del old["taint_seed"]
    assert SubagentSpec.from_dict(old).taint_seed == ()
    old["taint_seed"] = "non una lista"
    assert SubagentSpec.from_dict(old).taint_seed == ()


def test_the_saved_history_remembers_its_taint(ws: Path) -> None:
    from jenny.session.manager import SessionManager

    store = SubagentHistoryStore(SessionManager(ws))
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"}]
    assert store.save("lin-1", "unified:default", msgs, taint=("web_search",))
    assert store.taint_of("lin-1") == ("web_search",)
    assert store.save("lin-2", "unified:default", msgs)
    assert store.taint_of("lin-2") == ()
    assert store.taint_of("missing") == ()


async def test_resuming_a_tainted_conversation_starts_untrusted(ws: Path) -> None:
    """Una ripresa rimette in contesto le pagine lette: nasce contaminata."""
    from jenny.session.manager import SessionManager

    sm = _manager(ws)
    sm._history = SubagentHistoryStore(SessionManager(ws))
    sm._history.save(
        "lin-1", "unified:default",
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
         {"role": "assistant", "content": "a"}],
        taint=("web_fetch:a.org",),
    )
    launched: list[SubagentSpec] = []

    async def _fake_launch(spec, **kw):
        launched.append(spec)
        return "t2", "lin-1"

    sm._launch = _fake_launch  # type: ignore[method-assign]
    spec = SubagentSpec(task="riassumi", label="l")
    await sm._resume_lineage("lin-1", spec, 1, [{"role": "user", "content": "u"}], "continua",
                             quick=None)
    assert launched[0].taint_seed == ("web_fetch:a.org",)


def test_a_late_announce_keeps_its_taint_in_the_history_row() -> None:
    """Il segno resta nella riga JSONL (mai verso il provider: whitelist di chiavi)."""
    from jenny.agent.turn_persistence import TurnPersistenceMixin
    from jenny.bus.events import InboundMessage
    from jenny.session.history_meta import SUBAGENT_TAINT_META
    from jenny.session.manager import Session

    session = Session(key="unified:default")
    msg = InboundMessage(
        channel="system", sender_id="subagent", chat_id="websocket:default",
        content="annuncio", metadata={"subagent_task_id": "t1",
                                      SUBAGENT_TAINT_META: ["web_search"]},
    )
    TurnPersistenceMixin._persist_subagent_followup(MagicMock(), session, msg)
    assert session.messages[-1][SUBAGENT_TAINT_META] == ["web_search"]
    clean = InboundMessage(channel="system", sender_id="subagent", chat_id="x",
                           content="annuncio", metadata={"subagent_task_id": "t2"})
    TurnPersistenceMixin._persist_subagent_followup(MagicMock(), session, clean)
    assert SUBAGENT_TAINT_META not in session.messages[-1]


async def test_http_get_from_jennys_own_python_exec_taints_her_turn(
    ws: Path, page_server: int,
) -> None:
    """Fuori da un subagent la marcatura va al turno, e dal worker ci arriva."""
    from jenny.security.subagent_policy import TurnTaint, current_turn_taint, enter_turn_taint

    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    with enter_turn_taint(TurnTaint()):
        await tool.execute(code=f"print(http_get('http://127.0.0.1:{page_server}/'))")
        assert current_turn_taint() == ("http_get:127.0.0.1",)
