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
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT
from jenny.session.plan_probe import set_plan_probe
from tests.support.aio import stop_agent_loop, wait_until

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
        # Il primo turno di pianificazione fa una domanda invece di proporre.
        self.ask_first = False
        # Il provider rifiuta di rispondere ai turni del passo (``content_filter``).
        self.refuse_steps = False
        # Il turno di pianificazione resta a pensare finché non lo si ferma: è lì
        # che l'utente annulla.
        self.hang_planning = False
        self.planning_started = asyncio.Event()

    async def __call__(self, **kw: Any) -> LLMResponse:
        messages = kw.get("messages") or []
        self.offered.append(
            {d.get("function", d).get("name") for d in (kw.get("tools") or [])}
        )
        if messages and messages[0].get("role") == "system":
            self.systems.append(_text(messages[0]))
        last = messages[-1]
        if last.get("role") == "tool":
            return self._after_tool(messages)
        text = _text(last)
        self.prompts.append(text)
        if "[Planning turn]" in text:
            if self.hang_planning:
                self.planning_started.set()
                await asyncio.Event().wait()  # fino al cancel
            if self.ask_first and "The user pressed Plan" in text:
                return LLMResponse(content="La camelia la tengo? Se non mi dici niente: sì.")
            # Prima si guarda il quaderno: ``plan_propose`` rifiuta senza.
            return _call("read_file", path="wiki/index.md")
        found = re.search(r"\[Plan(?: reminder)? — step (\d+) of", text)
        if found and self.refuse_steps:
            return LLMResponse(content=None, finish_reason="content_filter")
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
            # Come la scriverebbe un tool del turno: dai recinti, che la annotano
            # nel registro del piano legato al turno del passo.
            from jenny.security.subagent_policy import check_write, settle_writes

            check_write(page)
            page.write_text(f"passo {n}\n", encoding="utf-8")
            settle_writes("write_file")
            return _call("plan_step_done", note=f"passo {n} scritto")
        return LLMResponse(content="Va bene.")

    @staticmethod
    def _after_tool(messages: list[dict[str, Any]]) -> LLMResponse:
        """Dopo un tool: se era la lettura del turno di pianificazione, la proposta."""
        called = [
            (c.get("function") or c).get("name")
            for m in messages if m.get("role") == "assistant"
            for c in (m.get("tool_calls") or [])
        ]
        if called and called[-1] == "read_file":
            return _call(
                "plan_propose",
                title="Rifare la wiki",
                coverage=[{"item": "acero, ginkgo, indice", "have": "l'indice",
                           "missing": "due pagine", "strategy": "notebook"}],
                steps=[
                    {"title": "Scrivo acero", "detail": "wiki/acero.md", "kind": "write",
                     "done_when": "la pagina c'è"},
                    {"title": "Scrivo ginkgo", "detail": "wiki/ginkgo.md", "kind": "write",
                     "done_when": "la pagina c'è"},
                    {"title": "Scrivo indice", "detail": "wiki/index.md", "kind": "write",
                     "done_when": "l'indice linka le pagine"},
                    # Dal 26/10 l'ultimo passo e' sempre un ``check``: qui lo chiude il
                    # copione come gli altri, perche' ``_loop`` spegne il controllo a
                    # macchina (il quaderno di prova non e' una wiki vera).
                    {"title": "Controllo", "detail": "repair what the check lists",
                     "kind": "check", "done_when": "zero link morti"},
                ],
            )
        return LLMResponse(content="Fatto.")


def _loop(workspace: Path, script: Script) -> tuple[AgentLoop, PlanDriver]:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 1024
    provider.chat_with_retry = script
    provider.chat_stream_with_retry = script
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace, model="test-model")
    driver = PlanDriver(PlanStore.for_workspace(workspace), loop.bus, LoopPlanHost(loop))
    # Niente controllo a macchina: il quaderno di prova non e' una wiki vera e
    # il passo ``check`` si comporta come gli altri (lo chiude il copione).
    driver._run_audit = lambda plan, step: None  # type: ignore[method-assign]
    loop.attach_plan_driver(driver)
    set_plan_probe(driver)
    return loop, driver


def _user(content: str, **metadata: Any) -> InboundMessage:
    return InboundMessage(
        channel="websocket", sender_id="u", chat_id=KEY, content=content, metadata=metadata,
    )


async def _planning(loop: AgentLoop, content: str, **metadata: Any) -> Any:
    """Un turno col tasto Piano, come lo fa ``_dispatch``: prima il gancio, poi il turno."""
    msg = _user(content, plan_mode=True, readonly=True, **metadata)
    await loop._plan_before_turn(msg, KEY)
    return await loop._process_message(msg, session_key=KEY)


# ── quali tool si vedono ─────────────────────────────────────────────────────


async def test_plan_tools_stay_listed_and_refuse_outside_their_turn(workspace: Path) -> None:
    """Dove si pianifica i tool del piano sono nella lista a ogni turno: cambiarla
    a meta' chat faceva ripagare la conversazione al provider (dal 03/11/2026).
    Fuori dal loro turno rispondono col motivo, senza girare."""
    from jenny.agent.loop_plan import NO_STEP_REFUSAL, NOT_PLANNING_REFUSAL, has_plans

    script = Script(workspace)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("ciao"), session_key=KEY)
    assert PLAN_TOOLS <= script.offered[-1]
    verdict = loop._plan_tool_filter(_user("ciao"), KEY)
    assert verdict("plan_propose") == NOT_PLANNING_REFUSAL
    assert all(verdict(name) == NO_STEP_REFUSAL for name in PLAN_STEP_TOOLS)
    await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="ciao"),
    )
    personal = has_plans("unified:default", driver)
    assert bool(script.offered[-1] & PLAN_TOOLS) is personal


async def test_a_refused_plan_tool_answers_without_running(workspace: Path) -> None:
    from jenny.agent.loop_plan import NO_STEP_REFUSAL
    from jenny.session.manager import Session

    loop, _ = _loop(workspace, Script(workspace))
    view = loop._turn_tool_view(_user("ciao"), Session(key=KEY), KEY, silent=False)
    assert view is not None and "plan_step_done" in view.tool_names
    tool, _params, error = view.prepare_call("plan_step_done", {"note": "x"})
    assert tool is None and error == NO_STEP_REFUSAL
    assert NO_STEP_REFUSAL in await view.execute("plan_step_done", {"note": "x"})


async def test_the_planning_turn_keeps_the_tool_list_of_the_chat(workspace: Path) -> None:
    script = Script(workspace)
    loop, driver = _loop(workspace, script)
    await loop._process_message(_user("ciao"), session_key=KEY)
    ordinary = script.offered[-1]
    script.offered.clear()
    script.prompts.clear()
    await _planning(loop, "Rifai la wiki")
    assert script.offered[0] == ordinary
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
    await _planning(loop, "piano")
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    step_msg = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=2)
    assert step_msg.metadata[INJECTED_EVENT_META] == PLAN_STEP_EVENT
    outcome = await loop._process_message(step_msg)
    assert PLAN_STEP_TOOLS <= script.offered[-1]
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
    await _planning(loop, "piano")
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
    await stop_agent_loop(loop, task)


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
    assert [s.status for s in plan.steps] == ["done"] * 4
    assert [s.note for s in plan.steps] == [f"passo {n} scritto" for n in range(1, 5)]
    assert driver.step_files(plan.id, "s2")["added"] == ["wiki/passo2.md"]
    assert driver.changes(plan.id)["added"] == [
        "wiki/passo1.md", "wiki/passo2.md", "wiki/passo3.md", "wiki/passo4.md",
    ]
    steps_seen = [p for p in script.prompts if "[Plan — step" in p]
    assert len(steps_seen) == 4


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


async def test_a_refused_step_blocks_without_a_reminder(workspace: Path) -> None:
    """Lo ``stop_reason`` del turno arriva al driver: niente promemoria, blocco ``refused``."""
    script = Script(workspace, close_steps=False)
    script.refuse_steps = True
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
    assert plan.blocked["reason"] == "refused"
    assert sum("[Plan reminder" in p for p in script.prompts) == 0
    # Il turno del passo è l'ultima riga ``user``: niente da togliere, una richiesta sola.
    assert sum("[Plan — step" in p for p in script.prompts) == 1


async def test_cancelling_a_planning_stops_the_turn_in_flight(workspace: Path) -> None:
    """L'annullamento ferma il turno che stava pianificando: il 25/10 andava
    avanti, ``plan_propose`` lo rifiutava e Jenny faceva il lavoro «diretta»."""
    script = Script(workspace)
    script.hang_planning = True
    loop, driver = _loop(workspace, script)
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("Sistema l'indice", plan_mode=True, readonly=True))
        await asyncio.wait_for(script.planning_started.wait(), timeout=5)
        assert driver.host.turn_in_flight(KEY)
        assert await driver.cancel_drafting("piante") is True
        await wait_until(lambda: not driver.host.turn_in_flight(KEY), timeout=5)
    finally:
        await _stop(loop, task)
    assert driver.store.open_plan_for("piante") is None
    # Una sola chiamata al modello, quella rimasta a pensare: dopo l'annullamento
    # il turno non è andato avanti (né lettura, né proposta, né lavoro «diretto»).
    assert len(script.offered) == 1
    assert (workspace / "wikis" / "piante" / "wiki" / "index.md").read_text() == "# Piante\n"


async def test_a_refused_chat_turn_does_not_judge_the_step_as_refused(workspace: Path) -> None:
    """Solo il lavoro del passo blocca per rifiuto: un turno di chat dell'utente
    rifiutato durante il piano arriva al driver senza ``provider_refused``."""
    loop, driver = _loop(workspace, Script(workspace, close_steps=False))
    seen: list[str] = []

    async def _on_turn_end(session_key, metadata, *, pending=False, stop_reason=""):
        seen.append(stop_reason)

    driver.on_turn_end = _on_turn_end
    await loop._plan_turn_ended(_user("come va?"), KEY, stop_reason="provider_refused")
    report = InboundMessage(
        channel="system", sender_id="subagent", chat_id=f"websocket:{KEY}", content="x",
        session_key_override=KEY,
        metadata={INJECTED_EVENT_META: "subagent_result", "subagent_task_id": "t1"},
    )
    await loop._plan_turn_ended(report, KEY, stop_reason="provider_refused")
    assert seen == ["", "provider_refused"]


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
    await _planning(loop, "piano")
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
    await _planning(loop, "piano")
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
    await _planning(loop, "piano")
    await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="a che punto?"),
    )
    assert 'Notebook piante: plan "Rifare la wiki" 0/4, proposed.' in script.prompts[-1]


def test_plan_tools_are_registered_in_both_scopes() -> None:
    from jenny.agent.tools.loader import ToolLoader
    from jenny.agent.tools.plan import TOOLS

    names = {cls().name for cls in TOOLS}
    assert names == PLAN_TOOLS
    for cls in TOOLS:
        assert cls._scopes == {"core", "orchestrator"}
    assert set(TOOLS) <= set(ToolLoader().discover())
    json.dumps([cls().parameters for cls in TOOLS])


def test_plan_blocked_asks_for_decisions_not_a_report() -> None:
    """Sul Titan la domanda era un resoconto di 1700 caratteri con due domande dentro:
    il tool chiede le decisioni, numerate, e manda il resoconto nella risposta."""
    from jenny.agent.tools.plan import PlanBlockedTool

    tool = PlanBlockedTool()
    question = tool.parameters["properties"]["question"]["description"]
    assert "numbered" in question and "Not a report" in question
    assert "in your reply" in tool.description


async def test_a_stale_step_message_is_not_judged_as_a_stall(workspace: Path) -> None:
    """Il passo 1 è rimasto sul bus mentre l'utente metteva in pausa e riprendeva:
    scartarlo non deve far partire il promemoria del passo 2."""
    script = Script(workspace, close_steps=False)
    loop, driver = _loop(workspace, script)
    await _planning(loop, "piano")
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
    await _planning(loop, "piano")
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
    from jenny.agent.loop_plan import NO_STEP_REFUSAL

    cron = _cron()
    assert loop._plan_tool_filter(cron, KEY)("plan_step_done") == NO_STEP_REFUSAL
    await _dispatch(loop, cron)
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


# ── la pianificazione dura più turni ────────────────────────────────────────


async def test_the_answer_to_a_question_is_still_a_planning_turn(workspace: Path) -> None:
    """Tre turni: domanda, risposta senza il tasto, proposta. La risposta è in sola
    lettura e vede ``plan_propose``; dopo la proposta si torna alla chat normale."""
    from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

    script = Script(workspace)
    script.ask_first = True
    loop, driver = _loop(workspace, script)
    await _planning(loop, "Rifai la wiki")
    assert driver.store.open_plan_for("piante") is None
    assert driver.drafting_for(KEY) is not None

    answer = _user("Sì, tienila")
    await loop._plan_before_turn(answer, KEY)
    assert answer.metadata[WORKSPACE_READONLY_METADATA_KEY] is True
    await loop._process_message(answer, session_key=KEY)
    assert "plan_propose" in script.offered[-2]
    assert "still preparing a plan" in script.prompts[-1]
    plan = driver.store.open_plan_for("piante")
    assert plan is not None and plan.status == "proposed"
    assert driver.drafting_for(KEY) is None

    after = _user("grazie")
    await loop._plan_before_turn(after, KEY)
    await loop._process_message(after, session_key=KEY)
    # La stessa lista: fuori dalla pianificazione ``plan_propose`` risponde col motivo.
    assert script.offered[-1] == script.offered[-2]
    assert "[Planning turn]" not in script.prompts[-1]


async def test_the_planning_turn_counts_its_reads(workspace: Path) -> None:
    script = Script(workspace)
    script.ask_first = True
    loop, driver = _loop(workspace, script)
    await _planning(loop, "Rifai la wiki")
    assert driver.drafting_info("piante")["reads"] == 0
    # La risposta legge (``read_file``) e propone: la lettura conta, e basta.
    msg = _user("ok")
    await loop._plan_before_turn(msg, KEY)
    await loop._process_message(msg, session_key=KEY)
    assert driver.store.open_plan_for("piante") is not None


async def test_no_web_while_planning(workspace: Path) -> None:
    """In un turno di pianificazione il web non gira (l'agente in modalità core
    lo avrebbe): una pagina letta adesso contaminerebbe gli aiutanti dei passi.
    Resta nella lista, e risponde col motivo."""
    from jenny.agent.loop_plan import PLANNING_REFUSAL

    loop, driver = _loop(workspace, Script(workspace))
    await driver.start_drafting(KEY)
    planning = loop._plan_tool_filter(_user("x"), KEY)
    plain = loop._plan_tool_filter(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="x"),
        "unified:default",
    )
    for name in ("web_search", "web_fetch", "browser_open", "download_file", "python_exec",
                 "write_stdin"):
        assert planning(name) == PLANNING_REFUSAL
        assert plain(name) is None
    assert planning("read_file") is None and planning("plan_propose") is False


async def test_spawn_while_planning_allows_only_the_notebook_editor(workspace: Path) -> None:
    from unittest.mock import AsyncMock

    from jenny.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from jenny.agent.tools.spawn import SpawnTool

    manager = MagicMock()
    manager.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(manager)
    tool.set_context(RequestContext(channel="websocket", chat_id=KEY, session_key=KEY))
    token = bind_request_context(RequestContext(
        channel="websocket", chat_id=KEY, session_key=KEY,
        metadata={"plan_mode": True, "readonly": True},
    ))
    try:
        for agent_type in ("researcher", None, "coder"):
            out = await tool.execute(task="cerca il glicine", agent_type=agent_type)
            assert out.startswith("Cannot spawn") and "research" in out, out
        manager.spawn.assert_not_called()
        assert await tool.execute(task="inventario", agent_type="notebook-editor") == "started"
    finally:
        reset_request_context(token)
    assert await tool.execute(task="cerca", agent_type="researcher") == "started"


def _helper_result(task_id: str) -> InboundMessage:
    return InboundMessage(
        channel="system", sender_id="subagent", chat_id="websocket:project:piante",
        content="Inventario: 10 piante.", session_key_override=KEY,
        metadata={"injected_event": "subagent_result", "subagent_task_id": task_id},
    )


async def test_a_helper_result_while_planning_is_planning_and_counts_only_if_spawned_there(
    workspace: Path,
) -> None:
    """Il rientro di un aiutante durante la pianificazione è pianificazione (sola
    lettura); conta come lettura solo se l'ha lanciato lei — un ``researcher`` di
    prima non ha guardato il quaderno per questo piano."""
    from jenny.agent.hook import ToolResultHookContext
    from jenny.agent.loop_plan import PlanReadHook
    from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

    loop, driver = _loop(workspace, Script(workspace))
    await driver.start_drafting(KEY)
    old = _helper_result("vecchio1")
    await loop._plan_system_turn(old, KEY)
    assert old.metadata["plan_mode"] is True
    assert old.metadata[WORKSPACE_READONLY_METADATA_KEY] is True
    await loop._plan_count_helper_result(old, KEY)
    assert driver.drafting_info("piante")["reads"] == 0

    hook = PlanReadHook(driver, KEY)
    await hook.after_execute_tool(ToolResultHookContext(
        name="spawn", call_id="c1", arguments={"agent_type": "notebook-editor"},
        result="Subagent [inventario] started (id: ab12cd34). The result arrives on its own.",
    ))
    mine = _helper_result("ab12cd34")
    await loop._plan_system_turn(mine, KEY)
    await loop._plan_count_helper_result(mine, KEY)
    assert driver.drafting_info("piante")["reads"] == 1

    await driver.end_drafting(KEY, "test")
    other = _helper_result("ab12cd34")
    await loop._plan_system_turn(other, KEY)
    assert "plan_mode" not in other.metadata


async def test_a_message_whose_planning_differs_from_the_turn_waits_its_own_turn(
    workspace: Path,
) -> None:
    """Un ``plan: true`` iniettato in un turno normale scriverebbe senza la sola
    lettura; una risposta normale iniettata in una pianificazione resterebbe in
    sola lettura. Diventa un turno suo."""
    loop, driver = _loop(workspace, Script(workspace))
    plain_turn: dict[str, Any] = {}
    planning_turn = {"plan_mode": True, "readonly": True}
    assert loop._plan_injection(_user("x", plan_mode=True), plain_turn, KEY) == "defer"
    assert loop._plan_injection(_user("x"), plain_turn, KEY) == "inject"
    await driver.start_drafting(KEY)
    assert loop._plan_injection(_user("risposta"), planning_turn, KEY) == "inject"
    assert loop._plan_injection(_user("risposta"), plain_turn, KEY) == "defer"
    await driver.end_drafting(KEY, "proposed")
    assert loop._plan_injection(_user("grazie"), planning_turn, KEY) == "defer"


async def test_slash_new_ends_a_planning(workspace: Path) -> None:
    script = Script(workspace)
    script.ask_first = True
    loop, driver = _loop(workspace, script)
    await _planning(loop, "Rifai la wiki")
    assert driver.drafting_for(KEY) is not None
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("/new"))
        await wait_until(lambda: driver.drafting_for(KEY) is None, timeout=5)
    finally:
        await _stop(loop, task)


async def test_slash_stop_ends_a_planning(workspace: Path) -> None:
    script = Script(workspace)
    script.ask_first = True
    loop, driver = _loop(workspace, script)
    await _planning(loop, "Rifai la wiki")
    assert driver.drafting_for(KEY) is not None
    task = await _run(loop)
    try:
        await loop.bus.publish_inbound(_user("/stop"))
        await wait_until(lambda: driver.drafting_for(KEY) is None, timeout=5)
    finally:
        await _stop(loop, task)


async def test_the_users_answer_mid_turn_is_deferred_not_injected(workspace: Path) -> None:
    """Risposta a un piano fermo arrivata mentre gira il turno di un resoconto:
    iniettata sarebbe chat e il piano resterebbe fermo. Diventa un turno suo."""
    loop, driver = _loop(workspace, Script(workspace, close_steps=False))
    plan, _ = await _approved(loop, driver)
    await driver.blocked(KEY, "Quale acero?")
    assert driver.awaits_answer(KEY)
    assert loop._plan_injection(_user("quello rosso"), {}, KEY) == "defer"
    report = InboundMessage(
        channel="system", sender_id="subagent", chat_id=f"websocket:{KEY}", content="x",
        session_key_override=KEY,
        metadata={INJECTED_EVENT_META: "subagent_result", "subagent_task_id": "t1"},
    )
    assert loop._plan_injection(report, {}, KEY) == "inject", "un resoconto passa"
    await driver.answer(plan.id, "quello rosso")
    assert loop._plan_injection(_user("e poi?"), {}, KEY) == "inject"


async def test_a_helper_report_of_the_open_step_records_its_gaps(workspace: Path) -> None:
    """Il resoconto di un aiutante del passo, col suo turno o iniettato a metà
    turno, porta i buchi **To research** sul passo aperto (``note_step_report``)."""
    loop, driver = _loop(workspace, Script(workspace, close_steps=False))
    plan, _ = await _approved(loop, driver)

    def _report(task_id: str, content: str) -> InboundMessage:
        return InboundMessage(
            channel="system", sender_id="subagent", chat_id=f"websocket:{KEY}",
            content=f"[Subagent 'editor' completed successfully]\n\nResult:\n{content}",
            session_key_override=KEY,
            metadata={
                INJECTED_EVENT_META: "subagent_result", "subagent_status": "ok",
                "subagent_task_id": task_id, "subagent_lineage_id": "ed1",
                "plan_id": plan.id, "plan_step": "s1",
            },
        )

    await loop._process_message(_report("t1", "Fatto.\n\n**To research**\n- felce: umidità"))
    assert driver.store.load(plan.id).steps[0].extra["gaps"] == {"ed1": ["felce: umidità"]}
    # Lo stesso aiutante, rimandato indietro, arriva a metà di un altro turno.
    await _dispatch(loop, _user("come va?"), _report("t2", "Pagine riempite."))
    assert "gaps" not in driver.store.load(plan.id).steps[0].extra
    # Il resoconto di un ricercatore registra il suo lineage, e i suoi
    # «To research» non sono buchi del passo.
    await loop._process_message(_report("t3", "Fatto.\n\n**To research**\n- felce: vaso"))
    researcher = _report("t4", "Salvate due fonti.\n\nTo research:\n- nulla di nuovo")
    researcher.metadata.update({"subagent_lineage_id": "rs1", "subagent_agent_type": "researcher"})
    await loop._process_message(researcher)
    extra = driver.store.load(plan.id).steps[0].extra
    assert extra["research_lineages"] == ["rs1"]
    assert extra["gaps"] == {"ed1": ["felce: vaso"]}


def test_plan_step_messages_stay_out_of_the_notes_dream_reads() -> None:
    """Il messaggio di un passo lo scrive il driver: negli appunti del
    consolidatore sarebbe un «USER:» di istruzioni, e Dream lo leggerebbe come
    detto dall'utente. Le risposte di Jenny restano."""
    from jenny.agent.memory import MemoryStore
    from jenny.session.history_meta import PLAN_STEP_EVENT

    text = MemoryStore._format_messages([
        {"role": "user", "content": "[Plan — step 1 of 3] Do step 1", "timestamp": "2026-11-03T10:00",
         INJECTED_EVENT_META: PLAN_STEP_EVENT},
        {"role": "assistant", "content": "Fatto il passo 1.", "timestamp": "2026-11-03T10:01"},
        {"role": "user", "content": "Grazie", "timestamp": "2026-11-03T10:02"},
    ])
    assert "Do step 1" not in text
    assert "Fatto il passo 1." in text and "USER: Grazie" in text


async def test_in_the_personal_chat_only_turns_about_the_step_see_its_tools(
    tmp_path: Path,
) -> None:
    """Nella chat personale arriva di tutto: un messaggio dalla mascotte a metà
    passo non vede i tool del passo; il turno del passo e il resoconto di un suo
    aiutante (che porta piano e passo) sì. Trovato sul telefono il 03/11/2026:
    il resoconto non li vedeva, e il passo si chiudeva solo al promemoria."""
    from jenny.agent.loop_plan import LoopPlanMixin
    from jenny.bus.events import InboundMessage
    from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META, PlanDriver
    from jenny.plan.store import PlanStore
    from tests.support.plan import FakeBus, FakeHost, propose

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    driver = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), FakeHost(workspace),
                        personal_plans=True)
    steps = [{"title": "Uno", "detail": "x", "kind": "write", "done_when": "x"},
             {"title": "Due", "detail": "x", "kind": "other", "done_when": "x"}]
    assert "card" in await propose(driver, "Prova", steps, key="unified:default")
    plan = driver.store.open_plan_for("personal")
    await driver.approve(plan.id, plan.version, [])

    loop = LoopPlanMixin()
    loop.plan_driver = driver

    def sees(metadata: dict) -> bool:
        msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default",
                             content="x", metadata=metadata)
        return loop._plan_tool_filter(msg, "unified:default")("plan_step_done") is False

    assert not sees({}), "un messaggio di chat a metà passo"
    assert sees({PLAN_ID_META: plan.id, PLAN_STEP_META: "s1"}), "il resoconto di un aiutante"
    assert not sees({PLAN_ID_META: plan.id, PLAN_STEP_META: "s2"}), "un passo che non è aperto"


async def test_in_the_personal_chat_a_message_waits_for_the_whole_step_turn(
    tmp_path: Path,
) -> None:
    """Un messaggio dalla mascotte a metà del turno di un passo aspetta la fine
    del turno, anche dopo che il passo si è chiuso: il rimandato si ricontrolla
    a ogni giro, e fino al 03/11/2026 a passo chiuso entrava nella coda del
    turno del passo (provato sul telefono), con la risposta in chat e non nel
    fumetto."""
    from jenny.agent.loop_plan import LoopPlanMixin
    from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META
    from tests.support.plan import FakeBus, FakeHost, propose

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    driver = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), FakeHost(workspace),
                        personal_plans=True)
    steps = [{"title": "Uno", "detail": "x", "kind": "write", "done_when": "x"},
             {"title": "Due", "detail": "x", "kind": "other", "done_when": "x"}]
    assert "card" in await propose(driver, "Prova", steps, key="unified:default")
    plan = driver.store.open_plan_for("personal")
    await driver.approve(plan.id, plan.version, [])

    loop = LoopPlanMixin()
    loop.plan_driver = driver
    step_turn = {INJECTED_EVENT_META: PLAN_STEP_EVENT, PLAN_ID_META: plan.id,
                 PLAN_STEP_META: "s1"}
    mascot = InboundMessage(channel="floating", sender_id="u", chat_id="shade", content="ciao")

    assert loop._plan_injection(mascot, step_turn, "unified:default") == "defer"
    await driver.step_done("unified:default", "fatto")
    assert not driver.step_open("unified:default")
    assert loop._plan_injection(mascot, step_turn, "unified:default") == "defer", (
        "il passo è chiuso, ma il turno è ancora il suo")
    assert loop._plan_injection(mascot, {}, "unified:default") == "inject", "fuori dal passo"


async def test_in_the_personal_chat_the_waiting_message_goes_before_the_next_step(
    tmp_path: Path,
) -> None:
    """A fine turno del passo il messaggio rimasto in coda va sul bus prima che
    il gancio apra il passo seguente; e il passo seguente, arrivato mentre gira
    il turno di quel messaggio, aspetta lui. Fino al 03/11/2026 la mascotte
    aspettava la fine dell'intero piano (provato sul telefono)."""
    from jenny.agent.loop_plan import LoopPlanMixin
    from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META
    from tests.support.plan import FakeBus, FakeHost, propose

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bus = FakeBus()
    driver = PlanDriver(PlanStore.for_workspace(workspace), bus, FakeHost(workspace),
                        personal_plans=True)
    steps = [{"title": "Uno", "detail": "x", "kind": "write", "done_when": "x"},
             {"title": "Due", "detail": "x", "kind": "other", "done_when": "x"}]
    assert "card" in await propose(driver, "Prova", steps, key="unified:default")
    plan = driver.store.open_plan_for("personal")
    await driver.approve(plan.id, plan.version, [])

    loop = LoopPlanMixin()
    loop.plan_driver = driver
    loop.bus = bus
    queue: asyncio.Queue = asyncio.Queue()
    loop._pending_queues = {"unified:default": queue}
    mascot = InboundMessage(channel="floating", sender_id="u", chat_id="shade", content="ciao")
    queue.put_nowait(mascot)
    step_turn = InboundMessage(
        channel="system", sender_id="plan", chat_id="websocket:default", content="passo 1",
        session_key_override="unified:default",
        metadata={INJECTED_EVENT_META: PLAN_STEP_EVENT, PLAN_ID_META: plan.id,
                  PLAN_STEP_META: "s1"})
    await driver.step_done("unified:default", "fatto")
    bus.inbound.clear()

    await loop._plan_turn_ended(step_turn, "unified:default")

    assert bus.inbound[0] is mascot, "il messaggio in attesa per primo"
    step2 = bus.inbound[1]
    assert step2.metadata.get(PLAN_STEP_META) == "s2"
    assert "unified:default" not in loop._pending_queues, "la coda è lasciata"
    assert loop._plan_injection(step2, {}, "unified:default") == "defer", (
        "il passo aspetta il turno della mascotte")
    assert loop._plan_injection(step2, step_turn.metadata, "unified:default") == "inject"
