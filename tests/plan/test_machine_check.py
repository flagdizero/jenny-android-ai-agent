"""Il controllo a macchina nel passo ``check`` del piano (``PlanDriver`` + ``plan/audit.py``).

Il passo di controllo non lo fa più il modello: all'apertura il codice controlla
il quaderno; se non c'è niente da riparare il passo si chiude da solo, altrimenti
il modello riceve solo la lista. A ``plan_step_done`` il codice ricontrolla: al
primo giro rifiuta una volta, al secondo accetta e scrive cosa resta.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.plan.driver import PlanDriver
from jenny.plan.model import PlanValidationError, validate_proposal
from jenny.plan.store import PlanStore
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT
from tests.support.plan import KEY, STEPS, FakeBus, FakeHost, propose, write_file

_TEXT = "Testo vero della pagina, con abbastanza parole per non sembrare vuota. " * 5


def _page(extra: str = "") -> str:
    return f"---\nstate: open\n---\n# Pagina\n\n{_TEXT}\n{extra}"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/index.md", "# indice")
    write_file(nb, "wiki/acero.md", _page())
    return ws


@pytest.fixture()
def notebook(workspace: Path) -> Path:
    return workspace / "wikis" / "piante"


@pytest.fixture()
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture()
def driver(workspace: Path, bus: FakeBus) -> PlanDriver:
    return PlanDriver(PlanStore.for_workspace(workspace), bus, FakeHost(workspace))


async def _run_to_check(
    driver: PlanDriver,
    steps: list[dict[str, Any]] | None = None,
    *,
    writes: dict[str, str] | None = None,
) -> Any:
    """Propone, approva e chiude i passi prima del ``check``: il piano è sul controllo.

    *writes* sono le pagine che i passi scrivono (dopo l'Approva: lavoro del piano).
    """
    text = await propose(driver, "Rifare la wiki", steps or STEPS)
    assert "card" in text, text
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    root = driver.host.notebook_root("piante")
    for rel, body in (writes or {}).items():
        write_file(root, rel, body)
    for _ in range(len(steps or STEPS) - 1):
        assert "closed" in await driver.step_done(KEY, "fatto")
        await driver.on_turn_end(KEY, {})
    return driver.store.load(plan.id)


def _messages(bus: FakeBus) -> list[str]:
    return [m.content for m in bus.inbound
            if m.metadata.get(INJECTED_EVENT_META) == PLAN_STEP_EVENT]


async def test_a_clean_notebook_closes_the_check_without_a_turn(
    driver: PlanDriver, bus: FakeBus, notebook: Path
) -> None:
    (notebook / "wiki" / "vecchia" / "vuota").mkdir(parents=True)
    plan = await _run_to_check(driver)
    assert plan.status == "done"
    check = plan.steps[-1]
    assert check.status == "done" and check.note is None
    assert check.extra["machine_check"]["final"] is True
    assert check.extra["machine_check"]["left"] == 0
    assert check.extra["machine_check"]["removed_dirs"] == 2
    assert not (notebook / "wiki" / "vecchia").exists()
    assert len(_messages(bus)) == 2  # ricerca e scrittura: il controllo non ha turni


async def test_defects_open_a_repair_list_instead_of_a_free_check(
    driver: PlanDriver, bus: FakeBus, notebook: Path
) -> None:
    plan = await _run_to_check(
        driver, writes={"wiki/salvia.md": _page("- pH: query da fare: «salvia ph»\n")}
    )
    assert plan.status == "running"
    message = _messages(bus)[-1]
    assert "This step is only the repair" in message
    assert "wiki/salvia.md" in message and "query da fare" in message
    assert "Zero link morti" not in message  # il done_when libero non c'è più
    assert plan.steps[-1].extra["machine_check"]["round"] == 1


async def test_the_recheck_refuses_once_then_closes_with_what_is_left(
    driver: PlanDriver, notebook: Path
) -> None:
    plan = await _run_to_check(
        driver, writes={"wiki/salvia.md": _page("- pH: query da fare: «salvia ph»\n")}
    )
    refused = await driver.step_done(KEY, "riparato")
    assert refused.startswith("Not closed yet: the code checked the notebook again")
    assert "wiki/salvia.md" in refused and "last round" in refused
    assert driver.store.load(plan.id).steps[-1].extra["machine_check"]["round"] == 2
    last = await driver.step_done(KEY, "riparato di nuovo")
    assert "It was the last step" in last and "still finds 1 thing(s)" in last
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    machine = plan.steps[-1].extra["machine_check"]
    assert plan.status == "done"
    assert machine["final"] is True and machine["left"] == 1
    assert machine["left_items"][0].startswith("wiki/salvia.md: left to search")


async def test_a_repair_that_fixes_everything_closes_at_the_first_recheck(
    driver: PlanDriver, notebook: Path
) -> None:
    plan = await _run_to_check(
        driver, writes={"wiki/salvia.md": _page("## Fonti\n- raw/research/salvia.md\n")}
    )
    write_file(notebook, "wiki/salvia.md", _page("## Fonti\n- https://example.org/salvia\n"))
    last = await driver.step_done(KEY, "link aggiunti")
    assert "It was the last step" in last and "still finds" not in last
    await driver.on_turn_end(KEY, {})
    plan = driver.store.load(plan.id)
    assert plan.status == "done" and plan.steps[-1].extra["machine_check"]["left"] == 0


async def test_defects_already_there_at_approve_are_not_asked(
    driver: PlanDriver, notebook: Path
) -> None:
    write_file(notebook, "wiki/vecchia.md", _page("Vedi [[non-esiste]].\n"))
    plan = await _run_to_check(driver)
    assert plan.status == "done"
    assert plan.steps[-1].extra["machine_check"]["preexisting"] == 1


async def test_expected_pages_that_are_missing_are_in_the_list(
    driver: PlanDriver, bus: FakeBus, notebook: Path
) -> None:
    steps = [dict(s) for s in STEPS]
    steps[-1]["expect"] = {"folders": "wiki/piante/*", "files": ["index", "acqua"]}
    write_file(notebook, "wiki/piante/salvia/index.md", _page())
    write_file(notebook, "wiki/piante/salvia/acqua.md", _page())
    write_file(notebook, "wiki/piante/timo/index.md", _page())
    plan = await _run_to_check(driver, steps)
    assert plan.status == "running"
    assert "wiki/piante/timo/acqua.md" in _messages(bus)[-1]


async def test_a_restart_on_the_check_step_checks_again_from_the_first_round(
    driver: PlanDriver, workspace: Path, notebook: Path
) -> None:
    plan = await _run_to_check(
        driver, writes={"wiki/salvia.md": _page("- query da fare: «x»\n")}
    )
    await driver.step_done(KEY, "riparato")  # rifiutato: secondo giro
    fresh = PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), FakeHost(workspace))
    assert fresh.recover() == [plan.id]
    await fresh.resume(plan.id)
    assert fresh.store.load(plan.id).steps[-1].extra["machine_check"]["round"] == 1


async def test_a_plan_without_a_final_check_gets_a_note(driver: PlanDriver) -> None:
    steps = [dict(s) for s in STEPS[:2]]
    text = await propose(driver, "Rifare la wiki", steps)
    assert "Note on the check" in text
    assert "Note on the check" not in await propose(driver, "Rifare la wiki", STEPS)


def test_expect_is_only_for_the_check_step_and_stays_inside() -> None:
    def _try(expect: Any, kind: str = "check") -> str:
        steps = [dict(s) for s in STEPS]
        steps[-1] = {**steps[-1], "kind": kind, "expect": expect}
        try:
            validate_proposal("Titolo", steps, [
                {"item": "x", "have": "", "missing": "y", "strategy": "web"},
            ])
        except PlanValidationError as exc:
            return str(exc)
        return ""

    assert _try({"folders": "wiki/piante/*", "files": ["index"]}) == ""
    assert "only the `check` step" in _try({"files": ["index"]}, kind="write")
    assert "inside the notebook" in _try({"folders": "../fuori/*", "files": ["index"]})
    # La barra iniziale si toglie: un percorso di ``expect`` parte dal quaderno.
    assert _try({"files": ["/wiki/index"]}) == ""
    assert "1 to 40 `files`" in _try({"folders": "wiki/*"})
    assert "must be an object" in _try(["index"])


async def test_a_step_resumed_after_a_restart_lists_its_interrupted_helpers(
    workspace: Path, bus: FakeBus
) -> None:
    """Il passo riparte da capo dopo Riprendi, i suoi aiutanti no: il messaggio dice
    di continuarli con ``subagent_send``. Solo quelli di questo piano e di questo passo."""
    host = FakeHost(workspace)
    driver = PlanDriver(PlanStore.for_workspace(workspace), bus, host)
    await propose(driver, "Rifare la wiki", STEPS)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    host.interrupted = [
        {"task_id": "t-acero", "lineage_id": "l1", "label": "Ricerca acero",
         "agent_type": "researcher", "plan_id": plan.id, "plan_step": "s1"},
        {"task_id": "t-altro", "lineage_id": "l2", "label": "Altro passo",
         "agent_type": "researcher", "plan_id": plan.id, "plan_step": "s2"},
        {"task_id": "t-vecchio", "lineage_id": "l3", "label": "Altro piano",
         "agent_type": "researcher", "plan_id": "p_altro", "plan_step": "s1"},
    ]
    restarted = PlanDriver(PlanStore.for_workspace(workspace), bus, host)
    assert restarted.recover() == [plan.id]
    await restarted.resume(plan.id)
    message = _messages(bus)[-1]
    assert "`t-acero`: Ricerca acero (researcher)" in message
    assert "subagent_send" in message
    assert "t-altro" not in message and "t-vecchio" not in message
    assert "app was closed" not in _messages(bus)[0]
