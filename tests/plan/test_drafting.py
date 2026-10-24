"""La pianificazione in corso (``drafting``): nasce col tasto, dura più turni, finisce.

Il driver è vero, con l'ospite e il bus finti dei suoi test; lo stato vive nello
store (``plans/drafting.json``), quindi qui si prova anche che regge a un riavvio
e che le RPC lo leggono senza agente.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from jenny.plan.driver import PLAN_FOLLOWUP_META, PLAN_MODE_META, PLAN_REVISE_META, PlanDriver
from jenny.plan.store import PlanStore
from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
from jenny.webui.commands import CommandContext, dispatch_command
from tests.support.plan import (
    COVERAGE,
    KEY,
    STEPS,
    FakeBus,
    FakeHost,
    start_drafting,
    write_file,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/index.md", "# indice")
    return ws


@pytest.fixture()
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture()
def driver(workspace: Path, bus: FakeBus) -> PlanDriver:
    return PlanDriver(PlanStore.for_workspace(workspace), bus, FakeHost(workspace))


def _changed(bus: FakeBus) -> list[dict[str, Any]]:
    return [m.metadata for m in bus.outbound if m.metadata.get("_plan_changed")]


def _ctx(workspace: Path, driver: PlanDriver | None) -> CommandContext:
    return CommandContext(
        get_workspace_root=lambda: workspace,
        invalidate_session=lambda _key: None,
        busy_session_keys=lambda: (),
        get_plan_driver=lambda: driver,
    )


# ── il ciclo ────────────────────────────────────────────────────────────────


async def test_question_answer_proposal_the_answer_is_still_planning(
    driver: PlanDriver, bus: FakeBus
) -> None:
    """Tre turni: la richiesta col tasto, la risposta senza, la proposta. Il secondo
    turno non porta ``plan: true`` ed è pianificazione lo stesso; il terzo, dopo la
    proposta, è una chat normale."""
    first: dict[str, Any] = {PLAN_MODE_META: True, WORKSPACE_READONLY_METADATA_KEY: True}
    await driver.before_user_turn(KEY, first)
    assert driver.drafting_for(KEY) is not None
    assert _changed(bus)[-1] == {"_plan_changed": True, "notebook": "piante", "plan_id": ""}
    assert PLAN_FOLLOWUP_META not in first
    await driver.note_read(KEY)

    answer: dict[str, Any] = {}
    await driver.before_user_turn(KEY, answer)
    assert answer[PLAN_MODE_META] is True
    assert answer[WORKSPACE_READONLY_METADATA_KEY] is True
    assert answer[PLAN_FOLLOWUP_META] is True
    assert driver.drafting_info("piante")["reads"] == 1

    text = await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    assert "card" in text, text
    assert driver.drafting_for(KEY) is None
    assert driver.store.drafting_get("piante") is None

    after: dict[str, Any] = {}
    await driver.before_user_turn(KEY, after)
    assert after == {}


async def test_a_revision_keeps_its_plan_id_through_the_answers(driver: PlanDriver) -> None:
    await driver.before_user_turn(KEY, {PLAN_MODE_META: True, PLAN_REVISE_META: "p_0123456789ab"})
    answer: dict[str, Any] = {}
    await driver.before_user_turn(KEY, answer)
    assert answer[PLAN_REVISE_META] == "p_0123456789ab"
    assert driver.drafting_info("piante")["revise"] == "p_0123456789ab"


async def test_a_second_plan_press_keeps_the_start_and_the_reads(driver: PlanDriver) -> None:
    await start_drafting(driver, reads=2)
    since = driver.drafting_info("piante")["since"]
    await driver.before_user_turn(KEY, {PLAN_MODE_META: True})
    assert driver.drafting_info("piante") == {"since": since, "revise": None, "reads": 2}


async def test_propose_refuses_without_looking_and_outside_a_planning(
    driver: PlanDriver,
) -> None:
    text = await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    assert text.startswith("Error: nobody is preparing a plan")
    await start_drafting(driver, reads=0)
    text = await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    assert text.startswith("Not yet: look at the notebook first")
    assert driver.store.open_plan_for("piante") is None
    await driver.note_read(KEY)
    assert "card" in await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)


async def test_cancel_ends_it_and_says_whether_there_was_one(
    driver: PlanDriver, bus: FakeBus
) -> None:
    await start_drafting(driver)
    bus.outbound.clear()
    assert await driver.cancel_drafting("piante") is True
    assert driver.drafting_for(KEY) is None
    assert _changed(bus) == [{"_plan_changed": True, "notebook": "piante", "plan_id": ""}]
    assert await driver.cancel_drafting("piante") is False
    meta: dict[str, Any] = {}
    await driver.before_user_turn(KEY, meta)
    assert meta == {}


async def test_it_survives_a_restart(workspace: Path, driver: PlanDriver) -> None:
    await start_drafting(driver, reads=3)
    reborn = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), FakeHost(workspace))
    reborn.recover()
    assert reborn.drafting_info("piante")["reads"] == 3
    meta: dict[str, Any] = {}
    await reborn.before_user_turn(KEY, meta)
    assert meta[PLAN_MODE_META] is True


async def test_it_expires_after_twelve_hours_without_turns(
    workspace: Path, driver: PlanDriver, bus: FakeBus
) -> None:
    await start_drafting(driver)
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat(timespec="seconds")
    driver.store.drafting_update("piante", lambda e: {**(e or {}), "since": old, "touched": old})
    driver.refresh_index()
    assert driver.drafting_for(KEY) is None
    got = await dispatch_command(_ctx(workspace, None), "plan.get", {"notebook": "piante"})
    assert got["drafting"] is None
    bus.outbound.clear()
    meta: dict[str, Any] = {}
    await driver.before_user_turn(KEY, meta)
    assert meta == {}
    assert driver.store.drafting_get("piante") is None
    assert _changed(bus)


async def test_approving_a_plan_ends_a_review_left_open(driver: PlanDriver) -> None:
    await start_drafting(driver)
    await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    plan = driver.store.open_plan_for("piante")
    await driver.before_user_turn(KEY, {PLAN_MODE_META: True, PLAN_REVISE_META: plan.id})
    assert driver.drafting_for(KEY) is not None
    await driver.approve(plan.id, plan.version, [])
    assert driver.drafting_for(KEY) is None


async def test_no_planning_starts_over_a_running_plan(driver: PlanDriver) -> None:
    await start_drafting(driver)
    await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    await driver.before_user_turn(KEY, {PLAN_MODE_META: True})
    assert driver.drafting_for(KEY) is None


async def test_it_follows_the_notebook_when_renamed_or_deleted(
    workspace: Path, driver: PlanDriver
) -> None:
    await start_drafting(driver)
    driver.notebook_renamed("piante", "giardino")
    assert driver.drafting_for("project:giardino") is not None
    assert driver.drafting_for(KEY) is None
    driver.notebook_deleted("giardino")
    assert driver.store.drafting_all() == {}


# ── le RPC ──────────────────────────────────────────────────────────────────


async def test_plan_get_carries_drafting_next_to_the_plan(
    workspace: Path, driver: PlanDriver
) -> None:
    ctx = _ctx(workspace, driver)
    got = await dispatch_command(ctx, "plan.get", {"notebook": "piante"})
    assert got == {"ok": True, "plan": None, "drafting": None}
    await start_drafting(driver, reads=2)
    got = await dispatch_command(ctx, "plan.get", {"notebook": "piante"})
    assert got["plan"] is None
    assert got["drafting"]["reads"] == 2 and got["drafting"]["revise"] is None
    assert set(got["drafting"]) == {"since", "revise", "reads"}
    # Senza agente si legge dal file, come i piani.
    got = await dispatch_command(_ctx(workspace, None), "plan.get", {"notebook": "piante"})
    assert got["drafting"]["reads"] == 2
    await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    plan = driver.store.open_plan_for("piante")
    by_id = await dispatch_command(ctx, "plan.get", {"id": plan.id})
    assert "drafting" not in by_id


async def test_cancel_drafting_over_rpc(workspace: Path, driver: PlanDriver, bus: FakeBus) -> None:
    ctx = _ctx(workspace, driver)
    await start_drafting(driver)
    assert await dispatch_command(ctx, "plan.cancel_drafting", {"notebook": "piante"}) == {
        "ok": True, "cancelled": True,
    }
    assert await dispatch_command(ctx, "plan.cancel_drafting", {"notebook": "piante"}) == {
        "ok": True, "cancelled": False,
    }
    from jenny.webui.commands import CommandError

    with pytest.raises(CommandError) as info:
        await dispatch_command(ctx, "plan.cancel_drafting", {})
    assert info.value.code == "bad_request"
    with pytest.raises(CommandError) as info:
        await dispatch_command(_ctx(workspace, None), "plan.cancel_drafting", {"notebook": "x"})
    assert info.value.code == "unavailable"


async def test_plan_full_and_the_view_carry_coverage_assumptions_done_when(
    workspace: Path, driver: PlanDriver
) -> None:
    ctx = _ctx(workspace, driver)
    await start_drafting(driver)
    await driver.propose(
        KEY, "Rifare la wiki", STEPS, coverage=COVERAGE, assumptions=["Tengo la camelia."]
    )
    plan = driver.store.open_plan_for("piante")
    full = await dispatch_command(ctx, "plan.full", {"id": plan.id})
    assert full["coverage"] == [
        {"id": "c1", "item": "Acero e Ginkgo", "have": "una pagina vecchia", "missing": "le cure",
         "strategy": "notebook", "why": ""},
    ]
    assert full["assumptions"] == ["Tengo la camelia."]
    assert full["steps"][0]["done_when"] == STEPS[0]["done_when"]
    assert [s["covers"] for s in full["steps"]] == [["c1"], [], []]
    view = (await dispatch_command(ctx, "plan.get", {"notebook": "piante"}))["plan"]
    assert view["coverage"] == full["coverage"]
    assert view["assumptions"] == ["Tengo la camelia."]
    assert [s["done_when"] for s in view["steps"]] == [s["done_when"] for s in STEPS]
    assert [s["covers"] for s in view["steps"]] == [["c1"], [], []]


def test_plan_propose_offers_covers_on_steps() -> None:
    """Il modello vede ``covers`` nello schema: senza, la validazione gli chiederebbe un
    campo che non sa di avere."""
    from jenny.agent.tools.plan import PlanProposeTool

    params = PlanProposeTool().parameters
    step = params["properties"]["steps"]["items"]["properties"]
    assert step["covers"]["type"] == "array"
    assert "research" in step["covers"]["description"]
    assert "c1, c2" in params["properties"]["coverage"]["description"]


async def test_an_old_plan_reads_with_empty_new_fields(workspace: Path, driver: PlanDriver) -> None:
    """Un ``plan.json`` di prima: copertura e assunzioni vuote, ``done_when`` vuoto,
    un blocco senza ``kind`` è una domanda senza bozze."""
    await start_drafting(driver)
    await driver.propose(KEY, "Rifare la wiki", STEPS, coverage=COVERAGE)
    plan = driver.store.open_plan_for("piante")
    path = driver.store.plan_dir(plan.id) / "plan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("coverage", "assumptions"):
        data.pop(key)
    for step in data["steps"]:
        step.pop("done_when")
    data["blocked"] = {"step": "s1", "question": "Quale?", "reason": "question", "at": "x"}
    path.write_text(json.dumps(data), encoding="utf-8")
    got = await dispatch_command(_ctx(workspace, driver), "plan.get", {"id": plan.id})
    view = got["plan"]
    assert view["coverage"] == [] and view["assumptions"] == []
    assert all(s["done_when"] == "" for s in view["steps"])
    assert view["blocked"]["kind"] == "question" and view["blocked"]["drafts"] == []


async def test_plan_true_on_every_message_is_a_follow_up_once_planning_is_open(
    driver: PlanDriver,
) -> None:
    """La casa rimanda ``plan: true`` a ogni messaggio finché il tasto è acceso: dal
    secondo in poi è il seguito della pianificazione, non una nuova, e la revisione
    chiesta all'inizio resta."""
    await driver.before_user_turn(
        KEY, {PLAN_MODE_META: True, PLAN_REVISE_META: "p_0123456789ab"}
    )
    await driver.note_read(KEY)
    since = driver.drafting_info("piante")["since"]
    again: dict[str, Any] = {PLAN_MODE_META: True, WORKSPACE_READONLY_METADATA_KEY: True}
    await driver.before_user_turn(KEY, again)
    assert again[PLAN_FOLLOWUP_META] is True
    assert again[PLAN_REVISE_META] == "p_0123456789ab"
    assert driver.drafting_info("piante") == {
        "since": since, "revise": "p_0123456789ab", "reads": 1,
    }
    other: dict[str, Any] = {PLAN_MODE_META: True, PLAN_REVISE_META: "p_ba9876543210"}
    await driver.before_user_turn(KEY, other)
    assert other[PLAN_REVISE_META] == "p_ba9876543210"
    assert driver.drafting_info("piante")["revise"] == "p_ba9876543210"


async def test_a_cancel_racing_a_follow_up_is_not_undone(driver: PlanDriver) -> None:
    """L'annullamento arriva mentre il turno che continua la pianificazione la
    tocca: resta annullata, sul file e in memoria."""
    import asyncio

    for _ in range(20):
        await start_drafting(driver)
        meta: dict[str, Any] = {}

        async def cancel() -> bool:
            await asyncio.sleep(0)
            return await driver.cancel_drafting("piante")

        _, cancelled = await asyncio.gather(driver.before_user_turn(KEY, meta), cancel())
        assert cancelled is True
        assert driver.store.drafting_get("piante") is None
        assert driver.drafting_for(KEY) is None
