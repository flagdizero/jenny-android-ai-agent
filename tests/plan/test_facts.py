"""I fatti persi da un piano: estrazione, normalizzazione, confronto, e il blocco del passo.

Testi inventati: il repo è pubblico, e la taratura sui quaderni veri si fa
fuori dal repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.plan.driver import PlanDriver
from jenny.plan.facts import MAX_LOST, extract_facts, lost_facts
from jenny.plan.store import PlanStore
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT
from tests.support.plan import KEY, STEPS, FakeBus, FakeHost, propose, write_file

# ── estrazione ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "facts"),
    [
        ("Ideale: **18–27 °C**, umidità 50-70%", ["18-27°c", "50-70%"]),
        ("da 18 a 27 °C", ["18-27°c"]),
        ("tra 2 e 3 giorni", ["2-3d"]),
        ("from 18 to 27°C", ["18-27°c"]),
        ("between 2 and 3 weeks", ["2-3w"]),
        ("18 °C – 27 °C", ["18-27°c"]),
        ("pH 4,5–5,5", ["ph4.5-5.5"]),
        ("pH ottimale 6", ["ph6"]),
        ("terriccio e perlite 1:1, diluire 1:10", ["1:1", "1:10"]),
        ("vaso da 1,50 m", ["1.5m"]),
        ("mai sotto −5 °C", ["-5°c"]),
        ("2 years", ["2y"]),
        ("2 anni", ["2y"]),
        ("1.000 lux", ["1000lux"]),
        ("3 pollici", ["3in"]),
    ],
)
def test_facts_are_numbers_with_units_normalised(text: str, facts: list[str]) -> None:
    assert list(extract_facts(text)) == facts


@pytest.mark.parametrize(
    "text",
    [
        "Anni 4-5: due rami",  # un numero senza unità
        "USDA 5-8, H5",
        "aggiornato alle 15:40",  # un orario, non un rapporto
        "ogni 10 min",
        "- 5 rami",
    ],
)
def test_numbers_without_a_unit_are_not_facts(text: str) -> None:
    assert extract_facts(text) == {}


def test_the_frontmatter_is_skipped_and_the_line_is_the_context() -> None:
    text = "---\nupdated: 2026-10-20\nsize: 5 cm\n---\n# Pianta\n\n- Vaso: **30 cm** di diametro\n"
    assert extract_facts(text) == {"30cm": "- Vaso: 30 cm di diametro"}


def test_the_same_fact_in_two_spellings_is_one_fact() -> None:
    before = extract_facts("Temperatura da 18 a 27 °C, vaso 1,20 m")
    after = extract_facts("Temperature 18–27°C; pot 1.2 m")
    assert set(before) == set(after)


# ── confronto ───────────────────────────────────────────────────────────────


def test_a_fact_missing_from_every_page_is_lost_with_its_page_and_line() -> None:
    before = {
        "wiki/felce.md": "- Umidità: 60-80%\n- Temperatura: 16-24 °C\n",
        "wiki/cactus.md": "- Acqua ogni 3 settimane",
    }
    after = {
        "wiki/felce.md": "- Umidità: 60–80 %\n",
        # Spostato in un'altra pagina: non è perso.
        "wiki/acqua.md": "Il cactus si bagna ogni 3 weeks.",
    }
    result = lost_facts(before, after)
    assert result.total == 1
    [item] = result.items
    assert (item.page, item.fact) == ("wiki/felce.md", "16-24°c")
    assert item.context == "- Temperatura: 16-24 °C"


def test_only_wiki_pages_count_on_both_sides() -> None:
    before = {"raw/research/felce.md": "30 cm", "wiki/felce.md": "vaso 20 cm"}
    after = {"raw/research/felce.md": "vaso 20 cm", "wiki/felce.md": "vaso"}
    result = lost_facts(before, after)
    assert [(i.page, i.fact) for i in result.items] == [("wiki/felce.md", "20cm")]


def test_the_list_is_sorted_by_page_and_capped_with_the_real_total() -> None:
    before = {
        "wiki/b.md": "\n".join(f"- riga {n}: {n} cm" for n in range(1, 31)),
        "wiki/a.md": "\n".join(f"- riga {n}: {n} g" for n in range(1, 31)),
    }
    result = lost_facts(before, {"wiki/a.md": "vuota"})
    assert result.total == 60
    assert len(result.items) == MAX_LOST
    assert result.items[0].page == "wiki/a.md" and result.items[-1].page == "wiki/b.md"


# ── il blocco nel passo di verifica ─────────────────────────────────────────


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/felce.md", "# Felce\n\n- Umidità: 60-80%\n- Temperatura: 16-24 °C\n")
    return ws


@pytest.fixture()
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture()
def driver(workspace: Path, bus: FakeBus) -> PlanDriver:
    return PlanDriver(PlanStore.for_workspace(workspace), bus, FakeHost(workspace))


def _step_messages(bus: FakeBus) -> list[Any]:
    return [m for m in bus.inbound if m.metadata.get(INJECTED_EVENT_META) == PLAN_STEP_EVENT]


async def _approve(driver: PlanDriver, steps: list[dict[str, Any]]) -> Any:
    assert "card" in await propose(driver, "Rifare la wiki", steps)
    plan = driver.store.open_plan_for("piante")
    await driver.approve(plan.id, plan.version, [])
    return driver.store.load(plan.id)


async def _close(driver: PlanDriver) -> None:
    assert "closed" in await driver.step_done(KEY, "fatto")
    await driver.on_turn_end(KEY, {})


_LOST_HEADER = "Facts that were in the notebook before this plan and are in no page now"


async def test_the_check_step_lists_the_facts_the_plan_lost(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    await _approve(driver, STEPS)
    await _close(driver)
    # Il passo di scrittura riscrive la pagina e perde la temperatura.
    write_file(workspace / "wikis" / "piante", "wiki/felce.md", "# Felce\n\n- Umidità 60–80 %\n")
    await _close(driver)
    research, write, check = _step_messages(bus)
    assert _LOST_HEADER not in research.content and _LOST_HEADER not in write.content
    assert _LOST_HEADER in check.content
    assert '- wiki/felce.md: 16-24°c — "- Temperatura: 16-24 °C"' in check.content
    assert "60-80%" not in check.content
    assert "shown of" not in check.content


async def test_a_plan_without_a_check_step_is_refused(driver: PlanDriver) -> None:
    """Fino al 26/10 i fatti persi (e i buchi riportati) finivano sull'ultimo passo
    qualunque fosse: sul Titan «Riscrivi i drawer». Il controllo e' obbligatorio."""
    from tests.support.plan import propose

    steps = [dict(s, kind="write") if s["kind"] == "check" else s for s in STEPS]
    text = await propose(driver, "Rifare la wiki", steps)
    assert text.startswith("Error") and "The last step must be a `check`" in text
    assert driver.store.open_plan_for("piante") is None


async def test_nothing_lost_means_no_block(driver: PlanDriver, bus: FakeBus) -> None:
    await _approve(driver, STEPS)
    await _close(driver)
    await _close(driver)
    assert all(_LOST_HEADER not in m.content for m in _step_messages(bus))


async def test_a_capped_list_says_the_total(
    driver: PlanDriver, workspace: Path, bus: FakeBus
) -> None:
    nb = workspace / "wikis" / "piante"
    write_file(nb, "wiki/vasi.md", "\n".join(f"- vaso {n}: {n} cm" for n in range(1, 51)))
    await _approve(driver, STEPS)
    write_file(nb, "wiki/vasi.md", "# Vasi\n")
    await _close(driver)
    await _close(driver)
    check = _step_messages(bus)[-1]
    assert f"({MAX_LOST} shown of 50)" in check.content
