"""Un monitor che passa il controllo a un aiutante non è un monitor che ha taciuto.

Fino al 31/10/2026 un monitor che chiamava ``spawn`` veniva registrato
``silenced``: la sequenza dei controlli mancati si azzerava e l'esito
dell'aiutante si perdeva. Ora il run resta ``delegated``, il verdetto lo scrive il
turno d'annuncio dell'aiutante (``MonitorFollowup``), e una delega mai tornata la
chiude il run dopo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from support.sessions import FakeSessions

from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage
from jenny.cron.bound_runner import run_bound_cron_job
from jenny.cron.monitor_followup import MonitorFollowup, job_id_of_monitor_session
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule
from jenny.session.keys import notebook_cron_session_key


class _Agent:
    def __init__(self, final_text: str) -> None:
        self.tools = ToolRegistry()
        self.sessions = FakeSessions()
        self.final_text = final_text
        self.prompts: list[str] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        return TurnOutcome.silent(final_text=self.final_text)


def _monitor(tmp_path: Path, agent: _Agent) -> tuple[CronService, str]:
    service = CronService(tmp_path / "cron" / "jobs.json")

    async def on_job(job: Any) -> str | None:
        return await run_bound_cron_job(job, agent=agent, cron=service)

    service.on_job = on_job
    job = service.add_job(
        name="pioggia",
        schedule=CronSchedule(kind="every", every_ms=1_800_000),
        message="controlla la pioggia",
        mode="monitor",
        session_key="unified:default",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    return service, job.id


def _followup(service: CronService) -> MonitorFollowup:
    return MonitorFollowup(cron=service, now_ms=lambda: 1_000)


async def test_a_delegated_run_is_recorded_as_delegated_and_keeps_the_streak(tmp_path) -> None:
    agent = _Agent("CHECK_FAILED: host down")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)
    assert service.get_job(job_id).state.consecutive_could_not_check == 1

    agent.final_text = "CHECK_DELEGATED: asked a helper to read the rain gauge"
    await service.run_job(job_id)

    state = service.get_job(job_id).state
    assert state.last_status == "delegated"
    assert state.pending_since_ms is not None
    # Il silenzio di un turno che ha delegato non prova niente: la sequenza resta.
    assert state.consecutive_could_not_check == 1


async def test_the_helpers_announce_gets_the_vocabulary_only_in_the_monitor_session(
    tmp_path,
) -> None:
    agent = _Agent("CHECK_DELEGATED: read the gauge")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)
    followup = _followup(service)

    block = followup.prompt_block(f"cron:{job_id}")
    assert "`checked=true`" in block and "`checked=false`" in block
    assert "controlla la pioggia" in block
    assert followup.prompt_block("unified:default") == ""
    assert followup.prompt_block("cron:reminders") == ""


async def test_check_ok_from_the_announce_closes_the_delegation(tmp_path) -> None:
    agent = _Agent("CHECK_FAILED: host down")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)
    agent.final_text = "CHECK_DELEGATED: read the gauge"
    await service.run_job(job_id)

    _followup(service).record(f"cron:{job_id}", final_text="All dry.\nCHECK_OK")

    state = service.get_job(job_id).state
    assert state.pending_since_ms is None
    assert state.consecutive_could_not_check == 0
    assert state.last_status == "silenced"
    assert state.run_history[-1].status == "silenced"


async def test_check_failed_from_the_announce_counts(tmp_path) -> None:
    agent = _Agent("CHECK_DELEGATED: read the gauge")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)

    _followup(service).record(f"cron:{job_id}", final_text="CHECK_FAILED: gauge offline\nCHECK_WARNED")

    state = service.get_job(job_id).state
    assert state.last_status == "could_not_check"
    assert state.last_error == "gauge offline"
    assert state.consecutive_could_not_check == 1
    assert state.could_not_check_escalated is True
    assert state.pending_since_ms is None


async def test_an_announce_without_a_verdict_leaves_it_to_the_next_run(tmp_path) -> None:
    agent = _Agent("CHECK_FAILED: host down")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)
    agent.final_text = "CHECK_DELEGATED: read the gauge"
    await service.run_job(job_id)

    _followup(service).record(f"cron:{job_id}", final_text="Here is the result.")
    assert service.get_job(job_id).state.pending_since_ms is not None

    # Il run dopo chiude la delega senza verdetto: il controllo vale come avvenuto.
    agent.final_text = "Nothing new."
    await service.run_job(job_id)
    state = service.get_job(job_id).state
    assert state.pending_since_ms is None
    assert state.last_status == "silenced"
    assert state.consecutive_could_not_check == 0


async def test_the_delegated_state_survives_a_restart(tmp_path) -> None:
    agent = _Agent("CHECK_DELEGATED: read the gauge")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)

    reloaded = CronService(tmp_path / "cron" / "jobs.json")
    assert reloaded.get_job(job_id).state.pending_since_ms is not None


def test_the_job_id_comes_from_both_monitor_session_shapes() -> None:
    assert job_id_of_monitor_session("cron:abc123") == "abc123"
    assert job_id_of_monitor_session(notebook_cron_session_key("casa", "abc123")) == "abc123"
    assert job_id_of_monitor_session("unified:default") is None
    assert job_id_of_monitor_session("project:casa") is None


async def test_a_spoken_result_closes_the_delegation_as_run(tmp_path) -> None:
    """Un annuncio che ha detto all'utente il risultato ha girato, anche se non
    l'ha dichiarato: il guasto, quando c'è, lo dice ``checked=false``."""
    agent = _Agent("CHECK_DELEGATED: read the gauge")
    service, job_id = _monitor(tmp_path, agent)
    await service.run_job(job_id)

    _followup(service).record(f"cron:{job_id}", final_text="", spoke=True)

    state = service.get_job(job_id).state
    assert state.pending_since_ms is None and state.last_status == "ok"
