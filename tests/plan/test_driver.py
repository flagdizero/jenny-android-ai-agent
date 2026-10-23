"""Il driver dei piani a lista, con un ospite finto: il ciclo intero senza il loop.

L'ospite finto risponde alle domande che il driver fa all'agente (turno in volo,
aiutanti, cancellazione, goal, cartella del quaderno, note); il bus finto
raccoglie quel che il driver pubblica. Lo store e le foto sono veri, su un
quaderno vero in una cartella temporanea: le foto sono il punto del piano, e
fingerle vorrebbe dire provare un'altra cosa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.plan.driver import (
    PLAN_ANSWER_META,
    PLAN_ID_META,
    PLAN_STEP_META,
    PlanCommandError,
    PlanDriver,
    plan_view,
)
from jenny.plan.store import PlanStore
from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    PLAN_REMINDER_EVENT,
    PLAN_STEP_EVENT,
)
from tests.support.plan import (
    KEY,
    STEPS,
    FakeBus,
    FakeHost,
    propose,
    write_file,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/index.md", "# indice")
    write_file(nb, "wiki/acero.md", "acero v1")
    write_file(nb, "raw/journal/20261023.md", "diario")
    return ws


@pytest.fixture()
def host(workspace: Path) -> FakeHost:
    return FakeHost(workspace)


@pytest.fixture()
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture()
def driver(workspace: Path, host: FakeHost, bus: FakeBus) -> PlanDriver:
    return PlanDriver(PlanStore.for_workspace(workspace), bus, host)




async def _propose(driver: PlanDriver, steps: list[dict[str, Any]] | None = None) -> Any:
    text = await propose(driver, "Rifare la wiki", steps or STEPS)
    assert "card" in text, text
    plan = driver.store.open_plan_for("piante")
    assert plan is not None
    return plan


async def _approve(driver: PlanDriver) -> Any:
    plan = await _propose(driver)
    await driver.approve(plan.id, plan.version, [])
    return driver.store.load(plan.id)


def _step_messages(bus: FakeBus, event: str = PLAN_STEP_EVENT) -> list[Any]:
    return [m for m in bus.inbound if m.metadata.get(INJECTED_EVENT_META) == event]


# ── proposta ────────────────────────────────────────────────────────────────


async def test_propose_writes_a_proposal_and_tells_the_model_to_wait(
    driver: PlanDriver, bus: FakeBus
) -> None:
    text = await propose(driver, "Rifare la wiki", STEPS)
    assert "do not start" in text
    plan = driver.store.open_plan_for("piante")
    assert plan is not None and plan.status == "proposed"
    assert driver.plan_open(KEY) and not driver.plan_running(KEY)
    assert any(m.metadata.get("_plan_changed") for m in bus.outbound)


async def test_a_second_proposal_supersedes_the_first(driver: PlanDriver) -> None:
    first = await _propose(driver)
    second = await _propose(
        driver, [*STEPS[:2], {"title": "Nuovo", "detail": "x", "done_when": "x"}]
    )
    assert second.version == 2 and second.replaces == first.id
    assert driver.store.load(first.id).status == "superseded"
    assert [s.tag for s in second.steps] == [None, None, "new"]


async def test_propose_refuses_outside_a_notebook_and_next_to_a_goal(
    driver: PlanDriver, host: FakeHost
) -> None:
    assert (await driver.propose("unified:default", "x", STEPS)).startswith("Error")
    host.goals.add(KEY)
    assert "sustained goal" in await propose(driver, "x", STEPS)
    assert driver.store.open_plan_for("piante") is None


async def test_a_malformed_proposal_returns_the_reason_to_the_model(driver: PlanDriver) -> None:
    text = await propose(driver, "Rifare", [{"title": "solo uno", "detail": "x"}])
    assert text.startswith("Error:") and "between 2 and 12" in text


# ── approva ─────────────────────────────────────────────────────────────────


async def test_approve_takes_photo_a_and_opens_step_one(driver: PlanDriver, bus: FakeBus) -> None:
    plan = await _approve(driver)
    assert plan.status == "running"
    assert plan.photos["start"]
    assert plan.steps[0].status == "now" and plan.steps[0].photo_before
    [msg] = _step_messages(bus)
    assert msg.channel == "system" and msg.session_key == KEY
    assert msg.chat_id == "websocket:project:piante"
    assert msg.metadata[PLAN_ID_META] == plan.id and msg.metadata[PLAN_STEP_META] == "s1"
    assert "Step 1: Cerco Acero e Ginkgo" in msg.content
    assert "Due ricerche" in msg.content
    assert driver.step_open(KEY) and driver.plan_running(KEY)


async def test_approve_refuses_a_stale_version_and_a_busy_notebook(
    driver: PlanDriver, host: FakeHost
) -> None:
    plan = await _propose(driver)
    with pytest.raises(PlanCommandError) as info:
        await driver.approve(plan.id, plan.version + 1, [])
    assert info.value.code == "conflict"
    host.busy.add(KEY)
    with pytest.raises(PlanCommandError) as info:
        await driver.approve(plan.id, plan.version, [])
    assert info.value.code == "busy"
    assert driver.store.load(plan.id).status == "proposed"


async def test_approving_a_superseded_version_is_a_conflict(driver: PlanDriver) -> None:
    first = await _propose(driver)
    await _propose(driver)
    with pytest.raises(PlanCommandError) as info:
        await driver.approve(first.id, first.version, [])
    assert info.value.code == "conflict"


async def test_cut_steps_are_skipped_and_not_counted(driver: PlanDriver, bus: FakeBus) -> None:
    plan = await _propose(driver)
    await driver.approve(plan.id, plan.version, ["s1"])
    plan = driver.store.load(plan.id)
    assert plan.steps[0].status == "cut" and plan.steps[1].status == "now"
    view = plan_view(plan)
    assert view["total"] == 2 and view["current_step_id"] == "s2"
    assert "step 1 of 2" in _step_messages(bus)[0].content


# ── il ciclo ────────────────────────────────────────────────────────────────


async def _close_step(driver: PlanDriver, workspace: Path, rel: str) -> None:
    write_file(workspace / "wikis" / "piante", rel, f"scritto da {rel}")
    text = await driver.step_done(KEY, f"scritto {rel}")
    assert "closed" in text, text
    await driver.on_turn_end(KEY, {})


async def test_three_steps_run_to_done_with_a_photo_per_step(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _approve(driver)
    await _close_step(driver, workspace, "raw/research/acero.md")
    await _close_step(driver, workspace, "wiki/ginkgo.md")
    await _close_step(driver, workspace, "wiki/link.md")
    plan = driver.store.load(plan.id)
    assert plan.status == "done"
    assert [s.status for s in plan.steps] == ["done", "done", "done"]
    assert plan.photos["end"]
    assert len(_step_messages(bus)) == 3
    assert plan.steps[1].extra["files"] == {"added": 1, "changed": 0, "removed": 0}
    assert driver.step_files(plan.id, "s2") == {
        "added": ["wiki/ginkgo.md"], "changed": [], "removed": [],
    }
    changes = driver.changes(plan.id)
    assert changes["added"] == ["raw/research/acero.md", "wiki/ginkgo.md", "wiki/link.md"]
    assert changes["groups"]["raw/research/acero.md"] == "sources"
    assert not driver.plan_open(KEY)
    assert plan_view(plan)["can_rollback"] is True


async def test_closing_a_step_with_helpers_running_is_refused(
    driver: PlanDriver, host: FakeHost
) -> None:
    await _approve(driver)
    host.helpers[KEY] = {"t1"}
    text = await driver.step_done(KEY, "fatto")
    assert text.startswith("Not yet")
    assert driver.step_open(KEY)


async def test_an_open_step_with_helpers_running_waits(
    driver: PlanDriver, host: FakeHost, bus: FakeBus
) -> None:
    await _approve(driver)
    host.helpers[KEY] = {"t1"}
    await driver.on_turn_end(KEY, {})
    assert _step_messages(bus, PLAN_REMINDER_EVENT) == []
    assert driver.step_open(KEY)


async def test_a_stalled_step_gets_one_reminder_then_blocks(
    driver: PlanDriver, bus: FakeBus
) -> None:
    plan = await _approve(driver)
    await driver.on_turn_end(KEY, {INJECTED_EVENT_META: PLAN_STEP_EVENT})
    [reminder] = _step_messages(bus, PLAN_REMINDER_EVENT)
    assert "only reminder" in reminder.content
    await driver.on_turn_end(KEY, dict(reminder.metadata))
    assert len(_step_messages(bus, PLAN_REMINDER_EVENT)) == 1
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked"
    assert plan.blocked["reason"] == "stalled" and plan.blocked["question"]
    assert plan.steps[0].status == "blocked"


async def test_a_pending_message_defers_the_judgement(driver: PlanDriver, bus: FakeBus) -> None:
    await _approve(driver)
    await driver.on_turn_end(KEY, {}, pending=True)
    assert _step_messages(bus, PLAN_REMINDER_EVENT) == []


async def test_pause_waits_for_the_step_then_resume_opens_the_next(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _approve(driver)
    await driver.pause(plan.id)
    assert driver.store.load(plan.id).status == "pausing"
    await _close_step(driver, workspace, "wiki/acero.md")
    plan = driver.store.load(plan.id)
    assert plan.status == "paused" and plan.steps[1].status == "pending"
    assert len(_step_messages(bus)) == 1
    await driver.resume(plan.id)
    plan = driver.store.load(plan.id)
    assert plan.status == "running" and plan.steps[1].status == "now"
    assert len(_step_messages(bus)) == 2


async def test_resume_before_the_pause_lands_cancels_it(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await driver.pause(plan.id)
    await driver.resume(plan.id)
    assert driver.store.load(plan.id).status == "running"


async def test_stop_cancels_turn_and_helpers_and_takes_photo_b(
    driver: PlanDriver, host: FakeHost
) -> None:
    plan = await _approve(driver)
    host.helpers[KEY] = {"t1", "t2"}
    host.busy.add(KEY)
    assert await driver.stop(plan.id, "keep") is None
    plan = driver.store.load(plan.id)
    assert host.cancelled == [KEY]
    assert plan.status == "stopped" and plan.photos["end"]
    assert [s.status for s in plan.steps] == ["interrupted", "pending", "pending"]
    assert plan.history[-1]["cancelled"] == 3
    assert not driver.plan_open(KEY)


async def test_a_step_message_left_on_the_bus_after_stop_is_stale(
    driver: PlanDriver, bus: FakeBus
) -> None:
    plan = await _approve(driver)
    [msg] = _step_messages(bus)
    assert driver.step_message_current(msg.metadata)
    await driver.stop(plan.id, "keep")
    assert not driver.step_message_current(msg.metadata)


async def test_blocked_then_answer_restarts_the_step(driver: PlanDriver, bus: FakeBus) -> None:
    plan = await _approve(driver)
    text = await driver.blocked(KEY, "Quale acero?")
    assert "waits for the user" in text
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["question"] == "Quale acero?"
    assert not driver.step_open(KEY)
    await driver.answer(plan.id, "Quello giapponese")
    plan = driver.store.load(plan.id)
    assert plan.status == "running" and plan.blocked is None
    assert plan.steps[0].status == "now"
    answer = bus.inbound[-1]
    assert answer.channel == "websocket" and answer.chat_id == KEY
    assert answer.content == "Quello giapponese"
    assert answer.metadata[PLAN_ANSWER_META] == plan.id


async def test_a_message_typed_while_blocked_is_the_answer(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await driver.blocked(KEY, "Quale acero?")
    metadata: dict[str, Any] = {}
    await driver.before_user_turn(KEY, metadata)
    assert metadata[PLAN_ANSWER_META] == plan.id
    assert driver.store.load(plan.id).status == "running"


async def test_a_paused_plan_makes_user_turns_read_only(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await driver.pause(plan.id)
    await driver.blocked(KEY, "?")  # pausing → blocked è ammesso
    await driver.answer(plan.id, "ok")
    await driver.pause(plan.id)
    await driver.step_done(KEY, "x")
    await driver.on_turn_end(KEY, {})
    assert driver.store.load(plan.id).status == "paused"
    metadata: dict[str, Any] = {}
    await driver.before_user_turn(KEY, metadata)
    assert metadata[WORKSPACE_READONLY_METADATA_KEY] is True


# ── riavvio ─────────────────────────────────────────────────────────────────


async def test_restart_mid_step_pauses_with_a_note(
    workspace: Path, host: FakeHost, bus: FakeBus
) -> None:
    store = PlanStore.for_workspace(workspace)
    first = PlanDriver(store, bus, host)
    plan = await _approve(first)
    # Il processo muore: un driver nuovo sullo stesso disco.
    fresh = PlanDriver(store, FakeBus(), host)
    assert fresh.recover() == [plan.id]
    plan = store.load(plan.id)
    assert plan.status == "paused"
    assert plan.steps[0].status == "pending" and plan.steps[0].photo_before is None
    last = plan.history[-1]
    assert last["reason"] == "restart" and last["step"] == 1 and last["note"]
    assert fresh.held_status(KEY) == "paused"


async def test_restart_after_a_close_photographs_the_step(
    workspace: Path, host: FakeHost, bus: FakeBus
) -> None:
    store = PlanStore.for_workspace(workspace)
    first = PlanDriver(store, bus, host)
    plan = await _approve(first)
    write_file(workspace / "wikis" / "piante", "wiki/nuova.md", "x")
    await first.step_done(KEY, "fatto")  # ...e il gateway muore prima della fine del turno
    PlanDriver(store, FakeBus(), host).recover()
    plan = store.load(plan.id)
    assert plan.status == "paused"
    assert plan.steps[0].status == "done" and plan.steps[0].photo_after
    assert plan.steps[1].status == "pending"


async def test_a_blocked_plan_is_paused_at_boot_too(
    workspace: Path, host: FakeHost, bus: FakeBus
) -> None:
    store = PlanStore.for_workspace(workspace)
    first = PlanDriver(store, bus, host)
    plan = await _approve(first)
    await first.blocked(KEY, "?")
    PlanDriver(store, FakeBus(), host).recover()
    assert store.load(plan.id).status == "paused"


# ── ritorno indietro ────────────────────────────────────────────────────────


async def _finished(driver: PlanDriver, workspace: Path) -> Any:
    plan = await _approve(driver)
    nb = workspace / "wikis" / "piante"
    write_file(nb, "wiki/acero.md", "acero v2")
    await driver.step_done(KEY, "riscritta")
    await driver.on_turn_end(KEY, {})
    write_file(nb, "raw/research/ginkgo.md", "ricerca")
    await driver.step_done(KEY, "ricerca")
    await driver.on_turn_end(KEY, {})
    await driver.step_skip(KEY, "niente da controllare")
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.status == "done"
    return plan


async def test_rollback_refuses_while_jenny_works_or_on_a_stale_summary(
    driver: PlanDriver, workspace: Path, host: FakeHost
) -> None:
    plan = await _finished(driver, workspace)
    host.busy.add(KEY)
    with pytest.raises(PlanCommandError) as info:
        await driver.rollback(plan.id, [], plan.photos["end"])
    assert info.value.code == "conflict"
    host.busy.clear()
    with pytest.raises(PlanCommandError) as info:
        await driver.rollback(plan.id, [], "0" * 16)
    assert info.value.code == "conflict"
    assert (workspace / "wikis/piante/wiki/acero.md").read_text() == "acero v2"


async def test_rollback_then_undo(driver: PlanDriver, workspace: Path, host: FakeHost) -> None:
    plan = await _finished(driver, workspace)
    nb = workspace / "wikis" / "piante"
    preview = await driver.rollback_preview(plan.id)
    assert preview["expect_end"] == plan.photos["end"]
    report = preview["report"]
    assert report["dry_run"] is True
    assert report["groups"]["pages"] == {"restored": 1}
    assert report["groups"]["sources"] == {"removed": 1}
    assert (nb / "wiki/acero.md").read_text() == "acero v2"

    done = await driver.rollback(plan.id, ["sources"], preview["expect_end"])
    assert done["report"]["counts"]["restored"] == 1
    assert done["report"]["kept"] == ["raw/research/ginkgo.md"]
    assert (nb / "wiki/acero.md").read_text() == "acero v1"
    assert (nb / "raw/research/ginkgo.md").exists()
    plan = driver.store.load(plan.id)
    assert plan.status == "rolled_back" and plan_view(plan)["can_undo_rollback"]
    assert host.notes and "rolled the notebook back" in host.notes[-1][1]

    await driver.undo_rollback(plan.id)
    plan = driver.store.load(plan.id)
    assert plan.status == "done" and plan_view(plan)["can_rollback"]
    assert (nb / "wiki/acero.md").read_text() == "acero v2"


async def test_stop_then_rollback(driver: PlanDriver, workspace: Path) -> None:
    plan = await _approve(driver)
    nb = workspace / "wikis" / "piante"
    write_file(nb, "wiki/acero.md", "rovinata a metà")
    result = await driver.stop(plan.id, "rollback")
    assert result is not None and result["report"]["counts"]["restored"] == 1
    assert (nb / "wiki/acero.md").read_text() == "acero v1"
    assert driver.store.load(plan.id).status == "rolled_back"


# ── contesto e convivenza ───────────────────────────────────────────────────


async def test_runtime_lines_in_the_notebook_and_in_the_personal_chat(
    driver: PlanDriver,
) -> None:
    await _approve(driver)
    lines = driver.runtime_lines(KEY)
    assert lines[0].startswith('Plan "Rifare la wiki" (v1, running, step 1 of 3)')
    assert lines[1] == "▶ 1. Cerco Acero e Ginkgo"
    assert "plan_step_done" in lines[-1]
    assert driver.runtime_lines("unified:default") == [
        'Notebook piante: plan "Rifare la wiki" 0/3, running.'
    ]
    assert driver.runtime_lines("project:altro") == []


async def test_the_probe_feeds_long_task_and_the_wall_timeout(driver: PlanDriver) -> None:
    from jenny.session.goal_state import (
        plan_open_in_session,
        set_plan_probe,
        sustained_goal_turn,
    )

    set_plan_probe(driver)
    try:
        assert not sustained_goal_turn({}, session_key=KEY)
        await _propose(driver)
        assert plan_open_in_session(KEY)
        assert not sustained_goal_turn({}, session_key=KEY)
        plan = driver.store.open_plan_for("piante")
        await driver.approve(plan.id, plan.version, [])
        assert sustained_goal_turn({}, session_key=KEY)
        assert not sustained_goal_turn({}, session_key="project:altro")
    finally:
        set_plan_probe(None)


async def test_long_task_refuses_next_to_an_open_plan(driver: PlanDriver, tmp_path: Path) -> None:
    from jenny.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from jenny.agent.tools.long_task import LongTaskTool
    from jenny.session.goal_state import set_plan_probe
    from jenny.session.manager import SessionManager

    tool = LongTaskTool(SessionManager(tmp_path / "ws2"))
    tool.set_context(RequestContext(channel="websocket", chat_id=KEY, session_key=KEY))
    token = bind_request_context(RequestContext(channel="websocket", chat_id=KEY, session_key=KEY))
    set_plan_probe(driver)
    try:
        await _propose(driver)
        text = await tool.execute(goal="Rifai tutto")
        assert text.startswith("Error: this notebook has an open plan")
    finally:
        set_plan_probe(None)
        reset_request_context(token)


async def test_notebook_rename_and_delete_follow_the_index(driver: PlanDriver) -> None:
    await _propose(driver)
    driver.notebook_renamed("piante", "giardino")
    assert driver.plan_open("project:giardino") and not driver.plan_open(KEY)
    driver.notebook_deleted("giardino")
    assert not driver.plan_open("project:giardino")
    assert driver.store.list_all() == []



# ── il promemoria assorbito da un altro turno ───────────────────────────────


async def test_a_reminder_absorbed_by_another_turn_still_ends_in_a_block(
    driver: PlanDriver, bus: FakeBus
) -> None:
    """Un messaggio passato davanti al promemoria apre il suo turno, e il
    promemoria gli arriva a metà: nessun turno finisce coi metadati del
    promemoria. Prima il piano restava in corsa per sempre."""
    plan = await _approve(driver)
    await driver.on_turn_end(KEY, {INJECTED_EVENT_META: PLAN_STEP_EVENT})
    assert len(_step_messages(bus, PLAN_REMINDER_EVENT)) == 1
    await driver.on_turn_end(KEY, {"_wants_stream": True})
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["reason"] == "stalled"
    assert len(_step_messages(bus, PLAN_REMINDER_EVENT)) == 1


async def test_a_pause_does_not_hang_in_pausing_on_an_absorbed_reminder(
    driver: PlanDriver,
) -> None:
    plan = await _approve(driver)
    await driver.on_turn_end(KEY, {INJECTED_EVENT_META: PLAN_STEP_EVENT})
    await driver.pause(plan.id)
    assert driver.store.load(plan.id).status == "pausing"
    await driver.on_turn_end(KEY, {})
    assert driver.store.load(plan.id).status == "blocked"


async def test_a_reminder_still_queued_keeps_waiting(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await driver.on_turn_end(KEY, {INJECTED_EVENT_META: PLAN_STEP_EVENT})
    await driver.on_turn_end(KEY, {}, pending=True)
    assert driver.store.load(plan.id).status == "running"


# ── ritorno: errori del disco, crolli, conferme ─────────────────────────────


async def test_a_disk_error_mid_rollback_leaves_no_journal_and_says_so(
    driver: PlanDriver, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.plan.photos import PlanPhotos

    plan = await _finished(driver, workspace)
    nb = workspace / "wikis" / "piante"
    original = PlanPhotos._apply_one
    calls: list[str] = []

    def _disk_full(self: PlanPhotos, op: Any) -> None:
        if calls:
            raise OSError(28, "No space left on device")
        calls.append(op.path)
        original(self, op)

    monkeypatch.setattr(PlanPhotos, "_apply_one", _disk_full)
    preview = await driver.rollback_preview(plan.id)
    with pytest.raises(PlanCommandError) as info:
        await driver.rollback(plan.id, [], preview["expect_end"])
    monkeypatch.setattr(PlanPhotos, "_apply_one", original)

    assert info.value.code == "rollback_failed"
    assert not driver.store.journal_path(plan.id).exists()
    plan = driver.store.load(plan.id)
    assert plan.status == "done"
    failure = plan_view(plan)["rollback_failed"]
    assert failure["kind"] == "rollback" and failure["applied"] == 1 and failure["total"] == 2
    assert "No space" in failure["error"]
    # L'utente scrive di nuovo; un riavvio non riapplica niente.
    write_file(nb, "wiki/acero.md", "acero dell'utente")
    driver.recover()
    assert (nb / "wiki/acero.md").read_text() == "acero dell'utente"
    # Riprovare è sicuro, e toglie il segno del fallimento.
    preview = await driver.rollback_preview(plan.id)
    await driver.rollback(plan.id, [], preview["expect_end"])
    plan = driver.store.load(plan.id)
    assert plan.status == "rolled_back" and "rollback_failed" not in plan_view(plan)
    assert (nb / "wiki/acero.md").read_text() == "acero dell'utente"


async def test_the_reviewed_repro_a_folder_the_user_filled_does_not_break_the_rollback(
    driver: PlanDriver, workspace: Path
) -> None:
    """Il caso della revisione: il piano fa di un file una cartella, l'utente ci
    aggiunge un suo file, il ritorno cadeva con ``IsADirectoryError`` lasciando
    il journal, e al riavvio cancellava il file che l'utente aveva ricreato."""
    nb = workspace / "wikis" / "piante"
    write_file(nb, "notes", "il file di prima")
    plan = await _approve(driver)
    (nb / "notes").unlink()
    write_file(nb, "notes/a.md", "del piano")
    await driver.stop(plan.id, "keep")
    write_file(nb, "notes/b.md", "dell'utente")
    preview = await driver.rollback_preview(plan.id)
    done = await driver.rollback(plan.id, [], preview["expect_end"])
    assert "notes" in done["report"]["left"]
    assert not driver.store.journal_path(plan.id).exists()
    write_file(nb, "notes/a.md", "ricreato dall'utente")
    driver.recover()
    assert (nb / "notes/a.md").read_text() == "ricreato dall'utente"
    assert (nb / "notes/b.md").read_text() == "dell'utente"


async def test_death_between_the_files_and_plan_json_is_finished_at_boot(
    workspace: Path, host: FakeHost, bus: FakeBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.plan import driver as driver_mod

    first = PlanDriver(PlanStore.for_workspace(workspace), bus, host)
    plan = await _finished(first, workspace)
    nb = workspace / "wikis" / "piante"

    original = driver_mod.apply_transition

    def _killed(p: Any, wanted: str, **kw: Any) -> Any:
        # Il gateway muore proprio mentre scrive ``plan.json``: i file sono già rimessi.
        if wanted == "rolled_back":
            raise KeyboardInterrupt("gateway killed before plan.json")
        return original(p, wanted, **kw)

    monkeypatch.setattr(driver_mod, "apply_transition", _killed)
    preview = await first.rollback_preview(plan.id)
    with pytest.raises(KeyboardInterrupt):
        await first.rollback(plan.id, ["sources"], preview["expect_end"])
    monkeypatch.undo()
    assert (nb / "wiki/acero.md").read_text() == "acero v1"
    assert first.store.load(plan.id).status == "done"
    assert first.store.journal_path(plan.id).exists()

    # Il riavvio: un driver nuovo, come al boot.
    host.notes.clear()
    fresh = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), host)
    fresh.recover()
    plan = fresh.store.load(plan.id)
    assert plan.status == "rolled_back"
    assert plan.photos["before_rollback"] == preview["expect_end"]
    assert plan.photos["after_rollback"]
    assert plan.history[-1]["recovered"] is True and plan.history[-1]["keep"] == ["sources"]
    assert not fresh.store.journal_path(plan.id).exists()
    assert len(host.notes) == 1 and "rolled the notebook back" in host.notes[0][1]
    assert (nb / "raw/research/ginkgo.md").exists()
    # E «Annulla il ritorno» funziona dalla foto del recupero.
    await fresh.undo_rollback(plan.id)
    assert (nb / "wiki/acero.md").read_text() == "acero v2"


async def test_death_after_plan_json_does_not_roll_back_twice(
    driver: PlanDriver, workspace: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.plan import driver as driver_mod

    plan = await _finished(driver, workspace)
    journal = driver.store.journal_path(plan.id)
    saved: list[str] = []
    original = driver_mod.apply_transition

    def _spy(p: Any, wanted: str, **kw: Any) -> Any:
        # Il journal deve esserci ancora mentre si scrive ``plan.json``.
        if wanted == "rolled_back":
            saved.append(journal.read_text("utf-8"))
        return original(p, wanted, **kw)

    monkeypatch.setattr(driver_mod, "apply_transition", _spy)
    preview = await driver.rollback_preview(plan.id)
    await driver.rollback(plan.id, [], preview["expect_end"])
    monkeypatch.undo()
    # Il crollo fra ``plan.json`` e la cancellazione del journal.
    journal.write_text(saved[0], "utf-8")
    notes = len(host.notes)
    history = len(driver.store.load(plan.id).history)
    driver.recover()
    plan = driver.store.load(plan.id)
    assert plan.status == "rolled_back" and len(plan.history) == history
    assert len(host.notes) == notes
    assert not journal.exists()


async def test_death_mid_undo_is_finished_at_boot(
    driver: PlanDriver, workspace: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.plan import driver as driver_mod

    plan = await _finished(driver, workspace)
    nb = workspace / "wikis" / "piante"
    preview = await driver.rollback_preview(plan.id)
    await driver.rollback(plan.id, [], preview["expect_end"])

    original = driver_mod.apply_transition

    def _killed(p: Any, wanted: str, **kw: Any) -> Any:
        if kw.get("event") == "rollback_undone":
            raise KeyboardInterrupt("gateway killed before plan.json")
        return original(p, wanted, **kw)

    monkeypatch.setattr(driver_mod, "apply_transition", _killed)
    with pytest.raises(KeyboardInterrupt):
        await driver.undo_rollback(plan.id)
    monkeypatch.undo()
    assert driver.store.load(plan.id).status == "rolled_back"
    driver.recover()
    plan = driver.store.load(plan.id)
    assert plan.status == "done" and not plan.photos["before_rollback"]
    assert "undid the rollback" in host.notes[-1][1]
    assert (nb / "wiki/acero.md").read_text() == "acero v2"


async def test_the_confirmation_sees_a_change_made_after_the_summary(
    driver: PlanDriver, workspace: Path
) -> None:
    """``expect_end`` era la foto B, che dopo il Ferma non cambia: il controllo
    «il quaderno è cambiato» non scattava mai. Ora è la foto dell'anteprima."""
    plan = await _finished(driver, workspace)
    nb = workspace / "wikis" / "piante"
    preview = await driver.rollback_preview(plan.id)
    write_file(nb, "wiki/nuova.md", "scritta dopo il conto")
    write_file(nb, "wiki/acero.md", "riscritta dopo il conto")
    with pytest.raises(PlanCommandError) as info:
        await driver.rollback(plan.id, [], preview["expect_end"])
    assert info.value.code == "conflict"
    with pytest.raises(PlanCommandError) as info:
        await driver.rollback(plan.id, [], plan.photos["end"])
    assert info.value.code == "conflict"
    assert (nb / "wiki/acero.md").read_text() == "riscritta dopo il conto"
    again = await driver.rollback_preview(plan.id)
    assert again["expect_end"] != preview["expect_end"]
    done = await driver.rollback(plan.id, [], again["expect_end"])
    assert done["report"]["left"] == ["wiki/acero.md"]


async def test_an_older_plan_cannot_go_back_while_a_newer_one_holds_the_notebook(
    driver: PlanDriver, workspace: Path
) -> None:
    old = await _finished(driver, workspace)
    newer = await _approve(driver)
    assert newer.status == "running"
    for call in (driver.rollback_preview(old.id), driver.rollback(old.id, [], "x" * 64)):
        with pytest.raises(PlanCommandError) as info:
            await call
        assert info.value.code == "plan_running"
    await driver.stop(newer.id, "keep")
    preview = await driver.rollback_preview(old.id)
    await driver.rollback(old.id, [], preview["expect_end"])
    third = await _approve(driver)
    with pytest.raises(PlanCommandError) as info:
        await driver.undo_rollback(old.id)
    assert info.value.code == "plan_running"
    assert driver.store.load(old.id).status == "rolled_back"
    await driver.stop(third.id, "keep")


async def test_stop_and_rollback_refuse_under_an_abandoned_turn(workspace: Path) -> None:
    """Il turno non muore entro la grazia e il loop lo abbandona: può ancora
    scrivere. Ferma e ritorno ferma il piano, ma il ritorno si rifiuta."""
    host = FakeHost(workspace, abandon=True)
    driver = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), host)
    plan = await _approve(driver)
    nb = workspace / "wikis" / "piante"
    write_file(nb, "wiki/acero.md", "scritta dal passo")
    host.busy.add(KEY)
    result = await driver.stop(plan.id, "rollback")
    assert result is not None and result["rollback_error"]["code"] == "conflict"
    assert driver.store.load(plan.id).status == "stopped"
    assert (nb / "wiki/acero.md").read_text() == "scritta dal passo"
    host.busy.clear()
    host.helpers[KEY] = {"aiutante-zombie"}
    with pytest.raises(PlanCommandError) as info:
        preview = await driver.rollback_preview(plan.id)
        await driver.rollback(plan.id, [], preview["expect_end"])
    assert info.value.code == "conflict"
    assert (nb / "wiki/acero.md").read_text() == "scritta dal passo"


async def test_held_roots_are_computed_once_per_index_change(
    driver: PlanDriver, host: FakeHost, workspace: Path
) -> None:
    assert driver.held_notebook_roots() == {}
    plan = await _approve(driver)
    lookups = host.root_lookups
    roots = driver.held_notebook_roots()
    assert roots == {(workspace / "wikis" / "piante").resolve(): "piante"}
    for _ in range(50):
        driver.held_notebook_roots()
    assert host.root_lookups == lookups + 1
    await driver.stop(plan.id, "keep")
    assert driver.held_notebook_roots() == {}


async def test_rollback_and_undo_each_append_a_line_to_todays_journal(
    driver: PlanDriver, workspace: Path
) -> None:
    """Il diario non torna indietro (append-only): dopo un ritorno, e dopo il suo
    annullamento, il codice ci aggiunge una riga in coda che dice cos'è successo."""
    from datetime import datetime

    from jenny.utils.wiki_paths import journal_page_name

    plan = await _finished(driver, workspace)
    nb = workspace / "wikis" / "piante"
    page = nb / "raw" / "journal" / journal_page_name(datetime.now().astimezone().date())
    before = page.read_text(encoding="utf-8") if page.exists() else ""
    preview = await driver.rollback_preview(plan.id)
    await driver.rollback(plan.id, [], preview["expect_end"])
    after_rollback = page.read_text(encoding="utf-8")
    assert after_rollback.startswith(before)
    [line] = after_rollback[len(before):].strip().splitlines()[-1:]
    assert "[system]" in line and "Rifare la wiki" in line
    await driver.undo_rollback(plan.id)
    after_undo = page.read_text(encoding="utf-8")
    assert after_undo.startswith(after_rollback)
    last = after_undo.strip().splitlines()[-1]
    assert "[system]" in last and "Rifare la wiki" in last and last != line
    # Il diario resta fuori dal ritorno: l'annullamento non ha tolto la riga di prima.
    assert line in after_undo


async def test_the_step_turn_carries_done_when_and_the_stop_on_a_false_premise_rule(
    driver: PlanDriver, bus: FakeBus
) -> None:
    await _approve(driver)
    [msg] = _step_messages(bus)
    assert f"Done when: {STEPS[0]['done_when']}" in msg.content
    assert "premise of the plan proves false" in msg.content
    assert "plan_blocked" in msg.content


async def test_closing_replies_ask_for_a_short_end_of_turn(driver: PlanDriver) -> None:
    """Fra la chiamata e la fine del turno la scheda è «in corso» senza un passo:
    quel tempo è il modello che scrive, e il riepilogo lo fa già la scheda."""
    await _approve(driver)
    assert "at most one short sentence" in await driver.step_done(KEY, "uno")
    await driver.on_turn_end(KEY, {})
    await driver.step_done(KEY, "due")
    await driver.on_turn_end(KEY, {})
    last = await driver.step_done(KEY, "tre")
    assert "one or two short sentences" in last and "summary" not in last


async def test_the_journal_line_uses_the_configured_timezone(
    driver: PlanDriver, workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il fuso di ``agents.defaults.timezone``, come ``journal_append``, non quello di
    sistema: la riga finisce nella pagina del giorno giusto, con l'ora giusta."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from jenny.config.loader import save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context
    from jenny.utils.wiki_paths import journal_page_name

    config = Config()
    config.agents.defaults.timezone = "Pacific/Kiritimati"
    path = tmp_path / "config.json"
    save_config(config, path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    plan = await _finished(driver, workspace)
    preview = await driver.rollback_preview(plan.id)
    now = datetime.now(ZoneInfo("Pacific/Kiritimati"))
    await driver.rollback(plan.id, [], preview["expect_end"])
    page = workspace / "wikis/piante/raw/journal" / journal_page_name(now.date())
    last = page.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert last.startswith(f"- {now.strftime('%H:')}"), last
