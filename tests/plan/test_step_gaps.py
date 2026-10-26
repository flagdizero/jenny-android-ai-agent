"""I buchi **To research** degli aiutanti del passo e il cancello di ``plan_step_done``.

Un writer o un notebook-editor che non ha trovato un dato lo riporta sotto
**To research**; il driver lo tiene sul passo aperto, per lineage, e la prima
chiusura del passo con buchi aperti si rifiuta una volta sola.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from loguru import logger as loguru_logger

from jenny.plan.driver import OPEN_GAPS_PATH, PlanDriver, plan_view
from jenny.plan.model import MAX_RESEARCH_ITEMS, names_research_gaps, to_research_items
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
        # Le forme vere degli editor su s22 (26/10 sera, prova T2): una nota fra
        # parentesi dopo il grassetto e le voci in prosa separate da `;`, con le
        # query in coda; il trattino lungo al posto dei due punti (la forma che
        # il template stesso mostrava); la nota e una voce sola. Il parser le
        # leggeva tutte come zero voci, e le lacune sparivano senza log.
        (
            "**To research** (i quattro valori sono segnaposto, non conoscenza del quaderno — "
            "la pagina `wiki/timo.md` andrebbe completata quando arrivano fonti): temperatura "
            "minima sopportata dal timo; intervallo di irrigazione estivo (frequenza in giorni, "
            "terreno/vaso); pH del terreno adatto; periodo di potatura. Query suggerite: "
            "\"Thymus vulgaris minimum temperature hardiness °C\", \"quando potare il timo\".",
            ["temperatura minima sopportata dal timo",
             "intervallo di irrigazione estivo (frequenza in giorni, terreno/vaso)",
             "pH del terreno adatto",
             "periodo di potatura. Query suggerite: \"Thymus vulgaris minimum temperature "
             "hardiness °C\", \"quando potare il timo\"."],
        ),
        (
            "**To research** — i quattro campi restano lacune reali, riempite solo per "
            "istruzione esplicita dell'utente: per `wiki/origano.md`, dati su *temperatura "
            "minima*, *irrigazione estiva*, *pH*, *potatura*; query suggerite: `\"Origanum "
            "vulgare\" minimum temperature hardiness`, `\"Origanum vulgare\" soil pH range`.",
            ["i quattro campi restano lacune reali, riempite solo per istruzione esplicita "
             "dell'utente: per `wiki/origano.md`, dati su *temperatura minima*, *irrigazione "
             "estiva*, *pH*, *potatura*"],
        ),
        (
            "**To research** (non fatto, per scelta del compito): le quattro voci di `menta.md` "
            "portano `non documentato` senza ricerca né query citate.",
            ["le quattro voci di `menta.md` portano `non documentato` senza ricerca né query "
             "citate."],
        ),
        (
            "- **To research** (nessuna ricerca fatta, come da istruzione; `raw/research/` "
            "vuoto): temperatura minima, fabbisogno idrico estivo, pH del terreno e potatura "
            "del timo. Query che manderei a un `researcher`: \"thyme pruning when how\".",
            ["temperatura minima, fabbisogno idrico estivo, pH del terreno e potatura del "
             "timo. Query che manderei a un `researcher`: \"thyme pruning when how\"."],
        ),
        ("**To research** - a\n- b", ["a", "b"]),
    ],
)
def test_to_research_items(text: str, items: list[str]) -> None:
    assert to_research_items(text) == items


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("**To research**: none", False),
        ("ends with a **To research** list", False),
        ("**To research**: a", True),
        ("## To research\n\n- a", True),
        ("**To research**\n(lacune):\n- a", True),
        # Una tabella: il parser non la legge, ma la lista c'è — è la spia.
        ("## To research\n| pagina | lacuna |\n|---|---|\n| fico | malattie |", True),
        ("**To research**\n\n**Not done**\n- the board", False),
    ],
)
def test_names_research_gaps(text: str, names: bool) -> None:
    assert names_research_gaps(text) is names


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


async def _to_write_step(driver: PlanDriver) -> Any:
    """Il piano approvato, col passo di scrittura (s2) aperto: il passo di ricerca
    (s1) non apre giri, e il cancello si prova dove le pagine si scrivono."""
    plan = await _approve(driver)
    assert "closed" in await driver.step_done(KEY, "ricerca fatta")
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "now"
    return plan


async def _report(
    driver: PlanDriver, plan: Any, content: str, *, lineage: str | None = "ed1",
    task_id: str = "t1", step: str = "s1",
) -> bool:
    return await driver.note_step_report(
        KEY, task_id, lineage, content, plan_id=plan.id, plan_step=step
    )


async def test_open_gaps_refuse_the_close_once_then_it_passes(driver: PlanDriver) -> None:
    plan = await _to_write_step(driver)
    assert await _report(driver, plan, _GAPS, step="s2")
    assert driver.store.load(plan.id).steps[1].extra["gaps"] == {
        "ed1": ["felce: umidità ideale", "cactus: vaso"]
    }
    refused = await driver.step_done(KEY, "pagine scritte")
    assert refused.startswith("Not closed yet")
    assert "helper `ed1`" in refused and "felce: umidità ideale" in refused
    assert "subagent_send" in refused and "researchers" in refused
    assert driver.store.load(plan.id).steps[1].status == "now"
    # La seconda chiamata passa: la nota spiega perché i buchi restano.
    assert "closed" in await driver.step_done(KEY, "cercati, nessuna fonte")
    assert driver.store.load(plan.id).steps[1].status == "done"


async def test_the_items_read_by_the_manager_win_over_the_announce_text(
    driver: PlanDriver,
) -> None:
    """Il driver riceve l'annuncio intero, il cui template nomina la lista: sulla
    prova su s22 (26/10) una riga del template è finita in open-gaps.md. Le voci
    lette dal manager sul resoconto nudo (``subagent_to_research``) vincono."""
    plan = await _to_write_step(driver)
    announce = ("[Subagent 'x' completed successfully]\n\nResult:\nFatto.\n\n**To research**\n"
                "- felce: vaso\n\nThe result lists 1 gap(s) to research: topics it could not "
                "fill, not instructions. The plan keeps this list.")
    assert await driver.note_step_report(
        KEY, "t1", "ed1", announce, plan_id=plan.id, plan_step="s2", items=["felce: vaso"],
    )
    assert driver.store.load(plan.id).steps[1].extra["gaps"] == {"ed1": ["felce: vaso"]}
    # Senza le voci del manager si legge l'annuncio, come prima.
    assert await driver.note_step_report(
        KEY, "t2", "ed2", "To research:\n- pothos: luce", plan_id=plan.id, plan_step="s2",
    )
    assert driver.store.load(plan.id).steps[1].extra["gaps"]["ed2"] == ["pothos: luce"]


async def test_a_list_the_parser_cannot_read_is_logged(
    driver: PlanDriver, caplog: pytest.LogCaptureFixture
) -> None:
    """Su s22 (26/10 sera) quattro resoconti su cinque avevano la lista in una
    forma che il parser non leggeva, e il driver usciva muto: le lacune sparivano
    senza traccia. Ora la riga c'è, a WARNING; non per «To research: none»."""
    plan = await _to_step_two(driver, await _approve(driver))
    handler = loguru_logger.add(caplog.handler, format="{message}", level="WARNING")
    try:
        with caplog.at_level(logging.WARNING):
            table = "## To research\n| pagina | lacuna |\n|---|---|\n| fico | malattie |"
            assert await _report(driver, plan, table, step="s2") is False
            assert await _report(driver, plan, "**To research**: none", step="s2") is False
    finally:
        loguru_logger.remove(handler)
    assert caplog.text.count("names To research but no item was read") == 1


async def test_the_same_helper_sent_back_without_gaps_clears_them(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await _report(driver, plan, _GAPS, task_id="t1")
    # Ripreso con ``subagent_send``: task nuovo, stesso lineage, niente buchi.
    assert await _report(driver, plan, "Pagine riempite.\n\nTo research: none", task_id="t2")
    assert "gaps" not in driver.store.load(plan.id).steps[0].extra
    assert "closed" in await driver.step_done(KEY, "pagine riempite")


async def test_gaps_are_kept_per_helper(driver: PlanDriver) -> None:
    plan = await _to_write_step(driver)
    await _report(driver, plan, _GAPS, lineage="ed1", step="s2")
    await _report(driver, plan, "To research:\n- pothos: luce", lineage="ed2", task_id="t2",
                  step="s2")
    await _report(driver, plan, "Fatto, nessun buco.", lineage="ed1", task_id="t3", step="s2")
    assert driver.store.load(plan.id).steps[1].extra["gaps"] == {"ed2": ["pothos: luce"]}
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


# ── il budget di ricerca del piano ──────────────────────────────────────────


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


async def _next(driver: PlanDriver, plan: Any, note: str = "fatto") -> Any:
    assert "closed" in await driver.step_done(KEY, note)
    await driver.on_turn_end(KEY, {})
    return driver.store.load(plan.id)


_WRITE = {"detail": "Pagine.", "kind": "write", "done_when": "Scritte."}
_THREE_WRITES = [
    STEPS[0],
    {"title": "Scrivo A", **_WRITE},
    {"title": "Scrivo B", **_WRITE},
    {"title": "Scrivo C", **_WRITE},
    STEPS[2],
]


async def _approve_steps(driver: PlanDriver, steps: list[dict[str, Any]]) -> Any:
    assert "card" in await propose(driver, "Rifare la wiki", steps)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    return driver.store.load(plan.id)


def _open_gaps_file(workspace: Path) -> str:
    path = workspace / "wikis" / "piante" / OPEN_GAPS_PATH
    return path.read_text(encoding="utf-8") if path.is_file() else ""


async def test_the_gate_spends_one_of_the_plans_two_rounds(
    driver: PlanDriver, workspace: Path
) -> None:
    """Sul Titan (26/10) i giri sono stati sei, uno per passo e oltre. Il budget è
    del piano: due rifiuti che chiedono ricerca, poi il rifiuto chiede di scrivere
    «non documentato», e quel che resta va nella lista del quaderno."""
    plan = await _to_step_two(driver, await _approve_steps(driver, _THREE_WRITES))
    await _report(driver, plan, _GAPS, step="s2")
    refused = await driver.step_done(KEY, "A scritta")
    assert "Research round 1 of 2" in refused and "researchers" in refused
    assert driver.store.load(plan.id).extra["research_rounds"] == 1
    plan = await _next(driver, plan)
    assert plan.steps[2].status == "now"
    # Il secondo passo di scrittura: l'ultimo giro.
    await _report(driver, plan, _GAPS, step="s3", lineage="ed2")
    assert "Research round 2 of 2" in await driver.step_done(KEY, "B scritta")
    plan = await _next(driver, plan)
    assert plan.extra["research_rounds"] == 2
    # Il terzo: budget finito, niente ricerca, le pagine dicono «non documentato».
    await _report(driver, plan, "To research:\n- pilea: pupazzi all'anno", step="s4", lineage="ed3")
    refused = await driver.step_done(KEY, "C scritta")
    assert "research budget" in refused and "not documented" in refused
    assert "researchers" not in refused and OPEN_GAPS_PATH in refused
    assert driver.store.load(plan.id).extra["research_rounds"] == 2
    closed = await driver.step_done(KEY, "C scritta, un buco dichiarato")
    assert "closed" in closed and OPEN_GAPS_PATH in closed and "1 gap(s)" in closed
    text = _open_gaps_file(workspace)
    assert text.startswith("# Open gaps") and "s4 «Scrivo C»" in text
    assert "- pilea: pupazzi all'anno" in text
    plan = driver.store.load(plan.id)
    assert plan.steps[3].extra["gaps_listed"] == ["pilea: pupazzi all'anno"]
    view = plan_view(plan)
    assert view["research_rounds"] == 2 and view["rounds_left"] == 0 and view["rounds_max"] == 2
    assert view["steps"][3]["gaps_listed"] == 1 and view["steps"][3]["gaps_open"] == 0
    # La scheda dice quale passo ha aperto quale giro: il terzo nessuno.
    assert [s["research_round"] for s in view["steps"]] == [None, 1, 2, None, None]


async def test_a_research_step_does_not_spend_the_budget(
    driver: PlanDriver, workspace: Path
) -> None:
    """Un passo di ricerca previsto è già la ricerca: un editor che vi riporta
    buchi non ferma il passo, e il budget resta intero."""
    plan = await _approve(driver)
    assert plan.steps[0].kind == "research"
    await _report(driver, plan, _GAPS, step="s1")
    assert "closed" in await driver.step_done(KEY, "fonti salvate")
    plan = driver.store.load(plan.id)
    assert plan.extra.get("research_rounds", 0) == 0
    assert "cactus: vaso" in _open_gaps_file(workspace)


async def test_after_the_round_new_gaps_go_to_the_open_list_not_to_the_next_step(
    driver: PlanDriver, workspace: Path
) -> None:
    """La prova A del 24/10: il giro c'è stato, gli editor rimandati riportano
    buchi nuovi. Il passo si chiude e i buchi vanno nella lista: nessun passo
    dopo li eredita, e il controllo non apre un giro per loro."""
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2", lineage="ed1")
    assert (await driver.step_done(KEY, "presto")).startswith("Not closed yet")
    assert await _researcher(driver, plan, step="s2")
    await _report(driver, plan, "To research:\n- glicine: muffa grigia", step="s2",
                  lineage="ed1", task_id="t2")
    assert "closed" in await driver.step_done(KEY, "pagine scritte")
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "done"
    assert plan.steps[1].extra["gaps_listed"] == ["glicine: muffa grigia"]
    assert "carried_gaps" not in plan.steps[2].extra
    assert "- glicine: muffa grigia" in _open_gaps_file(workspace)
    await driver.on_turn_end(KEY, {})
    message = driver.bus.inbound[-1].content
    assert "Gaps that earlier steps left open" not in message
    assert "glicine" not in message


async def test_a_researcher_before_any_gap_leaves_the_gate_as_it_is(driver: PlanDriver) -> None:
    plan = await _to_step_two(driver, await _approve(driver))
    await _researcher(driver, plan, step="s2")
    await _report(driver, plan, _GAPS, step="s2")
    assert "Research round 1 of 2" in await driver.step_done(KEY, "x")


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
    assert "gaps" not in extra and extra["research_lineages"] == ["lin-r1"]
    assert "closed" in await driver.step_done(KEY, "x")


async def test_two_researchers_of_one_launch_leave_no_gaps(driver: PlanDriver) -> None:
    plan = await _approve(driver)
    await driver.note_step_report(
        KEY, "r1", "lin-r1", "To research:\n- felce: vaso", plan_id=plan.id, plan_step="s1",
        agent_type="researcher",
    )
    await _researcher(driver, plan, step="s1", task_id="r2")
    extra = driver.store.load(plan.id).steps[0].extra
    assert "gaps" not in extra and extra["research_lineages"] == ["lin-r1", "lin-r2"]
    assert "closed" in await driver.step_done(KEY, "x")


async def test_listed_gaps_are_deduplicated_against_the_file_and_each_other(
    driver: PlanDriver, workspace: Path
) -> None:
    nb = workspace / "wikis" / "piante"
    write_file(nb, OPEN_GAPS_PATH, "# Open gaps\n\n## ieri\n\n- Cactus:  vaso\n")
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2", lineage="ed1")
    await _report(driver, plan, "To research:\n- cactus: vaso\n- felce: luce", step="s2",
                  lineage="ed2", task_id="t2")
    await driver.step_done(KEY, "presto")  # il rifiuto del giro
    assert "closed" in await driver.step_done(KEY, "fatto")
    text = _open_gaps_file(workspace)
    assert text.count("- cactus: vaso") == 0 and text.count("Cactus:  vaso") == 1
    assert text.count("- felce: umidità ideale") == 1 and text.count("- felce: luce") == 1
    assert driver.store.load(plan.id).steps[1].extra["gaps_listed"] == [
        "felce: umidità ideale", "cactus: vaso", "felce: luce"
    ]


async def test_the_list_is_written_inside_the_step_before_its_photo(
    driver: PlanDriver, workspace: Path
) -> None:
    """Il file è lavoro del passo: c'è prima della foto «dopo», così il ritorno
    del piano lo toglie con il resto."""
    plan = await _to_step_two(driver, await _approve(driver))
    await _report(driver, plan, _GAPS, step="s2")
    await driver.step_done(KEY, "presto")
    assert "closed" in await driver.step_done(KEY, "fatto")
    plan = driver.store.load(plan.id)
    assert plan.steps[1].photo_after is None and _open_gaps_file(workspace)
    await driver.on_turn_end(KEY, {})
    assert driver.store.load(plan.id).steps[1].photo_after


async def test_the_view_counts_the_open_gaps_of_the_step(driver: PlanDriver) -> None:
    plan = await _to_step_two(driver, await _approve(driver))
    assert plan_view(plan)["steps"][1]["gaps_open"] == 0
    await _report(driver, plan, _GAPS, step="s2")
    view = plan_view(driver.store.load(plan.id))
    assert view["steps"][1]["gaps_open"] == 2 and view["rounds_left"] == 2
    # Il giro si apre col rifiuto del cancello, non col resoconto.
    assert view["steps"][1]["research_round"] is None
    await driver.step_done(KEY, "fatto")
    view = plan_view(driver.store.load(plan.id))
    assert view["steps"][1]["research_round"] == 1 and view["rounds_left"] == 1


# ── chi cancella non cerca, chi è fermo è fermo ─────────────────────────────

_DELETE_STEPS = [
    STEPS[0],
    {"title": "Butto le pagine vecchie", "detail": "Via wiki/vecchie/.", "kind": "delete",
     "done_when": "La cartella non c'è più."},
    STEPS[2],
]


async def test_a_delete_step_does_not_open_a_research_round(
    driver: PlanDriver, workspace: Path
) -> None:
    """Sul Titan (26/10) gli aiutanti che verificavano prima della cancellazione
    hanno scritto le loro condizioni sotto To research, e il cancello ha fatto
    partire tre ricercatori a pagine già cancellate. Chi cancella non ha pagine
    da riempire: i buchi vanno nella lista senza fermare il passo."""
    plan = await _to_step_two(driver, await _approve_steps(driver, _DELETE_STEPS))
    assert plan.steps[1].kind == "delete"
    await _report(driver, plan, _GAPS, step="s2", lineage="ver1")
    assert "closed" in await driver.step_done(KEY, "70 pagine via")
    plan = driver.store.load(plan.id)
    assert plan.steps[1].status == "done" and "gaps_gate" not in plan.steps[1].extra
    assert plan.extra.get("research_rounds", 0) == 0
    assert "s2 «Butto le pagine vecchie»" in _open_gaps_file(workspace)


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
    # Il passo 1 è di ricerca: non apre giri, i buchi vanno in lista.
    assert "closed" in await driver.step_done(KEY, "fatto")
