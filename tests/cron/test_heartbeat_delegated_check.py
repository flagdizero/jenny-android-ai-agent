"""Un controllo dell'heartbeat **delegato** a un subagent può essere registrato.

Il caso è quello osservato sul Titan 2 il 2026-08-11 e riprodotto qui per intero.
L'agente principale gira in ``orchestrator_mode`` e non ha ``python_exec``:
il task WaterBot — quello che ha motivato tutto il terzo esito — *deve* essere
delegato con ``spawn``, che ritorna subito. Il turno dell'heartbeat finiva quindi
senza marcatori (logcat: ``Heartbeat: check completed`` alle 12:48:02) mentre il
subagent eseguiva il suo ``python_exec`` alle 12:48:08. Per B13, "nessun
marcatore" voleva dire "il task è stato eseguito": un controllo delegato non
poteva essere registrato come mancato, mai.

Qui il ciclo è completo e nessuno dei due turni è recitato a copione:

- **T0**, il turno dell'heartbeat, passa dal ``CronDispatcher`` vero e da un
  agente che *legge il prompt* e si comporta come il contratto gli chiede.
- **T1**, il turno d'annuncio del subagent, passa dall'``AgentLoop`` vero
  (``_process_system_message``): è il percorso reale, bus compreso nella forma
  del messaggio, e vede il blocco di prompt che gli mette
  ``jenny.cron.heartbeat_followup``.

Lo stato passa dal disco, con un ``CronService`` vero.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.sessions import FakeSessions

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.message import MessageTool
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.could_not_check import ESCALATE_AFTER_FAILURES
from jenny.cron.heartbeat_tasks import parse_heartbeat_tasks
from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from jenny.providers.base import LLMResponse
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.session.keys import HEARTBEAT_SESSION_KEY, UNIFIED_SESSION_KEY

_WATERBOT = (
    "- Ogni ciclo, controlla l'umidità delle piante e avvisami solo se una è sotto il 15%. "
    "Se hps è irraggiungibile salta il ciclo in silenzio."
)
_VITAMINE = "- Alle 9 ricordami le vitamine."

_ESCALATION_HEAD = "These recurring tasks have now failed to run"
_FOLLOWUP_HEAD = "This subagent was doing the work of a scheduled check"
_SILENCE_HEAD = "The user has ALREADY been told"

# Un istante fisso e il passo dell'heartbeat sul device.
_T0_MS = 1_755_000_000_000
_CYCLE_MS = 1_800_000


def _heartbeat_md(*tasks: str) -> str:
    body = "\n".join(tasks)
    return f"# Heartbeat Tasks\n\n## Active Tasks\n\n{body}\n"


def _escalated_labels(prompt: str) -> list[str]:
    """Le etichette dei controlli che il prompt sta chiedendo di riferire."""
    lines = prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if _ESCALATION_HEAD in line)
    labels: list[str] = []
    for line in lines[start + 1:]:
        if not line.startswith("- "):
            break
        labels.append(line.split(". ", 1)[1])
    return labels


class _DelegatingHeartbeatAgent:
    """T0: l'orchestratore. Delega, e per contratto lo dichiara.

    ``delegated``: numero del task → cosa è stato passato al subagent. Non
    produce nessun risultato in questo turno — è il punto: ``spawn`` ritorna
    subito.
    """

    def __init__(self) -> None:
        self.sessions = FakeSessions()
        self.prompts: list[str] = []
        self.messages: list[str] = []
        self.delegated: dict[int, str] = {}
        self.declares_the_delegation = True

    async def process_direct_outcome(self, prompt: str, **_kwargs: Any) -> TurnOutcome:
        self.prompts.append(prompt)
        if not self.declares_the_delegation:
            return TurnOutcome.silent(final_text="")
        marks = "\n".join(
            f"CHECK_DELEGATED {number}: {what}"
            for number, what in sorted(self.delegated.items())
        )
        return TurnOutcome.silent(final_text=marks)

    def evict_pruned_sessions(self, keys: list[str]) -> None:  # pragma: no cover
        pass


class _Harness:
    """``CronService`` + ``CronDispatcher`` + ``AgentLoop`` veri."""

    def __init__(self, tmp_path: Path, content: str) -> None:
        self.workspace = tmp_path
        self.file = tmp_path / "HEARTBEAT.md"
        self.file.write_text(content, encoding="utf-8")
        self.store_path = tmp_path / "cron" / "jobs.json"
        self.agent = _DelegatingHeartbeatAgent()
        self.service = CronService(self.store_path)
        # Un orologio che si guida: l'heartbeat confronta l'istante dell'avviso
        # con quello dell'ultimo messaggio dell'utente, e col tempo vero i due
        # cadono nello stesso millisecondo.
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
                schedule=CronSchedule(kind="every", every_ms=1_800_000),
                payload=CronPayload(kind="system_event"),
            )
        )

        # L'agente del turno d'annuncio: quello vero, con il provider finto.
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
        self.announce_prompts: list[str] = []
        # Cosa il subagent riferisce: ``None`` = ha fatto il suo lavoro.
        self.subagent_failure: tuple[int, str] | None = None
        self.announces_the_number = True
        # Un modello che si scorda il marcatore, in un verso o nell'altro. È il
        # comportamento osservato sul device il 2026-08-13, e l'unica cosa che
        # separa un avviso per guasto da uno ogni due ore.
        self.forgets_the_marker = False
        # E la stessa dimenticanza a intermittenza. Il flag qui sopra è a senso
        # unico — una volta acceso il modello non dichiara mai più — quindi il
        # motivo che rende questo difetto vivo, cioè un verdetto sì e uno no,
        # non era rappresentabile: v. ``forgets_the_next``.
        self._forgetful_announces = 0
        self.announce_count = 0
        self.loop._run_agent_loop = self._fake_announce_turn  # type: ignore[method-assign]

    async def _fake_announce_turn(self, initial_messages: list[dict], **_kwargs: Any):
        """T1: legge il prompt e si comporta come gli è stato chiesto."""
        prompt = "\n\n".join(
            str(m.get("content")) for m in initial_messages if isinstance(m.get("content"), str)
        )
        self.announce_prompts.append(prompt)
        text = ""
        forgetful = self.forgets_the_marker or self._forgetful_announces > 0
        if self._forgetful_announces > 0:
            self._forgetful_announces -= 1
        if not forgetful and _FOLLOWUP_HEAD in prompt:
            number, reason = self.subagent_failure or (
                next(iter(self.agent.delegated), 1), "",
            )
            ref = f" {number}" if self.announces_the_number else ""
            if self.subagent_failure is None:
                # Il contratto chiede un verdetto in entrambi i versi: un
                # controllo che ha girato lo dichiara, e la sua voce si chiude.
                text = f"CHECK_OK{ref}"
            else:
                text = f"CHECK_FAILED{ref}: {reason}"
            if self.subagent_failure is not None and _ESCALATION_HEAD in prompt:
                tool = self.loop.tools.get("message")
                assert isinstance(tool, MessageTool)
                tool._sent_in_turn = True
                # Il messaggio nomina i controlli che il prompt elenca: se il
                # blocco non portasse le etichette, qui non ci sarebbe niente da
                # scrivere.
                self.agent.messages.append(
                    "Non riesco più a eseguire: " + ", ".join(_escalated_labels(prompt))
                )
                # E la riga che registra l'avviso, che il prompt chiede insieme
                # al messaggio: il timbro nello stato viene da qui, non dal fatto
                # che ``message`` sia stato chiamato.
                text += f"\nCHECK_WARNED{ref}"
        return (text, [], [*initial_messages, {"role": "assistant", "content": text}], "stop", False)

    def rewrite(self, content: str) -> None:
        self.file.write_text(content, encoding="utf-8")

    def forgets_the_next(self, announces: int) -> None:
        """I prossimi *announces* turni d'annuncio non dichiarano niente."""
        self._forgetful_announces = announces

    def user_says(self, text: str = "ciao", **extra: Any) -> None:
        """Una riga dell'utente nella conversazione unificata, timbrata adesso.

        L'orologio avanza di un minuto prima: si scrive *dopo* l'avviso, e due
        timbri identici non direbbero niente né in un senso né nell'altro.
        """
        self.now_ms += 60_000
        self.agent.sessions.get_or_create(UNIFIED_SESSION_KEY).messages.append(
            {
                "role": "user",
                "content": text,
                "timestamp": datetime.fromtimestamp(self.now_ms / 1000).isoformat(),
                **extra,
            }
        )

    async def cycle(self, *, session_key: str = HEARTBEAT_SESSION_KEY) -> None:
        """Un ciclo intero: il turno dell'heartbeat e poi il ritorno del subagent."""
        self.now_ms += _CYCLE_MS
        await self.service.run_job("heartbeat")
        self.announce_count += 1
        await self.loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="websocket:default",
                content="[Subagent 'waterbot-check' completed successfully]\n\nResult:\nvedi sopra",
                metadata={"subagent_task_id": f"sub-{self.announce_count}"},
                session_key_override=session_key,
            )
        )

    async def cycles(self, count: int) -> None:
        for _ in range(count):
            await self.cycle()

    @property
    def state(self) -> CronJobState:
        job = self.service.get_job("heartbeat")
        assert job is not None
        return job.state

    def entry_for(self, index: int):
        tasks = parse_heartbeat_tasks(self.file.read_text(encoding="utf-8"))
        return self.state.task_checks.get(tasks[index].id)


@pytest.fixture
def two_tasks(tmp_path: Path) -> _Harness:
    harness = _Harness(tmp_path, _heartbeat_md(_WATERBOT, _VITAMINE))
    harness.agent.delegated = {1: "leggi l'umidità da hps"}
    return harness


class TestTheDeviceCase:
    """Il controllo delle piante è delegato per forza: deve poter fallire."""

    async def test_a_delegated_failure_is_recorded_at_all(self, two_tasks: _Harness) -> None:
        """Prima di questa correzione la voce restava vuota: il turno che
        delegava non aveva marcatori e il task passava per eseguito."""
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")

        await two_tasks.cycles(1)

        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.consecutive_could_not_check == 1

    async def test_the_streak_survives_the_next_heartbeat_turn(
        self, two_tasks: _Harness
    ) -> None:
        """Il turno che delega non deve azzerare ciò che il ritorno ha scritto."""
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")

        await two_tasks.cycles(2)

        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.consecutive_could_not_check == 2

    async def test_three_delegated_failures_produce_exactly_one_message(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 1
        # E nomina il controllo, non l'heartbeat.
        assert "controlla l'umidità delle piante" in two_tasks.agent.messages[0]
        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.consecutive_could_not_check == ESCALATE_AFTER_FAILURES
        assert entry.escalated is True

    async def test_the_alert_is_not_repeated_while_the_check_stays_broken(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")

        await two_tasks.cycles(12)

        assert len(two_tasks.agent.messages) == 1

    async def test_a_delegated_check_that_works_is_never_mentioned(
        self, two_tasks: _Harness
    ) -> None:
        """Il caso normale: il subagent fa il suo lavoro e nessuno dice niente."""
        await two_tasks.cycles(10)

        assert two_tasks.agent.messages == []
        assert two_tasks.state.last_status == "ok"

    async def test_a_healthy_delegated_task_never_accumulates_a_streak(
        self, two_tasks: _Harness
    ) -> None:
        """Dentro il ciclo la voce esiste — dice "in attesa", non "rotto", e
        senza di lei il turno di ritorno non saprebbe di essere il giudice di
        qualcosa — ma il verdetto positivo la chiude, quindi a ciclo concluso
        non resta niente. Il contatore non si muove mai, ed è quello che decide
        se l'utente viene disturbato."""
        await two_tasks.service.run_job("heartbeat")
        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        assert list(two_tasks.state.task_checks) == [tasks[0].id]

        await two_tasks.cycles(10)

        assert two_tasks.state.task_checks == {}

    async def test_a_healthy_delegating_heartbeat_sees_the_same_prompt_every_time(
        self, two_tasks: _Harness
    ) -> None:
        """Delegare non deve costare una riga di prompt in più al giro dopo: la
        voce "in attesa" esiste per lo stato, non per il modello."""
        await two_tasks.cycles(5)

        assert len(set(two_tasks.agent.prompts)) == 1
        assert _ESCALATION_HEAD not in two_tasks.agent.prompts[0]

    async def test_a_check_that_starts_working_again_stops_the_streak(
        self, two_tasks: _Harness
    ) -> None:
        """Il ritorno alla normalità non è una notizia, e chiude la sequenza.

        La chiude il turno d'annuncio, che è l'unico ad avere in mano il
        risultato: lo dichiara con ``CHECK_OK`` e la voce sparisce. Un annuncio
        *silenzioso* non basterebbe — potrebbe essere il subagent di un altro
        task, o lo stesso che si è dimenticato di dire che è ancora rotto.
        """
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")
        await two_tasks.cycles(2)

        two_tasks.subagent_failure = None
        await two_tasks.cycles(2)

        assert two_tasks.entry_for(0) is None
        assert two_tasks.agent.messages == []

    async def test_the_healthy_task_in_the_same_file_is_left_alone(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        assert list(two_tasks.state.task_checks) == [tasks[0].id]
        assert "vitamine" not in two_tasks.agent.messages[0]

    async def test_with_one_pending_check_the_number_can_be_left_out(
        self, two_tasks: _Harness
    ) -> None:
        """Il file ha due task, ma in sospeso ce n'è uno: non c'è ambiguità."""
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")
        two_tasks.announces_the_number = False

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 1


class TestOneFaultIsOneWarning:
    """La notte del 2026-08-13, per intero, dal ciclo completo.

    Tailscale giù per dieci ore, un solo controllo rotto, **quattro** avvisi
    all'utente. Il meccanismo: il turno d'annuncio a volte non dichiarava
    niente, la voce restava "in sospeso", e il run successivo la cancellava
    insieme al ricordo di aver già parlato. Tre cicli dopo la sequenza era di
    nuovo a tre e l'avviso ripartiva.
    """

    async def test_a_forgotten_marker_does_not_bring_the_alert_back(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)
        assert len(two_tasks.agent.messages) == 1

        # Da qui il modello smette di dichiarare l'esito. Il guasto c'è ancora,
        # ma nessuno lo scrive: è esattamente il 02:31 del logcat.
        two_tasks.forgets_the_marker = True
        await two_tasks.cycles(12)

        assert len(two_tasks.agent.messages) == 1

    async def test_the_streak_still_restarts_when_nobody_reports(
        self, two_tasks: _Harness
    ) -> None:
        """Conservare il ricordo non vuol dire conservare il conteggio: da uno
        stato vecchio non deve poter nascere un allarme."""
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        two_tasks.forgets_the_marker = True
        await two_tasks.cycles(4)

        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.escalated is True
        assert entry.consecutive_could_not_check == 0

    async def test_a_new_fault_after_a_recovery_warns_again(
        self, two_tasks: _Harness
    ) -> None:
        """Il test che vale tutti gli altri: un avviso per guasto, e un guasto
        nuovo è un guasto nuovo."""
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)
        assert len(two_tasks.agent.messages) == 1

        # Il controllo torna a funzionare e lo dichiara: la voce si chiude, e con
        # lei il ricordo dell'avviso.
        two_tasks.subagent_failure = None
        await two_tasks.cycles(2)
        assert two_tasks.entry_for(0) is None

        # Settimane dopo si rompe di nuovo. L'utente deve saperlo.
        two_tasks.subagent_failure = (1, "hps di nuovo irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 2

    async def test_the_run_prompt_tells_it_to_stay_quiet_once_it_has_spoken(
        self, two_tasks: _Harness
    ) -> None:
        """Non chiedere di parlare non è chiedere di tacere: al quarto ciclo il
        modello sul device chiamava ``message`` di propria iniziativa."""
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert _ESCALATION_HEAD in two_tasks.agent.prompts[-1]
        assert _SILENCE_HEAD not in two_tasks.agent.prompts[-1]

        await two_tasks.cycles(1)

        assert _SILENCE_HEAD in two_tasks.agent.prompts[-1]
        assert _ESCALATION_HEAD not in two_tasks.agent.prompts[-1]

    async def test_a_healthy_run_never_carries_either_block(
        self, two_tasks: _Harness
    ) -> None:
        """La proprietà su cui si regge la cache di prefisso del provider."""
        await two_tasks.cycles(5)

        assert len(set(two_tasks.agent.prompts)) == 1
        assert _SILENCE_HEAD not in two_tasks.agent.prompts[0]
        assert _ESCALATION_HEAD not in two_tasks.agent.prompts[0]


class TestTheUserComingBack:
    """Su un controllo delegato il riarmo è l'unica uscita che non dipende dal modello.

    ``CHECK_OK`` chiude la voce, ma è il modello a doverlo scrivere — e sul
    device del 2026-08-16 lo stato *sano* di un controllo delegato era proprio
    un follow-up senza marcatori. Se ha scritto, invece, l'avviso l'ha letto:
    quello lo sappiamo senza chiederlo a nessuno.
    """

    async def test_a_user_message_costs_exactly_one_new_alert(
        self, two_tasks: _Harness
    ) -> None:
        """Un ciclo intero ha due turni che possono parlare — quello del run e
        quello d'annuncio — e il riarmo li riguarda entrambi. Uno solo dei due
        deve consegnare."""
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)
        assert len(two_tasks.agent.messages) == 1

        two_tasks.user_says("ci sei?")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 2

    async def test_the_alert_waits_for_the_streak_to_be_rebuilt(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)
        two_tasks.user_says()

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES - 1)

        assert len(two_tasks.agent.messages) == 1

    async def test_a_markerless_followup_cannot_be_turned_into_an_alert(
        self, two_tasks: _Harness
    ) -> None:
        """Il riarmo dà al modello il diritto di riparlare, non un guasto nuovo.

        Con i verdetti che smettono di arrivare — lo stato sano di un controllo
        delegato, misurato — la sequenza si azzera a ogni run, quindi non
        raggiunge mai la soglia e nessun messaggio dell'utente può farla
        arrivare. La direzione dell'errore del modulo, intatta.
        """
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        two_tasks.forgets_the_next(20)
        for _ in range(6):
            two_tasks.user_says()
            await two_tasks.cycles(2)

        assert len(two_tasks.agent.messages) == 1

    async def test_a_verdict_that_comes_back_after_a_silent_one_still_counts(
        self, two_tasks: _Harness
    ) -> None:
        """Il pattern del device: un annuncio dichiara, il successivo no.

        Serve a fissare la manopola a intermittenza, senza la quale il caso non
        era rappresentabile: ogni verdetto arrivato conta, e quelli saltati
        azzerano la sequenza senza cancellare il ricordo dell'avviso.
        """
        two_tasks.subagent_failure = (1, "hps irraggiungibile")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        for _ in range(4):
            two_tasks.forgets_the_next(1)
            await two_tasks.cycles(1)
            await two_tasks.cycles(1)

        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.escalated is True
        assert entry.consecutive_could_not_check == 1
        assert len(two_tasks.agent.messages) == 1


class TestTheOptimismIsPreserved:
    """Al massimo si tace su un guasto non dichiarato, mai il contrario."""

    async def test_a_subagent_that_never_reports_accumulates_nothing(
        self, two_tasks: _Harness
    ) -> None:
        """Voce in sospeso che nessuno smentisce: il run dopo la chiude come
        eseguita. È anche ciò che impedisce a una voce di restare appesa."""
        for _ in range(6):
            await two_tasks.service.run_job("heartbeat")

        assert two_tasks.agent.messages == []
        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.consecutive_could_not_check == 0
        assert entry.pending_since_ms is not None

    async def test_a_recovery_just_before_the_threshold_is_not_an_alert(
        self, two_tasks: _Harness
    ) -> None:
        """Due guasti, poi il controllo riparte, poi ne fallisce un altro: sono
        due sequenze da uno e due, non tre di fila. L'utente non deve sentire
        niente, e il prompt del run che segue la ripresa non deve chiedere di
        avvisarlo."""
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES - 1)

        two_tasks.subagent_failure = None
        await two_tasks.cycles(1)
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")
        await two_tasks.cycles(1)

        assert two_tasks.agent.messages == []
        assert _ESCALATION_HEAD not in two_tasks.agent.prompts[-1]
        entry = two_tasks.entry_for(0)
        assert entry is not None
        assert entry.consecutive_could_not_check == 1

    async def test_an_instructed_silent_skip_by_the_subagent_is_not_a_failure(
        self, two_tasks: _Harness
    ) -> None:
        """Il task WaterBot dice "se hps è irraggiungibile salta il ciclo in
        silenzio". Delegato, quel silenzio arriva dal subagent — e resta un
        successo: chi salta perché gli è stato chiesto ha fatto il suo lavoro,
        e il prompt gli chiede infatti di dichiararlo con ``CHECK_OK``."""
        two_tasks.subagent_failure = None

        await two_tasks.cycles(10)

        assert two_tasks.agent.messages == []
        assert two_tasks.entry_for(0) is None

    async def test_an_undeclared_delegation_is_treated_as_a_check_that_ran(
        self, two_tasks: _Harness
    ) -> None:
        """Un modello che non scrive CHECK_DELEGATED torna al comportamento di
        prima: silenzioso. Mai un avviso su un controllo che nessuno ha
        dichiarato rotto."""
        two_tasks.agent.declares_the_delegation = False
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")

        await two_tasks.cycles(6)

        assert two_tasks.agent.messages == []
        assert two_tasks.state.task_checks == {}


class TestTheBlockOnlyAppearsWhereItBelongs:
    """Un annuncio che non riguarda un controllo in sospeso non cambia di un byte."""

    async def test_no_block_when_the_heartbeat_delegated_nothing(
        self, tmp_path: Path
    ) -> None:
        harness = _Harness(tmp_path, _heartbeat_md(_WATERBOT))
        await harness.cycles(1)

        assert _FOLLOWUP_HEAD not in harness.announce_prompts[0]

    async def test_no_block_outside_the_heartbeat_session(
        self, two_tasks: _Harness
    ) -> None:
        await two_tasks.service.run_job("heartbeat")
        await two_tasks.cycle(session_key=UNIFIED_SESSION_KEY)

        assert _FOLLOWUP_HEAD not in two_tasks.announce_prompts[-1]

    async def test_a_mark_with_nothing_pending_blames_nobody(
        self, two_tasks: _Harness
    ) -> None:
        """Il marcatore su un annuncio che non stava aspettando nessun controllo
        non deve incolpare un task sano."""
        two_tasks.agent.delegated = {}
        await two_tasks.service.run_job("heartbeat")
        two_tasks.loop._run_agent_loop = lambda initial_messages, **_kw: _answer(  # type: ignore[method-assign]
            initial_messages, "CHECK_FAILED 1: qualcosa"
        )
        await two_tasks.cycle()

        assert two_tasks.state.task_checks == {}

    async def test_a_pending_entry_for_a_deleted_task_is_pruned(
        self, two_tasks: _Harness
    ) -> None:
        """Lo stato si autoripara anche per una delega: se il task non c'è più
        nel file, la voce in attesa non resta appesa."""
        two_tasks.subagent_failure = (1, "import di wb_probe fallito")
        await two_tasks.cycles(1)
        assert two_tasks.state.task_checks != {}

        two_tasks.rewrite(_heartbeat_md(_VITAMINE))
        two_tasks.agent.delegated = {}
        await two_tasks.service.run_job("heartbeat")

        assert two_tasks.state.task_checks == {}

    async def test_the_block_is_an_instruction_not_a_message_in_the_history(
        self, two_tasks: _Harness
    ) -> None:
        """Se finisse nella history tornerebbe in ogni turno successivo, e la
        sessione dell'heartbeat accumulerebbe istruzioni scadute."""
        await two_tasks.cycles(1)

        session = two_tasks.loop.sessions.get_or_create(HEARTBEAT_SESSION_KEY)
        assert _FOLLOWUP_HEAD in two_tasks.announce_prompts[0]
        assert not any(
            _FOLLOWUP_HEAD in str(m.get("content")) for m in session.messages
        )


async def _answer(initial_messages: list[dict], text: str):
    return (text, [], [*initial_messages, {"role": "assistant", "content": text}], "stop", False)
