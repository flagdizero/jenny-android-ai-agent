"""Un solo risveglio per passo: i resoconti degli aiutanti arrivano insieme.

Nella prova 2 ogni resoconto di aiutante apriva un turno intero di Jenny, con
tutta la conversazione del quaderno dietro: un passo con N aiutanti faceva fino
a N turni. Ora un resoconto riuscito, se un fratello dello stesso passo lavora
ancora, si scrive in storia **senza turno** (``FollowupState.HELD``): nessuna
chiamata al modello, nessun messaggio in uscita, ``handled`` solo al risveglio.
Il turno parte con l'ultimo, o quando i fratelli rimasti sono tutti fermi, o
dopo un tetto di tempo. Un errore o un budget svegliano subito; fuori dai piani
non cambia niente.

Qui c'è l'``AgentLoop`` vero col driver vero; gli aiutanti sono finti (lo stato
che il manager darebbe), il modello registra cosa vede.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.plan_host import LoopPlanHost
from jenny.agent.subagent import SubagentManager
from jenny.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.plan.driver import PLAN_WAKE_META, PlanDriver
from jenny.plan.store import PlanStore
from jenny.providers.base import LLMProvider, LLMResponse
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT
from jenny.session.plan_probe import set_plan_probe
from tests.support.aio import wait_until
from tests.support.plan import STEPS, propose

KEY = "project:piante"


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


class Model:
    """Il modello: risponde «Fatto.» e si tiene ogni richiesta."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def __call__(self, **kw: Any) -> LLMResponse:
        self.calls.append(list(kw.get("messages") or []))
        return LLMResponse(content="Fatto.")

    def saw(self, needle: str, call: int = -1) -> bool:
        return any(needle in _text(m) for m in self.calls[call])


class Rig:
    def __init__(self, workspace: Path, *, wake_after: float = 600.0) -> None:
        self.model = Model()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.generation.max_tokens = 1024
        provider.chat_with_retry = self.model
        provider.chat_stream_with_retry = self.model
        self.loop = AgentLoop(
            bus=MessageBus(), provider=provider, workspace=workspace, model="test-model",
        )
        self.driver = PlanDriver(
            PlanStore.for_workspace(workspace), self.loop.bus, LoopPlanHost(self.loop),
            held_wake_after_s=wake_after,
        )
        self.loop.attach_plan_driver(self.driver)
        set_plan_probe(self.driver)
        # Gli aiutanti vivi della sessione, come li darebbe il manager:
        # task_id → (state, plan_id, plan_step).
        self.helpers: dict[str, tuple[str, str | None, str | None]] = {}
        self.groups: dict[str, tuple[str | None, str | None]] = {}
        subagents = self.loop.subagents
        subagents.live_helper_states = (  # type: ignore[method-assign]
            lambda key: dict(self.helpers) if key == KEY else {}
        )
        subagents.helper_plan_step = self.groups.get  # type: ignore[method-assign]
        subagents.get_running_ids_by_session = (  # type: ignore[method-assign]
            lambda key: frozenset(self.helpers) if key == KEY else frozenset()
        )
        self.marked: list[str] = []
        subagents.mark_handled = self.marked.append  # type: ignore[method-assign]
        self.plan_id = ""

    async def start(self) -> None:
        await propose(self.driver, "Rifare la wiki", STEPS)
        plan = self.driver.store.open_plan_for("piante")
        assert plan is not None
        await self.driver.approve(plan.id, plan.version, [])
        step = await asyncio.wait_for(self.loop.bus.consume_inbound(), timeout=2)
        assert step.metadata[INJECTED_EVENT_META] == PLAN_STEP_EVENT
        self.plan_id = plan.id
        self.drain_outbound()

    def helper(self, task_id: str, state: str = "running", step: str | None = "s1") -> None:
        self.helpers[task_id] = (state, self.plan_id, step)
        self.groups[task_id] = (self.plan_id, step)

    def drain_outbound(self) -> list[Any]:
        out = []
        while not self.loop.bus.outbound.empty():
            out.append(self.loop.bus.outbound.get_nowait())
        return out

    async def dispatch(self, msg: InboundMessage) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        self.loop._pending_queues[KEY] = queue
        await asyncio.wait_for(self.loop._dispatch(msg, queue), timeout=5)

    def rows(self) -> list[dict[str, Any]]:
        session = self.loop.sessions.get_or_create(KEY)
        return [m for m in session.messages if m.get(INJECTED_EVENT_META) == "subagent_result"]


def _result(task_id: str, status: str = "ok", key: str = KEY) -> InboundMessage:
    return InboundMessage(
        channel="system", sender_id="subagent", chat_id=f"websocket:{key}",
        content=f"[Subagent '{task_id}' finished]\n\nResult:\nRESULT-{task_id}",
        session_key_override=key,
        metadata={
            INJECTED_EVENT_META: "subagent_result",
            "subagent_task_id": task_id,
            "subagent_status": status,
        },
    )


def _assistant_bubbles(out: list[Any]) -> list[Any]:
    return [m for m in out if m.content and not m.metadata.get("_progress")]


# ── due aiutanti nello stesso passo ──────────────────────────────────────────


async def test_two_helpers_of_a_step_wake_jenny_once(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")

    await rig.dispatch(_result("h1"))
    assert rig.model.calls == [], "the first result costs no model call"
    assert _assistant_bubbles(rig.drain_outbound()) == []
    assert [r["subagent_task_id"] for r in rig.rows()] == ["h1"]
    assert rig.driver.held_results(KEY) == ("h1",)
    assert rig.marked == [], "handled only at the wake-up"

    # h1 è finito; h2 sta annunciando (il suo task non è ancora chiuso).
    del rig.helpers["h1"]
    await rig.dispatch(_result("h2"))
    assert len(rig.model.calls) == 1
    assert rig.model.saw("RESULT-h1") and rig.model.saw("RESULT-h2")
    assert [m.content for m in _assistant_bubbles(rig.drain_outbound())] == ["Fatto."]
    assert sorted(rig.marked) == ["h1", "h2"]
    assert rig.driver.held_results(KEY) == ()


async def test_a_held_result_is_not_judged_by_the_plan(workspace: Path) -> None:
    """La riga tenuta non è un turno: niente giudizio di fine turno, niente promemoria."""
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    judged: list[Any] = []
    real = rig.driver.on_turn_end

    async def spy(*args: Any, **kw: Any) -> None:
        judged.append(args)
        await real(*args, **kw)

    rig.driver.on_turn_end = spy  # type: ignore[method-assign]
    await rig.dispatch(_result("h1"))
    assert judged == []
    assert rig.loop.bus.inbound.empty()


async def test_a_duplicate_of_a_held_result_stays_held(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    await rig.dispatch(_result("h1"))
    assert rig.model.calls == []
    assert [r["subagent_task_id"] for r in rig.rows()] == ["h1"]


async def test_a_helper_of_another_step_is_not_a_sibling(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("old", step="s0")
    await rig.dispatch(_result("h1"))
    assert len(rig.model.calls) == 1


# ── rete di sicurezza ────────────────────────────────────────────────────────


async def test_a_stalled_sibling_does_not_hold_and_its_stall_wakes(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    rig.helper("h3")
    await rig.dispatch(_result("h1"))
    assert rig.model.calls == []

    # h2 si ferma (il watchdog), h3 lavora ancora: si aspetta h3.
    rig.helper("h2", state="stalled")
    rig.loop.subagents._notify_stalls({KEY: ["h2"]})
    await asyncio.sleep(0.05)
    assert rig.loop.bus.inbound.empty()

    # Anche h3 si ferma: nessun fratello lavora più, Jenny si sveglia.
    rig.helper("h3", state="stalled")
    rig.loop.subagents._notify_stalls({KEY: ["h3"]})
    await wait_until(lambda: not rig.loop.bus.inbound.empty(), timeout=2)
    wake = rig.loop.bus.inbound.get_nowait()
    assert wake.metadata[PLAN_WAKE_META] == "stalled"
    assert wake.metadata[INJECTED_EVENT_META] == PLAN_STEP_EVENT
    assert "1 helper result(s)" in wake.content and "2 helper(s)" in wake.content

    await rig.dispatch(wake)
    assert len(rig.model.calls) == 1
    assert rig.model.saw("RESULT-h1")
    assert rig.marked == ["h1"]
    assert rig.driver.held_results(KEY) == ()


async def test_a_result_with_only_stalled_siblings_wakes_at_once(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2", state="stalled")
    await rig.dispatch(_result("h1"))
    assert len(rig.model.calls) == 1


async def test_the_timer_wakes_jenny(workspace: Path) -> None:
    rig = Rig(workspace, wake_after=0.05)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    assert rig.model.calls == []

    await wait_until(lambda: not rig.loop.bus.inbound.empty(), timeout=2)
    wake = rig.loop.bus.inbound.get_nowait()
    assert wake.metadata[PLAN_WAKE_META] == "timeout"
    await rig.dispatch(wake)
    assert len(rig.model.calls) == 1
    assert rig.marked == ["h1"]
    # Uno solo: il turno l'ha spento, e non riparte da solo.
    await asyncio.sleep(0.15)
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop))


def _drain_inbound(loop: AgentLoop) -> list[InboundMessage]:
    out = []
    while not loop.bus.inbound.empty():
        out.append(loop.bus.inbound.get_nowait())
    return out


async def test_stopping_the_plan_disarms_the_timer(workspace: Path) -> None:
    rig = Rig(workspace, wake_after=0.05)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    rig.loop.subagents.cancel_by_session = _cancel_none  # type: ignore[method-assign]
    await rig.driver.stop(rig.plan_id, "keep")
    await asyncio.sleep(0.15)
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop))
    # La riga tenuta resta in storia: il primo turno la marca.
    assert rig.driver.held_results(KEY) == ("h1",)


async def _cancel_none(key: str, *, grace_s: float = 2.0) -> int:
    return 0


async def test_a_user_turn_in_between_releases_the_held_results(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    await rig.dispatch(InboundMessage(
        channel="websocket", sender_id="u", chat_id=KEY, content="come va?",
    ))
    assert rig.model.saw("RESULT-h1")
    assert rig.marked == ["h1"]
    assert rig.driver.held_results(KEY) == ()


# ── D4: errori e budget svegliano subito; fuori dai piani niente cambia ──────


@pytest.mark.parametrize("status", ["budget", "error", "cancelled"])
async def test_a_result_that_is_not_ok_wakes_at_once(workspace: Path, status: str) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1", status=status))
    assert len(rig.model.calls) == 1
    assert rig.marked == ["h1"]


async def test_the_wake_up_after_a_budget_also_releases_the_held(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    rig.helper("h3")
    await rig.dispatch(_result("h1"))
    del rig.helpers["h1"]
    await rig.dispatch(_result("h2", status="budget"))
    assert len(rig.model.calls) == 1
    assert rig.model.saw("RESULT-h1") and rig.model.saw("RESULT-h2")
    assert sorted(rig.marked) == ["h1", "h2"]


async def test_outside_a_plan_every_result_has_its_turn(workspace: Path) -> None:
    rig = Rig(workspace)
    # Nessun piano approvato: due aiutanti, due turni.
    rig.helpers["h1"] = ("running", None, None)
    rig.helpers["h2"] = ("running", None, None)
    rig.groups["h1"] = rig.groups["h2"] = (None, None)
    await rig.dispatch(_result("h1"))
    assert len(rig.model.calls) == 1
    await rig.dispatch(_result("h2"))
    assert len(rig.model.calls) == 2
    assert rig.marked == ["h1", "h2"]


# ── il gancio del watchdog nel manager vero ──────────────────────────────────


def _manager(tmp_path: Path, **kw: Any) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    defaults: dict[str, Any] = dict(
        provider=provider, workspace=tmp_path, bus=MessageBus(), model="test-model",
        max_tool_result_chars=16_000, stall_threshold_s=0.05, stall_check_interval_s=0.01,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


async def test_the_watchdog_tells_the_listener_and_the_plan_step_is_known(
    tmp_path: Path,
) -> None:
    from jenny.agent.runner import AgentRunResult

    sm = _manager(tmp_path)
    block = asyncio.Event()

    async def run(_spec: Any) -> AgentRunResult:
        await block.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = run  # type: ignore[method-assign]
    sm.bus.publish_inbound = _noop  # type: ignore[method-assign]
    heard: list[tuple[str, list[str]]] = []
    sm.set_stall_listener(lambda key, ids: heard.append((key, list(ids))))
    token = bind_request_context(RequestContext(
        channel="websocket", chat_id=KEY, session_key=KEY,
        metadata={"plan_id": "plan-1", "plan_step": "s2"},
    ))
    try:
        await sm.spawn("slow work", session_key=KEY)
    finally:
        reset_request_context(token)
    [task_id] = list(sm._task_statuses)
    assert sm.helper_plan_step(task_id) == ("plan-1", "s2")
    assert sm.live_helper_states(KEY) == {task_id: ("running", "plan-1", "s2")}

    await wait_until(lambda: heard == [(KEY, [task_id])], timeout=2)
    assert sm.live_helper_states(KEY)[task_id][0] == "stalled"

    block.set()
    await asyncio.gather(*sm._running_tasks.values(), return_exceptions=True)
    await sm._cancel_stall_watchdog()
    assert sm.live_helper_states(KEY) == {}
    # Finito, la spec del lineage dice ancora per quale passo lavorava.
    assert sm.helper_plan_step(task_id) == ("plan-1", "s2")


async def _noop(_msg: Any) -> None:
    return None


async def test_a_broken_listener_does_not_stop_the_watchdog(tmp_path: Path) -> None:
    sm = _manager(tmp_path)

    def boom(_key: str, _ids: list[str]) -> None:
        raise RuntimeError("listener down")

    heard: list[str] = []
    sm.set_stall_listener(boom)
    sm._notify_stalls({"a": ["t1"]})
    sm.set_stall_listener(lambda key, _ids: heard.append(key))
    sm._notify_stalls({"a": ["t1"], "b": ["t2"]})
    assert heard == ["a", "b"]


# ── il tool ``notebook`` fuori dai quaderni ──────────────────────────────────


async def test_the_notebook_tool_is_offered_only_in_a_notebook_chat(workspace: Path) -> None:
    rig = Rig(workspace)
    offered: list[set[str]] = []
    real = rig.model.__call__

    async def model(**kw: Any) -> LLMResponse:
        offered.append({d.get("function", d).get("name") for d in (kw.get("tools") or [])})
        return await real(**kw)

    provider = rig.loop.provider
    provider.chat_with_retry = model
    provider.chat_stream_with_retry = model
    assert "notebook" in rig.loop.tools.tool_names

    await rig.loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="ciao",
    ))
    assert "notebook" not in offered[-1]
    await rig.loop._process_message(InboundMessage(
        channel="websocket", sender_id="u", chat_id=KEY, content="ciao",
    ), session_key=KEY)
    assert "notebook" in offered[-1]
    # Il rientro di un aiutante nella chat personale: il percorso di sistema.
    await rig.loop._process_message(_result("p1", key="websocket:default"))
    assert "notebook" not in offered[-1]
    await rig.loop._process_message(_result("p2"))
    assert "notebook" in offered[-1]


async def test_no_wake_up_for_results_that_slash_new_threw_away(workspace: Path) -> None:
    """``/new`` svuota la conversazione: il risveglio di sicurezza non parla di
    righe che non ci sono più, e il timer non resta appeso."""
    rig = Rig(workspace, wake_after=0.05)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    session = rig.loop.sessions.get_or_create(KEY)
    session.clear()  # quel che fa ``cmd_new`` alla conversazione
    rig.loop.sessions.save(session)
    await asyncio.sleep(0.15)
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop))
    assert rig.driver.held_results(KEY) == ()


# ── revisione: gruppi senza passo, invii veri, riarmo, corse, /new, shutdown ──


async def test_a_user_turn_helper_during_a_plan_is_not_held(workspace: Path) -> None:
    """Un aiutante lanciato da un turno dell'utente non ha passo: non si tiene,
    e non tiene gli altri."""
    rig = Rig(workspace)
    await rig.start()
    rig.helpers["u1"] = ("running", None, None)
    rig.groups["u1"] = (None, None)
    rig.helpers["u2"] = ("running", None, None)
    rig.groups["u2"] = (None, None)
    rig.helper("h1")
    await rig.dispatch(_result("u1"))
    assert len(rig.model.calls) == 1, "no step: its own turn"
    # Un aiutante del passo con solo fratelli senza passo: nessuno da aspettare.
    await rig.dispatch(_result("h1"))
    assert len(rig.model.calls) == 2


async def test_a_helper_with_a_plan_but_no_step_is_not_held(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("x1", step=None)
    rig.helper("x2", step=None)
    await rig.dispatch(_result("x1"))
    assert len(rig.model.calls) == 1


def _budget_turn(loop: AgentLoop, *exchange: tuple[str, str]) -> None:
    """Un turno d'annuncio finto che fa i ``subagent_send`` dati: (task_id, risultato)."""

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        sent: list[dict[str, Any]] = []
        for n, (target, result) in enumerate(exchange):
            sent.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": f"c{n}", "type": "function",
                "function": {"name": "subagent_send",
                             "arguments": f'{{"task_id": "{target}", "message": "go on"}}'},
            }]})
            sent.append({"role": "tool", "tool_call_id": f"c{n}", "name": "subagent_send",
                         "content": result})
        sent.append({"role": "assistant", "content": "Ripreso."})
        return ("Ripreso.", ["subagent_send"], [*initial_messages, *sent], "stop", False)

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]


def _budget(task_id: str = "b1") -> InboundMessage:
    msg = _result(task_id, status="budget", key="websocket:default")
    msg.metadata["subagent_lineage_id"] = "lin-b1"
    return msg


async def test_a_budget_announce_is_silent_only_after_a_real_send_to_it(
    workspace: Path,
) -> None:
    from jenny.agent.turn_types import TurnDisposition

    rig = Rig(workspace)
    resumed = "Resumed subagent [b1] from its saved conversation (new id: n1)."
    # Rifiutato: l'utente deve sapere che l'aiutante si è fermato.
    _budget_turn(rig.loop, ("b1", "Cannot send to subagent [b1]: the pool is full."))
    outcome = await rig.loop._process_message(_budget("b1"))
    assert outcome.disposition is TurnDisposition.DELIVERED
    # Riuscito, ma a un altro aiutante.
    _budget_turn(rig.loop, ("zz", resumed))
    outcome = await rig.loop._process_message(_budget("b2"))
    assert outcome.disposition is TurnDisposition.DELIVERED
    # Riuscito a questo, per lineage: in chat parla solo il lavoro ripreso.
    _budget_turn(rig.loop, ("zz", "Refused: you already sent this."), ("lin-b1", resumed))
    outcome = await rig.loop._process_message(_budget("b3"))
    assert outcome.disposition is TurnDisposition.SILENT


async def test_a_result_arriving_while_blocked_is_held_until_the_answer(workspace: Path) -> None:
    """Fino al 26/10 un resoconto arrivato a piano fermo apriva un turno senza i
    tool del passo: ora si tiene, senza timer, e la risposta lo sveglia."""
    from jenny.agent.turn_types import TurnDisposition

    rig = Rig(workspace, wake_after=0.1)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    assert "Error" not in await rig.driver.blocked(KEY, "Quale vaso?")
    outcome = await rig.loop._process_message(_result("h1"))
    assert outcome.disposition is TurnDisposition.SILENT, "tenuto: nessun turno a piano fermo"
    assert rig.driver.held_results(KEY) == ("h1",)
    await asyncio.sleep(0.25)
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop)), "niente timer"
    await rig.driver.answer(rig.plan_id, "Quello rosso")
    seen: list[InboundMessage] = []

    def woke() -> bool:
        seen.extend(_drain_inbound(rig.loop))
        return any(m.metadata.get(PLAN_WAKE_META) for m in seen)

    await wait_until(woke, timeout=2)


async def test_a_block_disarms_and_the_answer_rearms_the_timer(workspace: Path) -> None:
    rig = Rig(workspace, wake_after=0.1)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    assert "Error" not in await rig.driver.blocked(KEY, "Quale vaso?")
    await asyncio.sleep(0.25)
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop))

    await rig.driver.answer(rig.plan_id, "Quello rosso")
    seen: list[InboundMessage] = []

    def woke() -> bool:
        seen.extend(_drain_inbound(rig.loop))
        return any(m.metadata.get(PLAN_WAKE_META) == "timeout" for m in seen)

    await wait_until(woke, timeout=2)


async def test_a_wake_up_does_not_fire_for_a_group_replaced_meanwhile(workspace: Path) -> None:
    """Durante la lettura del piano un turno prende i tenuti e un resoconto nuovo
    apre un altro gruppo: quel risveglio non è per lui."""
    from jenny.plan.driver import _HeldResults

    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    load = rig.driver.store.load

    def load_and_replace(plan_id: str) -> Any:
        # Un oggetto nuovo, con una riga che in storia c'è: solo l'identità
        # lo distingue da quello per cui il risveglio era partito.
        rig.driver._held[KEY] = _HeldResults(rig.plan_id, "s1", ["h1"], 0.0)
        return load(plan_id)

    rig.driver.store.load = load_and_replace  # type: ignore[method-assign]
    await rig.driver._wake_held(KEY, "timeout")
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop))


async def test_after_slash_new_the_next_turn_does_not_mark_the_held(workspace: Path) -> None:
    rig = Rig(workspace)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    session = rig.loop.sessions.get_or_create(KEY)
    session.clear()  # quel che fa ``cmd_new`` alla conversazione
    rig.loop.sessions.save(session)
    await rig.dispatch(InboundMessage(
        channel="websocket", sender_id="u", chat_id=KEY, content="ricominciamo",
    ))
    assert rig.marked == [], "Jenny never saw it"
    assert rig.driver.held_results(KEY) == ()


async def test_shutdown_cancels_the_wake_up_timers(workspace: Path) -> None:
    rig = Rig(workspace, wake_after=0.1)
    await rig.start()
    rig.helper("h1")
    rig.helper("h2")
    await rig.dispatch(_result("h1"))
    timer = rig.driver._held[KEY].timer
    assert timer is not None and not timer.done()
    await rig.loop.shutdown(timeout_s=1.0)
    assert timer.done()
    await asyncio.sleep(0.2)
    assert not any(m.metadata.get(PLAN_WAKE_META) for m in _drain_inbound(rig.loop))
    # Chiuso: nessun timer nuovo si arma più.
    rig.helper("h3")
    rig.driver.hold_helper_result(KEY, "h2")
    assert rig.driver._held[KEY].timer is None
