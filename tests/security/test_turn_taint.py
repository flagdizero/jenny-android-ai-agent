"""Il taint passa dal turno di Jenny ai lavori che avvia, senza bloccarla.

Il risultato di un lavoro non fidato entra nella conversazione: da lì può
guidare il compito del prossimo subagent, un messaggio a un lavoro vivo, un
promemoria che girerà da solo. Finché quella riga è nella parte non
consolidata della sessione, ciò che Jenny avvia nasce contaminato.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from jenny.agent.subagent import SubagentManager, SubagentSpec
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.security.subagent_policy import (
    SubagentRunPolicy,
    current_turn_taint,
    enter_turn_taint,
    note_turn_taint,
    turn_taint_for,
)
from jenny.session.history_meta import SUBAGENT_TAINT_META
from jenny.session.manager import Session


def _session_with_tainted_row() -> Session:
    session = Session(key="unified:default")
    session.add_message("user", "ciao")
    session.add_message("user", "annuncio", injected_event="subagent_result",
                        **{SUBAGENT_TAINT_META: ["web_fetch:evil.example"]})
    session.add_message("assistant", "ecco")
    return session


def test_a_tainted_row_in_view_taints_the_turn() -> None:
    session = _session_with_tainted_row()
    taint = turn_taint_for(session.messages[session.last_consolidated:], {}, SUBAGENT_TAINT_META)
    assert taint.sources == ["web_fetch:evil.example"]


def test_the_taint_expires_once_the_row_is_consolidated() -> None:
    session = _session_with_tainted_row()
    session.last_consolidated = len(session.messages)
    taint = turn_taint_for(session.messages[session.last_consolidated:], {}, SUBAGENT_TAINT_META)
    assert taint.sources == []


def test_a_cron_turn_carries_the_taint_of_its_job() -> None:
    taint = turn_taint_for([], {"_taint": ["web_search"]}, SUBAGENT_TAINT_META)
    assert taint.sources == ["web_search"]


def test_a_mid_turn_result_joins_the_turn() -> None:
    taint = turn_taint_for([], {}, SUBAGENT_TAINT_META)
    with enter_turn_taint(taint):
        assert current_turn_taint() == ()
        note_turn_taint(["browser_read:x.org"])
        assert current_turn_taint() == ("browser_read:x.org",)
    assert current_turn_taint() == ()


def _manager(ws: Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "m"
    return SubagentManager(provider=provider, workspace=ws, bus=MessageBus(), model="m",
                           max_tool_result_chars=16_000, max_concurrent_subagents=8)


async def test_a_spawn_from_a_tainted_turn_is_born_untrusted(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    seen: list[SubagentSpec] = []

    async def _fake_spawn_spec(spec, **kw):
        seen.append(spec)
        return "ok"

    sm._spawn_spec = _fake_spawn_spec  # type: ignore[method-assign]
    with enter_turn_taint(turn_taint_for([], {"_taint": ["web_search"]}, SUBAGENT_TAINT_META)):
        await sm.spawn("scrivi una skill da quel che hai trovato")
    await sm.spawn("un compito pulito")
    assert seen[0].taint_seed == ("web_search",)
    assert seen[1].taint_seed == ()


async def test_a_message_to_a_live_job_from_a_tainted_turn_taints_it(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    policy = SubagentRunPolicy.for_run("t1", tmp_path, task="")
    sm._run_policies["t1"] = policy
    sm._live_task_for = lambda target: "t1"  # type: ignore[method-assign]
    sm._task_statuses["t1"] = MagicMock(state="running", label="l")
    sm._pending_injections["t1"] = asyncio.Queue()
    sm._enqueue_injection = MagicMock()  # type: ignore[method-assign]
    with enter_turn_taint(turn_taint_for([], {"_taint": ["web_search"]}, SUBAGENT_TAINT_META)):
        result = await sm.send("t1", "fai anche questo")
    assert result.mode == "injected"
    assert policy.taint_sources() == ("web_search",)


async def test_a_reminder_made_in_a_tainted_turn_keeps_the_taint(tmp_path: Path) -> None:
    from jenny.agent.tools.cron import CronTool
    from jenny.cron.service import CronService

    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)
    tool.set_context(MagicMock(channel="websocket", chat_id="default",
                               session_key="unified:default", metadata={}))
    with enter_turn_taint(turn_taint_for([], {"_taint": ["web_fetch:a.org"]},
                                         SUBAGENT_TAINT_META)):
        out = await tool.execute(action="add", message="controlla il sito", every_seconds=3600)
    assert "Created job" in out, out
    [job] = service.list_jobs(include_disabled=True)
    assert job.payload.origin_metadata["_taint"] == ["web_fetch:a.org"]


async def test_a_reminder_from_a_clean_turn_has_no_taint(tmp_path: Path) -> None:
    from jenny.agent.tools.cron import CronTool
    from jenny.cron.service import CronService

    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)
    tool.set_context(MagicMock(channel="websocket", chat_id="default",
                               session_key="unified:default", metadata={}))
    out = await tool.execute(action="add", message="bevi acqua", every_seconds=3600)
    assert "Created job" in out, out
    [job] = service.list_jobs(include_disabled=True)
    assert "_taint" not in job.payload.origin_metadata


def test_the_loop_binds_the_turn_taint() -> None:
    """Il punto di legame esiste ed è sciolto nel ``finally`` (lettura del sorgente:
    il turno vero è coperto dai test sopra, il legame qui)."""
    source = Path("jenny/agent/loop.py").read_text(encoding="utf-8")
    assert "turn_taint_for(in_view, metadata, SUBAGENT_TAINT_META)" in source
    assert "reset_turn_taint(turn_taint_token)" in source
