"""La passata del giardiniere e la bolla di lavoro della home.

Cosa si fissa: con un reporter la passata annuncia le sue fasi, passa al turno
progresso e riprova al posto del silenzio, conta le scritture mentre avvengono;
``yield_to_user=False`` (la passata chiesta a mano) non cede il passo; senza
reporter non cambia niente. E la chiusura: i campi dell'esito, non frasi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.gardener import (
    GardenerOutcome,
    _yield_to_user_guard,
    report_gardener_outcome,
    run_gardener,
)
from jenny.bus.events import OUTBOUND_META_WORK_STATUS
from jenny.runtime import work_status
from jenny.runtime.work_status import WorkReporter
from jenny.session.turn_visibility import silent_progress
from tests.agent.test_gardener import _FakeAgent, _project, _store

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


class _Bus:
    def __init__(self) -> None:
        self.sent: list = []

    def try_publish_outbound(self, msg) -> bool:
        self.sent.append(msg)
        return True

    def phases(self) -> list:
        return [m.metadata[OUTBOUND_META_WORK_STATUS]["phase"] for m in self.sent]


@pytest.fixture(autouse=True)
def _clean_registry():
    work_status.reset_registry()
    yield
    work_status.reset_registry()


def _agent(tmp_path: Path, *, busy: tuple[str, ...] = ()) -> _FakeAgent:
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    agent = _FakeAgent(sessions)
    agent.active_session_keys = lambda: busy  # type: ignore[attr-defined]
    return agent


def _reporter(bus: _Bus) -> WorkReporter:
    return WorkReporter(bus, kind="gardener", chat_id="project:viaggio", phases=("pages", "board"))


async def test_the_pass_announces_its_phase_and_hands_the_turn_the_reporter(tmp_path) -> None:
    _project(tmp_path)
    agent, bus = _agent(tmp_path), _Bus()
    reporter = _reporter(bus)

    await run_gardener(agent, _store(tmp_path), reporter=reporter)

    assert "pages" in bus.phases()
    (call,) = agent.calls
    assert call["on_progress"] == reporter.on_progress
    assert call["on_retry_wait"] == reporter.on_retry_wait


async def test_without_a_reporter_the_turn_stays_silent(tmp_path) -> None:
    _project(tmp_path)
    agent = _agent(tmp_path)

    await run_gardener(agent, _store(tmp_path))

    (call,) = agent.calls
    assert call["on_progress"] is silent_progress
    assert call["on_retry_wait"] is None


async def test_writes_are_counted_by_the_guard_and_a_manual_pass_does_not_yield(
    tmp_path, monkeypatch
) -> None:
    """Il cuore della passata a mano: chi l'ha lanciata scrive nel quaderno
    (``project:viaggio`` in volo) e la passata continua, contando le scritture."""
    _project(tmp_path)
    agent, bus = _agent(tmp_path, busy=("project:viaggio",)), _Bus()
    reporter = _reporter(bus)
    store = _store(tmp_path)
    guards: list = []
    real_build = store.build_tools

    def _capture(*, write_guard=None, **kwargs):
        guards.append(write_guard)
        return real_build(write_guard=write_guard, **kwargs)

    monkeypatch.setattr(store, "build_tools", _capture)

    await run_gardener(agent, store, reporter=reporter, yield_to_user=False)

    page = (tmp_path / "wikis" / "viaggio" / "wiki" / "tappe.md").resolve()
    assert guards[0](page, "# tappe\n") is None
    assert reporter.writes == 1


async def test_an_automatic_pass_still_yields_and_the_refused_write_is_not_counted(
    tmp_path, monkeypatch
) -> None:
    _project(tmp_path)
    agent, bus = _agent(tmp_path, busy=("project:viaggio",)), _Bus()
    reporter = _reporter(bus)
    store = _store(tmp_path)
    guards: list = []
    real_build = store.build_tools

    def _capture(*, write_guard=None, **kwargs):
        guards.append(write_guard)
        return real_build(write_guard=write_guard, **kwargs)

    monkeypatch.setattr(store, "build_tools", _capture)

    await run_gardener(agent, store, reporter=reporter)

    page = (tmp_path / "wikis" / "viaggio" / "wiki" / "tappe.md").resolve()
    assert guards[0](page, "# tappe\n") is not None
    assert reporter.writes == 0


def test_the_yield_guard_is_the_one_a_manual_pass_leaves_out(tmp_path) -> None:
    """Contro-prova del test sopra: la guardia, montata, rifiuta davvero."""
    agent = _agent(tmp_path, busy=("project:viaggio",))
    guard = _yield_to_user_guard(agent, "viaggio", [])
    assert guard is not None
    assert guard("x", "y") is not None


def test_the_outcome_closes_the_bubble_with_fields(tmp_path) -> None:
    root = _project(tmp_path)
    bus = _Bus()
    reporter = _reporter(bus)

    report_gardener_outcome(
        reporter, GardenerOutcome(status="written", writes=7, board="done"), root,
        absence="",
    )

    payload = bus.sent[-1].metadata[OUTBOUND_META_WORK_STATUS]
    assert payload["state"] == "done"
    assert payload["outcome"] == {
        "status": "written", "writes": 7, "board": "done", "proposals": 0,
    }


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_a_pass_that_did_not_finish_is_a_failed_bubble(tmp_path, status) -> None:
    root = _project(tmp_path)
    bus = _Bus()
    reporter = _reporter(bus)

    report_gardener_outcome(reporter, GardenerOutcome(status=status), root)

    payload = bus.sent[-1].metadata[OUTBOUND_META_WORK_STATUS]
    assert payload["state"] == "failed"
    assert payload["outcome"]["reason"] == "model"
