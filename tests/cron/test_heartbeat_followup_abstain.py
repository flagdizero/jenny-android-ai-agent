"""Un'astensione dichiarata col tool non deve poter chiudere un controllo.

Il tool ``nothing_to_report`` esiste perché su un turno silenzioso tacere non
era un'azione. Trascriverlo nel linguaggio dei marcatori è ciò che gli permette
di non toccare nessun lettore a valle — ma solo con un numero esplicito, ed è
questo il file che lo tiene fermo.

``CHECK_OK`` non significa "non ho niente da dire", significa "il controllo ha
prodotto la sua risposta". Un marcatore anonimo viene attribuito al task in
sospeso quando ce n'è uno solo (``attribute_marks``, parametro ``default``) e
``record_followup_outcomes`` su un verdetto positivo fa
``del state.task_checks[task.id]``: sparisce la sequenza dei guasti, sparisce
``since_ms``, sparisce ``escalated``. Sintetizzarlo su ogni astensione avrebbe
chiuso come sano un controllo delegato di cui il turno non ha detto niente,
reso codice morto il ramo "il verdetto non è arrivato affatto" di
``resolve_pending_delegations`` — che ``escalated`` lo conserva di proposito — e
azzerato per sempre la sequenza da cui dipende ``silence_watchdog``.

Il turno d'annuncio qui passa dall'``AgentLoop`` vero
(``_process_system_message``) e dal ``HeartbeatFollowup`` vero, con lo stato su
disco in un ``CronService`` vero. Solo la chiamata del modello è recitata: al
suo posto il fake invoca il **tool vero** preso dal registry del loop.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.sessions import FakeSessions

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.nothing_to_report import NothingToReportTool
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.heartbeat_tasks import parse_heartbeat_tasks
from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from jenny.providers.base import LLMResponse
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.session.keys import HEARTBEAT_SESSION_KEY

_WATERBOT = "- Ogni ciclo controlla l'umidità delle piante e avvisami sotto il 15%."
_VITAMINE = "- Alle 9 ricordami le vitamine."

_T0_MS = 1_755_000_000_000
_CYCLE_MS = 1_800_000


class _DelegatingAgent:
    """T0: delega entrambi i task e lo dichiara, come chiede il contratto."""

    def __init__(self) -> None:
        self.sessions = FakeSessions()
        self.delegated: dict[int, str] = {}

    async def process_direct_outcome(self, prompt: str, **_kwargs: Any) -> TurnOutcome:
        marks = "\n".join(
            f"CHECK_DELEGATED {number}: {what}"
            for number, what in sorted(self.delegated.items())
        )
        return TurnOutcome.silent(final_text=marks)

    def evict_pruned_sessions(self, keys: list[str]) -> None:  # pragma: no cover
        pass


class _Harness:
    def __init__(self, tmp_path: Path, *tasks: str) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.file = tmp_path / "HEARTBEAT.md"
        self.file.write_text(
            "# Heartbeat Tasks\n\n## Active Tasks\n\n" + "\n".join(tasks) + "\n",
            encoding="utf-8",
        )
        self.agent = _DelegatingAgent()
        self.service = CronService(tmp_path / "cron" / "jobs.json")
        self.now_ms = _T0_MS
        self.service.on_job = CronDispatcher(
            get_agent=lambda: self.agent,
            config=SimpleNamespace(workspace_path=tmp_path),
            cron=self.service,
            heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
            now_ms=lambda: self.now_ms,
        ).dispatch
        self.service.register_system_job(
            CronJob(
                id="heartbeat",
                name="heartbeat",
                schedule=CronSchedule(kind="every", every_ms=_CYCLE_MS),
                payload=CronPayload(kind="system_event"),
            )
        )

        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.generation = SimpleNamespace(max_tokens=4096)
        provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content=""))
        self.loop = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=tmp_path,
            model="test-model",
            cron_service=self.service,
        )
        self.loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
            return_value=False
        )
        # Cosa fa il turno d'annuncio: ``None`` = si astiene senza numero.
        self.abstains_with: int | None = None
        # ...e ``False`` = non chiama niente, cioè il comportamento di prima.
        self.abstains = True
        self.announce_count = 0
        self.loop._run_agent_loop = self._fake_announce_turn  # type: ignore[method-assign]

    async def _fake_announce_turn(self, initial_messages: list[dict], **_kwargs: Any):
        if self.abstains:
            tool = self.loop.tools.get("nothing_to_report")
            assert isinstance(tool, NothingToReportTool)
            await tool.execute(task=self.abstains_with)
        return ("", [], list(initial_messages), "stop", False)

    async def cycle(self) -> None:
        self.now_ms += _CYCLE_MS
        await self.service.run_job("heartbeat")
        self.announce_count += 1
        await self.loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="websocket:default",
                content="[Subagent 'waterbot-check' completed successfully]\n\nResult:\nok",
                metadata={"subagent_task_id": f"sub-{self.announce_count}"},
                session_key_override=HEARTBEAT_SESSION_KEY,
            )
        )

    @property
    def state(self) -> CronJobState:
        job = self.service.get_job("heartbeat")
        assert job is not None
        return job.state

    def entry_for(self, index: int):
        tasks = parse_heartbeat_tasks(self.file.read_text(encoding="utf-8"))
        return self.state.task_checks.get(tasks[index].id)


@pytest.fixture
def one_delegated(tmp_path: Path) -> _Harness:
    harness = _Harness(tmp_path, _WATERBOT, _VITAMINE)
    harness.agent.delegated = {1: "leggi l'umidità"}
    return harness


@pytest.fixture
def two_delegated(tmp_path: Path) -> _Harness:
    harness = _Harness(tmp_path, _WATERBOT, _VITAMINE)
    harness.agent.delegated = {1: "leggi l'umidità", 2: "controlla le vitamine"}
    return harness


class TestABareAbstentionDeclaresNothing:
    async def test_it_does_not_close_a_delegated_check(self, one_delegated: _Harness) -> None:
        """Il caso pericoloso: un controllo delegato in sospeso, uno solo, e un
        turno che si astiene senza dire di quale controllo sta parlando."""
        one_delegated.abstains_with = None

        await one_delegated.cycle()

        entry = one_delegated.entry_for(0)
        assert entry is not None
        assert entry.pending_since_ms is not None

    async def test_it_leaves_the_state_exactly_as_a_silent_announce_would(
        self, one_delegated: _Harness, tmp_path: Path
    ) -> None:
        """Nessuna regressione possibile: l'astensione nuda è il vecchio
        comportamento, byte per byte."""
        one_delegated.abstains_with = None
        await one_delegated.cycle()
        with_tool = one_delegated.entry_for(0)

        silent = _Harness(tmp_path / "b", _WATERBOT, _VITAMINE)
        silent.agent.delegated = {1: "leggi l'umidità"}
        silent.abstains = False
        await silent.cycle()

        assert with_tool == silent.entry_for(0)

    async def test_the_next_run_still_sees_a_verdict_that_never_arrived(
        self, one_delegated: _Harness
    ) -> None:
        """``resolve_pending_delegations`` deve restare vivo: è il ramo che
        conserva ``escalated`` quando nessuno si è pronunciato."""
        one_delegated.abstains_with = None

        await one_delegated.cycle()
        await one_delegated.cycle()

        # Il run successivo risolve la delega non riferita e, non essendo mai
        # stato dato un avviso, ne cancella la voce: la nuova delega dello stesso
        # ciclo la riapre in sospeso.
        entry = one_delegated.entry_for(0)
        assert entry is not None
        assert entry.pending_since_ms is not None


class TestANumberedAbstentionClosesOnlyThatCheck:
    async def test_it_closes_the_check_it_names(self, two_delegated: _Harness) -> None:
        two_delegated.abstains_with = 1

        await two_delegated.cycle()

        assert two_delegated.entry_for(0) is None

    async def test_it_leaves_the_other_one_pending(self, two_delegated: _Harness) -> None:
        """Con due controlli in sospeso un verdetto anonimo non chiuderebbe
        niente; uno numerato deve chiudere esattamente il suo."""
        two_delegated.abstains_with = 1

        await two_delegated.cycle()

        other = two_delegated.entry_for(1)
        assert other is not None
        assert other.pending_since_ms is not None

