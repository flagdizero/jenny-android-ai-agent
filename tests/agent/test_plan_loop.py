"""Il piano a lista dentro il loop vero: tool visibili, turni di passo, la catena intera.

I test del driver (``tests/plan/test_driver.py``) provano gli anelli con un ospite
finto. Qui c'è l'``AgentLoop`` vero con un modello scritto a copione: il turno di
pianificazione propone, l'Approva apre il passo 1, ogni turno di passo chiude il
suo, il gancio di fine turno apre il seguente, e il piano arriva a ``done`` con
le sue foto. È la catena che nessun test degli anelli vede.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.plan_host import LoopPlanHost
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.plan.driver import PLAN_STEP_TOOLS, PlanDriver
from jenny.plan.store import PlanStore
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session.goal_state import set_plan_probe
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT
from tests.support.aio import wait_until

KEY = "project:piante"
PLAN_TOOLS = {"plan_propose", *PLAN_STEP_TOOLS}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    (nb / "wiki").mkdir(parents=True)
    (nb / "wiki" / "index.md").write_text("# Piante\n", encoding="utf-8")
    (nb / "AGENTS.md").write_text("# piante\n", encoding="utf-8")
    return ws


@pytest.fixture(autouse=True)
def _no_probe_left_behind():
    yield
    set_plan_probe(None)


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id=f"c-{name}", name=name, arguments=arguments)],
    )


class Script:
    """Il modello a copione: legge l'ultimo messaggio e fa la mossa del piano."""

    def __init__(self, workspace: Path, *, close_steps: bool = True) -> None:
        self.workspace = workspace
        self.close_steps = close_steps
        self.offered: list[set[str]] = []
        self.prompts: list[str] = []
        self.systems: list[str] = []
        # Un passo che non muore al ``cancel``: il loop lo abbandona, e lui resta.
        self.hang_steps = False
        self.release = asyncio.Event()

    async def __call__(self, **kw: Any) -> LLMResponse:
        messages = kw.get("messages") or []
        self.offered.append(
            {d.get("function", d).get("name") for d in (kw.get("tools") or [])}
        )
        if messages and messages[0].get("role") == "system":
            self.systems.append(_text(messages[0]))
        last = messages[-1]
        if last.get("role") == "tool":
            return LLMResponse(content="Fatto.")
        text = _text(last)
        self.prompts.append(text)
        if "[Planning turn]" in text:
            return _call(
                "plan_propose",
                title="Rifare la wiki",
                steps=[
                    {"title": "Scrivo acero", "detail": "wiki/acero.md", "kind": "write"},
                    {"title": "Scrivo ginkgo", "detail": "wiki/ginkgo.md", "kind": "write"},
                    {"title": "Scrivo indice", "detail": "wiki/index.md", "kind": "write"},
                ],
            )
        found = re.search(r"\[Plan(?: reminder)? — step (\d+) of", text)
        if found and self.hang_steps:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            return LLMResponse(content="Fermata.")
        if found and self.close_steps:
            n = int(found.group(1))
            page = self.workspace / "wikis" / "piante" / "wiki" / f"passo{n}.md"
            page.write_text(f"passo {n}\n", encoding="utf-8")
            return _call("plan_step_done", note=f"passo {n} scritto")
        return LLMResponse(content="Va bene.")


def _loop(workspace: Path, script: Script) -> tuple[AgentLoop, PlanDriver]:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 1024
    provider.chat_with_retry = script
    provider.chat_stream_with_retry = script
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace, model="test-model")
    driver = PlanDriver(PlanStore.for_workspace(workspace), loop.bus, LoopPlanHost(loop))
    loop.attach_plan_driver(driver)
    set_plan_probe(driver)
    return loop, driver


def _user(content: str, **metadata: Any) -> InboundMessage:
    return InboundMessage(
        channel="websocket", sender_id="u", chat_id=KEY, content=content, metadata=metadata,
    )


# ── quali tool si vedono ─────────────────────────────────────────────────────


async def test_plan_tools_are_hidden_in_an_ordinary_turn(workspace: Path) -> None:
    script = Script(workspace)
    loop, _ = _loop(workspace, script)
    await loop._process_message(_user("ciao"), session_key=KEY)
    assert not (script.offered[-1] & PLAN_TOOLS)
    await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="ciao"),
    )
    assert not (script.offered[-1] & PLAN_TOOLS)


async def test_the_planning_turn_offers_only_plan_propose(workspace: Path) -> None:
    script = Script(workspace)
    loop, driver = _loop(workspace, script)
    await loop._process_message(
        _user("Rifai la wiki", plan_mode=True, readonly=True), session_key=KEY
    )
    assert "plan_propose" in script.offered[0]
    assert not (script.offered[0] & PLAN_STEP_TOOLS)
    assert "[Planning turn]" in script.prompts[0]
    plan = driver.store.open_plan_for("piante")
    assert plan is not None and plan.status == "proposed"


async def test_plan_mode_outside_a_notebook_offers_nothing(workspace: Path) -> None:
    script = Script(workspace)
    loop, _ = _loop(workspace, script)
    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="piano",
        metadata={"plan_mode": True},
    ))
    assert not (script.offered[-1] & PLAN_TOOLS)
    assert "[Planning turn]" not in script.prompts[-1]


async def test_a_step_turn_offers_the_step_tools_and_persists_no_user_bubble(
    workspace: Path,
) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    step_msg = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    assert step_msg.metadata[INJECTED_EVENT_META] == PLAN_STEP_EVENT
    outcome = await loop._process_message(step_msg)
    assert PLAN_STEP_TOOLS <= script.offered[-1]
    assert "plan_propose" not in script.offered[-1]
    assert "Step 1: Scrivo acero" in script.prompts[-1]
    # La lista del piano è anche nel contesto del turno.
    assert 'Plan "Rifare la wiki"' in script.prompts[-1]
    session = loop.sessions.get_or_create(KEY)
    row = next(m for m in session.messages if m.get(INJECTED_EVENT_META) == PLAN_STEP_EVENT)
    assert row["role"] == "user" and row["plan_step"] == "s1"
    from jenny.webui.transcript import _session_user_event

    assert _session_user_event(KEY, row) is None
    assert outcome.message is not None and outcome.message.content == "Va bene."


async def test_a_stale_step_message_does_not_run(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    step_msg = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    await driver.stop(plan.id, "keep")
    calls = len(script.offered)
    await loop._process_message(step_msg)
    assert len(script.offered) == calls


# ── la catena intera ─────────────────────────────────────────────────────────


async def _run(loop: AgentLoop) -> asyncio.Task:
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    return task


async def _stop(loop: AgentLoop, task: asyncio.Task) -> None:
    loop.stop()
    await asyncio.wait_for(task, timeout=5)


async def test_propose_approve_and_three_steps_to_done(workspace: Path) -> None:
    script = Script(workspace)
    loop, driver = _loop(workspace, script)
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("Rifai la wiki", plan_mode=True, readonly=True))
        await wait_until(lambda: driver.store.open_plan_for("piante") is not None, timeout=5)
        await wait_until(lambda: KEY not in loop._pending_queues, timeout=5)
        plan = driver.store.open_plan_for("piante")
        await driver.approve(plan.id, plan.version, [])
        await wait_until(lambda: driver.store.load(plan.id).status == "done", timeout=10)
    finally:
        await _stop(loop, task)
    plan = driver.store.load(plan.id)
    assert [s.status for s in plan.steps] == ["done", "done", "done"]
    assert [s.note for s in plan.steps] == ["passo 1 scritto", "passo 2 scritto", "passo 3 scritto"]
    assert plan.photos["start"] and plan.photos["end"]
    assert driver.step_files(plan.id, "s2")["added"] == ["wiki/passo2.md"]
    assert driver.changes(plan.id)["added"] == [
        "wiki/passo1.md", "wiki/passo2.md", "wiki/passo3.md",
    ]
    steps_seen = [p for p in script.prompts if "[Plan — step" in p]
    assert len(steps_seen) == 3


async def test_a_stalled_step_is_reminded_once_then_blocked(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("Rifai la wiki", plan_mode=True, readonly=True))
        await wait_until(lambda: driver.store.open_plan_for("piante") is not None, timeout=5)
        await wait_until(lambda: KEY not in loop._pending_queues, timeout=5)
        plan = driver.store.open_plan_for("piante")
        await driver.approve(plan.id, plan.version, [])
        await wait_until(lambda: driver.store.load(plan.id).status == "blocked", timeout=10)
    finally:
        await _stop(loop, task)
    plan = driver.store.load(plan.id)
    assert plan.blocked["reason"] == "stalled"
    assert sum("[Plan reminder" in p for p in script.prompts) == 1


async def test_stop_with_a_helper_running(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    running = {"aiutante"}
    loop.subagents.get_running_ids_by_session = (
        lambda key: frozenset(running) if key == KEY else frozenset()
    )
    cancelled: list[str] = []

    async def cancel_by_session(key: str, *, grace_s: float = 2.0) -> int:
        cancelled.append(key)
        running.clear()
        return 1

    loop.subagents.cancel_by_session = cancel_by_session
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("Rifai la wiki", plan_mode=True, readonly=True))
        await wait_until(lambda: driver.store.open_plan_for("piante") is not None, timeout=5)
        await wait_until(lambda: KEY not in loop._pending_queues, timeout=5)
        plan = driver.store.open_plan_for("piante")
        running.clear()
        await driver.approve(plan.id, plan.version, [])
        running.add("aiutante")
        # Il passo ha lanciato un aiutante: il turno finisce, il passo resta aperto.
        await wait_until(lambda: any("[Plan — step 1" in p for p in script.prompts), timeout=5)
        await wait_until(lambda: KEY not in loop._pending_queues, timeout=5)
        assert driver.store.load(plan.id).status == "running"
        await driver.stop(plan.id, "keep")
    finally:
        await _stop(loop, task)
    plan = driver.store.load(plan.id)
    assert cancelled == [KEY]
    assert plan.status == "stopped" and plan.steps[0].status == "interrupted"
    assert not any("[Plan reminder" in p for p in script.prompts)


async def test_slash_stop_in_the_notebook_stops_the_plan(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    await loop.bus.consume_inbound()
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("/stop"))
        await wait_until(lambda: driver.store.load(plan.id).status == "stopped", timeout=5)
    finally:
        await _stop(loop, task)


async def test_a_paused_plan_runs_user_turns_read_only(workspace: Path) -> None:
    from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    await loop.bus.consume_inbound()
    await driver.pause(plan.id)
    await driver.step_done(KEY, "fatto")
    await driver.on_turn_end(KEY, {})
    assert driver.store.load(plan.id).status == "paused"
    msg = _user("come va?")
    queue: asyncio.Queue = asyncio.Queue()
    loop._pending_queues[KEY] = queue
    await loop._dispatch(msg, queue)
    assert msg.metadata[WORKSPACE_READONLY_METADATA_KEY] is True
    assert "The user paused the plan" in script.prompts[-1]


async def test_the_personal_chat_sees_one_line_per_open_plan(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="a che punto?"),
    )
    assert 'Notebook piante: plan "Rifare la wiki" 0/3, proposed.' in script.prompts[-1]


def test_plan_tools_are_registered_in_both_scopes() -> None:
    from jenny.agent.tools.loader import ToolLoader
    from jenny.agent.tools.plan import TOOLS

    names = {cls().name for cls in TOOLS}
    assert names == PLAN_TOOLS
    for cls in TOOLS:
        assert cls._scopes == {"core", "orchestrator"}
    assert set(TOOLS) <= set(ToolLoader().discover())
    json.dumps([cls().parameters for cls in TOOLS])


async def test_a_stale_step_message_is_not_judged_as_a_stall(workspace: Path) -> None:
    """Il passo 1 è rimasto sul bus mentre l'utente metteva in pausa e riprendeva:
    scartarlo non deve far partire il promemoria del passo 2."""
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    stale = await loop.bus.consume_inbound()
    await driver.pause(plan.id)
    await driver.step_done(KEY, "fatto")
    await driver.on_turn_end(KEY, {})
    await driver.resume(plan.id)
    second = await loop.bus.consume_inbound()
    assert second.metadata["plan_step"] == "s2"
    queue: asyncio.Queue = asyncio.Queue()
    loop._pending_queues[KEY] = queue
    await loop._dispatch(stale, queue)
    assert loop.bus.inbound.empty()
    assert driver.store.load(plan.id).steps[1].status == "now"



# ── revisione: scope, cron, promemoria, turni abbandonati ────────────────────


async def _approved(loop: AgentLoop, driver: PlanDriver) -> tuple[Any, InboundMessage]:
    await loop._process_message(_user("piano", plan_mode=True, readonly=True), session_key=KEY)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    step_msg = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    return plan, step_msg


async def _dispatch(loop: AgentLoop, msg: InboundMessage, *queued: InboundMessage) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    for item in queued:
        queue.put_nowait(item)
    loop._pending_queues[msg.session_key] = queue
    await loop._dispatch(msg, queue)


def _cron(session_key: str = KEY, content: str = "Controlla le piante") -> InboundMessage:
    from jenny.cron.session_turns import CRON_TRIGGER_META

    return InboundMessage(
        channel="websocket", sender_id="cron", chat_id=KEY, content=content,
        metadata={CRON_TRIGGER_META: {"job_id": "j1", "run_id": "j1:1", "job_name": "piante"}},
        session_key_override=session_key,
    )


async def test_a_step_turn_reads_the_notebooks_instructions(workspace: Path) -> None:
    """Il turno di passo è di sistema (``channel="system"``): lo scope del suo
    prompt veniva dal canale, cioè la radice del workspace."""
    (workspace / "AGENTS.md").write_text("ROOT-ONLY-INSTRUCTIONS\n", encoding="utf-8")
    (workspace / "wikis" / "piante" / "AGENTS.md").write_text(
        "NOTEBOOK-PIANTE-INSTRUCTIONS\n", encoding="utf-8"
    )
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    _, step_msg = await _approved(loop, driver)
    await loop._process_message(step_msg)
    system = script.systems[-1]
    assert "NOTEBOOK-PIANTE-INSTRUCTIONS" in system
    assert "ROOT-ONLY-INSTRUCTIONS" not in system


async def test_a_helper_result_in_a_notebook_reads_the_notebooks_instructions(
    workspace: Path,
) -> None:
    """Stesso difetto, stessa riga: il rientro di un aiutante nella chat di un quaderno."""
    (workspace / "AGENTS.md").write_text("ROOT-ONLY-INSTRUCTIONS\n", encoding="utf-8")
    (workspace / "wikis" / "piante" / "AGENTS.md").write_text(
        "NOTEBOOK-PIANTE-INSTRUCTIONS\n", encoding="utf-8"
    )
    script = Script(workspace)
    loop, _ = _loop(workspace, script)
    await loop._process_message(InboundMessage(
        channel="system", sender_id="subagent", chat_id=f"websocket:{KEY}",
        content="[Subagent result] fatto", session_key_override=KEY,
        metadata={INJECTED_EVENT_META: "subagent_result", "subagent_task_id": "t1"},
    ))
    assert "NOTEBOOK-PIANTE-INSTRUCTIONS" in script.systems[-1]
    assert "ROOT-ONLY-INSTRUCTIONS" not in script.systems[-1]


async def test_a_cron_turn_is_not_the_answer_to_a_blocked_plan(workspace: Path) -> None:
    from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    plan, _ = await _approved(loop, driver)
    await driver.blocked(KEY, "Quale acero?")
    cron = _cron()
    await _dispatch(loop, cron)
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["question"] == "Quale acero?"
    assert cron.metadata[WORKSPACE_READONLY_METADATA_KEY] is True


async def test_a_cron_turn_during_a_step_neither_sees_nor_judges_it(workspace: Path) -> None:
    from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    plan, _ = await _approved(loop, driver)
    cron = _cron()
    await _dispatch(loop, cron)
    assert not (script.offered[-1] & PLAN_STEP_TOOLS)
    assert cron.metadata[WORKSPACE_READONLY_METADATA_KEY] is True
    # Nessun promemoria: la fine di un cron non giudica il passo.
    assert loop.bus.inbound.empty()
    plan = driver.store.load(plan.id)
    assert plan.status == "running" and "nudge" not in plan.steps[0].extra


async def test_a_board_monitor_cron_runs_read_only_while_the_notebook_is_held(
    workspace: Path,
) -> None:
    from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
    from jenny.session.keys import notebook_cron_session_key

    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    plan, _ = await _approved(loop, driver)
    monitor = _cron(notebook_cron_session_key("piante", "j1"))
    await _dispatch(loop, monitor)
    assert monitor.metadata[WORKSPACE_READONLY_METADATA_KEY] is True
    await driver.stop(plan.id, "keep")
    later = _cron(notebook_cron_session_key("piante", "j1"))
    await _dispatch(loop, later)
    assert WORKSPACE_READONLY_METADATA_KEY not in later.metadata


async def test_a_reminder_injected_into_another_turn_ends_in_a_block(workspace: Path) -> None:
    """Un messaggio passato davanti al promemoria apre il suo turno e il
    promemoria gli arriva a metà: nessun turno finiva più coi metadati del
    promemoria, e il piano restava in corsa per sempre."""
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    plan, step_msg = await _approved(loop, driver)
    await _dispatch(loop, step_msg)
    reminder = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    assert reminder.metadata[INJECTED_EVENT_META] == "plan_reminder"
    await _dispatch(loop, _user("ci sei?"), reminder)
    assert any("[Plan reminder" in p for p in script.prompts)
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["reason"] == "stalled"


async def test_a_stale_reminder_is_not_injected_mid_turn(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    plan, step_msg = await _approved(loop, driver)
    await _dispatch(loop, step_msg)
    reminder = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    await driver.stop(plan.id, "keep")
    await _dispatch(loop, _user("e adesso?"), reminder)
    assert not any("[Plan reminder" in p for p in script.prompts)
    assert loop.bus.inbound.empty()


async def test_a_reminder_waits_for_a_cron_turn_to_end(workspace: Path) -> None:
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    plan, step_msg = await _approved(loop, driver)
    await _dispatch(loop, step_msg)
    reminder = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    await _dispatch(loop, _cron(), reminder)
    assert not any("[Plan reminder" in p for p in script.prompts)
    again = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    assert again is reminder
    await _dispatch(loop, again)
    assert driver.store.load(plan.id).status == "blocked"


async def test_stop_and_rollback_refuse_under_an_abandoned_turn(workspace: Path) -> None:
    """Il turno del passo non muore entro la grazia: il loop lo abbandona e lo
    toglie dai suoi turni attivi, ma può ancora scrivere. Il ritorno non deve
    partire sotto di lui."""
    script = Script(workspace, close_steps=False)
    script.hang_steps = True
    loop, driver = _loop(workspace, script)
    original = loop._cancel_active_tasks

    async def quick(key: str, **kw: Any) -> int:
        return await original(key, grace_s=0.1, **kw)

    loop._cancel_active_tasks = quick
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("Rifai la wiki", plan_mode=True, readonly=True))
        await wait_until(lambda: driver.store.open_plan_for("piante") is not None, timeout=5)
        await wait_until(lambda: KEY not in loop._pending_queues, timeout=5)
        plan = driver.store.open_plan_for("piante")
        await driver.approve(plan.id, plan.version, [])
        await wait_until(lambda: any("[Plan — step 1" in p for p in script.prompts), timeout=5)
        page = workspace / "wikis" / "piante" / "wiki" / "acero.md"
        page.write_text("scritta dal passo\n", encoding="utf-8")
        result = await driver.stop(plan.id, "rollback")
        assert result is not None and result["rollback_error"]["code"] == "conflict"
        assert driver.store.load(plan.id).status == "stopped"
        assert page.read_text(encoding="utf-8") == "scritta dal passo\n"
    finally:
        script.release.set()
        await _stop(loop, task)
