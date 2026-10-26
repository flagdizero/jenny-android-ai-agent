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
        ("To research these I would need the web.", []),
        ("Nessuna lista qui.\n- un punto qualunque", []),
        # In grassetto a metà riga, coi due punti: la lista del Titan (26/10), che
        # il driver contava zero mentre l'annuncio mandava Jenny a cercare.
        (
            "I drawer citano pagine cancellate. **To research:** (i) additivo anti-alghe "
            "(`algae inhibitor`); (ii) protezione dal gelo delle radici in vaso; (iii) "
            "rinvaso del ginkgo: nessuna fonte.",
            ["additivo anti-alghe (`algae inhibitor`);",
             "protezione dal gelo delle radici in vaso;",
             "rinvaso del ginkgo: nessuna fonte."],
        ),
        ("- Pages written. **To research:** acqua in vaso del fico", ["acqua in vaso del fico"]),
        # Una riga d'introduzione sotto l'etichetta non è una voce (Titan: contava).
        (
            "**To research:**\n(lacune emerse leggendo, nessuna query ancora fatta):\n"
            "- *Fico* — malattie: `fico ruggine`\n- *Prunus* — dosi",
            ["*Fico* — malattie: `fico ruggine`", "*Prunus* — dosi"],
        ),
        # Una voce che è solo un titolo: contano i suoi sottopunti.
        (
            "To research:\n- Fico — malattie:\n  - ruggine, `a`\n  - cocciniglia, `b`\n- Prunus: dosi",
            ["ruggine, `a`", "cocciniglia, `b`", "Prunus: dosi"],
        ),
    ],
)
def test_to_research_items(text: str, items: list[str]) -> None:
    assert to_research_items(text) == items


def test_a_long_item_is_cut_at_a_word_not_in_the_middle_of_a_query() -> None:
    """A 300 caratteri la voce del Titan finiva con «Query: `acer palma»."""
    item = ("acero filato " * 50) + "Query: `acer palmatum leggy`"
    [got] = to_research_items(f"To research:\n- {item}")
    assert len(got) <= 600 and not got.endswith("ace") and " " in got
    assert got == item[: len(got)] and item[len(got)] == " "


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


# ── un giro di ricerca per passo ────────────────────────────────────────────


async def _researcher(driver: PlanDriver, plan: Any, *, step: str, task_id: str = "r1") -> bool:
    return await driver.note_step_report(
        KEY, task_id, f"lin-{task_id}", "Salvate 3 fonti in raw/research/.",
        plan_id=plan.id, plan_step=step, agent_type="researcher",
    )


async def _to_step_two(driver: PlanDriver, plan: Any) -> Any:
    assert "closed" in await driver.step_done(KEY, "ricerca fatta")
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "now"
    return plan


async def test_after_a_research_round_new_gaps_go_to_the_check_step(driver: PlanDriver) -> None:
    """La prova A: primo giro sui buchi degli editor, poi gli editor rimandati
    riportano buchi nuovi. Il passo si chiude, e i buchi nuovi vanno al controllo."""
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2", lineage="ed1")
    assert await _researcher(driver, plan, step="s2")
    assert driver.store.load(plan.id).steps[1].extra["gaps_round"] is True
    # L'editor rimandato riempie e trova un buco nuovo.
    await _report(driver, plan, "To research:\n- glicine: muffa grigia", step="s2",
                  lineage="ed1", task_id="t2")
    assert "closed" in await driver.step_done(KEY, "pagine scritte")
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "done"
    assert plan.steps[2].extra["carried_gaps"] == [
        {"step": "Scrivo le pagine", "items": ["glicine: muffa grigia"]}
    ]
    await driver.on_turn_end(KEY, {})
    message = driver.bus.inbound[-1].content
    assert "Gaps that earlier steps left open" in message
    assert 'from "Scrivo le pagine"' in message and "glicine: muffa grigia" in message


async def test_a_researcher_with_no_gaps_open_is_not_a_round(driver: PlanDriver) -> None:
    """Il ricercatore di un passo di ricerca previsto, prima di ogni buco: il
    cancello resta com'era."""
    plan = await _to_step_two(driver, await _approve(driver))
    await _researcher(driver, plan, step="s2")
    assert "gaps_round" not in driver.store.load(plan.id).steps[1].extra
    await _report(driver, plan, _GAPS, step="s2")
    assert (await driver.step_done(KEY, "x")).startswith("Not closed yet")


async def test_a_researchers_own_gaps_are_its_outcome_not_the_steps_gaps(
    driver: PlanDriver,
) -> None:
    """Quel che un ricercatore non trova è cercato: contarlo come buco faceva un
    secondo giro dentro ogni passo di ricerca (26/10)."""
    plan = await _to_step_two(driver, await _approve(driver))
    await driver.note_step_report(
        KEY, "r1", "lin-r1", "To research:\n- felce: vaso", plan_id=plan.id, plan_step="s2",
        agent_type="researcher",
    )
    extra = driver.store.load(plan.id).steps[1].extra
    assert "gaps" not in extra and "gaps_round" not in extra
    assert "closed" in await driver.step_done(KEY, "x")


async def test_after_a_gate_forced_round_new_gaps_are_still_carried(driver: PlanDriver) -> None:
    """Il cancello rifiuta, il giro parte, gli editor rimandati trovano buchi
    nuovi: alla chiusura non si perdono, vanno al controllo."""
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2", lineage="ed1")
    assert (await driver.step_done(KEY, "presto")).startswith("Not closed yet")
    await _researcher(driver, plan, step="s2")
    await _report(driver, plan, "To research:\n- glicine: muffa", step="s2",
                  lineage="ed1", task_id="t2")
    assert "closed" in await driver.step_done(KEY, "fatto")
    assert driver.store.load(plan.id).steps[2].extra["carried_gaps"] == [
        {"step": "Scrivo le pagine", "items": ["glicine: muffa"]}
    ]


async def test_two_researchers_of_one_launch_are_not_a_round(driver: PlanDriver) -> None:
    """In un passo di ricerca previsto due ricercatori che rientrano uno dopo
    l'altro non sono un giro, e non lasciano buchi: il passo si chiude."""
    plan = await _approve(driver)
    await driver.note_step_report(
        KEY, "r1", "lin-r1", "To research:\n- felce: vaso", plan_id=plan.id, plan_step="s1",
        agent_type="researcher",
    )
    await _researcher(driver, plan, step="s1", task_id="r2")
    extra = driver.store.load(plan.id).steps[0].extra
    assert "gaps_round" not in extra and "gaps" not in extra
    assert extra["research_lineages"] == ["lin-r1", "lin-r2"]
    assert "closed" in await driver.step_done(KEY, "x")


# ── chi cancella non cerca, chi è fermo è fermo ─────────────────────────────

_DELETE_STEPS = [
    STEPS[0],
    {"title": "Butto le pagine vecchie", "detail": "Via wiki/vecchie/.", "kind": "delete",
     "done_when": "La cartella non c'è più."},
    STEPS[2],
]


async def test_a_delete_step_does_not_open_a_research_round(driver: PlanDriver) -> None:
    """Sul Titan (26/10) gli aiutanti che verificavano prima della cancellazione
    hanno scritto le loro condizioni sotto To research, e il cancello ha fatto
    partire tre ricercatori a pagine già cancellate. Chi cancella non ha pagine
    da riempire: i buchi vanno al controllo senza fermare il passo."""
    assert "card" in await propose(driver, "Pulizia", _DELETE_STEPS)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    plan = await _to_step_two(driver, driver.store.load(plan.id))
    assert plan.steps[1].kind == "delete"
    await _report(driver, plan, _GAPS, step="s2", lineage="ver1")
    assert "closed" in await driver.step_done(KEY, "70 pagine via")
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "done" and "gaps_gate" not in plan.steps[1].extra
    assert plan.steps[2].extra["carried_gaps"] == [
        {"step": "Butto le pagine vecchie", "items": ["felce: umidità ideale", "cactus: vaso"]}
    ]


async def test_a_report_arriving_while_blocked_still_counts(driver: PlanDriver) -> None:
    """Un aiutante partito prima del ``plan_blocked`` rientra a piano fermo: i
    suoi buchi sono del passo, e il cancello li vede alla chiusura."""
    plan = await _approve(driver)
    await driver.blocked(KEY, "Tengo la camelia?")
    assert driver.store.load(plan.id).status == "blocked"
    assert await _report(driver, plan, _GAPS)
    assert driver.store.load(plan.id).steps[0].extra["gaps"] == {
        "ed1": ["felce: umidità ideale", "cactus: vaso"]
    }
    await driver.answer(plan.id, "sì")
    assert (await driver.step_done(KEY, "fatto")).startswith("Not closed yet")


async def test_researchers_for_carried_gaps_make_the_check_steps_round(
    driver: PlanDriver,
) -> None:
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2")
    await _researcher(driver, plan, step="s2")
    await _report(driver, plan, "To research:\n- glicine: muffa", step="s2", task_id="t2")
    await driver.step_done(KEY, "fatto")
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.steps[2].status == "now" and plan.steps[2].extra["carried_gaps"]
    # Il ricercatore dei buchi riportati segna il giro anche senza buchi del passo.
    await _researcher(driver, plan, step="s3", task_id="r9")
    assert driver.store.load(plan.id).steps[2].extra["gaps_round"] is True
    await _report(driver, plan, "To research:\n- pothos: luce", step="s3", lineage="ed7")
    assert "closed" in await driver.step_done(KEY, "controllato")


async def test_the_round_does_not_carry_into_the_next_step(driver: PlanDriver) -> None:
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2")
    await _researcher(driver, plan, step="s2")
    await driver.step_done(KEY, "fatto")
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.steps[2].status == "now" and "gaps_round" not in plan.steps[2].extra
    # Sul passo di controllo, l'ultimo: il giro c'è, il passo si chiude e non
    # c'è nessun passo dopo a cui riportare.
    await _report(driver, plan, _GAPS, step="s3", lineage="ed9")
    await _researcher(driver, plan, step="s3", task_id="r2")
    assert "closed" in await driver.step_done(KEY, "controllato")
