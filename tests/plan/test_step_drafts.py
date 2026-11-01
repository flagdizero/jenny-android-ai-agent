"""Un passo che lascia bozze non si chiude: aspetta l'utente, e riparte da solo.

La prova del 23/10: un aiutante contaminato ha cambiato l'``AGENTS.md`` del
quaderno, la scrittura è finita in bozza, e il passo è stato chiuso come fatto
con «Nuovo AGENTS.md» — il file non era scritto e i passi dopo lavoravano con le
regole vecchie. Qui il driver è vero, le bozze sono quelle vere
(``staged_writes``) sotto la radice dell'installazione, l'ospite è finto.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jenny.agent import staged_writes
from jenny.plan.driver import PlanCommandError, PlanDriver
from jenny.plan.store import PlanStore
from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT
from tests.support.aio import wait_until
from tests.support.plan import KEY, STEPS, FakeBus, FakeHost, propose, write_file

TARGET = "wikis/piante/AGENTS.md"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/index.md", "# indice")
    return ws


@pytest.fixture()
def host(workspace: Path) -> FakeHost:
    return FakeHost(workspace)


@pytest.fixture()
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture()
def driver(workspace: Path, host: FakeHost, bus: FakeBus) -> Any:
    staged_writes.clear_change_listeners()
    driver = PlanDriver(PlanStore.for_workspace(workspace), bus, host)
    driver.watch_drafts()
    yield driver
    staged_writes.clear_change_listeners()


async def _running(driver: PlanDriver) -> Any:
    await propose(driver, "Rifare la wiki", STEPS)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    return driver.store.load(plan.id)


def _stage(workspace: Path, text: str = "id: piante\nregole nuove\n", rel: str = TARGET) -> str:
    ref = staged_writes.stage(
        workspace, rel, text.encode(), sources=("web_fetch:example.org",), task_id="t1"
    )
    return ref.id


def _steps_opened(bus: FakeBus) -> list[str]:
    return [
        m.metadata["plan_step"] for m in bus.inbound
        if m.metadata.get(INJECTED_EVENT_META) == PLAN_STEP_EVENT
    ]


async def test_a_step_with_a_draft_waits_and_closes_when_it_is_installed(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    draft = _stage(workspace)
    text = await driver.step_done(KEY, "Nuovo AGENTS.md")
    assert "the change is waiting for the user's approval; the step will close when they decide" in text
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked"
    assert plan.blocked["kind"] == "drafts"
    assert plan.blocked["drafts"] == [{"id": draft, "target": TARGET}]
    assert plan.blocked["step"] == "s1" and plan.steps[0].status == "blocked"
    # La fine del turno non lo porta avanti: aspetta l'utente.
    await driver.on_turn_end(KEY, {})
    assert _steps_opened(bus) == ["s1"]

    target_sha = staged_writes.sha256_of(workspace / TARGET)
    assert staged_writes.install(workspace, draft, target_sha) == "installed"
    await wait_until(lambda: _steps_opened(bus) == ["s1", "s2"])
    plan = driver.store.load(plan.id)
    assert plan.status == "running" and plan.blocked is None
    step = plan.steps[0]
    assert step.status == "done"
    assert step.note == "Nuovo AGENTS.md — change installed"
    # La bozza installata è lavoro del passo: è nella sua foto «dopo».
    assert step.extra["files"] == {"added": 0, "changed": 1, "removed": 0}
    assert plan.steps[1].status == "now"


async def test_a_discarded_draft_closes_the_step_saying_so(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "")
    assert staged_writes.discard(workspace, draft)
    await wait_until(lambda: _steps_opened(bus) == ["s1", "s2"])
    step = driver.store.load(plan.id).steps[0]
    assert step.status == "done" and step.note == "change discarded"
    assert step.extra["files"] == {"added": 0, "changed": 0, "removed": 0}


async def test_two_drafts_close_only_when_both_are_decided(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    first = _stage(workspace)
    second = _stage(workspace, "x", rel="wikis/piante/skills/nuova/SKILL.md")
    await driver.step_skip(KEY, "c'era già")
    assert len(driver.store.load(plan.id).blocked["drafts"]) == 2
    staged_writes.discard(workspace, first)
    await wait_until(
        lambda: driver.store.load(plan.id).steps[0].extra.get("draft_outcomes")
        == {first: "discarded"}
    )
    assert driver.store.load(plan.id).status == "blocked"
    staged_writes.install(workspace, second, None)
    await wait_until(lambda: _steps_opened(bus) == ["s1", "s2"])
    step = driver.store.load(plan.id).steps[0]
    assert step.status == "skipped" and step.note == "c'era già — changes decided"


async def test_drafts_from_before_the_plan_and_outside_the_notebook_do_not_hold_a_step(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    before = _stage(workspace)  # di prima dell'Approva
    # Retrodatata: nello stesso millisecondo dell'Approva il confronto sarebbe un pari.
    meta_path = workspace / ".jenny" / "staged" / before / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_ms"] -= 5_000
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    plan = await _running(driver)
    _stage(workspace, rel="USER.md")  # fuori dal quaderno
    await driver.step_done(KEY, "fatto")
    assert driver.store.load(plan.id).status == "running"
    await driver.on_turn_end(KEY, {})
    assert _steps_opened(bus) == ["s1", "s2"]


async def test_a_draft_left_after_the_step_closed_blocks_at_turn_end(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    await driver.step_done(KEY, "fatto")
    draft = _stage(workspace)
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["drafts"][0]["id"] == draft
    assert plan.steps[0].status == "blocked" and plan.steps[0].note is None
    assert _steps_opened(bus) == ["s1"]
    staged_writes.discard(workspace, draft)
    await wait_until(lambda: _steps_opened(bus) == ["s1", "s2"])
    assert driver.store.load(plan.id).steps[0].note == "fatto — change discarded"


async def test_decided_while_the_step_turn_is_still_running(
    driver: PlanDriver, workspace: Path, host: FakeHost, bus: FakeBus
) -> None:
    """La decisione arriva a turno in volo: il passo si chiude, e lo porta avanti il
    gancio di fine turno, come ogni passo — non due aperture dello stesso passo."""
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    host.busy.add(KEY)
    staged_writes.discard(workspace, draft)
    await wait_until(lambda: driver.store.load(plan.id).status == "running")
    assert _steps_opened(bus) == ["s1"]
    host.busy.discard(KEY)
    await driver.on_turn_end(KEY, {})
    assert _steps_opened(bus) == ["s1", "s2"]


async def test_a_chat_message_is_not_an_answer_to_drafts(
    driver: PlanDriver, workspace: Path
) -> None:
    plan = await _running(driver)
    _stage(workspace)
    await driver.step_done(KEY, "fatto")
    meta: dict[str, Any] = {}
    await driver.before_user_turn(KEY, meta)
    assert meta == {WORKSPACE_READONLY_METADATA_KEY: True}
    assert driver.store.load(plan.id).status == "blocked"
    with pytest.raises(PlanCommandError) as info:
        await driver.answer(plan.id, "vai avanti")
    assert info.value.code == "bad_state"


async def test_runtime_lines_say_the_step_waits_for_its_drafts(
    driver: PlanDriver, workspace: Path
) -> None:
    await _running(driver)
    _stage(workspace)
    await driver.step_done(KEY, "fatto")
    lines = "\n".join(driver.runtime_lines(KEY))
    assert "install or discard" in lines and TARGET in lines


# ── la Pausa, la corsa, il filo, la rete di sicurezza ───────────────────────


async def test_a_pause_asked_before_the_drafts_block_is_kept(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    await driver.pause(plan.id)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    assert driver.store.load(plan.id).status == "blocked"
    staged_writes.discard(workspace, draft)
    await wait_until(lambda: driver.store.load(plan.id).status == "paused")
    assert _steps_opened(bus) == ["s1"]


async def test_a_pause_asked_while_blocked_on_drafts_arrives_when_they_are_decided(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    assert (await driver.pause(plan.id)).status == "blocked"
    # Riprendi toglie la Pausa ricordata; una seconda Pausa la rimette.
    await driver.resume(plan.id)
    await driver.pause(plan.id)
    staged_writes.discard(workspace, draft)
    await wait_until(lambda: driver.store.load(plan.id).status == "paused")
    assert driver.store.load(plan.id).steps[0].status == "done"
    assert _steps_opened(bus) == ["s1"]


async def test_resume_cancels_a_pause_remembered_on_drafts(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    await driver.pause(plan.id)
    await driver.resume(plan.id)
    staged_writes.discard(workspace, draft)
    await wait_until(lambda: _steps_opened(bus) == ["s1", "s2"])
    assert driver.store.load(plan.id).status == "running"


async def test_a_draft_decided_between_the_list_and_the_block_does_not_stick(
    driver: PlanDriver, workspace: Path, host: FakeHost, bus: FakeBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La bozza si installa mentre il passo si chiude: l'avviso arriva prima del
    blocco e non trova niente da sciogliere. Il blocco si riguarda da solo."""
    plan = await _running(driver)
    draft = _stage(workspace)
    staged_writes.clear_change_listeners()
    real = host.notebook_drafts
    calls = {"n": 0}

    def racing(notebook: str) -> list[dict[str, Any]]:
        listed = real(notebook)
        calls["n"] += 1
        if calls["n"] == 1:
            staged_writes.install(workspace, draft, staged_writes.sha256_of(workspace / TARGET))
        return listed

    monkeypatch.setattr(host, "notebook_drafts", racing)
    host.busy.add(KEY)  # il tool gira dentro il turno del passo
    text = await driver.step_done(KEY, "fatto")
    assert text.startswith("Step 1 closed"), text
    plan = driver.store.load(plan.id)
    assert plan.status == "running" and plan.steps[0].note == "fatto — changes decided"
    host.busy.discard(KEY)
    await driver.on_turn_end(KEY, {})
    assert _steps_opened(bus) == ["s1", "s2"]


async def test_an_install_from_a_worker_thread_settles_on_the_loop(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    """La RPC installa in ``to_thread``: l'avviso arriva da un thread e rientra
    nel loop (``call_soon_threadsafe``), che decide."""
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    sha = staged_writes.sha256_of(workspace / TARGET)
    assert await asyncio.to_thread(staged_writes.install, workspace, draft, sha) == "installed"
    await wait_until(lambda: _steps_opened(bus) == ["s1", "s2"])
    assert driver.store.load(plan.id).steps[0].note == "fatto — change installed"


async def test_a_lost_notice_is_caught_by_plan_get_and_by_the_next_user_turn(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    from jenny.webui.commands import CommandContext, dispatch_command

    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    staged_writes.clear_change_listeners()  # l'avviso si perde
    staged_writes.discard(workspace, draft)
    assert driver.store.load(plan.id).status == "blocked"
    ctx = CommandContext(
        get_workspace_root=lambda: workspace, invalidate_session=lambda _k: None,
        busy_session_keys=lambda: (), get_plan_driver=lambda: driver,
    )
    got = await dispatch_command(ctx, "plan.get", {"notebook": "piante"})
    assert got["plan"]["status"] == "running"
    assert _steps_opened(bus) == ["s1", "s2"]

    # Lo stesso, dal gancio di inizio turno.
    second = _stage(workspace)
    await driver.step_done(KEY, "due")
    staged_writes.discard(workspace, second)
    meta: dict[str, Any] = {}
    await driver.before_user_turn(KEY, meta)
    assert driver.store.load(plan.id).steps[1].status == "done"


async def test_a_question_behind_drafts_comes_after_them(
    driver: PlanDriver, workspace: Path
) -> None:
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.blocked(KEY, "Tengo la camelia?")
    plan = driver.store.load(plan.id)
    assert plan.blocked["kind"] == "drafts"
    staged_writes.discard(workspace, draft)
    await wait_until(lambda: driver.store.load(plan.id).blocked["kind"] == "question")
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["question"] == "Tengo la camelia?"
    assert plan.blocked["drafts"] == [] and plan.steps[0].status == "blocked"
    await driver.answer(plan.id, "sì")
    assert driver.store.load(plan.id).status == "running"


async def test_a_question_over_the_cap_is_refused_even_behind_drafts(
    driver: PlanDriver, workspace: Path
) -> None:
    """Il ramo delle bozze teneva la domanda in attesa gia' tagliata: ora il rifiuto
    viene prima, e le bozze non fermano il passo per una domanda che non c'e'."""
    plan = await _running(driver)
    _stage(workspace)
    text = await driver.blocked(KEY, "z" * 4001)
    assert text.startswith("Error:")
    plan = driver.store.load(plan.id)
    assert plan.status == "running" and plan.blocked is None


async def test_a_stalled_step_with_drafts_waits_on_them_first(
    driver: PlanDriver, workspace: Path
) -> None:
    from jenny.plan.driver import PLAN_STEP_META
    from jenny.session.history_meta import PLAN_REMINDER_EVENT

    plan = await _running(driver)
    _stage(workspace)
    await driver.on_turn_end(KEY, {})  # il promemoria
    await driver.on_turn_end(
        KEY, {INJECTED_EVENT_META: PLAN_REMINDER_EVENT, PLAN_STEP_META: "s1"}
    )
    plan = driver.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["kind"] == "drafts"
    assert plan.steps[0].extra["pending_reason"] == "stalled"


async def test_rollback_throws_away_the_plans_pending_drafts(
    driver: PlanDriver, workspace: Path
) -> None:
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    unrelated = _stage(workspace, rel="USER.md")
    result = await driver.stop(plan.id, "rollback")
    assert result is not None
    assert result["discarded_drafts"] == [{"id": draft, "target": TARGET}]
    assert [d["id"] for d in staged_writes.list_staged(workspace)] == [unrelated]


async def test_after_a_restart_a_plan_on_its_drafts_still_waits_on_them(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    """Riavvio con il passo fermo sulle bozze: il piano resta fermo (aspettava gia'
    l'utente), e deciderle lo fa andare avanti — la rilettura di ``plan.get``."""
    plan = await _running(driver)
    draft = _stage(workspace)
    await driver.step_done(KEY, "fatto")
    assert driver.store.load(plan.id).blocked["kind"] == "drafts"
    reborn = PlanDriver(driver.store, bus, driver.host)
    assert reborn.recover() == []
    plan = reborn.store.load(plan.id)
    assert plan.status == "blocked" and plan.blocked["kind"] == "drafts"
    staged_writes.discard(workspace, draft)
    await reborn.recheck_drafts(plan.id)
    await wait_until(lambda: reborn.store.load(plan.id).steps[1].status == "now")
    assert reborn.store.load(plan.id).steps[0].status == "done"


async def test_after_a_restart_mid_step_the_step_is_told_its_drafts_already_wait(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    """Riavvio a passo aperto, con una bozza gia' lasciata: il piano va in pausa e
    Riprendi rifa' il passo. La bozza resta (e' lavoro fatto, l'utente puo'
    deciderla), e il turno del passo la nomina perche' non la rifaccia."""
    plan = await _running(driver)
    _stage(workspace)
    reborn = PlanDriver(driver.store, bus, driver.host)
    reborn.recover()
    assert reborn.store.load(plan.id).status == "paused"
    await reborn.resume(plan.id)
    step_msgs = [
        m for m in bus.inbound if m.metadata.get(INJECTED_EVENT_META) == PLAN_STEP_EVENT
    ]
    assert "Already waiting for the user's approval" in step_msgs[-1].content
    assert TARGET in step_msgs[-1].content
    assert "Already waiting" not in step_msgs[0].content


async def test_the_real_host_finds_the_notebooks_drafts(workspace: Path) -> None:
    from types import SimpleNamespace

    from jenny.agent.plan_host import LoopPlanHost
    from jenny.security.workspace_access import WorkspaceScopeResolver

    loop = SimpleNamespace(
        workspace=workspace,
        workspace_scopes=WorkspaceScopeResolver(
            default_workspace=workspace, default_restrict_to_workspace=True
        ),
    )
    host = LoopPlanHost(loop)  # type: ignore[arg-type]
    mine = _stage(workspace)
    _stage(workspace, rel="USER.md")
    _stage(workspace, rel="wikis/altro/AGENTS.md")
    found = host.notebook_drafts("piante")
    assert [(d["id"], d["target"]) for d in found] == [(mine, TARGET)]
    assert isinstance(found[0]["created_ms"], int)
    assert host.discard_draft(mine) is True
    assert host.notebook_drafts("piante") == []
