"""I buchi **To research** degli aiutanti del passo e il cancello di ``plan_step_done``.

Un writer o un notebook-editor che non ha trovato un dato lo riporta sotto
**To research**; il driver lo tiene sul passo aperto, per lineage, e la prima
chiusura del passo con buchi aperti si rifiuta una volta sola.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.plan.driver import PlanDriver
from jenny.plan.model import MAX_RESEARCH_ITEMS, to_research_items
from jenny.plan.store import PlanStore
from tests.support.plan import KEY, STEPS, FakeBus, FakeHost, propose, write_file

# ── la lista nel resoconto ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "items"),
    [
        (
            "Pagine scritte.\n\n**To research**\n- felce: umidità ideale, per wiki/felce.md\n"
            "- cactus: vaso\n  - un dettaglio della voce sopra\n\nNon fatto: niente.",
            ["felce: umidità ideale, per wiki/felce.md", "cactus: vaso"],
        ),
        ("## To research\n\n1. Uno\n2) Due\n\nAltro", ["Uno", "Due"]),
        ("To research (2):\n* a\n* **b**\nUn paragrafo", ["a", "b"]),
        ("- **To research**: il pH del terriccio", ["il pH del terriccio"]),
        ("To research:\n- x\n  continua la voce\n- y\n### Altro\n- z", ["x", "y"]),
        ("**To research:** none", []),
        ("To research: nothing — every page is filled", []),
        ("**To research**: —", []),
        ("To research\n- None", []),
        # Nominata in una frase non è una lista (l'annuncio dell'aiutante lo fa).
        ("The result ends with a **To research** list: gaps", []),
        ("Nessuna lista qui.\n- un punto qualunque", []),
    ],
)
def test_to_research_items(text: str, items: list[str]) -> None:
    assert to_research_items(text) == items


def test_to_research_items_are_capped() -> None:
    text = "To research:\n" + "\n".join(f"- voce {n}" for n in range(50))
    assert len(to_research_items(text)) == MAX_RESEARCH_ITEMS


# ── il cancello ─────────────────────────────────────────────────────────────


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/index.md", "# indice")
    return ws


class _Host(FakeHost):
    """L'ospite finto, col registro degli aiutanti: task → (piano, passo)."""

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.groups: dict[str, tuple[str | None, str | None]] = {}

    def helper_plan_step(self, task_id: str) -> tuple[str | None, str | None] | None:
        return self.groups.get(task_id)


@pytest.fixture()
def host(workspace: Path) -> _Host:
    return _Host(workspace)


@pytest.fixture()
def driver(workspace: Path, host: _Host) -> PlanDriver:
    return PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), host)


async def _approve(driver: PlanDriver) -> Any:
    assert "card" in await propose(driver, "Rifare la wiki", STEPS)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    return driver.store.load(plan.id)


_GAPS = "Fatto.\n\n**To research**\n- felce: umidità ideale\n- cactus: vaso\n"


async def _report(
    driver: PlanDriver, plan: Any, content: str, *, lineage: str | None = "ed1",
    task_id: str = "t1", step: str = "s1",
) -> bool:
    return await driver.note_step_report(
        KEY, task_id, lineage, content, plan_id=plan.id, plan_step=step
    )


async def test_open_gaps_refuse_the_close_once_then_it_passes(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    assert await _report(driver, plan, _GAPS)
    assert driver.store.load(plan.id).steps[0].extra["gaps"] == {
        "ed1": ["felce: umidità ideale", "cactus: vaso"]
    }
    refused = await driver.step_done(KEY, "pagine scritte")
    assert refused.startswith("Not closed yet")
    assert "helper `ed1`" in refused and "felce: umidità ideale" in refused
    assert "subagent_send" in refused and "researchers" in refused
    assert driver.store.load(plan.id).steps[0].status == "now"
    # La seconda chiamata passa: la nota spiega perché i buchi restano.
    assert "closed" in await driver.step_done(KEY, "cercati, nessuna fonte")
    assert driver.store.load(plan.id).steps[0].status == "done"


async def test_the_same_helper_sent_back_without_gaps_clears_them(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await _report(driver, plan, _GAPS, task_id="t1")
    # Ripreso con ``subagent_send``: task nuovo, stesso lineage, niente buchi.
    assert await _report(driver, plan, "Pagine riempite.\n\nTo research: none", task_id="t2")
    assert "gaps" not in driver.store.load(plan.id).steps[0].extra
    assert "closed" in await driver.step_done(KEY, "pagine riempite")


async def test_gaps_are_kept_per_helper(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await _report(driver, plan, _GAPS, lineage="ed1")
    await _report(driver, plan, "To research:\n- pothos: luce", lineage="ed2", task_id="t2")
    await _report(driver, plan, "Fatto, nessun buco.", lineage="ed1", task_id="t3")
    assert driver.store.load(plan.id).steps[0].extra["gaps"] == {"ed2": ["pothos: luce"]}
    refused = await driver.step_done(KEY, "x")
    assert "helper `ed2`" in refused and "ed1" not in refused


async def test_without_a_lineage_the_task_id_is_the_key(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await _report(driver, plan, _GAPS, lineage=None, task_id="t9")
    assert set(driver.store.load(plan.id).steps[0].extra["gaps"]) == {"t9"}


async def test_without_plan_metadata_the_helper_registry_says_the_step(
    driver: PlanDriver, host: _Host
) -> None:
    plan = await _approve(driver)
    host.groups["t1"] = (plan.id, "s1")
    assert await driver.note_step_report(KEY, "t1", "ed1", _GAPS)
    assert "gaps" in driver.store.load(plan.id).steps[0].extra


async def test_a_report_of_another_step_or_plan_is_ignored(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    assert not await _report(driver, plan, _GAPS, step="s2")
    assert not await driver.note_step_report(
        KEY, "t1", "ed1", _GAPS, plan_id="p_other", plan_step="s1"
    )
    assert not await driver.note_step_report(KEY, "t1", "ed1", _GAPS)  # passo ignoto
    assert "gaps" not in driver.store.load(plan.id).steps[0].extra
    assert "closed" in await driver.step_done(KEY, "x")


async def test_no_plan_running_records_nothing(driver: PlanDriver) -> None:
    assert not await driver.note_step_report(
        KEY, "t1", "ed1", _GAPS, plan_id="p_x", plan_step="s1"
    )


async def test_skip_is_not_gated(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await _report(driver, plan, _GAPS)
    assert "skipped" in await driver.step_skip(KEY, "niente da fare")


async def test_the_next_step_starts_without_the_gaps_of_the_last(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await _report(driver, plan, _GAPS)
    await driver.step_done(KEY, "x")
    await driver.step_done(KEY, "y")
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "now"
    assert "gaps" not in plan.steps[1].extra and "gaps_gate" not in plan.steps[1].extra
    await _report(driver, plan, _GAPS, step="s2", lineage="ed5")
    assert (await driver.step_done(KEY, "z")).startswith("Not closed yet")
